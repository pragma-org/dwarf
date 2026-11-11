import base64
import hashlib
import json
import time
from pathlib import Path

import pytest
import yaml

from test_antithesis_image_build import storage, prepared, catalog, fake_docker, fake_controller, fast_poll, wait, IMAGE_ID
from profile_manager import antithesis_image_build as build
from profile_manager.antithesis_package import PackageError
from profile_manager import antithesis_preparation as prep


def module():
    from profile_manager import antithesis_publication
    return antithesis_publication


@pytest.fixture
def built(prepared, monkeypatch):
    monkeypatch.setattr(build, 'controller_request', fake_controller([]))
    identifier = build.start_build(prepared['receipt']['id'])['id']
    assert wait(build, identifier)['state'] == 'built'
    return build.read_build_result(identifier)


def create(built):
    return module().create_handoff(built['receipt']['id'], 'ghcr.io/example/dwarf', request_id='b'*32)


def finished(identifier, operation):
    for _ in range(500):
        result = module().read_operation(identifier, operation)
        if result['state'] not in ('queued', 'running'):
            return result
        time.sleep(.01)
    pytest.fail('publication worker did not finish')


def run(handoff, action, target=None, request_id=None, **kw):
    job = module().start_operation(handoff['id'], action, target=target, request_id=request_id, **kw)
    return finished(handoff['id'], job['id'])


PAT = 'ghp_' + 'Publish' * 5


def fake_publish_controller(calls, *, fail=None):
    """A controller that publishes: it reads the staged secret (so the test can
    prove the secret existed during the operation) and records every call."""
    from profile_manager import binary_imports
    executions = {}
    def controller(operation, parameters, timeout, idempotency_key=None):
        calls.append((operation, dict(parameters), timeout, idempotency_key))
        if operation == 'antithesis.image-publish':
            secret = binary_imports._default_staging_root() / parameters['secret_id'] / 'secret'
            calls[-1][1]['_secret_seen'] = secret.read_text() if secret.is_file() else None
            if fail is not None:
                code, detail = fail
                executions[parameters['execution_id']] = {'state': 'failed', 'failure': {'code': code, 'stage': 'image-publish', 'message': 'canned ' + code},
                                                          'progress': {'status': 'failed', 'stage': 'login', 'detail': detail}}
            else:
                executions[parameters['execution_id']] = {'state': 'applied', 'result': {'status': 'published', 'handoff_id': parameters['handoff_id'], 'registry': 'ghcr.io',
                                                          'pushed': [{'destination': row['destination'], 'image_id': row['image_id'], 'pushed_at': 'now'} for row in parameters['images']]},
                                                          'progress': {'status': 'published'}}
            return {'status': 'accepted', 'execution_id': parameters['execution_id']}
        if operation == 'execution.status':
            return {'execution_id': parameters['execution_id'], **executions[parameters['execution_id']]}
        raise AssertionError(operation)
    return controller


def _no_saved_pat(monkeypatch):
    monkeypatch.setattr(module(), '_saved_publish_token', lambda: '')


def public_images(monkeypatch):
    def verify(reference, expected_id=None):
        return {'reference':reference.split('@')[0].split(':')[0]+'@sha256:'+'c'*64,
                'manifest_digest':'sha256:'+'c'*64,'amd64_manifest_digest':IMAGE_ID,
                'config_digest':'sha256:'+'d'*64,'platform':'linux/amd64',
                'anonymous':True, 'expected_identity_matched':bool(expected_id)}
    monkeypatch.setattr(module(), 'verify_image', verify)


def target():
    return dict(repository='Example/dwarf', commit='e'*40, directory='antithesis/example', requester='tester', try_number=1)


def test_create_is_immutable_recoverable_and_does_not_push(built, monkeypatch):
    monkeypatch.setattr(build, '_docker', lambda *a,**k:pytest.fail('implicit docker'))
    h=create(built)
    assert h['state']=='planned' and h['submission_ready'] is False
    assert h['request']==built['receipt']['request']
    assert h['images'][0]['destination']==f"ghcr.io/example/dwarf-sm-workload:{h['id']}"
    assert create(built)==h
    with pytest.raises(PackageError):
        module().create_handoff(built['receipt']['id'],'ghcr.io/different/name',request_id=h['id'])


@pytest.mark.parametrize('prefix',['http://localhost/x','ghcr.io/u/x:latest','ghcr.io/u/x@sha256:abc','ghcr.io/u/x;echo','ghcr.io/u/../x','ghcr.io/UPPER/x','ghcr.io/x'])
def test_destination_validation_before_side_effects(built,prefix):
    with pytest.raises(PackageError):module().create_handoff(built['receipt']['id'],prefix)


def test_explicit_push_runs_in_the_controller_and_replay_never_pushes_twice(built,monkeypatch):
    _no_saved_pat(monkeypatch)
    monkeypatch.setattr(build,'_docker',lambda *a,**k:pytest.fail('docker in the dashboard'))
    h=create(built);calls=[];controller=fake_publish_controller(calls)
    job=run(h,'push',request_id='1'*32,publish_token=PAT,controller=controller)
    assert job['state']=='succeeded',job
    assert module().read_handoff(h['id'])['state']=='awaiting-visibility'
    assert module().read_handoff(h['id'])['images'][0]['push_completed'] is True
    assert run(h,'push',request_id='1'*32,publish_token=PAT,controller=controller)==job
    assert sum(c[0]=='antithesis.image-publish' for c in calls)==1
    with pytest.raises(PackageError):module().start_operation(h['id'],'push',request_id='2'*32,publish_token=PAT,controller=controller)
    start=next(c for c in calls if c[0]=='antithesis.image-publish')
    assert start[1]['username']=='example' and start[1]['handoff_id']==h['id']
    assert start[1]['images']==[{'reference':row['reference'],'image_id':row['image_id'],'destination':row['destination']} for row in h['images']]
    assert start[2]==3600 and start[3]==start[1]['execution_id']


def test_push_stages_the_token_and_never_records_it(built,monkeypatch):
    _no_saved_pat(monkeypatch)
    h=create(built);calls=[]
    assert run(h,'push',publish_token=PAT,controller=fake_publish_controller(calls))['state']=='succeeded'
    start=next(c for c in calls if c[0]=='antithesis.image-publish')
    assert start[1]['_secret_seen']==PAT, 'the controller read the staged secret during the push'
    import re
    assert re.fullmatch(r'ghcr-[0-9a-f]{24}',start[1]['secret_id'])
    everything=''
    for path in (module().state_directory()/h['id']).rglob('*.json'):
        everything+=path.read_text()
    assert PAT not in everything and start[1]['secret_id'] not in everything


def test_push_deletes_the_staged_secret_after_the_operation_ends(built,monkeypatch):
    from profile_manager import binary_imports
    _no_saved_pat(monkeypatch)
    for fail in (None,('registry-login-failed','ghcr.io rejected the login')):
        h=module().create_handoff(built['receipt']['id'],'ghcr.io/example/dwarf')
        calls=[];run(h,'push',publish_token=PAT,controller=fake_publish_controller(calls,fail=fail))
        secret_id=next(c for c in calls if c[0]=='antithesis.image-publish')[1]['secret_id']
        assert not (binary_imports._default_staging_root()/secret_id).exists()


def test_failed_push_can_be_retried(built,monkeypatch):
    _no_saved_pat(monkeypatch)
    h=create(built)
    job=run(h,'push',publish_token=PAT,controller=fake_publish_controller([],fail=('registry-login-failed','ghcr.io rejected the login for example')))
    assert job['state']=='failed' and job['message']=='ghcr.io rejected the login for example'
    receipt=module().read_handoff(h['id'])
    assert receipt['push_attempted'] is False and receipt['state']=='planned'
    assert PAT not in json.dumps(receipt)
    assert run(h,'push',publish_token=PAT,controller=fake_publish_controller([]))['state']=='succeeded'
    assert module().read_handoff(h['id'])['state']=='awaiting-visibility'


def test_push_without_any_token_is_422(built,monkeypatch):
    _no_saved_pat(monkeypatch)
    h=create(built)
    with pytest.raises(PackageError) as error:
        module().start_operation(h['id'],'push',controller=fake_publish_controller([]))
    assert error.value.status==422 and 'write:packages' in str(error.value)
    assert module().read_handoff(h['id'])['push_attempted'] is False


def test_push_uses_the_saved_pat_when_none_is_typed(built,monkeypatch):
    monkeypatch.setattr(module(),'_saved_publish_token',lambda:'ghp_saved')
    h=create(built);calls=[]
    assert run(h,'push',controller=fake_publish_controller(calls))['state']=='succeeded'
    assert next(c for c in calls if c[0]=='antithesis.image-publish')[1]['_secret_seen']=='ghp_saved'


def test_push_with_save_token_persists_the_pat_through_settings(built,monkeypatch):
    saved={}
    from profile_manager.config import DeploymentConfig
    monkeypatch.setattr(module(),'_load_config',lambda:DeploymentConfig.from_dict({'moog':{'ghcr_prefix':'ghcr.io/saved/dwarf'}}))
    monkeypatch.setattr(module(),'_save_config',lambda config:saved.update(config.moog))
    monkeypatch.setattr(module(),'_saved_publish_token',lambda:'')
    h=create(built)
    job=run(h,'push',publish_token=PAT,save_token=True,controller=fake_publish_controller([]))
    assert job['state']=='succeeded' and job['token_saved'] is True
    assert saved['ghcr_publish_pat']==PAT and saved['ghcr_prefix']=='ghcr.io/saved/dwarf'
    assert PAT not in json.dumps(module().read_handoff(h['id']))


def test_push_uses_the_typed_prefix_without_saving_it(built,monkeypatch):
    _no_saved_pat(monkeypatch)
    monkeypatch.setattr(module(),'_save_config',lambda config:pytest.fail('settings must not change'))
    h=module().create_handoff(built['receipt']['id'],'ghcr.io/typed/dwarf')
    calls=[]
    assert run(h,'push',publish_token=PAT,controller=fake_publish_controller(calls))['state']=='succeeded'
    start=next(c for c in calls if c[0]=='antithesis.image-publish')
    assert start[1]['username']=='typed' and start[1]['images'][0]['destination'].startswith('ghcr.io/typed/dwarf-')


def test_anonymous_verification_materializes_runtime_only_package(built,monkeypatch):
    h=create(built);public_images(monkeypatch)
    assert run(h,'verify-images')['state']=='succeeded'
    result=module().read_result(h['id'])
    files,_=prep._unpack(base64.b64decode(result['archive_base64']))
    original,_=prep._unpack(base64.b64decode(built['archive_base64']))
    assert not any(n.startswith('provenance/') for n in files)
    after=yaml.safe_load(files['docker-compose.yaml'][0]);before=yaml.safe_load(original['docker-compose.yaml'][0])
    pushed={row['service']:row['destination'] for row in result['receipt']['images']}
    for name,service in before['services'].items():
        # Every controller-built service (the workload and, with taps on by default, the measurement
        # services) is exported as its pushed image; public images keep their own reference.
        reference=pushed.get(name,service.get('image'))
        service.pop('build',None)
        service['image']=module().verify_image(reference)['reference']
    assert after==before
    assert all(':' not in service['image'].split('@')[0].rsplit('/',1)[-1] for service in after['services'].values())
    assert all(original[n]==entry for n,entry in files.items() if n!='docker-compose.yaml')
    assert result['receipt']['request']==built['receipt']['request']
    assert result['receipt']['execution_ready'] is False
    assert build.read_build_result(built['receipt']['id'])==built


def test_private_images_cannot_produce_public_package_and_can_recheck(built,monkeypatch):
    h=create(built)
    monkeypatch.setattr(module(),'verify_image',lambda *a,**k:(_ for _ in ()).throw(PackageError('Image is not publicly accessible.',status=422)))
    assert run(h,'verify-images')['state']=='failed'
    with pytest.raises(PackageError):module().read_result(h['id'])
    public_images(monkeypatch);assert run(h,'verify-images')['state']=='succeeded'


def test_git_verification_uses_original_request_and_existing_builder(built,monkeypatch):
    h=create(built);public_images(monkeypatch);run(h,'verify-images')
    seen=[]
    def git(repo,commit,directory,files):
        seen.append((repo,commit,directory));assert 'docker-compose.yaml' in files
        return {'anonymous':True,'file_count':len(files)}
    monkeypatch.setattr(module(),'verify_git',git)
    monkeypatch.setattr(module(),'_moog_settings',lambda:{'token_id':'public-token','requester_wallet_file':'/wallet','github_pat':'SECRET'})
    from profile_manager import remote
    monkeypatch.setattr(remote,'run_moog_create_test',lambda *a,**k:pytest.fail('submission'))
    job=run(h,'verify-git',target());assert job['state']=='succeeded',job
    receipt=module().read_handoff(h['id']);plan=receipt['request_plan']
    assert plan['commit']==target()['commit']
    assert plan['duration_hours']==built['receipt']['request']['duration_hours']
    assert plan['faults_enabled']==(built['receipt']['request']['faults']=='on')
    assert 'requester create-test' in plan['command']
    assert 'SECRET' not in json.dumps(receipt)
    assert receipt['submission_ready'] is False


def test_api_auth_before_work_and_shapes(built,monkeypatch):
    from profile_manager.data.antithesis_package import dispatch_package_request as dispatch
    def call(action,payload,token='valid'):
        return dispatch(method='POST',path='/api/antithesis-run/package/'+action,body=json.dumps(dict(payload,token=token)).encode(),expected_token='valid')
    args={'build_id':built['receipt']['id'],'registry_prefix':'ghcr.io/example/dwarf','request_id':'b'*32}
    assert call('publication-create',args,token='wrong')[0]==403
    assert call('publication-create',dict(args,extra=1))[0]==400
    status,_,body=call('publication-create',args);assert status==200
    assert json.loads(body)['handoff']['build_id']==built['receipt']['id']


def test_changed_export_is_not_accepted(built,monkeypatch):
    h=create(built);public_images(monkeypatch);run(h,'verify-images')
    (module().state_directory()/h['id']/'package/docker-compose.yaml').write_text('changed')
    with pytest.raises(PackageError,match='changed'):module().read_result(h['id'])


def test_missing_settings_produces_reviewable_defaults_without_crashing(tmp_path,monkeypatch):
    monkeypatch.setenv('ADA2_PROFILE_MANAGER_CONFIG',str(tmp_path/'absent.json'))
    settings=module()._moog_settings()
    assert 'moog_binary' in settings
    assert 'github_pat' not in settings


def test_concurrent_operation_rejected_and_input_reuse_rejected(built,monkeypatch):
    import threading
    h=create(built);entered=threading.Event();release=threading.Event()
    public_images(monkeypatch);verify=module().verify_image
    def held(*args):
        entered.set();assert release.wait(5);return verify(*args)
    monkeypatch.setattr(module(),'verify_image',held)
    job=module().start_operation(h['id'],'verify-images',request_id='1'*32)
    try:
        assert entered.wait(5)
        assert module().start_operation(h['id'],'verify-images',request_id='1'*32)['id']==job['id']
        with pytest.raises(PackageError,match='different'):module().start_operation(h['id'],'push',request_id='1'*32)
        with pytest.raises(PackageError,match='active'):module().start_operation(h['id'],'verify-images',request_id='2'*32)
    finally:release.set()
    assert finished(h['id'],job['id'])['state']=='succeeded'


def test_interrupted_push_stays_uncertain_and_cannot_repush(built):
    h=create(built);directory=module().state_directory()/h['id'];op=directory/'operations'/('1'*32);op.mkdir()
    status={'id':'1'*32,'handoff_id':h['id'],'action':'push','target':None,'state':'running'}
    build._save(op/'status.json',status);h['push_attempted']=True;build._save(directory/'receipt.json',h)
    assert module().read_operation(h['id'],'1'*32)['state']=='interrupted'
    assert module().start_operation(h['id'],'push',request_id='1'*32)['state']=='interrupted'
    with pytest.raises(PackageError,match='already attempted'):module().start_operation(h['id'],'push',request_id='2'*32)


def test_unconfigured_moog_is_blocked_in_request_preview(built,monkeypatch):
    h=create(built);public_images(monkeypatch);run(h,'verify-images')
    monkeypatch.setattr(module(),'verify_git',lambda *a:{'anonymous':True})
    monkeypatch.setattr(module(),'_moog_settings',lambda:{'token_id':'REPLACE_WITH_YOUR_MOOG_TOKEN_ID','enabled':False})
    assert run(h,'verify-git',target())['state']=='succeeded'
    assert module().read_handoff(h['id'])['request_plan']['state']=='blocked'


def test_handoff_preserves_source_findings_and_qualification_limits(built):
    h=create(built)
    assert h['source_issues']==built['receipt']['issues']
    assert 'scenario record' not in ' '.join(h['limitations']).lower()
    assert 'runtime' in ' '.join(h['limitations']).lower() or 'binary behavior' in ' '.join(h['limitations']).lower()
    assert 'config-image' in ' '.join(h['limitations'])


@pytest.mark.parametrize('identifier',['',0,[],{}])
def test_invalid_creation_identity_rejected(built,identifier):
    with pytest.raises(PackageError):module().create_handoff(built['receipt']['id'],'ghcr.io/example/dwarf',request_id=identifier)


def test_verify_reports_visibility_required_per_package(built,monkeypatch):
    """One private package and one public one: the handoff names exactly the
    private package with its settings URLs and stays awaiting-visibility; once
    it is public the next verify reaches images-public."""
    _no_saved_pat(monkeypatch)
    h=create(built)
    assert run(h,'push',publish_token=PAT,controller=fake_publish_controller([]))['state']=='succeeded'
    private={'ghcr.io/example/dwarf-sm-workload:'+h['id']}
    def verify(reference,expected_id=None):
        if reference in private:
            raise PackageError('Anonymous public read returned HTTP 404. Check visibility, reference and provider rate limits.',status=422)
        return {'reference':reference.split('@')[0].split(':')[0]+'@sha256:'+'c'*64,'manifest_digest':'sha256:'+'c'*64,
                'amd64_manifest_digest':IMAGE_ID,'config_digest':'sha256:'+'d'*64,'platform':'linux/amd64','anonymous':True,
                'expected_identity_matched':bool(expected_id)}
    monkeypatch.setattr(module(),'verify_image',verify)
    job=run(h,'verify-images')
    assert job['state']=='succeeded',job
    receipt=module().read_handoff(h['id'])
    assert receipt['state']=='awaiting-visibility'
    assert receipt['visibility_required']==[{'service':'sm-workload','reference':'ghcr.io/example/dwarf-sm-workload:'+h['id'],
        'package':'dwarf-sm-workload','owner':'example','status':'private',
        'urls':{'user':'https://github.com/users/example/packages/container/dwarf-sm-workload/settings',
                'org':'https://github.com/orgs/example/packages/container/dwarf-sm-workload/settings'}}]
    assert 'inventory' not in receipt
    with pytest.raises(PackageError):module().read_result(h['id'])
    private.clear()
    assert run(h,'verify-images')['state']=='succeeded'
    receipt=module().read_handoff(h['id'])
    assert receipt['state']=='images-public' and receipt['visibility_required']==[]


def test_start_call_failure_keeps_the_handoff_pushable_and_discards_the_secret(built,monkeypatch):
    """Review fix: a controller that cannot even accept the publish (unreachable,
    contract rejection) must not leave the handoff 'working' with push_attempted."""
    from profile_manager import binary_imports
    _no_saved_pat(monkeypatch)
    h=create(built)
    staged=[];real=binary_imports.stage_secret
    monkeypatch.setattr(binary_imports,'stage_secret',lambda value,**kw:(staged.append(real(value,**kw)) or staged[-1]))
    def controller(operation,parameters,timeout,idempotency_key=None):
        raise module().TopologyActionError('controller unreachable')
    job=run(h,'push',publish_token=PAT,controller=controller)
    assert job['state']=='failed' and 'controller unreachable' in job['message'],job
    receipt=module().read_handoff(h['id'])
    assert receipt['push_attempted'] is False and receipt['state']=='planned'
    assert staged and not (binary_imports._default_staging_root()/staged[0]).exists()
    assert run(h,'push',publish_token=PAT,controller=fake_publish_controller([]))['state']=='succeeded'


def test_start_operation_failure_after_staging_discards_the_secret(built,monkeypatch):
    """Review fix: if start_operation fails after the PAT was staged, the staged
    file must not outlive the failed request."""
    from profile_manager import binary_imports
    _no_saved_pat(monkeypatch)
    h=create(built)
    root=binary_imports._default_staging_root()
    before={p.name for p in root.glob('ghcr-*')} if root.exists() else set()
    real=build._save
    def save(path,payload):
        if path.name=='status.json' and 'operations' in Path(path).parts:raise OSError('disk full')
        return real(path,payload)
    monkeypatch.setattr(build,'_save',save)
    with pytest.raises(OSError):module().start_operation(h['id'],'push',request_id='c'*32,publish_token=PAT)
    assert {p.name for p in root.glob('ghcr-*')}==before,'a failed start must not leave a staged PAT behind'
    assert module().read_handoff(h['id'])['push_attempted'] is False


def test_verify_before_push_does_not_claim_packages_are_private(built,monkeypatch):
    """Review fix: a 404 on a handoff that was never pushed means the package
    does not exist yet, not that someone must make it public."""
    h=create(built)
    def verify(reference,expected_id=None):
        raise PackageError('Anonymous public read returned HTTP 404. Check visibility, reference and provider rate limits.',status=422)
    monkeypatch.setattr(module(),'verify_image',verify)
    job=run(h,'verify-images')
    assert job['state']=='failed' and 'HTTP 404' in job['message'],job
    receipt=module().read_handoff(h['id'])
    assert receipt['state']=='planned' and not receipt.get('visibility_required')


def test_handoff_receipt_carries_workload_image_contents(built):
    assert create(built)['workload_image_contents']==[]


def test_handoff_copies_image_contents_from_the_workload_binding(built,monkeypatch):
    source=build.read_build_result(built['receipt']['id'])
    source['receipt']['workload_binding']['image_contents']=[{'path':'environment/dwarf-workload/Dockerfile','sha256':'sha256:'+'1'*64}]
    monkeypatch.setattr(build,'read_build_result',lambda identifier:source)
    receipt=module().create_handoff(built['receipt']['id'],'ghcr.io/example/dwarf',request_id='c'*32)
    assert receipt['workload_image_contents']==[{'path':'environment/dwarf-workload/Dockerfile','sha256':'sha256:'+'1'*64}]
