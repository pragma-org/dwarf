import base64
import hashlib
import json
import time
from pathlib import Path
import pytest
from test_antithesis_package import request
from test_antithesis_preflight import package


@pytest.fixture
def staged(tmp_path,monkeypatch):
    from profile_manager import antithesis_preparation as prep
    for key in ('STATE','RUNS','BUNDLES'):
        monkeypatch.setenv('ADA2_DWARF_'+key+'_DIR',str(tmp_path/key.lower()))
    monkeypatch.setenv('ADA2_DWARF_VERSION_IMPORTS_DIR',str(tmp_path/'version-imports'))
    root=tmp_path/'supplied';root.mkdir()
    for name,(data,mode) in package().items():
        path=root/name;path.write_bytes(data);path.chmod(mode)
    return prep.prepare_package(request(package_source='existing'),{'compose_path':str(root),'image_references':[]})


def module():
    from profile_manager import antithesis_package_validation
    return antithesis_package_validation


def wait(identifier):
    for _ in range(300):
        result=module().read_validation(identifier)
        if result['state'] not in ('queued','running'):return result
        time.sleep(.01)
    pytest.fail('validation worker did not finish')


def no_controller(*a,**k):
    pytest.fail('static check reached the controller')


def facts_for(files,*,execution_id,archive_sha256,corrupt_paths=(),platform='amd64',schema='pass',unclean=False,context_blocked=False,image_id='sha256:'+'a'*64):
    """What controller.antithesis_validate returns for this package."""
    from profile_manager import antithesis_preflight as preflight
    report=preflight.inspect_package(files)
    images=[{'reference':row['reference'],'state':'pass' if platform=='amd64' else 'fail','image_id':image_id} for row in report['images']]
    if context_blocked:
        candidate={'context_blocked':True}
    else:
        candidate={'context_blocked':False,'reference':'dwarf-antithesis-config-check:'+execution_id[-32:],'container_name':'dwarf-antithesis-check-'+execution_id[-32:],
                   'attempted':schema=='pass','built':schema=='pass','image_id':image_id if schema=='pass' else None,
                   'platform_ok':(platform=='amd64') if schema=='pass' else None,'created':schema=='pass' and platform=='amd64',
                   'completed':schema=='pass' and platform=='amd64','files':[],'cleanup':{}}
        if candidate['completed']:
            for path,(data,mode) in sorted(files.items()):
                expected=data.replace(b'${INTERNAL_NETWORK}',b'false') if path=='docker-compose.yaml' else data
                candidate['files'].append({'path':path,'kind':'directory' if data is None else 'file','mode':mode,
                                           'sha256':None if data is None else hashlib.sha256(expected).hexdigest(),'matches':path not in corrupt_paths})
            candidate['cleanup']={'container':'unconfirmed' if unclean else 'removed','image':'unconfirmed' if unclean else 'removed'}
        elif candidate['attempted']:
            candidate['cleanup']={'image':'removed'}
    return {'status':'validated','execution_id':execution_id,'stage_id':'validate-'+'0'*24,'archive_sha256':archive_sha256,
            'images':images,'compose_schema':{'state':schema},'candidate':candidate}


def fake_controller(calls,*,corrupt_paths=(),platform='amd64',schema='pass',unclean=False,context_blocked=False,unavailable=False):
    """Plays the controller: records the request, reads the staged archive the
    way controller.antithesis_validate does, answers with facts."""
    from profile_manager import antithesis_preparation as prep
    from profile_manager.binary_imports import _default_staging_root
    from profile_manager.topology_actions import TopologyActionError
    state={}
    def controller(operation,parameters,timeout,idempotency_key=None):
        calls.append((operation,dict(parameters)))
        if unavailable:raise TopologyActionError('controller is not configured')
        if operation=='antithesis.package-validate':
            staged=_default_staging_root()/parameters['stage_id']
            data=(staged/'package.zip').read_bytes()
            descriptor=json.loads((staged/'descriptor.json').read_text())
            assert descriptor['kind']=='antithesis-package-archive' and descriptor['archive_sha256']==parameters['archive_sha256']
            assert 'sha256:'+hashlib.sha256(data).hexdigest()==parameters['archive_sha256']
            assert (staged/'package.zip').stat().st_mode & 0o777==0o600
            files,_=prep._unpack(data)
            state['facts']=facts_for(files,execution_id=parameters['execution_id'],archive_sha256=parameters['archive_sha256'],corrupt_paths=corrupt_paths,
                                     platform=platform,schema=schema,unclean=unclean,context_blocked=context_blocked)
            state['staged']=staged
            return {'status':'accepted','execution_id':parameters['execution_id']}
        assert operation=='execution.status'
        return {'execution_id':parameters['execution_id'],'state':'applied','result':state['facts']}
    controller.state=state
    return controller


def test_static_job_uses_saved_snapshot_and_never_calls_the_controller(staged):
    m=module()
    source={'kind':'preparation','id':staged['receipt']['id']}
    identifier='a'*32
    result=wait(m.start_validation(source,'static',identifier,controller=no_controller)['id'])
    assert result['state']=='complete'
    assert result['report']['state']=='review'
    assert result['source']==source
    assert result['submission_ready'] is False
    assert m.start_validation(source,'static',identifier,controller=no_controller)==result
    with pytest.raises(m.prep.PackageError,match='different'):
        m.start_validation(source,'artifacts',identifier,controller=no_controller)


@pytest.mark.parametrize('kind', ['preparation','build','publication'])
def test_each_snapshot_path_runs_same_validator(staged,monkeypatch,kind):
    m=module();calls=[]
    reader={'preparation':(m.prep,'read_preparation'),'build':(m.build,'read_build_result'),'publication':(m.publication,'read_result')}[kind]
    monkeypatch.setattr(*reader,lambda identifier:(calls.append(identifier) or staged))
    result=wait(m.start_validation({'kind':kind,'id':staged['receipt']['id']},'static','b'*32,controller=no_controller)['id'])
    assert calls==[staged['receipt']['id']]
    assert result['report']['images'][0]['reference']=='example/node:experimental'


def test_artifacts_mode_stages_the_exact_archive_for_the_controller_and_discards_it(staged):
    m=module();calls=[];controller=fake_controller(calls)
    result=wait(m.start_validation({'kind':'preparation','id':staged['receipt']['id']},'artifacts','d'*32,controller=controller)['id'])
    assert result['state']=='complete',result
    operations=[c[0] for c in calls]
    assert operations[0]=='antithesis.package-validate' and 'execution.status' in operations
    sent=calls[0][1]
    assert sent['archive_sha256']=='sha256:'+hashlib.sha256(base64.b64decode(staged['archive_base64'])).hexdigest()
    assert sent['execution_id'].startswith('antithesis-validate-') and result['controller_execution_id']==sent['execution_id']
    assert not controller.state['staged'].exists()
    checks=result['report']['checks']
    assert any(r['code']=='candidate-files' and r['state']=='pass' for r in checks)
    assert any(r['code']=='local-image-platform' and r['state']=='pass' for r in checks)
    assert any(r['code']=='public-image' and r['state']=='not_checked' for r in checks)
    assert any(r['code']=='compose-schema' and r['state']=='pass' for r in checks)
    assert result['report']['candidate']['inspected_on']=='DWARF controller'
    assert result['report']['candidate']['cleanup']=={'container':'removed','image':'removed'}
    assert result['report']['submission_ready'] is False


def test_changed_candidate_bytes_fail_and_still_report_cleanup(staged):
    m=module();calls=[];controller=fake_controller(calls,corrupt_paths={'config.json'})
    result=wait(m.start_validation({'kind':'preparation','id':staged['receipt']['id']},'artifacts','e'*32,controller=controller)['id'])
    assert result['report']['state']=='blocked'
    assert any(r['code']=='candidate-files' and r['state']=='fail' and r['location']=='config.json' for r in result['report']['checks'])
    assert result['report']['candidate']['cleanup']['container']=='removed'


def test_unavailable_controller_remains_unchecked_not_success(staged):
    m=module();calls=[];controller=fake_controller(calls,unavailable=True)
    result=wait(m.start_validation({'kind':'preparation','id':staged['receipt']['id']},'artifacts','f'*32,controller=controller)['id'])
    assert result['state']=='complete'
    checks=result['report']['checks']
    assert any(r['code']=='candidate-image' and r['state']=='not_checked' for r in checks)
    assert any(r['code']=='local-image-platform' and r['state']=='not_checked' for r in checks)
    assert 'not configured' not in json.dumps(result)
    staging=Path(m.prep.state_directory()).parent.parent/'version-imports'
    assert not any(staging.glob('validate-*')) if staging.exists() else True


def test_source_drift_rejected(staged):
    m=module();root=m.prep.state_directory()/staged['receipt']['id']/'package'
    (root/'config.json').write_text('changed')
    with pytest.raises(m.prep.PackageError,match='changed'):
        m.start_validation({'kind':'preparation','id':staged['receipt']['id']},'static','1'*32,controller=no_controller)


def test_authentication_precedes_inspection(staged,monkeypatch):
    from profile_manager.data.antithesis_package import dispatch_package_request
    m=module();monkeypatch.setattr(m,'start_validation',lambda *a,**k:pytest.fail('unauthorized work'))
    status,_,_=dispatch_package_request(method='POST',path='/api/antithesis-run/package/validate',
        body=json.dumps({'token':'bad','source':{'kind':'preparation','id':staged['receipt']['id']},'mode':'static','request_id':'1'*32}).encode(),expected_token='good')
    assert status==403


def test_context_system_paths_fail_without_a_candidate_row():
    m=module();files=package();files['etc/ld.so.preload']=(b'/evil',0o644)
    report=m.preflight.inspect_package(files)
    m.inspect_candidate(report,facts_for(files,execution_id='antithesis-validate-'+'a'*32,archive_sha256='sha256:'+'b'*64,context_blocked=True))
    assert any(r['code']=='candidate-context' and r['state']=='fail' for r in report['checks'])
    assert 'candidate' not in report


def test_candidate_directory_mismatch_is_reported_at_its_path():
    m=module();files=package(volumes=['./empty:/data']);files['empty']=(None,0o755);files['.dockerignore']=(b'empty\n',0o644)
    report=m.preflight.inspect_package(files)
    m.inspect_candidate(report,facts_for(files,execution_id='antithesis-validate-'+'8'*32,archive_sha256='sha256:'+'b'*64,corrupt_paths={'empty'}))
    assert any(r['code']=='candidate-files' and r['location']=='empty' and r['state']=='fail' for r in report['checks'])


def test_one_active_validation_and_interrupted_recovery(staged,monkeypatch):
    import threading
    m=module();entered=threading.Event();release=threading.Event();original=m.preflight.inspect_package
    def held(files):entered.set();release.wait(5);return original(files)
    monkeypatch.setattr(m.preflight,'inspect_package',held)
    source={'kind':'preparation','id':staged['receipt']['id']}
    first=m.start_validation(source,'static','2'*32,controller=no_controller)
    assert entered.wait(2)
    try:
        assert m.start_validation(source,'static','2'*32,controller=no_controller)['id']==first['id']
        with pytest.raises(m.prep.PackageError,match='active'):m.start_validation(source,'static','3'*32,controller=no_controller)
    finally:release.set()
    assert wait(first['id'])['state']=='complete'
    directory=m.state_directory()/first['id'];status=json.loads((directory/'status.json').read_text());status['state']='running'
    m.build._save(directory/'status.json',status)
    assert m.read_validation(first['id'])['state']=='interrupted'


def test_failed_anonymous_verification_is_not_swallowed_as_pass(staged,monkeypatch):
    m=module();files=package(image='ghcr.io/example/node:exp');report=m.preflight.inspect_package(files)
    def failed(*a,**k):raise RuntimeError('secret token never expose')
    monkeypatch.setattr(m,'verify_image',failed)
    m.inspect_images(m.state_directory(),report,facts_for(files,execution_id='antithesis-validate-'+'a'*32,archive_sha256='sha256:'+'b'*64))
    assert any(r['code']=='public-image' and r['state']=='fail' for r in report['checks'])
    assert 'secret token' not in json.dumps(report)


def test_wrong_local_platform_and_cleanup_failure_are_visible(tmp_path):
    m=module();files=package()
    report=m.preflight.inspect_package(files)
    m.inspect_images(tmp_path,report,facts_for(files,execution_id='antithesis-validate-'+'9'*32,archive_sha256='sha256:'+'b'*64,platform='arm64'))
    assert any(r['code']=='local-image-platform' and r['state']=='fail' for r in report['checks'])
    m.inspect_candidate(report,facts_for(files,execution_id='antithesis-validate-'+'9'*32,archive_sha256='sha256:'+'b'*64,unclean=True))
    assert report['candidate']['cleanup']['container']=='unconfirmed'
    assert any(r['code']=='candidate-cleanup' and r['state']=='warn' for r in report['checks'])


def test_controller_facts_are_checked_before_they_are_believed(tmp_path):
    m=module();files=package();report=m.preflight.inspect_package(files)
    facts=facts_for(files,execution_id='antithesis-validate-'+'a'*32,archive_sha256='sha256:'+'b'*64)
    facts['images'][0]['image_id']='not-a-digest'
    m.inspect_images(tmp_path,report,facts)
    assert any(r['code']=='local-image-platform' and r['state']=='not_checked' for r in report['checks'])
    assert report['images'][0]['local_image_id'] is None


def test_authenticated_api_returns_validation_job_and_report(staged):
    from profile_manager.data.antithesis_package import dispatch_package_request
    payload={'token':'test','source':{'kind':'preparation','id':staged['receipt']['id']},'mode':'static','request_id':'a'*32}
    code,_,data=dispatch_package_request(method='POST',path='/api/antithesis-run/package/validate',body=json.dumps(payload).encode(),expected_token='test')
    assert code==202
    result=wait(json.loads(data)['validation']['id'])
    code,_,data=dispatch_package_request(method='POST',path='/api/antithesis-run/package/validation-status',body=json.dumps({'token':'test','validation_id':result['id']}).encode(),expected_token='test')
    assert code==200 and json.loads(data)['validation']['state']=='complete'


def test_validation_api_failure_does_not_claim_no_build(staged,monkeypatch):
    from profile_manager.data.antithesis_package import dispatch_package_request
    m=module()
    def failed(*a,**k):raise OSError('raw secret')
    monkeypatch.setattr(m,'start_validation',failed)
    payload={'token':'test','source':{'kind':'preparation','id':staged['receipt']['id']},'mode':'artifacts','request_id':'a'*32}
    code,_,data=dispatch_package_request(method='POST',path='/api/antithesis-run/package/validate',body=json.dumps(payload).encode(),expected_token='test')
    assert code==503
    assert 'recover' in data.decode().lower() and 'raw secret' not in data.decode()


def test_compose_schema_failure_blocks_the_candidate_rows(staged):
    m=module();calls=[];controller=fake_controller(calls,schema='fail')
    result=wait(m.start_validation({'kind':'preparation','id':staged['receipt']['id']},'artifacts','7'*32,controller=controller)['id'])
    checks=result['report']['checks']
    assert any(r['code']=='compose-schema' and r['state']=='fail' for r in checks)
    assert not any(r['code']=='candidate-files' for r in checks)
    assert 'candidate' not in result['report']
