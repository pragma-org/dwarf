import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

import profile_manager.run_plan as run_plan_module
from profile_manager.launch_store import create_launch
from profile_manager.run_plan import (
    RunPlanError,
    RunPlanRequest,
    resolve_run_plan,
)


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


def test_inline_substrate_forwards_selected_operator_registration(monkeypatch):
    selected = "registration-release-build-selected-stock"

    def preview(profile_data, *, operator_registration_id=None, **_kwargs):
        assert operator_registration_id == selected
        return {
            "policy": "exact",
            "policy_source": "explicit",
            "scope": "mixed",
            "status": "unknown",
            "reason": "operator-selected local Amaru",
            "catalog_revision": "c" * 64,
            "operator_overlay_revision": "d" * 64,
            "operator_registration_id": selected,
            "pair": None,
            "requires_acknowledgement": True,
            "resolved": {
                "amaru": {
                    "implementation": "amaru",
                    "version": AMARU["version"],
                    "source_revision": AMARU["source_revision"],
                    "artifacts": [{
                        "kind": "oci",
                        "availability": "available",
                        "reference": AMARU["image"],
                        "digest": AMARU["image_digest"],
                        "platforms": ["linux/amd64"],
                        "public": False,
                    }],
                },
                "cardano-node": {
                    "implementation": "cardano-node",
                    "version": CARDANO["version"],
                    "source_revision": CARDANO["source_revision"],
                    "artifacts": [{
                        "kind": "oci",
                        "availability": "available",
                        "reference": CARDANO["image"],
                        "digest": CARDANO["image_digest"],
                        "platforms": ["linux/amd64"],
                        "public": True,
                    }],
                },
            },
            "supporting": {},
        }

    monkeypatch.setattr(run_plan_module, "build_deployment_version_preview", preview)
    plan = resolve_run_plan(
        RunPlanRequest(
            scenario_id="runtime-substrate-mux-ingress-overrun-mixed-amaru",
            operator_registration_id=selected,
            acknowledge_unknown_versions=True,
            measurement_profile="none",
        )
    )

    assert plan["versions"]["operator_registration_id"] == selected


def test_inline_substrate_version_scope_excludes_supporting_producer():
    scenario = SimpleNamespace(
        id="latest-amaru-security-regression",
        title="Latest Amaru security regression",
        substrate={
            "network": "testnet_42",
            "nodes": [
                {
                    "id": "amaru1",
                    "impl": "amaru",
                    "version": "resolved-plan",
                    "identity_binding": "resolved-plan",
                    "role": "submitter",
                },
                {
                    "id": "producer1",
                    "impl": "cardano-node",
                    "version": "resolved-plan",
                    "identity_binding": "resolved-plan",
                    "role": "producer",
                    "supporting": True,
                },
            ],
        },
    )

    profile = run_plan_module._scenario_substrate_version_profile(
        scenario, RunPlanRequest(scenario_id=scenario.id)
    )

    assert profile["amaru_count"] == 1
    assert profile["haskell_count"] == 0


def test_inline_substrate_materializes_local_config_identity(monkeypatch, tmp_path):
    config_id = "sha256:" + "9" * 64
    executable_digest = "sha256:" + "8" * 64
    selected = "registration-release-build-selected-stock"
    installation = "install-selected"
    overlay_revision = "d" * 64
    registration = {
        "registration_id": selected,
        "implementation": "amaru",
        "version": "10.11.20261029",
        "image_reference": config_id,
        "manifest_digest": None,
        "config_image_id": config_id,
        "executable_digest": executable_digest,
        "platform": "linux/amd64",
        "runtime_interface": "extracted-binary",
        "controller_installation_id": installation,
        "inspected_at": "2026-10-31T06:04:45Z",
        "provenance": {
            "source_revision": "23031e0ed4423b9cb899da030d515cb69d203749",
            "public": None,
            "qualification": "unverified",
        },
    }

    def preview(_profile_data, *, operator_registration_id=None, **_kwargs):
        assert operator_registration_id == selected
        local_amaru = {
            "implementation": "amaru",
            "version": registration["version"],
            "source_revision": registration["provenance"]["source_revision"],
            "artifacts": [{
                "kind": "oci",
                "availability": "available",
                "reference": config_id,
                "digest": None,
                "config_image_id": config_id,
                "executable_digest": executable_digest,
                "platforms": [registration["platform"]],
                "public": False,
                "runtime_interface": registration["runtime_interface"],
                "operator_registration": registration,
            }],
            "operator_registration": registration,
        }
        registry_cardano = {
            "implementation": "cardano-node",
            "version": CARDANO["version"],
            "source_revision": CARDANO["source_revision"],
            "artifacts": [{
                "kind": "oci",
                "availability": "available",
                "reference": CARDANO["image"],
                "digest": CARDANO["image_digest"],
                "platforms": ["linux/amd64"],
                "public": True,
            }],
        }
        return {
            "policy": "exact",
            "policy_source": "explicit",
            "scope": "mixed",
            "status": "unknown",
            "reason": "operator-selected local Amaru",
            "catalog_revision": "c" * 64,
            "operator_overlay_revision": overlay_revision,
            "operator_registration_id": selected,
            "pair": None,
            "requires_acknowledgement": True,
            "resolved": {"amaru": local_amaru, "cardano-node": registry_cardano},
            "supporting": {},
        }

    monkeypatch.setattr(run_plan_module, "build_deployment_version_preview", preview)
    plan = resolve_run_plan(
        RunPlanRequest(
            scenario_id="runtime-substrate-mux-ingress-overrun-mixed-amaru",
            operator_registration_id=selected,
            acknowledge_unknown_versions=True,
            measurement_profile="none",
        )
    )
    binding = next(
        item
        for item in plan["scenario"]["substrate_bindings"]
        if item["declared"]["impl"] == "amaru"
    )
    assert binding["resolved"] == {
        "version": registration["version"],
        "source_revision": registration["provenance"]["source_revision"],
        "image": config_id,
        "image_config_id": config_id,
        "executable_digest": executable_digest,
        "platform": registration["platform"],
        "runtime_interface": registration["runtime_interface"],
        "operator_registration_id": selected,
        "controller_installation_id": installation,
        "operator_overlay_revision": overlay_revision,
    }
    launch = create_launch(plan, root=tmp_path / "launches")
    frozen = json.loads(launch["path"].joinpath("scenario.yaml").read_text())
    amaru = next(node for node in frozen["substrate"]["nodes"] if node["impl"] == "amaru")
    assert amaru["image"] == config_id
    assert amaru["image_config_id"] == config_id
    assert "image_digest" not in amaru


def test_inline_substrate_prefers_operator_config_identity_when_manifest_exists(
    monkeypatch, tmp_path
):
    config_id = "sha256:" + "9" * 64
    manifest_digest = "sha256:" + "7" * 64
    executable_digest = "sha256:" + "8" * 64
    selected = "registration-release-build-selected-stock"
    installation = "install-selected"
    registration = {
        "registration_id": selected,
        "implementation": "amaru",
        "version": "10.11.20261029",
        "image_reference": "local/amaru:stock",
        "manifest_digest": manifest_digest,
        "config_image_id": config_id,
        "executable_digest": executable_digest,
        "platform": "linux/amd64",
        "runtime_interface": "extracted-binary",
        "controller_installation_id": installation,
        "inspected_at": "2026-10-31T06:04:45Z",
        "provenance": {
            "source_revision": "23031e0ed4423b9cb899da030d515cb69d203749",
            "public": None,
            "qualification": "unverified",
        },
    }

    def preview(_profile_data, *, operator_registration_id=None, **_kwargs):
        assert operator_registration_id == selected
        release = {
            "implementation": "amaru",
            "version": registration["version"],
            "source_revision": registration["provenance"]["source_revision"],
            "artifacts": [{
                "kind": "oci",
                "availability": "available",
                "reference": registration["image_reference"],
                "digest": manifest_digest,
                "config_image_id": config_id,
                "executable_digest": executable_digest,
                "platforms": [registration["platform"]],
                "public": False,
                "runtime_interface": registration["runtime_interface"],
                "operator_registration": registration,
            }],
            "operator_registration": registration,
        }
        cardano_release = {
            "implementation": "cardano-node",
            "version": CARDANO["version"],
            "source_revision": CARDANO["source_revision"],
            "artifacts": [{
                "kind": "oci",
                "availability": "available",
                "reference": CARDANO["image"],
                "digest": CARDANO["image_digest"],
                "platforms": ["linux/amd64"],
                "public": True,
            }],
        }
        return {
            "policy": "exact",
            "policy_source": "explicit",
            "scope": "mixed",
            "status": "unknown",
            "reason": "operator-selected local Amaru",
            "catalog_revision": "c" * 64,
            "operator_overlay_revision": "d" * 64,
            "operator_registration_id": selected,
            "pair": None,
            "requires_acknowledgement": True,
            "resolved": {"amaru": release, "cardano-node": cardano_release},
            "supporting": {},
        }

    monkeypatch.setattr(run_plan_module, "build_deployment_version_preview", preview)
    plan = resolve_run_plan(
        RunPlanRequest(
            scenario_id="runtime-substrate-mux-ingress-overrun-mixed-amaru",
            operator_registration_id=selected,
            acknowledge_unknown_versions=True,
            measurement_profile="none",
        )
    )
    binding = next(
        item
        for item in plan["scenario"]["substrate_bindings"]
        if item["declared"]["impl"] == "amaru"
    )
    assert binding["resolved"]["image"] == config_id
    assert binding["resolved"]["image_config_id"] == config_id
    assert "image_digest" not in binding["resolved"]
    launch = create_launch(plan, root=tmp_path / "launches")
    frozen = json.loads(launch["path"].joinpath("scenario.yaml").read_text())
    amaru = next(node for node in frozen["substrate"]["nodes"] if node["impl"] == "amaru")
    assert amaru["image"] == config_id
    assert amaru["image_config_id"] == config_id
    assert "image_digest" not in amaru


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
        assert frozen["substrate"]["network"] == "testnet_42"
        observation = next(
            step
            for step in frozen["load"]
            if step["primitive"] == "runtime_multi_node_observation"
        )
        assert observation["network_magic"] == 42
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
