"""Local publication contracts: no downloads, uploads, extraction or execution."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from jsonschema import Draft202012Validator

from profile_manager import campaign_publication as api
from profile_manager.security_campaign import digest_security_campaign, resolve_security_campaign
from profile_manager.security_campaign_suite import load_security_campaign_suite, load_measurement_suite
from profile_manager.testplease_candidates import discover_candidates
from scripts import runtime_bundle_sign as signing

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def frozen_campaign(tmp_path_factory):
    root = tmp_path_factory.mktemp("publication-candidate")
    source = root / "dwarf/testplease/amaru/proposal"
    (source / "crates/amaru/src").mkdir(parents=True)
    (source / "Cargo.toml").write_text('[workspace]\nmembers = ["crates/amaru"]\n')
    (source / "crates/amaru/Cargo.toml").write_text('[package]\nname = "amaru"\nversion = "0.1.0"\n')
    (source / "crates/amaru/src/main.rs").write_text("fn main() {}\n")
    candidate, = discover_candidates(root, repository_commit="a" * 40)
    security = load_security_campaign_suite(ROOT / "dwarf/security-suites/github-security-regression-v1.yaml")
    measurements = load_measurement_suite(ROOT / "dwarf/qualification-suites/node-measurements-v1.yaml")
    return resolve_security_campaign(candidate, security, measurements, selection={
        "entry_ids": ["amaru-cbor-tx-body-fuzz-structured"],
        "implementations": {"amaru": {"kind": "candidate", "version": "v99.0.0"}}},
        catalog={"schema_version": 1, "updated_at": "2026-10-19T00:00:00Z", "releases": [], "compatibility_pairs": []})


@pytest.fixture
def publication(frozen_campaign):
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    signer = "ed25519:" + hashlib.sha256(bytes.fromhex(public)).hexdigest()
    trust = {"schema_version": "v1", "kind": "campaign-trusted-publishers", "publishers": [
        {"signer_id": signer, "algorithm": "ed25519", "public_key_hex": public, "status": "active"}]}
    campaign = deepcopy(frozen_campaign)
    common = {"schema_version": "v1", "campaign_id": campaign["campaign_id"],
              "campaign_digest": campaign["campaign_digest"], "evidence_digest": "sha256:" + "d" * 64}
    evaluation = {"security_validity": {**common, "kind": "campaign-security-validity", "classification": "finding", "entries": [{"run_id": "run-1"}]},
        "measurement_completeness": {**common, "kind": "campaign-measurement-completeness", "measurement_complete": False, "cells": []}}
    body = b"opaque exported tarball bytes; never extract during publication"
    digest = hashlib.sha256(body).hexdigest()
    manifest = {"run-1-bundle-export.tar.gz": digest}
    signed = signing.canonical_signature_payload_bytes(manifest=manifest, target_run_id="run-1", signing_actor="publisher", excluded_relpaths=signing.DEFAULT_EXCLUDED_RELPATHS)
    signature = {"schema_version": "v2", "target_run_id": "run-1", "signing_actor": "publisher",
        "excluded_relpaths": sorted(signing.DEFAULT_EXCLUDED_RELPATHS), "public_key_hex": public,
        "signature_hex": key.sign(signed).hex(), "manifest_sha256": hashlib.sha256(signing.canonical_manifest_bytes(manifest)).hexdigest(),
        "signed_payload_sha256": hashlib.sha256(signed).hexdigest(), "key_source": "operator-key", "operator_warning": False, "signing_unavailable": False}
    base = "https://github.com/pragma-org/dwarf/releases/download/" + campaign["campaign_id"] + "/"
    name = "run-1-" + digest + ".tar.gz"
    archive = {"run_id": "run-1", "archive_bytes": body, "archive_sha256": digest, "signer_id": signer,
        "signature": signature, "archive_url": base + name, "signature_url": base + name + ".signature.json"}
    return campaign, evaluation, [archive], trust


def build(inputs):
    campaign, evaluation, archives, trust = inputs
    return api.build_publication_record(campaign, evaluation, archives=archives,
        trusted_publishers=trust, published_at="2026-10-19T12:00:00Z")


def test_compact_record_and_validated_versioned_files(publication):
    record = build(publication)
    assert record["campaign_digest"] == publication[0]["campaign_digest"]
    assert record["run_ids"] == ["run-1"]
    assert record["implementations"] == ["amaru"]
    assert record["versions"] == [{"implementation": "amaru", "version": "v99.0.0"}]
    assert record["security_result"] == "finding"
    assert record["measurement_completeness"] == "incomplete"
    assert record["modes"] == ["coverage", "patched", "stock"]
    assert record["archives"][0]["archive_sha256"] == publication[2][0]["archive_sha256"]
    assert record["archives"][0]["signer_id"] == publication[3]["publishers"][0]["signer_id"]
    assert "archive_bytes" not in api.serialize_publication(record)
    catalog = api.add_publication(api.empty_catalog(), record, publication[3])
    schema = json.loads((ROOT / "dwarf/spec/v1/campaign-publication.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(catalog)
    for name, validator in (("campaigns.json", api.validate_catalog), ("trusted-publishers.json", api.validate_trusted_publishers)):
        validator(json.loads((ROOT / "docs/evidence" / name).read_text()), *([publication[3]] if name == "campaigns.json" else []))


def test_compact_record_retains_explicit_stock_replay_authority(publication):
    campaign, evaluation, _archives, _trust = publication
    authority = {
        "schema_version": "v1",
        "kind": "campaign-stock-replay-authority",
        "primary_campaign_digest": campaign["campaign_digest"],
        "stock_campaign_digest": "sha256:" + "a" * 64,
        "primary_evidence_digest": evaluation["security_validity"]["evidence_digest"],
        "stock_evidence_digest": "sha256:" + "b" * 64,
        "authority": "patched-primary-stock-confirmed",
        "stock_security_result": "finding",
        "entries": [{
            "entry_id": "case",
            "authority": "confirmed",
            "primary_run_id": "run-1",
            "stock_run_id": "run-1",
            "primary_classification": "finding",
            "stock_classification": "finding",
            "matched_input_digests": ["sha256:" + "c" * 64],
        }],
    }
    authority["authority_digest"] = "sha256:" + hashlib.sha256(
        api.serialize_publication(authority).encode()
    ).hexdigest()
    evaluation["stock_replay_authority"] = authority

    record = build(publication)

    assert record["security_result"] == "finding"
    assert record["security_authority"] == "patched-primary-stock-confirmed"
    api.add_publication(api.empty_catalog(), record, publication[3])


@pytest.mark.parametrize("mutation", ["untrusted", "development-source", "development-key", "wrong-key", "bad-signature", "bad-run", "bad-hash", "modified-bytes", "bad-manifest", "bad-signed-payload", "exclusions", "unsigned", "campaign-id", "campaign-digest", "evaluation", "missing-run", "duplicate-run"])
def test_rejects_untrusted_or_mismatched_identities(publication, mutation):
    campaign, evaluation, archives, trust = publication
    archive = archives[0]
    signature = archive["signature"]
    if mutation == "untrusted": trust["publishers"] = []
    elif mutation == "development-source": signature["key_source"] = "development-embedded-key"
    elif mutation == "development-key":
        public = api.DEVELOPMENT_PUBLIC_KEY_HEX
        trust["publishers"][0].update(public_key_hex=public, signer_id="ed25519:" + hashlib.sha256(bytes.fromhex(public)).hexdigest())
    elif mutation == "wrong-key": signature["public_key_hex"] = "00" * 32
    elif mutation == "bad-signature": signature["signature_hex"] = "00" * 64
    elif mutation == "bad-run": signature["target_run_id"] = "another-run"
    elif mutation == "bad-hash": archive["archive_sha256"] = "0" * 64
    elif mutation == "modified-bytes": archive["archive_bytes"] += b"tamper"
    elif mutation == "bad-manifest": signature["manifest_sha256"] = "0" * 64
    elif mutation == "bad-signed-payload": signature["signed_payload_sha256"] = "0" * 64
    elif mutation == "exclusions": signature["excluded_relpaths"].append("extra")
    elif mutation == "unsigned": signature["signing_unavailable"] = True
    elif mutation == "campaign-id": campaign["campaign_id"] = "campaign-" + "0" * 64
    elif mutation == "campaign-digest": campaign["topologies"][0]["nodes"][0]["identity"]["version"] = "changed"
    elif mutation == "evaluation": evaluation["security_validity"]["campaign_digest"] = "sha256:" + "0" * 64
    elif mutation == "missing-run": evaluation["security_validity"]["entries"].append({"run_id": "missing"})
    else: archives.append(deepcopy(archive))
    with pytest.raises(ValueError): build(publication)


@pytest.mark.parametrize("bad", ["/tmp/archive.tar.gz", "C:\\private\\archive.tar.gz", "file:///tmp/archive.tar.gz", "http://github.com/o/r/releases/download/t/a", "https://user:token@github.com/o/r/releases/download/t/a", "https://127.0.0.1/archive.tar.gz", "https://example.org/archive.tar.gz", "https://github.com/o/r/releases/latest/download/a", "https://github.com/o/r/releases/download/t/a?token=secret", "https://github.com/o/r/releases/download/t/a#secret", "https://github.com/o/r/releases/download/t/%2e%2e", "https://github.com/o/r/releases/download/t/a%0a"])
def test_rejects_unsafe_urls(publication, bad):
    for field in ("archive_url", "signature_url"):
        mutated = deepcopy(publication)
        mutated[2][0][field] = bad
        with pytest.raises(ValueError): build(mutated)


def test_catalog_retries_conflicts_canonical_order_and_no_aliases(publication):
    record = build(publication)
    before = deepcopy(record)
    first = api.add_publication(api.empty_catalog(), record, publication[3])
    assert api.add_publication(first, record, publication[3]) == first
    assert record == before
    conflicting = deepcopy(record)
    conflicting["published_at"] = "2026-10-20T12:00:00Z"
    with pytest.raises(ValueError, match="conflict"):
        api.add_publication(first, conflicting, publication[3])
    first["campaigns"][0]["run_ids"].append("aliased")
    assert record == before
    assert api.serialize_publication({"z": 2, "a": 1}) == '{"a":1,"z":2}'


@pytest.mark.parametrize("field,value", [("secret", "private"), ("source_path", "/Users/private"), ("token", "ghp_example"), ("archive_bytes", "data")])
def test_catalog_rejects_extra_fields(publication, field, value):
    record = build(publication)
    for target in (record, record["archives"][0], record["versions"][0], record["suites"][0]):
        target[field] = value
        with pytest.raises(ValueError): api.add_publication(api.empty_catalog(), record, publication[3])
        del target[field]


@pytest.mark.parametrize("mutation", ["version", "extra", "duplicate", "fingerprint", "revoked", "bad-key", "missing"])
def test_trust_shape_and_fingerprint(publication, mutation):
    trust = publication[3]
    if mutation == "version": trust["schema_version"] = "v9"
    elif mutation == "extra": trust["publishers"][0]["private_key"] = "secret"
    elif mutation == "duplicate": trust["publishers"] *= 2
    elif mutation == "fingerprint": trust["publishers"][0]["signer_id"] = "ed25519:" + "0" * 64
    elif mutation == "revoked": trust["publishers"][0]["status"] = "revoked"
    elif mutation == "bad-key": trust["publishers"][0]["public_key_hex"] = "not-a-key"
    else: del trust["publishers"][0]["algorithm"]
    with pytest.raises(ValueError): build(publication)


def test_atomic_update_idempotent_and_failure_preserves_existing_file(publication, tmp_path):
    record = build(publication)
    path = tmp_path / "campaigns.json"
    api.write_publication(path, record, publication[3])
    before = path.read_bytes()
    modified = path.stat().st_mtime_ns
    api.write_publication(path, record, publication[3])
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == modified
    invalid = deepcopy(record)
    invalid["archive_url"] = "file:///private"
    with pytest.raises(ValueError): api.write_publication(path, invalid, publication[3])
    assert path.read_bytes() == before


def test_duplicate_json_keys_and_catalog_shape_rejected(publication, tmp_path):
    path = tmp_path / "campaigns.json"
    path.write_text('{"schema_version":"v1","schema_version":"v2"}')
    with pytest.raises(ValueError): api.write_publication(path, build(publication), publication[3])
    for document in ({}, {**api.empty_catalog(), "secret": "value"}, {**api.empty_catalog(), "schema_version": "v2"}):
        with pytest.raises(ValueError): api.validate_catalog(document, publication[3])


def test_rejects_malformed_frozen_manifest_even_with_recomputed_identity(publication):
    campaign = publication[0]
    original_id = campaign["campaign_id"]
    campaign["kind"] = "arbitrary-document"
    campaign["campaign_digest"] = digest_security_campaign(campaign)
    campaign["campaign_id"] = "campaign-" + campaign["campaign_digest"].split(":")[1]
    for evaluation in publication[1].values():
        evaluation.update(campaign_id=campaign["campaign_id"], campaign_digest=campaign["campaign_digest"])
    for archive in publication[2]:
        for field in ("archive_url", "signature_url"):
            archive[field] = archive[field].replace(original_id, campaign["campaign_id"])
    with pytest.raises(ValueError): build(publication)


@pytest.mark.parametrize("mutation", ["version", "entries", "cells", "run-type"])
def test_malformed_retained_evaluation_rejected(publication, mutation):
    evaluation = publication[1]
    if mutation == "version": evaluation["security_validity"]["schema_version"] = "v9"
    elif mutation == "entries": evaluation["security_validity"]["entries"] = {}
    elif mutation == "cells": del evaluation["measurement_completeness"]["cells"]
    else: evaluation["security_validity"]["entries"][0]["run_id"] = ["run-1"]
    with pytest.raises(ValueError): build(publication)


@pytest.mark.parametrize("boundary", ["trust", "build", "add"])
def test_embedded_development_key_with_newline_cannot_bypass_trust(publication, boundary):
    record = build(publication)
    archive = publication[2][0]
    public = api.DEVELOPMENT_PUBLIC_KEY_HEX + "\n"
    signer = "ed25519:" + hashlib.sha256(bytes.fromhex(public)).hexdigest()
    publication[3]["publishers"][0].update(public_key_hex=public, signer_id=signer)
    archive["signer_id"] = signer
    signature = archive["signature"]
    signature["public_key_hex"] = public
    manifest = {"run-1-bundle-export.tar.gz": archive["archive_sha256"]}
    signed = signing.canonical_signature_payload_bytes(manifest=manifest, target_run_id="run-1",
        signing_actor="publisher", excluded_relpaths=signing.DEFAULT_EXCLUDED_RELPATHS)
    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(signing.DEV_PRIVATE_KEY_HEX))
    signature["signature_hex"] = key.sign(signed).hex()
    record["archives"][0]["signer_id"] = signer
    with pytest.raises(ValueError):
        if boundary == "trust": api.validate_trusted_publishers(publication[3])
        elif boundary == "build": build(publication)
        else: api.add_publication(api.empty_catalog(), record, publication[3])


@pytest.mark.parametrize("transform", [lambda s: s + "\n", lambda s: " " + s, lambda s: s + "\t", str.upper, lambda s: s + "x"])
def test_public_key_hex_requires_exact_lowercase_text(publication, transform):
    record = build(publication)
    publisher = publication[3]["publishers"][0]
    publisher["public_key_hex"] = transform(publisher["public_key_hex"])
    publication[2][0]["signature"]["public_key_hex"] = publisher["public_key_hex"]
    for call in (lambda: api.validate_trusted_publishers(publication[3]), lambda: build(publication),
                 lambda: api.add_publication(api.empty_catalog(), record, publication[3])):
        with pytest.raises(ValueError): call()


@pytest.mark.parametrize("field", ["signature_hex", "public_key_hex", "manifest_sha256", "signed_payload_sha256"])
@pytest.mark.parametrize("transform", [lambda s: s + "\n", str.upper, lambda s: s[:10] + " " + s[10:]])
def test_signature_hex_fields_reject_noncanonical_text(publication, field, transform):
    signature = publication[2][0]["signature"]
    signature[field] = transform(signature[field])
    with pytest.raises(ValueError): build(publication)


@pytest.mark.parametrize("field", ["campaign_id", "campaign_digest", "evidence_digest", "suite_digest", "archive_sha256", "signer_id"])
@pytest.mark.parametrize("transform", [lambda s: s + "\n", str.upper, lambda s: " " + s, lambda s: s + "x"])
def test_catalog_hex_identities_and_schema_reject_noncanonical_text(publication, field, transform):
    record = build(publication)
    if field == "suite_digest": target, key = record["suites"][0], "digest"
    elif field in {"archive_sha256", "signer_id"}: target, key = record["archives"][0], field
    else: target, key = record, field
    target[key] = transform(target[key])
    catalog = {**api.empty_catalog(), "campaigns": [record]}
    schema = json.loads((ROOT / "dwarf/spec/v1/campaign-publication.schema.json").read_text())
    assert list(Draft202012Validator(schema).iter_errors(catalog))
    with pytest.raises(ValueError): api.add_publication(api.empty_catalog(), record, publication[3])
