"""Pure health/runtime-tip helpers for the dashboard data layer."""
from __future__ import annotations

import json
import os
import re
import shlex
import socket
import subprocess
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from profile_manager.config import storage_roots

from profile_manager.config import config_exists, load_config
from profile_manager.data.files import _latest_files, _read_text
from profile_manager.inspect import inspect_health_command
from profile_manager.profiles import load_profiles
from profile_manager.release_identity import (
    CATALOG_DIGEST_RE,
    GIT_REVISION_RE,
    CatalogIdentityError,
    catalog_snapshot,
)
from profile_manager.remote import CommandResult, ssh_command


DWARF_ROOT = Path(__file__).resolve().parents[2]
RELEASE_IDENTITY_PATH = DWARF_ROOT / "release-identity.json"
PACKAGED_SCENARIOS_DIR = DWARF_ROOT / "scenarios"
PRIMITIVE_REGISTRY_PATH = DWARF_ROOT / "primitives" / "registry.json"
_MAX_CATALOG_NAMES = 100
_SHA256_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_IMMUTABLE_IMAGE_RE = re.compile(r"^.+@(sha256:[0-9a-f]{64})$")
_TRANSFER_SOURCE_INSTALLATION_ID_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
)


def _configured_target_identity_is_immutable(
    configured: dict,
    observed: dict,
    *,
    operator_registration: dict | None = None,
) -> bool:
    """Bind either a repository manifest or qualified imported config identity."""
    target_image = str(configured.get("image") or "")
    if _IMMUTABLE_IMAGE_RE.fullmatch(target_image):
        return True
    if not _SHA256_ID_RE.fullmatch(target_image):
        return False
    if operator_registration is not None:
        config_id = str(operator_registration.get("config_image_id") or "")
        return bool(
            configured.get("target_mode") == "stock"
            and configured.get("operator_registration") == operator_registration
            and target_image == config_id
            and str(observed.get("target_image_id") or "") == config_id
            and str(observed.get("artifact_image_id") or "") == config_id
        )
    source_reference = str(configured.get("source_image_reference") or "")
    source_match = _IMMUTABLE_IMAGE_RE.fullmatch(source_reference)
    manifest_digest = str(configured.get("image_manifest_digest") or "")
    runtime_probe_image = str(configured.get("runtime_probe_image") or "")
    return bool(
        configured.get("target_mode") == "patched"
        and configured.get("image") == configured.get("image_digest")
        and configured.get("image_config_digest") == target_image
        and source_match
        and source_match.group(1) == manifest_digest
        and _SHA256_ID_RE.fullmatch(manifest_digest)
        and _SHA256_ID_RE.fullmatch(
            str(configured.get("qualification_image_id") or "")
        )
        and _SHA256_ID_RE.fullmatch(
            str(configured.get("transfer_manifest_sha256") or "")
        )
        and _TRANSFER_SOURCE_INSTALLATION_ID_RE.fullmatch(
            str(configured.get("transfer_source_installation_id") or "")
        )
        and _IMMUTABLE_IMAGE_RE.fullmatch(runtime_probe_image)
        and runtime_probe_image == str(observed.get("runtime_image") or "")
        and str(observed.get("target_image_id") or "") == target_image
        and str(observed.get("artifact_image_id") or "") == target_image
    )


def _validated_operator_stock_registration(
    substrate: dict,
) -> tuple[bool, dict | None]:
    """Re-read one exact local overlay before trusting a bare stock config ID."""
    registration_id = substrate.get("operator_registration_id")
    overlay_revision = substrate.get("operator_overlay_revision")
    catalog = substrate.get("catalog_snapshot")
    registered_nodes = [
        node
        for node in substrate.get("nodes") or []
        if isinstance(node, dict)
        and isinstance(node.get("operator_registration"), dict)
    ]
    present = bool(
        registration_id is not None
        or overlay_revision is not None
        or registered_nodes
    )
    if not present:
        return False, None
    if (
        not isinstance(registration_id, str)
        or not registration_id
        or not isinstance(overlay_revision, str)
        or _SHA256_ID_RE.fullmatch(overlay_revision) is None
        or not isinstance(catalog, dict)
        or catalog.get("operator_registration_id") != registration_id
        or catalog.get("operator_overlay_revision") != overlay_revision
        or not registered_nodes
    ):
        return True, None
    try:
        from profile_manager.deployment_versions import (
            DeploymentVersionGateError,
            operator_registration_release,
        )
        from profile_manager.version_discovery import load_operator_registration

        selected = load_operator_registration(registration_id)
        registration = selected.get("registration")
        if not isinstance(registration, dict):
            return True, None
        release = operator_registration_release(registration)
    except (DeploymentVersionGateError, TypeError, ValueError):
        return True, None
    raw_arch = str(os.environ.get("DWARF_HOST_ARCH") or "").casefold()
    host_arch = {"x86_64": "amd64", "aarch64": "arm64"}.get(
        raw_arch, raw_arch
    )
    config_id = str(registration.get("config_image_id") or "")
    if (
        release.get("implementation") != "amaru"
        or registration.get("runtime_interface") != "extracted-binary"
        or host_arch not in {"amd64", "arm64"}
        or registration.get("platform") != f"linux/{host_arch}"
        or any(
            node.get("operator_registration") != registration
            or node.get("target_mode") != "stock"
            or node.get("impl") != release.get("implementation")
            or node.get("version") != release.get("version")
            or node.get("image") != config_id
            or node.get("amaru_runtime_interface")
            != registration.get("runtime_interface")
            for node in registered_nodes
        )
    ):
        return True, None
    return True, registration


def _release_identity() -> tuple[dict, str, bool, str | None]:
    """Read fixed image identity, with an explicitly unattested dev fallback."""
    if RELEASE_IDENTITY_PATH.is_file():
        try:
            raw = json.loads(RELEASE_IDENTITY_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            raw = {}
        valid_object = isinstance(raw, dict)
        if not valid_object:
            raw = {}
        errors = []
        if type(raw.get("schema_version")) is not int or raw.get("schema_version") != 1:
            errors.append("schema_version")
        if raw.get("component_role") != "dashboard":
            errors.append("component_role")
        for field in ("release_revision", "catalog_revision"):
            value = raw.get(field)
            if not isinstance(value, str) or not GIT_REVISION_RE.fullmatch(value):
                errors.append(field)
        digest = raw.get("catalog_digest")
        if not isinstance(digest, str) or not CATALOG_DIGEST_RE.fullmatch(digest):
            errors.append("catalog_digest")
        for field in ("packaged_scenario_count", "packaged_primitive_count"):
            value = raw.get(field)
            if type(value) is not int or value < 0:
                errors.append(field)
        identity = {
            "component_role": raw.get("component_role")
            if isinstance(raw.get("component_role"), str)
            else "unknown",
            "release_revision": raw.get("release_revision")
            if isinstance(raw.get("release_revision"), str)
            else "unknown",
            "catalog_revision": raw.get("catalog_revision")
            if isinstance(raw.get("catalog_revision"), str)
            else "unknown",
            "catalog_digest": digest if isinstance(digest, str) else None,
            "packaged_scenario_count": raw.get("packaged_scenario_count")
            if type(raw.get("packaged_scenario_count")) is int
            else None,
            "packaged_primitive_count": raw.get("packaged_primitive_count")
            if type(raw.get("packaged_primitive_count")) is int
            else None,
        }
        attested = valid_object and not errors
        reason = (
            None
            if attested
            else "packaged release identity artifact is invalid"
            + (f": {', '.join(errors)}" if valid_object and errors else "")
        )
        return identity, "packaged_artifact", attested, reason

    identity = {
        "component_role": os.environ.get("DWARF_COMPONENT_ROLE", "").strip()
        or "unknown",
        "release_revision": os.environ.get("DWARF_SOURCE_REVISION", "").strip()
        or "unknown",
        "catalog_revision": os.environ.get("DWARF_CATALOG_REVISION", "").strip()
        or "unknown",
        "catalog_digest": None,
        "packaged_scenario_count": None,
        "packaged_primitive_count": None,
    }
    return (
        identity,
        "development_environment_fallback",
        False,
        "release identity artifact is missing; environment fallback is unattested",
    )


def _bounded_names(names: set[str]) -> tuple[list[str], bool]:
    ordered = sorted(names)
    return ordered[:_MAX_CATALOG_NAMES], len(ordered) > _MAX_CATALOG_NAMES


def _catalog_runtime(identity: dict, identity_source: str) -> dict:
    active_dir_value = os.environ.get("ADA2_DWARF_SCENARIOS_DIR", "").strip()
    active_dir = Path(active_dir_value) if active_dir_value else PACKAGED_SCENARIOS_DIR
    errors = []
    try:
        packaged = catalog_snapshot(PACKAGED_SCENARIOS_DIR, PRIMITIVE_REGISTRY_PATH)
    except CatalogIdentityError:
        packaged = {
            "catalog_digest": None,
            "scenario_count": 0,
            "primitive_count": 0,
            "scenario_names": [],
        }
        errors.append("packaged catalog could not be read")
    same_catalog_path = active_dir.resolve() == PACKAGED_SCENARIOS_DIR.resolve()
    if same_catalog_path:
        active = packaged
    else:
        try:
            active = catalog_snapshot(active_dir, PRIMITIVE_REGISTRY_PATH)
        except CatalogIdentityError:
            active = {
                "catalog_digest": None,
                "scenario_count": 0,
                "primitive_count": packaged["primitive_count"],
                "scenario_names": [],
            }
            errors.append("active catalog could not be read")

    packaged_names = set(packaged["scenario_names"])
    active_names = set(active["scenario_names"])
    extensions = active_names - packaged_names
    missing = packaged_names - active_names
    modified = set()
    names_to_compare = set() if same_catalog_path else packaged_names & active_names
    for name in names_to_compare:
        try:
            if (PACKAGED_SCENARIOS_DIR / name).read_bytes() != (active_dir / name).read_bytes():
                modified.add(name)
        except OSError:
            modified.add(name)

    artifact_digest = identity.get("catalog_digest")
    artifact_digest_matches = (
        artifact_digest == packaged["catalog_digest"]
        if artifact_digest is not None and packaged["catalog_digest"] is not None
        else None
    )
    artifact_counts_match = (
        identity.get("packaged_scenario_count") == packaged["scenario_count"]
        and identity.get("packaged_primitive_count") == packaged["primitive_count"]
        if identity_source == "packaged_artifact"
        else None
    )
    if identity_source == "packaged_artifact":
        if artifact_digest_matches is not True:
            errors.append("packaged catalog digest does not match release identity")
        if artifact_counts_match is not True:
            errors.append("packaged catalog counts do not match release identity")
    if missing:
        errors.append("active catalog is missing packaged scenarios")
    if modified:
        errors.append("active catalog modifies packaged scenarios")

    if errors:
        status = "drift"
    elif extensions:
        status = "extended"
    else:
        status = "aligned"
    extension_names, extension_names_truncated = _bounded_names(extensions)
    missing_names, missing_names_truncated = _bounded_names(missing)
    modified_names, modified_names_truncated = _bounded_names(modified)
    return {
        "status": status,
        "status_reason": "; ".join(errors) if errors else None,
        "packaged_digest": packaged["catalog_digest"],
        "artifact_digest": artifact_digest,
        "artifact_digest_matches": artifact_digest_matches,
        "active_digest": active["catalog_digest"],
        "scenario_count": active["scenario_count"],
        "packaged_scenario_count": packaged["scenario_count"],
        "primitive_count": packaged["primitive_count"],
        "packaged_primitive_count": packaged["primitive_count"],
        "artifact_counts_match": artifact_counts_match,
        "runtime_extension_count": len(extensions),
        "runtime_extension_names": extension_names,
        "runtime_extension_names_truncated": extension_names_truncated,
        "missing_packaged_count": len(missing),
        "missing_packaged_names": missing_names,
        "missing_packaged_names_truncated": missing_names_truncated,
        "modified_packaged_count": len(modified),
        "modified_packaged_names": modified_names,
        "modified_packaged_names_truncated": modified_names_truncated,
        "active_scenarios_source": "environment" if active_dir_value else "packaged",
    }


def _storage_roots() -> dict:
    resolved = storage_roots()
    return {
        name: {
            "path": str(getattr(resolved, name)),
            "configured": True,
            "resolution": resolved.sources[name],
            "status": "configured",
        }
        for name in ("runs", "state", "bundles")
    }


def runtime_identity() -> dict:
    """Return the dashboard's safe release, catalog, and storage identity."""
    from profile_manager.data.status import deployed_source_summary

    source = deployed_source_summary()
    identity, identity_source, attested, identity_problem = _release_identity()
    release_revision = identity["release_revision"]
    catalog_revision = identity["catalog_revision"]
    revisions_known = (
        bool(GIT_REVISION_RE.fullmatch(release_revision))
        and bool(GIT_REVISION_RE.fullmatch(catalog_revision))
    )
    matches_release = (
        catalog_revision == release_revision if revisions_known else None
    )
    catalog = _catalog_runtime(identity, identity_source)
    if not attested:
        readiness = {
            "ready": False,
            "state": "unknown",
            "reason": identity_problem or "release identity is unattested",
        }
    elif not matches_release:
        readiness = {
            "ready": False,
            "state": "drift",
            "reason": "catalog revision does not match release revision",
        }
    elif catalog["status"] == "drift":
        readiness = {
            "ready": False,
            "state": "drift",
            "reason": catalog["status_reason"] or "packaged catalog drift detected",
        }
    elif catalog["status"] == "extended":
        readiness = {
            "ready": True,
            "state": "ready",
            "reason": (
                "attested identity; packaged base aligned with "
                f"{catalog['runtime_extension_count']} runtime extensions"
            ),
        }
    else:
        readiness = {
            "ready": True,
            "state": "ready",
            "reason": "attested identity and packaged catalog are aligned",
        }
    return {
        "component_role": identity["component_role"],
        "release_revision": release_revision,
        "release_revision_known": bool(GIT_REVISION_RE.fullmatch(release_revision)),
        "identity_source": identity_source,
        "attested": attested,
        "readiness": readiness,
        "catalog": {
            **catalog,
            "revision": catalog_revision,
            "repository": source["repository"],
            "matches_release": matches_release,
        },
        "storage_roots": _storage_roots(),
    }


def _local_ipv4_addresses() -> set[str]:
    """Return every IPv4 address bound on a local interface, plus loopback.

    Used by _is_local_host to decide whether the dashboard process can reach
    its target via direct subprocess exec instead of SSH-loopback.
    """
    addresses: set[str] = {"127.0.0.1"}
    try:
        addresses.add(socket.gethostbyname(socket.gethostname()))
    except (OSError, socket.gaierror):
        pass
    try:
        result = subprocess.run(
            ["ip", "-4", "addr", "show"],
            text=True, capture_output=True, timeout=2, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return addresses
    for match in re.finditer(r"\binet (\d+\.\d+\.\d+\.\d+)", result.stdout or ""):
        addresses.add(match.group(1))
    return addresses


def _is_local_host(host: str) -> bool:
    """True iff `host` resolves to an IP bound on this machine.

    The deployed dashboard can run on the same host as the configured target;
    SSH-to-itself works in principle but
    requires loopback authorized_keys and a path-portable ssh_key_path.
    Direct exec is simpler and matches the operator's mental model: the
    dashboard inspects the substrate it lives on. The macOS workstation
    case still uses the SSH path because config.host won't resolve local.
    """
    if not host:
        return False
    if host in {"localhost", "127.0.0.1"}:
        return True
    locals_ = _local_ipv4_addresses()
    if host in locals_:
        return True
    try:
        return socket.gethostbyname(host) in locals_
    except (OSError, socket.gaierror):
        return False


def _local_command(remote_command: str, *, timeout: int | None = None) -> CommandResult:
    """Run a shell command locally via bash -c and return CommandResult."""
    try:
        completed = subprocess.run(
            ["bash", "-c", remote_command],
            text=True, capture_output=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        # exc.stdout/exc.stderr are bytes when capture_output=True even with
        # text=True; coerce defensively before string concatenation.
        def _decode(value):
            if value is None:
                return ""
            if isinstance(value, bytes):
                return value.decode("utf-8", errors="replace")
            return str(value)
        return CommandResult(
            returncode=124,
            stdout=_decode(exc.stdout),
            stderr=_decode(exc.stderr) + f"\n[timed out after {timeout}s]",
            rendered_command=f"bash -c <inspect-health command, {len(remote_command)} chars>",
        )
    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        rendered_command=f"bash -c <inspect-health command, {len(remote_command)} chars>",
    )


def _latest_profile_health():
    from profile_manager.dashboard import PROJECT_ROOT

    root = PROJECT_ROOT / "agent" / "testing" / "devnet-profiles" / "profile-a-haskell-peersharing-disabled"
    files = _latest_files(root, ["*-inspect-health.md"], count=1)
    if not files:
        return None, ""
    return files[0], _read_text(files[0], limit=200000)


def _extract_health_value(body, key):
    prefix = f"{key}="
    value = "unknown"
    for line in body.splitlines():
        if line.startswith(prefix):
            value = line.split("=", 1)[1].strip()
    return value


def _extract_tip_json(body):
    marker = "## tip"
    if marker not in body:
        return {}
    after = body.rsplit(marker, 1)[1]
    start = after.find("{")
    end = after.find("}\n")
    if start == -1 or end == -1:
        return {}
    try:
        return json.loads(after[start : end + 1])
    except json.JSONDecodeError:
        return {}


def _health_from_body(body, evidence_path=None, returncode=0, stderr=""):
    from profile_manager.dashboard import PROJECT_ROOT

    tip = _extract_tip_json(body)
    return {
        "returncode": returncode,
        "stderr": stderr,
        "stdout": body,
        "evidence_path": str(evidence_path.relative_to(PROJECT_ROOT)) if evidence_path else None,
        "parsed": {
            "cardano_node_processes": _extract_health_value(body, "cardano_node_processes"),
            "socket_count": _extract_health_value(body, "socket_count"),
            "listener_count": _extract_health_value(body, "listener_count"),
            "loopback_only": _extract_health_value(body, "loopback_only"),
            "tip_block": tip.get("block", "unknown"),
            "tip_slot": tip.get("slot", "unknown"),
            "sync_progress": tip.get("syncProgress", "unknown"),
        },
    }


def _topology_result_from_body(body):
    """Return an exact Cardano/Amaru probe result from command output."""
    for raw in reversed((body or "").splitlines()):
        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        if (
            isinstance(value, dict)
            and value.get("topology_id") == "cardano_amaru"
            and value.get("state")
            and isinstance(value.get("observation"), dict)
        ):
            return value
    return None


def persisted_deployment_from_body(body):
    """Parse the bounded deployment-plan sentinel from a health probe."""
    prefix = "DWARF_PERSISTED_DEPLOYMENT="
    for raw in reversed((body or "").splitlines()):
        if not raw.startswith(prefix):
            continue
        try:
            value = json.loads(raw[len(prefix) :])
        except (TypeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None
    return None


def controller_inventory(*, timeout: int = 5) -> dict:
    """Read the authenticated controller inventory without exposing its token."""
    base_url = (os.environ.get("DWARF_CONTROLLER_URL") or "").rstrip("/")
    token = os.environ.get("DWARF_CONTROLLER_TOKEN") or ""
    expected_installation_id = os.environ.get("DWARF_INSTALLATION_ID") or ""
    if not base_url or len(token) < 32 or not expected_installation_id:
        return {
            "available": False,
            "managed": [],
            "unmanaged": [],
            "error": "controller inventory is not configured",
        }
    body = json.dumps(
        {
            "schema_version": "v1",
            "request_id": str(uuid.uuid4()),
            "operation": "inventory.get",
            "timeout_seconds": min(10, max(1, int(timeout))),
        }
    ).encode("utf-8")
    request = Request(
        base_url + "/v1/requests",
        data=body,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read())
    except (HTTPError, URLError, OSError, ValueError, TypeError):
        return {
            "available": False,
            "managed": [],
            "unmanaged": [],
            "error": "controller inventory request failed",
        }
    if not isinstance(payload, dict):
        return {
            "available": False,
            "managed": [],
            "unmanaged": [],
            "error": "controller inventory response is invalid",
        }
    response_installation_id = payload.get("installation_id")
    if (
        isinstance(response_installation_id, str)
        and response_installation_id
        and response_installation_id != expected_installation_id
    ):
        return {
            "available": False,
            "managed": [],
            "unmanaged": [],
            "error": "controller inventory installation does not match",
        }
    managed = payload.get("managed")
    unmanaged = payload.get("unmanaged")
    owned_resources = payload.get("owned_resources")
    managed_count = payload.get("managed_count")
    owned_resource_count = payload.get("owned_resource_count")
    unmanaged_count = payload.get("unmanaged_count")
    if (
        not isinstance(managed, list)
        or not isinstance(unmanaged, list)
        or not isinstance(owned_resources, list)
    ):
        return {
            "available": False,
            "managed": [],
            "unmanaged": [],
            "error": "controller inventory response is invalid",
        }
    owned_resources_valid = all(
        isinstance(row, dict)
        and all(
            isinstance(row.get(field), str) and bool(row.get(field))
            for field in ("id", "name", "topology_id")
        )
        and row.get("component") in {"node", "support", "network", "volume"}
        and row.get("ownership") == "managed"
        and row.get("mutation_allowed") is True
        for row in owned_resources
    )
    valid = (
        isinstance(payload.get("installation_id"), str)
        and bool(payload.get("installation_id"))
        and all(isinstance(row, dict) for row in managed)
        and owned_resources_valid
        and all(isinstance(row, dict) for row in unmanaged)
        and type(managed_count) is int
        and managed_count == len(managed)
        and type(owned_resource_count) is int
        and owned_resource_count == len(owned_resources)
        and type(unmanaged_count) is int
        and unmanaged_count >= len(unmanaged)
    )
    if not valid:
        return {
            "available": False,
            "managed": [],
            "unmanaged": [],
            "error": "controller inventory response is invalid",
        }
    return {
        **payload,
        "available": True,
        "managed": [row for row in managed if isinstance(row, dict)],
        "owned_resources": [
            row for row in owned_resources if isinstance(row, dict)
        ],
        "unmanaged": [row for row in unmanaged if isinstance(row, dict)],
    }


def controller_topology_health(topology_id: str, *, timeout: int = 120) -> dict:
    """Read one typed semantic health sample from the local controller."""

    base_url = (os.environ.get("DWARF_CONTROLLER_URL") or "").rstrip("/")
    token = os.environ.get("DWARF_CONTROLLER_TOKEN") or ""
    if not base_url or len(token) < 32:
        return {"available": False, "error": "controller health is not configured"}
    operation_timeout = min(120, max(1, int(timeout)))
    body = json.dumps(
        {
            "schema_version": "v1",
            "request_id": str(uuid.uuid4()),
            "operation": "topology.health",
            "timeout_seconds": operation_timeout,
            "topology_id": topology_id,
        }
    ).encode("utf-8")
    request = Request(
        base_url + "/v1/requests",
        data=body,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        # The controller may use its entire bounded operation window before
        # serializing the terminal receipt.  Keep the HTTP transport alive
        # long enough to receive that result instead of misclassifying a
        # successful semantic check as unavailable at the same deadline.
        with urlopen(request, timeout=operation_timeout + 5) as response:
            payload = json.loads(response.read())
    except (HTTPError, URLError, OSError, ValueError, TypeError):
        return {"available": False, "error": "controller health request failed"}
    if (
        not isinstance(payload, dict)
        or payload.get("topology_id") != topology_id
        or payload.get("state") not in {"healthy", "unhealthy", "unknown", "absent"}
        or not isinstance(payload.get("nodes"), list)
    ):
        return {"available": False, "error": "controller health response is invalid"}
    return {**payload, "available": True}


def _controller_profile_health(profile_id: str) -> dict | None:
    """Translate controller semantic evidence into the existing bounded-sample model."""

    if not (os.environ.get("DWARF_CONTROLLER_URL") and os.environ.get("DWARF_CONTROLLER_TOKEN")):
        return None
    sample = controller_topology_health(profile_id)
    if not sample.get("available"):
        return {
            "enabled": False,
            "profile_id": profile_id,
            "error": sample.get("error") or "controller health is unavailable",
            "health": _health_from_body(""),
            "persisted_deployment": None,
        }
    nodes = [node for node in sample.get("nodes") or [] if isinstance(node, dict)]
    if any(node.get("implementation") != "cardano-node" for node in nodes):
        persisted = sample.get("persisted_deployment")
        substrate = persisted.get("substrate") if isinstance(persisted, dict) else None
        configured_nodes = substrate.get("nodes") if isinstance(substrate, dict) else None
        retained = sample.get("retained_evidence")
        configured_by_id = {
            str(node.get("id") or ""): node
            for node in (configured_nodes or [])
            if isinstance(node, dict) and node.get("id")
        }
        operator_registration_present, operator_registration = (
            _validated_operator_stock_registration(substrate or {})
        )
        observed_by_id = {
            str(node.get("id") or ""): node
            for node in nodes
            if node.get("id")
        }
        retained_valid = bool(
            isinstance(retained, dict)
            and retained.get("profile_id") == profile_id
            and all(
                isinstance(retained.get(field), str)
                and _SHA256_ID_RE.fullmatch(str(retained[field]))
                for field in (
                    "deployment_config_sha256",
                    "compose_sha256",
                    "semantic_report_sha256",
                )
            )
        )
        contract_valid = bool(
            isinstance(persisted, dict)
            and persisted.get("profile_id") == profile_id
            and isinstance(substrate, dict)
            and substrate.get("profile_id") == profile_id
            and configured_by_id
            and set(configured_by_id) == set(observed_by_id)
            and sample.get("expected_nodes") == len(configured_by_id)
            and retained_valid
            and (
                not operator_registration_present
                or operator_registration is not None
            )
        )
        observed_nodes = []
        if contract_valid:
            for node_id, configured in configured_by_id.items():
                observed = observed_by_id[node_id]
                target_image = str(configured.get("image") or "")
                target_identity_valid = _configured_target_identity_is_immutable(
                    configured,
                    observed,
                    operator_registration=(
                        operator_registration
                        if configured.get("operator_registration") is not None
                        else None
                    ),
                )
                runtime_image = str(observed.get("runtime_image") or "")
                runtime_match = _IMMUTABLE_IMAGE_RE.fullmatch(runtime_image)
                target_image_id = str(observed.get("target_image_id") or "")
                artifact_image_id = str(observed.get("artifact_image_id") or "")
                runtime_image_id = str(observed.get("runtime_image_id") or "")
                runtime_container_image_id = str(
                    observed.get("runtime_container_image_id") or ""
                )
                expected = {
                    "implementation": str(
                        configured.get("implementation") or configured.get("impl") or ""
                    ),
                    "version": str(configured.get("version") or ""),
                    "image": target_image,
                }
                contract_valid = bool(
                    contract_valid
                    and target_identity_valid
                    and runtime_match
                    and str(observed.get("implementation") or "")
                    == expected["implementation"]
                    and str(observed.get("version") or "") == expected["version"]
                    and str(observed.get("image") or "") == expected["image"]
                    and _SHA256_ID_RE.fullmatch(target_image_id)
                    and artifact_image_id == target_image_id
                    and _SHA256_ID_RE.fullmatch(runtime_image_id)
                    and runtime_container_image_id == runtime_image_id
                    and observed.get("wrapper_matched") is True
                    and isinstance(observed.get("container_name"), str)
                    and bool(observed.get("container_name"))
                )
                observed_nodes.append(
                    {
                        "node_id": node_id,
                        **expected,
                        "runtime_image": runtime_image,
                        "state": str(observed.get("container_state") or "unknown"),
                        "container_name": str(observed.get("container_name") or ""),
                    }
                )
        if not contract_valid:
            return {
                "enabled": False,
                "profile_id": profile_id,
                "error": "controller Amaru identity contract is invalid",
                "health": _health_from_body(""),
                "persisted_deployment": None,
                "topology_result": {
                    "state": "unknown",
                    "reason_code": "controller_identity_contract_invalid",
                    "detail": "The retained controller plan, target provenance, or runtime wrapper identity did not match.",
                    "checked_at": sample.get("checked_at"),
                },
            }
        semantic_health = sample.get("semantic_health")
        completed_at = (
            semantic_health.get("completed_at")
            if isinstance(semantic_health, dict)
            else None
        )
        topology_result = {
            "state": sample.get("state") or "unknown",
            "reason_code": sample.get("reason_code")
            or "controller_health_not_ready",
            "detail": sample.get("detail"),
            "checked_at": completed_at or sample.get("checked_at"),
            "controller_health": sample,
        }
        return {
            "enabled": True,
            "profile_id": profile_id,
            "expected_nodes": sample.get("expected_nodes", len(nodes)),
            "transport": "controller",
            "health": {
                "returncode": 0,
                "stdout": "",
                "stderr": "",
                "evidence_path": None,
                "parsed": {},
            },
            "topology_result": topology_result,
            "persisted_deployment": persisted,
            "observed_nodes": observed_nodes,
        }
    running = [node for node in nodes if node.get("container_state") == "running"]
    listeners = [node for node in nodes if node.get("listener_ready") is True]
    sockets = [node for node in nodes if node.get("chain_ready") is True]
    tips: list[dict] = []
    for node in nodes:
        tip = node.get("tip")
        if isinstance(tip, dict):
            tips.append(tip)
    slots = [int(tip["slot"]) for tip in tips if str(tip.get("slot", "")).isdigit()]
    blocks = [int(tip["block"]) for tip in tips if str(tip.get("block", "")).isdigit()]
    sync_values = [
        tip.get("syncProgress")
        for tip in tips
        if tip.get("syncProgress") is not None
    ]
    try:
        minimum_sync = min(sync_values, key=lambda value: float(str(value)))
    except (TypeError, ValueError):
        minimum_sync = "unknown"
    parsed = {
        "cardano_node_processes": len(running),
        "socket_count": len(sockets),
        "listener_count": len(listeners),
        "loopback_only": "true" if nodes and len(listeners) == len(nodes) else "false",
        "tip_block": min(blocks) if blocks else "unknown",
        "tip_slot": min(slots) if slots else "unknown",
        "sync_progress": minimum_sync if sync_values else "unknown",
    }
    result = {
        "enabled": True,
        "profile_id": profile_id,
        "expected_nodes": sample.get("expected_nodes", len(nodes)),
        "transport": "controller",
        "health": {
            "returncode": 0,
            "stdout": "",
            "stderr": "",
            "evidence_path": None,
            "parsed": parsed,
        },
        "controller_health": sample,
        "persisted_deployment": sample.get("persisted_deployment"),
    }
    if sample.get("state") != "healthy":
        result["topology_result"] = {
            "state": sample.get("state") or "unknown",
            "reason_code": sample.get("reason_code") or "controller_health_not_ready",
            "detail": sample.get("detail") or "Controller semantic health is not ready.",
            "controller_health": sample,
        }
    return result


def _amaru_control_health_command(cfg, profile):
    dwarf_root = str(cfg.remote_dwarf_root or "").strip()
    if not dwarf_root:
        return "false # remote_dwarf_root is required for exact topology health"
    probe = f"{dwarf_root.rstrip('/')}/scripts/check_cardano_amaru_topology.py"
    output = f"{profile.remote_runtime_root}/evidence/dashboard-health-latest.json"
    return " ".join([
        f"cd {shlex.quote(dwarf_root)} &&",
        "PYTHONPATH=. python3",
        shlex.quote(probe),
        "--topology cardano_amaru",
        f"--project {shlex.quote(profile.compose_project)}",
        "--sample-seconds 10",
        f"--output {shlex.quote(output)}",
    ])


def _live_health(profile_id=None):
    if profile_id is not None:
        controller_health = _controller_profile_health(profile_id)
        if controller_health is not None:
            return controller_health
    if not config_exists():
        return {
            "enabled": False,
            "error": "config missing",
            "health": _health_from_body(""),
        }
    cfg = load_config()
    transport = "local" if _is_local_host(cfg.host) else "ssh"
    from profile_manager.remote import control_shim_enabled
    if profile_id is None:
        from profile_manager.profiles import active_profile_command
        active_command = active_profile_command()
        if transport == "local":
            active = _local_command(active_command, timeout=30)
        else:
            active = ssh_command(
                cfg, active_command, timeout=30,
                verb=("active",) if control_shim_enabled() else None,
            )
        if active.returncode != 0:
            return {
                "enabled": False, "state": "unknown",
                "error": (active.stderr or "active profile discovery failed").strip(),
                "health": _health_from_body(""),
            }
        ids = active_profile_ids(active.stdout)
        if not ids:
            return {
                "enabled": False, "state": "no_active",
                "error": "no active managed profile", "health": _health_from_body(""),
            }
        if len(ids) != 1:
            return {
                "enabled": False, "state": "unknown",
                "error": "multiple active managed profiles: " + ", ".join(ids),
                "active_profile_ids": ids, "health": _health_from_body(""),
            }
        profile_id = ids[0]
    profile = next((item for item in load_profiles() if item.id == profile_id), None)
    if not profile:
        return {
            "enabled": False,
            "error": f"profile not found: {profile_id}",
            "health": _health_from_body(""),
        }
    from profile_manager.profiles import (
        deployment_adapter_for_profile,
        health_with_persisted_deployment_command,
    )
    exact_topology = deployment_adapter_for_profile(profile) == "amaru-control"
    command = (
        _amaru_control_health_command(cfg, profile)
        if exact_topology and not control_shim_enabled()
        else inspect_health_command(profile.remote_runtime_root)
    )
    command = health_with_persisted_deployment_command(
        command, profile.remote_runtime_root
    )
    if transport == "local":
        result = _local_command(command, timeout=30)
        health = _health_from_body(result.stdout, returncode=result.returncode, stderr=result.stderr)
    elif control_shim_enabled():
        result = ssh_command(
            cfg, command, timeout=30, verb=("profile-health", profile.id)
        )
        health = _health_from_body(
            result.stdout, returncode=result.returncode, stderr=result.stderr
        )
    else:
        result = ssh_command(cfg, command, timeout=30)
        health = _health_from_body(result.stdout, returncode=result.returncode, stderr=result.stderr)
    response = {
        "enabled": True,
        "profile_id": profile.id,
        "runtime_root": profile.remote_runtime_root,
        "transport": transport,
        "expected_nodes": profile.node_count + profile.amaru_node_count,
        "health": health,
        "persisted_deployment": persisted_deployment_from_body(result.stdout),
    }
    if exact_topology:
        topology_result = _topology_result_from_body(result.stdout)
        if topology_result is not None:
            response["topology_result"] = topology_result
        else:
            response["health"] = _health_from_body(
                result.stdout,
                returncode=result.returncode or 1,
                stderr=result.stderr or "The exact topology probe returned invalid JSON.",
            )
    return response


def _count_active_nodes(stdout: str) -> int:
    """Count devnet node lines the `active` verb emits (DWARF_NODE per node)."""
    return sum(1 for line in (stdout or "").splitlines()
               if line.strip().startswith("DWARF_NODE"))


def active_profile_ids(stdout: str) -> list[str]:
    """Return distinct managed profile ids from the restricted active listing."""
    found = set()
    for raw in (stdout or "").splitlines():
        parts = raw.strip().split()
        if len(parts) < 3 or parts[0] != "DWARF_NODE":
            continue
        candidate = parts[2]
        if candidate.startswith("dwarf-profile-"):
            candidate = candidate.removeprefix("dwarf-")
        if candidate.startswith("profile-"):
            found.add(candidate)
    return sorted(found)
