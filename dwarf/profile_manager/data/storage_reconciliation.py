"""Read-only storage inventory and explicit, provenance-preserving reconciliation.

This module deliberately does not reuse the historical bundle importer: legacy
run trees are forensic artifacts and must never be renamed or rewritten.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from profile_manager.config import StorageRoots


class StorageReconciliationError(RuntimeError):
    """A source cannot be reconciled without losing or conflating evidence."""


_STAGING_DIRECTORY = ".storage-reconciliation-staging"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _source_layout(source_root: str | os.PathLike[str]) -> tuple[Path, Path, Path]:
    root = Path(source_root).expanduser().resolve(strict=False)
    if root.name == "runs" and not (root / "runs").exists():
        return root.parent, root, root.parent / "bundles"
    return root, root / "runs", root / "bundles"


def _git_revision(root: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    revision = completed.stdout.strip()
    return revision if completed.returncode == 0 and len(revision) == 40 else None


def _tree_inventory(root: Path) -> tuple[list[dict[str, Any]], str]:
    """Return stable regular-file inventory and its digest; reject unsafe trees."""

    root_info = root.lstat()
    if not stat.S_ISDIR(root_info.st_mode) or root.is_symlink():
        raise ValueError("run tree root is not a regular directory")
    if root_info.st_mode & 0o555 == 0:
        raise PermissionError(f"unreadable directory: {root}")
    files: list[dict[str, Any]] = [
        {"path": ".", "type": "directory", "mode": stat.S_IMODE(root_info.st_mode)}
    ]
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name)
        except PermissionError as exc:
            raise PermissionError(f"unreadable directory: {directory}") from exc
        for entry in entries:
            path = Path(entry.path)
            relative = path.relative_to(root).as_posix()
            try:
                info = entry.stat(follow_symlinks=False)
            except PermissionError as exc:
                raise PermissionError(f"unreadable path: {relative}") from exc
            mode = info.st_mode
            if stat.S_ISLNK(mode):
                raise ValueError(f"symlink is not importable: {relative}")
            if stat.S_ISDIR(mode):
                if mode & 0o555 == 0:
                    raise PermissionError(f"unreadable directory: {relative}")
                files.append(
                    {"path": relative, "type": "directory", "mode": stat.S_IMODE(mode)}
                )
                pending.append(path)
                continue
            if not stat.S_ISREG(mode):
                raise ValueError(f"special file is not importable: {relative}")
            if mode & 0o444 == 0:
                raise PermissionError(f"unreadable file: {relative}")
            try:
                body = path.read_bytes()
            except PermissionError as exc:
                raise PermissionError(f"unreadable file: {relative}") from exc
            files.append(
                {
                    "path": relative,
                    "type": "file",
                    "size": len(body),
                    "sha256": _sha256(body),
                    "mode": stat.S_IMODE(mode),
                }
            )
    files.sort(key=lambda item: item["path"])
    return files, _sha256(_canonical(files))


def _read_json(path: Path, label: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, f"{label} JSON is unreadable or invalid: {exc}"
    if not isinstance(value, dict):
        return None, f"{label} must be a JSON object"
    return value, None


def _inspect_run(run_dir: Path) -> tuple[str, dict[str, Any]]:
    row: dict[str, Any] = {"run_id": run_dir.name, "path": str(run_dir), "reasons": []}
    manifest_path = run_dir / "manifest.json"
    chain_path = run_dir / "chain.json"
    missing = [name for name, path in (("manifest.json", manifest_path), ("chain.json", chain_path)) if not path.is_file()]
    if missing:
        row["reasons"] = [f"missing required file: {name}" for name in missing]
        return "incomplete", row
    try:
        files, digest = _tree_inventory(run_dir)
    except PermissionError as exc:
        row["reasons"] = [str(exc)]
        return "unreadable", row
    except (OSError, ValueError) as exc:
        row["reasons"] = [str(exc)]
        return "invalid", row

    manifest, manifest_error = _read_json(manifest_path, "manifest")
    chain, chain_error = _read_json(chain_path, "chain")
    reasons = [reason for reason in (manifest_error, chain_error) if reason]
    if manifest is not None and str(manifest.get("run_id") or "") != run_dir.name:
        reasons.append("manifest run_id does not match directory name")
    if chain is not None and str(chain.get("run_id") or "") != run_dir.name:
        reasons.append("chain run_id does not match directory name")
    if manifest is not None and chain is not None:
        actual = _sha256(_canonical(manifest))
        if chain.get("manifest_hash") != actual:
            reasons.append("chain manifest_hash does not match canonical manifest")
        previous = chain.get("prev_hash")
        if not isinstance(previous, str) or not previous:
            reasons.append("chain prev_hash must be a non-empty string")
    if reasons:
        row["reasons"] = reasons
        return "invalid", row

    assert manifest is not None and chain is not None
    row.update(
        {
            "digest": digest,
            "files": files,
            "manifest": manifest,
            "chain": chain,
            "chain_hash": _sha256(_canonical(chain)),
            "manifest_framework_commit": str((manifest.get("framework") or {}).get("commit") or "") or None,
        }
    )
    return "complete", row


def _public_run(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key not in {"manifest", "chain"}}


def _bundle_inventory(bundles_dir: Path) -> dict[str, Any]:
    result: dict[str, list[dict[str, Any]]] = {"complete": [], "invalid": [], "unreadable": []}
    if bundles_dir.exists() and not bundles_dir.is_dir():
        result["invalid"].append({"name": bundles_dir.name, "path": str(bundles_dir), "reason": "bundles path is not a directory"})
    elif bundles_dir.is_dir():
        for path in sorted(bundles_dir.iterdir(), key=lambda item: item.name):
            if path.name == _STAGING_DIRECTORY:
                continue
            try:
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or path.is_symlink():
                    result["invalid"].append({"name": path.name, "path": str(path), "reason": "bundle is not a regular file"})
                elif info.st_mode & 0o444 == 0:
                    result["unreadable"].append({"name": path.name, "path": str(path), "reason": "bundle is unreadable"})
                else:
                    body = path.read_bytes()
                    result["complete"].append({"name": path.name, "path": str(path), "size": len(body), "sha256": _sha256(body)})
            except PermissionError as exc:
                result["unreadable"].append({"name": path.name, "path": str(path), "reason": str(exc)})
            except OSError as exc:
                result["invalid"].append({"name": path.name, "path": str(path), "reason": str(exc)})
    return {**result, "counts": {key: len(value) for key, value in result.items()}}


def _inventory_paths(*, root: Path, runs_dir: Path, bundles_dir: Path) -> dict[str, Any]:
    classified: dict[str, list[dict[str, Any]]] = {
        "complete": [], "incomplete": [], "invalid": [], "unreadable": []
    }
    run_directory_count = 0
    manifest_present = 0
    chain_present = 0
    if runs_dir.exists() and not runs_dir.is_dir():
        classified["invalid"].append({"run_id": runs_dir.name, "path": str(runs_dir), "reasons": ["runs path is not a directory"]})
    elif runs_dir.is_dir():
        for child in sorted(runs_dir.iterdir(), key=lambda item: item.name):
            if child.name == _STAGING_DIRECTORY:
                continue
            try:
                mode = child.lstat().st_mode
            except OSError as exc:
                classified["unreadable"].append({"run_id": child.name, "path": str(child), "reasons": [str(exc)]})
                continue
            if child.is_symlink() or not stat.S_ISDIR(mode):
                classified["invalid"].append({"run_id": child.name, "path": str(child), "reasons": ["run entry is not a regular directory"]})
                continue
            run_directory_count += 1
            manifest_present += int((child / "manifest.json").is_file())
            chain_present += int((child / "chain.json").is_file())
            category, row = _inspect_run(child)
            classified[category].append(row)

    # A locally valid chain entry is complete only when its full predecessor
    # closure reaches genesis. Iteration also rejects cycles and descendants
    # of a broken predecessor rather than validating just one edge.
    candidates = list(classified["complete"])
    valid_hashes: set[str] = set()
    remaining = list(candidates)
    while remaining:
        accepted = [
            row for row in remaining
            if row["chain"]["prev_hash"] == "genesis"
            or row["chain"]["prev_hash"] in valid_hashes
        ]
        if not accepted:
            break
        for row in accepted:
            valid_hashes.add(row["chain_hash"])
            remaining.remove(row)
    broken = remaining
    if broken:
        classified["complete"] = [row for row in candidates if row not in broken]
        retained_hashes = {row["chain_hash"] for row in classified["complete"]}
        for row in broken:
            previous = row["chain"]["prev_hash"]
            row["reasons"] = [
                f"predecessor {previous} is not retained in a valid source chain"
                if previous not in retained_hashes
                else "chain closure is cyclic"
            ]
        classified["invalid"].extend(broken)

    public: dict[str, Any] = {
        key: [_public_run(row) for row in value] for key, value in classified.items()
    }
    public["counts"] = {
        **{key: len(value) for key, value in classified.items()},
        "run_directories": run_directory_count,
        "manifest_present": manifest_present,
        "chain_present": chain_present,
        "importable_complete": len(classified["complete"]),
    }
    return {
        "source_root": str(root),
        "runs_dir": str(runs_dir),
        "bundles_dir": str(bundles_dir),
        "source_revision": _git_revision(root),
        "runs": public,
        "bundles": _bundle_inventory(bundles_dir),
        "_run_records": classified,
    }


def inventory_storage(source_root: str | os.PathLike[str]) -> dict[str, Any]:
    root, runs_dir, bundles_dir = _source_layout(source_root)
    return _inventory_paths(root=root, runs_dir=runs_dir, bundles_dir=bundles_dir)


def inventory_resolved_storage(roots: StorageRoots) -> dict[str, Any]:
    """Inventory exact configured roots, including intentionally disjoint trios."""

    root = roots.runtime_root or roots.state.parent
    return _inventory_paths(root=root, runs_dir=roots.runs, bundles_dir=roots.bundles)


def public_inventory(inventory: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in inventory.items() if not key.startswith("_")}


def _ordered_closure(
    selected: Iterable[str],
    *,
    source_by_id: dict[str, dict[str, Any]],
    source_by_hash: dict[str, dict[str, Any]],
    destination_by_hash: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    ordered: list[dict[str, Any]] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(row: dict[str, Any]) -> None:
        run_id = row["run_id"]
        if run_id in visited:
            return
        if run_id in visiting:
            raise StorageReconciliationError(f"chain cycle includes {run_id}")
        visiting.add(run_id)
        previous = row["chain"]["prev_hash"]
        if previous != "genesis" and previous not in destination_by_hash:
            predecessor = source_by_hash.get(previous)
            if predecessor is None:
                raise StorageReconciliationError(
                    f"predecessor {previous} required by {run_id} is unavailable"
                )
            visit(predecessor)
        visiting.remove(run_id)
        visited.add(run_id)
        ordered.append(row)

    for run_id in selected:
        row = source_by_id.get(run_id)
        if row is None:
            raise StorageReconciliationError(f"run {run_id} is not a complete importable source run")
        visit(row)
    return ordered


def _plan(
    source_root: str | os.PathLike[str],
    *,
    destination: StorageRoots,
    run_ids: Iterable[str] | None,
    include_runs: bool,
    include_bundles: bool,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    source = inventory_storage(source_root)
    destination_inventory = inventory_resolved_storage(destination)
    source_records = source["_run_records"]
    destination_records = destination_inventory["_run_records"]
    source_complete = {row["run_id"]: row for row in source_records["complete"]}
    destination_complete = {row["run_id"]: row for row in destination_records["complete"]}
    source_hashes = {row["chain_hash"]: row for row in source_records["complete"]}
    destination_hashes = {row["chain_hash"]: row for row in destination_records["complete"]}

    selected = list(dict.fromkeys(run_ids if run_ids is not None else sorted(source_complete))) if include_runs else []
    noncomplete = {
        row["run_id"]: row
        for category in ("incomplete", "invalid", "unreadable")
        for row in source_records[category]
    }
    for run_id in selected:
        if run_id not in source_complete and run_id in noncomplete:
            reasons = "; ".join(noncomplete[run_id].get("reasons") or [])
            raise StorageReconciliationError(
                f"run {run_id} is not a complete importable source run: {reasons}"
            )
    ordered = _ordered_closure(
        selected,
        source_by_id=source_complete,
        source_by_hash=source_hashes,
        destination_by_hash=destination_hashes,
    )
    run_plan: dict[str, Any] = {
        "mode": "include" if include_runs else "inventory-only",
        "import": [], "duplicate": [], "collision": [], "available": [],
    }
    for row in ordered:
        existing = destination_complete.get(row["run_id"])
        if existing is None:
            # A same-name invalid/incomplete destination is also a collision.
            target = destination.runs / row["run_id"]
            if target.exists():
                run_plan["collision"].append(row["run_id"])
            else:
                (run_plan["import"] if include_runs else run_plan["available"]).append(row["run_id"])
        elif existing["digest"] == row["digest"]:
            run_plan["duplicate"].append(row["run_id"])
        else:
            run_plan["collision"].append(row["run_id"])

    source_bundles = {row["name"]: row for row in source["bundles"]["complete"]}
    destination_bundles = {row["name"]: row for row in destination_inventory["bundles"]["complete"]}
    bundle_plan: dict[str, Any] = {
        "mode": "include" if include_bundles else "inventory-only",
        "import": [], "duplicate": [], "collision": [], "available": [],
    }
    for name, row in sorted(source_bundles.items()):
        existing = destination_bundles.get(name)
        if existing is None:
            (bundle_plan["import"] if include_bundles else bundle_plan["available"]).append(name)
        elif existing["sha256"] == row["sha256"]:
            bundle_plan["duplicate"].append(name)
        else:
            bundle_plan["collision"].append(name)

    blockers: list[str] = []
    if include_runs and run_ids is None:
        for category in ("invalid", "unreadable"):
            for row in source_records[category]:
                blockers.append(f"{category} run {row['run_id']}")
    if include_bundles:
        for category in ("invalid", "unreadable"):
            for row in source["bundles"][category]:
                blockers.append(f"{category} bundle {row['name']}")

    plan = {
        "schema_version": "dwarf-storage-reconciliation-v1",
        "source_root": source["source_root"],
        "source_revision": source["source_revision"],
        "destination": {
            "runs": str(destination.runs),
            "state": str(destination.state),
            "bundles": str(destination.bundles),
        },
        "runs": run_plan,
        "bundles": bundle_plan,
        "safety_blockers": blockers,
        "source_inventory": public_inventory(source),
        "destination_inventory": public_inventory(destination_inventory),
        "_ordered_records": ordered,
        "_source_bundle_records": source_bundles,
    }
    return plan, source, destination_inventory


def _public_plan(plan: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in plan.items() if not key.startswith("_")}


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def reconcile_storage(
    source_root: str | os.PathLike[str],
    *,
    destination: StorageRoots,
    run_ids: Iterable[str] | None = None,
    apply: bool = False,
    include_runs: bool = True,
    include_bundles: bool = False,
    expected_plan_digest: str | None = None,
) -> dict[str, Any]:
    """Plan or apply a reconciliation. The default is immutable dry-run."""

    plan, _source_inventory, _destination_inventory = _plan(
        source_root,
        destination=destination,
        run_ids=run_ids,
        include_runs=include_runs,
        include_bundles=include_bundles,
    )
    result = _public_plan(plan)
    plan_digest = _sha256(_canonical(result))
    result["plan_digest"] = plan_digest
    result.update({"mode": "apply" if apply else "dry-run", "status": "planned", "receipt_path": None})
    if not apply:
        return result
    if expected_plan_digest is not None and expected_plan_digest != plan_digest:
        raise StorageReconciliationError(
            f"stale plan digest: expected {expected_plan_digest}, current {plan_digest}"
        )
    if plan["safety_blockers"]:
        raise StorageReconciliationError(
            "source contains invalid or unreadable selected content: "
            + ", ".join(plan["safety_blockers"])
        )
    if plan["runs"]["collision"]:
        raise StorageReconciliationError(
            "run collision: same ID has a different full-tree digest: "
            + ", ".join(plan["runs"]["collision"])
        )
    if include_bundles and plan["bundles"]["collision"]:
        raise StorageReconciliationError(
            "bundle collision: same name has different content: "
            + ", ".join(plan["bundles"]["collision"])
        )

    imports = set(plan["runs"]["import"])
    bundle_imports = set(plan["bundles"]["import"])
    if not imports and not bundle_imports:
        result["status"] = "noop"
        return result

    destination.runs.mkdir(parents=True, exist_ok=True)
    destination.state.mkdir(parents=True, exist_ok=True)
    if bundle_imports:
        destination.bundles.mkdir(parents=True, exist_ok=True)
    # Runs, state, and bundles are deliberately separate container volumes.
    # Stage each artifact on the same filesystem as its final target so the
    # final os.replace remains atomic instead of failing with EXDEV.
    transaction_id = uuid.uuid4().hex
    run_staging_parent = destination.runs / _STAGING_DIRECTORY
    bundle_staging_parent = destination.bundles / _STAGING_DIRECTORY
    run_stage: Path | None = None
    bundle_stage: Path | None = None
    if imports:
        run_staging_parent.mkdir(parents=True, exist_ok=True)
        run_stage = Path(tempfile.mkdtemp(prefix=f"{transaction_id}-", dir=run_staging_parent))
    if bundle_imports:
        bundle_staging_parent.mkdir(parents=True, exist_ok=True)
        bundle_stage = Path(
            tempfile.mkdtemp(prefix=f"{transaction_id}-", dir=bundle_staging_parent)
        )
    receipt_runs: list[dict[str, Any]] = []
    receipt_bundles: list[dict[str, Any]] = []
    staged_run_items: list[tuple[Path, Path, dict[str, Any]]] = []
    staged_bundle_items: list[tuple[Path, Path, dict[str, Any]]] = []
    transaction_path: Path | None = None
    transaction: dict[str, Any] | None = None
    try:
        for row in plan["_ordered_records"]:
            if row["run_id"] not in imports:
                continue
            assert run_stage is not None
            source_dir = Path(row["path"])
            staged = run_stage / row["run_id"]
            shutil.copytree(source_dir, staged, copy_function=shutil.copy2)
            files, digest = _tree_inventory(staged)
            if digest != row["digest"] or files != row["files"]:
                raise StorageReconciliationError(
                    f"staged run {row['run_id']} differs from its source"
                )
            target = destination.runs / row["run_id"]
            if target.exists():
                raise StorageReconciliationError(f"destination changed during reconciliation: {row['run_id']}")
            record = {
                "run_id": row["run_id"],
                "classification": "imported",
                "source_digest": row["digest"],
                "destination_digest": digest,
                "file_inventory": files,
                "manifest_framework_commit": row["manifest_framework_commit"],
            }
            receipt_runs.append(record)
            staged_run_items.append((staged, target, record))

        for name in sorted(bundle_imports):
            assert bundle_stage is not None
            row = plan["_source_bundle_records"][name]
            staged = bundle_stage / name
            shutil.copy2(row["path"], staged)
            body = staged.read_bytes()
            if _sha256(body) != row["sha256"]:
                raise StorageReconciliationError(f"staged bundle {name} differs from its source")
            target = destination.bundles / name
            if target.exists():
                raise StorageReconciliationError(f"destination changed during reconciliation: {name}")
            record = {
                "name": name,
                "classification": "imported",
                "source_digest": row["sha256"],
                "destination_digest": _sha256(body),
                "size": row["size"],
            }
            receipt_bundles.append(record)
            staged_bundle_items.append((staged, target, record))

        # Persist the complete provenance intent before the first atomic move.
        # If the process is interrupted between moves, this journal identifies
        # every verified source tree and every destination already committed.
        transaction = {
            "schema_version": "dwarf-storage-reconciliation-transaction-v1",
            "status": "applying",
            "started_at": _utc_now(),
            "plan_digest": plan_digest,
            "source_root": plan["source_root"],
            "source_checkout_revision": plan["source_revision"],
            "destination": plan["destination"],
            "classification": {"runs": plan["runs"], "bundles": plan["bundles"]},
            "runs": receipt_runs,
            "bundles": receipt_bundles,
            "committed_runs": [],
            "committed_bundles": [],
        }
        transactions = destination.state / "storage-reconciliation" / "transactions"
        transaction_path = transactions / f"{transaction_id}.json"
        _write_json_atomic(transaction_path, transaction)

        for staged, target, record in staged_run_items:
            os.replace(staged, target)
            destination_files, destination_digest = _tree_inventory(target)
            if destination_digest != record["source_digest"] or destination_files != record["file_inventory"]:
                raise StorageReconciliationError(
                    f"committed run {record['run_id']} differs from its source"
                )
            transaction["committed_runs"].append(record["run_id"])
            _write_json_atomic(transaction_path, transaction)
        for staged, target, record in staged_bundle_items:
            os.replace(staged, target)
            if _sha256(target.read_bytes()) != record["source_digest"]:
                raise StorageReconciliationError(
                    f"committed bundle {record['name']} differs from its source"
                )
            transaction["committed_bundles"].append(record["name"])
            _write_json_atomic(transaction_path, transaction)
    except Exception as exc:
        if transaction is not None and transaction_path is not None:
            transaction["status"] = "failed"
            transaction["failed_at"] = _utc_now()
            transaction["error"] = f"{type(exc).__name__}: {exc}"
            _write_json_atomic(transaction_path, transaction)
        raise
    finally:
        for stage, parent in (
            (run_stage, run_staging_parent),
            (bundle_stage, bundle_staging_parent),
        ):
            if stage is not None:
                shutil.rmtree(stage, ignore_errors=True)
            try:
                parent.rmdir()
            except OSError:
                pass

    imported_at = _utc_now()
    receipt = {
        "schema_version": "dwarf-storage-reconciliation-receipt-v1",
        "imported_at": imported_at,
        "source_root": plan["source_root"],
        "source_checkout_revision": plan["source_revision"],
        "destination": plan["destination"],
        "classification": {"runs": plan["runs"], "bundles": plan["bundles"]},
        "runs": receipt_runs,
        "bundles": receipt_bundles,
    }
    receipts = destination.state / "storage-reconciliation" / "receipts"
    receipts.mkdir(parents=True, exist_ok=True)
    receipt_path = receipts / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')}-{uuid.uuid4().hex}.json"
    _write_json_atomic(receipt_path, receipt)
    assert transaction is not None and transaction_path is not None
    transaction["status"] = "complete"
    transaction["completed_at"] = imported_at
    transaction["receipt_path"] = str(receipt_path)
    _write_json_atomic(transaction_path, transaction)
    result.update({"status": "applied", "receipt_path": str(receipt_path), "imported_at": imported_at})
    return result
