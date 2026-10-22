import base64
import copy
import io
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

import pytest
import yaml

from test_antithesis_amaru_binding import context, selection
from test_antithesis_version_binding import catalog, request, generate, compose

SCENARIO = 'cardano-amaru-miniprotocol-security-local'
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'antithesis/cardano_amaru_miniprotocol_security'


def case(**changes):
    return request(scenario_id=SCENARIO, versions=selection('mixed')) | changes


def test_remote_compose_networks_and_helper_match_platform_contract(catalog):
    body = compose(generate(case()))
    assert all(network.get('internal', False) is False
               for network in body['networks'].values())
    for name, service in body['services'].items():
        assert service['container_name'] == name
        assert service['init'] is True
        assert '_' not in service['hostname']
    helper = body['services']['amaru-target-extract']
    assert helper['hostname'] == 'amaru-target-extract'
    assert helper['labels']['com.antithesis.exclude_from_faults'] == 'network,kill,pause,stop'


@pytest.mark.parametrize('version', ['10.11.20260918', '10.11.20260925', '10.11.0'])
def test_selected_versions_reach_workload_victims_and_existing_control(catalog, version):
    result = generate(case(versions=selection('mixed', version)))
    report = result['workload_binding']; body = compose(result); services = body['services']
    assert report['status'] == 'applied'
    assert report['runtime_verified'] is False
    assert report['scenario_id'] == SCENARIO
    assert report['targets'] == ['sm-cardano-victim', 'sm-amaru-victim']
    assert services['sm-cardano-victim']['image'] == services['relay1']['image']
    assert services['sm-amaru-victim']['image'] == services['amaru-relay-1']['image']
    source = yaml.safe_load((SOURCE/'docker-compose.yaml').read_text())['services']
    for target in report['targets']:
        for field in ('command','entrypoint','healthcheck'):
            assert services[target][field] == source[target][field]
        binding = next(s for s in result['version_binding']['services'] if s['service']==target)
        assert binding['status'] == 'applied'
        assert binding['role'] == 'target'
    if version != '10.11.0':
        victim = services['sm-amaru-victim']
        assert victim['environment']['AMARU_BIN'] == '/target/amaru'
        assert victim['depends_on']['amaru-target-extract']['condition'] == 'service_completed_successfully'
        assert {'type':'volume','source':'amaru-target-bin','target':'/target','read_only':True} in victim['volumes']
    for name in ('sm-cardano-fuzzer','sm-amaru-fuzzer'):
        assert services[name]['command'] == source[name]['command']
        assert services[name]['depends_on'] == source[name]['depends_on']
    assert services['sm-workload']['environment'] == source['sm-workload']['environment']
    assert services['sm-workload']['volumes'] == source['sm-workload']['volumes']
    assert 'prefault-ready' in services['sidecar']['command'][0]
    assert 'image' not in services['sm-workload']
    assert services['sm-workload']['build'] == {'context':'./miniprotocol', 'dockerfile':'workload/Dockerfile'}
    patches = {p['path']:p for p in report['source_patches']}
    assert set(patches) == {'environment/miniprotocol/workload/miniprotocol_observer.py',
        'environment/miniprotocol/workload/workload_entrypoint.py',
        'environment/miniprotocol/workload/test/v1/mixed-miniprotocol-security/anytime_containment.py',
        'environment/miniprotocol/workload/test/v1/mixed-miniprotocol-security/eventually_recovery.py'}
    for path in SOURCE.glob('workload/**/*.py'):
        emitted='environment/miniprotocol/'+str(path.relative_to(SOURCE))
        if emitted not in patches:
            assert result['files'][emitted] == path.read_text()
        else:
            import hashlib
            assert patches[emitted]['sha256']=='sha256:'+hashlib.sha256(result['files'][emitted].encode()).hexdigest()
    assert result['files']['environment/miniprotocol/workload/Dockerfile'] == (SOURCE/'workload/Dockerfile').read_text()
    fields = {r['field']:r for r in result['engine_handoff']['inputs']}
    assert fields['scenario_id']['status'] == 'partial'
    assert 'workload' in fields['scenario_id']['detail'].lower()
    assert result['engine_handoff']['renderer'] == 'control-harness'
    codes = {i['code'] for i in result['issues']}
    assert 'scenario-harness-workload-not-implemented' not in codes
    assert 'scenario-local-orchestration-not-translated' in codes
    assert 'workload-image-build-required' in codes
    assert not result['execution_ready']


def test_explicit_mixed_topology_and_unknown_versions_preserve_intent(catalog):
    result = generate(case(**context('mixed'), versions=selection('mixed', 'experiment')))
    assert result['workload_binding']['status'] == 'applied'
    assert result['version_binding']['status'] == 'partial'
    rows = {s['service']:s for s in result['version_binding']['services']}
    assert rows['sm-amaru-victim']['status'] == 'unchanged'
    assert rows['sm-cardano-victim']['status'] == 'applied'
    assert 'amaru-target-extract' not in compose(result)['services']


@pytest.mark.parametrize('change', [dict(scenario_id='amaru-cbor-block-fuzz'), context('amaru')])
def test_unrelated_scenario_or_amaru_only_context_does_not_gain_paired_workload(catalog, change):
    result = generate(case(**change))
    assert result['workload_binding']['status'] == 'recorded'
    assert 'sm-workload' not in compose(result)['services']
    assert 'scenario-harness-workload-not-implemented' in {i['code'] for i in result['issues']}


@pytest.mark.parametrize('name', ['docker-compose.yaml','workload/miniprotocol_observer.py', 'workload/test/v1/mixed-miniprotocol-security/anytime_containment.py','sm-cardano-topology.json'])
def test_changed_recipe_leaves_no_partial_workload_mutation(catalog,tmp_path,monkeypatch,name):
    from profile_manager import antithesis_workload_binding as workload
    source = tmp_path/'source'; shutil.copytree(SOURCE,source)
    (source/name).write_text((source/name).read_text()+'\n# drift\n')
    monkeypatch.setattr(workload,'SOURCE',source)
    result = generate(case())
    assert result['workload_binding']['status'] == 'recorded'
    assert 'workload-source-contract-changed' in {i['code'] for i in result['issues']}
    assert 'sm-workload' not in compose(result)['services']
    assert not any('/miniprotocol/' in p for p in result['files'])
    assert result['version_binding']['status'] == 'applied'


def test_changed_scenario_does_not_reuse_old_semantics(catalog,monkeypatch):
    from profile_manager import antithesis_package as package
    from dataclasses import replace
    original = package.load_definition
    def changed(kind, identifier):
        record = original(kind,identifier)
        if identifier == SCENARIO:
            body = json.loads(record.source); body['seed']='0x123'
            record = replace(record,source=json.dumps(body).encode())
        return record
    monkeypatch.setattr(package,'load_definition',changed)
    result = generate(case())
    assert result['workload_binding']['status'] == 'recorded'
    assert 'workload-scenario-contract-changed' in {i['code'] for i in result['issues']}
    assert 'sm-workload' not in compose(result)['services']


def test_staging_preserves_required_build_inputs_receipt_and_compose(catalog,tmp_path):
    from profile_manager.antithesis_preparation import prepare_package, read_preparation
    result = prepare_package(case(),{},state_dir=tmp_path/'state')
    receipt = result['receipt']
    assert receipt['workload_binding']['status']=='applied'
    archive = zipfile.ZipFile(io.BytesIO(base64.b64decode(result['archive_base64'])))
    archive.extractall(tmp_path/'package')
    body = yaml.safe_load(archive.read('docker-compose.yaml'))
    assert body['services']['sm-workload']['build']['context']=='./miniprotocol'
    assert archive.read('miniprotocol/workload/Dockerfile') == (SOURCE/'workload/Dockerfile').read_bytes()
    assert any(b['service']=='sm-workload' for b in receipt['build_requirements'])
    assert not {'missing-input','missing-image'} & {i['code'] for i in receipt['issues']}
    assert read_preparation(receipt['id'],state_dir=tmp_path/'state')['receipt']==receipt
    assert not receipt['execution_ready'] and not receipt['submission_ready']
    checked = subprocess.run(['docker','compose','-f',str(tmp_path/'package/docker-compose.yaml'),'config','--quiet'],capture_output=True,text=True)
    assert checked.returncode == 0, checked.stderr


def test_no_execution_on_workload_prepare(catalog,monkeypatch,tmp_path):
    from scripts import qualify_node_versions as q
    from profile_manager import run_plan,run_service,remote
    from profile_manager.antithesis_preparation import prepare_package
    def forbidden(*a,**kw):raise AssertionError('Execution invoked')
    for module,name in [(q,'_run'),(q,'run_qualification'),(run_plan,'resolve_run_plan'),(run_service,'execute_run'),(remote,'run_moog_create_test')]:
        monkeypatch.setattr(module,name,forbidden)
    assert prepare_package(case(),{},state_dir=tmp_path)['receipt']['workload_binding']['status']=='applied'


def test_generated_observer_reads_json_measurement_trace_tips(catalog):
    import types
    generated=generate(case())
    observer=types.ModuleType('json_tip_observer')
    exec(compile(generated['files']['environment/miniprotocol/workload/miniprotocol_observer.py'],'observer.py','exec'),observer.__dict__)
    text=json.dumps({'fields':{'message':'tip.adopt','slot':1413,'block_height':282,'header_hash':'a'*64}})
    assert observer.parse_amaru_tip(text)=={'status':'ok','slot':1413,'block':282,'hash':'a'*64}
    assert observer.parse_amaru_tip(json.dumps({'fields':{'message':'tip.adopt','slot':True,'block_height':282,'header_hash':'a'*64}}))['status']=='unavailable'


@pytest.mark.parametrize('tip',[{},[],{'slot':True,'block':1,'hash':'a'*64},
    {'slot':1,'block':-1,'hash':'a'*64},{'slot':'1','block':1,'hash':'a'*64},
    {'slot':1,'block':1,'hash':'bad'}, {'slot':1,'block':1,'hash':'a'*64}])
def test_generated_cardano_tip_rejects_missing_or_malformed_values(catalog,monkeypatch,tip):
    import types
    source=generate(case())['files']['environment/miniprotocol/workload/miniprotocol_observer.py']
    observer=types.ModuleType('checked_tip_observer');exec(compile(source,'observer.py','exec'),observer.__dict__)
    monkeypatch.setattr(observer.subprocess,'run',lambda *a,**kw:types.SimpleNamespace(returncode=0,stdout=json.dumps(tip),stderr=''))
    expected='ok' if tip=={'slot':1,'block':1,'hash':'a'*64} and type(tip['slot']) is int else 'unavailable'
    assert observer.query_tip('fixture')['status']==expected


def test_generated_setup_requires_real_baseline_tips_and_clears_stale_ready(catalog,tmp_path,monkeypatch):
    import sys,types
    fake=types.ModuleType('miniprotocol_observer')
    fake.compare_prefault_streams=lambda *a:{'matched':True,'complete_coverage':True}
    fake.parse_amaru_tip=lambda *a:{'status':'unavailable'}
    fake.parse_fuzzer_log=lambda *a:{'injections':[],'seed':'0x1'}
    fake.query_tip=lambda *a:{'status':'ok','slot':1}
    fake.read_text=lambda *a:''
    fake.tcp_reachable=lambda *a:True
    monkeypatch.setitem(sys.modules,'miniprotocol_observer',fake)
    generated=generate(case());entry=types.ModuleType('generated_entry')
    exec(compile(generated['files']['environment/miniprotocol/workload/workload_entrypoint.py'],'entry.py','exec'),entry.__dict__)
    for key in ('CARDANO_FUZZER_LOG','AMARU_FUZZER_LOG','CARDANO_TARGET_HOST','AMARU_TARGET_HOST','CONTROL_P1_SOCKET_PATH','CONTROL_CONSUMER_SOCKET_PATH','CARDANO_SOCKET_PATH','AMARU_RUNTIME_LOG'):
        monkeypatch.setenv(key,'fixture')
    for key in ('CARDANO_TARGET_PORT','AMARU_TARGET_PORT'):monkeypatch.setenv(key,'3001')
    monkeypatch.setenv('STATUS_DIR',str(tmp_path));monkeypatch.setenv('DWARF_SM_SEED','0x1');monkeypatch.setenv('PREFAULT_TIMEOUT_SECONDS','1')
    (tmp_path/'prefault-ready').write_text('stale')
    clock=iter([0,.1,2])
    def sleep(seconds):
        if seconds==60:pytest.fail('Unavailable baseline incorrectly reached readiness')
    entry.time=types.SimpleNamespace(monotonic=lambda:next(clock),sleep=sleep)
    assert entry.main()==3
    assert not (tmp_path/'prefault-ready').exists()
    assert not (tmp_path/'prefault.json').exists()


@pytest.mark.parametrize('command', ['parallel-observer','anytime','recovery'])
@pytest.mark.parametrize('evidence', ['complete','missing-logs','missing-cardano-runtime','missing-amaru-runtime','mismatched-prefault','fatal-target','ahead-tips','behind-tips','different-hash','fatal-ahead-tips','fatal-unreachable','fatal-cardano-missing-amaru'])
def test_generated_observer_requires_classifiable_evidence(catalog,tmp_path,monkeypatch,evidence,command):
    import types
    generated = generate(case())
    source = generated['files']['environment/miniprotocol/workload/miniprotocol_observer.py']
    observer = types.ModuleType('prepared_observer')
    exec(compile(source,'prepared_observer.py','exec'),observer.__dict__)
    stream = [{'protocol':p,'class':c,'legal_prefix':1,'frames':2}
              for p in observer.PROTOCOLS for c in observer.DEPARTURE_CLASSES]
    transcript = 'seed: using explicit seed 0x20260907\n' + '\n'.join(
        f"state-machine-init[{i['protocol']}]: dialing; injecting departure={i['class']} legalPrefix=1 frames=2"
        for i in stream*2)
    paths = {'CARDANO_FUZZER_LOG':'c-fuzz','AMARU_FUZZER_LOG':'a-fuzz',
             'AMARU_RUNTIME_LOG':'a-runtime','CARDANO_RUNTIME_LOG':'c-runtime'}
    for key,name in paths.items():monkeypatch.setenv(key,str(tmp_path/name))
    if evidence != 'missing-logs':
        for name in ('c-fuzz','a-fuzz'):(tmp_path/name).write_text(transcript)
        (tmp_path/'c-runtime').write_text('cardano-node: panicked at invariant' if evidence in ('fatal-target','fatal-ahead-tips','fatal-unreachable','fatal-cardano-missing-amaru') else 'node running')
        (tmp_path/'a-runtime').write_text('tip.adopt slot=100 header_hash="'+'a'*64+'" block_height=100')
    if evidence=='missing-cardano-runtime':(tmp_path/'c-runtime').unlink()
    if evidence in ('missing-amaru-runtime','fatal-cardano-missing-amaru'):(tmp_path/'a-runtime').unlink()
    monkeypatch.setenv('STATUS_DIR',str(tmp_path))
    prefault=observer.compare_prefault_streams(stream,stream)
    if evidence=='mismatched-prefault':prefault.update(matched=False,complete_coverage=False)
    (tmp_path/'prefault.json').write_text(json.dumps({'comparison':prefault,'counts':{'cardano':24,'amaru':24},
        'tips':{name:{'slot':10} for name in ('p1','consumer','cardano','amaru')}}))
    for key in ('CARDANO_SOCKET_PATH','CONTROL_P1_SOCKET_PATH','CONTROL_P2_SOCKET_PATH','CONTROL_P3_SOCKET_PATH','CONTROL_CONSUMER_SOCKET_PATH'):
        monkeypatch.setenv(key,key)
    for target in ('CARDANO','AMARU'):
        monkeypatch.setenv(target+'_TARGET_HOST',target);monkeypatch.setenv(target+'_TARGET_PORT','3001')
    def query_tip(path):
        if path=='CONTROL_CONSUMER_SOCKET_PATH' and evidence in ('ahead-tips','behind-tips','different-hash','fatal-ahead-tips'):
            height=99 if evidence=='behind-tips' else 100 if evidence=='different-hash' else 101
            return {'status':'ok','slot':height,'block':height,'hash':'b'*64}
        return {'status':'ok','slot':100,'block':100,'hash':'a'*64}
    monkeypatch.setattr(observer,'query_tip',query_tip)
    monkeypatch.setattr(observer,'tcp_reachable',lambda *a:evidence!='fatal-unreachable')
    emitted=[];coverage=[]
    monkeypatch.setattr(observer,'sometimes',lambda condition,name,details:coverage.append((name,condition)))
    monkeypatch.setattr(observer,'always',lambda condition,name,details:emitted.append((name,condition)))
    result=observer.observe_runtime()
    if command=='parallel-observer':
        observer.emit_assertions(result,'test')
    else:
        import sys
        sdk=types.ModuleType('antithesis.assertions')
        sdk.always=lambda condition,name,details:emitted.append((name,condition))
        sdk.sometimes=(lambda condition,name,details:emitted.append((name,condition))) if command=='recovery' else (lambda *a:None)
        parent=types.ModuleType('antithesis');parent.assertions=sdk
        monkeypatch.setitem(sys.modules,'antithesis',parent)
        monkeypatch.setitem(sys.modules,'antithesis.assertions',sdk)
        monkeypatch.setitem(sys.modules,'miniprotocol_observer',observer)
        filename='eventually_recovery.py' if command=='recovery' else 'anytime_containment.py'
        if command=='recovery':
            import time
            clock=iter([0,1,151]);monkeypatch.setattr(time,'monotonic',lambda:next(clock));monkeypatch.setattr(time,'sleep',lambda *a:None)
            monkeypatch.delenv('ANTITHESIS_STOP_FAULTS',raising=False)
        path='environment/miniprotocol/workload/test/v1/mixed-miniprotocol-security/'+filename
        exec(compile(generated['files'][path],path,'exec'),{})
    if command=='recovery':
        assert ('mixed_sm_targets_and_consumer_recovered',evidence=='complete') in emitted
        return
    if evidence in ('missing-logs','missing-cardano-runtime','missing-amaru-runtime','mismatched-prefault'):
        assert result['classifiable'] is False and result['safe'] is None
        if evidence=='missing-logs':assert not emitted
        elif evidence=='mismatched-prefault':
            assert ('mixed_sm_cardano_no_panic_or_fatal_termination',True) in emitted
            assert not any(name=='mixed_sm_illegal_sessions_contained' for name,_ in emitted)
        elif evidence=='missing-cardano-runtime':assert not any('cardano_no_panic' in name for name,_ in emitted)
        elif evidence=='missing-amaru-runtime':assert not any('amaru_no_panic' in name for name,_ in emitted)
    elif evidence in ('fatal-unreachable','fatal-cardano-missing-amaru'):
        assert result['classifiable'] is False
        assert ('mixed_sm_cardano_no_panic_or_fatal_termination',False) in emitted
        assert not any(name=='mixed_sm_illegal_sessions_contained' for name,_ in emitted)
    elif evidence in ('ahead-tips','behind-tips','different-hash','fatal-ahead-tips'):
        assert result['classifiable'] is True
        assert result['convergence_observation']['status']=='unavailable'
        assert result['inputs']['converged'] is False
        if command=='parallel-observer':
            assert ('mixed_sm_convergence_observation_available',False) in coverage
        assert result['safe'] is (False if evidence=='fatal-ahead-tips' else None)
        if evidence=='fatal-ahead-tips':
            assert ('mixed_sm_cardano_no_panic_or_fatal_termination',False) in emitted
        else:
            assert not any(name=='mixed_sm_illegal_sessions_contained' for name,_ in emitted)
            assert ('mixed_sm_cardano_no_panic_or_fatal_termination',True) in emitted
    elif evidence=='fatal-target':
        assert result['classifiable'] is True and result['safe'] is False
        assert ('mixed_sm_cardano_no_panic_or_fatal_termination',False) in emitted
    else:
        assert result['classifiable'] is True and result['safe'] is True
        assert (('mixed_sm_illegal_sessions_contained' if command=='parallel-observer' else 'mixed_sm_cardano_no_panic_or_fatal_termination'),True) in emitted


def test_changed_control_sidecar_cannot_silently_be_replaced(catalog,monkeypatch):
    from profile_manager import antithesis_topology as topology
    original=topology.compile_topology
    def drift(identifier):
        value=original(identifier)
        value['compose']['services']['sidecar']['environment']['NETWORKMAGIC']='99'
        return value
    monkeypatch.setattr(topology,'compile_topology',drift)
    result=generate(case())
    assert result['workload_binding']['status']=='recorded'
    assert 'sm-workload' not in compose(result)['services']
    assert compose(result)['services']['sidecar']['environment']['NETWORKMAGIC']=='99'


def test_same_request_has_same_package_digest_across_hash_seeds(tmp_path):
    import os
    program = ('import json,sys; from profile_manager.antithesis_package import preview_package; '
               'print(preview_package(json.load(sys.stdin))["package_digest"])')
    digests=[]
    for seed in ('1','2'):
        result=subprocess.run(['python3','-c',program],input=json.dumps(case()),capture_output=True,text=True,
            env={**os.environ,'PYTHONHASHSEED':seed,'ADA2_DWARF_STATE_DIR':str(tmp_path)})
        assert result.returncode==0,result.stderr
        digests.append(result.stdout.strip())
    assert digests[0]==digests[1]


@pytest.mark.parametrize('available', [True,False])
def test_packaged_recovery_does_not_claim_success_without_runtime_evidence(catalog,monkeypatch,available):
    import sys,types,time
    generated=generate(case())
    # All legacy recovery predicates are true; only actual runtime evidence is missing.
    observation={'classifiable':available,'runtime_log_available':{'cardano':available,'amaru':True},
        'inputs':{'recovered':{'cardano':True,'amaru':True,'consumer':True},'converged':True,
        'control_progress':True,'post_setup_counts':{'cardano':4,'amaru':4},'fatal':{'cardano':False,'amaru':False}},'tips':{}}
    observer=types.ModuleType('miniprotocol_observer');observer.observe_runtime=lambda:copy.deepcopy(observation)
    emitted=[];sdk=types.ModuleType('antithesis.assertions');sdk.sometimes=lambda condition,name,details:emitted.append((name,condition))
    parent=types.ModuleType('antithesis');parent.assertions=sdk
    for name,value in [('miniprotocol_observer',observer),('antithesis',parent),('antithesis.assertions',sdk)]:monkeypatch.setitem(sys.modules,name,value)
    clock=iter([0,1,151]);monkeypatch.setattr(time,'monotonic',lambda:next(clock));monkeypatch.setattr(time,'sleep',lambda *a:None)
    monkeypatch.delenv('ANTITHESIS_STOP_FAULTS',raising=False)
    path='environment/miniprotocol/workload/test/v1/mixed-miniprotocol-security/eventually_recovery.py'
    exec(compile(generated['files'][path],path,'exec'),{})
    assert ('mixed_sm_targets_and_consumer_recovered',available) in emitted
