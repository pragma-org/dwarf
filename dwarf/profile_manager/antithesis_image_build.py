"""Build only saved, generated Antithesis inputs. No runtime, push or submission."""
from __future__ import annotations

import base64
from collections import deque
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import threading
import uuid

import yaml

from profile_manager import antithesis_preparation as prep
from profile_manager.antithesis_package import PackageError, _json

_ACTIVE = {'queued', 'building'}
_TERMINAL = {'built', 'not-required'}
BUILD_TIMEOUT = 1200
LOG_LIMIT = 256 * 1024


class BuildFailure(RuntimeError):
    pass


def state_directory():
    return prep.state_directory().parent / 'builds'


def _save(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        os.chmod(temporary, 0o600)
        stream.write(_json(value)); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)


def _lock(path):
    stream = path.open('a+')
    os.chmod(path, 0o600)
    try:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return stream
    except BlockingIOError:
        stream.close()
        return None


def _docker(argv, *, cwd, log_path, timeout):
    """Drain bounded output; terminate the CLI process group on timeout."""
    chunks, size = deque(), 0
    process = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, start_new_session=True)
    def drain():
        nonlocal size
        with process.stdout:
            while chunk := process.stdout.read(8192):
                chunks.append(chunk); size += len(chunk)
                while size > LOG_LIMIT:
                    size -= len(chunks.popleft())
    reader = threading.Thread(target=drain, daemon=True); reader.start()
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try: os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError: pass
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            process.wait()
    reader.join(timeout=5)
    output = b''.join(chunks)
    with log_path.open('wb') as stream:
        os.chmod(log_path, 0o600); stream.write(output)
    if timed_out:
        raise BuildFailure('Docker operation timed out. Partial images or build cache may remain on the build host.')
    if process.returncode:
        raise BuildFailure(f'Docker operation failed (exit {process.returncode}). See the private build log on the Dwarf host.')
    return output.decode('utf-8', errors='replace')


def _plan(files):
    if 'docker-compose.yaml' not in files:
        raise PackageError('Generated package needs root docker-compose.yaml.', status=422)
    prep._bound_compose(files['docker-compose.yaml'][0])
    compose = yaml.safe_load(files['docker-compose.yaml'][0])
    services = compose.get('services') if isinstance(compose, dict) else None
    if not isinstance(services, dict) or not services:
        raise PackageError('Generated Compose has no services.', status=422)
    builds = []
    for name, service in services.items():
        if not isinstance(service, dict):
            raise PackageError('Generated service must be a mapping.', status=422)
        if 'build' not in service:
            continue
        build = service['build']
        if (not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}', str(name))
                or not isinstance(build, dict) or set(build)-{'context','dockerfile'}):
            raise PackageError('Generated build uses unsupported options.', status=422)
        context = build.get('context', '.')
        dockerfile = build.get('dockerfile', 'Dockerfile')
        if not isinstance(context, str) or not isinstance(dockerfile, str):
            raise PackageError('Generated build paths must be strings.', status=422)
        context = '.' if context in ('.','./') else prep._name(context)
        dockerfile = prep._name(dockerfile)
        if '${' in context or '${' in dockerfile:
            raise PackageError('Generated build paths must be explicit.', status=422)
        filename = str(Path(context)/dockerfile)
        if filename not in files or files[filename][0] is None:
            raise PackageError('Generated Dockerfile is missing.', status=422)
        if service.get('platform', 'linux/amd64') != 'linux/amd64':
            raise PackageError('Generated build must target linux/amd64.', status=422)
        builds.append({'service':name, 'context':context, 'dockerfile':dockerfile})
    return compose, builds


def start_build(preparation_id, *, request_id=None):
    identifier = request_id if request_id is not None else uuid.uuid4().hex
    if not isinstance(identifier, str) or not prep._ID.fullmatch(identifier):
        raise PackageError('Invalid build request ID.', field='request_id')
    def recover():
        if not (state_directory()/identifier/'status.json').exists():
            return None
        previous = read_build(identifier)
        if previous['preparation_id'] != preparation_id:
            raise PackageError('Build request ID belongs to a different preparation.', status=409)
        return previous
    previous = recover()
    if previous is not None:
        return previous
    # Decode the exact inventory-verified bytes once; never build the live source tree.
    prepared = prep.read_preparation(preparation_id)
    source = prepared['receipt']
    if source['source']['kind'] != 'build':
        raise PackageError('Only Dwarf-generated snapshots can be built here.', status=422)
    files, _ = prep._unpack(base64.b64decode(prepared['archive_base64']))
    compose, builds = _plan(files)
    root = state_directory(); root.mkdir(parents=True, exist_ok=True, mode=0o700)
    global_lock = _lock(root/'.build.lock')
    if global_lock is None:
        raise PackageError('Another Antithesis image build is active. Retry after it finishes.', status=409)
    job_lock = None
    try:
        previous = recover()
        if previous is not None:
            global_lock.close()
            return previous
        directory = root/identifier; directory.mkdir(mode=0o700)
        job_lock = _lock(directory/'worker.lock')
        status = {'schema_version':1, 'id':identifier, 'preparation_id':preparation_id,
                  'state':'queued', 'created_at':datetime.now(timezone.utc).isoformat(),
                  'current_service':None, 'build_count':len(builds), 'built_images':[],
                  'execution_ready':False, 'submission_ready':False,
                  'message':'Waiting to build generated images.'}
        _save(directory/'status.json', status)
        worker = threading.Thread(target=_worker,
            args=(directory, status.copy(), source, files, compose, builds, global_lock, job_lock), daemon=True)
        worker.start()
        return status
    except Exception:
        if job_lock: job_lock.close()
        global_lock.close()
        raise


def _worker(directory, status, source, files, compose, builds, global_lock, job_lock):
    try:
        package = directory/'package'; prep._write_tree(package, files)
        # Prepared node images are built on this daemon and must join publication.
        for artifact in source.get('version_binding',{}).get('node_artifacts',{}).values():
            raw=_docker(['docker','image','inspect',artifact['reference']],cwd=package,
                        log_path=directory/('node-'+artifact['implementation']+'.log'),timeout=30)
            if json.loads(raw)[0].get('Id') != artifact['image_id']:
                raise BuildFailure('Prepared node image changed; prepare the node again.')
            # Docker's containerd store can report an OCI index for the original
            # artifact. Publication checks the selected platform image instead.
            # Resolve that child through the verified immutable parent identity.
            raw=_docker(['docker','image','inspect','--platform','linux/amd64',artifact['image_id']],
                        cwd=package,log_path=directory/('node-'+artifact['implementation']+'-platform.log'),timeout=30)
            info=json.loads(raw)[0]
            if (info.get('Os')!='linux' or info.get('Architecture')!='amd64'
                    or not re.fullmatch(r'sha256:[0-9a-f]{64}',info.get('Id',''))):
                raise BuildFailure('Prepared node image must provide Linux/amd64.')
            for name,service in compose['services'].items():
                if service.get('image')==artifact['reference']:
                    status['built_images'].append({'service':name,'reference':artifact['reference'],
                        'image_id':info['Id'],'artifact_image_id':artifact['image_id'],
                        'artifact_id':artifact['id'],'platform':'linux/amd64','publication':'local-only'})
        for index, build in enumerate(builds):
            name = build['service']
            status.update(state='building', current_service=name, message=f'Building {name} for linux/amd64.')
            _save(directory/'status.json', status)
            reference = f'dwarf-antithesis-{status["id"]}-{index}:prepared'
            context = package/build['context']
            _docker(['docker','build','--platform','linux/amd64','--tag',reference,
                     '--file',build['dockerfile'],'.'], cwd=context,
                    log_path=directory/f'build-{index}.log', timeout=BUILD_TIMEOUT)
            raw = _docker(['docker','image','inspect','--platform','linux/amd64',reference],
                          cwd=context, log_path=directory/f'image-{index}.log', timeout=30)
            info = json.loads(raw)[0]
            if (info.get('Os') != 'linux' or info.get('Architecture') != 'amd64'
                    or not re.fullmatch(r'sha256:[a-f0-9]{64}', info.get('Id',''))):
                raise BuildFailure('Built image metadata does not identify a Linux/amd64 image.')
            status['built_images'].append({'service':name,'reference':reference,'image_id':info['Id'],
                                           'platform':'linux/amd64','publication':'local-only'})
            compose['services'][name].pop('build')
            compose['services'][name]['image'] = reference
            _save(directory/'status.json', status)
        if builds:
            (package/'docker-compose.yaml').write_text(yaml.safe_dump(compose, sort_keys=False))
        final_files = prep._read_tree(package)
        compose_path, findings, images, remaining = prep._inspect(final_files, package)
        if remaining:
            raise BuildFailure('Built package still contains unresolved image builds.')
        superseded = {'image-build-required','workload-image-build-required',
                      'image-build-publication-deferred','publication-deferred'}
        issues = [i for i in source['issues'] if i['code'] not in superseded]
        issues.extend(i for i in findings if i not in issues)
        issues.append({'code':'publication-required','message':'Built images exist only on the Dwarf build host. Publication, runtime validation and MOOG submission remain separate.'
                       if builds else 'No generated image build was needed. Existing images and remote execution have not been verified.'})
        state = 'built' if builds else 'not-required'
        receipt = dict(source, id=status['id'], state=state, preparation_id=source['id'],
                       created_at=status['created_at'], inventory=prep._inventory(final_files),
                       compose_path=compose_path, images=images, build_requirements=[],
                       built_images=status['built_images'], issues=issues,
                       execution_ready=False, submission_ready=False)
        receipt['build_host'] = 'Dwarf dashboard Docker daemon'
        receipt['workload_binding'] = json.loads(json.dumps(source['workload_binding']))
        workload_image = receipt['workload_binding'].get('workload_image')
        if workload_image:
            matched = next((r for r in status['built_images'] if r['service']==workload_image.get('service')), None)
            if matched:
                workload_image.update(status='built-locally', **matched)
                receipt['workload_binding']['limitations'] = [
                    text.replace('Workload build, node bootstrap,', 'Node bootstrap,')
                    for text in receipt['workload_binding'].get('limitations', [])]

        _save(directory/'receipt.json', receipt)
        status.update(state=state, current_service=None,
                      message='Images built locally; package saved. Publication and remote validation remain.' if builds
                      else 'No image build was needed. Existing images were not built or verified.')
    except BuildFailure as exc:
        status.update(state='failed', message=str(exc))
    except Exception:
        # Raw subprocess/provider errors may contain credentials; retain only bounded private CLI logs.
        status.update(state='failed', message='Image preparation failed. Check Docker availability and the private build logs on the Dwarf host.')
    finally:
        try:
            status['finished_at'] = datetime.now(timezone.utc).isoformat()
            _save(directory/'status.json', status)
        finally:
            job_lock.close(); global_lock.close()


def read_build(identifier):
    if not isinstance(identifier,str) or not prep._ID.fullmatch(identifier):
        raise PackageError('Invalid build ID.', field='build_id')
    directory = state_directory()/identifier
    try:
        status = json.loads((directory/'status.json').read_text())
    except FileNotFoundError as exc:
        raise PackageError('Image build was not found.', field='build_id', status=404) from exc
    if status['id'] != identifier:
        raise PackageError('Stored build identity changed.', status=409)
    if status['state'] in _ACTIVE:
        probe = _lock(directory/'worker.lock')
        if probe is not None:
            probe.close()
            # Re-read after acquiring: the worker may have just completed.
            status = json.loads((directory/'status.json').read_text())
            if status['state'] in _ACTIVE:
                status.update(state='interrupted',message='Build worker stopped before recording completion. Images or cache may remain; retry explicitly.')
    return status


def read_build_result(identifier):
    status = read_build(identifier)
    if status['state'] not in _TERMINAL:
        raise PackageError('Image build is not complete.', field='build_id', status=409)
    directory = state_directory()/identifier
    receipt = json.loads((directory/'receipt.json').read_text())
    files = prep._read_tree(directory/'package')
    if receipt['id'] != identifier or prep._inventory(files) != receipt['inventory']:
        raise PackageError('Built package changed; build again from its preparation.', status=409)
    result = prep._result(receipt, files)
    result['archive_name'] = 'antithesis-built-'+identifier+'.zip'
    return result
