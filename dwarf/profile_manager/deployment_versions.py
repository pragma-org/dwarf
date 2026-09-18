"""Deployment preview and fail-closed version policy gates."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from profile_manager.data.catalog_definitions import load_definition
from profile_manager.version_catalog import (
    DEFAULT_CATALOG_PATH,
    CatalogError,
    load_version_catalog,
    resolve_default,
    resolve_profile_versions,
)


class DeploymentVersionGateError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def version_catalog_revision(path: str | Path = DEFAULT_CATALOG_PATH) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_deployment_version_preview(
    profile_data: dict[str, Any],
    catalog: dict[str, Any] | None = None,
    *,
    catalog_revision: str | None = None,
) -> dict[str, Any]:
    checked_catalog = catalog if catalog is not None else load_version_catalog()
    try:
        resolution = resolve_profile_versions(profile_data, checked_catalog)
    except CatalogError as exc:
        raise DeploymentVersionGateError("version-resolution-failed", str(exc)) from exc
    supporting: dict[str, Any] = {}
    if resolution.get("scope") == "amaru-only":
        # Amaru is presently a relay/consumer, not a standalone block producer.
        # Disclose the exact honest Cardano source required by this contract in
        # the same preview that gates the target release.
        supporting["cardano-node"] = resolve_default(
            checked_catalog, "cardano-only"
        )["release"]
    return {
        "profile_id": str(profile_data.get("id") or ""),
        "catalog_revision": catalog_revision or version_catalog_revision(),
        "supporting": supporting,
        **resolution,
    }


def profile_deployment_version_preview(profile_id: str) -> dict[str, Any]:
    record = load_definition("profiles", profile_id)
    return build_deployment_version_preview(record.data)


def enforce_deployment_version_gate(
    preview: dict[str, Any], *, acknowledge_unknown: bool
) -> dict[str, Any]:
    status = str(preview.get("status") or "unknown")
    if preview.get("blocked") or status in {"incompatible", "blocked"}:
        reason = str(preview.get("reason") or "the selected release is not deployable")
        raise DeploymentVersionGateError("version-selection-blocked", reason)
    if preview.get("requires_acknowledgement") and not acknowledge_unknown:
        raise DeploymentVersionGateError(
            "unknown-version-ack-required",
            "The selected stable release has not passed this deployment contract; explicit one-run acknowledgement is required.",
        )
    acknowledgement = None
    if preview.get("requires_acknowledgement"):
        acknowledgement = {
            "status": status,
            "one_run_only": True,
            "catalog_revision": preview.get("catalog_revision"),
            "profile_id": preview.get("profile_id"),
        }
    return {"allowed": True, "preview": preview, "acknowledgement": acknowledgement}
