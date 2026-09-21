import json
from pathlib import Path

import pytest

from profile_manager import primitives as primitive_module
from profile_manager import scenario as scenario_module
from profile_manager import telemetry


class _Observer:
    def __init__(self, **_kwargs):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def summarize(self):
        return {}


class _Handle:
    def __init__(self, run_dir: Path, target_identity: dict | None = None):
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._measurement_context = {
            "resolution": {"target_identity": target_identity or {}}
        }
        self.logs = []
        self.samples = []

    def log(self, **entry):
        self.logs.append(entry)

    def probe_sample(self, name, *, value, meta=None):
        self.samples.append({"name": name, "value": value, "meta": meta})


def _identity() -> dict:
    return {
        "implementation": "amaru",
        "version": "10.11.20260912",
        "source_revision": "b159172f25a9c389f82f20bca4f15e3032791638",
        "mode": "patched",
        "image_digest": "sha256:" + "a" * 64,
        "executable_digest": "sha256:" + "b" * 64,
        "patch_set_sha256": "c" * 64,
    }


def _primitive(class_name: str, params: dict):
    assert hasattr(primitive_module, class_name), f"missing primitive class {class_name}"
    return getattr(primitive_module, class_name)(params=params)


def test_scenario_runner_samples_one_shot_probe_after_load(monkeypatch, tmp_path):
    body = {
        "spec_version": "v1",
        "id": "one-shot-probe-test",
        "title": "One shot probe test",
        "target": {"implementation": "cardano-node", "version": "test"},
        "runtime": "library",
        "setup": [],
        "load": [],
        "faults": [],
        "probes": [{"primitive": "parser_exit_status"}],
        "assertions": [],
        "teardown": [],
    }
    path = tmp_path / "scenario.yaml"
    path.write_text(json.dumps(body), encoding="utf-8")
    monkeypatch.setattr(telemetry, "ObserverCollector", _Observer)
    samples = []

    class _OneShotProbe:
        def sample(self, handle):
            samples.append(handle.run_id)

    monkeypatch.setattr(
        primitive_module,
        "instantiate",
        lambda *_args, **_kwargs: _OneShotProbe(),
    )

    scenario_module.run_scenario(
        path,
        runs_dir=tmp_path / "runs",
        state_dir=tmp_path / "state",
    )

    assert len(samples) == 1


def test_scenario_runner_exposes_measurement_runtime_to_primitives(monkeypatch, tmp_path):
    body = {
        "spec_version": "v1",
        "id": "measurement-runtime-binding-test",
        "title": "Measurement runtime binding test",
        "target": {"implementation": "cardano-node", "version": "test"},
        "runtime": "library",
        "setup": [{"primitive": "parser_setup"}],
        "load": [],
        "faults": [],
        "probes": [],
        "assertions": [],
        "teardown": [],
    }
    path = tmp_path / "scenario.yaml"
    path.write_text(json.dumps(body), encoding="utf-8")
    monkeypatch.setattr(telemetry, "ObserverCollector", _Observer)
    observed = []

    class _RuntimeAwareSetup:
        def run(self, handle, _rng):
            observed.append(getattr(handle, "_measurement_runtime", None))

    monkeypatch.setattr(
        primitive_module,
        "instantiate",
        lambda *_args, **_kwargs: _RuntimeAwareSetup(),
    )

    scenario_module.run_scenario(
        path,
        runs_dir=tmp_path / "runs",
        state_dir=tmp_path / "state",
        measurement_context={"resolved": [], "target_identity": {}},
    )

    assert len(observed) == 1
    assert observed[0] is not None
    assert observed[0].__class__.__name__ == "MeasurementRuntime"


def test_runtime_verify_exact_target_retains_matching_identity(tmp_path):
    expected = _identity()
    handle = _Handle(tmp_path / "run", target_identity=expected)
    primitive = _primitive("RuntimeVerifyExactTarget", expected)

    primitive.run(handle, None)

    proof = json.loads(
        (handle.run_dir / "outputs" / "client-example-proof" / "exact-target.json").read_text()
    )
    assert proof["matched"] is True
    assert proof["observed"] == expected
    assert handle.logs[-1]["payload"]["outcome"] == "ok"


def test_runtime_verify_exact_target_fails_closed_on_mismatch(tmp_path):
    expected = _identity()
    observed = {**expected, "source_revision": "wrong"}
    handle = _Handle(tmp_path / "run", target_identity=observed)
    primitive = _primitive("RuntimeVerifyExactTarget", expected)

    with pytest.raises(RuntimeError, match="source_revision"):
        primitive.run(handle, None)


def _write_protocol_report(run_dir: Path, *, peer_usable: bool = True) -> Path:
    destination = run_dir / "outputs" / "protocol-decode-cases" / "result.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(
            {
                "target_health": {
                    "before": {"running": True, "restart_count": 2, "oom_killed": False},
                    "after": {"running": True, "restart_count": 2, "oom_killed": False},
                    "tip_before": {"block_height": 100, "hash": "aa"},
                    "tip_after": {"block_height": 104, "hash": "bb"},
                    "log_signals": {"fatal": [], "background": []},
                },
                "peer_session": {
                    "peer_id": "honest-peer-1",
                    "before": {"usable": True},
                    "during": {"usable": peer_usable},
                    "after": {"usable": True},
                    "recovered_within_seconds": 0.0,
                },
            }
        ),
        encoding="utf-8",
    )
    return destination


def test_target_health_and_progress_probe_retains_non_vacuous_evidence(tmp_path):
    handle = _Handle(tmp_path / "run")
    _write_protocol_report(handle.run_dir)
    primitive = _primitive("RuntimeTargetHealthAndProgress", {})

    primitive.sample(handle)

    sample = handle.samples[-1]
    assert sample["name"] == "runtime_target_health_and_progress"
    assert sample["value"]["checks"] == {
        "no_fatal_signals": True,
        "target_not_oom_killed": True,
        "target_restart_count_unchanged": True,
        "target_running_after": True,
        "target_running_before": True,
        "target_progressed": True,
    }


def test_peer_session_health_probe_requires_before_during_and_after(tmp_path):
    handle = _Handle(tmp_path / "run")
    _write_protocol_report(handle.run_dir, peer_usable=True)
    primitive = _primitive("RuntimePeerSessionHealth", {})

    primitive.sample(handle)

    sample = handle.samples[-1]
    assert sample["name"] == "runtime_peer_session_health"
    assert sample["value"]["peer_id"] == "honest-peer-1"
    assert sample["value"]["checks"] == {
        "peer_usable_after": True,
        "peer_usable_before": True,
        "peer_usable_during": True,
    }


def test_health_and_peer_probes_refresh_live_post_recovery_state(monkeypatch, tmp_path):
    handle = _Handle(tmp_path / "run")
    report_path = _write_protocol_report(handle.run_dir, peer_usable=True)
    report = json.loads(report_path.read_text())
    report["target_health"]["tip_before"] = {"block_height": 95, "hash": "00"}
    report["target_health"]["tip_after"] = {"block_height": 100, "hash": "aa"}
    report["target_health"]["observer"] = {"implementation": "amaru"}
    report["peer_session"]["after"] = {"usable": False}
    report["peer_session"]["observer"] = {"container": "honest-peer"}
    report_path.write_text(json.dumps(report))
    monkeypatch.setattr(
        primitive_module,
        "_observe_protocol_target",
        lambda _observer: {
            "state": {"running": True, "restart_count": 2, "oom_killed": False},
            "tip": {"block_height": 105, "hash": "cc"},
            "log_signals": {"fatal": [], "background": []},
        },
    )
    monkeypatch.setattr(
        primitive_module,
        "_observe_protocol_peer",
        lambda _observer: {"usable": True, "tip": {"block_height": 105}},
    )

    _primitive("RuntimeTargetHealthAndProgress", {}).sample(handle)
    _primitive("RuntimePeerSessionHealth", {}).sample(handle)

    assert handle.samples[-2]["value"]["tip_after"]["block_height"] == 105
    assert handle.samples[-2]["value"]["tip_initial"]["block_height"] == 95
    assert handle.samples[-2]["value"]["tip_before"]["block_height"] == 100
    assert handle.samples[-2]["value"]["tip_hostile_end"]["block_height"] == 100
    assert handle.samples[-2]["value"]["checks"]["target_progressed"] is True
    assert handle.samples[-1]["value"]["after"]["usable"] is True


def test_shared_proof_controls_are_registered_with_schemas():
    root = Path(__file__).resolve().parents[1]
    registry = primitive_module.load_registry(root / "dwarf" / "primitives" / "registry.json")
    expected = {
        "runtime_verify_exact_target": "setup",
        "runtime_target_health_and_progress": "probe",
        "runtime_peer_session_health": "probe",
    }
    for name, family in expected.items():
        assert name in registry
        assert registry[name].family == family
        assert registry[name].params_schema
        assert (root / "dwarf" / registry[name].params_schema).is_file()


def test_baseline_and_recovery_markers_create_complete_windows(tmp_path):
    handle = _Handle(tmp_path / "run")
    observed = []

    class _Runtime:
        def mark_phase(self, phase_id, state):
            observed.append((phase_id, state))
            return {"phase_id": phase_id, "state": state}

    handle._measurement_runtime = _Runtime()

    _primitive("RuntimeMarkBaselineWindow", {"duration_seconds": 0}).run(handle, None)
    _primitive("RuntimeMarkRecoveryWindow", {"duration_seconds": 0}).run(handle, None)

    assert observed == [
        ("baseline", "start"),
        ("baseline", "end"),
        ("recovery", "start"),
        ("recovery", "end"),
    ]


def test_baseline_marker_rejects_negative_duration(tmp_path):
    handle = _Handle(tmp_path / "run")
    handle._measurement_runtime = object()

    with pytest.raises(ValueError, match="non-negative"):
        _primitive("RuntimeMarkBaselineWindow", {"duration_seconds": -1}).run(
            handle, None
        )


def test_recovery_marker_always_closes_a_started_window(monkeypatch, tmp_path):
    handle = _Handle(tmp_path / "run")
    observed = []

    class _Runtime:
        def mark_phase(self, phase_id, state):
            observed.append((phase_id, state))

    handle._measurement_runtime = _Runtime()

    def fail_wait(_duration):
        raise RuntimeError("wait interrupted")

    monkeypatch.setattr("time.sleep", fail_wait)

    with pytest.raises(RuntimeError, match="wait interrupted"):
        _primitive("RuntimeMarkRecoveryWindow", {"duration_seconds": 1}).run(
            handle, None
        )

    assert observed == [("recovery", "start"), ("recovery", "end")]


def test_hostile_marker_requires_workload_owned_complete_window(tmp_path):
    handle = _Handle(tmp_path / "run")
    windows = handle.run_dir / "measurements" / "windows.ndjson"
    windows.parent.mkdir(parents=True)
    windows.write_text(
        "\n".join(
            [
                json.dumps({"phase_id": "hostile", "state": "start", "epoch_seconds": 10.0}),
                json.dumps({"phase_id": "hostile", "state": "end", "epoch_seconds": 45.0}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    _primitive("RuntimeMarkHostileWindow", {}).run(handle, None)

    assert handle.logs[-1]["payload"]["outcome"] == "ok"
    assert handle.logs[-1]["payload"]["duration_seconds"] == 35.0


@pytest.mark.parametrize(
    "markers,error",
    [
        (
            [
                {"phase_id": "hostile", "state": "start", "epoch_seconds": 45.0},
                {"phase_id": "hostile", "state": "end", "epoch_seconds": 10.0},
            ],
            "reversed",
        ),
        (
            [
                {"phase_id": "hostile", "state": "start", "epoch_seconds": 10.0},
                {"phase_id": "hostile", "state": "start", "epoch_seconds": 11.0},
                {"phase_id": "hostile", "state": "end", "epoch_seconds": 45.0},
            ],
            "exactly one",
        ),
    ],
)
def test_hostile_marker_rejects_invalid_pairs(tmp_path, markers, error):
    handle = _Handle(tmp_path / "run")
    windows = handle.run_dir / "measurements" / "windows.ndjson"
    windows.parent.mkdir(parents=True)
    windows.write_text(
        "\n".join(json.dumps(marker) for marker in markers) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match=error):
        _primitive("RuntimeMarkHostileWindow", {}).run(handle, None)


def test_window_marker_primitives_are_registered_with_schemas():
    root = Path(__file__).resolve().parents[1]
    registry = primitive_module.load_registry(root / "dwarf" / "primitives" / "registry.json")
    expected = {
        "runtime_mark_baseline_window": "setup",
        "runtime_mark_hostile_window": "load",
        "runtime_mark_recovery_window": "load",
    }
    for name, family in expected.items():
        assert name in registry
        assert registry[name].family == family
        assert (root / "dwarf" / registry[name].params_schema).is_file()


def test_target_health_probe_can_bind_progress_to_load_start(monkeypatch, tmp_path):
    handle = _Handle(tmp_path / "run")
    report_path = _write_protocol_report(handle.run_dir)
    report = json.loads(report_path.read_text())
    report["target_health"]["tip_before"] = {"block_height": 100, "hash": "aa"}
    report["target_health"]["tip_after"] = {"block_height": 105, "hash": "bb"}
    report["target_health"]["observer"] = {"implementation": "amaru"}
    report_path.write_text(json.dumps(report))
    monkeypatch.setattr(
        primitive_module,
        "_observe_protocol_target",
        lambda _observer: {
            "state": {"running": True, "restart_count": 2, "oom_killed": False},
            "tip": {"block_height": 105, "hash": "bb"},
            "log_signals": {"fatal": [], "background": []},
        },
    )

    _primitive(
        "RuntimeTargetHealthAndProgress",
        {"progress_reference": "load-start"},
    ).sample(handle)

    evidence = handle.samples[-1]["value"]
    assert evidence["progress_reference"] == "load-start"
    assert evidence["tip_before"]["block_height"] == 100
    assert evidence["checks"]["target_progressed"] is True
