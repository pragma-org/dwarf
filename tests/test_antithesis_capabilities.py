import json
from pathlib import Path

import pytest

from profile_manager.antithesis_capabilities import STATUSES, classify, load_matrix, skip_policy
from profile_manager.scenario import scenario_from_body

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / 'dwarf' / 'scenarios'


def catalog(identifier):
    return scenario_from_body((SCENARIOS / (identifier + '.yaml')).read_bytes())


FREEZE_YAML = json.dumps({
    'spec_version': 'v1', 'id': 'freeze-probe', 'title': 'freeze', 'authors': ['t'], 'tags': [],
    'target': {'implementation': 'cardano-node', 'version': 'any'}, 'runtime': 'devnet',
    'profile': 'profile-a-haskell-peersharing-disabled',
    'load': [{'primitive': 'load_shell_command', 'command': 'true'}],
    'faults': [{'primitive': 'fault_node_freeze', 'target_node': 'node1', 'duration_seconds': 1}],
    'assertions': [{'primitive': 'unknown_primitive_x'}],
}).encode()


def test_substrate_observation_scenario_is_partial_with_delegated_lifecycle():
    result = classify(catalog('runtime-substrate-mempool-failure-containment-mixed-amaru'))
    assert result['level'] == 'partial'
    assert result['runnable_load'] is True
    assert result['reason'] is None
    statuses = {row['primitive']: row['status'] for row in result['primitives']}
    assert statuses['runtime_compose_substrate'] == 'delegated'
    assert statuses['runtime_install_version'] == 'delegated'
    assert statuses['runtime_teardown_substrate'] == 'delegated'
    assert statuses['runtime_multi_node_observation'] == 'runs-with-note'
    assert statuses['all_nodes_responsive'] == 'runs'
    assert statuses['runtime_mempool_failure_probe'] == 'unsupported'
    assert {gap['code'] for gap in result['gaps']} == {'primitive-unsupported-on-antithesis'}
    assert all(gap['kind'] == 'implementation-gap' and gap['primitive'] for gap in result['gaps'])
    assert [row['phase'] for row in result['primitives']][:2] == ['setup', 'setup']


def test_library_scenario_is_none_no_faultable_sut():
    result = classify(catalog('amaru-cbor-auxiliary-data-fuzz-structured'))
    assert result['level'] == 'none'
    assert result['reason'] == 'no-faultable-sut'


def test_unsupported_only_load_is_none_no_runnable_load():
    result = classify(catalog('opcert-soak-counter-edge-mixed-1112-amaru-20260918'))
    assert result['level'] == 'none'
    assert result['reason'] == 'no-runnable-load'
    assert result['runnable_load'] is False


def test_unknown_primitive_defaults_to_unsupported_and_fault_is_delegated():
    result = classify(scenario_from_body(FREEZE_YAML))
    statuses = {row['primitive']: row['status'] for row in result['primitives']}
    assert statuses['fault_node_freeze'] == 'delegated-fault'
    assert statuses['unknown_primitive_x'] == 'unsupported'
    assert statuses['load_shell_command'] == 'runs-with-note'
    assert result['level'] == 'partial'
    assert {gap['code'] for gap in result['gaps']} == {'fault-delegated-to-platform', 'primitive-unsupported-on-antithesis'}
    assert skip_policy(result) == {'fault_node_freeze': 'delegated-fault', 'unknown_primitive_x': 'unsupported',
                                   'load_shell_command': 'runs-with-note'}


def test_full_when_every_primitive_runs_or_is_delegated():
    body = json.loads(FREEZE_YAML)
    body['assertions'] = [{'primitive': 'load_events_are_ok'}]
    result = classify(scenario_from_body(json.dumps(body).encode()))
    assert result['level'] == 'full'
    assert {gap['code'] for gap in result['gaps']} == {'fault-delegated-to-platform'}


def test_matrix_names_exist_in_registry_and_statuses_are_known():
    matrix = load_matrix()
    assert matrix['schema_version'] == 1 and matrix['default'] == 'unsupported'
    registry = json.load(open(ROOT / 'dwarf' / 'primitives' / 'registry.json'))['primitives']
    block_apply = {'forge_block', 'serve_crafted_block', 'block_apply_differential'}
    assert set(matrix['primitives']) - set(registry) <= block_apply
    assert {row['status'] for row in matrix['primitives'].values()} <= set(STATUSES)
    assert all(row['note'] for row in matrix['primitives'].values() if row['status'] != 'runs')


def test_phases_are_flattened_in_order():
    body = json.loads(FREEZE_YAML)
    body.pop('load'); body.pop('faults'); body.pop('assertions')
    body['phases'] = [
        {'id': 'one', 'load': [{'primitive': 'load_shell_command', 'command': 'true'}]},
        {'id': 'two', 'assertions': [{'primitive': 'load_events_are_ok'}]},
    ]
    result = classify(scenario_from_body(json.dumps(body).encode()))
    assert [(row['phase'], row['primitive']) for row in result['primitives']] == [
        ('one:load', 'load_shell_command'), ('two:assertions', 'load_events_are_ok')]
    assert result['level'] == 'full'


def test_assertions_whose_evidence_producer_is_unsupported_are_unsupported_too():
    matrix = load_matrix()['primitives']
    assert matrix['mempool_failure_contained']['status'] == 'unsupported'
    assert matrix['peer_connectivity_observed']['status'] == 'unsupported'
    result = classify(catalog('runtime-substrate-mempool-failure-containment-mixed-amaru'))
    statuses = {row['primitive']: row['status'] for row in result['primitives']}
    assert statuses['mempool_failure_contained'] == 'unsupported'


def test_public_network_devnet_is_none_before_the_primitive_walk():
    public = json.dumps({
        'spec_version': 'v1', 'id': 'public-probe', 'title': 'public', 'authors': ['t'], 'tags': [],
        'target': {'implementation': 'cardano-node', 'version': 'any'}, 'runtime': 'devnet',
        'substrate': {'compose_mode': 'docker', 'network': 'preview',
                      'nodes': [{'id': 'node1', 'impl': 'cardano-node', 'version': 'resolved-plan', 'identity_binding': 'resolved-plan', 'role': 'honest'}],
                      'topology': {'edges': []}},
        'load': [{'primitive': 'load_shell_command', 'command': 'true'}],
        'assertions': [{'primitive': 'load_events_are_ok'}],
    }).encode()
    result = classify(scenario_from_body(public))
    assert result['level'] == 'none' and result['reason'] == 'public-network'
    assert result['runnable_load'] is True
