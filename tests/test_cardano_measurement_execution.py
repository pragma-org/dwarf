import json
from dataclasses import replace

import pytest

from profile_manager.measurement_execution import MeasurementExecutionError, prepare_scenario_measurements
from profile_manager.measurement_resolution import MeasurementResolutionError, resolve_measurements
from profile_manager.scenario import load_scenario, scenario_from_body


VERSION = "11.1.2"
REVISION = "fef83fed01d7926f3de83b3b917be5a4a48768b5"
DIGEST = "sha256:6365403f44713d0a046865fb0466503ef207b71beae1b1ffece7f4399356db9f"
PATCH_SET = "7a948067c6b957b277400675cf95e32864ed8d92cd130fbadb673775249b5cc1"
PATCHED_DIGEST = "sha256:" + "a" * 64
LOCAL_CONFIG_ID = "sha256:" + "2" * 64
LOCAL_EXECUTABLE = "sha256:" + "3" * 64
LOCAL_OVERLAY = "sha256:" + "4" * 64
LOCAL_REGISTRATION = "registration-7efbdb922f9a412d8bb94e07"


def _runtime(path, *, satisfied=True, digest=DIGEST):
    release = {
        "implementation": "cardano-node",
        "version": VERSION,
        "source_revision": REVISION,
        "artifacts": [{"kind": "oci", "availability": "available", "reference": "ghcr.io/intersectmbo/cardano-node:11.1.2", "digest": DIGEST}],
    }
    body = {
        "profile_id": "profile-s-cardano-measurement-stock-control",
        "compose_project": "dwarf-profile-s-cardano-measurement-stock-control",
        "network_magic": 42,
        "version_provenance": {
            "catalog_revision": "a" * 64,
            "catalog_snapshot": {"selected_releases": [release]},
            "nodes": [{"id": "node1", "implementation": "cardano-node", "requested_version": VERSION, "source_revision": REVISION, "image_digest": DIGEST, "identity_status": "running-version-verified"}],
        },
        "nodes": [{
            "id": "node1", "impl": "cardano-node", "version": VERSION,
            "source_revision": REVISION,
            "container_name": "dwarf-cardano-node1",
            "container_socket_path": "/env/socket/node1/sock",
            "image_ref": f"ghcr.io/intersectmbo/cardano-node:11.1.2@{DIGEST}",
            "artifact_identity": {"satisfied": satisfied, "image_digest": digest, "container_image_id": digest},
            "version_identity": {"satisfied": satisfied, "requested_version": VERSION, "reported_version": VERSION},
            "log_path": str(path.parent / "logs" / "node1" / "stdout.log"),
        }],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body))
    return path


def test_prepare_cardano_measurements_resolves_exact_live_stock_runtime(tmp_path):
    scenario = load_scenario("dwarf/scenarios/cardano-measurement-e2e-stock.yaml")
    runtime_path = _runtime(tmp_path / "runtime.json")
    prepared = prepare_scenario_measurements(
        scenario,
        runtime_metadata_path=runtime_path,
        tip_probe=lambda: {"block_height": 1, "block_hash": "a" * 64},
    )
    identity = prepared.resolution["target_identity"]
    assert identity["implementation"] == "cardano-node"
    assert identity["version"] == VERSION
    assert identity["source_revision"] == REVISION
    assert identity["image_digest"] == DIGEST
    assert identity["mode"] == "stock"
    assert len(prepared.resolution["resolved"]) == 10
    factories = prepared.build_factories(tmp_path / "run")
    assert set(factories) == {row["id"] for row in prepared.resolution["resolved"]}
    entry = next(
        row
        for row in prepared.resolution["resolved"]
        if row["id"] == "cardano-stock-network"
    )
    collector = factories["cardano-stock-network"](entry)
    assert collector.trace_paths == [
        tmp_path / "run" / "outputs" / "cardano-measurement-calibration" / "raw" / "node1.ndjson",
        tmp_path / "run" / "outputs" / "protocol-decode-cases" / "raw" / "node1.ndjson",
    ]


def test_prepare_cardano_measurements_fails_closed_on_unproven_runtime(tmp_path):
    scenario = load_scenario("dwarf/scenarios/cardano-measurement-e2e-stock.yaml")
    runtime_path = _runtime(tmp_path / "runtime.json", satisfied=False)
    with pytest.raises(MeasurementExecutionError, match="identity is not proven"):
        prepare_scenario_measurements(scenario, runtime_metadata_path=runtime_path)
    runtime_path = _runtime(tmp_path / "runtime.json", digest="sha256:" + "9" * 64)
    with pytest.raises(MeasurementExecutionError, match="image digest"):
        prepare_scenario_measurements(scenario, runtime_metadata_path=runtime_path)


@pytest.mark.parametrize("declared_source", (None, "d" * 40))
def test_prepare_local_cardano_measurements_binds_frozen_consent_and_config(
    tmp_path, declared_source
):
    document = {
        "spec_version": "v1",
        "id": "cardano-local-experimental-measurement",
        "title": "Cardano local experimental measurement",
        "target": {"implementation": "cardano-node", "version": "local-cardano"},
        "runtime": "devnet",
        "profile": "profile-s-cardano-measurement-stock-control",
        "measurement_profile": "none",
        "measurements": [
            {"id": "cardano-external-workload-accounting", "enabled": True}
        ],
        "setup": [],
        "load": [],
        "faults": [],
        "probes": [],
        "assertions": [],
        "teardown": [],
    }
    scenario = scenario_from_body((json.dumps(document) + "\n").encode())
    identity = {
        "implementation": "cardano-node",
        "version": "local-cardano",
        "source_revision": declared_source,
        "mode": "stock",
        "identity_kind": "operator-local-config",
        "image_reference": LOCAL_CONFIG_ID,
        "image_digest": None,
        "image_config_id": LOCAL_CONFIG_ID,
        "executable_digest": LOCAL_EXECUTABLE,
        "platform": "linux/amd64",
        "operator_registration_id": LOCAL_REGISTRATION,
        "operator_overlay_revision": LOCAL_OVERLAY,
        "controller_installation_id": "install-test",
        "version_catalog_revision": "a" * 64,
    }
    with pytest.raises(MeasurementResolutionError) as raised:
        resolve_measurements(
            scenario,
            target_identity=identity,
            capabilities={"dwarf-workload-events"},
        )
    frozen = resolve_measurements(
        scenario,
        target_identity=identity,
        capabilities={"dwarf-workload-events"},
        experimental_consent=raised.value.resolution[
            "experimental_consent_required"
        ],
    )
    registration = {
        "registration_id": LOCAL_REGISTRATION,
        "implementation": "cardano-node",
        "version": "local-cardano",
        "config_image_id": LOCAL_CONFIG_ID,
        "executable_digest": LOCAL_EXECUTABLE,
        "platform": "linux/amd64",
        "controller_installation_id": "install-test",
        "provenance": {
            "source_revision": declared_source,
            "public": None,
            "qualification": "unverified",
        },
    }
    runtime = {
        "profile_id": "profile-s-cardano-measurement-stock-control",
        "network_magic": 42,
        "version_provenance": {
            "catalog_revision": "a" * 64,
            "catalog_snapshot": {
                "operator_registration_id": LOCAL_REGISTRATION,
                "operator_overlay_revision": LOCAL_OVERLAY,
                "selected_releases": [
                    {
                        "implementation": "cardano-node",
                        "version": "local-cardano",
                        "source_revision": declared_source,
                        "operator_registration": registration,
                        "artifacts": [
                            {
                                "kind": "oci",
                                "availability": "available",
                                "reference": LOCAL_CONFIG_ID,
                                "config_id": LOCAL_CONFIG_ID,
                                "digest": None,
                            }
                        ],
                    }
                ],
            },
        },
        "nodes": [
            {
                "id": "node1",
                "impl": "cardano-node",
                "version": "local-cardano",
                "source_revision": None,
                "container_name": "dwarf-local-cardano-node1",
                "container_socket_path": "/env/socket/node1/sock",
                "image_ref": LOCAL_CONFIG_ID,
                "artifact_identity": {
                    "satisfied": True,
                    "image_digest": LOCAL_CONFIG_ID,
                    "container_image_id": LOCAL_CONFIG_ID,
                },
                "version_identity": {
                    "satisfied": True,
                    "requested_version": "local-cardano",
                    "reported_version": "local-cardano",
                },
            }
        ],
    }
    runtime_path = tmp_path / "runtime.json"
    runtime_path.write_text(json.dumps(runtime), encoding="utf-8")

    prepared = prepare_scenario_measurements(
        scenario,
        runtime_metadata_path=runtime_path,
        tip_probe=lambda: {"block_height": 1, "block_hash": "a" * 64},
        expected_resolution=frozen,
    )

    assert prepared.resolution["target_identity"] == identity
    assert prepared.resolution["resolved"][0]["experimental_consent"] is True


def _patched_runtime(path, *, patch_set=PATCH_SET):
    _runtime(path)
    body = json.loads(path.read_text())
    body["profile_id"] = "profile-t-cardano-measurement-patched"
    body["runtime_root"] = str(path.parent)
    node = body["nodes"][0]
    node.update({
        "target_mode": "patched",
        "patch_set_sha256": patch_set,
        "image_ref": "dwarf/cardano-measurement@" + PATCHED_DIGEST,
        "image_digest": PATCHED_DIGEST,
        "executable_digest": "sha256:" + "b" * 64,
        "build_result_sha256": "sha256:" + "c" * 64,
        "runtime_probe_log_sha256": "sha256:" + "d" * 64,
        "artifact_identity": {
            "satisfied": True,
            "image_digest": PATCHED_DIGEST,
            "container_image_id": PATCHED_DIGEST,
        },
    })
    path.write_text(json.dumps(body))
    return path


def test_prepare_cardano_measurements_resolves_exact_live_patched_runtime(tmp_path):
    stock = load_scenario("dwarf/scenarios/cardano-measurement-e2e-stock.yaml")
    scenario = replace(
        stock,
        profile="profile-t-cardano-measurement-patched",
        measurement_profile="cardano-security-patched",
    )
    runtime_path = _patched_runtime(tmp_path / "runtime.json")

    prepared = prepare_scenario_measurements(
        scenario,
        runtime_metadata_path=runtime_path,
        tip_probe=lambda: {"block_height": 1, "block_hash": "a" * 64},
    )

    identity = prepared.resolution["target_identity"]
    assert identity["mode"] == "patched"
    assert identity["image_digest"] == PATCHED_DIGEST
    assert identity["patch_set_sha256"] == PATCH_SET
    assert len(prepared.resolution["resolved"]) == 12
    factories = prepared.build_factories(tmp_path / "run")
    assert "cardano-patched-protocol-decode" in factories
    assert "cardano-patched-ledger-plutus-stages" in factories


def test_prepare_cardano_measurements_rejects_wrong_patched_identity(tmp_path):
    stock = load_scenario("dwarf/scenarios/cardano-measurement-e2e-stock.yaml")
    scenario = replace(
        stock,
        profile="profile-t-cardano-measurement-patched",
        measurement_profile="cardano-security-patched",
    )
    runtime_path = _patched_runtime(
        tmp_path / "runtime.json", patch_set="9" * 64
    )

    with pytest.raises(MeasurementExecutionError, match="patch-set"):
        prepare_scenario_measurements(scenario, runtime_metadata_path=runtime_path)
