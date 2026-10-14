"""Plan-first topology actions for the dashboard/controller boundary."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from profile_manager.config import storage_roots


_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,126}[a-z0-9]$|^[a-z0-9]$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_AUDIT_RECEIPT = re.compile(
    r"^controller/controller-audit/audit-[a-z0-9][a-z0-9._-]{0,126}\.json$"
)
_ACTIONS = frozenset({"deploy", "repair", "redeploy", "remove"})
_DESTRUCTIVE = frozenset({"redeploy", "remove"})
_MUTATION_LOCK = threading.Lock()


class TopologyActionError(RuntimeError):
    """A requested topology action is unsafe, stale, or invalid."""

    def __init__(
        self,
        message: str,
        *,
        execution_id: str | None = None,
        audit_receipt: str | None = None,
        terminal: bool = False,
        failure: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.execution_id = execution_id
        self.audit_receipt = audit_receipt
        self.terminal = terminal
        self.failure = dict(failure or {})


_FAILURE_IDENTITY = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_FAILURE_FRAME = re.compile(
    r"^(?:dwarf|tests)/[A-Za-z0-9_./-]{1,240}:\d+:[A-Za-z0-9_<>]{1,128}$"
)


def _validated_controller_failure(value: Any) -> dict[str, Any]:
    """Accept only the controller's bounded structured failure contract."""

    if not isinstance(value, dict):
        return {}
    failure: dict[str, Any] = {}
    for key in ("type", "code", "stage"):
        item = value.get(key)
        if isinstance(item, str) and _FAILURE_IDENTITY.fullmatch(item):
            failure[key] = item
    message = value.get("message")
    if isinstance(message, str) and len(message) <= 256:
        failure["message"] = message
    for key, lower, upper in (
        ("return_code", -255, 255),
        ("errno", 0, 4096),
        ("http_status", 100, 599),
    ):
        item = value.get(key)
        if isinstance(item, int) and lower <= item <= upper:
            failure[key] = item
    trace = value.get("trace")
    if isinstance(trace, list):
        checked = [
            item
            for item in trace[:8]
            if isinstance(item, str) and _FAILURE_FRAME.fullmatch(item)
        ]
        if checked:
            failure["trace"] = checked
    output = value.get("adapter_output")
    if isinstance(output, dict):
        checked_output: dict[str, list[str]] = {}
        for stream in ("stdout", "stderr"):
            items = output.get(stream)
            if isinstance(items, list):
                checked_output[stream] = [
                    item
                    for item in items[:8]
                    if isinstance(item, str) and len(item) <= 256
                ]
        if any(checked_output.values()):
            failure["adapter_output"] = checked_output
    return failure


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _preview_binding_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Bind consent to topology identity and policy, not moving probe samples."""
    health = snapshot.get("health")
    health = health if isinstance(health, dict) else {}
    return {
        "schema_version": snapshot.get("schema_version"),
        "topology_id": snapshot.get("topology_id"),
        "profile_id": snapshot.get("profile_id"),
        "state": snapshot.get("state"),
        "reason_code": snapshot.get("reason_code"),
        "desired": snapshot.get("desired"),
        "persisted": snapshot.get("persisted"),
        "observed": snapshot.get("observed"),
        "unmanaged_visibility": snapshot.get("unmanaged_visibility"),
        "reconciliation": snapshot.get("reconciliation"),
        "actions": snapshot.get("actions"),
        "health": {
            "state": health.get("state"),
            "reason_code": health.get("reason_code"),
            "fresh": health.get("fresh"),
        },
    }


def _preview_binding_digest(snapshot: dict[str, Any]) -> str:
    return _digest(_preview_binding_snapshot(snapshot))


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_canonical(value) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def controller_request(
    operation: str,
    parameters: dict[str, Any],
    timeout_seconds: int,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Send one typed authenticated request to the local controller."""
    base_url = (os.environ.get("DWARF_CONTROLLER_URL") or "").rstrip("/")
    token = os.environ.get("DWARF_CONTROLLER_TOKEN") or ""
    if not base_url or len(token) < 32:
        raise TopologyActionError("controller is not configured")
    body: dict[str, Any] = {
        "schema_version": "v1",
        "request_id": str(uuid.uuid4()),
        "operation": operation,
        "timeout_seconds": timeout_seconds,
        **parameters,
    }
    if idempotency_key is not None:
        body["idempotency_key"] = idempotency_key
    request = Request(
        base_url + "/v1/requests",
        data=_canonical(body),
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds + 5) as response:
            payload = json.loads(response.read())
    except HTTPError as exc:
        try:
            failure = json.loads(exc.read())
        except (ValueError, AttributeError):
            failure = {}
        detail = failure.get("error") if isinstance(failure, dict) else None
        execution_id = failure.get("execution_id") if isinstance(failure, dict) else None
        audit_receipt = failure.get("audit_receipt") if isinstance(failure, dict) else None
        structured_failure = _validated_controller_failure(
            failure.get("failure") if isinstance(failure, dict) else None
        )
        terminal = bool(
            isinstance(execution_id, str)
            and _IDENTIFIER.fullmatch(execution_id)
            and isinstance(audit_receipt, str)
            and _AUDIT_RECEIPT.fullmatch(audit_receipt)
        )
        raise TopologyActionError(
            str(detail or "controller rejected the request"),
            execution_id=execution_id if terminal else None,
            audit_receipt=audit_receipt if terminal else None,
            terminal=terminal,
            failure=structured_failure,
        ) from exc
    except (URLError, OSError, ValueError, TypeError) as exc:
        raise TopologyActionError("controller request failed") from exc
    if not isinstance(payload, dict):
        raise TopologyActionError("controller response is invalid")
    return payload


class TopologyActionService:
    """Bind dashboard previews to exact controller plans and durable action records."""

    def __init__(
        self,
        request_controller: Callable[..., dict[str, Any]] = controller_request,
        state_dir: str | Path | None = None,
    ) -> None:
        self.request_controller = request_controller
        self.root = Path(state_dir or storage_roots().state) / "topology-actions"

    def _path(self, plan_id: str) -> Path:
        if not isinstance(plan_id, str) or not _IDENTIFIER.fullmatch(plan_id):
            raise TopologyActionError("invalid plan identity")
        return self.root / f"{plan_id}.json"

    def _read(self, plan_id: str) -> dict[str, Any]:
        path = self._path(plan_id)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise TopologyActionError("topology action preview does not exist") from exc
        if not isinstance(value, dict):
            raise TopologyActionError("topology action preview is invalid")
        return value

    def _write_event(self, record: dict[str, Any], state: str, **fields: Any) -> dict[str, Any]:
        now = _now()
        updated = {
            **record,
            **fields,
            "state": state,
            "updated_at": now,
            "events": [*(record.get("events") or []), {"state": state, "at": now}],
        }
        _atomic_json(self._path(str(record["plan_id"])), updated)
        return updated

    def preview_binding_reusable(
        self,
        plan_id: str,
        plan_digest: str,
        execution_id: str,
        *,
        action: str,
        topology_id: str,
        snapshot: dict[str, Any],
    ) -> bool:
        """Keep only a live/reconcilable preview bound to a later Prepare."""

        if (
            not isinstance(plan_digest, str)
            or not _DIGEST.fullmatch(plan_digest)
            or not isinstance(execution_id, str)
            or not _IDENTIFIER.fullmatch(execution_id)
        ):
            return False
        record = self._read(plan_id)
        if (
            record.get("plan_digest") != plan_digest
            or record.get("execution_id") != execution_id
            or record.get("action") != action
            or record.get("topology_id") != topology_id
            or record.get("snapshot_sha256") != _preview_binding_digest(snapshot)
        ):
            return False
        state = record.get("state")
        if state in {"planned", "uncertain", "running", "cancelling"}:
            return True
        if state in {"failed", "cancelled", "applied", "timed_out", "interrupted"}:
            return False
        raise TopologyActionError("topology action preview state cannot be reconciled")

    @staticmethod
    def _controller_topology(snapshot: dict[str, Any]) -> dict[str, Any]:
        desired = snapshot.get("desired")
        if not isinstance(desired, dict):
            raise TopologyActionError("snapshot has no desired topology")
        nodes = desired.get("nodes")
        if not isinstance(nodes, list) or not nodes:
            raise TopologyActionError("desired topology has no nodes")
        topology = {
            "topology_id": desired.get("topology_id"),
            "network_magic": desired.get("network_magic"),
            "nodes": [
                {name: node.get(name) for name in ("id", "implementation", "version", "image")}
                for node in nodes
                if isinstance(node, dict)
            ],
        }
        if desired.get("catalog_revision"):
            topology["catalog_revision"] = desired["catalog_revision"]
        if desired.get("operator_registration_id"):
            topology["operator_registration_id"] = desired[
                "operator_registration_id"
            ]
            topology["operator_overlay_revision"] = desired.get(
                "operator_overlay_revision"
            )
        return topology

    def preview(self, action: str, snapshot: dict[str, Any]) -> dict[str, Any]:
        if action not in _ACTIONS:
            raise TopologyActionError("unsupported topology action")
        policy = (snapshot.get("actions") or {}).get(action)
        if not isinstance(policy, dict) or policy.get("allowed") is not True:
            raise TopologyActionError(f"{action} is not allowed by the current topology snapshot")
        topology_id = snapshot.get("topology_id")
        if not isinstance(topology_id, str) or not _IDENTIFIER.fullmatch(topology_id):
            raise TopologyActionError("snapshot topology identity is invalid")
        desired_nodes = {
            str(node.get("id"))
            for node in ((snapshot.get("desired") or {}).get("nodes") or [])
            if isinstance(node, dict)
        }
        unmanaged_nodes = {
            str(row.get("node_id") or row.get("id"))
            for row in ((snapshot.get("observed") or {}).get("unmanaged") or [])
            if isinstance(row, dict)
        }
        if desired_nodes & unmanaged_nodes:
            raise TopologyActionError("action would target an unmanaged resource")
        parameters: dict[str, Any] = {"action": action}
        if action == "remove":
            parameters["topology_id"] = topology_id
        else:
            parameters["topology"] = self._controller_topology(snapshot)
        plan = self.request_controller("topology.plan", parameters, 30)
        if (
            plan.get("status") != "planned"
            or plan.get("action") != action
            or plan.get("topology_id") != topology_id
            or not isinstance(plan.get("plan_id"), str)
            or not _IDENTIFIER.fullmatch(plan["plan_id"])
            or not isinstance(plan.get("plan_digest"), str)
            or not _DIGEST.fullmatch(plan["plan_digest"])
        ):
            raise TopologyActionError("controller returned an invalid topology plan")
        now = _now()
        execution_id = "execution-" + uuid.uuid4().hex
        record = {
            "schema_version": "v1",
            "plan_id": plan["plan_id"],
            "plan_digest": plan["plan_digest"],
            "action": action,
            "topology_id": topology_id,
            "snapshot_sha256": _preview_binding_digest(snapshot),
            "execution_id": execution_id,
            "state": "planned",
            "created_at": now,
            "updated_at": now,
            "events": [{"state": "planned", "at": now}],
        }
        _atomic_json(self._path(plan["plan_id"]), record)
        return {
            **plan,
            "execution_id": execution_id,
            "confirmation_required": action in _DESTRUCTIVE,
        }

    def apply(
        self,
        plan_id: str,
        plan_digest: str,
        *,
        confirmation: str | None = None,
        expected_execution_id: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        try:
            record = self._read(plan_id)
        except TopologyActionError as exc:
            terminal = bool(
                isinstance(expected_execution_id, str)
                and _IDENTIFIER.fullmatch(expected_execution_id)
            )
            raise TopologyActionError(
                str(exc),
                execution_id=expected_execution_id if terminal else None,
                terminal=terminal,
            ) from exc
        execution_id = str(record.get("execution_id") or ("execution-" + uuid.uuid4().hex))

        def reject(message: str) -> None:
            raise TopologyActionError(
                message,
                execution_id=execution_id,
                terminal=True,
            )

        if expected_execution_id is not None and expected_execution_id != execution_id:
            reject("execution identity does not match the retained preview")
        if record.get("plan_digest") != plan_digest or not _DIGEST.fullmatch(str(plan_digest)):
            reject("plan identity does not match the retained preview")
        if record.get("state") not in {"planned", "uncertain", "running", "cancelling"}:
            reject(f"topology plan is already {record.get('state', 'consumed')}")
        action = str(record["action"])
        topology_id = str(record["topology_id"])
        if action in _DESTRUCTIVE and confirmation != f"{action.upper()} {topology_id}":
            reject("exact destructive confirmation is required")
        if not _MUTATION_LOCK.acquire(blocking=False):
            reject("another topology mutation is already in progress")
        idempotency_key = str(
            record.get("idempotency_key") or f"{action}-{topology_id}-{plan_id}"
        )
        worker: threading.Thread | None = None
        try:
            record = self._write_event(
                record,
                "running",
                execution_id=execution_id,
                idempotency_key=idempotency_key,
            )
            result_box: dict[str, dict[str, Any]] = {}
            error_box: list[BaseException] = []
            finished = threading.Event()

            def invoke_controller() -> None:
                try:
                    result_box["result"] = self.request_controller(
                        "topology.apply",
                        {
                            "plan_id": plan_id,
                            "plan_digest": plan_digest,
                            "execution_id": execution_id,
                        },
                        2400,
                        idempotency_key,
                    )
                except BaseException as exc:
                    error_box.append(exc)
                finally:
                    finished.set()

            worker = threading.Thread(target=invoke_controller, daemon=True)
            worker.start()
            yield {"stage": "accepted", "execution_id": execution_id, "plan_id": plan_id}
            while not finished.wait(timeout=0.25):
                try:
                    progress = self.request_controller(
                        "execution.status", {"execution_id": execution_id}, 10
                    )
                except TopologyActionError:
                    continue
                state = progress.get("state")
                if progress.get("execution_id") != execution_id or state not in {
                    "running",
                    "cancelling",
                    "applied",
                    "cancelled",
                    "failed",
                    "timed_out",
                    "interrupted",
                }:
                    continue
                yield {
                    "stage": "progress",
                    "execution_id": execution_id,
                    "controller_state": state,
                    "accepted_at": progress.get("accepted_at"),
                    "completed_at": progress.get("completed_at"),
                    "audit_receipt": progress.get("audit_receipt"),
                }
            worker.join()
            if error_box:
                failure = error_box[0]
                if (
                    isinstance(failure, TopologyActionError)
                    and failure.terminal
                    and failure.execution_id == execution_id
                    and isinstance(failure.audit_receipt, str)
                    and _AUDIT_RECEIPT.fullmatch(failure.audit_receipt)
                ):
                    self._write_event(
                        record,
                        "failed",
                        error=str(failure)[:512],
                        controller_audit_receipt=failure.audit_receipt,
                    )
                else:
                    self._write_event(
                        record, "uncertain", error=type(failure).__name__
                    )
                raise failure
            result = result_box["result"]
            if (
                result.get("execution_id") != execution_id
                or result.get("status") not in {"applied", "cancelled"}
                or not isinstance(result.get("audit_receipt"), str)
                or not _AUDIT_RECEIPT.fullmatch(result["audit_receipt"])
            ):
                self._write_event(record, "failed", error="invalid controller result")
                raise TopologyActionError("controller returned an invalid apply result")
            terminal = str(result["status"])
            self._write_event(
                record,
                terminal,
                controller_audit_receipt=result.get("audit_receipt"),
            )
            yield {"stage": "completed", **result}
        finally:
            if worker is not None:
                worker.join()
            _MUTATION_LOCK.release()

    def status(self, execution_id: str) -> dict[str, Any]:
        if not isinstance(execution_id, str) or not _IDENTIFIER.fullmatch(execution_id):
            raise TopologyActionError("invalid execution identity")
        matches = []
        for path in self.root.glob("plan-*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(record, dict) and record.get("execution_id") == execution_id:
                matches.append(record)
        if len(matches) != 1:
            raise TopologyActionError("execution ledger identity is missing or ambiguous")
        record = matches[0]
        result = self.request_controller(
            "execution.status", {"execution_id": execution_id}, 10
        )
        state = result.get("state")
        if (
            result.get("execution_id") != execution_id
            or state
            not in {
                "running",
                "cancelling",
                "applied",
                "cancelled",
                "failed",
                "timed_out",
                "interrupted",
            }
            or (
                state in {"applied", "cancelled"}
                and (
                    not isinstance(result.get("audit_receipt"), str)
                    or not _AUDIT_RECEIPT.fullmatch(result["audit_receipt"])
                )
            )
        ):
            raise TopologyActionError("controller returned an invalid execution status")
        self._write_event(
            record,
            str(state),
            controller_audit_receipt=result.get("audit_receipt"),
        )
        return result

    def cancel(self, execution_id: str, reason: str) -> dict[str, Any]:
        if not isinstance(execution_id, str) or not _IDENTIFIER.fullmatch(execution_id):
            raise TopologyActionError("invalid execution identity")
        if not isinstance(reason, str) or not reason or len(reason) > 256:
            raise TopologyActionError("cancellation reason is required")
        return self.request_controller(
            "execution.cancel",
            {"execution_id": execution_id, "reason": reason},
            30,
            "cancel-" + execution_id,
        )
