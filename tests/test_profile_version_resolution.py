import copy
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from profile_manager.deployment_versions import build_deployment_version_preview
from profile_manager.profiles import Profile, deployment_adapter_for_profile, load_profiles
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


@pytest.mark.parametrize("missing_value", [None, ""])
def test_omitted_or_blank_policy_uses_safe_confirmed_default(missing_value):
    data = _profile()
    if missing_value is None:
        del data["version_policy"]
    else:
        data["version_policy"] = missing_value

    resolved = resolve_profile_versions(data, load_version_catalog(CATALOG_PATH))
    profile = Profile.from_dict(data)

    assert resolved["policy"] == "latest-confirmed"
    assert resolved["policy_source"] == "implicit-default"
    assert resolved["status"] == "confirmed"
    assert resolved["resolved"]["cardano-node"]["version"] == "11.1.2"
    assert resolved["resolved"]["cardano-node"]["artifacts"][0]["digest"].startswith(
        "sha256:"
    )
    assert profile.version_policy == "latest-confirmed"
    assert profile.version_policy_source == "implicit-default"


def test_loaded_implicit_default_remains_implicit_through_cli_preview_serialization():
    data = _profile()
    del data["version_policy"]
    profile = Profile.from_dict(data)

    preview = build_deployment_version_preview(asdict(profile))

    assert preview["policy"] == "latest-confirmed"
    assert preview["policy_source"] == "implicit-default"


def test_every_shipped_profile_declares_safe_policy_and_preserves_adapter_class():
    expected_adapters = {
        "profile-a-haskell-peersharing-disabled": "generated-cardano-local",
        "profile-b-haskell-peersharing-enabled": "generated-cardano-local",
        "profile-c-mixed-haskell-amaru-minimal": "amaru-control",
        "profile-d-amaru-preview-proof": "amaru-public-peer",
        "profile-e-haskell-preview-proof": "cardano-public-peer",
        "profile-f-amaru-preview2-proof": "amaru-public-peer",
        "profile-g-haskell-preview2-proof": "cardano-public-peer",
        "profile-h-generated-mixed-haskell2-amaru1": "amaru-control",
        "profile-i-generated-haskell3": "generated-cardano-local",
        "profile-j-haskell-preprod-proof": "cardano-public-peer",
        "profile-k-amaru-preprod-proof": "amaru-public-peer",
        "profile-l-amaru-closed-devnet": "amaru-control",
        "profile-m-consensus-threshold": "generated-cardano-local",
        "profile-n-cardano-latest-confirmed": "generated-cardano-local",
        "profile-o-amaru-target-latest-confirmed": "amaru-control",
        "profile-p-mixed-latest-confirmed": "amaru-control",
        "profile-q-amaru-measurement-patched": "amaru-control",
        "profile-r-amaru-measurement-stock-control": "amaru-control",
    }
    profiles = load_profiles()

    assert len(profiles) == 18
    assert {profile.id for profile in profiles} == set(expected_adapters)
    for profile in profiles:
        source = next(
            CATALOG_PATH.parents[1].glob(f"profiles/{profile.id}/profile.yaml")
        )
        raw = json.loads(source.read_text(encoding="utf-8"))
        expected_policy = (
            "exact"
            if profile.id in {
                "profile-q-amaru-measurement-patched",
                "profile-r-amaru-measurement-stock-control",
            }
            else "latest-confirmed"
        )
        assert raw["version_policy"] == expected_policy
        assert profile.version_policy_source == "explicit"
        assert deployment_adapter_for_profile(profile) == expected_adapters[profile.id]
        preview = build_deployment_version_preview(raw)
        if expected_adapters[profile.id] in {"cardano-public-peer", "amaru-public-peer"}:
            assert preview["status"] == "unknown"
            assert preview["requires_acknowledgement"] is True
        else:
            assert preview["status"] == "confirmed"


def test_latest_stable_cardano_resolves_newest_qualified_release():
    resolved = resolve_profile_versions(_profile(), load_version_catalog(CATALOG_PATH))

    assert resolved["policy_source"] == "explicit"
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
    for item in modified["releases"]:
        if item["implementation"] == "amaru" and "amaru-only" in item["verification"]:
            item["verification"]["amaru-only"]["default"] = False
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
        cardano_version="11.1.1",
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
            "cardano_version": "11.1.1",
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


def test_shipped_version_aware_defaults_resolve_exact_confirmed_contracts():
    catalog = load_version_catalog(CATALOG_PATH)
    profiles_root = CATALOG_PATH.parents[1] / "profiles"
    expected = {
        "profile-n-cardano-latest-confirmed": ("cardano-only", "11.1.2", None),
        "profile-o-amaru-target-latest-confirmed": ("amaru-only", "10.11.20260912", "10.7.1"),
        "profile-p-mixed-latest-confirmed": ("mixed", "10.11.0", None),
    }
    for profile_id, (scope, target_version, support_version) in expected.items():
        body = json.loads((profiles_root / profile_id / "profile.yaml").read_text(encoding="utf-8"))
        resolved = resolve_profile_versions(body, catalog)
        assert body["version_policy"] == "latest-confirmed"
        assert resolved["scope"] == scope
        assert resolved["status"] == "confirmed"
        implementation = "cardano-node" if scope == "cardano-only" else "amaru"
        assert resolved["resolved"][implementation]["version"] == target_version
        if support_version:
            assert resolved["supporting"]["cardano-node"]["version"] == support_version
