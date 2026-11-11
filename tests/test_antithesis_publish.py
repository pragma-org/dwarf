import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from controller import antithesis_publish as publish
from controller.docker_runtime import DockerResult, DockerRuntimeError
from test_antithesis_image_build import prepared, storage, IMAGE_ID  # noqa: F401  (fixtures)
from test_antithesis_version_binding import catalog  # noqa: F401  (fixture used by `prepared`)

BUILD_ID = 'antithesis-build-' + 'f' * 32
PUBLISH_ID = 'antithesis-publish-' + 'e' * 32
PAT = 'ghp_' + 'S3cret' * 6



def _is_build(argv):
    argv = tuple(argv)
    return argv[:1] == ('build',) or argv[:2] == ('buildx', 'build')

def _inspect_json(image_id=IMAGE_ID):
    return json.dumps([{'Id': image_id, 'Os': 'linux', 'Architecture': 'amd64'}])


@pytest.fixture
def runtime(tmp_path):
    def _run(arguments, timeout=30, execution_id=None, *, output_limit=None, output_path=None, stdin_path=None):
        arguments = tuple(arguments)
        runtime.calls.append({'argv': arguments, 'stdin_path': stdin_path, 'output_path': output_path})
        if output_path is not None:
            Path(output_path).write_text('log for ' + ' '.join(arguments[:3]) + '\n')
        head = arguments[:2]
        if head == ('image', 'inspect'):
            if runtime.missing_image and runtime.missing_image in arguments:
                return DockerResult(1, '[]', 'Error: No such image: ' + runtime.missing_image)
            return DockerResult(0, _inspect_json(), '')
        if _is_build(arguments):
            return DockerResult(runtime.build_rc, 'Successfully built', '' if runtime.build_rc == 0 else 'ERROR: failed to solve')
        if 'login' in arguments:
            return DockerResult(runtime.login_rc, 'Login Succeeded' if runtime.login_rc == 0 else '',
                                '' if runtime.login_rc == 0 else 'Error response from daemon: denied: token ' + PAT + ' rejected')
        if arguments[0] == 'tag':
            return DockerResult(0, '', '')
        if 'push' in arguments:
            return DockerResult(runtime.push_rc, 'pushed', '' if runtime.push_rc == 0 else 'denied: requested access to the resource is denied')
        return DockerResult(0, '', '')
    runtime = SimpleNamespace(state_dir=tmp_path / 'controller-state', host_state_dir=Path('/host/controller'),
                              installation_id='test-install', calls=[], _run=_run, build_rc=0, login_rc=0, push_rc=0, missing_image=None,
                              version_imports_dir=tmp_path / 'version-imports')
    runtime.state_dir.mkdir()
    runtime.version_imports_dir.mkdir()
    return runtime


def _build_params(preparation_id):
    return {'preparation_id': preparation_id, 'execution_id': BUILD_ID, 'build_sha256': 'sha256:' + 'a' * 64}


def _preparations_dir(monkeypatch):
    directory = Path(os.environ['ADA2_DWARF_STATE_DIR']) / 'antithesis' / 'preparations'
    monkeypatch.setenv('DWARF_CONTROLLER_PREPARATIONS_DIR', str(directory))
    return directory


def test_build_unpacks_the_preparation_and_builds_each_build_service(runtime, prepared, monkeypatch):
    _preparations_dir(monkeypatch)
    preparation_id = prepared['receipt']['id']
    result = publish.build_images(runtime, _build_params(preparation_id), BUILD_ID, 1800, threading.Event())
    assert result['status'] == 'built' and result['preparation_id'] == preparation_id
    assert result['build_host'] == 'DWARF controller'
    built = [row for row in result['built_images'] if row.get('reference', '').startswith('dwarf-antithesis-')]
    assert built and built[0]['service'] == 'sm-workload'
    assert built[0]['reference'] == 'dwarf-antithesis-' + 'f' * 32 + '-0:prepared'
    assert built[0]['image_id'] == IMAGE_ID and built[0]['publication'] == 'local-only'
    build_call = next(c for c in runtime.calls if _is_build(c['argv']))
    argv = build_call['argv']
    assert argv[argv.index('--platform') + 1] == 'linux/amd64'
    context = Path(argv[-1])
    assert context == runtime.state_dir / 'antithesis-builds' / BUILD_ID / 'package' / 'miniprotocol'
    assert (context / 'workload' / 'Dockerfile').is_file()
    assert argv[argv.index('--file') + 1] == str(context / 'workload' / 'Dockerfile')
    progress = publish.read_build_progress(runtime, BUILD_ID)
    assert progress['status'] == 'built' and progress['execution_id'] == BUILD_ID


def test_build_refuses_a_preparation_whose_inventory_changed(runtime, prepared, monkeypatch):
    directory = _preparations_dir(monkeypatch)
    preparation_id = prepared['receipt']['id']
    compose = directory / preparation_id / 'package' / 'docker-compose.yaml'
    compose.write_text(compose.read_text() + '\n# tampered\n')
    with pytest.raises(DockerRuntimeError) as info:
        publish.build_images(runtime, _build_params(preparation_id), BUILD_ID, 1800, threading.Event())
    assert info.value.failure_code == 'build-source-invalid' and info.value.failure_stage == 'image-build'
    assert not any(_is_build(c['argv']) for c in runtime.calls)


def test_build_reports_a_failed_docker_build(runtime, prepared, monkeypatch):
    _preparations_dir(monkeypatch)
    runtime.build_rc = 1
    with pytest.raises(DockerRuntimeError) as info:
        publish.build_images(runtime, _build_params(prepared['receipt']['id']), BUILD_ID, 1800, threading.Event())
    assert info.value.failure_code == 'image-build-failed'
    assert publish.read_build_progress(runtime, BUILD_ID)['status'] == 'failed'


def test_build_reports_a_missing_node_artifact_image(runtime, prepared, monkeypatch):
    _preparations_dir(monkeypatch)
    receipt = prepared['receipt']
    # Give the preparation a node artifact row so the controller must find its image.
    directory = Path(os.environ['DWARF_CONTROLLER_PREPARATIONS_DIR']) / receipt['id']
    stored = json.loads((directory / 'receipt.json').read_text())
    stored.setdefault('version_binding', {})['node_artifacts'] = {'amaru': {
        'id': 'c' * 32, 'implementation': 'amaru', 'reference': 'dwarf-antithesis-node-' + 'c' * 32 + ':prepared',
        'image_id': 'sha256:' + 'b' * 64}}
    (directory / 'receipt.json').write_text(json.dumps(stored))
    runtime.missing_image = 'dwarf-antithesis-node-' + 'c' * 32 + ':prepared'
    with pytest.raises(DockerRuntimeError) as info:
        publish.build_images(runtime, _build_params(receipt['id']), BUILD_ID, 1800, threading.Event())
    assert info.value.failure_code == 'artifact-image-missing'


# --- publish ---------------------------------------------------------------

def _stage(runtime, value=PAT, *, expired=False):
    from profile_manager import binary_imports
    secret_id = binary_imports.stage_secret(value, root=runtime.version_imports_dir)
    if expired:
        descriptor = runtime.version_imports_dir / secret_id / 'descriptor.json'
        body = json.loads(descriptor.read_text())
        body['expires_at'] = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat().replace('+00:00', 'Z')
        descriptor.write_text(json.dumps(body))
    return secret_id


def _publish_params(secret_id):
    return {'handoff_id': 'd' * 32, 'secret_id': secret_id, 'username': 'j-gainsec',
            'images': [{'reference': 'dwarf-antithesis-' + 'f' * 32 + '-0:prepared', 'image_id': IMAGE_ID,
                        'destination': 'ghcr.io/j-gainsec/dwarf-sm-workload:' + 'd' * 32}],
            'execution_id': PUBLISH_ID, 'publish_sha256': 'sha256:' + 'e' * 64}


def test_publish_logs_in_with_password_stdin_then_tags_and_pushes(runtime, monkeypatch):
    secret_id = _stage(runtime)
    result = publish.publish_images(runtime, _publish_params(secret_id), PUBLISH_ID, 1800, threading.Event())
    assert result['status'] == 'published' and result['registry'] == 'ghcr.io'
    assert result['pushed'][0]['destination'] == 'ghcr.io/j-gainsec/dwarf-sm-workload:' + 'd' * 32
    login = next(c for c in runtime.calls if 'login' in c['argv'])
    assert login['argv'][:2] == ('--config', login['argv'][1]) and login['argv'][1].startswith('/tmp/dwarf-ghcr-')
    assert login['argv'][2:] == ('login', 'ghcr.io', '-u', 'j-gainsec', '--password-stdin')
    assert login['stdin_path'] == runtime.version_imports_dir / secret_id / 'secret'
    order = [c['argv'][0] if c['argv'][0] != '--config' else c['argv'][2] for c in runtime.calls]
    assert order == ['login', 'image', 'tag', 'push']
    push = runtime.calls[-1]['argv']
    assert push[:2] == ('--config', login['argv'][1]) and push[2:] == ('push', 'ghcr.io/j-gainsec/dwarf-sm-workload:' + 'd' * 32)
    assert not Path(login['argv'][1]).exists(), 'temporary docker config must be removed'
    everything = json.dumps([list(c['argv']) for c in runtime.calls]) + json.dumps(publish.read_publish_progress(runtime, PUBLISH_ID)) + json.dumps(result)
    for log in (runtime.state_dir / 'antithesis-publishes' / PUBLISH_ID).rglob('*.log'):
        everything += log.read_text()
    assert PAT not in everything and secret_id not in json.dumps(result)


def test_publish_refuses_an_expired_secret(runtime):
    secret_id = _stage(runtime, expired=True)
    with pytest.raises(DockerRuntimeError) as info:
        publish.publish_images(runtime, _publish_params(secret_id), PUBLISH_ID, 1800, threading.Event())
    assert info.value.failure_code == 'registry-secret-invalid' and info.value.failure_stage == 'image-publish'
    assert not any('login' in c['argv'] for c in runtime.calls)


def test_publish_refuses_a_missing_secret(runtime):
    with pytest.raises(DockerRuntimeError) as info:
        publish.publish_images(runtime, _publish_params('ghcr-' + '0' * 24), PUBLISH_ID, 1800, threading.Event())
    assert info.value.failure_code == 'registry-secret-invalid'


def test_publish_login_failure_is_bounded(runtime):
    secret_id = _stage(runtime)
    runtime.login_rc = 1
    with pytest.raises(DockerRuntimeError) as info:
        publish.publish_images(runtime, _publish_params(secret_id), PUBLISH_ID, 1800, threading.Event())
    assert info.value.failure_code == 'registry-login-failed'
    assert PAT not in str(info.value)
    progress = publish.read_publish_progress(runtime, PUBLISH_ID)
    assert progress['status'] == 'failed' and PAT not in json.dumps(progress)
    assert not any(c['argv'][0] == 'tag' for c in runtime.calls)


def test_publish_push_failure_keeps_the_rejected_destination(runtime):
    secret_id = _stage(runtime)
    runtime.push_rc = 1
    with pytest.raises(DockerRuntimeError) as info:
        publish.publish_images(runtime, _publish_params(secret_id), PUBLISH_ID, 1800, threading.Event())
    assert info.value.failure_code == 'image-push-failed'
    assert 'dwarf-sm-workload' in publish.read_publish_progress(runtime, PUBLISH_ID)['detail']


def test_stage_secret_writes_0600_with_expiry_and_discard_removes_it(tmp_path):
    from profile_manager import binary_imports
    secret_id = binary_imports.stage_secret(PAT, root=tmp_path)
    assert __import__('re').fullmatch(r'ghcr-[0-9a-f]{24}', secret_id)
    secret = tmp_path / secret_id / 'secret'
    assert secret.read_text() == PAT and (secret.stat().st_mode & 0o777) == 0o600
    descriptor = json.loads((tmp_path / secret_id / 'descriptor.json').read_text())
    assert descriptor['kind'] == 'ghcr-publish-pat' and descriptor['secret_id'] == secret_id
    expires = datetime.fromisoformat(descriptor['expires_at'].replace('Z', '+00:00'))
    assert timedelta(minutes=14) < expires - datetime.now(timezone.utc) <= timedelta(minutes=15)
    binary_imports.discard_secret(secret_id, root=tmp_path)
    assert not (tmp_path / secret_id).exists()
    binary_imports.discard_secret(secret_id, root=tmp_path)   # idempotent


def test_inspect_falls_back_when_the_cli_lacks_platform(runtime, prepared, monkeypatch):
    """The controller image's docker CLI may predate `image inspect --platform`
    (F31): fall back to a plain inspect and still check Os/Architecture."""
    _preparations_dir(monkeypatch)
    original = runtime._run
    seen = []
    def _run(arguments, timeout=30, execution_id=None, **kwargs):
        arguments = tuple(arguments)
        if arguments[:2] == ('image', 'inspect') and '--platform' in arguments:
            seen.append(arguments)
            if kwargs.get('output_path'):
                Path(kwargs['output_path']).write_text("unknown flag: --platform\nSee 'docker image inspect --help'.\n")
            return DockerResult(125, '', "unknown flag: --platform\nSee 'docker image inspect --help'.")
        return original(arguments, timeout, execution_id, **kwargs)
    runtime._run = _run
    result = publish.build_images(runtime, _build_params(prepared['receipt']['id']), BUILD_ID, 1800, threading.Event())
    assert result['status'] == 'built'
    assert seen, 'the --platform form is still tried first'
    plain = [c['argv'] for c in runtime.calls if c['argv'][:2] == ('image', 'inspect') and '--platform' not in c['argv']]
    assert plain, 'the plain inspect was used as the fallback'


@pytest.mark.parametrize('has_buildx', [False, True])
def test_build_rewrites_copy_chmod_when_the_cli_has_no_buildkit(runtime, prepared, monkeypatch, has_buildx):
    """The controller image's docker CLI has no buildx (F32): `COPY --chmod`
    needs BuildKit, so the controller's own copy of the Dockerfile is rewritten
    to COPY + RUN chmod, exactly as release_builds does for Cardano images.
    With buildx present the Dockerfile is built as written."""
    _preparations_dir(monkeypatch)
    original = runtime._run
    def _run(arguments, timeout=30, execution_id=None, **kwargs):
        arguments = tuple(arguments)
        if arguments[:2] == ('buildx', 'version'):
            runtime.calls.append({'argv': arguments, 'stdin_path': None, 'output_path': None})
            return DockerResult(0, 'github.com/docker/buildx v0.19.0', '') if has_buildx else DockerResult(1, '', "docker: 'buildx' is not a docker command.")
        return original(arguments, timeout, execution_id, **kwargs)
    runtime._run = _run
    result = publish.build_images(runtime, _build_params(prepared['receipt']['id']), BUILD_ID, 1800, threading.Event())
    assert result['status'] == 'built'
    build_call = next(c for c in runtime.calls if _is_build(c['argv']))
    dockerfile = Path(build_call['argv'][build_call['argv'].index('--file') + 1]).read_text()
    original_text = (Path(__file__).resolve().parents[1] / 'antithesis/cardano_amaru_miniprotocol_security/workload/Dockerfile').read_text()
    assert 'COPY --chmod=0755' in original_text
    if has_buildx:
        assert dockerfile == original_text
        argv = build_call['argv']
        output = argv[argv.index('--output') + 1]
        # Every layer is re-written as OCI gzip, so the pushed manifest never mixes Docker-typed base
        # layers into an OCI manifest (Antithesis' buildah import refuses that).
        for part in ('type=image', 'oci-mediatypes=true', 'compression=gzip', 'force-compression=true', 'push=false'):
            assert part in output.split(',')
        assert 'name=' + build_call['argv'][argv.index('--output') + 1].split('name=')[1].split(',')[0] in output
    else:
        assert '--chmod' not in dockerfile
        assert 'COPY workload/test/v1/mixed-miniprotocol-security/ /opt/antithesis/test/v1/mixed-miniprotocol-security/\nRUN chmod -R 0755 /opt/antithesis/test/v1/mixed-miniprotocol-security/' in dockerfile
        assert result['legacy_builder'] is True


@pytest.mark.parametrize('message', ['Docker operation timed out', 'Docker operation output exceeded limit'])
def test_runtime_failure_without_a_code_records_failed_progress(runtime, prepared, monkeypatch, message):
    """Review fix: _run's own code-less errors (timeout, output cap) must leave
    progress.json failed with the bounded message, not stuck at 'building'."""
    from controller.docker_runtime import DockerRuntimeError
    _preparations_dir(monkeypatch)
    original = runtime._run
    def _run(arguments, timeout=30, execution_id=None, **kwargs):
        if _is_build(tuple(arguments)):
            raise DockerRuntimeError(message)
        return original(arguments, timeout, execution_id, **kwargs)
    runtime._run = _run
    with pytest.raises(DockerRuntimeError):
        publish.build_images(runtime, _build_params(prepared['receipt']['id']), BUILD_ID, 1800, threading.Event())
    progress = publish.read_build_progress(runtime, BUILD_ID)
    assert progress['status'] == 'failed' and progress['stage'] == 'error' and message in progress['detail'], progress


def test_build_passes_network_none_only_for_labelled_services(runtime, monkeypatch, catalog):
    import copy
    from profile_manager import antithesis_versions
    from profile_manager.antithesis_preparation import prepare_package
    from test_antithesis_package import request
    from test_antithesis_amaru_binding import selection
    _preparations_dir(monkeypatch)
    prepared = prepare_package(request(scenario_id='runtime-substrate-mempool-failure-containment-mixed-amaru', versions=selection('mixed')), {})
    result = publish.build_images(runtime, _build_params(prepared['receipt']['id']), BUILD_ID, 1800, threading.Event())
    assert result['status'] == 'built'
    builds = [c['argv'] for c in runtime.calls if _is_build(c['argv'])]
    workload = [argv for argv in builds if argv[-1].endswith('/dwarf-workload')]
    assert len(workload) == 1 and workload[0][workload[0].index('--network') + 1] == 'none'
    assert all('--network' not in argv for argv in builds if not argv[-1].endswith('/dwarf-workload'))
    row = next(r for r in result['built_images'] if r['service'] == 'dwarf-workload')
    assert row['network'] == 'none'
