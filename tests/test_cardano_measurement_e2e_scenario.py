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
            "artifact_identity": {
                "satisfied": True,
                "image_digest": digest,
                "image_ref": f"ghcr.io/intersectmbo/cardano-node:11.1.2@{digest}",
            },
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


def test_cardano_plutus_workload_preserves_direct_host_execution(
    monkeypatch, tmp_path
):
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    runtime_body = _runtime_body()
    runtime_root.joinpath("runtime.json").write_text(json.dumps(runtime_body))
    output_dir = tmp_path / "output"
    received = {}
    monkeypatch.setattr(calibration, "_controller_container", lambda: None)
    monkeypatch.setattr(calibration, "_container_ip", lambda _container: "127.0.0.1")
    monkeypatch.setattr(
        calibration,
        "run_attempts",
        lambda **_kwargs: [{
            "attempt_id": "handshake-0000",
            "outcome": "rejected",
            "elapsed_micros": 1,
        }],
    )

    def run_plutus_workload(**kwargs):
        received.update(kwargs)
        return {"status": "completed", "transactions": 1}

    def capture(_container, *, since, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            '{"ns":"Net.Handshake.Remote","data":{"kind":"Handshake"}}\n'
        )
        return {
            "record_count": 1,
            "protocol_record_count": 1,
            "byte_count": destination.stat().st_size,
            "namespaces": ["Net.Handshake.Remote"],
        }

    monkeypatch.setattr(calibration, "run_plutus_workload", run_plutus_workload)
    monkeypatch.setattr(calibration, "_capture_logs", capture)
    monkeypatch.setattr(calibration.time, "sleep", lambda _seconds: None)

    result = calibration.run_leg(
        runtime_root=runtime_root,
        output_dir=output_dir,
        attempt_count=1,
        timeout_seconds=1,
        observation_seconds=0,
        trace_timeout_seconds=0,
        plutus_transactions=1,
    )

    assert received["runtime"] == runtime_body
    assert received["node"] == runtime_body["nodes"][0]
    assert result["plutus_workload"] == {"status": "completed", "transactions": 1, "submission_entry": "target"}


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


def _support_inspect(image_ref, image_id, labels):
    return {
        "Id": "c" * 64,
        "Name": "/consumer",
        "Names": [],
        "Image": image_id,
        "State": {"Running": True},
        "Config": {"Image": image_ref, "Labels": labels},
    }


def test_verify_workload_container_accepts_support_component_labels(monkeypatch):
    image_ref = "ghcr.io/intersectmbo/cardano-node@sha256:" + "3" * 64
    image_id = "sha256:" + "3" * 64
    inspected = _support_inspect(image_ref, image_id, {
        "io.dwarf.managed": "true",
        "io.dwarf.component": "support",
        "io.dwarf.topology": "profile-o-amaru-target-latest-confirmed",
        "com.docker.compose.project": "proj",
        "com.docker.compose.service": "amaru-consumer",
    })
    monkeypatch.setattr(calibration, "_inspect_container", lambda _id: inspected)
    runtime = {"profile_id": "profile-o-amaru-target-latest-confirmed", "compose_project": "proj"}
    node = {
        "id": "amaru-consumer", "service_name": "amaru-consumer", "container_name": "consumer",
        "container_id": "c" * 64, "image_ref": image_ref, "image_id": image_id,
        "component": "support",
    }

    assert calibration._verify_workload_container(runtime, node) is inspected
    with pytest.raises(RuntimeError, match="ownership labels"):
        calibration._verify_workload_container(runtime, dict(node, component="node"))


def _operator_local_workload(monkeypatch, tmp_path, image_ref):
    image_id = "sha256:" + "d" * 64
    inspected = _support_inspect(image_ref, image_id, {})
    monkeypatch.setattr(calibration, "_verify_workload_container", lambda _r, _n: inspected)
    monkeypatch.setattr(
        calibration, "_owned_socket_volume", lambda **_k: ("proj_node1-env", "1000:1000")
    )
    monkeypatch.setattr(calibration, "_mount_source", lambda *_a, **_k: str(tmp_path / "runs"))
    output = tmp_path / "output"
    output.mkdir(exist_ok=True)
    monkeypatch.setattr(
        calibration, "_owned_run_output_relative", lambda *_a, **_k: (output, Path("run-1"))
    )
    monkeypatch.setattr(calibration, "_cleanup_workload_containers", lambda _id: None)
    runtime_root = tmp_path / "runtime"
    key_root = runtime_root / "env" / "utxo-keys" / "utxo1"
    key_root.mkdir(parents=True, exist_ok=True)
    (key_root / "utxo.vkey").write_text("{}")
    (key_root / "utxo.skey").write_text("{}")
    node = {
        "id": "node1", "service_name": "node1", "container_name": "node1-1",
        "container_id": "c" * 64, "image_ref": image_ref,
        "container_socket_path": "/env/socket/node1/sock", "socket_mount_destination": "/env",
        "socket_mount_type": "volume", "socket_volume_key": "node1-env",
    }
    return calibration._containerized_cardano_workload(
        runtime_root=runtime_root, runtime={"profile_id": "p", "compose_project": "proj"},
        node=node, output_dir=output, controller={"Id": "k" * 64},
    )


def test_containerized_workload_accepts_operator_local_image_id_only_when_inspected_matches(
    monkeypatch, tmp_path
):
    with _operator_local_workload(monkeypatch, tmp_path, "sha256:" + "d" * 64) as (
        workload_runtime, workload_node
    ):
        assert Path(workload_runtime["support_binaries"]["cardano-cli"]).is_file()
        assert workload_node["socket_path"] == "/env/socket/node1/sock"

    with pytest.raises(RuntimeError, match="digest-pinned"):
        with _operator_local_workload(
            monkeypatch, tmp_path, "ghcr.io/intersectmbo/cardano-node:11.1.2"
        ):
            pass


def test_amaru_entry_node_binds_consumer_socket_volume(monkeypatch):
    monkeypatch.setattr(calibration, "_inspect_container", lambda _id: {"Id": "e" * 64})
    runtime = {"identity": {"services": {"amaru-consumer": {
        "container": "consumer-1", "matched": True,
        "expected_image": "ghcr.io/intersectmbo/cardano-node@sha256:" + "3" * 64,
        "expected_image_id": "sha256:" + "3" * 64,
    }}}}

    node = calibration.amaru_entry_node(runtime)

    assert node["id"] == node["service_name"] == "amaru-consumer"
    assert node["container_name"] == "consumer-1"
    assert node["container_id"] == "e" * 64
    assert node["image_ref"].endswith("@sha256:" + "3" * 64)
    assert node["image_id"] == "sha256:" + "3" * 64
    assert node["socket_path"] == node["container_socket_path"] == "/state/node.socket"
    assert node["socket_mount_destination"] == "/state"
    assert node["socket_mount_type"] == "volume"
    assert node["socket_volume_key"] == "amaru-consumer-state"
    assert node["component"] == "support"
    with pytest.raises(RuntimeError, match="entry node"):
        calibration.amaru_entry_node({"identity": {"services": {}}})


def _fake_plutus_cli(monkeypatch, *, wait_outcomes=None):
    """Stand in for cardano-cli and chain queries around run_plutus_workload."""
    from types import SimpleNamespace

    calls = {"checked": [], "waits": [], "absent_waits": []}

    def checked(command, **_kwargs):
        calls["checked"].append(command)
        if "--out-file" in command:
            Path(command[command.index("--out-file") + 1]).write_text("tx")
        if command[1:3] == ["address", "build"]:
            return SimpleNamespace(stdout="addr_test1fake\n")
        return SimpleNamespace(stdout="")

    def query_utxo(**_kwargs):
        return {
            "genesis#0": {"value": {"lovelace": 10**12}},
            "lock0#1": {"value": {"lovelace": 5_000_000}},
            "lock1#1": {"value": {"lovelace": 5_000_000}},
        }

    outcomes = iter(wait_outcomes or [])

    def wait_for_utxo(**kwargs):
        calls["waits"].append(kwargs)
        try:
            outcome = next(outcomes)
        except StopIteration:
            outcome = ({kwargs["transaction_id"] + "#0": {"value": {"lovelace": 1}}}, 1.0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def wait_absent(**kwargs):
        calls["absent_waits"].append(kwargs)
        return 1.0

    def run(command, **_kwargs):
        # Submits go through _run so the wrapper's submit clock line is captured.
        calls["checked"].append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(calibration, "_checked", checked)
    monkeypatch.setattr(calibration, "_run", run)
    monkeypatch.setattr(calibration, "_query_utxo", query_utxo)
    monkeypatch.setattr(calibration, "_wait_for_utxo", wait_for_utxo)
    monkeypatch.setattr(calibration, "_wait_for_utxo_key_absent", wait_absent)
    monkeypatch.setattr(
        calibration, "_transaction_id",
        lambda _cli, path: path.name.split(".")[0].replace("-", ""),
    )
    return calls


def _plutus_runtime(tmp_path):
    key_root = tmp_path / "keys"
    key_root.mkdir()
    (key_root / "utxo.vkey").write_text("{}")
    (key_root / "utxo.skey").write_text("{}")
    runtime = {"network_magic": 42, "workload_socket_remote": True,
               "workload_key_root": str(key_root), "support_binaries": {"cardano-cli": "fake-cli"}}
    node = {"socket_path": "/state/node.socket"}
    return runtime, node


def test_select_plutus_language_prefers_v2_then_v3(monkeypatch):
    from types import SimpleNamespace

    for models, expected in (
        ({"PlutusV1": [], "PlutusV2": [], "PlutusV3": []}, "v2"),
        ({"PlutusV1": [], "PlutusV3": []}, "v3"),
    ):
        monkeypatch.setattr(
            calibration, "_checked",
            lambda command, **_k: SimpleNamespace(stdout=json.dumps({"costModels": models})),
        )
        assert calibration.select_plutus_language(
            cli="cardano-cli", socket_path="/s", network_magic=42
        ) == expected
    monkeypatch.setattr(
        calibration, "_checked",
        lambda command, **_k: SimpleNamespace(stdout=json.dumps({"costModels": {"PlutusV1": []}})),
    )
    with pytest.raises(RuntimeError, match="cost model"):
        calibration.select_plutus_language(cli="cardano-cli", socket_path="/s", network_magic=42)


def test_plutus_workload_uses_v3_control_scripts_when_selected(monkeypatch, tmp_path):
    import hashlib

    _fake_plutus_cli(monkeypatch)
    runtime, node = _plutus_runtime(tmp_path)

    result = calibration.run_plutus_workload(
        runtime_root=tmp_path, runtime=runtime, node=node, output_dir=tmp_path / "out",
        transaction_count=2, script_language="v3",
    )

    work = tmp_path / "out" / "plutus-workload"
    assert (work / "always-succeeds-v3.plutus").is_file()
    assert (work / "always-fails-v3.plutus").is_file()
    assert result["script_language"] == "v3"
    succeeds = ROOT / "dwarf/corpora/redeemer-container/live-controls/always-succeeds-v3.plutus"
    fails = ROOT / "dwarf/corpora/cardano-measurement/always-fails-v3.plutus"
    assert result["scripts"]["accepted"]["sha256"] == "sha256:" + hashlib.sha256(succeeds.read_bytes()).hexdigest()
    assert result["scripts"]["rejected"]["sha256"] == "sha256:" + hashlib.sha256(fails.read_bytes()).hexdigest()
    assert json.loads(fails.read_text())["type"] == "PlutusScriptV3"
    spends = [row for row in result["records"] if not row["attempt_id"].endswith("-double-spend")]
    assert [row["outcome"] for row in spends] == ["accepted", "rejected"]


def test_plutus_workload_falls_back_to_producer_after_inclusion_timeout(monkeypatch, tmp_path):
    calls = _fake_plutus_cli(monkeypatch, wait_outcomes=[RuntimeError("transaction lock0 did not appear")])
    runtime, node = _plutus_runtime(tmp_path)
    forwarded = []

    result = calibration.run_plutus_workload(
        runtime_root=tmp_path, runtime=runtime, node=node, output_dir=tmp_path / "out",
        transaction_count=1, fallback_submit=forwarded.append, inclusion_fallback_seconds=0.1,
    )

    # the lock missed the window; the spend then goes straight to the producer
    assert [path.name for path in forwarded] == ["lock-0.tx", "spend-0.tx"]
    assert calls["waits"][0]["timeout_seconds"] == 0.1
    assert "timeout_seconds" not in calls["waits"][1]
    assert result["records"][0]["relay_forwarded"] is False
    assert result["records"][0]["fallback_errors"] == []


def test_plutus_workload_without_fallback_keeps_single_wait_and_null_relay_flag(monkeypatch, tmp_path):
    calls = _fake_plutus_cli(monkeypatch)
    runtime, node = _plutus_runtime(tmp_path)

    result = calibration.run_plutus_workload(
        runtime_root=tmp_path, runtime=runtime, node=node, output_dir=tmp_path / "out",
        transaction_count=1,
    )

    assert len(calls["waits"]) == 2
    assert all("timeout_seconds" not in wait for wait in calls["waits"])
    assert result["records"][0]["relay_forwarded"] is None
    assert result["script_language"] == "v2"


def test_cardano_workload_scenario_extends_e2e_with_transactions_epoch_restart_and_sync():
    base = json.loads(
        (ROOT / "dwarf/scenarios/cardano-measurement-e2e-stock-portable.yaml").read_text()
    )
    path = ROOT / "dwarf/scenarios/cardano-measurement-workload-stock-portable.yaml"
    doc = json.loads(path.read_text(encoding="utf-8"))
    parsed = scenario_from_body((json.dumps(doc) + "\n").encode())

    assert parsed.id == "cardano-measurement-workload-stock-portable"
    assert (parsed.profile, parsed.measurement_profile) == (base["profile"], base["measurement_profile"])
    assert parsed.target == base["target"]
    assert [m.id for m in parsed.measurements] == ["cardano-stock-header-validation"]
    load = doc["load"]
    assert [l["primitive"] for l in load] == [
        "runtime_cardano_measurement_calibration",
        "runtime_real_target_restart_and_readiness",
        "runtime_controlled_sync_range",
    ]
    assert load[0] == {
        **base["load"][0],
        "plutus_transactions": 20,
        "epoch_observation_seconds": 60,
        "timeout_seconds": 1500,
        "submission_entry": "peer",
        "plutus_split": 0.5,
    }
    assert load[1] == {
        "primitive": "runtime_real_target_restart_and_readiness",
        "profile_id": base["profile"], "timeout_seconds": 300, "poll_interval_seconds": 1,
    }
    assert load[2]["minimum_blocks"] == 20
    assert load[2]["peer_policy"] == "three-node-controlled-local-mesh"
    assert [a["primitive"] for a in doc["assertions"]] == [
        "load_events_are_ok", "restart_readiness_complete", "controlled_sync_range_complete",
        "target_progress_continues", "no_target_fatal_signal",
    ]
    assert doc["assertions"][0] == {"primitive": "load_events_are_ok", "min_completed": 3, "min_event_count": 3}
    assert [p["primitive"] for p in doc["probes"]] == [
        "runtime_target_health_and_progress", "runtime_peer_session_health",
    ]
    assert "workload" in doc["tags"]
    blockers = " ".join(doc["promotion_blockers"])
    for tap in ("cardano-patched-header-validation", "cardano-patched-blockfetch-handler-queue",
                "cardano-patched-txsubmission-residence"):
        assert tap in blockers
    text = path.read_text(encoding="utf-8")
    assert "/home/" not in text and "/Users/" not in text


def test_cardano_calibration_primitive_merges_plutus_accounting_stages(monkeypatch, tmp_path):
    import subprocess

    from profile_manager.primitives import RuntimeCardanoMeasurementCalibration

    class Handle:
        run_dir = tmp_path
        events = []

        def log(self, **event):
            self.events.append(event)

    def run(command, **_kwargs):
        output_dir = Path(command[command.index("--output-dir") + 1])
        output_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "attempt_id": "plutus-0000", "outcome": "accepted",
            "spend_transaction_id": "ab" * 32, "transaction_bytes": 400,
            "elapsed_nanos": 2_000_000, "elapsed_micros": 2000.0,
            "submit_to_block_inclusion_nanos": 2_000_000, "submit_to_block_inclusion_micros": 2000.0,
            "submit_to_protocol_response_nanos": 50_000, "submit_to_protocol_response_micros": 50.0,
            "submit_to_chain_adoption_nanos": 2_000_000, "submit_to_chain_adoption_micros": 2000.0,
        }
        (output_dir / "result.json").write_text(json.dumps({"plutus_workload": {"records": [record]}}))
        (output_dir / "attempts.ndjson").write_text(json.dumps({
            "attempt_id": "handshake-0000", "outcome": "rejected", "elapsed_micros": 5, "request_length": 12,
        }) + "\n")
        return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    handle = Handle()
    RuntimeCardanoMeasurementCalibration(params={
        "runtime_root": "/runtime", "attempts": 30, "plutus_transactions": 1,
    }).run(handle, None)

    accounting = next(event for event in handle.events if event["event"] == "workload_accounting" and event["payload"]["attempts"])
    payload = accounting["payload"]
    assert payload["attempted"] == 2 and payload["successful"] == 1 and payload["rejected"] == 1
    assert payload["bytes"] == 412
    plutus = payload["attempts"][1]
    assert plutus["input_id"] == "plutus-0000"
    assert plutus["submit_to_block_inclusion_micros"] == 2000.0
    assert plutus["submit_to_protocol_response_micros"] == 50.0
    assert plutus["submit_to_chain_adoption_micros"] == 2000.0


def test_verify_workload_container_rejects_operator_local_image_id_mismatch(monkeypatch):
    image_id = "sha256:" + "d" * 64
    inspected = _support_inspect(image_id, "sha256:" + "e" * 64, {
        "ada2.managed": "dwarf", "io.dwarf.managed": "true", "io.dwarf.component": "node",
        "io.dwarf.implementation": "cardano-node", "io.dwarf.topology": "p",
        "io.dwarf.node_id": "node1", "com.docker.compose.project": "proj",
        "com.docker.compose.service": "node1",
    })
    monkeypatch.setattr(calibration, "_inspect_container", lambda _id: inspected)
    node = {"id": "node1", "service_name": "node1", "container_name": "consumer",
            "container_id": "c" * 64, "image_ref": image_id, "image_id": image_id}
    with pytest.raises(RuntimeError, match="image identity does not match"):
        calibration._verify_workload_container({"profile_id": "p", "compose_project": "proj"}, node)


def test_plutus_workload_tolerates_a_failing_fallback_submit_and_records_it(monkeypatch, tmp_path):
    calls = _fake_plutus_cli(monkeypatch, wait_outcomes=[RuntimeError("transaction lock0 did not appear")])
    runtime, node = _plutus_runtime(tmp_path)

    def failing_submit(_path):
        raise RuntimeError("Transaction already in mempool")

    result = calibration.run_plutus_workload(
        runtime_root=tmp_path, runtime=runtime, node=node, output_dir=tmp_path / "out",
        transaction_count=1, fallback_submit=failing_submit, inclusion_fallback_seconds=0.1,
    )

    record = result["records"][0]
    assert record["relay_forwarded"] is False
    # lock: window missed, producer submit failed; spend: sticky producer submit failed too
    assert record["fallback_errors"] == ["Transaction already in mempool"] * 2
    assert len(calls["waits"]) == 3


def test_plutus_workload_fallback_becomes_sticky_after_first_miss(monkeypatch, tmp_path):
    calls = _fake_plutus_cli(monkeypatch, wait_outcomes=[RuntimeError("transaction lock0 did not appear")])
    runtime, node = _plutus_runtime(tmp_path)
    forwarded = []

    result = calibration.run_plutus_workload(
        runtime_root=tmp_path, runtime=runtime, node=node, output_dir=tmp_path / "out",
        transaction_count=2, fallback_submit=forwarded.append, inclusion_fallback_seconds=0.1,
    )

    # tx 0 lock missed the window -> fallback; from then on every submit goes
    # to the producer immediately and waits with the default budget only.
    assert [path.name for path in forwarded] == ["lock-0.tx", "spend-0.tx", "lock-1.tx", "spend-1.tx"]
    assert [row["relay_forwarded"] for row in result["records"] if not row["attempt_id"].endswith("-double-spend")] == [False, False]
    assert all("timeout_seconds" not in wait for wait in calls["waits"][1:])
    assert result["fallback_sticky_from"] == 0


def test_parse_submit_started_at_reads_the_wrapper_line():
    assert calibration.parse_submit_started_at("warning: x\nsubmit_started_at_ns=1762200000123456789\n") == 1762200000123456789
    assert calibration.parse_submit_started_at("") is None


def _fake_double_spend_run(monkeypatch, *, accept=False):
    from types import SimpleNamespace
    submitted = set()
    runs = []

    def run(command, **_kwargs):
        runs.append(command)
        if command[1:4] == ["conway", "transaction", "submit"]:
            tx = command[command.index("--tx-file") + 1]
            if tx in submitted and not accept:
                return SimpleNamespace(returncode=1, stdout="", stderr="Command failed: transaction submit  Error: TxValidationErrorInCardanoEra (BadInputsUTxO ...)")
            submitted.add(tx)
            return SimpleNamespace(returncode=0, stdout="", stderr="submit_started_at_ns=1762200000123456789\n")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(calibration, "_run", run)
    return runs


def test_plutus_workload_records_a_rejected_double_spend_per_batch(monkeypatch, tmp_path):
    _fake_plutus_cli(monkeypatch)
    runs = _fake_double_spend_run(monkeypatch)
    runtime, node = _plutus_runtime(tmp_path)
    result = calibration.run_plutus_workload(
        runtime_root=tmp_path, runtime=runtime, node=node, output_dir=tmp_path / "out",
        transaction_count=2, double_spend_every=2,
    )
    rejected = [r for r in result["records"] if r["attempt_id"].endswith("-double-spend")]
    assert len(rejected) == 1 and rejected[0]["outcome"] == "rejected" and rejected[0]["chain_outcome"] == "rejected-phase1"
    assert "BadInputsUTxO" in rejected[0]["rejected_reason"]
    assert rejected[0]["spend_transaction_id"] == "spend0"
    assert "submit_to_block_inclusion_nanos" not in rejected[0]
    spends = [r for r in result["records"] if not r["attempt_id"].endswith("-double-spend")]
    assert len(spends) == 2 and all(r["submit_started_at_ns"] == 1762200000123456789 for r in spends)
    assert [r["outcome"] for r in result["records"]] == ["accepted", "rejected", "rejected"]


def test_double_spend_that_is_accepted_is_flagged_not_counted(monkeypatch, tmp_path):
    _fake_plutus_cli(monkeypatch)
    _fake_double_spend_run(monkeypatch, accept=True)
    runtime, node = _plutus_runtime(tmp_path)
    result = calibration.run_plutus_workload(
        runtime_root=tmp_path, runtime=runtime, node=node, output_dir=tmp_path / "out",
        transaction_count=2, double_spend_every=2,
    )
    assert [r["outcome"] for r in result["records"] if r["attempt_id"].endswith("-double-spend")] == ["accepted-unexpected"]


def test_cardano_leg_splits_plutus_batch_between_target_and_peer(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    runtime_body = _runtime_body()
    if len(runtime_body["nodes"]) < 2:
        peer = dict(runtime_body["nodes"][0])
        peer.update({"id": "node2", "name": "node2", "container_name": "node2-container",
                     "container_socket_path": "/env/socket/node2/sock"})
        runtime_body["nodes"].append(peer)
    runtime_root.joinpath("runtime.json").write_text(json.dumps(runtime_body))
    calls = []
    monkeypatch.setattr(calibration, "_controller_container", lambda: None)
    monkeypatch.setattr(calibration, "_container_ip", lambda _container: "127.0.0.1")
    monkeypatch.setattr(calibration, "run_attempts", lambda **_kwargs: [{"attempt_id": "handshake-0000", "outcome": "rejected", "elapsed_micros": 1}])

    def run_plutus_workload(**kwargs):
        node_id = kwargs["node"].get("id") or kwargs["node"].get("name")
        calls.append((node_id, kwargs["transaction_count"]))
        return {"records": [{"attempt_id": f"plutus-{node_id}-0", "outcome": "accepted"}], "scripts": {}}

    def capture(_container, *, since, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text('{"ns":"Net.Handshake.Remote","data":{"kind":"Handshake"}}\n')
        return {"record_count": 1, "protocol_record_count": 1, "byte_count": 1, "namespaces": ["Net.Handshake.Remote"]}

    monkeypatch.setattr(calibration, "run_plutus_workload", run_plutus_workload)
    monkeypatch.setattr(calibration, "_capture_logs", capture)
    monkeypatch.setattr(calibration.time, "sleep", lambda _seconds: None)
    result = calibration.run_leg(
        runtime_root=runtime_root, output_dir=tmp_path / "output", attempt_count=1, timeout_seconds=1,
        observation_seconds=0, trace_timeout_seconds=0, plutus_transactions=4,
        submission_entry="peer", plutus_split=0.5,
    )
    assert calls == [("node1", 2), ("node2", 2)]
    records = result["plutus_workload"]["records"]
    assert [r["submission_entry"] for r in records] == ["target", "peer"]
    assert [r["boundary_node"] for r in records] == ["node1", "node2"]
    assert [r["attempt_id"] for r in records] == ["plutus-t-node1-0", "plutus-p-node2-0"]
    assert result["plutus_workload"]["submission_entry"] == "peer"


def test_cardano_calibration_primitive_passes_submission_entry_flags():
    from profile_manager.primitives import _cardano_workload_flag_arguments
    arguments, logged = _cardano_workload_flag_arguments({"submission_entry": "peer", "plutus_split": 0.25})
    assert arguments == ["--submission-entry", "peer", "--plutus-split", "0.25"]
    assert logged == {"submission_entry": "peer", "plutus_split": 0.25}
    assert _cardano_workload_flag_arguments({}) == ([], {})


def test_cardano_workload_scenario_submits_through_the_peer():
    scenario = json.loads((ROOT / "dwarf/scenarios/cardano-measurement-workload-stock-portable.yaml").read_text())
    calibration_step = next(step for step in scenario["load"] if step["primitive"] == "runtime_cardano_measurement_calibration")
    assert calibration_step["submission_entry"] == "peer"
