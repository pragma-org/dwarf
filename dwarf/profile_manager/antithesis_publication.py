"""Explicit publication handoff for generated Antithesis builds. Never submits."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
import os
import tempfile
import threading
import time
import uuid
from pathlib import Path

import yaml

from profile_manager import antithesis_image_build as build
from profile_manager import antithesis_preparation as prep
from profile_manager.antithesis_package import PackageError
from profile_manager.antithesis_publication_network import REPOSITORY, validate_target, verify_image, verify_git


def state_directory():return prep.state_directory().parent/'publications'
def _now():return datetime.now(timezone.utc).isoformat()


def _id(value,field):
    if not isinstance(value,str) or not prep._ID.fullmatch(value):
        raise PackageError('Invalid publication identity.',field=field)
    return value


def _directory(identifier):return state_directory()/_id(identifier,'handoff_id')


def read_handoff(identifier):
    try:receipt=json.loads((_directory(identifier)/'receipt.json').read_text())
    except FileNotFoundError as exc:raise PackageError('Publication handoff was not found.',status=404) from exc
    if receipt['id']!=identifier:raise PackageError('Stored publication identity changed.',status=409)
    return receipt


def create_handoff(build_id,registry_prefix,*,request_id=None):
    identifier=_id(request_id if request_id is not None else uuid.uuid4().hex,'request_id')
    if (not isinstance(registry_prefix,str) or len(registry_prefix)>180 or not registry_prefix.startswith('ghcr.io/')
            or not REPOSITORY.fullmatch(registry_prefix.removeprefix('ghcr.io/'))):
        raise PackageError('Use a lowercase GHCR repository prefix, such as ghcr.io/owner/dwarf.',status=422)
    root=state_directory();root.mkdir(parents=True,exist_ok=True,mode=0o700)
    lock=build._lock(root/'.create.lock')
    if lock is None:raise PackageError('Another handoff is being saved. Retry.',status=409)
    try:
        directory=root/identifier
        if directory.exists():
            existing=read_handoff(identifier)
            if existing['build_id']!=build_id or existing['registry_prefix']!=registry_prefix:
                raise PackageError('Request ID belongs to a different publication handoff.',status=409)
            return existing
        result=build.read_build_result(build_id);source=result['receipt']
        if source['source']['kind']!='build':raise PackageError('Publication requires a saved generated build.',status=422)
        files,_=prep._unpack(base64.b64decode(result['archive_base64']))
        images=[]
        for row in source['built_images']:
            service=row['service'].lower().replace('_','-')
            destination=f'{registry_prefix}-{service}:{identifier}'
            images.append(dict(row,destination=destination))
        if len({r['destination'] for r in images})!=len(images):
            raise PackageError('Generated service names collide at the registry destination.',status=422)
        receipt={'schema_version':1,'id':identifier,'build_id':build_id,'preparation_id':source['preparation_id'],
                 'created_at':_now(),'registry_prefix':registry_prefix,'state':'planned','images':images,
                 'request':source['request'],'version_binding':source['version_binding'],
                 'workload_binding':source['workload_binding'], 'measurement_binding':source.get('measurement_binding',{}),'source_inventory':prep._inventory(files),
                 'source_issues':source['issues'],
                 'limitations':['Static contract review, fresh local validation and exact candidate config-image inspection remain separate gates.',
                     'Anonymous manifests/configs do not establish layer retrieval, binary behavior or remote qualification.'],
                 'push_attempted':False,'execution_ready':False,'submission_ready':False,
                 'message':'Review destinations, then push images explicitly. Make them public before verification.'}
        with tempfile.TemporaryDirectory(prefix='.handoff-',dir=root) as temporary:
            staging=Path(temporary)
            prep._write_tree(staging/'source',files)
            (staging/'operations').mkdir(mode=0o700)
            build._save(staging/'receipt.json',receipt)
            os.rename(staging,directory)
        return receipt
    finally:lock.close()


def _files(directory,receipt,kind):
    files=prep._read_tree(directory/kind)
    expected=receipt['source_inventory'] if kind=='source' else receipt.get('inventory')
    if not expected or prep._inventory(files)!=expected:
        raise PackageError('Saved publication package changed. Create a new handoff.',status=409)
    return files


def read_result(identifier):
    receipt=read_handoff(identifier)
    if not receipt.get('inventory'):raise PackageError('Public images have not been verified.',status=409)
    files=_files(_directory(identifier),receipt,'package')
    result=prep._result(receipt,files);result['archive_name']='antithesis-public-'+identifier+'.zip'
    return result


def read_operation(identifier,operation_id):
    directory=_directory(identifier)/'operations'/_id(operation_id,'operation_id')
    try:status=json.loads((directory/'status.json').read_text())
    except FileNotFoundError as exc:raise PackageError('Publication operation was not found.',status=404) from exc
    if status['id']!=operation_id or status['handoff_id']!=identifier:
        raise PackageError('Publication operation identity changed.',status=409)
    if status['state'] in ('queued','running'):
        lock=build._lock(directory/'worker.lock')
        if lock:
            lock.close();status=json.loads((directory/'status.json').read_text())
            if status['state'] in ('queued','running'):
                status.update(state='interrupted',message='Worker stopped. A push may have partially completed; verify public images before creating another handoff.')
    return status


def start_operation(identifier,action,*,target=None,request_id=None):
    directory=_directory(identifier);read_handoff(identifier)
    operation_id=_id(request_id if request_id is not None else uuid.uuid4().hex,'request_id')
    if action not in ('push','verify-images','verify-git'):raise PackageError('Unknown publication operation.')
    if action=='verify-git':validate_target(target)
    elif target is not None:raise PackageError('This operation does not accept a Git target.')
    def recover():
        if not (directory/'operations'/operation_id/'status.json').exists():return None
        previous=read_operation(identifier,operation_id)
        if previous['action']!=action or previous['target']!=target:
            raise PackageError('Operation ID belongs to different inputs.',status=409)
        return previous
    previous=recover()
    if previous:return previous
    lock=build._lock(directory/'operation.lock')
    if lock is None:raise PackageError('Another publication operation is active.',status=409)
    worker_lock=None
    try:
        previous=recover()
        if previous:lock.close();return previous
        receipt=read_handoff(identifier)
        _files(directory,receipt,'source')
        if action=='push' and receipt['push_attempted']:
            raise PackageError('A push was already attempted. Verify public images; use a new handoff for an explicit new push.',status=409)
        if action=='verify-git':_files(directory,receipt,'package')
        op=directory/'operations'/operation_id;op.mkdir(mode=0o700)
        worker_lock=build._lock(op/'worker.lock')
        status={'id':operation_id,'handoff_id':identifier,'action':action,'target':target,'state':'queued',
                'created_at':_now(),'message':'Publication operation queued.','submission_ready':False}
        build._save(op/'status.json',status)
        # Persist before any external mutation; even a crash cannot silently replay a push.
        if action=='push':receipt['push_attempted']=True
        receipt.pop('request_plan',None);receipt.pop('git',None)
        receipt.update(state='working',message='Publication operation in progress.')
        build._save(directory/'receipt.json',receipt)
        threading.Thread(target=_worker,args=(directory,op,status.copy(),receipt,lock,worker_lock),daemon=True).start()
        return status
    except Exception:
        if worker_lock:worker_lock.close()
        lock.close();raise


def _push(directory,op,receipt,progress):
    for index,row in enumerate(receipt['images']):
        progress('Inspecting and pushing '+row['service']+'.')
        def docker(argv,label):return build._docker(['docker',*argv],cwd=directory,
            log_path=op/f'{index}-{label}.log',timeout=600 if label=='push' else 30)
        def inspect(reference,label):
            info=json.loads(docker(['image','inspect','--platform','linux/amd64',reference],label))[0]
            if info.get('Id')!=row['image_id'] or info.get('Os')!='linux' or info.get('Architecture')!='amd64':
                raise PackageError('Local image identity changed; build again before publication.',status=409)
        inspect(row['reference'],'source')
        docker(['tag',row['reference'],row['destination']],'tag')
        inspect(row['destination'],'destination')
        docker(['push',row['destination']],'push')
        row['push_completed']=True
        build._save(directory/'receipt.json',receipt)
    receipt.update(state='awaiting-visibility',message='Push completed. Make the GHCR packages public, then verify public images.')


def _verify_images(directory,receipt,progress):
    files=_files(directory,receipt,'source')
    compose=yaml.safe_load(files['docker-compose.yaml'][0]);cache={};rows={r['service']:r for r in receipt['images']}
    deadline=time.monotonic()+600
    for name,service in compose['services'].items():
        if time.monotonic()>deadline:raise PackageError('Public image verification timed out. Retry the read-only check.',status=504)
        row=rows.get(name);reference=(row.get('public',{}).get('reference') or row['destination']) if row else service.get('image')
        if not reference or 'build' in service:raise PackageError('Every public service must use an image without build directives.',status=422)
        progress('Verifying public image for '+name+'.')
        key=(reference,row['image_id'] if row else None)
        if key not in cache:cache[key]=verify_image(reference,key[1])
        # The Antithesis image-parameter path accepts repository@digest;
        # normalize verified catalog images as well as locally built images.
        service['image']=cache[key]['reference']
        if row:
            row['public']=cache[key]
    # Keep inspection provenance private; publish only the generated runtime tree.
    files={name:entry for name,entry in files.items() if name!='provenance' and not name.startswith('provenance/')}
    files['docker-compose.yaml']=(yaml.safe_dump(compose,sort_keys=False).encode(),files['docker-compose.yaml'][1])
    package=directory/'package'
    if package.exists():
        previous=_files(directory,receipt,'package')
        if previous!=files:raise PackageError('Public package identity changed; create a new handoff.',status=409)
    else:prep._write_tree(package,files)
    materialized=prep._read_tree(package)
    receipt.update(inventory=prep._inventory(materialized),public_images=list(cache.values()),verified_at=_now(),
                   state='images-public',message='Public manifests and configs verified. Commit the exported ZIP contents to the public GitHub directory.')


def _moog_settings():
    from profile_manager.config import load_config, DeploymentConfig
    from profile_manager.data.operate_config import moog_setup_payload
    try:config=load_config()
    except FileNotFoundError:config=DeploymentConfig.from_dict({})
    # Reuse the settings form's environment precedence; secrets are not needed by the plan.
    return {f['key']:f['value'] for f in moog_setup_payload(config)['fields'] if not f['secret']}


def _verify_git(directory,receipt,target,progress):
    files=_files(directory,receipt,'package')
    progress('Verifying the exact public Git commit and executable modes.')
    evidence=verify_git(target['repository'],target['commit'],target['directory'],files)
    from profile_manager.moog import build_moog_create_test_plan
    settings=_moog_settings()
    plan=build_moog_create_test_plan(settings,repo=target['repository'],github_user=target['requester'],
        directory=target['directory'],commit=target['commit'],try_number=target['try_number'],
        duration_hours=receipt['request']['duration_hours'],asset_dir=str(directory/'package'),
        no_faults=receipt['request']['faults']=='off')
    if not settings.get('enabled', True) or not settings.get('token_id') or str(settings['token_id']).startswith('REPLACE_'):
        plan['checks'].append({'id':'configured_moog','state':'error','detail':'Enable MOOG and save a real token ID in Dwarf settings.'})
        plan['state']='blocked'
    plan['scope']='Request preview only. Registration, repository whitelist, credentials, duplicate requests and remote qualification remain unchecked.'
    plan['submission_ready']=False
    plan['remaining_gates']=receipt['limitations']
    receipt.update(git=evidence,request_plan=plan,state='review',
                   message='Public Git assets match. Review the existing MOOG request and remaining checks; nothing was submitted.')


def _worker(directory,op,status,receipt,lock,worker_lock):
    def progress(message):
        status.update(state='running',message=message);build._save(op/'status.json',status)
    try:
        if status['action']=='push':_push(directory,op,receipt,progress)
        elif status['action']=='verify-images':_verify_images(directory,receipt,progress)
        else:_verify_git(directory,receipt,status['target'],progress)
        build._save(directory/'receipt.json',receipt)
        status.update(state='succeeded',message=receipt['message'])
    except (PackageError,build.BuildFailure) as exc:
        status.update(state='failed',message=str(exc))
    except Exception:
        status.update(state='failed',message='Publication operation failed. Check the saved operation and private host logs; no test was submitted.')
    finally:
        try:
            status['finished_at']=_now();build._save(op/'status.json',status)
        finally:worker_lock.close();lock.close()
