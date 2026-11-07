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
