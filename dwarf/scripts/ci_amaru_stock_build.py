"""Hosted Amaru stock build with recorded network fetch; no offline claim.

The testplease push workflow invokes this path on a disposable GitHub runner.
It does not relax the separate launch runtime, patched or coverage lanes.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import uuid

from profile_manager.candidate_build_executor import (
    _discovered_candidates, _owned_directory, _save, _snapshot, _SourceBudget,
)
from profile_manager.candidate_builds import plan_candidate_builds

BUILDER_REPOSITORY = "ghcr.io/j-gainsec/dwarf-amaru-builder"
RUNTIME_BASE_IMAGE = (
    "docker.io/library/busybox:1.37.0-musl@"
    "sha256:dc88b80842580654294472735332ec7c7789a4010ec9a68b69f9b88c19abede6"
)
RUST = "nightly-2026-09-04"
NETWORK = "dependency-fetch-allowed; not-hermetic; not-offline"
CONFIGS_REPOSITORY = "cardano-foundation/cardano-configurations"
PEER_NETWORKS = ("mainnet", "preprod", "preview")
# Fixed trusted script. No candidate metadata is interpolated into shell text.
BUILD_SCRIPT = """set -eu
export HOME=/work CARGO_HOME=/work/cargo CARGO_TARGET_DIR=/work/target
mkdir -p /work/source
cp -R /source/. /work/source/
rm -rf /work/source/.git
for network in mainnet preprod preview; do
  mkdir -p "/work/source/crates/amaru-node/config/peer-snapshots/$network"
  cp "/peer-snapshots/$network/peer-snapshot.json" \
    "/work/source/crates/amaru-node/config/peer-snapshots/$network/peer-snapshot.json"
done
cd /work/source
git init
git config user.name DWARF
git config user.email dwarf@invalid.example
git add --all --force
GIT_AUTHOR_DATE="$DWARF_SOURCE_DATE" GIT_COMMITTER_DATE="$DWARF_SOURCE_DATE" \
  git commit --no-gpg-sign -m "DWARF synthetic candidate snapshot $DWARF_SOURCE_COMMIT"
python3 - <<'PY'
import json, os, subprocess
commit = subprocess.run(['git', 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()
with open('/work/synthetic-git.json', 'w') as stream:
    json.dump({'kind': 'dwarf-synthetic-git-identity', 'commit': commit,
               'submission_commit': os.environ['DWARF_SOURCE_COMMIT'],
               'submission_time': os.environ['DWARF_SOURCE_DATE']}, stream, sort_keys=True)
PY
export AMARU_SKIP_PEER_SNAPSHOT_FETCH=1
rustc --version
cargo --version
cargo-zigbuild --version
python-zig version
cargo fetch --locked --target x86_64-unknown-linux-musl
cargo metadata --locked --format-version 1 > /work/fetch-metadata.json
cargo zigbuild --locked --release -p amaru --bin amaru --target x86_64-unknown-linux-musl
cp /work/target/x86_64-unknown-linux-musl/release/amaru /work/executable
"""


def validate_builder(image):
    if not isinstance(image, str) or not re.fullmatch(re.escape(BUILDER_REPOSITORY) + r"@sha256:[0-9a-f]{64}", image):
        raise ValueError("builder must be the allowed GHCR repository pinned by @sha256")
    return image


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _fetch(url, headers):
    request = Request(url, headers=headers)
    with urlopen(request, timeout=120) as response:
        return response.read()


def prepare_peer_snapshots(directory, submission_time, *, fetch=_fetch):
    """Resolve and retain real peer snapshots using only fixed upstream URLs."""
    try:
        parsed_time = datetime.fromisoformat(submission_time.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValueError("submission time must be ISO-8601") from exc
    if parsed_time.tzinfo is None:
        raise ValueError("submission time must include a timezone")
    directory = Path(directory).absolute()
    if directory.exists() or directory.is_symlink():
        raise ValueError("peer snapshot output already exists")
    directory.mkdir(parents=True, mode=0o700)
    token = os.environ.get("GITHUB_API_TOKEN", "")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "dwarf-github-runner"}
    if token:
        headers["Authorization"] = "Bearer " + token
    query = urlencode({"until": submission_time, "per_page": 1})
    api_url = f"https://api.github.com/repos/{CONFIGS_REPOSITORY}/commits?{query}"
    try:
        rows = json.loads(fetch(api_url, headers))
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
        raise ValueError("configs commit response is not JSON") from exc
    commit = rows[0].get("sha") if isinstance(rows, list) and rows and isinstance(rows[0], dict) else None
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("configs commit response has no exact commit")
    receipt = {"kind": "amaru-peer-snapshot-inputs", "schema_version": "v1",
               "repository": CONFIGS_REPOSITORY, "configs_commit": commit,
               "submission_time": submission_time, "commit_query": api_url, "files": {}, "urls": {}}
    for network in PEER_NETWORKS:
        url = (f"https://raw.githubusercontent.com/{CONFIGS_REPOSITORY}/{commit}/"
               f"network/{network}/cardano-node/peer-snapshot.json")
        body = fetch(url, {"User-Agent": "dwarf-github-runner"})
        try:
            document = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
            raise ValueError(f"peer snapshot for {network} is not JSON") from exc
        if not isinstance(document, (dict, list)) or not body:
            raise ValueError(f"peer snapshot for {network} has invalid JSON content")
        path = directory / network / "peer-snapshot.json"
        path.parent.mkdir(mode=0o700)
        path.write_bytes(body)
        receipt["files"][network] = sha256(path)
        receipt["urls"][network] = url
    (directory / "receipt.json").write_text(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n")
    return receipt


def _validated_peer_snapshots(directory, source_date):
    directory = Path(directory).absolute()
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("peer snapshot input directory is missing")
    receipt_path = directory / "receipt.json"
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise ValueError("peer snapshot receipt is missing")
    receipt = json.loads(receipt_path.read_text())
    if (receipt.get("kind") != "amaru-peer-snapshot-inputs"
            or receipt.get("submission_time") != source_date
            or not re.fullmatch(r"[0-9a-f]{40}", str(receipt.get("configs_commit", "")))):
        raise ValueError("peer snapshot receipt identity mismatch")
    for network in PEER_NETWORKS:
        path = directory / network / "peer-snapshot.json"
        if path.is_symlink() or not path.is_file() or receipt.get("files", {}).get(network) != sha256(path):
            raise ValueError(f"peer snapshot input failed verification: {network}")
    return receipt


def build_stock(candidate, *, directory, builder_image=None, source_date=None, peer_snapshots=None,
                declared=None, run=subprocess.run):
    image = validate_builder(builder_image)  # before any Docker pull/run
    snapshot_receipt = _validated_peer_snapshots(peer_snapshots, source_date)
    output = _owned_directory(directory)
    receipt = {"kind": "amaru-network-stock-build", "status": "partial", "ready": False,
               "builder_image": image, "network": NETWORK, "dependency_closure": "not-retained",
               "runtime_base_image": RUNTIME_BASE_IMAGE,
               "repository_commit": candidate.repository_commit, "source_tree_digest": candidate.source_tree_digest,
               "source_date": source_date, "peer_snapshot_inputs": snapshot_receipt,
               "files": {}, "commands": BUILD_SCRIPT.splitlines()}
    _save(output / "receipt.json", receipt)
    worker_output = output / "worker-output"
    worker_output.mkdir(mode=0o700)
    name = "dwarf-amaru-stock-" + uuid.uuid4().hex
    started = False
    try:
        budget = _SourceBudget()
        with _snapshot(candidate, budget) as frozen:
            if frozen.implementation != "amaru":
                raise ValueError("only Amaru stock is supported")
            plans = plan_candidate_builds(frozen)
            if declared is not None and declared != [
                    {key: getattr(p, key) for key in ("variant", "plan_digest", "recipe_digest")} for p in plans]:
                raise ValueError("discovered trusted recipes changed")
            plan = plans[0]
            if dict(plan.toolchain).get("rust") != RUST:
                raise ValueError("candidate Rust identity differs from this exact builder")
            shutil.copyfile(frozen.source_root / "Cargo.lock", output / "Cargo.lock")
            shutil.copyfile(Path(peer_snapshots) / "receipt.json", output / "peer-snapshots.json")
            receipt["source_path"] = f"dwarf/testplease/amaru/{frozen.candidate_name}"
            receipt["recipe_sha256"] = sha256(Path(__file__))
            _save(output / "fetch-policy.json", {"network": NETWORK, "builder_image": image,
                  "cargo_lock_sha256": sha256(output / "Cargo.lock"), "cargo_home": "fresh invocation-owned; no shared cache"})
            with (output / "build.log").open("wb") as log:
                worker_started = time.monotonic()
                run(["docker", "pull", image], check=True, stdout=log, stderr=subprocess.STDOUT, timeout=600)
                # Container has no host socket/credentials, and only its own output mount.
                started = True
                run(["docker", "run", "--name", name, "--platform", "linux/amd64", "--read-only",
                     "--user", f"{os.getuid()}:{os.getgid()}", "--cap-drop=ALL", "--security-opt", "no-new-privileges",
                     "--cpus", "2", "--memory", "8g", "--pids-limit", "256", "--network", "bridge", "--tmpfs", "/tmp:rw,nosuid,nodev",
                     "--env", f"DWARF_SOURCE_COMMIT={candidate.repository_commit}",
                     "--env", f"DWARF_SOURCE_DATE={source_date}",
                     "--mount", f"type=bind,src={frozen.source_root},dst=/source,readonly",
                     "--mount", f"type=bind,src={Path(peer_snapshots).absolute()},dst=/peer-snapshots,readonly",
                     "--mount", f"type=bind,src={worker_output},dst=/work", image, "/bin/sh", "-c", BUILD_SCRIPT],
                    check=True, stdout=log, stderr=subprocess.STDOUT, timeout=3600)
                # Candidate code can write only staging, never retained evidence.
                for filename in ("executable", "fetch-metadata.json", "synthetic-git.json"):
                    staged = worker_output / filename
                    if staged.is_symlink() or not staged.is_file():
                        raise ValueError(f"missing regular worker output: {filename}")
                    shutil.copyfile(staged, output / filename)
                # Package only verified regular binary bytes in an isolated context.
                binary = output / "executable"
                if binary.is_symlink() or not binary.is_file():
                    raise ValueError("missing regular executable")
                context = output / "runtime-context"
                context.mkdir()
                shutil.copyfile(binary, context / "executable")
                (context / "executable").chmod(0o555)
                (context / "Dockerfile").write_text(
                    f"FROM {RUNTIME_BASE_IMAGE}\n"
                    "COPY executable /usr/local/bin/amaru\n"
                    'ENTRYPOINT ["/usr/local/bin/amaru"]\n'
                )
                archive = output / "runtime.docker.tar"
                if archive.exists() or archive.is_symlink():
                    raise ValueError("runtime archive path was populated by candidate")
                runtime_name = "dwarf/amaru-candidate:" + frozen.source_tree_digest[:16]
                receipt["runtime_image_name"] = runtime_name
                run(["docker", "buildx", "build", "--network=none", "--platform", "linux/amd64", "--provenance=false",
                     "--output", f"type=docker,name={runtime_name},dest={output / 'runtime.docker.tar'}", str(context)],
                    check=True, stdout=log, stderr=subprocess.STDOUT, timeout=600)
                # The bounded source freeze has finished; Docker subprocesses
                # enforce their own build deadlines independently.
                budget.started += time.monotonic() - worker_started
        receipt["status"] = "built"
    except Exception as exc:
        receipt.update(status="error", detail=str(exc)[:2048])
    finally:
        if started:
            try:
                run(["docker", "rm", "--force", name], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, timeout=60)
                _save(output / "cleanup.log", {"owned_container_removed": True})
            except Exception as exc:
                receipt.update(status="error", detail="worker cleanup failed: " + str(exc)[:2048])
        # Keep requested evidence, not Cargo caches or intermediate build trees.
        for dirname in ("worker-output", "runtime-context"):
            path = output / dirname
            if path.is_symlink():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
        for filename in ("Cargo.lock", "fetch-policy.json", "fetch-metadata.json", "synthetic-git.json",
                         "peer-snapshots.json", "build.log", "cleanup.log", "executable", "runtime.docker.tar"):
            path = output / filename
            if path.is_file() and not path.is_symlink():
                receipt["files"][filename] = sha256(path)
        if receipt["status"] == "built" and not {"Cargo.lock", "fetch-metadata.json", "synthetic-git.json",
                "peer-snapshots.json", "build.log", "executable", "runtime.docker.tar"} <= receipt["files"].keys():
            receipt.update(status="error", detail="required retained build output missing")
        _save(output / "receipt.json", receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--builder-image", required=True)
    parser.add_argument("--validate-builder-only", action="store_true")
    parser.add_argument("--discovery", type=Path)
    parser.add_argument("--source-repository", type=Path)
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--expected-commit")
    parser.add_argument("--expected-trusted-revision")
    args = parser.parse_args(argv)
    validate_builder(args.builder_image)
    if args.validate_builder_only:
        return 0
    if not all((args.discovery, args.source_repository, args.directory, args.expected_commit, args.expected_trusted_revision)):
        parser.error("build requires discovery, source, output and exact commit identities")
    content = args.discovery.read_bytes()
    if hashlib.sha256(content).hexdigest() != args.discovery.with_suffix(".sha256").read_text().strip():
        raise ValueError("discovery digest mismatch")
    selected = _discovered_candidates(json.loads(content), args.source_repository, args.expected_commit, args.expected_trusted_revision)
    output = _owned_directory(args.directory)
    date_result = subprocess.run(["git", "show", "-s", "--format=%cI", args.expected_commit],
                                 cwd=args.source_repository, check=True, capture_output=True, text=True, timeout=30)
    source_date = date_result.stdout.strip()
    peer_snapshots = output / "peer-snapshots"
    prepare_peer_snapshots(peer_snapshots, source_date)
    statuses = []
    for candidate, declared in selected:
        if candidate.implementation != "amaru":
            continue  # no other implementation/lane executes
        statuses.append(build_stock(candidate, directory=output / f"stock-{len(statuses):04d}",
                                    builder_image=args.builder_image, source_date=source_date,
                                    peer_snapshots=peer_snapshots, declared=declared)["status"])
    _save(output / "summary.json", {"statuses": statuses, "network": NETWORK,
          "other_lanes": "blocked", "runtime_ready": False, "published": False})
    return 0 if statuses and all(s == "built" for s in statuses) else 1


if __name__ == "__main__":
    raise SystemExit(main())
