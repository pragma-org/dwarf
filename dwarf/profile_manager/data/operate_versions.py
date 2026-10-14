"""Read-only data model for the node release and compatibility catalog."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from profile_manager.deployment_versions import version_catalog_revision
from profile_manager.operator_version_decisions import (
    OperatorVersionDecisionLedger,
    active_decisions,
    active_preferences,
)
from profile_manager.version_catalog import CatalogError, resolve_default, resolve_verification
from profile_manager.version_discovery import (
    load_effective_version_catalog,
    read_refresh_status,
)


def _datetime_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _release_sort_key(release: dict[str, Any]) -> tuple[datetime, str]:
    parsed = _datetime_utc(release.get("released_at"))
    return (parsed or datetime.min.replace(tzinfo=timezone.utc), str(release.get("version") or ""))


def _human_date(value: object) -> str:
    parsed = _datetime_utc(value)
    if parsed is None:
        return "Unknown date"
    return f"{parsed.strftime('%b')} {parsed.day}, {parsed.year}"


def _human_timestamp(value: object) -> str:
    parsed = _datetime_utc(value)
    if parsed is None:
        return "Not checked"
    return f"{parsed.strftime('%b')} {parsed.day}, {parsed.year} · {parsed.strftime('%H:%M')} UTC"


def _artifact_presentation(artifact: dict[str, Any]) -> dict[str, Any]:
    availability = str(artifact.get("availability") or "unknown")
    digest = artifact.get("digest")
    public = artifact.get("public")
    platforms = [str(item) for item in (artifact.get("platforms") or [])]
    if availability == "available" and digest and public is True and platforms:
        eligibility = "public-platform-eligible"
        label = "Public immutable artifact · " + ", ".join(platforms)
    elif availability == "available" and digest and public is False:
        eligibility = "not-public"
        label = "Immutable identity recorded · not public"
    elif availability == "available" and digest:
        eligibility = "identity-recorded"
        label = "Immutable identity recorded · public/platform eligibility unverified"
    elif availability == "unavailable":
        eligibility = "unavailable"
        label = "Artifact unavailable"
    else:
        eligibility = "unknown"
        label = "Artifact eligibility unknown"
    display_label = {
        "public-platform-eligible": "Verified",
        "identity-recorded": "Unverified",
        "unknown": "Unknown",
    }.get(eligibility, label)
    return {
        "artifact_public": public,
        "artifact_platforms": platforms,
        "artifact_eligibility": eligibility,
        "artifact_eligibility_label": label,
        "artifact_eligibility_display_label": display_label,
    }


def _compatibility_matrix(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    cardano_versions = list(dict.fromkeys(str(pair["cardano_version"]) for pair in pairs))
    amaru_versions = list(dict.fromkeys(str(pair["amaru_version"]) for pair in pairs))
    by_versions: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for pair in pairs:
        key = (str(pair["cardano_version"]), str(pair["amaru_version"]))
        by_versions.setdefault(key, []).append(pair)
    rows = []
    for cardano_version in cardano_versions:
        cells = []
        for amaru_version in amaru_versions:
            matching_pairs = by_versions.get((cardano_version, amaru_version), [])
            if not matching_pairs:
                cells.append(
                    {
                        "amaru_version": amaru_version,
                        "recorded": False,
                        "pair_id": None,
                        "status": "not-checked",
                        "status_label": "Not checked",
                        "default": False,
                        "reason": "No compatibility proof is recorded for this exact pair.",
                        "evidence": [],
                        "records": [],
                        "recorded_count": 0,
                    }
                )
                continue
            records = [
                {
                    "pair_id": pair["id"],
                    "scope": pair.get("scope") or "mixed",
                    "status": pair["status"],
                    "status_label": str(pair["status"]).replace("-", " ").title(),
                    "default": bool(pair.get("default")),
                    "reason": pair.get("reason") or "No qualification reason recorded.",
                    "evidence": list(pair.get("evidence") or []),
                    "issues": list(pair.get("issues") or []),
                    "checked_at": pair.get("checked_at"),
                    "checked_human": _human_timestamp(pair.get("checked_at")),
                    "topology_revision": pair.get("topology_revision"),
                    "dwarf_revision": pair.get("dwarf_revision"),
                }
                for pair in matching_pairs
            ]
            first = records[0]
            cells.append(
                {
                    "amaru_version": amaru_version,
                    "recorded": True,
                    "pair_id": first["pair_id"] if len(records) == 1 else None,
                    "status": first["status"] if len(records) == 1 else "multiple",
                    "status_label": first["status_label"] if len(records) == 1 else "Multiple records",
                    "default": any(record["default"] for record in records),
                    "reason": first["reason"] if len(records) == 1 else "Multiple scoped records exist for this version pair.",
                    "evidence": [item for record in records for item in record["evidence"]],
                    "records": records,
                    "recorded_count": len(records),
                }
            )
        rows.append({"cardano_version": cardano_version, "cells": cells})
    return {
        "cardano_versions": cardano_versions,
        "amaru_versions": amaru_versions,
        "rows": rows,
        "recorded_count": len(pairs),
    }


def version_catalog_view() -> dict[str, Any]:
    catalog = load_effective_version_catalog()
    refresh = read_refresh_status()
    releases = []
    for release in catalog["releases"]:
        artifact = next(
            (
                item
                for item in release.get("artifacts", [])
                if item.get("availability") == "available" and item.get("digest")
            ),
            (release.get("artifacts") or [{}])[0],
        )
        scopes = (
            ("cardano-only", "mixed")
            if release["implementation"] == "cardano-node"
            else ("amaru-only", "mixed")
        )
        scope_statuses = {}
        for scope in scopes:
            record = dict(
                resolve_verification(
                    catalog, release["implementation"], release["version"], scope
                )
            )
            record["checked_human"] = _human_timestamp(record.get("checked_at"))
            scope_statuses[scope] = record
        qualification_statuses = sorted(
            {str(record["status"]) for record in scope_statuses.values()}
        )
        default_scopes = [
            scope for scope, record in scope_statuses.items() if record.get("default")
        ]
        implementation_label = (
            "Amaru" if release["implementation"] == "amaru" else "Cardano"
        )
        releases.append(
            {
                **release,
                "release_id": f"{release['implementation']}:{release['version']}",
                "released_exact": release.get("released_at"),
                "released_human": _human_date(release.get("released_at")),
                "artifact_reference": artifact.get("reference"),
                "artifact_digest": artifact.get("digest"),
                "artifact_availability": artifact.get("availability", "unknown"),
                "scope_statuses": scope_statuses,
                "qualification_statuses": qualification_statuses,
                "qualification_filter": " ".join(qualification_statuses),
                "default_scopes": default_scopes,
                "sort_implementation": implementation_label,
                "sort_release": str(release["version"]),
                "sort_qualification": " ".join(
                    f"{scope} {record['status']}"
                    for scope, record in scope_statuses.items()
                ),
                "sort_default": ", ".join(default_scopes) if default_scopes else "No",
                "sort_technical": str(release.get("source_revision") or ""),
                "search_text": " ".join(
                    str(value or "")
                    for value in (
                        release["implementation"],
                        release["version"],
                        release.get("channel"),
                        release.get("source_revision"),
                        artifact.get("reference"),
                        artifact.get("digest"),
                        " ".join(qualification_statuses),
                    )
                ).lower(),
                **_artifact_presentation(artifact),
            }
        )
        releases[-1]["sort_artifact"] = releases[-1][
            "artifact_eligibility_display_label"
        ]
    releases.sort(
        key=lambda release: release["artifact_eligibility"]
        != "public-platform-eligible"
    )
    implementation_summaries = []
    source_status = dict(refresh.get("source_status") or {})
    implementation_receipts = refresh.get("implementations")
    if not isinstance(implementation_receipts, dict):
        implementation_receipts = {}
    for implementation, label, scope in (
        ("amaru", "Amaru", "amaru-only"),
        ("cardano-node", "Cardano-node", "cardano-only"),
    ):
        matching = [row for row in releases if row["implementation"] == implementation]
        latest = max(
            matching,
            key=_release_sort_key,
        ) if matching else None
        qualified_default = None
        default_state = "No catalog release" if not matching else "No qualified default"
        if matching:
            try:
                default_version = resolve_default(catalog, scope)["release"]["version"]
            except CatalogError:
                default_version = None
            if default_version is not None:
                qualified_default = next(
                    (row for row in matching if row["version"] == default_version), None
                )
                if qualified_default is not None:
                    default_state = "Qualified default"
        receipt = implementation_receipts.get(implementation)
        if not isinstance(receipt, dict):
            legacy_source_state = str(
                source_status.get(implementation) or "not-checked"
            )
            receipt = {
                "state": {
                    "checking": "running",
                    "ok": "success",
                    "error": "error",
                    "not-checked": "never",
                }.get(legacy_source_state, "never"),
                "last_attempt_at": refresh.get("last_attempt_at"),
                "last_success_at": refresh.get("last_success_at"),
                "new_versions": [
                    row for row in refresh.get("new_versions") or []
                    if isinstance(row, dict)
                    and row.get("implementation") == implementation
                ],
                "error": refresh.get("error")
                if legacy_source_state == "error" else None,
            }
        receipt = {
            **receipt,
            "last_attempt_human": _human_timestamp(receipt.get("last_attempt_at")),
            "last_success_human": _human_timestamp(receipt.get("last_success_at")),
        }
        implementation_summaries.append(
            {
                "implementation": implementation,
                "label": label,
                "scope": scope,
                "default_release": qualified_default,
                "default_state": default_state,
                "latest_release": latest,
                "latest_is_default": bool(
                    latest and qualified_default
                    and latest["version"] == qualified_default["version"]
                ),
                "source_status": source_status.get(implementation, "not-checked"),
                "refresh_receipt": receipt,
            }
        )
    operator_error = None
    try:
        operator_state = OperatorVersionDecisionLedger().read()
    except (OSError, ValueError, RuntimeError) as exc:
        operator_state = None
        operator_error = str(exc)
    current_decisions = active_decisions(operator_state) if operator_state else {}
    registrations = [
        {**item, "inspected_human": _human_timestamp(item.get("inspected_at"))}
        for item in (operator_state["registrations"] if operator_state else [])
    ]
    operator = {
        "revision": operator_state["revision"] if operator_state else None,
        "registrations": registrations,
        "decisions": list(operator_state["decisions"]) if operator_state else [],
        "active_decisions": list(current_decisions.values()),
        "active_decision_ids": [
            item["decision_id"]
            for item in current_decisions.values()
            if item["decision"] == "supported"
        ],
        "preferences": list(active_preferences(operator_state).values()) if operator_state else [],
        "error": operator_error,
        # The current finite controller protocol has no read-only image-inspect
        # operation.  Do not render an identity form that would trust claims
        # supplied by the browser.
        "registration_available": True,
        "registration_reason": (
            "Registration requires the configured local controller to inspect an "
            "already-present image. It never pulls or trusts browser-supplied identity."
        ),
    }
    try:
        from profile_manager.binary_imports import list_binary_import_jobs

        operator["binary_imports"] = list_binary_import_jobs()
    except (OSError, RuntimeError, ValueError):
        operator["binary_imports"] = []
    refresh_state = str(refresh.get("state") or "never")
    last_attempt_at = refresh.get("last_attempt_at")
    last_success_at = refresh.get("last_success_at")
    refresh_never = refresh_state == "never" and last_attempt_at is None
    refresh_cached = refresh_state == "error" and last_success_at is not None
    if refresh_never:
        refresh_message = "Discovery has not been checked yet."
    elif refresh_state == "running":
        refresh_message = "A discovery check is in progress."
    elif refresh_cached:
        refresh_message = "Showing retained catalog data after an unsuccessful refresh."
    elif refresh_state == "error":
        refresh_message = "Discovery failed before a successful check; the checked-in catalog remains active."
    else:
        refresh_message = None
    return {
        "updated_at": catalog["updated_at"],
        "updated_human": _human_timestamp(catalog["updated_at"]),
        "catalog_revision": version_catalog_revision(catalog=catalog),
        "releases": releases,
        "implementation_summaries": implementation_summaries,
        "pairs": [dict(pair) for pair in catalog["compatibility_pairs"]],
        "compatibility_matrix": _compatibility_matrix(
            [dict(pair) for pair in catalog["compatibility_pairs"]]
        ),
        "sources": dict(catalog.get("sources") or {}),
        "refresh": refresh,
        "refresh_view": {
            "state": refresh_state,
            "cached": refresh_cached,
            "never": refresh_never,
            "status_message": refresh_message,
            "error": refresh.get("error"),
            "last_attempt_at": last_attempt_at,
            "last_attempt_human": _human_timestamp(last_attempt_at),
            "last_success_at": last_success_at,
            "last_success_human": _human_timestamp(last_success_at),
        },
        "source_checks": [
            {
                "implementation": implementation,
                "label": label,
                "status": source_status.get(implementation, "not-checked"),
            }
            for implementation, label in (("amaru", "Amaru"), ("cardano-node", "Cardano-node"))
        ],
        "operator": operator,
    }


def version_default_summary() -> dict[str, str]:
    """Return the three evidence-backed defaults used by landing-page links."""

    catalog = load_effective_version_catalog()
    cardano = resolve_default(catalog, "cardano-only")["release"]
    amaru_default = resolve_default(catalog, "amaru-only")
    amaru = amaru_default["release"]
    amaru_verification = resolve_verification(
        catalog, "amaru", amaru["version"], "amaru-only"
    )
    mixed = resolve_default(catalog, "mixed")["pair"]
    return {
        "cardano": cardano["version"],
        "amaru": amaru["version"],
        "amaru_support": str(amaru_verification["supporting_cardano_version"]),
        "mixed_cardano": mixed["cardano_version"],
        "mixed_amaru": mixed["amaru_version"],
    }
