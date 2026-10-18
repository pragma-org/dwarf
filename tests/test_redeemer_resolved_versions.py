import json
from dataclasses import replace

import pytest

from profile_manager import scenario as scenario_module
from profile_manager.dashboard import _prepare_run_plan
from profile_manager.launch_store import LaunchStoreError, materialized_scenario
from profile_manager.run_plan import (
    RunPlanError,
    RunPlanRequest,
    digest_run_plan,
    resolve_run_plan,
)
from profile_manager.submit_primitives import RuntimeRedeemerContainerDifferential
from scripts import runtime_redeemer_container_live_controls as live_controls


SCENARIO_ID = "ledger-submit-empty-redeemers-container-differential-amaru-cardano-node"


def _request():
    return RunPlanRequest(
        scenario_id=SCENARIO_ID,
        version_policy="exact",
        cardano_version="11.1.2",
        amaru_version="10.11.20260925",
        acknowledge_unknown_versions=True,
        measurement_profile="none",
    )


def test_substrate_versions_bind_from_existing_exact_release_selection():
    plan = resolve_run_plan(_request())

    assert plan["profile"] is None
    assert plan["versions"]["selection_kind"] == "scenario-substrate"
    assert plan["versions"]["status"] == "unknown"
    assert plan["versions"]["unknown_acknowledged"] is True
    assert plan["versions"]["resolved"]["amaru"]["version"] == "10.11.20260925"
    assert plan["versions"]["resolved"]["cardano-node"]["version"] == "11.1.2"
    assert plan["readiness"]["profile_required"] is False

    frozen = json.loads(materialized_scenario(plan))
    nodes = {node["impl"]: node for node in frozen["substrate"]["nodes"]}
    assert nodes["amaru"]["version"] == "10.11.20260925"
    assert nodes["amaru"]["source_revision"] == (
        "eaf8ac3fa309f2a174c045454c276194c9312b8d"
    )
    assert nodes["amaru"]["image"].endswith(
        "@sha256:ad95eabd49d29b04978763b94e3036ad727c5e53c06946eeea98bd706cbaaee7"
    )
    assert nodes["cardano-node"]["version"] == "11.1.2"
    assert nodes["cardano-node"]["source_revision"] == (
        "fef83fed01d7926f3de83b3b917be5a4a48768b5"
    )
    assert "expected_target_version" not in frozen["load"][0]
    assert frozen["target"]["version"] == "10.11.20260925"


def test_self_composed_version_selection_never_invokes_topology_actions():
    plan = resolve_run_plan(_request())

    class ForbiddenActions:
        def __getattr__(self, name):
            raise AssertionError(f"self-composed scenario invoked topology action {name}")

    events = list(
        _prepare_run_plan(
            plan,
            digest_run_plan(plan),
            action_service=ForbiddenActions(),
        )
    )

    assert [(event["stage"], event["state"]) for event in events] == [
        ("ready", "ready")
    ]
    assert events[-1]["profile_id"] is None
    assert events[-1]["topology_id"] is None
    assert all(event.get("stage") != "topology_preview" for event in events)


def test_stale_substrate_binding_is_rejected_before_launch_materialization():
    plan = resolve_run_plan(_request())
    plan["scenario"]["substrate_bindings"][0]["declared"]["role"] = "changed"

    with pytest.raises(LaunchStoreError, match="substrate binding"):
        materialized_scenario(plan)


def test_materialized_load_primitive_receives_resolved_node_identities(
    monkeypatch, tmp_path
):
    plan = resolve_run_plan(_request())
    path = tmp_path / "scenario.yaml"
    path.write_bytes(materialized_scenario(plan))
    loaded = scenario_module.load_scenario(path)
    load_ref = loaded.load[0]
    primitive = RuntimeRedeemerContainerDifferential(params=load_ref.params)
    assert hasattr(primitive, "bound_substrate")
    primitive.bound_substrate = loaded.substrate
    runtime_path = tmp_path / "run" / "outputs/substrate-compose/runtime.json"
    runtime_path.parent.mkdir(parents=True)
    runtime_path.write_text(json.dumps({"nodes": []}))
    captured = {}

    def fake_live_controls(**kwargs):
        captured.update(kwargs)
        return {"ledger_control_status": {"complete": True}}

    monkeypatch.setattr(
        live_controls, "run_fresh_funded_live_controls", fake_live_controls
    )

    class Handle:
        run_dir = tmp_path / "run"

    primitive.run(Handle(), None)

    assert captured["expected_target_version"] == "10.11.20260925"
    assert captured["expected_target_source"] == (
        "eaf8ac3fa309f2a174c045454c276194c9312b8d"
    )
    assert captured["expected_reference_version"] == "11.1.2"
    assert captured["expected_reference_source"] == (
        "fef83fed01d7926f3de83b3b917be5a4a48768b5"
    )


def test_unknown_pair_requires_acknowledgement_without_fallback():
    request = replace(_request(), acknowledge_unknown_versions=False)

    with pytest.raises(RunPlanError) as raised:
        resolve_run_plan(request)

    assert getattr(raised.value, "field", None) == "versions"
    assert raised.value.details["requires_acknowledgement"] is True


def test_same_scenario_resolves_an_alternate_confirmed_release_pair():
    request = replace(
        _request(),
        amaru_version="10.11.20260918",
        acknowledge_unknown_versions=False,
    )

    plan = resolve_run_plan(request)
    frozen = json.loads(materialized_scenario(plan))
    nodes = {node["impl"]: node for node in frozen["substrate"]["nodes"]}

    assert plan["versions"]["status"] == "confirmed"
    assert nodes["amaru"]["version"] == "10.11.20260918"
    assert nodes["amaru"]["source_revision"] == (
        "aedfe797a5b8ef00d8b362be40b47a52c3b4a379"
    )


def test_explicit_release_fields_use_exact_policy_without_scenario_pin():
    request = replace(_request(), version_policy=None)

    plan = resolve_run_plan(request)

    assert plan["versions"]["policy"] == "exact"
    assert plan["versions"]["resolved"]["amaru"]["version"] == "10.11.20260925"
