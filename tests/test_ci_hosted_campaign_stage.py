"""Focused contracts for the GitHub-hosted build-to-campaign handoff."""
import hashlib
import json
from pathlib import Path

from scripts import ci_hosted_campaign_stage as api


COMMIT = "a" * 40
TRUSTED = "b" * 40
TREE = "c" * 64


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")))


def inputs(tmp_path):
    discovery = tmp_path / "input/discovery.json"
    document = {"kind": "security-campaign-discovery", "schema_version": "v1", "status": "validated",
                "repository_commit": COMMIT, "trusted_revision": TRUSTED,
                "candidates": [{"implementation": "amaru", "candidate_name": "candidate",
                                "source_path": "dwarf/testplease/amaru/candidate",
                                "repository_commit": COMMIT, "source_tree_digest": TREE}]}
    write_json(discovery, document)
    discovery.with_suffix(".sha256").write_text(hashlib.sha256(discovery.read_bytes()).hexdigest() + "\n")
    stock = tmp_path / "stock"
    lane = stock / "stock-0000"
    files = {}
    for name in api._REQUIRED:
        path = lane / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(("content:" + name).encode())
        files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    builder = json.loads((api.ROOT / "infrastructure/docker/amaru-stock-builder.identity.json").read_text())["builder"]
    write_json(lane / "receipt.json", {"kind": "amaru-network-stock-build", "status": "built",
               "builder_image": builder, "source_path": "dwarf/testplease/amaru/candidate",
               "runtime_image_name": "dwarf/amaru-candidate:" + TREE[:16],
               "repository_commit": COMMIT, "source_tree_digest": TREE, "files": files})
    write_json(stock / "summary.json", {"statuses": ["built"]})
    return discovery, stock


def test_verified_hosted_build_is_loaded_and_inspected_without_claiming_campaign_readiness(tmp_path):
    discovery, stock = inputs(tmp_path)
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        if argv[:3] == ["docker", "image", "inspect"]:
            name = "dwarf/amaru-candidate:" + TREE[:16]
            return type("Result", (), {"stdout": json.dumps([{"Id": "sha256:" + "e" * 64,
                "Os": "linux", "Architecture": "amd64", "RepoTags": [name],
                "Config": {"Entrypoint": ["/usr/local/bin/amaru"]}}]), "stderr": "", "returncode": 0})()
        return type("Result", (), {"stdout": "Loaded image\n", "stderr": "", "returncode": 0})()
    result = api.stage_hosted_build(discovery, stock, tmp_path / "output",
                                    expected_commit=COMMIT, expected_trusted_revision=TRUSTED, run=run)
    assert result["status"] == "staged"
    assert result["ready_for_campaign"] is False
    assert result["reason"] == "hosted-campaign-plan-registration-pending"
    assert result["published"] is False
    assert result["candidates"][0]["files"]["runtime.docker.tar"]
    assert result["candidates"][0]["runtime_image_config_id"] == "sha256:" + "e" * 64
    host_tool = tmp_path / "output/host-tools/bin/amaru"
    assert host_tool.read_bytes() == (stock / "stock-0000/executable").read_bytes()
    assert result["candidates"][0]["host_tool"] == {
        "path": "host-tools/bin/amaru",
        "sha256": hashlib.sha256(host_tool.read_bytes()).hexdigest(),
    }
    assert host_tool.stat().st_mode & 0o777 == 0o555
    assert calls[0][:2] == ["docker", "load"]
    assert calls[1][:3] == ["docker", "image", "inspect"]


def test_tampered_hosted_build_is_rejected(tmp_path):
    discovery, stock = inputs(tmp_path)
    (stock / "stock-0000/runtime.docker.tar").write_bytes(b"tampered")
    result = api.stage_hosted_build(discovery, stock, tmp_path / "output",
                                    expected_commit=COMMIT, expected_trusted_revision=TRUSTED)
    assert result["status"] == "error"
    assert result["reason"] == "hosted-build-transfer-invalid"
