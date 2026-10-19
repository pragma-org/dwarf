"""Offline contracts for the manually enabled network-fetch stock path."""
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from test_candidate_builds import COMMIT, source_candidate

ROOT = Path(__file__).resolve().parents[1]
PIN = "ghcr.io/j-gainsec/dwarf-amaru-builder@sha256:" + "a" * 64


def api():
    from scripts import ci_amaru_stock_build
    return ci_amaru_stock_build


def snapshots(tmp_path):
    root = tmp_path / "peer-snapshots"
    files = {}
    for network in ("mainnet", "preprod", "preview"):
        path = root / network / "peer-snapshot.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"network":"' + network + '"}')
        files[network] = hashlib.sha256(path.read_bytes()).hexdigest()
    (root / "receipt.json").write_text(json.dumps({"kind": "amaru-peer-snapshot-inputs",
        "configs_commit": "d" * 40, "submission_time": "2026-10-19T12:00:00+00:00", "files": files}))
    return root


@pytest.mark.parametrize("image", [None, "ghcr.io/j-gainsec/dwarf-amaru-builder:latest", "ghcr.io/other/builder@sha256:" + "a" * 64])
def test_unconfigured_mutable_or_foreign_builder_never_launches(tmp_path, image):
    calls = []
    with pytest.raises(ValueError, match="builder"):
        api().build_stock(source_candidate(tmp_path / "source"), directory=tmp_path / "out", builder_image=image,
                          source_date="2026-10-19T12:00:00+00:00", peer_snapshots=snapshots(tmp_path),
                          run=lambda *a, **k: calls.append(a))
    assert calls == []


def test_fixed_locked_build_retains_network_metadata_logs_and_computed_hashes(tmp_path):
    candidate = source_candidate(tmp_path / "source")
    peer_snapshots = snapshots(tmp_path)
    calls = []
    runtime_dockerfile = []
    def run(argv, **kwargs):
        calls.append(argv)
        if argv[:2] == ["docker", "run"]:
            work = Path(next(value for value in argv if value.startswith("type=bind,src=") and ",dst=/work" in value).split(",")[1].removeprefix("src="))
            assert work != tmp_path / "out"
            assert not any((work / name).exists() for name in ("receipt.json", "Cargo.lock", "fetch-policy.json", "build.log", "cleanup.log"))
            (work / "executable").write_bytes(b"synthetic executable")
            (work / "fetch-metadata.json").write_text('{"packages":[]}')
            (work / "synthetic-git.json").write_text('{"commit":"' + "e" * 40 + '"}')
            kwargs["stdout"].write(b"fetch and build log\n")
        if argv[:3] == ["docker", "buildx", "build"]:
            runtime_dockerfile.append(
                (Path(argv[-1]) / "Dockerfile").read_text(encoding="utf-8")
            )
            output = Path(argv[argv.index("--output") + 1].split("dest=", 1)[1])
            output.write_bytes(b"synthetic Docker archive")
    receipt = api().build_stock(candidate, directory=tmp_path / "out", builder_image=PIN,
                                source_date="2026-10-19T12:00:00+00:00", peer_snapshots=peer_snapshots, run=run)
    assert receipt["status"] == "built" and receipt["ready"] is False
    assert receipt["network"] == "dependency-fetch-allowed; not-hermetic; not-offline"
    assert receipt["builder_image"] == PIN and receipt["repository_commit"] == COMMIT
    assert receipt["source_tree_digest"] == candidate.source_tree_digest
    for name in ("Cargo.lock", "fetch-metadata.json", "synthetic-git.json", "peer-snapshots.json",
                 "build.log", "executable", "runtime.docker.tar"):
        assert receipt["files"][name] == hashlib.sha256((tmp_path / "out" / name).read_bytes()).hexdigest()
    assert "cargo zigbuild --locked" in api().BUILD_SCRIPT
    assert "cargo fetch --locked" in api().BUILD_SCRIPT
    assert "AMARU_SKIP_PEER_SNAPSHOT_FETCH=1" in api().BUILD_SCRIPT
    assert "git init" in api().BUILD_SCRIPT
    assert any(argv[:2] == ["docker", "pull"] and argv[-1] == PIN for argv in calls)
    assert runtime_dockerfile == [
        "FROM docker.io/library/busybox:1.37.0-musl@"
        "sha256:dc88b80842580654294472735332ec7c7789a4010ec9a68b69f9b88c19abede6\n"
        "COPY executable /usr/local/bin/amaru\n"
        'ENTRYPOINT ["/usr/local/bin/amaru"]\n'
    ]
    assert receipt["runtime_base_image"] == (
        "docker.io/library/busybox:1.37.0-musl@"
        "sha256:dc88b80842580654294472735332ec7c7789a4010ec9a68b69f9b88c19abede6"
    )
    docker_run = next(argv for argv in calls if argv[:2] == ["docker", "run"])
    assert any(value.endswith(",dst=/peer-snapshots,readonly") for value in docker_run)
    assert "DWARF_SOURCE_DATE=2026-10-19T12:00:00+00:00" in docker_run
    assert calls[-1][:3] == ["docker", "rm", "--force"]
    assert receipt["dependency_closure"] == "not-retained"
    assert not (tmp_path / "out/worker-output").exists()


def test_trusted_peer_snapshot_resolver_retains_exact_commit_urls_and_hashes(tmp_path):
    commit = "d" * 40
    calls = []
    def fetch(url, headers):
        calls.append((url, headers))
        if "/commits?" in url:
            return json.dumps([{"sha": commit}]).encode()
        network = next(name for name in ("mainnet", "preprod", "preview") if f"/network/{name}/" in url)
        return json.dumps({"network": network, "peers": []}).encode()
    result = api().prepare_peer_snapshots(tmp_path / "snapshots", "2026-10-19T12:00:00+00:00", fetch=fetch)
    assert result["configs_commit"] == commit
    assert set(result["files"]) == {"mainnet", "preprod", "preview"}
    assert all(len(value) == 64 for value in result["files"].values())
    assert calls[0][0].startswith("https://api.github.com/repos/cardano-foundation/cardano-configurations/commits?")
    assert all(commit in url for url, _ in calls[1:])


def test_trusted_peer_snapshot_resolver_rejects_non_json_snapshot(tmp_path):
    def fetch(url, headers):
        return json.dumps([{"sha": "d" * 40}]).encode() if "/commits?" in url else b"not json"
    with pytest.raises(ValueError, match="JSON"):
        api().prepare_peer_snapshots(tmp_path / "snapshots", "2026-10-19T12:00:00+00:00", fetch=fetch)


def test_builder_recipe_and_campaign_use_committed_identity_on_hosted_runners():
    dockerfile = (ROOT / "infrastructure/docker/amaru-stock-builder.Dockerfile").read_text()
    assert "FROM rust:bookworm@sha256:e70e2eec3d495fd5c8e0be74adda86507dfac7f51a724fbf9813ff59b2b247c7" in dockerfile
    for pin in ("nightly-2026-09-04", "ziglang==0.15.2", "--version 0.23.4"):
        assert pin in dockerfile
    assert "CARGO_ZIGBUILD_ZIG_COMMAND=/usr/local/bin/python-zig" in dockerfile
    publisher = yaml.safe_load((ROOT / ".github/workflows/dwarf-amaru-builder.yml").read_text())
    events = publisher.get("on", publisher.get(True))
    assert list(events) == ["workflow_dispatch"]
    assert publisher["permissions"] == {"contents": "read"}
    assert publisher["jobs"]["publish"]["permissions"]["packages"] == "write"
    campaign = yaml.safe_load((ROOT / ".github/workflows/dwarf-security-campaign.yml").read_text())
    job = campaign["jobs"]["stock-build"]
    assert job["runs-on"] == "ubuntu-latest"
    assert "needs.discover.outputs.has_work == 'true'" in job["if"]
    assert "inputs." not in str(job)
    assert "container" not in job
    assert job["permissions"] == {"contents": "read"}
    assert "ci_amaru_stock_build" in str(job)
    assert "amaru-stock-builder.identity.json" in str(job)
    assert "docker/login-action" not in str(job)
    identity = json.loads((ROOT / "infrastructure/docker/amaru-stock-builder.identity.json").read_text())
    assert identity["builder"] == (
        "ghcr.io/j-gainsec/dwarf-amaru-builder@"
        "sha256:653a848d80384259b7b31da732d33b2d5e8fe5001a0a38a98b6fc1a9f1e15a9f"
    )
    assert identity["dockerfile_sha256"] == hashlib.sha256(dockerfile.encode()).hexdigest()
