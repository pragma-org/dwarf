import json
from pathlib import Path

import pytest

from profile_manager.primitives import load_registry
from profile_manager.scenario import scenario_from_body
import scripts.runtime_cardano_measurement_calibration as calibration


ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / "dwarf" / "scenarios" / "cardano-measurement-e2e-stock.yaml"
SCHEMA = ROOT / "dwarf" / "primitives" / "load" / "runtime_cardano_measurement_calibration.schema.json"


def test_cardano_measurement_scenario_is_exact_non_vacuous_and_portable():
    body = json.loads(SCENARIO.read_text())
    scenario = scenario_from_body((json.dumps(body) + "\n").encode())
    assert scenario.target == {"implementation": "cardano-node", "version": "11.1.2"}
    assert scenario.profile == "profile-s-cardano-measurement-stock-control"
    assert scenario.measurement_profile == "cardano-security-default"
    assert scenario.seed == "0xCA4DA001"
    assert len(body["load"]) == 1
    load = body["load"][0]
    assert load["primitive"] == "runtime_cardano_measurement_calibration"
    assert load["attempts"] >= 100
    assert load["observation_seconds"] >= 2
    assert load["trace_timeout_seconds"] >= load["observation_seconds"]
    assert load["profile_id"] == scenario.profile
    assert "runtime_root" not in load
    text = SCENARIO.read_text()
    assert "/home/" not in text and "/Users/" not in text


def test_cardano_calibration_is_registered_and_schema_is_portable():
    registry = load_registry(ROOT / "dwarf" / "primitives" / "registry.json")
    spec = registry["runtime_cardano_measurement_calibration"]
    assert spec.supports == ["cardano-node"]
    schema = json.loads(SCHEMA.read_text())
    assert schema["oneOf"] == [
        {"required": ["profile_id"], "not": {"required": ["runtime_root"]}},
        {"required": ["runtime_root"], "not": {"required": ["profile_id"]}},
    ]


def test_cardano_workload_identity_is_exact_and_targets_real_n2n_listener():
    identity = calibration.build_workload_identity(attempt_count=100)
    assert identity["implementation"] == "cardano-node"
    assert identity["target_port"] == 3001
    assert identity["mini_protocol"] == "handshake"
    assert identity["case"] == "unsupported-version-refusal"
    assert identity["workload_digest"].startswith("sha256:")


def _runtime_body(*, digest: str = "sha256:" + "6" * 64):
    revision = "fef83fed01d7926f3de83b3b917be5a4a48768b5"
    release = {
        "implementation": "cardano-node",
        "version": "11.1.2",
        "source_revision": revision,
        "artifacts": [{"kind": "oci", "availability": "available", "digest": digest}],
    }
    return {
        "version_provenance": {"catalog_snapshot": {"selected_releases": [release]}},
        "nodes": [{
            "id": "node1",
            "impl": "cardano-node",
            "source_revision": revision,
            "container_name": "dwarf-cardano-node1",
            "image_ref": f"ghcr.io/intersectmbo/cardano-node:11.1.2@{digest}",
            "artifact_identity": {"satisfied": True, "image_digest": digest},
            "version_identity": {"satisfied": True},
        }],
    }


def test_cardano_runtime_leg_retains_every_timed_outcome_and_exact_identity(
    monkeypatch, tmp_path
):
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    runtime_root.joinpath("runtime.json").write_text(json.dumps(_runtime_body()))
    output_dir = tmp_path / "run" / "outputs" / "cardano-measurement-calibration"
    attempts = [
        {"attempt_id": "handshake-0000", "outcome": "rejected", "elapsed_micros": 11},
        {"attempt_id": "handshake-0001", "outcome": "timeout", "elapsed_micros": 23},
        {"attempt_id": "handshake-0002", "outcome": "disconnected", "elapsed_micros": 17},
    ]
    monkeypatch.setattr(calibration, "_container_ip", lambda container: "172.20.0.2")
    monkeypatch.setattr(calibration, "run_attempts", lambda **kwargs: attempts)
    monkeypatch.setattr(calibration.time, "sleep", lambda _seconds: None)

    def capture(container, *, since, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text('{"ns":"Net.Handshake.Remote","data":{"kind":"Handshake"}}\n')
        return {
            "record_count": 1,
            "protocol_record_count": 1,
            "byte_count": destination.stat().st_size,
            "namespaces": ["Net.Handshake.Remote"],
        }

    monkeypatch.setattr(calibration, "_capture_logs", capture)
    result = calibration.run_leg(
        runtime_root=runtime_root,
        output_dir=output_dir,
        attempt_count=3,
        timeout_seconds=2,
        observation_seconds=2,
        trace_timeout_seconds=20,
    )

    assert result["target"]["version"] == "11.1.2"
    assert result["target"]["source_revision"] == "fef83fed01d7926f3de83b3b917be5a4a48768b5"
    assert result["attempts"] == {
        "total": 3,
        "outcomes": {"disconnected": 1, "rejected": 1, "timeout": 1},
        "artifact": "attempts.ndjson",
    }
    assert result["measurements"]["handshake_rejection_roundtrip"]["sample_count"] == 3
    assert len(output_dir.joinpath("attempts.ndjson").read_text().splitlines()) == 3
    assert output_dir.joinpath("raw/node1.ndjson").is_file()
    assert result["node_trace"]["record_count"] == 1
    assert result["node_trace"]["protocol_record_count"] == 1
    assert result["node_trace"]["minimum_observation_seconds"] == 2


def test_cardano_runtime_identity_fails_closed_on_digest_mismatch():
    body = _runtime_body()
    body["nodes"][0]["artifact_identity"]["image_digest"] = "sha256:" + "9" * 64
    with pytest.raises(RuntimeError, match="does not match the catalog"):
        calibration._target(body)


def test_cardano_runtime_identity_accepts_exact_patched_target():
    body = _runtime_body()
    node = body["nodes"][0]
    patched_digest = "sha256:" + "a" * 64
    node.update({
        "target_mode": "patched",
        "patch_set_sha256": calibration.CARDANO_MEASUREMENT_PATCH_SHA256,
        "image_digest": patched_digest,
        "image_ref": "dwarf/cardano-measurement@" + patched_digest,
        "executable_digest": "sha256:" + "b" * 64,
        "build_result_sha256": "sha256:" + "c" * 64,
        "runtime_probe_log_sha256": "sha256:" + "d" * 64,
        "artifact_identity": {"satisfied": True, "image_digest": patched_digest},
    })

    target, _node = calibration._target(body)

    assert target["mode"] == "patched"
    assert target["image_digest"] == patched_digest
    assert target["patch_set_sha256"] == calibration.CARDANO_MEASUREMENT_PATCH_SHA256


def test_cardano_runtime_identity_rejects_wrong_patched_target():
    body = _runtime_body()
    node = body["nodes"][0]
    node.update({
        "target_mode": "patched",
        "patch_set_sha256": "9" * 64,
        "image_digest": "sha256:" + "a" * 64,
        "image_ref": "dwarf/cardano-measurement@sha256:" + "a" * 64,
        "artifact_identity": {"satisfied": True, "image_digest": "sha256:" + "a" * 64},
    })
    with pytest.raises(RuntimeError, match="patch-set"):
        calibration._target(body)


def test_cardano_runtime_cli_fails_closed_when_node_trace_is_empty(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        calibration,
        "run_leg",
        lambda **_kwargs: {
            "node_trace": {"record_count": 1, "protocol_record_count": 0}
        },
    )

    exit_code = calibration.main(
        [
            "leg",
            "--runtime-root",
            str(tmp_path / "runtime"),
            "--output-dir",
            str(tmp_path / "output"),
            "--attempts",
            "100",
            "--observation-seconds",
            "2",
            "--trace-timeout-seconds",
            "20",
        ]
    )

    assert exit_code == 2


def test_cardano_paired_calibration_requires_exact_identity_and_common_samples():
    common = {
        "schema_version": 1,
        "runner": {"script": "runtime_cardano_measurement_calibration.py", "script_sha256": "sha256:" + "1" * 64},
        "timing_policy": {"clock": "monotonic-perf-counter-ns"},
        "hardware": {"architecture": "x86_64", "logical_cpu_count": 16},
        "workload_identity": calibration.build_workload_identity(attempt_count=30),
        "attempts": {"total": 30, "outcomes": {"rejected": 30}},
        "measurements": {
            "handshake_rejection_roundtrip": {
                "sample_count": 30,
                "unit": "us",
                "mean": 10,
                "median": 10,
                "p95": 12,
                "p99": 13,
            }
        },
    }
    stock = {
        **common,
        "target": {
            "implementation": "cardano-node", "version": "11.1.2",
            "source_revision": calibration.CARDANO_SOURCE_REVISION, "mode": "stock",
        },
    }
    patched = json.loads(json.dumps(common))
    patched["target"] = {
        "implementation": "cardano-node", "version": "11.1.2",
        "source_revision": calibration.CARDANO_SOURCE_REVISION, "mode": "patched",
        "patch_set_sha256": calibration.CARDANO_MEASUREMENT_PATCH_SHA256,
    }
    patched["measurements"]["handshake_rejection_roundtrip"].update({
        "mean": 11, "median": 11, "p95": 13, "p99": 14,
    })

    result = calibration.paired_cardano_overhead_calibration(stock, patched)

    assert result["status"] == "available"
    assert result["metrics"]["handshake_rejection_roundtrip"]["median_percent_delta"] == 10.0
