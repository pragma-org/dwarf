"""Local, fail-closed publication metadata; never upload, extract or run archives.

Supply already exported archive bytes and their existing detached v2 signatures.
Only ``write_publication`` changes files, using an explicit atomic catalog update.
The caller keeps archives outside Git and uploads them separately as immutable
GitHub Release assets. No private archive paths or signing secrets are accepted.
An empty shipped trust file deliberately authorizes no publisher. Pin reviewed
Ed25519 public keys there before using this API; a signature cannot establish trust.

Limits: 10,000 campaigns, 256 archives/versions per campaign, 256 publisher pins.
Concurrent writers must be serialized by the caller; atomic replace prevents torn
files but is not a multi-process compare-and-swap. Remote release immutability
cannot be established offline; digest-addressed names detect content replacement.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from jsonschema import Draft202012Validator, FormatChecker

from profile_manager.run_ledger import atomic_write_bytes
from profile_manager.security_campaign import _schema_validate, campaign_evaluation_status, serialize_campaign
from scripts.runtime_bundle_sign import (
    DEFAULT_EXCLUDED_RELPATHS, canonical_manifest_bytes, canonical_signature_payload_bytes,
)

DEVELOPMENT_PUBLIC_KEY_HEX = "526aeb2348f8b61365eea645a5fe0ef8b6f63134153bdd9d3a96f810f7a40037"
_DEVELOPMENT_PUBLIC_KEY_BYTES = bytes.fromhex(DEVELOPMENT_PUBLIC_KEY_HEX)
_SCHEMA = Path(__file__).resolve().parents[1] / "spec/v1/campaign-publication.schema.json"
_SECRET = re.compile(r"(?:gh[pousr]_|github_pat_|AKIA[0-9A-Z]{16}|-----BEGIN|(?:password|secret|token|credential)[=:])", re.I)


def serialize_publication(document: Any) -> str:
    """Deterministic compact JSON (ASCII, sorted keys, no NaN), without newline."""
    return serialize_campaign(document)


def empty_catalog() -> dict:
    return {"schema_version": "v1", "kind": "campaign-publication-catalog", "campaigns": []}


def _hex_bytes(text: Any, *, size: int = 32) -> bytes:
    # fromhex() alone accepts whitespace and mixed case, creating ambiguous
    # textual identities. Reject those representations before decoding.
    if not isinstance(text, str) or re.fullmatch(r"[0-9a-f]{" + str(size * 2) + "}", text) is None:
        raise ValueError("publication requires exact lowercase hexadecimal text")
    return bytes.fromhex(text)


def _validate_shape(document: Any, definition: str) -> None:
    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    validator = Draft202012Validator({"$ref": "#/$defs/" + definition, "$defs": schema["$defs"]}, format_checker=FormatChecker())
    error = next(validator.iter_errors(document), None)
    if error:
        # Do not echo potentially secret or machine-local values in diagnostics.
        raise ValueError(f"invalid publication {definition} shape")
    if _SECRET.search(serialize_publication(document)):
        raise ValueError("publication contains credential or secret material")


def validate_trusted_publishers(document: dict) -> None:
    """Validate independent public-key pins, including revoked records."""
    _validate_shape(document, "trusted_publishers")
    seen = set()
    for publisher in document["publishers"]:
        public = _hex_bytes(publisher["public_key_hex"])
        fingerprint = "ed25519:" + hashlib.sha256(public).hexdigest()
        if publisher["signer_id"] != fingerprint or fingerprint in seen:
            raise ValueError("duplicate publisher or signer fingerprint mismatch")
        if public == _DEVELOPMENT_PUBLIC_KEY_BYTES:
            raise ValueError("embedded development key cannot be a trusted publisher")
        Ed25519PublicKey.from_public_bytes(public)
        seen.add(fingerprint)


def _pins(trusted_publishers: dict) -> dict:
    validate_trusted_publishers(trusted_publishers)
    return {p["signer_id"]: p["public_key_hex"] for p in trusted_publishers["publishers"] if p["status"] == "active"}


def _validate_record(record: dict, pins: dict) -> None:
    _validate_shape(record, "record")
    if record["campaign_id"] != "campaign-" + record["campaign_digest"].split(":")[1]:
        raise ValueError("campaign identity mismatch")
    if set(record["implementations"]) != {v["implementation"] for v in record["versions"]}:
        raise ValueError("implementation/version summary mismatch")
    if {s["kind"] for s in record["suites"]} != {"security", "measurement"}:
        raise ValueError("suite summary mismatch")
    runs = [a["run_id"] for a in record["archives"]]
    if len(set(runs)) != len(runs) or set(runs) != set(record["run_ids"]):
        raise ValueError("archive/run identity mismatch")
    locations = set()
    for archive in record["archives"]:
        if archive["signer_id"] not in pins:
            raise ValueError("archive signer is not an active trusted publisher")
        name = archive["run_id"] + "-" + archive["archive_sha256"] + ".tar.gz"
        archive_url = urlsplit(archive["archive_url"])
        signature_url = urlsplit(archive["signature_url"])
        # Shape forbids credentials, ports, query, fragment, escapes and hosts
        # other than github.com. Also bind release and content-addressed asset.
        base, asset = archive_url.path.rsplit("/", 1)
        if base.rsplit("/", 1)[-1] != record["campaign_id"] or asset != name:
            raise ValueError("archive URL does not bind campaign and archive digest")
        if signature_url.path != base + "/" + name + ".signature.json":
            raise ValueError("signature URL does not bind the archive asset")
        for url in (archive["archive_url"], archive["signature_url"]):
            if url in locations:
                raise ValueError("conflicting archive asset reuse")
            locations.add(url)


def validate_catalog(document: dict, trusted_publishers: dict) -> None:
    """Validate catalog structure and exact identity/reuse constraints, offline.

    Existing catalog entries contain no archive bytes; cryptographic verification
    happens when building new records and again in the viewer on archive import.
    """
    pins = _pins(trusted_publishers)
    _validate_shape(document, "catalog")
    campaigns, assets, runs = set(), set(), set()
    for record in document["campaigns"]:
        _validate_record(record, pins)
        if record["campaign_digest"] in campaigns:
            raise ValueError("conflicting campaign reuse")
        campaigns.add(record["campaign_digest"])
        for archive in record["archives"]:
            if archive["run_id"] in runs:
                raise ValueError("conflicting run reuse")
            runs.add(archive["run_id"])
            for url in (archive["archive_url"], archive["signature_url"]):
                if url in assets:
                    raise ValueError("conflicting archive asset reuse")
                assets.add(url)


def _run_ids(document: Any) -> set[str]:
    if isinstance(document, dict):
        if "run_id" in document and (not isinstance(document["run_id"], str) or not document["run_id"]):
            raise ValueError("invalid retained evaluation run identity")
        result = {document["run_id"]} if document.get("run_id") else set()
        for value in document.values():
            result.update(_run_ids(value))
        return result
    if isinstance(document, list):
        return set().union(*(_run_ids(value) for value in document))
    return set()


def _verify_archive(archive: dict, pins: dict) -> dict:
    expected = {"run_id", "archive_bytes", "archive_sha256", "signer_id", "signature", "archive_url", "signature_url"}
    if not isinstance(archive, dict) or set(archive) != expected:
        raise ValueError("invalid local archive input shape")
    signature = archive["signature"]
    public = pins.get(archive["signer_id"])
    if not public or not isinstance(signature, dict) or signature.get("public_key_hex") != public:
        raise ValueError("archive signer is not an active trusted publisher")
    public_bytes = _hex_bytes(signature.get("public_key_hex"))
    signature_bytes = _hex_bytes(signature.get("signature_hex"), size=64)
    for field in ("manifest_sha256", "signed_payload_sha256"):
        _hex_bytes(signature.get(field))
    _hex_bytes(archive["archive_sha256"])
    if (signature.get("schema_version") != "v2" or signature.get("target_run_id") != archive["run_id"]
            or signature.get("key_source") == "development-embedded-key"
            or signature.get("signing_unavailable") is not False or signature.get("operator_warning") is not False
            or signature.get("excluded_relpaths") != sorted(DEFAULT_EXCLUDED_RELPATHS)):
        raise ValueError("unsupported, unsigned or development archive signature")
    if not isinstance(archive["archive_bytes"], bytes):
        raise ValueError("archive requires exported bytes, never a local path")
    digest = hashlib.sha256(archive["archive_bytes"]).hexdigest()
    if digest != archive["archive_sha256"]:
        raise ValueError("archive digest mismatch")
    manifest = {archive["run_id"] + "-bundle-export.tar.gz": digest}
    signed = canonical_signature_payload_bytes(manifest=manifest, target_run_id=archive["run_id"],
        signing_actor=signature.get("signing_actor", ""), excluded_relpaths=DEFAULT_EXCLUDED_RELPATHS)
    if (signature.get("manifest_sha256") != hashlib.sha256(canonical_manifest_bytes(manifest)).hexdigest()
            or signature.get("signed_payload_sha256") != hashlib.sha256(signed).hexdigest()):
        raise ValueError("signature scope does not match exact exported archive")
    try:
        Ed25519PublicKey.from_public_bytes(public_bytes).verify(signature_bytes, signed)
    except (InvalidSignature, KeyError, TypeError, ValueError) as exc:
        raise ValueError("archive signature verification failed") from exc
    return {k: archive[k] for k in ("run_id", "archive_url", "signature_url", "archive_sha256", "signer_id")}


def build_publication_record(campaign: dict, evaluation: dict, *, archives: list[dict],
                             trusted_publishers: dict, published_at: str) -> dict:
    """Verify caller-supplied exported bytes and project compact public metadata.

    Status comes from retained evaluator records, never the pending frozen plan.
    Every evaluator run ID must have exactly one signed archive. The trusted
    caller supplies retained evaluation: this API does not re-evaluate security
    observations or inspect tar members, and does not assert campaign success.
    """
    _schema_validate(campaign)
    if not isinstance(evaluation, dict):
        raise ValueError("invalid retained evaluation")
    for name, rows in (("security_validity", "entries"), ("measurement_completeness", "cells")):
        result = evaluation.get(name)
        if (not isinstance(result, dict) or result.get("schema_version") != "v1"
                or not isinstance(result.get(rows), list) or any(not isinstance(row, dict) for row in result[rows])):
            raise ValueError("invalid retained evaluation shape/version")
    status = campaign_evaluation_status(campaign, evaluation)
    pins = _pins(trusted_publishers)
    rows = [_verify_archive(a, pins) for a in archives]
    run_ids = [a["run_id"] for a in rows]
    if len(set(run_ids)) != len(run_ids) or set(run_ids) != _run_ids(evaluation):
        raise ValueError("archive runs do not exactly match retained campaign evaluation")
    versions = {(node["implementation"], node["identity"]["version"])
                for topology in campaign["topologies"] for node in topology["nodes"]}
    record = {**status, "run_ids": sorted(run_ids), "archives": sorted(rows, key=lambda r: r["run_id"]),
        "implementations": sorted({v[0] for v in versions}),
        "versions": [{"implementation": impl, "version": version} for impl, version in sorted(versions)],
        "modes": sorted({variant["mode"] for variant in campaign["build_variants"]}),
        "suites": [{"kind": kind, "id": campaign["suites"][kind]["document"]["id"],
                    "digest": campaign["suites"][kind]["digest"]} for kind in sorted(campaign["suites"])],
        "published_at": published_at}
    _validate_record(record, pins)
    return deepcopy(record)


def _ordered(record: dict) -> dict:
    result = deepcopy(record)
    for field in ("run_ids", "implementations", "modes"):
        result[field].sort()
    result["archives"].sort(key=lambda r: r["run_id"])
    result["versions"].sort(key=lambda r: (r["implementation"], r["version"]))
    result["suites"].sort(key=lambda r: r["kind"])
    return result


def add_publication(catalog: dict, record: dict, trusted_publishers: dict) -> dict:
    """Pure canonical insert. Identical retries succeed; any identity reuse fails."""
    validate_catalog(catalog, trusted_publishers)
    _validate_record(record, _pins(trusted_publishers))
    result = deepcopy(catalog)
    canonical = _ordered(record)
    for existing in result["campaigns"]:
        if existing["campaign_digest"] == record["campaign_digest"]:
            if _ordered(existing) != canonical:
                raise ValueError("conflicting campaign publication reuse")
            break
    else:
        result["campaigns"].append(canonical)
    result["campaigns"] = sorted((_ordered(r) for r in result["campaigns"]), key=lambda r: r["campaign_digest"])
    validate_catalog(result, trusted_publishers)
    return result


def _unique_pairs(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key in publication catalog")
        result[key] = value
    return result


def write_publication(path: Path, record: dict, trusted_publishers: dict) -> dict:
    """Explicit local atomic update; identical retries leave file bytes/mtime alone."""
    path = Path(path)
    original = path.read_bytes() if path.exists() else None
    try:
        catalog = json.loads(original, object_pairs_hook=_unique_pairs) if original is not None else empty_catalog()
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid publication catalog JSON") from exc
    result = add_publication(catalog, record, trusted_publishers)
    content = (serialize_publication(result) + "\n").encode("utf-8")
    if content != original:
        atomic_write_bytes(path, content)
    return result
