"""Authenticated local controller service with durable plan and audit state."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import stat
import time
import threading
import traceback
import uuid
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any, Mapping

from .contracts import (
    AuditReceipt,
    ContractError,
    ControllerRequest,
    Operation,
    authenticate_bearer,
    parse_request,
)


class ControllerConflict(RuntimeError):
    """A valid request conflicts with retained controller state."""


class ControllerPlanningFailed(RuntimeError):
    """A read-only planning stage failed with bounded diagnostic evidence."""

    def __init__(self, stage: str, cause: Mapping[str, str]) -> None:
        self.client_conflict = cause.get("message") != "controller runtime failed"
        super().__init__(
            f"controller planning failed during {stage}"
            if self.client_conflict
            else "controller operation failed"
        )
        self.failure = {
            "stage": stage,
            "cause": dict(cause),
        }


class ControllerExecutionFailed(RuntimeError):
    """A mutation failed after an execution and audit record were created."""

    def __init__(
        self,
        message: str,
        *,
        execution_id: str | None = None,
        audit_receipt: str | None = None,
        failure: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.execution_id = execution_id
        self.audit_receipt = audit_receipt
        self.failure = dict(failure or {})


class ControllerExecutionTimedOut(TimeoutError):
    """A mutation timed out after its execution and audit records were finalized."""

    def __init__(
        self,
        message: str,
        *,
        execution_id: str | None = None,
        audit_receipt: str | None = None,
    ) -> None:
        super().__init__(message)
        self.execution_id = execution_id
        self.audit_receipt = audit_receipt


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _thaw(value: Any) -> Any:
    if isinstance(value, MappingProxyType) or isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_thaw(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return _thaw(asdict(value))
    return value


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        _thaw(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _bounded_failure(exc: BaseException, *, secret: str) -> dict[str, Any]:
    """Expose only reviewed DWARF-authored causes; raw runtime text stays private."""

    failure_type = type(exc).__name__[:128]
    message = " ".join(str(exc).split()) or "controller runtime failed"
    structured_safe = {
        (
            "DockerRuntimeError",
            "Amaru control lifecycle requires peer_sharing=false",
            "amaru_peer_sharing_contract_invalid",
            "adapter_preflight",
        ),
        (
            "DockerRuntimeError",
            "Amaru deployment preflight found retained project resources",
            "compose_project_not_fresh",
            "adapter_preflight",
        ),
        (
            "DockerRuntimeError",
            "Amaru deployment preflight found retained runtime assets",
            "runtime_assets_exist",
            "adapter_preflight",
        ),
        (
            "DockerRuntimeError",
            "Amaru deployment runtime assets are unavailable",
            "required_runtime_asset_missing",
            "adapter_asset_staging",
        ),
        (
            "DockerRuntimeError",
            "Amaru deployment runtime assets are not writable",
            "runtime_asset_permission_denied",
            "adapter_asset_staging",
        ),
    }
    code = str(getattr(exc, "failure_code", "") or "")
    stage = str(getattr(exc, "failure_stage", "") or "")
    artifact_failures = {
        ("staged_artifact_invalid", "artifact_copy"): "staged artifact identity changed",
        ("staged_artifact_expired", "artifact_copy"): "staged artifact expired",
        ("staged_artifact_unavailable", "artifact_copy"): "staged artifact is unavailable",
        ("artifact_elf_invalid", "artifact_copy"): "uploaded artifact is not a supported executable",
        ("artifact_platform_mismatch", "artifact_copy"): "uploaded artifact platform does not match this installation",
        ("artifact_base_unavailable", "artifact_package"): "standard packaging base is unavailable",
        ("artifact_base_platform_mismatch", "artifact_package"): "standard packaging base platform does not match this installation",
        ("artifact_build_failed", "artifact_package"): "local artifact image packaging failed",
        ("artifact_interface_invalid", "artifact_probe"): "packaged artifact interface identity is invalid",
        ("artifact_probe_failed", "artifact_probe"): "packaged artifact executable smoke failed",
        ("artifact_probe_output_exceeded", "artifact_probe"): "packaged artifact probe output exceeded its safety limit",
        ("artifact_interface_probe_failed", "artifact_probe"): "packaged artifact does not expose the required run interface",
        ("artifact_cleanup_failed", "artifact_cleanup"): "artifact package cleanup could not be verified",
    }
    artifact_message = artifact_failures.get((code, stage))
    if failure_type == "DockerRuntimeError" and artifact_message is not None:
        return {
            "type": failure_type,
            "message": artifact_message,
            "code": code,
            "stage": stage,
        }
    if (failure_type, message, code, stage) in structured_safe:
        return {
            "type": failure_type,
            "message": message,
            "code": code,
            "stage": stage,
        }
    safe_messages = frozenset(
        {
            "configured topology assets are unavailable",
            "managed topology runtime metadata is unavailable",
            "only single-machine Docker topology adapters are supported",
            "operator-registered image differs from the selected identity",
            "operator-registered image identity or platform differs",
            "operator-registered image provenance is invalid",
            "qualified local target image is unavailable",
            "requested catalog resolution is blocked",
            "requested catalog revision differs from the controller catalog",
            "requested catalog revision is invalid",
            "requested identity differs from the catalog-resolved topology",
            "retained topology differs from the current catalog plan",
            "scenario runner returned no canonical run identity",
            "target is not an approved golden image",
            "topology is not a catalog-owned profile",
        }
    )
    if failure_type == "DockerRuntimeError" and message in safe_messages:
        return {"type": failure_type, "message": message}
    return {"type": failure_type, "message": "controller runtime failed"}


def _trusted_trace(exc: BaseException) -> list[str]:
    """Return bounded repository-relative frame identities without source or locals."""

    repository = Path(__file__).resolve().parents[2]
    frames: list[str] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(seen) < 4:
        seen.add(id(current))
        for frame in traceback.extract_tb(current.__traceback__):
            try:
                relative = Path(frame.filename).resolve().relative_to(repository)
            except (OSError, ValueError):
                continue
            if not relative.parts or relative.parts[0] not in {"dwarf", "tests"}:
                continue
            identity = f"{relative.as_posix()}:{int(frame.lineno)}:{frame.name}"
            if identity not in frames:
                frames.append(identity)
        current = current.__cause__ or current.__context__
    return frames[-8:]


def _safe_run_output_line(value: str) -> str:
    line = " ".join(value.replace("\x00", " ").split())[:256]
    lowered = line.lower()
    if "exec format error" in lowered:
        return "adapter executable format is invalid"
    if "bad interpreter" in lowered or "cannot execute" in lowered:
        return "adapter executable could not start"
    if any(
        marker in lowered
        for marker in (
            "error while loading shared libraries",
            "dynamic linker",
            "loader",
        )
    ):
        return "adapter runtime loader or shared library is unavailable"
    if "permission denied" in lowered:
        return "adapter process permission denied"
    if "operation not permitted" in lowered:
        return "adapter process operation is not permitted"
    if "no such file" in lowered or "not found" in lowered:
        return "adapter executable or dependency is unavailable"
    return "[redacted]"


def _run_failure(exc: BaseException, *, secret: str) -> dict[str, Any]:
    """Normalize one Run exception into the single durable safe failure contract."""

    failure = _bounded_failure(exc, secret=secret)
    failure["code"] = str(
        getattr(exc, "failure_code", "") or "run_execution_failed"
    )[:128]
    failure["stage"] = str(
        getattr(exc, "failure_stage", "") or "run_launch"
    )[:128]

    return_code = getattr(exc, "return_code", None)
    if isinstance(return_code, int) and -255 <= return_code <= 255:
        failure["return_code"] = return_code
    adapter_output = getattr(exc, "adapter_output", None)
    if isinstance(adapter_output, Mapping):
        bounded_output: dict[str, list[str]] = {}
        for stream in ("stdout", "stderr"):
            values = adapter_output.get(stream)
            if not isinstance(values, (list, tuple)):
                continue
            bounded_output[stream] = [
                _safe_run_output_line(value)
                for value in values[:8]
                if isinstance(value, str)
            ]
        if any(bounded_output.values()):
            failure["adapter_output"] = bounded_output

    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(seen) < 4:
        seen.add(id(current))
        error_number = getattr(current, "errno", None)
        if isinstance(error_number, int):
            failure.setdefault("errno", error_number)
        if type(current).__name__ == "HTTPError":
            http_status = getattr(current, "code", None)
            if isinstance(http_status, int) and 100 <= http_status <= 599:
                failure.setdefault("http_status", http_status)
        current = current.__cause__ or current.__context__
    trace = _trusted_trace(exc)
    if trace:
        failure["trace"] = trace
    return failure


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = _canonical_bytes(value) + b"\n"
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ControllerConflict(f"retained controller record is unreadable: {path.name}") from exc
    if not isinstance(value, dict):
        raise ControllerConflict(f"retained controller record is invalid: {path.name}")
    return value


class ControllerService:
    """Dispatch typed requests while retaining replay, execution, and audit state."""

    def __init__(
        self,
        token: str,
        state_dir: str | Path,
        runtime: Any,
        release_revision: str,
        installation_id: str,
        expected_peer_revision: str | None = None,
        cancellation_grace_seconds: float = 10.0,
    ) -> None:
        if len(token) < 32:
            raise ValueError("controller token must contain at least 32 characters")
        self.token = token
        self.state_dir = Path(state_dir)
        self.root = self.state_dir / "controller"
        self.runtime = runtime
        self.release_revision = release_revision
        self.installation_id = installation_id
        self.expected_peer_revision = expected_peer_revision or release_revision
        self.cancellation_grace_seconds = max(0.0, float(cancellation_grace_seconds))
        self.plans_dir = self.root / "plans"
        self.idempotency_dir = self.root / "idempotency"
        self.executions_dir = self.root / "executions"
        self.audit_dir = self.root / "controller-audit"
        self.topologies_dir = self.root / "topologies"
        for directory in (
            self.plans_dir,
            self.idempotency_dir,
            self.executions_dir,
            self.audit_dir,
            self.topologies_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = threading.RLock()
        self._mutation_lock = threading.Lock()
        self._cancellations: dict[str, threading.Event] = {}
        self._background_threads: set[threading.Thread] = set()
        reconcile_attachments = getattr(runtime, "reconcile_run_attachments", None)
        if reconcile_attachments is not None:
            reconcile_attachments()
        self._reconcile_interrupted_records()

    @contextmanager
    def _profile_catalog_read_lock(self):
        """Hold the dashboard profile lifecycle lock across plan/apply reads."""
        configured = os.environ.get("ADA2_DWARF_PROFILES_DIR")
        if not configured:
            yield None
            return
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        root_descriptor = descriptor = None
        try:
            root_descriptor = os.open(
                Path(configured),
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow,
            )
            descriptor = os.open(
                ".dwarf-catalog.lock",
                os.O_RDONLY | nofollow,
                dir_fd=root_descriptor,
            )
        except OSError as exc:
            if root_descriptor is not None:
                os.close(root_descriptor)
            raise ControllerConflict("profile catalog lifecycle lock is unavailable") from exc
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise ControllerConflict("profile catalog lifecycle lock is invalid")
            fcntl.flock(descriptor, fcntl.LOCK_SH)
            yield root_descriptor
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
                os.close(root_descriptor)

    @staticmethod
    def _profile_definition_sha256(
        topology_id: str, root_descriptor: int | None
    ) -> str | None:
        configured = os.environ.get("ADA2_DWARF_PROFILES_DIR")
        if not configured:
            return None
        if root_descriptor is None:
            raise ControllerConflict("profile catalog lifecycle lock is unavailable")
        if (
            not topology_id
            or Path(topology_id).name != topology_id
            or topology_id in {".", ".."}
        ):
            raise ControllerConflict("topology profile id is invalid")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        directory_descriptor = file_descriptor = None
        try:
            directory_descriptor = os.open(
                topology_id,
                flags | getattr(os, "O_DIRECTORY", 0),
                dir_fd=root_descriptor,
            )
            file_descriptor = os.open(
                "profile.yaml", flags, dir_fd=directory_descriptor
            )
            opened = os.fstat(file_descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise ControllerConflict("topology profile definition is invalid")
            chunks = []
            while True:
                chunk = os.read(file_descriptor, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
            return "sha256:" + hashlib.sha256(b"".join(chunks)).hexdigest()
        except OSError as exc:
            raise ControllerConflict("topology profile definition is unavailable") from exc
        finally:
            for descriptor in (file_descriptor, directory_descriptor):
                if descriptor is not None:
                    os.close(descriptor)

    def _reconcile_interrupted_records(self) -> None:
        """Fail closed after restart; never silently replay an uncertain mutation."""

        completed_at = _utc_now()
        for path in self.executions_dir.glob("*.json"):
            record = _read_json(path)
            state = record.get("state")
            expected_execution_id = path.stem
            artifact_cleanup_pending = bool(
                record.get("operation") == Operation.ARTIFACT_PACKAGE.value
                and (
                    state in {"running", "cancelling"}
                    or record.get("artifact_cleanup") == "failed"
                )
            )
            artifact_cleanup = record.get("artifact_cleanup")
            if artifact_cleanup_pending:
                cleanup = getattr(self.runtime, "cleanup_artifact_package", None)
                try:
                    if not callable(cleanup):
                        raise RuntimeError("artifact cleanup is unavailable")
                    cleanup(
                        dict(record.get("request_parameters") or {}),
                        expected_execution_id,
                    )
                    artifact_cleanup = "completed"
                except BaseException:
                    artifact_cleanup = "failed"
            identity_invalid = record.get("execution_id") != expected_execution_id
            receipt_missing = state in {"applied", "cancelled"} and not self._valid_receipt(
                record, expected_execution_id=expected_execution_id
            )
            if (
                state in {"running", "cancelling"}
                or identity_invalid
                or receipt_missing
                or artifact_cleanup_pending
            ):
                if identity_invalid:
                    reason = "execution_identity_mismatch"
                elif receipt_missing:
                    reason = "terminal_execution_receipt_missing"
                elif artifact_cleanup == "failed":
                    reason = "artifact_package_cleanup_failed"
                else:
                    reason = "controller_restarted_during_mutation"
                _atomic_json(
                    path,
                    {
                        **record,
                        "state": "interrupted",
                        "completed_at": completed_at,
                        "audit_receipt": None if receipt_missing else record.get("audit_receipt"),
                        "run_id": None if receipt_missing else record.get("run_id"),
                        "result": None if receipt_missing else record.get("result"),
                        "reconciliation_reason": reason,
                        **(
                            {"artifact_cleanup": artifact_cleanup}
                            if artifact_cleanup_pending
                            else {}
                        ),
                    },
                )
        for path in self.idempotency_dir.glob("*.json"):
            record = _read_json(path)
            if record.get("state") == "running":
                _atomic_json(
                    path,
                    {**record, "state": "interrupted", "completed_at": completed_at},
                )

    def _valid_receipt(
        self,
        record: Mapping[str, Any],
        *,
        expected_execution_id: str,
    ) -> bool:
        if record.get("execution_id") != expected_execution_id:
            return False
        operation = record.get("operation")
        parameters = record.get("request_parameters")
        if (
            operation
            not in {
                Operation.ARTIFACT_PACKAGE.value,
                Operation.TOPOLOGY_APPLY.value,
                Operation.RUN_EXECUTE.value,
            }
            or not isinstance(parameters, dict)
            or parameters.get("execution_id") != expected_execution_id
        ):
            return False
        request_sha256 = _sha256({"operation": operation, "parameters": parameters})
        if request_sha256 != record.get("request_sha256"):
            return False
        relative = record.get("audit_receipt")
        if not isinstance(relative, str):
            return False
        parts = Path(relative).parts
        if (
            len(parts) != 3
            or parts[:2] != ("controller", "controller-audit")
            or not parts[2].startswith("audit-")
            or not parts[2].endswith(".json")
        ):
            return False
        try:
            receipt = AuditReceipt.from_mapping(_read_json(self.state_dir / relative))
        except (ControllerConflict, ContractError):
            return False
        expected_plan_sha256 = (
            parameters.get("executable_sha256")
            if operation == Operation.ARTIFACT_PACKAGE.value
            else parameters.get("plan_digest")
        )
        valid = (
            receipt.audit_id == Path(relative).stem
            and receipt.request_id == record.get("request_id")
            and receipt.operation == operation
            and receipt.status == record.get("state")
            and receipt.artifact_paths == (relative,)
            and receipt.accepted_at == record.get("accepted_at")
            and receipt.completed_at == record.get("completed_at")
            and receipt.request_sha256 == request_sha256
            and receipt.plan_sha256 == record.get("plan_sha256")
            and record.get("plan_sha256") == expected_plan_sha256
        )
        if not valid:
            return False
        if operation == Operation.ARTIFACT_PACKAGE.value:
            result = record.get("result")
            return (
                isinstance(result, dict)
                and receipt.after_sha256 == _sha256(result)
                and (
                    (
                        record.get("state") == "applied"
                        and result.get("status") == "packaged"
                    )
                    or (
                        record.get("state") == "cancelled"
                        and result.get("status") == "cancelled"
                    )
                )
            )
        if operation != Operation.RUN_EXECUTE.value:
            return True
        result = record.get("result")
        run_id = record.get("run_id")
        if (
            not isinstance(result, dict)
            or run_id != result.get("run_id")
            or receipt.after_sha256 != _sha256(result)
        ):
            return False
        if record.get("state") == "applied":
            return (
                result.get("status") == "completed"
                and isinstance(run_id, str)
                and bool(run_id)
            )
        return (
            record.get("state") == "cancelled"
            and result.get("status") == "cancelled"
            and run_id is None
        )

    def dispatch(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes,
    ) -> tuple[int, dict[str, Any]]:
        if method == "GET" and path == "/healthz":
            return self._health()
        if method != "POST" or path != "/v1/requests":
            return 404, {"ok": False, "error": "not found"}
        try:
            authenticate_bearer(headers.get("Authorization"), self.token)
        except ContractError:
            return 401, {"ok": False, "error": "authentication failed"}
        raw: Any = None
        try:
            raw = json.loads(body.decode("utf-8"))
            request = parse_request(raw)
            return self._execute(request)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._record_refusal(raw, "invalid JSON")
            return 400, {"ok": False, "error": "invalid JSON"}
        except ContractError as exc:
            self._record_refusal(raw, str(exc))
            return 400, {"ok": False, "error": str(exc)}
        except ControllerPlanningFailed as exc:
            self._record_refusal(raw, str(exc), failure=exc.failure)
            if not exc.client_conflict:
                return 500, {"ok": False, "error": "controller operation failed"}
            return 409, {
                "ok": False,
                "error": str(exc),
                "failure": exc.failure,
            }
        except ControllerConflict as exc:
            self._record_refusal(raw, str(exc))
            return 409, {"ok": False, "error": str(exc)}
        except ControllerExecutionTimedOut as exc:
            return 504, {
                "ok": False,
                "error": str(exc),
                "execution_id": exc.execution_id,
                "audit_receipt": exc.audit_receipt,
            }
        except TimeoutError as exc:
            self._record_refusal(raw, str(exc))
            return 504, {"ok": False, "error": str(exc)}
        except ControllerExecutionFailed as exc:
            return 500, {
                "ok": False,
                "error": str(exc),
                "failure": exc.failure,
                "execution_id": exc.execution_id,
                "audit_receipt": exc.audit_receipt,
            }
        except Exception:
            self._record_refusal(raw, "controller operation failed")
            return 500, {"ok": False, "error": "controller operation failed"}

    def _health(self) -> tuple[int, dict[str, Any]]:
        runtime_health = self.runtime.health()
        writable = os.access(self.root, os.W_OK)
        compatible = self.expected_peer_revision == self.release_revision
        images_ready = runtime_health.get("golden_images", "ok") == "ok"
        status = (
            "ok"
            if writable
            and compatible
            and images_ready
            and runtime_health.get("docker") == "ok"
            and runtime_health.get("compose", "ok") == "ok"
            else "error"
        )
        return (200 if status == "ok" else 503), {
            "status": status,
            "release_revision": self.release_revision,
            "installation_id": self.installation_id,
            "runtime": runtime_health,
            "state_writable": writable,
            "peer_revision": self.expected_peer_revision,
            "revision_compatible": compatible,
        }

    def _execute(self, request: ControllerRequest) -> tuple[int, dict[str, Any]]:
        if request.operation is Operation.INVENTORY_GET:
            return 200, _thaw(self.runtime.inventory())
        if request.operation is Operation.IMAGE_INSPECT:
            inspected = _thaw(
                self.runtime.inspect_image(
                    request.parameters["image"],
                    implementation=request.parameters["implementation"],
                )
            )
            return 200, {
                "schema_version": "v1",
                "implementation": request.parameters["implementation"],
                "version": request.parameters["version"],
                "requested_image": request.parameters["image"],
                **inspected,
                "declared_source_revision": request.parameters.get(
                    "declared_source_revision"
                ),
                "provenance": {
                    "source_revision": "unverified",
                    "public": None,
                    "qualification": "unverified",
                },
                "installation_id": self.installation_id,
                "inspected_at": _utc_now(),
            }
        if request.operation is Operation.ARTIFACT_PACKAGE:
            return self._idempotent(request, self._package_artifact_start)
        if request.operation is Operation.TOPOLOGY_HEALTH:
            return 200, _thaw(
                self.runtime.topology_health(request.parameters["topology_id"])
            )
        if request.operation is Operation.TOPOLOGY_PLAN:
            return 200, self._plan(request)
        if request.operation is Operation.TOPOLOGY_APPLY:
            return self._idempotent(request, self._apply)
        if request.operation is Operation.TOPOLOGY_SAVE:
            return self._idempotent(request, self._save)
        if request.operation is Operation.RUN_EXECUTE:
            return self._idempotent(request, self._run_execute)
        if request.operation is Operation.EXECUTION_CANCEL:
            return self._idempotent(request, self._cancel)
        if request.operation is Operation.EXECUTION_STATUS:
            return 200, self._status(request)
        if request.operation is Operation.LOGS_STREAM:
            return 200, _thaw(self.runtime.logs(**dict(request.parameters)))
        raise AssertionError(f"unhandled operation: {request.operation}")

    def _package_artifact_start(
        self, request: ControllerRequest
    ) -> tuple[int, dict[str, Any]]:
        parameters = dict(request.parameters)
        execution_id = str(parameters["execution_id"])
        execution_path = self.executions_dir / f"{execution_id}.json"
        cancellation = threading.Event()
        accepted_at = _utc_now()
        execution = {
            "schema_version": "v1",
            "execution_id": execution_id,
            "request_id": request.request_id,
            "operation": request.operation.value,
            "request_sha256": self._intent_sha256(request),
            "request_parameters": _thaw(request.parameters),
            "plan_sha256": parameters["executable_sha256"],
            "topology_id": None,
            "state": "running",
            "accepted_at": accepted_at,
            "completed_at": None,
        }
        with self._lock:
            if execution_path.exists():
                raise ControllerConflict("execution id already exists")
            self._cancellations[execution_id] = cancellation
            _atomic_json(execution_path, execution)
            worker = threading.Thread(
                target=self._package_artifact_worker,
                args=(request, execution, cancellation),
                daemon=True,
                name=f"dwarf-{execution_id}",
            )
            self._background_threads.add(worker)
            worker.start()
        return 202, {"status": "accepted", "execution_id": execution_id}

    def _package_artifact_worker(
        self,
        request: ControllerRequest,
        execution: dict[str, Any],
        cancellation: threading.Event,
    ) -> None:
        parameters = dict(request.parameters)
        execution_id = str(parameters["execution_id"])
        execution_path = self.executions_dir / f"{execution_id}.json"
        accepted_at = str(execution["accepted_at"])
        lock_started = time.monotonic()
        acquired = False
        process_lock = None
        try:
            acquired = self._mutation_lock.acquire(timeout=request.timeout_seconds)
            if not acquired:
                raise TimeoutError("controller mutation lock timed out")
            remaining = max(
                1, int(request.timeout_seconds - (time.monotonic() - lock_started))
            )
            process_lock = self._acquire_process_lock(remaining)
            if cancellation.is_set():
                result = {"status": "cancelled"}
            else:
                result = _thaw(
                    self.runtime.package_artifact(
                        parameters,
                        execution_id,
                        timeout=remaining,
                        cancel_event=cancellation,
                    )
                )
            status = "cancelled" if result.get("status") == "cancelled" else "applied"
            if status == "applied" and result.get("status") != "packaged":
                raise RuntimeError("artifact packaging returned an invalid terminal result")
            completed_at = _utc_now()
            receipt_path = self._write_receipt(
                request=request,
                status=status,
                accepted_at=accepted_at,
                completed_at=completed_at,
                plan_sha256=parameters["executable_sha256"],
                before={
                    "import_id": parameters["import_id"],
                    "executable_sha256": parameters["executable_sha256"],
                },
                after=result,
            )
            _atomic_json(
                execution_path,
                {
                    **execution,
                    "state": status,
                    "completed_at": completed_at,
                    "audit_receipt": receipt_path,
                    "result": result,
                },
            )
        except BaseException as exc:
            completed_at = _utc_now()
            failure = _bounded_failure(exc, secret=self.token)
            receipt_path = self._write_receipt(
                request=request,
                status="failed",
                accepted_at=accepted_at,
                completed_at=completed_at,
                plan_sha256=parameters["executable_sha256"],
                before={
                    "import_id": parameters["import_id"],
                    "executable_sha256": parameters["executable_sha256"],
                },
                after={"status": "failed", "failure": failure},
                failure=failure,
            )
            _atomic_json(
                execution_path,
                {
                    **execution,
                    "state": "failed",
                    "completed_at": completed_at,
                    "audit_receipt": receipt_path,
                    "failure": failure,
                },
            )
        finally:
            if process_lock is not None:
                fcntl.flock(process_lock, fcntl.LOCK_UN)
                os.close(process_lock)
            if acquired:
                self._mutation_lock.release()
            with self._lock:
                self._cancellations.pop(execution_id, None)
                self._background_threads.discard(threading.current_thread())

    def _status(self, request: ControllerRequest) -> dict[str, Any]:
        execution_id = str(request.parameters["execution_id"])
        path = self.executions_dir / f"{execution_id}.json"
        if not path.is_file():
            raise ControllerConflict("execution is not managed by this controller")
        record = _read_json(path)
        if record.get("execution_id") != execution_id:
            raise ControllerConflict("retained execution identity does not match its path")
        if record.get("state") in {"applied", "cancelled"} and not self._valid_receipt(
            record, expected_execution_id=execution_id
        ):
            record = {
                **record,
                "state": "interrupted",
                "completed_at": _utc_now(),
                "audit_receipt": None,
                "run_id": None,
                "result": None,
                "reconciliation_reason": "terminal_execution_receipt_missing",
            }
            _atomic_json(path, record)
        return {
            "schema_version": "v1",
            "execution_id": execution_id,
            "topology_id": record.get("topology_id"),
            "state": record.get("state"),
            "accepted_at": record.get("accepted_at"),
            "completed_at": record.get("completed_at"),
            "audit_receipt": record.get("audit_receipt"),
            "run_id": record.get("run_id"),
            "result": record.get("result"),
            "failure": record.get("failure"),
        }

    def _run_resolution_request(
        self, operation, parameters, timeout, idempotency_key=None
    ) -> dict[str, Any]:
        """Serve the one read-only controller receipt lookup used by Run resolution."""
        if (
            operation != "execution.status"
            or idempotency_key is not None
            or not isinstance(parameters, dict)
            or set(parameters) != {"execution_id"}
            or not isinstance(timeout, int)
            or isinstance(timeout, bool)
            or timeout < 1
            or timeout > 30
        ):
            raise ControllerConflict("run resolution request is unsupported")
        return self._status(SimpleNamespace(parameters=dict(parameters)))

    def _run_execute(self, request: ControllerRequest) -> tuple[int, dict[str, Any]]:
        """Run one immutable launch while retaining controller lifecycle evidence."""

        parameters = dict(request.parameters)
        execution_id = str(parameters["execution_id"])
        execution_path = self.executions_dir / f"{execution_id}.json"
        accepted_at = _utc_now()
        cancellation = threading.Event()
        execution = {
            "schema_version": "v1",
            "execution_id": execution_id,
            "request_id": request.request_id,
            "operation": request.operation.value,
            "request_sha256": self._intent_sha256(request),
            "request_parameters": _thaw(request.parameters),
            "plan_sha256": parameters["plan_digest"],
            "topology_id": None,
            "state": "running",
            "accepted_at": accepted_at,
            "completed_at": None,
        }
        with self._lock:
            if execution_path.exists():
                raise ControllerConflict("execution id already exists")
            self._cancellations[execution_id] = cancellation
            _atomic_json(execution_path, execution)

        lock_started = time.monotonic()
        acquired = self._mutation_lock.acquire(timeout=request.timeout_seconds)
        if not acquired:
            completed_at = _utc_now()
            after = {"state": "timed_out", "reason": "mutation_lock_timeout"}
            receipt_path = self._write_receipt(
                request=request,
                status="timed_out",
                accepted_at=accepted_at,
                completed_at=completed_at,
                plan_sha256=parameters["plan_digest"],
                before=execution,
                after=after,
            )
            _atomic_json(
                execution_path,
                {
                    **execution,
                    "state": "timed_out",
                    "completed_at": completed_at,
                    "audit_receipt": receipt_path,
                },
            )
            with self._lock:
                self._cancellations.pop(execution_id, None)
            raise ControllerExecutionTimedOut(
                "controller mutation lock timed out",
                execution_id=execution_id,
                audit_receipt=receipt_path,
            )

        try:
            remaining = max(
                1, int(request.timeout_seconds - (time.monotonic() - lock_started))
            )
            cancelled_during_run = cancellation.is_set()
            if cancelled_during_run:
                result = {
                    "status": "cancelled",
                    "run_id": None,
                    "exit_code": 130,
                }
            else:
                result = _thaw(
                    self.runtime.run_launch(
                        launch_id=parameters["launch_id"],
                        plan_digest=parameters["plan_digest"],
                        execution_id=execution_id,
                        timeout=remaining,
                        cancel_event=cancellation,
                        request_controller=self._run_resolution_request,
                    )
                )
            cancelled_during_run = result.get("status") == "cancelled"
            status = "cancelled" if cancelled_during_run else "applied"
            completed_at = _utc_now()
            receipt_path = self._write_receipt(
                request=request,
                status=status,
                accepted_at=accepted_at,
                completed_at=completed_at,
                plan_sha256=parameters["plan_digest"],
                before=execution,
                after=result,
            )
            terminal = {
                **execution,
                "state": status,
                "completed_at": completed_at,
                "audit_receipt": receipt_path,
                "run_id": result.get("run_id"),
                "result": result,
            }
            _atomic_json(execution_path, terminal)
            return 200, {
                "status": status,
                "execution_id": execution_id,
                "result": result,
                "audit_receipt": receipt_path,
            }
        except Exception as exc:
            completed_at = _utc_now()
            failure = _run_failure(exc, secret=self.token)
            after = {"state": "failed", "failure": failure}
            receipt_path = self._write_receipt(
                request=request,
                status="failed",
                accepted_at=accepted_at,
                completed_at=completed_at,
                plan_sha256=parameters["plan_digest"],
                before=execution,
                after=after,
                failure=failure,
            )
            _atomic_json(
                execution_path,
                {
                    **execution,
                    "state": "failed",
                    "completed_at": completed_at,
                    "audit_receipt": receipt_path,
                    "failure": failure,
                },
            )
            raise ControllerExecutionFailed(
                "controller run failed",
                execution_id=execution_id,
                audit_receipt=receipt_path,
                failure=failure,
            ) from exc
        finally:
            self._mutation_lock.release()
            with self._lock:
                self._cancellations.pop(execution_id, None)

    def _plan(self, request: ControllerRequest) -> dict[str, Any]:
        with self._profile_catalog_read_lock() as profile_root:
            return self._plan_with_profile_lock(request, profile_root)

    def _plan_with_profile_lock(
        self, request: ControllerRequest, profile_root: int | None
    ) -> dict[str, Any]:
        parameters = _thaw(request.parameters)
        action = parameters["action"]
        topology = parameters.get("topology")
        topology_id = (
            topology["topology_id"] if topology is not None else parameters["topology_id"]
        )
        try:
            deployment = (
                _thaw(self.runtime.prepare_topology(topology))
                if topology is not None
                else None
            )
        except ControllerConflict:
            raise
        except Exception as exc:
            failure = _bounded_failure(exc, secret=self.token)
            raise ControllerPlanningFailed("prepare_topology", failure) from exc
        try:
            observed = _thaw(self.runtime.snapshot(topology_id))
        except ControllerConflict:
            raise
        except Exception as exc:
            failure = _bounded_failure(exc, secret=self.token)
            raise ControllerPlanningFailed("snapshot", failure) from exc
        material = {
            "schema_version": "v1",
            "release_revision": self.release_revision,
            "installation_id": self.installation_id,
            "action": action,
            "topology_id": topology_id,
            "profile_definition_sha256": self._profile_definition_sha256(
                topology_id, profile_root
            ),
            "topology": topology,
            "deployment": deployment,
            "observed_before": observed,
            "observed_before_sha256": _sha256(observed),
            "created_at": _utc_now(),
        }
        try:
            material_digest = _sha256(material)
        except Exception as exc:
            failure = _bounded_failure(exc, secret=self.token)
            raise ControllerPlanningFailed("plan_material", failure) from exc
        plan_id = "plan-" + material_digest.removeprefix("sha256:")[:20]
        plan = {**material, "plan_id": plan_id}
        created = datetime.fromisoformat(plan["created_at"].replace("Z", "+00:00"))
        plan["expires_at"] = datetime.fromtimestamp(
            created.timestamp() + 900, timezone.utc
        ).isoformat().replace("+00:00", "Z")
        plan_digest = _sha256(plan)
        try:
            with self._lock:
                _atomic_json(
                    self.plans_dir / f"{plan_id}.json",
                    {"plan": plan, "plan_digest": plan_digest},
                )
        except Exception as exc:
            failure = _bounded_failure(exc, secret=self.token)
            raise ControllerPlanningFailed("plan_persist", failure) from exc
        return {
            "status": "planned",
            "plan_id": plan_id,
            "plan_digest": plan_digest,
            "action": action,
            "topology_id": topology_id,
            "observed_before_sha256": material["observed_before_sha256"],
        }

    def _load_plan(self, plan_id: str, supplied_digest: str) -> dict[str, Any]:
        path = self.plans_dir / f"{plan_id}.json"
        if not path.is_file():
            raise ControllerConflict("plan does not exist")
        envelope = _read_json(path)
        plan = envelope.get("plan")
        retained_digest = envelope.get("plan_digest")
        if not isinstance(plan, dict) or retained_digest != _sha256(plan):
            raise ControllerConflict("retained plan digest is invalid")
        if supplied_digest != retained_digest:
            raise ControllerConflict("plan digest does not match retained plan")
        if plan.get("release_revision") != self.release_revision:
            raise ControllerConflict("plan release revision does not match controller")
        if plan.get("installation_id") != self.installation_id:
            raise ControllerConflict("plan installation does not match controller")
        expires_at = plan.get("expires_at")
        try:
            expiry = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ControllerConflict("retained plan expiry is invalid") from exc
        if datetime.now(timezone.utc) >= expiry:
            raise ControllerConflict("plan has expired")
        return plan

    def _acquire_process_lock(self, timeout: int):
        lock_path = self.root / "mutation.lock"
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return descriptor
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(descriptor)
                    raise TimeoutError("controller mutation lock timed out")
                time.sleep(0.05)

    def _apply(self, request: ControllerRequest) -> tuple[int, dict[str, Any]]:
        with self._profile_catalog_read_lock() as profile_root:
            return self._apply_with_profile_lock(request, profile_root)

    def _apply_with_profile_lock(
        self, request: ControllerRequest, profile_root: int | None
    ) -> tuple[int, dict[str, Any]]:
        parameters = dict(request.parameters)
        plan = self._load_plan(parameters["plan_id"], parameters["plan_digest"])
        if self._profile_definition_sha256(
            plan["topology_id"], profile_root
        ) != plan.get("profile_definition_sha256"):
            raise ControllerConflict("topology profile changed after plan creation")
        before = _thaw(self.runtime.snapshot(plan["topology_id"]))
        if _sha256(before) != plan["observed_before_sha256"]:
            raise ControllerConflict("observed state changed after plan creation")
        execution_id = str(
            parameters.get("execution_id") or ("execution-" + uuid.uuid4().hex)
        )
        cancellation = threading.Event()
        execution_path = self.executions_dir / f"{execution_id}.json"
        accepted_at = _utc_now()
        execution = {
            "schema_version": "v1",
            "execution_id": execution_id,
            "request_id": request.request_id,
            "operation": request.operation.value,
            "request_sha256": self._intent_sha256(request),
            "request_parameters": _thaw(request.parameters),
            "plan_sha256": parameters["plan_digest"],
            "topology_id": plan["topology_id"],
            "state": "running",
            "accepted_at": accepted_at,
            "completed_at": None,
        }
        with self._lock:
            if execution_path.exists():
                raise ControllerConflict("execution id already exists")
            self._cancellations[execution_id] = cancellation
            _atomic_json(execution_path, execution)
        lock_started = time.monotonic()
        acquired = self._mutation_lock.acquire(timeout=request.timeout_seconds)
        if not acquired:
            completed_at = _utc_now()
            after = _thaw(self.runtime.snapshot(plan["topology_id"]))
            receipt_path = self._write_receipt(
                request=request,
                status="timed_out",
                accepted_at=accepted_at,
                completed_at=completed_at,
                plan_sha256=parameters["plan_digest"],
                before=before,
                after=after,
            )
            _atomic_json(
                execution_path,
                {
                    **execution,
                    "state": "timed_out",
                    "completed_at": completed_at,
                    "audit_receipt": receipt_path,
                },
            )
            with self._lock:
                self._cancellations.pop(execution_id, None)
            raise ControllerExecutionTimedOut(
                "controller mutation lock timed out",
                execution_id=execution_id,
                audit_receipt=receipt_path,
            )
        process_lock = None
        try:
            remaining = max(1, int(request.timeout_seconds - (time.monotonic() - lock_started)))
            process_lock = self._acquire_process_lock(remaining)
            result_box: dict[str, Any] = {}
            error_box: list[BaseException] = []

            def run_runtime() -> None:
                try:
                    result_box["result"] = self.runtime.apply(
                        plan, execution_id, cancellation
                    )
                except BaseException as exc:  # retained and re-raised in request thread
                    error_box.append(exc)

            worker = threading.Thread(target=run_runtime, daemon=True)
            worker.start()
            remaining = max(0.0, request.timeout_seconds - (time.monotonic() - lock_started))
            worker.join(timeout=remaining)
            if worker.is_alive():
                cancellation.set()
                cancellation_error: BaseException | None = None
                try:
                    self.runtime.cancel(execution_id)
                except BaseException as exc:
                    cancellation_error = exc
                worker.join(timeout=self.cancellation_grace_seconds)
                if worker.is_alive():
                    _atomic_json(
                        execution_path,
                        {
                            **execution,
                            "state": "cancelling",
                            "cancellation_requested_at": _utc_now(),
                        },
                    )
                    # Safety takes priority over HTTP latency: the process and
                    # in-process locks must remain held until the runtime has
                    # actually stopped mutating Docker state.
                    worker.join()
                if error_box:
                    raise error_box[0]
                timed_result = _thaw(result_box.get("result") or {})
                if not timed_result and cancellation_error is not None:
                    raise cancellation_error
                completed_at = _utc_now()
                after = _thaw(self.runtime.snapshot(plan["topology_id"]))
                if timed_result.get("status") == "applied":
                    receipt_path = self._write_receipt(
                        request=request,
                        status="applied",
                        accepted_at=accepted_at,
                        completed_at=completed_at,
                        plan_sha256=parameters["plan_digest"],
                        before=before,
                        after=after,
                    )
                    _atomic_json(
                        execution_path,
                        {
                            **execution,
                            "state": "applied",
                            "completed_at": completed_at,
                            "audit_receipt": receipt_path,
                        },
                    )
                    return 200, {
                        "status": "applied",
                        "execution_id": execution_id,
                        "result": timed_result,
                        "audit_receipt": receipt_path,
                    }
                receipt_path = self._write_receipt(
                    request=request,
                    status="timed_out",
                    accepted_at=accepted_at,
                    completed_at=completed_at,
                    plan_sha256=parameters["plan_digest"],
                    before=before,
                    after=after,
                )
                _atomic_json(
                    execution_path,
                    {
                        **execution,
                        "state": "timed_out",
                        "completed_at": completed_at,
                        "audit_receipt": receipt_path,
                    },
                )
                raise ControllerExecutionTimedOut(
                    "controller operation timed out and cancellation was requested",
                    execution_id=execution_id,
                    audit_receipt=receipt_path,
                )
            if error_box:
                raise error_box[0]
            result = _thaw(result_box["result"])
            after = _thaw(self.runtime.snapshot(plan["topology_id"]))
            # The runtime owns the final cancellation handshake.  A cancel
            # arriving after its last reversible point is too late and must
            # not rewrite an already-applied mutation as cancelled.
            status = "cancelled" if result.get("status") == "cancelled" else "applied"
            completed_at = _utc_now()
            receipt_path = self._write_receipt(
                request=request,
                status=status,
                accepted_at=accepted_at,
                completed_at=completed_at,
                plan_sha256=parameters["plan_digest"],
                before=before,
                after=after,
            )
            _atomic_json(
                execution_path,
                {
                    **execution,
                    "state": status,
                    "completed_at": completed_at,
                    "audit_receipt": receipt_path,
                },
            )
            return 200, {
                "status": status,
                "execution_id": execution_id,
                "result": result,
                "audit_receipt": receipt_path,
            }
        except TimeoutError as exc:
            retained = _read_json(execution_path)
            receipt_path = retained.get("audit_receipt")
            if retained.get("state") == "running":
                completed_at = _utc_now()
                after = _thaw(self.runtime.snapshot(plan["topology_id"]))
                receipt_path = self._write_receipt(
                    request=request,
                    status="timed_out",
                    accepted_at=accepted_at,
                    completed_at=completed_at,
                    plan_sha256=parameters["plan_digest"],
                    before=before,
                    after=after,
                )
                _atomic_json(
                    execution_path,
                    {
                        **retained,
                        "state": "timed_out",
                        "completed_at": completed_at,
                        "audit_receipt": receipt_path,
                    },
                )
            if isinstance(exc, ControllerExecutionTimedOut):
                raise
            raise ControllerExecutionTimedOut(
                str(exc),
                execution_id=execution_id,
                audit_receipt=receipt_path,
            ) from exc
        except Exception as exc:
            completed_at = _utc_now()
            retained = _read_json(execution_path)
            after = _thaw(self.runtime.snapshot(plan["topology_id"]))
            failure = _bounded_failure(exc, secret=self.token)
            receipt_path = self._write_receipt(
                request=request,
                status="failed",
                accepted_at=accepted_at,
                completed_at=completed_at,
                plan_sha256=parameters["plan_digest"],
                before=before,
                after=after,
                failure=failure,
            )
            if retained.get("state") in {"running", "cancelling"}:
                _atomic_json(
                    execution_path,
                    {
                        **retained,
                        "state": "failed",
                        "completed_at": completed_at,
                        "audit_receipt": receipt_path,
                        "failure": failure,
                    },
                )
            raise ControllerExecutionFailed(
                failure["message"],
                execution_id=execution_id,
                audit_receipt=receipt_path,
                failure=failure,
            ) from exc
        finally:
            if process_lock is not None:
                fcntl.flock(process_lock, fcntl.LOCK_UN)
                os.close(process_lock)
            self._mutation_lock.release()
            with self._lock:
                self._cancellations.pop(execution_id, None)

    def _save(self, request: ControllerRequest) -> tuple[int, dict[str, Any]]:
        from profile_manager.topology_export import attest_exact_configuration

        parameters = dict(request.parameters)
        accepted_at = _utc_now()
        lock_started = time.monotonic()
        acquired = self._mutation_lock.acquire(timeout=request.timeout_seconds)
        if not acquired:
            raise TimeoutError("controller mutation lock timed out")
        process_lock = None
        try:
            remaining = max(1, int(request.timeout_seconds - (time.monotonic() - lock_started)))
            process_lock = self._acquire_process_lock(remaining)
            topology = attest_exact_configuration(
                _thaw(self.runtime.export_topology(parameters["topology_id"])),
                secret=self.token,
                installation_id=self.installation_id,
                release_revision=self.release_revision,
            )
            destination = self.topologies_dir / parameters["file_name"]
            # JSON is a valid YAML subset, so the same deterministic representation
            # is safe for all accepted extensions without another parser dependency.
            _atomic_json(destination, topology)
            completed_at = _utc_now()
            receipt_path = self._write_receipt(
                request=request,
                status="applied",
                accepted_at=accepted_at,
                completed_at=completed_at,
                plan_sha256=_sha256(None),
                before=None,
                after=topology,
            )
            return 200, {
                "status": "applied",
                "file_name": parameters["file_name"],
                "content_sha256": topology.get("content_sha256") or _sha256(topology),
                "configuration": topology,
                "audit_receipt": receipt_path,
            }
        finally:
            if process_lock is not None:
                fcntl.flock(process_lock, fcntl.LOCK_UN)
                os.close(process_lock)
            self._mutation_lock.release()

    def _cancel(self, request: ControllerRequest) -> tuple[int, dict[str, Any]]:
        execution_id = str(request.parameters["execution_id"])
        accepted_at = _utc_now()
        path = self.executions_dir / f"{execution_id}.json"
        if not path.is_file():
            raise ControllerConflict("execution is not managed by this controller")
        with self._lock:
            cancellation = self._cancellations.get(execution_id)
            if cancellation is None:
                record = _read_json(path)
                if record.get("state") == "cancelled":
                    return 200, {"status": "cancelled", "execution_id": execution_id}
                raise ControllerConflict("execution is not currently cancellable")
            record = _read_json(path)
            cancellation_requested_at = _utc_now()
            cancelling = {
                **record,
                "state": "cancelling",
                "cancellation_requested_at": cancellation_requested_at,
            }
            _atomic_json(path, cancelling)
            # Publish the nonterminal state before signalling the worker so a
            # fast cancellation cannot be overwritten back to "cancelling".
            cancellation.set()
            self.runtime.cancel(execution_id)
        completed_at = _utc_now()
        receipt_path = self._write_receipt(
            request=request,
            # The cancel request was applied; the execution itself remains
            # nonterminal until its worker has actually stopped.
            status="applied",
            accepted_at=accepted_at,
            completed_at=completed_at,
            plan_sha256=_sha256(None),
            before=record,
            after=cancelling,
        )
        return 200, {
            "status": "cancelling",
            "execution_id": execution_id,
            "audit_receipt": receipt_path,
        }

    def _intent_sha256(self, request: ControllerRequest) -> str:
        return _sha256(
            {"operation": request.operation.value, "parameters": request.parameters}
        )

    def _idempotent(self, request: ControllerRequest, action: Any) -> tuple[int, dict[str, Any]]:
        assert request.idempotency_key is not None
        path = self.idempotency_dir / f"{request.idempotency_key}.json"
        intent = self._intent_sha256(request)
        with self._lock:
            if path.is_file():
                retained = _read_json(path)
                if retained.get("intent_sha256") != intent:
                    raise ControllerConflict("idempotency key was already used for different input")
                if retained.get("state") == "completed":
                    return int(retained["http_status"]), _thaw(retained["response"])
                raise ControllerConflict(
                    f"idempotent operation is {retained.get('state', 'not safely replayable')}"
                )
            _atomic_json(
                path,
                {
                    "schema_version": "v1",
                    "idempotency_key": request.idempotency_key,
                    "intent_sha256": intent,
                    "state": "running",
                    "request_id": request.request_id,
                    "started_at": _utc_now(),
                },
            )
        try:
            status, response = action(request)
        except Exception as exc:
            # A failed attempt is retained for audit.  Reuse of the same key
            # returns a stable conflict rather than running a second mutation.
            terminal: dict[str, Any] = {}
            if isinstance(exc, ControllerExecutionFailed):
                terminal = {
                    "execution_id": exc.execution_id,
                    "audit_receipt": exc.audit_receipt,
                    "failure": exc.failure,
                }
            with self._lock:
                retained = _read_json(path)
                _atomic_json(
                    path,
                    {
                        **retained,
                        "state": "failed",
                        "completed_at": _utc_now(),
                        **terminal,
                    },
                )
            raise
        with self._lock:
            _atomic_json(
                path,
                {
                    "schema_version": "v1",
                    "idempotency_key": request.idempotency_key,
                    "intent_sha256": intent,
                    "state": "completed",
                    "request_id": request.request_id,
                    "completed_at": _utc_now(),
                    "http_status": status,
                    "response": response,
                },
            )
        return status, response

    def _write_receipt(
        self,
        *,
        request: ControllerRequest,
        status: str,
        accepted_at: str,
        completed_at: str,
        plan_sha256: str,
        before: Any,
        after: Any,
        failure: Mapping[str, Any] | None = None,
    ) -> str:
        audit_id = "audit-" + uuid.uuid4().hex
        relative = f"controller/controller-audit/{audit_id}.json"
        artifact_paths = [relative]
        if failure is not None:
            failure_relative = (
                f"controller/controller-audit/{audit_id}-failure.json"
            )
            _atomic_json(
                self.state_dir / failure_relative,
                {
                    "schema_version": "v1",
                    "failure": dict(failure),
                    "redactions": ["authorization", "credentials"],
                },
            )
            artifact_paths.append(failure_relative)
        receipt = AuditReceipt.from_mapping(
            {
                "schema_version": "v1",
                "audit_id": audit_id,
                "request_id": request.request_id,
                "operation": request.operation.value,
                "status": status,
                "actor": "dashboard",
                "accepted_at": accepted_at,
                "completed_at": completed_at,
                "request_sha256": self._intent_sha256(request),
                "plan_sha256": plan_sha256,
                "before_sha256": _sha256(before),
                "after_sha256": _sha256(after),
                "artifact_paths": artifact_paths,
                "redactions": ["authorization"],
            }
        )
        _atomic_json(self.state_dir / relative, receipt.to_mapping())
        return relative

    def _record_refusal(
        self,
        raw: Any,
        reason: str,
        *,
        failure: Mapping[str, Any] | None = None,
    ) -> None:
        audit_id = "refusal-" + uuid.uuid4().hex
        body = {
            "schema_version": "v1",
            "audit_id": audit_id,
            "status": "refused",
            "accepted_at": _utc_now(),
            "completed_at": _utc_now(),
            "request_sha256": _sha256(raw),
            "reason": reason[:512],
            "redactions": ["authorization"],
        }
        if failure is not None:
            body["failure"] = _thaw(failure)
        _atomic_json(
            self.audit_dir / f"{audit_id}.json",
            body,
        )


class _Handler(BaseHTTPRequestHandler):
    server_version = "DWARFController/1"

    @property
    def controller(self) -> ControllerService:
        return self.server.controller  # type: ignore[attr-defined]

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        self._dispatch(b"")

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > 1_048_576:
            self._send(413, {"ok": False, "error": "request body is too large"})
            return
        self._dispatch(self.rfile.read(length))

    def log_message(self, format: str, *args: Any) -> None:
        # Avoid the default request-line log because privileged headers must
        # never become coupled to access output.
        return

    def _dispatch(self, body: bytes) -> None:
        status, payload = self.controller.dispatch(
            self.command,
            self.path,
            {key: value for key, value in self.headers.items()},
            body,
        )
        self._send(status, payload)

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = _canonical_bytes(payload) + b"\n"
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def serve(
    service: ControllerService, *, bind: str = "0.0.0.0", port: int = 8790
) -> None:
    server = ThreadingHTTPServer((bind, port), _Handler)
    server.controller = service  # type: ignore[attr-defined]
    server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DWARF local controller")
    parser.add_argument("--bind", default=os.environ.get("DWARF_CONTROLLER_BIND", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("DWARF_CONTROLLER_PORT", "8790")))
    arguments = parser.parse_args(argv)
    token = os.environ.get("DWARF_CONTROLLER_TOKEN", "")
    state_dir = os.environ.get("ADA2_DWARF_STATE_DIR", "/var/dwarf/state")
    release_revision = os.environ.get("DWARF_SOURCE_REVISION", "")
    installation_id = os.environ.get("DWARF_INSTALLATION_ID", "")
    expected_peer_revision = os.environ.get("DWARF_DASHBOARD_REVISION", release_revision)
    if not release_revision or release_revision == "unknown":
        parser.error("DWARF_SOURCE_REVISION must be an immutable revision")
    if not installation_id:
        parser.error("DWARF_INSTALLATION_ID must be configured")
    from .docker_runtime import DockerRuntime

    service = ControllerService(
        token=token,
        state_dir=state_dir,
        runtime=DockerRuntime(installation_id=installation_id, state_dir=state_dir),
        release_revision=release_revision,
        installation_id=installation_id,
        expected_peer_revision=expected_peer_revision,
    )
    serve(service, bind=arguments.bind, port=arguments.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
