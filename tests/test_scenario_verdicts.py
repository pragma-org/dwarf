from __future__ import annotations

import json

from profile_manager import cli, forensic
from profile_manager.data.operate_run import _security_execution_section, operate_run_detail
from profile_manager.run_ledger import RunLedger
from profile_manager.scenario_verdict import evaluate_scenario_verdict
from profile_manager.views.operate_run import render_operate_run


def _measurement(*, gated: bool, state: str, gate_result: str) -> dict:
    return {
        "resolution": {
            "resolved": [
                {
                    "id": "required-tap",
                    "threshold_gate": {
                        "enabled": gated,
                        "thresholds": [
                            {
                                "metric": "samples",
                                "operator": "gte",
                                "value": 1,
                            }
                        ],
                    },
                }
            ]
        },
        "collector_states": {"required-tap": state},
        "collector_errors": (
            []
            if state == "finalized"
            else [{"measurement_id": "required-tap", "phase": "finalize"}]
        ),
        "threshold_gates": {
            "required-tap": {
                "enabled": gated,
                "result": gate_result,
                "checks": [],
            }
        },
        "gate_failed": gated and gate_result == "fail",
    }


def test_zero_assertions_cannot_produce_a_passing_scenario_verdict():
    verdict = evaluate_scenario_verdict(
        exit_status="pass",
        assertions={"total": 0, "pass": 0, "fail": 0},
        measurements=None,
        run_kind="scenario",
    )

    assert verdict.security_verdict == "fail"
    assert verdict.operational_outcome == "pass"
    assert verdict.reason_codes == ("no-passing-assertions",)


def test_required_missing_measurement_evidence_cannot_pass():
    verdict = evaluate_scenario_verdict(
        exit_status="pass",
        assertions={"total": 1, "pass": 1, "fail": 0},
        measurements=_measurement(gated=True, state="error", gate_result="fail"),
        run_kind="scenario",
    )

    assert verdict.security_verdict == "fail"
    assert verdict.operational_outcome == "pass"
    assert verdict.reason_codes == ("required-measurement-evidence-missing",)
    assert verdict.measurement_failures == ("required-tap",)


def test_optional_measurement_failure_does_not_override_passing_assertions():
    verdict = evaluate_scenario_verdict(
        exit_status="pass",
        assertions={"total": 1, "pass": 1, "fail": 0},
        measurements=_measurement(gated=False, state="error", gate_result="not-gated"),
        run_kind="scenario",
    )

    assert verdict.security_verdict == "pass"
    assert verdict.reason_codes == ()


def test_operational_run_has_an_outcome_but_no_test_verdict():
    verdict = evaluate_scenario_verdict(
        exit_status="pass",
        assertions={"total": 0, "pass": 0, "fail": 0},
        measurements=None,
        run_kind="maintenance",
    )

    assert verdict.operational_outcome == "pass"
    assert verdict.security_verdict is None
    assert verdict.reason_codes == ()


def test_forensic_finalization_keeps_outcome_separate_from_non_vacuous_verdict(tmp_path):
    handle = forensic.start_run(
        scenario_id="empty-scenario",
        scenario_yaml=b"id: empty-scenario\n",
        target={"implementation": "amaru", "version": "test"},
        runtime="library",
        profile_id=None,
        profile_resolved=None,
        framework_version="test",
        framework_commit="a" * 40,
        seed=1,
        runs_dir=tmp_path / "runs",
        state_dir=tmp_path / "state",
    )
    handle.end(exit_status="pass")

    manifest = json.loads((handle.run_dir / "manifest.json").read_text())
    entry = RunLedger(tmp_path / "state").get(handle.run_id)
    assert manifest["exit_status"] == "pass"
    assert manifest["execution"]["security_verdict"] == "fail"
    assert entry["state"] == "completed"
    assert entry["verdict"] == "fail"


def test_forensic_finalization_fails_closed_on_required_measurement_evidence(tmp_path):
    handle = forensic.start_run(
        scenario_id="missing-measurement",
        scenario_yaml=b"id: missing-measurement\n",
        target={"implementation": "amaru", "version": "test"},
        runtime="library",
        profile_id=None,
        profile_resolved=None,
        framework_version="test",
        framework_commit="a" * 40,
        seed=1,
        runs_dir=tmp_path / "runs",
        state_dir=tmp_path / "state",
        measurement_context=_measurement(
            gated=True, state="error", gate_result="fail"
        ),
    )
    handle.assertion_result(
        primitive="target_reached",
        params={},
        evaluated_value={"reached": True},
        data_points_used=1,
        result="pass",
        note="one real assertion passed",
    )
    handle.end(exit_status="pass")

    manifest = json.loads((handle.run_dir / "manifest.json").read_text())
    assert manifest["exit_status"] == "pass"
    assert manifest["assertion_summary"] == {"total": 1, "pass": 1, "fail": 0}
    assert manifest["measurements"]["threshold_gates"]["required-tap"]["result"] == "fail"
    assert manifest["execution"]["security_verdict"] == "fail"


def test_maintenance_finalization_never_synthesizes_a_test_verdict(tmp_path):
    handle = forensic.start_run(
        scenario_id="maintenance-operation",
        scenario_yaml=b"id: maintenance-operation\n",
        target={"implementation": "dwarf", "version": "test"},
        runtime="library",
        profile_id=None,
        profile_resolved=None,
        framework_version="test",
        framework_commit="a" * 40,
        seed=1,
        runs_dir=tmp_path / "runs",
        state_dir=tmp_path / "state",
        run_kind="maintenance",
    )
    handle.end(exit_status="pass")

    manifest = json.loads((handle.run_dir / "manifest.json").read_text())
    entry = RunLedger(tmp_path / "state").get(handle.run_id)
    assert manifest["exit_status"] == "pass"
    assert manifest["execution"]["security_verdict"] is None
    assert entry["state"] == "completed"
    assert entry["verdict"] is None


def test_cli_run_returns_failure_for_operationally_clean_but_vacuous_scenario(tmp_path):
    scenario_path = tmp_path / "vacuous.json"
    scenario_path.write_text(
        json.dumps(
            {
                "spec_version": "v1",
                "id": "vacuous-cli-scenario",
                "title": "Vacuous CLI scenario",
                "target": {"implementation": "amaru", "version": "test"},
                "runtime": "library",
                "setup": [],
                "load": [],
                "faults": [],
                "probes": [],
                "assertions": [],
                "teardown": [],
            }
        )
    )

    result = cli.main(
        [
            "scenario",
            "run",
            str(scenario_path),
            "--runs-dir",
            str(tmp_path / "runs"),
            "--state-dir",
            str(tmp_path / "state"),
        ]
    )
    manifest_path = next((tmp_path / "runs").glob("*/manifest.json"))
    manifest = json.loads(manifest_path.read_text())
    assert manifest["exit_status"] == "pass"
    assert manifest["execution"]["security_verdict"] == "fail"
    assert result == 1


def test_dashboard_fallback_uses_the_same_non_vacuous_verdict(tmp_path):
    run_dir = tmp_path / "legacy-run"
    run_dir.mkdir()
    manifest = {
        "run_id": "legacy-run",
        "exit_status": "pass",
        "assertion_summary": {"total": 0, "pass": 0, "fail": 0},
    }

    execution = _security_execution_section(run_dir, manifest, [])

    assert execution == {
        "state": "completed",
        "classification": "completed",
        "security_verdict": "fail",
    }


def test_run_inspector_uses_test_verdict_and_labels_operational_outcome(
    tmp_path, monkeypatch
):
    runs_dir = tmp_path / "runs"
    state_dir = tmp_path / "state"
    handle = forensic.start_run(
        scenario_id="vacuous-dashboard-run",
        scenario_yaml=b"id: vacuous-dashboard-run\n",
        target={"implementation": "cardano-node", "version": "test"},
        runtime="library",
        profile_id=None,
        profile_resolved=None,
        framework_version="test",
        framework_commit="a" * 40,
        seed=1,
        runs_dir=runs_dir,
        state_dir=state_dir,
    )
    handle.end(exit_status="pass")
    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(runs_dir))
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(state_dir))
    monkeypatch.setenv("ADA2_DWARF_BUNDLES_DIR", str(tmp_path / "bundles"))

    detail = operate_run_detail(handle.run_id, runs_dir=runs_dir)
    html = render_operate_run(handle.run_id)

    assert detail["operational_outcome"] == "pass"
    assert detail["verdict_status"] == "fail"
    assert '<div class="dk-verdict"><span class="v dk-g-fail">▲ fail</span></div>' in html
    assert "Test verdict fail" not in html
    assert "operational outcome pass" not in html
    assert 'data-run-verdict-summary' in html
    assert "Test verdict: fail. Operational outcome: pass." in html
    assert '<div class="run-status-banner' not in html
