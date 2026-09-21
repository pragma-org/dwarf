#!/usr/bin/env python3
"""Build an exact-source cargo-fuzz/libFuzzer coverage target for Amaru.

The builder clones the pinned upstream revision into a disposable directory,
stages a digest-checked DWARF harness beside the real production crates, and
retains the resulting compiler-instrumented target identity. Coverage targets
are path evidence only: their performance measurements are always labelled
non-authoritative.
"""
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
from typing import Any, Mapping, Sequence

from scripts import build_amaru_measurement_target as exact_builder
from scripts.cargo_fuzz_campaign import resolve_fuzz_target_binary


SCRIPT_DIR = Path(__file__).resolve().parent
DWARF_ROOT = SCRIPT_DIR.parent
DEFAULT_REVISION = "b159172f25a9c389f82f20bca4f15e3032791638"
DEFAULT_MANIFEST = (
    DWARF_ROOT
    / "targets"
    / "amaru"
    / "coverage-targets"
    / DEFAULT_REVISION
    / "manifest.json"
)
BuildContractError = exact_builder.BuildContractError


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def load_manifest(path: Path) -> dict[str, Any]:
    return exact_builder.load_manifest(path)


def _harness_digest(rows: Sequence[tuple[str, str]]) -> str:
    payload = "".join(f"{digest}  {path}\n" for path, digest in rows)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def verify_harness_set(root: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    files = manifest.get("harness_files")
    if not isinstance(files, list) or not files:
        raise BuildContractError("coverage manifest must contain harness_files")
    rows: list[tuple[str, str]] = []
    verified = []
    for index, item in enumerate(files):
        if not isinstance(item, Mapping):
            raise BuildContractError(f"harness_files[{index}] must be an object")
        relative = exact_builder._safe_relative_path(
            str(item.get("path", "")), field=f"harness_files[{index}].path"
        )
        path = root / relative
        if not path.is_file():
            raise BuildContractError(f"missing coverage harness file: {relative}")
        actual = exact_builder.sha256_file(path)
        expected = str(item.get("sha256") or "")
        if actual != expected:
            raise BuildContractError(
                f"coverage harness digest mismatch for {relative}: expected {expected}, got {actual}"
            )
        rows.append((relative.as_posix(), actual))
        verified.append({"path": relative.as_posix(), "sha256": actual})
    digest = _harness_digest(rows)
    if digest != manifest.get("coverage_harness_sha256"):
        raise BuildContractError(
            "coverage harness set digest does not match the checked-in manifest"
        )
    return {"coverage_harness_sha256": digest, "files": verified}


def _verify_manifest_contract(manifest: Mapping[str, Any]) -> None:
    source = manifest.get("source")
    if not isinstance(source, Mapping) or source.get("revision") != DEFAULT_REVISION:
        raise BuildContractError("coverage manifest is not pinned to the audited Amaru revision")
    if manifest.get("engine") != "cargo-fuzz/libFuzzer":
        raise BuildContractError("coverage target must reuse cargo-fuzz/libFuzzer")
    if manifest.get("performance_authority") != "non-authoritative":
        raise BuildContractError("coverage performance authority must be non-authoritative")
    if manifest.get("non_authoritative_performance") is not True:
        raise BuildContractError("coverage target must label performance non-authoritative")
    if manifest.get("target_name") != "plutus_data":
        raise BuildContractError("initial coverage target must be the qualified plutus_data surface")


def _stage_harness(
    *, source: Path, harness_root: Path, verified: Mapping[str, Any]
) -> Path:
    destination = source / "dwarf-coverage-fuzz"
    if destination.exists():
        raise BuildContractError(f"coverage harness destination already exists: {destination}")
    for item in verified["files"]:
        relative = Path(item["path"])
        output = destination / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(harness_root / relative, output)
        if exact_builder.sha256_file(output) != item["sha256"]:
            raise BuildContractError(f"staged coverage harness changed: {relative}")
    return destination


def _verify_cargo_fuzz(expected: str, *, env: Mapping[str, str]) -> str:
    proc = subprocess.run(
        ["cargo", "fuzz", "--version"],
        capture_output=True,
        text=True,
        check=False,
        env=dict(env),
    )
    if proc.returncode != 0:
        raise BuildContractError("cargo-fuzz is unavailable")
    observed = (proc.stdout or proc.stderr).strip().split()[-1]
    if observed != expected:
        raise BuildContractError(
            f"cargo-fuzz version mismatch: expected {expected}, got {observed}"
        )
    return observed


def _portable_state_ref(output_dir: Path) -> str:
    return f"state:measurement-target-builds/{output_dir.name}"


def publish_target_record(
    result: Mapping[str, Any], *, registry_root: Path
) -> dict[str, Any]:
    source = result.get("source")
    executable = result.get("executable")
    if not isinstance(source, Mapping) or not isinstance(executable, Mapping):
        raise BuildContractError("coverage registry requires source and executable evidence")
    result_digest = str(result.get("result_sha256") or "")
    if len(result_digest) != 64:
        raise BuildContractError("coverage registry requires a build-result sha256")
    record = {
        "schema_version": 1,
        "implementation": "amaru",
        "version": source.get("release"),
        "source_revision": source.get("revision"),
        "mode": "coverage",
        "engine": result.get("engine"),
        "target_name": result.get("target_name"),
        "coverage_harness_sha256": result.get("coverage_harness_sha256"),
        "executable_digest": f"sha256:{executable.get('sha256')}",
        "build_result_sha256": f"sha256:{result_digest}",
        "coverage_target_ref": result.get("coverage_target_ref"),
        "working_dir": "work/source",
        "fuzz_dir": "work/source/dwarf-coverage-fuzz",
        "binary": executable.get("path"),
        "performance_authority": "non-authoritative",
        "non_authoritative_performance": True,
        "created_at": result.get("created_at"),
    }
    serialized = json.dumps(record, sort_keys=True)
    if any(marker in serialized for marker in ("/home/", "/Users/", '"~')):
        raise BuildContractError("coverage target registry record contains a machine path")
    path = (
        registry_root
        / "amaru"
        / "coverage"
        / str(record["source_revision"])
        / f"{record['coverage_harness_sha256']}.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)
    return record


def build_target(
    *,
    manifest_path: Path,
    source_repository: str | Path,
    output_dir: Path,
    target_registry_root: Path | None,
) -> dict[str, Any]:
    if output_dir.exists():
        raise BuildContractError(f"output directory already exists: {output_dir}")
    output_dir.mkdir(parents=True)
    logs = output_dir / "logs"
    evidence = output_dir / "evidence"
    evidence.mkdir()

    manifest = load_manifest(manifest_path)
    _verify_manifest_contract(manifest)
    harness_root = manifest_path.parent
    harness = verify_harness_set(harness_root, manifest)
    revision = str(manifest["source"]["revision"])
    source = exact_builder.clone_exact_source(
        source_repository, output_dir / "work" / "source", revision
    )
    fuzz_dir = _stage_harness(
        source=source, harness_root=harness_root, verified=harness
    )

    build_env = dict(os.environ)
    build_env.update(
        {str(key): str(value) for key, value in manifest["build"]["environment"].items()}
    )
    build_env["SOURCE_DATE_EPOCH"] = exact_builder._git(
        source, "show", "-s", "--format=%ct", revision
    )
    cargo_fuzz_version = _verify_cargo_fuzz(
        str(manifest["cargo_fuzz_version"]), env=build_env
    )
    command = [str(part) for part in manifest["build"]["command"]]
    exact_builder.run_logged(
        command,
        cwd=source,
        log_path=logs / "cargo-fuzz-build.log",
        env=build_env,
    )
    executable = resolve_fuzz_target_binary(
        working_dir=source,
        fuzz_dir=fuzz_dir,
        target_name=str(manifest["target_name"]),
        toolchain=str(manifest["toolchain"]),
    )
    if executable is None:
        raise BuildContractError("cargo-fuzz build did not produce the target executable")

    result: dict[str, Any] = {
        "schema_version": 1,
        "created_at": _utc_now(),
        "source": manifest["source"],
        "toolchain": manifest["toolchain"],
        "engine": manifest["engine"],
        "cargo_fuzz_version": cargo_fuzz_version,
        "target_name": manifest["target_name"],
        "production_entrypoint": manifest["production_entrypoint"],
        "coverage_harness_sha256": harness["coverage_harness_sha256"],
        "harness_files": harness["files"],
        "coverage_target_ref": _portable_state_ref(output_dir),
        "performance_authority": "non-authoritative",
        "non_authoritative_performance": True,
        "build": {
            "command": command,
            "environment": {
                **manifest["build"]["environment"],
                "SOURCE_DATE_EPOCH": build_env["SOURCE_DATE_EPOCH"],
            },
            "log": exact_builder.artifact_record(
                logs / "cargo-fuzz-build.log", root=output_dir
            ),
        },
        "executable": exact_builder.artifact_record(executable, root=output_dir),
    }
    result_path = evidence / "build-result.json"
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    result["result_sha256"] = exact_builder.sha256_file(result_path)
    if target_registry_root is not None:
        result["target_registry"] = publish_target_record(
            result, registry_root=target_registry_root
        )
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--source-repository", default="https://github.com/pragma-org/amaru.git"
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--target-registry",
        type=Path,
        default=exact_builder.default_target_registry_root(),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = build_target(
            manifest_path=args.manifest.resolve(),
            source_repository=args.source_repository,
            output_dir=args.output_dir.resolve(),
            target_registry_root=args.target_registry.resolve(),
        )
    except BuildContractError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
