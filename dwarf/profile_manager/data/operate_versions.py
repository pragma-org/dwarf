"""Read-only data model for the node release and compatibility catalog."""
from __future__ import annotations

from typing import Any

from profile_manager.deployment_versions import version_catalog_revision
from profile_manager.version_catalog import resolve_verification
from profile_manager.version_catalog import resolve_default
from profile_manager.version_discovery import (
    load_effective_version_catalog,
    read_refresh_status,
)


def version_catalog_view() -> dict[str, Any]:
    catalog = load_effective_version_catalog()
    releases = []
    for release in catalog["releases"]:
        artifact = next(
            (
                item
                for item in release.get("artifacts", [])
                if item.get("availability") == "available" and item.get("digest")
            ),
            (release.get("artifacts") or [{}])[0],
        )
        scopes = (
            ("cardano-only", "mixed")
            if release["implementation"] == "cardano-node"
            else ("amaru-only", "mixed")
        )
        scope_statuses = {
            scope: resolve_verification(
                catalog, release["implementation"], release["version"], scope
            )
            for scope in scopes
        }
        releases.append(
            {
                **release,
                "artifact_reference": artifact.get("reference"),
                "artifact_digest": artifact.get("digest"),
                "artifact_availability": artifact.get("availability", "unknown"),
                "scope_statuses": scope_statuses,
            }
        )
    return {
        "updated_at": catalog["updated_at"],
        "catalog_revision": version_catalog_revision(catalog=catalog),
        "releases": releases,
        "pairs": [dict(pair) for pair in catalog["compatibility_pairs"]],
        "sources": dict(catalog.get("sources") or {}),
        "refresh": read_refresh_status(),
    }


def version_default_summary() -> dict[str, str]:
    """Return the three evidence-backed defaults used by landing-page links."""

    catalog = load_effective_version_catalog()
    cardano = resolve_default(catalog, "cardano-only")["release"]
    amaru_default = resolve_default(catalog, "amaru-only")
    amaru = amaru_default["release"]
    amaru_verification = resolve_verification(
        catalog, "amaru", amaru["version"], "amaru-only"
    )
    mixed = resolve_default(catalog, "mixed")["pair"]
    return {
        "cardano": cardano["version"],
        "amaru": amaru["version"],
        "amaru_support": str(amaru_verification["supporting_cardano_version"]),
        "mixed_cardano": mixed["cardano_version"],
        "mixed_amaru": mixed["amaru_version"],
    }
