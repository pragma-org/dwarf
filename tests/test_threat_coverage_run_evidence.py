"""Retained campaign runs are evidence on the threat page: a passing run whose tap
recorded real values verifies that tap, and an entry whose scenario ran is covered."""
import json

import pytest

from profile_manager.data.basic_deck import deck_binder
from profile_manager.data.measurement_coverage import measurement_coverage_payload


@pytest.fixture
def runs_root(monkeypatch, tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(runs))
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ADA2_DWARF_BUNDLES_DIR", str(tmp_path / "bundles"))
    return runs


def _target(payload):
    """A mapped threat whose current state is not Verified, one scenario and one implemented tap."""
    for row in payload["views"]["threats"]:
        taps = [t for t in row["measurements"] if t["source"] != "Reserved" and t["status"] != "Verified"]
        if row["status"] != "Verified" and row["scenarios"] and taps:
            return row["id"], row["scenarios"][0]["id"], taps[0]["id"]
    pytest.skip("every mapped threat is already verified")


def _run(runs, run_id, scenario_id, tap_id, *, exit_status="pass", live=True):
    run = runs / run_id
    (run / "measurements" / tap_id).mkdir(parents=True)
    (run / "manifest.json").write_text(json.dumps({
        "run_id": run_id, "scenario": {"id": scenario_id}, "exit_status": exit_status}))
    record = ({"name": "m", "status": "available", "value": 7, "sample_count": 3} if live
              else {"name": "m", "status": "unavailable", "reason": "no records in window"})
    (run / "measurements" / tap_id / "result.json").write_text(json.dumps({
        "measurement_id": tap_id, "status": "available" if live else "unavailable", "records": [record]}))


def _row(payload, row_id):
    return next(r for r in payload["views"]["threats"] if r["id"] == row_id)


def test_a_passing_run_with_live_tap_values_verifies_that_tap(runs_root):
    row_id, scenario_id, tap_id = _target(measurement_coverage_payload())
    _run(runs_root, "20261110T010000Z-aaaaaaaa", scenario_id, tap_id)
    row = _row(measurement_coverage_payload(), row_id)
    tap = next(t for t in row["measurements"] if t["id"] == tap_id)
    assert tap["status"] == "Verified"
    assert row["status"] == "Verified"
    assert any(e["run_id"] == "20261110T010000Z-aaaaaaaa" and e["url"] == "/operate/runs/20261110T010000Z-aaaaaaaa"
               for e in row["evidence"])


@pytest.mark.parametrize("exit_status,live", [("fail", True), ("pass", False)])
def test_failed_runs_and_valueless_taps_are_not_evidence(runs_root, exit_status, live):
    row_id, scenario_id, tap_id = _target(measurement_coverage_payload())
    _run(runs_root, "20261110T020000Z-bbbbbbbb", scenario_id, tap_id, exit_status=exit_status, live=live)
    row = _row(measurement_coverage_payload(), row_id)
    assert next(t for t in row["measurements"] if t["id"] == tap_id)["status"] != "Verified"
    assert all(e["run_id"] != "20261110T020000Z-bbbbbbbb" for e in row["evidence"])


def test_binder_shows_an_entry_whose_scenario_ran_as_verified(runs_root):
    row_id, scenario_id, tap_id = _target(measurement_coverage_payload())
    before = next(e for e in deck_binder()["entries"] if e["id"] == row_id)
    assert before["tier"] == "mapped"
    _run(runs_root, "20261110T030000Z-cccccccc", scenario_id, tap_id)
    after = next(e for e in deck_binder()["entries"] if e["id"] == row_id)
    assert after["tier"] == "verified"
    assert after["verified"] >= 1


def test_binder_counts_a_passing_run_without_tap_values_as_covered(runs_root):
    row_id, scenario_id, tap_id = _target(measurement_coverage_payload())
    _run(runs_root, "20261110T040000Z-dddddddd", scenario_id, tap_id, live=False)
    entry = next(e for e in deck_binder()["entries"] if e["id"] == row_id)
    assert entry["tier"] == "verified"
    assert entry["validated_scenarios"] == 1
