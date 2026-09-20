#!/usr/bin/env python3
"""Build the revision-locked Amaru production CBOR conformance adapter."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts import build_amaru_measurement_target as exact_builder


REVISION = "b159172f25a9c389f82f20bca4f15e3032791638"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "targets" / "amaru" / "conformance-adapters" / REVISION / "manifest.json"
BuildContractError = exact_builder.BuildContractError


def _digest_rows(rows: list[tuple[str, str]]) -> str:
    payload = "".join(f"{digest}  {path}\n" for path, digest in rows)
    return hashlib.sha256(payload.encode()).hexdigest()


def _manifest_revision(manifest_path: Path, manifest: dict[str, Any]) -> str:
    return exact_builder.manifest_revision(manifest_path, manifest)


def _verify_files(manifest_path: Path, manifest: dict[str, Any]) -> tuple[list[dict[str, str]], str]:
    verified = []
    rows = []
    for item in manifest.get("adapter_files") or []:
        relative = exact_builder._safe_relative_path(str(item.get("path") or ""), field="adapter_files.path")
        path = manifest_path.parent / relative
        actual = exact_builder.sha256_file(path)
        if actual != item.get("sha256"):
            raise BuildContractError(f"adapter file digest mismatch: {relative}")
        verified.append({"path": relative.as_posix(), "sha256": actual})
        rows.append((relative.as_posix(), actual))
    if not verified:
        raise BuildContractError("adapter manifest has no files")
    return verified, _digest_rows(rows)


def _tool_version(command: list[str]) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as error:
        raise BuildContractError(f"tool is unavailable: {command[0]}") from error
    if result.returncode != 0:
        raise BuildContractError(f"tool is unavailable: {command[0]}")
    return (result.stdout or result.stderr).strip()


def build_adapter(
    *,
    manifest_path: Path,
    source_repository: str,
    output_dir: Path,
    registry_root: Path,
    cargo_binary: Path,
    rustc_binary: Path,
) -> dict[str, Any]:
    if output_dir.exists():
        raise BuildContractError(f"output directory already exists: {output_dir}")
    manifest = exact_builder.load_manifest(manifest_path)
    if manifest.get("kind") != "production-cbor-conformance":
        raise BuildContractError("manifest kind is not production CBOR conformance")
    revision = _manifest_revision(manifest_path, manifest)
    files, adapter_set_sha256 = _verify_files(manifest_path, manifest)
    if adapter_set_sha256 != manifest.get("adapter_set_sha256"):
        raise BuildContractError("adapter-set digest mismatch")
    output_dir.mkdir(parents=True)
    source = exact_builder.clone_exact_source(
        source_repository, output_dir / "work" / "source", revision
    )
    stage = source / "dwarf-conformance-adapters" / "amaru-cbor"
    for item in files:
        relative = Path(item["path"]).relative_to("cbor")
        destination = stage / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(manifest_path.parent / item["path"], destination)
    environment = dict(os.environ)
    environment.update({str(k): str(v) for k, v in manifest["build"]["environment"].items()})
    command = [str(part) for part in manifest["build"]["command"]]
    command[0] = str(cargo_binary)
    log = output_dir / "logs" / "cargo-build.log"
    exact_builder.run_logged(command, cwd=source, log_path=log, env=environment)
    executable = source / "dwarf-conformance-adapters" / "amaru-cbor" / "target" / "release" / "dwarf-amaru-cbor-conformance"
    if not executable.is_file():
        raise BuildContractError("Amaru CBOR adapter executable was not produced")
    evidence = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "kind": manifest["kind"],
        "implementation": "amaru",
        "source_revision": revision,
        "source_release": manifest["source"]["release"],
        "measurement_boundary": manifest["measurement_boundary"],
        "production_entrypoint": manifest["production_entrypoint"],
        "toolchain": {
            "requested": manifest["toolchain"],
            "rustc": _tool_version([str(rustc_binary), f"+{manifest['toolchain']}", "--version"]),
            "cargo": _tool_version([str(cargo_binary), f"+{manifest['toolchain']}", "--version"]),
        },
        "adapter_set_sha256": adapter_set_sha256,
        "adapter_files": files,
        "build_command": command,
        "build_log_sha256": exact_builder.sha256_file(log),
        "executable": str(executable.resolve()),
        "executable_sha256": exact_builder.sha256_file(executable),
    }
    evidence_path = output_dir / "evidence" / "build-result.json"
    evidence_path.parent.mkdir(parents=True)
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    build_result_sha256 = exact_builder.sha256_file(evidence_path)
    record = {**evidence, "build_result_sha256": build_result_sha256}
    record_path = registry_root / "amaru" / "conformance" / revision / f"{adapter_set_sha256}.json"
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return {**record, "record_path": str(record_path)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--source-repository", default="https://github.com/pragma-org/amaru.git")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--cargo", type=Path, default=Path("/home/nigel/.cargo/bin/cargo")
    )
    parser.add_argument(
        "--rustc", type=Path, default=Path("/home/nigel/.cargo/bin/rustc")
    )
    parser.add_argument(
        "--target-registry",
        type=Path,
        default=exact_builder.default_target_registry_root(),
    )
    args = parser.parse_args(argv)
    try:
        result = build_adapter(
            manifest_path=args.manifest.resolve(),
            source_repository=args.source_repository,
            output_dir=args.output_dir.resolve(),
            registry_root=args.target_registry.resolve(),
            cargo_binary=args.cargo.absolute(),
            rustc_binary=args.rustc.absolute(),
        )
    except BuildContractError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
