import json
from pathlib import Path

import pytest

from profile_manager import scenario, telemetry
from profile_manager.data.operate_run import operate_run_detail
from profile_manager.templating import render
from profile_manager.topology_health import (
    TopologyLockBusy,
    acquire_topology_lock,
)


def _write_scenario(path: Path, *, attached: bool = True) -> Path:
    body = {
        "spec_version": "v1",
        "id": "topology-preflight-test",
        "title": "Topology preflight test",
        "target": {"implementation": "cardano-node", "version": "test"},
        "runtime": "devnet" if attached else "library",
        "setup": (
            [{"primitive": "runtime_attach_topology", "topology": "cardano_amaru"}]
            if attached
            else []
        ),
        "load": [{"primitive": "load_shell_command", "command": "true"}],
        "faults": [],
        "probes": [],
        "assertions": [],
        "teardown": [],
    }
    if attached:
        body["attach"] = {"topology": "cardano_amaru"}
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


class _Observer:
    def __init__(self, **_kwargs):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def summarize(self):
        return {}


def _run(
    monkeypatch,
    tmp_path,
    *,
    health_state="healthy",
    attached=True,
    auto_redeploy=False,
    topology_redeploy=None,
    health_results=None,
):
    calls = []
    monkeypatch.setattr(telemetry, "ObserverCollector", _Observer)

    def fake_phase(_handle, _rng, _registry, _scen, refs, phase, **_kwargs):
        calls.extend((phase, ref.primitive) for ref in refs)

    monkeypatch.setattr(scenario, "_run_phase", fake_phase)
    scenario_path = _write_scenario(tmp_path / "scenario.yaml", attached=attached)
    health = {
        "state": health_state,
        "reason_code": (
            "all_mixed_readiness_gates_passed"
            if health_state == "healthy"
            else "amaru_consumer_stalled"
        ),
        "checked_at": "2026-09-15T00:00:00Z",
        "consumer_lag_slots": 902,
    }
    preflight_calls = []

    def preflight(topology_id, output):
        preflight_calls.append((topology_id, output))
        if health_results is not None:
            return health_results.pop(0)
        return health

    runs_dir = tmp_path / "runs"
    state_dir = tmp_path / "state"
    handle = scenario.run_scenario(
        scenario_path,
        runs_dir=runs_dir,
        state_dir=state_dir,
        topology_preflight=preflight,
        topology_redeploy=topology_redeploy,
        auto_redeploy_unhealthy=auto_redeploy,
    )
    manifest = json.loads((handle.run_dir / "manifest.json").read_text(encoding="utf-8"))
    return handle, manifest, calls, preflight_calls


def test_healthy_attached_topology_runs_workload(monkeypatch, tmp_path):
    _handle, manifest, calls, preflight_calls = _run(monkeypatch, tmp_path)

    assert manifest["exit_status"] == "pass"
    assert preflight_calls[0][0] == "cardano_amaru"
    assert any(primitive == "runtime_attach_topology" for _phase, primitive in calls)
    assert any(primitive == "load_shell_command" for _phase, primitive in calls)


@pytest.mark.parametrize("state", ["unhealthy", "catching_up", "unknown"])
def test_unready_attached_topology_blocks_all_scenario_phases(
    monkeypatch, tmp_path, state
):
    handle, manifest, calls, preflight_calls = _run(
        monkeypatch, tmp_path, health_state=state
    )

    assert preflight_calls[0][0] == "cardano_amaru"
    assert calls == []
    assert manifest["exit_status"] == "precondition_failed"
    assert manifest["precondition"]["reason"] == "external_topology_unhealthy"
    assert manifest["precondition"]["health_state"] == state
    assert manifest["assertion_summary"] == {"pass": 0, "fail": 0, "total": 0}
    evidence = handle.run_dir / "outputs" / "topology-preflight" / "topology-health.json"
    assert json.loads(evidence.read_text(encoding="utf-8"))["state"] == state


def test_non_attached_scenario_does_not_invoke_topology_preflight(monkeypatch, tmp_path):
    _handle, manifest, calls, preflight_calls = _run(
        monkeypatch, tmp_path, attached=False
    )

    assert preflight_calls == []
    assert manifest["exit_status"] == "pass"
    assert any(primitive == "load_shell_command" for _phase, primitive in calls)


def test_attached_scenario_holds_shared_topology_lock_through_workload(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(telemetry, "ObserverCollector", _Observer)
    state_dir = tmp_path / "state"
    lock_checks = []

    def fake_phase(_handle, _rng, _registry, _scen, refs, _phase, **_kwargs):
        if refs:
            with pytest.raises(TopologyLockBusy):
                acquire_topology_lock(state_dir, exclusive=True)
            lock_checks.append(True)

    monkeypatch.setattr(scenario, "_run_phase", fake_phase)
    handle = scenario.run_scenario(
        _write_scenario(tmp_path / "scenario.yaml"),
        runs_dir=tmp_path / "runs",
        state_dir=state_dir,
        topology_preflight=lambda _topology, _output: {
            "state": "healthy",
            "reason_code": "all_mixed_readiness_gates_passed",
        },
    )

    assert lock_checks
    with acquire_topology_lock(state_dir, exclusive=True):
        pass
    manifest = json.loads((handle.run_dir / "manifest.json").read_text())
    assert manifest["exit_status"] == "pass"


def test_run_page_distinguishes_precondition_failure_from_security_failure(
    monkeypatch, tmp_path
):
    handle, _manifest, _calls, _preflight_calls = _run(
        monkeypatch, tmp_path, health_state="unhealthy"
    )

    detail = operate_run_detail(handle.run_id, runs_dir=handle.run_dir.parent)
    html = render(
        "operate/run.j2",
        page_title="Run",
        density="reading",
        active="operate",
        active_sub="runs",
        run=detail,
    )

    assert detail["precondition"]["present"] is True
    assert detail["precondition"]["reason_code"] == "amaru_consumer_stalled"
    assert "Precondition failed" in html
    assert "No security workload or scenario assertions were executed" in html
    assert "outputs/topology-preflight/topology-health.json" in html


def test_disabled_auto_redeploy_never_mutates_unhealthy_topology(
    monkeypatch, tmp_path
):
    repair_calls = []
    _handle, manifest, calls, _preflight_calls = _run(
        monkeypatch,
        tmp_path,
        health_state="unhealthy",
        auto_redeploy=False,
        topology_redeploy=lambda *_args: repair_calls.append(True),
    )

    assert repair_calls == []
    assert calls == []
    assert manifest["exit_status"] == "precondition_failed"


@pytest.mark.parametrize("state", ["unknown", "catching_up"])
def test_auto_redeploy_does_not_mutate_ambiguous_or_advancing_state(
    monkeypatch, tmp_path, state
):
    repair_calls = []
    _handle, manifest, calls, _preflight_calls = _run(
        monkeypatch,
        tmp_path,
        health_state=state,
        auto_redeploy=True,
        topology_redeploy=lambda *_args: repair_calls.append(True),
    )

    assert repair_calls == []
    assert calls == []
    assert manifest["exit_status"] == "precondition_failed"


def test_enabled_auto_redeploy_repairs_once_then_runs_after_fresh_preflight(
    monkeypatch, tmp_path
):
    unhealthy = {
        "state": "unhealthy",
        "reason_code": "amaru_relay_stalled",
        "checked_at": "2026-09-15T00:00:00Z",
    }
    healthy = {
        "state": "healthy",
        "reason_code": "all_mixed_readiness_gates_passed",
        "checked_at": "2026-09-15T00:20:00Z",
    }
    repair_calls = []

    handle, manifest, calls, preflight_calls = _run(
        monkeypatch,
        tmp_path,
        auto_redeploy=True,
        health_results=[unhealthy, healthy],
        topology_redeploy=lambda topology_id, state_dir: repair_calls.append(
            (topology_id, state_dir)
        )
        or {"passed": True, "evidence_root": "/retained/redeploy"},
    )

    assert len(repair_calls) == 1
    assert len(preflight_calls) == 2
    assert any(primitive == "load_shell_command" for _phase, primitive in calls)
    assert manifest["exit_status"] == "pass"
    before = (
        handle.run_dir
        / "outputs"
        / "topology-preflight"
        / "topology-health-before-repair.json"
    )
    assert json.loads(before.read_text())["state"] == "unhealthy"


def test_enabled_auto_redeploy_failure_stops_after_one_attempt(monkeypatch, tmp_path):
    repair_calls = []
    _handle, manifest, calls, preflight_calls = _run(
        monkeypatch,
        tmp_path,
        health_state="unhealthy",
        auto_redeploy=True,
        topology_redeploy=lambda *_args: repair_calls.append(True)
        or {"passed": False, "evidence_root": "/retained/redeploy"},
    )

    assert repair_calls == [True]
    assert len(preflight_calls) == 1
    assert calls == []
    assert manifest["exit_status"] == "precondition_failed"
    assert manifest["precondition"]["reason_code"] == "topology_redeploy_failed"
