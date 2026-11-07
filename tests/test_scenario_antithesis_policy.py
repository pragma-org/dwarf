import json
from pathlib import Path

import pytest

from profile_manager import scenario, telemetry
from profile_manager.scenario_verdict import evaluate_scenario_verdict


class _Observer:
    def __init__(self, **_kwargs):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def summarize(self):
        return {}


def _write_scenario(path: Path) -> Path:
    body = {
        "spec_version": "v1", "id": "antithesis-policy-test", "title": "policy", "authors": ["t"], "tags": [],
        "target": {"implementation": "cardano-node", "version": "test"}, "runtime": "library",
        "load": [{"primitive": "load_shell_command", "command": "true"}, {"primitive": "load_shell_command", "command": "true"}],
        "faults": [{"primitive": "fault_node_freeze", "target_node": "node1", "duration_seconds": 1}],
        "probes": [{"primitive": "unknown_probe_y"}],
        "assertions": [{"primitive": "unknown_primitive_x"}, {"primitive": "load_events_are_ok"}],
        "teardown": [],
    }
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


POLICY = {"fault_node_freeze": "delegated-fault", "unknown_primitive_x": "unsupported",
          "unknown_probe_y": "unsupported", "load_shell_command": "runs-with-note"}


def _events(handle):
    rows = []
    for line in (handle.run_dir / "log.ndjson").read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def _run(tmp_path, monkeypatch, **kwargs):
    monkeypatch.setattr(telemetry, "ObserverCollector", _Observer)
    handle = scenario.run_scenario(_write_scenario(tmp_path / "scenario.yaml"), runs_dir=tmp_path / "runs",
                                   state_dir=tmp_path / "state", **kwargs)
    manifest = json.loads((handle.run_dir / "manifest.json").read_text(encoding="utf-8"))
    assertions = json.loads((handle.run_dir / "assertions.json").read_text(encoding="utf-8"))
    return handle, manifest, assertions


def test_policy_skips_unsupported_and_delegated_fault_primitives(tmp_path, monkeypatch):
    handle, manifest, assertions = _run(tmp_path, monkeypatch, primitive_policy=POLICY)
    skipped = [(e["primitive"], e["payload"]["policy"]) for e in _events(handle) if e.get("event") == "primitive_skipped"]
    assert sorted(skipped) == [("fault_node_freeze", "delegated-fault"), ("unknown_primitive_x", "unsupported"),
                               ("unknown_probe_y", "unsupported")]
    by_primitive = {row["primitive"]: row for row in assertions}
    assert by_primitive["unknown_primitive_x"]["result"] == "skipped"
    assert by_primitive["unknown_primitive_x"]["note"] == "unsupported"
    assert by_primitive["load_events_are_ok"]["result"] == "pass"
    assert manifest["exit_status"] == "pass"
    assert manifest["assertion_summary"] == {"total": 2, "pass": 1, "fail": 0, "skipped": 1}


def test_without_policy_unknown_primitives_still_error(tmp_path, monkeypatch):
    handle, manifest, _ = _run(tmp_path, monkeypatch)
    assert manifest["exit_status"] == "error"


def test_after_prepare_runs_once_before_phases(tmp_path, monkeypatch):
    seen = []

    def hook(handle):
        seen.append(handle.run_dir)

    handle, manifest, _ = _run(tmp_path, monkeypatch, primitive_policy=POLICY, after_prepare=hook)
    assert seen == [handle.run_dir]
    events = [e.get("event") for e in _events(handle)]
    assert events.index("after_prepare_completed") < events.index("phase_started")
    assert manifest["exit_status"] == "pass"


def test_after_prepare_failure_ends_the_run_before_any_phase(tmp_path, monkeypatch):
    def hook(handle):
        raise RuntimeError("antithesis readiness probe timed out: node1")

    handle, manifest, assertions = _run(tmp_path, monkeypatch, primitive_policy=POLICY, after_prepare=hook)
    assert manifest["exit_status"] == "precondition_failed"
    assert manifest["precondition"]["reason_code"] == "after_prepare_failed"
    assert "timed out" in manifest["precondition"]["message"]
    assert assertions == []
    assert not any(e.get("event") == "phase_started" for e in _events(handle))


def test_skipped_assertions_never_count_as_passes():
    verdict = evaluate_scenario_verdict(exit_status="pass", assertions=[{"result": "skipped"}], measurements=None, run_kind="scenario")
    assert "no-passing-assertions" in verdict.reason_codes
    verdict = evaluate_scenario_verdict(exit_status="pass", assertions={"total": 2, "pass": 1, "fail": 0, "skipped": 1},
                                        measurements=None, run_kind="scenario")
    assert verdict.security_verdict == "pass"


def test_package_mode_delegates_scenario_measurements_to_the_package_collector(tmp_path, monkeypatch):
    """F43: inside the package there is no locally deployed profile runtime; the
    dwarf-measurements sidecar collects, so the runner must not prepare local
    measurements (which would abort the run before readiness)."""
    monkeypatch.setenv("DWARF_ANTITHESIS_PACKAGE_DIR", str(tmp_path / "package"))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("local measurement preparation ran inside the package")
    monkeypatch.setattr(scenario, "prepare_auto_measurements", forbidden)
    handle, manifest, assertions = _run(tmp_path, monkeypatch, primitive_policy=POLICY)
    skipped = [e for e in _events(handle) if e.get("event") == "measurement_auto_attach_skipped"]
    assert skipped and "dwarf-measurements" in skipped[0]["payload"]["reason"]
    assert manifest["exit_status"] == "pass"


def test_package_mode_seeds_the_package_runtime_as_the_deployed_substrate(tmp_path, monkeypatch):
    """F48: profile scenarios have no runtime_compose_substrate step; inside the
    package the rendered devnet's runtime.json is the deployed runtime, so the
    runner seeds the shared state and the prepared-runtime handle attribute."""
    package = tmp_path / "package"
    package.mkdir()
    (package / "runtime.json").write_text(json.dumps({"nodes": []}), encoding="utf-8")
    monkeypatch.setenv("DWARF_ANTITHESIS_PACKAGE_DIR", str(package))
    seen = {}
    original = scenario._run_phase

    def capture(handle, rng, registry, scen, refs, phase, **kwargs):
        seen["state"] = dict(kwargs.get("shared_state") or {})
        seen["prepared"] = getattr(handle, "_prepared_runtime_metadata_path", None)
        return original(handle, rng, registry, scen, refs, phase, **kwargs)
    monkeypatch.setattr(scenario, "_run_phase", capture)
    handle, manifest, assertions = _run(tmp_path, monkeypatch, primitive_policy=POLICY)
    expected = str(package / "runtime.json")
    # Profile-endpoint primitives accept exactly one of profile_id or substrate
    # metadata, so the substrate state must stay empty; the prepared-runtime
    # handle is what the profile route reads first.
    assert "substrate_runtime_metadata_path" not in seen["state"]
    assert str(seen["prepared"]) == expected
    assert manifest["exit_status"] == "pass"
