import json

import pytest

from profile_manager.launch_store import (
    LaunchStoreError,
    create_launch,
    load_launch,
    retain_launch_inputs,
)
from profile_manager.run_plan import (
    RunPlanError,
    RunPlanRequest,
    resolve_run_plan,
)


def test_measurement_scenario_resolves_exact_profile_versions_and_taps():
    plan = resolve_run_plan(
        RunPlanRequest(scenario_id="amaru-measurement-e2e-stock")
    )

    assert plan["scenario"]["id"] == "amaru-measurement-e2e-stock"
    assert plan["scenario"]["runtime"] == "devnet"
    assert plan["profile"]["id"] == "profile-r-amaru-measurement-stock-control"
    assert plan["versions"]["policy"] == "exact"
    assert plan["versions"]["resolved"]["amaru"]["version"] == "10.11.20260912"
    assert plan["measurements"]["profile"]["id"] == "amaru-security-default"
    assert plan["measurements"]["resolved"]
    assert "runtime_amaru_measurement_calibration" in plan["primitive_names"]
    assert plan["readiness"]["mixed_topology_required"] is False
    assert plan["readiness"]["profile_required"] is True


def test_attached_scenario_requires_only_its_named_topology():
    plan = resolve_run_plan(
        RunPlanRequest(scenario_id="consensus-chainhold-upstream-differential")
    )

    assert plan["readiness"]["mixed_topology_required"] is True
    assert plan["readiness"]["topology_id"] == "cardano_amaru"
    assert plan["readiness"]["profile_required"] is False


def test_profile_scenario_uses_latest_confirmed_and_implicit_measurements():
    plan = resolve_run_plan(
        RunPlanRequest(scenario_id="m3-runtime-blockfetch-multi-peer-historical-range")
    )

    assert plan["versions"]["policy"] == "latest-confirmed"
    assert plan["versions"]["status"] == "confirmed"
    assert plan["measurements"]["profile"]["id"] == "cardano-security-default"
    assert plan["measurements"]["profile"]["source"] == "implicit-default"
    assert plan["scenario"]["seed"] == "0xC0DE3604"
    assert plan["scenario"]["iterations"] is None


def test_library_scenario_does_not_require_mixed_topology_or_profile():
    plan = resolve_run_plan(
        RunPlanRequest(scenario_id="edge-cases-cbor-tx-body-amaru")
    )

    assert plan["scenario"]["runtime"] == "library"
    assert plan["profile"] is None
    assert plan["readiness"]["mixed_topology_required"] is False
    assert plan["readiness"]["profile_required"] is False


@pytest.mark.parametrize(
    "body,field",
    [
        ({"scenario_id": "../escape"}, "scenario_id"),
        ({"scenario_id": "edge-cases-cbor-tx-body-amaru", "command": "id"}, "request"),
        ({"scenario_id": "edge-cases-cbor-tx-body-amaru", "runtime": "simulator"}, "runtime"),
    ],
)
def test_request_rejects_paths_commands_unknown_fields_and_unsupported_runtime(body, field):
    with pytest.raises(RunPlanError) as raised:
        RunPlanRequest.from_mapping(body)

    assert raised.value.field == field


def test_incompatible_profile_is_rejected():
    with pytest.raises(RunPlanError) as raised:
        resolve_run_plan(
            RunPlanRequest(
                scenario_id="amaru-measurement-e2e-stock",
                profile_id="profile-s-cardano-measurement-stock-control",
            )
        )

    assert raised.value.field == "profile_id"


def test_unknown_exact_version_requires_explicit_acknowledgement():
    with pytest.raises(RunPlanError) as raised:
        resolve_run_plan(
            RunPlanRequest(
                scenario_id="cardano-measurement-e2e-stock",
                version_policy="exact",
                cardano_version="11.1.1",
            )
        )

    assert raised.value.field == "versions"
    assert "acknowledgement" in str(raised.value).lower()


@pytest.mark.parametrize(
    "scenario_body,expected",
    [
        (
            {
                "spec_version": "v1",
                "id": "bad-primitive",
                "title": "Bad primitive",
                "target": {"implementation": "cardano-node", "version": "any"},
                "runtime": "library",
                "load": [{"primitive": "not_registered"}],
                "assertions": [],
            },
            "not present in the primitive registry",
        ),
        (
            {
                "spec_version": "v1",
                "id": "broken-producer",
                "title": "Broken producer",
                "target": {"implementation": "cardano-node", "version": "any"},
                "runtime": "library",
                "load": [],
                "assertions": [{"primitive": "all_nodes_responsive"}],
            },
            "requires one of",
        ),
    ],
)
def test_semantically_invalid_scenarios_fail_before_launch(
    tmp_path, monkeypatch, scenario_body, expected
):
    scenario_path = tmp_path / f"{scenario_body['id']}.yaml"
    scenario_path.write_text(json.dumps(scenario_body), encoding="utf-8")
    monkeypatch.setenv("ADA2_DWARF_SCENARIOS_DIR", str(tmp_path))

    with pytest.raises(RunPlanError) as raised:
        resolve_run_plan(RunPlanRequest(scenario_id=scenario_body["id"]))

    assert expected in str(raised.value)


def test_launch_store_writes_immutable_materialized_inputs(tmp_path):
    plan = resolve_run_plan(
        RunPlanRequest(scenario_id="m3-runtime-blockfetch-multi-peer-historical-range")
    )

    stored = create_launch(plan, root=tmp_path)
    loaded = load_launch(stored["launch_id"], root=tmp_path)

    assert stored["launch_id"].startswith("launch-")
    assert len(stored["launch_id"]) == len("launch-") + 24
    assert loaded["plan"]["scenario"]["id"] == plan["scenario"]["id"]
    scenario = json.loads(loaded["scenario_path"].read_text(encoding="utf-8"))
    assert scenario["profile"] == plan["profile"]["id"]
    assert scenario["target"] == plan["scenario"]["target"]
    assert scenario["measurement_profile"] == "cardano-security-default"
    assert loaded["plan_path"].stat().st_mode & 0o777 == 0o600
    assert loaded["scenario_path"].stat().st_mode & 0o777 == 0o600


def test_launch_store_rejects_traversal_and_tampering(tmp_path):
    plan = resolve_run_plan(
        RunPlanRequest(scenario_id="edge-cases-cbor-tx-body-amaru")
    )
    stored = create_launch(plan, root=tmp_path)

    with pytest.raises(LaunchStoreError):
        load_launch("../" + stored["launch_id"], root=tmp_path)

    scenario_path = tmp_path / stored["launch_id"] / "scenario.yaml"
    scenario_path.write_text("{}", encoding="utf-8")
    with pytest.raises(LaunchStoreError, match="digest"):
        load_launch(stored["launch_id"], root=tmp_path)


def test_launch_inputs_are_retained_in_run_evidence(tmp_path):
    launch_root = tmp_path / "launches"
    run_dir = tmp_path / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    stored = create_launch(
        resolve_run_plan(
            RunPlanRequest(scenario_id="edge-cases-cbor-tx-body-amaru")
        ),
        root=launch_root,
    )

    retained = retain_launch_inputs(
        stored["launch_id"], run_dir=run_dir, root=launch_root
    )

    assert retained == run_dir / "launch"
    assert (retained / "plan.json").is_file()
    assert (retained / "scenario.yaml").is_file()
