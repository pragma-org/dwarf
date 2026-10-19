#!/usr/bin/env python3
"""Validate release-backed campaign defaults and stage same-origin Pages evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil

from profile_manager.campaign_publication import validate_catalog, validate_trusted_publishers
from profile_manager.security_campaign import serialize_campaign

_SCHEMA = "dwarf.default-run-archives.v1"
_TAG = re.compile(r"campaign-[0-9a-f]{64}")
_ASSET = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.tar\.gz")
_SHA = re.compile(r"[0-9a-f]{64}")
_MAX_ARCHIVES = 10
_MAX_ARCHIVE_BYTES = 10 * 1024 * 1024
_MAX_TOTAL_BYTES = 25 * 1024 * 1024


def load(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate_defaults(document: dict) -> list[dict]:
    if not isinstance(document, dict) or document.get("schema_version") != _SCHEMA:
        raise ValueError("unsupported Pages default manifest")
    rows = document.get("archives")
    if not isinstance(rows, list) or not rows or len(rows) > _MAX_ARCHIVES:
        raise ValueError("Pages default manifest archive count is invalid")
    seen, total = set(), 0
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Pages default archive entry is invalid")
        required = ("url", "size_bytes", "sha256", "run_id", "implementation", "version")
        if any(row.get(field) in (None, "") for field in required):
            raise ValueError("Pages default archive entry is incomplete")
        url = row["url"]
        if not isinstance(url, str) or not url.startswith("runs/") or not _ASSET.fullmatch(url[5:]):
            raise ValueError("Pages default archive URL is unsafe")
        if url in seen:
            raise ValueError("Pages default archive is duplicated")
        seen.add(url)
        size = row["size_bytes"]
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0 or size > _MAX_ARCHIVE_BYTES:
            raise ValueError("Pages default archive size is invalid")
        total += size
        if total > _MAX_TOTAL_BYTES:
            raise ValueError("Pages default archives exceed the total byte limit")
        if not isinstance(row["sha256"], str) or not _SHA.fullmatch(row["sha256"]):
            raise ValueError("Pages default archive digest is invalid")
        release_tag, asset = row.get("release_tag"), row.get("asset_name")
        if (release_tag is None) != (asset is None):
            raise ValueError("Pages release-backed archive binding is incomplete")
        if release_tag is not None:
            if not _TAG.fullmatch(release_tag) or not _ASSET.fullmatch(asset) or asset != url[5:]:
                raise ValueError("Pages release-backed archive binding is invalid")
    return rows


def release_assets(defaults: dict) -> list[tuple[str, str]]:
    return sorted({(row["release_tag"], row["asset_name"])
                   for row in validate_defaults(defaults) if row.get("release_tag")})


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stage(*, committed: Path, defaults_path: Path, catalog_path: Path,
          downloads: Path, output: Path, trusted_publishers_path: Path) -> None:
    defaults, catalog, trusted = load(defaults_path), load(catalog_path), load(trusted_publishers_path)
    rows = validate_defaults(defaults)
    validate_trusted_publishers(trusted)
    validate_catalog(catalog, trusted)
    campaigns = {row["campaign_id"]: row for row in catalog["campaigns"]}
    for row in rows:
        if not row.get("release_tag"):
            continue
        record = campaigns.get(row["release_tag"])
        archives = [] if record is None else [item for item in record["archives"]
            if item["run_id"] == row["run_id"] and item["archive_sha256"] == row["sha256"]]
        if len(archives) != 1 or archives[0]["archive_url"].rsplit("/", 1)[-1] != row["asset_name"]:
            raise ValueError("Pages release-backed archive is absent from the trusted campaign catalog")
    if output.exists():
        shutil.rmtree(output)
    shutil.copytree(committed, output)
    runs = output / "runs"
    runs.mkdir(exist_ok=True)
    for row in rows:
        name = row["url"][5:]
        if row.get("release_tag"):
            source = downloads / row["release_tag"] / name
        else:
            source = committed / "runs" / name
        if not source.is_file() or source.stat().st_size != row["size_bytes"] or _hash(source) != row["sha256"]:
            raise ValueError(f"Pages default archive bytes do not match {name}")
        if source.resolve() != (runs / name).resolve():
            shutil.copyfile(source, runs / name)
    (output / "default-runs.json").write_text(serialize_campaign(defaults) + "\n", encoding="utf-8")
    (output / "campaigns.json").write_text(serialize_campaign(catalog) + "\n", encoding="utf-8")
    shutil.copyfile(trusted_publishers_path, output / "trusted-publishers.json")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("list-assets")
    listing.add_argument("--defaults", type=Path, required=True)
    assemble = sub.add_parser("stage")
    for name in ("committed", "defaults", "catalog", "downloads", "output", "trusted-publishers"):
        assemble.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "list-assets":
            for tag, asset in release_assets(load(args.defaults)):
                print(tag + "\t" + asset)
        else:
            stage(committed=args.committed, defaults_path=args.defaults,
                  catalog_path=args.catalog, downloads=args.downloads,
                  output=args.output, trusted_publishers_path=args.trusted_publishers)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"Pages campaign staging: {exc}", file=__import__('sys').stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
