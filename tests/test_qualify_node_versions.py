import json
from pathlib import Path

import pytest

from scripts.qualify_node_versions import (
    PROTECTED_PROJECTS,
    QualificationError,
    build_project_name,
    candidate_matrix,
    classify_qualification,
    classify_terminal_runtime_failure,
    exact_oci_reference,
    identity_matches,
    propose_catalog_update,
    transform_compose_model,
)


def _release(implementation, version, released_at, *, channel="stable", digest="1" * 64):
    repository = (
        "ghcr.io/intersectmbo/cardano-node"
        if implementation == "cardano-node"
        else "ghcr.io/pragma-org/amaru"
    )
    tag = version if implementation == "cardano-node" else f"v{version}"
    return {
        "implementation": implementation,
        "version": version,
        "channel": channel,
        "released_at": released_at,
        "source_revision": "a" * 40,
        "artifacts": [{
            "kind": "oci",
            "reference": f"{repository}:{tag}",
            "availability": "available",
            "digest": f"sha256:{digest}",
        }],
        "verification": {},
    }


def _catalog():
    cardano_new = _release("cardano-node", "11.1.2", "2026-09-17T00:00:00Z", digest="1" * 64)
    cardano_old = _release("cardano-node", "10.7.1", "2026-04-15T00:00:00Z", digest="2" * 64)
    amaru_new = _release("amaru", "10.11.20260912", "2026-09-12T00:00:00Z", digest="3" * 64)
    amaru_old = _release("amaru", "10.11.0", "2026-09-03T00:00:00Z", digest="4" * 64)
    prerelease = _release("cardano-node", "12.0.0-rc1", "2026-09-18T00:00:00Z", channel="prerelease")
    return {
        "schema_version": 1,
        "updated_at": "2026-09-17T00:00:00Z",
        "releases": [cardano_old, prerelease, amaru_old, cardano_new, amaru_new],
        "compatibility_pairs": [],
    }


def _compose_model():
    cardano = {
        "image": "old-cardano",
        "container_name": "p1",
        "command": ["run"],
        "networks": {"default": None},
    }
    amaru = {
        "image": "old-amaru",
        "container_name": "amaru-relay-1",
        "entrypoint": ["/bin/sh"],
        "command": ["-c", "exec /bin/amaru node run --network testnet_42"],
        "networks": {"default": None, "amaru-consumer-net": None},
    }
    return {
        "services": {
            "configurator": {"image": "configurator", "container_name": "configurator"},
            "tracer": {"image": "tracer", "container_name": "tracer"},
            "tracer-sidecar": {"image": "tracer-sidecar", "container_name": "tracer-sidecar"},
            "log-tailer": {"image": "log-tailer", "container_name": "log-tailer"},
            "sidecar": {"image": "sidecar", "container_name": "sidecar"},
            "p1": dict(cardano),
            "p2": dict(cardano),
            "p3": dict(cardano),
            "relay1": dict(cardano),
            "relay2": dict(cardano),
            "amaru-consumer": dict(cardano),
            "bootstrap-producer": {"image": "bootstrap", "container_name": "bootstrap-producer"},
            "amaru-consumer-seed": {"image": "bootstrap", "container_name": "amaru-consumer-seed"},
            "amaru-relay-1": dict(amaru),
            "amaru-relay-2": dict(amaru),
        },
        "volumes": {"p1-state": {}, "a1-state": {}, "external": {"external": True}},
        "networks": {
            "default": {"name": "cardano-amaru-testnet", "driver": "bridge"},
            "amaru-consumer-net": {"name": "cardano-amaru-consumer-net", "driver": "bridge"},
        },
    }


def test_candidate_matrix_is_newest_to_oldest_and_ignores_prereleases():
    catalog = _catalog()

    cardano = candidate_matrix(catalog, "cardano-only")
    amaru = candidate_matrix(catalog, "amaru-only")
    mixed = candidate_matrix(catalog, "mixed")

    assert [item["cardano_version"] for item in cardano] == ["11.1.2", "10.7.1"]
    assert [item["amaru_version"] for item in amaru] == ["10.11.20260912", "10.11.0"]
    assert mixed[0]["cardano_version"] == "11.1.2"
    assert mixed[0]["amaru_version"] == "10.11.20260912"
    assert mixed[-1]["cardano_version"] == "10.7.1"
    assert mixed[-1]["amaru_version"] == "10.11.0"


def test_exact_oci_reference_uses_digest_without_mutable_tag():
    release = _catalog()["releases"][-2]
    assert exact_oci_reference(release) == (
        "ghcr.io/intersectmbo/cardano-node@sha256:" + "1" * 64
    )


def test_identity_match_requires_both_node_version_and_exact_running_image():
    assert identity_matches(
        expected_version="11.1.2",
        reported_version="cardano-node 11.1.2\ngit rev " + "a" * 40,
        expected_image_id="sha256:" + "1" * 64,
        running_image_id="sha256:" + "1" * 64,
    ) is True
    assert identity_matches(
        expected_version="11.1.2",
        reported_version="cardano-node 11.1.2",
        expected_image_id="sha256:" + "1" * 64,
        running_image_id="sha256:" + "2" * 64,
    ) is False


def test_project_names_are_unique_safe_and_never_the_live_project():
    first = build_project_name("mixed", "11.1.2", "10.11.20260912", token="abc123")
    second = build_project_name("mixed", "11.1.2", "10.11.20260912", token="def456")
    assert first != second
    assert first.startswith("dwarf-qual-mixed-")
    assert first not in PROTECTED_PROJECTS


def test_transform_mixed_is_namespaced_and_changes_only_target_artifacts():
    transformed = transform_compose_model(
        _compose_model(),
        scope="mixed",
        project="dwarf-qual-mixed-demo",
        cardano_image="new-cardano",
        amaru_image="new-amaru",
    )

    assert all("container_name" not in service for service in transformed["services"].values())
    assert transformed["networks"]["default"]["name"] == "dwarf-qual-mixed-demo-default"
    assert transformed["services"]["p1"]["image"] == "new-cardano"
    assert transformed["services"]["amaru-consumer"]["image"] == "new-cardano"
    assert transformed["services"]["amaru-relay-1"]["image"] == "new-amaru"
    assert transformed["services"]["amaru-relay-1"]["user"] == "0:0"
    assert transformed["services"]["amaru-relay-1"]["environment"]["AMARU_MIGRATE_CHAIN_DB"] == "true"
    assert "chown -R 10000:10000 /srv/amaru /startup /opt/amaru-logs" in transformed["services"]["amaru-relay-1"]["command"][1]
    assert "setpriv --reuid=10000 --regid=10000 --clear-groups /usr/local/bin/amaru run" in transformed["services"]["amaru-relay-1"]["command"][1]
    assert "exec chown" not in transformed["services"]["amaru-relay-1"]["command"][1]
    assert transformed["services"]["bootstrap-producer"]["image"] == "bootstrap"
    assert transformed["volumes"]["external"].get("external") is not True


def test_cardano_only_transform_removes_amaru_paths_but_keeps_real_producers():
    transformed = transform_compose_model(
        _compose_model(),
        scope="cardano-only",
        project="dwarf-qual-cardano-demo",
        cardano_image="new-cardano",
        amaru_image=None,
    )
    assert set(transformed["services"]) == {
        "configurator", "tracer", "tracer-sidecar", "log-tailer",
        "p1", "p2", "p3", "relay1", "relay2",
    }
    assert transformed["services"]["p3"]["image"] == "new-cardano"


def test_amaru_only_contract_keeps_fixed_honest_source_and_marks_target_scope():
    transformed = transform_compose_model(
        _compose_model(),
        scope="amaru-only",
        project="dwarf-qual-amaru-demo",
        cardano_image="known-good-cardano",
        amaru_image="candidate-amaru",
    )
    assert transformed["services"]["p1"]["image"] == "known-good-cardano"
    assert transformed["services"]["amaru-relay-2"]["image"] == "candidate-amaru"
    assert transformed["x-dwarf-qualification"]["contract"] == "amaru-relay-consumer"


def test_classification_requires_every_non_vacuous_runtime_gate():
    gates = {
        "fresh_state": True,
        "exact_identity": True,
        "required_services": True,
        "chain_progress": True,
        "peer_formation": True,
        "consumer_amaru_only_path": True,
        "consumer_converged": True,
        "no_fatal_signatures": True,
        "no_restart_loop": True,
        "clean_teardown": True,
    }
    assert classify_qualification("mixed", gates)["passed"] is True
    gates["consumer_amaru_only_path"] = False
    result = classify_qualification("mixed", gates)
    assert result["passed"] is False
    assert "consumer_amaru_only_path" in result["failed_gates"]


def test_terminal_store_and_cli_failures_are_classified_for_fast_stop():
    assert classify_terminal_runtime_failure(
        "chain database cannot be migrated to version 5 automatically"
    ) == "amaru-bootstrap-store-incompatible"
    assert classify_terminal_runtime_failure(
        "error: unexpected argument '--migrate-chain-db' found"
    ) == "amaru-runtime-interface-incompatible"
    assert classify_terminal_runtime_failure("waiting for chain progress") is None


def test_catalog_proposal_never_mutates_or_auto_confirms(tmp_path):
    result = {
        "scope": "mixed",
        "passed": True,
        "candidate": {"cardano_version": "11.1.2", "amaru_version": "10.11.20260912"},
        "completed_at": "2026-09-17T23:00:00Z",
        "evidence_root": str(tmp_path),
        "classification": "passed-all-gates",
    }
    proposal = propose_catalog_update(result)
    assert proposal["apply_automatically"] is False
    assert proposal["proposed_status"] == "confirmed"
    assert proposal["review_required"] is True


@pytest.mark.parametrize("project", sorted(PROTECTED_PROJECTS))
def test_transform_refuses_protected_projects(project):
    with pytest.raises(QualificationError, match="protected"):
        transform_compose_model(
            _compose_model(),
            scope="mixed",
            project=project,
            cardano_image="cardano",
            amaru_image="amaru",
        )
