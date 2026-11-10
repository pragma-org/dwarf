"""Bind explicit scenario measurements to a proven deployed real-node runtime."""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from profile_manager.measurement_collectors.amaru_factory import (
    build_amaru_measurement_factories,
)
from profile_manager.measurement_collectors.amaru_patched import (
    AMARU_MEASUREMENT_REVISIONS,
    resolve_amaru_measurement_revision,
)
from profile_manager.measurement_collectors.cardano_factory import (
    build_cardano_measurement_factories,
)
from profile_manager.measurement_collectors.cardano_patched import (
    CARDANO_MEASUREMENT_REVISIONS,
    CARDANO_SOURCE_REVISION,
)
from profile_manager.measurement_resolution import (
    MeasurementResolutionError,
    resolve_measurements,
)
from scripts.runtime_amaru_preview_proof import extract_latest_adopted_tip


TRANSFER_SOURCE_INSTALLATION_ID = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
)
IMMUTABLE_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
SOURCE_REVISION = re.compile(r"^[0-9a-f]{40}$")
LOCAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _measurement_revision_for_patch(
    implementation: str, patch_set_sha256: str
) -> str | None:
    revisions = {
        "amaru": AMARU_MEASUREMENT_REVISIONS,
        "cardano-node": CARDANO_MEASUREMENT_REVISIONS,
    }
    return revisions.get(implementation, {}).get(patch_set_sha256)


AMARU_STOCK_CAPABILITIES = {
    "amaru-header-lifecycle-metrics",
    "amaru-json-traces",
    "amaru-fork-switch-metrics",
    "amaru-mempool-metrics",
    "amaru-ledger-rule-traces",
    "amaru-transaction-validation-spans",
    "amaru-plutus-execution-spans",
    "amaru-block-epoch-spans",
    "amaru-protocol-metrics",
    "amaru-network-traces",
    "amaru-system-metrics",
    "dwarf-container-metrics",
    "dwarf-target-lifecycle",
    "amaru-readiness-probe",
    "dwarf-chain-tip-probe",
    "amaru-chain-tip",
    "dwarf-workload-events",
}
AMARU_JSON_TRACE_CAPABILITIES = {
    "amaru-header-lifecycle-metrics",
    "amaru-json-traces",
    "amaru-fork-switch-metrics",
    "amaru-mempool-metrics",
    "amaru-ledger-rule-traces",
    "amaru-transaction-validation-spans",
    "amaru-plutus-execution-spans",
    "amaru-block-epoch-spans",
    "amaru-protocol-metrics",
    "amaru-network-traces",
}
AMARU_PATCHED_CAPABILITIES = AMARU_STOCK_CAPABILITIES | {
    "amaru-measurement-patch-protocol-decode",
    "amaru-measurement-patch-blockfetch",
    "amaru-measurement-patch-txsubmission2",
    "amaru-patch-identity",
}

CARDANO_STOCK_CAPABILITIES = {
    "cardano-new-tracing",
    "cardano-chainsync-traces",
    "cardano-chaindb-traces",
    "cardano-blockfetch-traces",
    "cardano-txsubmission-traces",
    "cardano-mempool-traces",
    "cardano-ledger-metrics",
    "cardano-network-traces",
    "cardano-keepalive-traces",
    "cardano-resource-traces",
    "dwarf-container-metrics",
    "dwarf-target-lifecycle",
    "cardano-readiness-probe",
    "dwarf-chain-tip-probe",
    "dwarf-workload-events",
}
CARDANO_PATCHED_CAPABILITIES = CARDANO_STOCK_CAPABILITIES | {
    "cardano-patched-protocol-decode",
    "cardano-patched-ledger-plutus-stages",
}
# Capabilities an instrumented Cardano build proves by the recipe revision it was compiled
# from: v3 adds the header-validation emitter, v4 the BlockFetch handler queue, v5 the
# TxSubmission residence; the Antithesis import correction keeps v5's instrumentation.
_CARDANO_V3_CAPABILITIES = frozenset({"cardano-patched-header-validation-instrumentation"})
_CARDANO_V4_CAPABILITIES = _CARDANO_V3_CAPABILITIES | {"cardano-patched-blockfetch-queue"}
_CARDANO_V5_CAPABILITIES = _CARDANO_V4_CAPABILITIES | {"cardano-patched-txsubmission-residence"}
CARDANO_REVISION_CAPABILITIES = {
    "nanoseconds-v3": _CARDANO_V3_CAPABILITIES,
    "nanoseconds-v4": _CARDANO_V4_CAPABILITIES,
    "nanoseconds-v5": _CARDANO_V5_CAPABILITIES,
    "nanoseconds-v5-antithesis1": _CARDANO_V5_CAPABILITIES,
}
CARDANO_PATCH_IDENTITY_CAPABILITY = "cardano-patch-identity"


class MeasurementExecutionError(RuntimeError):
    """The selected measurements cannot be bound to proven runtime evidence."""


def _selected_release(runtime: dict[str, Any], implementation: str) -> dict[str, Any]:
    provenance = runtime.get("versions") or runtime.get("version_provenance") or {}
    snapshot = (provenance.get("catalog_snapshot") or {})
    for release in snapshot.get("selected_releases") or []:
        if release.get("implementation") == implementation:
            return release
    raise MeasurementExecutionError(
        f"runtime catalog snapshot does not contain {implementation}"
    )


def _catalog_revision(runtime: dict[str, Any]) -> str:
    provenance = runtime.get("versions") or runtime.get("version_provenance") or {}
    return str(
        provenance.get("catalog_revision")
        or (provenance.get("catalog_snapshot") or {}).get("catalog_revision")
        or ""
    )


def _verify_service_image_identity(
    service: dict[str, Any],
    *,
    image_reference: str,
    label: str,
    expected_config_id: str | None = None,
) -> None:
    """Bind a digest-pinned manifest reference to its inspected config ID.

    Docker's repository digest and image config ID are distinct hashes.  The
    retained qualification record proves their relationship with
    ``expected_image``/``expected_image_id`` and the observed extractor image
    ID; comparing the config ID directly to the repository digest rejects a
    valid qualified image.
    """
    if str(service.get("expected_image") or "") != image_reference:
        raise MeasurementExecutionError(
            f"deployed {label} image reference does not match runtime identity"
        )
    expected_image_id = str(service.get("expected_image_id") or "")
    artifact_image_id = str(service.get("artifact_image_id") or "")
    if (
        len(expected_image_id) != 71
        or not expected_image_id.startswith("sha256:")
        or (
            expected_config_id is not None
            and expected_image_id != expected_config_id
        )
        or artifact_image_id != expected_image_id
    ):
        raise MeasurementExecutionError(
            f"deployed {label} image config identity does not match runtime"
        )


def _operator_local_identity(
    runtime: dict[str, Any],
    scenario,
    release: dict[str, Any],
    *,
    observed_config_id: str,
    expected_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Recover an exact local registration from retained deployed provenance."""
    registration = release.get("operator_registration")
    snapshot = (
        (runtime.get("versions") or runtime.get("version_provenance") or {}).get(
            "catalog_snapshot"
        )
        or {}
    )
    if not isinstance(registration, dict):
        raise MeasurementExecutionError("local runtime registration is unavailable")
    registration_id = str(registration.get("registration_id") or "")
    config_id = str(registration.get("config_image_id") or "")
    retained_executable = registration.get("executable_digest")
    expected_identity = expected_identity or {}
    executable_digest = str(
        retained_executable or expected_identity.get("executable_digest") or ""
    )
    platform = str(registration.get("platform") or "")
    controller_installation_id = str(
        registration.get("controller_installation_id") or ""
    )
    overlay_revision = str(snapshot.get("operator_overlay_revision") or "")
    registration_provenance = registration.get("provenance")
    if not isinstance(registration_provenance, dict):
        raise MeasurementExecutionError("local runtime registration identity is invalid")
    source_revision = registration_provenance.get("source_revision")
    version = str(registration.get("version") or "")
    if (
        registration.get("implementation") != scenario.target.get("implementation")
        or version != scenario.target.get("version")
        or snapshot.get("operator_registration_id") != registration_id
        or not LOCAL_ID.fullmatch(registration_id)
        or not IMMUTABLE_SHA256.fullmatch(config_id)
        or not IMMUTABLE_SHA256.fullmatch(executable_digest)
        or not IMMUTABLE_SHA256.fullmatch(overlay_revision)
        or platform not in {"linux/amd64", "linux/arm64"}
        or not LOCAL_ID.fullmatch(controller_installation_id)
        or (
            source_revision is not None
            and not SOURCE_REVISION.fullmatch(str(source_revision))
        )
    ):
        raise MeasurementExecutionError("local runtime registration identity is invalid")
    if retained_executable is None and any(
        expected_identity.get(field) != expected
        for field, expected in (
            ("implementation", registration.get("implementation")),
            ("version", version),
            ("image_config_id", config_id),
            ("platform", platform),
            ("operator_registration_id", registration_id),
            ("operator_overlay_revision", overlay_revision),
            ("controller_installation_id", controller_installation_id),
        )
    ):
        raise MeasurementExecutionError(
            "legacy local runtime cannot recover its executable identity"
        )
    if observed_config_id != config_id:
        raise MeasurementExecutionError(
            "deployed local image config identity does not match registration"
        )
    identity = {
        "implementation": registration["implementation"],
        "version": version,
        "source_revision": source_revision,
        "mode": "stock",
        "identity_kind": "operator-local-config",
        "image_reference": config_id,
        "image_digest": None,
        "image_config_id": config_id,
        "executable_digest": executable_digest,
        "platform": platform,
        "operator_registration_id": registration_id,
        "operator_overlay_revision": overlay_revision,
        "controller_installation_id": controller_installation_id,
        "version_catalog_revision": _catalog_revision(runtime),
        "known_incompatible_measurements": list(
            registration.get("known_incompatible_measurements") or []
        ),
    }
    for field in ("verified_instrumentation",):
        if registration.get(field) is not None:
            identity[field] = registration[field]
    from profile_manager.release_build_identity import instrumented_identity
    return instrumented_identity(registration, identity)


def _stock_amaru_identity(
    runtime: dict[str, Any], scenario, *, expected_identity: dict[str, Any] | None = None
) -> dict[str, Any]:
    if runtime.get("profile_id") != scenario.profile:
        raise MeasurementExecutionError("runtime profile does not match scenario profile")
    runtime_identity = runtime.get("identity") or {}
    if runtime_identity.get("matched") is not True:
        raise MeasurementExecutionError("deployed Amaru identity is not proven")
    release = _selected_release(runtime, "amaru")
    version = str(release.get("version") or "")
    source_revision = str(release.get("source_revision") or "")
    if version != scenario.target.get("version"):
        raise MeasurementExecutionError(
            f"deployed Amaru version {version or 'unknown'} does not match scenario"
        )
    registration = release.get("operator_registration")
    if isinstance(registration, dict):
        service = ((runtime_identity.get("services") or {}).get("amaru-relay-1") or {})
        if service.get("matched") is not True:
            raise MeasurementExecutionError("Amaru relay identity is not proven")
        config_id = str(registration.get("config_image_id") or "")
        _verify_service_image_identity(
            service,
            image_reference=config_id,
            label="local Amaru",
            expected_config_id=config_id,
        )
        return _operator_local_identity(
            runtime,
            scenario,
            release,
            observed_config_id=str(service.get("artifact_image_id") or ""),
            expected_identity=expected_identity,
        )
    artifact = next(
        (
            item
            for item in release.get("artifacts") or []
            if item.get("kind") == "oci" and item.get("availability") == "available"
        ),
        None,
    )
    if artifact is None:
        raise MeasurementExecutionError("runtime catalog has no immutable Amaru image")
    digest = str(artifact.get("digest") or "")
    image_reference = str((runtime.get("images") or {}).get("amaru") or "")
    service = ((runtime_identity.get("services") or {}).get("amaru-relay-1") or {})
    if service.get("matched") is not True:
        raise MeasurementExecutionError("Amaru relay identity is not proven")
    if not image_reference.endswith("@" + digest):
        raise MeasurementExecutionError("runtime Amaru image reference is not digest-pinned")
    _verify_service_image_identity(
        service,
        image_reference=image_reference,
        label="Amaru",
    )
    return {
        "implementation": "amaru",
        "version": version,
        "source_revision": source_revision,
        "mode": "stock",
        "image_reference": image_reference,
        "image_digest": digest,
        "executable_digest": None,
        "version_catalog_revision": _catalog_revision(runtime),
    }


def _immutable_sha256(value: Any, field: str) -> str:
    text = str(value or "")
    if not text.startswith("sha256:") or len(text) != 71:
        raise MeasurementExecutionError(
            f"deployed patched Amaru {field} is not immutable"
        )
    return text


def _patched_amaru_identity(runtime: dict[str, Any], scenario) -> dict[str, Any]:
    if runtime.get("profile_id") != scenario.profile:
        raise MeasurementExecutionError("runtime profile does not match scenario profile")
    runtime_identity = runtime.get("identity") or {}
    if runtime_identity.get("matched") is not True:
        raise MeasurementExecutionError("deployed Amaru identity is not proven")
    release = _selected_release(runtime, "amaru")
    target = runtime.get("measurement_target") or {}
    if target.get("target_mode") != "patched":
        raise MeasurementExecutionError("runtime is not a patched Amaru target")
    version = str(target.get("version") or "")
    if version != scenario.target.get("version") or version != str(release.get("version") or ""):
        raise MeasurementExecutionError(
            f"deployed patched Amaru version {version or 'unknown'} does not match scenario"
        )
    source_revision = str(target.get("source_revision") or "")
    if source_revision != str(release.get("source_revision") or ""):
        raise MeasurementExecutionError(
            "deployed patched Amaru source revision does not match collector"
        )
    patch_set = str(target.get("patch_set_sha256") or "")
    measurement_revision = resolve_amaru_measurement_revision(
        source_revision, patch_set
    )
    if measurement_revision is None:
        raise MeasurementExecutionError(
            "deployed patched Amaru source revision and patch-set identity do not match collector"
        )
    image_digest = _immutable_sha256(target.get("image_digest"), "image config ID")
    executable_digest = _immutable_sha256(
        target.get("executable_digest"), "executable digest"
    )
    build_result_sha256 = _immutable_sha256(
        target.get("build_result_sha256"), "build result"
    )
    runtime_probe_log_sha256 = _immutable_sha256(
        target.get("runtime_probe_log_sha256"), "runtime probe log"
    )
    image_reference = str(target.get("image") or target.get("image_reference") or "")
    imported_manifest_digest = None
    source_image_reference = None
    transfer_source_installation_id = None
    imported_config_digest = None
    qualification_image_id = None
    transfer_manifest_sha256 = None
    local_manifest_digest = None
    if "@" in image_reference:
        # A locally built patched target is addressed by its repository digest
        # (`dwarf/amaru-measurement@sha256:…`); the run plan freezes that digest as
        # image_manifest_digest, so the runtime identity carries it too.
        local_manifest_digest = _immutable_sha256(
            image_reference.rsplit("@", 1)[1],
            "repository manifest digest",
        )
    elif image_reference == image_digest:
        imported_manifest_digest = _immutable_sha256(
            target.get("image_manifest_digest"),
            "repository manifest digest",
        )
        imported_config_digest = _immutable_sha256(
            target.get("image_config_digest"),
            "imported image config digest",
        )
        if imported_config_digest != image_digest:
            raise MeasurementExecutionError(
                "runtime patched Amaru image config identity is not proven"
            )
        qualification_image_id = _immutable_sha256(
            target.get("qualification_image_id"),
            "qualification image identity",
        )
        transfer_manifest_sha256 = _immutable_sha256(
            target.get("transfer_manifest_sha256"),
            "transfer manifest digest",
        )
        source_image_reference = str(target.get("source_image_reference") or "")
        if not source_image_reference.endswith("@" + imported_manifest_digest):
            raise MeasurementExecutionError(
                "runtime patched Amaru source manifest identity is not proven"
            )
        transfer_source_installation_id = str(
            target.get("transfer_source_installation_id") or ""
        )
        if not TRANSFER_SOURCE_INSTALLATION_ID.fullmatch(
            transfer_source_installation_id
        ):
            raise MeasurementExecutionError(
                "runtime patched Amaru source installation identity is not proven"
            )
    else:
        raise MeasurementExecutionError(
            "runtime patched Amaru image reference is not immutable"
        )
    service = ((runtime_identity.get("services") or {}).get("amaru-relay-1") or {})
    if service.get("matched") is not True:
        raise MeasurementExecutionError("patched Amaru relay identity is not proven")
    _verify_service_image_identity(
        service,
        image_reference=image_reference,
        label="patched Amaru",
        expected_config_id=image_digest,
    )
    if service.get("executable_digest_matched") is not True:
        raise MeasurementExecutionError(
            "deployed patched Amaru executable identity is not proven"
        )
    if str(service.get("executable_digest_reported") or "") != executable_digest:
        raise MeasurementExecutionError(
            "deployed patched Amaru executable digest does not match runtime"
        )
    result = {
        "implementation": "amaru",
        "version": version,
        "source_revision": source_revision,
        "mode": "patched",
        "patch_set_sha256": patch_set,
        "measurement_revision": measurement_revision,
        "image_reference": image_reference,
        "image_digest": image_digest,
        "executable_digest": executable_digest,
        "build_result_sha256": build_result_sha256,
        "runtime_probe_log_sha256": runtime_probe_log_sha256,
        "version_catalog_revision": _catalog_revision(runtime),
    }
    if imported_manifest_digest is not None:
        result["image_manifest_digest"] = imported_manifest_digest
        result["image_config_digest"] = imported_config_digest
        result["qualification_image_id"] = qualification_image_id
        result["transfer_manifest_sha256"] = transfer_manifest_sha256
        result["source_image_reference"] = source_image_reference
        result[
            "transfer_source_installation_id"
        ] = transfer_source_installation_id
    elif local_manifest_digest is not None:
        result["image_manifest_digest"] = local_manifest_digest
    return result


def _container_socket_path(container: str) -> str | None:
    """The --socket-path the node container was started with (its own command line)."""
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{json .Config.Cmd}}", container],
        capture_output=True, text=True, check=False, timeout=30,
    )
    if result.returncode != 0:
        return None
    try:
        command = json.loads(result.stdout) or []
    except ValueError:
        return None
    for flag, value in zip(command, command[1:]):
        if flag == "--socket-path":
            return str(value)
    return None


def _mixed_cardano_nodes(runtime: dict[str, Any]) -> list[dict[str, Any]]:
    """Cardano-node targets of a mixed Cardano/Amaru controller runtime (no ``nodes`` list).

    Built only from what the deploy proved: the selected node service, its identity record
    (running image id and the version/commit the binary reported) and the pinned image."""
    topology = runtime.get("actual_topology") or {}
    cardano_services = set(topology.get("cardano_services") or [])
    services = ((runtime.get("identity") or {}).get("services")) or {}
    image_ref = str((runtime.get("images") or {}).get("cardano-node") or "")
    nodes = []
    for node_id, service in (topology.get("selected_node_services") or {}).items():
        record = services.get(service)
        if service not in cardano_services or not isinstance(record, dict):
            continue
        running = str(record.get("running_image_id") or "")
        proven = record.get("matched") is True and running == str(record.get("expected_image_id") or "")
        container = str(record.get("container") or "")
        nodes.append({
            "id": node_id, "impl": "cardano-node", "service": service,
            "container_name": container,
            "container_socket_path": _container_socket_path(container) if container else None,
            "source_revision": str(record.get("reported_git_commit") or ""),
            "image_ref": image_ref, "target_mode": "stock",
            "artifact_identity": {"satisfied": proven, "container_image_id": running},
            "version_identity": {"satisfied": proven, "reported_version": record.get("expected_version")},
        })
    return nodes


def _stock_cardano_identity(
    runtime: dict[str, Any], scenario, *, expected_identity: dict[str, Any] | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    runtime_profile = str(runtime.get("profile_id") or "")
    if runtime_profile != scenario.profile:
        raise MeasurementExecutionError("runtime profile does not match scenario profile")
    release = _selected_release(runtime, "cardano-node")
    version = str(release.get("version") or "")
    source_revision = str(release.get("source_revision") or "")
    if version != scenario.target.get("version"):
        raise MeasurementExecutionError(
            f"deployed Cardano-node version {version or 'unknown'} does not match scenario"
        )
    artifact = next(
        (
            item
            for item in release.get("artifacts") or []
            if item.get("kind") == "oci" and item.get("availability") == "available"
        ),
        None,
    )
    if artifact is None:
        raise MeasurementExecutionError("runtime catalog has no immutable Cardano-node image")
    digest = str(artifact.get("digest") or "")
    nodes = [
        node
        for node in (runtime.get("nodes") or runtime.get("haskell_nodes") or _mixed_cardano_nodes(runtime))
        if node.get("impl") == "cardano-node"
    ]
    if not nodes:
        raise MeasurementExecutionError("runtime has no Cardano-node target")
    node = nodes[0]
    artifact_identity = node.get("artifact_identity") or {}
    version_identity = node.get("version_identity") or {}
    if artifact_identity.get("satisfied") is not True or version_identity.get("satisfied") is not True:
        raise MeasurementExecutionError("deployed Cardano-node identity is not proven")
    observed_digest = str(
        artifact_identity.get("image_digest")
        or artifact_identity.get("container_image_id")
        or node.get("image_digest")
        or ""
    )
    image_reference = str(node.get("image_ref") or node.get("image") or "")
    if isinstance(release.get("operator_registration"), dict):
        identity = _operator_local_identity(
            runtime,
            scenario,
            release,
            observed_config_id=observed_digest,
            expected_identity=expected_identity,
        )
        if image_reference != identity["image_config_id"]:
            raise MeasurementExecutionError(
                "deployed local Cardano-node image config identity does not match runtime"
            )
        return identity, node
    if str(node.get("source_revision") or "") != source_revision:
        raise MeasurementExecutionError("deployed Cardano-node source revision does not match catalog")
    mode = str(node.get("target_mode") or "stock")
    if mode == "patched":
        if source_revision != CARDANO_SOURCE_REVISION:
            raise MeasurementExecutionError("patched Cardano-node source revision does not match collector")
        measurement_revision = _measurement_revision_for_patch(
            "cardano-node", str(node.get("patch_set_sha256") or "")
        )
        if measurement_revision is None:
            raise MeasurementExecutionError("patched Cardano-node patch-set identity does not match collector")
        digest = str(node.get("image_digest") or observed_digest)
        if observed_digest != digest:
            raise MeasurementExecutionError("deployed patched Cardano-node image digest does not match runtime")
        for field in (
            "image_digest", "executable_digest", "build_result_sha256",
            "runtime_probe_log_sha256",
        ):
            value = str(node.get(field) or "")
            if not value.startswith("sha256:") or len(value) != 71:
                raise MeasurementExecutionError(
                    f"deployed patched Cardano-node {field} is not immutable"
                )
        if not image_reference.endswith("@" + digest):
            raise MeasurementExecutionError(
                "runtime patched Cardano-node image reference is not digest-pinned"
            )
        return ({
            "implementation": "cardano-node",
            "version": version,
            "source_revision": source_revision,
            "mode": "patched",
            "patch_set_sha256": node["patch_set_sha256"],
            "measurement_revision": measurement_revision,
            "image_reference": image_reference,
            "image_digest": digest,
            # The digest-pinned reference is the repository digest the run plan
            # froze as image_manifest_digest for the exact-target verifier.
            "image_manifest_digest": digest,
            "executable_digest": node["executable_digest"],
            "build_result_sha256": node["build_result_sha256"],
            "runtime_probe_log_sha256": node["runtime_probe_log_sha256"],
            "version_catalog_revision": _catalog_revision(runtime),
        }, node)
    if mode != "stock":
        raise MeasurementExecutionError(f"unsupported Cardano-node target mode: {mode}")
    if observed_digest != digest:
        raise MeasurementExecutionError("deployed Cardano-node image digest does not match catalog")
    if not image_reference.endswith("@" + digest):
        raise MeasurementExecutionError("runtime Cardano-node image reference is not digest-pinned")
    return ({
        "implementation": "cardano-node",
        "version": version,
        "source_revision": source_revision,
        "mode": "stock",
        "image_reference": image_reference,
        "image_digest": digest,
        "executable_digest": None,
        "version_catalog_revision": _catalog_revision(runtime),
    }, node)


def _docker_tip_probe(runtime: dict[str, Any]) -> Callable[[], dict[str, Any]]:
    service = (((runtime.get("identity") or {}).get("services") or {}).get(
        "amaru-relay-1"
    ) or {})
    container = str(service.get("container") or "")
    if not container:
        raise MeasurementExecutionError("runtime has no Amaru relay container identity")
    return _docker_amaru_container_tip_probe(container)


def _docker_amaru_container_tip_probe(container: str) -> Callable[[], dict[str, Any]]:
    """Latest adopted tip from the exact Amaru container's own log."""

    def probe() -> dict[str, Any]:
        result = subprocess.run(
            ["docker", "logs", "--tail", "4000", container],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "cannot read Amaru logs")
        tip = extract_latest_adopted_tip(result.stdout + "\n" + result.stderr)
        if tip is None:
            raise RuntimeError("Amaru logs contain no adopted tip")
        return tip

    return probe


def _docker_cardano_tip_probe(runtime: dict[str, Any], node: dict[str, Any]) -> Callable[[], dict[str, Any]]:
    container = str(node.get("container_name") or "")
    socket_path = str(node.get("container_socket_path") or "")
    network_magic = runtime.get("network_magic")
    if not container or not socket_path or not isinstance(network_magic, int):
        raise MeasurementExecutionError("runtime has no trusted Cardano-node tip-probe identity")

    def probe() -> dict[str, Any]:
        result = subprocess.run(
            [
                "docker", "exec", container, "cardano-cli", "query", "tip",
                "--socket-path", socket_path, "--testnet-magic", str(network_magic),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "cannot query Cardano-node tip")
        body = json.loads(result.stdout)
        height = body.get("block")
        if isinstance(height, bool) or not isinstance(height, int):
            raise RuntimeError("Cardano-node tip has no numeric block height")
        return {"block_height": height, "block_hash": body.get("hash")}

    return probe


@dataclass(frozen=True)
class PreparedScenarioMeasurements:
    resolution: dict[str, Any]
    runtime_metadata_path: Path
    target_node: str
    tip_probe: Callable[[], dict[str, Any]]
    implementation: str = "amaru"
    patched_trace_paths: tuple[Path, ...] = ()
    # Self-composed substrates: the composed node's own log is the trace source and
    # the collectors read it live (offset at start, delta at finalize).
    trace_paths: tuple[Path, ...] | None = None
    peer_policy: str | None = None
    # Profile Cardano runs: the target container whose own log the runner captures
    # for the run window (capture_container_trace_window) into run_window_trace_path.
    trace_container: str | None = None

    def run_window_trace_path(self, run_dir: str | Path) -> Path:
        return (
            Path(run_dir) / "outputs" / "cardano-measurement-run" / "raw"
            / f"{self.target_node}.ndjson"
        )

    def build_factories(self, run_dir: str | Path) -> dict[str, Any]:
        run_path = Path(run_dir)
        if self.implementation == "cardano-node" and self.trace_paths is not None:
            all_factories = build_cardano_measurement_factories(
                runtime_metadata_path=self.runtime_metadata_path,
                target_node=self.target_node,
                trace_paths=self.trace_paths,
                tip_probe=self.tip_probe,
                peer_policy=self.peer_policy or "unspecified",
                allow_missing_trace_sources=True,
                patched_trace_paths=self.patched_trace_paths,
                target_identity=self.resolution["target_identity"],
            )
            return self._resolved_factories(all_factories)
        if self.implementation == "amaru" and self.trace_paths is not None:
            all_factories = build_amaru_measurement_factories(
                runtime_metadata_path=self.runtime_metadata_path,
                target_node=self.target_node,
                json_trace_paths=self.trace_paths,
                otlp_trace_paths=[],
                tip_probe=self.tip_probe,
                peer_policy=self.peer_policy or "unspecified",
                target_identity=self.resolution["target_identity"],
                allow_missing_trace_sources=True,
            )
            return self._resolved_factories(all_factories)
        if self.implementation == "cardano-node":
            traces = (
                run_path
                / "outputs"
                / "cardano-measurement-calibration"
                / "raw"
                / "node1.ndjson",
                run_path
                / "outputs"
                / "protocol-decode-cases"
                / "raw"
                / "node1.ndjson",
                self.run_window_trace_path(run_path),
            )
            all_factories = build_cardano_measurement_factories(
                runtime_metadata_path=self.runtime_metadata_path,
                target_node=self.target_node,
                trace_paths=traces,
                tip_probe=self.tip_probe,
                peer_policy="three-node-controlled-local-mesh",
                allow_missing_trace_sources=True,
                patched_trace_paths=self.patched_trace_paths,
                target_identity=self.resolution["target_identity"],
            )
            return self._resolved_factories(all_factories)
        traces = (
            run_path
            / "outputs"
            / "amaru-measurement-run"
            / "raw"
            / "amaru-relay-1.ndjson",
        )
        all_factories = build_amaru_measurement_factories(
            runtime_metadata_path=self.runtime_metadata_path,
            target_node=self.target_node,
            json_trace_paths=traces,
            otlp_trace_paths=[],
            tip_probe=self.tip_probe,
            peer_policy="single-controlled-producer",
            target_identity=self.resolution["target_identity"],
            allow_missing_trace_sources=True,
        )
        return self._resolved_factories(all_factories)

    def _resolved_factories(self, all_factories: dict[str, Any]) -> dict[str, Any]:
        """Expose factories for exactly the resolved taps.

        The builders register every version-pinned collector the target can
        support; a tap only runs when the resolved measurement selection
        includes it. Returning the resolved-and-registered intersection keeps
        the registered factories and the resolved selection consistent, while a
        resolved tap without a registered collector stays absent here so callers
        still report it as unavailable.
        """
        return {
            entry["id"]: all_factories[entry["id"]]
            for entry in self.resolution.get("resolved", [])
            if entry["id"] in all_factories
        }



def prepare_scenario_measurements(
    scenario,
    *,
    runtime_metadata_path: str | Path | None = None,
    tip_probe: Callable[[], dict[str, Any]] | None = None,
    expected_resolution: dict[str, Any] | None = None,
) -> PreparedScenarioMeasurements:
    """Resolve an explicit measurement selection against live real-node evidence."""
    implementation = scenario.target.get("implementation")
    if implementation not in {"amaru", "cardano-node"}:
        raise MeasurementExecutionError("runtime measurement binding is unsupported")
    if not scenario.profile:
        raise MeasurementExecutionError("measurements require a deployed profile")
    if runtime_metadata_path is None:
        from profile_manager.profiles import runtime_root

        runtime_metadata_path = runtime_root(scenario.profile) / "runtime.json"
    path = Path(runtime_metadata_path)
    try:
        runtime = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MeasurementExecutionError(
            f"fresh deployed runtime is unavailable: {path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise MeasurementExecutionError(f"runtime metadata is invalid: {path}") from exc
    expected_identity = (
        expected_resolution.get("target_identity")
        if isinstance(expected_resolution, dict)
        else None
    )
    if implementation == "amaru":
        target_mode = str(
            (runtime.get("measurement_target") or {}).get("target_mode") or "stock"
        )
        if target_mode == "patched":
            identity = _patched_amaru_identity(runtime, scenario)
            capabilities = set(AMARU_PATCHED_CAPABILITIES)
        elif target_mode == "stock":
            identity = _stock_amaru_identity(
                runtime, scenario, expected_identity=expected_identity
            )
            capabilities = set(AMARU_STOCK_CAPABILITIES)
        else:
            raise MeasurementExecutionError(
                f"unsupported Amaru target mode: {target_mode}"
            )
        target_node = "amaru-relay-1"
        resolved_tip_probe = tip_probe or _docker_tip_probe(runtime)
        if runtime.get("amaru_json_traces") is not True:
            capabilities -= AMARU_JSON_TRACE_CAPABILITIES
    else:
        identity, node = _stock_cardano_identity(
            runtime, scenario, expected_identity=expected_identity
        )
        target_node = str(node.get("id") or "node1")
        resolved_tip_probe = tip_probe or _docker_cardano_tip_probe(runtime, node)
        capabilities = (
            CARDANO_PATCHED_CAPABILITIES
            if identity["mode"] == "patched"
            else CARDANO_STOCK_CAPABILITIES
        )
        patched_trace_paths: tuple[Path, ...] = ()
        if identity["mode"] == "patched":
            runtime_root = Path(str(runtime.get("runtime_root") or ""))
            if not runtime_root.is_absolute():
                raise MeasurementExecutionError(
                    "patched Cardano-node runtime root is not an absolute retained path"
                )
            patched_trace_paths = (
                runtime_root / "logs" / target_node / "cardano-measurement.ndjson",
                runtime_root / "logs" / target_node / "cardano-plutus.ndjson",
            )
    if identity.get("identity_kind") == "operator-local-config":
        if implementation == "amaru" and identity["mode"] == "patched":
            capabilities = set(AMARU_PATCHED_CAPABILITIES)
        instrumentation = identity.get("verified_instrumentation") or {}
        capabilities = set(capabilities) | set(instrumentation.get("capabilities") or [])
        if implementation == "amaru" and runtime.get("amaru_json_traces") is not True:
            capabilities -= AMARU_JSON_TRACE_CAPABILITIES
        if not isinstance(expected_resolution, dict):
            raise MeasurementExecutionError(
                "local measurements require the frozen launch resolution"
            )
        exact_local_fields = (
            "implementation",
            "version",
            "source_revision",
            "mode",
            "identity_kind",
            "image_reference",
            "image_digest",
            "image_config_id",
            "executable_digest",
            "platform",
            "operator_registration_id",
            "controller_installation_id",
            "version_catalog_revision",
        )
        mismatches = [
            field
            for field in exact_local_fields
            if expected_identity.get(field) != identity.get(field)
        ]
        # The overlay hashes the entire operator ledger, not this deployed
        # artifact. An unrelated registration may advance it without changing
        # any selected immutable identity. Keep the frozen revision for consent
        # revalidation below; do not replace it with the runtime's older value.
        if not IMMUTABLE_SHA256.fullmatch(
            str(expected_identity.get("operator_overlay_revision") or "")
        ):
            mismatches.append("operator_overlay_revision")
        if (
            (expected_identity.get("known_incompatible_measurements") or [])
            != (identity.get("known_incompatible_measurements") or [])
            or expected_identity.get("verified_instrumentation")
            != identity.get("verified_instrumentation")
        ):
            mismatches.append("capability evidence")
        if mismatches:
            raise MeasurementExecutionError(
                "deployed local measurement identity does not match frozen launch"
            )
        identity = json.loads(json.dumps(expected_identity))
        retained_consent = expected_resolution.get("experimental_consent") or {}
        experimental_consent = {
            "tap_ids": retained_consent.get("tap_ids"),
            "context_digest": retained_consent.get("context_digest"),
        }
    else:
        experimental_consent = None
    try:
        resolution = resolve_measurements(
            scenario,
            target_identity=identity,
            capabilities=capabilities,
            experimental_consent=experimental_consent,
        )
    except MeasurementResolutionError as exc:
        raise MeasurementExecutionError(str(exc)) from exc
    return PreparedScenarioMeasurements(
        resolution=resolution,
        runtime_metadata_path=path,
        target_node=target_node,
        tip_probe=resolved_tip_probe,
        implementation=implementation,
        patched_trace_paths=(
            patched_trace_paths if implementation == "cardano-node" else ()
        ),
        trace_container=(
            str(node.get("container_name") or "") or None
            if implementation == "cardano-node"
            else None
        ),
    )


def capture_container_trace_window(
    container: str,
    *,
    started_at: str,
    ended_at: str,
    destination: str | Path,
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """Retain the measured node container's own JSON log records for one run window.

    The node's log is the trace source the stock collectors read; without it they see
    only what a calibration primitive happened to write. Non-JSON lines are dropped,
    records are kept byte-for-byte."""
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    result = runner(
        ["docker", "logs", "--since", started_at, "--until", ended_at, container],
        capture_output=True, text=True, check=False, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(
            (result.stderr or "").strip()[:256] or "cannot read the measured node container log"
        )
    rows = []
    for line in (str(result.stdout or "") + "\n" + str(result.stderr or "")).splitlines():
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            record = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            rows.append(text)
    body = "".join(row + "\n" for row in rows)
    path.write_text(body, encoding="utf-8")
    return {"container": container, "record_count": len(rows), "bytes": len(body.encode("utf-8"))}


# Version sentinels a portable scenario target may still carry when it runs without a
# launch (the composed node's own proven version is then the target version).
_PORTABLE_TARGET_VERSIONS = frozenset({"resolved-plan", "any", ""})


def _composed_target_node(runtime: dict[str, Any], implementation: str) -> dict[str, Any]:
    """The measured node of a self-composed substrate (runtime_compose_substrate).

    Like ``_mixed_cardano_nodes`` it uses only what the compose step proved: the first
    non-supporting node of the scenario's target implementation, in composed order.
    Supporting producers and the other implementation's nodes are never measured."""
    nodes = runtime.get("nodes")
    if not isinstance(nodes, list):
        nodes = list(runtime.get("haskell_nodes") or []) + list(runtime.get("amaru_nodes") or [])
    for node in nodes:
        if not isinstance(node, dict):
            continue
        impl = str(node.get("impl") or node.get("implementation") or "")
        if impl == implementation and node.get("supporting") is not True:
            return node
    raise MeasurementExecutionError(
        f"runtime has no composed {implementation} target node"
    )


def _composed_node_identity(
    node: dict[str, Any],
    scenario,
    *,
    expected_identity: dict[str, Any] | None,
    catalog_revision: str,
) -> dict[str, Any]:
    implementation = str(node.get("impl") or node.get("implementation") or "")
    artifact = node.get("artifact_identity") or {}
    version_identity = node.get("version_identity") or {}
    running = str(artifact.get("container_image_id") or "")
    if (
        artifact.get("satisfied") is not True
        or version_identity.get("satisfied") is not True
        or not IMMUTABLE_SHA256.fullmatch(running)
        or running != str(artifact.get("expected_image_id") or "")
    ):
        raise MeasurementExecutionError(
            f"composed {implementation} identity is not proven"
        )
    version = str(node.get("version") or "")
    declared = str(scenario.target.get("version") or "")
    if declared not in _PORTABLE_TARGET_VERSIONS and declared != version:
        raise MeasurementExecutionError(
            f"composed {implementation} version {version or 'unknown'} does not match scenario"
        )
    source_revision = node.get("source_revision")
    if node.get("operator_registration_id"):
        # Operator-local build: its capability evidence (verified instrumentation,
        # known incompatibilities) lives in the frozen launch identity only.
        if not isinstance(expected_identity, dict) or (
            expected_identity.get("identity_kind") != "operator-local-config"
        ):
            raise MeasurementExecutionError(
                "operator-local composed target requires the frozen launch identity"
            )
        observed = {
            "implementation": implementation,
            "version": version,
            "source_revision": source_revision,
            "image_config_id": running,
            "executable_digest": node.get("executable_digest"),
            "platform": node.get("platform"),
            "operator_registration_id": node.get("operator_registration_id"),
            "controller_installation_id": node.get("controller_installation_id"),
        }
        if str(node.get("image_config_id") or running) != running or any(
            expected_identity.get(field) != value for field, value in observed.items()
        ):
            raise MeasurementExecutionError(
                "composed local measurement identity does not match frozen launch"
            )
        return json.loads(json.dumps(expected_identity))
    digest = str(artifact.get("image_digest") or node.get("image_digest") or "")
    image_reference = str(node.get("image_ref") or node.get("image") or "")
    if not IMMUTABLE_SHA256.fullmatch(digest) or not image_reference.endswith("@" + digest):
        raise MeasurementExecutionError(
            f"composed {implementation} image reference is not digest-pinned"
        )
    if not SOURCE_REVISION.fullmatch(str(source_revision or "")):
        raise MeasurementExecutionError(
            f"composed {implementation} source revision is not an exact commit"
        )
    if str(node.get("target_mode") or "stock") != "stock":
        raise MeasurementExecutionError(
            "patched composed targets are not bound to measurements"
        )
    identity = {
        "implementation": implementation,
        "version": version,
        "source_revision": source_revision,
        "mode": "stock",
        "image_reference": image_reference,
        "image_digest": digest,
        "executable_digest": None,
        "version_catalog_revision": catalog_revision,
    }
    if isinstance(expected_identity, dict) and expected_identity:
        mismatched = [
            field
            for field in ("implementation", "version", "source_revision", "mode", "image_digest")
            if expected_identity.get(field) != identity[field]
        ]
        if mismatched:
            raise MeasurementExecutionError(
                "composed measurement identity does not match frozen launch: "
                + ", ".join(mismatched)
            )
    return identity


def prepare_substrate_measurements(
    scenario,
    *,
    runtime_metadata_path: str | Path,
    expected_resolution: dict[str, Any] | None = None,
    tip_probe: Callable[[], dict[str, Any]] | None = None,
) -> PreparedScenarioMeasurements:
    """Resolve taps against the node a self-composed substrate proved it runs.

    Called once ``runtime_compose_substrate`` has written its runtime.json. The tap
    selection is the same resolver the profile path uses (default profile per
    implementation and mode, or the scenario's frozen/override selection); only the
    target binding differs: the composed node's verified image and version identity."""
    implementation = scenario.target.get("implementation")
    if implementation not in {"amaru", "cardano-node"}:
        raise MeasurementExecutionError("runtime measurement binding is unsupported")
    path = Path(runtime_metadata_path)
    try:
        runtime = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MeasurementExecutionError(f"composed runtime is unavailable: {path}") from exc
    except json.JSONDecodeError as exc:
        raise MeasurementExecutionError(f"runtime metadata is invalid: {path}") from exc
    if runtime.get("multi_host") is True:
        # Remote nodes: no local log file, container or socket for the collectors.
        raise MeasurementExecutionError(
            "multi-host substrate nodes are not locally observable by the collectors"
        )
    if runtime.get("compose_mode") != "docker":
        raise MeasurementExecutionError(
            "only Docker-composed substrate nodes are bound to measurements"
        )
    node = _composed_target_node(runtime, implementation)
    expected = (
        expected_resolution
        if isinstance(expected_resolution, dict) and expected_resolution.get("profile") is not None
        else None
    )
    expected_identity = (expected or {}).get("target_identity") or None
    identity = _composed_node_identity(
        node,
        scenario,
        expected_identity=expected_identity,
        catalog_revision=_catalog_revision(runtime),
    )
    target_node = str(node.get("id") or node.get("name") or "")
    log_path = str(node.get("log_path") or "")
    if implementation == "amaru":
        capabilities = set(AMARU_STOCK_CAPABILITIES)
        json_traces = runtime.get("amaru_json_traces") is True
        if not json_traces:
            capabilities -= AMARU_JSON_TRACE_CAPABILITIES
        trace_paths = (Path(log_path),) if json_traces and log_path else ()
        container = str(node.get("container_name") or "")
        if tip_probe is None:
            if not container:
                raise MeasurementExecutionError("composed Amaru node has no container identity")
            tip_probe = _docker_amaru_container_tip_probe(container)
    else:
        capabilities = set(CARDANO_STOCK_CAPABILITIES)
        trace_paths = (Path(log_path),) if log_path else ()
        if tip_probe is None:
            tip_probe = _docker_cardano_tip_probe(runtime, node)
    experimental_consent = None
    if identity.get("identity_kind") == "operator-local-config":
        instrumentation = identity.get("verified_instrumentation") or {}
        capabilities |= set(instrumentation.get("capabilities") or [])
        if implementation == "amaru" and runtime.get("amaru_json_traces") is not True:
            capabilities -= AMARU_JSON_TRACE_CAPABILITIES
        retained = (expected or {}).get("experimental_consent") or {}
        if retained:
            experimental_consent = {
                "tap_ids": retained.get("tap_ids"),
                "context_digest": retained.get("context_digest"),
            }
    try:
        resolution = resolve_measurements(
            scenario,
            target_identity=identity,
            capabilities=capabilities,
            experimental_consent=experimental_consent,
        )
    except MeasurementResolutionError as exc:
        raise MeasurementExecutionError(str(exc)) from exc
    return PreparedScenarioMeasurements(
        resolution=resolution,
        runtime_metadata_path=path,
        target_node=target_node,
        tip_probe=tip_probe,
        implementation=implementation,
        trace_paths=trace_paths,
        peer_policy="self-composed-substrate",
    )
