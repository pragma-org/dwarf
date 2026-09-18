#!/usr/bin/env python3
"""Discover upstream releases without turning discovery into runtime proof."""
from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

SCRIPT_DIR = Path(__file__).resolve().parent
DWARF_ROOT = SCRIPT_DIR.parent
if str(DWARF_ROOT) not in sys.path:
    sys.path.insert(0, str(DWARF_ROOT))

from profile_manager.version_catalog import DEFAULT_CATALOG_PATH, validate_version_catalog


REPOSITORIES = {
    "cardano-node": "IntersectMBO/cardano-node",
    "amaru": "pragma-org/amaru",
}


def fetch_json_url(url: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "DWARF-version-catalog-refresh",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def _version_text(implementation: str, tag: str) -> str:
    if implementation == "amaru" and tag.startswith("v"):
        return tag[1:]
    return tag


def _version_sort_key(version: str) -> tuple:
    pieces: list[tuple[int, int | str]] = []
    current = ""
    numeric = version[:1].isdigit()
    for character in version:
        is_numeric = character.isdigit()
        if current and is_numeric != numeric:
            pieces.append((1 if numeric else 0, int(current) if numeric else current.lower()))
            current = ""
        if character.isalnum():
            current += character
            numeric = is_numeric
        elif current:
            pieces.append((1 if numeric else 0, int(current) if numeric else current.lower()))
            current = ""
    if current:
        pieces.append((1 if numeric else 0, int(current) if numeric else current.lower()))
    return tuple(pieces)


def _unknown_verification(implementation: str, checked_at: str) -> dict[str, dict[str, Any]]:
    primary_scope = "cardano-only" if implementation == "cardano-node" else "amaru-only"
    reason = "Discovered from the official release feed; real-node qualification is pending."
    return {
        primary_scope: {
            "status": "unknown",
            "default": False,
            "checked_at": checked_at,
            "reason": reason,
            "evidence": [],
            "issues": [],
        },
        "mixed": {
            "status": "unknown",
            "default": False,
            "checked_at": checked_at,
            "reason": "No exact mixed-pair runtime qualification has been recorded.",
            "evidence": [],
            "issues": [],
        },
    }


def resolve_oci_artifacts(implementation: str, tag: str, release: dict[str, Any]) -> list[dict[str, Any]]:
    """Read a registry manifest descriptor without pulling the image layers."""

    references = {
        "cardano-node": [f"ghcr.io/intersectmbo/cardano-node:{tag}"],
        "amaru": [f"ghcr.io/pragma-org/amaru:{tag.lstrip('v')}"],
    }[implementation]
    artifacts: list[dict[str, Any]] = []
    for reference in references:
        command = [
            "docker",
            "buildx",
            "imagetools",
            "inspect",
            reference,
            "--format",
            "{{json .Manifest}}",
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            result = None
        digest = None
        if result is not None and result.returncode == 0:
            try:
                manifest = json.loads(result.stdout)
                if isinstance(manifest, dict):
                    digest = manifest.get("digest") or manifest.get("Digest")
                elif isinstance(manifest, str):
                    digest = manifest
            except json.JSONDecodeError:
                digest = result.stdout.strip().strip('"')
        if isinstance(digest, str) and digest.startswith("sha256:"):
            artifacts.append(
                {
                    "kind": "oci",
                    "reference": reference,
                    "availability": "available",
                    "digest": digest,
                }
            )
        else:
            artifacts.append(
                {
                    "kind": "oci",
                    "reference": reference,
                    "availability": "unknown",
                }
            )
    return artifacts


def load_git_tag_revisions(repository: str) -> dict[str, str]:
    """Resolve every release tag in one public Git transport request."""

    url = f"https://github.com/{repository}.git"
    result = subprocess.run(
        ["git", "ls-remote", "--tags", url],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"cannot resolve tags for {repository}: {result.stderr.strip()}")
    direct: dict[str, str] = {}
    peeled: dict[str, str] = {}
    for line in result.stdout.splitlines():
        try:
            revision, reference = line.split("\t", 1)
        except ValueError:
            continue
        prefix = "refs/tags/"
        if not reference.startswith(prefix):
            continue
        tag = reference[len(prefix):]
        if tag.endswith("^{}"):
            peeled[tag[:-3]] = revision
        else:
            direct[tag] = revision
    return {tag: peeled.get(tag, revision) for tag, revision in direct.items()}


def refresh_version_catalog(
    catalog: dict[str, Any],
    *,
    fetch_json: Callable[[str], Any] = fetch_json_url,
    artifact_resolver: Callable[[str, str, dict[str, Any]], list[dict[str, Any]]] = resolve_oci_artifacts,
    artifact_limit_per_implementation: int | None = None,
    tag_revision_resolver: Callable[[str, str], str] | None = None,
    checked_at: str | None = None,
) -> dict[str, Any]:
    """Return a deterministic candidate catalog while retaining human evidence."""

    refreshed_at = checked_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    original = copy.deepcopy(catalog)
    existing = {
        (release["implementation"], release["version"]): copy.deepcopy(release)
        for release in original.get("releases", [])
    }
    discovered: dict[tuple[str, str], dict[str, Any]] = {}
    for implementation, repository in REPOSITORIES.items():
        releases_url = f"https://api.github.com/repos/{repository}/releases?per_page=100"
        releases = fetch_json(releases_url)
        if not isinstance(releases, list):
            raise ValueError(f"unexpected release response for {repository}")
        ordered_releases = sorted(
            (release for release in releases if isinstance(release, dict)),
            key=lambda release: _version_sort_key(
                _version_text(implementation, str(release.get("tag_name") or ""))
            ),
            reverse=True,
        )
        artifact_lookups = 0
        for release in ordered_releases:
            if not isinstance(release, dict) or release.get("draft"):
                continue
            tag = str(release.get("tag_name") or "").strip()
            published_at = str(release.get("published_at") or "").strip()
            if not tag or not published_at:
                continue
            version = _version_text(implementation, tag)
            if tag_revision_resolver is None:
                commit_url = f"https://api.github.com/repos/{repository}/commits/{tag}"
                commit = fetch_json(commit_url)
                source_revision = str((commit or {}).get("sha") or "")
            else:
                source_revision = str(tag_revision_resolver(repository, tag) or "")
            key = (implementation, version)
            prior = existing.get(key, {})
            should_resolve_artifacts = (
                artifact_limit_per_implementation is None
                or artifact_lookups < artifact_limit_per_implementation
            )
            artifacts = artifact_resolver(implementation, tag, release) if should_resolve_artifacts else []
            if should_resolve_artifacts:
                artifact_lookups += 1
            if not artifacts and prior.get("artifacts"):
                artifacts = copy.deepcopy(prior["artifacts"])
            discovered[key] = {
                "implementation": implementation,
                "version": version,
                "channel": "prerelease" if release.get("prerelease") else "stable",
                "released_at": published_at,
                "source_revision": source_revision,
                "release_url": str(release.get("html_url") or ""),
                "artifacts": artifacts,
                "verification": copy.deepcopy(
                    prior.get("verification") or _unknown_verification(implementation, refreshed_at)
                ),
            }

    # Retain old releases and their evidence even when the first API page no
    # longer lists them. Discovery is additive; removal is a reviewed action.
    for key, release in existing.items():
        discovered.setdefault(key, release)

    releases = sorted(
        discovered.values(),
        key=lambda record: (
            record["implementation"],
            _version_sort_key(record["version"]),
        ),
        reverse=True,
    )
    candidate = copy.deepcopy(original)
    candidate["updated_at"] = refreshed_at
    candidate.setdefault("sources", {})["release_refresh_at"] = refreshed_at
    candidate["sources"]["cardano_node"] = "https://github.com/IntersectMBO/cardano-node"
    candidate["sources"]["amaru"] = "https://github.com/pragma-org/amaru"
    candidate["releases"] = releases
    return validate_version_catalog(candidate)


def write_catalog_candidate(
    catalog: dict[str, Any], *, output_path: str | Path, source_path: str | Path = DEFAULT_CATALOG_PATH
) -> Path:
    output = Path(output_path).resolve()
    source = Path(source_path).resolve()
    if output == source:
        raise ValueError("refresh refuses to overwrite the source catalog; review a candidate file first")
    validated = validate_version_catalog(catalog)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(validated, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default=str(DEFAULT_CATALOG_PATH))
    parser.add_argument("--output", required=True, help="Reviewable candidate path; cannot equal --catalog")
    parser.add_argument(
        "--registry-limit",
        type=int,
        default=3,
        help="Inspect OCI metadata for only the newest N releases per implementation (default: 3)",
    )
    args = parser.parse_args(argv)
    source_path = Path(args.catalog)
    source = json.loads(source_path.read_text(encoding="utf-8"))
    tag_revisions = {
        repository: load_git_tag_revisions(repository)
        for repository in REPOSITORIES.values()
    }

    def resolve_tag(repository: str, tag: str) -> str:
        revision = tag_revisions.get(repository, {}).get(tag)
        if not revision:
            raise ValueError(f"official release tag {repository}@{tag} was not found by git ls-remote")
        return revision

    candidate = refresh_version_catalog(
        source,
        artifact_limit_per_implementation=max(0, args.registry_limit),
        tag_revision_resolver=resolve_tag,
    )
    written = write_catalog_candidate(candidate, output_path=args.output, source_path=source_path)
    print(written)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
