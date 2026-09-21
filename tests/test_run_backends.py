from pathlib import Path

from profile_manager import scenario as scenario_module
from profile_manager.run_backends import classify_run_backends
from profile_manager.run_plan import RunPlanRequest, resolve_run_plan


ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "dwarf" / "scenarios"


def _scenario(scenario_id):
    return scenario_module.load_scenario(SCENARIOS / f"{scenario_id}.yaml")


def test_local_backend_is_supported_for_all_real_dwarf_runtimes():
    for scenario_id in (
        "edge-cases-cbor-tx-body-amaru",
        "runtime-substrate-honest-baseline-docker-mode-example-smoke",
        "consensus-chainhold-upstream-differential",
    ):
        local = classify_run_backends(_scenario(scenario_id))["local"]
        assert local["state"] == "supported-unconfirmed"
        assert local["action"] == "start-local"


def test_local_backend_can_be_promoted_only_by_retained_confirmation():
    local = classify_run_backends(
        _scenario("edge-cases-cbor-tx-body-amaru"),
        local_confirmed=True,
    )["local"]

    assert local["state"] == "confirmed"
    assert "retained" in local["reason"].lower()


def test_github_actions_maps_only_self_provisioning_cardano_devnets():
    supported = classify_run_backends(
        _scenario("runtime-substrate-honest-baseline-docker-mode-example-smoke")
    )["github-actions"]
    attached = classify_run_backends(
        _scenario("consensus-chainhold-upstream-differential")
    )["github-actions"]
    library = classify_run_backends(
        _scenario("edge-cases-cbor-tx-body-cardano-node")
    )["github-actions"]

    assert supported["state"] == "supported-unconfirmed"
    assert supported["workflow"] == "dwarf-devnet-smoke.yml"
    assert supported["inputs"] == {
        "scenarios": "dwarf/scenarios/runtime-substrate-honest-baseline-docker-mode-example-smoke.yaml"
    }
    assert attached["state"] == "unsupported"
    assert library["state"] == "unsupported"


def test_antithesis_support_comes_from_the_real_generator_contract():
    supported = classify_run_backends(
        _scenario("cardano-node-cbor-tx-body-fuzz-structured")
    )["antithesis"]
    unsupported = classify_run_backends(
        _scenario("amaru-cbor-tx-body-fuzz")
    )["antithesis"]

    assert supported["state"] == "supported-unconfirmed"
    assert supported["action"] == "prepare-antithesis"
    assert unsupported["state"] == "unsupported"
    assert "not supported" in unsupported["reason"].lower()


def test_resolved_plan_carries_backend_capabilities():
    plan = resolve_run_plan(
        RunPlanRequest(
            scenario_id="runtime-substrate-honest-baseline-docker-mode-example-smoke"
        )
    )

    assert plan["backends"]["local"]["action"] == "start-local"
    assert plan["backends"]["github-actions"]["workflow"] == "dwarf-devnet-smoke.yml"
