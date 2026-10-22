"""Explicit validation of saved Antithesis packages; no workload execution or submission."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import threading
import time

from profile_manager import antithesis_preparation as prep
from profile_manager import antithesis_image_build as build
from profile_manager import antithesis_publication as publication
from profile_manager import antithesis_preflight as preflight
from profile_manager.antithesis_publication_network import verify_image

# Pinned User.Agent.PushTest.dockerfile, not a supplied Dockerfile.
CONFIG_DOCKERFILE = '''FROM docker.io/debian:stable-slim AS build
ENV TZ="UTC"
RUN ln -snf /usr/share/zoneinfo/${TZ} /etc/localtime && echo ${TZ} > /etc/timezone
COPY . /
RUN sed -i 's/${INTERNAL_NETWORK}/false/g' /docker-compose.yaml
'''
_ACTIVE = {'queued','running'}
_SYSTEM_ROOTS = {'bin','boot','dev','etc','home','lib','lib32','lib64','libx32','media','mnt','opt','proc','root','run','sbin','srv','sys','tmp','usr','var'}


def state_directory():
    return prep.state_directory().parent/'validations'


def _id(value):
    if not isinstance(value,str) or not prep._ID.fullmatch(value):
        raise prep.PackageError('Invalid validation or snapshot ID.',status=400)
    return value


def read_validation(identifier):
    directory=state_directory()/_id(identifier)
    try: result=json.loads((directory/'status.json').read_text())
    except FileNotFoundError as exc: raise prep.PackageError('Validation was not found.',status=404) from exc
    if result['id']!=identifier:raise prep.PackageError('Stored validation identity changed.',status=409)
    if result['state'] in _ACTIVE:
        lock=build._lock(directory/'worker.lock')
        if lock is not None:
            try:
                result=json.loads((directory/'status.json').read_text())
                if result['state'] in _ACTIVE:
                    result.update(state='interrupted',message='Validation worker stopped. No completion is established; inspect the saved job before explicitly checking again.')
            finally:lock.close()
    return result


def start_validation(source,mode,request_id):
    identifier=_id(request_id)
    prep._shape(source,{'kind','id'},'source')
    if source['kind'] not in ('preparation','build','publication') or mode not in ('static','artifacts'):
        raise prep.PackageError('Select a saved package and static or artifacts validation.',status=400)
    _id(source['id'])
    root=state_directory();root.mkdir(parents=True,exist_ok=True,mode=0o700)
    def recover():
        if not (root/identifier/'status.json').exists():return None
        previous=read_validation(identifier)
        if previous['source']!=source or previous['mode']!=mode:
            raise prep.PackageError('Validation ID belongs to different inputs.',status=409)
        return previous
    previous=recover()
    if previous is not None:return previous
    lock=build._lock(root/'.validation.lock')
    if lock is None:raise prep.PackageError('Another package validation is active. Retry after it finishes.',status=409)
    worker_lock=None
    try:
        previous=recover()
        if previous is not None:
            lock.close();return previous
        reader={'preparation':prep.read_preparation,'build':build.read_build_result,'publication':publication.read_result}[source['kind']]
        result=reader(source['id'])
        files,_=prep._unpack(base64.b64decode(result['archive_base64']))
        directory=root/identifier;directory.mkdir(mode=0o700)
        worker_lock=build._lock(directory/'worker.lock')
        status=dict(schema_version=1,id=identifier,source=source,mode=mode,state='queued',
                    created_at=datetime.now(timezone.utc).isoformat(),execution_ready=False,submission_ready=False,
                    message='Checking the saved package.',report=None)
        build._save(directory/'status.json',status)
        threading.Thread(target=_worker,args=(directory,status.copy(),files,lock,worker_lock),daemon=True).start()
        return status
    except Exception:
        if worker_lock:worker_lock.close()
        lock.close();raise


def _worker(directory,status,files,lock,worker_lock):
    try:
        status.update(state='running');build._save(directory/'status.json',status)
        report=preflight.inspect_package(files)
        status['report']=report
        build._save(directory/'status.json',status)
        if status['mode']=='artifacts':
            inspect_images(directory,report)
            if report['state']=='blocked':
                preflight.add_check(report,'C01','candidate-image','not_checked','Fix static package failures before building the config-image candidate.')
            else:inspect_candidate(directory,files,report,status['id'])
        else:
            preflight.add_check(report,'C02','image-inspection','not_checked','Use Inspect images/config image for local image metadata and anonymous GHCR checks.')
            preflight.add_check(report,'C01','candidate-image','not_checked','Candidate config-image contents have not been inspected.')
        preflight.finish(report)
        status.update(state='complete',message='Package checks complete. Review failures and unchecked requirements; no test was submitted.')
    except Exception:
        # Untrusted files and provider/CLI failures must never leak credentials.
        status.update(state='failed',message='Package validation could not finish. Check the private job logs on the Dwarf host; no test was submitted.')
    finally:
        try:
            status['finished_at']=datetime.now(timezone.utc).isoformat()
            build._save(directory/'status.json',status)
        finally:worker_lock.close();lock.close()


def inspect_images(directory,report):
    cache={};public_cache={};deadline=time.monotonic()+600
    for index,row in enumerate(report['images']):
        reference=row['reference'];location='services.'+row['service']
        if time.monotonic()>deadline:
            preflight.add_check(report,'C02','image-inspection','not_checked','Image inspection time budget exhausted.',location);continue
        if reference not in cache:
            try:
                raw=build._docker(['docker','image','inspect','--platform','linux/amd64',reference],cwd=directory,
                                  log_path=directory/f'local-image-{index}.log',timeout=30)
                info=json.loads(raw)[0]
                valid=info.get('Os')=='linux' and info.get('Architecture')=='amd64'
                identity=info.get('Id','')
                if not re.fullmatch(r'sha256:[a-f0-9]{64}',identity):raise ValueError('invalid identity')
                cache[reference]=('pass' if valid else 'fail','Local image metadata is Linux/amd64.' if valid else 'Local image has a different OS/architecture.',identity)
            except Exception:
                cache[reference]=('not_checked','Local Linux/amd64 image metadata unavailable. No service image was pulled.',None)
        state,detail,identity=cache[reference]
        preflight.add_check(report,'C02','local-image-platform',state,detail,location)
        row['local_image_id']=identity
        if not reference.startswith('ghcr.io/'):
            preflight.add_check(report,'C03','public-image','not_checked','Anonymous verification currently supports GHCR. Local presence does not prove public access.',location);continue
        if reference not in public_cache:
            try:public_cache[reference]=verify_image(reference)
            except Exception:public_cache[reference]=None
        public=public_cache[reference]
        row['public']=public
        preflight.add_check(report,'C03','public-image','pass' if public else 'fail',
            'Anonymous Linux/amd64 manifest/config verified; layers and runtime remain unchecked.' if public
            else 'Anonymous GHCR verification failed. Check visibility, reference, platform and provider availability.',location)
        if public and identity and identity not in (public.get('config_digest'),public.get('amd64_manifest_digest')):
            preflight.add_check(report,'C15','image-drift','warn','Local image differs from the current public reference. Pin the intended digest before delivery.',location)


def inspect_candidate(directory,files,report,identifier):
    """Build pinned MOOG recipe and compare files using a never-started container."""
    if any(name.split('/')[0] in _SYSTEM_ROOTS for name in files):
        preflight.add_check(report,'C01','candidate-context','fail','Package would overwrite config-image system paths before MOOG executes sed. Move assets under a dedicated relative directory.')
        return
    context=directory/'candidate-context';prep._write_tree(context,files)
    # Validate the effective Compose without importing host environment values.
    schema_file=directory/'effective-compose.yaml'
    schema_file.write_bytes(files['docker-compose.yaml'][0].replace(b'${INTERNAL_NETWORK}',b'false'))
    report['checks']=[r for r in report['checks'] if r['code']!='compose-schema']
    try:
        build._docker(['docker','compose','--project-name','dwarf-antithesis-check','--env-file','/dev/null',
                       '--project-directory',str(context),'--file',str(schema_file),'config',
                       '--quiet','--no-interpolate','--no-env-resolution'],cwd=context,
                      log_path=directory/'compose-schema.log',timeout=30)
    except build.BuildFailure:
        preflight.add_check(report,'C01','compose-schema','fail','Docker Compose config check failed. Inspect the private diagnostic for invalid configuration or unavailable CLI support.')
        return
    except OSError:
        preflight.add_check(report,'C01','compose-schema','not_checked','Docker Compose is unavailable; candidate inspection was not attempted.')
        preflight.add_check(report,'C01','candidate-image','not_checked','Candidate inspection requires a successful Compose config check.')
        return
    preflight.add_check(report,'C01','compose-schema','pass','Docker Compose config accepted the effective file without environment interpolation or service env-file resolution.')
    dockerfile=directory/'MOOG-config.Dockerfile';dockerfile.write_text(CONFIG_DOCKERFILE)
    tag='dwarf-antithesis-config-check:'+identifier
    name='dwarf-antithesis-check-'+identifier
    report['candidate']={'reference':tag,'container_name':name,'moog_source_commit':preflight.MOOG_COMMIT,
                         'base_reference':'docker.io/debian:stable-slim','base_identity':'not_checked',
                         'recipe_sha256':prep._sha(CONFIG_DOCKERFILE.encode()),'platform':'linux/amd64',
                         'scope':'Local candidate only; actual remote MOOG image remains unverified.',
                         'cleanup':{}}
    candidate=report['candidate'];deadline=time.monotonic()+600
    attempted=False;created=False
    def docker(args,label,timeout=30):
        remaining=deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('candidate inspection budget exhausted')
        return build._docker(['docker',*args],cwd=context,log_path=directory/(label+'.log'),timeout=min(timeout,remaining))
    try:
        attempted=True
        docker(['build','--platform','linux/amd64','--network=none','--tag',tag,'--file',str(dockerfile),'.'],'candidate-build',300)
        info=json.loads(docker(['image','inspect',tag],'candidate-image'))[0]
        if info.get('Os')!='linux' or info.get('Architecture')!='amd64' or not re.fullmatch(r'sha256:[a-f0-9]{64}',info.get('Id','')):
            preflight.add_check(report,'C02','candidate-platform','fail','Candidate image metadata is not Linux/amd64.');return
        candidate['image_id']=info['Id']
        preflight.add_check(report,'C15','candidate-base','not_checked','Final candidate image identity is retained; the mutable Debian base digest and actual remote build identity are not established.')
        # Mark before create so a lost response still triggers cleanup of our unique name.
        created=True
        docker(['create','--platform','linux/amd64','--network','none','--name',name,info['Id'],'/bin/true'],'candidate-create')
        copied=directory/'candidate-readback';copied.mkdir(mode=0o700)
        compared=[]
        for index,(path,(data,mode)) in enumerate(sorted(files.items())):
            target=copied/str(index)
            try:
                docker(['cp',name+':/'+path,str(target)],f'candidate-copy-{index}')
                expected=data.replace(b'${INTERNAL_NETWORK}',b'false') if path=='docker-compose.yaml' else data
                if data is None:
                    matches=not target.is_symlink() and target.is_dir() and target.stat().st_mode & 0o777==mode
                else:
                    matches=(not target.is_symlink() and target.is_file() and target.stat().st_size==len(expected)
                             and target.read_bytes()==expected and target.stat().st_mode & 0o777==mode)
            except (OSError,build.BuildFailure):matches=False
            if not matches:
                preflight.add_check(report,'C01','candidate-files','fail','Candidate file is missing or differs in bytes/mode (including Docker ignore rules).',path)
            else:compared.append({'path':path,'kind':'directory' if data is None else 'file','sha256':None if data is None else prep._sha(expected),'mode':mode})
            if target.is_dir() and not target.is_symlink():shutil.rmtree(target)
        candidate['files']=compared
        if not any(r['code']=='candidate-files' and r['state']=='fail' for r in report['checks']):
            preflight.add_check(report,'C01','candidate-files','pass','All packaged files and directory modes match stopped-image readback after the pinned MOOG Compose substitution.')
    except Exception:
        preflight.add_check(report,'C01','candidate-image','not_checked','Candidate build/inspection did not complete. Check Docker and private job logs; this is not a packaging pass.')
    finally:
        for label,args,needed in [('container',['rm','-f',name],created),('image',['image','rm',tag],attempted)]:
            if not needed:continue
            try:
                build._docker(['docker',*args],cwd=context,log_path=directory/('cleanup-'+label+'.log'),timeout=30)
                candidate['cleanup'][label]='removed'
            except Exception:
                candidate['cleanup'][label]='unconfirmed'
                preflight.add_check(report,'C01','candidate-cleanup','warn','Temporary '+label+' cleanup was not confirmed; inspect the saved candidate identity.')
