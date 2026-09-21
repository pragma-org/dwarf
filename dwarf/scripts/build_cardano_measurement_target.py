#!/usr/bin/env python3
"""Build DWARF's exact Cardano-node 11.1.2 measurement target."""
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
    / "measurement-patches"
    / REVISION
    / "manifest.json"
)


class BuildContractError(RuntimeError):
    pass


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
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BuildContractError(f"cannot load patch manifest {path}: {error}") from error
    if not isinstance(body, dict):
        raise BuildContractError("patch manifest must be an object")
    return body


def _relative(value: str, *, field: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise BuildContractError(f"{field} must be a bounded relative path: {value}")
    return path


def _patch_set_digest(rows: Sequence[tuple[str, str]]) -> str:
    payload = "".join(f"{digest}  {path}\n" for path, digest in rows)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def verify_patch_set(root: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    patches = manifest.get("patches")
    if not isinstance(patches, list) or not patches:
        raise BuildContractError("patch manifest must contain patches")
    rows: list[tuple[str, str]] = []
    verified = []
    for item in patches:
        relative = _relative(str(item.get("path", "")), field="patch path")
        patch = root / relative
        if not patch.is_file():
            raise BuildContractError(f"missing patch: {relative}")
        actual = sha256_file(patch)
        expected = str(item.get("sha256", ""))
        if actual != expected:
            raise BuildContractError(
                f"patch digest mismatch for {relative}: expected {expected}, got {actual}"
            )
        rows.append((relative.as_posix(), actual))
        verified.append({"path": relative.as_posix(), "sha256": actual})
    digest = _patch_set_digest(rows)
    if digest != manifest.get("patch_set_sha256"):
        raise BuildContractError("patch-set digest mismatch")
    return {"patch_set_sha256": digest, "patches": verified}


def _git(source: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments], cwd=source, text=True, capture_output=True
    )
    if result.returncode != 0:
        raise BuildContractError(
            f"git {' '.join(arguments)} failed: {(result.stderr or result.stdout).strip()}"
        )
    return result.stdout.strip()


def validate_clean_source(source: Path, expected_revision: str) -> str:
    actual = _git(source, "rev-parse", "HEAD")
    if actual != expected_revision:
        raise BuildContractError(
            f"source revision mismatch: expected {expected_revision}, got {actual}"
        )
    if _git(source, "status", "--porcelain=v1", "--untracked-files=all"):
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
    _git(destination, "checkout", "--detach", revision)
    validate_clean_source(destination, revision)
    return destination


def verify_dependency_archive(path: Path, expected_sha256: str) -> str:
    if not path.is_file():
        raise BuildContractError(f"dependency archive is missing: {path}")
    actual = sha256_file(path)
    if actual != expected_sha256:
        raise BuildContractError(
            f"dependency archive digest mismatch: expected {expected_sha256}, got {actual}"
        )
    return actual


def verify_preimages(root: Path, preimages: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    verified = []
    for item in preimages:
        relative = _relative(str(item.get("path", "")), field="preimage path")
        path = root / relative
        if not path.is_file():
            raise BuildContractError(f"missing preimage: {relative}")
        actual = sha256_file(path)
        expected = str(item.get("sha256", ""))
        if actual != expected:
            raise BuildContractError(
                f"preimage digest mismatch for {relative}: expected {expected}, got {actual}"
            )
        verified.append({"path": relative.as_posix(), "sha256": actual})
    return verified


def run_logged(
    command: Sequence[str], *, cwd: Path, log_path: Path, env: Mapping[str, str] | None = None
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as stream:
        result = subprocess.run(
            list(command), cwd=cwd, env=dict(env) if env else None,
            stdout=stream, stderr=subprocess.STDOUT, text=True
        )
    if result.returncode != 0:
        raise BuildContractError(
            f"command failed with exit {result.returncode}; retained log: {log_path}"
        )


def apply_patch_to_dependency(
    dependency_root: Path,
    patch_path: Path,
    *,
    check_log: Path,
    apply_log: Path,
) -> None:
    """Apply a patch relative to one extracted dependency, never its parent repo.

    The dependency archives live below the disposable cardano-node checkout.
    Without the Git ceiling, ``git apply`` discovers that parent repository and
    silently filters every patch path outside the current untracked prefix.
    """

    env = dict(os.environ)
    env["GIT_CEILING_DIRECTORIES"] = str(dependency_root.parent.resolve())
    run_logged(
        ["git", "apply", "--check", "--whitespace=error-all", str(patch_path)],
        cwd=dependency_root,
        log_path=check_log,
        env=env,
    )
    run_logged(
        ["git", "apply", "--whitespace=error-all", str(patch_path)],
        cwd=dependency_root,
        log_path=apply_log,
        env=env,
    )


def _artifact(path: Path, *, root: Path) -> dict[str, Any]:
    if not path.is_file():
        raise BuildContractError(f"missing artifact: {path}")
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def discover_runtime_libraries(binary: Path) -> list[Path]:
    result = subprocess.run(
        ["ldd", str(binary)], text=True, capture_output=True, check=False
    )
    if result.returncode != 0:
        raise BuildContractError(
            f"cannot inspect dynamic runtime libraries: {result.stderr.strip()}"
        )
    libraries: list[Path] = []
    for line in result.stdout.splitlines():
        tokens = line.strip().split()
        candidate = None
        if "=>" in tokens:
            index = tokens.index("=>")
            if index + 1 < len(tokens) and tokens[index + 1].startswith("/"):
                candidate = tokens[index + 1]
        elif tokens and tokens[0].startswith("/"):
            candidate = tokens[0]
        if candidate is None:
            continue
        path = Path(candidate)
        if not path.is_file():
            raise BuildContractError(f"dynamic runtime library is missing: {path}")
        if path not in libraries:
            libraries.append(path)
    if not libraries:
        raise BuildContractError("dynamic runtime library set is empty")
    return libraries


def stage_runtime_libraries(
    libraries: Sequence[Path], *, context: Path
) -> list[dict[str, Any]]:
    runtime_root = context / "runtime-root"
    records = []
    for source in libraries:
        if not source.is_absolute() or not source.is_file():
            raise BuildContractError(f"invalid runtime library: {source}")
        relative = source.relative_to("/")
        destination = runtime_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        records.append({
            "source_path": "/" + relative.as_posix(),
            "install_path": "/opt/dwarf-cardano-runtime/" + relative.as_posix(),
            "sha256": sha256_file(destination),
            "size_bytes": destination.stat().st_size,
        })
    return records


def measurement_wrapper() -> str:
    return (
        "#!/bin/sh\n"
        "exec /opt/dwarf-cardano-runtime/lib64/ld-linux-x86-64.so.2 "
        "--library-path "
        "/opt/dwarf-cardano-runtime/lib/x86_64-linux-gnu:"
        "/opt/dwarf-cardano-runtime/usr/local/lib "
        "/opt/dwarf-cardano-runtime/bin/cardano-node \"$@\"\n"
    )


def measurement_dockerfile(
    base_image: str, *, source_revision: str, patch_set_sha256: str
) -> str:
    return (
        f"FROM {base_image}\n"
        f'LABEL org.opencontainers.image.revision="{source_revision}"\n'
        f'LABEL org.dwarf.measurement.patch-sha256="{patch_set_sha256}"\n'
        "COPY runtime-root/ /opt/dwarf-cardano-runtime/\n"
        "COPY --chmod=0755 cardano-node /opt/dwarf-cardano-runtime/bin/cardano-node\n"
        "COPY --chmod=0755 cardano-node-wrapper /usr/local/bin/cardano-node\n"
    )


def _dependency_archive(cache: Path, dependency: Mapping[str, Any]) -> Path:
    return (
        cache
        / str(dependency["package"])
        / str(dependency["version"])
        / str(dependency["archive"])
    )


def _extract_dependency(
    archive: Path, destination: Path, *, expected_directory: str
) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    run_logged(
        ["tar", "-xzf", str(archive), "-C", str(destination)],
        cwd=destination,
        log_path=destination.parent / "logs" / "dependency-extract.log",
    )
    root = destination / expected_directory
    if not root.is_dir():
        raise BuildContractError(
            f"dependency archive did not contain {expected_directory}"
        )
    return root


def _image_digest(image: str) -> tuple[str, list[str]]:
    result = subprocess.run(
        ["docker", "image", "inspect", image], text=True, capture_output=True
    )
    if result.returncode != 0:
        raise BuildContractError(f"cannot inspect built image: {result.stderr.strip()}")
    body = json.loads(result.stdout)[0]
    return str(body["Id"]), [str(value) for value in body.get("RepoDigests") or []]


def publish_target_record(result: Mapping[str, Any], *, registry_root: Path) -> dict[str, Any]:
    source = result.get("source")
    image = result.get("image")
    executable = result.get("executable")
    if not isinstance(source, Mapping) or not isinstance(image, Mapping) or not isinstance(executable, Mapping):
        raise BuildContractError("patched registry requires source, executable, and image")
    if image.get("status") != "built":
        raise BuildContractError("patched registry requires a built image")
    smoke = image.get("smoke")
    if not isinstance(smoke, Mapping) or smoke.get("status") != "passed":
        raise BuildContractError("patched registry requires a passed image smoke")
    runtime_probe = image.get("runtime_probe")
    if not isinstance(runtime_probe, Mapping) or runtime_probe.get("status") != "passed":
        raise BuildContractError("patched registry requires a passed runtime probe")
    runtime_probe_log = runtime_probe.get("log")
    if (
        not isinstance(runtime_probe_log, Mapping)
        or not isinstance(runtime_probe_log.get("sha256"), str)
        or len(runtime_probe_log["sha256"]) != 64
    ):
        raise BuildContractError("patched registry runtime probe log digest is missing")
    digests = image.get("repo_digests") or []
    immutable = next((str(value) for value in digests if "@sha256:" in str(value)), None)
    if immutable is None:
        image_id = str(image.get("image_id") or "")
        if not image_id.startswith("sha256:"):
            raise BuildContractError("patched image identity is not immutable")
        immutable = f"{image.get('reference')}@{image_id}"
    image_digest = immutable.rsplit("@", 1)[-1]
    record = {
        "schema_version": 1,
        "implementation": "cardano-node",
        "version": source.get("release"),
        "source_revision": source.get("revision"),
        "mode": "patched",
        "patch_set_sha256": result.get("patch_set_sha256"),
        "image_reference": immutable,
        "image_digest": image_digest,
        "executable_digest": f"sha256:{executable.get('sha256')}",
        "build_result_sha256": f"sha256:{result.get('build_result_sha256')}",
        "smoke_log_sha256": f"sha256:{smoke.get('log', {}).get('sha256')}",
        "runtime_probe_log_sha256": f"sha256:{runtime_probe_log['sha256']}",
        "created_at": result.get("created_at"),
    }
    serialized = json.dumps(record, sort_keys=True)
    if any(marker in serialized for marker in ("/home/", "/Users/", '"~')):
        raise BuildContractError("patched target registry record contains a machine path")
    path = (
        registry_root / "cardano-node" / str(record["source_revision"])
        / f"{record['patch_set_sha256']}.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return record


def build_target(
    *, manifest_path: Path, source_repository: str | Path,
    dependency_cache: Path, output_dir: Path, registry_root: Path | None,
) -> dict[str, Any]:
    if output_dir.exists():
        raise BuildContractError(f"output directory already exists: {output_dir}")
    output_dir.mkdir(parents=True)
    logs = output_dir / "logs"
    manifest = load_manifest(manifest_path)
    if manifest.get("source", {}).get("revision") != REVISION:
        raise BuildContractError("patch manifest is not pinned to Cardano 11.1.2")
    patch_set = verify_patch_set(manifest_path.parent, manifest)
    source = clone_exact_source(source_repository, output_dir / "work" / "source", REVISION)

    dependencies = []
    dependency_roots: dict[str, Path] = {}
    for dependency in manifest["dependencies"]:
        archive = _dependency_archive(dependency_cache, dependency)
        verified_sha256 = verify_dependency_archive(
            archive, str(dependency["sha256"])
        )
        directory = f"{dependency['package']}-{dependency['version']}"
        extracted = _extract_dependency(
            archive, source / "vendor", expected_directory=directory
        )
        dependency_roots[f"dependency:{directory}"] = extracted
        dependencies.append({**dependency, "verified_sha256": verified_sha256})

    applied_patches = []
    for index, patch_item in enumerate(manifest["patches"], start=1):
        root_key = str(patch_item.get("root") or "")
        patch_root = dependency_roots.get(root_key)
        if patch_root is None:
            raise BuildContractError(f"patch root is not an exact dependency: {root_key}")
        preimages = verify_preimages(patch_root, patch_item["preimages"])
        patch_path = manifest_path.parent / patch_item["path"]
        apply_patch_to_dependency(
            patch_root,
            patch_path,
            check_log=logs / f"patch-{index:02d}-check.log",
            apply_log=logs / f"patch-{index:02d}-apply.log",
        )
        applied_patches.append(
            {**patch_set["patches"][index - 1], "root": root_key, "preimages": preimages}
        )
    local_project = source / "cabal.project.local"
    if local_project.exists():
        raise BuildContractError("exact source unexpectedly contains cabal.project.local")
    local_project.write_text(
        "packages: "
        + " ".join(
            f"vendor/{dependency['package']}-{dependency['version']}"
            for dependency in manifest["dependencies"]
        )
        + "\n",
        encoding="utf-8",
    )

    env = dict(os.environ)
    env.update({str(k): str(v) for k, v in manifest["build"]["environment"].items()})
    command = [str(value) for value in manifest["build"]["command"]]
    run_logged(command, cwd=source, log_path=logs / "cabal-build.log", env=env)
    query = subprocess.run(
        [str(value) for value in manifest["build"]["binary_query"]],
        cwd=source, env=env, text=True, capture_output=True
    )
    if query.returncode != 0:
        raise BuildContractError(f"cannot locate built cardano-node: {query.stderr.strip()}")
    binary = Path(query.stdout.strip())
    executable = _artifact(binary, root=output_dir)

    context = output_dir / "image-context"
    context.mkdir()
    shutil.copy2(binary, context / "cardano-node")
    runtime_libraries = stage_runtime_libraries(
        discover_runtime_libraries(binary), context=context
    )
    (context / "cardano-node-wrapper").write_text(
        measurement_wrapper(), encoding="utf-8"
    )
    (context / "Dockerfile").write_text(
        measurement_dockerfile(
            str(manifest["base_image"]),
            source_revision=REVISION,
            patch_set_sha256=patch_set["patch_set_sha256"],
        ),
        encoding="utf-8",
    )
    tag = f"dwarf/cardano-measurement:11.1.2-{REVISION[:8]}-{patch_set['patch_set_sha256'][:8]}"
    run_logged(
        ["docker", "build", "--pull=false", "-t", tag, str(context)],
        cwd=context, log_path=logs / "docker-build.log"
    )
    image_id, repo_digests = _image_digest(tag)
    smoke_log = logs / "image-smoke.log"
    run_logged(
        ["docker", "run", "--rm", "--entrypoint", "cardano-node", tag, "--version"],
        cwd=output_dir, log_path=smoke_log
    )
    if REVISION not in smoke_log.read_text(encoding="utf-8"):
        raise BuildContractError("patched node smoke did not report the exact source revision")
    runtime_probe_log = logs / "runtime-probe.log"
    run_logged(
        [
            "docker", "run", "--rm", "--entrypoint",
            "/usr/local/bin/cardano-node", tag, "--version",
        ],
        cwd=output_dir, log_path=runtime_probe_log
    )
    if REVISION not in runtime_probe_log.read_text(encoding="utf-8"):
        raise BuildContractError("patched node runtime probe did not report the exact source revision")

    body: dict[str, Any] = {
        "schema_version": 1,
        "created_at": _utc_now(),
        "source": manifest["source"],
        "toolchain": manifest["toolchain"],
        "dependencies": dependencies,
        "patch_set_sha256": patch_set["patch_set_sha256"],
        "patches": applied_patches,
        "instrumentation": manifest["instrumentation"],
        "build": {"command": command, "log": _artifact(logs / "cabal-build.log", root=output_dir)},
        "executable": executable,
        "runtime_libraries": runtime_libraries,
        "image": {
            "status": "built", "reference": tag, "image_id": image_id,
            "repo_digests": repo_digests,
            "smoke": {"status": "passed", "log": _artifact(smoke_log, root=output_dir)},
            "runtime_probe": {
                "status": "passed",
                "log": _artifact(runtime_probe_log, root=output_dir),
            },
        },
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
