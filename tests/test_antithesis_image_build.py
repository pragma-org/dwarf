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


@pytest.fixture(autouse=True)
def fast_poll(monkeypatch):
    monkeypatch.setattr(module(), '_POLL_SECONDS', 0)


def fake_docker(calls):
    """Only the retained bounded CLI helper (`_docker`) is exercised with this
    in the dashboard; builds themselves go through the controller."""
    def run(argv, *, cwd, log_path, timeout):
        calls.append(argv)
        if argv[1:3] == ['image', 'inspect']:
            return json.dumps([{'Id':IMAGE_ID,'Os':'linux','Architecture':'amd64'}])
        assert argv[:2] == ['docker', 'build']
        assert argv[argv.index('--platform')+1] == 'linux/amd64'
        assert (Path(cwd)/'workload/Dockerfile').is_file()
        return ''
    return run


def fake_controller(calls, *, fail=None, rows=None, hold=None, node_child=None):
    """A controller that builds like controller/antithesis_publish does: rows for
    every build: service and every node artifact, all with IMAGE_ID. `fail` is a
    (code, detail) tuple ('partial' keeps the first row in progress); `rows`
    overrides the returned rows; `hold` is an Event the build waits on;
    `node_child` is the amd64 child id reported for node-artifact rows."""
    from profile_manager import antithesis_preparation as prep
    from profile_manager.antithesis_image_build import _plan
    executions = {}
    def controller(operation, parameters, timeout, idempotency_key=None):
        calls.append((operation, dict(parameters), timeout, idempotency_key))
        if operation == 'antithesis.image-build':
            if hold is not None:
                assert hold.wait(5)
            prepared = prep.read_preparation(parameters['preparation_id'])
            files, _ = prep._unpack(base64.b64decode(prepared['archive_base64']))
            compose, builds = _plan(files)
            built = []
            for artifact in prepared['receipt'].get('version_binding', {}).get('node_artifacts', {}).values():
                for name, service in compose['services'].items():
                    if service.get('image') == artifact['reference']:
                        built.append({'service': name, 'reference': artifact['reference'], 'image_id': node_child or IMAGE_ID,
                                      'artifact_image_id': artifact['image_id'], 'artifact_id': artifact['id'],
                                      'platform': 'linux/amd64', 'publication': 'local-only'})
            for index, item in enumerate(builds):
                built.append({'service': item['service'],
                              'reference': 'dwarf-antithesis-' + parameters['execution_id'].rsplit('-', 1)[-1] + '-' + str(index) + ':prepared',
                              'image_id': IMAGE_ID, 'platform': 'linux/amd64', 'publication': 'local-only'})
            if fail is not None:
                code, detail = fail
                executions[parameters['execution_id']] = {
                    'state': 'failed', 'failure': {'code': code, 'stage': 'image-build', 'message': 'canned ' + code},
                    'progress': {'status': 'failed', 'stage': 'build', 'detail': detail, 'built_images': built[:1] if code == 'partial' else []}}
            else:
                executions[parameters['execution_id']] = {
                    'state': 'applied',
                    'result': {'status': 'built', 'preparation_id': parameters['preparation_id'],
                               'built_images': rows if rows is not None else built, 'build_host': 'DWARF controller', 'build_count': len(builds)},
                    'progress': {'status': 'built', 'stage': 'complete'}}
            return {'status': 'accepted', 'execution_id': parameters['execution_id']}
        if operation == 'execution.status':
            return {'execution_id': parameters['execution_id'], **executions[parameters['execution_id']]}
        raise AssertionError(operation)
    return controller


def wait(build, identifier):
    for _ in range(300):
        result = build.read_build(identifier)
        if result['state'] not in ('queued', 'building'):
            return result
        time.sleep(.01)
    pytest.fail('worker did not finish')


def test_generated_build_materializes_separate_package_without_changing_selections(prepared, monkeypatch):
    build = module(); calls = []
    monkeypatch.setattr(build, '_docker', lambda *a, **k: pytest.fail('the dashboard must not run docker'))
    identifier = build.start_build(prepared['receipt']['id'], controller=fake_controller(calls))['id']
    status = wait(build, identifier)
    assert status['state'] == 'built', status
    assert status['execution_id'].startswith('antithesis-build-')
    assert calls[0][0] == 'antithesis.image-build' and calls[0][1]['preparation_id'] == prepared['receipt']['id']
    assert calls[0][2] == 3600 and calls[0][3] == calls[0][1]['execution_id']
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
    built = {name: row['reference'] for row in receipt['built_images'] for name in row.get('services', [row['service']])}
    expected = before['services']['sm-workload'].copy(); expected.pop('build')
    expected['image'] = built['sm-workload']
    assert after['services']['sm-workload'] == expected
    # Every controller-built service (with taps on by default: the measurement services too) is
    # rewritten to its built image; nothing else in the compose changes.
    for name, service in before['services'].items():
        if 'build' in service:
            service.pop('build'); service['image'] = built[name]
    assert after == before
    assert receipt['built_images'][0]['reference'].startswith('dwarf-antithesis-')
    assert [c[0] for c in calls] == ['antithesis.image-build', 'execution.status']
    assert receipt['build_host'] == 'DWARF controller' and receipt['execution_id'] == status['execution_id']
    assert all(archive.read(n) == original.read(n) for n in original.namelist() if n != 'docker-compose.yaml')
    assert read_preparation(prepared['receipt']['id']) == prepared
    assert not {'image-build-required','workload-image-build-required'} & {i['code'] for i in receipt['issues']}
    assert 'publication-required' in {i['code'] for i in receipt['issues']}


def test_authentication_precedes_any_build_work(prepared, monkeypatch):
    from profile_manager.data.antithesis_package import dispatch_package_request
    build = module()
    monkeypatch.setattr(build, 'start_build', lambda *a, **k: pytest.fail('unauthorized build'))
    status, _, _ = dispatch_package_request(method='POST', path='/api/antithesis-run/package/build',
        body=json.dumps({'token':'wrong','preparation_id':prepared['receipt']['id']}).encode(), expected_token='test')
    assert status == 403


def test_failed_build_does_not_produce_a_download_and_can_retry(prepared):
    build = module()
    first = build.start_build(prepared['receipt']['id'],
                              controller=fake_controller([], fail=('image-build-failed', 'sm-workload: ERROR: failed to solve')))['id']
    failed = wait(build, first)
    assert failed['state'] == 'failed' and failed['message'] == 'sm-workload: ERROR: failed to solve'
    with pytest.raises(build.PackageError, match='not complete'):
        build.read_build_result(first)
    second = build.start_build(prepared['receipt']['id'], controller=fake_controller([]))['id']
    assert second != first and wait(build, second)['state'] == 'built'


def test_concurrent_builds_are_rejected_and_source_copy_is_private(prepared):
    build = module(); release = threading.Event(); entered = threading.Event(); calls = []
    inner = fake_controller(calls, hold=release)
    def held(operation, parameters, timeout, idempotency_key=None):
        if operation == 'antithesis.image-build':
            entered.set()
        return inner(operation, parameters, timeout, idempotency_key)
    first = build.start_build(prepared['receipt']['id'], controller=held)['id']
    try:
        assert entered.wait(5)
        with pytest.raises(build.PackageError) as error:
            build.start_build(prepared['receipt']['id'], controller=held)
        assert error.value.status == 409
    finally:
        release.set()
    assert wait(build, first)['state'] == 'built'
    assert (build.state_directory()/first).stat().st_mode & 0o777 == 0o700


def test_changed_preparation_is_rejected_before_docker(prepared):
    from profile_manager.antithesis_preparation import state_directory
    build = module()
    (state_directory()/prepared['receipt']['id']/'package/miniprotocol/workload/Dockerfile').write_text('FROM changed')
    with pytest.raises(build.PackageError, match='changed'):
        build.start_build(prepared['receipt']['id'], controller=lambda *a, **k: pytest.fail('changed source built'))


def test_supplied_assets_are_not_built(tmp_path, monkeypatch):
    monkeypatch.setenv('ADA2_DWARF_STATE_DIR', str(tmp_path/'state'))
    root=tmp_path/'source';root.mkdir();(root/'docker-compose.yaml').write_text('services:\n  node:\n    build: .\n')
    result=prepare_package(case(package_source='existing'),{'compose_path':str(root),'image_references':[]})
    with pytest.raises(module().PackageError, match='generated'):
        module().start_build(result['receipt']['id'], controller=lambda *a, **k: pytest.fail('supplied asset built'))


def test_wrong_image_architecture_is_not_success(prepared):
    build=module()
    rows=[{'service':'sm-workload','reference':'dwarf-antithesis-'+'f'*32+'-0:prepared','image_id':IMAGE_ID,
           'platform':'linux/arm64','publication':'local-only'}]
    identifier=build.start_build(prepared['receipt']['id'],controller=fake_controller([],rows=rows))['id']
    assert wait(build,identifier)['state']=='failed'


def test_result_tampering_is_detected(prepared):
    build=module()
    identifier=build.start_build(prepared['receipt']['id'],controller=fake_controller([]))['id']
    assert wait(build,identifier)['state']=='built'
    (build.state_directory()/identifier/'package/docker-compose.yaml').write_text('services: {}')
    with pytest.raises(build.PackageError,match='changed'):build.read_build_result(identifier)


def test_api_build_status_result_and_no_local_execution(prepared, monkeypatch):
    from profile_manager.data.antithesis_package import dispatch_package_request
    from profile_manager import run_service, remote
    build=module();monkeypatch.setattr(build,'controller_request',fake_controller([]))
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


def test_zero_build_snapshot_is_honest_without_running_docker(catalog):
    from test_antithesis_package import request
    prepared=prepare_package(request(),{})
    build=module()
    identifier=build.start_build(prepared['receipt']['id'],controller=lambda *a,**k:pytest.fail('no build required'))['id']
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


def test_repeated_start_request_recovers_same_completed_job(prepared):
    build=module();calls=[];controller=fake_controller(calls);request_id='b'*32
    first=build.start_build(prepared['receipt']['id'],request_id=request_id,controller=controller)
    assert wait(build,first['id'])['state']=='built'
    second=build.start_build(prepared['receipt']['id'],request_id=request_id,controller=controller)
    assert second['id']==first['id'] and second['state']=='built'
    assert len(calls)==2
    with pytest.raises(build.PackageError,match='different preparation'):
        build.start_build('c'*32,request_id=request_id,controller=controller)


def test_built_receipt_supersedes_build_limitation(prepared):
    build=module();job=build.start_build(prepared['receipt']['id'],controller=fake_controller([]))
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
    prepared=prepare_package(case(),{});build=module()
    # The controller reports the rows it finished before the failure in its progress.
    job=build.start_build(prepared['receipt']['id'],controller=fake_controller([],fail=('partial','second-workload: build failed')))
    status=wait(build,job['id'])
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
    build=module()
    # The controller verifies the index and resolves the amd64 child; the
    # dashboard passes its rows through or records the failure.
    if changed or platform!='amd64' or child is None:
        controller=fake_controller([],fail=('artifact-image-missing','prepared node image is missing or changed'))
    else:
        controller=fake_controller([],node_child=child_id)
    status=wait(build,build.start_build(prepared['receipt']['id'],controller=controller)['id'])
    if changed or platform!='amd64' or child is None:
        assert status['state']=='failed'
        assert status['built_images']==[]
    else:
        assert status['state']=='built',status
        row=next(r for r in status['built_images'] if r['service']=='amaru-target-extract')
        assert row['image_id']==child_id
        assert row['artifact_image_id']==index_id
        assert build.read_build_result(status['id'])['receipt']['version_binding']['node_artifacts']['amaru']['image_id']==index_id


def test_build_never_invokes_docker_in_the_dashboard(prepared, monkeypatch):
    build=module();monkeypatch.setattr(build,'_docker',lambda *a,**k:pytest.fail('docker in the dashboard'))
    import subprocess
    monkeypatch.setattr(subprocess,'Popen',lambda *a,**k:pytest.fail('subprocess in the dashboard'))
    identifier=build.start_build(prepared['receipt']['id'],controller=fake_controller([]))['id']
    assert wait(build,identifier)['state']=='built'


def test_build_status_mirrors_controller_failure_detail(prepared):
    build=module()
    job=build.start_build(prepared['receipt']['id'],controller=fake_controller([],fail=('image-build-failed','sm-workload: no space left on device')))
    status=wait(build,job['id'])
    assert status['state']=='failed' and status['message']=='sm-workload: no space left on device'
    assert status['failure_code']=='image-build-failed'


def test_plan_reads_the_build_network_label():
    body={'services':{'dwarf-workload':{'build':{'context':'./dwarf-workload'},'labels':{'com.dwarf.build-network':'none'}},
                      'other':{'build':{'context':'./other'}}}}
    files={'docker-compose.yaml':(yaml.safe_dump(body).encode(),0o644),
           'dwarf-workload/Dockerfile':(b'FROM x',0o644),'other/Dockerfile':(b'FROM x',0o644)}
    _,builds=module()._plan(files)
    assert {b['service']:b['network'] for b in builds}=={'dwarf-workload':'none','other':None}


def test_plan_rejects_unknown_build_network():
    body={'services':{'w':{'build':{'context':'./w'},'labels':{'com.dwarf.build-network':'host'}}}}
    files={'docker-compose.yaml':(yaml.safe_dump(body).encode(),0o644),'w/Dockerfile':(b'FROM x',0o644)}
    with pytest.raises(module().PackageError,match='Unsupported build network'):module()._plan(files)


def test_services_that_share_a_dockerfile_share_one_build_and_one_registry_name(prepared, monkeypatch):
    """F179: with taps on by default the measurement helper services (dwarf-measurements and one
    measure-config-<node> per cardano node) all build ./measurements/Dockerfile. One build, one
    image, one GHCR name — not one public click per node name."""
    build = module(); calls = []
    identifier = build.start_build(prepared['receipt']['id'], controller=fake_controller(calls))['id']
    assert wait(build, identifier)['state'] == 'built'
    from profile_manager import antithesis_preparation as prep
    files, _ = prep._unpack(base64.b64decode(prepared['archive_base64']))
    _, plan = build._plan(files)   # the controller derives its plan from the same function
    measurement = [b for b in plan if b['context'] == 'measurements']
    assert len(measurement) == 1 and measurement[0]['service'] == 'dwarf-measurements'
    assert measurement[0]['aliases'] and all(a.startswith('measure-config-') for a in measurement[0]['aliases'])
    result = build.read_build_result(identifier)
    row = next(r for r in result['receipt']['built_images'] if r['service'] == 'dwarf-measurements')
    assert set(row['services']) == {'dwarf-measurements', *measurement[0]['aliases']}
    compose = yaml.safe_load(zipfile.ZipFile(io.BytesIO(base64.b64decode(result['archive_base64']))).read('docker-compose.yaml'))
    assert {compose['services'][n]['image'] for n in row['services']} == {row['reference']}
    assert all('build' not in compose['services'][n] for n in row['services'])
