"""Build and publish Antithesis package images inside the controller.

The dashboard prepares a package (``state/antithesis/preparations/<id>``) and
has no Docker socket. This module is the only place the package's ``build:``
services are built and its images are logged in, tagged and pushed to GHCR:

* ``build_images``: re-read the preparation through ``read_preparation`` (the
  inventory digests must still match what the dashboard inspected), unpack it
  under the controller's own state, build every ``build:`` service for
  linux/amd64, confirm the prepared node-artifact images are present, and
  return the ``built_images`` rows the dashboard writes into its receipt.
* ``publish_images``: ``docker login`` with a staged, expiring secret file fed
  through stdin into a throwaway ``DOCKER_CONFIG`` directory, then tag and push
  each image. The PAT never appears on argv, in progress or in logs.

Every failure is a ``DockerRuntimeError`` with an allow-listed ``(code,
stage)``; the bounded detail lives in ``progress.json``.
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import stat
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from controller.docker_runtime import DockerRuntimeError
from controller import release_builds as builds

PREPARATIONS_ENV = 'DWARF_CONTROLLER_PREPARATIONS_DIR'
BUILD_CEILING = 1200
PUSH_CEILING = 600
_LOG_LIMIT = 8 * 1024 * 1024
_SECRET_LIMIT = 4096
_SECRET_ID = re.compile(r'^ghcr-[0-9a-f]{24}$')
_IMAGE_ID = re.compile(r'^sha256:[0-9a-f]{64}$')
_DETAIL_LIMIT = 300


def _now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _detail(text):
    return re.sub(r'[^\x20-\x7e]', ' ', str(text or ''))[:_DETAIL_LIMIT]


def _directory(runtime, execution_id, kind):
    if not isinstance(execution_id, str) or not builds._JOB.fullmatch(execution_id):
        raise ValueError('Invalid execution ID')
    root = Path(runtime.state_dir) / ('antithesis-builds' if kind == 'build' else 'antithesis-publishes')
    path = root / execution_id
    if root.is_symlink() or path.is_symlink():
        raise ValueError('Job directory cannot be a symlink')
    return path


class _Job:
    def __init__(self, runtime, execution_id, kind, timeout, cancel_event, stage):
        self.runtime = runtime
        self.execution_id = execution_id
        self.kind = kind
        self.stage = stage
        self.directory = _directory(runtime, execution_id, kind)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.deadline = time.monotonic() + timeout
        self.cancel_event = cancel_event
        if (self.directory / 'progress.json').exists():
            raise ValueError('Execution already has retained state')

    def progress(self, **changes):
        path = self.directory / 'progress.json'
        progress = builds._json(path) if path.exists() else {}
        progress.update(changes)
        builds._write(path, progress)
        return progress

    def remaining(self, ceiling):
        if self.cancel_event.is_set():
            raise builds.BuildCancelled(self.kind + ' cancelled')
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError(self.kind + ' reached its time limit')
        return max(1, int(min(left, ceiling)))

    def blocked(self, code, stage, detail):
        self.progress(status='failed', stage=stage, detail=_detail(detail))
        return DockerRuntimeError(_detail(detail) or code, code=code, stage=self.stage)

    def run(self, arguments, *, log, ceiling, stdin_path=None):
        return self.runtime._run(tuple(str(a) for a in arguments), timeout=self.remaining(ceiling),
                                 execution_id=self.execution_id, output_limit=_LOG_LIMIT,
                                 output_path=self.directory / log, stdin_path=stdin_path)


def _inspect_amd64(job, image, log):
    result = job.run(('image', 'inspect', '--platform', 'linux/amd64', image), log=log, ceiling=30)
    if result.returncode and 'unknown flag' in (result.stderr or '') + (result.stdout or ''):
        # Older docker CLIs (the controller image's) predate `--platform`; the
        # plain inspect still reports Os/Architecture for a single-platform image.
        result = job.run(('image', 'inspect', image), log=log, ceiling=30)
    if result.returncode:
        return None
    try:
        info = json.loads(result.stdout)[0]
    except (ValueError, IndexError, TypeError):
        return None
    if not isinstance(info, dict) or info.get('Os') != 'linux' or info.get('Architecture') != 'amd64' or not _IMAGE_ID.fullmatch(str(info.get('Id', ''))):
        return None
    return info


_COPY_CHMOD = re.compile(r'^COPY\s+--chmod=(?P<mode>[0-7]{3,4})\s+(?P<rest>.+?)\s+(?P<destination>\S+)\s*$', re.MULTILINE)


def _legacy_builder_dockerfile(text):
    """Rewrite `COPY --chmod=M src dst` to `COPY src dst` + `RUN chmod -R M dst`
    for a docker CLI without BuildKit. Equivalent for the copied tree."""
    return _COPY_CHMOD.sub(lambda m: 'COPY ' + m.group('rest') + ' ' + m.group('destination')
                           + '\nRUN chmod -R ' + m.group('mode') + ' ' + m.group('destination'), text)


# ---------------------------------------------------------------------------
# build

def build_images(runtime, parameters, execution_id, timeout, cancel_event):
    from profile_manager import antithesis_preparation as prep
    from profile_manager.antithesis_image_build import _plan
    from profile_manager.antithesis_package import PackageError
    if parameters.get('execution_id', execution_id) != execution_id:
        raise ValueError('Build execution identity differs')
    job = _Job(runtime, execution_id, 'build', timeout, cancel_event, stage='image-build')
    preparation_id = parameters['preparation_id']
    job.progress(status='building', execution_id=execution_id, controller_installation_id=runtime.installation_id,
                 preparation_id=preparation_id, stage='read-preparation', current_service=None, started_at=_now())
    preparations = os.environ.get(PREPARATIONS_ENV, '').strip()
    if not preparations:
        raise job.blocked('build-source-invalid', 'read-preparation', PREPARATIONS_ENV + ' is not configured on the controller')
    try:
        prepared = prep.read_preparation(preparation_id, state_dir=preparations)
        source = prepared['receipt']
        if source['source']['kind'] != 'build':
            raise job.blocked('build-source-invalid', 'read-preparation', 'only DWARF-generated packages are built here')
        files, _ = prep._unpack(base64.b64decode(prepared['archive_base64']))
        compose, plan = _plan(files)
    except PackageError as exc:
        raise job.blocked('build-source-invalid', 'read-preparation', str(exc)) from exc
    package = job.directory / 'package'
    prep._write_tree(package, files)
    rows = []
    try:
        job.progress(stage='node-artifacts')
        for artifact in (source.get('version_binding') or {}).get('node_artifacts', {}).values():
            reference, image_id = artifact['reference'], artifact['image_id']
            probe = job.run(('image', 'inspect', reference), log='node-' + artifact['implementation'] + '.log', ceiling=30)
            try:
                observed = json.loads(probe.stdout)[0].get('Id') if probe.returncode == 0 else None
            except (ValueError, IndexError, TypeError, AttributeError):
                observed = None
            if observed != image_id:
                raise job.blocked('artifact-image-missing', 'node-artifacts',
                                  'prepared node image ' + reference + ' is missing or changed; adapt the prepared build again')
            info = _inspect_amd64(job, image_id, 'node-' + artifact['implementation'] + '-platform.log')
            if info is None:
                raise job.blocked('artifact-image-missing', 'node-artifacts', 'prepared node image ' + reference + ' does not provide linux/amd64')
            for name, service in compose['services'].items():
                if isinstance(service, dict) and service.get('image') == reference:
                    rows.append({'service': name, 'reference': reference, 'image_id': info['Id'], 'artifact_image_id': image_id,
                                 'artifact_id': artifact['id'], 'platform': 'linux/amd64', 'publication': 'local-only'})
        legacy_builder = False
        if plan:
            # The controller image's docker CLI may lack buildx; the legacy
            # builder cannot do `COPY --chmod`, so the controller's own copy of
            # the Dockerfile is rewritten to COPY + RUN chmod (the package the
            # dashboard keeps is untouched). release_builds does the same for
            # Cardano images.
            probe = job.run(('buildx', 'version'), log='buildx.log', ceiling=15)
            legacy_builder = probe.returncode != 0
            job.progress(legacy_builder=legacy_builder)
        for index, item in enumerate(plan):
            name = item['service']
            job.progress(stage='build', current_service=name)
            reference = 'dwarf-antithesis-' + execution_id.rsplit('-', 1)[-1] + '-' + str(index) + ':prepared'
            context = package / item['context']
            dockerfile = context / item['dockerfile']
            if legacy_builder:
                dockerfile.write_text(_legacy_builder_dockerfile(dockerfile.read_text()))
            result = job.run(('build', '--platform', 'linux/amd64', '--tag', reference, '--file', str(dockerfile), str(context)),
                             log='build-' + str(index) + '.log', ceiling=BUILD_CEILING)
            if result.returncode:
                raise job.blocked('image-build-failed', 'build', name + ': ' + (result.stderr or result.stdout)[-_DETAIL_LIMIT:])
            info = _inspect_amd64(job, reference, 'image-' + str(index) + '.log')
            if info is None:
                raise job.blocked('image-build-failed', 'build', name + ': built image is not a linux/amd64 image')
            rows.append({'service': name, 'reference': reference, 'image_id': info['Id'], 'platform': 'linux/amd64', 'publication': 'local-only'})
        result = {'status': 'built', 'preparation_id': preparation_id, 'built_images': rows, 'build_host': 'DWARF controller',
                  'build_count': len(plan), 'legacy_builder': legacy_builder}
        job.progress(status='built', stage='complete', current_service=None, completed_at=_now(), result=result)
        return result
    except DockerRuntimeError:
        raise
    except BaseException as exc:
        job.progress(status='failed', stage='error', detail=_detail(exc))
        raise


# ---------------------------------------------------------------------------
# publish

def _verified_staged_secret(runtime, secret_id):
    """The staged registry credential as a private path, or a bounded error."""
    root = Path(getattr(runtime, 'version_imports_dir', None) or os.environ.get('ADA2_DWARF_VERSION_IMPORTS_DIR', ''))
    if not _SECRET_ID.fullmatch(str(secret_id)) or not root:
        raise DockerRuntimeError('registry credential reference is invalid', code='registry-secret-invalid', stage='image-publish')
    nofollow = getattr(os, 'O_NOFOLLOW', 0)
    directory = root / secret_id
    try:
        opened = os.open(directory, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | nofollow)
    except OSError as exc:
        raise DockerRuntimeError('registry credential is not staged', code='registry-secret-invalid', stage='image-publish') from exc
    try:
        if os.fstat(opened).st_uid not in {0, os.geteuid()}:
            raise DockerRuntimeError('registry credential is not owned by DWARF', code='registry-secret-invalid', stage='image-publish')
        try:
            descriptor_fd = os.open('descriptor.json', os.O_RDONLY | nofollow, dir_fd=opened)
        except OSError as exc:
            raise DockerRuntimeError('registry credential descriptor is missing', code='registry-secret-invalid', stage='image-publish') from exc
        try:
            descriptor = json.loads(os.read(descriptor_fd, 16 * 1024).decode('utf-8'))
        except (OSError, ValueError) as exc:
            raise DockerRuntimeError('registry credential descriptor is invalid', code='registry-secret-invalid', stage='image-publish') from exc
        finally:
            os.close(descriptor_fd)
        if not isinstance(descriptor, dict) or descriptor.get('kind') != 'ghcr-publish-pat' or descriptor.get('secret_id') != secret_id:
            raise DockerRuntimeError('registry credential descriptor is invalid', code='registry-secret-invalid', stage='image-publish')
        try:
            expires = datetime.fromisoformat(str(descriptor.get('expires_at')).replace('Z', '+00:00'))
        except ValueError as exc:
            raise DockerRuntimeError('registry credential descriptor is invalid', code='registry-secret-invalid', stage='image-publish') from exc
        if expires <= datetime.now(timezone.utc):
            raise DockerRuntimeError('registry credential expired; enter the PAT again', code='registry-secret-invalid', stage='image-publish')
        try:
            info = os.stat('secret', dir_fd=opened, follow_symlinks=False)
        except OSError as exc:
            raise DockerRuntimeError('registry credential is missing', code='registry-secret-invalid', stage='image-publish') from exc
        if not stat.S_ISREG(info.st_mode) or info.st_size == 0 or info.st_size > _SECRET_LIMIT or info.st_uid not in {0, os.geteuid()}:
            raise DockerRuntimeError('registry credential is invalid', code='registry-secret-invalid', stage='image-publish')
    finally:
        os.close(opened)
    return directory / 'secret'


def publish_images(runtime, parameters, execution_id, timeout, cancel_event):
    if parameters.get('execution_id', execution_id) != execution_id:
        raise ValueError('Publish execution identity differs')
    job = _Job(runtime, execution_id, 'publish', timeout, cancel_event, stage='image-publish')
    images = list(parameters['images'])
    job.progress(status='publishing', execution_id=execution_id, controller_installation_id=runtime.installation_id,
                 handoff_id=parameters['handoff_id'], registry='ghcr.io', stage='credential',
                 destinations=[row['destination'] for row in images], started_at=_now())
    try:
        secret = _verified_staged_secret(runtime, parameters['secret_id'])
    except DockerRuntimeError as exc:
        job.progress(status='failed', stage='credential', detail=_detail(exc))
        raise
    config = tempfile.mkdtemp(prefix='dwarf-ghcr-', dir='/tmp')
    os.chmod(config, 0o700)
    pushed = []
    try:
        job.progress(stage='login')
        login = job.run(('--config', config, 'login', 'ghcr.io', '-u', parameters['username'], '--password-stdin'),
                        log='login.log', ceiling=120, stdin_path=secret)
        # Never copy the login output anywhere: a rejected token is echoed by some daemons.
        (job.directory / 'login.log').write_text('login exit ' + str(login.returncode) + '\n')
        if login.returncode:
            raise job.blocked('registry-login-failed', 'login', 'ghcr.io rejected the login for ' + parameters['username'])
        for index, row in enumerate(images):
            reference, image_id, destination = row['reference'], row['image_id'], row['destination']
            job.progress(stage='push', current_image=destination)
            info = _inspect_amd64(job, reference, 'source-' + str(index) + '.log')
            if info is None or info['Id'] != image_id:
                raise job.blocked('image-tag-failed', 'push', reference + ' is missing or changed; rebuild the package images')
            tagged = job.run(('tag', reference, destination), log='tag-' + str(index) + '.log', ceiling=60)
            if tagged.returncode:
                raise job.blocked('image-tag-failed', 'push', 'docker tag failed for ' + destination)
            push = job.run(('--config', config, 'push', destination), log='push-' + str(index) + '.log', ceiling=PUSH_CEILING)
            if push.returncode:
                raise job.blocked('image-push-failed', 'push', destination + ': ' + (push.stderr or push.stdout)[-_DETAIL_LIMIT:])
            pushed.append({'destination': destination, 'image_id': image_id, 'pushed_at': _now()})
        result = {'status': 'published', 'handoff_id': parameters['handoff_id'], 'registry': 'ghcr.io', 'pushed': pushed}
        job.progress(status='published', stage='complete', current_image=None, completed_at=_now(), result=result)
        return result
    except DockerRuntimeError:
        raise
    except BaseException as exc:
        job.progress(status='failed', stage='error', detail=_detail(exc))
        raise
    finally:
        shutil.rmtree(config, ignore_errors=True)


# ---------------------------------------------------------------------------
# progress / cleanup

def _read_progress(runtime, execution_id, kind):
    path = _directory(runtime, execution_id, kind) / 'progress.json'
    if not path.exists():
        return None
    progress = builds._json(path)
    if progress.get('execution_id') != execution_id or progress.get('controller_installation_id') != runtime.installation_id:
        raise ValueError('Retained job identity differs')
    return progress


def read_build_progress(runtime, execution_id):
    return _read_progress(runtime, execution_id, 'build')


def read_publish_progress(runtime, execution_id):
    return _read_progress(runtime, execution_id, 'publish')


def cleanup_build(runtime, execution_id):
    """Builds leave no containers; drop the unpacked package context."""
    package = _directory(runtime, execution_id, 'build') / 'package'
    if package.is_dir() and not package.is_symlink():
        shutil.rmtree(package, ignore_errors=True)


def cleanup_publish(runtime, execution_id):
    """Pushes leave no containers; the temporary docker config is removed inline."""
    _directory(runtime, execution_id, 'publish')
