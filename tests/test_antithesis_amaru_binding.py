import base64
import copy
import io
import json
import zipfile

import pytest
import yaml

from test_antithesis_version_binding import catalog, chosen, compose, generate, request
from profile_manager import antithesis_topology as topology


def context(scope):
    value = dict(source='new', name='Version experiment', template_id=scope+'-control-v1',
                 cardano_count=2 if scope=='mixed' else 0, amaru_count=2, network_magic=42)
    resolved = topology.resolve_topology(value)
    return {'topology':value, 'topology_digest':resolved['topology_digest']}


def selection(scope, version='10.11.20260918', **changes):
    return chosen(versions={'amaru':version, **({'cardano-node':'10.6.2'} if scope=='mixed' else {})}, **changes)


def case(scope='amaru', **changes):
    return request(scenario_id='amaru-cbor-block-fuzz', versions=selection(scope), **context(scope)) | changes


@pytest.mark.parametrize('scope', ['amaru','mixed'])
@pytest.mark.parametrize('version', ['10.11.20260918','10.11.20260925'])
def test_versions_reach_actual_binaries_and_preserve_support_harness(catalog, scope, version):
    result=generate(case(scope, versions=selection(scope,version)))
    report=result['version_binding'];body=compose(result)
    baseline=topology.resolve_topology(context(scope)['topology'])['compose']
    assert report['status']=='applied' and report['source']=='control-harness'
    assert report['target_contract']['evidence']=='emitted-configuration'
    assert report['target_contract']['scope']==('mixed' if scope=='mixed' else 'amaru-only')
    selected=next(r for r in catalog['releases'] if r['implementation']=='amaru' and r['version']==version)
    from profile_manager.antithesis_versions import _artifact
    image=_artifact(selected['artifacts'][0])['pull_reference']
    assert body['services']['amaru-target-extract']['image']==image
    for name in ('amaru-relay-1','amaru-relay-2'):
        service=body['services'][name]
        assert service['image']==baseline['services'][name]['image']
        assert service['entrypoint']=='amaru-relay-bootstrap'
        assert service['environment']['AMARU_BIN']=='/target/amaru'
        assert service['depends_on']['amaru-target-extract']['condition']=='service_completed_successfully'
        assert any(isinstance(m,dict) and m['source']=='amaru-target-bin' and m['target']=='/target' and m['read_only'] for m in service['volumes'])
        binding=next(s for s in report['services'] if s['service']==name)
        assert binding['requested_version']==version and binding['status']=='applied'
        assert binding['target_binary']['image']==image
        assert binding['target_binary']['path']=='/target/amaru'
        assert binding['bootstrap_image']==baseline['services'][name]['image']
    targets={'amaru-relay-1','amaru-relay-2'} | ({'relay1','relay2'} if scope=='mixed' else set())
    assert {s['service'] for s in report['services'] if s['role']=='target'}==targets
    for name,service in baseline['services'].items():
        if name not in targets:
            assert body['services'][name]==service
    if scope=='mixed':
        assert body['services']['relay1']['image']==body['services']['relay2']['image']
        assert body['services']['relay1']['image']!=baseline['services']['relay1']['image']
    assert body['networks']==baseline['networks']
    assert 'x-dwarf-qualification' not in body
    assert all(not any(k.startswith('com.dwarf.qualification') for k in s.get('labels',{})) for s in body['services'].values())
    for path,digest in topology.resolve_topology(context(scope)['topology'])['input_digests'].items():
        if path!='docker-compose.yaml':
            assert result['files']['environment/'+path]==(topology.ASSET_ROOT/path).read_text()
    assert json.loads(result['files']['package-manifest.json'])['version_binding']==report
    assert next(i for i in result['images'] if i['service']=='amaru-target-extract')['reference']==image
    assert report['runtime_identity']=='unverified' and report['compatibility']=='not-evaluated'
    assert result['execution_ready'] is False


@pytest.mark.parametrize('scope', ['amaru','mixed'])
@pytest.mark.parametrize('policy', ['exact','latest-stable','latest-confirmed','profile-default'])
def test_assessment_and_preparation_share_choices(catalog,scope,policy):
    from profile_manager.antithesis_versions import resolve_versions
    value=case(scope)
    if policy!='exact':value['versions']=selection(scope,policy=policy);value['versions']['versions']={}
    assessment=resolve_versions({**value['versions'],**context(scope),'scenario_id':value['scenario_id']})
    generated=generate(value)
    assert generated['version_binding']['release_resolution']==assessment['release_resolution']
    assert generated['version_binding']['status']==('recorded' if policy=='profile-default' else 'applied')


@pytest.mark.parametrize('scenario_id,scope', [('amaru-cbor-block-fuzz','amaru-only'),
    ('cardano-amaru-relay-bootstrap-control-local','mixed')])
def test_default_harness_binds_versions_without_claiming_scenario_workload(catalog,scenario_id,scope):
    from profile_manager.antithesis_versions import resolve_versions
    v=selection('mixed' if scope=='mixed' else 'amaru')
    value=request(scenario_id=scenario_id,versions=v)
    result=generate(value); report=result['version_binding']
    assert report['status']=='applied'
    assert report['target_contract']['scope']==scope
    assert result['engine_handoff']['renderer']=='control-harness'
    assert result['engine_handoff']['renderer_invoked'] is True
    assert result['engine_handoff']['adversary'] is None
    fields={r['field']:r for r in result['engine_handoff']['inputs']}
    assert fields['scenario_id']['status']=='recorded'
    assert fields['versions']['status']=='applied'
    assert 'scenario-harness-workload-not-implemented' in {i['code'] for i in result['issues']}
    assert 'dwarf-adversary' not in compose(result)['services']
    assessment=resolve_versions({**v,'topology':value['topology'],'topology_digest':None,'scenario_id':scenario_id})
    assert assessment['release_resolution']==report['release_resolution']


@pytest.mark.parametrize('scope', ['amaru','mixed'])
def test_staged_archive_receipt_and_readback_preserve_effective_targets(catalog,tmp_path,scope):
    from profile_manager.antithesis_preparation import prepare_package,read_preparation
    result=prepare_package(case(scope),{},state_dir=tmp_path/'state')
    receipt=result['receipt']
    assert receipt['version_binding']['status']=='applied'
    archive=zipfile.ZipFile(io.BytesIO(base64.b64decode(result['archive_base64'])))
    body=yaml.safe_load(archive.read('docker-compose.yaml'))
    assert body['services']['amaru-target-extract']['image']==next(s for s in receipt['version_binding']['services'] if s['service']=='amaru-relay-1')['target_binary']['image']
    assert archive.read('amaru-runtime/era-history.json')
    assert not {'missing-input','missing-compose'} & {i['code'] for i in receipt['issues']}
    assert read_preparation(receipt['id'],state_dir=tmp_path/'state')['receipt']==receipt
    assert not receipt['execution_ready'] and not receipt['submission_ready']


@pytest.mark.parametrize('scope', ['amaru','mixed'])
def test_unknown_release_never_claims_applied_or_substitutes(catalog,scope):
    result=generate(case(scope,versions=selection(scope,'experiment')))
    assert result['version_binding']['status']==('partial' if scope=='mixed' else 'recorded')
    assert 'unknown-release' in {i['code'] for i in result['issues']}
    assert 'amaru-target-extract' not in compose(result)['services']
    assert all(s['status']=='unchanged' for s in result['version_binding']['services'] if s.get('implementation')=='amaru')


def test_instrumented_intent_retains_baseline_without_claiming_measurement_support(catalog):
    result=generate(case('mixed',versions=selection('mixed',build_variant='instrumented')))
    assert result['version_binding']['status']=='recorded'
    assert compose(result)==topology.resolve_topology(context('mixed')['topology'])['compose']
    assert 'patched-build-not-implemented' in {i['code'] for i in result['issues']}


def test_legacy_wrapper_selection_keeps_its_own_bootstrap_contract(catalog):
    result=generate(case(versions=selection('amaru','10.11.0')))
    assert result['version_binding']['status']=='applied'
    assert 'amaru-target-extract' not in compose(result)['services']
    assert all(s['target_binary']['mode']=='legacy-wrapper' for s in result['version_binding']['services'] if s['role']=='target')


def test_unrecognised_image_layout_is_an_adapter_gap_not_a_compatibility_gate(catalog):
    release=next(r for r in catalog['releases'] if r['implementation']=='amaru' and r['version']=='10.11.20260918')
    release['artifacts']=[{'kind':'oci','reference':'custom/amaru:experiment','availability':'unknown'}]
    result=generate(case())
    assert result['version_binding']['status']=='recorded'
    assert 'amaru-runtime-interface-not-implemented' in {i['code'] for i in result['issues']}


def test_saved_configuration_uses_same_binding(catalog,tmp_path,monkeypatch):
    chosen_topology=context('mixed');value=chosen_topology['topology']
    monkeypatch.setattr(topology,'state_directory',lambda:tmp_path)
    saved=topology.save_configuration(value,chosen_topology['topology_digest'])
    result=generate(case('mixed',topology={'source':'saved','configuration_id':saved['id']}))
    assert result['version_binding']['status']=='applied'


def test_preparation_does_not_call_local_execution_or_qualification(catalog,monkeypatch,tmp_path):
    from scripts import qualify_node_versions as q
    from profile_manager import run_plan,run_service,remote
    from profile_manager.antithesis_preparation import prepare_package
    def forbidden(*a,**kw):raise AssertionError('Execution invoked')
    for module,name in [(q,'_run'),(q,'run_qualification'),(run_plan,'resolve_run_plan'),(run_service,'execute_run'),(remote,'run_moog_create_test')]:
        monkeypatch.setattr(module,name,forbidden)
    assert prepare_package(case('mixed'),{},state_dir=tmp_path)['receipt']['version_binding']['status']=='applied'


@pytest.mark.parametrize('drift', ['wrapper','wrapper-image','peer','consumer-network'])
def test_changed_control_contract_does_not_claim_binding(catalog,monkeypatch,drift):
    original=topology.compile_topology
    def changed(identifier):
        result=original(identifier);services=result['compose']['services']
        if drift=='wrapper':services['amaru-relay-1']['entrypoint']='/bin/true'
        elif drift=='wrapper-image':services['amaru-relay-2']['image']='busybox:latest'
        elif drift=='peer':services['amaru-relay-1']['environment']['AMARU_PEER']='missing:3001'
        else:services['amaru-consumer']['networks']={'default':None}
        return result
    monkeypatch.setattr(topology,'compile_topology',changed)
    result=generate(case())
    assert result['version_binding']['status']=='recorded'
    assert 'harness-target-contract-changed' in {i['code'] for i in result['issues']}
    assert 'amaru-target-extract' not in compose(result)['services']


@pytest.mark.parametrize('drift', ['binary-path','source-image','dependency','volume','command','exception'])
def test_changed_assembly_does_not_apply_partial_changes(catalog,monkeypatch,drift):
    from scripts import qualify_node_versions as assembly
    original=assembly.transform_compose_model
    def changed(*args,**kwargs):
        if drift=='exception':raise assembly.QualificationError('changed interface')
        result=original(*args,**kwargs);relay=result['services']['amaru-relay-1']
        if drift=='binary-path':relay['environment']['AMARU_BIN']='/bin/other'
        elif drift=='source-image':result['services']['amaru-target-extract']['image']='busybox:latest'
        elif drift=='dependency':relay['depends_on'].pop('amaru-target-extract')
        elif drift=='volume':relay['volumes']=[m for m in relay['volumes'] if isinstance(m,str)]
        elif drift=='command':result['services']['amaru-target-extract']['command']=['true']
        return result
    monkeypatch.setattr(assembly,'transform_compose_model',changed)
    result=generate(case())
    assert result['version_binding']['status']=='recorded'
    assert 'harness-assembly-contract-changed' in {i['code'] for i in result['issues']}
    assert compose(result)==topology.resolve_topology(context('amaru')['topology'])['compose']


def test_catalog_failure_keeps_control_environment_and_explicit_gap(monkeypatch):
    from profile_manager import antithesis_versions
    def unavailable():raise OSError('catalog unavailable')
    monkeypatch.setattr(antithesis_versions,'load_effective_version_catalog',unavailable)
    result=generate(case())
    assert result['version_binding']['status']=='recorded'
    assert 'version-catalog-unavailable' in {i['code'] for i in result['issues']}


def test_declared_unknown_availability_is_not_a_remote_qualification_gate(catalog):
    release=next(r for r in catalog['releases'] if r['implementation']=='amaru' and r['version']=='10.11.20260918')
    release['artifacts']=[{'kind':'oci','reference':'ghcr.io/pragma-org/amaru:experiment','availability':'unknown'}]
    result=generate(case())
    assert result['version_binding']['status']=='applied'
    assert compose(result)['services']['amaru-target-extract']['image']=='ghcr.io/pragma-org/amaru:experiment'
    assert 'unverified-image-reference' in {i['code'] for i in result['issues']}


def test_failed_mutating_assembly_does_not_leak_into_partial_mixed_output(catalog,monkeypatch):
    from scripts import qualify_node_versions as assembly
    def changed(model,**kwargs):
        model['services']['amaru-relay-1']['environment']['AMARU_BIN']='/bin/other'
        raise assembly.QualificationError('mutated before failure')
    monkeypatch.setattr(assembly,'transform_compose_model',changed)
    result=generate(case('mixed'))
    assert result['version_binding']['status']=='partial'
    baseline=topology.resolve_topology(context('mixed')['topology'])['compose']
    assert compose(result)['services']['amaru-relay-1']==baseline['services']['amaru-relay-1']
    assert compose(result)['services']['relay1']['image']!=baseline['services']['relay1']['image']


@pytest.mark.parametrize('change',['early-exit','user','profile'])
def test_extractor_cannot_succeed_without_producing_the_binary(catalog,monkeypatch,change):
    from scripts import qualify_node_versions as assembly
    original=assembly.transform_compose_model
    def changed(*args,**kwargs):
        result=original(*args,**kwargs);service=result['services']['amaru-target-extract']
        if change=='early-exit':service['command'][0]='exit 0; '+service['command'][0]
        elif change=='user':service['user']='1000:1000'
        else:service['profiles']=['disabled']
        return result
    monkeypatch.setattr(assembly,'transform_compose_model',changed)
    result=generate(case())
    assert result['version_binding']['status']=='recorded'
    assert 'harness-assembly-contract-changed' in {i['code'] for i in result['issues']}
