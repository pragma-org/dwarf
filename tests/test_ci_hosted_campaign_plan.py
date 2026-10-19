"""Focused contracts for hosted candidate registration and campaign planning."""
import hashlib
import json
from pathlib import Path

from scripts import ci_hosted_campaign_plan as api


COMMIT = "a" * 40
TRUSTED = "b" * 40
TREE = "c" * 64
CONFIG = "sha256:" + "d" * 64
MANIFEST = "sha256:" + "e" * 64


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")


def inputs(tmp_path):
    discovery = tmp_path / "discovery.json"
    save(discovery, {"kind": "security-campaign-discovery", "schema_version": "v1",
         "status": "validated", "repository_commit": COMMIT, "trusted_revision": TRUSTED,
         "candidates": [{"implementation": "amaru", "candidate_name": "candidate",
                         "source_path": "dwarf/testplease/amaru/candidate",
                         "repository_commit": COMMIT, "source_tree_digest": TREE}]})
    discovery.with_suffix(".sha256").write_text(hashlib.sha256(discovery.read_bytes()).hexdigest() + "\n")
    stock = tmp_path / "stock"
    lane = stock / "stock-0000"
    lane.mkdir(parents=True)
    executable = lane / "executable"
    executable.write_bytes(b"candidate executable")
    metadata = {"packages": [{"name": "amaru", "version": "10.11.0",
                 "targets": [{"name": "amaru", "kind": ["bin"]}]}]}
    save(lane / "fetch-metadata.json", metadata)
    save(lane / "receipt.json", {"kind": "amaru-network-stock-build", "status": "built",
         "repository_commit": COMMIT, "source_tree_digest": TREE,
         "source_path": "dwarf/testplease/amaru/candidate",
         "runtime_image_name": "dwarf/amaru-candidate:" + TREE[:16],
         "files": {"executable": hashlib.sha256(executable.read_bytes()).hexdigest(),
                   "fetch-metadata.json": hashlib.sha256((lane / "fetch-metadata.json").read_bytes()).hexdigest()}})
    stage = tmp_path / "stage.json"
    save(stage, {"kind": "hosted-campaign-stage", "schema_version": "v1", "status": "staged",
         "reason": "hosted-campaign-plan-registration-pending", "ready_for_campaign": False,
         "discovery_sha256": hashlib.sha256(discovery.read_bytes()).hexdigest(),
         "candidates": [{"candidate_name": "candidate", "repository_commit": COMMIT,
                         "source_tree_digest": TREE, "lane": "stock-0000",
                         "runtime_image_name": "dwarf/amaru-candidate:" + TREE[:16],
                         "runtime_image_config_id": CONFIG,
                         "files": {"executable": hashlib.sha256(executable.read_bytes()).hexdigest(),
                                   "fetch-metadata.json": hashlib.sha256((lane / "fetch-metadata.json").read_bytes()).hexdigest()}}]})
    return discovery, stock, stage


def test_hosted_candidate_is_registered_and_frozen_without_inventing_compatibility(tmp_path):
    discovery, stock, stage = inputs(tmp_path)
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[:3] == ["docker", "image", "inspect"]:
            reference = api.REGISTRY_REPOSITORY + ":" + TREE[:16]
            return type("Result", (), {"stdout": json.dumps([{
                "Id": CONFIG, "RepoDigests": [api.REGISTRY_REPOSITORY + "@" + MANIFEST],
                "RepoTags": [reference], "Os": "linux", "Architecture": "amd64",
                "Config": {"Entrypoint": ["/usr/local/bin/amaru"]}}]),
                "stderr": "", "returncode": 0})()
        return type("Result", (), {"stdout": "", "stderr": "", "returncode": 0})()

    result = api.plan_hosted_campaign(discovery, stage, stock, tmp_path / "out", run=run)
    assert result["status"] == "planned"
    assert result["ready_for_campaign"] is False
    assert result["reason"] == "runtime-compatibility-qualification-pending"
    assert result["candidate_version"] == "10.11.0"
    assert result["supporting_cardano_version"] == "11.1.2"
    assert result["selected_entry_ids"] == list(api.INITIAL_SECURITY_ENTRIES)
    assert [call[:3] for call in calls] == [["docker", "image", "tag"],
                                            ["docker", "image", "push"],
                                            ["docker", "image", "inspect"]]
    manifest = json.loads((tmp_path / "out/manifest.json").read_text())
    assert len(manifest["entries"]) == 6
    assert len(manifest["required_tap_cells"]) == 23
    assert len({(cell["implementation"], cell["measurement_id"])
                for cell in manifest["required_tap_cells"]}) == 23
    assert {tuple(cell["target_modes"]) for cell in manifest["required_tap_cells"]} == {("stock",)}
    assert {entry["execution"]["compatibility"]["status"] for entry in manifest["entries"]} == {"pending-prerequisites"}
    assert all(not entry["contract"]["scenario_id"].endswith("10.11.0") for entry in manifest["entries"])
    catalog = json.loads((tmp_path / "out/version-catalog.json").read_text())
    candidate = next(row for row in catalog["releases"] if row["implementation"] == "amaru" and row["version"] == "10.11.0")
    assert candidate["source_revision"] == COMMIT
    assert candidate["artifacts"][0]["reference"] == api.REGISTRY_REPOSITORY
    assert candidate["artifacts"][0]["digest"] == MANIFEST
    assert candidate["artifacts"][0]["config_image_id"] == CONFIG
    assert candidate["artifacts"][0]["runtime_interface"] == "extracted-binary"
    assert all(pair["amaru_version"] != "10.11.0" for pair in catalog["compatibility_pairs"])
    builds = json.loads((tmp_path / "out/build-observations.json").read_text())
    assert builds[0]["artifacts"]["image_digest"] == MANIFEST
    assert builds[0]["runtime_image_config_id"] == CONFIG


def test_hosted_plan_rejects_loaded_image_identity_change(tmp_path):
    discovery, stock, stage = inputs(tmp_path)

    def run(argv, **kwargs):
        if argv[:3] == ["docker", "image", "inspect"]:
            return type("Result", (), {"stdout": json.dumps([{
                "Id": "sha256:" + "0" * 64,
                "RepoDigests": [api.REGISTRY_REPOSITORY + "@" + MANIFEST],
                "RepoTags": [api.REGISTRY_REPOSITORY + ":" + TREE[:16]],
                "Os": "linux", "Architecture": "amd64",
                "Config": {"Entrypoint": ["/usr/local/bin/amaru"]}}]),
                "stderr": "", "returncode": 0})()
        return type("Result", (), {"stdout": "", "stderr": "", "returncode": 0})()

    result = api.plan_hosted_campaign(discovery, stage, stock, tmp_path / "out", run=run)
    assert result["status"] == "error"
    assert result["reason"] == "hosted-campaign-plan-invalid"
