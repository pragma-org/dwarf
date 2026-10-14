#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DWARF_ROOT = SCRIPT_DIR.parent
if str(DWARF_ROOT) not in sys.path:
    sys.path.insert(0, str(DWARF_ROOT))

from profile_manager import forensic  # noqa: E402
from scripts import runtime_bundle_sign  # noqa: E402


MAX_ARCHIVE_MEMBERS = 100_000
MAX_ARCHIVE_FILE_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_EXPANDED_BYTES = 20 * 1024 * 1024 * 1024


def _recompute_manifest_hash(manifest_path: Path) -> str:
    manifest_obj = json.loads(manifest_path.read_text(encoding="utf-8"))
    return forensic._sha256_hex(forensic._canonical_json(manifest_obj))


def _tree_content_manifest(root: Path) -> dict[str, dict[str, object]]:
    manifest: dict[str, dict[str, object]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.is_symlink():
            raise ValueError(f"unsupported imported bundle entry: {relative}")
        # Signed exports deliberately exclude their own generated download
        # pair.  Ignore only that derived subtree when deciding whether an
        # import is an exact idempotent replay of an existing run.
        if relative == Path("outputs/export") or Path("outputs/export") in relative.parents:
            continue
        relpath = str(relative)
        # The export format records files and recreates their parent
        # directories during extraction; empty directory metadata is not part
        # of the signed evidence identity.
        if path.is_dir():
            continue
        if path.is_file():
            manifest[relpath] = {
                "type": "file",
                "size": path.stat().st_size,
                "sha256": runtime_bundle_sign._sha256_file(path),
            }
        else:
            raise ValueError(f"unsupported imported bundle entry: {relpath}")
    return manifest


def _safe_extract(handle: tarfile.TarFile, destination: Path) -> None:
    root = destination.resolve()
    members = handle.getmembers()
    if len(members) > MAX_ARCHIVE_MEMBERS:
        raise ValueError(
            f"archive member limit exceeded: {len(members)} > {MAX_ARCHIVE_MEMBERS}"
        )
    expanded_bytes = 0
    for member in members:
        member_path = Path(member.name)
        if (
            member_path.is_absolute()
            or ".." in member_path.parts
            or member.issym()
            or member.islnk()
            or not (member.isfile() or member.isdir())
        ):
            raise ValueError(f"unsafe archive member: {member.name}")
        if member.isfile():
            if member.size < 0 or member.size > MAX_ARCHIVE_FILE_BYTES:
                raise ValueError(
                    "archive file size limit exceeded: "
                    f"{member.name} is {member.size} bytes"
                )
            expanded_bytes += member.size
            if expanded_bytes > MAX_ARCHIVE_EXPANDED_BYTES:
                raise ValueError(
                    "archive expanded size limit exceeded: "
                    f"{expanded_bytes} > {MAX_ARCHIVE_EXPANDED_BYTES}"
                )
        if not (root / member_path).resolve().is_relative_to(root):
            raise ValueError(f"archive member escapes extraction root: {member.name}")
    handle.extractall(destination, members=members)


def _verify_export_signature(tarball_path: Path, signature_path: Path) -> dict:
    if not signature_path.is_file():
        return {
            "verdict": "unsigned",
            "manifest_sha256_recomputed": None,
            "manifest_sha256_signed": None,
            "reason": "missing_signature",
        }
    with tempfile.TemporaryDirectory() as tmpdir:
        staging = Path(tmpdir) / "signature-input"
        staging.mkdir()
        shutil.copy2(tarball_path, staging / tarball_path.name)
        try:
            expected_manifest = {
                tarball_path.name: runtime_bundle_sign._sha256_file(tarball_path)
            }
            verification = runtime_bundle_sign.verify_signature(
                run_dir=staging,
                signature_path=signature_path,
                expected_manifest=expected_manifest,
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return {
                "verdict": "tampered",
                "manifest_sha256_recomputed": None,
                "manifest_sha256_signed": None,
                "reason": f"invalid_signature_record:{type(exc).__name__}",
            }
        return verification


def _import_staged_bundle(
    *,
    staged_tarball_path: Path,
    staged_signature_path: Path,
    tarball_path: Path,
    signature_path: Path,
    runs_dir: Path,
) -> dict:
    verification = _verify_export_signature(
        staged_tarball_path, staged_signature_path
    )
    if verification["verdict"] != "verified":
        return {
            "verdict": verification["verdict"],
            "signature_verdict": verification["verdict"],
            "requested_run_id": None,
            "imported_run_id": None,
            "tarball_path": str(tarball_path),
            "signature_path": str(signature_path),
            **{
                key: value
                for key, value in verification.items()
                if key != "verdict"
            },
        }
    runs_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmpdir:
        extract_root = Path(tmpdir) / "extract"
        extract_root.mkdir(parents=True, exist_ok=True)
        with tarfile.open(staged_tarball_path, "r:gz") as handle:
            _safe_extract(handle, extract_root)
        bundle_roots = sorted(path for path in extract_root.iterdir() if path.is_dir())
        if len(bundle_roots) != 1:
            raise ValueError("archive must contain exactly one bundle root directory")
        bundle_root = bundle_roots[0]
        manifest_path = bundle_root / "manifest.json"
        chain_path = bundle_root / "chain.json"
        if not manifest_path.exists() or not chain_path.exists():
            raise ValueError("bundle is missing manifest.json or chain.json")
        if (
            verification.get("signature_schema_version") == "v2"
            and verification.get("signed_target_run_id") != bundle_root.name
        ):
            return {
                "verdict": "tampered",
                "signature_verdict": "verified",
                "requested_run_id": bundle_root.name,
                "imported_run_id": None,
                "reason": "signature_target_identity_mismatch",
                "tarball_path": str(tarball_path),
                "signature_path": str(signature_path),
            }
        chain_entry = json.loads(chain_path.read_text(encoding="utf-8"))
        manifest_entry = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            manifest_entry.get("run_id") != bundle_root.name
            or chain_entry.get("run_id") != bundle_root.name
        ):
            return {
                "verdict": "tampered",
                "signature_verdict": "verified",
                "requested_run_id": bundle_root.name,
                "imported_run_id": None,
                "reason": "bundle_identity_mismatch",
                "tarball_path": str(tarball_path),
                "signature_path": str(signature_path),
            }
        recomputed = _recompute_manifest_hash(manifest_path)
        if chain_entry.get("manifest_hash") != recomputed:
            return {
                "verdict": "tampered",
                "requested_run_id": bundle_root.name,
                "imported_run_id": None,
                "manifest_sha256_recomputed": recomputed,
                "manifest_sha256_signed": chain_entry.get("manifest_hash"),
                "tarball_path": str(tarball_path),
            }
        imported_run_id = bundle_root.name
        destination = runs_dir / imported_run_id
        if destination.exists():
            if destination.is_symlink():
                return {
                    "verdict": "collision",
                    "signature_verdict": "verified",
                    "requested_run_id": bundle_root.name,
                    "imported_run_id": None,
                    "reason": "existing_destination_unsupported_entry",
                    "tarball_path": str(tarball_path),
                    "signature_path": str(signature_path),
                    "destination_dir": str(destination),
                }
            try:
                exact_existing = destination.is_dir() and _tree_content_manifest(
                    destination
                ) == _tree_content_manifest(bundle_root)
            except ValueError:
                return {
                    "verdict": "collision",
                    "signature_verdict": "verified",
                    "requested_run_id": bundle_root.name,
                    "imported_run_id": None,
                    "reason": "existing_destination_unsupported_entry",
                    "tarball_path": str(tarball_path),
                    "signature_path": str(signature_path),
                    "destination_dir": str(destination),
                }
            if exact_existing:
                return {
                    "verdict": "imported",
                    "signature_verdict": "verified",
                    "signing_public_key_hex": verification.get("signing_public_key_hex"),
                    "signing_actor": verification.get("signing_actor"),
                    "operator_warning": verification.get("operator_warning"),
                    "requested_run_id": bundle_root.name,
                    "imported_run_id": imported_run_id,
                    "manifest_sha256_recomputed": recomputed,
                    "manifest_sha256_signed": chain_entry.get("manifest_hash"),
                    "tarball_path": str(tarball_path),
                    "signature_path": str(signature_path),
                    "destination_dir": str(destination),
                    "idempotent": True,
                }
            return {
                "verdict": "collision",
                "signature_verdict": "verified",
                "requested_run_id": bundle_root.name,
                "imported_run_id": None,
                "tarball_path": str(tarball_path),
                "signature_path": str(signature_path),
                "destination_dir": str(destination),
            }
        staging_parent = Path(
            tempfile.mkdtemp(prefix=".dwarf-bundle-import-", dir=runs_dir)
        )
        try:
            staged_destination = staging_parent / imported_run_id
            shutil.copytree(bundle_root, staged_destination)
            try:
                staged_destination.rename(destination)
            except OSError:
                try:
                    exact_racing_destination = (
                        not destination.is_symlink()
                        and destination.is_dir()
                        and _tree_content_manifest(destination)
                        == _tree_content_manifest(staged_destination)
                    )
                except ValueError:
                    exact_racing_destination = False
                if not exact_racing_destination:
                    raise
        finally:
            shutil.rmtree(staging_parent, ignore_errors=True)
        return {
            "verdict": "imported",
            "signature_verdict": "verified",
            "signing_public_key_hex": verification.get("signing_public_key_hex"),
            "signing_actor": verification.get("signing_actor"),
            "operator_warning": verification.get("operator_warning"),
            "requested_run_id": bundle_root.name,
            "imported_run_id": imported_run_id,
            "manifest_sha256_recomputed": recomputed,
            "manifest_sha256_signed": chain_entry.get("manifest_hash"),
            "tarball_path": str(tarball_path),
            "signature_path": str(signature_path),
            "destination_dir": str(destination),
            "idempotent": False,
        }


def import_bundle(
    *, tarball_path: Path, signature_path: Path, runs_dir: Path
) -> dict:
    tarball_path = Path(tarball_path)
    signature_path = Path(signature_path)
    with tempfile.TemporaryDirectory(prefix="dwarf-bundle-input-") as tmpdir:
        snapshot_root = Path(tmpdir)
        archive_root = snapshot_root / "archive"
        signature_root = snapshot_root / "signature"
        archive_root.mkdir(mode=0o700)
        signature_root.mkdir(mode=0o700)
        staged_tarball_path = archive_root / tarball_path.name
        staged_signature_path = signature_root / signature_path.name
        shutil.copyfile(tarball_path, staged_tarball_path)
        if signature_path.is_file():
            shutil.copyfile(signature_path, staged_signature_path)
        return _import_staged_bundle(
            staged_tarball_path=staged_tarball_path,
            staged_signature_path=staged_signature_path,
            tarball_path=tarball_path,
            signature_path=signature_path,
            runs_dir=Path(runs_dir),
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import a Dwarf bundle archive into runs/")
    parser.add_argument("tarball_path")
    parser.add_argument("--signature-path", required=True)
    parser.add_argument("--runs-dir", required=True)
    args = parser.parse_args(argv)
    result = import_bundle(
        tarball_path=Path(args.tarball_path),
        signature_path=Path(args.signature_path),
        runs_dir=Path(args.runs_dir),
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["verdict"] == "imported" else 1


if __name__ == "__main__":
    raise SystemExit(main())
