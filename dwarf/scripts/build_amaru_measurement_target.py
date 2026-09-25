#!/usr/bin/env python3
"""Build DWARF's exact, minimally instrumented Amaru measurement target.

The command always creates a disposable detached checkout.  It refuses a
different source revision, dirty source, changed patch, changed preimage, or
mutable base image.  Build and image logs remain in the output directory even
when a command fails.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
DWARF_ROOT = SCRIPT_DIR.parent
REPOSITORY_ROOT = DWARF_ROOT.parent
DEFAULT_REVISION = "b159172f25a9c389f82f20bca4f15e3032791638"
DEFAULT_MANIFEST = (
    DWARF_ROOT
    / "targets"
    / "amaru"
    / "measurement-patches"
    / DEFAULT_REVISION
    / "manifest.json"
)


def default_target_registry_root() -> Path:
    explicit = os.environ.get("ADA2_DWARF_MEASUREMENT_TARGET_REGISTRY", "").strip()
    if explicit:
        return Path(explicit)
    state = os.environ.get("ADA2_DWARF_STATE_DIR", "").strip()
    if state:
        return Path(state) / "measurement-targets"
    return Path.home() / ".local" / "share" / "dwarf" / "state" / "measurement-targets"


class BuildContractError(RuntimeError):
    """The exact-source measurement target cannot be built truthfully."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise BuildContractError(f"cannot load patch manifest {path}: {error}") from error
    if not isinstance(value, dict):
        raise BuildContractError("patch manifest must be a JSON object")
    return value


def manifest_revision(manifest_path: Path, manifest: Mapping[str, Any]) -> str:
    source = manifest.get("source")
    revision = source.get("revision") if isinstance(source, Mapping) else None
    if not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise BuildContractError("manifest source revision must be a 40-character lowercase Git revision")
    if manifest_path.parent.name != revision:
        raise BuildContractError(
            "manifest source revision does not match its revision directory"
        )
    return revision


def audited_measurement_revisions(targets_root: Path | None = None) -> frozenset[str]:
    """Amaru source revisions with a checked-in measurement-target manifest."""
    root = targets_root or (DWARF_ROOT / "targets" / "amaru")
    return frozenset(
        path.parent.name
        for path in root.glob("measurement-patches*/*/manifest.json")
    )


def require_audited_revision(manifest_path: Path, manifest: Mapping[str, Any]) -> str:
    """Fail closed unless the manifest sits in its revision directory and that
    revision has an audited measurement target."""
    revision = manifest_revision(manifest_path, manifest)
    if revision not in audited_measurement_revisions():
        raise BuildContractError(
            f"source revision {revision} has no audited Amaru measurement target"
        )
    return revision


def _safe_relative_path(value: str, *, field: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise BuildContractError(f"{field} must be a bounded relative path: {value}")
    return path


def _patch_set_digest(records: Sequence[tuple[str, str]]) -> str:
    payload = "".join(f"{digest}  {path}\n" for path, digest in records)
    return hashlib.sha256(payload.encode()).hexdigest()


def verify_patch_set(root: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    patches = manifest.get("patches")
    if not isinstance(patches, list) or not patches:
        raise BuildContractError("patch manifest must contain at least one patch")
    records: list[tuple[str, str]] = []
    verified: list[dict[str, Any]] = []
    for index, item in enumerate(patches):
        if not isinstance(item, dict):
            raise BuildContractError(f"patches[{index}] must be an object")
        relative = _safe_relative_path(str(item.get("path", "")), field=f"patches[{index}].path")
        patch = root / relative
        if not patch.is_file():
            raise BuildContractError(f"missing patch: {relative.as_posix()}")
        actual = sha256_file(patch)
        expected = str(item.get("sha256", ""))
        if actual != expected:
            raise BuildContractError(
                f"patch digest mismatch for {relative.as_posix()}: expected {expected}, got {actual}"
            )
        records.append((relative.as_posix(), actual))
        verified.append({"path": relative.as_posix(), "sha256": actual})
    patch_set = _patch_set_digest(records)
    expected_set = str(manifest.get("patch_set_sha256", ""))
    if patch_set != expected_set:
        raise BuildContractError(
            f"patch-set digest mismatch: expected {expected_set}, got {patch_set}"
        )
    return {"patch_set_sha256": patch_set, "patches": verified}


def _git(source: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments], cwd=source, text=True, capture_output=True
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise BuildContractError(f"git {' '.join(arguments)} failed: {detail}")
    return result.stdout.strip()


def validate_clean_source(source: Path, expected_revision: str) -> str:
    actual = _git(source, "rev-parse", "HEAD")
    if actual != expected_revision:
        raise BuildContractError(
            f"source revision mismatch: expected {expected_revision}, got {actual}"
        )
    status = _git(source, "status", "--porcelain=v1", "--untracked-files=all")
    if status:
        raise BuildContractError(f"source tree is not clean:\n{status}")
    return actual


def clone_exact_source(repository: str | Path, destination: Path, revision: str) -> Path:
    if destination.exists():
        raise BuildContractError(f"disposable checkout already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    clone = subprocess.run(
        ["git", "clone", "--no-checkout", "--", str(repository), str(destination)],
        text=True,
        capture_output=True,
    )
    if clone.returncode != 0:
        raise BuildContractError(f"git clone failed: {(clone.stderr or clone.stdout).strip()}")
    _git(destination, "checkout", "--detach", revision)
    validate_clean_source(destination, revision)
    return destination


def verify_preimages(source: Path, preimages: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    verified: list[dict[str, str]] = []
    for index, item in enumerate(preimages):
        relative = _safe_relative_path(
            str(item.get("path", "")), field=f"preimages[{index}].path"
        )
        path = source / relative
        if not path.is_file():
            raise BuildContractError(f"missing preimage: {relative.as_posix()}")
        actual = sha256_file(path)
        expected = str(item.get("sha256", ""))
        if actual != expected:
            raise BuildContractError(
                f"preimage digest mismatch for {relative.as_posix()}: expected {expected}, got {actual}"
            )
        verified.append({"path": relative.as_posix(), "sha256": actual})
    return verified


def run_logged(
    command: Sequence[str],
    *,
    cwd: Path,
    log_path: Path,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w") as log:
        result = subprocess.run(
            list(command),
            cwd=cwd,
            env=dict(env) if env is not None else None,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
        )
    if result.returncode != 0:
        raise BuildContractError(
            f"command failed with exit {result.returncode}; retained log: {log_path}"
        )
    return result


def apply_patch_set(
    source: Path,
    patch_root: Path,
    manifest: Mapping[str, Any],
    *,
    log_path: Path,
) -> list[dict[str, Any]]:
    validate_clean_source(source, str(manifest["source"]["revision"]))
    results: list[dict[str, Any]] = []
    for item in manifest["patches"]:
        preimages = verify_preimages(source, item.get("preimages", []))
        relative = _safe_relative_path(str(item["path"]), field="patch path")
        patch = patch_root / relative
        run_logged(
            ["git", "apply", "--check", "--whitespace=error-all", str(patch)],
            cwd=source,
            log_path=log_path,
        )
        run_logged(
            ["git", "apply", "--whitespace=error-all", str(patch)],
            cwd=source,
            log_path=log_path.with_name(f"{log_path.stem}-apply{log_path.suffix}"),
        )
        results.append(
            {
                "path": relative.as_posix(),
                "sha256": sha256_file(patch),
                "preimages": preimages,
                "application": "exact-preimage; git-apply-check; whitespace-error-all",
            }
        )
    return results


def artifact_record(path: Path, *, root: Path) -> dict[str, Any]:
    if not path.is_file():
        raise BuildContractError(f"missing artifact: {path}")
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError as error:
        raise BuildContractError(f"artifact is outside output root: {path}") from error
    return {
        "path": relative,
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def _verify_toolchain(source: Path, expected: str) -> None:
    with (source / "rust-toolchain.toml").open("rb") as stream:
        actual = tomllib.load(stream).get("toolchain", {}).get("channel")
    if actual != expected:
        raise BuildContractError(
            f"Rust toolchain mismatch: expected {expected}, got {actual}"
        )


def verify_build_tools(manifest: Mapping[str, Any]) -> dict[str, str]:
    expected = manifest.get("build_tools")
    if not isinstance(expected, Mapping):
        raise BuildContractError("patch manifest must pin build_tools")
    probes = {
        "cargo-zigbuild": ["cargo-zigbuild", "--version"],
        "zig": [sys.executable, "-m", "ziglang", "version"],
    }
    observed: dict[str, str] = {}
    for name, command in probes.items():
        result = subprocess.run(command, text=True, capture_output=True)
        if result.returncode != 0:
            raise BuildContractError(f"cannot execute pinned build tool {name}")
        version = (result.stdout or result.stderr).strip().split()[-1]
        if version != str(expected.get(name) or ""):
            raise BuildContractError(
                f"{name} version mismatch: expected {expected.get(name)}, got {version}"
            )
        observed[name] = version
    target = str(expected.get("target") or "")
    if target != "x86_64-unknown-linux-musl":
        raise BuildContractError("build target must be x86_64-unknown-linux-musl")
    observed["target"] = target
    return observed


def verify_static_binary(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise BuildContractError(f"missing binary for static verification: {path}")
    file_result = subprocess.run(["file", str(path)], text=True, capture_output=True)
    if file_result.returncode != 0 or "statically linked" not in file_result.stdout:
        raise BuildContractError("Amaru measurement binary is not statically linked")
    readelf = subprocess.run(
        ["readelf", "-l", str(path)], text=True, capture_output=True
    )
    if readelf.returncode != 0:
        raise BuildContractError("cannot inspect Amaru measurement ELF program headers")
    if "Requesting program interpreter" in readelf.stdout:
        raise BuildContractError("Amaru measurement binary declares a dynamic interpreter")
    return {
        "linkage": "static",
        "target": "x86_64-unknown-linux-musl",
        "file_summary": file_result.stdout.strip(),
    }


def _write_derived_dockerfile(
    path: Path, *, base_image: str, source_revision: str, patch_digest: str
) -> None:
    if "@sha256:" not in base_image:
        raise BuildContractError("base image must be pinned by sha256 digest")
    path.write_text(
        "\n".join(
            [
                f"FROM {base_image}",
                "USER root",
                "COPY amaru /usr/local/bin/amaru",
                "RUN chmod 0755 /usr/local/bin/amaru",
                f'LABEL org.opencontainers.image.revision="{source_revision}"',
                f'LABEL org.dwarf.measurement.patch-sha256="{patch_digest}"',
                "USER amaru",
                "",
            ]
        )
    )


def publish_target_record(
    result: Mapping[str, Any], *, registry_root: str | Path
) -> dict[str, Any]:
    image = result.get("image")
    if not isinstance(image, Mapping) or image.get("status") != "built":
        raise BuildContractError("target registry requires a built image")
    smoke = image.get("smoke")
    if not isinstance(smoke, Mapping) or smoke.get("status") != "passed":
        raise BuildContractError("target registry requires a passed image smoke test")
    runtime_probe = image.get("runtime_probe")
    if not isinstance(runtime_probe, Mapping) or runtime_probe.get("status") != "passed":
        raise BuildContractError(
            "target registry requires a passed bootstrap-wrapper runtime probe"
        )
    source = result.get("source")
    executable = result.get("executable")
    if not isinstance(source, Mapping) or not isinstance(executable, Mapping):
        raise BuildContractError("target registry requires source and executable evidence")
    result_digest = str(result.get("result_sha256") or "")
    if len(result_digest) != 64:
        raise BuildContractError("target registry requires a build-result sha256")
    repo_digests = image.get("repo_digests")
    if not isinstance(repo_digests, list) or not repo_digests:
        raise BuildContractError(
            "target registry requires a locally resolvable immutable repository digest"
        )
    image_reference = str(repo_digests[0])
    if "@sha256:" not in image_reference:
        raise BuildContractError("target repository digest is not immutable sha256")
    record = {
        "schema_version": 1,
        "implementation": "amaru",
        "mode": "patched",
        "version": source.get("release"),
        "source_revision": source.get("revision"),
        "patch_set_sha256": result.get("patch_set_sha256"),
        "image_reference": image_reference,
        "image_digest": image.get("image_id"),
        "executable_digest": f"sha256:{executable.get('sha256')}",
        "build_result_sha256": f"sha256:{result_digest}",
        "smoke_log_sha256": f"sha256:{smoke.get('log', {}).get('sha256')}",
        "runtime_probe_image": runtime_probe.get("image"),
        "runtime_probe_log_sha256": (
            f"sha256:{runtime_probe.get('log', {}).get('sha256')}"
        ),
        "created_at": result.get("created_at"),
    }
    serialized = json.dumps(record, sort_keys=True)
    if any(marker in serialized for marker in ("/home/", "/Users/", '"~')):
        raise BuildContractError("target registry record contains a machine-specific path")
    revision = str(record["source_revision"])
    patch_set = str(record["patch_set_sha256"])
    path = Path(registry_root) / "amaru" / revision / f"{patch_set}.json"
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
    image_ref: str,
    build_image: bool,
    target_registry_root: Path | None = None,
) -> dict[str, Any]:
    if output_dir.exists():
        raise BuildContractError(f"output directory already exists: {output_dir}")
    output_dir.mkdir(parents=True)
    logs = output_dir / "logs"
    evidence = output_dir / "evidence"
    evidence.mkdir()

    manifest = load_manifest(manifest_path)
    revision = manifest_revision(manifest_path, manifest)
    patch_root = manifest_path.parent
    patch_identity = verify_patch_set(patch_root, manifest)
    source = clone_exact_source(source_repository, output_dir / "work" / "source", revision)
    _verify_toolchain(source, str(manifest["toolchain"]))
    build_tools = verify_build_tools(manifest)
    applied = apply_patch_set(
        source, patch_root, manifest, log_path=logs / "patch-check.log"
    )

    build_env = dict(os.environ)
    build_env.update({str(key): str(value) for key, value in manifest["build"].get("environment", {}).items()})
    build_env["SOURCE_DATE_EPOCH"] = _git(source, "show", "-s", "--format=%ct", revision)
    run_logged(
        [str(part) for part in manifest["build"]["command"]],
        cwd=source,
        log_path=logs / "cargo-build.log",
        env=build_env,
    )
    built_binary = source / str(manifest["build"]["binary"])
    if not built_binary.is_file():
        raise BuildContractError(f"build did not produce {built_binary}")
    packaged_binary = output_dir / "artifacts" / "amaru"
    packaged_binary.parent.mkdir()
    shutil.copy2(built_binary, packaged_binary)
    static_contract = verify_static_binary(packaged_binary)

    result: dict[str, Any] = {
        "schema_version": 1,
        "created_at": _utc_now(),
        "source": manifest["source"],
        "toolchain": manifest["toolchain"],
        "build_tools": build_tools,
        "build": {
            "command": manifest["build"]["command"],
            "environment": {
                **manifest["build"].get("environment", {}),
                "SOURCE_DATE_EPOCH": build_env["SOURCE_DATE_EPOCH"],
            },
            "log": artifact_record(logs / "cargo-build.log", root=output_dir),
        },
        "patches": applied,
        "patch_set_sha256": patch_identity["patch_set_sha256"],
        "executable": artifact_record(packaged_binary, root=output_dir),
        "executable_contract": static_contract,
        "image": {"status": "not-built"},
    }

    if build_image:
        context = output_dir / "image-context"
        context.mkdir()
        shutil.copy2(packaged_binary, context / "amaru")
        _write_derived_dockerfile(
            context / "Dockerfile",
            base_image=str(manifest["base_image"]),
            source_revision=revision,
            patch_digest=patch_identity["patch_set_sha256"],
        )
        run_logged(
            ["docker", "build", "--pull=false", "--tag", image_ref, "."],
            cwd=context,
            log_path=logs / "docker-build.log",
        )
        inspect = subprocess.run(
            ["docker", "image", "inspect", image_ref],
            text=True,
            capture_output=True,
        )
        if inspect.returncode != 0:
            raise BuildContractError(f"cannot inspect built image: {inspect.stderr.strip()}")
        image = json.loads(inspect.stdout)[0]
        smoke_log = logs / "image-smoke.log"
        run_logged(
            [
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "/usr/local/bin/amaru",
                image_ref,
                "--version",
            ],
            cwd=output_dir,
            log_path=smoke_log,
        )
        runtime_probe_image = str(manifest["runtime_probe_image"])
        runtime_probe_log = logs / "bootstrap-wrapper-smoke.log"
        run_logged(
            [
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "/bin/bash",
                "--volume",
                f"{packaged_binary.resolve()}:/target/amaru:ro",
                runtime_probe_image,
                "-lc",
                "/target/amaru --version",
            ],
            cwd=output_dir,
            log_path=runtime_probe_log,
        )
        result["image"] = {
            "status": "built",
            "reference": image_ref,
            "image_id": image["Id"],
            "repo_digests": image.get("RepoDigests") or [],
            "base_image": manifest["base_image"],
            "build_log": artifact_record(logs / "docker-build.log", root=output_dir),
            "smoke": {
                "status": "passed",
                "command": ["/usr/local/bin/amaru", "--version"],
                "log": artifact_record(smoke_log, root=output_dir),
            },
            "runtime_probe": {
                "status": "passed",
                "image": runtime_probe_image,
                "command": ["/target/amaru", "--version"],
                "log": artifact_record(runtime_probe_log, root=output_dir),
            },
        }

    result_path = evidence / "build-result.json"
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    result["result_sha256"] = sha256_file(result_path)
    if build_image and target_registry_root is not None:
        result["target_registry"] = publish_target_record(
            result, registry_root=target_registry_root
        )
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--source-repository", default="https://github.com/pragma-org/amaru.git")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--image-ref", default="dwarf/amaru-measurement:10.11.20260912-b159172"
    )
    parser.add_argument("--no-image", action="store_true")
    parser.add_argument(
        "--target-registry",
        type=Path,
        default=default_target_registry_root(),
        help="Publish the exact built target for fail-closed profile resolution.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = build_target(
            manifest_path=args.manifest.resolve(),
            source_repository=args.source_repository,
            output_dir=args.output_dir.resolve(),
            image_ref=args.image_ref,
            build_image=not args.no_image,
            target_registry_root=None if args.no_image else args.target_registry.resolve(),
        )
    except BuildContractError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
