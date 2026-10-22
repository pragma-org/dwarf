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


def test_static_job_uses_saved_snapshot_and_never_calls_docker(staged,monkeypatch):
    m=module();monkeypatch.setattr(m.build,'_docker',lambda *a,**k:pytest.fail('static check invoked Docker'))
    source={'kind':'preparation','id':staged['receipt']['id']}
    identifier='a'*32
    result=wait(m.start_validation(source,'static',identifier)['id'])
    assert result['state']=='complete'
    assert result['report']['state']=='review'
    assert result['source']==source
    assert result['submission_ready'] is False
    assert m.start_validation(source,'static',identifier)==result
    with pytest.raises(m.prep.PackageError,match='different'):
        m.start_validation(source,'artifacts',identifier)


@pytest.mark.parametrize('kind', ['preparation','build','publication'])
def test_each_snapshot_path_runs_same_validator(staged,monkeypatch,kind):
    m=module();calls=[]
    reader={'preparation':(m.prep,'read_preparation'),'build':(m.build,'read_build_result'),'publication':(m.publication,'read_result')}[kind]
    monkeypatch.setattr(*reader,lambda identifier:(calls.append(identifier) or staged))
    result=wait(m.start_validation({'kind':kind,'id':staged['receipt']['id']},'static','b'*32)['id'])
    assert calls==[staged['receipt']['id']]
    assert result['report']['images'][0]['reference']=='example/node:experimental'


def fake_docker(calls,corrupt=False,platform='amd64'):
    def run(argv,*,cwd,log_path,timeout):
        calls.append(argv)
        if argv[1:3]==['image','inspect']:
            return json.dumps([{'Id':'sha256:'+'a'*64,'Os':'linux','Architecture':platform}])
        if argv[1]=='compose':
            assert 'config' in argv and '--quiet' in argv
            return ''
        if argv[1]=='build':
            assert '--network=none' in argv
            assert Path(argv[argv.index('--file')+1]).parent!=Path(cwd)
            assert 'COPY . /' in Path(argv[argv.index('--file')+1]).read_text()
            return ''
        if argv[1]=='create':return 'c'*64
        if argv[1]=='cp':
            relative=argv[2].split(':/',1)[1];target=Path(argv[3])
            source=Path(cwd)/relative
            target.write_bytes(b'changed' if corrupt else source.read_bytes().replace(b'${INTERNAL_NETWORK}',b'false') if relative=='docker-compose.yaml' else source.read_bytes())
            target.chmod(source.stat().st_mode & 0o777)
            return ''
        assert argv[1] in ('rm','image'),argv
        return ''
    return run


def test_artifacts_inspect_stopped_candidate_and_cleanup(staged,monkeypatch):
    m=module();calls=[];monkeypatch.setattr(m.build,'_docker',fake_docker(calls))
    result=wait(m.start_validation({'kind':'preparation','id':staged['receipt']['id']},'artifacts','d'*32)['id'])
    assert result['state']=='complete',result
    checks=result['report']['checks']
    assert any(r['code']=='candidate-files' and r['state']=='pass' for r in checks)
    assert any(r['code']=='local-image-platform' and r['state']=='pass' for r in checks)
    assert any(r['code']=='public-image' and r['state']=='not_checked' for r in checks)
    assert any(c[1]=='rm' for c in calls)
    assert not any(c[1] in ('run','start','push','pull') for c in calls)
    assert result['report']['submission_ready'] is False


def test_changed_candidate_bytes_fail_and_container_is_cleaned(staged,monkeypatch):
    m=module();calls=[];monkeypatch.setattr(m.build,'_docker',fake_docker(calls,corrupt=True))
    result=wait(m.start_validation({'kind':'preparation','id':staged['receipt']['id']},'artifacts','e'*32)['id'])
    assert result['report']['state']=='blocked'
    assert any(r['code']=='candidate-files' and r['state']=='fail' for r in result['report']['checks'])
    assert any(c[1]=='rm' for c in calls)


def test_missing_docker_remains_unchecked_not_success(staged,monkeypatch):
    m=module()
    def absent(*a,**k):raise FileNotFoundError('private diagnostic')
    monkeypatch.setattr(m.build,'_docker',absent)
    result=wait(m.start_validation({'kind':'preparation','id':staged['receipt']['id']},'artifacts','f'*32)['id'])
    assert result['state']=='complete'
    assert any(r['code']=='candidate-image' and r['state']=='not_checked' for r in result['report']['checks'])
    assert 'private diagnostic' not in json.dumps(result)


def test_source_drift_rejected(staged):
    m=module();root=m.prep.state_directory()/staged['receipt']['id']/'package'
    (root/'config.json').write_text('changed')
    with pytest.raises(m.prep.PackageError,match='changed'):
        m.start_validation({'kind':'preparation','id':staged['receipt']['id']},'static','1'*32)


def test_authentication_precedes_inspection(staged,monkeypatch):
    from profile_manager.data.antithesis_package import dispatch_package_request
    m=module();monkeypatch.setattr(m,'start_validation',lambda *a,**k:pytest.fail('unauthorized work'))
    status,_,_=dispatch_package_request(method='POST',path='/api/antithesis-run/package/validate',
        body=json.dumps({'token':'bad','source':{'kind':'preparation','id':staged['receipt']['id']},'mode':'static','request_id':'1'*32}).encode(),expected_token='good')
    assert status==403


def test_context_system_paths_skip_candidate_without_executing_recipe(tmp_path,monkeypatch):
    m=module();calls=[];monkeypatch.setattr(m.build,'_docker',fake_docker(calls))
    files=package();files['etc/ld.so.preload']=(b'/evil',0o644)
    report=m.preflight.inspect_package(files)
    m.inspect_candidate(tmp_path,files,report,'a'*32)
    assert not calls
    assert any(r['code']=='candidate-context' and r['state']=='fail' for r in report['checks'])


def test_candidate_checks_empty_bind_directories(tmp_path,monkeypatch):
    m=module();calls=[];monkeypatch.setattr(m.build,'_docker',fake_docker(calls))
    files=package(volumes=['./empty:/data']);files['empty']=(None,0o755);files['.dockerignore']=(b'empty\n',0o644)
    report=m.preflight.inspect_package(files)
    m.inspect_candidate(tmp_path,files,report,'8'*32)
    assert any(r['code']=='candidate-files' and r['location']=='empty' and r['state']=='fail' for r in report['checks'])


def test_one_active_validation_and_interrupted_recovery(staged,monkeypatch):
    import threading
    m=module();entered=threading.Event();release=threading.Event();original=m.preflight.inspect_package
    def held(files):entered.set();release.wait(5);return original(files)
    monkeypatch.setattr(m.preflight,'inspect_package',held)
    source={'kind':'preparation','id':staged['receipt']['id']}
    first=m.start_validation(source,'static','2'*32)
    assert entered.wait(2)
    try:
        assert m.start_validation(source,'static','2'*32)['id']==first['id']
        with pytest.raises(m.prep.PackageError,match='active'):m.start_validation(source,'static','3'*32)
    finally:release.set()
    assert wait(first['id'])['state']=='complete'
    directory=m.state_directory()/first['id'];status=json.loads((directory/'status.json').read_text());status['state']='running'
    m.build._save(directory/'status.json',status)
    assert m.read_validation(first['id'])['state']=='interrupted'


def test_failed_anonymous_verification_is_not_swallowed_as_pass(staged,monkeypatch):
    m=module();report=m.preflight.inspect_package(package(image='ghcr.io/example/node:exp'));calls=[]
    monkeypatch.setattr(m.build,'_docker',fake_docker(calls))
    def failed(*a,**k):raise RuntimeError('secret token never expose')
    monkeypatch.setattr(m,'verify_image',failed)
    m.inspect_images(m.state_directory(),report)
    assert any(r['code']=='public-image' and r['state']=='fail' for r in report['checks'])
    assert 'secret token' not in json.dumps(report)


def test_wrong_local_platform_and_cleanup_failure_are_visible(staged,monkeypatch,tmp_path):
    m=module();calls=[];fake=fake_docker(calls,platform='arm64')
    monkeypatch.setattr(m.build,'_docker',fake)
    report=m.preflight.inspect_package(package());m.inspect_images(tmp_path,report)
    assert any(r['code']=='local-image-platform' and r['state']=='fail' for r in report['checks'])
    fake=fake_docker(calls)
    def unclean(argv,**kwargs):
        if argv[1]=='rm':raise m.build.BuildFailure('private detail')
        return fake(argv,**kwargs)
    monkeypatch.setattr(m.build,'_docker',unclean)
    m.inspect_candidate(tmp_path,package(),report,'9'*32)
    assert report['candidate']['cleanup']['container']=='unconfirmed'
    assert any(r['code']=='candidate-cleanup' and r['state']=='warn' for r in report['checks'])
    assert 'private detail' not in json.dumps(report)


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


def test_candidate_checks_compose_schema_before_build(tmp_path,monkeypatch):
    m=module();calls=[];fake=fake_docker(calls)
    def schema_error(argv,**kwargs):
        if argv[1]=='compose':
            calls.append(argv)
            assert '--no-interpolate' in argv and '--no-env-resolution' in argv and '--quiet' in argv
            raise m.build.BuildFailure('schema failed')
        return fake(argv,**kwargs)
    monkeypatch.setattr(m.build,'_docker',schema_error)
    report=m.preflight.inspect_package(package());m.inspect_candidate(tmp_path,package(),report,'7'*32)
    assert any(r['code']=='compose-schema' and r['state']=='fail' for r in report['checks'])
    assert not any(c[1]=='build' for c in calls)
