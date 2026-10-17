import json
import subprocess
from pathlib import Path

import pytest

from profile_manager import primitives, scenario
from profile_manager.launch_store import materialized_scenario
from profile_manager.run_plan import RunPlanRequest, resolve_run_plan
from scripts import runtime_cardano_measurement_calibration as calibration
from scripts import runtime_compose_substrate as compose


ROOT = Path(__file__).resolve().parents[1]
CASE_PATH = (
    ROOT
    / "dwarf/scenarios/ledger-submit-empty-redeemers-container-differential-amaru-cardano-node.yaml"
)
GOVERNANCE_CASE_PATH = (
    ROOT
    / "dwarf/scenarios/governance-zero-denominator-admission-differential-amaru-cardano-node.yaml"
)


class _Handle:
    def __init__(self, run_dir):
        self.run_dir = run_dir
        self.run_id = "20261009T040000Z-proof"
        self.events = []

    def log(self, **event):
        self.events.append(event)


def test_redeemer_case_uses_one_way_bootstrap_and_observed_sync_budget():
    parsed = scenario.load_scenario(CASE_PATH)

    assert parsed.substrate["topology"]["edges"] == [{"from": "node1", "to": "node2"}]
    compose_ref = next(
        ref for ref in parsed.setup if ref.primitive == "runtime_compose_substrate"
    )
    assert compose_ref.params["healthy_timeout_seconds"] == 360
    assert compose_ref.params["timeout_seconds"] == 600


def test_controller_scenario_runtime_uses_matching_host_visible_root(monkeypatch, tmp_path):
    controller_state = tmp_path / "controller-state"
    host_state = "/srv/dwarf/controller-state"
    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(controller_state))
    monkeypatch.setenv("ADA2_DWARF_BUNDLES_DIR", str(tmp_path / "bundles"))
    monkeypatch.setenv("DWARF_CONTROLLER_HOST_STATE_DIR", host_state)
    def successful_compose(command, **_kwargs):
        config = json.loads(Path(command[-1]).read_text())
        output_dir = Path(config["output_dir"])
        (output_dir / "compose-report.json").write_text(
            json.dumps({"nodes": [], "runtime_root": config["runtime_root"]})
        )
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(primitives.subprocess, "run", successful_compose)

    substrate = {
        "compose_mode": "docker",
        "docker_host_runtime_root": None,
        "nodes": [{"id": "node1"}],
    }
    primitive = primitives.RuntimeComposeSubstrate(
        params={"output_dir": "outputs/substrate-compose"}
    )
    primitive.bound_substrate = substrate
    primitive.bound_state = {}
    handle = _Handle(tmp_path / "run")

    primitive.run(handle, None)

    config = json.loads(
        (handle.run_dir / "outputs/substrate-compose/compose-substrate-config.json").read_text()
    )
    project = "dwarf-substrate-20261009T040000Z-proof"
    relative_runtime = f"controller/scenario-runtimes/{project}"
    assert config["runtime_root"] == str(controller_state / relative_runtime)
    assert config["substrate"]["docker_host_runtime_root"] == f"{host_state}/{relative_runtime}"
    assert substrate["docker_host_runtime_root"] is None


def test_loaded_scenario_serializes_exact_node_artifact_identity_into_setup_configs(
    monkeypatch, tmp_path
):
    plan = resolve_run_plan(
        RunPlanRequest(
            scenario_id=CASE_PATH.stem,
            version_policy="exact",
            cardano_version="11.1.2",
            amaru_version="10.11.20260925",
            acknowledge_unknown_versions=True,
            measurement_profile="none",
        )
    )
    frozen_path = tmp_path / "scenario.yaml"
    frozen_path.write_bytes(materialized_scenario(plan))
    parsed = scenario.load_scenario(frozen_path)
    expected_nodes = {
        node["id"]: {
            "image": node["image"],
            "source_revision": node["source_revision"],
            "version": node["version"],
        }
        for node in json.loads(frozen_path.read_text())["substrate"]["nodes"]
    }
    handle = _Handle(tmp_path / "run")
    shared_state = {}

    def successful_runtime(command, **_kwargs):
        config_path = Path(command[-1])
        config = json.loads(config_path.read_text())
        serialized = {
            node["id"]: {
                "image": node["image"],
                "source_revision": node["source_revision"],
                "version": node["version"],
            }
            for node in config["substrate"]["nodes"]
        }
        assert serialized == expected_nodes
        output_dir = Path(config["output_dir"])
        if "runtime_install_version.py" in str(command[1]):
            (output_dir / "install-report.json").write_text(
                json.dumps({"nodes": [{"id": node_id} for node_id in serialized]})
            )
        elif "runtime_compose_substrate.py" in str(command[1]):
            runtime_path = output_dir / "runtime.json"
            runtime_path.write_text(json.dumps({"nodes": list(serialized)}))
            (output_dir / "compose-report.json").write_text(
                json.dumps(
                    {
                        "nodes": [
                            {"id": node_id, "healthy": True}
                            for node_id in serialized
                        ],
                        "runtime_metadata_path": str(runtime_path),
                    }
                )
            )
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(primitives.subprocess, "run", successful_runtime)
    for ref in parsed.setup:
        primitive_type = {
            "runtime_install_version": primitives.RuntimeInstallVersion,
            "runtime_compose_substrate": primitives.RuntimeComposeSubstrate,
        }[ref.primitive]
        primitive = primitive_type(params=ref.params)
        primitive.bound_substrate = parsed.substrate
        primitive.bound_state = shared_state
        primitive.run(handle, None)


def test_loaded_governance_fixture_reaches_runtime_compose_config(monkeypatch, tmp_path):
    parsed = scenario.load_scenario(GOVERNANCE_CASE_PATH)
    compose_ref = next(
        ref for ref in parsed.setup if ref.primitive == "runtime_compose_substrate"
    )
    expected = {
        "name": "always-succeeds-v3",
        "expected_active_script_hash": (
            "fa24fb305126805cf2164c161d852a0e7330cf988f1fe558cf7d4a64"
        ),
    }

    def successful_compose(command, **_kwargs):
        config = json.loads(Path(command[-1]).read_text())
        assert config["substrate"]["governance_guardrails_fixture"] == expected
        output_dir = Path(config["output_dir"])
        runtime_path = output_dir / "runtime.json"
        runtime_path.write_text(json.dumps({"nodes": []}), encoding="utf-8")
        (output_dir / "compose-report.json").write_text(
            json.dumps({"nodes": [], "runtime_metadata_path": str(runtime_path)}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(primitives.subprocess, "run", successful_compose)
    primitive = primitives.RuntimeComposeSubstrate(params=compose_ref.params)
    primitive.bound_substrate = parsed.substrate
    primitive.bound_state = {}
    primitive.run(_Handle(tmp_path / "run"), None)


def test_loaded_scenario_owns_cardano_target_for_shared_workload(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("DWARF_INSTALLATION_ID", "install-4448b9a5541416fb")
    plan = resolve_run_plan(
        RunPlanRequest(
            scenario_id=CASE_PATH.stem,
            version_policy="exact",
            cardano_version="11.1.2",
            amaru_version="10.11.20260925",
            acknowledge_unknown_versions=True,
            measurement_profile="none",
        )
    )
    frozen_path = tmp_path / "scenario.yaml"
    frozen_path.write_bytes(materialized_scenario(plan))
    parsed = scenario.load_scenario(frozen_path)
    handle = _Handle(tmp_path / "run")
    topology_id = "dwarf-substrate-20261009t040000z-proof"

    def successful_compose(command, **_kwargs):
        config = json.loads(Path(command[-1]).read_text())
        substrate = config["substrate"]
        assert substrate["profile_id"] == topology_id
        assert substrate["controller_ownership"] == {
            "installation_id": "install-4448b9a5541416fb",
            "topology_id": topology_id,
        }
        selected = next(
            node for node in substrate["nodes"] if node["impl"] == "cardano-node"
        )
        compose_node = {
            **selected,
            "listen_address": "127.0.0.1:33001",
            "host_slot_index": 1,
        }
        service = compose._docker_compose_body(
            compose_project=topology_id,
            nodes=[compose_node],
            network_name="testnet_42",
            network_magic=42,
            controller_ownership=substrate["controller_ownership"],
        )["services"][selected["id"]]
        container_id = "sha256:" + "9" * 64
        labels = {
            **service["labels"],
            "com.docker.compose.project": topology_id,
            "com.docker.compose.service": selected["id"],
        }
        inspected = {
            "Id": container_id,
            "Name": f"/{topology_id}-{selected['id']}-1",
            "Config": {"Image": selected["image"], "Labels": labels},
            "Image": container_id,
            "State": {"Running": True},
        }
        monkeypatch.setattr(
            calibration,
            "_inspect_container",
            lambda expected: inspected if expected == container_id else None,
        )
        verified = calibration._verify_workload_container(
            {"profile_id": topology_id, "compose_project": topology_id},
            {
                "id": selected["id"],
                "container_id": container_id,
                "container_name": f"{topology_id}-{selected['id']}-1",
                "image_ref": selected["image"],
                "image_id": container_id,
            },
        )
        assert verified["Id"] == container_id
        output_dir = Path(config["output_dir"])
        (output_dir / "compose-report.json").write_text(
            json.dumps({"nodes": [], "runtime_root": config["runtime_root"]})
        )
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(primitives.subprocess, "run", successful_compose)
    compose_ref = next(
        ref for ref in parsed.setup if ref.primitive == "runtime_compose_substrate"
    )
    primitive = primitives.RuntimeComposeSubstrate(params=compose_ref.params)
    primitive.bound_substrate = parsed.substrate
    primitive.bound_state = {}

    primitive.run(handle, None)


def test_failed_compose_raises_original_error_before_runtime_metadata_is_consumed(
    monkeypatch, tmp_path
):
    parsed = scenario.load_scenario(CASE_PATH)
    primitive = primitives.RuntimeComposeSubstrate(
        params={"output_dir": "outputs/substrate-compose"}
    )
    primitive.bound_substrate = parsed.substrate
    primitive.bound_state = {}
    handle = _Handle(tmp_path / "run")
    error = "version artifact resolution failed for node1: image-missing"
    monkeypatch.setattr(
        primitives.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 1, b"", error.encode()
        ),
    )

    with pytest.raises(RuntimeError, match=error):
        primitive.run(handle, None)

    completed = [event for event in handle.events if event["event"] == "completed"]
    assert completed[-1]["payload"]["outcome"] == "unexpected_exit"
    assert completed[-1]["payload"]["artifact_summary"]["has_compose_report"] is False


def test_failed_partial_compose_publishes_runtime_metadata_for_teardown(monkeypatch, tmp_path):
    parsed = scenario.load_scenario(CASE_PATH)
    runtime_root = tmp_path / "runtime"
    primitive = primitives.RuntimeComposeSubstrate(
        params={
            "output_dir": "outputs/substrate-compose",
            "runtime_root": str(runtime_root),
        }
    )
    primitive.bound_substrate = parsed.substrate
    primitive.bound_state = {}
    handle = _Handle(tmp_path / "run")

    def failed_after_runtime_metadata(command, **_kwargs):
        runtime_root.mkdir(parents=True, exist_ok=True)
        (runtime_root / "runtime.json").write_text(
            json.dumps({"compose_mode": "docker", "compose_project": "owned-case"})
        )
        return subprocess.CompletedProcess(command, 1, b"", b"startup gate failed")

    monkeypatch.setattr(primitives.subprocess, "run", failed_after_runtime_metadata)

    with pytest.raises(RuntimeError, match="startup gate failed"):
        primitive.run(handle, None)

    assert primitive.bound_state["substrate_runtime_root"] == str(runtime_root)
    assert primitive.bound_state["substrate_runtime_metadata_path"] == str(
        runtime_root / "runtime.json"
    )
