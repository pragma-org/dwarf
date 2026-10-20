import json
from dataclasses import replace

import pytest

from profile_manager.launch_store import create_launch
from profile_manager.run_plan import RunPlanError, RunPlanRequest, resolve_run_plan


AMARU = {
    "version": "10.11.20260918",
    "source_revision": "aedfe797a5b8ef00d8b362be40b47a52c3b4a379",
    "image": (
        "ghcr.io/pragma-org/amaru:v10.11.20260918@sha256:"
        "2176f12085219a4052be17dca89742954897eb9a5321554c799d0458913c9c60"
    ),
    "image_digest": (
        "sha256:2176f12085219a4052be17dca89742954897eb9a5321554c799d0458913c9c60"
    ),
}
CARDANO = {
    "version": "11.1.2",
    "source_revision": "fef83fed01d7926f3de83b3b917be5a4a48768b5",
    "image": (
        "ghcr.io/intersectmbo/cardano-node:11.1.2@sha256:"
        "6365403f44713d0a046865fb0466503ef207b71beae1b1ffece7f4399356db9f"
    ),
    "image_digest": (
        "sha256:6365403f44713d0a046865fb0466503ef207b71beae1b1ffece7f4399356db9f"
    ),
}
SCENARIOS = (
    ("runtime-substrate-mux-ingress-overrun-mixed-amaru", "amaru"),
    ("runtime-substrate-mempool-failure-containment-mixed-amaru", "cardano-node"),
    (
        "runtime-substrate-plutus-phase2-cek-divmod-differential-mixed-amaru",
        "cardano-node",
    ),
    (
        "runtime-substrate-plutus-phase2-cek-value-differential-mixed-amaru",
        "cardano-node",
    ),
    (
        "runtime-substrate-plutus-phase2-crypto-builtins-differential-mixed-amaru",
        "cardano-node",
    ),
    (
        "runtime-substrate-plutus-phase2-plutusdata-decode-differential-mixed-amaru",
        "cardano-node",
    ),
    (
        "runtime-substrate-txsubmission-unexpected-body-mixed-amaru",
        "cardano-node",
    ),
)


def _request(scenario_id):
    return RunPlanRequest(
        scenario_id=scenario_id,
        version_policy="exact",
        cardano_version=CARDANO["version"],
        amaru_version=AMARU["version"],
        acknowledge_unknown_versions=False,
        measurement_profile="none",
    )


@pytest.mark.parametrize(
    ("scenario_id", "target_implementation"),
    SCENARIOS,
    ids=(
        "mux-ingress",
        "mempool-containment",
        "plutus-phase2-divmod",
        "plutus-phase2-value",
        "plutus-phase2-crypto",
        "plutus-phase2-plutusdata",
        "txsubmission-unexpected-body",
    ),
)
def test_selected_pair_is_frozen_into_launch_provenance(
    tmp_path, scenario_id, target_implementation
):
    plan = resolve_run_plan(_request(scenario_id))
    launch = create_launch(plan, root=tmp_path / "launches")
    retained_plan = json.loads(
        launch["path"].joinpath("plan.json").read_text(encoding="utf-8")
    )
    frozen = json.loads(
        launch["path"].joinpath("scenario.yaml").read_text(encoding="utf-8")
    )
    nodes = {node["impl"]: node for node in frozen["substrate"]["nodes"]}
    expected = {"amaru": AMARU, "cardano-node": CARDANO}

    assert retained_plan["profile"] is None
    assert retained_plan["readiness"]["profile_required"] is False
    assert retained_plan["versions"]["selection_kind"] == "scenario-substrate"
    assert len(retained_plan["scenario"]["substrate_bindings"]) == 2
    if scenario_id == "runtime-substrate-mempool-failure-containment-mixed-amaru":
        assert frozen["substrate"]["compose_mode"] == "docker"
    assert all(
        binding["kind"] == "resolved-plan"
        for binding in retained_plan["scenario"]["substrate_bindings"]
    )
    assert frozen["target"] == {
        "implementation": target_implementation,
        "version": expected[target_implementation]["version"],
        "source_revision": expected[target_implementation]["source_revision"],
    }
    for implementation, identity in expected.items():
        assert {
            key: nodes[implementation][key]
            for key in ("version", "source_revision", "image", "image_digest")
        } == identity


@pytest.mark.parametrize(
    "scenario_id",
    [scenario_id for scenario_id, _target in SCENARIOS],
    ids=(
        "mux-ingress",
        "mempool-containment",
        "plutus-phase2-divmod",
        "plutus-phase2-value",
        "plutus-phase2-crypto",
        "plutus-phase2-plutusdata",
        "txsubmission-unexpected-body",
    ),
)
def test_unknown_selected_pair_requires_existing_acknowledgement(scenario_id):
    request = replace(
        _request(scenario_id),
        amaru_version="10.11.20260925",
        acknowledge_unknown_versions=False,
    )

    with pytest.raises(RunPlanError) as raised:
        resolve_run_plan(request)

    assert getattr(raised.value, "field", None) == "versions"
    assert raised.value.details["requires_acknowledgement"] is True
