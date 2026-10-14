"""Cached, non-promoting discovery of official node releases.

The checked-in catalog remains the qualification authority.  This module may
add fully identified upstream releases to the operator view as ``unknown``;
it never changes retained verification or compatibility evidence.
"""
from __future__ import annotations

import copy
import fcntl
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from profile_manager.config import storage_roots
from profile_manager.operator_version_decisions import OperatorVersionDecisionLedger

from profile_manager.version_catalog import (
    DEFAULT_CATALOG_PATH,
    DIGEST,
    SHA40,
    enrich_retained_oci_artifacts,
    load_version_catalog,
    validate_version_catalog,
)


DEFAULT_MAX_AGE_SECONDS = 6 * 60 * 60
_THREADS: set[threading.Thread] = set()
_THREADS_LOCK = threading.Lock()
_REGISTRATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")
IMPLEMENTATIONS = ("amaru", "cardano-node")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime | None = None) -> str:
    return (value or _now()).isoformat().replace("+00:00", "Z")


def _state_dir(state_dir: str | Path | None = None) -> Path:
    if state_dir is not None:
        return Path(state_dir)
    catalog_state_dir = os.environ.get(
        "ADA2_DWARF_VERSION_CATALOG_STATE_DIR", ""
    ).strip()
    if catalog_state_dir:
        return Path(catalog_state_dir)
    return storage_roots().state


def _root(state_dir: str | Path | None = None) -> Path:
    return _state_dir(state_dir) / "version-catalog"


def candidate_path(state_dir: str | Path | None = None) -> Path:
    return _root(state_dir) / "discovery-candidate.json"


def status_path(state_dir: str | Path | None = None) -> Path:
    return _root(state_dir) / "refresh-status.json"


def load_operator_registrations(
    state_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Read the local overlay without merging it into shared qualification."""
    state = OperatorVersionDecisionLedger(state_dir).read()
    return {
        "revision": state["revision"],
        "registrations": copy.deepcopy(state["registrations"]),
    }


def load_operator_registration(
    registration_id: str, state_dir: str | Path | None = None
) -> dict[str, Any]:
    if not isinstance(registration_id, str) or not _REGISTRATION_ID.fullmatch(
        registration_id
    ):
        raise ValueError("registration_id must be a safe local identity")
    overlay = load_operator_registrations(state_dir)
    matches = [
        row
        for row in overlay["registrations"]
        if row.get("registration_id") == registration_id
    ]
    if len(matches) != 1:
        raise ValueError("operator registration is missing or ambiguous")
    return {"revision": overlay["revision"], "registration": matches[0]}


def _atomic_json(path: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(body, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def read_refresh_status(state_dir: str | Path | None = None) -> dict[str, Any]:
    path = status_path(state_dir)
    fallback = {
        "schema_version": 1,
        "state": "never",
        "last_attempt_at": None,
        "last_success_at": None,
        "source_status": {"cardano-node": "not-checked", "amaru": "not-checked"},
        "new_versions": [],
        "error": None,
        "implementations": {
            name: {
                "state": "never",
                "last_attempt_at": None,
                "last_success_at": None,
                "new_versions": [],
                "error": None,
            }
            for name in IMPLEMENTATIONS
        },
    }
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback
    if not isinstance(body, dict) or body.get("schema_version") != 1:
        return fallback
    status = {**fallback, **body}
    source_status = dict(status.get("source_status") or {})
    implementations = body.get("implementations")
    if not isinstance(implementations, dict):
        implementations = {}
    normalized = {}
    for implementation in IMPLEMENTATIONS:
        source_state = str(source_status.get(implementation) or "not-checked")
        legacy_state = {
            "checking": "running",
            "ok": "success",
            "error": "error",
            "not-checked": "never",
        }.get(source_state, "never")
        retained = implementations.get(implementation)
        if not isinstance(retained, dict):
            retained = {}
        attempted = retained.get("last_attempt_at")
        if attempted is None and legacy_state != "never":
            attempted = status.get("last_attempt_at")
        succeeded = retained.get("last_success_at")
        if succeeded is None and legacy_state == "success":
            succeeded = status.get("last_success_at")
        normalized[implementation] = {
            "state": retained.get("state") or legacy_state,
            "last_attempt_at": attempted,
            "last_success_at": succeeded,
            "new_versions": list(
                retained.get("new_versions")
                if isinstance(retained.get("new_versions"), list)
                else [
                    row for row in status.get("new_versions") or []
                    if isinstance(row, dict)
                    and row.get("implementation") == implementation
                ]
            ),
            "error": retained.get("error") or (
                status.get("error") if legacy_state == "error" else None
            ),
        }
    status["implementations"] = normalized
    return status


def refresh_is_stale(
    status: dict[str, Any], *, max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    now: datetime | None = None,
) -> bool:
    if status.get("state") == "running":
        return False
    text = status.get("last_attempt_at")
    if not isinstance(text, str) or not text:
        return True
    try:
        checked = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return True
    reference = now or _now()
    return (reference - checked).total_seconds() >= max(0, max_age_seconds)


def _has_immutable_artifact(release: dict[str, Any]) -> bool:
    if not SHA40.fullmatch(str(release.get("source_revision") or "")):
        return False
    return any(
        artifact.get("availability") == "available"
        and DIGEST.fullmatch(str(artifact.get("digest") or ""))
        for artifact in release.get("artifacts") or []
        if isinstance(artifact, dict)
    )


def merge_discovery_candidate(
    source: dict[str, Any], candidate: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Add only fully identified new releases while retaining source facts."""

    checked_source = validate_version_catalog(source)
    checked_candidate = validate_version_catalog(candidate)
    merged = copy.deepcopy(checked_source)
    known = {
        (release["implementation"], release["version"])
        for release in checked_source["releases"]
    }
    retained_index = {
        (release["implementation"], release["version"]): index
        for index, release in enumerate(merged["releases"])
    }
    discovered: list[dict[str, str]] = []
    for release in checked_candidate["releases"]:
        key = (release["implementation"], release["version"])
        if key in known:
            index = retained_index[key]
            merged["releases"][index] = enrich_retained_oci_artifacts(
                merged["releases"][index], release.get("artifacts") or []
            )
            continue
        if not _has_immutable_artifact(release):
            continue
        clean = copy.deepcopy(release)
        for record in (clean.get("verification") or {}).values():
            record["status"] = "unknown"
            record["default"] = False
            record["evidence"] = []
            record["issues"] = []
            record["reason"] = "Discovered from the official release feed; real-node qualification is pending."
            record.pop("supporting_cardano_version", None)
        merged["releases"].append(clean)
        known.add(key)
        discovered.append({"implementation": key[0], "version": key[1]})
    merged["updated_at"] = max(
        str(checked_source.get("updated_at") or ""),
        str(checked_candidate.get("updated_at") or ""),
    )
    merged.setdefault("sources", {})["runtime_discovery_overlay"] = str(
        checked_candidate.get("updated_at") or ""
    )
    merged["releases"].sort(
        key=lambda item: (
            item["implementation"], str(item.get("released_at") or ""), item["version"]
        ),
        reverse=True,
    )
    return validate_version_catalog(merged), discovered


def load_effective_version_catalog(
    source_path: str | Path = DEFAULT_CATALOG_PATH,
    *, state_dir: str | Path | None = None,
) -> dict[str, Any]:
    source = load_version_catalog(source_path)
    try:
        candidate = json.loads(candidate_path(state_dir).read_text(encoding="utf-8"))
        merged, _ = merge_discovery_candidate(source, candidate)
        return merged
    except (OSError, json.JSONDecodeError, ValueError):
        # An invalid runtime overlay must never make retained defaults unusable.
        return source


def perform_release_refresh(
    *,
    implementation: str | None = None,
    state_dir: str | Path | None = None,
    source_path: str | Path = DEFAULT_CATALOG_PATH,
    fetch_json: Callable[[str], Any] | None = None,
    artifact_resolver: Callable[[str, str, dict[str, Any]], list[dict[str, Any]]] | None = None,
    tag_revision_resolver: Callable[[str, str], str] | None = None,
    checked_at: str | None = None,
    manual: bool = False,
) -> dict[str, Any]:
    from scripts.refresh_version_catalog import (
        REPOSITORIES,
        fetch_json_url,
        refresh_version_catalog,
        resolve_oci_artifacts_http,
    )

    if implementation is not None and implementation not in IMPLEMENTATIONS:
        raise ValueError("unknown release implementation")
    selected = IMPLEMENTATIONS if implementation is None else (implementation,)
    attempted_at = checked_at or _timestamp()
    prior_status = read_refresh_status(state_dir)
    source_status = dict(prior_status.get("source_status") or {})
    implementation_status = copy.deepcopy(prior_status["implementations"])
    for name in selected:
        source_status[name] = "checking"
        implementation_status[name] = {
            **implementation_status[name],
            "state": "running",
            "last_attempt_at": attempted_at,
            "error": None,
        }
    running = {
        **prior_status,
        "schema_version": 1,
        "state": "running",
        "manual": bool(manual),
        "last_attempt_at": attempted_at,
        "source_status": source_status,
        "implementations": implementation_status,
        "error": None,
    }
    _atomic_json(status_path(state_dir), running)
    source = load_version_catalog(source_path)
    refresh_base = load_effective_version_catalog(source_path, state_dir=state_dir)
    source_keys = {
        (release["implementation"], release["version"])
        for release in source["releases"]
    }
    raw_fetch = fetch_json or fetch_json_url

    def tracked_fetch(url: str) -> Any:
        implementation = next(
            (
                name for name, repository in REPOSITORIES.items()
                if f"/repos/{repository}/" in url
            ),
            None,
        )
        if implementation:
            source_status[implementation] = "checking"
        try:
            result = raw_fetch(url)
        except Exception:
            if implementation:
                source_status[implementation] = "error"
            raise
        if implementation:
            source_status[implementation] = "ok"
        return result
    resolve_artifact = artifact_resolver or resolve_oci_artifacts_http
    try:
        candidate = refresh_version_catalog(
            refresh_base,
            fetch_json=tracked_fetch,
            artifact_resolver=resolve_artifact,
            artifact_limit_per_implementation=3,
            tag_revision_resolver=tag_revision_resolver,
            checked_at=attempted_at,
            implementations=list(selected),
        )
        _atomic_json(candidate_path(state_dir), candidate)
        _effective, discovered = merge_discovery_candidate(source, candidate)
        # Include only releases actually eligible for the effective catalog.
        eligible = [
            item for item in discovered
            if item["implementation"] in selected
            and (item["implementation"], item["version"]) not in source_keys
        ]
        for name in selected:
            source_status[name] = "ok"
            implementation_status[name] = {
                **implementation_status[name],
                "state": "success",
                "last_attempt_at": attempted_at,
                "last_success_at": attempted_at,
                "new_versions": [
                    row for row in eligible if row["implementation"] == name
                ],
                "error": None,
            }
        completed = {
            **running,
            "state": "success",
            "last_success_at": attempted_at,
            "source_status": source_status,
            "implementations": implementation_status,
            "new_versions": eligible,
            "error": None,
        }
    except Exception as exc:
        failed = [
            name for name in selected if source_status.get(name) == "error"
        ]
        if implementation is not None or not failed:
            failed = list(selected)
        for name in selected:
            if name in failed:
                source_status[name] = "error"
                implementation_status[name] = {
                    **implementation_status[name],
                    "state": "error",
                    "last_attempt_at": attempted_at,
                    "error": str(exc)[:1000],
                }
            elif implementation is None and source_status.get(name) == "checking":
                source_status[name] = "not-checked"
                implementation_status[name] = prior_status["implementations"][name]
        completed = {
            **running,
            "state": "error",
            "source_status": source_status,
            "implementations": implementation_status,
            "error": str(exc)[:1000],
        }
    _atomic_json(status_path(state_dir), completed)
    return completed


def _try_lock(state_dir: str | Path | None):
    path = _root(state_dir) / "refresh.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def start_release_refresh(
    *, implementation: str, manual: bool, state_dir: str | Path | None = None
) -> dict[str, Any]:
    """Start one serialized background refresh and return immediately."""

    if implementation not in IMPLEMENTATIONS:
        raise ValueError("unknown release implementation")

    lock_handle = _try_lock(state_dir)
    if lock_handle is None:
        return {"started": False, **read_refresh_status(state_dir), "state": "running"}

    attempted_at = _timestamp()
    prior = read_refresh_status(state_dir)
    source_status = dict(prior.get("source_status") or {})
    source_status[implementation] = "checking"
    implementation_status = copy.deepcopy(prior["implementations"])
    implementation_status[implementation] = {
        **implementation_status[implementation],
        "state": "running",
        "last_attempt_at": attempted_at,
        "error": None,
    }
    _atomic_json(
        status_path(state_dir),
        {
            **prior,
            "schema_version": 1,
            "state": "running",
            "manual": bool(manual),
            "last_attempt_at": attempted_at,
            "source_status": source_status,
            "implementations": implementation_status,
            "error": None,
        },
    )

    def worker() -> None:
        try:
            perform_release_refresh(
                implementation=implementation,
                state_dir=state_dir, checked_at=attempted_at, manual=manual
            )
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            lock_handle.close()
            with _THREADS_LOCK:
                _THREADS.discard(threading.current_thread())

    thread = threading.Thread(target=worker, name="dwarf-version-refresh", daemon=True)
    with _THREADS_LOCK:
        _THREADS.add(thread)
    thread.start()
    return {"started": True, **read_refresh_status(state_dir)}


def ensure_release_refresh(*, max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS) -> dict[str, Any]:
    """Compatibility shim: automatic upstream discovery is intentionally disabled."""

    del max_age_seconds
    return {"started": False, **read_refresh_status()}
