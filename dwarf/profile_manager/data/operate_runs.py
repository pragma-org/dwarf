"""Recent forensic runs index for /operate/runs.

Walks recent_runs_payload(limit=N) and re-reads each run's manifest.json
defensively to extract profile_id and target_implementation (which the
upstream payload doesn't carry). Mirrors slice-7's _comparison_from_run
per-run-dir read pattern; missing or malformed manifest -> the affected
fields fall back to None and the row renders with a "—" placeholder.

URL helpers are imported, not inlined:
    _bundle_inspector_url (slice 7) for run-id links
    _profile_url (slice 9) for profile-id links

The status_pill_inventory always returns three pills (all / pass / fail)
even when one count is zero, so the filter UI stays consistent.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from profile_manager.data.compare import _bundle_inspector_url
from profile_manager.data.operate_profiles import _profile_url


_SCENARIO_PHASES = (
    ("setup", "setup"),
    ("load", "load"),
    ("faults", "fault"),
    ("probes", "probe"),
    ("assertions", "assert"),
    ("teardown", "teardown"),
)


def _read_scenario_snapshot(run_dir: Path) -> dict[str, Any]:
    """Read the exact scenario retained in a run bundle, if usable."""
    try:
        value = yaml.safe_load((run_dir / "scenario.yaml").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        return {}
    return value if isinstance(value, dict) else {}


def _scenario_execution_metadata(
    run_dir: Path,
    *,
    profile_id: str | None,
    runtime: str | None,
) -> dict[str, Any]:
    """Normalize execution mode and primitives from retained run inputs."""
    scenario = _read_scenario_snapshot(run_dir)
    if profile_id:
        execution = {"kind": "profile", "label": "profile", "value": profile_id}
    elif isinstance(scenario.get("attach"), dict):
        attach = scenario["attach"]
        value = next(
            (
                candidate for candidate in (
                    attach.get("topology"), attach.get("id"), attach.get("name")
                )
                if isinstance(candidate, str) and candidate
            ),
            None,
        )
        execution = {"kind": "attach", "label": "attach", "value": value}
    elif isinstance(scenario.get("substrate"), dict):
        substrate = scenario["substrate"]
        value = next(
            (
                candidate for candidate in (
                    substrate.get("id"), substrate.get("name"), substrate.get("topology")
                )
                if isinstance(candidate, str) and candidate
            ),
            None,
        )
        if not value:
            parts = []
            if isinstance(substrate.get("nodes"), list):
                parts.append(f"{len(substrate['nodes'])} nodes")
            if isinstance(substrate.get("network"), str) and substrate["network"]:
                parts.append(substrate["network"])
            value = " · ".join(parts) or None
        execution = {"kind": "substrate", "label": "substrate", "value": value}
    elif runtime == "library":
        execution = {"kind": "library", "label": "library", "value": None}
    else:
        execution = {"kind": "unknown", "label": "unknown", "value": None}

    phase_inventory = []
    primitive_names = []
    for key, label in _SCENARIO_PHASES:
        items = scenario.get(key)
        if not isinstance(items, list):
            continue
        primitives = [
            item.get("primitive")
            for item in items
            if isinstance(item, dict) and isinstance(item.get("primitive"), str)
        ]
        if not primitives:
            continue
        phase_inventory.append({
            "key": key,
            "label": label,
            "count": len(primitives),
            "primitives": primitives,
        })
        primitive_names.extend(primitives)

    workload_label = " · ".join(
        f"{phase['label']} {phase['count']}" for phase in phase_inventory
    ) or "—"
    workload_title = "; ".join(
        f"{phase['label']}: {', '.join(phase['primitives'])}"
        for phase in phase_inventory
    )
    return {
        "execution": execution,
        "phase_inventory": phase_inventory,
        "primitive_names": primitive_names,
        "workload_label": workload_label,
        "workload_title": workload_title,
    }


def _interesting_evidence_metadata(
    run_id: str,
    lifecycle_rows: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Attach lifecycle records to their exact source run and derive tags."""
    matching = [
        row for row in (lifecycle_rows or [])
        if row.get("source_run_id") == run_id
    ]
    tags: list[str] = []

    def add_tag(value: str | None) -> None:
        if value and value not in tags:
            tags.append(value)

    inactive = {None, "", "none", "unknown", "unpromoted"}
    for row in matching:
        classification = row.get("classification")
        if classification not in inactive:
            add_tag(classification)
        replay_state = row.get("replay_state")
        if replay_state not in inactive:
            add_tag(f"replay {replay_state}")
        compare_state = row.get("compare_state")
        if compare_state not in inactive:
            add_tag("differential")
            add_tag(f"compare {compare_state}")
        minimization_state = row.get("minimization_state")
        if minimization_state not in inactive:
            add_tag(f"minimize {minimization_state}")
        promotion_state = row.get("promotion_state")
        if promotion_state not in inactive:
            add_tag(promotion_state)

    return {"interesting_evidence": matching, "evidence_tags": tags}


def _known_profile_ids() -> set[str]:
    from profile_manager.data.operate_profiles import operate_profile_entries
    return {entry["id"] for entry in operate_profile_entries()}


def _enrich_run_row(
    run: dict,
    runs_dir: Path,
    profile_ids: set[str] | None = None,
    lifecycle_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Augment a recent_runs_payload entry with profile_id +
    target_implementation by re-reading the run's manifest.json.

    Defensive: missing manifest, malformed JSON, or missing nested key
    falls back to None. The row itself never drops out — recent_runs_payload
    already enumerated it.
    """
    run_id = run["run_id"]
    profile_id: str | None = None
    target_implementation: str | None = None
    manifest: dict[str, Any] = {}
    manifest_path = runs_dir / run_id / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        profile = manifest.get("profile") or {}
        if isinstance(profile, dict):
            profile_id = profile.get("id")
        target = manifest.get("target") or {}
        if isinstance(target, dict):
            target_implementation = target.get("implementation")
    except (json.JSONDecodeError, OSError, KeyError, ValueError):
        pass

    source = run.get("source") or "local"
    is_local = source == "local"
    runtime = run.get("runtime") or manifest.get("runtime")
    scenario_metadata = _scenario_execution_metadata(
        runs_dir / run_id,
        profile_id=profile_id,
        runtime=runtime,
    )
    evidence_metadata = _interesting_evidence_metadata(
        run_id,
        lifecycle_rows if is_local else [],
    )

    # Item A (Phase 4.3 D-1) — thinness-suspicion badge per row. Only
    # evaluated for local bundles (remote rows don't carry the
    # underlying telemetry on this filesystem).
    thinness_signals: list[dict] = []
    if is_local:
        from profile_manager.data.thinness_signals import detect_thinness
        try:
            thinness_signals = detect_thinness(runs_dir / run_id)
        except Exception:  # noqa: BLE001 — never crash the index render
            thinness_signals = []

    return {
        "run_id": run_id,
        "ended_at": run.get("ended_at"),
        "scenario_id": run.get("scenario_id"),
        "profile_id": profile_id,
        "profile_url": _profile_url(profile_id) if profile_id and profile_id in (profile_ids if profile_ids is not None else _known_profile_ids()) else None,
        "runtime": runtime,
        "exit_status": run.get("exit_status"),
        "target_implementation": target_implementation,
        "run_url": _bundle_inspector_url(run_id),
        "source": source,
        "is_local": is_local,
        "thinness_signals": thinness_signals,
        **scenario_metadata,
        **evidence_metadata,
    }


def operate_run_rows(*, runs_dir: Path | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Walk recent_runs_payload, return enriched rows.

    Order is the newest-first sort the upstream extractor produces.
    Remote-source runs render run_id without an inspector link
    (matches existing legacy treatment in dashboard.py).
    """
    from profile_manager.data.runs import recent_runs_payload, _forensic_runs_dir

    base = Path(runs_dir) if runs_dir is not None else _forensic_runs_dir()
    payload = recent_runs_payload(runs_dir=base, limit=limit)
    profile_ids = _known_profile_ids()
    from profile_manager.data.operate_testcases import testcase_catalog_rows
    lifecycle_rows = testcase_catalog_rows(runs_dir=base)
    return [
        _enrich_run_row(
            run,
            runs_dir=base,
            profile_ids=profile_ids,
            lifecycle_rows=lifecycle_rows,
        )
        for run in payload.get("recent_runs", [])
    ]


def apply_run_filters(rows: list[dict[str, Any]], *, outcome: str = "",
                      q: str = "") -> list[dict[str, Any]]:
    """Slice 46 — server-side filter rails over operate_run_rows.

    ``outcome`` matches ``exit_status`` exactly when set; empty string
    means "all". ``q`` is a case-insensitive substring match against
    run identity, target, execution source, and primitive names."""
    out = list(rows)
    if outcome:
        out = [r for r in out if (r.get("exit_status") or "") == outcome]
    if q:
        needle = q.lower()
        def hay(row: dict[str, Any]) -> str:
            execution = row.get("execution") or {}
            parts = [
                row.get("run_id") or "",
                row.get("scenario_id") or "",
                row.get("profile_id") or "",
                row.get("target_implementation") or "",
                execution.get("kind") or "",
                execution.get("label") or "",
                execution.get("value") or "",
                *(row.get("primitive_names") or []),
                *(row.get("evidence_tags") or []),
            ]
            return " ".join(parts).lower()
        out = [r for r in out if needle in hay(r)]
    return out


def status_pill_inventory(rows: list[dict[str, Any]], *, active_outcome: str = "") -> list[dict[str, Any]]:
    """Filter-pill set: all (default-active), pass, fail, error.

    Pills with zero matching rows still render — clicking them shows
    the empty-filtered state honestly. error is the bucket for runs
    that never completed cleanly enough to produce a pass/fail outcome.
    Counts are computed against the unfiltered ``rows`` so the pills
    advertise the corpus size, not the filtered subset.
    """
    pass_count = sum(1 for r in rows if r.get("exit_status") == "pass")
    fail_count = sum(1 for r in rows if r.get("exit_status") == "fail")
    error_count = sum(1 for r in rows if r.get("exit_status") == "error")
    return [
        {"slug": "", "label": "all", "count": len(rows), "active": active_outcome == ""},
        {"slug": "pass", "label": "pass", "count": pass_count, "active": active_outcome == "pass"},
        {"slug": "fail", "label": "fail", "count": fail_count, "active": active_outcome == "fail"},
        {"slug": "error", "label": "error", "count": error_count, "active": active_outcome == "error"},
    ]
