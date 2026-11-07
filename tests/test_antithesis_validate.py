"""controller.antithesis_validate: the artifacts-mode package inspection runs
where the Docker socket is (F51). The dashboard stages the exact package
archive; the controller inspects images, checks the Compose schema, builds the
pinned MOOG config-image candidate, reads the files back and returns facts."""
import hashlib
import io
import json
import re
import secrets
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from controller import antithesis_validate as validate
from controller.docker_runtime import DockerResult, DockerRuntimeError
from test_antithesis_preflight import package

EXECUTION_ID = 'antithesis-validate-' + 'f' * 32
IMAGE_ID = 'sha256:' + 'a' * 64


def _zip(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name, (data, mode) in sorted(files.items()):
            info = zipfile.ZipInfo(name + ('/' if data is None else ''))
            info.external_attr = ((0o040000 if data is None else 0o100000) | mode) << 16
            archive.writestr(info, b'' if data is None else data)
    return buffer.getvalue()


def stage(runtime, files, *, expired=False, digest=None):
    stage_id = 'validate-' + secrets.token_hex(12)
    data = _zip(files)
    archive_sha256 = digest or ('sha256:' + hashlib.sha256(data).hexdigest())
    directory = runtime.version_imports_dir / stage_id
    directory.mkdir(mode=0o700)
    (directory / 'package.zip').write_bytes(data)
    expires = datetime.now(timezone.utc) + timedelta(seconds=-1 if expired else 900)
    (directory / 'descriptor.json').write_text(json.dumps({
        'schema_version': 'v1', 'kind': 'antithesis-package-archive', 'stage_id': stage_id,
        'archive_sha256': 'sha256:' + hashlib.sha256(data).hexdigest(),
        'expires_at': expires.isoformat().replace('+00:00', 'Z'), 'state': 'staged'}))
    return {'stage_id': stage_id, 'archive_sha256': archive_sha256, 'execution_id': EXECUTION_ID}


@pytest.fixture
def runtime(tmp_path):
    def _run(arguments, timeout=30, execution_id=None, *, output_limit=None, output_path=None, stdin_path=None):
        arguments = tuple(arguments)
        runtime.calls.append(arguments)
        if output_path is not None:
            Path(output_path).write_text('log for ' + ' '.join(arguments[:3]) + '\n')
        head = arguments[:2]
        if head == ('image', 'inspect'):
            reference = arguments[-1]
            if reference in runtime.missing_images:
                return DockerResult(1, '[]', 'Error: No such image: ' + reference)
            return DockerResult(0, json.dumps([{'Id': IMAGE_ID, 'Os': 'linux', 'Architecture': runtime.platform}]), '')
        if arguments[0] == 'compose':
            assert 'config' in arguments and '--quiet' in arguments and '--no-interpolate' in arguments and '--no-env-resolution' in arguments
            return DockerResult(runtime.compose_rc, '', 'invalid compose' if runtime.compose_rc else '')
        if arguments[0] == 'build':
            assert '--network' in arguments and 'none' in arguments
            dockerfile = Path(arguments[arguments.index('--file') + 1])
            assert 'COPY . /' in dockerfile.read_text()
            return DockerResult(runtime.build_rc, 'built', '' if runtime.build_rc == 0 else 'ERROR: failed to solve')
        if arguments[0] == 'create':
            return DockerResult(0, 'c' * 64, '')
        if arguments[0] == 'cp':
            relative = arguments[1].split(':/', 1)[1]
            target = Path(arguments[2])
            source = target.parent.parent / 'context' / relative
            if relative in runtime.corrupt_paths:
                target.write_bytes(b'changed')
            elif source.is_dir():
                target.mkdir()
                target.chmod(source.stat().st_mode & 0o777)
            else:
                body = source.read_bytes()
                if relative == 'docker-compose.yaml':
                    body = body.replace(b'${INTERNAL_NETWORK}', b'false')
                target.write_bytes(body)
                target.chmod(source.stat().st_mode & 0o777)
            return DockerResult(0, '', '')
        if arguments[0] == 'rm' or head == ('image', 'rm'):
            if runtime.unclean:
                raise DockerRuntimeError('private cleanup detail')
            return DockerResult(0, '', '')
        raise AssertionError('unexpected docker call: ' + ' '.join(arguments))
    runtime = SimpleNamespace(state_dir=tmp_path / 'controller-state', installation_id='test-install', calls=[], _run=_run,
                              platform='amd64', compose_rc=0, build_rc=0, missing_images=set(), corrupt_paths=set(), unclean=False,
                              version_imports_dir=tmp_path / 'version-imports')
    runtime.state_dir.mkdir()
    runtime.version_imports_dir.mkdir()
    return runtime


def _validate(runtime, files, execution_id=EXECUTION_ID, **overrides):
    import threading
    parameters = {**stage(runtime, files), **overrides, 'execution_id': execution_id}
    return validate.validate_package(runtime, parameters, execution_id, 600, threading.Event())


def test_validate_package_inspects_images_compose_and_candidate(runtime):
    result = _validate(runtime, package())

    assert result['status'] == 'validated' and result['execution_id'] == EXECUTION_ID
    assert result['images'] == [{'reference': 'example/node:experimental', 'state': 'pass', 'image_id': IMAGE_ID}]
    assert result['compose_schema'] == {'state': 'pass'}
    candidate = result['candidate']
    assert candidate['context_blocked'] is False and candidate['completed'] is True and candidate['platform_ok'] is True
    assert candidate['image_id'] == IMAGE_ID and candidate['reference'].startswith('dwarf-antithesis-config-check:')
    assert {row['path'] for row in candidate['files']} == {'docker-compose.yaml', 'config.json'}
    assert all(row['matches'] for row in candidate['files'])
    assert all(re.fullmatch(r'[0-9a-f]{64}', row['sha256']) for row in candidate['files'])
    assert candidate['cleanup'] == {'container': 'removed', 'image': 'removed'}
    heads = [call[0] for call in runtime.calls]
    assert 'build' in heads and 'create' in heads and 'rm' in heads
    assert not any(head in ('run', 'start', 'push', 'pull') for head in heads)
    progress = validate.read_validate_progress(runtime, EXECUTION_ID)
    assert progress['status'] == 'validated' and progress['stage'] == 'complete'
    # The staged archive is unpacked under the controller's own job directory only.
    assert not (runtime.state_dir / 'antithesis-validations' / EXECUTION_ID / 'context').exists()


def test_validate_package_rejects_missing_changed_or_expired_archive(runtime):
    import threading
    files = package()
    for parameters in (
        {**stage(runtime, files), 'stage_id': 'validate-' + '0' * 24},
        stage(runtime, files, digest='sha256:' + '9' * 64),
        stage(runtime, files, expired=True),
    ):
        with pytest.raises(DockerRuntimeError) as failure:
            validate.validate_package(runtime, parameters, EXECUTION_ID, 600, threading.Event())
        assert failure.value.failure_code == 'package-archive-invalid' and failure.value.failure_stage == 'package-validate'
        (runtime.state_dir / 'antithesis-validations' / EXECUTION_ID / 'progress.json').unlink()
    assert not any(call[0] in ('build', 'create') for call in runtime.calls)


def test_validate_package_reports_changed_bytes_and_still_cleans_up(runtime):
    runtime.corrupt_paths = {'config.json'}
    result = _validate(runtime, package())
    rows = {row['path']: row for row in result['candidate']['files']}
    assert rows['config.json']['matches'] is False and rows['docker-compose.yaml']['matches'] is True
    assert result['candidate']['cleanup'] == {'container': 'removed', 'image': 'removed'}


def test_validate_package_reports_directory_members(runtime):
    files = package(volumes=['./empty:/data'])
    files['empty'] = (None, 0o755)
    files['.dockerignore'] = (b'empty\n', 0o644)
    result = _validate(runtime, files)
    rows = {row['path']: row for row in result['candidate']['files']}
    assert rows['empty']['kind'] == 'directory' and rows['empty']['sha256'] is None


def test_validate_package_marks_wrong_platform_and_absent_images(runtime):
    runtime.platform = 'arm64'
    result = _validate(runtime, package())
    assert result['images'][0]['state'] == 'fail'
    assert result['candidate']['platform_ok'] is False and result['candidate']['completed'] is False
    runtime.platform = 'amd64'
    runtime.missing_images = {'example/node:experimental'}
    result = _validate(runtime, package(), execution_id='antithesis-validate-' + 'e' * 32)
    assert result['images'][0] == {'reference': 'example/node:experimental', 'state': 'not_checked', 'image_id': None}


def test_validate_package_blocks_system_root_context_without_building(runtime):
    files = package()
    files['etc/ld.so.preload'] = (b'/evil', 0o644)
    result = _validate(runtime, files)
    assert result['candidate'] == {'context_blocked': True}
    assert not any(call[0] in ('build', 'create', 'compose') for call in runtime.calls)


def test_validate_package_compose_schema_failure_skips_the_build(runtime):
    runtime.compose_rc = 1
    result = _validate(runtime, package())
    assert result['compose_schema'] == {'state': 'fail'}
    assert result['candidate']['attempted'] is False
    assert not any(call[0] == 'build' for call in runtime.calls)


def test_validate_package_build_failure_is_a_fact_not_a_crash(runtime):
    runtime.build_rc = 1
    result = _validate(runtime, package())
    candidate = result['candidate']
    assert candidate['attempted'] is True and candidate['completed'] is False and candidate['image_id'] is None
    assert candidate['cleanup'] == {'image': 'removed'}


def test_validate_package_unconfirmed_cleanup_is_reported_without_detail(runtime):
    runtime.unclean = True
    result = _validate(runtime, package())
    assert result['candidate']['cleanup'] == {'container': 'unconfirmed', 'image': 'unconfirmed'}
    assert 'private cleanup detail' not in json.dumps(result)
    assert 'private cleanup detail' not in json.dumps(validate.read_validate_progress(runtime, EXECUTION_ID))


def test_cleanup_validate_removes_leftover_container_image_and_context(runtime):
    directory = runtime.state_dir / 'antithesis-validations' / EXECUTION_ID
    (directory / 'context').mkdir(parents=True)
    (directory / 'progress.json').write_text(json.dumps({
        'execution_id': EXECUTION_ID, 'controller_installation_id': 'test-install', 'status': 'validating',
        'candidate_reference': 'dwarf-antithesis-config-check:' + 'f' * 32, 'candidate_container': 'dwarf-antithesis-check-' + 'f' * 32}))
    validate.cleanup_validate(runtime, EXECUTION_ID)
    assert ('rm', '-f', 'dwarf-antithesis-check-' + 'f' * 32) in runtime.calls
    assert ('image', 'rm', 'dwarf-antithesis-config-check:' + 'f' * 32) in runtime.calls
    assert not (directory / 'context').exists()
