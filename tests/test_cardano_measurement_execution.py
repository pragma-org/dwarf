import json

import pytest

from profile_manager.measurement_execution import MeasurementExecutionError, prepare_scenario_measurements
from profile_manager.scenario import load_scenario


VERSION = "11.1.2"
REVISION = "fef83fed01d7926f3de83b3b917be5a4a48768b5"
DIGEST = "sha256:6365403f44713d0a046865fb0466503ef207b71beae1b1ffece7f4399356db9f"


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


def test_prepare_cardano_measurements_fails_closed_on_unproven_runtime(tmp_path):
    scenario = load_scenario("dwarf/scenarios/cardano-measurement-e2e-stock.yaml")
    runtime_path = _runtime(tmp_path / "runtime.json", satisfied=False)
    with pytest.raises(MeasurementExecutionError, match="identity is not proven"):
        prepare_scenario_measurements(scenario, runtime_metadata_path=runtime_path)
    runtime_path = _runtime(tmp_path / "runtime.json", digest="sha256:" + "9" * 64)
    with pytest.raises(MeasurementExecutionError, match="image digest"):
        prepare_scenario_measurements(scenario, runtime_metadata_path=runtime_path)
