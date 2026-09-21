#!/usr/bin/env python3
"""Build revision-locked Cardano production-decoder coverage executables.

The target is compiled with GHC HPC and is deliberately not a performance
authority. It exists to prove which production decoder paths a retained corpus
reaches at the exact Cardano release identity.
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


SCRIPT_DIR = Path(__file__).resolve().parent
DWARF_ROOT = SCRIPT_DIR.parent
REVISION = "fef83fed01d7926f3de83b3b917be5a4a48768b5"
DEFAULT_MANIFEST = (
    DWARF_ROOT
    / "targets"
    / "cardano-node"
    / "coverage-targets"
    / REVISION
    / "manifest.json"
)
DEFAULT_HARNESS_ROOT = DEFAULT_MANIFEST.parent


class BuildContractError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_dependency_archive(path: Path, expected_sha256: str) -> str:
    if not path.is_file():
        raise BuildContractError(f"dependency archive is missing: {path}")
    actual = sha256_file(path)
    if actual != expected_sha256:
        raise BuildContractError(
            f"dependency archive digest mismatch: expected {expected_sha256}, got {actual}"
        )
    return actual


def canonical_digest(body: Mapping[str, Any]) -> str:
    payload = json.dumps(body, sort_keys=True, separators=(",", ":")) + "\n"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BuildContractError(f"cannot load coverage manifest {path}: {error}") from error
    if not isinstance(body, dict):
        raise BuildContractError("coverage manifest must be an object")
    return body


def _relative(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise BuildContractError(f"harness path is not bounded: {value}")
    return path


def _set_digest(rows: Sequence[tuple[str, str]]) -> str:
    payload = "".join(f"{digest}  {path}\n" for path, digest in rows)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def verify_harness_set(root: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    files = manifest.get("harness_files")
    if not isinstance(files, list) or not files:
        raise BuildContractError("coverage manifest requires harness_files")
    rows: list[tuple[str, str]] = []
    verified: list[dict[str, str]] = []
    for item in files:
        relative = _relative(str(item.get("path", "")))
        path = root / relative
        if not path.is_file():
            raise BuildContractError(f"missing harness file: {relative}")
        actual = sha256_file(path)
        expected = str(item.get("sha256", ""))
        if actual != expected:
            raise BuildContractError(
                f"harness digest mismatch for {relative}: expected {expected}, got {actual}"
            )
        rows.append((relative.as_posix(), actual))
        verified.append({"path": relative.as_posix(), "sha256": actual})
    digest = _set_digest(rows)
    if digest != manifest.get("coverage_harness_sha256"):
        raise BuildContractError("coverage harness set digest mismatch")
    return {"coverage_harness_sha256": digest, "files": verified}


def copy_harness_files(source: Path, destination: Path, manifest: Mapping[str, Any]) -> None:
    verified = verify_harness_set(source, manifest)
    destination.mkdir(parents=True, exist_ok=False)
    for item in verified["files"]:
        relative = Path(item["path"])
        output = destination / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, output)


def run_checked(command: Sequence[str], *, cwd: Path) -> str:
    result = subprocess.run(list(command), cwd=cwd, text=True, capture_output=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise BuildContractError(f"{' '.join(command)} failed: {detail}")
    return result.stdout.strip()


def git(source: Path, *arguments: str) -> str:
    return run_checked(["git", *arguments], cwd=source)


def validate_clean_source(source: Path, expected_revision: str) -> str:
    actual = git(source, "rev-parse", "HEAD")
    if actual != expected_revision:
        raise BuildContractError(
            f"source revision mismatch: expected {expected_revision}, got {actual}"
        )
    if git(source, "status", "--porcelain=v1", "--untracked-files=all"):
        raise BuildContractError("source tree is not clean")
    return actual


def clone_exact_source(repository: str | Path, destination: Path, revision: str) -> Path:
    if destination.exists():
        raise BuildContractError(f"disposable checkout already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "clone", "--no-checkout", "--", str(repository), str(destination)],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise BuildContractError(f"git clone failed: {(result.stderr or result.stdout).strip()}")
    git(destination, "checkout", "--detach", revision)
    validate_clean_source(destination, revision)
    return destination


def _tool_version(command: Sequence[str]) -> str:
    result = subprocess.run(list(command), text=True, capture_output=True)
    if result.returncode != 0:
        raise BuildContractError(f"tool unavailable: {' '.join(command)}")
    return result.stdout.strip()


def _artifact(path: Path, *, root: Path, target_name: str) -> dict[str, Any]:
    if not path.is_file():
        raise BuildContractError(f"missing coverage executable: {path}")
    return {
        "target_name": target_name,
        "path": path.relative_to(root).as_posix(),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def publish_target_record(
    result: Mapping[str, Any], *, registry_root: Path
) -> dict[str, Any]:
    source = result.get("source")
    if not isinstance(source, Mapping):
        raise BuildContractError("coverage result requires source identity")
    executables = result.get("executables")
    if not isinstance(executables, list) or not executables:
        raise BuildContractError("coverage result requires executables")
    record = {
        "schema_version": 1,
        "implementation": "cardano-node",
        "version": source.get("release"),
        "source_revision": source.get("revision"),
        "mode": "coverage",
        "engine": result.get("engine"),
        "coverage_harness_sha256": result.get("coverage_harness_sha256"),
        "build_result_sha256": f"sha256:{result.get('build_result_sha256')}",
        "coverage_target_ref": result.get("coverage_target_ref"),
        "target_names": result.get("target_names"),
        "executables": executables,
        "performance_authority": "non-authoritative",
        "non_authoritative_performance": True,
        "created_at": result.get("created_at"),
    }
    serialized = json.dumps(record, sort_keys=True)
    if any(marker in serialized for marker in ("/home/", "/Users/", '"~')):
        raise BuildContractError("coverage target record contains a machine path")
    path = (
        registry_root
        / "cardano-node"
        / "coverage"
        / str(record["source_revision"])
        / f"{record['coverage_harness_sha256']}.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return record


def build_target(
    *,
    manifest_path: Path,
    harness_root: Path,
    source_repository: str | Path,
    dependency_cache: Path,
    output_dir: Path,
    registry_root: Path | None,
) -> dict[str, Any]:
    if output_dir.exists():
        raise BuildContractError(f"output directory already exists: {output_dir}")
    output_dir.mkdir(parents=True)
    manifest = load_manifest(manifest_path)
    if manifest.get("source", {}).get("revision") != REVISION:
        raise BuildContractError("coverage manifest is not pinned to Cardano 11.1.2")
    if manifest.get("performance_authority") != "non-authoritative":
        raise BuildContractError("coverage target cannot be a performance authority")
    harness = verify_harness_set(harness_root, manifest)
    source = clone_exact_source(
        source_repository, output_dir / "work" / "source", REVISION
    )
    staged = source / "dwarf-coverage-harness"
    copy_harness_files(harness_root, staged, manifest)
    dependency = manifest["dependencies"][0]
    archive = (
        dependency_cache / dependency["package"] / dependency["version"]
        / dependency["archive"]
    )
    verify_dependency_archive(archive, dependency["sha256"])
    vendor = source / "vendor"
    vendor.mkdir()
    result = subprocess.run(
        ["tar", "-xzf", str(archive), "-C", str(vendor)],
        cwd=source,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise BuildContractError(f"dependency extraction failed: {result.stderr.strip()}")
    vendored_package = vendor / f"{dependency['package']}-{dependency['version']}"
    if not vendored_package.is_dir():
        raise BuildContractError("dependency archive did not contain its exact package directory")

    toolchain = manifest["toolchain"]
    if _tool_version(["ghc", "--numeric-version"]) != toolchain["ghc"]:
        raise BuildContractError("GHC version does not match coverage manifest")
    if _tool_version(["cabal", "--numeric-version"]) != toolchain["cabal"]:
        raise BuildContractError("cabal version does not match coverage manifest")

    local_project = source / "cabal.project.local"
    instrumented_packages = manifest["build"]["instrumented_packages"]
    ghc_options = " ".join(manifest["build"]["ghc_options"])
    local_project.write_text(
        f"packages: dwarf-coverage-harness {vendored_package.relative_to(source)}\n"
        + "".join(
            f"package {package}\n  ghc-options: {ghc_options}\n"
            for package in instrumented_packages
        ),
        encoding="utf-8",
    )
    targets = [f"exe:{name}" for name in manifest["target_names"]]
    command = [*manifest["build"]["command_prefix"], *targets]
    log = output_dir / "logs" / "cabal-build.log"
    log.parent.mkdir(parents=True)
    with log.open("w", encoding="utf-8") as stream:
        result = subprocess.run(command, cwd=source, stdout=stream, stderr=subprocess.STDOUT, text=True)
    if result.returncode != 0:
        raise BuildContractError(f"coverage build failed; retained log: {log}")

    executables = []
    for name in manifest["target_names"]:
        path = Path(run_checked(["cabal", "list-bin", f"exe:{name}"], cwd=source))
        executables.append(_artifact(path, root=output_dir, target_name=name))
    body: dict[str, Any] = {
        "schema_version": 1,
        "created_at": utc_now(),
        "source": manifest["source"],
        "toolchain": toolchain,
        "engine": manifest["engine"],
        "coverage_harness_sha256": harness["coverage_harness_sha256"],
        "target_names": manifest["target_names"],
        "executables": executables,
        "coverage_target_ref": f"state:measurement-target-builds/{output_dir.name}",
        "performance_authority": "non-authoritative",
        "non_authoritative_performance": True,
        "build": {"command": command, "log_sha256": sha256_file(log)},
        "instrumented_packages": instrumented_packages,
        "dependencies": [{**dependency, "verified_sha256": dependency["sha256"]}],
    }
    result_path = output_dir / "evidence" / "build-result.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    body["build_result_sha256"] = sha256_file(result_path)
    if registry_root is not None:
        body["target_registry"] = publish_target_record(body, registry_root=registry_root)
    return body


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--harness-root", type=Path, default=DEFAULT_HARNESS_ROOT)
    parser.add_argument("--source-repository", default="https://github.com/IntersectMBO/cardano-node.git")
    parser.add_argument(
        "--dependency-cache", type=Path,
        default=Path.home() / ".cabal" / "packages" / "cardano-haskell-packages",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--target-registry", type=Path)
    args = parser.parse_args(argv)
    try:
        body = build_target(
            manifest_path=args.manifest.resolve(),
            harness_root=args.harness_root.resolve(),
            source_repository=args.source_repository,
            dependency_cache=args.dependency_cache.resolve(),
            output_dir=args.output_dir.resolve(),
            registry_root=args.target_registry.resolve() if args.target_registry else None,
        )
    except BuildContractError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(body, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
