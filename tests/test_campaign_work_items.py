"""Immutable work-item contracts for hosted campaign fan-out."""
from copy import deepcopy

import pytest

from profile_manager import campaign_work_items as api
from profile_manager.security_campaign import digest_security_campaign


def frozen(manifest):
    manifest["campaign_digest"] = digest_security_campaign(manifest)
    manifest["campaign_id"] = "campaign-" + manifest["campaign_digest"].split(":", 1)[1]
    return manifest


def test_calibration_items_are_stable_and_bound_to_each_manifest(campaign_plan):
    stock = deepcopy(campaign_plan)
    patched = deepcopy(campaign_plan)
    patched["topologies"][0]["nodes"][0]["identity"]["mode"] = "patched"
    frozen(patched)

    items = api.calibration_work_items(patched, stock)

    assert [item["item_id"] for item in items] == ["calibration:patched", "calibration:stock"]
    assert items[0]["manifest_role"] == "primary"
    assert items[1]["manifest_role"] == "stock"
    assert items == api.calibration_work_items(patched, stock)
    assert all(item["work_item_digest"].startswith("sha256:") for item in items)


def test_stock_primary_schedules_no_unbuilt_patched_calibration(campaign_plan):
    items = api.calibration_work_items(campaign_plan, campaign_plan)

    assert [item["item_id"] for item in items] == ["calibration:stock"]


def test_execution_items_group_measurements_by_runtime_recipe(campaign_plan):
    manifest = deepcopy(campaign_plan)
    base = deepcopy(manifest["required_tap_cells"][0])
    base.update(runtime={"status": "implemented"}, target_modes=["stock"])
    second = deepcopy(base)
    second["measurement_id"] = "another-measurement"
    second["definition_digest"] = "sha256:" + "9" * 64
    missing = deepcopy(base)
    missing.update(measurement_id="missing-measurement", runtime={"status": "missing", "reason": "no probe"})
    manifest["required_tap_cells"] = [base, second, missing]
    frozen(manifest)

    inventory = api.execution_work_items(manifest)
    security = [item for item in inventory["items"] if item["kind"] == "security"]
    measurement = [item for item in inventory["items"] if item["kind"] == "measurement"]

    assert [item["entry_id"] for item in security] == [manifest["entries"][0]["id"]]
    assert {item["phase"] for item in security} == {"amaru-solo"}
    assert len(measurement) == 1
    assert measurement[0]["phase"] == "amaru-solo"
    assert measurement[0]["measurement_ids"] == sorted([base["measurement_id"], second["measurement_id"]])
    assert inventory["blockers"] == [{
        "measurement_id": "missing-measurement",
        "mode": "stock",
        "node_id": missing["node_id"],
        "reason": "no probe",
        "topology_id": missing["topology_id"],
    }]


def test_patched_execution_adds_one_stock_replay_per_security_entry(campaign_plan):
    stock = deepcopy(campaign_plan)
    patched = deepcopy(campaign_plan)
    patched["topologies"][0]["nodes"][0]["identity"]["mode"] = "patched"
    for cell in patched["required_tap_cells"]:
        cell["target_modes"] = ["patched"]
    frozen(patched)

    result = api.execution_work_items(patched, stock_manifest=stock, stock_replay_required=True)
    replays = [item for item in result["items"] if item["kind"] == "stock-replay"]

    assert len(replays) == len(stock["entries"])
    assert replays[0]["manifest_role"] == "stock"
    assert replays[0]["campaign_digest"] == stock["campaign_digest"]


def test_execution_items_are_partitioned_amaru_cardano_then_mixed():
    inventory = {"items": [
        {"item_id": "mixed", "phase": "mixed"},
        {"item_id": "amaru", "phase": "amaru-solo"},
        {"item_id": "cardano", "phase": "cardano-solo"},
    ]}

    phases = api.phase_work_items(inventory)

    assert list(phases) == ["amaru-solo", "cardano-solo", "mixed"]
    assert [[item["item_id"] for item in phases[phase]] for phase in phases] == [
        ["amaru"], ["cardano"], ["mixed"]
    ]


def test_mixed_campaign_runs_per_node_measurements_before_shared_security(campaign_plan):
    manifest = deepcopy(campaign_plan)
    topology = manifest["topologies"][0]
    topology["nodes"].append({
        "node_id": "node2",
        "identity": {
            **deepcopy(topology["nodes"][0]["identity"]),
            "implementation": "cardano-node",
            "kind": "release",
        },
    })
    implemented = deepcopy(manifest["required_tap_cells"][0])
    implemented.update(runtime={"status": "implemented"}, target_modes=["stock"])
    cardano = deepcopy(implemented)
    cardano.update(
        implementation="cardano-node",
        measurement_id="cardano-stock-resources",
        node_id="node2",
        definition_digest="sha256:" + "8" * 64,
    )
    manifest["required_tap_cells"] = [implemented, cardano]
    frozen(manifest)

    phases = api.phase_work_items(api.execution_work_items(manifest))

    assert {item["kind"] for item in phases["amaru-solo"]} == {"measurement"}
    assert {item["kind"] for item in phases["cardano-solo"]} == {"measurement"}
    assert {item["kind"] for item in phases["mixed"]} == {"security"}


def test_measurement_using_existing_mixed_scenario_stays_in_mixed_phase(campaign_plan):
    manifest = deepcopy(campaign_plan)
    cell = deepcopy(manifest["required_tap_cells"][0])
    cell.update(
        runtime={"status": "implemented"},
        target_modes=["stock"],
        scenario_id="existing-mixed-scenario",
    )
    manifest["required_tap_cells"] = [cell]
    manifest["definitions"]["scenarios"]["existing-mixed-scenario"] = {
        "document": {
            "substrate": {"nodes": [
                {"id": "node1", "impl": "amaru"},
                {"id": "node2", "impl": "cardano-node"},
            ]}
        }
    }
    frozen(manifest)

    phases = api.phase_work_items(api.execution_work_items(manifest))

    assert {item["kind"] for item in phases["amaru-solo"]} == {"security"}
    assert {item["kind"] for item in phases["mixed"]} == {"measurement"}


def test_work_item_validation_rejects_tampering(campaign_plan):
    item = api.execution_work_items(campaign_plan)["items"][0]
    api.validate_work_item(item, [item])
    changed = deepcopy(item)
    changed["entry_id"] = "different"

    with pytest.raises(ValueError, match="digest"):
        api.validate_work_item(changed, [item])


@pytest.fixture
def campaign_plan():
    # Imported lazily to keep this module's public fixtures independent.
    from test_campaign_evidence import campaign_plan as fixture

    return fixture.__wrapped__()
