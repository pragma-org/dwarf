import json
import subprocess
from pathlib import Path

import jsonschema
import pytest

from profile_manager import primitives
from profile_manager.measurement_targets import measurement_target_record_path
from profile_manager.run_plan import RunPlanRequest, resolve_run_plan
from scripts import runtime_txsubmission_probe


class _Handle:
    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True)
        self.logs = []

    def log(self, **entry):
        self.logs.append(entry)


def _primitive_schema():
    return json.loads(
        Path("dwarf/primitives/load/runtime_txsubmission_probe.schema.json").read_text()
    )


def _write_qualified_amaru_target(tmp_path, monkeypatch):
    revision = "aedfe797a5b8ef00d8b362be40b47a52c3b4a379"
    patch = "47787f152bc7bdcd645d5d32125f6658847812c447fd375ede44d31b546620f6"
    manifest = "sha256:" + "f" * 64
    config = "sha256:" + "4" * 64
    record = {
        "schema_version": 1,
        "implementation": "amaru",
        "mode": "patched",
        "version": "10.11.20260918",
        "source_revision": revision,
        "patch_set_sha256": patch,
        "image_reference": "dwarf/qualified-amaru@" + manifest,
        "runtime_image_reference": config,
        "image_digest": config,
        "image_manifest_digest": manifest,
        "image_config_digest": config,
        "qualification_image_id": manifest,
        "executable_digest": "sha256:" + "e" * 64,
        "build_result_sha256": "sha256:" + "b" * 64,
        "runtime_probe_image": "dwarf/probe@sha256:" + "c" * 64,
        "runtime_probe_log_sha256": "sha256:" + "d" * 64,
        "transfer_source_installation_id": "legacy-qualified-source",
        "transfer_manifest_sha256": "sha256:" + "2" * 64,
    }
    path = measurement_target_record_path(
        "amaru", revision, patch, registry_root=tmp_path / "registry"
    )
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    monkeypatch.setenv(
        "ADA2_DWARF_MEASUREMENT_TARGET_REGISTRY", str(tmp_path / "registry")
    )


def test_profile_backed_txsubmission_uses_the_verified_prepared_endpoint(
    monkeypatch, tmp_path
):
    observed = {}
    monkeypatch.setattr(
        primitives,
        "_resolve_client_runtime_target",
        lambda _handle, params: {
            "id": "amaru-relay-1",
            "implementation": "amaru",
            "listen_host": "172.24.0.10",
            "listen_port": 3000,
            "runtime_metadata_path": "/prepared/runtime.json",
        },
    )

    def run(command, **_kwargs):
        config_path = Path(command[command.index("--config") + 1])
        observed.update(json.loads(config_path.read_text()))
        output_dir = Path(observed["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        output_dir.joinpath("result.json").write_text(
            json.dumps({"result": {"txids_processed": 2}})
        )
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(primitives.subprocess, "run", run)
    primitive = primitives.RuntimeTxsubmissionWindowPressure(
        params={
            "profile_id": "profile-z-amaru-20260918-nanoseconds-v3",
            "output_dir": "outputs/txsubmission-window",
        }
    )

    primitive.run(_Handle(tmp_path / "run"), None)

    assert observed["target_node"] == "amaru-relay-1"
    assert observed["target_host"] == "172.24.0.10"
    assert observed["target_port"] == 3000
    assert observed["endpoint_source"] == "prepared-runtime"
    assert "runtime_metadata_path" not in observed


def test_profile_backed_txsubmission_rejects_a_different_requested_node(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        primitives,
        "_resolve_client_runtime_target",
        lambda _handle, params: {
            "id": "amaru-relay-1",
            "listen_host": "172.24.0.10",
            "listen_port": 3000,
            "runtime_metadata_path": "/prepared/runtime.json",
        },
    )
    primitive = primitives.RuntimeTxsubmissionWindowPressure(
        params={
            "profile_id": "profile-z-amaru-20260918-nanoseconds-v3",
            "target_node": "amaru-relay-2",
            "output_dir": "outputs/txsubmission-window",
        }
    )

    try:
        primitive.run(_Handle(tmp_path / "run"), None)
    except RuntimeError as exc:
        assert str(exc) == "prepared runtime target node does not match the requested node"
    else:
        raise AssertionError("mismatched prepared-runtime target was accepted")


def test_profile_and_substrate_endpoint_modes_are_mutually_exclusive(tmp_path):
    mixed = {
        "profile_id": "profile-z-amaru-20260918-nanoseconds-v3",
        "runtime_metadata_path": "outputs/substrate/runtime.json",
        "target_node": "node-b",
        "output_dir": "outputs/txsubmission-window",
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(_primitive_schema()).validate(mixed)

    primitive = primitives.RuntimeTxsubmissionWindowPressure(params=mixed)
    with pytest.raises(ValueError, match="exactly one endpoint mode"):
        primitive.run(_Handle(tmp_path / "run"), None)


def test_profile_endpoint_mode_rejects_a_direct_host_override(tmp_path):
    params = {
        "profile_id": "profile-z-amaru-20260918-nanoseconds-v3",
        "target_host": "203.0.113.7",
        "output_dir": "outputs/txsubmission-window",
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(_primitive_schema()).validate(params)

    primitive = primitives.RuntimeTxsubmissionWindowPressure(params=params)
    with pytest.raises(ValueError, match="does not accept target_host"):
        primitive.run(_Handle(tmp_path / "run"), None)


def test_substrate_backed_txsubmission_retains_the_existing_metadata_contract(
    monkeypatch, tmp_path
):
    observed = {}

    def run(command, **_kwargs):
        config_path = Path(command[command.index("--config") + 1])
        observed.update(json.loads(config_path.read_text()))
        output_dir = Path(observed["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        output_dir.joinpath("result.json").write_text(json.dumps({"result": {}}))
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(primitives.subprocess, "run", run)
    primitive = primitives.RuntimeTxsubmissionWindowPressure(
        params={
            "runtime_metadata_path": "outputs/substrate/runtime.json",
            "target_node": "node-b",
            "output_dir": "outputs/txsubmission-window",
        }
    )

    primitive.run(_Handle(tmp_path / "run"), None)

    assert observed["target_node"] == "node-b"
    assert observed["target_host"] == "127.0.0.1"
    assert observed["runtime_metadata_path"].endswith(
        "/outputs/substrate/runtime.json"
    )
    assert observed["endpoint_source"] == "substrate-runtime"
    assert "target_port" not in observed


def test_txsubmission_probe_accepts_a_verified_direct_endpoint(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "target_node": "amaru-relay-1",
                "target_host": "172.24.0.10",
                "target_port": 3000,
                "endpoint_source": "prepared-runtime",
                "output_dir": str(tmp_path / "output"),
            }
        )
    )
    observed = {}

    def run_probe(**kwargs):
        observed.update(kwargs)
        return {
            "mode": kwargs["mode"],
            "target_node": kwargs["target_node"],
            "handshake_response_kind": "data",
            "txsubmission_response_kind": "reset",
        }

    monkeypatch.setattr(runtime_txsubmission_probe, "run_probe", run_probe)

    assert (
        runtime_txsubmission_probe.main(
            ["--config", str(config_path), "--mode", "txsubmission_window_pressure"]
        )
        == 0
    )
    assert observed["runtime_metadata_path"] is None
    assert observed["target_port"] == 3000
    assert observed["target_host"] == "172.24.0.10"


def test_txsubmission_probe_rejects_an_invalid_verified_direct_endpoint(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "target_node": "amaru-relay-1",
                "target_host": "172.24.0.10",
                "target_port": 0,
                "endpoint_source": "prepared-runtime",
                "output_dir": str(tmp_path / "output"),
            }
        )
    )

    try:
        runtime_txsubmission_probe.main(
            ["--config", str(config_path), "--mode", "txsubmission_window_pressure"]
        )
    except RuntimeError as exc:
        assert str(exc) == "verified target port is invalid"
    else:
        raise AssertionError("invalid prepared-runtime endpoint was accepted")


def test_txsubmission_probe_result_proves_exact_rejected_overflow(tmp_path):
    result = runtime_txsubmission_probe.build_result_body(
        mode="txsubmission_window_pressure",
        handshake_response_kind="data",
        txsubmission_response_kind="reset",
        node_stayed_up=True,
        accounting={
            "txids_processed": 4,
            "txsubmission_messages_observed": 3,
            "txsubmission_message_kinds": ["init", "request-txids", "reply-txids"],
            "decode_failure_count": 0,
        },
        negotiated_window=3,
        overflow_attempt_txids=4,
    )
    handle = _Handle(tmp_path / "run")
    handle.events = [
        {
            "phase": "load",
            "primitive": "runtime_txsubmission_window_pressure",
            "event": "completed",
            "payload": {"outcome": "ok", "result": result},
        }
    ]

    verdict = primitives.TxsubmissionWindowEnforced(
        params={"min_txsubmission_messages_observed": 3}
    ).evaluate(handle)

    assert verdict["result"] == "pass"
    assert verdict["evaluated_value"]["overflow_attempt_txids"] == 4
    assert verdict["evaluated_value"]["client_reply_txids_observed"] == 4
    assert "txids_processed" not in result
    assert "max_in_flight_txids" not in result

    result["overflow_attempt_txids"] = 3
    assert (
        primitives.TxsubmissionWindowEnforced(params={}).evaluate(handle)["result"]
        == "fail"
    )


def test_portable_amaru_txsubmission_scenario_resolves_the_exact_qualified_target(
    tmp_path, monkeypatch
):
    _write_qualified_amaru_target(tmp_path, monkeypatch)
    plan = resolve_run_plan(
        RunPlanRequest.from_mapping(
            {
                "scenario_id": "client-example-txsubmission-window-amaru-portable-20260918",
                "profile_id": "profile-z-amaru-20260918-nanoseconds-v3",
            }
        )
    )

    assert plan["scenario"]["target"] == {
        "implementation": "amaru",
        "version": "10.11.20260918",
    }
    assert plan["measurements"]["target_identity"]["mode"] == "patched"
    assert plan["measurements"]["target_identity"]["source_revision"] == (
        "aedfe797a5b8ef00d8b362be40b47a52c3b4a379"
    )
    assert "runtime_txsubmission_window_pressure" in plan["primitive_names"]
    assert not plan["measurements"]["incompatible"]
