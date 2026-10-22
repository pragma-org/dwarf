import base64
import io
import json
import zipfile

import pytest
import yaml


def request(**changes):
    return dict(package_source='build', topology={'source':'scenario-default'}, topology_digest=None,
        versions={'policy':'profile-default','versions':{},'build_variant':'stock','experimental_intent':False},
        scenario_id='cardano-node-cbor-block-header-fuzz', measurements={'profile_id':None,'tap_ids':[]},
        duration_hours=1, faults='off') | changes


def generate(value=None):
    from profile_manager.antithesis_package import preview_package
    return preview_package(value or request())


def test_native_submission_network_is_not_internal():
    result = generate()
    body = yaml.safe_load(result['files']['environment/docker-compose.yaml'])
    assert all(network.get('internal', False) is False
               for network in body['networks'].values())


def test_preview_contains_real_environment_and_no_false_assertion_driver(tmp_path, monkeypatch):
    monkeypatch.setenv('ADA2_DWARF_STATE_DIR',str(tmp_path/'state'))
    result=generate()
    assert result['state']=='incomplete' and result['execution_ready'] is False
    files=result['files']
    compose=yaml.safe_load(files['environment/docker-compose.yaml'])
    assert 'dwarf-adversary' in compose['services']
    assert 'decoder-fuzz' not in compose['services']
    assert 'decoder-fuzz-applyblock' not in compose['services']
    for service in compose['services'].values():
        for mount in service.get('volumes',[]):
            origin=mount.split(':')[0]
            if origin.startswith('./'):
                assert 'environment/'+origin[2:] in files
    assert not any('parallel_driver' in name for name in files)
    assert any(i['code']=='assertion-observation-not-implemented' for i in result['issues'])
    assert any(i['code']=='default-version-binding-not-implemented' for i in result['issues'])
    assert 'inputs/scenario.yaml' in files
    assert not list(tmp_path.rglob('*'))
    archive=zipfile.ZipFile(io.BytesIO(base64.b64decode(result['archive_base64'])))
    assert set(archive.namelist())==set(files)
    assert all(archive.read(name)==content.encode() for name,content in files.items())
    assert result==generate()


def test_unknown_experimental_version_is_preserved_not_an_eligibility_gate():
    chosen={'policy':'exact','versions':{'cardano-node':'my-experiment'},'build_variant':'instrumented','experimental_intent':True}
    result=generate(request(versions=chosen))
    manifest=json.loads(result['files']['package-manifest.json'])
    assert manifest['request']['versions']==chosen
    assert {'version-binding-not-implemented','patched-build-not-implemented'} <= {i['code'] for i in result['issues']}
    assert all(i.get('kind')=='implementation-gap' for i in result['issues'])


def test_unsupported_scenario_returns_explicit_gap_not_silent_fallback():
    result=generate(request(scenario_id='cardano-node-cov-handshake-aflpp-smoke'))
    assert 'environment/docker-compose.yaml' not in result['files']
    assert any(i['code']=='scenario-generator-not-implemented' for i in result['issues'])
    assert 'inputs/scenario.yaml' in result['files']


def test_selected_measurements_are_snapshotted_and_not_claimed_collected():
    from profile_manager.data.catalog_definitions import list_definitions
    tap=list_definitions('measurements')[0].definition_id
    result=generate(request(measurements={'profile_id':None,'tap_ids':[tap]}))
    assert 'inputs/measurements/'+tap.replace('-patched-','-instrumented-')+'.yaml' in result['files']
    assert any(i['code']=='collector-not-implemented' and i['field'].endswith(tap) for i in result['issues'])


def test_topology_override_is_kept_separate_and_stale_digest_is_rejected():
    from profile_manager.antithesis_topology import resolve_topology
    from profile_manager.antithesis_package import PackageError
    selected={'source':'new','name':'My rig','template_id':'cardano-control-v1','cardano_count':2,'amaru_count':0,'network_magic':42}
    resolved=resolve_topology(selected)
    result=generate(request(topology=selected,topology_digest=resolved['topology_digest']))
    assert 'environment/docker-compose.yaml' not in result['files']
    assert yaml.safe_load(result['files']['topology-preview/docker-compose.yaml'])==resolved['compose']
    assert 'topology-preview/source-docker-compose.yaml' in result['files']
    assert len(result['images'])==9
    assert all(i['source']=='topology-preview/docker-compose.yaml' for i in result['images'])
    assert any(i['code']=='topology-scenario-merge-not-implemented' for i in result['issues'])
    with pytest.raises(PackageError,match='Topology changed'):
        generate(request(topology={**selected,'name':'edited'},topology_digest=resolved['topology_digest']))


def test_changed_request_changes_package_identity_and_keeps_requested_fault_policy():
    first=generate();second=generate(request(duration_hours=9,faults='on'))
    assert first['package_digest']!=second['package_digest']
    manifest=json.loads(second['files']['package-manifest.json'])
    assert manifest['request']['faults']=='on' and manifest['request']['duration_hours']==9
    assert manifest['compatibility']=='not-evaluated'


@pytest.mark.parametrize('change',[
    {'package_source':'url'}, {'duration_hours':True}, {'duration_hours':0}, {'duration_hours':1.5},
    {'scenario_id':'../../etc/passwd'}, {'faults':[]}, {'extra':'ignored'},
    {'measurements':{'profile_id':None,'tap_ids':['x','x']}}, {'topology':{'source':'local-profile','profile_id':[]}},
    {'versions':{'policy':'exact','versions':{},'build_variant':'stock','experimental_intent':'yes'}},
])
def test_invalid_or_unimplemented_input_returns_typed_error(change):
    from profile_manager.antithesis_package import PackageError
    with pytest.raises(PackageError): generate(request(**change))


def test_missing_scenario_is_actionable():
    from profile_manager.antithesis_package import PackageError
    with pytest.raises(PackageError,match='scenario'): generate(request(scenario_id=None))


@pytest.mark.parametrize('body',[b'{',b'{"x":NaN}',b'{"x":1e999}',b'{"x":'+b'1'*5000+b'}',b'['*600+b'0'+b']'*600,b'\xff'])
def test_api_malformed_input_is_json_400(body):
    from profile_manager.data.antithesis_package import dispatch_package_request
    status,_,raw=dispatch_package_request(method='POST',path='/api/antithesis-run/package/preview',body=body)
    assert status==400 and json.loads(raw)['ok'] is False


def test_api_does_not_capture_local_routes_and_limits_method_body():
    from profile_manager.data.antithesis_package import dispatch_package_request as dispatch
    assert dispatch(method='POST',path='/api/run/prepare',body=b'{}') is None
    assert dispatch(method='GET',path='/api/antithesis-run/package/preview')[0]==405
    assert dispatch(method='POST',path='/api/antithesis-run/package/preview',body=b'x'*32769)[0]==413
    assert dispatch(method='POST',path='/api/antithesis-run/package/other')[0]==404
    status,_,raw=dispatch(method='POST',path='/api/antithesis-run/package/preview',body=json.dumps(request()).encode())
    assert status==200 and json.loads(raw)['package']['execution_ready'] is False


def test_invalid_generator_yaml_returns_structured_unavailable(monkeypatch):
    from types import SimpleNamespace
    from profile_manager import antithesis_package
    from profile_manager.data.antithesis_package import dispatch_package_request
    monkeypatch.setattr(antithesis_package.native,'render_bundle',lambda *args,**kw:SimpleNamespace(files={'config/docker-compose.yaml':'services: ['}))
    status,_,raw=dispatch_package_request(method='POST',path='/api/antithesis-run/package/preview',body=json.dumps(request()).encode())
    assert status==503 and json.loads(raw)['ok'] is False


def test_handoff_describes_actual_native_output_and_reuses_renderer_once(monkeypatch):
    from profile_manager import antithesis_package
    original = antithesis_package.native.render_bundle
    calls = []

    def render(*args, **kwargs):
        calls.append(args[0].id)
        return original(*args, **kwargs)

    monkeypatch.setattr(antithesis_package.native, 'render_bundle', render)
    result = generate()
    handoff = result['engine_handoff']
    assert calls == [request()['scenario_id']]
    assert handoff['renderer'] == 'native-scenario'
    assert handoff['renderer_invoked'] is True
    assert handoff['environment_emitted'] is True
    assert handoff['adversary'] == {'protocol':'chainsync', 'shape':'block-header'}
    compose = yaml.safe_load(result['files']['environment/docker-compose.yaml'])
    assert {s['name']:s['image'] for s in handoff['services']} == {
        name:service.get('image') for name,service in compose['services'].items()}
    assert handoff['config_files'] == sorted(
        name for name in result['files'] if name.startswith('environment/') and not name.endswith('docker-compose.yaml'))
    fields = {row['field']:row for row in handoff['inputs']}
    assert fields['scenario_id']['status'] == 'partial'
    assert fields['topology']['status'] == 'applied'
    assert fields['package_source']['status'] == 'applied'
    assert set(fields) == {'package_source','scenario_id','topology','versions','build_variant',
                           'experimental_intent','measurements','duration_hours','faults'}
    assert json.loads(result['files']['package-manifest.json'])['engine_handoff'] == handoff


def test_version_taps_and_submission_intent_are_recorded_without_changing_environment():
    from profile_manager.data.catalog_definitions import list_definitions
    tap = list_definitions('measurements')[0].definition_id
    baseline = generate()
    selected = request(
        versions={'policy':'exact','versions':{'cardano-node':'unqualified-experiment'},
                  'build_variant':'instrumented','experimental_intent':True},
        measurements={'profile_id':None,'tap_ids':[tap]}, duration_hours=7, faults='on')
    result = generate(selected)
    assert result['files']['environment/docker-compose.yaml'] == baseline['files']['environment/docker-compose.yaml']
    fields = {row['field']:row for row in result['engine_handoff']['inputs']}
    for field in ('versions','build_variant','experimental_intent','measurements','duration_hours','faults'):
        assert fields[field]['status'] == 'recorded'
    assert all(path in result['files'] for row in fields.values() for path in row['evidence'])
    assert json.loads(result['files']['package-manifest.json'])['request'] == selected
    assert result['execution_ready'] is False


def test_unsupported_scenario_handoff_cannot_claim_applied_environment():
    result = generate(request(scenario_id='cardano-node-cov-handshake-aflpp-smoke'))
    handoff = result['engine_handoff']
    assert handoff['renderer_invoked'] is True
    assert handoff['environment_emitted'] is False
    assert handoff['services'] == [] and handoff['adversary'] is None
    fields = {row['field']:row for row in handoff['inputs']}
    assert fields['scenario_id']['status'] == 'recorded'
    assert fields['topology']['status'] == 'recorded'


def test_override_handoff_does_not_claim_native_renderer_or_applied_scenario(monkeypatch):
    from profile_manager import antithesis_package, antithesis_topology
    def forbidden(*args, **kwargs):
        raise AssertionError('Unmerged override must not invoke the native renderer')
    monkeypatch.setattr(antithesis_package.native, 'render_bundle', forbidden)
    chosen = {'source':'new','name':'My rig','template_id':'cardano-control-v1',
              'cardano_count':2,'amaru_count':0,'network_magic':42}
    resolved = antithesis_topology.resolve_topology(chosen)
    result = generate(request(topology=chosen, topology_digest=resolved['topology_digest']))
    handoff = result['engine_handoff']
    assert handoff['renderer_invoked'] is False
    assert handoff['environment_emitted'] is False
    assert handoff['services'] == [] and handoff['config_files'] == []
    fields = {row['field']:row for row in handoff['inputs']}
    assert fields['topology']['status'] == 'recorded'
    assert fields['scenario_id']['status'] == 'recorded'


def test_handoff_protocol_and_shape_follow_emitted_command_not_scenario_label(monkeypatch):
    from profile_manager import antithesis_package
    original = antithesis_package.native.render_bundle

    def render(*args, **kwargs):
        generated = original(*args, **kwargs)
        path = 'config/docker-compose.yaml'
        compose = yaml.safe_load(generated.files[path])
        command = compose['services']['dwarf-adversary']['command']
        command[command.index('--protocol') + 1] = 'blockfetch'
        command[command.index('--cbor-shape') + 1] = 'block'
        generated.files[path] = yaml.safe_dump(compose)
        return generated

    monkeypatch.setattr(antithesis_package.native, 'render_bundle', render)
    result = generate()
    assert result['engine_handoff']['adversary'] == {'protocol':'blockfetch','shape':'block'}
