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
from profile_manager.topology_actions import TopologyActionError, controller_request
from profile_manager import antithesis_preparation as prep
from profile_manager.antithesis_package import PackageError
from profile_manager.antithesis_publication_network import REPOSITORY, validate_target, verify_image, verify_git, valid_registry_prefix, visibility_required, package_settings_urls


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
    if not valid_registry_prefix(registry_prefix):
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
                 'workload_image_contents':list((source.get('workload_binding') or {}).get('image_contents') or []),
                 'capabilities':source.get('capabilities'),
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


_POLL_SECONDS=2.0


def _load_config():
    from profile_manager.config import load_config, DeploymentConfig
    try:return load_config()
    except FileNotFoundError:return DeploymentConfig.from_dict({})


def _save_config(config):
    from profile_manager.config import save_config
    save_config(config)


def _saved_publish_token():
    """The configured GHCR publish PAT (environment first, like the settings form)."""
    from profile_manager.data.operate_config import MOOG_SETUP_FIELDS, _first_env_key
    meta=next(f for f in MOOG_SETUP_FIELDS if f['key']=='ghcr_publish_pat')
    env_key=_first_env_key(meta.get('env') or [],os.environ)
    if env_key:return os.environ.get(env_key) or ''
    from profile_manager.moog import normalize_moog_config
    return str(normalize_moog_config(getattr(_load_config(),'moog',None)).get('ghcr_publish_pat') or '')


def _persist_publish_token(token):
    from profile_manager.data.operate_config import apply_moog_setup_form
    _save_config(apply_moog_setup_form(_load_config(),{'ghcr_publish_pat':[token]},partial=True))


def start_operation(identifier,action,*,target=None,request_id=None,publish_token=None,save_token=False,controller=None):
    directory=_directory(identifier);read_handoff(identifier)
    operation_id=_id(request_id if request_id is not None else uuid.uuid4().hex,'request_id')
    if action not in ('push','verify-images','verify-git'):raise PackageError('Unknown publication operation.')
    if action=='verify-git':validate_target(target)
    elif target is not None:raise PackageError('This operation does not accept a Git target.')
    if action!='push' and (publish_token is not None or save_token):
        raise PackageError('Only a push takes a registry token.')
    if action=='push' and publish_token is not None and (not isinstance(publish_token,str) or not publish_token.strip() or len(publish_token)>4096):
        raise PackageError('Enter a GitHub PAT with write:packages or leave the field blank to use the saved one.',status=422)
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
    worker_lock=None;secret_id=None
    try:
        previous=recover()
        if previous:lock.close();return previous
        receipt=read_handoff(identifier)
        _files(directory,receipt,'source')
        if action=='push' and receipt['push_attempted']:
            raise PackageError('A push was already attempted. Verify public images; use a new handoff for an explicit new push.',status=409)
        token=None
        if action=='push':
            # Resolved only once this request is known to be a new push.
            token=(publish_token or '').strip() or _saved_publish_token()
            if not token:
                raise PackageError('Enter a GitHub PAT with write:packages or save one in settings (GHCR publish PAT).',status=422)
        if action=='verify-git':_files(directory,receipt,'package')
        op=directory/'operations'/operation_id;op.mkdir(mode=0o700)
        worker_lock=build._lock(op/'worker.lock')
        status={'id':operation_id,'handoff_id':identifier,'action':action,'target':target,'state':'queued',
                'created_at':_now(),'message':'Publication operation queued.','submission_ready':False}
        if action=='push':
            if save_token and publish_token:
                _persist_publish_token(token);status['token_saved']=True
            from profile_manager.binary_imports import stage_secret
            secret_id=stage_secret(token)
        build._save(op/'status.json',status)
        # Persist before any external mutation; even a crash cannot silently replay a push.
        if action=='push':receipt['push_attempted']=True
        receipt.pop('request_plan',None);receipt.pop('git',None)
        restore_state=receipt['state']
        receipt.update(state='working',message='Publication operation in progress.')
        build._save(directory/'receipt.json',receipt)
        threading.Thread(target=_worker,args=(directory,op,status.copy(),receipt,lock,worker_lock),
                         kwargs={'secret_id':secret_id,'controller':controller or controller_request,'restore_state':restore_state},daemon=True).start()
        return status
    except Exception:
        # A staged PAT must not outlive the request that staged it.
        if secret_id:
            from profile_manager.binary_imports import discard_secret
            discard_secret(secret_id)
        if worker_lock:worker_lock.close()
        lock.close();raise


def _push(directory,op,receipt,progress,*,secret_id,controller):
    """Log in, tag and push on the controller (the only process with Docker).
    The staged secret is referenced by id; the token itself never leaves the
    staging directory or reaches any row."""
    images=[{'reference':row['reference'],'image_id':row['image_id'],'destination':row['destination']} for row in receipt['images']]
    if not images:raise PackageError('This handoff has no images to push.',status=422)
    username=receipt['registry_prefix'].removeprefix('ghcr.io/').split('/')[0]
    execution_id='antithesis-publish-'+uuid.uuid4().hex
    progress('Logging in to ghcr.io and pushing '+str(len(images))+' image(s) from the controller.')
    try:
        controller('antithesis.image-publish',{'handoff_id':receipt['id'],'secret_id':secret_id,'username':username,
            'images':images,'execution_id':execution_id},3600,idempotency_key=execution_id)
    except TopologyActionError as exc:
        # Nothing was accepted, so nothing can replay: the handoff stays pushable.
        receipt['push_attempted']=False
        receipt.update(state='planned',message='Push could not start; the handoff can be pushed again.')
        raise PackageError('The controller did not accept the push: '+str(exc)) from exc
    # Recorded before polling so a lost status poll still leaves the operator the execution to recover.
    receipt['push_execution_id']=execution_id
    while True:
        try:observed=controller('execution.status',{'execution_id':execution_id},10)
        except TopologyActionError as exc:
            if str(exc)!='execution is not managed by this controller':raise
            observed={'execution_id':execution_id,'state':'interrupted'}
        if observed.get('execution_id')!=execution_id:raise PackageError('Publish status has a different execution identity.')
        if observed.get('state') in {'running','cancelling'}:
            stage=(observed.get('progress') or {}).get('current_image')
            if stage:progress('Pushing '+str(stage)+'.')
            if _POLL_SECONDS:time.sleep(_POLL_SECONDS)
            continue
        break
    if observed.get('state')=='applied':
        result=observed.get('result') or {}
        pushed={row.get('destination') for row in result.get('pushed') or [] if isinstance(row,dict)}
        if result.get('status')!='published' or result.get('handoff_id')!=receipt['id'] or pushed!={row['destination'] for row in images}:
            raise PackageError('Controller publish result differs from this handoff.')
        for row in receipt['images']:row['push_completed']=True
        receipt.update(push_execution_id=execution_id,state='awaiting-visibility',
                       message='Push completed. Make the GHCR packages public (one click per package name), then verify public images.')
        return
    # A failed push leaves nothing to replay: the handoff can be pushed again.
    receipt['push_attempted']=False
    receipt.update(state='planned',message='Push failed; the handoff can be pushed again.')
    progress_record=observed.get('progress') or {};failure=observed.get('failure') or {}
    raise PackageError(progress_record.get('detail') or failure.get('message') or ('Push '+str(observed.get('state'))+' on the controller.'))


def _verify_images(directory,receipt,progress):
    files=_files(directory,receipt,'source')
    compose=yaml.safe_load(files['docker-compose.yaml'][0]);cache={};rows={name:r for r in receipt['images'] for name in (r.get('services') or [r['service']])}
    deadline=time.monotonic()+600
    private=[]
    for name,service in compose['services'].items():
        if time.monotonic()>deadline:raise PackageError('Public image verification timed out. Retry the read-only check.',status=504)
        row=rows.get(name);reference=(row.get('public',{}).get('reference') or row['destination']) if row else service.get('image')
        if not reference or 'build' in service:raise PackageError('Every public service must use an image without build directives.',status=422)
        progress('Verifying public image for '+name+'.')
        key=(reference,row['image_id'] if row else None)
        if key not in cache:
            try:cache[key]=verify_image(reference,key[1])
            except PackageError as exc:
                # Only a pushed package can be "still private"; before a push a 404 means it does not exist.
                if not visibility_required(exc) or not receipt.get('push_attempted'):raise
                cache[key]=None   # the package exists but is still private; keep checking the rest
        if cache[key] is None:
            urls=package_settings_urls(reference)
            private.append({'service':name,'reference':reference,'package':urls['package'],'owner':urls['owner'],
                            'status':'private','urls':{'user':urls['user'],'org':urls['org']}})
            continue
        # The Antithesis image-parameter path accepts repository@digest;
        # normalize verified catalog images as well as locally built images.
        service['image']=cache[key]['reference']
        if row:
            row['public']=cache[key]
    if private:
        receipt.update(visibility_required=private,state='awaiting-visibility',
                       message='Some packages are still private. Open each link, make the package public (once per package name), then verify again.')
        return
    receipt['visibility_required']=[]
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
        no_faults=receipt['request']['faults']=='off',
        no_instrumentation=receipt['request'].get('instrumentation','on')=='off')
    if not settings.get('enabled', True) or not settings.get('token_id') or str(settings['token_id']).startswith('REPLACE_'):
        plan['checks'].append({'id':'configured_moog','state':'error','detail':'Enable MOOG and save a real token ID in Dwarf settings.'})
        plan['state']='blocked'
    plan['scope']='Request preview only. Registration, repository whitelist, credentials, duplicate requests and remote qualification remain unchecked.'
    plan['submission_ready']=False
    plan['remaining_gates']=receipt['limitations']
    receipt.update(git=evidence,request_plan=plan,state='review',
                   message='Public Git assets match. Review the existing MOOG request and remaining checks; nothing was submitted.')


def _worker(directory,op,status,receipt,lock,worker_lock,*,secret_id=None,controller=None,restore_state=None):
    def progress(message):
        status.update(state='running',message=message);build._save(op/'status.json',status)
    try:
        if status['action']=='push':
            try:_push(directory,op,receipt,progress,secret_id=secret_id,controller=controller)
            finally:
                from profile_manager.binary_imports import discard_secret
                discard_secret(secret_id)
        elif status['action']=='verify-images':_verify_images(directory,receipt,progress)
        else:_verify_git(directory,receipt,status['target'],progress)
        build._save(directory/'receipt.json',receipt)
        status.update(state='succeeded',message=receipt['message'])
    except (PackageError,build.BuildFailure) as exc:
        # A bounded failure that did not move the handoff leaves it where it was, not 'working'.
        if receipt.get('state')=='working' and restore_state:receipt.update(state=restore_state,message=str(exc))
        build._save(directory/'receipt.json',receipt)
        status.update(state='failed',message=str(exc))
    except Exception:
        build._save(directory/'receipt.json',receipt)
        status.update(state='failed',message='Publication operation failed. Check the saved operation and private host logs; no test was submitted.')
    finally:
        try:
            status['finished_at']=_now();build._save(op/'status.json',status)
        finally:worker_lock.close();lock.close()
