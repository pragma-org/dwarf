"""Read-only data model for the node release and compatibility catalog."""
from __future__ import annotations

from typing import Any

from profile_manager.deployment_versions import version_catalog_revision
from profile_manager.version_catalog import load_version_catalog, resolve_verification


def version_catalog_view() -> dict[str, Any]:
    catalog = load_version_catalog()
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
        "catalog_revision": version_catalog_revision(),
        "releases": releases,
        "pairs": [dict(pair) for pair in catalog["compatibility_pairs"]],
        "sources": dict(catalog.get("sources") or {}),
    }
