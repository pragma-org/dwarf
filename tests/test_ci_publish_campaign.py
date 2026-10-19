from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

from profile_manager import campaign_publication
from profile_manager.security_campaign import resolve_security_campaign
from profile_manager.security_campaign_suite import load_measurement_suite, load_security_campaign_suite
from profile_manager.testplease_candidates import CandidateSnapshot
from scripts import ci_publish_campaign as api
from scripts.runtime_bundle_sign import DEFAULT_EXCLUDED_RELPATHS, canonical_manifest_bytes, canonical_signature_payload_bytes

ROOT = Path(__file__).resolve().parents[1]


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")


def signature(key, run_id, body, actor="ci", source="workflow-key"):
    digest = hashlib.sha256(body).hexdigest()
    manifest = {f"{run_id}-bundle-export.tar.gz": digest}
    signed = canonical_signature_payload_bytes(manifest=manifest, target_run_id=run_id,
        signing_actor=actor, excluded_relpaths=DEFAULT_EXCLUDED_RELPATHS)
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    return {"schema_version": "v2", "target_run_id": run_id, "signing_actor": actor,
        "excluded_relpaths": sorted(DEFAULT_EXCLUDED_RELPATHS), "public_key_hex": public,
        "signature_hex": key.sign(signed).hex(),
        "manifest_sha256": hashlib.sha256(canonical_manifest_bytes(manifest)).hexdigest(),
        "signed_payload_sha256": hashlib.sha256(signed).hexdigest(), "key_source": source,
        "operator_warning": False, "signing_unavailable": False}


@pytest.fixture
def inputs(tmp_path):
    source = tmp_path / "source/crates/amaru/src"
    source.mkdir(parents=True)
    (source.parents[2] / "Cargo.toml").write_text('[workspace]\nmembers=["crates/amaru"]\n')
    (source.parent / "Cargo.toml").write_text('[package]\nname="amaru"\nversion="99.0.0"\n')
    (source / "main.rs").write_text("fn main() {}\n")
    candidate = CandidateSnapshot("amaru", "proposal", "a" * 40, "b" * 64, source.parents[2])
    campaign = resolve_security_campaign(candidate,
        load_security_campaign_suite(ROOT / "dwarf/security-suites/github-security-regression-v1.yaml"),
        load_measurement_suite(ROOT / "dwarf/qualification-suites/node-measurements-v1.yaml"),
        selection={"entry_ids": ["amaru-cbor-tx-body-fuzz-structured"],
                   "implementations": {"amaru": {"kind": "candidate", "version": "v99.0.0"}}},
        catalog={"schema_version": 1, "updated_at": "2026-10-19T00:00:00Z", "releases": [], "compatibility_pairs": []})
    common = {"schema_version": "v1", "campaign_id": campaign["campaign_id"],
              "campaign_digest": campaign["campaign_digest"], "evidence_digest": "sha256:" + "d" * 64}
    evaluation = {
        "security_validity": {**common, "kind": "campaign-security-validity", "security_valid": True,
            "classification": "finding", "entries": [{"run_id": "run-1"}]},
        "measurement_completeness": {**common, "kind": "campaign-measurement-completeness",
            "measurement_complete": True, "cells": []},
    }
    runtime = tmp_path / "runtime"
    export = runtime / "runs/run-1/outputs/export"
    export.mkdir(parents=True)
    body = b"opaque campaign archive"
    archive = export / "run-1-bundle-export.tar.gz"
    archive.write_bytes(body)
    ephemeral = Ed25519PrivateKey.generate()
    dump(export / "signature.json", signature(ephemeral, "run-1", body))
    receipt = {"run_id": "run-1", "archive": "/previous/runner/archive",
        "signature": "/previous/runner/signature",
        "archive_sha256": hashlib.sha256(body).hexdigest(),
        "signature_sha256": hashlib.sha256((export / "signature.json").read_bytes()).hexdigest()}
    state = {"schema_version": "v1", "campaign_digest": campaign["campaign_digest"],
        "readiness": {"passed": True}, "cleanup_errors": [], "evaluation": evaluation,
        "cells": {"security:case": {"status": "complete", "result": {"run_id": "run-1"},
                                      "archives": [receipt]}}}
    publisher = Ed25519PrivateKey.generate()
    public = publisher.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    signer = "ed25519:" + hashlib.sha256(bytes.fromhex(public)).hexdigest()
    trust = {"schema_version": "v1", "kind": "campaign-trusted-publishers", "publishers": [
        {"signer_id": signer, "algorithm": "ed25519", "public_key_hex": public, "status": "active"}]}
    manifest_path, state_path, trust_path, key_path = (tmp_path / name for name in
        ("manifest.json", "state.json", "trusted.json", "publisher.pem"))
    dump(manifest_path, campaign); dump(state_path, state); dump(trust_path, trust)
    key_path.write_bytes(publisher.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    return campaign, state, trust, manifest_path, state_path, trust_path, key_path, runtime, tmp_path / "published"


def test_prepare_reverifies_and_resigns_opaque_campaign_archives(inputs):
    campaign, state, trust, manifest, state_path, trust_path, key, runtime, output = inputs
    metadata = api.prepare(manifest_path=manifest, state_path=state_path, runtime_root=runtime,
        trusted_publishers_path=trust_path, signing_key_path=key, output=output,
        repository="pragma-org/dwarf", published_at="2026-10-19T00:00:00Z")
    assert metadata["campaign_id"] == campaign["campaign_id"]
    assert sorted(path.name for path in (output / "assets").iterdir()) == metadata["assets"]
    catalog = json.loads((output / "campaigns.json").read_text())
    campaign_publication.validate_catalog(catalog, trust)
    defaults = json.loads((output / "default-runs.json").read_text())
    assert defaults["archives"][0]["run_id"] == "run-1"
    assert defaults["archives"][0]["release_tag"] == campaign["campaign_id"]
    assert defaults["archives"][0]["url"].startswith("runs/run-1-")


def test_prepare_rejects_a_post_run_archive_change(inputs):
    *_, runtime, output = inputs
    (runtime / "runs/run-1/outputs/export/run-1-bundle-export.tar.gz").write_bytes(b"changed")
    with pytest.raises(ValueError, match="receipt digest mismatch"):
        api.prepare(manifest_path=inputs[3], state_path=inputs[4], runtime_root=runtime,
            trusted_publishers_path=inputs[5], signing_key_path=inputs[6], output=output,
            repository="pragma-org/dwarf", published_at="2026-10-19T00:00:00Z")


def test_default_manifest_prefers_current_results_and_stays_bounded():
    previous = {"schema_version": api._DEFAULT_SCHEMA,
                "archives": [{"run_id": f"old-{i}", "size_bytes": 10} for i in range(10)]}
    current = [{"run_id": f"new-{i}", "size_bytes": 10} for i in range(3)]
    result = api.update_default_manifest(previous, current)
    assert len(result["archives"]) == 10
    assert [row["run_id"] for row in result["archives"][-3:]] == ["new-0", "new-1", "new-2"]
