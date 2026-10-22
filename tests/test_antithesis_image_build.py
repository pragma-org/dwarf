import base64
import io
import json
import threading
import time
import zipfile
from pathlib import Path

import pytest
import yaml

from test_antithesis_miniprotocol_binding import case
from test_antithesis_version_binding import catalog
from profile_manager.antithesis_preparation import prepare_package, read_preparation

IMAGE_ID = 'sha256:' + 'a' * 64


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    for key, name in [('RUNS','runs'),('STATE','state'),('BUNDLES','bundles')]:
        monkeypatch.setenv('ADA2_DWARF_'+key+'_DIR', str(tmp_path/name))


@pytest.fixture
def prepared(catalog, tmp_path, monkeypatch):
    monkeypatch.setenv('ADA2_DWARF_STATE_DIR', str(tmp_path/'state'))
    return prepare_package(case(), {})


def module():
    from profile_manager import antithesis_image_build
    return antithesis_image_build


def fake_docker(calls):
    def run(argv, *, cwd, log_path, timeout):
        calls.append(argv)
        if argv[1:3] == ['image', 'inspect']:
            return json.dumps([{'Id':IMAGE_ID,'Os':'linux','Architecture':'amd64'}])
        assert argv[:2] == ['docker', 'build']
        assert argv[argv.index('--platform')+1] == 'linux/amd64'
        assert (Path(cwd)/'workload/Dockerfile').is_file()
        return ''
    return run


def wait(build, identifier):
    for _ in range(300):
        result = build.read_build(identifier)
        if result['state'] not in ('queued', 'building'):
            return result
        time.sleep(.01)
    pytest.fail('worker did not finish')


def test_generated_build_materializes_separate_package_without_changing_selections(prepared, monkeypatch):
    build = module(); calls = []
    monkeypatch.setattr(build, '_docker', fake_docker(calls))
    identifier = build.start_build(prepared['receipt']['id'])['id']
    status = wait(build, identifier)
    assert status['state'] == 'built', status
    result = build.read_build_result(identifier)
    receipt = result['receipt']
    assert receipt['preparation_id'] == prepared['receipt']['id']
    assert receipt['execution_ready'] is False and receipt['submission_ready'] is False
    assert receipt['request'] == prepared['receipt']['request']
    assert receipt['version_binding'] == prepared['receipt']['version_binding']
    assert receipt['built_images'][0]['image_id'] == IMAGE_ID
    archive = zipfile.ZipFile(io.BytesIO(base64.b64decode(result['archive_base64'])))
    original = zipfile.ZipFile(io.BytesIO(base64.b64decode(prepared['archive_base64'])))
    before = yaml.safe_load(original.read('docker-compose.yaml'))
    after = yaml.safe_load(archive.read('docker-compose.yaml'))
    expected = before['services']['sm-workload'].copy(); expected.pop('build')
    expected['image'] = receipt['built_images'][0]['reference']
    assert after['services']['sm-workload'] == expected
    before['services']['sm-workload'] = expected
    assert after == before
    assert receipt['built_images'][0]['reference'].startswith('dwarf-antithesis-')
    assert len(calls) == 2
    assert not any('push' in c or 'up' in c for c in calls)
    assert all(archive.read(n) == original.read(n) for n in original.namelist() if n != 'docker-compose.yaml')
    assert read_preparation(prepared['receipt']['id']) == prepared
    assert not {'image-build-required','workload-image-build-required'} & {i['code'] for i in receipt['issues']}
    assert 'publication-required' in {i['code'] for i in receipt['issues']}


def test_authentication_precedes_any_build_work(prepared, monkeypatch):
    from profile_manager.data.antithesis_package import dispatch_package_request
    build = module()
    monkeypatch.setattr(build, 'start_build', lambda *a: pytest.fail('unauthorized build'))
    status, _, _ = dispatch_package_request(method='POST', path='/api/antithesis-run/package/build',
        body=json.dumps({'token':'wrong','preparation_id':prepared['receipt']['id']}).encode(), expected_token='test')
    assert status == 403


def test_failed_build_does_not_produce_a_download_and_can_retry(prepared, monkeypatch):
    build = module()
    def fail(*args, **kwargs):
        raise build.BuildFailure('Docker build failed (exit 7).')
    monkeypatch.setattr(build, '_docker', fail)
    first = build.start_build(prepared['receipt']['id'])['id']
    assert wait(build, first)['state'] == 'failed'
    with pytest.raises(build.PackageError, match='not complete'):
        build.read_build_result(first)
    monkeypatch.setattr(build, '_docker', fake_docker([]))
    second = build.start_build(prepared['receipt']['id'])['id']
    assert second != first and wait(build, second)['state'] == 'built'


def test_concurrent_builds_are_rejected_and_source_copy_is_private(prepared, monkeypatch):
    build = module(); entered = threading.Event(); release = threading.Event(); calls=[]
    def held(argv, **kwargs):
        if argv[1] == 'build':
            entered.set(); assert release.wait(5)
            assert 'builds' in str(kwargs['cwd'])
        return fake_docker(calls)(argv, **kwargs)
    monkeypatch.setattr(build, '_docker', held)
    first = build.start_build(prepared['receipt']['id'])['id']
    try:
        assert entered.wait(5)
        with pytest.raises(build.PackageError) as error:
            build.start_build(prepared['receipt']['id'])
        assert error.value.status == 409
    finally: release.set()
    assert wait(build, first)['state'] == 'built'


def test_changed_preparation_is_rejected_before_docker(prepared, monkeypatch):
    from profile_manager.antithesis_preparation import state_directory
    build = module(); monkeypatch.setattr(build, '_docker', lambda *a, **k:pytest.fail('changed source built'))
    (state_directory()/prepared['receipt']['id']/'package/miniprotocol/workload/Dockerfile').write_text('FROM changed')
    with pytest.raises(build.PackageError, match='changed'):
        build.start_build(prepared['receipt']['id'])


def test_supplied_assets_are_not_built(tmp_path, monkeypatch):
    monkeypatch.setenv('ADA2_DWARF_STATE_DIR', str(tmp_path/'state'))
    root=tmp_path/'source';root.mkdir();(root/'docker-compose.yaml').write_text('services:\n  node:\n    build: .\n')
    result=prepare_package(case(package_source='existing'),{'compose_path':str(root),'image_references':[]})
    with pytest.raises(module().PackageError, match='generated'):
        module().start_build(result['receipt']['id'])


def test_wrong_image_architecture_is_not_success(prepared, monkeypatch):
    build=module(); run=fake_docker([])
    def wrong(argv, **kwargs):
        return run(argv, **kwargs).replace('amd64','arm64')
    monkeypatch.setattr(build,'_docker',wrong)
    identifier=build.start_build(prepared['receipt']['id'])['id']
    assert wait(build,identifier)['state']=='failed'


def test_result_tampering_is_detected(prepared,monkeypatch):
    build=module();monkeypatch.setattr(build,'_docker',fake_docker([]))
    identifier=build.start_build(prepared['receipt']['id'])['id'];assert wait(build,identifier)['state']=='built'
    (build.state_directory()/identifier/'package/docker-compose.yaml').write_text('services: {}')
    with pytest.raises(build.PackageError,match='changed'):build.read_build_result(identifier)


def test_api_build_status_result_and_no_local_execution(prepared, monkeypatch):
    from profile_manager.data.antithesis_package import dispatch_package_request
    from profile_manager import run_service, remote
    build=module();monkeypatch.setattr(build,'_docker',fake_docker([]))
    monkeypatch.setattr(run_service,'execute_run',lambda *a,**k:pytest.fail('Local execution'))
    monkeypatch.setattr(remote,'run_moog_create_test',lambda *a,**k:pytest.fail('MOOG submission'))
    def api(action, payload, method='POST'):
        status, _, raw = dispatch_package_request(method=method,path='/api/antithesis-run/package/'+action,
            body=json.dumps({'token':'test',**payload}).encode(),expected_token='test')
        return status,json.loads(raw)
    assert api('build',{'preparation_id':prepared['receipt']['id']},'GET')[0]==405
    status, body=api('build',{'preparation_id':prepared['receipt']['id']})
    assert status==202,body
    identifier=body['build']['id'];assert wait(build,identifier)['state']=='built'
    assert api('build-status',{'build_id':identifier})[1]['build']['state']=='built'
    assert api('build-result',{'build_id':identifier})[1]['preparation']['receipt']['state']=='built'
    assert api('build',{'preparation_id':prepared['receipt']['id'],'extra':1})[0]==400


def test_zero_build_snapshot_is_honest_without_running_docker(catalog,monkeypatch):
    from test_antithesis_package import request
    prepared=prepare_package(request(),{})
    build=module();monkeypatch.setattr(build,'_docker',lambda *a,**k:pytest.fail('no build required'))
    identifier=build.start_build(prepared['receipt']['id'])['id']
    status=wait(build,identifier);assert status['state']=='not-required'
    assert status['built_images']==[]
    assert build.read_build_result(identifier)['receipt']['execution_ready'] is False


@pytest.mark.parametrize('value',[{'context':'../outside'},{'context':'.','dockerfile':'/etc/passwd'},{'context':'.','args':{'X':'y'}},{'context':'.','dockerfile':'missing'}])
def test_generated_build_paths_and_options_reject_escape(value):
    files={'docker-compose.yaml':(yaml.safe_dump({'services':{'workload':{'build':value}}}).encode(),0o644)}
    with pytest.raises(module().PackageError):module()._plan(files)


def test_abandoned_job_is_interrupted_not_built(tmp_path):
    build=module();identifier='d'*32;directory=build.state_directory()/identifier;directory.mkdir(parents=True)
    (directory/'status.json').write_text(json.dumps({'id':identifier,'state':'building','built_images':[]}))
    assert build.read_build(identifier)['state']=='interrupted'


def test_docker_output_is_bounded_and_failure_does_not_expose_output(tmp_path):
    import sys
    build=module();log=tmp_path/'log'
    with pytest.raises(build.BuildFailure,match='exit 9') as error:
        build._docker([sys.executable,'-c','print("private-value"*50000);raise SystemExit(9)'],cwd=tmp_path,log_path=log,timeout=5)
    assert log.stat().st_size<=build.LOG_LIMIT
    assert log.stat().st_mode&0o777==0o600
    assert 'private-value' not in str(error.value)


def test_docker_timeout_is_bounded(tmp_path):
    import sys
    build=module()
    with pytest.raises(build.BuildFailure,match='timed out'):
        build._docker([sys.executable,'-c','import time;time.sleep(30)'],cwd=tmp_path,log_path=tmp_path/'log',timeout=.05)


def test_repeated_start_request_recovers_same_completed_job(prepared, monkeypatch):
    build=module();calls=[];monkeypatch.setattr(build,'_docker',fake_docker(calls));request_id='b'*32
    first=build.start_build(prepared['receipt']['id'],request_id=request_id)
    assert wait(build,first['id'])['state']=='built'
    second=build.start_build(prepared['receipt']['id'],request_id=request_id)
    assert second['id']==first['id'] and second['state']=='built'
    assert len(calls)==2
    with pytest.raises(build.PackageError,match='different preparation'):
        build.start_build('c'*32,request_id=request_id)


def test_built_receipt_supersedes_build_limitation(prepared,monkeypatch):
    build=module();monkeypatch.setattr(build,'_docker',fake_docker([]));job=build.start_build(prepared['receipt']['id'])
    assert wait(build,job['id'])['state']=='built'
    receipt=build.read_build_result(job['id'])['receipt']
    assert not any('Workload build,' in s for s in receipt['workload_binding']['limitations'])
    assert any('Antithesis execution remain unverified' in s for s in receipt['workload_binding']['limitations'])


def test_later_image_failure_retains_partial_success(catalog,monkeypatch):
    from profile_manager import antithesis_preparation as prep
    original=prep.preview_package
    def two_builds(value):
        result=original(value)
        body=yaml.safe_load(result['files']['environment/docker-compose.yaml'])
        body['services']['second-workload']={'build':dict(body['services']['sm-workload']['build'])}
        result['files']['environment/docker-compose.yaml']=yaml.safe_dump(body,sort_keys=False)
        return result
    monkeypatch.setattr(prep,'preview_package',two_builds)
    prepared=prepare_package(case(),{});build=module();calls=[]
    def second_fails(argv,**kwargs):
        if argv[1]=='build' and any(str(a).endswith('-1:prepared') for a in argv):
            raise build.BuildFailure('Second build failed.')
        return fake_docker(calls)(argv,**kwargs)
    monkeypatch.setattr(build,'_docker',second_fails)
    job=build.start_build(prepared['receipt']['id']);status=wait(build,job['id'])
    assert status['state']=='failed' and len(status['built_images'])==1
    assert status['built_images'][0]['service']=='sm-workload'
    with pytest.raises(build.PackageError):build.read_build_result(job['id'])


@pytest.mark.parametrize('changed,platform,child', [(False,'amd64','c'), (True,'amd64','c'), (False,'arm64','c'), (False,'amd64','b'), (False,'amd64',None)])
def test_native_node_index_is_verified_then_publishes_immutable_amd64_child(catalog, monkeypatch, changed, platform, child):
    from profile_manager import antithesis_artifacts as artifacts
    index_id='sha256:'+'b'*64
    child_id='sha256:'+child*64 if child else 'invalid'
    node={'id':'e'*32,'state':'complete','implementation':'amaru','version':'custom',
          'mode':'stock','reference':'dwarf-antithesis-native:prepared','runtime_interface':'extracted-binary',
          'image_id':index_id,'source_revision':None}
    monkeypatch.setattr(artifacts,'read',lambda identifier:node)
    request=case()
    request['versions'].update(artifacts={'amaru':node['id']})
    request['versions']['versions']['amaru']='custom'
    prepared=prepare_package(request,{})
    build=module();calls=[]
    fallback=fake_docker(calls)
    def docker(argv, **kwargs):
        if argv[1:3]==['image','inspect'] and argv[-1] in (node['reference'],index_id):
            calls.append(argv)
            if '--platform' in argv:
                assert argv[-1]==index_id, 'Resolve the verified immutable index, not its mutable tag'
                return json.dumps([{'Id':child_id,'Os':'linux','Architecture':platform}])
            return json.dumps([{'Id':IMAGE_ID if changed else index_id,'Os':'linux','Architecture':'amd64'}])
        return fallback(argv,**kwargs)
    monkeypatch.setattr(build,'_docker',docker)
    status=wait(build,build.start_build(prepared['receipt']['id'])['id'])
    if changed or platform!='amd64' or child is None:
        assert status['state']=='failed'
        assert status['built_images']==[]
    else:
        assert status['state']=='built',status
        row=next(r for r in status['built_images'] if r['service']=='amaru-target-extract')
        assert row['image_id']==child_id
        assert row['artifact_image_id']==index_id
        assert build.read_build_result(status['id'])['receipt']['version_binding']['node_artifacts']['amaru']['image_id']==index_id
