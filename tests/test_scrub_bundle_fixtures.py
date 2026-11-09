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


def test_mac_homes_private_addresses_and_signing_keys_are_masked(tmp_path):
    """Campaign bundles for dwarf/examples (2026-11-09): Mac home paths, RFC 1918 addresses and devnet
    signing keys are masked; public addresses and version numbers are left alone."""
    import json

    from scripts import scrub_bundle_fixtures as scrub

    run = tmp_path / "run1"
    (run / "outputs").mkdir(parents=True)
    log = run / "outputs" / "log.txt"
    log.write_text("bundle at /Users/alice/work/x; peers 10.192.7.4:3001 172.20.0.3 192.168.30.16 "
                   "public 172.233.144.9 version 10.11.20261029 node 11.1.2\n", encoding="utf-8")
    key = run / "outputs" / "utxo.skey"
    key.write_text(json.dumps({"type": "PaymentSigningKeyShelley_ed25519", "description": "Payment Signing Key",
                               "cborHex": "5820" + "ab" * 32}), encoding="utf-8")
    vkey = run / "outputs" / "utxo.vkey"
    vkey.write_text(json.dumps({"type": "PaymentVerificationKeyShelley_ed25519", "description": "",
                                "cborHex": "5820" + "cd" * 32}), encoding="utf-8")

    assert scrub.remaining_host_details(tmp_path, private_ips=True, signing_keys=True)
    scrub.scrub_tree(tmp_path, private_ips=True, signing_keys=True)
    text = log.read_text(encoding="utf-8")
    assert "/Users/alice" not in text and "/home/dwarf/work/x" in text
    assert "10.192.7.4" not in text and "172.20.0.3" not in text and "192.168.30.16" not in text
    assert "192.0.2.4:3001" in text
    assert "172.233.144.9" in text and "10.11.20261029" in text and "11.1.2" in text
    body = json.loads(key.read_text(encoding="utf-8"))
    assert body["type"] == "PaymentSigningKeyShelley_ed25519" and "ab" * 32 not in body["cborHex"]
    assert json.loads(vkey.read_text(encoding="utf-8"))["cborHex"] == "5820" + "cd" * 32
    assert scrub.remaining_host_details(tmp_path, private_ips=True, signing_keys=True) == []
