"""Version and compatibility facts for real-node DWARF deployments.

The catalog deliberately separates release discovery from runtime proof.  A
new release may be stable and selectable while its scope-specific verification
status remains ``unknown``.  Only retained evidence may promote it to
``confirmed``.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any


DEFAULT_CATALOG_PATH = Path(__file__).resolve().parents[1] / "versions" / "catalog.json"
IMPLEMENTATIONS = {"cardano-node", "amaru"}
CHANNELS = {"stable", "prerelease", "nightly", "main"}
STATUSES = {"confirmed", "unknown", "incompatible", "blocked"}
SCOPES = {"cardano-only", "amaru-only", "mixed"}
ARTIFACT_AVAILABILITY = {"available", "unavailable", "unknown"}
SHA40 = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class CatalogError(ValueError):
    """The checked-in version catalog is internally inconsistent."""


def _require_mapping(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CatalogError(f"{context} must be a mapping")
    return value


def _require_text(record: dict[str, Any], key: str, context: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CatalogError(f"{context}.{key} must be a non-empty string")
    return value


def _validate_verification(record: Any, context: str) -> dict[str, Any]:
    body = _require_mapping(record, context)
    status = _require_text(body, "status", context)
    if status not in STATUSES:
        raise CatalogError(f"{context}.status must be one of {sorted(STATUSES)}")
    if not isinstance(body.get("default", False), bool):
        raise CatalogError(f"{context}.default must be a boolean")
    evidence = body.get("evidence", [])
    issues = body.get("issues", [])
    if not isinstance(evidence, list) or not all(isinstance(item, str) and item for item in evidence):
        raise CatalogError(f"{context}.evidence must be a list of references")
    if not isinstance(issues, list) or not all(isinstance(item, str) and item for item in issues):
        raise CatalogError(f"{context}.issues must be a list of references")
    if status == "confirmed":
        if not evidence:
            raise CatalogError(f"{context} is confirmed but has no evidence")
        _require_text(body, "checked_at", context)
    if status in {"incompatible", "blocked"}:
        _require_text(body, "reason", context)
    return body


def validate_version_catalog(data: Any) -> dict[str, Any]:
    """Validate and return a detached catalog mapping.

    This is intentionally stricter than the dashboard needs: invalid evidence
    must fail closed before it can influence deployment policy.
    """

    catalog = copy.deepcopy(_require_mapping(data, "catalog"))
    if catalog.get("schema_version") != 1:
        raise CatalogError("catalog.schema_version must be 1")
    _require_text(catalog, "updated_at", "catalog")
    releases = catalog.get("releases")
    pairs = catalog.get("compatibility_pairs")
    if not isinstance(releases, list):
        raise CatalogError("catalog.releases must be a list")
    if not isinstance(pairs, list):
        raise CatalogError("catalog.compatibility_pairs must be a list")

    release_keys: set[tuple[str, str]] = set()
    supporting_cardano_references: list[tuple[str, str]] = []
    default_scopes: dict[str, list[str]] = {scope: [] for scope in SCOPES}
    for index, item in enumerate(releases):
        context = f"catalog.releases[{index}]"
        release = _require_mapping(item, context)
        implementation = _require_text(release, "implementation", context)
        version = _require_text(release, "version", context)
        if implementation not in IMPLEMENTATIONS:
            raise CatalogError(f"{context}.implementation must be one of {sorted(IMPLEMENTATIONS)}")
        key = (implementation, version)
        if key in release_keys:
            raise CatalogError(f"duplicate release {implementation} {version}")
        release_keys.add(key)
        channel = _require_text(release, "channel", context)
        if channel not in CHANNELS:
            raise CatalogError(f"{context}.channel must be one of {sorted(CHANNELS)}")
        revision = _require_text(release, "source_revision", context)
        if not SHA40.fullmatch(revision):
            raise CatalogError(f"{context}.source_revision must be an exact 40-character Git revision")
        artifacts = release.get("artifacts", [])
        if not isinstance(artifacts, list):
            raise CatalogError(f"{context}.artifacts must be a list")
        for artifact_index, artifact_value in enumerate(artifacts):
            artifact_context = f"{context}.artifacts[{artifact_index}]"
            artifact = _require_mapping(artifact_value, artifact_context)
            _require_text(artifact, "kind", artifact_context)
            _require_text(artifact, "reference", artifact_context)
            availability = _require_text(artifact, "availability", artifact_context)
            if availability not in ARTIFACT_AVAILABILITY:
                raise CatalogError(
                    f"{artifact_context}.availability must be one of {sorted(ARTIFACT_AVAILABILITY)}"
                )
            if artifact.get("kind") == "oci" and availability == "available":
                digest = artifact.get("digest")
                if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
                    raise CatalogError(f"{artifact_context} requires an immutable sha256 digest")
        verification = _require_mapping(release.get("verification", {}), f"{context}.verification")
        for scope, record in verification.items():
            if scope not in SCOPES:
                raise CatalogError(f"{context}.verification has unknown scope {scope!r}")
            checked = _validate_verification(record, f"{context}.verification.{scope}")
            supporting_cardano_version = checked.get("supporting_cardano_version")
            if supporting_cardano_version is not None:
                if (
                    implementation != "amaru"
                    or scope != "amaru-only"
                    or not isinstance(supporting_cardano_version, str)
                    or not supporting_cardano_version.strip()
                ):
                    raise CatalogError(
                        f"{context}.verification.{scope}.supporting_cardano_version "
                        "is only valid as a non-empty value for Amaru-only verification"
                    )
                supporting_cardano_references.append(
                    (f"{context}.verification.{scope}", supporting_cardano_version)
                )
            if (
                implementation == "amaru"
                and scope == "amaru-only"
                and checked.get("status") == "confirmed"
                and supporting_cardano_version is None
            ):
                raise CatalogError(
                    f"{context}.verification.{scope} is confirmed but has no "
                    "supporting_cardano_version"
                )
            if checked.get("default"):
                expected_scope = "cardano-only" if implementation == "cardano-node" else "amaru-only"
                if scope != expected_scope:
                    raise CatalogError(
                        f"{context}.verification.{scope} cannot be a release default; use a mixed compatibility pair"
                    )
                default_scopes[scope].append(f"{implementation}:{version}")

    for context, version in supporting_cardano_references:
        if ("cardano-node", version) not in release_keys:
            raise CatalogError(
                f"{context}.supporting_cardano_version references unknown "
                f"cardano-node {version}"
            )

    pair_ids: set[str] = set()
    pair_keys: set[tuple[str, str]] = set()
    for index, item in enumerate(pairs):
        context = f"catalog.compatibility_pairs[{index}]"
        pair = _require_mapping(item, context)
        pair_id = _require_text(pair, "id", context)
        cardano_version = _require_text(pair, "cardano_version", context)
        amaru_version = _require_text(pair, "amaru_version", context)
        if pair_id in pair_ids:
            raise CatalogError(f"duplicate compatibility pair id {pair_id}")
        key = (cardano_version, amaru_version)
        if key in pair_keys:
            raise CatalogError(f"duplicate compatibility pair {cardano_version}/{amaru_version}")
        if ("cardano-node", cardano_version) not in release_keys:
            raise CatalogError(f"{context} references unknown cardano-node {cardano_version}")
        if ("amaru", amaru_version) not in release_keys:
            raise CatalogError(f"{context} references unknown Amaru {amaru_version}")
        pair_ids.add(pair_id)
        pair_keys.add(key)
        checked = _validate_verification(pair, context)
        for revision_key in ("topology_revision", "dwarf_revision"):
            revision = _require_text(pair, revision_key, context)
            if not SHA40.fullmatch(revision):
                raise CatalogError(f"{context}.{revision_key} must be an exact 40-character Git revision")
        if checked.get("default"):
            default_scopes["mixed"].append(pair_id)

    for scope, records in default_scopes.items():
        if len(records) > 1:
            raise CatalogError(f"multiple defaults declared for {scope}: {', '.join(records)}")
    return catalog


def load_version_catalog(path: str | Path = DEFAULT_CATALOG_PATH) -> dict[str, Any]:
    catalog_path = Path(path)
    try:
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogError(f"cannot load version catalog {catalog_path}: {exc}") from exc
    return validate_version_catalog(data)


def resolve_release(catalog: dict[str, Any], implementation: str, version: str) -> dict[str, Any]:
    for release in catalog.get("releases", []):
        if release.get("implementation") == implementation and release.get("version") == version:
            return release
    raise CatalogError(f"unknown release {implementation} {version}")


def resolve_verification(
    catalog: dict[str, Any], implementation: str, version: str, scope: str
) -> dict[str, Any]:
    if scope not in SCOPES:
        raise CatalogError(f"unknown deployment scope {scope}")
    release = resolve_release(catalog, implementation, version)
    record = (release.get("verification") or {}).get(scope)
    if record is None:
        return {"status": "unknown", "default": False, "evidence": [], "issues": []}
    return record


def resolve_default(catalog: dict[str, Any], scope: str) -> dict[str, Any]:
    if scope == "mixed":
        defaults = [pair for pair in catalog.get("compatibility_pairs", []) if pair.get("default")]
        if len(defaults) != 1:
            raise CatalogError(f"expected exactly one default for mixed, found {len(defaults)}")
        return {"kind": "compatibility-pair", "status": defaults[0]["status"], "pair": defaults[0]}
    implementation = {"cardano-only": "cardano-node", "amaru-only": "amaru"}.get(scope)
    if implementation is None:
        raise CatalogError(f"unknown deployment scope {scope}")
    matches = []
    for release in catalog.get("releases", []):
        if release.get("implementation") != implementation:
            continue
        verification = (release.get("verification") or {}).get(scope) or {}
        if verification.get("default"):
            matches.append((release, verification))
    if len(matches) != 1:
        raise CatalogError(f"expected exactly one default for {scope}, found {len(matches)}")
    release, verification = matches[0]
    return {"kind": "release", "status": verification["status"], "release": release}


def _profile_scope(data: dict[str, Any]) -> str:
    haskell = int(data.get("haskell_count", data.get("node_count", 0)) or 0)
    amaru = int(data.get("amaru_count", data.get("amaru_node_count", 0)) or 0)
    if haskell > 0 and amaru > 0:
        return "mixed"
    if amaru > 0:
        return "amaru-only"
    if haskell > 0:
        return "cardano-only"
    raise CatalogError("profile must contain at least one Cardano-node or Amaru node")


def _latest_stable(catalog: dict[str, Any], implementation: str) -> dict[str, Any]:
    releases = [
        release
        for release in catalog.get("releases", [])
        if release.get("implementation") == implementation and release.get("channel") == "stable"
    ]
    if not releases:
        raise CatalogError(f"no stable {implementation} releases are present in the version catalog")
    return max(releases, key=lambda release: (str(release.get("released_at") or ""), release["version"]))


def _pair_by_id(catalog: dict[str, Any], pair_id: str) -> dict[str, Any]:
    for pair in catalog.get("compatibility_pairs", []):
        if pair.get("id") == pair_id:
            return pair
    raise CatalogError(f"unknown compatibility_pair {pair_id!r}")


def _pair_by_versions(
    catalog: dict[str, Any], cardano_version: str, amaru_version: str
) -> dict[str, Any] | None:
    for pair in catalog.get("compatibility_pairs", []):
        if pair.get("cardano_version") == cardano_version and pair.get("amaru_version") == amaru_version:
            return pair
    return None


def resolve_profile_versions(
    data: dict[str, Any], catalog: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Freeze a profile's version policy into exact, evidence-labelled releases."""

    checked_catalog = validate_version_catalog(catalog) if catalog is not None else load_version_catalog()
    scope = _profile_scope(data)
    policy = str(data.get("version_policy") or "legacy")
    if policy == "legacy":
        return {
            "policy": "legacy",
            "scope": scope,
            "status": "unknown",
            "resolved": {},
            "supporting": {},
            "pair": None,
            "requires_acknowledgement": False,
            "blocked": False,
            "reason": "No version policy is declared; preserve existing deployment behavior without making a version claim.",
        }
    if policy not in {"latest-confirmed", "latest-stable", "exact"}:
        raise CatalogError(
            "profile.version_policy must be one of latest-confirmed, latest-stable, or exact"
        )

    pair: dict[str, Any] | None = None
    resolved: dict[str, dict[str, Any]] = {}
    verification: dict[str, Any]
    if scope == "mixed":
        if policy == "latest-confirmed":
            default = resolve_default(checked_catalog, "mixed")
            pair = default["pair"]
            if pair["status"] != "confirmed":
                raise CatalogError("the mixed default is not confirmed")
        elif policy == "latest-stable":
            cardano = _latest_stable(checked_catalog, "cardano-node")
            amaru = _latest_stable(checked_catalog, "amaru")
            pair = _pair_by_versions(checked_catalog, cardano["version"], amaru["version"])
            if pair is None:
                verification = {
                    "status": "unknown",
                    "default": False,
                    "reason": "The newest stable release combination is not present in the compatibility matrix.",
                    "evidence": [],
                    "issues": [],
                }
                resolved = {"cardano-node": cardano, "amaru": amaru}
        else:
            pair_id = str(data.get("compatibility_pair") or "").strip()
            if pair_id:
                pair = _pair_by_id(checked_catalog, pair_id)
            else:
                cardano_version = str(data.get("cardano_version") or "").strip()
                amaru_version = str(data.get("amaru_version") or "").strip()
                if not cardano_version or not amaru_version:
                    raise CatalogError(
                        "an exact mixed profile requires compatibility_pair or both cardano_version and amaru_version"
                    )
                cardano = resolve_release(checked_catalog, "cardano-node", cardano_version)
                amaru = resolve_release(checked_catalog, "amaru", amaru_version)
                pair = _pair_by_versions(checked_catalog, cardano_version, amaru_version)
                if pair is None:
                    verification = {
                        "status": "unknown",
                        "default": False,
                        "reason": "The exact release combination is not present in the compatibility matrix.",
                        "evidence": [],
                        "issues": [],
                    }
                    resolved = {"cardano-node": cardano, "amaru": amaru}
        if pair is not None:
            resolved = {
                "cardano-node": resolve_release(
                    checked_catalog, "cardano-node", pair["cardano_version"]
                ),
                "amaru": resolve_release(checked_catalog, "amaru", pair["amaru_version"]),
            }
            verification = pair
    else:
        implementation = "cardano-node" if scope == "cardano-only" else "amaru"
        if policy == "latest-confirmed":
            default = resolve_default(checked_catalog, scope)
            release = default["release"]
            verification = resolve_verification(
                checked_catalog, implementation, release["version"], scope
            )
            if verification["status"] != "confirmed":
                raise CatalogError(f"the {scope} default is not confirmed")
        elif policy == "latest-stable":
            release = _latest_stable(checked_catalog, implementation)
            verification = resolve_verification(
                checked_catalog, implementation, release["version"], scope
            )
        else:
            field = "cardano_version" if implementation == "cardano-node" else "amaru_version"
            version = str(data.get(field) or "").strip()
            if not version:
                raise CatalogError(f"an exact {scope} profile requires {field}")
            release = resolve_release(checked_catalog, implementation, version)
            verification = resolve_verification(checked_catalog, implementation, version, scope)
        resolved = {implementation: release}

    supporting: dict[str, dict[str, Any]] = {}
    if scope == "amaru-only":
        supporting_cardano_version = verification.get("supporting_cardano_version")
        if supporting_cardano_version:
            supporting["cardano-node"] = resolve_release(
                checked_catalog, "cardano-node", str(supporting_cardano_version)
            )
        elif verification.get("status") == "confirmed":
            raise CatalogError(
                "confirmed Amaru-only verification has no supporting Cardano release"
            )

    status = str(verification["status"])
    return {
        "policy": policy,
        "scope": scope,
        "status": status,
        "resolved": resolved,
        "supporting": supporting,
        "pair": pair,
        "requires_acknowledgement": status == "unknown",
        "blocked": status in {"incompatible", "blocked"},
        "reason": str(verification.get("reason") or ""),
        "evidence": list(verification.get("evidence") or []),
        "issues": list(verification.get("issues") or []),
    }
