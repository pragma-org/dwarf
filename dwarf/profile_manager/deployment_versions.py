"""Deployment preview and fail-closed version policy gates."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from profile_manager.data.catalog_definitions import load_definition
from profile_manager.version_catalog import (
    DEFAULT_CATALOG_PATH,
    CatalogError,
    resolve_default,
    resolve_profile_versions,
)
from profile_manager.version_discovery import load_effective_version_catalog


class DeploymentVersionGateError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


_SHA256_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_SOURCE_REVISION = re.compile(r"^[0-9a-f]{40}$")


def version_catalog_revision(
    path: str | Path = DEFAULT_CATALOG_PATH, *, catalog: dict[str, Any] | None = None
) -> str:
    if catalog is None:
        catalog = load_effective_version_catalog(path)
    canonical = json.dumps(catalog, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def build_deployment_version_preview(
    profile_data: dict[str, Any],
    catalog: dict[str, Any] | None = None,
    *,
    catalog_revision: str | None = None,
) -> dict[str, Any]:
    checked_catalog = catalog if catalog is not None else load_effective_version_catalog()
    try:
        resolution = resolve_profile_versions(profile_data, checked_catalog)
    except CatalogError as exc:
        raise DeploymentVersionGateError("version-resolution-failed", str(exc)) from exc
    supporting: dict[str, Any] = dict(resolution.get("supporting") or {})
    if resolution.get("scope") == "amaru-only":
        # Amaru is presently a relay/consumer, not a standalone block producer.
        # Disclose the exact honest Cardano source required by this contract in
        # the same preview that gates the target release.
        if "cardano-node" not in supporting:
            supporting["cardano-node"] = resolve_default(
                checked_catalog, "cardano-only"
            )["release"]
    public_network = str(
        profile_data.get("public_network") or profile_data.get("amaru_network") or ""
    ).strip()
    upstream_peer = str(profile_data.get("upstream_peer_address") or "").strip()
    if not public_network and upstream_peer:
        public_network = "preprod" if "preprod" in upstream_peer else "preview"
    release_status = str(resolution.get("status") or "unknown")
    if public_network:
        resolution = {
            **resolution,
            "status": "unknown",
            "requires_acknowledgement": True,
            "blocked": False,
            "reason": (
                f"The selected artifact is {release_status} for its catalogued local-devnet "
                f"contract, but DWARF has not qualified that evidence as a public-{public_network} "
                "deployment contract. One-run acknowledgement is required."
            ),
        }
    from profile_manager.profiles import Profile, deployment_adapter_for_profile

    adapter_profile_data = {
        "id": str(profile_data.get("id") or "version-preview"),
        "label": str(profile_data.get("label") or profile_data.get("id") or "Version preview"),
        "network_magic": int(profile_data.get("network_magic", 42)),
        "peer_sharing": bool(profile_data.get("peer_sharing", False)),
        **profile_data,
    }
    deployment_adapter = deployment_adapter_for_profile(
        Profile.from_dict(adapter_profile_data)
    )
    return {
        **resolution,
        "profile_id": str(profile_data.get("id") or ""),
        "release_status": release_status,
        "deployment_context": f"public-{public_network}" if public_network else "local-devnet",
        "deployment_adapter": deployment_adapter,
        "catalog_revision": catalog_revision or version_catalog_revision(catalog=checked_catalog),
        "catalog_snapshot": checked_catalog,
        "supporting": supporting,
    }


def profile_deployment_version_preview(profile_id: str) -> dict[str, Any]:
    record = load_definition("profiles", profile_id)
    return build_deployment_version_preview(record.data)


def build_measurement_target_identity(
    preview: dict[str, Any],
    *,
    implementation: str,
    mode: str,
    image_reference: str | None = None,
    image_digest: str | None = None,
    executable_digest: str | None = None,
) -> dict[str, Any]:
    """Return the immutable real-target identity consumed by tap resolution."""
    if implementation not in {"amaru", "cardano-node"}:
        raise DeploymentVersionGateError(
            "measurement-target-implementation", "unsupported measurement target implementation"
        )
    if mode not in {"stock", "coverage", "patched"}:
        raise DeploymentVersionGateError(
            "measurement-target-mode", "measurement target mode must be stock, coverage, or patched"
        )
    release = (preview.get("resolved") or {}).get(implementation)
    if not isinstance(release, dict):
        raise DeploymentVersionGateError(
            "measurement-target-missing", f"deployment preview does not resolve {implementation}"
        )
    version = release.get("version")
    source_revision = release.get("source_revision")
    if not isinstance(version, str) or not version:
        raise DeploymentVersionGateError(
            "measurement-version-missing", "resolved target has no exact version"
        )
    if not isinstance(source_revision, str) or not _SOURCE_REVISION.fullmatch(source_revision):
        raise DeploymentVersionGateError(
            "measurement-revision-missing", "resolved target has no exact source revision"
        )

    artifact = next(
        (
            item for item in release.get("artifacts") or []
            if item.get("kind") == "oci" and item.get("availability") == "available"
        ),
        None,
    )
    if mode == "stock":
        if artifact is None:
            raise DeploymentVersionGateError(
                "measurement-image-missing", "stock target has no available immutable OCI artifact"
            )
        catalog_digest = artifact.get("digest")
        if image_digest is not None and image_digest != catalog_digest:
            raise DeploymentVersionGateError(
                "measurement-image-mismatch",
                "stock target image digest does not match the version catalog",
            )
        image_digest = image_digest or catalog_digest
        image_reference = image_reference or artifact.get("reference")
    elif image_digest is None or image_reference is None:
        raise DeploymentVersionGateError(
            "measurement-instrumented-image-missing",
            f"{mode} target requires an explicit immutable image reference and digest",
        )
    if not isinstance(image_digest, str) or not _SHA256_DIGEST.fullmatch(image_digest):
        raise DeploymentVersionGateError(
            "measurement-image-invalid", "measurement image_digest must be immutable sha256"
        )
    if not isinstance(image_reference, str) or not image_reference:
        raise DeploymentVersionGateError(
            "measurement-image-reference-missing", "measurement image reference is required"
        )
    if "@sha256:" in image_reference:
        reference_digest = "sha256:" + image_reference.rsplit("@sha256:", 1)[1]
        if reference_digest != image_digest:
            raise DeploymentVersionGateError(
                "measurement-image-reference-mismatch",
                "measurement image reference digest does not match image_digest",
            )
    else:
        image_reference = f"{image_reference}@{image_digest}"
    if executable_digest is not None and not _SHA256_DIGEST.fullmatch(executable_digest):
        raise DeploymentVersionGateError(
            "measurement-executable-invalid", "executable_digest must be immutable sha256"
        )
    return {
        "implementation": implementation,
        "version": version,
        "source_revision": source_revision,
        "mode": mode,
        "image_reference": image_reference,
        "image_digest": image_digest,
        "executable_digest": executable_digest,
        "version_catalog_revision": preview.get("catalog_revision"),
        "profile_id": preview.get("profile_id"),
        "deployment_adapter": preview.get("deployment_adapter"),
        "qualification_status": preview.get("status"),
    }


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
