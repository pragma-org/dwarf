"""CI observations must not turn absent process facts into healthy defaults."""
from copy import deepcopy
import hashlib
import json
import subprocess
import pytest

from scripts import ci_cardano_health as health


def observation():
    return {"Id": "a" * 64, "State": {"Running": True, "OOMKilled": False,
            "StartedAt": "2026-10-22T00:00:00Z"}, "RestartCount": 0}


@pytest.mark.parametrize("missing", ["Running", "OOMKilled", "StartedAt", "RestartCount"])
def test_inspection_requires_explicit_process_fields(missing):
    value = observation()
    (value if missing == "RestartCount" else value["State"]).pop(missing)
    with pytest.raises(ValueError, match="process observation"):
        health.process_observation(value)


@pytest.mark.parametrize("change", [None, "restart", "oom", "stopped", "container", "started", "tip", "fatal", "logs"])
def test_health_requires_same_live_process_progress_and_logs(change):
    before = health.process_observation(observation())
    after = deepcopy(before)
    tips = [{"block_height": 2}, {"block_height": 3}]
    signals, records = {"fatal": []}, 2
    if change == "restart": after["restart_count"] = 1
    if change == "oom": after["oom_killed"] = True
    if change == "stopped": after["running"] = False
    if change == "container": after["container_id"] = "b" * 64
    if change == "started": after["started_at"] = "2026-10-22T00:00:01Z"
    if change == "tip": tips[1] = tips[0]
    if change == "fatal": signals = {"fatal": ["panic"]}
    if change == "logs": records = 0
    checks = health.health_checks(before, after, *tips, signals, records)
    assert all(checks.values()) is (change is None)


@pytest.mark.parametrize("mismatch", [None, "run", "campaign", "target", "retained", "native-failure"])
def test_reader_uses_only_matching_retained_ci_health_without_overriding_native(tmp_path, mismatch):
    from profile_manager.campaign_runtime_evidence import read_native_evidence
    root = tmp_path / "run-1"
    helper = root / "outputs/cardano-measurement-calibration"
    helper.mkdir(parents=True)
    target = {"implementation": "cardano-node", "version": "11.1.2"}
    report = {"target": target}
    observed = {"run_id": root.name, "campaign_digest": "sha256:" + "a" * 64,
                "node_id": "node2", "target": target, "observer": {"container": "b" * 64},
                "checks": health.health_checks(health.process_observation(observation()),
                    health.process_observation(observation()), {"block_height": 2},
                    {"block_height": 3}, {"fatal": []}, 1)}
    manifest = {"campaign_digest": observed["campaign_digest"], "required_tap_cells": [],
                "definitions": {"scenarios": {"scenario": {"digest": "sha256:" + "c" * 64}}}}
    if mismatch == "run": observed["run_id"] = "another-run"
    if mismatch == "campaign": observed["campaign_digest"] = "sha256:" + "d" * 64
    if mismatch == "target": observed["target"] = {**target, "version": "other"}
    if mismatch == "native-failure": report["target_health"] = {"checks": {"target_running_after": False}}
    path = root / "outputs/ci-cardano-health/result.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({} if mismatch == "retained" else observed))
    (helper / "result.json").write_text(json.dumps(report))
    attempt = {"request_hex": "01", "response_hex": "02", "request_sha256": "sha256:" + hashlib.sha256(b"\x01").hexdigest(),
               "listener_reached": True, "outcome": "accepted", "expected_external_outcome": "accepted", "detail": "accept-response"}
    (helper / "attempts.ndjson").write_text(json.dumps(attempt) + "\n")
    output = read_native_evidence(manifest, root, scenario_id="scenario",
        topology={"nodes": [{"node_id": "node2", "implementation": "cardano-node"}]}, mode="stock", plan={},
        health_observations={"node2": observed})
    assert (output["control"]["status"] == "pass") is (mismatch is None)


@pytest.mark.parametrize("failure", [None, "stopped", "inspect", "logs", "identity", "timeout"])
def test_observer_retains_success_and_post_run_failures(tmp_path, monkeypatch, failure):
    from scripts import runtime_cardano_measurement_calibration as native
    from profile_manager import profiles
    image = "sha256:" + "d" * 64
    target = {"implementation": "cardano-node", "version": "11.1.2", "source_revision": "e" * 40,
              "mode": "stock", "image_digest": image}
    node = {"container_id": "a" * 64, "container_name": "test-node", "container_socket_path": "/state/node.socket"}
    inspected = {**observation(), "Name": "/test-node", "Image": image,
                 "Config": {"Image": "example@" + image, "Labels": {"io.dwarf.installation": "test"}}}
    runtime = {"compose_project": "dwarf-profile-test-profile", "network_magic": 42}
    (tmp_path / "runtime.json").write_text(json.dumps(runtime))
    monkeypatch.setenv("DWARF_INSTALLATION_ID", "test")
    monkeypatch.setattr(profiles, "runtime_root", lambda _: tmp_path)
    monkeypatch.setattr(native, "_target", lambda _: (target, node))
    monkeypatch.setattr(native, "_verify_workload_container", lambda *_: inspected)
    tips = iter([{"block_height": 2}, {"block_height": 3}])
    monkeypatch.setattr(native, "_query_cardano_tip", lambda *a, **k: next(tips))
    manifest = {"campaign_digest": "sha256:" + "f" * 64, "definitions": {"profiles": {"profile": {}},
        "scenarios": {"scenario": {"document": {"load": [{"primitive": "runtime_cardano_measurement_calibration"}]}}}}}
    topology = {"nodes": [{"node_id": "node2", "identity": {**target, "artifacts": {"image_digest": image}}}]}
    # A grouped recipe can have an earlier representative measurement name.
    cell = {"node_id": "node2", "measurement_id": "cardano-external-workload-accounting"}
    plan = {"profile": {"id": "profile"}, "scenario": {"id": "scenario"}}
    started = health.begin(manifest, topology, "stock", plan, cell, "launch-1")
    assert started["before"]["container_id"] == node["container_id"]
    after = deepcopy(inspected)
    if failure == "stopped": after["State"]["Running"] = False
    if failure == "identity": after["Image"] = "sha256:" + "b" * 64
    def inspect(_):
        if failure == "inspect": raise RuntimeError("container unavailable")
        if failure == "timeout": raise subprocess.TimeoutExpired("docker inspect", 30)
        return after
    def logs(container, *, since, destination):
        assert container == node["container_id"] and since == started["started_at"]
        if failure == "logs": raise RuntimeError("logs unavailable")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text('{"ns":"ChainDB.AddBlockEvent.AddedToCurrentChain"}\n')
        return {"record_count": 1}
    monkeypatch.setattr(native, "_inspect_container", inspect)
    monkeypatch.setattr(native, "_capture_logs", logs)
    root = tmp_path / "run-1"
    result = health.finish(started, root)["node2"]
    assert all(result["checks"].values()) is (failure is None)
    assert json.loads((root / result["artifact"]).read_text()) == result
