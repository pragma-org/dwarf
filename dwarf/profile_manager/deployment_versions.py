"""Deployment preview and fail-closed version policy gates."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable

from profile_manager.data.catalog_definitions import load_definition
from profile_manager.version_catalog import (
    DEFAULT_CATALOG_PATH,
    CatalogError,
    resolve_default,
    resolve_profile_versions,
)
from profile_manager.version_discovery import load_effective_version_catalog
from profile_manager.version_discovery import load_operator_registration


class DeploymentVersionGateError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


_SHA256_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_SOURCE_REVISION = re.compile(r"^[0-9a-f]{40}$")
_OCI_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*(?:@sha256:[0-9a-f]{64})?$")
_OPERATOR_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")


def operator_registration_release(registration: dict[str, Any]) -> dict[str, Any]:
    """Project one local registration without inventing catalog provenance."""
    implementation = registration.get("implementation")
    version = registration.get("version")
    reference = registration.get("image_reference")
    manifest = registration.get("manifest_digest")
    config_id = registration.get("config_image_id")
    executable_digest = registration.get("executable_digest")
    platform = registration.get("platform")
    runtime_interface = registration.get("runtime_interface")
    provenance = registration.get("provenance")
    if not isinstance(implementation, str) or not implementation:
        raise DeploymentVersionGateError("operator-registration-invalid", "local registration has no implementation")
    if not isinstance(version, str) or not version:
        raise DeploymentVersionGateError("operator-registration-invalid", "local registration has no version")
    if reference is not None and (
        not isinstance(reference, str)
        or not _OCI_REFERENCE.fullmatch(reference)
        or ".." in reference
        or reference.startswith(("/", "file:"))
    ):
        raise DeploymentVersionGateError("operator-registration-invalid", "local registration image reference is unsafe")
    if not isinstance(config_id, str) or not _SHA256_DIGEST.fullmatch(config_id):
        raise DeploymentVersionGateError("operator-registration-invalid", "local registration has no exact config image ID")
    if executable_digest is not None and (
        not isinstance(executable_digest, str)
        or not _SHA256_DIGEST.fullmatch(executable_digest)
    ):
        raise DeploymentVersionGateError(
            "operator-registration-invalid",
            "local registration executable digest is invalid",
        )
    if manifest is not None and (
        not isinstance(manifest, str) or not _SHA256_DIGEST.fullmatch(manifest)
    ):
        raise DeploymentVersionGateError("operator-registration-invalid", "local registration manifest digest is invalid")
    if manifest is not None and reference is not None and not reference.endswith("@" + manifest):
        raise DeploymentVersionGateError("operator-registration-invalid", "local registration reference and manifest differ")
    if not isinstance(platform, str) or not re.fullmatch(r"linux/(?:amd64|arm64)", platform):
        raise DeploymentVersionGateError("operator-registration-invalid", "local registration platform is unsupported")
    if implementation == "amaru" and runtime_interface != "extracted-binary":
        raise DeploymentVersionGateError(
            "operator-registration-interface-unverified",
            "local Amaru artifact runtime interface is unsupported or unverified; "
            "inspect an immutable image with the native Amaru executable contract",
        )
    if implementation != "amaru" and runtime_interface is not None:
        raise DeploymentVersionGateError(
            "operator-registration-invalid",
            "local registration runtime interface does not match its implementation",
        )
    if not isinstance(provenance, dict):
        raise DeploymentVersionGateError("operator-registration-invalid", "local registration provenance is missing")
    qualification = provenance.get("qualification")
    if qualification == "blocked":
        raise DeploymentVersionGateError(
            "operator-registration-blocked",
            "local registration is blocked and cannot be acknowledged into use",
        )
    if qualification != "unverified":
        raise DeploymentVersionGateError(
            "operator-registration-invalid",
            "local registration qualification must remain unverified in the operator overlay",
        )
    source_revision = provenance.get("source_revision")
    if source_revision is not None and not _SOURCE_REVISION.fullmatch(str(source_revision)):
        raise DeploymentVersionGateError("operator-registration-invalid", "local registration source revision is invalid")
    return {
        "implementation": implementation,
        "version": version,
        "channel": "operator-local",
        "released_at": registration.get("inspected_at"),
        "source_revision": source_revision,
        "artifacts": [{
            "kind": "oci",
            "reference": reference,
            "availability": "available",
            "digest": manifest,
            "config_image_id": config_id,
            "executable_digest": executable_digest,
            "public": provenance.get("public") is True,
            "platforms": [platform],
            "runtime_interface": runtime_interface,
            "operator_registration": copy.deepcopy(registration),
        }],
        "verification": {},
        "operator_registration": copy.deepcopy(registration),
    }


def operator_registration_immutable_identity(
    registration: dict[str, Any],
) -> dict[str, Any]:
    """Validate the immutable identity retained from one controller package job."""
    release = operator_registration_release(registration)
    registration_id = registration.get("registration_id")
    controller_installation_id = registration.get("controller_installation_id")
    executable_digest = registration.get("executable_digest")
    if (
        not isinstance(registration_id, str)
        or not _OPERATOR_IDENTIFIER.fullmatch(registration_id)
        or not registration_id.startswith("registration-")
        or not isinstance(controller_installation_id, str)
        or not _OPERATOR_IDENTIFIER.fullmatch(controller_installation_id)
        or not isinstance(executable_digest, str)
        or not _SHA256_DIGEST.fullmatch(executable_digest)
    ):
        raise DeploymentVersionGateError(
            "operator-registration-executable-unverified",
            "local registration has no controller-verified executable identity",
        )
    return {
        "schema_version": "v1",
        "registration_id": registration_id,
        "implementation": release["implementation"],
        "version": release["version"],
        "config_image_id": registration["config_image_id"],
        "executable_digest": executable_digest,
        "platform": registration["platform"],
        "runtime_interface": registration.get("runtime_interface"),
        "controller_installation_id": controller_installation_id,
        "source_revision": release.get("source_revision"),
        "qualification": "unverified",
    }


def operator_registration_capability_context_from_observation(
    registration: dict[str, Any], observed: dict[str, Any]
) -> dict[str, Any]:
    """Bind one local registration to an exact controller image inspection."""
    operator_registration_release(registration)
    source_revision = (registration.get("provenance") or {}).get(
        "source_revision"
    )
    expected_manifest = registration.get("manifest_digest")
    manifests = (
        observed.get("manifest_references")
        if isinstance(observed, dict)
        else None
    )
    exact = (
        isinstance(observed, dict)
        and observed.get("schema_version") == "v1"
        and observed.get("implementation") == registration["implementation"]
        and observed.get("version") == registration["version"]
        and observed.get("requested_image") == registration["image_reference"]
        and observed.get("config_image_id") == registration["config_image_id"]
        and observed.get("platform") == registration["platform"]
        and observed.get("runtime_interface") == registration.get("runtime_interface")
        and observed.get("declared_source_revision") == source_revision
        and observed.get("installation_id")
        == registration.get("controller_installation_id")
        and isinstance(manifests, list)
        and (
            expected_manifest is None
            or registration["image_reference"] in manifests
        )
    )
    if not exact:
        raise DeploymentVersionGateError(
            "operator-registration-inspection-mismatch",
            "The selected local image no longer matches its inspected registration.",
        )
    identity = {
        "config_image_id": registration["config_image_id"],
        "platform": registration["platform"],
    }
    return {
        "inspected_images": {
            registration["image_reference"]: identity,
            registration["config_image_id"]: identity,
        }
    }


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
    operator_registration_id: str | None = None,
    operator_state_dir: str | Path | None = None,
    request_controller: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    checked_catalog = catalog if catalog is not None else load_effective_version_catalog()
    try:
        resolution = resolve_profile_versions(profile_data, checked_catalog)
    except CatalogError as exc:
        raise DeploymentVersionGateError("version-resolution-failed", str(exc)) from exc
    supporting: dict[str, Any] = dict(resolution.get("supporting") or {})
    operator_selection = None
    if operator_registration_id is not None:
        if resolution.get("blocked") or resolution.get("status") in {
            "blocked", "incompatible"
        }:
            raise DeploymentVersionGateError(
                "operator-registration-policy",
                "local registration cannot override blocked or incompatible catalog policy",
            )
        if resolution.get("scope") == "mixed":
            raise DeploymentVersionGateError(
                "operator-registration-scope",
                "local registration requires an explicit evidence-linked mixed pair",
            )
        try:
            operator_selection = load_operator_registration(
                operator_registration_id, operator_state_dir
            )
            package_proof = None
            try:
                from profile_manager.binary_imports import (
                    BinaryImportError,
                    retained_import_executable_digest,
                )

                import_root = (
                    Path(operator_state_dir) / "version-imports"
                    if operator_state_dir is not None
                    else None
                )
                recovered = retained_import_executable_digest(
                    operator_selection["registration"],
                    staging_root=import_root,
                    request_controller=request_controller,
                )
                operator_selection = {
                    **operator_selection,
                    "registration": {
                        **operator_selection["registration"],
                        "executable_digest": recovered,
                    },
                }
                registration = operator_selection["registration"]
                package_proof = {
                    "status": "verified",
                    "registration_id": registration["registration_id"],
                    "config_image_id": registration["config_image_id"],
                    "executable_digest": recovered,
                    "platform": registration["platform"],
                    "runtime_interface": registration.get("runtime_interface"),
                    "controller_installation_id": registration[
                        "controller_installation_id"
                    ],
                }
            except BinaryImportError:
                # A manually inspected image or missing/mismatched retained
                # import record remains usable for no-measurement workflows,
                # but cannot earn a local measurement identity.
                pass
            local_release = operator_registration_release(
                operator_selection["registration"]
            )
            if package_proof is not None:
                local_release["operator_package_proof"] = package_proof
        except DeploymentVersionGateError:
            raise
        except ValueError as exc:
            raise DeploymentVersionGateError(
                "operator-registration-invalid", str(exc)
            ) from exc
        implementation = local_release["implementation"]
        if implementation not in (resolution.get("resolved") or {}):
            raise DeploymentVersionGateError(
                "operator-registration-scope",
                "local registration implementation is outside the selected profile scope",
            )
        resolution = {
            **resolution,
            "resolved": {**resolution["resolved"], implementation: local_release},
            "status": "unknown",
            "requires_acknowledgement": True,
            "blocked": False,
            "reason": (
                "This exact local artifact is operator-registered and unqualified; "
                "one-run acknowledgement is required."
            ),
        }
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
        "operator_registration_id": operator_registration_id,
        "operator_overlay_revision": (
            operator_selection["revision"] if operator_selection else None
        ),
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
    image_manifest_digest: str | None = None,
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
    artifact = next(
        (
            item for item in release.get("artifacts") or []
            if item.get("kind") == "oci" and item.get("availability") == "available"
        ),
        None,
    )
    registration = artifact.get("operator_registration") if artifact else None
    local_config = (
        mode == "stock"
        and isinstance(registration, dict)
        and artifact.get("digest") is None
        and artifact.get("config_image_id") is not None
    )
    if local_config:
        if source_revision is not None and (
            not isinstance(source_revision, str)
            or not _SOURCE_REVISION.fullmatch(source_revision)
        ):
            raise DeploymentVersionGateError(
                "measurement-revision-invalid",
                "local target source revision is invalid",
            )
        config_id = artifact.get("config_image_id")
        executable = artifact.get("executable_digest")
        platforms = artifact.get("platforms")
        registration_id = registration.get("registration_id")
        overlay_revision = preview.get("operator_overlay_revision")
        if not isinstance(config_id, str) or not _SHA256_DIGEST.fullmatch(config_id):
            raise DeploymentVersionGateError(
                "measurement-config-image-missing",
                "local target has no exact inspected config image ID",
            )
        if not isinstance(executable, str) or not _SHA256_DIGEST.fullmatch(executable):
            raise DeploymentVersionGateError(
                "measurement-executable-missing",
                "local target has no retained executable digest",
            )
        if (
            not isinstance(platforms, list)
            or len(platforms) != 1
            or platforms[0] not in {"linux/amd64", "linux/arm64"}
        ):
            raise DeploymentVersionGateError(
                "measurement-platform-invalid",
                "local target has no exact supported Linux platform",
            )
        if (
            registration_id != preview.get("operator_registration_id")
            or not isinstance(registration_id, str)
            or not registration_id
        ):
            raise DeploymentVersionGateError(
                "measurement-registration-mismatch",
                "local target registration identity differs",
            )
        if not isinstance(overlay_revision, str) or not _SHA256_DIGEST.fullmatch(
            overlay_revision
        ):
            raise DeploymentVersionGateError(
                "measurement-overlay-invalid",
                "local target overlay revision is invalid",
            )
        if artifact.get("reference") != config_id:
            raise DeploymentVersionGateError(
                "measurement-config-reference-mismatch",
                "local target execution reference differs from its config image ID",
            )
        return {
            "implementation": implementation,
            "version": version,
            "source_revision": source_revision,
            "mode": mode,
            "identity_kind": "operator-local-config",
            "image_reference": config_id,
            "image_digest": None,
            "image_manifest_digest": None,
            "image_config_id": config_id,
            "executable_digest": executable,
            "platform": platforms[0],
            "operator_registration_id": registration_id,
            "operator_overlay_revision": overlay_revision,
            "controller_installation_id": registration.get(
                "controller_installation_id"
            ),
            "version_catalog_revision": preview.get("catalog_revision"),
            "profile_id": preview.get("profile_id"),
            "deployment_adapter": preview.get("deployment_adapter"),
            "qualification_status": preview.get("status"),
            "known_incompatible_measurements": [],
        }
    if not isinstance(source_revision, str) or not _SOURCE_REVISION.fullmatch(source_revision):
        raise DeploymentVersionGateError(
            "measurement-revision-missing", "resolved target has no exact source revision"
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
        if image_manifest_digest is not None and image_manifest_digest != reference_digest:
            raise DeploymentVersionGateError(
                "measurement-image-manifest-mismatch",
                "measurement image manifest digest does not match its source reference",
            )
        if mode == "stock" and reference_digest != image_digest:
            raise DeploymentVersionGateError(
                "measurement-image-reference-mismatch",
                "measurement image reference digest does not match image_digest",
            )
    elif mode == "patched" and image_reference == image_digest:
        if not isinstance(image_manifest_digest, str) or not _SHA256_DIGEST.fullmatch(
            image_manifest_digest
        ):
            raise DeploymentVersionGateError(
                "measurement-image-manifest-missing",
                "imported patched target requires its qualified manifest digest",
            )
        reference_digest = image_manifest_digest
    else:
        image_reference = f"{image_reference}@{image_digest}"
        reference_digest = image_digest
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
        "image_manifest_digest": reference_digest,
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
