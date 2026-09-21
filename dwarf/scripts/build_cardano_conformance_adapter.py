#!/usr/bin/env python3
"""Build the revision-locked Cardano production CBOR conformance adapter."""
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
from typing import Any, Mapping

from scripts import build_cardano_measurement_target as exact_builder


REVISION = "fef83fed01d7926f3de83b3b917be5a4a48768b5"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = (
    ROOT / "targets" / "cardano-node" / "conformance-adapters" / REVISION / "manifest.json"
)
BuildContractError = exact_builder.BuildContractError
load_manifest = exact_builder.load_manifest


def _digest_rows(rows: list[tuple[str, str]]) -> str:
    payload = "".join(f"{digest}  {path}\n" for path, digest in rows)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def verify_adapter_set(root: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    entries = manifest.get("adapter_files")
    if not isinstance(entries, list) or not entries:
        raise BuildContractError("adapter manifest has no files")
    files = []
    rows = []
    for item in entries:
        relative = exact_builder._relative(
            str(item.get("path") or ""), field="adapter file path"
        )
        path = root / relative
        if not path.is_file():
            raise BuildContractError(f"missing adapter file: {relative}")
        actual = exact_builder.sha256_file(path)
        if actual != item.get("sha256"):
            raise BuildContractError(f"adapter file digest mismatch: {relative}")
        row = {"path": relative.as_posix(), "sha256": actual}
        files.append(row)
        rows.append((row["path"], actual))
    digest = _digest_rows(rows)
    if digest != manifest.get("adapter_set_sha256"):
        raise BuildContractError("adapter-set digest mismatch")
    return {"adapter_set_sha256": digest, "files": files}


def copy_adapter_files(
    source_root: Path, destination_root: Path, manifest: Mapping[str, Any]
) -> None:
    verified = verify_adapter_set(source_root, manifest)
    for item in verified["files"]:
        relative = Path(item["path"])
        destination = destination_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_root / relative, destination)


def _tool_version(binary: Path) -> str:
    result = subprocess.run(
        [str(binary), "--numeric-version"], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise BuildContractError(f"tool is unavailable: {binary}")
    return result.stdout.strip()


def _run_capture(command: list[str], *, cwd: Path, env: Mapping[str, str]) -> str:
    result = subprocess.run(
        command, cwd=cwd, env=dict(env), capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise BuildContractError(
            f"command failed with exit {result.returncode}: {(result.stderr or result.stdout).strip()}"
        )
    return result.stdout.strip()


def _registry_root() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    return base / "dwarf" / "state" / "measurement-targets"


def publish_adapter_record(
    result: Mapping[str, Any], *, registry_root: Path
) -> dict[str, Any]:
    if result.get("source_revision") != REVISION:
        raise BuildContractError("adapter result has the wrong source revision")
    for key in ("adapter_set_sha256", "executable_sha256", "build_result_sha256"):
        value = result.get(key)
        if not isinstance(value, str) or len(value) != 64:
            raise BuildContractError(f"adapter result has no valid {key}")
    record = {
        key: value
        for key, value in result.items()
        if key != "executable"
    }
    command = record.get("build_command")
    if isinstance(command, list):
        record["build_command"] = [
            Path(value).name if isinstance(value, str) and Path(value).is_absolute() else value
            for value in command
        ]
    record["executable_ref"] = (
        "build:{}#dwarf-cardano-cbor-conformance".format(result["build_result_sha256"])
    )
    path = (
        registry_root
        / "cardano-node"
        / "conformance"
        / REVISION
        / "{}.json".format(result["adapter_set_sha256"])
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return record


def write_runtime_record(result: Mapping[str, Any], *, output_dir: Path) -> Path:
    path = output_dir / "evidence" / "runtime-record.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(result), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def build_adapter(
    *,
    manifest_path: Path,
    source_repository: str,
    output_dir: Path,
    registry_root: Path,
    cabal_binary: Path,
    ghc_binary: Path,
) -> dict[str, Any]:
    if output_dir.exists():
        raise BuildContractError(f"output directory already exists: {output_dir}")
    manifest = load_manifest(manifest_path)
    if manifest.get("kind") != "production-cbor-conformance":
        raise BuildContractError("manifest kind is not production CBOR conformance")
    if (manifest.get("source") or {}).get("revision") != REVISION:
        raise BuildContractError("manifest source revision is not Cardano-node 11.1.2")
    verified = verify_adapter_set(manifest_path.parent, manifest)
    if _tool_version(cabal_binary) != manifest["toolchain"]["cabal"]:
        raise BuildContractError("Cabal version does not match the adapter manifest")
    if _tool_version(ghc_binary) != manifest["toolchain"]["ghc"]:
        raise BuildContractError("GHC version does not match the adapter manifest")

    source = exact_builder.clone_exact_source(
        source_repository, output_dir / "work" / "source", REVISION
    )
    stage = source / "dwarf-conformance-adapters" / "cardano-cbor"
    for item in verified["files"]:
        relative = Path(item["path"])
        if relative.parts[0] != "cbor":
            raise BuildContractError("adapter file must be below cbor")
        destination = stage.joinpath(*relative.parts[1:])
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(manifest_path.parent / relative, destination)
    environment = dict(os.environ)
    environment.update(
        {str(key): str(value) for key, value in manifest["build"]["environment"].items()}
    )
    command = [str(part) for part in manifest["build"]["command"]]
    command[0] = str(cabal_binary.resolve())
    command[command.index("ghc-9.6.7")] = str(ghc_binary.resolve())
    log = output_dir / "logs" / "cabal-build.log"
    exact_builder.run_logged(command, cwd=source, log_path=log, env=environment)
    query = [str(part) for part in manifest["build"]["binary_query"]]
    query[0] = str(cabal_binary.resolve())
    query[query.index("ghc-9.6.7")] = str(ghc_binary.resolve())
    executable = Path(_run_capture(query, cwd=source, env=environment))
    if not executable.is_absolute():
        executable = source / executable
    executable = executable.resolve()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise BuildContractError("Cardano CBOR adapter executable was not produced")

    evidence = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "kind": manifest["kind"],
        "implementation": "cardano-node",
        "source_revision": REVISION,
        "source_release": manifest["source"]["release"],
        "measurement_boundary": manifest["measurement_boundary"],
        "production_entrypoint": manifest["production_entrypoint"],
        "toolchain": {
            "ghc": _tool_version(ghc_binary),
            "cabal": _tool_version(cabal_binary),
        },
        "adapter_set_sha256": verified["adapter_set_sha256"],
        "adapter_files": verified["files"],
        "build_command": command,
        "build_log_sha256": exact_builder.sha256_file(log),
        "executable": str(executable),
        "executable_sha256": exact_builder.sha256_file(executable),
    }
    evidence_path = output_dir / "evidence" / "build-result.json"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result = {
        **evidence,
        "build_result_sha256": exact_builder.sha256_file(evidence_path),
    }
    runtime_record = write_runtime_record(result, output_dir=output_dir)
    record = publish_adapter_record(result, registry_root=registry_root)
    return {**result, "runtime_record": str(runtime_record), "record": record}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--source-repository", default="https://github.com/IntersectMBO/cardano-node.git"
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--target-registry", type=Path, default=_registry_root())
    parser.add_argument(
        "--cabal", type=Path, default=Path.home() / ".ghcup/bin/cabal-3.16.0.0"
    )
    parser.add_argument(
        "--ghc", type=Path, default=Path.home() / ".ghcup/bin/ghc-9.6.7"
    )
    args = parser.parse_args(argv)
    try:
        result = build_adapter(
            manifest_path=args.manifest.resolve(),
            source_repository=args.source_repository,
            output_dir=args.output_dir.resolve(),
            registry_root=args.target_registry.resolve(),
            cabal_binary=args.cabal.resolve(),
            ghc_binary=args.ghc.resolve(),
        )
    except BuildContractError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
