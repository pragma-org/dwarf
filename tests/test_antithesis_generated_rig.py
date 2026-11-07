import json
from pathlib import Path

import pytest

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


def catalog(identifier):
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
    devnet = rig.scenario_devnet(catalog('cardano-amaru-relay-bootstrap-control-local'))
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
