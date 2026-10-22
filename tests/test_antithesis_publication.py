import base64
import hashlib
import json
import time
from pathlib import Path

import pytest
import yaml

from test_antithesis_image_build import storage, prepared, catalog, fake_docker, wait, IMAGE_ID
from profile_manager import antithesis_image_build as build
from profile_manager.antithesis_package import PackageError
from profile_manager import antithesis_preparation as prep


def module():
    from profile_manager import antithesis_publication
    return antithesis_publication


@pytest.fixture
def built(prepared, monkeypatch):
    monkeypatch.setattr(build, '_docker', fake_docker([]))
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


def run(handoff, action, target=None, request_id=None):
    job = module().start_operation(handoff['id'], action, target=target, request_id=request_id)
    return finished(handoff['id'], job['id'])


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


def test_explicit_push_checks_image_identity_and_replay_never_pushes_twice(built,monkeypatch):
    h=create(built);calls=[]
    def docker(argv,**kwargs):
        calls.append(argv)
        if argv[1:3]==['image','inspect']:return json.dumps([{'Id':IMAGE_ID,'Os':'linux','Architecture':'amd64'}])
        assert argv[1] in ('tag','push');return ''
    monkeypatch.setattr(build,'_docker',docker)
    job=run(h,'push',request_id='1'*32)
    assert job['state']=='succeeded',job
    assert module().read_handoff(h['id'])['state']=='awaiting-visibility'
    assert run(h,'push',request_id='1'*32)==job
    assert sum(c[1]=='push' for c in calls)==1
    with pytest.raises(PackageError):module().start_operation(h['id'],'push',request_id='2'*32)


def test_changed_image_ref_is_rejected_before_push(built,monkeypatch):
    h=create(built);calls=[]
    def docker(argv,**kwargs):
        calls.append(argv);return json.dumps([{'Id':'sha256:'+'f'*64,'Os':'linux','Architecture':'amd64'}])
    monkeypatch.setattr(build,'_docker',docker)
    assert run(h,'push')['state']=='failed'
    assert not any(c[1]=='push' for c in calls)


def test_anonymous_verification_materializes_runtime_only_package(built,monkeypatch):
    h=create(built);public_images(monkeypatch)
    assert run(h,'verify-images')['state']=='succeeded'
    result=module().read_result(h['id'])
    files,_=prep._unpack(base64.b64decode(result['archive_base64']))
    original,_=prep._unpack(base64.b64decode(built['archive_base64']))
    assert not any(n.startswith('provenance/') for n in files)
    after=yaml.safe_load(files['docker-compose.yaml'][0]);before=yaml.safe_load(original['docker-compose.yaml'][0])
    for name,service in before['services'].items():
        reference=result['receipt']['images'][0]['destination'] if name=='sm-workload' else service['image']
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
