#!/usr/bin/env python3
"""Prepare verified, content-addressed campaign release assets and Pages defaults.

This command is local and network-free. It reads a completed hosted campaign,
checks its retained receipt hashes and workflow-local Ed25519 signatures, then
signs the opaque archive bytes with a separately pinned publication key. It never
extracts an archive and never uploads, commits, or invokes GitHub. The workflow
owns those remote side effects after this command succeeds.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from profile_manager import campaign_publication as publication
from profile_manager.security_campaign import _schema_validate, serialize_campaign
from scripts.runtime_bundle_sign import (
    DEFAULT_EXCLUDED_RELPATHS,
    canonical_manifest_bytes,
    canonical_signature_payload_bytes,
)

_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:+-]{0,127}")
_REPOSITORY = re.compile(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+")
_DEFAULT_SCHEMA = "dwarf.default-run-archives.v1"
_MAX_DEFAULTS = 10
_MAX_DEFAULT_ARCHIVE_BYTES = 10 * 1024 * 1024
_MAX_DEFAULT_TOTAL_BYTES = 25 * 1024 * 1024


def _load(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialize_campaign(value) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _publisher(key_path: Path, trusted: dict) -> tuple[Ed25519PrivateKey, str, str]:
    publication.validate_trusted_publishers(trusted)
    key = serialization.load_pem_private_key(Path(key_path).read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("campaign publication key must be Ed25519")
    public_hex = key.public_key().public_bytes_raw().hex()
    signer_id = "ed25519:" + hashlib.sha256(bytes.fromhex(public_hex)).hexdigest()
    matches = [row for row in trusted["publishers"]
               if row["status"] == "active" and row["signer_id"] == signer_id
               and row["public_key_hex"] == public_hex]
    if len(matches) != 1:
        raise ValueError("campaign publication key is not the one active pinned publisher")
    return key, public_hex, signer_id


def _receipt_map(state: dict) -> dict[str, dict]:
    result: dict[str, dict] = {}
    groups = [("campaign", state.get("cells"))]
    replay = state.get("stock_replay")
    if replay is not None:
        if not isinstance(replay, dict):
            raise ValueError("campaign stock replay state is invalid")
        groups.append(("stock replay", replay.get("cells")))
    for label, cells in groups:
        if not isinstance(cells, dict):
            raise ValueError(f"{label} state has no cells")
        for cell in cells.values():
            if not isinstance(cell, dict):
                raise ValueError(f"{label} state cell is invalid")
            for receipt in cell.get("archives") or []:
                if not isinstance(receipt, dict) or not _RUN_ID.fullmatch(str(receipt.get("run_id") or "")):
                    raise ValueError("campaign archive receipt is invalid")
                run_id = receipt["run_id"]
                if run_id in result and result[run_id] != receipt:
                    raise ValueError("campaign has conflicting archive receipts")
                result[run_id] = receipt
    return result


def _verified_ephemeral_archive(runtime_root: Path, run_id: str, receipt: dict,
                                campaign_id: str, repository: str) -> tuple[Path, bytes]:
    archive = runtime_root / "runs" / run_id / "outputs" / "export" / f"{run_id}-bundle-export.tar.gz"
    signature_path = runtime_root / "runs" / run_id / "outputs" / "export" / "signature.json"
    if not archive.is_file() or not signature_path.is_file():
        raise ValueError(f"campaign archive files are missing for {run_id}")
    if receipt.get("archive_sha256") != _sha256(archive) or receipt.get("signature_sha256") != _sha256(signature_path):
        raise ValueError(f"campaign archive receipt digest mismatch for {run_id}")
    signature = _load(signature_path)
    public_hex = signature.get("public_key_hex")
    if not isinstance(public_hex, str) or not re.fullmatch(r"[0-9a-f]{64}", public_hex):
        raise ValueError(f"campaign workflow signature is invalid for {run_id}")
    signer_id = "ed25519:" + hashlib.sha256(bytes.fromhex(public_hex)).hexdigest()
    body = archive.read_bytes()
    digest = hashlib.sha256(body).hexdigest()
    name = f"{run_id}-{digest}.tar.gz"
    base = f"https://github.com/{repository}/releases/download/{campaign_id}/"
    publication._verify_archive({
        "run_id": run_id,
        "archive_bytes": body,
        "archive_sha256": digest,
        "signer_id": signer_id,
        "signature": signature,
        "archive_url": base + name,
        "signature_url": base + name + ".signature.json",
    }, {signer_id: public_hex})
    return archive, body


def _trusted_signature(key: Ed25519PrivateKey, public_hex: str, run_id: str,
                       digest: str) -> dict:
    actor = "github-actions-campaign-publisher"
    manifest = {f"{run_id}-bundle-export.tar.gz": digest}
    signed = canonical_signature_payload_bytes(
        manifest=manifest,
        target_run_id=run_id,
        signing_actor=actor,
        excluded_relpaths=DEFAULT_EXCLUDED_RELPATHS,
    )
    return {
        "schema_version": "v2",
        "target_run_id": run_id,
        "signing_actor": actor,
        "excluded_relpaths": sorted(DEFAULT_EXCLUDED_RELPATHS),
        "public_key_hex": public_hex,
        "signature_hex": key.sign(signed).hex(),
        "manifest_sha256": hashlib.sha256(canonical_manifest_bytes(manifest)).hexdigest(),
        "signed_payload_sha256": hashlib.sha256(signed).hexdigest(),
        "key_source": "github-actions-secret",
        "operator_warning": False,
        "signing_unavailable": False,
    }


def _candidate_identity(campaign: dict) -> tuple[str, str]:
    rows = []
    for topology in campaign["topologies"]:
        for node in topology["nodes"]:
            identity = node["identity"]
            if identity.get("kind") == "candidate":
                rows.append((node["implementation"], identity["version"]))
    unique = sorted(set(rows))
    if len(unique) != 1:
        raise ValueError("publication requires one exact candidate implementation/version")
    return unique[0]


def _result_priority(state: dict, required: set[str]) -> list[str]:
    measured, security, controls = [], [], []
    for key, cell in sorted(state["cells"].items()):
        if not isinstance(cell, dict):
            continue
        result = cell.get("result")
        if isinstance(result, dict) and result.get("run_id") in required:
            (measured if key.startswith("measurement:") else security).append(result["run_id"])
        control = cell.get("control")
        if isinstance(control, dict) and control.get("run_id") in required:
            controls.append(control["run_id"])
    ordered = []
    for run_id in measured + security + sorted(required) + controls:
        if run_id in required and run_id not in ordered:
            ordered.append(run_id)
    return ordered


def update_default_manifest(previous: dict | None, current: list[dict]) -> dict:
    if previous is None:
        previous = {"schema_version": _DEFAULT_SCHEMA, "archives": []}
    if (not isinstance(previous, dict) or previous.get("schema_version") != _DEFAULT_SCHEMA
            or not isinstance(previous.get("archives"), list)):
        raise ValueError("previous Pages default manifest is invalid")
    def eligible(row):
        return (isinstance(row, dict) and isinstance(row.get("size_bytes"), int)
                and not isinstance(row.get("size_bytes"), bool)
                and 0 < row["size_bytes"] <= _MAX_DEFAULT_ARCHIVE_BYTES)

    current_ids = {row["run_id"] for row in current if eligible(row)}
    older = [deepcopy(row) for row in previous["archives"]
             if eligible(row) and row.get("run_id") not in current_ids]
    selected_current, total = [], 0
    for row in current:
        if (eligible(row) and len(selected_current) < _MAX_DEFAULTS
                and total + row["size_bytes"] <= _MAX_DEFAULT_TOTAL_BYTES):
            selected_current.append(deepcopy(row))
            total += row["size_bytes"]
    selected_older = []
    for row in reversed(older):
        if len(selected_older) + len(selected_current) >= _MAX_DEFAULTS:
            break
        if total + row["size_bytes"] <= _MAX_DEFAULT_TOTAL_BYTES:
            selected_older.append(row)
            total += row["size_bytes"]
    selected = list(reversed(selected_older)) + selected_current
    if not selected:
        raise ValueError("no published archive fits the Pages default limits")
    return {"schema_version": _DEFAULT_SCHEMA, "archives": selected}


def prepare(*, manifest_path: Path, state_path: Path, runtime_root: Path,
            trusted_publishers_path: Path, signing_key_path: Path,
            output: Path, repository: str, published_at: str,
            previous_catalog_path: Path | None = None,
            previous_defaults_path: Path | None = None) -> dict:
    if not _REPOSITORY.fullmatch(repository):
        raise ValueError("GitHub repository must be owner/name")
    campaign, state, trusted = _load(manifest_path), _load(state_path), _load(trusted_publishers_path)
    _schema_validate(campaign)
    if state.get("campaign_digest") != campaign["campaign_digest"]:
        raise ValueError("campaign state does not match frozen manifest")
    if state.get("readiness", {}).get("passed") is not True or state.get("cleanup_errors"):
        raise ValueError("campaign is not publication-ready")
    evaluation = state.get("evaluation")
    if not isinstance(evaluation, dict):
        raise ValueError("campaign retained no evaluation")
    status = publication.campaign_evaluation_status(campaign, evaluation)
    patched_primary = any(
        node["identity"].get("kind") == "candidate"
        and node["identity"].get("mode", "stock") == "patched"
        for topology in campaign["topologies"]
        for node in topology["nodes"]
    )
    if patched_primary and status.get("security_authority") not in {
        "patched-primary-stock-confirmed",
        "patched-primary-stock-disagrees",
    }:
        raise ValueError("patched primary requires complete stock replay authority")
    if status["security_result"] not in {"pass", "finding"} or status["measurement_completeness"] != "complete":
        raise ValueError("campaign security/measurement evaluation is incomplete")
    required = publication._run_ids(evaluation)
    receipts = _receipt_map(state)
    if not required or set(receipts) != required:
        raise ValueError("campaign receipts do not exactly match evaluated run identities")
    key, public_hex, signer_id = _publisher(signing_key_path, trusted)
    output.mkdir(parents=True, exist_ok=True)
    assets = output / "assets"
    assets.mkdir(exist_ok=True)
    archive_inputs, metadata_assets, sizes = [], [], {}
    base = f"https://github.com/{repository}/releases/download/{campaign['campaign_id']}/"
    for run_id in sorted(required):
        source, body = _verified_ephemeral_archive(runtime_root, run_id, receipts[run_id], campaign["campaign_id"], repository)
        digest = hashlib.sha256(body).hexdigest()
        name = f"{run_id}-{digest}.tar.gz"
        destination = assets / name
        shutil.copyfile(source, destination)
        signature = _trusted_signature(key, public_hex, run_id, digest)
        signature_name = name + ".signature.json"
        _write(assets / signature_name, signature)
        archive_inputs.append({
            "run_id": run_id, "archive_bytes": body, "archive_sha256": digest,
            "signer_id": signer_id, "signature": signature,
            "archive_url": base + name, "signature_url": base + signature_name,
        })
        sizes[run_id] = len(body)
        metadata_assets.extend([name, signature_name])
    record = publication.build_publication_record(
        campaign, evaluation, archives=archive_inputs,
        trusted_publishers=trusted, published_at=published_at,
    )
    prior_catalog = _load(previous_catalog_path) if previous_catalog_path and previous_catalog_path.is_file() else publication.empty_catalog()
    catalog = publication.add_publication(prior_catalog, record, trusted)
    _write(output / "campaigns.json", catalog)
    _write(output / "record.json", record)
    implementation, version = _candidate_identity(campaign)
    current_defaults = []
    by_run = {row["run_id"]: row for row in record["archives"]}
    for run_id in _result_priority(state, required)[:_MAX_DEFAULTS]:
        row = by_run[run_id]
        name = row["archive_url"].rsplit("/", 1)[-1]
        current_defaults.append({
            "url": "runs/" + name,
            "size_bytes": sizes[run_id],
            "sha256": row["archive_sha256"],
            "run_id": run_id,
            "implementation": implementation,
            "version": version,
            "release_tag": campaign["campaign_id"],
            "asset_name": name,
            "published_at": published_at,
        })
    previous_defaults = _load(previous_defaults_path) if previous_defaults_path and previous_defaults_path.is_file() else None
    defaults = update_default_manifest(previous_defaults, current_defaults)
    _write(output / "default-runs.json", defaults)
    metadata = {
        "schema_version": "v1",
        "campaign_id": campaign["campaign_id"],
        "campaign_digest": campaign["campaign_digest"],
        "signer_id": signer_id,
        "assets": sorted(metadata_assets),
        "catalog_asset": "campaigns.json",
        "defaults_asset": "default-runs.json",
    }
    _write(output / "metadata.json", metadata)
    return metadata


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--trusted-publishers", type=Path, required=True)
    parser.add_argument("--signing-key", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--published-at", default=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"))
    parser.add_argument("--previous-catalog", type=Path)
    parser.add_argument("--previous-defaults", type=Path)
    args = parser.parse_args(argv)
    try:
        metadata = prepare(
            manifest_path=args.manifest, state_path=args.state, runtime_root=args.runtime_root,
            trusted_publishers_path=args.trusted_publishers, signing_key_path=args.signing_key,
            output=args.output, repository=args.repository, published_at=args.published_at,
            previous_catalog_path=args.previous_catalog, previous_defaults_path=args.previous_defaults,
        )
        print(serialize_campaign(metadata))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"campaign publication: {exc}", file=__import__('sys').stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
