#!/usr/bin/env python3
"""Verify hosted stock-build artifacts before campaign runtime registration.

This stage consumes only retained files from the preceding GitHub-hosted build.
It does not execute the candidate binary, publish results, or claim that the OCI
artifact has passed DWARF readiness.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

from profile_manager.candidate_build_executor import _owned_directory, _save

ROOT = Path(__file__).resolve().parents[2]
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_HEX = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED = ("Cargo.lock", "fetch-metadata.json", "synthetic-git.json", "peer-snapshots.json",
             "build.log", "executable", "runtime.docker.tar")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_regular(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"missing regular JSON input: {path.name}")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"JSON input must be an object: {path.name}")
    return value


def stage_hosted_build(discovery_path, stock_build, directory, *, expected_commit, expected_trusted_revision,
                       run=None):
    output = _owned_directory(directory)
    status = {"schema_version": "v1", "kind": "hosted-campaign-stage", "status": "partial",
              "ready_for_campaign": False, "published": False, "candidates": []}
    _save(output / "status.json", status)
    try:
        if not _COMMIT.fullmatch(expected_commit) or not _COMMIT.fullmatch(expected_trusted_revision):
            raise ValueError("expected revisions must be exact Git commits")
        discovery_path = Path(discovery_path)
        if discovery_path.is_symlink() or discovery_path.with_suffix(".sha256").is_symlink():
            raise ValueError("symlink discovery input")
        discovery_bytes = discovery_path.read_bytes()
        discovery_sha = _sha256(discovery_path)
        if discovery_path.with_suffix(".sha256").read_text().strip() != discovery_sha:
            raise ValueError("discovery digest mismatch")
        discovery = json.loads(discovery_bytes)
        if (discovery.get("kind") != "security-campaign-discovery"
                or discovery.get("schema_version") != "v1"
                or discovery.get("repository_commit") != expected_commit
                or discovery.get("trusted_revision") != expected_trusted_revision
                or discovery.get("status") != "validated"):
            raise ValueError("discovery identity or validation state mismatch")
        candidates = [row for row in discovery.get("candidates", []) if row.get("implementation") == "amaru"]
        if not candidates:
            raise ValueError("no Amaru candidate was selected")

        builder = _load_regular(ROOT / "infrastructure/docker/amaru-stock-builder.identity.json")["builder"]
        stock_build = Path(stock_build)
        summary = _load_regular(stock_build / "summary.json")
        lanes = sorted(path for path in stock_build.glob("stock-*") if path.is_dir() and not path.is_symlink())
        if summary.get("statuses") != ["built"] * len(candidates) or len(lanes) != len(candidates):
            raise ValueError("stock-build summary or lane count differs from discovery")

        discovered = {(row["source_path"], row["repository_commit"], row["source_tree_digest"]): row
                      for row in candidates}
        seen = set()
        for lane in lanes:
            receipt = _load_regular(lane / "receipt.json")
            key = (receipt.get("source_path"), receipt.get("repository_commit"), receipt.get("source_tree_digest"))
            if (receipt.get("kind") != "amaru-network-stock-build" or receipt.get("status") != "built"
                    or receipt.get("builder_image") != builder or key not in discovered or key in seen
                    or receipt.get("runtime_image_name") != "dwarf/amaru-candidate:" + str(key[2])[:16]):
                raise ValueError("stock-build receipt identity differs from discovery")
            seen.add(key)
            files = receipt.get("files")
            if not isinstance(files, dict):
                raise ValueError("stock-build receipt files are missing")
            verified = {}
            for name in _REQUIRED:
                path = lane / name
                expected = files.get(name)
                if path.is_symlink() or not path.is_file() or not _HEX.fullmatch(str(expected)) or _sha256(path) != expected:
                    raise ValueError(f"stock-build artifact failed verification: {name}")
                verified[name] = expected
            row = discovered[key]
            status["candidates"].append({
                "candidate_name": row["candidate_name"],
                "repository_commit": key[1],
                "source_tree_digest": key[2],
                "builder_image": builder,
                "runtime_image_name": receipt.get("runtime_image_name"),
                "lane": lane.name,
                "files": verified,
            })
        if len(seen) != len(discovered):
            raise ValueError("a discovered Amaru candidate has no unique stock build")
        status.update(status="staged", discovery_sha256=discovery_sha,
                      reason="hosted-campaign-runtime-registration-pending")
        if run is not None:
            for candidate in status["candidates"]:
                archive = stock_build / candidate["lane"] / "runtime.docker.tar"
                run(["docker", "load", "--input", str(archive)], check=True, capture_output=True,
                    text=True, timeout=300)
                image_name = candidate["runtime_image_name"]
                inspected = run(["docker", "image", "inspect", image_name], check=True,
                                capture_output=True, text=True, timeout=60)
                decoded = json.loads(inspected.stdout)
                image = decoded[0] if isinstance(decoded, list) and len(decoded) == 1 else None
                config_id = image.get("Id") if isinstance(image, dict) else None
                if (not re.fullmatch(r"sha256:[0-9a-f]{64}", str(config_id))
                        or image.get("Os") != "linux" or image.get("Architecture") != "amd64"
                        or image_name not in (image.get("RepoTags") or [])
                        or (image.get("Config") or {}).get("Entrypoint") != ["/usr/local/bin/amaru"]):
                    raise ValueError("loaded runtime image identity or platform differs")
                candidate["runtime_image_config_id"] = config_id
            status["reason"] = "hosted-campaign-plan-registration-pending"
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        status.update(status="error", reason="hosted-build-transfer-invalid", detail=str(exc)[:2048])
    _save(output / "status.json", status)
    return status


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--discovery", type=Path, required=True)
    parser.add_argument("--stock-build", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--expected-trusted-revision", required=True)
    args = parser.parse_args(argv)
    result = stage_hosted_build(args.discovery, args.stock_build, args.directory,
                                expected_commit=args.expected_commit,
                                expected_trusted_revision=args.expected_trusted_revision,
                                run=subprocess.run)
    print(result["reason"])
    return 0 if result["status"] == "staged" else 1


if __name__ == "__main__":
    raise SystemExit(main())
