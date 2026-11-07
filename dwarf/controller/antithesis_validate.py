"""Artifacts-mode inspection of a saved Antithesis package inside the controller (F51).

The dashboard has no Docker socket (BC2: every Docker operation runs in the
controller). For ``antithesis.package-validate`` it stages the exact package
archive under version-imports, the way registry credentials are staged, and
passes the archive's digest. This module reads the archive by id, checks the
digest, inspects every service image for linux/amd64, checks the effective
Compose schema, builds the pinned MOOG config-image candidate with
``--network none``, reads every packaged file back out of a never-started
container, compares bytes and modes, and removes the container and the image.

It returns *facts*. The dashboard turns them into the preflight contract rows
and keeps the policy text; nothing here decides pass or fail for the package.
Every Docker log stays in the controller's own job directory.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import time
from datetime import datetime, timezone
from pathlib import Path

from controller.docker_runtime import DockerRuntimeError
from controller.antithesis_publish import _Job, _detail, _now, _read_progress

STAGE_ID = re.compile(r'^validate-[0-9a-f]{24}$')
_ARCHIVE_LIMIT = 32 * 1024 * 1024
_IMAGE_ID = re.compile(r'^sha256:[0-9a-f]{64}$')
INSPECT_CEILING = 30
COMPOSE_CEILING = 30
BUILD_CEILING = 300
IMAGES_BUDGET = 600
CANDIDATE_BUDGET = 600


def _invalid(message):
    return DockerRuntimeError(message, code='package-archive-invalid', stage='package-validate')


def _verified_staged_archive(runtime, stage_id, archive_sha256):
    """The staged package archive as bytes, or a bounded error. Same owner,
    symlink, descriptor and expiry checks as the staged registry credential."""
    root_raw = getattr(runtime, 'version_imports_dir', None) or os.environ.get('ADA2_DWARF_VERSION_IMPORTS_DIR', '')
    if not STAGE_ID.fullmatch(str(stage_id)) or not root_raw:
        raise _invalid('package archive reference is invalid')
    root = Path(root_raw)
    nofollow = getattr(os, 'O_NOFOLLOW', 0)
    directory = root / stage_id
    try:
        opened = os.open(directory, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | nofollow)
    except OSError as exc:
        raise _invalid('package archive is not staged') from exc
    try:
        if os.fstat(opened).st_uid not in {0, os.geteuid()}:
            raise _invalid('package archive is not owned by DWARF')
        try:
            descriptor_fd = os.open('descriptor.json', os.O_RDONLY | nofollow, dir_fd=opened)
        except OSError as exc:
            raise _invalid('package archive descriptor is missing') from exc
        try:
            descriptor = json.loads(os.read(descriptor_fd, 16 * 1024).decode('utf-8'))
        except (OSError, ValueError) as exc:
            raise _invalid('package archive descriptor is invalid') from exc
        finally:
            os.close(descriptor_fd)
        if (not isinstance(descriptor, dict) or descriptor.get('kind') != 'antithesis-package-archive'
                or descriptor.get('stage_id') != stage_id or descriptor.get('archive_sha256') != archive_sha256):
            raise _invalid('package archive descriptor does not match the request')
        try:
            expires = datetime.fromisoformat(str(descriptor.get('expires_at')).replace('Z', '+00:00'))
        except ValueError as exc:
            raise _invalid('package archive descriptor is invalid') from exc
        if expires <= datetime.now(timezone.utc):
            raise _invalid('staged package archive expired; validate again')
        try:
            info = os.stat('package.zip', dir_fd=opened, follow_symlinks=False)
        except OSError as exc:
            raise _invalid('package archive is missing') from exc
        if not stat.S_ISREG(info.st_mode) or info.st_size == 0 or info.st_size > _ARCHIVE_LIMIT or info.st_uid not in {0, os.geteuid()}:
            raise _invalid('package archive is invalid')
        archive_fd = os.open('package.zip', os.O_RDONLY | nofollow, dir_fd=opened)
        try:
            chunks = []
            while True:
                chunk = os.read(archive_fd, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
                if sum(len(c) for c in chunks) > _ARCHIVE_LIMIT:
                    raise _invalid('package archive is invalid')
            data = b''.join(chunks)
        finally:
            os.close(archive_fd)
    finally:
        os.close(opened)
    if 'sha256:' + hashlib.sha256(data).hexdigest() != archive_sha256:
        raise _invalid('package archive digest differs from the request')
    return data


def _inspect_local(job, reference, log):
    """Local image facts: ``pass`` (linux/amd64), ``fail`` (another platform)
    or ``not_checked`` (absent or unreadable). Nothing is pulled."""
    unknown = {'reference': reference, 'state': 'not_checked', 'image_id': None}
    try:
        result = job.run(('image', 'inspect', '--platform', 'linux/amd64', reference), log=log, ceiling=INSPECT_CEILING)
        if result.returncode and 'unknown flag' in (result.stderr or '') + (result.stdout or ''):
            result = job.run(('image', 'inspect', reference), log=log, ceiling=INSPECT_CEILING)
    except (DockerRuntimeError, TimeoutError):
        # One slow or capped inspect is a fact about that image, not the end of the job.
        return unknown
    if result.returncode:
        return unknown
    try:
        info = json.loads(result.stdout)[0]
    except (ValueError, IndexError, TypeError):
        return unknown
    if not isinstance(info, dict) or not _IMAGE_ID.fullmatch(str(info.get('Id', ''))):
        return unknown
    valid = info.get('Os') == 'linux' and info.get('Architecture') == 'amd64'
    return {'reference': reference, 'state': 'pass' if valid else 'fail', 'image_id': str(info['Id'])}


def _inspect_images(job, report):
    deadline = time.monotonic() + IMAGES_BUDGET
    rows, seen = [], set()
    for index, row in enumerate(report['images']):
        reference = row['reference']
        if reference in seen:
            continue
        seen.add(reference)
        if time.monotonic() > deadline:
            rows.append({'reference': reference, 'state': 'not_checked', 'image_id': None})
            continue
        job.progress(stage='image-inspect', current_image=reference)
        rows.append(_inspect_local(job, reference, 'local-image-' + str(index) + '.log'))
    return rows


def _compose_schema(job, context, files):
    """Validate the effective Compose without importing host environment values."""
    schema_file = job.directory / 'effective-compose.yaml'
    schema_file.write_bytes(files['docker-compose.yaml'][0].replace(b'${INTERNAL_NETWORK}', b'false'))
    job.progress(stage='compose-schema', current_image=None)
    try:
        result = job.run(('compose', '--project-name', 'dwarf-antithesis-check', '--env-file', '/dev/null',
                          '--project-directory', str(context), '--file', str(schema_file), 'config',
                          '--quiet', '--no-interpolate', '--no-env-resolution'), log='compose-schema.log', ceiling=COMPOSE_CEILING)
    except (DockerRuntimeError, OSError, TimeoutError):
        return {'state': 'not_checked'}
    return {'state': 'pass' if result.returncode == 0 else 'fail'}


def _candidate(job, context, files, compose_schema, dockerfile_text):
    """Build the pinned MOOG recipe and compare files through a never-started container."""
    suffix = job.execution_id.rsplit('-', 1)[-1]
    tag = 'dwarf-antithesis-config-check:' + suffix
    name = 'dwarf-antithesis-check-' + suffix
    candidate = {'context_blocked': False, 'reference': tag, 'container_name': name, 'attempted': False, 'built': False,
                 'image_id': None, 'platform_ok': None, 'created': False, 'completed': False, 'files': [], 'cleanup': {}}
    if compose_schema.get('state') != 'pass':
        return candidate
    job.progress(stage='candidate', candidate_reference=tag, candidate_container=name)
    dockerfile = job.directory / 'MOOG-config.Dockerfile'
    dockerfile.write_text(dockerfile_text)
    deadline = time.monotonic() + CANDIDATE_BUDGET
    readback = job.directory / 'candidate-readback'

    def run(arguments, log, ceiling=30):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('candidate inspection budget exhausted')
        return job.run(arguments, log=log, ceiling=min(ceiling, remaining))

    try:
      try:
        candidate['attempted'] = True
        built = run(('build', '--platform', 'linux/amd64', '--network', 'none', '--tag', tag, '--file', str(dockerfile), str(context)),
                    'candidate-build.log', BUILD_CEILING)
        if built.returncode:
            return candidate
        candidate['built'] = True
        info = _inspect_local(job, tag, 'candidate-image.log')
        candidate['image_id'] = info['image_id']
        candidate['platform_ok'] = info['state'] == 'pass'
        if not candidate['platform_ok']:
            return candidate
        # Mark before create so a lost response still triggers cleanup of our unique name.
        candidate['created'] = True
        created = run(('create', '--platform', 'linux/amd64', '--network', 'none', '--name', name, info['image_id'], '/bin/true'),
                      'candidate-create.log')
        if created.returncode:
            return candidate
        readback.mkdir(mode=0o700)
        rows = []
        for index, (path, (data, mode)) in enumerate(sorted(files.items())):
            target = readback / str(index)
            expected = data.replace(b'${INTERNAL_NETWORK}', b'false') if path == 'docker-compose.yaml' and data is not None else data
            try:
                copied = run(('cp', name + ':/' + path, str(target)), 'candidate-copy-' + str(index) + '.log')
                if copied.returncode:
                    matches = False
                elif data is None:
                    matches = not target.is_symlink() and target.is_dir() and target.stat().st_mode & 0o777 == mode
                else:
                    matches = (not target.is_symlink() and target.is_file() and target.stat().st_size == len(expected)
                               and target.read_bytes() == expected and target.stat().st_mode & 0o777 == mode)
            except (OSError, DockerRuntimeError):
                matches = False
            rows.append({'path': path, 'kind': 'directory' if data is None else 'file', 'mode': mode,
                         'sha256': None if data is None else hashlib.sha256(expected).hexdigest(), 'matches': bool(matches)})
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target, ignore_errors=True)
        candidate['files'] = rows
        candidate['completed'] = True
        return candidate
      except (DockerRuntimeError, TimeoutError):
        # A timed-out or output-capped step is a fact (completed stays False);
        # cancellation propagates after the cleanup below.
        return candidate
    finally:
        _remove_candidate(job.runtime, job.execution_id, job.directory, name if candidate['created'] else None,
                          tag if candidate['attempted'] else None, candidate['cleanup'])
        shutil.rmtree(readback, ignore_errors=True)


def _remove_candidate(runtime, execution_id, directory, container, image, cleanup):
    """Remove the candidate container and image directly through the runtime,
    outside the job's deadline and cancel flag, so a cancelled or timed-out
    inspection never leaves them on the host daemon."""
    for label, arguments in (('container', ('rm', '-f', container) if container else None),
                             ('image', ('image', 'rm', image) if image else None)):
        if arguments is None:
            continue
        try:
            result = runtime._run(arguments, timeout=30, execution_id=execution_id,
                                  output_limit=64 * 1024, output_path=directory / ('cleanup-' + label + '.log'))
            cleanup[label] = 'removed' if result.returncode == 0 else 'unconfirmed'
        except Exception:  # noqa: BLE001 - cleanup must not mask the inspection result
            cleanup[label] = 'unconfirmed'


def validate_package(runtime, parameters, execution_id, timeout, cancel_event):
    from profile_manager import antithesis_preparation as prep
    from profile_manager import antithesis_preflight as preflight
    from profile_manager.antithesis_package_validation import CONFIG_DOCKERFILE, _SYSTEM_ROOTS
    if parameters.get('execution_id', execution_id) != execution_id:
        raise ValueError('Validation execution identity differs')
    job = _Job(runtime, execution_id, 'validate', timeout, cancel_event, stage='package-validate')
    stage_id, archive_sha256 = parameters['stage_id'], parameters['archive_sha256']
    job.progress(status='validating', execution_id=execution_id, controller_installation_id=runtime.installation_id,
                 stage_id=stage_id, archive_sha256=archive_sha256, stage='staged-archive', current_image=None, started_at=_now())
    try:
        data = _verified_staged_archive(runtime, stage_id, archive_sha256)
    except DockerRuntimeError as exc:
        job.progress(status='failed', stage='staged-archive', detail=_detail(exc))
        raise
    context = job.directory / 'context'
    try:
        files, _ = prep._unpack(data)
        report = preflight.inspect_package(files)
        images = _inspect_images(job, report)
        if report['state'] == 'blocked':
            # The static contract already refused this package; its Compose file
            # is not trusted with `compose config` or a build on the socket host.
            compose_schema = {'state': 'not_checked'}
            candidate = {'static_blocked': True}
        elif any(name.split('/')[0] in _SYSTEM_ROOTS for name in files):
            compose_schema = {'state': 'not_checked'}
            candidate = {'context_blocked': True}
        else:
            prep._write_tree(context, files)
            compose_schema = _compose_schema(job, context, files)
            candidate = _candidate(job, context, files, compose_schema, CONFIG_DOCKERFILE)
        result = {'status': 'validated', 'execution_id': execution_id, 'stage_id': stage_id, 'archive_sha256': archive_sha256,
                  'images': images, 'compose_schema': compose_schema, 'candidate': candidate}
        job.progress(status='validated', stage='complete', current_image=None, completed_at=_now(), result=result)
        return result
    except DockerRuntimeError as exc:
        if exc.failure_code is None:
            job.progress(status='failed', stage='error', detail=_detail(exc))
        raise
    except BaseException as exc:
        job.progress(status='failed', stage='error', detail=_detail(exc))
        raise
    finally:
        shutil.rmtree(context, ignore_errors=True)


def read_validate_progress(runtime, execution_id):
    return _read_progress(runtime, execution_id, 'validate')


def cleanup_validate(runtime, execution_id):
    """Remove the candidate container and image an interrupted run may have
    left, then the unpacked context. Best effort; nothing here raises."""
    try:
        progress = read_validate_progress(runtime, execution_id) or {}
    except ValueError:
        progress = {}
    from controller.antithesis_publish import _directory
    directory = _directory(runtime, execution_id, 'validate')
    container, image = progress.get('candidate_container'), progress.get('candidate_reference')
    _remove_candidate(runtime, execution_id, directory, container if isinstance(container, str) else None,
                      image if isinstance(image, str) else None, {})
    for name in ('context', 'candidate-readback'):
        path = directory / name
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)
