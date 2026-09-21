import copy
import json
from pathlib import Path

import pytest

from profile_manager.version_catalog import (
    CatalogError,
    load_version_catalog,
    resolve_default,
    resolve_release,
    resolve_verification,
    validate_version_catalog,
)


CATALOG_PATH = Path(__file__).resolve().parents[1] / "dwarf" / "versions" / "catalog.json"


def _catalog() -> dict:
    return {
        "schema_version": 1,
        "updated_at": "2026-09-17T20:00:00Z",
        "releases": [
            {
                "implementation": "cardano-node",
                "version": "11.1.2",
                "channel": "stable",
                "released_at": "2026-09-17T00:00:00Z",
                "source_revision": "a" * 40,
                "artifacts": [
                    {
                        "kind": "oci",
                        "reference": "ghcr.io/example/cardano-node:11.1.2",
                        "availability": "available",
                        "digest": "sha256:" + "1" * 64,
                    }
                ],
                "verification": {
                    "cardano-only": {
                        "status": "confirmed",
                        "default": True,
                        "checked_at": "2026-09-17T20:00:00Z",
                        "reason": "Passed the Cardano-only runtime contract.",
                        "evidence": ["run:cardano-only"],
                        "issues": [],
                    }
                },
            },
            {
                "implementation": "amaru",
                "version": "10.11.20260912",
                "channel": "stable",
                "released_at": "2026-09-12T00:00:00Z",
                "source_revision": "b" * 40,
                "artifacts": [],
                "verification": {},
            },
        ],
        "compatibility_pairs": [
            {
                "id": "cardano-11.1.2__amaru-10.11.20260912",
                "cardano_version": "11.1.2",
                "amaru_version": "10.11.20260912",
                "status": "confirmed",
                "default": True,
                "checked_at": "2026-09-17T20:00:00Z",
                "topology_revision": "c" * 40,
                "dwarf_revision": "d" * 40,
                "evidence": ["run:demo"],
                "reason": "Passed the mixed runtime contract.",
                "issues": [],
            }
        ],
    }


def test_checked_in_catalog_is_valid_and_retains_current_candidates():
    catalog = load_version_catalog(CATALOG_PATH)

    assert catalog["schema_version"] == 1
    assert resolve_release(catalog, "cardano-node", "11.1.2")["channel"] == "stable"
    assert resolve_release(catalog, "amaru", "10.11.20260912")["channel"] == "stable"


def test_latest_confirmed_amaru_default_is_current_stable_with_disclosed_support():
    catalog = load_version_catalog(CATALOG_PATH)

    selected = resolve_default(catalog, "amaru-only")
    verification = resolve_verification(
        catalog, "amaru", selected["release"]["version"], "amaru-only"
    )

    assert selected["release"]["version"] == "10.11.20260912"
    assert selected["release"]["channel"] == "stable"
    assert verification["supporting_cardano_version"] == "10.7.1"
    assert verification["evidence"] == [
        "state:version-qualifications/"
        "20260918T091139Z-dwarf-qual-amaru-10-7-1-10-11-20260912-ae435ef5"
    ]


def test_mixed_default_remains_reproduced_control_after_latest_pair_failure():
    catalog = load_version_catalog(CATALOG_PATH)

    selected = resolve_default(catalog, "mixed")
    failed = next(
        pair
        for pair in catalog["compatibility_pairs"]
        if pair["id"] == "cardano-11.1.2__amaru-10.11.20260912"
    )

    assert selected["pair"]["id"] == "cardano-10.7.1__amaru-10.11.0"
    assert "20260918T085655Z" in selected["pair"]["evidence"][-1]
    assert failed["status"] == "incompatible"
    assert "20260918T092619Z" in failed["evidence"][0]


def test_validation_rejects_duplicate_release_identity():
    catalog = _catalog()
    catalog["releases"].append(copy.deepcopy(catalog["releases"][0]))

    with pytest.raises(CatalogError, match="duplicate release"):
        validate_version_catalog(catalog)


def test_available_oci_artifact_requires_immutable_digest():
    catalog = _catalog()
    del catalog["releases"][0]["artifacts"][0]["digest"]

    with pytest.raises(CatalogError, match="immutable sha256 digest"):
        validate_version_catalog(catalog)


def test_verification_status_is_scoped_and_defaults_to_unknown():
    catalog = validate_version_catalog(_catalog())

    cardano = resolve_verification(catalog, "cardano-node", "11.1.2", "cardano-only")
    mixed = resolve_verification(catalog, "cardano-node", "11.1.2", "mixed")

    assert cardano["status"] == "confirmed"
    assert cardano["default"] is True
    assert mixed == {"status": "unknown", "default": False, "evidence": [], "issues": []}


def test_default_requires_confirmation_status():
    catalog = _catalog()
    record = catalog["releases"][0]["verification"]["cardano-only"]
    record["status"] = "unknown"
    record["evidence"] = []

    with pytest.raises(CatalogError, match="default.*confirmed"):
        validate_version_catalog(catalog)


def test_validation_rejects_multiple_defaults_for_same_scope():
    catalog = _catalog()
    second = copy.deepcopy(catalog["releases"][0])
    second["version"] = "11.1.1"
    second["source_revision"] = "e" * 40
    catalog["releases"].append(second)

    with pytest.raises(CatalogError, match="multiple defaults.*cardano-only"):
        validate_version_catalog(catalog)


def test_confirmed_status_requires_evidence_and_exact_revisions():
    catalog = _catalog()
    catalog["compatibility_pairs"][0]["evidence"] = []

    with pytest.raises(CatalogError, match="confirmed.*evidence"):
        validate_version_catalog(catalog)


def test_confirmed_amaru_contract_requires_a_known_cardano_support_release():
    catalog = _catalog()
    verification = catalog["releases"][1]["verification"]
    verification["amaru-only"] = {
        "status": "confirmed",
        "default": False,
        "checked_at": "2026-09-18T05:00:00Z",
        "reason": "Passed the relay/consumer contract.",
        "evidence": ["qualification:demo"],
        "issues": [],
    }

    with pytest.raises(CatalogError, match="supporting_cardano_version"):
        validate_version_catalog(catalog)

    verification["amaru-only"]["supporting_cardano_version"] = "99.99.99"
    with pytest.raises(CatalogError, match="unknown cardano-node"):
        validate_version_catalog(catalog)


def test_mixed_default_resolves_a_compatible_exact_pair():
    catalog = validate_version_catalog(_catalog())

    resolved = resolve_default(catalog, "mixed")

    assert resolved["kind"] == "compatibility-pair"
    assert resolved["status"] == "confirmed"
    assert resolved["pair"]["cardano_version"] == "11.1.2"
    assert resolved["pair"]["amaru_version"] == "10.11.20260912"


@pytest.mark.parametrize("status", ["supported", "passing", "default"])
def test_validation_rejects_ambiguous_status_words(status):
    catalog = _catalog()
    catalog["releases"][0]["verification"]["cardano-only"]["status"] = status

    with pytest.raises(CatalogError, match="status"):
        validate_version_catalog(catalog)


def test_catalog_serializes_without_private_or_runtime_values():
    catalog = validate_version_catalog(_catalog())
    encoded = json.dumps(catalog, sort_keys=True)

    assert "password" not in encoded.lower()
    assert "$HOME" not in encoded
