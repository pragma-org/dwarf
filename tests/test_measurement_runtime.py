import json

import pytest

from profile_manager import scenario, telemetry
from profile_manager.measurement_runtime import (
    MeasurementArtifactPathError,
    MeasurementRuntime,
)


def _resolved(measurement_id, *, gate=None):
    return {
        "id": measurement_id,
        "enabled": True,
        "source": "scenario-override",
        "parameters": {"window_seconds": 5},
        "threshold_gate": gate or {"enabled": False, "thresholds": []},
        "definition_digest": "sha256:" + "d" * 64,
        "definition": {
            "schema_version": "v1",
            "id": measurement_id,
            "collection_mode": "external",
        },
    }


def _resolution(*entries):
    return {
        "target_identity": {
            "implementation": "amaru",
            "version": "10.11.20260912",
            "source_revision": "b159172f25a9c389f82f20bca4f15e3032791638",
            "mode": "stock",
            "image_digest": "sha256:" + "4" * 64,
        },
        "profile": {"id": "test", "source": "explicit-profile"},
        "requested": [
            {"id": entry["id"], "enabled": True, "source": entry["source"]}
            for entry in entries
        ],
        "resolved": list(entries),
        "skipped": [],
        "incompatible": [],
        "disabled": [],
    }


class RecordingCollector:
    def __init__(self, events, *, result=None, fail_at=None):
        self.events = events
        self.result = result or {"metrics": []}
        self.fail_at = fail_at

    def _record(self, name, value=None):
        self.events.append((name, value))
        if self.fail_at == name:
            raise RuntimeError(f"failed during {name}")

    def prepare(self, context):
        self._record("prepare", context.measurement_id)
        context.write_json("prepared.json", {"prepared": True})

    def start(self, context):
        self._record("start", context.measurement_id)

    def on_marker(self, marker, context):
        self._record("marker", (marker["phase_id"], marker["state"]))

    def stop(self, context):
        self._record("stop", context.measurement_id)

    def finalize(self, context):
        self._record("finalize", context.measurement_id)
        return self.result


def test_runtime_executes_lifecycle_and_retains_phase_windows(tmp_path):
    events = []
    runtime = MeasurementRuntime(
        run_dir=tmp_path,
        resolution=_resolution(_resolved("tap-a")),
        collector_factories={
            "tap-a": lambda _entry: RecordingCollector(events),
        },
        wall_clock=iter([100.0, 101.0, 111.0, 112.0]).__next__,
        monotonic_clock=iter([10.0, 11.0, 21.0, 22.0]).__next__,
    )

    runtime.prepare()
    runtime.start()
    runtime.mark_phase("hostile", "start", phase_index=1)
    runtime.mark_phase("hostile", "end", phase_index=1)
    result = runtime.finalize()

    assert events == [
        ("prepare", "tap-a"),
        ("start", "tap-a"),
        ("marker", ("hostile", "start")),
        ("marker", ("hostile", "end")),
        ("marker", ("run", "end")),
        ("stop", "tap-a"),
        ("finalize", "tap-a"),
    ]
    assert result["collector_states"] == {"tap-a": "finalized"}
    assert result["gate_failed"] is False
    assert json.loads(
        (tmp_path / "measurements/collectors/tap-a/prepared.json").read_text()
    ) == {"prepared": True}
    markers = [
        json.loads(line)
        for line in (tmp_path / "measurements/windows.ndjson").read_text().splitlines()
    ]
    assert [(item["phase_id"], item["state"]) for item in markers] == [
        ("run", "start"),
        ("hostile", "start"),
        ("hostile", "end"),
        ("run", "end"),
    ]
    assert markers[-1]["elapsed_seconds"] == 12.0


@pytest.mark.parametrize("path", ["../escape.json", "/tmp/escape.json", "a/../../escape"])
def test_collector_artifacts_cannot_escape_their_bounded_directory(tmp_path, path):
    class EscapingCollector(RecordingCollector):
        def prepare(self, context):
            context.write_json(path, {"bad": True})

    runtime = MeasurementRuntime(
        run_dir=tmp_path,
        resolution=_resolution(_resolved("tap-a")),
        collector_factories={
            "tap-a": lambda _entry: EscapingCollector([]),
        },
    )

    runtime.prepare()

    assert runtime.snapshot()["collector_states"] == {"tap-a": "error"}
    assert runtime.snapshot()["collector_errors"][0]["type"] == "MeasurementArtifactPathError"
    assert not (tmp_path.parent / "escape.json").exists()


def test_collector_failure_is_isolated_and_other_collectors_finalize(tmp_path):
    bad_events = []
    good_events = []
    runtime = MeasurementRuntime(
        run_dir=tmp_path,
        resolution=_resolution(_resolved("bad"), _resolved("good")),
        collector_factories={
            "bad": lambda _entry: RecordingCollector(bad_events, fail_at="start"),
            "good": lambda _entry: RecordingCollector(good_events),
        },
    )

    runtime.prepare()
    runtime.start()
    runtime.mark_phase("load", "start")
    result = runtime.finalize()

    assert result["collector_states"] == {"bad": "error", "good": "finalized"}
    assert result["collector_errors"][0]["measurement_id"] == "bad"
    assert ("finalize", "good") in good_events
    assert ("marker", ("load", "start")) in good_events


def test_only_explicit_threshold_gate_can_change_scenario_exit_status(tmp_path):
    metric = {
        "metrics": [
            {"name": "accepted_per_second", "value": 7.0, "unit": "tx/s"}
        ]
    }
    ungated = MeasurementRuntime(
        run_dir=tmp_path / "ungated",
        resolution=_resolution(_resolved("tap-a")),
        collector_factories={
            "tap-a": lambda _entry: RecordingCollector([], result=metric),
        },
    )
    ungated.prepare()
    ungated.start()
    ungated_result = ungated.finalize()

    gate = {
        "enabled": True,
        "thresholds": [
            {
                "metric": "accepted_per_second",
                "operator": "gte",
                "value": 10,
                "unit": "tx/s",
            }
        ],
    }
    gated = MeasurementRuntime(
        run_dir=tmp_path / "gated",
        resolution=_resolution(_resolved("tap-a", gate=gate)),
        collector_factories={
            "tap-a": lambda _entry: RecordingCollector([], result=metric),
        },
    )
    gated.prepare()
    gated.start()
    gated_result = gated.finalize()

    assert ungated_result["gate_failed"] is False
    assert ungated.scenario_exit_status("pass") == "pass"
    assert gated_result["gate_failed"] is True
    assert gated_result["threshold_gates"]["tap-a"]["result"] == "fail"
    assert gated.scenario_exit_status("pass") == "fail"
    assert gated.scenario_exit_status("error") == "error"


def test_explicit_gate_fails_closed_when_collector_is_unavailable(tmp_path):
    gate = {
        "enabled": True,
        "thresholds": [
            {"metric": "accepted_per_second", "operator": "gte", "value": 1}
        ],
    }
    runtime = MeasurementRuntime(
        run_dir=tmp_path,
        resolution=_resolution(_resolved("missing-tap", gate=gate)),
        collector_factories={},
    )

    runtime.prepare()
    runtime.start()
    result = runtime.finalize()

    assert result["collector_states"] == {"missing-tap": "error"}
    assert result["threshold_gates"]["missing-tap"]["result"] == "fail"
    assert result["gate_failed"] is True
    assert runtime.scenario_exit_status("pass") == "fail"


def test_finalize_writes_collector_measurements_into_existing_run_bundle(tmp_path):
    collector_result = {
        "measurements": {
            "transfer": {
                "status": "available",
                "unit": "us",
                "sample_count": 20,
                "mean": 52.0,
                "median": 51.0,
                "p95": 70.0,
                "p99": 90.0,
            }
        }
    }
    runtime = MeasurementRuntime(
        run_dir=tmp_path,
        resolution=_resolution(_resolved("tap-a")),
        collector_factories={
            "tap-a": lambda _entry: RecordingCollector([], result=collector_result),
        },
        scenario_id="amaru-transfer-test",
    )
    runtime.prepare()
    runtime.start()
    result = runtime.finalize()

    assert result["report_artifacts"] == {
        "summary": "measurements/summary.json",
        "report": "measurements/report.json",
        "readable": "measurements/report.md",
        "compact_table": "measurements/compact-table.json",
    }
    report = json.loads((tmp_path / "measurements/report.json").read_text())
    assert report["scenario"] == "amaru-transfer-test"
    assert report["measurements"]["transfer"]["p99"] == 90.0


def test_finalize_stops_collectors_after_runner_exception_and_is_idempotent(tmp_path):
    events = []
    runtime = MeasurementRuntime(
        run_dir=tmp_path,
        resolution=_resolution(_resolved("tap-a")),
        collector_factories={
            "tap-a": lambda _entry: RecordingCollector(events),
        },
    )
    runtime.prepare()
    runtime.start()

    try:
        raise RuntimeError("scenario runner failed")
    except RuntimeError:
        first = runtime.finalize()
    second = runtime.finalize()

    assert events.count(("stop", "tap-a")) == 1
    assert events.count(("finalize", "tap-a")) == 1
    assert second == first


class _NoopObserver:
    def __init__(self, **_kwargs):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def summarize(self):
        return {}


def test_scenario_runner_wraps_actual_phases_and_finalizes_after_exception(
    monkeypatch, tmp_path
):
    events = []
    scenario_path = tmp_path / "scenario.yaml"
    scenario_path.write_text(
        json.dumps(
            {
                "spec_version": "v1",
                "id": "measured-runner",
                "title": "Measured runner",
                "target": {
                    "implementation": "amaru",
                    "version": "10.11.20260912",
                },
                "runtime": "library",
                "setup": [],
                "load": [{"primitive": "load_shell_command", "command": "true"}],
                "faults": [],
                "probes": [],
                "assertions": [],
                "teardown": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(telemetry, "ObserverCollector", _NoopObserver)

    def fail_load(_handle, _rng, _registry, _scen, refs, phase, **_kwargs):
        if refs and phase.endswith(":load"):
            raise RuntimeError("load failed")

    monkeypatch.setattr(scenario, "_run_phase", fail_load)

    handle = scenario.run_scenario(
        scenario_path,
        runs_dir=tmp_path / "runs",
        state_dir=tmp_path / "state",
        measurement_context=_resolution(_resolved("tap-a")),
        measurement_collector_factories={
            "tap-a": lambda _entry: RecordingCollector(events),
        },
    )

    manifest = json.loads((handle.run_dir / "manifest.json").read_text())
    assert manifest["exit_status"] == "error"
    assert manifest["measurements"]["collector_states"] == {"tap-a": "finalized"}
    assert ("stop", "tap-a") in events
    assert ("finalize", "tap-a") in events
    markers = [
        json.loads(line)
        for line in (handle.run_dir / "measurements/windows.ndjson").read_text().splitlines()
    ]
    assert [(item["phase_id"], item["state"]) for item in markers] == [
        ("run", "start"),
        ("default", "start"),
        ("default", "end"),
        ("run", "end"),
    ]
