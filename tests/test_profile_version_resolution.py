import copy
from pathlib import Path

import pytest

from profile_manager.profiles import Profile
from profile_manager.version_catalog import (
    CatalogError,
    load_version_catalog,
    resolve_profile_versions,
    validate_version_catalog,
)


CATALOG_PATH = Path(__file__).resolve().parents[1] / "dwarf" / "versions" / "catalog.json"


def _profile(**updates):
    data = {
        "id": "profile-version-test",
        "label": "Version test",
        "node_type": "cardano-node",
        "node_count": 1,
        "amaru_node_count": 0,
        "network_magic": 42,
        "peer_sharing": False,
        "version_policy": "latest-stable",
    }
    data.update(updates)
    return data


def test_legacy_profile_remains_loadable_without_silent_version_claim():
    data = _profile()
    del data["version_policy"]

    resolved = resolve_profile_versions(data, load_version_catalog(CATALOG_PATH))
    profile = Profile.from_dict(data)

    assert resolved["policy"] == "legacy"
    assert resolved["status"] == "unknown"
    assert resolved["resolved"] == {}
    assert "existing deployment behavior" in resolved["reason"]
    assert profile.version_policy == "legacy"


def test_latest_stable_cardano_resolves_newest_qualified_release():
    resolved = resolve_profile_versions(_profile(), load_version_catalog(CATALOG_PATH))

    assert resolved["scope"] == "cardano-only"
    assert resolved["resolved"]["cardano-node"]["version"] == "11.1.2"
    assert resolved["status"] == "confirmed"
    assert resolved["requires_acknowledgement"] is False
    assert resolved["blocked"] is False


def test_latest_confirmed_mixed_uses_exact_default_pair_and_artifacts():
    data = _profile(
        node_type="mixed",
        node_count=1,
        amaru_node_count=1,
        version_policy="latest-confirmed",
    )

    resolved = resolve_profile_versions(data, load_version_catalog(CATALOG_PATH))

    assert resolved["status"] == "confirmed"
    assert resolved["pair"]["id"] == "cardano-10.7.1__amaru-10.11.0"
    assert resolved["resolved"]["cardano-node"]["version"] == "10.7.1"
    assert resolved["resolved"]["amaru"]["version"] == "10.11.0"
    assert resolved["resolved"]["cardano-node"]["artifacts"][0]["digest"].startswith("sha256:")
    assert resolved["requires_acknowledgement"] is False


def test_confirmed_amaru_default_resolves_its_qualified_cardano_support():
    catalog = load_version_catalog(CATALOG_PATH)
    modified = copy.deepcopy(catalog)
    release = next(
        item
        for item in modified["releases"]
        if item["implementation"] == "amaru"
        and item["version"] == "10.11.20260730"
    )
    release["verification"]["amaru-only"] = {
        "status": "confirmed",
        "default": True,
        "checked_at": "2026-09-18T05:00:00Z",
        "supporting_cardano_version": "10.7.1",
        "reason": "Passed with the exact supporting Cardano release.",
        "evidence": ["qualification:demo"],
        "issues": [],
    }

    resolved = resolve_profile_versions(
        _profile(
            node_type="amaru",
            node_count=0,
            amaru_node_count=1,
            version_policy="latest-confirmed",
        ),
        validate_version_catalog(modified),
    )

    assert resolved["resolved"]["amaru"]["version"] == "10.11.20260730"
    assert resolved["supporting"]["cardano-node"]["version"] == "10.7.1"


def test_exact_known_pair_resolves_by_catalog_id():
    data = _profile(
        node_type="mixed",
        node_count=1,
        amaru_node_count=1,
        version_policy="exact",
        compatibility_pair="cardano-10.7.1__amaru-10.11.0",
    )

    resolved = resolve_profile_versions(data, load_version_catalog(CATALOG_PATH))

    assert resolved["status"] == "confirmed"
    assert resolved["pair"]["topology_revision"]


def test_exact_unlisted_pair_is_unknown_not_implicitly_compatible():
    data = _profile(
        node_type="mixed",
        node_count=1,
        amaru_node_count=1,
        version_policy="exact",
        cardano_version="11.1.2",
        amaru_version="10.11.20260912",
    )

    resolved = resolve_profile_versions(data, load_version_catalog(CATALOG_PATH))

    assert resolved["status"] == "unknown"
    assert resolved["pair"] is None
    assert resolved["requires_acknowledgement"] is True
    assert "not present in the compatibility matrix" in resolved["reason"]


def test_incompatible_pair_is_a_hard_block():
    catalog = load_version_catalog(CATALOG_PATH)
    incompatible = copy.deepcopy(catalog)
    incompatible["compatibility_pairs"].append(
        {
            "id": "incompatible-current",
            "cardano_version": "11.1.2",
            "amaru_version": "10.11.20260912",
            "status": "incompatible",
            "default": False,
            "checked_at": "2026-09-17T22:00:00Z",
            "topology_revision": "a" * 40,
            "dwarf_revision": "b" * 40,
            "reason": "Runtime genesis contract failed.",
            "evidence": ["run:failed"],
            "issues": [],
        }
    )
    data = _profile(
        node_type="mixed",
        node_count=1,
        amaru_node_count=1,
        version_policy="exact",
        compatibility_pair="incompatible-current",
    )

    resolved = resolve_profile_versions(data, validate_version_catalog(incompatible))

    assert resolved["status"] == "incompatible"
    assert resolved["blocked"] is True
    assert resolved["requires_acknowledgement"] is False


def test_profile_model_retains_version_intent():
    profile = Profile.from_dict(
        _profile(version_policy="exact", cardano_version="11.1.2")
    )

    assert profile.version_policy == "exact"
    assert profile.cardano_version == "11.1.2"
    assert profile.amaru_version is None
    assert profile.compatibility_pair is None


def test_invalid_policy_fails_closed():
    with pytest.raises(CatalogError, match="version_policy"):
        resolve_profile_versions(
            _profile(version_policy="whatever-is-newest"),
            load_version_catalog(CATALOG_PATH),
        )
