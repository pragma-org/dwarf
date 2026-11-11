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


def test_substrate_observation_scenario_is_full_with_delegated_lifecycle():
    # Every primitive of this scenario is proven in a package since the 2026-11-11 smokes.
    result = classify(catalog('runtime-substrate-mempool-failure-containment-mixed-amaru'))
    assert result['level'] == 'full'
    assert result['runnable_load'] is True
    assert result['reason'] is None
    statuses = {row['primitive']: row['status'] for row in result['primitives']}
    assert statuses['runtime_compose_substrate'] == 'delegated'
    assert statuses['runtime_install_version'] == 'delegated'
    assert statuses['runtime_teardown_substrate'] == 'delegated'
    assert statuses['runtime_multi_node_observation'] == 'runs-with-note'
    assert statuses['all_nodes_responsive'] == 'runs'
    assert statuses['runtime_mempool_failure_probe'] == 'runs'
    assert not [gap for gap in result['gaps'] if gap['code'] in ('primitive-unsupported-on-antithesis', 'unproven-in-package')]
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


def test_unknown_primitive_defaults_to_unsupported_and_fault_is_delegated(monkeypatch):
    _matrix_with(monkeypatch, {'load_shell_command', 'load_events_are_ok'})
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


def test_full_when_every_primitive_runs_or_is_delegated(monkeypatch):
    _matrix_with(monkeypatch, {'load_shell_command', 'load_events_are_ok'})
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


def test_phases_are_flattened_in_order(monkeypatch):
    _matrix_with(monkeypatch, {'load_shell_command', 'load_events_are_ok'})
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
    assert matrix['unrelated_peer_session_usable']['status'] == 'runs'
    assert matrix['runtime_peer_session_health']['status'] == 'unsupported'
    result = classify(catalog('client-example-invalid-mini-protocol-amaru-20260918'))
    statuses = {row['primitive']: row['status'] for row in result['primitives']}
    assert statuses['unrelated_peer_session_usable'] == 'unsupported'


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


def test_exact_target_verification_runs_in_the_package_with_its_limitation_named():
    """F46 → F182: runtime_verify_exact_target used to read the frozen local launch identity the
    package does not carry; it now compares the scenario target with the package pin and names
    the running-image limitation."""
    result = classify(catalog('client-example-txsubmission-window-amaru-portable-20260918'))
    rows = {r['primitive']: r['status'] for r in result['primitives']}
    assert rows['runtime_verify_exact_target'] == 'runs-with-note'
    assert 'not inspected' in load_matrix()['primitives']['runtime_verify_exact_target']['note']


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
    result = classify(catalog('client-example-invalid-mini-protocol-amaru-20260918'))
    rows = {row['primitive']: row for row in result['primitives']}
    name = 'unrelated_peer_session_usable'
    assert rows[name]['status'] == 'unsupported'
    assert 'runtime_peer_session_health' in rows[name]['note']
    assert skip_policy(result)[name] == 'unsupported'
    assert any(gap['primitive'] == name and gap['code'] == 'assertion-evidence-unsupported' for gap in result['gaps'])


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
    body['probes'] = [{'primitive': 'runtime_peer_session_health'}]
    body['assertions'] = [{'primitive': 'unrelated_peer_session_usable'}, {'primitive': 'unknown_primitive_x'}]
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


def test_process_observations_make_the_scenario_partial_with_a_named_gap():
    """F168: resource_profile / syscall_trace need process access the package does not have.
    The observation still runs (tips, TCP), so the scenario is partial, not full, and says why."""
    result = classify(catalog('runtime-substrate-resource-cpu-host-pressure-example-smoke'))
    assert result['level'] == 'partial'
    gaps = [g for g in result['gaps'] if g['code'] == 'observation-unavailable-in-package']
    assert len(gaps) == 1 and gaps[0]['primitive'] == 'runtime_multi_node_observation'
    assert 'resource_profile' in gaps[0]['message']
    window = classify(catalog('runtime-substrate-txsubmission-window-pressure-example-smoke'))
    assert window['level'] == 'full' and not window['gaps']


def _matrix_with(monkeypatch, proven):
    """The committed matrix with proven_by set only on the named primitives."""
    from profile_manager import antithesis_capabilities as caps
    matrix = json.loads(caps.MATRIX_PATH.read_text(encoding='utf-8'))
    for name, row in matrix['primitives'].items():
        row.pop('proven_by', None)
        if name in proven:
            row['proven_by'] = {'scenario': 'x', 'build': 'b', 'date': '2026-11-11'}
    monkeypatch.setattr(caps, 'load_matrix', lambda: matrix)


def test_a_runnable_primitive_never_proven_in_a_package_keeps_the_scenario_partial(monkeypatch):
    """'runs' in the matrix is a claim; 'full' needs every runnable step proven by a package smoke."""
    _matrix_with(monkeypatch, {'load_events_are_ok'})
    body = json.loads(FREEZE_YAML)
    body['assertions'] = [{'primitive': 'load_events_are_ok'}]
    result = classify(scenario_from_body(json.dumps(body).encode()))
    assert result['level'] == 'partial'
    unproven = [g for g in result['gaps'] if g['code'] == 'unproven-in-package']
    assert [g['primitive'] for g in unproven] == ['load_shell_command']


def test_full_when_every_runnable_primitive_is_proven(monkeypatch):
    _matrix_with(monkeypatch, {'load_events_are_ok', 'load_shell_command'})
    body = json.loads(FREEZE_YAML)
    body['assertions'] = [{'primitive': 'load_events_are_ok'}]
    result = classify(scenario_from_body(json.dumps(body).encode()))
    assert result['level'] == 'full'
    assert not [g for g in result['gaps'] if g['code'] == 'unproven-in-package']


def test_every_recorded_proof_names_a_catalog_scenario_that_uses_the_primitive():
    from profile_manager.antithesis_capabilities import _steps
    matrix = load_matrix()
    for name, row in matrix['primitives'].items():
        proof = row.get('proven_by')
        if not proof:
            continue
        assert row['status'] in ('runs', 'runs-with-note'), name
        scenario = catalog(proof['scenario'])
        assert name in {step[2] for step in _steps(scenario)}, (name, proof['scenario'])
        assert proof.get('build') and proof.get('date'), name


@pytest.mark.parametrize('identifier', [
    'm3-runtime-profile-restart-postrecovery-blockfetch', 'm3-runtime-node2-copied-state-bounded-divergence',
    'm3-runtime-node2-copied-state-chainsync-divergence', 'm3-runtime-node2-copied-state-postremediation-blockfetch'])
def test_shell_load_that_controls_host_node_sessions_is_refused(identifier):
    """F170: these loads run runtime_profile_recovery_check.py, which starts, stops and replaces
    profile-a node sessions; a package has no process control over other services (package smoke:
    'can't open file …/scripts/runtime_profile_recovery_check.py', and the session control would
    fail next). 'Full' was the matrix's blanket claim for load_shell_command."""
    result = classify(catalog(identifier))
    assert result['level'] == 'none' and result['reason'] == 'no-runnable-load'
    gap = [g for g in result['gaps'] if g['primitive'] == 'load_shell_command']
    assert gap and gap[0]['code'] == 'primitive-unsupported-on-antithesis'
    assert 'runtime_profile_recovery_check.py' in gap[0]['message']


def test_every_proven_assertion_declares_its_antithesis_semantics():
    """F174: a proven assertion must say whether it is liveness (Sometimes) or safety (Always
    when measured), else it falls back to an unconditional Always that faults turn red."""
    matrix = load_matrix()
    names = {ref.primitive for path in SCENARIOS.glob('*.yaml')
             for ref in (catalog(path.stem).assertions if path.read_bytes().lstrip()[:1] == b'{' else [])}
    checked = 0
    for name, row in matrix['primitives'].items():
        if row.get('proven_by') and name in names:
            checked += 1
            semantics = row.get('antithesis_assertion') or {}
            assert semantics.get('kind') in ('liveness', 'safety'), name
            if semantics['kind'] == 'safety':
                assert semantics.get('measured_when'), name
    assert checked >= 4


def test_measurement_window_marks_run_in_the_package_through_the_phase_marker_proxy():
    """F185: the window marks were unlisted (so unsupported and skipped) because the package has
    no measurement runtime; the phase-marker proxy gives them one."""
    rows = {row['primitive']: row for row in classify(catalog('client-example-txsubmission-window-amaru-portable-20260918'))['primitives']}
    for name in ('runtime_mark_hostile_window', 'runtime_mark_recovery_window'):
        assert rows[name]['status'] == 'runs-with-note', name
    assert load_matrix()['primitives']['runtime_mark_baseline_window']['status'] == 'runs-with-note'
