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


def test_instrumentation_off_is_recorded_as_submission_intent_only():
    result=generate(request(instrumentation='off'))
    manifest=json.loads(result['files']['package-manifest.json'])
    assert manifest['request']['instrumentation']=='off'
    fields={row['field']:row for row in manifest['engine_handoff']['inputs']}
    assert fields['instrumentation']['status']=='recorded'
    assert 'instrumentation' not in json.loads(generate()['files']['package-manifest.json'])['request']


def test_changed_request_changes_package_identity_and_keeps_requested_fault_policy():
    first=generate();second=generate(request(duration_hours=9,faults='on'))
    assert first['package_digest']!=second['package_digest']
    manifest=json.loads(second['files']['package-manifest.json'])
    assert manifest['request']['faults']=='on' and manifest['request']['duration_hours']==9
    assert manifest['compatibility']=='not-evaluated'


@pytest.mark.parametrize('change',[
    {'package_source':'url'}, {'duration_hours':True}, {'duration_hours':0}, {'duration_hours':1.5},
    {'scenario_id':'../../etc/passwd'}, {'faults':[]}, {'extra':'ignored'}, {'instrumentation':'maybe'},
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


def _node_select(body, expected_token='right'):
    from profile_manager.data.antithesis_package import dispatch_package_request
    status, _, payload = dispatch_package_request(method='POST', path='/api/antithesis-run/package/node-select',
                                                  body=json.dumps(body).encode(), expected_token=expected_token)
    return status, json.loads(payload)


def test_node_select_starts_adapter_and_returns_202(monkeypatch):
    from profile_manager import antithesis_artifacts as a
    from profile_manager.data import antithesis_package as endpoint
    registration_id = 'registration-release-build-' + '1' * 32 + '-stock'
    calls = []
    def from_registration(rid, **kw):
        calls.append((rid, kw.get('implementation')))
        return {'id': 'a' * 32, 'state': 'building', 'implementation': 'amaru', 'registration_id': rid, 'message': 'Adapting prepared release'}
    monkeypatch.setattr(a, 'from_registration', from_registration)
    monkeypatch.setattr(endpoint, '_prepared_registration', lambda rid: {'implementation': 'amaru', 'source_build': {'variant': 'stock'}})
    status, body = _node_select({'token': 'right', 'implementation': 'amaru', 'registration_id': registration_id})
    assert status == 202 and body['ok'] is True and body['artifact']['id'] == 'a' * 32
    assert calls == [(registration_id, 'amaru')]


def test_node_select_rejects_implementation_mismatch(monkeypatch):
    from profile_manager import antithesis_artifacts as a
    from profile_manager.data import antithesis_package as endpoint
    monkeypatch.setattr(a, 'from_registration', lambda rid, **kw: pytest.fail('adapter must not start'))
    monkeypatch.setattr(endpoint, '_prepared_registration', lambda rid: {'implementation': 'cardano-node', 'source_build': {'variant': 'stock'}})
    status, body = _node_select({'token': 'right', 'implementation': 'amaru', 'registration_id': 'registration-release-build-' + '1' * 32 + '-stock'})
    assert status == 422 and body['error']['field'] == 'registration_id'


def test_node_select_requires_token():
    status, body = _node_select({'token': 'wrong', 'implementation': 'amaru', 'registration_id': 'registration-x'})
    assert status == 403


def test_node_start_action_is_gone():
    from profile_manager.data.antithesis_package import dispatch_package_request
    status, _, payload = dispatch_package_request(method='POST', path='/api/antithesis-run/package/node-start',
                                                  body=b'{"token":"right"}', expected_token='right')
    assert status == 404


def test_node_select_unknown_registration_is_422_with_its_own_message(monkeypatch):
    """Review Focus 1: a registration deleted between listing and click must be
    named as missing, not reported as 'a different implementation'."""
    from profile_manager import antithesis_artifacts as a
    from profile_manager.data import antithesis_package as endpoint
    monkeypatch.setattr(a, 'from_registration', lambda rid, **kw: pytest.fail('adapter must not start'))
    monkeypatch.setattr(endpoint, '_prepared_registration', lambda rid: None)
    rid = 'registration-release-build-' + '3' * 32 + '-stock'
    status, body = _node_select({'token': 'right', 'implementation': 'amaru', 'registration_id': rid})
    assert status == 422 and body['error']['field'] == 'registration_id'
    assert 'no longer in the operator ledger' in body['error']['message'] and rid in body['error']['message']
    assert 'different implementation' not in body['error']['message']


def test_node_select_rejects_registrations_without_source_build(monkeypatch):
    """Only operator-prepared builds are offered by the wizard; an inspected
    catalog image registration (no source_build) must be refused here, before
    any controller call."""
    from profile_manager import antithesis_artifacts as a
    from profile_manager.data import antithesis_package as endpoint
    monkeypatch.setattr(a, 'from_registration', lambda rid, **kw: pytest.fail('adapter must not start'))
    monkeypatch.setattr(endpoint, '_prepared_registration', lambda rid: {'implementation': 'amaru'})
    status, body = _node_select({'token': 'right', 'implementation': 'amaru', 'registration_id': 'registration-release-build-' + '4' * 32 + '-stock'})
    assert status == 422 and body['error']['field'] == 'registration_id'
    assert 'prepared build' in body['error']['message'].lower()


def test_publication_push_shape_accepts_an_optional_one_time_token(monkeypatch):
    from profile_manager import antithesis_publication
    from profile_manager.data.antithesis_package import dispatch_package_request
    seen = {}
    def start_operation(identifier, action, *, target=None, request_id=None, publish_token=None, save_token=False):
        seen.update(identifier=identifier, action=action, publish_token=publish_token, save_token=save_token)
        return {'id': request_id, 'handoff_id': identifier, 'action': action, 'target': target, 'state': 'queued', 'submission_ready': False}
    monkeypatch.setattr(antithesis_publication, 'start_operation', start_operation)
    body = {'token': 'right', 'handoff_id': 'a' * 32, 'action': 'push', 'target': None, 'request_id': 'b' * 32,
            'publish_token': 'ghp_once', 'save_token': True}
    status, _, payload = dispatch_package_request(method='POST', path='/api/antithesis-run/package/publication-action',
                                                  body=json.dumps(body).encode(), expected_token='right')
    assert status == 202 and seen == {'identifier': 'a' * 32, 'action': 'push', 'publish_token': 'ghp_once', 'save_token': True}
    assert 'ghp_once' not in payload.decode()
    without = {k: v for k, v in body.items() if k not in ('publish_token', 'save_token')}
    status, _, _ = dispatch_package_request(method='POST', path='/api/antithesis-run/package/publication-action',
                                            body=json.dumps(without).encode(), expected_token='right')
    assert status == 202 and seen['publish_token'] is None and seen['save_token'] is False


def _mixed_versions():
    from test_antithesis_amaru_binding import selection
    return selection('mixed')


def test_devnet_scenario_previews_through_the_generated_workload(monkeypatch):
    import copy
    from profile_manager import antithesis_versions
    from profile_manager.version_catalog import load_version_catalog
    data = load_version_catalog()
    monkeypatch.setattr(antithesis_versions, 'load_effective_version_catalog', lambda: copy.deepcopy(data))
    result = generate(request(scenario_id='runtime-substrate-mempool-failure-containment-mixed-amaru', versions=_mixed_versions()))
    assert result['workload_binding']['source'] == 'dwarf-workload'
    assert result['workload_binding']['status'] == 'applied', result['workload_binding']['issues']
    assert result['workload_binding']['template'] == 'scenario-rig' and result['workload_binding']['rig']['shape'] == 'mixed'
    assert result['version_binding']['source'] == 'generated-rig' and result['version_binding']['status'] == 'applied'
    assert result['capabilities']['level'] == 'full'   # every step proven in a package (2026-11-11 smokes)
    assert 'environment/dwarf-workload/Dockerfile' in result['files']
    compose = yaml.safe_load(result['files']['environment/docker-compose.yaml'])
    # Taps are on by default (F178): the measurement services ride along; the rig itself is unchanged.
    assert {'configurator', 'tracer', 'log-tailer', 'node1', 'node2', 'amaru-target-extract', 'dwarf-workload', 'dwarf-measurements'} <= set(compose['services'])
    assert compose['services']['node2']['environment']['AMARU_PEER'] == 'node1.example:3001'
    rows = {r['field']: r for r in result['engine_handoff']['inputs']}
    assert rows['scenario_id']['status'] == 'applied'
    assert 'dwarf-workload' in rows['scenario_id']['detail']
    assert rows['topology']['status'] == 'applied' and 'scenario devnet' in rows['topology']['detail'].lower()
    assert result['engine_handoff']['renderer'] == 'scenario-rig'
    manifest = json.loads(result['files']['package-manifest.json'])
    assert manifest['capabilities'] == result['capabilities']
    codes = {issue['code'] for issue in result['issues']}
    assert not codes & {'unproven-in-package', 'primitive-unsupported-on-antithesis'}
    assert 'scenario-harness-workload-not-implemented' not in codes
    assert any(image['service'] == 'dwarf-workload' for image in result['images'])
    assert 'environment/testnet.yaml' in result['files'] and 'environment/topology/node1.json' in result['files']


def test_profile_scenario_without_versions_is_recorded_with_binding_gaps():
    # The m3 profile scenarios drive host node sessions and are refused since F170; this one stays runnable.
    result = generate(request(scenario_id='phase3-runtime-generated-haskell3-capability-demo'))
    assert result['workload_binding']['source'] == 'dwarf-workload'
    assert result['capabilities']['level'] == 'partial'
    assert 'environment/dwarf-workload/package/runtime.json' in result['files']
    assert result['version_binding']['source'] == 'generated-rig'
    assert result['engine_handoff']['renderer'] == 'scenario-rig'


def _catalog(monkeypatch):
    import copy
    from profile_manager import antithesis_versions
    from profile_manager.version_catalog import load_version_catalog
    data = load_version_catalog()
    monkeypatch.setattr(antithesis_versions, 'load_effective_version_catalog', lambda: copy.deepcopy(data))


def test_cardano_only_scenario_starts_only_cardano_containers(monkeypatch):
    _catalog(monkeypatch)
    versions = {'policy': 'exact', 'versions': {'cardano-node': '10.7.1'}, 'build_variant': 'stock', 'experimental_intent': False}
    result = generate(request(scenario_id='phase3-runtime-generated-haskell3-capability-demo', versions=versions))
    assert result['version_binding']['status'] == 'applied', result['version_binding']['issues']
    assert result['workload_binding']['status'] == 'applied' and result['workload_binding']['rig']['shape'] == 'cardano-only'
    compose = yaml.safe_load(result['files']['environment/docker-compose.yaml'])
    assert {'configurator', 'tracer', 'log-tailer', 'node1', 'node2', 'node3', 'dwarf-workload', 'dwarf-measurements'} <= set(compose['services'])
    assert {s for s in compose['services'] if s.startswith('node')} == {'node1', 'node2', 'node3'}
    assert not any('amaru' in name for name in compose['services'])
    assert result['files']['environment/testnet.yaml'].count('poolCount: 3') == 1
    assert compose['services']['dwarf-workload']['environment']['DWARF_ANTITHESIS_READY_TIMEOUT'] == '900'
    assert result['workload_binding']['targets'] == ['node1', 'node2', 'node3']
    # A cardano-only scenario whose load is unsupported still renders its rig, without the workload.
    none = generate(request(scenario_id='m3-runtime-blockfetch-port-drop-timeout', versions=versions))
    assert none['capabilities']['level'] == 'none' and none['workload_binding']['status'] == 'recorded'
    assert set(yaml.safe_load(none['files']['environment/docker-compose.yaml'])['services']) == {'configurator', 'tracer', 'log-tailer', 'node1', 'node2', 'node3'}


def test_amaru_only_scenario_starts_its_supporting_producer_and_amaru(monkeypatch):
    _catalog(monkeypatch)
    versions = {'policy': 'exact', 'versions': {'amaru': '10.11.20260918'}, 'build_variant': 'stock', 'experimental_intent': False}
    result = generate(request(scenario_id='client-example-txsubmission-window-amaru-portable-20260918', versions=versions))
    assert result['version_binding']['status'] == 'applied', result['version_binding']['issues']
    compose = yaml.safe_load(result['files']['environment/docker-compose.yaml'])
    # The scenario declares its own measurement taps, so the collector service rides along.
    assert set(compose['services']) == {'configurator', 'tracer', 'log-tailer', 'bootstrap-cardano', 'amaru1', 'amaru-target-extract',
                                        'dwarf-workload', 'dwarf-measurements'}
    # Some scenario taps have no remote hook (pre-existing 'partial'); the connected ones ride the rig's own evidence volume.
    assert result['measurement_binding']['status'] in ('applied', 'partial'), result['measurement_binding']['issues']
    assert 'amaru1-evidence:/inputs/amaru1:ro' in compose['services']['dwarf-measurements']['volumes']
    assert compose['services']['dwarf-workload']['environment']['DWARF_ANTITHESIS_READY_TIMEOUT'] == '2400'
    assert result['workload_binding']['targets'] == ['amaru1']
    assert 'unused-target-selection' not in {i['code'] for i in result['issues']}


def test_public_network_scenario_is_refused_without_a_compose():
    result = generate(request(scenario_id='runtime-substrate-multihost-honest-baseline-example-smoke'))
    assert result['capabilities']['level'] == 'none' and result['capabilities']['reason'] == 'public-network'
    assert result['workload_binding']['status'] == 'recorded' and result['workload_binding']['source'] == 'dwarf-workload'
    assert 'environment/docker-compose.yaml' not in result['files']
    assert 'public-network' in {i['code'] for i in result['issues']}
    assert result['engine_handoff']['renderer'] is None


def test_attached_scenario_keeps_the_control_rig_with_a_victim_clone(monkeypatch):
    _catalog(monkeypatch)
    result = generate(request(scenario_id='cardano-amaru-relay-bootstrap-control-local', versions=_mixed_versions()))
    assert result['workload_binding']['status'] == 'applied', result['workload_binding']['issues']
    assert result['workload_binding']['template'] == 'mixed-control-v1'
    assert result['version_binding']['source'] == 'control-harness' and result['engine_handoff']['renderer'] == 'control-harness'
    compose = yaml.safe_load(result['files']['environment/docker-compose.yaml'])
    assert {'sm-cardano-seed', 'p1', 'amaru-relay-1', 'dwarf-workload'} <= set(compose['services'])
    assert ('sm-amaru-victim' in compose['services']) or ('sm-cardano-victim' in compose['services'])


def test_library_scenario_keeps_the_native_path_and_reports_no_faultable_sut():
    result = generate(request(scenario_id='cardano-node-cbor-block-header-fuzz'))
    assert result['capabilities']['level'] == 'none'
    assert result['capabilities']['reason'] == 'no-faultable-sut'
    assert result['workload_binding']['source'] == 'none'
    assert any(issue['code'] == 'scenario-capability-none' for issue in result['issues'])
    compose = yaml.safe_load(result['files']['environment/docker-compose.yaml'])
    assert 'dwarf-adversary' in compose['services'] and 'dwarf-workload' not in compose['services']


def test_curated_scenario_keeps_the_curated_binder(monkeypatch):
    import copy
    from profile_manager import antithesis_versions
    from profile_manager.version_catalog import load_version_catalog
    data = load_version_catalog()
    monkeypatch.setattr(antithesis_versions, 'load_effective_version_catalog', lambda: copy.deepcopy(data))
    result = generate(request(scenario_id='cardano-amaru-miniprotocol-security-local', versions=_mixed_versions()))
    assert result['workload_binding']['source'] == 'curated-miniprotocol'
    assert result['workload_binding']['status'] == 'applied'
    assert 'environment/dwarf-workload/Dockerfile' not in result['files']
