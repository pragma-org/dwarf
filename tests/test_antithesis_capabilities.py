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
    # Its only assertion is unsupported, so a run could evaluate nothing: refused, not "partial".
    assert result['level'] == 'none' and result['reason'] == 'no-evaluable-assertion'
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


def test_exact_target_verification_is_unsupported_until_the_package_identity_route_exists():
    """F46: runtime_verify_exact_target reads the frozen local launch identity
    (resolved-plan fields + measurement context) that the package does not carry;
    inside the package it raised and turned a healthy run into 'error'."""
    result = classify(catalog('client-example-txsubmission-window-amaru-portable-20260918'))
    rows = {r['primitive']: r['status'] for r in result['primitives']}
    assert rows['runtime_verify_exact_target'] == 'unsupported'
    assert 'package' in load_matrix()['primitives']['runtime_verify_exact_target']['note']


def test_client_proof_assertions_follow_their_evidence_producer():
    """AT-2b: an assertion that can only read a proof written by an unsupported
    producer is skipped with a gap naming the producer, not failed for lack of
    a file the package could never write."""
    matrix = load_matrix()['primitives']
    producers = {name: row['evidence_producer'] for name, row in matrix.items() if 'evidence_producer' in row}
    assert producers['target_progress_continues'] == 'runtime_target_health_and_progress'
    assert producers['no_target_fatal_signal'] == 'runtime_target_health_and_progress'
    assert producers['unrelated_peer_session_usable'] == 'runtime_peer_session_health'
    assert all(producer in matrix for producer in producers.values())
    result = classify(catalog('client-example-txsubmission-window-amaru-portable-20260918'))
    rows = {row['primitive']: row for row in result['primitives']}
    for name in ('target_progress_continues', 'no_target_fatal_signal'):
        assert rows[name]['status'] == 'unsupported'
        assert 'runtime_target_health_and_progress' in rows[name]['note']
        assert skip_policy(result)[name] == 'unsupported'
    assert any(gap['primitive'] == 'target_progress_continues' and gap['code'] == 'assertion-evidence-unsupported' for gap in result['gaps'])


def test_assertion_with_a_running_producer_keeps_its_own_status():
    body = json.loads(FREEZE_YAML)
    body['probes'] = [{'primitive': 'all_nodes_responsive'}]
    body['assertions'] = [{'primitive': 'target_progress_continues'}]
    body['faults'] = []
    result = classify(scenario_from_body(json.dumps(body).encode()))
    rows = {row['primitive']: row for row in result['primitives']}
    # No producer in this scenario at all: the matrix entry's own status stands
    # (a genuinely missing proof is a scenario defect, not a package gap).
    assert rows['target_progress_continues']['status'] == 'runs'


def test_window_assertion_runs_and_follows_its_probe():
    """AT-2b: txsubmission_window_enforced only reads the probe's result file;
    unlisted it defaulted to unsupported and was skipped, which let a run whose
    probe failed end with every assertion skipped."""
    matrix = load_matrix()['primitives']
    assert matrix['txsubmission_window_enforced']['status'] == 'runs'
    assert matrix['txsubmission_window_enforced']['evidence_producer'] == 'runtime_txsubmission_window_pressure'
    result = classify(catalog('client-example-txsubmission-window-amaru-portable-20260918'))
    rows = {row['primitive']: row for row in result['primitives']}
    assert rows['txsubmission_window_enforced']['status'] == 'runs'
    assert result['level'] == 'partial'


def test_scenario_whose_assertions_are_all_skipped_is_refused_as_unverifiable():
    body = json.loads(FREEZE_YAML)
    body['faults'] = []
    body['probes'] = [{'primitive': 'runtime_target_health_and_progress'}]
    body['assertions'] = [{'primitive': 'target_progress_continues'}, {'primitive': 'no_target_fatal_signal'}]
    result = classify(scenario_from_body(json.dumps(body).encode()))
    assert result['level'] == 'none' and result['reason'] == 'no-evaluable-assertion'
    assert all(row['status'] == 'unsupported' for row in result['primitives'] if row['phase'] == 'assertions')


def test_chainhold_package_has_an_evaluable_workload():
    """F123: chain_select_differential compares node tips only, which the in-package observation
    collects; listing it lets the self-composed chainhold run on Antithesis."""
    from profile_manager.antithesis_package import preview_package
    from test_antithesis_package import request
    result = preview_package(request(scenario_id="consensus-chainhold-upstream-differential"))
    codes = {issue.get("code") for issue in result["issues"]}
    assert "scenario-capability-none" not in codes
    assert not any(issue.get("primitive") == "chain_select_differential" for issue in result["issues"])


def test_resolved_plan_scenarios_are_not_an_unknown_release():
    """F130b: 'resolved-plan' means 'no concrete pin' (like latest/any), not a release named resolved-plan."""
    from profile_manager.antithesis_package import preview_package
    from test_antithesis_package import request
    result = preview_package(request(scenario_id="runtime-substrate-byzantine-blockfetch-example-smoke"))
    assert "unknown-release" not in {issue.get("code") for issue in result["issues"]}
