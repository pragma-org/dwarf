"""Canonical DWARF scenario execution service.

The dashboard, CLI, and scheduler are adapters around this module.  The
scenario engine remains responsible for phase execution and forensic
finalization; this service owns resolution, invocation provenance, canonical
storage, and the plan retained inside the resulting evidence bundle.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import fcntl
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from profile_manager import forensic as forensic_module
from profile_manager import primitives as primitives_module
from profile_manager import scenario as scenario_module
from profile_manager.config import storage_roots
from profile_manager.run_ledger import (
    FINAL_STATES_BY_EXIT_STATUS,
    RunLedger,
    atomic_write_bytes,
)


_ORIGINS = frozenset({"cli", "dashboard", "scheduler", "replay", "ci", "external"})
_BACKENDS = frozenset({"local", "antithesis", "moog", "external"})
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class RunServiceError(RuntimeError):
    """A canonical run could not be resolved or its evidence reconciled."""


@dataclass(frozen=True)
class RunRequest:
    scenario_path: Path | str
    origin: str = "cli"
    backend: str = "local"
    runs_dir: Path | str | None = None
    state_dir: Path | str | None = None
    registry_path: Path | str | None = None
    fuzz_iterations: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "scenario_path", Path(self.scenario_path))
        for name in ("runs_dir", "state_dir", "registry_path"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, Path(value))
        if self.origin not in _ORIGINS:
            raise ValueError(f"unsupported run origin: {self.origin}")
        if self.backend not in _BACKENDS:
            raise ValueError(f"unsupported run backend: {self.backend}")
        if self.fuzz_iterations is not None and (
            isinstance(self.fuzz_iterations, bool)
            or not 1 <= self.fuzz_iterations <= 1_000_000
        ):
            raise ValueError("fuzz_iterations must be between 1 and 1000000")


@dataclass(frozen=True)
class ExternalRunRequest:
    """One idempotent launch request executed by an external backend."""

    backend: str
    launch: Mapping[str, Any]
    origin: str = "external"
    runs_dir: Path | str | None = None
    state_dir: Path | str | None = None
    scenario_path: Path | str | None = None
    registry_path: Path | str | None = None

    def __post_init__(self) -> None:
        if self.backend == "local" or self.backend not in _BACKENDS:
            raise ValueError(f"unsupported external backend: {self.backend}")
        if self.origin not in _ORIGINS:
            raise ValueError(f"unsupported run origin: {self.origin}")
        if not isinstance(self.launch, Mapping) or not self.launch:
            raise ValueError("external launch evidence must be a non-empty mapping")
        try:
            json.dumps(self.launch, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise ValueError("external launch evidence must be JSON serializable") from exc
        for name in ("runs_dir", "state_dir", "scenario_path", "registry_path"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, Path(value))


@dataclass(frozen=True)
class _ResolvedExecution:
    plan: dict[str, Any]
    scenario_path: Path
    scenario_definition: Any
    registry_definition: dict[str, Any]
    runs_dir: Path
    state_dir: Path
    registry_path: Path
    release_revision: str


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _local_run_kind(request: RunRequest) -> str:
    """Keep CI activity distinct while all interactive adapters remain scenarios."""
    return "ci" if request.origin == "ci" else "scenario"


def _scenario_with_overrides(
    scenario_path: Path, source: bytes, request: RunRequest
) -> Any:
    if request.fuzz_iterations is None:
        return replace(
            scenario_module.scenario_from_body(source), path=scenario_path
        )
    try:
        document = json.loads(source.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunServiceError(f"cannot apply fuzz override: {exc}") from exc
    changed = 0
    for item in document.get("load") or []:
        if item.get("primitive") in {"cbor_fuzz_target", "cbor_fuzz_structured"}:
            item["iterations"] = request.fuzz_iterations
            changed += 1
    if changed == 0:
        raise RunServiceError(
            "fuzz_iterations requires a registered scenario with a fuzz load primitive"
        )
    effective = json.dumps(
        document, sort_keys=True, ensure_ascii=False, indent=2
    ).encode("utf-8") + b"\n"
    return replace(
        scenario_module.scenario_from_body(effective), path=scenario_path
    )


def _resolved_paths(request: RunRequest) -> tuple[Path, Path, Path]:
    roots = (
        storage_roots()
        if request.runs_dir is None or request.state_dir is None
        else None
    )
    if request.runs_dir is None:
        if roots is None:
            raise RunServiceError("canonical runs root could not be resolved")
        runs = roots.runs
    else:
        runs = Path(request.runs_dir)
    if request.state_dir is None:
        if roots is None:
            raise RunServiceError("canonical state root could not be resolved")
        state = roots.state
    else:
        state = Path(request.state_dir)
    registry = (
        Path(request.registry_path)
        if request.registry_path is not None
        else Path(scenario_module.DEFAULT_REGISTRY_PATH)
    )
    return runs.resolve(), state.resolve(), registry.resolve()


def _resolve_execution(request: RunRequest) -> _ResolvedExecution:
    """Load and freeze every input that the local engine will consume."""
    if not isinstance(request, RunRequest):
        raise TypeError("request must be a RunRequest")
    scenario_path = Path(request.scenario_path).resolve()
    if not scenario_path.is_file():
        raise RunServiceError(f"scenario does not exist: {scenario_path}")
    runs_dir, state_dir, registry_path = _resolved_paths(request)
    if not registry_path.is_file():
        raise RunServiceError(f"primitive registry does not exist: {registry_path}")
    scenario_source = scenario_path.read_bytes()
    scenario = _scenario_with_overrides(scenario_path, scenario_source, request)
    if scenario_path.read_bytes() != scenario_source:
        raise RunServiceError("scenario changed while resolving the run")
    registry_source = registry_path.read_bytes()
    registry = primitives_module.load_registry(registry_path)
    if registry_path.read_bytes() != registry_source:
        raise RunServiceError("primitive registry changed while resolving the run")
    registry_entries = {
        name: asdict(entry) for name, entry in sorted(registry.items())
    }
    release_revision = scenario_module._resolve_framework_commit(None)
    resolved = {
        "schema_version": "dwarf.run-service.plan.v1",
        "scenario": {
            "id": scenario.id,
            "digest": _sha256(scenario.raw_bytes),
            "runtime": scenario.runtime,
            "target": dict(scenario.target),
            "profile_id": scenario.profile,
            "topology_id": scenario_module._attached_topology_id(scenario),
            "measurement_profile": scenario.measurement_profile,
            "seed": scenario.seed if scenario.seed is not None else 0,
            "phases": {
                name: [entry.primitive for entry in getattr(scenario, name)]
                for name in (
                    "setup",
                    "load",
                    "faults",
                    "probes",
                    "assertions",
                    "teardown",
                )
            },
        },
        "registry": {
            "source_digest": _sha256(registry_source),
            "digest": _sha256(_canonical_bytes(registry_entries)),
            "entries": registry_entries,
        },
        "release_revision": release_revision,
        "input_overrides": (
            {"fuzz_iterations": request.fuzz_iterations}
            if request.fuzz_iterations is not None
            else {}
        ),
        "evidence_layout": "forensic-bundle-v1",
        "verdict_contract": "non-vacuous-scenario-v1",
    }
    plan_digest = _sha256(_canonical_bytes(resolved))
    if not _SHA256.fullmatch(plan_digest):  # defensive invariant
        raise RunServiceError("resolved plan digest is malformed")
    plan = {
        "schema_version": "dwarf.run-service.invocation.v1",
        "plan_digest": plan_digest,
        "resolved": resolved,
        "invocation": {
            "origin": request.origin,
            "backend": request.backend,
        },
        "storage": {
            "runs_dir": str(runs_dir),
            "state_dir": str(state_dir),
        },
    }
    return _ResolvedExecution(
        plan=plan,
        scenario_path=scenario_path,
        scenario_definition=scenario,
        registry_definition=registry,
        runs_dir=runs_dir,
        state_dir=state_dir,
        registry_path=registry_path,
        release_revision=release_revision,
    )


def resolve_run(request: RunRequest) -> dict[str, Any]:
    """Resolve one deterministic engine plan plus honest adapter provenance."""
    return _resolve_execution(request).plan


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RunServiceError(f"{label} is unavailable: {exc}") from exc
    if not isinstance(value, dict):
        raise RunServiceError(f"{label} must be a JSON object")
    return value


def _require_equal(actual: object, expected: object, label: str) -> None:
    if actual != expected:
        raise RunServiceError(
            f"{label} mismatch: expected {expected!r}, observed {actual!r}"
        )


def _reconcile_result(
    *, handle: Any, execution: _ResolvedExecution, request: RunRequest
) -> tuple[dict[str, Any], dict[str, Any]]:
    plan = execution.plan
    run_dir = Path(handle.run_dir).resolve()
    _require_equal(handle.run_id, run_dir.name, "run handle identity")

    retained_plan = _read_json_object(
        run_dir / "inputs" / "run-service-plan.json", "retained run plan"
    )
    _require_equal(retained_plan, plan, "retained run plan")
    _require_equal(
        _sha256(_canonical_bytes(retained_plan.get("resolved"))),
        retained_plan.get("plan_digest"),
        "retained run plan digest",
    )

    scenario_plan = plan["resolved"]["scenario"]
    expected_scenario_hash = str(scenario_plan["digest"]).removeprefix("sha256:")
    try:
        retained_scenario = (run_dir / "scenario.yaml").read_bytes()
    except OSError as exc:
        raise RunServiceError(f"retained scenario is unavailable: {exc}") from exc
    _require_equal(
        hashlib.sha256(retained_scenario).hexdigest(),
        expected_scenario_hash,
        "retained scenario digest",
    )

    manifest_path = run_dir / "manifest.json"
    manifest = _read_json_object(manifest_path, "run manifest")
    manifest_scenario = manifest.get("scenario") or {}
    manifest_framework = manifest.get("framework") or {}
    _require_equal(manifest.get("run_id"), handle.run_id, "manifest run identity")
    _require_equal(
        manifest_scenario.get("id"), scenario_plan["id"], "manifest scenario id"
    )
    _require_equal(
        manifest_scenario.get("sha256"),
        expected_scenario_hash,
        "manifest scenario digest",
    )
    _require_equal(
        manifest_framework.get("commit"),
        execution.release_revision,
        "manifest release revision",
    )
    manifest_run_service = manifest.get("run_service") or {}
    _require_equal(
        manifest_run_service.get("plan_path"),
        "inputs/run-service-plan.json",
        "manifest run service plan path",
    )
    _require_equal(
        manifest_run_service.get("plan_sha256"),
        hashlib.sha256(_canonical_bytes(plan)).hexdigest(),
        "manifest run service plan digest",
    )
    exit_status = manifest.get("exit_status")
    if exit_status not in FINAL_STATES_BY_EXIT_STATUS:
        raise RunServiceError(f"manifest exit status is unsupported: {exit_status!r}")
    execution_result = manifest.get("execution") or {}
    manifest_verdict = execution_result.get("security_verdict")

    try:
        ledger = RunLedger(execution.state_dir).get(handle.run_id)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RunServiceError(f"run ledger entry is unavailable: {exc}") from exc
    _require_equal(ledger.get("run_id"), handle.run_id, "ledger run identity")
    _require_equal(ledger.get("kind"), _local_run_kind(request), "ledger kind")
    _require_equal(ledger.get("origin"), request.origin, "ledger origin")
    _require_equal(ledger.get("backend"), request.backend, "ledger backend")
    _require_equal(
        ledger.get("release_revision"),
        execution.release_revision,
        "ledger release revision",
    )
    _require_equal(
        ledger.get("scenario"),
        {"id": scenario_plan["id"], "sha256": expected_scenario_hash},
        "ledger scenario identity",
    )
    _require_equal(ledger.get("state"), "running", "ledger pre-publish state")
    _require_equal(ledger.get("verdict"), None, "ledger pre-publish verdict")
    _require_equal(
        ledger.get("manifest_path"), None, "ledger pre-publish manifest path"
    )
    _require_equal(
        Path(str(ledger.get("evidence_path", ""))).resolve(),
        run_dir,
        "ledger evidence path",
    )
    verified = forensic_module.verify(
        handle.run_id,
        runs_dir=execution.runs_dir,
        state_dir=execution.state_dir,
    )
    if not verified.ok:
        raise RunServiceError(
            "forensic bundle verification failed: " + "; ".join(verified.errors)
        )
    terminal = RunLedger(execution.state_dir).transition(
        handle.run_id,
        FINAL_STATES_BY_EXIT_STATUS[exit_status],
        verdict=manifest_verdict,
        ended_at=manifest.get("ended_at"),
        manifest_path=str(manifest_path.resolve()),
        evidence_path=str(run_dir),
    )
    return manifest, terminal


def _record_reconciliation_failure(
    *, handle: Any, execution: _ResolvedExecution, error: BaseException
) -> None:
    """Publish an explicit failed state only while the entry is still provisional."""
    ledger_store = RunLedger(execution.state_dir)
    try:
        entry = ledger_store.get(handle.run_id)
        if entry.get("state") != "running":
            return
        provenance = dict(entry.get("provenance") or {})
        provenance["reconciliation"] = {
            "state": "failed",
            "error_type": type(error).__name__,
            "error": str(error)[:1000],
        }
        manifest_path = Path(handle.run_dir) / "manifest.json"
        ledger_store.transition(
            handle.run_id,
            "failed",
            verdict="inconclusive",
            manifest_path=(
                str(manifest_path.resolve()) if manifest_path.is_file() else None
            ),
            evidence_path=str(Path(handle.run_dir).resolve()),
            provenance=provenance,
        )
    except (OSError, ValueError, json.JSONDecodeError):
        return


def execute_run(request: RunRequest) -> dict[str, Any]:
    """Execute one local scenario and reconcile bundle, manifest, and ledger."""
    if request.backend != "local":
        raise RunServiceError(
            f"backend {request.backend!r} execution is not implemented by the local Run Service"
        )
    execution = _resolve_execution(request)
    plan = execution.plan
    handle = scenario_module.run_scenario(
        execution.scenario_path,
        runs_dir=execution.runs_dir,
        state_dir=execution.state_dir,
        registry_path=execution.registry_path,
        scenario_definition=execution.scenario_definition,
        registry_definition=execution.registry_definition,
        framework_commit=execution.release_revision,
        run_kind=_local_run_kind(request),
        origin=request.origin,
        backend=request.backend,
        actor=f"run-service:{request.origin}",
        run_service_plan=plan,
        defer_ledger_terminal=True,
    )
    try:
        manifest, ledger = _reconcile_result(
            handle=handle, execution=execution, request=request
        )
    except Exception as exc:
        _record_reconciliation_failure(
            handle=handle, execution=execution, error=exc
        )
        if isinstance(exc, RunServiceError):
            raise
        raise RunServiceError(f"run reconciliation failed: {exc}") from exc
    return {
        "run_id": handle.run_id,
        "run_dir": str(handle.run_dir),
        "plan": plan,
        "manifest": manifest,
        "ledger": ledger,
    }


def _external_storage_paths(request: ExternalRunRequest) -> tuple[Path, Path]:
    roots = (
        storage_roots()
        if request.runs_dir is None or request.state_dir is None
        else None
    )
    runs_dir = (
        roots.runs if request.runs_dir is None and roots is not None else request.runs_dir
    )
    state_dir = (
        roots.state
        if request.state_dir is None and roots is not None
        else request.state_dir
    )
    if runs_dir is None or state_dir is None:
        raise RunServiceError("canonical external-run storage could not be resolved")
    return Path(runs_dir).resolve(), Path(state_dir).resolve()


def _write_receipt(path: Path, payload: Mapping[str, Any]) -> None:
    try:
        encoded = json.dumps(
            payload, sort_keys=True, ensure_ascii=False, indent=2
        ).encode("utf-8") + b"\n"
    except (TypeError, ValueError) as exc:
        raise RunServiceError("external evidence is not JSON serializable") from exc
    atomic_write_bytes(path, encoded)


def reserve_external_run(request: ExternalRunRequest) -> dict[str, Any]:
    """Reserve exactly one ledger identity for one canonical launch request."""
    if not isinstance(request, ExternalRunRequest):
        raise TypeError("request must be an ExternalRunRequest")
    runs_dir, state_dir = _external_storage_paths(request)
    release_revision = scenario_module._resolve_framework_commit(None)
    scenario_identity = None
    scenario_resolved = None
    if request.scenario_path is not None:
        resolved = _resolve_execution(
            RunRequest(
                scenario_path=request.scenario_path,
                origin=request.origin,
                backend=request.backend,
                runs_dir=runs_dir,
                state_dir=state_dir,
                registry_path=request.registry_path,
            )
        )
        scenario_resolved = resolved.plan["resolved"]
        scenario_plan = scenario_resolved["scenario"]
        scenario_identity = {
            "id": scenario_plan["id"],
            "sha256": str(scenario_plan["digest"]).removeprefix("sha256:"),
        }
        release_revision = resolved.release_revision

    launch = dict(request.launch)
    # Idempotency follows the external action, not the adapter or DWARF release
    # that happened to request it. The same Moog launch must never be submitted
    # twice merely because a caller changed surfaces or upgraded DWARF.
    if launch.get("mode") == "link-existing":
        transport = str(launch.get("transport") or "").strip()
        external_test_run_id = str(
            launch.get("external_test_run_id") or ""
        ).strip()
        if not transport or not external_test_run_id:
            raise RunServiceError(
                "linked external launch requires transport and external test id"
            )
        identity_document = {
            "backend": request.backend,
            "transport": transport,
            "external_test_run_id": external_test_run_id,
        }
    else:
        identity_document = {"backend": request.backend, "launch": launch}
    external_key = _sha256(_canonical_bytes(identity_document))
    run_id = f"external-{request.backend}-{external_key.removeprefix('sha256:')[:24]}"
    resolved_plan = {
        "schema_version": "dwarf.external-run.plan.v1",
        "launch": launch,
        "scenario": scenario_resolved,
        "release_revision": release_revision,
        "evidence_layout": "external-run-evidence-v1",
        "verdict_contract": "verified-non-vacuous-bundle-or-inconclusive-v1",
    }
    plan = {
        "schema_version": "dwarf.run-service.invocation.v1",
        "plan_digest": _sha256(_canonical_bytes(resolved_plan)),
        "resolved": resolved_plan,
        "invocation": {"origin": request.origin, "backend": request.backend},
        "storage": {"runs_dir": str(runs_dir), "state_dir": str(state_dir)},
    }
    run_dir = runs_dir / run_id
    plan_path = run_dir / "inputs" / "run-service-plan.json"
    launch_path = run_dir / "external" / "launch.json"
    provenance = {
        "actor": f"run-service:{request.origin}",
        "external_key": external_key,
        "launch_request_path": str(launch_path.resolve()),
        "plan_path": str(plan_path.resolve()),
        "verdict_contract": resolved_plan["verdict_contract"],
    }
    ledger = RunLedger(state_dir)
    try:
        existing = ledger.get(run_id)
    except FileNotFoundError:
        existing = None
    if existing is not None:
        if existing.get("provenance", {}).get("external_key") != external_key:
            raise RunServiceError("external run identity collision")
        _require_equal(
            existing.get("scenario"),
            scenario_identity,
            "retained external scenario",
        )
        try:
            retained_plan = json.loads(plan_path.read_text(encoding="utf-8"))
            retained_launch = json.loads(launch_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RunServiceError(
                f"retained external launch evidence is unavailable: {exc}"
            ) from exc
        _require_equal(retained_launch, launch, "retained external launch request")
        _require_equal(
            (retained_plan.get("invocation") or {}).get("backend"),
            request.backend,
            "retained external backend",
        )
        _require_equal(
            (retained_plan.get("resolved") or {}).get("launch"),
            launch,
            "retained external plan launch",
        )
        retained_scenario = (
            ((retained_plan.get("resolved") or {}).get("scenario") or {}).get(
                "scenario"
            )
            if scenario_identity is not None
            else None
        )
        retained_scenario_identity = (
            {
                "id": retained_scenario.get("id"),
                "sha256": str(retained_scenario.get("digest") or "").removeprefix(
                    "sha256:"
                ),
            }
            if isinstance(retained_scenario, Mapping)
            else None
        )
        _require_equal(
            retained_scenario_identity,
            scenario_identity,
            "retained external plan scenario",
        )
        return {
            "run_id": run_id,
            "run_dir": str(run_dir.resolve()),
            "plan": retained_plan,
            "ledger": existing,
            "created": False,
        }

    try:
        run_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        try:
            ledger.get(run_id)
        except FileNotFoundError:
            raise RunServiceError(
                "external reservation evidence exists without a ledger entry; "
                "reconciliation required"
            ) from exc
        return reserve_external_run(request)

    _write_receipt(plan_path, plan)
    _write_receipt(launch_path, launch)
    try:
        entry = ledger.create(
            run_id=run_id,
            kind="external",
            origin=request.origin,
            backend=request.backend,
            scenario=scenario_identity,
            release_revision=release_revision,
            evidence_path=str(run_dir.resolve()),
            provenance=provenance,
        )
        created = True
    except ValueError as exc:
        if "already exists" not in str(exc):
            raise
        entry = ledger.get(run_id)
        if entry.get("provenance", {}).get("external_key") != external_key:
            raise RunServiceError("external run identity collision") from exc
        _require_equal(
            entry.get("scenario"),
            scenario_identity,
            "racing external scenario",
        )
        raise RunServiceError(
            "external reservation ledger changed during creation; retry reconciliation"
        ) from exc
    return {
        "run_id": run_id,
        "run_dir": str(run_dir.resolve()),
        "plan": plan,
        "ledger": entry,
        "created": created,
    }


def record_external_submission(
    run_id: str,
    *,
    state_dir: Path | str,
    response: Mapping[str, Any],
    external_test_run_id: str | None = None,
) -> dict[str, Any]:
    """Persist the exact submission receipt, then publish running/failed state."""
    ledger = RunLedger(state_dir)
    entry = ledger.get(run_id)
    if entry.get("kind") != "external":
        raise RunServiceError(f"run is not external: {run_id}")
    evidence_dir = Path(str(entry["evidence_path"]))
    receipt_path = evidence_dir / "external" / "submission.json"
    receipt = dict(response)
    if external_test_run_id:
        receipt["external_test_run_id"] = external_test_run_id
    _write_receipt(receipt_path, receipt)
    provenance = {
        **(entry.get("provenance") or {}),
        "submission_response_path": str(receipt_path.resolve()),
    }
    if external_test_run_id:
        provenance["external_test_run_id"] = external_test_run_id
    successful = int(response.get("returncode", 1)) == 0
    if entry["state"] == "queued":
        return ledger.transition(
            run_id,
            "running" if successful else "failed",
            verdict=None if successful else "inconclusive",
            provenance=provenance,
        )
    return ledger.annotate(run_id, provenance=provenance)


def link_existing_external_run(
    run_id: str,
    *,
    state_dir: Path | str,
    external_test_run_id: str,
    link_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Link a read-only external fact without inventing a new submission."""
    ledger = RunLedger(state_dir)
    entry = ledger.get(run_id)
    if entry.get("kind") != "external":
        raise RunServiceError(f"run is not external: {run_id}")
    if not external_test_run_id:
        raise RunServiceError("external test id is required")
    evidence = dict(link_evidence)
    if evidence.get("test_run_id") != external_test_run_id:
        raise RunServiceError("linked external test id does not match evidence")
    evidence_dir = Path(str(entry["evidence_path"]))
    lock_path = evidence_dir / "external" / "link.lock"
    with lock_path.open("a+b") as lock_stream:
        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
        try:
            entry = ledger.get(run_id)
            retained_external_id = (entry.get("provenance") or {}).get(
                "external_test_run_id"
            )
            if retained_external_id and retained_external_id != external_test_run_id:
                raise RunServiceError(
                    "external test id mismatch: "
                    f"expected {retained_external_id!r}, observed {external_test_run_id!r}"
                )

            launch_path = evidence_dir / "external" / "launch.json"
            plan_path = evidence_dir / "inputs" / "run-service-plan.json"
            try:
                retained_launch = json.loads(launch_path.read_text(encoding="utf-8"))
                retained_plan = json.loads(plan_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise RunServiceError(
                    f"retained external launch evidence is unavailable: {exc}"
                ) from exc
            if retained_launch.get("mode") != "link-existing":
                raise RunServiceError("external run was not reserved as a linked run")
            if retained_launch.get("external_test_run_id") != external_test_run_id:
                raise RunServiceError(
                    "external test id mismatch: "
                    f"expected {retained_launch.get('external_test_run_id')!r}, "
                    f"observed {external_test_run_id!r}"
                )
            resolved_launch = (retained_plan.get("resolved") or {}).get("launch")
            _require_equal(
                resolved_launch, retained_launch, "retained external plan launch"
            )
            _require_equal(
                (retained_plan.get("invocation") or {}).get("backend"),
                entry.get("backend"),
                "retained external plan backend",
            )
            link_path = evidence_dir / "external" / "link.json"
            if link_path.exists():
                try:
                    retained_evidence = json.loads(
                        link_path.read_text(encoding="utf-8")
                    )
                except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                    raise RunServiceError(
                        f"retained external link evidence is unavailable: {exc}"
                    ) from exc
                _require_equal(
                    retained_evidence, evidence, "retained external link evidence"
                )
                if retained_external_id == external_test_run_id:
                    return entry
            else:
                _write_receipt(link_path, evidence)

            provenance = {
                **(entry.get("provenance") or {}),
                "external_test_run_id": external_test_run_id,
                "external_link_path": str(link_path.resolve()),
                "linked_existing": True,
            }
            if entry["state"] == "queued":
                return ledger.transition(run_id, "running", provenance=provenance)
            return ledger.annotate(run_id, provenance=provenance)
        finally:
            fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)


def _external_result_verdict(
    entry: Mapping[str, Any],
    *,
    external_test_run_id: str | None,
    result_bundle_path: Path | str | None,
    result_signature_path: Path | str | None,
    trusted_public_key_hex: str | None,
    result_runs_dir: Path | str | None,
) -> tuple[str, str | None, str, dict[str, Any]]:
    """Import and validate a signed result without trusting adapter assertions."""
    supplied = (
        result_bundle_path,
        result_signature_path,
        result_runs_dir,
    )
    if not any(value is not None for value in supplied):
        return "inconclusive", None, "no_verified_result_bundle", {}
    if not all(value is not None for value in supplied):
        return "inconclusive", None, "result_bundle_inputs_incomplete", {}
    assert result_bundle_path is not None
    assert result_signature_path is not None
    assert result_runs_dir is not None

    from scripts import bundle_import as bundle_import_module

    try:
        imported = bundle_import_module.import_bundle(
            tarball_path=Path(result_bundle_path),
            signature_path=Path(result_signature_path),
            runs_dir=Path(result_runs_dir),
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return (
            "inconclusive",
            None,
            "result_bundle_import_failed",
            {"result_bundle_error": f"{type(exc).__name__}: {exc}"},
        )
    evidence: dict[str, Any] = {"result_bundle": dict(imported)}
    if (
        imported.get("verdict") != "imported"
        or imported.get("signature_verdict") != "verified"
    ):
        return "inconclusive", None, "result_bundle_not_verified", evidence

    signer = str(imported.get("signing_public_key_hex") or "").lower()
    trusted = str(trusted_public_key_hex or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", trusted) or signer != trusted:
        return "inconclusive", None, "result_bundle_signer_untrusted", evidence

    result_root = Path(result_runs_dir).resolve()
    destination = Path(str(imported.get("destination_dir") or "")).resolve()
    if not destination.is_relative_to(result_root):
        return "inconclusive", None, "result_bundle_path_outside_store", evidence
    manifest_path = destination / "manifest.json"
    chain_path = destination / "chain.json"
    assertions_path = destination / "assertions.json"
    try:
        manifest = _read_json_object(manifest_path, "external result manifest")
        chain = _read_json_object(chain_path, "external result chain")
        assertions = json.loads(assertions_path.read_text(encoding="utf-8"))
    except (RunServiceError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        return (
            "inconclusive",
            None,
            "result_bundle_evidence_unavailable",
            {**evidence, "result_bundle_error": f"{type(exc).__name__}: {exc}"},
        )
    if not isinstance(assertions, list):
        return "inconclusive", None, "result_bundle_assertions_invalid", evidence

    result_run_id = destination.name
    if not (
        imported.get("requested_run_id")
        == imported.get("imported_run_id")
        == result_run_id
        == manifest.get("run_id")
        == chain.get("run_id")
    ):
        return "inconclusive", None, "result_bundle_run_identity_mismatch", evidence
    manifest_hash = forensic_module._sha256_hex(
        forensic_module._canonical_json(manifest)
    )
    if chain.get("manifest_hash") != manifest_hash:
        return "inconclusive", None, "result_bundle_manifest_hash_mismatch", evidence

    expected_external_id = (entry.get("provenance") or {}).get(
        "external_test_run_id"
    )
    external = manifest.get("external") or {}
    if (
        not expected_external_id
        or external_test_run_id != expected_external_id
        or external.get("test_run_id") != expected_external_id
        or external.get("backend") != entry.get("backend")
    ):
        return "inconclusive", None, "result_bundle_external_identity_mismatch", evidence

    expected_scenario = entry.get("scenario")
    observed_scenario = manifest.get("scenario") or {}
    if not isinstance(expected_scenario, Mapping):
        return "inconclusive", None, "result_bundle_scenario_unbound", evidence
    expected_scenario_sha = str(expected_scenario.get("sha256") or "").removeprefix(
        "sha256:"
    )
    observed_scenario_sha = str(observed_scenario.get("sha256") or "").removeprefix(
        "sha256:"
    )
    if (
        observed_scenario.get("id") != expected_scenario.get("id")
        or observed_scenario_sha != expected_scenario_sha
    ):
        return "inconclusive", None, "result_bundle_scenario_identity_mismatch", evidence

    summary = manifest.get("assertion_summary") or {}
    if not isinstance(summary, Mapping):
        return "inconclusive", None, "result_bundle_assertions_invalid", evidence
    raw_total = summary.get("total")
    raw_passed = summary.get("pass")
    raw_failed = summary.get("fail")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in (raw_total, raw_passed, raw_failed)
    ):
        return "inconclusive", None, "result_bundle_assertions_invalid", evidence
    assert isinstance(raw_total, int)
    assert isinstance(raw_passed, int)
    assert isinstance(raw_failed, int)
    total, passed, failed = raw_total, raw_passed, raw_failed
    actual_passed = sum(
        isinstance(assertion, Mapping) and assertion.get("result") == "pass"
        for assertion in assertions
    )
    actual_failed = sum(
        isinstance(assertion, Mapping) and assertion.get("result") == "fail"
        for assertion in assertions
    )
    if (
        total <= 0
        or total != passed + failed
        or total != len(assertions)
        or (passed, failed) != (actual_passed, actual_failed)
    ):
        return "inconclusive", None, "result_bundle_assertions_invalid", evidence

    from profile_manager.scenario_verdict import evaluate_scenario_verdict

    canonical = evaluate_scenario_verdict(
        exit_status=str(manifest.get("exit_status") or "unknown"),
        assertions=summary,
        measurements=manifest.get("measurements"),
        run_kind="external",
    )
    claimed = (manifest.get("execution") or {}).get("security_verdict")
    if (
        canonical.security_verdict not in {"pass", "fail"}
        or claimed != canonical.security_verdict
    ):
        return "inconclusive", None, "result_bundle_verdict_mismatch", evidence
    evidence["result_bundle_manifest_sha256"] = manifest_hash
    evidence["result_bundle_run_id"] = result_run_id
    return (
        canonical.security_verdict,
        str(manifest_path),
        "verified_non_vacuous_result_bundle",
        evidence,
    )


def reconcile_external_status(
    run_id: str,
    *,
    state_dir: Path | str,
    phase: str | None,
    status_evidence: Mapping[str, Any],
    result_bundle_path: Path | str | None = None,
    result_signature_path: Path | str | None = None,
    trusted_public_key_hex: str | None = None,
    result_runs_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Reconcile external operational state without inventing a test verdict."""
    ledger = RunLedger(state_dir)
    entry = ledger.get(run_id)
    if entry.get("kind") != "external":
        raise RunServiceError(f"run is not external: {run_id}")
    expected_external_id = (entry.get("provenance") or {}).get(
        "external_test_run_id"
    )
    observed_external_id = status_evidence.get("test_run_id")
    if not expected_external_id:
        raise RunServiceError(
            "external test id is not bound; record submission or link the run first"
        )
    if observed_external_id != expected_external_id:
        raise RunServiceError(
            "external test id mismatch: "
            f"expected {expected_external_id!r}, observed {observed_external_id!r}"
        )
    evidence_dir = Path(str(entry["evidence_path"]))
    status_path = evidence_dir / "external" / "status.json"
    _write_receipt(status_path, dict(status_evidence))
    normalized = str(phase or "unknown").strip().lower()
    provenance = {
        **(entry.get("provenance") or {}),
        "status_evidence_path": str(status_path.resolve()),
        "external_phase": normalized,
    }
    active = {"pending", "requested", "submitted", "accepted", "running"}
    failed = {"failed", "rejected", "cancelled", "canceled", "error"}
    completed = {"complete", "completed", "finished", "passed", "succeeded"}
    if normalized in active or normalized == "unknown":
        if entry["state"] == "queued":
            return ledger.transition(run_id, "running", provenance=provenance)
        return ledger.annotate(run_id, provenance=provenance)

    if entry["state"] == "queued" and normalized in completed:
        entry = ledger.transition(run_id, "running", provenance=provenance)
    verdict, manifest_path, verdict_reason, result_provenance = (
        _external_result_verdict(
            entry,
            external_test_run_id=(
                str(observed_external_id) if observed_external_id is not None else None
            ),
            result_bundle_path=result_bundle_path,
            result_signature_path=result_signature_path,
            trusted_public_key_hex=trusted_public_key_hex,
            result_runs_dir=result_runs_dir,
        )
    )
    provenance.update(result_provenance)
    provenance["verdict_reason"] = verdict_reason

    if entry["state"] not in {"queued", "running"}:
        if verdict in {"pass", "fail"} and manifest_path:
            return ledger.reconcile_terminal_evidence(
                run_id,
                verdict=verdict,
                manifest_path=manifest_path,
                provenance=provenance,
            )
        return ledger.annotate(run_id, provenance=provenance)

    terminal_state = "failed" if normalized in failed else "completed"
    if normalized not in failed | completed:
        terminal_state = "failed"
        provenance["verdict_reason"] = "unrecognized_terminal_phase"
    return ledger.transition(
        run_id,
        terminal_state,
        verdict=verdict,
        provenance=provenance,
        manifest_path=manifest_path,
    )


def find_external_run(
    external_test_run_id: str, *, state_dir: Path | str
) -> dict[str, Any] | None:
    for entry in RunLedger(state_dir).list_entries():
        if (
            entry.get("kind") == "external"
            and (entry.get("provenance") or {}).get("external_test_run_id")
            == external_test_run_id
        ):
            return entry
    return None
