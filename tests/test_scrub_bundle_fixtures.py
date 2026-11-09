"""Fixture bundles are scrubbed of host details and stay internally consistent and verified."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts import runtime_bundle_attestation as attestation
from scripts import scrub_bundle_fixtures as scrub


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fixture(tmp_path: Path) -> Path:
    run = tmp_path / "20260427T000000Z-demo"
    (run / "outputs" / "attestation").mkdir(parents=True)
    env = b'{"host": "example-box", "cwd": "/home/someone/dwarf-fw/runs/x", "net": "10.99.7.2"}\n'
    (run / "env.json").write_bytes(env)
    scenario = b'{"id": "demo", "runtime_root": "/home/someone/devnet"}\n'
    (run / "scenario.yaml").write_bytes(scenario)
    (run / "manifest.json").write_text(json.dumps({"env_sha256": _sha(env), "run_id": run.name}), encoding="utf-8")
    statement = {"scenario_path": "scenario.yaml", "scenario_yaml_sha256": _sha(scenario),
                 "active_profile_id": None, "active_profile_sha256": None, "dashboard_config_sha256": None,
                 "dwarf_source_sha256": "a" * 64, "tooling_versions": {"python": "/home/someone/.venv/bin/python 3.12"}}
    scrub.sign_attestation(run / "outputs" / "attestation" / "attestation.json",
                           target_run_id=run.name, attested_at_utc="2026-04-27T09:00:00Z", statement=statement)
    return tmp_path


def test_scrub_removes_host_details_and_keeps_digests_and_signatures(tmp_path):
    root = _fixture(tmp_path)
    report = scrub.scrub_tree(root, hosts=["example-box"], subnets=["10.99.7"])
    for path in root.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            assert not any(token in text for token in ("/home/someone", "example-box", "10.99.7.")), path
    run = root / "20260427T000000Z-demo"
    manifest = json.loads((run / "manifest.json").read_text())
    assert manifest["env_sha256"] == _sha((run / "env.json").read_bytes())
    body = json.loads((run / "outputs/attestation/attestation.json").read_text())
    assert body["statement"]["scenario_yaml_sha256"] == _sha((run / "scenario.yaml").read_bytes())
    assert body["attested_at_utc"] == "2026-04-27T09:00:00Z"
    assert attestation.verify_attestation(run / "outputs/attestation/attestation.json")["verdict"] == "verified"
    assert report["files_changed"] >= 4 and report["attestations_resigned"] == 1


def test_scrub_is_idempotent(tmp_path):
    root = _fixture(tmp_path)
    scrub.scrub_tree(root, hosts=["example-box"], subnets=["10.99.7"])
    assert scrub.scrub_tree(root, hosts=["example-box"], subnets=["10.99.7"])["files_changed"] == 0
