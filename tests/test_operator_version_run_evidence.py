import hashlib
import json
import os
import copy
import shutil
import pytest
from pathlib import Path
from urllib.parse import urlencode

from profile_manager import (
    dashboard,
    forensic,
    operator_version_decisions as decisions,
    primitives as primitives_module,
    run_plan as run_plan_module,
)
from profile_manager import profiles as profiles_module
from profile_manager.deployment_versions import operator_registration_release
from profile_manager.data.operate_run import operate_run_detail
from profile_manager.launch_store import create_launch, record_launch_preflight, retain_launch_inputs
from profile_manager.run_ledger import RunLedger
from profile_manager.run_plan import RunPlanRequest, _compact_release, resolve_run_plan


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _desired_topology_pair():
    digest = "sha256:" + "4" * 64
    retained = {
        "profile_id": "profile-r",
        "compose_mode": "docker",
        "scope": "amaru-only",
        "target_node_count": 1,
        "support_node_count": 0,
        "deployment_adapter": "amaru-control",
        "network": "testnet_42",
        "network_magic": 42,
        "peer_sharing": False,
        "version_policy": "default",
        "version_policy_source": "catalog",
        "version_status": "confirmed",
        "unknown_acknowledged": False,
        "catalog_revision": "a" * 64,
        "catalog_snapshot": {
            "schema_version": 1,
            "catalog_revision": "a" * 64,
            "catalog_updated_at": "2026-10-11T12:00:00Z",
            "selection_policy": "default",
            "selection_status": "confirmed",
            "selected_pair": None,
            "selected_releases": [{
                "implementation": "amaru",
                "version": "10.11.20260912",
                "source_revision": "b159" + "1" * 36,
                "verification": {"amaru-only": {
                    "status": "confirmed",
                    "default": True,
                    "checked_at": "2026-10-11T12:00:00Z",
                    "evidence": ["state:qualification/exact-0912"],
                    "issues": [],
                }},
                "artifacts": [{
                    "kind": "oci",
                    "availability": "available",
                    "reference": "registry.invalid/amaru:10.11.20260912",
                    "digest": digest,
                    "qualification": {
                        "status": "verified",
                        "evidence_sha256": "c" * 64,
                    },
                }],
            }],
        },
        "measurement_target_mode": "stock",
        "amaru_json_traces": True,
        "resource_limits": {"memory": "4g"},
        "nodes": [{
            "id": "amaru-1",
            "impl": "amaru",
            "version": "10.11.20260912",
            "image": "registry.invalid/amaru:10.11.20260912@" + digest,
            "role": "consumer",
            "supporting": False,
        }],
        "topology": {"edges": []},
    }
    current = copy.deepcopy(retained)
    current["catalog_revision"] = "b" * 64
    current["catalog_snapshot"]["catalog_revision"] = "b" * 64
    current["catalog_snapshot"]["catalog_updated_at"] = "2026-10-11T13:00:00Z"
    current_artifact = current["catalog_snapshot"]["selected_releases"][0][
        "artifacts"
    ][0]
    current_artifact.update(
        public=True, platforms=["linux/amd64", "linux/arm64"]
    )
    return (
        profiles_module.desired_topology_from_substrate("profile-r", retained),
        profiles_module.desired_topology_from_substrate("profile-r", current),
    )


def _operator_registration_topology(*, manifest):
    registration = {
        "registration_id": "local-amaru-evidence",
        "implementation": "amaru",
        "version": "10.12.0-local",
        "image_reference": (
            "local/amaru@sha256:" + "7" * 64 if manifest else "local/amaru:dev"
        ),
        "manifest_digest": "sha256:" + "7" * 64 if manifest else None,
        "config_image_id": "sha256:" + "8" * 64,
        "platform": "linux/amd64",
        "runtime_interface": "extracted-binary",
        "inspected_at": "2026-10-11T18:00:00Z",
        "provenance": {
            "source_revision": None,
            "public": None,
            "qualification": "unverified",
        },
    }
    release = operator_registration_release(registration)
    substrate = {
        "profile_id": "profile-r",
        "compose_mode": "docker",
        "scope": "amaru-only",
        "target_node_count": 1,
        "support_node_count": 0,
        "deployment_adapter": "amaru-control",
        "network": "testnet_42",
        "network_magic": 42,
        "peer_sharing": False,
        "version_policy": "exact",
        "version_policy_source": "operator-registration",
        "version_status": "unknown",
        "unknown_acknowledged": True,
        "catalog_revision": "f" * 64,
        "operator_registration_id": registration["registration_id"],
        "operator_overlay_revision": "sha256:" + "6" * 64,
        "catalog_snapshot": {
            "schema_version": 1,
            "catalog_revision": "f" * 64,
            "catalog_updated_at": "2026-10-11T18:00:00Z",
            "selection_policy": "exact",
            "selection_status": "unknown",
            "selected_pair": None,
            "operator_registration_id": registration["registration_id"],
            "operator_overlay_revision": "sha256:" + "6" * 64,
            "selected_releases": [release],
        },
        "measurement_target_mode": "stock",
        "amaru_json_traces": True,
        "nodes": [{
            "id": "amaru-1",
            "impl": "amaru",
            "version": registration["version"],
            "image": registration["config_image_id"],
            "role": "consumer",
            "supporting": False,
            "operator_registration": registration,
        }],
        "topology": {"edges": []},
    }
    desired = profiles_module.desired_topology_from_substrate(
        "profile-r", substrate
    )
    return (copy.deepcopy(desired), desired), _compact_release(release)


def _retained_run(
    tmp_path: Path,
    *,
    with_runtime_identity: bool = True,
    defer_terminal: bool = False,
    desired_pair=None,
    release=None,
):
    release = release or {
        "implementation": "amaru",
        "version": "10.11.20260912",
        "image_reference": "registry.invalid/amaru:10.11.20260912@sha256:" + "4" * 64,
        "image_digest": "sha256:" + "4" * 64,
        "image_config_id": None,
    }
    version = release["version"]
    scenario = (
        "schema_version: v1\nid: canonical-evidence\ntitle: Canonical evidence\n"
        "runtime: devnet\ntarget:\n  implementation: amaru\n"
        f"  version: {version}\nprimitives: []\n"
    ).encode()
    scenario_sha = hashlib.sha256(scenario).hexdigest()
    executed_scenario = scenario + b"# materialized from the immutable launch snapshot\n"
    executed_scenario_sha = hashlib.sha256(executed_scenario).hexdigest()
    config_id = release.get("image_config_id") or "sha256:" + "b" * 64
    observed_desired, expected_desired = desired_pair or _desired_topology_pair()
    launch_plan = {
        "schema_version": 1,
        "scenario": {
            "id": "canonical-evidence",
            "digest": "sha256:" + scenario_sha,
            "target": {"implementation": "amaru", "version": version},
        },
        "profile": {"id": "profile-r", "digest": "sha256:" + "c" * 64},
        "versions": {
            "scope": "amaru-only",
            "deployment_identity_sha256": expected_desired["deployment_sha256"],
            "topology_plan_sha256": expected_desired["plan_sha256"],
            "expected_desired_topology": expected_desired,
            "pair": None,
            "resolved": {"amaru": release},
        },
        "measurements": run_plan_module.explicit_no_measurements_context(),
        "artifacts": {"scenario.yaml": "sha256:" + executed_scenario_sha},
        "launch_id": "launch-canonical",
    }
    run_service_plan = {
        "schema_version": "dwarf.run-service.invocation.v1",
        "plan_digest": "sha256:" + "e" * 64,
        "resolved": {
            "scenario": {
                "id": "canonical-evidence", "digest": "sha256:" + executed_scenario_sha,
                "profile_id": "profile-r",
                "target": {"implementation": "amaru", "version": version},
            }
        },
        "invocation": {"origin": "dashboard"},
        "storage": {},
    }
    handle = forensic.start_run(
        scenario_id="canonical-evidence", scenario_yaml=executed_scenario,
        target={"implementation": "amaru", "version": version},
        runtime="devnet", profile_id="profile-r", profile_resolved={"id": "profile-r"},
        framework_version="0.1.0", framework_commit="1" * 40, seed=7,
        runs_dir=tmp_path / "runs", state_dir=tmp_path / "state",
        run_service_plan=run_service_plan,
        defer_ledger_terminal=defer_terminal,
    )
    launch = handle.run_dir / "launch"
    launch.mkdir()
    (launch / "plan.json").write_bytes(_canonical(launch_plan))
    (launch / "scenario.yaml").write_bytes(executed_scenario)
    nodes = []
    if with_runtime_identity:
        nodes.append({
            "id": "amaru-1", "implementation": "amaru",
            "version": version, "target_image_id": config_id,
            "artifact_image_id": config_id,
            "service": "amaru-relay-1",
            "container_name": "registered-amaru-relay-1",
            "runtime_image_id": "sha256:" + "d" * 64,
            "runtime_container_image_id": "sha256:" + "d" * 64,
            "wrapper_matched": True,
            "healthy": True,
        })
    preflight = {
        "state": "ready", "kind": "profile", "profile_id": "profile-r",
        "checks": [{"id": "deployed-version-identity", "passed": True}],
        "health": {"desired": observed_desired, "health": {"fresh": True, "state": "healthy", "observations": [
            {"topology_result": {"controller_health": {"nodes": nodes}}}
        ]}},
    }
    (launch / "preflight.json").write_bytes(_canonical(preflight))
    inputs = handle.run_dir / "inputs"
    inputs.mkdir(exist_ok=True)
    (inputs / "run-service-plan.json").write_bytes(_canonical(run_service_plan))
    return handle, run_service_plan, config_id


def _registered_runtime_metadata(
    tmp_path, *, config_id, version="10.12.0-local", profile_id="profile-r"
):
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps({
        "profile_id": profile_id,
        "network_magic": 42,
        "identity": {
            "matched": True,
            "services": {
                "amaru-relay-1": {
                    "container": "registered-amaru-relay-1",
                    "artifact_image_id": config_id,
                    "expected_image_id": config_id,
                    "expected_version": version,
                    "identity_basis": "operator-registration-config-id",
                    "running_image_id": "sha256:" + "d" * 64,
                    "wrapper_expected_image_id": "sha256:" + "d" * 64,
                    "matched": True,
                    "wrapper_matched": True,
                },
                "amaru-consumer": {
                    "container": "registered-amaru-consumer",
                },
            },
        },
    }, sort_keys=True))
    return path


def test_native_registered_none_plan_reaches_restart_with_frozen_target(
    tmp_path, monkeypatch
):
    _desired_pair, release = _operator_registration_topology(manifest=True)
    registration = release["operator_registration"]
    ledger = decisions.OperatorVersionDecisionLedger(tmp_path / "version-state")
    state = ledger.read()
    ledger.append_registration(registration, expected_revision=state["revision"])
    monkeypatch.setenv(
        "ADA2_DWARF_VERSION_CATALOG_STATE_DIR", str(tmp_path / "version-state")
    )
    request = RunPlanRequest(
        scenario_id="amaru-measurement-e2e-stock",
        profile_id="profile-r-amaru-measurement-stock-control",
        operator_registration_id=registration["registration_id"],
        acknowledge_unknown_versions=True,
        measurement_profile="none",
    )
    context = {
        "host_os": "linux",
        "host_arch": "amd64",
        "inspected_images": {
            registration["image_reference"]: {
                "config_image_id": registration["config_image_id"],
                "platform": registration["platform"],
            }
        },
    }
    initial = resolve_run_plan(request, capability_context=context)
    supporting = [
        item for item in initial["availability"]["required_image_digests"]
        if item != registration["config_image_id"]
    ]
    plan = resolve_run_plan(
        request,
        capability_context={**context, "available_image_digests": supporting},
    )
    assert run_plan_module.is_explicit_no_measurements_context(plan["measurements"])
    stored = create_launch(plan, root=tmp_path / "launches")
    desired = plan["versions"]["expected_desired_topology"]
    node = next(item for item in desired["nodes"] if item["implementation"] == "amaru")
    config_id = registration["config_image_id"]
    wrapper_id = "sha256:" + "d" * 64
    observed_nodes = []
    for desired_node in desired["nodes"]:
        implementation = desired_node["implementation"]
        selected_release = (
            plan["versions"]["resolved"].get(implementation)
            or plan["versions"].get("supporting", {}).get(implementation)
            or {}
        )
        selected_config = selected_release.get("image_config_id")
        if implementation == "amaru":
            selected_config = config_id
        observed = {
            "id": desired_node["id"],
            "implementation": implementation,
            "version": desired_node["version"],
            "target_image_id": selected_config or "sha256:" + "c" * 64,
            "artifact_image_id": selected_config or "sha256:" + "c" * 64,
            "healthy": True,
        }
        if desired_node["id"] == node["id"]:
            observed.update({
                "service": "amaru-relay-1",
                "container_name": "registered-amaru-relay-1",
                "runtime_image_id": wrapper_id,
                "runtime_container_image_id": wrapper_id,
                "wrapper_matched": True,
            })
        observed_nodes.append(observed)
    preflight = {
        "state": "ready",
        "kind": "profile",
        "profile_id": plan["profile"]["id"],
        "checks": [{"id": "deployed-version-identity", "passed": True}],
        "health": {"desired": desired, "health": {
            "fresh": True,
            "state": "healthy",
            "observations": [{"topology_result": {"controller_health": {
                "nodes": observed_nodes,
            }}}],
        }},
    }
    record_launch_preflight(stored["launch_id"], preflight, root=tmp_path / "launches")
    materialized = stored["path"].joinpath("scenario.yaml").read_bytes()
    service_plan = {
        "schema_version": "dwarf.run-service.invocation.v1",
        "plan_digest": "sha256:" + "e" * 64,
        "resolved": {"scenario": {
            "id": plan["scenario"]["id"],
            "digest": plan["artifacts"]["scenario.yaml"] if "artifacts" in plan else None,
            "profile_id": plan["profile"]["id"],
            "target": plan["scenario"]["target"],
        }},
        "invocation": {"origin": "dashboard"},
        "storage": {},
    }
    service_plan["resolved"]["scenario"]["digest"] = "sha256:" + hashlib.sha256(materialized).hexdigest()
    handle = forensic.start_run(
        scenario_id=plan["scenario"]["id"], scenario_yaml=materialized,
        target=plan["scenario"]["target"], runtime="devnet",
        profile_id=plan["profile"]["id"], profile_resolved={"id": plan["profile"]["id"]},
        framework_version="0.1.0", framework_commit="1" * 40, seed=7,
        runs_dir=tmp_path / "runs", state_dir=tmp_path / "state",
        run_service_plan=service_plan,
    )
    shutil.rmtree(handle.run_dir / "launch", ignore_errors=True)
    retain_launch_inputs(
        stored["launch_id"], run_dir=handle.run_dir, root=tmp_path / "launches"
    )
    (handle.run_dir / "inputs").mkdir(exist_ok=True)
    (handle.run_dir / "inputs/run-service-plan.json").write_bytes(_canonical(service_plan))
    runtime_path = _registered_runtime_metadata(
        tmp_path, config_id=config_id, version=registration["version"],
        profile_id=plan["profile"]["id"],
    )
    from scripts import runtime_amaru_measurement_calibration as calibration
    monkeypatch.setattr(calibration, "_container_ip", lambda _container: "172.24.0.10")
    tips = iter((
        {"slot": 100, "hash": "a" * 64, "block_height": 10},
        {"slot": 101, "hash": "b" * 64, "block_height": 11},
    ))
    monkeypatch.setattr(primitives_module, "_observe_client_target_tip", lambda _target: next(tips))
    restarted = []
    monkeypatch.setattr(
        primitives_module, "_restart_client_runtime_target",
        lambda target: restarted.append(target["container"]) or {"exit_code": 0},
    )
    monkeypatch.setattr(primitives_module, "_client_listener_ready", lambda _target: True)
    monkeypatch.setattr(
        primitives_module, "_client_peer_role_ready",
        lambda _target: {"ready": True, "peer_id": "amaru-consumer"},
    )
    primitives_module.RuntimeRealTargetRestartAndReadiness(params={
        "profile_id": plan["profile"]["id"],
        "runtime_metadata_path": str(runtime_path),
    }).run(handle, None)
    assert restarted == ["registered-amaru-relay-1"]
    assert handle._measurement_context is None
    assert not (handle.run_dir / "measurements/windows.ndjson").exists()


def test_registered_none_measurement_run_uses_frozen_execution_target(
    tmp_path, monkeypatch
):
    desired_pair, release = _operator_registration_topology(manifest=True)
    handle, _plan, config_id = _retained_run(
        tmp_path, desired_pair=desired_pair, release=release
    )
    runtime_path = _registered_runtime_metadata(
        tmp_path, config_id=config_id, version=release["version"]
    )
    from scripts import runtime_amaru_measurement_calibration as calibration

    monkeypatch.setattr(calibration, "_container_ip", lambda _container: "172.24.0.10")
    tips = iter((
        {"slot": 100, "hash": "a" * 64, "block_height": 10},
        {"slot": 101, "hash": "b" * 64, "block_height": 11},
        {"slot": 101, "hash": "b" * 64, "block_height": 11},
    ))
    monkeypatch.setattr(primitives_module, "_observe_client_target_tip", lambda _target: next(tips))
    monkeypatch.setattr(
        primitives_module,
        "_restart_client_runtime_target",
        lambda target: {"command": f"docker restart {target['container']}", "exit_code": 0},
    )
    monkeypatch.setattr(primitives_module, "_client_listener_ready", lambda _target: True)
    monkeypatch.setattr(
        primitives_module,
        "_client_peer_role_ready",
        lambda _target: {"ready": True, "peer_id": "amaru-consumer"},
    )
    monkeypatch.setattr(
        primitives_module,
        "_protocol_container_state",
        lambda _container: {"status": "running", "started_at": "2026-10-12T01:00:00Z"},
    )
    monkeypatch.setattr(
        primitives_module,
        "_wait_for_tip_delta",
        lambda _target, start, **_kwargs: (
            {"slot": 106, "hash": "c" * 64, "block_height": 16},
            [{"slot": 106, "hash": "c" * 64, "block_height": 16}],
        ),
    )
    monkeypatch.setattr(
        primitives_module,
        "_observe_client_window_health",
        lambda *_args, **_kwargs: {"healthy": True},
    )

    primitive = primitives_module.RuntimeRealTargetRestartAndReadiness(
        params={"profile_id": "profile-r", "runtime_metadata_path": str(runtime_path)}
    )
    primitive.run(handle, None)
    primitives_module.RuntimeControlledSyncRange(
        params={
            "profile_id": "profile-r",
            "runtime_metadata_path": str(runtime_path),
            "minimum_blocks": 5,
        }
    ).run(handle, None)

    proof = json.loads(
        (handle.run_dir / "outputs/client-example-proof/restart-readiness.json").read_text()
    )
    assert handle._measurement_context is None
    assert not (handle.run_dir / "measurements/windows.ndjson").exists()
    assert proof["target_node"] == "amaru-relay-1"
    assert proof["checks"] == {
        "all_readiness_gates_observed": True,
        "readiness_gates_ordered": True,
        "target_progressed_after_restart": True,
    }
    sync_proof = json.loads(
        (handle.run_dir / "outputs/client-example-proof/controlled-sync-range.json").read_text()
    )
    assert sync_proof["checks"] == {"controlled_sync_range_complete": True}
    assert sync_proof["sync_start_marker"]["source"] == "scenario-operational-clock"
    assert sync_proof["sync_end_marker"]["source"] == "scenario-operational-clock"


@pytest.mark.parametrize(
    "mutation",
    (
        "runtime-config",
        "runtime-version",
        "runtime-wrapper",
        "runtime-container",
        "runtime-identity-basis",
        "measurement-contract",
        "measurement-contract-missing",
        "measurement-contract-extra",
        "measurement-requested",
        "measurement-skipped",
        "measurement-incompatible",
        "measurement-disabled",
        "measurement-target-identity",
        "malformed-wrapper-id",
        "stale-preflight",
        "unhealthy-preflight",
        "wrong-profile",
        "missing-launch",
        "operator-overlay",
    ),
)
def test_registered_none_measurement_run_refuses_wrong_frozen_binding(
    tmp_path, monkeypatch, mutation
):
    desired_pair, release = _operator_registration_topology(manifest=True)
    handle, _plan, config_id = _retained_run(
        tmp_path, desired_pair=desired_pair, release=release
    )
    runtime_path = _registered_runtime_metadata(
        tmp_path, config_id=config_id, version=release["version"]
    )
    if mutation == "runtime-config":
        runtime = json.loads(runtime_path.read_text())
        service = runtime["identity"]["services"]["amaru-relay-1"]
        service["artifact_image_id"] = "sha256:" + "9" * 64
        runtime_path.write_text(json.dumps(runtime, sort_keys=True))
    elif mutation == "runtime-version":
        runtime = json.loads(runtime_path.read_text())
        runtime["identity"]["services"]["amaru-relay-1"]["expected_version"] = "other"
        runtime_path.write_text(json.dumps(runtime, sort_keys=True))
    elif mutation == "runtime-wrapper":
        runtime = json.loads(runtime_path.read_text())
        runtime["identity"]["services"]["amaru-relay-1"]["wrapper_matched"] = False
        runtime_path.write_text(json.dumps(runtime, sort_keys=True))
    elif mutation == "runtime-container":
        runtime = json.loads(runtime_path.read_text())
        runtime["identity"]["services"]["amaru-relay-1"]["container"] = "substituted"
        runtime_path.write_text(json.dumps(runtime, sort_keys=True))
    elif mutation == "runtime-identity-basis":
        runtime = json.loads(runtime_path.read_text())
        runtime["identity"]["services"]["amaru-relay-1"]["identity_basis"] = "catalog-version-or-source"
        runtime_path.write_text(json.dumps(runtime, sort_keys=True))
    elif mutation == "measurement-contract":
        path = handle.run_dir / "launch/plan.json"
        plan = json.loads(path.read_text())
        plan["measurements"] = {
            "profile": "amaru-stock",
            "resolved": [{"id": "amaru-stock-sync-progress"}],
            "target_identity": {"implementation": "amaru"},
        }
        path.write_text(json.dumps(plan, sort_keys=True))
    elif mutation == "measurement-contract-missing":
        path = handle.run_dir / "launch/plan.json"
        plan = json.loads(path.read_text())
        del plan["measurements"]["target_identity"]
        path.write_text(json.dumps(plan, sort_keys=True))
    elif mutation == "measurement-contract-extra":
        path = handle.run_dir / "launch/plan.json"
        plan = json.loads(path.read_text())
        plan["measurements"]["unexpected"] = False
        path.write_text(json.dumps(plan, sort_keys=True))
    elif mutation in {
        "measurement-requested",
        "measurement-skipped",
        "measurement-incompatible",
        "measurement-disabled",
    }:
        path = handle.run_dir / "launch/plan.json"
        plan = json.loads(path.read_text())
        field = mutation.removeprefix("measurement-")
        plan["measurements"][field] = [{"id": "unexpected-tap"}]
        path.write_text(json.dumps(plan, sort_keys=True))
    elif mutation == "measurement-target-identity":
        path = handle.run_dir / "launch/plan.json"
        plan = json.loads(path.read_text())
        plan["measurements"]["target_identity"] = {"implementation": "amaru"}
        path.write_text(json.dumps(plan, sort_keys=True))
    elif mutation == "malformed-wrapper-id":
        runtime = json.loads(runtime_path.read_text())
        service = runtime["identity"]["services"]["amaru-relay-1"]
        service["running_image_id"] = "sha256:not-a-config-id"
        service["wrapper_expected_image_id"] = "sha256:not-a-config-id"
        runtime_path.write_text(json.dumps(runtime, sort_keys=True))
        path = handle.run_dir / "launch/preflight.json"
        preflight = json.loads(path.read_text())
        node = preflight["health"]["health"]["observations"][0][
            "topology_result"
        ]["controller_health"]["nodes"][0]
        node["runtime_image_id"] = "sha256:not-a-config-id"
        node["runtime_container_image_id"] = "sha256:not-a-config-id"
        path.write_text(json.dumps(preflight, sort_keys=True))
    elif mutation == "stale-preflight":
        path = handle.run_dir / "launch/preflight.json"
        preflight = json.loads(path.read_text())
        preflight["health"]["health"]["fresh"] = False
        path.write_text(json.dumps(preflight, sort_keys=True))
    elif mutation == "unhealthy-preflight":
        path = handle.run_dir / "launch/preflight.json"
        preflight = json.loads(path.read_text())
        preflight["health"]["health"]["state"] = "blocked"
        path.write_text(json.dumps(preflight, sort_keys=True))
    elif mutation == "wrong-profile":
        runtime = json.loads(runtime_path.read_text())
        runtime["profile_id"] = "profile-other"
        runtime_path.write_text(json.dumps(runtime, sort_keys=True))
    elif mutation == "missing-launch":
        (handle.run_dir / "launch/plan.json").unlink()
    else:
        path = handle.run_dir / "launch/plan.json"
        plan = json.loads(path.read_text())
        plan["versions"]["expected_desired_topology"][
            "operator_overlay_revision"
        ] = "sha256:" + "9" * 64
        path.write_text(json.dumps(plan, sort_keys=True))
    restarted = []
    monkeypatch.setattr(
        primitives_module,
        "_restart_client_runtime_target",
        lambda target: restarted.append(target) or {"exit_code": 0},
    )

    primitive = primitives_module.RuntimeRealTargetRestartAndReadiness(
        params={"profile_id": "profile-r", "runtime_metadata_path": str(runtime_path)}
    )
    with pytest.raises(RuntimeError):
        primitive.run(handle, None)

    assert restarted == []


def test_registered_execution_target_refuses_between_read_replacement(
    tmp_path, monkeypatch
):
    desired_pair, release = _operator_registration_topology(manifest=True)
    handle, _plan, _config_id = _retained_run(
        tmp_path, desired_pair=desired_pair, release=release
    )
    original = decisions._retained_json
    launch_reads = 0

    def replacing_read(root, relative, *, label):
        nonlocal launch_reads
        value, payload = original(root, relative, label=label)
        if relative == "launch/plan.json":
            launch_reads += 1
            if launch_reads == 2:
                value = copy.deepcopy(value)
                value["measurements"]["unexpected"] = True
                payload = _canonical(value)
        return value, payload

    monkeypatch.setattr(decisions, "_retained_json", replacing_read)
    with pytest.raises(
        decisions.InvalidOperatorVersionState,
        match="changed during verification",
    ):
        decisions.build_operator_version_execution_target(
            handle.run_dir,
            run_service_plan_sha256=handle._run_service_plan_sha256,
        )


def test_new_canonical_manifest_stamps_runtime_verified_operator_subject(tmp_path):
    handle, plan, config_id = _retained_run(tmp_path)
    handle.end(exit_status="fail")
    manifest = json.loads((handle.run_dir / "manifest.json").read_text())

    stamp = manifest["operator_version_evidence"]
    assert stamp["subject"]["config_image_id"] == config_id
    assert stamp["subject"]["manifest_digest"] == "sha256:" + "4" * 64
    assert stamp["subject"]["scenario_sha256"] != manifest["scenario"]["sha256"]
    assert stamp["executed_scenario_sha256"] == manifest["scenario"]["sha256"]
    assert stamp["run_service_plan_sha256"] == hashlib.sha256(_canonical(plan)).hexdigest()
    assert stamp["runtime_inspection"]["path"] == "launch/preflight.json"
    assert stamp["topology_identity"]["expected_plan_sha256"] != stamp[
        "topology_identity"
    ]["observed_plan_sha256"]
    assert stamp["topology_identity"]["deployment_sha256"] == stamp["subject"][
        "deployment_identity_sha256"
    ]
    assert decisions.decision_subject_from_evidence(manifest) == stamp["subject"]
    assert manifest["execution"]["security_verdict"] == "fail"


def test_existing_v1_exact_plan_stamp_remains_verifiable(tmp_path):
    handle, _plan, _config_id = _retained_run(tmp_path)
    handle.end(exit_status="pass")
    manifest = json.loads((handle.run_dir / "manifest.json").read_text())
    stamp = manifest["operator_version_evidence"]
    stamp["schema_version"] = "v1"
    stamp.pop("topology_identity")
    binding = {
        "subject_key": stamp["subject_key"],
        "run_service_plan_sha256": stamp["run_service_plan_sha256"],
        "run_service_file_sha256": stamp["run_service_file_sha256"],
        "launch_plan_sha256": stamp["launch_plan_sha256"],
        "runtime_inspection_sha256": stamp["runtime_inspection"]["sha256"],
    }
    stamp["binding_sha256"] = "sha256:" + hashlib.sha256(
        _canonical(binding)
    ).hexdigest()

    assert decisions.decision_subject_from_evidence(manifest) == stamp["subject"]


@pytest.mark.parametrize("manifest_bearing", (False, True))
def test_operator_registration_exact_plan_stamps_honest_unverified_identity(
    tmp_path, manifest_bearing
):
    desired_pair, release = _operator_registration_topology(
        manifest=manifest_bearing
    )
    handle, _plan, config_id = _retained_run(
        tmp_path, desired_pair=desired_pair, release=release
    )
    handle.end(exit_status="pass")
    manifest = json.loads((handle.run_dir / "manifest.json").read_text())

    stamp = manifest["operator_version_evidence"]
    assert stamp["subject"]["config_image_id"] == config_id
    assert stamp["subject"]["manifest_digest"] == (
        "sha256:" + "7" * 64 if manifest_bearing else None
    )
    assert stamp["topology_identity"]["expected_plan_sha256"] == stamp[
        "topology_identity"
    ]["observed_plan_sha256"]


def test_unverified_operator_registration_cannot_earn_catalog_enrichment(tmp_path):
    desired_pair, release = _operator_registration_topology(manifest=False)
    handle, _plan, _config_id = _retained_run(
        tmp_path, desired_pair=desired_pair, release=release
    )
    plan_path = handle.run_dir / "launch" / "plan.json"
    plan = json.loads(plan_path.read_text())
    expected = plan["versions"]["expected_desired_topology"]
    artifact = expected["catalog_reuse_metadata"]["artifacts"][0]
    artifact.update(
        public=True,
        public_state="valid",
        platforms=["linux/amd64", "linux/arm64"],
        platforms_state="valid",
    )
    expected.pop("plan_sha256")
    expected["plan_sha256"] = "sha256:" + hashlib.sha256(
        _canonical(expected)
    ).hexdigest()
    plan["versions"]["topology_plan_sha256"] = expected["plan_sha256"]
    plan_path.write_bytes(_canonical(plan))

    handle.end(exit_status="pass")
    manifest = json.loads((handle.run_dir / "manifest.json").read_text())
    assert "operator_version_evidence" not in manifest


@pytest.mark.parametrize(
    "case_name",
    (
        "missing-expected",
        "missing-metadata",
        "malformed-metadata",
        "malformed-equal-metadata",
        "inconsistent-equal-verification",
        "inconsistent-equal-reference",
        "public-withdrawal",
        "platform-removal",
        "platform-incompatible",
        "no-genuine-enrichment",
        "source-invalid",
        "source-change",
        "digest-change",
        "qualification-invalid",
        "qualification-change",
        "image-change",
        "node-change",
        "topology-change",
        "profile-change",
        "operator-revision-change",
        "observed-node-tamper",
    ),
)
def test_operator_stamp_rejects_unproved_or_runtime_affecting_topology_change(
    tmp_path, case_name
):
    handle, _plan, _config_id = _retained_run(tmp_path)
    plan_path = handle.run_dir / "launch" / "plan.json"
    preflight_path = handle.run_dir / "launch" / "preflight.json"
    plan = json.loads(plan_path.read_text())
    preflight = json.loads(preflight_path.read_text())
    versions = plan["versions"]
    expected = versions.get("expected_desired_topology")
    observed = preflight["health"]["desired"]

    def rehash(value):
        value.pop("plan_sha256", None)
        value["plan_sha256"] = "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()

    def make_observed_enriched():
        observed["catalog_reuse_metadata"] = copy.deepcopy(
            expected["catalog_reuse_metadata"]
        )
        observed["catalog_revision"] = "9" * 64
        rehash(observed)

    if case_name == "missing-expected":
        versions.pop("expected_desired_topology")
    elif case_name == "missing-metadata":
        expected.pop("catalog_reuse_metadata")
        rehash(expected)
        versions["topology_plan_sha256"] = expected["plan_sha256"]
    elif case_name == "malformed-metadata":
        expected["catalog_reuse_metadata"]["artifacts"] = "not-a-list"
        rehash(expected)
        versions["topology_plan_sha256"] = expected["plan_sha256"]
    elif case_name == "malformed-equal-metadata":
        expected["catalog_reuse_metadata"]["artifacts"] = []
        expected["catalog_reuse_metadata"]["eligible"] = False
        expected["catalog_revision"] = observed["catalog_revision"]
        rehash(expected)
        observed.clear()
        observed.update(copy.deepcopy(expected))
        versions["topology_plan_sha256"] = expected["plan_sha256"]
    elif case_name in {
        "inconsistent-equal-verification",
        "inconsistent-equal-reference",
    }:
        artifact = expected["catalog_reuse_metadata"]["artifacts"][0]
        if case_name == "inconsistent-equal-verification":
            artifact["verification_valid"] = False
            artifact["availability"] = "invalid"
        else:
            artifact["reference"] = "https://user:password@example.invalid/image"
            artifact["reference_valid"] = True
        expected["catalog_revision"] = observed["catalog_revision"]
        rehash(expected)
        observed.clear()
        observed.update(copy.deepcopy(expected))
        versions["topology_plan_sha256"] = expected["plan_sha256"]
    elif case_name == "public-withdrawal":
        make_observed_enriched()
        artifact = expected["catalog_reuse_metadata"]["artifacts"][0]
        artifact.update(public_state="valid", public=False)
        expected["catalog_reuse_metadata"]["eligible"] = False
        rehash(expected)
        versions["topology_plan_sha256"] = expected["plan_sha256"]
    elif case_name == "platform-removal":
        make_observed_enriched()
        expected["catalog_reuse_metadata"]["artifacts"][0]["platforms"] = [
            "linux/amd64"
        ]
        rehash(expected)
        versions["topology_plan_sha256"] = expected["plan_sha256"]
    elif case_name == "platform-incompatible":
        container_platform = expected["catalog_reuse_metadata"][
            "container_platform"
        ]
        incompatible_platform = (
            "linux/amd64"
            if container_platform == "linux/arm64"
            else "linux/arm64"
        )
        expected["catalog_reuse_metadata"]["artifacts"][0]["platforms"] = [
            incompatible_platform
        ]
        expected["catalog_reuse_metadata"]["eligible"] = False
        rehash(expected)
        versions["topology_plan_sha256"] = expected["plan_sha256"]
    elif case_name == "no-genuine-enrichment":
        expected["catalog_reuse_metadata"] = copy.deepcopy(
            observed["catalog_reuse_metadata"]
        )
        rehash(expected)
        versions["topology_plan_sha256"] = expected["plan_sha256"]
    elif case_name in {
        "source-invalid",
        "source-change",
        "qualification-invalid",
        "qualification-change",
        "digest-change",
    }:
        artifact = expected["catalog_reuse_metadata"]["artifacts"][0]
        if case_name == "source-invalid":
            artifact["source_revision_valid"] = False
        elif case_name == "source-change":
            artifact["source_revision"] = "f" * 40
        elif case_name == "qualification-invalid":
            artifact["qualification_valid"] = False
        elif case_name == "qualification-change":
            artifact["qualification_present"] = False
        else:
            artifact["digest"] = "sha256:" + "8" * 64
        rehash(expected)
        versions["topology_plan_sha256"] = expected["plan_sha256"]
    elif case_name == "observed-node-tamper":
        observed["nodes"][0]["image"] = "registry.invalid/amaru:tampered"
        rehash(observed)
    else:
        changed_deployment = "sha256:" + "e" * 64
        expected["deployment_sha256"] = changed_deployment
        versions["deployment_identity_sha256"] = changed_deployment
        if case_name == "image-change":
            expected["nodes"][0]["image"] = "registry.invalid/amaru:changed"
        elif case_name == "node-change":
            expected["nodes"][0]["version"] = "changed"
        elif case_name == "topology-change":
            expected["topology"] = {"edges": [["amaru-1", "other"]]}
        elif case_name == "profile-change":
            expected["profile_id"] = "other-profile"
        else:
            expected["operator_overlay_revision"] = "f" * 64
        rehash(expected)
        versions["topology_plan_sha256"] = expected["plan_sha256"]

    plan_path.write_bytes(_canonical(plan))
    preflight_path.write_bytes(_canonical(preflight))
    handle.end(exit_status="pass")
    manifest = json.loads((handle.run_dir / "manifest.json").read_text())

    assert "operator_version_evidence" not in manifest
    assert manifest.get("operator_version_evidence_unavailable")


def test_new_manifest_disables_operator_action_without_verified_runtime_identity(tmp_path):
    handle, _plan, _config_id = _retained_run(tmp_path, with_runtime_identity=False)
    handle.end(exit_status="pass")
    manifest = json.loads((handle.run_dir / "manifest.json").read_text())

    assert "operator_version_evidence" not in manifest
    assert manifest["operator_version_evidence_unavailable"] == "runtime node observation is incomplete"


def test_stale_or_unhealthy_preflight_disables_decision_without_losing_run(tmp_path):
    for field, value in (("fresh", False), ("state", "unhealthy")):
        case = tmp_path / field
        handle, _plan, _config_id = _retained_run(case)
        path = handle.run_dir / "launch" / "preflight.json"
        preflight = json.loads(path.read_text())
        preflight["health"]["health"][field] = value
        path.write_bytes(_canonical(preflight))
        handle.end(exit_status="pass")
        manifest = json.loads((handle.run_dir / "manifest.json").read_text())
        assert "operator_version_evidence" not in manifest
        assert "not fresh and healthy" in manifest["operator_version_evidence_unavailable"]
        assert manifest["exit_status"] == "pass"


def test_mixed_subject_requires_verified_identity_for_every_resolved_implementation(tmp_path):
    handle, _plan, _config_id = _retained_run(tmp_path)
    path = handle.run_dir / "launch" / "plan.json"
    plan = json.loads(path.read_text())
    plan["versions"]["scope"] = "mixed"
    plan["versions"]["pair"] = {"id": "exact-pair"}
    plan["versions"]["resolved"]["cardano-node"] = {
        "implementation": "cardano-node", "version": "11.1.2",
        "image_reference": "registry.invalid/cardano@sha256:" + "9" * 64,
        "image_digest": "sha256:" + "9" * 64,
        "image_config_id": "sha256:" + "8" * 64,
    }
    path.write_bytes(_canonical(plan))
    handle.end(exit_status="pass")
    manifest = json.loads((handle.run_dir / "manifest.json").read_text())
    assert "operator_version_evidence" not in manifest
    assert "runtime target identity is unavailable" in manifest["operator_version_evidence_unavailable"]


def test_runtime_identity_refuses_split_duplicate_and_mismatched_observations(tmp_path):
    for case_name in ("split", "duplicate", "wrong-version"):
        case = tmp_path / case_name
        handle, _plan, config_id = _retained_run(case)
        plan_path = handle.run_dir / "launch" / "plan.json"
        plan = json.loads(plan_path.read_text())
        plan["versions"]["scope"] = "mixed"
        plan["versions"]["resolved"]["cardano-node"] = {
            "implementation": "cardano-node", "version": "11.1.2",
            "image_reference": "registry.invalid/cardano:11.1.2",
            "image_digest": "sha256:" + "9" * 64,
            "image_config_id": "sha256:" + "8" * 64,
        }
        plan_path.write_bytes(_canonical(plan))
        preflight_path = handle.run_dir / "launch" / "preflight.json"
        preflight = json.loads(preflight_path.read_text())
        preflight["health"]["desired"]["nodes"].append({
            "id": "cardano-1", "impl": "cardano-node", "version": "11.1.2"
        })
        amaru = preflight["health"]["health"]["observations"][0]
        cardano_node = {
            "id": "cardano-1", "implementation": "cardano-node", "version": "11.1.2",
            "target_image_id": "sha256:" + "8" * 64,
            "artifact_image_id": "sha256:" + "8" * 64, "healthy": True,
        }
        if case_name == "split":
            preflight["health"]["health"]["observations"].append({
                "topology_result": {"controller_health": {"nodes": [cardano_node]}}
            })
        elif case_name == "duplicate":
            nodes = amaru["topology_result"]["controller_health"]["nodes"]
            nodes.extend([cardano_node, dict(cardano_node)])
        else:
            bad = dict(cardano_node)
            bad["version"] = "wrong"
            amaru["topology_result"]["controller_health"]["nodes"].append(bad)
        preflight_path.write_bytes(_canonical(preflight))
        handle.end(exit_status="pass")
        manifest = json.loads((handle.run_dir / "manifest.json").read_text())
        assert "operator_version_evidence" not in manifest, (case_name, config_id)


def test_runtime_identity_binds_health_desired_to_launch_and_rejects_malformed_sample(tmp_path):
    for case_name in ("different-desired", "malformed-sample"):
        case = tmp_path / case_name
        handle, _plan, _config_id = _retained_run(case)
        path = handle.run_dir / "launch" / "preflight.json"
        preflight = json.loads(path.read_text())
        if case_name == "different-desired":
            preflight["health"]["desired"]["deployment_sha256"] = "sha256:" + "e" * 64
        else:
            preflight["health"]["health"]["observations"].append("malformed")
        path.write_bytes(_canonical(preflight))
        handle.end(exit_status="pass")
        manifest = json.loads((handle.run_dir / "manifest.json").read_text())
        assert "operator_version_evidence" not in manifest


def test_retained_input_tamper_and_symlink_disable_results_action(tmp_path):
    valid = tmp_path / "valid"
    handle, _plan, _config_id = _retained_run(valid)
    handle.end(exit_status="pass")
    launch_plan = handle.run_dir / "launch" / "plan.json"
    launch_plan.write_bytes(launch_plan.read_bytes() + b"\n")
    detail = operate_run_detail(handle.run_id, runs_dir=valid / "runs")
    assert detail["operator_version"]["available"] is False
    assert "launch/plan.json hash mismatch" in detail["operator_version"]["reason"]

    scenario_case = tmp_path / "scenario-tamper"
    scenario_handle, _plan, _config_id = _retained_run(scenario_case)
    scenario_handle.end(exit_status="pass")
    (scenario_handle.run_dir / "launch" / "scenario.yaml").unlink()
    scenario_detail = operate_run_detail(
        scenario_handle.run_id, runs_dir=scenario_case / "runs"
    )
    assert scenario_detail["operator_version"]["available"] is False
    assert "launch/scenario.yaml" in scenario_detail["operator_version"]["reason"]

    linked = tmp_path / "linked"
    symlinked, _plan, _config_id = _retained_run(linked)
    preflight = symlinked.run_dir / "launch" / "preflight.json"
    target = linked / "outside-preflight.json"
    target.write_bytes(preflight.read_bytes())
    preflight.unlink()
    os.symlink(target, preflight)
    symlinked.end(exit_status="pass")
    manifest = json.loads((symlinked.run_dir / "manifest.json").read_text())
    assert "operator_version_evidence" not in manifest
    assert "retained runtime inspection is unavailable" in manifest["operator_version_evidence_unavailable"]


def test_retained_parent_symlink_is_rejected_and_open_fd_resists_leaf_swap(tmp_path, monkeypatch):
    linked = tmp_path / "linked-parent"
    handle, _plan, _config_id = _retained_run(linked)
    launch = handle.run_dir / "launch"
    real_launch = handle.run_dir / "real-launch"
    launch.rename(real_launch)
    launch.symlink_to(real_launch, target_is_directory=True)
    handle.end(exit_status="pass")
    manifest = json.loads((handle.run_dir / "manifest.json").read_text())
    assert "parent is unsafe" in manifest["operator_version_evidence_unavailable"]

    root = tmp_path / "fd-safe"
    (root / "inputs").mkdir(parents=True)
    retained = root / "inputs" / "value.json"
    retained.write_text('{"trusted":true}')
    outside = tmp_path / "outside.json"
    outside.write_text('{"trusted":false}')
    real_open = decisions.os.open
    swapped = False

    def swap_after_open(path, flags, *args, **kwargs):
        nonlocal swapped
        descriptor = real_open(path, flags, *args, **kwargs)
        if not swapped and path == "inputs" and kwargs.get("dir_fd") is not None:
            swapped = True
            original = root / "inputs-original"
            (root / "inputs").rename(original)
            (root / "inputs").symlink_to(outside.parent, target_is_directory=True)
        return descriptor

    monkeypatch.setattr(decisions.os, "open", swap_after_open)
    value, _payload = decisions._retained_json(
        root, "inputs/value.json", label="swap test"
    )
    assert value == {"trusted": True}

    symlink_root = tmp_path / "fd-safe-link"
    symlink_root.symlink_to(root, target_is_directory=True)
    try:
        decisions._retained_json(symlink_root, "inputs/value.json", label="root link")
    except decisions.InvalidOperatorVersionState as exc:
        assert "root is unsafe" in str(exc)
    else:
        raise AssertionError("relative/root symlink was accepted")


def test_malformed_nested_records_never_abort_manifest_and_chain_finalization(tmp_path):
    for field, replacement in (
        ("artifacts", []),
        ("scenario", []),
        ("controller", "not-an-object"),
    ):
        case = tmp_path / field
        handle, _plan, _config_id = _retained_run(case)
        plan_path = handle.run_dir / "launch" / "plan.json"
        preflight_path = handle.run_dir / "launch" / "preflight.json"
        if field in {"artifacts", "scenario"}:
            plan = json.loads(plan_path.read_text())
            plan[field] = replacement
            plan_path.write_bytes(_canonical(plan))
        else:
            preflight = json.loads(preflight_path.read_text())
            preflight["health"]["health"]["observations"][0]["topology_result"]["controller_health"] = replacement
            preflight_path.write_bytes(_canonical(preflight))
        handle.end(exit_status="pass")
        manifest = json.loads((handle.run_dir / "manifest.json").read_text())
        assert manifest["exit_status"] == "pass"
        assert "operator_version_evidence" not in manifest
        assert (handle.run_dir / "chain.json").is_file()


def test_decision_waits_for_matching_terminal_ledger_reconciliation(tmp_path):
    handle, _plan, _config_id = _retained_run(tmp_path, defer_terminal=True)
    handle.end(exit_status="pass")
    running = operate_run_detail(handle.run_id, runs_dir=tmp_path / "runs")
    assert running["operator_version"]["available"] is False
    assert "matching terminal state" in running["operator_version"]["reason"]

    manifest = json.loads((handle.run_dir / "manifest.json").read_text())
    RunLedger(tmp_path / "state").transition(
        handle.run_id, "completed", verdict="pass", ended_at=manifest["ended_at"],
        manifest_path=str((handle.run_dir / "manifest.json").resolve()),
        evidence_path=str(handle.run_dir.resolve()),
    )
    terminal = operate_run_detail(handle.run_id, runs_dir=tmp_path / "runs")
    assert terminal["operator_version"]["available"] is True


def test_manifest_target_profile_and_executed_scenario_mismatch_are_rejected(tmp_path):
    handle, _plan, _config_id = _retained_run(tmp_path)
    handle.end(exit_status="pass")
    manifest = json.loads((handle.run_dir / "manifest.json").read_text())
    for field, mutate in (
        ("target", lambda value: value["target"].update({"version": "wrong"})),
        ("profile", lambda value: value["profile"].update({"id": "wrong"})),
        ("scenario", lambda value: value["scenario"].update({"sha256": "f" * 64})),
    ):
        changed = json.loads(json.dumps(manifest))
        mutate(changed)
        try:
            decisions.decision_subject_from_evidence(changed)
        except decisions.InvalidOperatorVersionState as exc:
            assert "disagrees with run manifest" in str(exc), field
        else:
            raise AssertionError(f"{field} mismatch was accepted")


def test_historical_manifest_is_never_enriched_on_results_read(tmp_path, monkeypatch):
    run_dir = tmp_path / "runs" / "historical-run"
    run_dir.mkdir(parents=True)
    manifest = {"run_id": "historical-run", "exit_status": "pass"}
    path = run_dir / "manifest.json"
    path.write_text(json.dumps(manifest))
    before = path.read_bytes()

    try:
        decisions.decision_subject_from_evidence(manifest)
    except decisions.InvalidOperatorVersionState:
        pass

    assert path.read_bytes() == before


def test_basic_results_records_explicit_failed_run_decision_without_changing_verdict(
    tmp_path, monkeypatch
):
    handle, _plan, _config_id = _retained_run(tmp_path)
    handle.end(exit_status="fail")
    ledger = decisions.OperatorVersionDecisionLedger(tmp_path / "state")
    revision = ledger.read()["revision"]
    body = urlencode({
        "token": "private-test-token", "run_id": handle.run_id,
        "decision": "supported", "reason": "Integration contract worked; property still failed.",
        "prefer": "true", "expected_revision": revision,
    }).encode()

    status, _ctype, _payload, headers = dashboard.dispatch_run_version_decision_request(
        method="POST", path="/api/runs/operator-version-decision", body=body,
        expected_token="private-test-token", ledger=ledger, runs_dir=tmp_path / "runs",
    )

    assert status == 303
    assert headers == {"Location": f"/operate/runs/{handle.run_id}"}
    state = ledger.read()
    assert state["decisions"][-1]["decision"] == "supported"
    assert state["decisions"][-1]["evidence"]["operational_outcome"] == "fail"
    assert state["decisions"][-1]["evidence"]["assertion_verdict"] == "fail"
    assert state["preferred_defaults"][-1]["decision_id"] == state["decisions"][-1]["decision_id"]
    assert json.loads((handle.run_dir / "manifest.json").read_text())["exit_status"] == "fail"


def test_results_auth_and_stale_revision_fail_without_ledger_write(tmp_path):
    handle, _plan, _config_id = _retained_run(tmp_path)
    handle.end(exit_status="pass")
    ledger = decisions.OperatorVersionDecisionLedger(tmp_path / "state")
    original = ledger.read()
    fields = {
        "token": "wrong", "run_id": handle.run_id, "decision": "unsupported",
        "reason": "Observed incompatibility", "prefer": "false",
        "expected_revision": original["revision"],
    }
    unauthorized = dashboard.dispatch_run_version_decision_request(
        method="POST", path="/api/runs/operator-version-decision",
        body=urlencode(fields).encode(), expected_token="right", ledger=ledger,
        runs_dir=tmp_path / "runs",
    )
    assert unauthorized[0] == 401
    assert ledger.read() == original

    fields["token"] = "right"
    fields["expected_revision"] = "sha256:" + "0" * 64
    stale = dashboard.dispatch_run_version_decision_request(
        method="POST", path="/api/runs/operator-version-decision",
        body=urlencode(fields).encode(), expected_token="right", ledger=ledger,
        runs_dir=tmp_path / "runs",
    )
    assert stale[0] == 409
    assert ledger.read() == original


def test_actual_run_route_renders_body_token_form_and_explicit_unavailable_reason(
    tmp_path, monkeypatch
):
    handle, _plan, _config_id = _retained_run(tmp_path)
    handle.end(exit_status="pass")
    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ADA2_DWARF_BUNDLES_DIR", str(tmp_path / "bundles"))
    html = dashboard.render_route_html(
        f"/operate/runs/{handle.run_id}", token="private-test-token"
    )
    assert 'action="/api/runs/operator-version-decision"' in html
    assert 'name="token" value="private-test-token"' in html
    assert "Operator support decision" in html
    decision = html.index('data-operator-version-decision')
    manifest = html.index('data-run-section="manifest"')
    assert decision > manifest
    opening = html[html.rfind("<details", 0, decision) : decision]
    assert " open" not in opening
    assert "Local compatibility" not in html

    historical = tmp_path / "runs" / "historical"
    historical.mkdir()
    (historical / "manifest.json").write_text(json.dumps({"run_id": "historical"}))
    unavailable = dashboard.render_route_html("/operate/runs/historical", token="private-test-token")
    assert "Decision unavailable:" in unavailable
    assert "matching terminal state" in unavailable
