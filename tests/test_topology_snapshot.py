from __future__ import annotations

import hashlib
import json
import subprocess
from types import SimpleNamespace

import pytest

from profile_manager import profiles as profiles_module
from profile_manager import topology_snapshot as topology_snapshot_module
from profile_manager.data import health as health_data
from profile_manager.data import operate_topology_health
from profile_manager import operator_version_decisions as operator_decisions
from profile_manager.operator_version_decisions import OperatorVersionDecisionLedger
from profile_manager.profiles import Profile, desired_topology_for_profile
from profile_manager.topology_snapshot import build_topology_snapshot


NOW = "2026-10-08T07:00:00Z"
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64
IMAGE_A = "ghcr.io/example/cardano-node@" + DIGEST_A
IMAGE_B = "ghcr.io/example/amaru@" + DIGEST_B
IMAGE_C = "ghcr.io/example/amaru@" + DIGEST_C


def _desired() -> dict:
    return {
        "schema_version": "v1",
        "topology_id": "local-lab",
        "profile_id": "local-lab",
        "plan_sha256": "sha256:" + "c" * 64,
        "nodes": [
            {
                "id": "node-1",
                "implementation": "cardano-node",
                "version": "10.7.1",
                "image": IMAGE_A,
            },
            {
                "id": "amaru-1",
                "implementation": "amaru",
                "version": "0.3.0",
                "image": IMAGE_B,
            },
        ],
    }


def _managed(node_id: str, *, image: str | None = None, state: str = "running") -> dict:
    desired = next(node for node in _desired()["nodes"] if node["id"] == node_id)
    return {
        "id": node_id + "-container-id",
        "name": "dwarf-install-local-lab-" + node_id,
        "topology_id": "local-lab",
        "node_id": node_id,
        "implementation": desired["implementation"],
        "version": desired["version"],
        "image": image or desired["image"],
        "state": state,
    }


def _health(state: str = "healthy", checked_at: str = "2026-10-08T06:59:55Z") -> dict:
    return {
        "state": state,
        "reason_code": "proof-" + state,
        "checked_at": checked_at,
    }


def _snapshot(
    *,
    managed: list[dict] | None = None,
    unmanaged: list[dict] | None = None,
    health: dict | None = None,
    persisted: dict | None = None,
) -> dict:
    return build_topology_snapshot(
        desired=_desired(),
        persisted=persisted,
        managed=managed or [],
        unmanaged=unmanaged or [],
        health=health,
        generated_at=NOW,
        health_max_age_seconds=30,
    )


def test_absent_topology_has_one_safe_deploy_path():
    result = _snapshot()

    assert result["schema_version"] == "v1"
    assert result["reconciliation"]["state"] == "absent"
    assert result["reconciliation"]["missing_node_ids"] == ["amaru-1", "node-1"]
    assert result["actions"]["deploy"]["allowed"] is True
    assert result["actions"]["repair"]["allowed"] is False
    assert result["actions"]["save"]["allowed"] is False
    assert result["actions"]["remove"]["allowed"] is False


def test_converged_topology_requires_matching_persisted_managed_and_fresh_health():
    desired = _desired()
    result = _snapshot(
        managed=[_managed("node-1"), _managed("amaru-1")],
        persisted={"topology_id": "local-lab", "nodes": desired["nodes"]},
        health=_health(),
    )

    assert result["reconciliation"]["state"] == "converged"
    assert result["health"]["fresh"] is True
    assert result["health"]["age_seconds"] == 5
    assert result["actions"]["deploy"]["allowed"] is False
    assert result["actions"]["save"]["allowed"] is True
    assert result["actions"]["remove"] == {
        "allowed": True,
        "reason": "remove requires managed resources",
        "confirmation_required": True,
    }
    assert all(row["ownership"] == "managed" for row in result["observed"]["managed"])


def test_unhealthy_topology_is_not_reported_as_converged():
    result = _snapshot(
        managed=[_managed("node-1"), _managed("amaru-1")],
        persisted={"topology_id": "local-lab", "nodes": _desired()["nodes"]},
        health=_health("unhealthy"),
    )

    assert result["reconciliation"]["state"] == "unhealthy"
    assert result["actions"]["repair"]["allowed"] is True
    assert result["actions"]["redeploy"]["confirmation_required"] is True


def test_bounded_chain_catchup_is_operational_without_authorizing_repair():
    result = _snapshot(
        managed=[_managed("node-1"), _managed("amaru-1")],
        persisted={"topology_id": "local-lab", "nodes": _desired()["nodes"]},
        health=_health("catching_up"),
    )

    assert result["state"] == "healthy"
    assert result["health"]["state"] == "catching_up"
    assert result["reconciliation"]["state"] == "converged"
    assert result["actions"]["repair"]["allowed"] is False


def test_unknown_health_evidence_does_not_authorize_repair():
    result = _snapshot(
        managed=[_managed("node-1"), _managed("amaru-1")],
        persisted={"topology_id": "local-lab", "nodes": _desired()["nodes"]},
        health=_health("unknown"),
    )

    assert result["state"] == "unknown"
    assert result["reconciliation"]["state"] == "unhealthy"
    assert result["actions"]["repair"]["allowed"] is False


def test_stopped_managed_resource_is_unhealthy_even_with_healthy_probe():
    result = _snapshot(
        managed=[_managed("node-1", state="exited"), _managed("amaru-1")],
        persisted={"topology_id": "local-lab", "nodes": _desired()["nodes"]},
        health=_health(),
    )

    assert result["reconciliation"]["state"] == "unhealthy"
    assert result["reconciliation"]["reason_code"] == "managed_resources_not_running"
    assert result["reconciliation"]["not_running_node_ids"] == ["node-1"]


def test_drifted_topology_names_the_exact_identity_mismatch():
    result = _snapshot(
        managed=[_managed("node-1", image=IMAGE_B), _managed("amaru-1")],
        persisted={"topology_id": "local-lab", "nodes": _desired()["nodes"]},
        health=_health(),
    )

    assert result["reconciliation"]["state"] == "drifted"
    assert result["reconciliation"]["mismatched_nodes"] == [
        {
            "node_id": "node-1",
            "fields": ["image"],
            "desired": {"image": IMAGE_A},
            "observed": {"image": IMAGE_B},
        }
    ]


def test_persisted_plan_must_match_profile_and_network_not_only_nodes():
    persisted = {
        "substrate": {
            "profile_id": "different-profile",
            "network_magic": 764824073,
            "deployment_adapter": "docker",
            "nodes": _desired()["nodes"],
        }
    }
    desired = {**_desired(), "network_magic": 42, "deployment_adapter": "docker"}
    result = build_topology_snapshot(
        desired=desired,
        persisted=persisted,
        managed=[_managed("node-1"), _managed("amaru-1")],
        unmanaged=[],
        health=_health(),
        generated_at=NOW,
    )

    assert result["reconciliation"]["state"] == "drifted"
    assert result["reconciliation"]["reason_code"] == "persisted_plan_drift"
    assert result["reconciliation"]["persisted_mismatched_fields"] == [
        "network_magic",
        "profile_id",
    ]


def test_partially_deployed_topology_names_missing_nodes():
    result = _snapshot(managed=[_managed("node-1")], health=_health())

    assert result["reconciliation"]["state"] == "partially_deployed"
    assert result["reconciliation"]["missing_node_ids"] == ["amaru-1"]
    assert result["actions"]["repair"]["allowed"] is True


def test_unmanaged_visible_resources_are_read_only_without_masking_convergence():
    unmanaged = {
        "id": "outside-id",
        "name": "someone-elses-cardano-node",
        "image": "cardano-node:latest",
        "state": "running",
        "node_id": "",
        "implementation": "",
        "version": "",
        "topology_id": "",
    }
    result = _snapshot(
        managed=[_managed("node-1"), _managed("amaru-1")],
        unmanaged=[unmanaged],
        persisted={"topology_id": "local-lab", "nodes": _desired()["nodes"]},
        health=_health(),
    )

    assert result["reconciliation"]["state"] == "converged"
    assert result["unmanaged_visibility"]["state"] == "visible"
    row = result["observed"]["unmanaged"][0]
    assert row["ownership"] == "unmanaged"
    assert row["mutation_allowed"] is False
    assert row["allowed_actions"] == ["inspect"]


def test_unmanaged_identity_collision_blocks_deploy_but_remains_read_only():
    collision = {
        **_managed("node-1"),
        "id": "foreign-container",
        "ownership": "unmanaged",
        "mutation_allowed": False,
    }
    result = _snapshot(unmanaged=[collision])

    assert result["reconciliation"]["state"] == "absent"
    assert result["reconciliation"]["unmanaged_conflict_node_ids"] == ["node-1"]
    assert result["actions"]["deploy"]["allowed"] is False
    assert result["actions"]["deploy"]["reason"] == "unmanaged resource collision"


def test_stale_health_evidence_cannot_prove_convergence():
    result = _snapshot(
        managed=[_managed("node-1"), _managed("amaru-1")],
        persisted={"topology_id": "local-lab", "nodes": _desired()["nodes"]},
        health=_health(checked_at="2026-10-08T06:00:00Z"),
    )

    assert result["reconciliation"]["state"] == "unhealthy"
    assert result["health"]["fresh"] is False
    assert result["reconciliation"]["reason_code"] == "health_evidence_stale"
    assert result["actions"]["repair"]["allowed"] is False


def test_aged_unhealthy_snapshot_revokes_every_mutation_action():
    snapshot = _snapshot(
        managed=[_managed("node-1"), _managed("amaru-1")],
        persisted={"topology_id": "local-lab", "nodes": _desired()["nodes"]},
        health=_health("unhealthy"),
    )
    assert snapshot["actions"]["repair"]["allowed"] is True

    presented = topology_snapshot_module.refresh_snapshot_freshness(
        snapshot, now="2026-10-08T07:01:00Z"
    )

    assert presented["state"] == "unknown"
    assert presented["reason_code"] == "health_evidence_stale"
    assert presented["reconciliation"]["state"] == "unhealthy"
    assert presented["reconciliation"]["stale_previous_state"] == "unhealthy"
    assert all(
        not action["allowed"]
        for name, action in presented["actions"].items()
        if name != "health"
    )


def test_cached_converged_snapshot_ages_out_instead_of_staying_green():
    snapshot = _snapshot(
        managed=[_managed("node-1"), _managed("amaru-1")],
        persisted={"topology_id": "local-lab", "nodes": _desired()["nodes"]},
        health=_health(),
    )

    presented = topology_snapshot_module.refresh_snapshot_freshness(
        snapshot, now="2026-10-08T07:01:00Z"
    )

    assert snapshot["reconciliation"]["state"] == "converged"
    assert presented["state"] == "unknown"
    assert presented["reason_code"] == "health_evidence_stale"
    assert presented["health"]["fresh"] is False
    assert presented["health"]["age_seconds"] == 65
    assert presented["reconciliation"]["state"] == "unhealthy"
    assert presented["actions"]["repair"]["allowed"] is False


def test_duplicate_managed_node_identity_is_drift_not_convergence():
    result = _snapshot(
        managed=[_managed("node-1"), _managed("node-1"), _managed("amaru-1")],
        persisted={"topology_id": "local-lab", "nodes": _desired()["nodes"]},
        health=_health(),
    )

    assert result["reconciliation"]["state"] == "drifted"
    assert result["reconciliation"]["reason_code"] == "duplicate_managed_identity"
    assert result["reconciliation"]["duplicate_managed_node_ids"] == ["node-1"]


def test_persisted_topology_edges_participate_in_plan_identity():
    desired = {
        **_desired(),
        "topology": {
            "edges": [
                {"from": "node-1", "to": "amaru-1"},
                {"from": "amaru-1", "to": "node-1"},
            ]
        },
    }
    persisted = {
        "topology_id": "local-lab",
        "nodes": desired["nodes"],
        "topology": {"edges": [{"from": "node-1", "to": "amaru-1"}]},
    }

    result = build_topology_snapshot(
        desired=desired,
        persisted=persisted,
        managed=[_managed("node-1"), _managed("amaru-1")],
        unmanaged=[],
        health=_health(),
        generated_at=NOW,
    )

    assert result["reconciliation"]["state"] == "drifted"
    assert result["reconciliation"]["reason_code"] == "persisted_plan_drift"
    assert result["reconciliation"]["persisted_mismatched_fields"] == ["topology"]


def test_profile_adapter_emits_digest_pinned_desired_plan():
    profile = Profile.from_dict(
        {
            "id": "profile-a",
            "label": "Profile A",
            "node_type": "cardano",
            "node_count": 1,
            "amaru_node_count": 0,
            "network_magic": 42,
            "peer_sharing": False,
        }
    )
    release = {
        "implementation": "cardano-node",
        "version": "10.7.1",
        "source_revision": "d" * 40,
        "artifacts": [
            {
                "kind": "oci",
                "availability": "available",
                "reference": "ghcr.io/example/cardano-node",
                "digest": DIGEST_A,
            }
        ],
    }
    preview = {
        "scope": "cardano-only",
        "resolved": {"cardano-node": release},
        "supporting": {},
        "catalog_snapshot": {},
        "catalog_revision": "catalog-sha",
        "policy": "latest-confirmed",
        "policy_source": "explicit",
        "status": "confirmed",
    }

    result = desired_topology_for_profile(profile, preview)

    assert result["schema_version"] == "v1"
    assert result["topology_id"] == "profile-a"
    assert result["network_magic"] == 42
    assert result["nodes"] == [
        {
            "id": "node1",
            "implementation": "cardano-node",
            "version": "10.7.1",
            "image": IMAGE_A,
            "role": "producer",
            "supporting": False,
        }
    ]
    assert result["topology"] == {"edges": []}
    assert result["plan_sha256"].startswith("sha256:")
    json.dumps(result)


def test_amaru_trace_mode_is_part_of_exact_desired_topology_identity():
    base = {
        "id": "profile-amaru-traces",
        "label": "Amaru trace identity",
        "node_type": "amaru",
        "node_count": 0,
        "amaru_node_count": 1,
        "network_magic": 42,
        "peer_sharing": False,
        "version_policy": "exact",
        "amaru_version": "10.11.20260918",
    }
    release = {
        "implementation": "amaru",
        "version": "10.11.20260918",
        "source_revision": "a" * 40,
        "artifacts": [
            {
                "kind": "oci",
                "availability": "available",
                "reference": "ghcr.io/example/amaru",
                "digest": DIGEST_A,
            }
        ],
    }
    cardano = {
        "implementation": "cardano-node",
        "version": "10.7.1",
        "source_revision": "b" * 40,
        "artifacts": [
            {
                "kind": "oci",
                "availability": "available",
                "reference": "ghcr.io/example/cardano-node",
                "digest": DIGEST_B,
            }
        ],
    }
    preview = {
        "scope": "amaru-only",
        "resolved": {"amaru": release},
        "supporting": {"cardano-node": cardano},
        "catalog_snapshot": {},
        "catalog_revision": "catalog-sha",
        "policy": "exact",
        "policy_source": "explicit",
        "status": "confirmed",
    }

    without_traces = desired_topology_for_profile(Profile.from_dict(base), preview)
    with_traces = desired_topology_for_profile(
        Profile.from_dict({**base, "amaru_json_traces": True}),
        preview,
    )

    assert without_traces["amaru_json_traces"] is False
    assert with_traces["amaru_json_traces"] is True
    assert without_traces["plan_sha256"] != with_traces["plan_sha256"]


def test_persisted_deployment_probe_is_bounded_and_round_trips_json():
    command = profiles_module.persisted_deployment_command("/safe/runtime")
    assert "versioned-deployment.json" in command
    assert "1048576" in command
    body = "noise\nDWARF_PERSISTED_DEPLOYMENT=" + json.dumps(
        {"substrate": {"profile_id": "profile-a", "nodes": []}}
    )

    assert health_data.persisted_deployment_from_body(body) == {
        "substrate": {"profile_id": "profile-a", "nodes": []}
    }
    assert health_data.persisted_deployment_from_body(
        "DWARF_PERSISTED_DEPLOYMENT=null"
    ) is None


def test_health_and_persisted_probe_preserves_original_health_exit(tmp_path):
    command = profiles_module.health_with_persisted_deployment_command(
        "printf 'HEALTH_FAILED\\n'; exit 23", str(tmp_path)
    )

    result = subprocess.run(
        ["bash", "-c", command], capture_output=True, text=True, check=False
    )

    assert result.returncode == 23
    assert "HEALTH_FAILED" in result.stdout
    assert "DWARF_PERSISTED_DEPLOYMENT=null" in result.stdout


def test_current_snapshot_joins_health_persisted_and_controller_inventory():
    first = {
        "enabled": True,
        "profile_id": "local-lab",
        "persisted_deployment": {
            "topology_id": "local-lab",
            "nodes": _desired()["nodes"],
        },
        "health": {"returncode": 0, "parsed": {}},
    }
    health = _health()
    inventory = {
        "managed": [_managed("node-1"), _managed("amaru-1")],
        "unmanaged": [{"id": "outside", "name": "outside-node", "image": "x"}],
    }

    result = operate_topology_health.build_current_topology_snapshot(
        desired=_desired(),
        first=first,
        second=None,
        classified_health=health,
        inventory=inventory,
        generated_at=NOW,
    )

    assert result["schema_version"] == "v1"
    assert result["state"] == "healthy"
    assert result["reason_code"] == "desired_observed_and_health_match"
    assert result["reconciliation"]["state"] == "converged"
    assert result["persisted"] == first["persisted_deployment"]
    assert result["unmanaged_visibility"]["count"] == 1


def test_profile_probe_reconciles_verified_logical_target_not_amaru_wrapper(
    monkeypatch,
):
    profile = SimpleNamespace(id="local-lab")
    retained_desired = _desired()
    catalog_desired = json.loads(json.dumps(retained_desired))
    catalog_amaru = next(
        node for node in catalog_desired["nodes"] if node["id"] == "amaru-1"
    )
    catalog_amaru.update({"version": "0.2.0", "image": IMAGE_C})
    retained_nodes = json.loads(json.dumps(retained_desired["nodes"]))
    for node in retained_nodes:
        node["role"] = "producer" if node["id"] == "node-1" else "consumer"
        node["supporting"] = False
    persisted = {
        "profile_id": "local-lab",
        "substrate": {
            "profile_id": "local-lab",
            "nodes": retained_nodes,
        }
    }
    inventory = {
        "available": True,
        "managed": [
            _managed("node-1"),
            _managed("amaru-1", image=IMAGE_A),
        ],
        "unmanaged": [],
    }
    observed_nodes = [
        {
            "node_id": "node-1",
            "implementation": "cardano-node",
            "version": "10.7.1",
            "image": IMAGE_A,
            "runtime_image": IMAGE_A,
            "state": "running",
            "container_name": "dwarf-install-local-lab-node-1",
        },
        {
            "node_id": "amaru-1",
            "implementation": "amaru",
            "version": "0.3.0",
            "image": IMAGE_B,
            "runtime_image": IMAGE_A,
            "state": "running",
            "container_name": "dwarf-install-local-lab-amaru-1",
        },
    ]
    sample = {
        "enabled": True,
        "profile_id": "local-lab",
        "transport": "controller",
        "persisted_deployment": persisted,
        "observed_nodes": observed_nodes,
        "topology_result": {
            "state": "healthy",
            "reason_code": "node_and_chain_healthy",
            "checked_at": NOW,
        },
    }
    monkeypatch.setattr(health_data, "controller_inventory", lambda: inventory)
    monkeypatch.setattr(profiles_module, "find_profile", lambda _profile_id: profile)
    monkeypatch.setattr(
        profiles_module,
        "desired_topology_for_profile",
        lambda _profile, _preview: catalog_desired,
    )
    monkeypatch.setattr(
        "profile_manager.deployment_versions.build_deployment_version_preview",
        lambda _data: {"status": "confirmed"},
    )
    monkeypatch.setattr(health_data, "_live_health", lambda _profile_id=None: sample)
    monkeypatch.setattr(operate_topology_health.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(operate_topology_health, "_utc_now", lambda: NOW)

    result = operate_topology_health._default_probe("local-lab")

    assert result["state"] == "healthy"
    assert result["reconciliation"]["state"] == "converged"
    observed = {
        row["node_id"]: row for row in result["observed"]["managed"]
    }
    assert observed["amaru-1"]["image"] == IMAGE_B
    assert observed["amaru-1"]["runtime_image"] == IMAGE_A
    retained_amaru = next(
        node for node in result["desired"]["nodes"] if node["id"] == "amaru-1"
    )
    assert retained_amaru["version"] == "0.3.0"
    assert retained_amaru["image"] == IMAGE_B
    assert result["desired"]["plan_sha256"] != catalog_desired["plan_sha256"]
    assert result["persisted"] == persisted


def test_retained_desired_preserves_exact_persisted_node_order_and_false_peer_sharing():
    """The verified retained plan must retain the browser-resolved plan identity."""
    cardano_image = "ghcr.io/intersectmbo/cardano-node@sha256:" + "3" * 64
    amaru_image = "ghcr.io/pragma-org/amaru:v10.11.20260918@sha256:" + "2" * 64
    persisted = {
        "profile_id": "profile-o-amaru-target-latest-confirmed",
        "substrate": {
            "profile_id": "profile-o-amaru-target-latest-confirmed",
            "network_magic": 42,
            "peer_sharing": False,
            "deployment_adapter": "amaru-control",
            "catalog_revision": "catalog-live",
            "version_policy": "exact",
            "topology": {
                "edges": [
                    {"from": "bootstrap-cardano", "to": "amaru1"},
                    {"from": "amaru1", "to": "bootstrap-cardano"},
                ]
            },
            "nodes": [
                {
                    "id": "bootstrap-cardano",
                    "implementation": "cardano-node",
                    "version": "10.7.1",
                    "image": cardano_image,
                    "role": "bootstrap-producer",
                    "supporting": True,
                },
                {
                    "id": "amaru1",
                    "implementation": "amaru",
                    "version": "10.11.20260918",
                    "image": amaru_image,
                    "role": "consumer",
                    "supporting": False,
                },
            ],
        },
    }
    observed = [
        {
            "node_id": node["id"],
            "implementation": node["implementation"],
            "version": node["version"],
            "image": node["image"],
        }
        for node in persisted["substrate"]["nodes"]
    ]
    expected = profiles_module.desired_topology_from_substrate(
        persisted["profile_id"], persisted["substrate"]
    )

    result = operate_topology_health._verified_retained_desired(
        profile_id=persisted["profile_id"],
        persisted=persisted,
        observed_nodes=observed,
    )

    assert result == expected
    assert result["peer_sharing"] is False
    assert result["substrate_sha256"].startswith("sha256:")

    legacy_without_peer_sharing = json.loads(json.dumps(persisted))
    legacy_without_peer_sharing["substrate"].pop("peer_sharing")
    legacy_result = operate_topology_health._verified_retained_desired(
        profile_id=persisted["profile_id"],
        persisted=legacy_without_peer_sharing,
        observed_nodes=observed,
    )
    assert legacy_result is not None
    assert legacy_result["peer_sharing"] is None
    assert legacy_result["plan_sha256"] != result["plan_sha256"]


def _catalog_enrichment_substrate() -> dict:
    image_digest = "sha256:" + "4" * 64
    return {
        "profile_id": "profile-r-amaru-measurement-stock-control",
        "compose_mode": "docker",
        "scope": "amaru-only",
        "target_node_count": 2,
        "support_node_count": 1,
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
            "selected_releases": [
                {
                    "implementation": "amaru",
                    "version": "10.11.20260912",
                    "source_revision": "b159" + "1" * 36,
                    "verification": {
                        "amaru-only": {
                            "status": "confirmed",
                            "default": True,
                            "checked_at": "2026-10-11T12:00:00Z",
                            "evidence": ["state:qualification/exact-0912"],
                            "issues": [],
                        }
                    },
                    "artifacts": [
                        {
                            "kind": "oci",
                            "availability": "available",
                            "reference": "ghcr.io/pragma-org/amaru:v10.11.20260912",
                            "digest": image_digest,
                            "qualification": {
                                "status": "verified",
                                "evidence_sha256": "c" * 64,
                            },
                        }
                    ],
                }
            ],
        },
        "measurement_target_mode": "stock",
        "amaru_json_traces": True,
        "cardano_measurement_traces": False,
        "plutus_v2_genesis": False,
        "resource_limits": {"memory": "4g"},
        "nodes": [
            {
                "id": "amaru1",
                "impl": "amaru",
                "version": "10.11.20260912",
                "image": "ghcr.io/pragma-org/amaru:v10.11.20260912@" + image_digest,
                "role": "consumer",
                "supporting": False,
            }
        ],
        "topology": {"edges": []},
    }


def _profile_r_two_artifact_catalog_projection() -> tuple[dict, dict]:
    """Mirror the retained/current stock profile-r catalog boundary on brabox."""
    retained = _catalog_enrichment_substrate()
    cardano_digest = "sha256:" + "3" * 64
    retained["catalog_snapshot"]["selected_releases"].append(
        {
            "implementation": "cardano-node",
            "version": "10.7.1",
            "source_revision": "7" * 40,
            "verification": {
                "amaru-only": {
                    "status": "confirmed",
                    "default": False,
                    "checked_at": "2026-10-11T12:00:00Z",
                    "evidence": ["state:qualification/supporting-cardano-10.7.1"],
                    "issues": [],
                }
            },
            "artifacts": [
                {
                    "kind": "oci",
                    "availability": "available",
                    "reference": "ghcr.io/intersectmbo/cardano-node@" + cardano_digest,
                    "digest": cardano_digest,
                }
            ],
        }
    )
    retained["nodes"].append(
        {
            "id": "bootstrap-cardano",
            "impl": "cardano-node",
            "version": "10.7.1",
            "image": "ghcr.io/intersectmbo/cardano-node@" + cardano_digest,
            "role": "producer",
            "supporting": True,
        }
    )
    current = json.loads(json.dumps(retained))
    current["catalog_revision"] = "d" * 64
    current["catalog_snapshot"]["catalog_revision"] = "d" * 64
    current["catalog_snapshot"]["catalog_updated_at"] = "2026-10-11T15:00:00Z"
    amaru_artifact = current["catalog_snapshot"]["selected_releases"][0][
        "artifacts"
    ][0]
    amaru_artifact.update(public=True, platforms=["linux/amd64", "linux/arm64"])
    return retained, current


def _catalog_reuse_health(profile_id: str, desired: dict) -> dict:
    return {
        "state": "healthy",
        "profile_id": profile_id,
        "desired": desired,
        "health": {"fresh": True},
        "reconciliation": {
            "state": "converged",
            "persisted_plan_matches": True,
            "missing_node_ids": [],
            "unexpected_managed_node_ids": [],
            "mismatched_nodes": [],
            "not_running_node_ids": [],
            "duplicate_managed_node_ids": [],
            "unmanaged_conflict_node_ids": [],
            "persisted_mismatched_fields": [],
        },
    }


def _profile_r_reuse_plan(profile_id: str) -> dict:
    return {
        "readiness": {"profile_id": profile_id},
        "profile": {"id": profile_id},
        "versions": {
            "resolved": {
                "amaru": {"image_digest": "sha256:" + "4" * 64},
                "cardano-node": {"image_digest": "sha256:" + "3" * 64},
            }
        },
        "availability": {"state": "ready"},
        "measurements": {"requested": [], "resolved": [], "incompatible": []},
    }


def _local_import_substrate(
    *,
    executable_digest: str | None,
    overlay_revision: str = "sha256:" + "7" * 64,
) -> dict:
    """Build the local import substrate through the production translators."""
    from profile_manager.deployment_versions import (
        build_deployment_version_preview,
        operator_registration_release,
    )

    profile = profiles_module.find_profile(
        "profile-r-amaru-measurement-stock-control"
    )
    preview = build_deployment_version_preview(vars(profile))
    registration = {
        "registration_id": "registration-97900317c3844f0882e515b1",
        "implementation": "amaru",
        "version": "10.11.20260912-local",
        "image_reference": "sha256:" + "2" * 64,
        "manifest_digest": None,
        "config_image_id": "sha256:" + "2" * 64,
        "platform": "linux/amd64",
        "runtime_interface": "extracted-binary",
        "controller_installation_id": "install-6a76067b945ecd80",
        "inspected_at": "2026-10-13T15:00:00Z",
        "provenance": {
            "source_revision": None,
            "public": None,
            "qualification": "unverified",
        },
    }
    if executable_digest is not None:
        registration["executable_digest"] = executable_digest
    release = operator_registration_release(registration)
    if executable_digest is not None:
        release["operator_package_proof"] = {
            "status": "verified",
            "registration_id": registration["registration_id"],
            "config_image_id": registration["config_image_id"],
            "executable_digest": executable_digest,
            "platform": registration["platform"],
            "runtime_interface": registration["runtime_interface"],
            "controller_installation_id": registration[
                "controller_installation_id"
            ],
        }
    preview = json.loads(json.dumps(preview))
    preview["resolved"]["amaru"] = release
    preview.update(
        operator_registration_id=registration["registration_id"],
        operator_overlay_revision=overlay_revision,
        status="unknown",
        requires_acknowledgement=True,
        unknown_acknowledged=True,
    )
    return profiles_module.versioned_substrate_for_profile(profile, preview)


def test_unrelated_registration_append_keeps_exact_local_runtime_reusable(
    monkeypatch, tmp_path
):
    """A global ledger append must not change the selected immutable runtime."""
    from profile_manager import dashboard

    ledger = OperatorVersionDecisionLedger(tmp_path)
    initial = ledger.read()
    selected_registration = {
        "registration_id": "registration-97900317c3844f0882e515b1",
        "implementation": "amaru",
        "version": "10.11.20260912-local",
        "image_reference": "sha256:" + "2" * 64,
        "manifest_digest": None,
        "config_image_id": "sha256:" + "2" * 64,
        "executable_digest": "sha256:" + "9" * 64,
        "platform": "linux/amd64",
        "runtime_interface": "extracted-binary",
        "controller_installation_id": "install-6a76067b945ecd80",
        "inspected_at": "2026-10-13T15:00:00Z",
        "provenance": {
            "source_revision": None,
            "public": None,
            "qualification": "unverified",
        },
    }
    selected_state = ledger.append_registration(
        selected_registration, expected_revision=initial["revision"]
    )
    unrelated_registration = {
        **selected_registration,
        "registration_id": "registration-unrelated-cardano",
        "implementation": "cardano-node",
        "version": "11.1.99-local",
        "image_reference": "sha256:" + "8" * 64,
        "config_image_id": "sha256:" + "8" * 64,
        "executable_digest": "sha256:" + "6" * 64,
        "runtime_interface": None,
        "inspected_at": "2026-10-14T05:47:19Z",
    }
    appended_state = ledger.append_registration(
        unrelated_registration, expected_revision=selected_state["revision"]
    )
    assert appended_state["revision"] != selected_state["revision"]

    retained = _local_import_substrate(
        executable_digest=selected_registration["executable_digest"],
        overlay_revision=selected_state["revision"],
    )
    current = _local_import_substrate(
        executable_digest=selected_registration["executable_digest"],
        overlay_revision=appended_state["revision"],
    )
    retained_desired = profiles_module.desired_topology_from_substrate(
        retained["profile_id"], retained
    )
    current_desired = profiles_module.desired_topology_from_substrate(
        current["profile_id"], current
    )

    assert retained_desired["deployment_sha256"] == current_desired[
        "deployment_sha256"
    ]
    assert retained_desired["plan_sha256"] != current_desired["plan_sha256"]
    assert profiles_module.deployment_substrates_reusable(retained, current) is True
    assert profiles_module.deployment_topologies_reusable(
        retained_desired, current_desired
    ) is True

    managed = [
        {
            "id": node["id"] + "-container",
            "name": "dwarf-install-" + retained["profile_id"] + "-" + node["id"],
            "topology_id": retained["profile_id"],
            "node_id": node["id"],
            "implementation": node["implementation"],
            "version": node["version"],
            "image": node["image"],
            "state": "running",
        }
        for node in retained_desired["nodes"]
    ]
    snapshot = build_topology_snapshot(
        desired=retained_desired,
        persisted={"profile_id": retained["profile_id"], "substrate": retained},
        managed=managed,
        unmanaged=[],
        health={"state": "healthy", "checked_at": NOW},
        generated_at=NOW,
    )
    plan = _profile_r_reuse_plan(current["profile_id"])
    plan["versions"].update(
        operator_registration_id=current["operator_registration_id"],
        operator_overlay_revision=current["operator_overlay_revision"],
    )
    registration = next(
        node["operator_registration"]
        for node in current["nodes"]
        if node.get("operator_registration")
    )
    plan["versions"]["resolved"]["amaru"].update(
        image_digest=None,
        image_config_id=registration["config_image_id"],
        image_reference=registration["config_image_id"],
        executable_digest=registration["executable_digest"],
        operator_registration=registration,
    )
    monkeypatch.setattr(
        dashboard, "_current_run_plan_desired_topology", lambda _plan: current_desired
    )

    class NoMutation:
        def preview(self, *_args, **_kwargs):
            raise AssertionError("unrelated registration must not preview topology")

        def apply(self, *_args, **_kwargs):
            raise AssertionError("unrelated registration must not apply topology")

    events = list(
        dashboard._prepare_run_plan(
            plan,
            "sha256:" + "f" * 64,
            action_service=NoMutation(),
            snapshot_loader=lambda _profile_id: snapshot,
            health_requester=lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("fresh retained health must be reused")
            ),
        )
    )
    assert events[-1]["stage"] == "ready"
    assert events[-1]["state"] == "ready"


def test_unrelated_overlay_append_reuses_retained_digest_after_proof_enrichment():
    """A retained exact digest may predate only its trusted package-proof marker."""
    retained = _local_import_substrate(
        executable_digest="sha256:" + "9" * 64,
        overlay_revision="sha256:" + "a" * 64,
    )
    current = _local_import_substrate(
        executable_digest="sha256:" + "9" * 64,
        overlay_revision="sha256:" + "b" * 64,
    )
    retained["catalog_snapshot"]["selected_releases"][0].pop(
        "operator_package_proof"
    )
    retained_desired = profiles_module.desired_topology_from_substrate(
        retained["profile_id"], retained
    )
    current_desired = profiles_module.desired_topology_from_substrate(
        current["profile_id"], current
    )
    observed_nodes = [
        {
            "node_id": node["id"],
            "implementation": node["impl"],
            "version": node["version"],
            "image": node["image"],
            "runtime_image": node["image"],
            "state": "running",
            "container_name": "dwarf-" + node["id"],
        }
        for node in retained["nodes"]
    ]
    inventory = {
        "available": True,
        "managed": [
            {
                "id": node["node_id"] + "-container",
                "name": node["container_name"],
                "topology_id": retained["profile_id"],
                "node_id": node["node_id"],
                "implementation": node["implementation"],
                "version": node["version"],
                "image": node["image"],
                "state": "running",
            }
            for node in observed_nodes
        ],
        "unmanaged": [],
    }
    first = {
        "enabled": True,
        "transport": "controller",
        "profile_id": retained["profile_id"],
        "persisted_deployment": {
            "profile_id": retained["profile_id"],
            "substrate": retained,
        },
        "observed_nodes": observed_nodes,
    }
    snapshot = operate_topology_health.build_current_topology_snapshot(
        desired=current_desired,
        first=first,
        second=None,
        classified_health={
            "state": "healthy",
            "reason_code": "node_and_chain_healthy",
            "checked_at": NOW,
            "profile_id": retained["profile_id"],
        },
        inventory=inventory,
        generated_at=NOW,
    )

    assert "local_executable_reuse_metadata" not in retained_desired
    assert retained_desired["deployment_sha256"] != current_desired[
        "deployment_sha256"
    ]
    assert profiles_module.deployment_substrates_reusable(retained, current) is True
    assert snapshot["desired"]["local_executable_reuse_metadata"][
        "executable_digest"
    ] == "sha256:" + "9" * 64
    assert snapshot["desired"]["operator_overlay_revision"] == "sha256:" + "a" * 64
    assert snapshot["desired"]["deployment_sha256"] == current_desired[
        "deployment_sha256"
    ]
    assert profiles_module.deployment_topologies_reusable(
        snapshot["desired"], current_desired
    ) is True


@pytest.mark.parametrize("mutation", ["changed-executable", "missing-current-proof"])
def test_retained_digest_proof_enrichment_still_rejects_untrusted_current(
    mutation,
):
    retained = _local_import_substrate(
        executable_digest="sha256:" + "9" * 64,
        overlay_revision="sha256:" + "a" * 64,
    )
    retained["catalog_snapshot"]["selected_releases"][0].pop(
        "operator_package_proof"
    )
    current = _local_import_substrate(
        executable_digest=(
            "sha256:" + ("8" if mutation == "changed-executable" else "9") * 64
        ),
        overlay_revision="sha256:" + "b" * 64,
    )
    if mutation == "missing-current-proof":
        current["catalog_snapshot"]["selected_releases"][0].pop(
            "operator_package_proof"
        )
    current_desired = profiles_module.desired_topology_from_substrate(
        current["profile_id"], current
    )
    retained_desired = profiles_module.desired_topology_from_retained_substrate(
        retained["profile_id"], retained, expected_desired=current_desired
    )

    assert profiles_module.deployment_substrates_reusable(retained, current) is False
    assert "local_executable_reuse_metadata" not in retained_desired
    if mutation == "changed-executable":
        assert profiles_module.deployment_topologies_reusable(
            retained_desired, current_desired
        ) is False


@pytest.mark.parametrize(
    "retained_proof",
    [
        None,
        {},
        {
            "status": "verified",
            "registration_id": "registration-97900317c3844f0882e515b1",
            "config_image_id": "sha256:" + "2" * 64,
            "executable_digest": "sha256:" + "8" * 64,
            "platform": "linux/amd64",
            "runtime_interface": "extracted-binary",
            "controller_installation_id": "install-6a76067b945ecd80",
        },
    ],
)
def test_retained_digest_enrichment_rejects_present_invalid_package_proof(
    retained_proof,
):
    retained = _local_import_substrate(
        executable_digest="sha256:" + "9" * 64,
        overlay_revision="sha256:" + "a" * 64,
    )
    retained["catalog_snapshot"]["selected_releases"][0][
        "operator_package_proof"
    ] = retained_proof
    current = _local_import_substrate(
        executable_digest="sha256:" + "9" * 64,
        overlay_revision="sha256:" + "b" * 64,
    )
    current_desired = profiles_module.desired_topology_from_substrate(
        current["profile_id"], current
    )
    retained_desired = profiles_module.desired_topology_from_retained_substrate(
        retained["profile_id"], retained, expected_desired=current_desired
    )

    assert profiles_module.deployment_substrates_reusable(retained, current) is False
    assert "local_executable_reuse_metadata" not in retained_desired
    assert profiles_module.deployment_topologies_reusable(
        retained_desired, current_desired
    ) is False


@pytest.mark.parametrize(
    "mutation",
    [
        "config",
        "executable",
        "platform",
        "interface",
        "source",
        "controller-installation",
        "supporting-artifact",
    ],
)
def test_overlay_advance_never_masks_selected_runtime_identity_drift(mutation):
    retained = _local_import_substrate(
        executable_digest="sha256:" + "9" * 64,
        overlay_revision="sha256:" + "a" * 64,
    )
    current = _local_import_substrate(
        executable_digest="sha256:" + "9" * 64,
        overlay_revision="sha256:" + "b" * 64,
    )
    selected_release = current["catalog_snapshot"]["selected_releases"][0]
    selected_artifact = selected_release["artifacts"][0]
    selected_node = next(
        node for node in current["nodes"] if node.get("operator_registration")
    )
    if mutation == "config":
        selected_node["image"] = "sha256:" + "8" * 64
    elif mutation == "executable":
        selected_release["operator_package_proof"]["executable_digest"] = (
            "sha256:" + "8" * 64
        )
    elif mutation == "platform":
        selected_artifact["operator_registration"]["platform"] = "linux/arm64"
    elif mutation == "interface":
        selected_node["amaru_runtime_interface"] = "container-entrypoint"
    elif mutation == "source":
        selected_release["source_revision"] = "1" * 40
    elif mutation == "controller-installation":
        selected_node["operator_registration"]["controller_installation_id"] = (
            "install-other"
        )
    elif mutation == "supporting-artifact":
        supporting_node = next(
            node for node in current["nodes"] if node["impl"] == "cardano-node"
        )
        supporting_node["image"] = "sha256:" + "8" * 64

    retained_desired = profiles_module.desired_topology_from_substrate(
        retained["profile_id"], retained
    )
    current_desired = profiles_module.desired_topology_from_substrate(
        current["profile_id"], current
    )
    assert profiles_module.deployment_substrates_reusable(retained, current) is False
    assert profiles_module.deployment_topologies_reusable(
        retained_desired, current_desired
    ) is False


def test_local_executable_proof_cannot_disappear_during_reuse_comparison():
    retained = _local_import_substrate(
        executable_digest="sha256:" + "9" * 64,
        overlay_revision="sha256:" + "a" * 64,
    )
    current = _local_import_substrate(
        executable_digest="sha256:" + "9" * 64,
        overlay_revision="sha256:" + "b" * 64,
    )
    current["catalog_snapshot"]["selected_releases"][0].pop(
        "operator_package_proof"
    )

    retained_desired = profiles_module.desired_topology_from_substrate(
        retained["profile_id"], retained
    )
    current_desired = profiles_module.desired_topology_from_substrate(
        current["profile_id"], current
    )
    assert profiles_module.deployment_substrates_reusable(retained, current) is False
    assert profiles_module.deployment_topologies_reusable(
        retained_desired, current_desired
    ) is False


@pytest.mark.parametrize(
    "mutation",
    ["missing-nested", "mismatched-nested", "malformed-top-and-nested"],
)
def test_local_overlay_reuse_requires_coherent_canonical_revision_binding(mutation):
    retained = _local_import_substrate(
        executable_digest="sha256:" + "9" * 64,
        overlay_revision="sha256:" + "a" * 64,
    )
    current = _local_import_substrate(
        executable_digest="sha256:" + "9" * 64,
        overlay_revision="sha256:" + "b" * 64,
    )
    if mutation == "missing-nested":
        current["catalog_snapshot"].pop("operator_overlay_revision")
    elif mutation == "mismatched-nested":
        current["catalog_snapshot"]["operator_overlay_revision"] = (
            "sha256:" + "c" * 64
        )
    else:
        current["operator_overlay_revision"] = "not-a-sha256-revision"
        current["catalog_snapshot"]["operator_overlay_revision"] = (
            "not-a-sha256-revision"
        )

    retained_desired = profiles_module.desired_topology_from_substrate(
        retained["profile_id"], retained
    )
    current_desired = profiles_module.desired_topology_from_substrate(
        current["profile_id"], current
    )
    assert profiles_module.deployment_substrates_reusable(retained, current) is False
    assert profiles_module.deployment_topologies_reusable(
        retained_desired, current_desired
    ) is False


def test_overlay_advance_rejects_coherent_selected_executable_change():
    retained = _local_import_substrate(
        executable_digest="sha256:" + "9" * 64,
        overlay_revision="sha256:" + "a" * 64,
    )
    current = _local_import_substrate(
        executable_digest="sha256:" + "9" * 64,
        overlay_revision="sha256:" + "b" * 64,
    )
    changed_digest = "sha256:" + "8" * 64
    release = current["catalog_snapshot"]["selected_releases"][0]
    release["operator_package_proof"]["executable_digest"] = changed_digest
    release["operator_registration"]["executable_digest"] = changed_digest
    for artifact in release["artifacts"]:
        artifact["executable_digest"] = changed_digest
        artifact["operator_registration"]["executable_digest"] = changed_digest
    for node in current["nodes"]:
        if node.get("operator_registration"):
            node["operator_registration"]["executable_digest"] = changed_digest

    retained_desired = profiles_module.desired_topology_from_substrate(
        retained["profile_id"], retained
    )
    current_desired = profiles_module.desired_topology_from_substrate(
        current["profile_id"], current
    )
    assert profiles_module.deployment_substrates_reusable(retained, current) is False
    assert profiles_module.deployment_topologies_reusable(
        retained_desired, current_desired
    ) is False


def test_overlay_advance_rejects_coherent_supporting_artifact_change():
    retained = _local_import_substrate(
        executable_digest="sha256:" + "9" * 64,
        overlay_revision="sha256:" + "a" * 64,
    )
    current = _local_import_substrate(
        executable_digest="sha256:" + "9" * 64,
        overlay_revision="sha256:" + "b" * 64,
    )
    changed_digest = "sha256:" + "8" * 64
    supporting_release = next(
        release
        for release in current["catalog_snapshot"]["selected_releases"]
        if release["implementation"] == "cardano-node"
    )
    supporting_artifact = supporting_release["artifacts"][0]
    changed_reference = (
        supporting_artifact["reference"].split("@", 1)[0] + "@" + changed_digest
    )
    supporting_artifact["reference"] = changed_reference
    supporting_artifact["digest"] = changed_digest
    supporting_node = next(
        node for node in current["nodes"] if node["impl"] == "cardano-node"
    )
    supporting_node["image"] = changed_reference

    retained_desired = profiles_module.desired_topology_from_substrate(
        retained["profile_id"], retained
    )
    current_desired = profiles_module.desired_topology_from_substrate(
        current["profile_id"], current
    )
    assert profiles_module.deployment_substrates_reusable(retained, current) is False
    assert profiles_module.deployment_topologies_reusable(
        retained_desired, current_desired
    ) is False


def test_local_import_digest_enrichment_reuses_exact_old_deployment_only():
    retained = _local_import_substrate(executable_digest=None)
    current = _local_import_substrate(executable_digest="sha256:" + "9" * 64)
    retained_desired = profiles_module.desired_topology_from_substrate(
        retained["profile_id"], retained
    )
    current_desired = profiles_module.desired_topology_from_substrate(
        current["profile_id"], current
    )

    assert retained_desired["deployment_sha256"] == current_desired[
        "deployment_sha256"
    ]
    assert retained_desired["plan_sha256"] != current_desired["plan_sha256"]
    assert profiles_module.deployment_topologies_reusable(
        retained_desired, current_desired
    ) is True

    changed_digest = _local_import_substrate(executable_digest="sha256:" + "8" * 64)
    changed_digest_desired = profiles_module.desired_topology_from_substrate(
        changed_digest["profile_id"], changed_digest
    )
    assert profiles_module.deployment_topologies_reusable(
        current_desired, changed_digest_desired
    ) is False

    missing_proof = _local_import_substrate(
        executable_digest="sha256:" + "9" * 64
    )
    missing_proof["catalog_snapshot"]["selected_releases"][0].pop(
        "operator_package_proof"
    )
    missing_proof_desired = profiles_module.desired_topology_from_substrate(
        missing_proof["profile_id"], missing_proof
    )
    assert "local_executable_reuse_metadata" not in missing_proof_desired
    assert profiles_module.deployment_topologies_reusable(
        retained_desired, missing_proof_desired
    ) is False

    absent_old_field = _local_import_substrate(executable_digest=None)
    absent_old_field["catalog_snapshot"]["selected_releases"][0]["artifacts"][
        0
    ].pop("executable_digest")
    absent_old_desired = profiles_module.desired_topology_from_substrate(
        absent_old_field["profile_id"], absent_old_field
    )
    assert absent_old_desired["deployment_sha256"] != current_desired[
        "deployment_sha256"
    ]
    assert profiles_module.deployment_topologies_reusable(
        absent_old_desired, current_desired
    ) is False


def test_prepare_reuses_real_old_null_local_digest_without_topology_action(
    monkeypatch,
):
    from profile_manager import dashboard

    retained = _local_import_substrate(executable_digest=None)
    current = _local_import_substrate(executable_digest="sha256:" + "9" * 64)
    retained_desired = profiles_module.desired_topology_from_substrate(
        retained["profile_id"], retained
    )
    current_desired = profiles_module.desired_topology_from_substrate(
        current["profile_id"], current
    )
    assert retained["catalog_snapshot"]["selected_releases"][0]["artifacts"][0][
        "executable_digest"
    ] is None
    assert retained_desired["plan_sha256"] != current_desired["plan_sha256"]

    managed = [
        {
            "id": node["id"] + "-container",
            "name": "dwarf-install-" + retained["profile_id"] + "-" + node["id"],
            "topology_id": retained["profile_id"],
            "node_id": node["id"],
            "implementation": node["implementation"],
            "version": node["version"],
            "image": node["image"],
            "state": "running",
        }
        for node in retained_desired["nodes"]
    ]
    snapshot = build_topology_snapshot(
        desired=retained_desired,
        persisted={"profile_id": retained["profile_id"], "substrate": retained},
        managed=managed,
        unmanaged=[],
        health={"state": "healthy", "checked_at": NOW},
        generated_at=NOW,
    )
    plan = _profile_r_reuse_plan(retained["profile_id"])
    plan["versions"].update(
        operator_registration_id=current["operator_registration_id"],
        operator_overlay_revision=current["operator_overlay_revision"],
    )
    plan["versions"]["resolved"]["amaru"]["executable_digest"] = (
        "sha256:" + "9" * 64
    )
    monkeypatch.setattr(
        dashboard,
        "_current_run_plan_desired_topology",
        lambda _plan: current_desired,
    )

    class NoMutation:
        def preview(self, *_args, **_kwargs):
            raise AssertionError("trusted null-to-digest enrichment must not preview")

        def apply(self, *_args, **_kwargs):
            raise AssertionError("trusted null-to-digest enrichment must not apply")

    events = list(
        dashboard._prepare_run_plan(
            plan,
            "sha256:" + "f" * 64,
            action_service=NoMutation(),
            snapshot_loader=lambda _profile_id: snapshot,
        )
    )
    assert events[-1]["stage"] == "ready"
    assert events[-1]["state"] == "ready"
    assert events[-1]["desired_topology_reusable"] is True


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("registration_id", "registration-foreign"),
        ("config_image_id", "sha256:" + "8" * 64),
        ("platform", "linux/arm64"),
        ("source_revision", "f" * 40),
        ("runtime_interface", "unsupported-interface"),
        ("controller_installation_id", "install-foreign"),
    ),
)
def test_local_import_digest_enrichment_rejects_changed_trusted_identity(
    field, value
):
    retained = _local_import_substrate(executable_digest=None)
    current = _local_import_substrate(executable_digest="sha256:" + "9" * 64)
    node = next(
        row for row in current["nodes"] if row.get("operator_registration")
    )
    registration = node["operator_registration"]
    registration[field] = value
    release = current["catalog_snapshot"]["selected_releases"][0]
    release["operator_registration"][field] = value
    release["artifacts"][0]["operator_registration"][field] = value
    if field == "registration_id":
        current["operator_registration_id"] = value
        current["catalog_snapshot"]["operator_registration_id"] = value
    elif field == "config_image_id":
        node["image"] = value
        release["artifacts"][0]["reference"] = value
        release["artifacts"][0]["config_image_id"] = value
    elif field == "platform":
        release["artifacts"][0]["platforms"] = [value]
    elif field == "source_revision":
        registration["provenance"]["source_revision"] = value
        release["operator_registration"]["provenance"]["source_revision"] = value
        release["artifacts"][0]["operator_registration"]["provenance"][
            "source_revision"
        ] = value
        release["source_revision"] = value
        node["source_revision"] = value
    elif field == "runtime_interface":
        node["amaru_runtime_interface"] = value
        release["artifacts"][0]["runtime_interface"] = value

    retained_desired = profiles_module.desired_topology_from_substrate(
        retained["profile_id"], retained
    )
    current_desired = profiles_module.desired_topology_from_substrate(
        current["profile_id"], current
    )
    assert profiles_module.deployment_topologies_reusable(
        retained_desired, current_desired
    ) is False


def test_default_profile_omits_unselected_operator_identity():
    from dataclasses import asdict

    from profile_manager.deployment_versions import build_deployment_version_preview
    from profile_manager.profiles import find_profile, versioned_substrate_for_profile

    profile = find_profile("profile-r-amaru-measurement-stock-control")
    preview = build_deployment_version_preview(asdict(profile))
    assert preview["operator_registration_id"] is None
    assert preview["operator_overlay_revision"] is None

    substrate = versioned_substrate_for_profile(profile, preview)
    desired = profiles_module.desired_topology_from_substrate(profile.id, substrate)

    assert "operator_registration_id" not in substrate
    assert "operator_overlay_revision" not in substrate
    assert "operator_registration_id" not in substrate["catalog_snapshot"]
    assert "operator_overlay_revision" not in substrate["catalog_snapshot"]
    assert "operator_registration_id" not in desired
    assert "operator_overlay_revision" not in desired


def test_catalog_amaru_artifact_preserves_extracted_binary_runtime_interface():
    release = {
        "implementation": "amaru",
        "version": "10.11.0",
        "source_revision": "a" * 40,
        "artifacts": [{
            "kind": "oci",
            "availability": "available",
            "reference": "localhost:5000/dwarf/amaru-candidate",
            "digest": "sha256:" + "b" * 64,
            "runtime_interface": "extracted-binary",
        }],
    }

    node = profiles_module._versioned_node(
        "amaru1", "consumer", release
    )

    assert node["amaru_runtime_interface"] == "extracted-binary"


def test_real_operator_registration_change_never_earns_default_migration_reuse():
    before = _catalog_enrichment_substrate()
    after = json.loads(json.dumps(before))
    before["operator_registration_id"] = "local-registration-a"
    before["catalog_snapshot"]["operator_registration_id"] = "local-registration-a"
    after["operator_registration_id"] = "local-registration-b"
    after["catalog_snapshot"]["operator_registration_id"] = "local-registration-b"

    assert profiles_module.deployment_substrates_reusable(before, after) is False
    assert profiles_module.desired_topology_from_substrate(
        before["profile_id"], before
    )["deployment_sha256"] != profiles_module.desired_topology_from_substrate(
        after["profile_id"], after
    )["deployment_sha256"]


def test_operator_ledger_revision_stales_full_plan_without_changing_physical_deployment():
    from profile_manager import dashboard

    before = _catalog_enrichment_substrate()
    after = json.loads(json.dumps(before))
    for substrate, revision in (
        (before, "sha256:" + "a" * 64),
        (after, "sha256:" + "b" * 64),
    ):
        substrate["operator_registration_id"] = "local-registration-a"
        substrate["catalog_snapshot"][
            "operator_registration_id"
        ] = "local-registration-a"
        substrate["operator_overlay_revision"] = revision
        substrate["catalog_snapshot"]["operator_overlay_revision"] = revision

    before_desired = profiles_module.desired_topology_from_substrate(
        before["profile_id"], before
    )
    after_desired = profiles_module.desired_topology_from_substrate(
        after["profile_id"], after
    )

    assert before_desired["deployment_sha256"] == after_desired["deployment_sha256"]
    assert before_desired["plan_sha256"] != after_desired["plan_sha256"]
    stale_hash = json.loads(json.dumps(after_desired))
    stale_hash["plan_sha256"] = before_desired["plan_sha256"]
    assert profiles_module.deployment_topologies_reusable(
        before_desired, stale_hash
    ) is False
    assert profiles_module.deployment_substrates_reusable(before, after) is True
    assert profiles_module.deployment_topologies_reusable(
        before_desired, after_desired
    ) is True
    preparation = dashboard._run_plan_preparation_status(
        _profile_r_reuse_plan(before["profile_id"]),
        "sha256:" + "7" * 64,
        _catalog_reuse_health(before["profile_id"], before_desired),
        expected_desired=after_desired,
    )
    assert preparation["state"] == "ready"
    assert preparation["topology_identity"] is True


@pytest.mark.parametrize("catalog_value", (None, "not-an-object"))
def test_revision_only_substrate_reuse_requires_complete_nested_catalog(catalog_value):
    before = _catalog_enrichment_substrate()
    after = json.loads(json.dumps(before))
    for substrate, revision in (
        (before, "sha256:" + "a" * 64),
        (after, "sha256:" + "b" * 64),
    ):
        substrate["operator_registration_id"] = "local-registration-a"
        substrate["operator_overlay_revision"] = revision
        if catalog_value is None:
            substrate.pop("catalog_snapshot", None)
        else:
            substrate["catalog_snapshot"] = catalog_value

    assert profiles_module.deployment_substrates_reusable(before, after) is False


@pytest.mark.parametrize(
    ("case_name", "after_registration", "after_revision"),
    (
        ("missing", "local-registration-a", None),
        ("malformed", "local-registration-a", "not-a-revision"),
        ("foreign", "local-registration-b", "sha256:" + "a" * 64),
        ("changed", "local-registration-a", "sha256:" + "b" * 64),
    ),
)
def test_catalog_enrichment_cannot_bypass_operator_overlay_identity(
    case_name, after_registration, after_revision
):
    before = _catalog_enrichment_substrate()
    after = json.loads(json.dumps(before))
    before["operator_registration_id"] = "local-registration-a"
    before["operator_overlay_revision"] = "sha256:" + "a" * 64
    before["catalog_snapshot"][
        "operator_registration_id"
    ] = before["operator_registration_id"]
    before["catalog_snapshot"][
        "operator_overlay_revision"
    ] = before["operator_overlay_revision"]
    after["operator_registration_id"] = after_registration
    after["operator_overlay_revision"] = after_revision
    after["catalog_snapshot"]["operator_registration_id"] = after_registration
    if after_revision is None:
        after["catalog_snapshot"].pop("operator_overlay_revision", None)
        after.pop("operator_overlay_revision", None)
    else:
        after["catalog_snapshot"]["operator_overlay_revision"] = after_revision
    after["catalog_revision"] = "d" * 64
    after["catalog_snapshot"]["catalog_revision"] = "d" * 64
    artifact = after["catalog_snapshot"]["selected_releases"][0]["artifacts"][0]
    artifact.update(public=True, platforms=["linux/amd64", "linux/arm64"])

    before_desired = profiles_module.desired_topology_from_substrate(
        before["profile_id"], before
    )
    after_desired = profiles_module.desired_topology_from_substrate(
        after["profile_id"], after
    )

    assert profiles_module.deployment_substrates_reusable(before, after) is False
    assert profiles_module.deployment_topologies_reusable(
        before_desired, after_desired
    ) is False


def test_catalog_enrichment_accepts_equal_valid_registered_overlay_identity():
    before = _catalog_enrichment_substrate()
    after = json.loads(json.dumps(before))
    for substrate in (before, after):
        substrate["operator_registration_id"] = "local-registration-a"
        substrate["operator_overlay_revision"] = "sha256:" + "a" * 64
        substrate["catalog_snapshot"][
            "operator_registration_id"
        ] = substrate["operator_registration_id"]
        substrate["catalog_snapshot"][
            "operator_overlay_revision"
        ] = substrate["operator_overlay_revision"]
    after["catalog_revision"] = "d" * 64
    after["catalog_snapshot"]["catalog_revision"] = "d" * 64
    artifact = after["catalog_snapshot"]["selected_releases"][0]["artifacts"][0]
    artifact.update(public=True, platforms=["linux/amd64", "linux/arm64"])

    before_desired = profiles_module.desired_topology_from_substrate(
        before["profile_id"], before
    )
    after_desired = profiles_module.desired_topology_from_substrate(
        after["profile_id"], after
    )

    assert profiles_module.deployment_substrates_reusable(before, after) is True
    assert profiles_module.deployment_topologies_reusable(
        before_desired, after_desired
    ) is True


def test_profile_r_reuses_additive_amaru_catalog_enrichment_with_neutral_support():
    from profile_manager import dashboard

    retained, current = _profile_r_two_artifact_catalog_projection()
    retained_desired = profiles_module.desired_topology_from_substrate(
        retained["profile_id"], retained
    )
    current_desired = profiles_module.desired_topology_from_substrate(
        current["profile_id"], current
    )
    health = _catalog_reuse_health(retained["profile_id"], retained_desired)

    result = dashboard._run_plan_preparation_status(
        _profile_r_reuse_plan(retained["profile_id"]),
        "sha256:" + "7" * 64,
        health,
        expected_desired=current_desired,
    )

    assert retained_desired["deployment_sha256"] == current_desired[
        "deployment_sha256"
    ]
    assert dashboard._desired_topology_reusable(
        retained_desired, current_desired
    ) is True
    assert result["state"] == "ready"
    assert result["topology_identity"] is True

    missing_support = json.loads(json.dumps(health))
    missing_support["reconciliation"]["missing_node_ids"] = ["bootstrap-cardano"]
    blocked = dashboard._run_plan_preparation_status(
        _profile_r_reuse_plan(retained["profile_id"]),
        "sha256:" + "7" * 64,
        missing_support,
        expected_desired=current_desired,
    )
    assert blocked["state"] == "blocked"
    assert blocked["topology_identity"] is False


@pytest.mark.parametrize("keep", [(), ("public",), ("platforms",)])
def test_profile_r_requires_complete_actual_catalog_enrichment(keep):
    from profile_manager import dashboard

    retained, current = _profile_r_two_artifact_catalog_projection()
    artifact = current["catalog_snapshot"]["selected_releases"][0]["artifacts"][0]
    for field in {"public", "platforms"} - set(keep):
        artifact.pop(field)
    retained_desired = profiles_module.desired_topology_from_substrate(
        retained["profile_id"], retained
    )
    current_desired = profiles_module.desired_topology_from_substrate(
        current["profile_id"], current
    )

    assert retained_desired["deployment_sha256"] == current_desired[
        "deployment_sha256"
    ]
    assert dashboard._desired_topology_reusable(
        retained_desired, current_desired
    ) is False


def test_catalog_provenance_enrichment_preserves_deployment_identity():
    retained = _catalog_enrichment_substrate()
    current = json.loads(json.dumps(retained))
    current["catalog_revision"] = "d" * 64
    current["catalog_snapshot"]["catalog_revision"] = "d" * 64
    current["catalog_snapshot"]["catalog_updated_at"] = "2026-10-11T15:00:00Z"
    artifact = current["catalog_snapshot"]["selected_releases"][0]["artifacts"][0]
    artifact["platforms"] = ["linux/amd64", "linux/arm64"]
    artifact["public"] = True

    retained_desired = profiles_module.desired_topology_from_substrate(
        retained["profile_id"], retained
    )
    current_desired = profiles_module.desired_topology_from_substrate(
        current["profile_id"], current
    )

    assert retained_desired["plan_sha256"] != current_desired["plan_sha256"]
    assert (
        retained_desired["deployment_sha256"]
        == current_desired["deployment_sha256"]
    )


def test_run_preparation_reuses_healthy_catalog_enriched_deployment():
    from profile_manager import dashboard

    retained = _catalog_enrichment_substrate()
    current = json.loads(json.dumps(retained))
    current["catalog_revision"] = "d" * 64
    current["catalog_snapshot"]["catalog_revision"] = "d" * 64
    current["catalog_snapshot"]["catalog_updated_at"] = "2026-10-11T15:00:00Z"
    artifact = current["catalog_snapshot"]["selected_releases"][0]["artifacts"][0]
    artifact["platforms"] = ["linux/amd64", "linux/arm64"]
    artifact["public"] = True
    retained_desired = profiles_module.desired_topology_from_substrate(
        retained["profile_id"], retained
    )
    current_desired = profiles_module.desired_topology_from_substrate(
        current["profile_id"], current
    )
    plan = {
        "readiness": {"profile_id": retained["profile_id"]},
        "profile": {"id": retained["profile_id"]},
        "versions": {"resolved": {"amaru": {"image_digest": DIGEST_A}}},
        "availability": {"state": "ready"},
        "measurements": {"requested": [], "resolved": [], "incompatible": []},
    }
    health = {
        "state": "healthy",
        "profile_id": retained["profile_id"],
        "desired": retained_desired,
        "health": {"fresh": True},
        "reconciliation": {
            "state": "converged",
            "persisted_plan_matches": True,
            "missing_node_ids": [],
            "unexpected_managed_node_ids": [],
            "mismatched_nodes": [],
            "not_running_node_ids": [],
            "duplicate_managed_node_ids": [],
            "unmanaged_conflict_node_ids": [],
            "persisted_mismatched_fields": [],
        },
    }

    result = dashboard._run_plan_preparation_status(
        plan,
        "sha256:" + "7" * 64,
        health,
        expected_desired=current_desired,
    )

    assert result["state"] == "ready"
    assert result["topology_identity"] is True
    assert result["expected_topology_plan_sha256"] != result[
        "observed_topology_plan_sha256"
    ]
    assert result["expected_topology_deployment_sha256"] == result[
        "observed_topology_deployment_sha256"
    ]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda artifact: artifact.update(public=False),
        lambda artifact: artifact.update(platforms=["linux/amd64"]),
        lambda artifact: (artifact.pop("public", None), artifact.pop("platforms", None)),
        lambda artifact: artifact.update(public=True, platforms=["unknown/unknown"]),
    ],
    ids=(
        "public-withdrawn",
        "platform-support-removed",
        "metadata-missing",
        "platform-untrusted",
    ),
)
def test_catalog_metadata_withdrawal_or_untrusted_state_cannot_earn_reuse(mutate):
    from profile_manager import dashboard

    retained = _catalog_enrichment_substrate()
    retained_artifact = retained["catalog_snapshot"]["selected_releases"][0][
        "artifacts"
    ][0]
    retained_artifact.update(
        public=True, platforms=["linux/amd64", "linux/arm64"]
    )
    current = json.loads(json.dumps(retained))
    current["catalog_revision"] = "d" * 64
    current["catalog_snapshot"]["catalog_revision"] = "d" * 64
    current["catalog_snapshot"]["catalog_updated_at"] = "2026-10-11T15:00:00Z"
    mutate(current["catalog_snapshot"]["selected_releases"][0]["artifacts"][0])
    retained_desired = profiles_module.desired_topology_from_substrate(
        retained["profile_id"], retained
    )
    current_desired = profiles_module.desired_topology_from_substrate(
        current["profile_id"], current
    )
    health = {
        "state": "healthy",
        "profile_id": retained["profile_id"],
        "desired": retained_desired,
        "health": {"fresh": True},
        "reconciliation": {
            "state": "converged",
            "persisted_plan_matches": True,
            "missing_node_ids": [],
            "unexpected_managed_node_ids": [],
            "mismatched_nodes": [],
            "not_running_node_ids": [],
            "duplicate_managed_node_ids": [],
            "unmanaged_conflict_node_ids": [],
            "persisted_mismatched_fields": [],
        },
    }
    plan = {
        "readiness": {"profile_id": retained["profile_id"]},
        "profile": {"id": retained["profile_id"]},
        "versions": {"resolved": {"amaru": {"image_digest": DIGEST_A}}},
        "availability": {"state": "ready"},
        "measurements": {"requested": [], "resolved": [], "incompatible": []},
    }

    result = dashboard._run_plan_preparation_status(
        plan,
        "sha256:" + "7" * 64,
        health,
        expected_desired=current_desired,
    )

    assert result["state"] == "blocked"
    assert result["topology_identity"] is False


@pytest.mark.parametrize(
    "observed,expected",
    [
        ({}, {}),
        ({"plan_sha256": None}, {"plan_sha256": None}),
        ({"plan_sha256": ""}, {"plan_sha256": ""}),
        ({"plan_sha256": "sha256:nope"}, {"plan_sha256": "sha256:nope"}),
    ],
    ids=("missing", "null", "empty", "malformed"),
)
def test_missing_or_malformed_plan_identity_never_earns_fast_path_reuse(
    observed, expected
):
    from profile_manager import dashboard

    assert dashboard._desired_topology_reusable(observed, expected) is False


def test_legacy_desired_requires_exact_complete_retained_substrate():
    from profile_manager import dashboard

    substrate = _catalog_enrichment_substrate()
    current = profiles_module.desired_topology_from_substrate(
        substrate["profile_id"], substrate
    )
    legacy = {
        key: value
        for key, value in current.items()
        if key
        not in {
            "plan_sha256",
            "deployment_sha256",
            "catalog_reuse_metadata",
        }
    }
    legacy_body = json.dumps(
        legacy, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    legacy["plan_sha256"] = "sha256:" + hashlib.sha256(legacy_body).hexdigest()
    snapshot = {
        "profile_id": substrate["profile_id"],
        "desired": legacy,
        "persisted": {
            "profile_id": substrate["profile_id"],
            "substrate": substrate,
        },
    }

    upgraded = dashboard._observed_desired_with_retained_identity(snapshot)
    assert upgraded["deployment_sha256"] == current["deployment_sha256"]
    assert upgraded["catalog_reuse_metadata"] == current["catalog_reuse_metadata"]

    tampered = json.loads(json.dumps(snapshot))
    tampered["persisted"]["substrate"]["nodes"][0]["version"] = "wrong"
    assert dashboard._observed_desired_with_retained_identity(tampered) == legacy

    missing = json.loads(json.dumps(snapshot))
    missing["persisted"].pop("substrate")
    assert dashboard._observed_desired_with_retained_identity(missing) == legacy

    foreign_substrate = json.loads(json.dumps(substrate))
    foreign_substrate["profile_id"] = "profile-foreign"
    foreign_current = profiles_module.desired_topology_from_substrate(
        substrate["profile_id"], foreign_substrate
    )
    foreign_legacy = {
        key: value
        for key, value in foreign_current.items()
        if key
        not in {
            "plan_sha256",
            "deployment_sha256",
            "catalog_reuse_metadata",
        }
    }
    foreign_body = json.dumps(
        foreign_legacy, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    foreign_legacy["plan_sha256"] = (
        "sha256:" + hashlib.sha256(foreign_body).hexdigest()
    )
    foreign_snapshot = json.loads(json.dumps(snapshot))
    foreign_snapshot["desired"] = foreign_legacy
    foreign_snapshot["persisted"]["substrate"] = foreign_substrate
    assert (
        dashboard._observed_desired_with_retained_identity(foreign_snapshot)
        == foreign_legacy
    )


def test_malformed_retained_platform_metadata_is_not_historical_absence():
    from profile_manager import dashboard

    retained = _catalog_enrichment_substrate()
    retained_artifact = retained["catalog_snapshot"]["selected_releases"][0][
        "artifacts"
    ][0]
    retained_artifact.update(public=True, platforms="linux/amd64")
    current = json.loads(json.dumps(retained))
    current["catalog_revision"] = "d" * 64
    current["catalog_snapshot"]["catalog_revision"] = "d" * 64
    current_artifact = current["catalog_snapshot"]["selected_releases"][0][
        "artifacts"
    ][0]
    current_artifact["platforms"] = ["linux/amd64", "linux/arm64"]
    retained_desired = profiles_module.desired_topology_from_substrate(
        retained["profile_id"], retained
    )
    current_desired = profiles_module.desired_topology_from_substrate(
        current["profile_id"], current
    )

    assert retained_desired["deployment_sha256"] == current_desired[
        "deployment_sha256"
    ]
    assert (
        dashboard._desired_topology_reusable(retained_desired, current_desired)
        is False
    )


@pytest.mark.parametrize(
    "corrupt",
    [
        lambda release, artifact: release.update(source_revision="not-a-git-sha"),
        lambda release, artifact: artifact.update(digest="sha256:not-a-digest"),
        lambda release, artifact: artifact.update(reference="not a reference"),
        lambda release, artifact: artifact.update(
            platforms=["linux/amd64", "garbage"]
        ),
        lambda release, artifact: release["verification"]["amaru-only"].update(
            status="unknown", evidence=[]
        ),
        lambda release, artifact: artifact["qualification"].update(
            status="unverified"
        ),
        lambda release, artifact: artifact["qualification"].update(
            evidence_sha256="not-a-hash"
        ),
        lambda release, artifact: artifact.update(qualification=None),
    ],
    ids=(
        "source-revision",
        "oci-digest",
        "oci-reference",
        "platform-list",
        "verification-evidence",
        "artifact-qualification-status",
        "artifact-qualification-evidence",
        "artifact-qualification-null",
    ),
)
def test_malformed_or_unverified_catalog_identity_cannot_gain_reuse(corrupt):
    from profile_manager import dashboard

    retained = _catalog_enrichment_substrate()
    release = retained["catalog_snapshot"]["selected_releases"][0]
    artifact = release["artifacts"][0]
    corrupt(release, artifact)
    current = json.loads(json.dumps(retained))
    current["catalog_revision"] = "d" * 64
    current["catalog_snapshot"]["catalog_revision"] = "d" * 64
    current_release = current["catalog_snapshot"]["selected_releases"][0]
    current_artifact = current_release["artifacts"][0]
    current_artifact["public"] = True
    if "platforms" not in current_artifact:
        current_artifact["platforms"] = ["linux/amd64", "linux/arm64"]
    retained_desired = profiles_module.desired_topology_from_substrate(
        retained["profile_id"], retained
    )
    current_desired = profiles_module.desired_topology_from_substrate(
        current["profile_id"], current
    )

    assert retained_desired["deployment_sha256"] == current_desired[
        "deployment_sha256"
    ]
    assert (
        dashboard._desired_topology_reusable(retained_desired, current_desired)
        is False
    )


@pytest.mark.parametrize(
    ("path", "changed"),
    [
        (("catalog_snapshot", "selected_releases", 0, "source_revision"), "f" * 40),
        (
            ("catalog_snapshot", "selected_releases", 0, "artifacts", 0, "digest"),
            "sha256:" + "9" * 64,
        ),
        (
            (
                "catalog_snapshot",
                "selected_releases",
                0,
                "artifacts",
                0,
                "qualification",
                "evidence_sha256",
            ),
            "8" * 64,
        ),
        (("topology", "edges"), [{"from": "amaru1", "to": "bootstrap-cardano"}]),
        (("plutus_v2_genesis",), True),
        (("resource_limits", "memory"), "8g"),
    ],
)
def test_deployment_identity_rejects_runtime_and_policy_changes(path, changed):
    retained = _catalog_enrichment_substrate()
    candidate = json.loads(json.dumps(retained))
    cursor = candidate
    for component in path[:-1]:
        cursor = cursor[component]
    cursor[path[-1]] = changed

    retained_desired = profiles_module.desired_topology_from_substrate(
        retained["profile_id"], retained
    )
    candidate_desired = profiles_module.desired_topology_from_substrate(
        candidate["profile_id"], candidate
    )

    assert (
        retained_desired["deployment_sha256"]
        != candidate_desired["deployment_sha256"]
    )


@pytest.mark.parametrize(
    ("path", "changed"),
    [
        (("profile_id",), "profile-other"),
        (("nodes", 0, "image"), "example.invalid/changed@sha256:" + "9" * 64),
        (("nodes", 0, "source_revision"), "f" * 40),
        (("nodes", 0, "id"), "other-node"),
    ],
    ids=("profile", "image", "source", "node"),
)
def test_controller_reuse_predicate_rejects_immutable_runtime_changes(path, changed):
    retained = _catalog_enrichment_substrate()
    current = json.loads(json.dumps(retained))
    current["catalog_revision"] = "d" * 64
    current["catalog_snapshot"]["catalog_revision"] = "d" * 64
    artifact = current["catalog_snapshot"]["selected_releases"][0]["artifacts"][0]
    artifact.update(public=True, platforms=["linux/amd64", "linux/arm64"])
    cursor = current
    for component in path[:-1]:
        cursor = cursor[component]
    cursor[path[-1]] = changed

    assert profiles_module.deployment_substrates_reusable(retained, current) is False


def test_profile_probe_completion_timestamp_starts_ttl_at_authoritative_result(
    monkeypatch,
):
    desired, inventory, sample = _authoritative_profile_probe_fixture()
    clock = {"now": "2026-10-08T07:00:00Z"}
    _install_authoritative_profile_probe(
        monkeypatch, desired=desired, inventory=inventory, samples=[sample]
    )
    monkeypatch.setattr(
        operate_topology_health.time,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(
            AssertionError("authoritative result must not be sampled again")
        ),
    )
    monkeypatch.setattr(
        operate_topology_health, "_utc_now", lambda: clock["now"]
    )

    completed = operate_topology_health._default_probe(desired["profile_id"])

    assert completed["checked_at"] == "2026-10-08T07:00:00Z"
    assert completed["generated_at"] == "2026-10-08T07:00:00Z"
    assert completed["health"]["age_seconds"] == 0
    still_fresh = topology_snapshot_module.refresh_snapshot_freshness(
        completed, now="2026-10-08T07:00:30Z"
    )
    stale = topology_snapshot_module.refresh_snapshot_freshness(
        completed, now="2026-10-08T07:00:31Z"
    )
    assert still_fresh["health"]["fresh"] is True
    assert stale["reason_code"] == "health_evidence_stale"


def _authoritative_profile_probe_fixture(*, registration_id: str | None = None):
    substrate = json.loads(json.dumps(_catalog_enrichment_substrate()))
    profile_id = substrate["profile_id"]
    if registration_id is not None:
        overlay_revision = "sha256:" + "9" * 64
        substrate["operator_registration_id"] = registration_id
        substrate["operator_overlay_revision"] = overlay_revision
        substrate["catalog_snapshot"]["operator_registration_id"] = registration_id
        substrate["catalog_snapshot"]["operator_overlay_revision"] = overlay_revision
    desired = profiles_module.desired_topology_from_substrate(profile_id, substrate)
    persisted = {"profile_id": profile_id, "substrate": substrate}
    node = desired["nodes"][0]
    container_name = f"dwarf-install-{profile_id}-{node['id']}"
    inventory = {
        "available": True,
        "managed": [
            {
                "id": node["id"] + "-container-id",
                "name": container_name,
                "topology_id": profile_id,
                "node_id": node["id"],
                "implementation": node["implementation"],
                "version": node["version"],
                "image": node["image"],
                "state": "running",
            }
        ],
        "unmanaged": [],
    }
    observed_nodes = [
        {
            "node_id": node["id"],
            "implementation": node["implementation"],
            "version": node["version"],
            "image": node["image"],
            "runtime_image": node["image"],
            "state": "running",
            "container_name": container_name,
        }
    ]
    required_gates = ["chain_progress", "exact_identity"]
    controller_health = {
        "available": True,
        "schema_version": "v1",
        "topology_id": profile_id,
        "checked_at": NOW,
        "state": "healthy",
        "reason_code": "node_and_chain_healthy",
        "expected_nodes": 1,
        "converged": True,
        "persisted_deployment": persisted,
        "retained_evidence": {
            "profile_id": profile_id,
            "deployment_config_sha256": "sha256:" + "1" * 64,
            "compose_sha256": "sha256:" + "2" * 64,
            "semantic_report_sha256": "sha256:" + "3" * 64,
        },
        "nodes": [
            {
                "id": node["id"],
                "container_state": "running",
                "listener_ready": True,
                "chain_ready": True,
                "synchronized": True,
                "healthy": True,
            }
        ],
        "semantic_health": {
            "schema_version": 1,
            "profile_id": profile_id,
            "completed_at": NOW,
            "fresh_observation": True,
            "retained": True,
            "passed": True,
            "classification": "retained-runtime-ready",
            "required_gates": required_gates,
            "failed_gates": [],
            "gates": {name: True for name in required_gates},
            "identity_matched": True,
            "sample_count": 2,
        },
    }
    sample = {
        "enabled": True,
        "profile_id": profile_id,
        "transport": "controller",
        "persisted_deployment": persisted,
        "observed_nodes": observed_nodes,
        "topology_result": {
            "state": "healthy",
            "reason_code": "all_mixed_readiness_gates_passed",
            "checked_at": NOW,
            "controller_health": controller_health,
        },
    }
    return desired, inventory, sample


def _install_authoritative_profile_probe(monkeypatch, *, desired, inventory, samples):
    profile = SimpleNamespace(id=desired["profile_id"])
    monkeypatch.setattr(health_data, "controller_inventory", lambda: inventory)
    monkeypatch.setattr(profiles_module, "find_profile", lambda _profile_id: profile)
    monkeypatch.setattr(
        profiles_module,
        "desired_topology_for_profile",
        lambda _profile, _preview: desired,
    )
    monkeypatch.setattr(
        "profile_manager.deployment_versions.build_deployment_version_preview",
        lambda _data, **_kwargs: {"status": "confirmed"},
    )
    monkeypatch.setattr(
        health_data, "_live_health", lambda _profile_id=None: samples.pop(0)
    )
    monkeypatch.setattr(operate_topology_health, "_utc_now", lambda: NOW)


def test_profile_probe_publishes_authoritative_controller_result_without_second_sample(
    monkeypatch,
):
    desired, inventory, sample = _authoritative_profile_probe_fixture()
    calls = []
    _install_authoritative_profile_probe(
        monkeypatch, desired=desired, inventory=inventory, samples=[sample]
    )
    monkeypatch.setattr(
        health_data,
        "_live_health",
        lambda _profile_id=None: calls.append(_profile_id) or sample,
    )
    monkeypatch.setattr(
        operate_topology_health.time,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(
            AssertionError("authoritative controller result was sampled again")
        ),
    )
    monkeypatch.setattr(operate_topology_health, "_utc_now", lambda: NOW)

    result = operate_topology_health._default_probe(desired["profile_id"])

    assert calls == [desired["profile_id"]]
    assert result["state"] == "healthy"
    assert result["health"]["controller_health"]["semantic_health"]["sample_count"] == 2
    assert result["reconciliation"]["state"] == "converged"


@pytest.mark.parametrize(
    "invalid_case",
    [
        "bare-result",
        "non-controller",
        "stale",
        "wrong-profile",
        "sample-count",
        "missing-semantic-schema",
        "wrong-semantic-schema",
        "boolean-semantic-schema",
        "float-semantic-schema",
        "missing-gates",
        "malformed-gates",
        "failed-gate",
        "failed-node-gate",
        "invalid-state",
        "wrong-registration",
    ],
)
def test_profile_probe_never_fast_paths_untrusted_controller_result(
    monkeypatch, invalid_case,
):
    desired, inventory, first = _authoritative_profile_probe_fixture(
        registration_id="registration-selected"
    )
    first = json.loads(json.dumps(first))
    topology_result = first["topology_result"]
    controller = topology_result["controller_health"]
    semantic = controller["semantic_health"]
    if invalid_case == "bare-result":
        first["topology_result"] = {"state": "healthy"}
    elif invalid_case == "non-controller":
        first["transport"] = "ssh"
    elif invalid_case == "stale":
        topology_result["checked_at"] = "2026-10-08T06:59:29Z"
        controller["checked_at"] = "2026-10-08T06:59:29Z"
        semantic["completed_at"] = "2026-10-08T06:59:29Z"
    elif invalid_case == "wrong-profile":
        controller["topology_id"] = "profile-other"
    elif invalid_case == "sample-count":
        semantic["sample_count"] = 1
    elif invalid_case == "missing-semantic-schema":
        semantic.pop("schema_version")
    elif invalid_case == "wrong-semantic-schema":
        semantic["schema_version"] = "1"
    elif invalid_case == "boolean-semantic-schema":
        semantic["schema_version"] = True
    elif invalid_case == "float-semantic-schema":
        semantic["schema_version"] = 1.0
    elif invalid_case == "missing-gates":
        semantic.pop("gates")
    elif invalid_case == "malformed-gates":
        semantic["required_gates"] = [{"not": "a gate name"}]
    elif invalid_case == "failed-gate":
        semantic["gates"]["chain_progress"] = False
        semantic["failed_gates"] = ["chain_progress"]
        semantic["passed"] = False
    elif invalid_case == "failed-node-gate":
        controller["nodes"][0]["chain_ready"] = False
    elif invalid_case == "invalid-state":
        topology_result["state"] = "ready-ish"
        controller["state"] = "ready-ish"
    elif invalid_case == "wrong-registration":
        retained = first["persisted_deployment"]["substrate"]
        retained["operator_registration_id"] = "registration-other"
        retained["operator_overlay_revision"] = "sha256:" + "8" * 64
        retained["catalog_snapshot"]["operator_registration_id"] = (
            "registration-other"
        )
        retained["catalog_snapshot"]["operator_overlay_revision"] = (
            "sha256:" + "8" * 64
        )
    second = {
        "enabled": False,
        "profile_id": desired["profile_id"],
        "error": "second sample unavailable",
    }
    samples = [first, second]
    sleeps = []
    _install_authoritative_profile_probe(
        monkeypatch, desired=desired, inventory=inventory, samples=samples
    )
    monkeypatch.setattr(
        operate_topology_health.time, "sleep", lambda seconds: sleeps.append(seconds)
    )

    result = operate_topology_health._default_probe(
        desired["profile_id"],
        operator_registration_id="registration-selected",
    )

    assert sleeps == [10]
    assert samples == []
    assert result["state"] != "healthy"


def test_profile_probe_uses_fresh_controller_completion_not_probe_start(
    monkeypatch,
):
    desired, inventory, sample = _authoritative_profile_probe_fixture()
    sample["topology_result"]["controller_health"]["checked_at"] = (
        "2026-10-08T06:57:50Z"
    )
    samples = [sample]
    _install_authoritative_profile_probe(
        monkeypatch, desired=desired, inventory=inventory, samples=samples
    )
    monkeypatch.setattr(
        operate_topology_health.time,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(
            AssertionError("fresh completed controller result was sampled again")
        ),
    )

    result = operate_topology_health._default_probe(desired["profile_id"])

    assert samples == []
    assert result["state"] == "healthy"
    assert result["checked_at"] == NOW


def test_profile_probe_binds_operator_registration_to_desired_identity(monkeypatch):
    profile = SimpleNamespace(id="local-lab")
    captured = []
    monkeypatch.setattr(
        health_data,
        "controller_inventory",
        lambda: {"available": True, "managed": [], "unmanaged": []},
    )
    monkeypatch.setattr(profiles_module, "find_profile", lambda _profile_id: profile)
    monkeypatch.setattr(
        "profile_manager.deployment_versions.build_deployment_version_preview",
        lambda _data, **kwargs: captured.append(kwargs) or {"status": "registered"},
    )
    monkeypatch.setattr(
        profiles_module,
        "desired_topology_for_profile",
        lambda _profile, _preview: _desired(),
    )

    result = operate_topology_health._default_probe(
        "local-lab",
        operator_registration_id="registration-local-amaru",
    )

    assert result["desired"] == _desired()
    assert captured == [
        {"operator_registration_id": "registration-local-amaru"}
    ]


def test_profile_probe_keeps_second_sample_for_missing_controller_result(monkeypatch):
    profile = SimpleNamespace(id="local-lab")
    inventory = {
        "available": True,
        "managed": [_managed("node-1"), _managed("amaru-1")],
        "unmanaged": [],
    }
    samples = [
        {
            "enabled": True,
            "profile_id": "local-lab",
            "persisted_deployment": None,
            "observed_nodes": [],
            "topology_result": {},
        },
        {
            "enabled": False,
            "profile_id": "local-lab",
            "error": "second sample unavailable",
        },
    ]
    sleeps = []
    monkeypatch.setattr(health_data, "controller_inventory", lambda: inventory)
    monkeypatch.setattr(profiles_module, "find_profile", lambda _profile_id: profile)
    monkeypatch.setattr(
        profiles_module,
        "desired_topology_for_profile",
        lambda _profile, _preview: _desired(),
    )
    monkeypatch.setattr(
        "profile_manager.deployment_versions.build_deployment_version_preview",
        lambda _data, **_kwargs: {"status": "confirmed"},
    )
    monkeypatch.setattr(
        health_data, "_live_health", lambda _profile_id=None: samples.pop(0)
    )
    monkeypatch.setattr(
        operate_topology_health.time, "sleep", lambda seconds: sleeps.append(seconds)
    )

    result = operate_topology_health._default_probe("local-lab")

    assert sleeps == [10]
    assert samples == []
    assert result["state"] == "unknown"
    assert result["health"]["reason_code"] == "active_profile_second_sample_failed"


def test_tampered_retained_identity_cannot_become_current_desired(monkeypatch):
    profile = SimpleNamespace(id="local-lab")
    catalog_desired = _desired()
    persisted = {
        "profile_id": "local-lab",
        "substrate": {
            "profile_id": "local-lab",
            "nodes": json.loads(json.dumps(catalog_desired["nodes"])),
        },
    }
    persisted["substrate"]["nodes"][1]["version"] = "9.9.9"
    inventory = {
        "available": True,
        "managed": [_managed("node-1"), _managed("amaru-1", image=IMAGE_A)],
        "unmanaged": [],
    }
    observed_nodes = [
        {
            "node_id": "node-1",
            "implementation": "cardano-node",
            "version": "10.7.1",
            "image": IMAGE_A,
            "runtime_image": IMAGE_A,
            "state": "running",
            "container_name": "dwarf-install-local-lab-node-1",
        },
        {
            "node_id": "amaru-1",
            "implementation": "amaru",
            "version": "0.3.0",
            "image": IMAGE_B,
            "runtime_image": IMAGE_A,
            "state": "running",
            "container_name": "dwarf-install-local-lab-amaru-1",
        },
    ]
    sample = {
        "enabled": True,
        "profile_id": "local-lab",
        "transport": "controller",
        "persisted_deployment": persisted,
        "observed_nodes": observed_nodes,
        "topology_result": {
            "state": "healthy",
            "reason_code": "node_and_chain_healthy",
            "checked_at": NOW,
        },
    }
    monkeypatch.setattr(health_data, "controller_inventory", lambda: inventory)
    monkeypatch.setattr(profiles_module, "find_profile", lambda _profile_id: profile)
    monkeypatch.setattr(
        profiles_module,
        "desired_topology_for_profile",
        lambda _profile, _preview: catalog_desired,
    )
    monkeypatch.setattr(
        "profile_manager.deployment_versions.build_deployment_version_preview",
        lambda _data: {"status": "confirmed"},
    )
    monkeypatch.setattr(health_data, "_live_health", lambda _profile_id=None: sample)
    monkeypatch.setattr(operate_topology_health.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(operate_topology_health, "_utc_now", lambda: NOW)

    result = operate_topology_health._default_probe("local-lab")

    assert result["state"] == "unhealthy"
    assert result["reconciliation"]["state"] == "drifted"
    assert result["reconciliation"]["reason_code"] == "persisted_plan_drift"
    assert result["desired"] == catalog_desired


def test_controller_identity_inventory_binding_failures_never_reconcile_healthy():
    desired = _desired()
    persisted = {
        "substrate": {
            "profile_id": "local-lab",
            "nodes": desired["nodes"],
        }
    }
    inventory = {
        "managed": [_managed("node-1"), _managed("amaru-1")],
        "unmanaged": [],
    }
    valid = [
        {
            "node_id": row["node_id"],
            "implementation": row["implementation"],
            "version": row["version"],
            "image": row["image"],
            "runtime_image": row["image"],
            "state": "running",
            "container_name": row["name"],
        }
        for row in inventory["managed"]
    ]
    invalid_mappings = [
        [{**valid[0], "container_name": "wrong-container"}, valid[1]],
        [valid[0]],
        [valid[0], valid[1], dict(valid[1])],
    ]

    for observed_nodes in invalid_mappings:
        sample = {
            "enabled": True,
            "profile_id": "local-lab",
            "transport": "controller",
            "persisted_deployment": persisted,
            "observed_nodes": observed_nodes,
        }
        result = operate_topology_health.build_current_topology_snapshot(
            desired=desired,
            first=sample,
            second=None,
            classified_health={
                "state": "healthy",
                "reason_code": "node_and_chain_healthy",
                "checked_at": NOW,
            },
            inventory=inventory,
            generated_at=NOW,
        )

        assert result["state"] == "unhealthy"
        assert result["reason_code"] == "controller_identity_inventory_binding_failed"
        assert result["reconciliation"]["state"] == "unhealthy"


def test_controller_inventory_uses_typed_authenticated_request(monkeypatch):
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {
                    "installation_id": "install",
                    "managed": [],
                    "managed_count": 0,
                    "owned_resources": [
                        {
                            "id": "container-id",
                            "name": "dwarf-profile-p1-1",
                            "topology_id": "profile-a",
                            "component": "support",
                            "ownership": "managed",
                            "mutation_allowed": True,
                        }
                    ],
                    "owned_resource_count": 1,
                    "unmanaged": [],
                    "unmanaged_count": 0,
                    "unmanaged_total_count": 0,
                }
            ).encode()

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return Response()

    monkeypatch.setenv("DWARF_CONTROLLER_URL", "http://controller:8790")
    monkeypatch.setenv("DWARF_CONTROLLER_TOKEN", "t" * 32)
    monkeypatch.setenv("DWARF_INSTALLATION_ID", "install")
    monkeypatch.setattr(health_data, "urlopen", fake_urlopen)

    result = health_data.controller_inventory(timeout=4)

    assert result["available"] is True
    assert result["owned_resources"] == [
        {
            "id": "container-id",
            "name": "dwarf-profile-p1-1",
            "topology_id": "profile-a",
            "component": "support",
            "ownership": "managed",
            "mutation_allowed": True,
        }
    ]
    request, timeout = requests[0]
    assert timeout == 4
    assert request.full_url == "http://controller:8790/v1/requests"
    assert request.get_header("Authorization") == "Bearer " + "t" * 32
    payload = json.loads(request.data)
    assert payload["operation"] == "inventory.get"
    assert set(payload) == {
        "schema_version",
        "request_id",
        "operation",
        "timeout_seconds",
    }


def test_controller_inventory_rejects_ambiguous_success_payload(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"ok":false,"error":"internal failure"}'

    monkeypatch.setenv("DWARF_CONTROLLER_URL", "http://controller:8790")
    monkeypatch.setenv("DWARF_CONTROLLER_TOKEN", "t" * 32)
    monkeypatch.setenv("DWARF_INSTALLATION_ID", "install")
    monkeypatch.setattr(health_data, "urlopen", lambda _request, timeout: Response())

    result = health_data.controller_inventory()

    assert result == {
        "available": False,
        "managed": [],
        "unmanaged": [],
        "error": "controller inventory response is invalid",
    }


def test_controller_inventory_rejects_legacy_payload_without_all_owned_resources(
    monkeypatch,
):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {
                    "installation_id": "install",
                    "managed": [],
                    "managed_count": 0,
                    "unmanaged": [],
                    "unmanaged_count": 0,
                }
            ).encode()

    monkeypatch.setenv("DWARF_CONTROLLER_URL", "http://controller:8790")
    monkeypatch.setenv("DWARF_CONTROLLER_TOKEN", "t" * 32)
    monkeypatch.setenv("DWARF_INSTALLATION_ID", "install")
    monkeypatch.setattr(health_data, "urlopen", lambda _request, timeout: Response())

    result = health_data.controller_inventory()

    assert result["available"] is False
    assert result["error"] == "controller inventory response is invalid"


@pytest.mark.parametrize("component", ["node", "support"])
def test_controller_inventory_rejects_owned_container_without_topology_identity(
    monkeypatch, component
):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {
                    "installation_id": "install",
                    "managed": [],
                    "managed_count": 0,
                    "owned_resources": [
                        {
                            "id": "owned-id",
                            "name": f"owned-{component}",
                            "topology_id": "",
                            "component": component,
                            "ownership": "managed",
                            "mutation_allowed": True,
                        }
                    ],
                    "owned_resource_count": 1,
                    "unmanaged": [],
                    "unmanaged_count": 0,
                }
            ).encode()

    monkeypatch.setenv("DWARF_CONTROLLER_URL", "http://controller:8790")
    monkeypatch.setenv("DWARF_CONTROLLER_TOKEN", "t" * 32)
    monkeypatch.setenv("DWARF_INSTALLATION_ID", "install")
    monkeypatch.setattr(health_data, "urlopen", lambda _request, timeout: Response())

    result = health_data.controller_inventory()

    assert result["available"] is False
    assert result["error"] == "controller inventory response is invalid"


def test_controller_inventory_rejects_another_installation(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {
                    "installation_id": "some-other-installation",
                    "managed": [],
                    "managed_count": 0,
                    "unmanaged": [],
                    "unmanaged_count": 0,
                }
            ).encode()

    monkeypatch.setenv("DWARF_CONTROLLER_URL", "http://controller:8790")
    monkeypatch.setenv("DWARF_CONTROLLER_TOKEN", "t" * 32)
    monkeypatch.setenv("DWARF_INSTALLATION_ID", "expected-installation")
    monkeypatch.setattr(health_data, "urlopen", lambda _request, timeout: Response())

    result = health_data.controller_inventory()

    assert result == {
        "available": False,
        "managed": [],
        "unmanaged": [],
        "error": "controller inventory installation does not match",
    }


def test_controller_topology_health_allows_transport_margin_after_probe_deadline(
    monkeypatch,
):
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {
                    "topology_id": "profile-z",
                    "state": "healthy",
                    "nodes": [],
                }
            ).encode()

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return Response()

    monkeypatch.setenv("DWARF_CONTROLLER_URL", "http://controller:8790")
    monkeypatch.setenv("DWARF_CONTROLLER_TOKEN", "t" * 32)
    monkeypatch.setattr(health_data, "urlopen", fake_urlopen)

    result = health_data.controller_topology_health("profile-z")

    assert result["available"] is True
    request, transport_timeout = requests[0]
    payload = json.loads(request.data)
    # The native Amaru health adapter performs two fresh semantic samples and
    # is itself bounded at 120 seconds.  Keep the wire request and response
    # transport aligned with that existing bound.
    assert payload["timeout_seconds"] == 120
    assert transport_timeout == 125


def test_controller_semantic_sample_maps_to_bounded_profile_health(monkeypatch):
    monkeypatch.setenv("DWARF_CONTROLLER_URL", "http://controller:8790")
    monkeypatch.setenv("DWARF_CONTROLLER_TOKEN", "x" * 32)
    persisted = {
        "substrate": {
            "profile_id": "profile-e-haskell-preview-proof",
            "nodes": [{"id": "node1", "impl": "cardano-node"}],
        }
    }
    monkeypatch.setattr(
        health_data,
        "controller_topology_health",
        lambda profile_id: {
            "available": True,
            "topology_id": profile_id,
            "state": "healthy",
            "expected_nodes": 1,
            "persisted_deployment": persisted,
            "nodes": [
                {
                    "id": "node1",
                    "implementation": "cardano-node",
                    "container_state": "running",
                    "listener_ready": True,
                    "chain_ready": True,
                    "tip": {
                        "hash": "abc",
                        "slot": 12345,
                        "block": 678,
                        "syncProgress": "100.00",
                    },
                }
            ],
        },
    )

    result = health_data._controller_profile_health(
        "profile-e-haskell-preview-proof"
    )

    assert result["enabled"] is True
    assert result["expected_nodes"] == 1
    assert result["health"]["parsed"] == {
        "cardano_node_processes": 1,
        "socket_count": 1,
        "listener_count": 1,
        "loopback_only": "true",
        "tip_block": 678,
        "tip_slot": 12345,
        "sync_progress": "100.00",
    }
    assert result["persisted_deployment"] == persisted


def test_controller_semantic_sample_preserves_unhealthy_verdict_and_minimum_sync(
    monkeypatch,
):
    monkeypatch.setenv("DWARF_CONTROLLER_URL", "http://controller:8790")
    monkeypatch.setenv("DWARF_CONTROLLER_TOKEN", "x" * 32)
    monkeypatch.setattr(
        health_data,
        "controller_topology_health",
        lambda profile_id: {
            "available": True,
            "topology_id": profile_id,
            "state": "unhealthy",
            "reason_code": "node_chain_not_converged",
            "expected_nodes": 2,
            "persisted_deployment": {},
            "nodes": [
                {
                    "id": "node1",
                    "implementation": "cardano-node",
                    "container_state": "running",
                    "listener_ready": True,
                    "chain_ready": True,
                    "tip": {
                        "hash": "chain-a",
                        "slot": 100,
                        "block": 10,
                        "syncProgress": "100.00",
                    },
                },
                {
                    "id": "node2",
                    "implementation": "cardano-node",
                    "container_state": "running",
                    "listener_ready": True,
                    "chain_ready": True,
                    "tip": {
                        "hash": "chain-b",
                        "slot": 100,
                        "block": 10,
                        "syncProgress": "5.00",
                    },
                },
            ],
        },
    )

    result = health_data._controller_profile_health("local-lab")

    assert result["health"]["parsed"]["sync_progress"] == "5.00"
    assert result["topology_result"]["state"] == "unhealthy"
    assert result["topology_result"]["reason_code"] == "node_chain_not_converged"


def _controller_amaru_sample(*, target_image=IMAGE_B, runtime_image=IMAGE_A):
    target_id = "sha256:" + "d" * 64
    runtime_id = "sha256:" + "e" * 64
    persisted = {
        "profile_id": "amaru-profile",
        "compose_project": "dwarf-profile-install-amaru-profile",
        "substrate": {
            "profile_id": "amaru-profile",
            "deployment_adapter": "amaru-control",
            "network_magic": 42,
            "nodes": [
                {
                    "id": "amaru-1",
                    "impl": "amaru",
                    "version": "0.3.0",
                    "image": target_image,
                    "role": "producer",
                    "supporting": False,
                }
            ],
        },
    }
    return {
        "available": True,
        "topology_id": "amaru-profile",
        "state": "healthy",
        "reason_code": "node_and_chain_healthy",
        "checked_at": NOW,
        "expected_nodes": 1,
        "persisted_deployment": persisted,
        "retained_evidence": {
            "profile_id": "amaru-profile",
            "deployment_config_sha256": "sha256:" + "1" * 64,
            "compose_sha256": "sha256:" + "2" * 64,
            "semantic_report_sha256": "sha256:" + "3" * 64,
        },
        "nodes": [
            {
                "id": "amaru-1",
                "implementation": "amaru",
                "version": "0.3.0",
                "image": target_image,
                "service": "amaru-relay-1",
                "container_name": "dwarf-profile-install-amaru-profile-amaru-relay-1-1",
                "container_state": "running",
                "listener_ready": True,
                "chain_ready": True,
                "synchronized": True,
                "healthy": True,
                "runtime_image": runtime_image,
                "runtime_image_id": runtime_id,
                "runtime_container_image_id": runtime_id,
                "target_image_id": target_id,
                "artifact_image_id": target_id,
                "wrapper_matched": True,
            }
        ],
    }


def _controller_imported_amaru_sample():
    sample = _controller_amaru_sample(target_image=DIGEST_B, runtime_image=IMAGE_A)
    configured = sample["persisted_deployment"]["substrate"]["nodes"][0]
    configured.update(
        {
            "target_mode": "patched",
            "image_digest": DIGEST_B,
            "runtime_probe_image": IMAGE_A,
            "source_image_reference": "dwarf/amaru-measurement@" + DIGEST_C,
            "image_manifest_digest": DIGEST_C,
            "image_config_digest": DIGEST_B,
            "qualification_image_id": DIGEST_A,
            "transfer_source_installation_id": "install-source-1234",
            "transfer_manifest_sha256": "sha256:" + "4" * 64,
        }
    )
    observed = sample["nodes"][0]
    observed["target_image_id"] = DIGEST_B
    observed["artifact_image_id"] = DIGEST_B
    return sample


def _controller_operator_registered_amaru_sample(tmp_path, monkeypatch):
    registration = {
        "registration_id": "registration-local-amaru",
        "implementation": "amaru",
        "version": "10.11.20260912-local",
        "image_reference": "ghcr.io/example/amaru@" + DIGEST_C,
        "manifest_digest": DIGEST_C,
        "config_image_id": DIGEST_B,
        "platform": "linux/amd64",
        "runtime_interface": "extracted-binary",
        "inspected_at": NOW,
        "provenance": {
            "source_revision": None,
            "public": None,
            "qualification": "unverified",
        },
    }
    ledger = OperatorVersionDecisionLedger(tmp_path)
    initial = ledger.read()
    state = ledger.append_registration(
        registration, expected_revision=initial["revision"]
    )
    monkeypatch.setenv("ADA2_DWARF_VERSION_CATALOG_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("DWARF_HOST_ARCH", "amd64")
    sample = _controller_amaru_sample(
        target_image=DIGEST_B, runtime_image=IMAGE_A
    )
    configured = sample["persisted_deployment"]["substrate"]["nodes"][0]
    configured.update(
        {
            "target_mode": "stock",
            "version": registration["version"],
            "amaru_runtime_interface": registration["runtime_interface"],
            "operator_registration": registration,
        }
    )
    substrate = sample["persisted_deployment"]["substrate"]
    substrate.update(
        {
            "operator_registration_id": registration["registration_id"],
            "operator_overlay_revision": state["revision"],
            "catalog_snapshot": {
                "operator_registration_id": registration["registration_id"],
                "operator_overlay_revision": state["revision"],
            },
        }
    )
    observed = sample["nodes"][0]
    observed.update(
        {
            "version": registration["version"],
            "target_image_id": DIGEST_B,
            "artifact_image_id": DIGEST_B,
        }
    )
    return sample, registration, state["revision"]


def test_operator_registered_stock_amaru_identity_publishes_exact_observed_node(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("DWARF_CONTROLLER_URL", "http://controller:8790")
    monkeypatch.setenv("DWARF_CONTROLLER_TOKEN", "x" * 32)
    sample, registration, _revision = _controller_operator_registered_amaru_sample(
        tmp_path, monkeypatch
    )
    monkeypatch.setattr(
        health_data, "controller_topology_health", lambda _profile_id: sample
    )

    joined = health_data._controller_profile_health("amaru-profile")

    assert joined["enabled"] is True
    assert joined["observed_nodes"] == [
        {
            "node_id": "amaru-1",
            "implementation": "amaru",
            "version": registration["version"],
            "image": registration["config_image_id"],
            "runtime_image": IMAGE_A,
            "state": "running",
            "container_name": (
                "dwarf-profile-install-amaru-profile-amaru-relay-1-1"
            ),
        }
    ]


def test_operator_registered_stock_probe_is_ready_without_wrapper_drift(
    monkeypatch,
):
    from profile_manager import dashboard

    registration_id = "registration-local-amaru"
    desired, inventory, sample = _authoritative_profile_probe_fixture(
        registration_id=registration_id
    )
    samples = [sample]
    _install_authoritative_profile_probe(
        monkeypatch, desired=desired, inventory=inventory, samples=samples
    )
    monkeypatch.setattr(
        operate_topology_health.time,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(
            AssertionError("trusted controller result was sampled again")
        ),
    )

    snapshot = operate_topology_health._default_probe(
        desired["profile_id"],
        operator_registration_id=registration_id,
    )
    preparation = dashboard._run_plan_preparation_status(
        _profile_r_reuse_plan(desired["profile_id"]),
        "sha256:" + "7" * 64,
        snapshot,
        expected_desired=desired,
    )

    assert snapshot["state"] == "healthy"
    assert snapshot["reconciliation"]["state"] == "converged"
    assert {
        key: snapshot["observed"]["managed"][0][key]
        for key in ("node_id", "implementation", "version", "image", "state")
    } == {
        key: inventory["managed"][0][key]
        for key in ("node_id", "implementation", "version", "image", "state")
    }
    assert preparation["state"] == "ready"
    assert preparation["topology_identity"] is True


def test_operator_decision_revision_keeps_healthy_registered_topology_ready(
    monkeypatch, tmp_path
):
    """A decision-only ledger append must not discard frozen health evidence."""
    from profile_manager import dashboard

    monkeypatch.setenv("DWARF_CONTROLLER_URL", "http://controller:8790")
    monkeypatch.setenv("DWARF_CONTROLLER_TOKEN", "x" * 32)
    retained_sample, registration, retained_revision = (
        _controller_operator_registered_amaru_sample(tmp_path, monkeypatch)
    )
    retained_substrate = _catalog_enrichment_substrate()
    retained_substrate["profile_id"] = "amaru-profile"
    retained_substrate["operator_registration_id"] = registration[
        "registration_id"
    ]
    retained_substrate["operator_overlay_revision"] = retained_revision
    retained_substrate["catalog_snapshot"]["operator_registration_id"] = (
        registration["registration_id"]
    )
    retained_substrate["catalog_snapshot"]["operator_overlay_revision"] = (
        retained_revision
    )
    retained_substrate["nodes"] = [
        {
            "id": "amaru-1",
            "impl": "amaru",
            "version": registration["version"],
            "image": registration["config_image_id"],
            "role": "producer",
            "supporting": False,
            "target_mode": "stock",
            "amaru_runtime_interface": registration["runtime_interface"],
            "operator_registration": registration,
        }
    ]
    retained_sample["persisted_deployment"]["substrate"] = retained_substrate

    subject = {
        "implementation": "amaru",
        "version": registration["version"],
        "image_reference": registration["image_reference"],
        "manifest_digest": registration["manifest_digest"],
        "config_image_id": registration["config_image_id"],
        "scenario_id": "scenario-restart-readiness",
        "scenario_sha256": "c" * 64,
        "scope": "amaru-only",
        "topology_profile_id": retained_substrate["profile_id"],
        "deployment_identity_sha256": "d" * 64,
        "mixed_pair": None,
    }
    decision = {
        "decision_id": "decision-after-retained-deployment",
        "subject_key": operator_decisions.subject_key(subject),
        "subject": subject,
        "decision": "supported",
        "reason": "operator reviewed retained canonical evidence",
        "actor": "local-operator",
        "decided_at": "2026-10-08T07:01:00Z",
        "evidence": {
            "run_id": "run-after-retained-deployment",
            "manifest_sha256": "e" * 64,
            "operational_outcome": "completed",
            "assertion_verdict": "pass",
        },
    }
    current_state = OperatorVersionDecisionLedger(tmp_path).append_decision(
        decision, expected_revision=retained_revision
    )
    assert current_state["revision"] != retained_revision

    current_substrate = json.loads(json.dumps(retained_substrate))
    current_substrate["operator_overlay_revision"] = current_state["revision"]
    current_substrate["catalog_snapshot"]["operator_overlay_revision"] = (
        current_state["revision"]
    )
    current_desired = profiles_module.desired_topology_from_substrate(
        current_substrate["profile_id"], current_substrate
    )
    retained_desired = profiles_module.desired_topology_from_substrate(
        retained_substrate["profile_id"], retained_substrate
    )
    assert profiles_module.deployment_topologies_reusable(
        retained_desired, current_desired
    )

    observed = retained_sample["nodes"][0]
    inventory = {
        "available": True,
        "managed": [
            {
                "id": "container-amaru-1",
                "name": observed["container_name"],
                "topology_id": retained_substrate["profile_id"],
                "node_id": observed["id"],
                "implementation": observed["implementation"],
                "version": observed["version"],
                "image": observed["runtime_image"],
                "state": "running",
            }
        ],
        "unmanaged": [],
    }
    profile = SimpleNamespace(id=retained_substrate["profile_id"])
    monkeypatch.setattr(
        health_data, "controller_topology_health", lambda _profile_id: retained_sample
    )
    monkeypatch.setattr(health_data, "controller_inventory", lambda: inventory)
    monkeypatch.setattr(profiles_module, "find_profile", lambda _profile_id: profile)
    monkeypatch.setattr(
        profiles_module,
        "desired_topology_for_profile",
        lambda _profile, _preview: current_desired,
    )
    monkeypatch.setattr(
        "profile_manager.deployment_versions.build_deployment_version_preview",
        lambda _data, **_kwargs: {"status": "confirmed"},
    )
    monkeypatch.setattr(operate_topology_health, "_utc_now", lambda: NOW)

    snapshot = operate_topology_health._default_probe(
        retained_substrate["profile_id"],
        operator_registration_id=registration["registration_id"],
    )
    assert snapshot["state"] == "healthy"
    assert snapshot["reconciliation"]["state"] == "converged"
    assert snapshot["desired"]["operator_overlay_revision"] == retained_revision
    assert dashboard._desired_topology_reusable(snapshot["desired"], current_desired)

    plan = _profile_r_reuse_plan(retained_substrate["profile_id"])
    monkeypatch.setattr(
        dashboard,
        "_current_run_plan_desired_topology",
        lambda _plan: current_desired,
    )

    class NoMutation:
        def preview(self, *_args, **_kwargs):
            raise AssertionError("decision-only revision must not preview mutation")

        def apply(self, *_args, **_kwargs):
            raise AssertionError("decision-only revision must not apply mutation")

    events = list(
        dashboard._prepare_run_plan(
            plan,
            "sha256:" + "f" * 64,
            action_service=NoMutation(),
            snapshot_loader=lambda _profile_id: snapshot,
            health_requester=lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("fresh authoritative health must not be requested again")
            ),
        )
    )
    assert events[-1]["stage"] == "ready"
    assert events[-1]["state"] == "ready"


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong-overlay-revision",
        "wrong-config-id",
        "wrong-platform",
        "wrong-interface",
        "missing-node-interface",
        "wrong-node-interface",
        "wrong-embedded-registration",
        "wrong-observed-target",
    ],
)
def test_operator_registered_stock_amaru_identity_rejects_untrusted_binding(
    monkeypatch, tmp_path, mutation
):
    monkeypatch.setenv("DWARF_CONTROLLER_URL", "http://controller:8790")
    monkeypatch.setenv("DWARF_CONTROLLER_TOKEN", "x" * 32)
    sample, registration, _revision = _controller_operator_registered_amaru_sample(
        tmp_path, monkeypatch
    )
    substrate = sample["persisted_deployment"]["substrate"]
    configured = substrate["nodes"][0]
    if mutation == "wrong-overlay-revision":
        substrate["operator_overlay_revision"] = DIGEST_A
    elif mutation == "wrong-config-id":
        configured["image"] = DIGEST_A
    elif mutation == "wrong-platform":
        configured["operator_registration"] = {
            **registration,
            "platform": "linux/arm64",
        }
    elif mutation == "wrong-interface":
        configured["operator_registration"] = {
            **registration,
            "runtime_interface": "unknown",
        }
    elif mutation == "missing-node-interface":
        configured.pop("amaru_runtime_interface")
    elif mutation == "wrong-node-interface":
        configured["amaru_runtime_interface"] = "container-entrypoint"
    elif mutation == "wrong-embedded-registration":
        configured["operator_registration"] = {
            **registration,
            "registration_id": "registration-other",
        }
    elif mutation == "wrong-observed-target":
        sample["nodes"][0]["target_image_id"] = DIGEST_A
    monkeypatch.setattr(
        health_data, "controller_topology_health", lambda _profile_id: sample
    )

    joined = health_data._controller_profile_health("amaru-profile")

    assert joined["enabled"] is False
    assert joined["topology_result"]["reason_code"] == (
        "controller_identity_contract_invalid"
    )


def test_imported_amaru_identity_reconciles_desired_persisted_observed_and_wrapper(
    monkeypatch,
):
    monkeypatch.setenv("DWARF_CONTROLLER_URL", "http://controller:8790")
    monkeypatch.setenv("DWARF_CONTROLLER_TOKEN", "x" * 32)
    sample = _controller_imported_amaru_sample()
    monkeypatch.setattr(
        health_data,
        "controller_topology_health",
        lambda _profile_id: sample,
    )

    joined = health_data._controller_profile_health("amaru-profile")

    assert joined["enabled"] is True
    assert joined["observed_nodes"][0]["image"] == DIGEST_B
    assert joined["observed_nodes"][0]["runtime_image"] == IMAGE_A
    snapshot = operate_topology_health.build_current_topology_snapshot(
        desired={
            "schema_version": "v1",
            "topology_id": "amaru-profile",
            "profile_id": "amaru-profile",
            "nodes": [
                {
                    "id": "amaru-1",
                    "implementation": "amaru",
                    "version": "0.3.0",
                    "image": DIGEST_B,
                    "role": "producer",
                    "supporting": False,
                }
            ],
        },
        first=joined,
        second=None,
        classified_health={
            "state": "healthy",
            "reason_code": "node_and_chain_healthy",
            "checked_at": NOW,
        },
        inventory={
            "managed": [
                {
                    "id": "container-id",
                    "name": sample["nodes"][0]["container_name"],
                    "topology_id": "amaru-profile",
                    "node_id": "amaru-1",
                    "implementation": "amaru",
                    "version": "0.3.0",
                    "image": IMAGE_A,
                    "state": "running",
                }
            ],
            "unmanaged": [],
        },
        generated_at=NOW,
    )
    assert snapshot["state"] == "healthy"
    assert snapshot["reconciliation"]["state"] == "converged"
    assert snapshot["reconciliation"]["mismatched_nodes"] == []
    assert snapshot["reconciliation"]["persisted_plan_matches"] is True
    assert snapshot["desired"]["nodes"][0]["image"] == DIGEST_B
    assert snapshot["observed"]["managed"][0]["image"] == DIGEST_B
    assert snapshot["observed"]["managed"][0]["runtime_image"] == IMAGE_A


def test_imported_amaru_identity_rejects_incomplete_or_crossed_provenance(monkeypatch):
    monkeypatch.setenv("DWARF_CONTROLLER_URL", "http://controller:8790")
    monkeypatch.setenv("DWARF_CONTROLLER_TOKEN", "x" * 32)
    cases = []
    for field, value in (
        ("image_digest", DIGEST_A),
        ("image_manifest_digest", DIGEST_A),
        ("image_config_digest", DIGEST_A),
        ("qualification_image_id", "not-a-digest"),
        ("transfer_source_installation_id", "bad source id"),
        ("transfer_manifest_sha256", "not-a-digest"),
        ("runtime_probe_image", IMAGE_C),
        ("source_image_reference", "dwarf/amaru-measurement@" + DIGEST_A),
    ):
        sample = _controller_imported_amaru_sample()
        sample["persisted_deployment"]["substrate"]["nodes"][0][field] = value
        cases.append(sample)

    malformed_target = _controller_imported_amaru_sample()
    malformed_target["persisted_deployment"]["substrate"]["nodes"][0]["image"] = (
        "dwarf/amaru-measurement:latest"
    )
    malformed_target["nodes"][0]["image"] = "dwarf/amaru-measurement:latest"
    cases.append(malformed_target)

    wrong_target_config = _controller_imported_amaru_sample()
    wrong_target_config["nodes"][0]["target_image_id"] = DIGEST_A
    cases.append(wrong_target_config)

    wrong_artifact_config = _controller_imported_amaru_sample()
    wrong_artifact_config["nodes"][0]["artifact_image_id"] = DIGEST_A
    cases.append(wrong_artifact_config)

    bare_wrapper = _controller_imported_amaru_sample()
    bare_wrapper["persisted_deployment"]["substrate"]["nodes"][0][
        "runtime_probe_image"
    ] = DIGEST_A
    bare_wrapper["nodes"][0]["runtime_image"] = DIGEST_A
    cases.append(bare_wrapper)

    false_wrapper_match = _controller_imported_amaru_sample()
    false_wrapper_match["nodes"][0]["wrapper_matched"] = False
    cases.append(false_wrapper_match)

    for sample in cases:
        monkeypatch.setattr(
            health_data,
            "controller_topology_health",
            lambda _profile_id, sample=sample: sample,
        )
        result = health_data._controller_profile_health("amaru-profile")
        assert result["enabled"] is False
        assert result["topology_result"]["reason_code"] == (
            "controller_identity_contract_invalid"
        )


def test_imported_amaru_nonready_controller_sample_remains_blocked(monkeypatch):
    monkeypatch.setenv("DWARF_CONTROLLER_URL", "http://controller:8790")
    monkeypatch.setenv("DWARF_CONTROLLER_TOKEN", "x" * 32)
    sample = _controller_imported_amaru_sample()
    sample.update(
        {
            "state": "unhealthy",
            "reason_code": "node_chain_not_converged",
            "detail": "consumer is catching up",
        }
    )
    monkeypatch.setattr(
        health_data,
        "controller_topology_health",
        lambda _profile_id: sample,
    )

    joined = health_data._controller_profile_health("amaru-profile")

    assert joined["enabled"] is True
    assert joined["topology_result"]["state"] == "unhealthy"
    assert joined["topology_result"]["reason_code"] == "node_chain_not_converged"


def test_controller_semantic_sample_preserves_exact_amaru_probe(monkeypatch):
    monkeypatch.setenv("DWARF_CONTROLLER_URL", "http://controller:8790")
    monkeypatch.setenv("DWARF_CONTROLLER_TOKEN", "x" * 32)
    monkeypatch.setattr(
        health_data,
        "controller_topology_health",
        lambda _profile_id: _controller_amaru_sample(),
    )

    result = health_data._controller_profile_health("amaru-profile")

    assert result["enabled"] is True
    assert result["transport"] == "controller"
    assert result["topology_result"]["state"] == "healthy"
    assert result["persisted_deployment"]["substrate"]["nodes"][0]["image"] == IMAGE_B
    assert result["observed_nodes"] == [
        {
            "node_id": "amaru-1",
            "implementation": "amaru",
            "version": "0.3.0",
            "image": IMAGE_B,
            "runtime_image": IMAGE_A,
            "state": "running",
            "container_name": "dwarf-profile-install-amaru-profile-amaru-relay-1-1",
        }
    ]


def test_controller_semantic_sample_uses_completed_observation_timestamp(monkeypatch):
    monkeypatch.setenv("DWARF_CONTROLLER_URL", "http://controller:8790")
    monkeypatch.setenv("DWARF_CONTROLLER_TOKEN", "x" * 32)
    sample = _controller_amaru_sample()
    sample["checked_at"] = "2026-10-08T06:57:50Z"
    sample["semantic_health"] = {
        "schema_version": 1,
        "profile_id": "amaru-profile",
        "completed_at": NOW,
    }
    monkeypatch.setattr(
        health_data, "controller_topology_health", lambda _profile_id: sample
    )

    result = health_data._controller_profile_health("amaru-profile")

    assert result["topology_result"]["checked_at"] == NOW
    assert result["topology_result"]["controller_health"]["checked_at"] == (
        "2026-10-08T06:57:50Z"
    )


def test_controller_amaru_identity_contract_rejects_wrong_wrapper_target_or_config(
    monkeypatch,
):
    monkeypatch.setenv("DWARF_CONTROLLER_URL", "http://controller:8790")
    monkeypatch.setenv("DWARF_CONTROLLER_TOKEN", "x" * 32)
    bad_samples = []

    wrong_wrapper = _controller_amaru_sample()
    wrong_wrapper["nodes"][0]["runtime_container_image_id"] = DIGEST_B
    bad_samples.append(wrong_wrapper)

    wrong_target = _controller_amaru_sample()
    wrong_target["nodes"][0]["artifact_image_id"] = DIGEST_A
    bad_samples.append(wrong_target)

    wrong_config = _controller_amaru_sample()
    wrong_config["persisted_deployment"]["substrate"]["nodes"][0]["version"] = "9.9.9"
    bad_samples.append(wrong_config)

    for sample in bad_samples:
        monkeypatch.setattr(
            health_data, "controller_topology_health", lambda _profile_id, sample=sample: sample
        )
        result = health_data._controller_profile_health("amaru-profile")
        assert result["enabled"] is False
        assert result["topology_result"]["state"] == "unknown"
        assert result["topology_result"]["reason_code"] == "controller_identity_contract_invalid"


def test_live_health_retains_persisted_deployment_file(monkeypatch):
    profile = SimpleNamespace(
        id="local-lab",
        remote_runtime_root="/safe/runtime",
        node_count=1,
        amaru_node_count=0,
    )
    captured = []
    monkeypatch.setattr(health_data, "config_exists", lambda: True)
    monkeypatch.setattr(health_data, "load_config", lambda: SimpleNamespace(host="localhost"))
    monkeypatch.setattr(health_data, "_is_local_host", lambda _host: True)
    monkeypatch.setattr(health_data, "load_profiles", lambda: [profile])
    monkeypatch.setattr(
        profiles_module, "deployment_adapter_for_profile", lambda _profile: "docker"
    )
    monkeypatch.setattr("profile_manager.remote.control_shim_enabled", lambda: False)
    monkeypatch.setattr(health_data, "inspect_health_command", lambda _root: "echo HEALTH")

    def local(command, timeout):
        captured.append((command, timeout))
        body = "DWARF_PERSISTED_DEPLOYMENT=" + json.dumps(
            {"topology_id": "local-lab", "nodes": []}
        )
        return SimpleNamespace(returncode=0, stdout=body, stderr="")

    monkeypatch.setattr(health_data, "_local_command", local)

    result = health_data._live_health("local-lab")

    assert "versioned-deployment.json" in captured[0][0]
    assert result["persisted_deployment"] == {
        "topology_id": "local-lab",
        "nodes": [],
    }


def test_requested_absent_profile_returns_versioned_snapshot_without_slow_probe(monkeypatch):
    profile = SimpleNamespace(id="local-lab")
    monkeypatch.setattr(health_data, "controller_inventory", lambda: {
        "available": True,
        "managed": [],
        "unmanaged": [],
    })
    monkeypatch.setattr(profiles_module, "find_profile", lambda _profile_id: profile)
    monkeypatch.setattr(
        profiles_module, "desired_topology_for_profile", lambda _profile, _preview: _desired()
    )
    monkeypatch.setattr(
        "profile_manager.deployment_versions.build_deployment_version_preview",
        lambda _data: {"status": "confirmed"},
    )
    monkeypatch.setattr(
        health_data,
        "_live_health",
        lambda _profile_id=None: (_ for _ in ()).throw(AssertionError("slow probe ran")),
    )

    result = operate_topology_health._default_probe("local-lab")

    assert result["schema_version"] == "v1"
    assert result["profile_id"] == "local-lab"
    assert result["reconciliation"]["state"] == "absent"


def test_unscoped_empty_controller_reports_no_active_without_legacy_config(monkeypatch):
    monkeypatch.setattr(health_data, "controller_inventory", lambda: {
        "available": True,
        "managed": [],
        "unmanaged": [],
    })
    monkeypatch.setattr(
        health_data,
        "_live_health",
        lambda _profile_id=None: (_ for _ in ()).throw(
            AssertionError("legacy config probe ran")
        ),
    )

    result = operate_topology_health._default_probe()

    assert result["schema_version"] == "v1"
    assert result["state"] == "no_active"
    assert result["reason_code"] == "no_managed_resources"
    assert result["observed"]["managed"] == []
    assert result["actions"]["deploy"] == {
        "allowed": False,
        "reason": "select a topology before deploy",
        "confirmation_required": False,
    }


def test_unscoped_unavailable_controller_inventory_fails_closed(monkeypatch):
    monkeypatch.setattr(health_data, "controller_inventory", lambda: {
        "available": False,
        "managed": [],
        "unmanaged": [],
        "error": "controller inventory request failed",
    })
    monkeypatch.setattr(
        health_data,
        "_live_health",
        lambda _profile_id=None: (_ for _ in ()).throw(
            AssertionError("legacy config probe ran")
        ),
    )

    result = operate_topology_health._default_probe()

    assert result["schema_version"] == "v1"
    assert result["state"] == "unknown"
    assert result["reason_code"] == "inventory_unavailable"
    assert result["detail"] == "controller inventory request failed"
    assert result["observed"] == {"managed": [], "unmanaged": []}
    for action in ("deploy", "repair", "redeploy", "remove", "save"):
        assert result["actions"][action] == {
            "allowed": False,
            "reason": "controller inventory is unavailable",
            "confirmation_required": False,
        }


def test_unscoped_controller_inventory_selects_the_one_managed_topology(monkeypatch):
    profile = SimpleNamespace(id="local-lab")
    inventory = {
        "available": True,
        "managed": [_managed("node-1")],
        "unmanaged": [],
    }
    health_calls = []
    monkeypatch.setattr(health_data, "controller_inventory", lambda: inventory)
    monkeypatch.setattr(profiles_module, "find_profile", lambda _profile_id: profile)
    monkeypatch.setattr(
        profiles_module,
        "desired_topology_for_profile",
        lambda _profile, _preview: _desired(),
    )
    monkeypatch.setattr(
        "profile_manager.deployment_versions.build_deployment_version_preview",
        lambda _data: {"status": "confirmed"},
    )
    monkeypatch.setattr(
        health_data,
        "_live_health",
        lambda profile_id=None: health_calls.append(profile_id)
        or {
            "enabled": False,
            "profile_id": profile_id,
            "persisted_deployment": None,
            "topology_result": {
                "state": "unhealthy",
                "reason_code": "managed_resources_not_running",
                "checked_at": NOW,
            },
        },
    )

    result = operate_topology_health._default_probe()

    assert health_calls == ["local-lab"]
    assert result["profile_id"] == "local-lab"
    assert result["topology_id"] == "local-lab"


def test_unscoped_multiple_managed_topologies_fail_closed(monkeypatch):
    second = {
        **_managed("node-1"),
        "id": "other-node-1-container-id",
        "name": "dwarf-install-other-node-1",
        "topology_id": "other",
    }
    monkeypatch.setattr(health_data, "controller_inventory", lambda: {
        "available": True,
        "managed": [_managed("node-1"), second],
        "unmanaged": [],
    })
    monkeypatch.setattr(
        health_data,
        "_live_health",
        lambda _profile_id=None: (_ for _ in ()).throw(
            AssertionError("ambiguous legacy config probe ran")
        ),
    )

    result = operate_topology_health._default_probe()

    assert result["state"] == "unknown"
    assert result["reason_code"] == "multiple_managed_topologies"
    assert {row["topology_id"] for row in result["observed"]["managed"]} == {
        "local-lab",
        "other",
    }
    for action in ("deploy", "repair", "redeploy", "remove", "save"):
        assert result["actions"][action]["allowed"] is False


def test_unavailable_inventory_cannot_be_reported_as_observed_absence(monkeypatch):
    profile = SimpleNamespace(id="local-lab")
    monkeypatch.setattr(health_data, "controller_inventory", lambda: {
        "available": False,
        "managed": [],
        "unmanaged": [],
        "error": "controller inventory request failed",
    })
    monkeypatch.setattr(profiles_module, "find_profile", lambda _profile_id: profile)
    monkeypatch.setattr(
        profiles_module, "desired_topology_for_profile", lambda _profile, _preview: _desired()
    )
    monkeypatch.setattr(
        "profile_manager.deployment_versions.build_deployment_version_preview",
        lambda _data: {"status": "confirmed"},
    )

    result = operate_topology_health._default_probe("local-lab")

    assert result["state"] == "unknown"
    assert result["reason_code"] == "inventory_unavailable"
    assert result["reconciliation"] == {
        **result["reconciliation"],
        "state": "unknown",
        "reason_code": "inventory_unavailable",
    }
    assert result["actions"]["deploy"] == {
        "allowed": False,
        "reason": "controller inventory is unavailable",
        "confirmation_required": False,
    }
