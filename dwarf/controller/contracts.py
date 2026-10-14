"""Strict wire contracts for the local DWARF controller.

This module deliberately contains validation only.  It cannot execute commands,
open the container socket, or mutate the host.  A later adapter may implement
these finite operations, but it must consume these validated value objects.
"""

from __future__ import annotations

import hmac
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Any, Mapping


class ContractError(ValueError):
    """A request failed the controller's public security contract."""


class Operation(str, Enum):
    INVENTORY_GET = "inventory.get"
    IMAGE_INSPECT = "image.inspect"
    ARTIFACT_PACKAGE = "artifact.package"
    TOPOLOGY_HEALTH = "topology.health"
    TOPOLOGY_PLAN = "topology.plan"
    TOPOLOGY_APPLY = "topology.apply"
    TOPOLOGY_SAVE = "topology.save"
    RUN_EXECUTE = "run.execute"
    EXECUTION_CANCEL = "execution.cancel"
    EXECUTION_STATUS = "execution.status"
    LOGS_STREAM = "logs.stream"


_TOP_LEVEL = {
    "schema_version",
    "request_id",
    "operation",
    "timeout_seconds",
    "idempotency_key",
}
_PARAMETERS: dict[Operation, frozenset[str]] = {
    Operation.INVENTORY_GET: frozenset(),
    Operation.IMAGE_INSPECT: frozenset(
        {"implementation", "version", "image", "declared_source_revision"}
    ),
    Operation.ARTIFACT_PACKAGE: frozenset(
        {
            "import_id",
            "execution_id",
            "implementation",
            "version",
            "platform",
            "upload_sha256",
            "executable_sha256",
            "executable_size",
            "declared_source_revision",
        }
    ),
    Operation.TOPOLOGY_HEALTH: frozenset({"topology_id"}),
    Operation.TOPOLOGY_PLAN: frozenset({"action", "topology", "topology_id"}),
    Operation.TOPOLOGY_APPLY: frozenset({"plan_id", "plan_digest", "execution_id"}),
    Operation.TOPOLOGY_SAVE: frozenset({"topology_id", "file_name"}),
    Operation.RUN_EXECUTE: frozenset(
        {"launch_id", "plan_digest", "execution_id"}
    ),
    Operation.EXECUTION_CANCEL: frozenset({"execution_id", "reason"}),
    Operation.EXECUTION_STATUS: frozenset({"execution_id"}),
    Operation.LOGS_STREAM: frozenset(
        {"resource_kind", "resource_id", "cursor", "max_lines", "follow_seconds"}
    ),
}
_TIMEOUT_CEILINGS = {
    Operation.INVENTORY_GET: 10,
    Operation.IMAGE_INSPECT: 20,
    Operation.ARTIFACT_PACKAGE: 1800,
    # The native Amaru verifier takes two fresh semantic samples and is
    # independently bounded at 120 seconds in DockerRuntime.  Expose the same
    # finite ceiling on the wire instead of promising an impossible 30-second
    # terminal response.
    Operation.TOPOLOGY_HEALTH: 120,
    Operation.TOPOLOGY_PLAN: 30,
    # Existing evidence-preserving redeploys allow 2,100 seconds.  Keep a
    # finite ceiling with a five-minute cleanup margin instead of silently
    # making today's verified lifecycle impossible through the controller.
    Operation.TOPOLOGY_APPLY: 2400,
    Operation.TOPOLOGY_SAVE: 30,
    Operation.RUN_EXECUTE: 2400,
    Operation.EXECUTION_CANCEL: 30,
    Operation.EXECUTION_STATUS: 10,
    Operation.LOGS_STREAM: 30,
}
_MUTATIONS = {
    Operation.ARTIFACT_PACKAGE,
    Operation.TOPOLOGY_APPLY,
    Operation.TOPOLOGY_SAVE,
    Operation.RUN_EXECUTE,
    Operation.EXECUTION_CANCEL,
}
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,126}[a-z0-9]$|^[a-z0-9]$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
_IMAGE = re.compile(
    r"^[a-z0-9][a-z0-9._/-]*(?::[A-Za-z0-9._-]+)?@sha256:[0-9a-f]{64}$"
)
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_CATALOG_REVISION = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_REVISION = re.compile(r"^[0-9a-f]{40}$")
_IMAGE_LOOKUP = re.compile(
    r"^[a-z0-9][a-z0-9._-]*(?::[0-9]{1,5})?"
    r"(?:/[a-z0-9][a-z0-9._-]*)*"
    r"(?::[A-Za-z0-9_][A-Za-z0-9._-]{0,127})?"
    r"(?:@sha256:[0-9a-f]{64})?$"
)


@dataclass(frozen=True)
class ControllerRequest:
    schema_version: str
    request_id: str
    operation: Operation
    timeout_seconds: int
    idempotency_key: str | None
    parameters: Mapping[str, Any]


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{field} must be an object")
    return value


def _strict(mapping: Mapping[str, Any], allowed: set[str] | frozenset[str], field: str) -> None:
    unknown = sorted(set(mapping) - set(allowed))
    if unknown:
        raise ContractError(f"{field} has unknown fields: {', '.join(unknown)}")


def _text(value: Any, field: str, *, maximum: int = 128) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ContractError(f"{field} must be non-empty text up to {maximum} characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ContractError(f"{field} contains control characters")
    return value


def _identifier(value: Any, field: str) -> str:
    text = _text(value, field)
    if not _IDENTIFIER.fullmatch(text):
        raise ContractError(f"{field} is not a safe identifier")
    return text


def _digest(value: Any, field: str) -> str:
    text = _text(value, field)
    if not _DIGEST.fullmatch(text):
        raise ContractError(f"{field} must be a sha256 digest")
    return text


def _bounded_int(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ContractError(f"{field} must be an integer from {minimum} to {maximum}")
    return value


def _validate_topology(value: Any) -> dict[str, Any]:
    topology = _mapping(value, "topology")
    _strict(
        topology,
        {
            "topology_id",
            "network_magic",
            "catalog_revision",
            "operator_registration_id",
            "operator_overlay_revision",
            "nodes",
        },
        "topology",
    )
    topology_id = _identifier(topology.get("topology_id"), "topology.topology_id")
    network_magic = _bounded_int(
        topology.get("network_magic"), "topology.network_magic", 0, 2**32 - 1
    )
    nodes = topology.get("nodes")
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 50:
        raise ContractError("topology.nodes must contain between 1 and 50 nodes")
    normalized_nodes: list[dict[str, str]] = []
    node_ids: set[str] = set()
    for index, raw_node in enumerate(nodes):
        field = f"topology.nodes[{index}]"
        node = _mapping(raw_node, field)
        _strict(node, {"id", "implementation", "version", "image"}, field)
        node_id = _identifier(node.get("id"), f"{field}.id")
        if node_id in node_ids:
            raise ContractError(f"{field}.id must be unique")
        node_ids.add(node_id)
        implementation = node.get("implementation")
        if implementation not in {"amaru", "cardano-node"}:
            raise ContractError(f"{field}.implementation is unsupported")
        version = _text(node.get("version"), f"{field}.version", maximum=64)
        if not _VERSION.fullmatch(version):
            raise ContractError(f"{field}.version is invalid")
        image = _text(node.get("image"), f"{field}.image", maximum=512)
        if not (_IMAGE.fullmatch(image) or _DIGEST.fullmatch(image)):
            raise ContractError(f"{field}.image must be digest-pinned")
        normalized_nodes.append(
            {
                "id": node_id,
                "implementation": implementation,
                "version": version,
                "image": image,
            }
        )
    normalized = {
        "topology_id": topology_id,
        "network_magic": network_magic,
        "nodes": tuple(normalized_nodes),
    }
    if "catalog_revision" in topology:
        catalog_revision = _text(
            topology.get("catalog_revision"),
            "topology.catalog_revision",
            maximum=64,
        )
        if not _CATALOG_REVISION.fullmatch(catalog_revision):
            raise ContractError(
                "topology.catalog_revision must be a 64-character sha256 hex value"
            )
        normalized["catalog_revision"] = catalog_revision
    has_operator_registration = "operator_registration_id" in topology
    has_operator_revision = "operator_overlay_revision" in topology
    if has_operator_registration != has_operator_revision:
        raise ContractError(
            "topology.operator_registration_id and "
            "topology.operator_overlay_revision must be provided together"
        )
    if has_operator_registration:
        normalized["operator_registration_id"] = _identifier(
            topology["operator_registration_id"],
            "topology.operator_registration_id",
        )
        normalized["operator_overlay_revision"] = _digest(
            topology["operator_overlay_revision"],
            "topology.operator_overlay_revision",
        )
    return normalized


def _validate_parameters(operation: Operation, raw: Mapping[str, Any]) -> dict[str, Any]:
    if operation is Operation.INVENTORY_GET:
        return {}
    if operation is Operation.IMAGE_INSPECT:
        implementation = raw.get("implementation")
        if implementation not in {"amaru", "cardano-node"}:
            raise ContractError("implementation is unsupported")
        version = _text(raw.get("version"), "version", maximum=64)
        if not _VERSION.fullmatch(version):
            raise ContractError("version is invalid")
        image = _text(raw.get("image"), "image", maximum=512)
        if (
            image.startswith("-")
            or "://" in image
            or ".." in image
            or "//" in image
            or not (_DIGEST.fullmatch(image) or _IMAGE_LOOKUP.fullmatch(image))
        ):
            raise ContractError("image lookup is unsafe")
        result = {
            "implementation": implementation,
            "version": version,
            "image": image,
        }
        source = raw.get("declared_source_revision")
        if source is not None:
            source = _text(source, "declared_source_revision", maximum=64)
            if not _SOURCE_REVISION.fullmatch(source):
                raise ContractError("declared source revision is invalid")
            result["declared_source_revision"] = source
        return result
    if operation is Operation.ARTIFACT_PACKAGE:
        import_id = _text(raw.get("import_id"), "import_id", maximum=31)
        if not re.fullmatch(r"import-[0-9a-f]{24}", import_id):
            raise ContractError("import_id is invalid")
        execution_id = _text(raw.get("execution_id"), "execution_id", maximum=42)
        if not re.fullmatch(r"execution-[0-9a-f]{32}", execution_id):
            raise ContractError("execution_id is invalid")
        implementation = raw.get("implementation")
        if implementation not in {"amaru", "cardano-node"}:
            raise ContractError("implementation is unsupported")
        version = _text(raw.get("version"), "version", maximum=64)
        if not _VERSION.fullmatch(version):
            raise ContractError("version is invalid")
        platform = raw.get("platform")
        if platform not in {"linux/amd64", "linux/arm64"}:
            raise ContractError("platform is unsupported")
        result = {
            "import_id": import_id,
            "execution_id": execution_id,
            "implementation": implementation,
            "version": version,
            "platform": platform,
            "upload_sha256": _digest(raw.get("upload_sha256"), "upload_sha256"),
            "executable_sha256": _digest(
                raw.get("executable_sha256"), "executable_sha256"
            ),
            "executable_size": _bounded_int(
                raw.get("executable_size"),
                "executable_size",
                1,
                256 * 1024 * 1024,
            ),
        }
        source = raw.get("declared_source_revision")
        if source is not None:
            source = _text(source, "declared_source_revision", maximum=40)
            if not _SOURCE_REVISION.fullmatch(source):
                raise ContractError("declared source revision is invalid")
            result["declared_source_revision"] = source
        return result
    if operation is Operation.TOPOLOGY_HEALTH:
        return {"topology_id": _identifier(raw.get("topology_id"), "topology_id")}
    if operation is Operation.TOPOLOGY_PLAN:
        action = raw.get("action")
        if action not in {"deploy", "repair", "redeploy", "remove"}:
            raise ContractError("action must be deploy, repair, redeploy, or remove")
        if action == "remove":
            if "topology" in raw or "topology_id" not in raw:
                raise ContractError("remove requires topology_id and forbids topology")
            return {"action": action, "topology_id": _identifier(raw["topology_id"], "topology_id")}
        if "topology_id" in raw or "topology" not in raw:
            raise ContractError(f"{action} requires topology and forbids topology_id")
        return {"action": action, "topology": _validate_topology(raw["topology"])}
    if operation is Operation.TOPOLOGY_APPLY:
        result = {
            "plan_id": _identifier(raw.get("plan_id"), "plan_id"),
            "plan_digest": _digest(raw.get("plan_digest"), "plan_digest"),
        }
        if raw.get("execution_id") is not None:
            result["execution_id"] = _identifier(raw["execution_id"], "execution_id")
        return result
    if operation is Operation.TOPOLOGY_SAVE:
        topology_id = _identifier(raw.get("topology_id"), "topology_id")
        file_name = _text(raw.get("file_name"), "file_name", maximum=128)
        path = PurePosixPath(file_name)
        if path.name != file_name or path.is_absolute() or path.suffix not in {".yaml", ".yml", ".json"}:
            raise ContractError("file_name must be a safe YAML or JSON basename")
        return {"topology_id": topology_id, "file_name": file_name}
    if operation is Operation.RUN_EXECUTE:
        launch_id = _identifier(raw.get("launch_id"), "launch_id")
        if not re.fullmatch(r"launch-[0-9a-f]{24}", launch_id):
            raise ContractError("launch_id is not a canonical launch identifier")
        return {
            "launch_id": launch_id,
            "plan_digest": _digest(raw.get("plan_digest"), "plan_digest"),
            "execution_id": _identifier(raw.get("execution_id"), "execution_id"),
        }
    if operation is Operation.EXECUTION_CANCEL:
        return {
            "execution_id": _identifier(raw.get("execution_id"), "execution_id"),
            "reason": _text(raw.get("reason"), "reason", maximum=256),
        }
    if operation is Operation.EXECUTION_STATUS:
        return {
            "execution_id": _identifier(raw.get("execution_id"), "execution_id"),
        }
    if operation is Operation.LOGS_STREAM:
        resource_kind = raw.get("resource_kind")
        if resource_kind not in {"controller", "topology", "run"}:
            raise ContractError("resource_kind is unsupported")
        result: dict[str, Any] = {
            "resource_kind": resource_kind,
            "resource_id": _identifier(raw.get("resource_id"), "resource_id"),
            "max_lines": _bounded_int(raw.get("max_lines", 500), "max_lines", 1, 5000),
            "follow_seconds": _bounded_int(
                raw.get("follow_seconds", 0), "follow_seconds", 0, 30
            ),
        }
        if raw.get("cursor") is not None:
            result["cursor"] = _identifier(raw["cursor"], "cursor")
        return result
    raise AssertionError(f"unhandled operation {operation}")


def parse_request(value: Any) -> ControllerRequest:
    """Parse an untrusted request into a finite, immutable controller command."""

    request = _mapping(value, "request")
    raw_operation = request.get("operation")
    try:
        operation = Operation(raw_operation)
    except (TypeError, ValueError) as exc:
        raise ContractError("unsupported operation") from exc
    allowed = _TOP_LEVEL | set(_PARAMETERS[operation])
    _strict(request, allowed, "request")
    if request.get("schema_version") != "v1":
        raise ContractError("schema_version must be v1")
    request_id = _text(request.get("request_id"), "request_id")
    try:
        if str(uuid.UUID(request_id)) != request_id:
            raise ValueError
    except ValueError as exc:
        raise ContractError("request_id must be a canonical UUID") from exc
    timeout = _bounded_int(
        request.get("timeout_seconds"),
        "timeout_seconds",
        1,
        _TIMEOUT_CEILINGS[operation],
    )
    idempotency = request.get("idempotency_key")
    if operation in _MUTATIONS:
        idempotency = _identifier(idempotency, "idempotency_key")
    elif idempotency is not None:
        raise ContractError("idempotency_key is only valid for mutation operations")
    parameters = {name: request[name] for name in _PARAMETERS[operation] if name in request}
    normalized = _validate_parameters(operation, parameters)
    return ControllerRequest(
        schema_version="v1",
        request_id=request_id,
        operation=operation,
        timeout_seconds=timeout,
        idempotency_key=idempotency,
        parameters=MappingProxyType(normalized),
    )


def authenticate_bearer(header: str | None, expected_token: str) -> None:
    """Validate one configured bearer token without exposing mismatch details."""

    if not isinstance(expected_token, str) or len(expected_token) < 32:
        raise ContractError("configured token must contain at least 32 characters")
    supplied = ""
    if isinstance(header, str) and header.startswith("Bearer "):
        supplied = header[7:]
    if not supplied or not hmac.compare_digest(supplied, expected_token):
        raise ContractError("authentication failed")


def _timestamp(value: Any, field: str) -> str:
    text = _text(value, field, maximum=64)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"{field} must be an RFC3339 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ContractError(f"{field} must be UTC")
    return text


def _artifact_path(value: Any) -> str:
    text = _text(value, "artifact_paths entry", maximum=512)
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts or str(path) != text:
        raise ContractError("artifact_paths must be bundle-relative normalized paths")
    return text


@dataclass(frozen=True)
class AuditReceipt:
    schema_version: str
    audit_id: str
    request_id: str
    operation: str
    status: str
    actor: str
    accepted_at: str
    completed_at: str
    request_sha256: str
    plan_sha256: str
    before_sha256: str
    after_sha256: str
    artifact_paths: tuple[str, ...]
    redactions: tuple[str, ...]

    @classmethod
    def from_mapping(cls, value: Any) -> "AuditReceipt":
        raw = _mapping(value, "audit receipt")
        fields = set(cls.__dataclass_fields__)
        _strict(raw, fields, "audit receipt")
        missing = sorted(fields - set(raw))
        if missing:
            raise ContractError(f"audit receipt missing fields: {', '.join(missing)}")
        if raw["schema_version"] != "v1":
            raise ContractError("schema_version must be v1")
        try:
            operation = Operation(raw["operation"])
        except (TypeError, ValueError) as exc:
            raise ContractError("unsupported operation") from exc
        if raw["status"] not in {
            "planned",
            "applied",
            "refused",
            "failed",
            "cancelled",
            "timed_out",
            "interrupted",
        }:
            raise ContractError("status is unsupported")
        artifacts = raw["artifact_paths"]
        redactions = raw["redactions"]
        if not isinstance(artifacts, list) or not artifacts:
            raise ContractError("artifact_paths must be a non-empty list")
        if not isinstance(redactions, list) or "authorization" not in redactions:
            raise ContractError("redactions must include authorization")
        request_id = _text(raw["request_id"], "request_id")
        try:
            normalized_request_id = str(uuid.UUID(request_id))
        except ValueError as exc:
            raise ContractError("request_id must be a UUID") from exc
        if normalized_request_id != request_id:
            raise ContractError("request_id must be a canonical UUID")
        return cls(
            schema_version="v1",
            audit_id=_identifier(raw["audit_id"], "audit_id"),
            request_id=normalized_request_id,
            operation=operation.value,
            status=raw["status"],
            actor=_identifier(raw["actor"], "actor"),
            accepted_at=_timestamp(raw["accepted_at"], "accepted_at"),
            completed_at=_timestamp(raw["completed_at"], "completed_at"),
            request_sha256=_digest(raw["request_sha256"], "request_sha256"),
            plan_sha256=_digest(raw["plan_sha256"], "plan_sha256"),
            before_sha256=_digest(raw["before_sha256"], "before_sha256"),
            after_sha256=_digest(raw["after_sha256"], "after_sha256"),
            artifact_paths=tuple(_artifact_path(item) for item in artifacts),
            redactions=tuple(_identifier(item, "redactions entry") for item in redactions),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "audit_id": self.audit_id,
            "request_id": self.request_id,
            "operation": self.operation,
            "status": self.status,
            "actor": self.actor,
            "accepted_at": self.accepted_at,
            "completed_at": self.completed_at,
            "request_sha256": self.request_sha256,
            "plan_sha256": self.plan_sha256,
            "before_sha256": self.before_sha256,
            "after_sha256": self.after_sha256,
            "artifact_paths": list(self.artifact_paths),
            "redactions": list(self.redactions),
        }
