import copy
import json
from pathlib import Path

import pytest
import yaml

from profile_manager import antithesis_generated_rig as rig
from profile_manager.antithesis_topology import TopologyError
from profile_manager.scenario import scenario_from_body

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / 'dwarf' / 'scenarios'


def body(**changes):
    base = {'spec_version': 'v1', 'id': 'rig-case', 'title': 'rig', 'authors': ['t'], 'tags': [],
            'target': {'implementation': 'cardano-node', 'version': 'any'}, 'runtime': 'devnet',
            'load': [{'primitive': 'runtime_multi_node_observation', 'runtime_metadata_path': 'outputs/substrate-compose/runtime.json',
                      'node_ids': ['node1'], 'observation_window_seconds': 1, 'sample_interval_seconds': 1,
                      'network_magic': 42, 'observation_primitives': ['tip_state'], 'output_dir': 'outputs/obs'}],
            'assertions': [{'primitive': 'all_nodes_responsive'}]}
    return scenario_from_body(json.dumps(base | changes).encode())


def substrate(*nodes, network='testnet_42', edges=None):
    return {'compose_mode': 'docker', 'network': network,
            'nodes': [{'id': i, 'impl': impl, 'version': 'resolved-plan', 'identity_binding': 'resolved-plan', 'role': 'observer'}
                      for i, impl in nodes],
            'topology': {'edges': edges or []}}


def scenario_file(identifier):
    return scenario_from_body((SCENARIOS / (identifier + '.yaml')).read_bytes())


# ---- Task 1: scenario devnet description -----------------------------------

def test_substrate_devnet_keeps_declared_nodes_edges_and_faults_the_target_implementation():
    edges = [{'from': 'node1', 'to': 'node2'}, {'from': 'node2', 'to': 'node1'}, {'from': 'node3', 'to': 'node1'}]
    scenario = body(substrate=substrate(('node1', 'cardano-node'), ('node2', 'cardano-node'), ('node3', 'amaru'), edges=edges))
    devnet = rig.scenario_devnet(scenario)
    assert devnet['shape'] == 'mixed' and devnet['network'] == 'testnet_42' and devnet['issues'] == []
    assert [(n['id'], n['impl'], n['role'], n['supporting']) for n in devnet['nodes']] == [
        ('node1', 'cardano-node', 'observer', False), ('node2', 'cardano-node', 'observer', False), ('node3', 'amaru', 'observer', False)]
    assert devnet['edges'] == edges
    assert devnet['faultable'] == ['node1', 'node2'] and devnet['target'] == 'cardano-node'


def test_substrate_without_edges_is_a_full_mesh():
    scenario = body(substrate=substrate(('node1', 'cardano-node'), ('node2', 'cardano-node')))
    assert rig.scenario_devnet(scenario)['edges'] == [{'from': 'node1', 'to': 'node2'}, {'from': 'node2', 'to': 'node1'}]


def test_public_network_substrate_is_refused():
    scenario = body(substrate=substrate(('node1', 'cardano-node'), ('node2', 'cardano-node'), network='preview'))
    devnet = rig.scenario_devnet(scenario)
    assert devnet['shape'] == 'none' and [i['code'] for i in devnet['issues']] == ['public-network']
    assert devnet['issues'][0]['kind'] == 'implementation-gap' and devnet['issues'][0]['field'] == 'scenario_id'


def test_cardano_profile_expands_to_producers_in_a_mesh():
    devnet = rig.scenario_devnet(body(profile='profile-a-haskell-peersharing-disabled'))
    assert devnet['shape'] == 'cardano-only' and devnet['network'] == 'testnet_42'
    assert [(n['id'], n['impl'], n['role'], n['supporting']) for n in devnet['nodes']] == [
        ('node1', 'cardano-node', 'producer', False), ('node2', 'cardano-node', 'producer', False), ('node3', 'cardano-node', 'producer', False)]
    assert len(devnet['edges']) == 6 and devnet['faultable'] == ['node1', 'node2', 'node3']


def test_amaru_only_profile_adds_the_supporting_producer_first():
    scenario = body(profile='profile-z-amaru-20260918-nanoseconds-v3', target={'implementation': 'amaru', 'version': 'any'})
    devnet = rig.scenario_devnet(scenario)
    assert devnet['shape'] == 'amaru-only'
    assert [(n['id'], n['impl'], n['role'], n['supporting']) for n in devnet['nodes']] == [
        ('bootstrap-cardano', 'cardano-node', 'bootstrap-producer', True), ('amaru1', 'amaru', 'consumer', False)]
    assert devnet['faultable'] == ['amaru1']
    assert devnet['edges'] == [{'from': 'bootstrap-cardano', 'to': 'amaru1'}, {'from': 'amaru1', 'to': 'bootstrap-cardano'}]


def test_mixed_profile_is_mixed():
    scenario = body(profile='profile-zb-mixed-1112-amaru-20260918-nanoseconds-v3', target={'implementation': 'amaru', 'version': 'any'})
    devnet = rig.scenario_devnet(scenario)
    assert devnet['shape'] == 'mixed' and [n['id'] for n in devnet['nodes']] == ['node1', 'node2', 'amaru1']
    assert devnet['faultable'] == ['amaru1']


def test_public_profile_is_refused():
    devnet = rig.scenario_devnet(body(profile='profile-e-haskell-preview-proof'))
    assert devnet['shape'] == 'none' and [i['code'] for i in devnet['issues']] == ['public-network']


def test_unknown_profile_is_an_issue():
    devnet = rig.scenario_devnet(body(profile='profile-does-not-exist'))
    assert devnet['shape'] == 'none' and [i['code'] for i in devnet['issues']] == ['profile-unknown'] and devnet['nodes'] == []


def test_attach_scenario_keeps_the_rig_shape():
    devnet = rig.scenario_devnet(scenario_file('cardano-amaru-relay-bootstrap-control-local'))
    assert devnet['shape'] == 'attach' and devnet['edges'] == []
    assert [n['id'] for n in devnet['nodes']] == ['p1', 'p2', 'p3', 'relay1', 'relay2', 'amaru-consumer', 'amaru-relay-1', 'amaru-relay-2']


def test_devnet_without_a_target_node_has_no_faultable_sut():
    scenario = body(substrate=substrate(('node1', 'cardano-node'), ('node2', 'cardano-node')),
                    target={'implementation': 'amaru', 'version': 'any'})
    devnet = rig.scenario_devnet(scenario)
    assert devnet['shape'] == 'none' and [i['code'] for i in devnet['issues']] == ['no-faultable-sut']


# ---- Task 2: render the rig ---------------------------------------------------

FAULT = 'com.antithesis.exclude_from_faults'


def rendered(scenario):
    return rig.render_rig(rig.scenario_devnet(scenario), scenario.id)


def test_cardano_profile_renders_only_cardano_blocks():
    out = rendered(body(profile='profile-a-haskell-peersharing-disabled'))
    services = out['compose']['services']
    assert set(services) == {'configurator', 'tracer', 'log-tailer', 'node1', 'node2', 'node3'}
    assert out['services'] == {'node1': 'node1', 'node2': 'node2', 'node3': 'node3'}
    assert out['files']['environment/testnet.yaml'].count('poolCount: 3') == 1
    configurator = services['configurator']
    assert [v for v in configurator['volumes'] if v.endswith(':/configs/1')] == ['node1-configs:/configs/1']
    assert [v for v in configurator['volumes'] if v.endswith(':/configs/3')] == ['node3-configs:/configs/3']
    assert '/configs/*' in configurator['command'][0] and '/configs/[123]' not in configurator['command'][0]
    for name in ('configurator', 'tracer', 'log-tailer'):
        assert services[name]['labels'][FAULT] == 'network,kill,pause,stop'
    for name in ('node1', 'node2', 'node3'):
        assert FAULT not in (services[name].get('labels') or {})
        assert services[name]['hostname'] == name + '.example' and services[name]['container_name'] == name
        assert services[name]['platform'] == 'linux/amd64' and services[name]['init'] is True
        assert services[name]['environment']['CARDANO_BLOCK_PRODUCER'] is True
        assert set(services[name]['depends_on']) == {'configurator', 'tracer'}
    node2 = services['node2']
    assert node2['volumes'] == ['node2-configs:/configs:ro', './topology/node2.json:/configs/configs/topology.json:ro',
                                'node2-state:/state', 'node2-evidence:/evidence', 'tracer:/tracer']
    topology = json.loads(out['files']['environment/topology/node2.json'])
    assert topology['localRoots'][0]['accessPoints'] == [{'address': 'node1.example', 'port': 3001}, {'address': 'node3.example', 'port': 3001}]
    assert topology['localRoots'][0]['valency'] == 2 and topology['publicRoots'] == []
    assert set(out['compose']['volumes']) == {'tracer', 'node1-configs', 'node2-configs', 'node3-configs',
                                              'node1-state', 'node2-state', 'node3-state', 'node1-evidence', 'node2-evidence', 'node3-evidence'}
    assert out['compose']['networks'] == {'default': {'name': 'dwarf-rig-case', 'driver': 'bridge', 'internal': '${INTERNAL_NETWORK:-false}'}}
    assert 'environment/amaru-runtime/era-history.json' not in out['files']
    assert 'environment/tracer-config.yaml' in out['files']
    assert out['source_revision'].startswith('sha256:')


def test_amaru_only_profile_bootstraps_from_the_supporting_producer():
    scenario = body(profile='profile-z-amaru-20260918-nanoseconds-v3', target={'implementation': 'amaru', 'version': 'any'})
    out = rendered(scenario)
    services = out['compose']['services']
    assert set(services) == {'configurator', 'tracer', 'log-tailer', 'bootstrap-cardano', 'amaru1'}
    assert services['bootstrap-cardano']['labels'][FAULT] == 'network,kill,pause,stop'
    assert FAULT not in (services['amaru1'].get('labels') or {})
    amaru = services['amaru1']
    assert amaru['entrypoint'] == ['/bin/bash', '-euo', 'pipefail', '-c']
    assert amaru['command'] == ['exec > >(tee -a /evidence/amaru.log) 2>&1\nexec amaru-relay-bootstrap\n']
    assert amaru['environment']['RELAY_NAME'] == 'amaru1' and amaru['environment']['AMARU_PEER'] == 'bootstrap-cardano.example:3001'
    assert amaru['environment']['AMARU_RUN_EXTRA_ARGS'] == '--listen-address 0.0.0.0:3000 --submit-api-address 0.0.0.0:3011'
    assert amaru['environment']['AMARU_NETWORK'] == 'testnet_42'
    assert amaru['volumes'] == ['bootstrap-cardano-state:/live:ro', 'bootstrap-cardano-configs:/cardano/config:ro',
                                './amaru-runtime:/amaru-runtime:ro', 'amaru-startup:/startup', 'amaru1-state:/srv/amaru', 'amaru1-evidence:/evidence']
    assert amaru['depends_on'] == {'bootstrap-cardano': {'condition': 'service_started'}}
    assert 'networks' not in amaru and amaru['hostname'] == 'amaru1.example'
    assert {'environment/amaru-runtime/era-history.json', 'environment/amaru-runtime/global-parameters.json'} <= set(out['files'])
    assert out['files']['environment/testnet.yaml'].count('poolCount: 1') == 1
    assert 'amaru-startup' in out['compose']['volumes'] and 'amaru1-state' in out['compose']['volumes']


def test_mixed_substrate_peers_amaru_with_its_first_cardano_edge():
    edges = [{'from': 'node2', 'to': 'node1'}, {'from': 'node1', 'to': 'node2'}]
    scenario = body(substrate=substrate(('node1', 'cardano-node'), ('node2', 'amaru'), edges=edges))
    out = rendered(scenario)
    services = out['compose']['services']
    assert services['node2']['environment']['AMARU_PEER'] == 'node1.example:3001'
    assert services['node2']['volumes'][0] == 'node1-state:/live:ro'
    topology = json.loads(out['files']['environment/topology/node1.json'])
    assert topology['localRoots'][0]['accessPoints'] == [{'address': 'node2.example', 'port': 3000}]
    assert FAULT not in (services['node1'].get('labels') or {}) and services['node2']['labels'][FAULT]


def test_amaru_without_a_cardano_edge_peers_with_the_first_producer():
    scenario = body(substrate=substrate(('node1', 'cardano-node'), ('node2', 'amaru'), edges=[{'from': 'node1', 'to': 'node2'}]))
    assert rendered(scenario)['compose']['services']['node2']['environment']['AMARU_PEER'] == 'node1.example:3001'


def test_render_refuses_a_none_shape_and_a_changed_rig_source(monkeypatch):
    public = body(substrate=substrate(('node1', 'cardano-node'), network='preview'))
    with pytest.raises(TopologyError):
        rig.render_rig(rig.scenario_devnet(public), public.id)
    from profile_manager import antithesis_topology
    monkeypatch.setattr(antithesis_topology, '_source', lambda: (_ for _ in ()).throw(TopologyError('changed', status=503)))
    with pytest.raises(TopologyError):
        rendered(body(profile='profile-a-haskell-peersharing-disabled'))


# ---- Task 3: per-node version binding -------------------------------------------

import hashlib
import os
import subprocess

from test_antithesis_version_binding import catalog, chosen  # noqa: F401 - fixture


def _run_launcher(base, *args, extra=None):
    from scripts.runtime_amaru_cli import launcher_script
    tmp_path = base / ('case-%d' % len(list(base.iterdir())))
    tmp_path.mkdir()
    target = tmp_path / 'target'
    target.mkdir()
    (target / 'amaru').write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$AMARU_ARGS_OUT"\n')
    (target / 'amaru').chmod(0o755)
    launcher = tmp_path / 'amaru-launch'
    launcher.write_text(launcher_script().replace('/target/', str(target) + '/'))
    launcher.chmod(0o755)
    out = tmp_path / 'args'
    env = dict(os.environ, AMARU_ARGS_OUT=str(out))
    if extra is not None:
        env['AMARU_RUN_EXTRA_ARGS'] = extra
    subprocess.run([str(launcher), *args], check=True, env=env)
    return out.read_text().split()


def test_launcher_appends_extra_args_to_run_only(tmp_path):
    # The fake executable answers `node run --help`, so the launcher takes the modern `node` command tree.
    assert _run_launcher(tmp_path, 'run', '--network', 'testnet_42', extra='--listen-address 0.0.0.0:3000 --submit-api-address 0.0.0.0:3011') == [
        'node', 'run', '--network', 'testnet_42', '--listen-address', '0.0.0.0:3000', '--submit-api-address', '0.0.0.0:3011']
    assert _run_launcher(tmp_path, 'bootstrap', '--network', 'testnet_42', extra='--listen-address 0.0.0.0:3000') == ['node', 'bootstrap', '--network', 'testnet_42']
    assert _run_launcher(tmp_path, 'run', '--network', 'testnet_42') == ['node', 'run', '--network', 'testnet_42']


def test_extract_service_helper_matches_the_transform_and_the_harness_pin():
    from profile_manager import antithesis_harness
    from scripts import qualify_node_versions as assembly
    from scripts.check_cardano_amaru_topology import AMARU_CONSUMER, AMARU_RELAYS, CARDANO_REFERENCE_NODES
    image = 'ghcr.io/pragma-org/amaru:v10.11.20260918'
    baseline = {'services': {name: {'image': 'x', 'environment': {'AMARU_PEER': 'p1.example:3001'} if name in AMARU_RELAYS else {},
                                    'volumes': [], 'depends_on': {}} for name in (*CARDANO_REFERENCE_NODES, AMARU_CONSUMER, *AMARU_RELAYS)},
                'networks': {'default': {}}}
    baseline['services']['amaru-consumer-seed'] = {'image': 'x'}
    transformed = assembly.transform_compose_model(copy.deepcopy(baseline), scope='mixed', project='dwarf-qual-x',
                                                   cardano_image=None, amaru_image=image, amaru_runtime_interface='extracted-binary')
    helper = assembly.amaru_target_extract_service(image, 'mixed')
    assert transformed['services'][assembly.AMARU_TARGET_EXTRACT] == helper
    assert hashlib.sha256(helper['command'][0].encode()).hexdigest() == antithesis_harness._EXTRACTION_COMMAND_SHA256


def _bound_rig(scenario, intent):
    devnet = rig.scenario_devnet(scenario)
    out = rig.render_rig(devnet, scenario.id)
    files = dict(out['files'])
    report = rig.bind_rig_versions(files, intent, devnet, out, scenario_default=scenario.target.get('version'))
    return files, out, report


def test_cardano_release_binds_to_every_producer(catalog):
    scenario = body(profile='profile-a-haskell-peersharing-disabled')
    files, out, report = _bound_rig(scenario, chosen('10.7.1'))
    reference = next(r for r in catalog['releases'] if r['implementation'] == 'cardano-node' and r['version'] == '10.7.1')['artifacts'][0]['reference']
    compose = yaml.safe_load(files['environment/docker-compose.yaml'])
    assert report['status'] == 'applied' and report['source'] == 'generated-rig', report['issues']
    for name in ('node1', 'node2', 'node3'):
        assert compose['services'][name]['image'] == reference
    rows = {r['service']: r for r in report['services']}
    assert set(rows) == {'node1', 'node2', 'node3'}
    assert rows['node2']['requested_version'] == '10.7.1' and rows['node2']['implementation'] == 'cardano-node'
    assert rows['node2']['status'] == 'applied' and rows['node2']['supporting'] is False
    assert report['target_contract']['scope'] == 'cardano-only' and report['target_contract']['services'] == ['node1', 'node2', 'node3']
    assert report['target_contract']['template_revision'] == out['source_revision']
    assert [r['implementation'] for r in report['selected_releases']] == ['cardano-node']
    assert 'amaru-target-extract' not in compose['services']


def test_modern_amaru_release_is_extracted_and_the_supporting_producer_uses_the_catalog_default(catalog):
    from scripts import qualify_node_versions as assembly
    scenario = body(profile='profile-z-amaru-20260918-nanoseconds-v3', target={'implementation': 'amaru', 'version': 'any'})
    intent = chosen(versions={'amaru': '10.11.20260918'})
    files, out, report = _bound_rig(scenario, intent)
    compose = yaml.safe_load(files['environment/docker-compose.yaml'])
    assert report['status'] == 'applied', report['issues']
    amaru = compose['services']['amaru1']
    assert amaru['image'] == assembly.CONTROL_AMARU_IMAGE
    assert amaru['environment']['AMARU_BIN'] == '/target/amaru-launch' and amaru['environment']['AMARU_MIGRATE_CHAIN_DB'] == 'true'
    assert {'type': 'volume', 'source': 'amaru-target-bin', 'target': '/target', 'read_only': True} in amaru['volumes']
    assert amaru['depends_on']['amaru-target-extract'] == {'condition': 'service_completed_successfully', 'required': True}
    assert amaru['environment']['AMARU_RUN_EXTRA_ARGS'].startswith('--listen-address')
    extract = compose['services']['amaru-target-extract']
    assert extract['image'].startswith('ghcr.io/pragma-org/amaru') and extract['platform'] == 'linux/amd64'
    assert extract['labels'] == {'com.antithesis.exclude_from_faults': 'network,kill,pause,stop'}
    assert compose['volumes']['amaru-target-bin'] == {}
    support = compose['services']['bootstrap-cardano']
    assert support['image'].startswith('ghcr.io/intersectmbo/cardano-node:11.1.2@sha256:')
    rows = {r['service']: r for r in report['services']}
    assert rows['bootstrap-cardano']['supporting'] is True and rows['bootstrap-cardano']['requested_version'] == '11.1.2'
    assert rows['amaru1']['target_binary']['mode'] == 'extracted-binary' and rows['amaru1']['target_binary']['path'] == '/target/amaru'
    assert rows['amaru-target-extract']['role'] == 'artifact-source'
    assert report['target_contract']['scope'] == 'amaru-only' and report['target_contract']['services'] == ['amaru1']
    assert [r['implementation'] for r in report['selected_releases']] == ['amaru']
    assert [(r['implementation'], r['version']) for r in report['supporting_releases']] == [('cardano-node', '11.1.2')]


def test_mixed_rig_without_an_amaru_selection_is_partial(catalog):
    scenario = body(substrate=substrate(('node1', 'cardano-node'), ('node2', 'amaru')))
    files, out, report = _bound_rig(scenario, chosen('10.7.1'))
    assert report['status'] == 'partial'
    assert 'missing-exact-version' in {i['code'] for i in report['issues']}
    compose = yaml.safe_load(files['environment/docker-compose.yaml'])
    assert compose['services']['node2']['image'] == out['compose']['services']['node2']['image']
    assert 'AMARU_BIN' not in compose['services']['node2']['environment']


def test_legacy_control_amaru_binds_as_the_wrapper_without_extra_args(catalog):
    # 10.11.0 is the retained control image itself: legacy wrapper, no launcher, no submit API.
    scenario = body(substrate=substrate(('node1', 'cardano-node'), ('node2', 'amaru')), target={'implementation': 'amaru', 'version': 'any'})
    files, out, report = _bound_rig(scenario, chosen(versions={'amaru': '10.11.0', 'cardano-node': '10.7.1'}))
    assert report['status'] == 'applied', report['issues']
    compose = yaml.safe_load(files['environment/docker-compose.yaml'])
    amaru = compose['services']['node2']
    assert amaru['image'].startswith('ghcr.io/lambdasistemi/amaru-bootstrap-producer:')
    assert not {'AMARU_BIN', 'AMARU_MIGRATE_CHAIN_DB', 'AMARU_RUN_EXTRA_ARGS'} & set(amaru['environment'])
    assert 'amaru-target-extract' not in compose['services']
    rows = {r['service']: r for r in report['services']}
    assert rows['node2']['target_binary'] == {'mode': 'legacy-wrapper', 'image': amaru['image'], 'path': '/bin/amaru', 'source_service': 'node2'}


def test_unknown_amaru_image_without_the_runtime_interface_is_a_gap(catalog, monkeypatch):
    from profile_manager import antithesis_versions
    scenario = body(substrate=substrate(('node1', 'cardano-node'), ('node2', 'amaru')), target={'implementation': 'amaru', 'version': 'any'})
    monkeypatch.setattr(antithesis_versions, 'stock_image_candidates', lambda release: (
        {'quay.io/other/amaru:1@sha256:' + '0' * 64: {'availability': 'available'}} if release['implementation'] == 'amaru'
        else {'ghcr.io/intersectmbo/cardano-node:10.7.1': {'availability': 'available'}}))
    files, out, report = _bound_rig(scenario, chosen(versions={'amaru': '10.11.0', 'cardano-node': '10.7.1'}))
    assert 'amaru-runtime-interface-not-implemented' in {i['code'] for i in report['issues']}
    assert report['status'] == 'partial' and 'AMARU_BIN' not in yaml.safe_load(files['environment/docker-compose.yaml'])['services']['node2']['environment']


def test_patched_build_without_artifacts_is_not_applied(catalog):
    scenario = body(profile='profile-a-haskell-peersharing-disabled')
    files, out, report = _bound_rig(scenario, chosen('10.7.1', build_variant='instrumented'))
    assert report['status'] == 'recorded' and [i['code'] for i in report['issues']] == ['patched-build-not-implemented']
