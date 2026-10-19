"""Fail-closed candidate build boundary with an injected stock worker seam.

No concrete worker, Docker integration or CLI backend configuration is provided.
An operator-owned backend must enforce the retained isolation/timeout contract;
candidate metadata can never select it or provide successful observations.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, ExitStack
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from typing import Protocol

from profile_manager import candidate_builds
from profile_manager.candidate_build_environments import _retained, resolve_build_environment
from profile_manager.candidate_builds import plan_candidate_builds, validate_candidate_build_plan
from profile_manager.run_ledger import atomic_write_bytes
from profile_manager.testplease_candidates import CandidateSnapshot, discover_candidates
from scripts.ci_security_campaign import HOST_LOCK, host_lock


_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_IMAGE = re.compile(r"[^\s@]+@sha256:[0-9a-f]{64}\Z")
# Bound the data-only freeze as well. These limits do not claim to sandbox a
# compiler; that backend remains unavailable until its own limits are reviewed.
MAX_SOURCE_BYTES = 512 * 1024 * 1024
MAX_SOURCE_ENTRIES = 100_000
FREEZE_TIMEOUT_SECONDS = 30
MAX_WORKER_OUTPUT_BYTES = 1024 * 1024 * 1024
MAX_WORKER_OUTPUT_ENTRIES = 1024
MAX_ERROR_DETAIL = 2048


@dataclass(frozen=True)
class StockWorkerRequest:
    """Private frozen inputs only; no caller credentials, source paths or env.

    identity_json contains the validated built-in recipe. environment_json is
    the validated manifest including pinned builder/platform, trusted probe argv
    and mandatory containment/resource limits. Inputs are private byte-verified
    copies, with their relative paths and expected SHA-256 identities.
    """
    identity_json: str
    environment_json: str
    inputs: tuple[tuple[str, str, Path], ...]
    source: CandidateSnapshot
    output: Path


class DisposableStockWorker(Protocol):
    def wait(self, *, timeout_seconds: int) -> int:
        """Enforce deadline, return terminal exit code, raise on worker failure."""
        ...

    def cleanup(self) -> None:
        """Stop/join all owned work and remove resources, or raise on failure."""
        ...


class StockWorkerBackend(Protocol):
    def start(self, request: StockWorkerRequest) -> DisposableStockWorker:
        """Start only this isolated worker; failed starts must clean themselves.

        The trusted adapter must enforce the manifest limits while producing
        fixed outputs executable, image, build-result.json, probes/NNNN.stdout.
        Probe outputs must come from the manifest argv executed in the builder,
        independently of candidate build hooks. Image bytes must be the retained
        runtime image artifact, with the verified executable packaged as declared.
        There is no implementation of this containment boundary in this module.
        """
        ...


def _save(path, document):
    atomic_write_bytes(path, (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode())


def _owned_directory(path):
    path = Path(path).absolute()
    for part in (*reversed(path.parents), path):
        if part.is_symlink():
            raise ValueError("symlink in executor output path")
    if path.exists():
        raise ValueError("executor output already exists; preserve it and use a fresh directory")
    path.mkdir(parents=True, mode=0o700)
    return path


class _SourceBudget:
    """One budget covers all source copies, recognition and plan validation."""

    def __init__(self, *, max_bytes=None, max_entries=None, timeout=None):
        self.started = time.monotonic()
        self.bytes = self.entries = 0
        self.max_bytes = MAX_SOURCE_BYTES if max_bytes is None else max_bytes
        self.max_entries = MAX_SOURCE_ENTRIES if max_entries is None else max_entries
        self.timeout = FREEZE_TIMEOUT_SECONDS if timeout is None else timeout

    def check(self, *, size=0, entries=0):
        self.bytes += size
        self.entries += entries
        if (self.bytes > self.max_bytes or self.entries > self.max_entries
                or time.monotonic() - self.started > self.timeout):
            raise ValueError("candidate source snapshot exceeds size, entry or time limit")


def _inode(value):
    return value.st_dev, value.st_ino, value.st_mode


def _fingerprint(value):
    return (*_inode(value), value.st_size, value.st_mtime_ns, value.st_ctime_ns, value.st_nlink)


@contextmanager
def _directory_fd(path, budget):
    """Keep ancestor descriptors until the copy finishes; detect replacements."""
    path = Path(path).absolute()
    if ".." in path.parts:
        raise ValueError("parent traversal in candidate source path")
    descriptors = [os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)]
    anchors = []
    try:
        for component in path.parts[1:]:
            budget.check()
            parent = descriptors[-1]
            before = os.stat(component, dir_fd=parent, follow_symlinks=False)
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            descriptors.append(child)
            if _inode(os.fstat(child)) != _inode(before):
                raise ValueError("candidate source ancestor changed during snapshot")
            anchors.append((parent, component, before))
        yield descriptors[-1]
        for parent, component, before in anchors:
            budget.check()
            if _inode(os.stat(component, dir_fd=parent, follow_symlinks=False)) != _inode(before):
                raise ValueError("candidate source ancestor replaced during snapshot")
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def _freeze_source(candidate, destination, *, budget):
    """Copy regular files via anchored descriptors; reject every symlink/type.

    No source file is hard-linked or executed. The private destination is owned
    by this invocation, and is never mounted with a socket or credentials.
    """
    def visit(directory_fd, output):
        directory_before = os.fstat(directory_fd)
        # Stream enumeration: os.listdir/sorting would allocate/read an
        # unbounded directory before an entry budget could be checked.
        with os.scandir(directory_fd) as iterator:
            while True:
                budget.check()
                try:
                    entry = next(iterator)
                except StopIteration:
                    break
                budget.check(entries=1)
                name = entry.name
                before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                mode = before.st_mode
                if stat.S_ISDIR(mode):
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
                    try:
                        if _fingerprint(os.fstat(child)) != _fingerprint(before):
                            raise ValueError("candidate source directory changed during snapshot")
                        target = output / name
                        target.mkdir(mode=0o700)
                        visit(child, target)
                    finally:
                        os.close(child)
                elif stat.S_ISREG(mode):
                    if before.st_nlink != 1:
                        raise ValueError("hardlinks are forbidden in executor source")
                    # Never read a raced FIFO/device/socket. O_NONBLOCK also
                    # prevents open() of a replacement FIFO from hanging.
                    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOCTTY,
                                 dir_fd=directory_fd)
                    with os.fdopen(fd, "rb") as source:
                        if _fingerprint(os.fstat(source.fileno())) != _fingerprint(before):
                            raise ValueError("candidate source file changed during snapshot")
                        if before.st_size > budget.max_bytes - budget.bytes:
                            raise ValueError("candidate source snapshot exceeds size limit")
                        target = output / name
                        with target.open("xb") as copied:
                            while True:
                                budget.check()
                                block = source.read(1024 * 1024)
                                if not block:
                                    break
                                budget.check(size=len(block))
                                copied.write(block)
                        if _fingerprint(os.fstat(source.fileno())) != _fingerprint(before):
                            raise ValueError("candidate source file mutated during snapshot")
                        target.chmod(0o500 if mode & 0o111 else 0o400)
                else:
                    raise ValueError("symlinks and special files are forbidden in executor source")
                if _fingerprint(os.stat(name, dir_fd=directory_fd, follow_symlinks=False)) != _fingerprint(before):
                    raise ValueError("candidate source entry replaced during snapshot")
        budget.check()
        if _fingerprint(os.fstat(directory_fd)) != _fingerprint(directory_before):
            raise ValueError("candidate source directory mutated during snapshot")

    with _directory_fd(candidate.source_root, budget) as fd:
        visit(fd, destination)


@contextmanager
def _snapshot(candidate, budget):
    """No recognition, digest or plan read is allowed on original pathnames."""
    if (candidate.implementation not in {"amaru", "cardano-node"}
            or not _NAME.fullmatch(candidate.candidate_name)
            or not _HEX.fullmatch(candidate.source_tree_digest)
            or not _COMMIT.fullmatch(candidate.repository_commit)):
        raise ValueError("invalid frozen source snapshot identity")
    with tempfile.TemporaryDirectory(prefix="dwarf-candidate-freeze-") as workspace:
        repository = Path(workspace).resolve()
        source = repository / "dwarf/testplease" / candidate.implementation / candidate.candidate_name
        source.mkdir(parents=True, mode=0o700)
        budget.check()
        _freeze_source(candidate, source, budget=budget)
        budget.check()
        # These pathname readers now see only this invocation's bounded,
        # regular-only private copy. They cannot race the submitted checkout.
        snapshots = discover_candidates(repository, repository_commit=candidate.repository_commit)
        budget.check()
        expected = replace(candidate, source_root=source)
        if snapshots != (expected,):
            raise ValueError("isolated source copy differs from frozen source snapshot")
        yield expected
        budget.check()


def _identity(plan):
    identity = asdict(plan)
    identity["candidate"].pop("source_root")
    identity.update(repository_commit=plan.repository_commit, source_tree_digest=plan.source_tree_digest)
    # JSON-compatible receipt independent of the caller's dataclass/tuples.
    return json.loads(json.dumps(identity))


def _blockers(plan):
    reasons = list(plan.build_environment_blockers)
    if not _IMAGE.fullmatch(plan.build_container_image or ""):
        reasons.append("build-container-image-unpinned")
    if not _IMAGE.fullmatch(plan.base_image or ""):
        reasons.append("runtime-base-image-unpinned")
    if plan.dependency_closure_digest is None:
        reasons.append("dependency-closure-unfrozen")
    if plan.build_environment_digest is None:
        reasons.append("runtime-packaging-recipe-unresolved")
    # A checked-in declaration/offline log does not establish a new execution's
    # observations, enforce containment, or produce verified output artifacts.
    reasons.extend(("observed-toolchain-unverified", "disposable-build-backend-unimplemented",
                    "artifact-verification-unimplemented"))
    return reasons


def _stock_prerequisites(plan):
    # The three execution obligations are satisfied by this path. Every other
    # unresolved prerequisite still blocks before the adapter is called.
    obligations = {
        "observed compiler/tool identities require verification inside the isolated environment",
        "isolated frozen-source copy and candidate build-hook containment require verification",
        "executable, image and build-result artifact digests have not been produced or verified",
    }
    return (plan.candidate.implementation == "amaru" and plan.variant == "stock"
            and not (set(plan.unresolved) - obligations)
            and not plan.build_environment_blockers
            and _IMAGE.fullmatch(plan.build_container_image or "")
            and _IMAGE.fullmatch(plan.base_image or "")
            and plan.build_environment_digest and plan.dependency_closure_digest)


def _verified_stock_outputs(plan, environment, retained):
    def digest(relative):
        actual, _ = _retained(retained, relative)
        if (retained / relative).stat().st_size == 0:
            raise ValueError(f"empty worker output: {relative}")
        return actual

    for index, probe in enumerate(environment["toolchain_probes"]):
        if digest(f"probes/{index:04d}.stdout") != probe["stdout_sha256"]:
            raise ValueError("worker observed toolchain probe mismatch")
    artifacts = {"executable_sha256": digest("executable"), "image_sha256": digest("image"),
                 "build_result_sha256": digest("build-result.json")}
    _, result = _retained(retained, "build-result.json", document=True)
    expected = {key: getattr(plan, key) for key in ("plan_digest", "source_tree_digest", "repository_commit",
                "build_environment_digest", "dependency_closure_digest")}
    expected.update({key: value for key, value in artifacts.items() if key != "build_result_sha256"})
    if not isinstance(result, dict) or any(result.get(key) != value for key, value in expected.items()):
        raise ValueError("worker build result identity/artifact mismatch")
    toolchain = json.dumps(dict(plan.toolchain), sort_keys=True, separators=(",", ":")).encode()
    observation = {"mode": "stock", "source": {
        "implementation": "amaru", "candidate_name": plan.candidate.candidate_name,
        "repository_commit": plan.repository_commit, "source_tree_digest": plan.source_tree_digest,
        "source_path": f"dwarf/testplease/amaru/{plan.candidate.candidate_name}"},
        **{key: getattr(plan, key) for key in ("plan_digest", "recipe_digest", "build_environment_digest", "dependency_closure_digest")},
        "toolchain_digest": "sha256:" + hashlib.sha256(toolchain).hexdigest(),
        "artifacts": {"executable_digest": "sha256:" + artifacts["executable_sha256"],
                      "binary_digest": "sha256:" + artifacts["executable_sha256"],
                      "image_digest": "sha256:" + artifacts["image_sha256"],
                      "build_result_digest": "sha256:" + artifacts["build_result_sha256"]}}
    return artifacts, observation


def _run_stock_worker(plan, output, backend, receipt, budget):
    resolution = resolve_build_environment(plan, trusted_root=candidate_builds.TRUSTED_ROOT)
    if (resolution.manifest_digest != plan.build_environment_digest
            or resolution.closure_digest != plan.dependency_closure_digest or resolution.blockers):
        raise ValueError("trusted worker environment drift")
    environment = json.loads(resolution.manifest_json)
    with tempfile.TemporaryDirectory(prefix="dwarf-stock-worker-") as workspace:
        workspace = Path(workspace).resolve()
        inputs, total, seen = [], 0, set()
        for row in plan.inputs:
            if row.origin != "trusted":
                continue
            if row.path in seen:
                continue
            seen.add(row.path)
            copied = workspace / "inputs" / row.path
            _retained(candidate_builds.TRUSTED_ROOT, row.path, row.sha256,
                      destination=copied, max_bytes=MAX_SOURCE_BYTES - total)
            total += copied.stat().st_size
            inputs.append((row.path, row.sha256, copied))
        worker_output = workspace / "outputs"
        worker_output.mkdir(mode=0o700)
        request = StockWorkerRequest(json.dumps(_identity(plan), sort_keys=True, separators=(",", ":")),
                                     resolution.manifest_json, tuple(inputs), plan.candidate, worker_output)
        worker, errors, exit_code = None, [], None
        started = time.monotonic()
        receipt["executed"] = True  # an attempted start remains an execution on failure
        try:
            worker = backend.start(request)
            exit_code = worker.wait(timeout_seconds=environment["isolation"]["timeout_seconds"])
            if time.monotonic() - started > environment["isolation"]["timeout_seconds"]:
                raise TimeoutError("worker deadline exceeded")
            if type(exit_code) is not int or exit_code != 0:
                raise RuntimeError("worker nonzero or invalid exit code")
        except Exception as exc:
            errors.append(str(exc)[:MAX_ERROR_DETAIL])
        finally:
            if worker is not None:
                try:
                    worker.cleanup()
                except Exception as exc:
                    errors.append("worker cleanup failed: " + str(exc)[:MAX_ERROR_DETAIL])
            # Build duration has its own trusted deadline. Do not spend the
            # source-freeze deadline while waiting for a disposable worker.
            budget.started += time.monotonic() - started
        retained = output / "worker"
        retained.mkdir(mode=0o700)
        try:
            capture_budget = _SourceBudget(max_bytes=min(MAX_WORKER_OUTPUT_BYTES, environment["isolation"]["output_bytes"]),
                                           max_entries=MAX_WORKER_OUTPUT_ENTRIES)
            _freeze_source(replace(plan.candidate, source_root=worker_output), retained, budget=capture_budget)
        except Exception as exc:
            errors.append("worker evidence retention failed: " + str(exc)[:MAX_ERROR_DETAIL])
        if errors:
            raise RuntimeError("; ".join(errors))
        # Recheck both originals and exactly the private inputs the backend saw.
        # No candidate-produced status/hash can override drift or bad probes.
        validate_candidate_build_plan(plan)
        for _, expected, path in inputs:
            _retained(path.parent, path.name, expected)
        return _verified_stock_outputs(plan, environment, retained)


def _execute_locked(plan, output, *, budget=None, backend=None):
    receipt = {"schema_version": "v1", "kind": "candidate-build-receipt", "status": "partial",
               "executed": False, "ready": False, "reason": "candidate-build-validation-pending",
               "source_copy_verified": False, "identity": _identity(plan), "artifacts": {},
               "observations": [], "blockers": []}
    path = output / "receipt.json"
    _save(path, receipt)
    try:
        result = None
        with ExitStack() as stack:
            if budget is None:
                budget = _SourceBudget()
                candidate = stack.enter_context(_snapshot(plan.candidate, budget))
                # source_root alone is excluded from the plan digest. Every
                # validation read must use the owned snapshot from here on.
                plan = replace(plan, candidate=candidate)
            budget.check()
            validate_candidate_build_plan(plan)
            budget.check()
            receipt.update(source_copy_verified=True, status="blocked",
                           reason="candidate-build-prerequisites-unresolved",
                           blockers=_blockers(plan), unresolved=list(plan.unresolved))
            if backend is not None and _stock_prerequisites(plan):
                result = _run_stock_worker(plan, output, backend, receipt, budget)
        # Source/input/output workspace cleanup is part of success, before its
        # observation is published. Context cleanup errors retain no success.
        if result is not None:
            artifacts, observation = result
            receipt.update(status="built", reason="candidate-stock-build-verified",
                           artifacts=artifacts, observations=[observation], blockers=[], unresolved=[])
    except (ValueError, OSError, RuntimeError) as exc:
        receipt.update(status="error", reason="candidate-build-validation-failed", detail=str(exc)[:MAX_ERROR_DETAIL])
    _save(path, receipt)
    return receipt


def execute_candidate_build(plan, *, directory, lock_path=HOST_LOCK, backend: StockWorkerBackend | None = None):
    """Retain a blocked/error receipt, or verify an explicitly injected worker.

    ``lock_path`` is an operator/test seam, never a field accepted from discovery
    or candidate metadata. Production CLI always shares the runtime host lock.
    """
    output = _owned_directory(directory)
    acquired = False
    try:
        with host_lock(lock_path):
            acquired = True
            return _execute_locked(plan, output, backend=backend)
    except (OSError, RuntimeError) as exc:
        if acquired:
            # A failed atomic receipt update leaves its previous partial state;
            # it must not be replaced with a misleading lock diagnostic.
            raise
        receipt = {"schema_version": "v1", "kind": "candidate-build-receipt", "status": "error",
                   "executed": False, "ready": False, "reason": "candidate-build-host-lock-unavailable",
                   "detail": str(exc), "source_copy_verified": False, "identity": _identity(plan),
                   "artifacts": {}, "observations": []}
        _save(output / "receipt.json", receipt)
        return receipt


def _discovered_candidates(document, repository, commit, trusted_revision):
    """Validate hosted metadata without opening any submitted source path."""
    if (not _COMMIT.fullmatch(commit) or not _COMMIT.fullmatch(trusted_revision)
            or document.get("repository_commit") != commit
            or document.get("trusted_revision") != trusted_revision
            or document.get("kind") != "security-campaign-discovery"
            or document.get("schema_version") != "v1"):
        raise ValueError("discovery commit, trusted revision or document identity mismatch")
    rows = document.get("candidates")
    if not isinstance(rows, list) or document.get("official_selection") != []:
        raise ValueError("only discovered source candidates are accepted; official selection requires review")
    if document.get("status") not in {"validated", "official-selection-review-required"}:
        raise ValueError("discovery was not validated")
    selected, seen = [], set()
    for row in rows:
        key = (row["implementation"], row["candidate_name"])
        if (key in seen or key[0] not in {"amaru", "cardano-node"}
                or not isinstance(key[1], str) or not _NAME.fullmatch(key[1])):
            raise ValueError("duplicate or unsafe discovered candidate")
        seen.add(key)
        expected_path = f"dwarf/testplease/{key[0]}/{key[1]}"
        source_digest = row.get("source_tree_digest")
        if (row.get("source_path") != expected_path or row.get("repository_commit") != commit
                or not isinstance(source_digest, str) or not _HEX.fullmatch(source_digest)):
            raise ValueError("discovered source path or frozen source identity mismatch")
        declared = [{field: item.get(field) for field in ("variant", "plan_digest", "recipe_digest")}
                    for item in row["build_plans"]]
        if ([item["variant"] for item in declared] != ["stock", "patched", "compiler-coverage"]
                or any(not isinstance(item[field], str) or not _HEX.fullmatch(item[field])
                       for item in declared for field in ("plan_digest", "recipe_digest"))):
            raise ValueError("invalid discovered plan identities")
        candidate = CandidateSnapshot(key[0], key[1], commit, source_digest, repository / expected_path)
        selected.append((candidate, declared))
    return selected


def preflight_discovery(discovery_path, *, source_repository, directory, expected_commit,
                        expected_trusted_revision, lock_path=HOST_LOCK):
    """Verify hosted discovery and retain every lane diagnostic atomically.

    The empty build-observations array is consumable by ci_security_campaign,
    where it establishes missing builds. Blocked receipts are never converted
    into successful build observations or runtime/publication authority.
    """
    output = _owned_directory(directory)
    summary = {"schema_version": "v1", "kind": "candidate-build-preflight", "status": "partial",
               "executed": False, "verified_artifacts": False, "input_integrity": "unverified",
               "reason": "candidate-build-validation-pending", "receipts": [],
               "blockers": ["campaign-signing-and-artifact-transfer-unconfigured"]}
    _save(output / "build-observations.json", [])
    _save(output / "blocked.json", summary)
    try:
        with host_lock(lock_path):
            path = Path(discovery_path)
            if path.is_symlink() or path.with_suffix(".sha256").is_symlink():
                raise ValueError("symlink discovery input")
            content = path.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            if path.with_suffix(".sha256").read_text().strip() != digest:
                raise ValueError("discovery digest mismatch")
            summary.update(input_integrity="sha256-matched", discovery_sha256=digest)
            selected = _discovered_candidates(json.loads(content), Path(source_repository),
                                               expected_commit, expected_trusted_revision)
            # One shared budget applies across every selected tree and its
            # copied-tree recognition/digests/plan checks, never reset per lane.
            budget = _SourceBudget()
            for candidate, declared in selected:
                with _snapshot(candidate, budget) as snapshot:
                    plans = plan_candidate_builds(snapshot)
                    budget.check()
                    expected = [{field: getattr(plan, field) for field in ("variant", "plan_digest", "recipe_digest")}
                                for plan in plans]
                    if declared != expected:
                        raise ValueError("discovered plans differ from current trusted build recipes")
                    for plan in plans:
                        lane = output / f"lane-{len(summary['receipts']):04d}"
                        lane.mkdir(mode=0o700)
                        receipt = _execute_locked(plan, lane, budget=budget)
                        summary["receipts"].append({"path": f"{lane.name}/receipt.json",
                            "sha256": hashlib.sha256((lane / "receipt.json").read_bytes()).hexdigest(),
                            "plan_digest": plan.plan_digest, "status": receipt["status"]})
                        _save(output / "blocked.json", summary)
            errors = any(item["status"] == "error" for item in summary["receipts"])
            summary.update(status="error" if errors else "blocked",
                           reason=("candidate-build-validation-failed" if errors else
                                   "candidate-build-prerequisites-unresolved" if selected else "official-selection-review-required"))
            if selected:
                summary["blockers"].insert(0, "candidate-build-prerequisites-unresolved")
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, AttributeError) as exc:
        summary.update(status="error", reason="candidate-build-discovery-validation-failed", detail=str(exc))
    _save(output / "blocked.json", summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--discovery", type=Path, required=True)
    parser.add_argument("--source-repository", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--expected-trusted-revision", required=True)
    args = parser.parse_args(argv)
    result = preflight_discovery(args.discovery, source_repository=args.source_repository,
                                directory=args.directory, expected_commit=args.expected_commit,
                                expected_trusted_revision=args.expected_trusted_revision)
    print(result["reason"])
    return 1  # No executable lane or verified campaign transfer exists yet.


if __name__ == "__main__":
    raise SystemExit(main())
