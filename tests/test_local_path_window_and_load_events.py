"""Local-path wiring campaign, second layer (2026-11-08): F58 and F59.

F58: the txsubmission probe primitives run inside a measured window but never
wrote the ``hostile`` phase markers that ``runtime_mark_hostile_window``
verifies, so the window scenario failed right after its exact-target check.
``runtime_protocol_decode_cases`` already marks the window; the probe family
must do the same whenever a measurement runtime is active.

F59: ``load_events_are_ok`` demanded at least two *completed* load events by
default, which a single-shot load primitive (one started, one completed) can
never satisfy; the non-vacuity guard counts load events, the completed-with-
outcome count is what ``min_completed`` governs.
"""
import subprocess
from pathlib import Path

from profile_manager import primitives


class _Handle:
    def __init__(self, run_dir, *, runtime=None, events=None):
        self.run_dir = run_dir
        self.logs = []
        if runtime is not None:
            self._measurement_runtime = runtime
        if events is not None:
            self.events = events

    def log(self, **entry):
        self.logs.append(entry)


class _Runtime:
    def __init__(self):
        self.marks = []

    def mark_phase(self, phase_id, state, *, phase_index=None):
        self.marks.append((phase_id, state))
        return {"phase_id": phase_id, "state": state}


def _fake_probe(monkeypatch, runtime, calls):
    monkeypatch.setattr(primitives, "_resolve_client_runtime_target", lambda handle, params: {
        "id": "amaru-relay-1", "listen_host": "10.192.7.10", "listen_port": 3000,
        "runtime_metadata_path": "/var/dwarf/controller-state/x/runtime.json",
    })
    monkeypatch.setattr(primitives, "build_runtime_txsubmission_probe_command",
                        lambda *, config_path, mode: ["true", mode])

    def fake_run(command, **kwargs):
        calls.append(("subprocess", list(runtime.marks)))
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(primitives.subprocess, "run", fake_run)


def test_txsubmission_window_pressure_marks_the_hostile_window_around_the_probe(tmp_path, monkeypatch):
    runtime = _Runtime(); calls = []
    _fake_probe(monkeypatch, runtime, calls)
    prim = primitives.RuntimeTxsubmissionWindowPressure(params={
        "profile_id": "profile-z-amaru-20260918-nanoseconds-v3", "output_dir": "outputs/txsubmission-window",
    })
    prim.run(_Handle(tmp_path, runtime=runtime), None)
    assert runtime.marks == [("hostile", "start"), ("hostile", "end")]
    # The probe ran strictly inside the window: start was written before it, end after.
    assert calls == [("subprocess", [("hostile", "start")])]


def test_txsubmission_probe_without_a_measurement_runtime_marks_nothing_and_still_runs(tmp_path, monkeypatch):
    runtime = _Runtime(); calls = []
    _fake_probe(monkeypatch, runtime, calls)
    prim = primitives.RuntimeTxsubmissionBatchPressure(params={
        "profile_id": "profile-z-amaru-20260918-nanoseconds-v3", "output_dir": "outputs/txsubmission-batch",
    })
    prim.run(_Handle(tmp_path), None)
    assert runtime.marks == [] and len(calls) == 1


def _load_events(*entries):
    return [{"phase": "load", "primitive": "shim_peer_malformed_blockfetch", **entry} for entry in entries]


def test_single_shot_load_with_one_ok_completion_passes(tmp_path):
    handle = _Handle(tmp_path, events=_load_events(
        {"event": "started", "payload": {}},
        {"event": "completed", "payload": {"outcome": "ok"}},
    ))
    result = primitives.LoadEventsAreOk(params={"min_completed": 1}).evaluate(handle)
    assert result["result"] == "pass", result
    assert result["evaluated_value"]["completed"] == 1
    assert result["evaluated_value"]["load_events"] == 2


def test_a_lone_completion_without_a_start_is_still_vacuous(tmp_path):
    handle = _Handle(tmp_path, events=_load_events({"event": "completed", "payload": {"outcome": "ok"}}))
    result = primitives.LoadEventsAreOk(params={"min_completed": 1}).evaluate(handle)
    assert result["result"] == "fail"
    assert "load events" in result["note"]


def test_a_non_ok_completion_still_fails(tmp_path):
    handle = _Handle(tmp_path, events=_load_events(
        {"event": "started", "payload": {}},
        {"event": "completed", "payload": {"outcome": "unexpected_exit", "exit_code": 1}},
    ))
    result = primitives.LoadEventsAreOk(params={"min_completed": 1}).evaluate(handle)
    assert result["result"] == "fail"
    assert result["data_points_used"] == [{"outcome": "unexpected_exit", "exit_code": 1}]


def test_explicit_three_completions_contract_is_unchanged(tmp_path):
    entries = []
    for _ in range(3):
        entries += [{"event": "started", "payload": {}}, {"event": "completed", "payload": {"outcome": "ok"}}]
    handle = _Handle(tmp_path, events=_load_events(*entries))
    result = primitives.LoadEventsAreOk(params={"min_completed": 3, "min_event_count": 3}).evaluate(handle)
    assert result["result"] == "pass"
    assert result["evaluated_value"]["required_completed"] == 3
