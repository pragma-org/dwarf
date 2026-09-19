"""Evidence-backed presentation facts for the guided run workflow."""
from __future__ import annotations

import json
import os
import statistics
from pathlib import Path


def profile_qualification(preview):
    if not isinstance(preview, dict):
        return {
            "qualification": "unmarked",
            "status_label": "",
            "status_reason": "Qualification could not be resolved.",
        }
    if preview.get("status") == "confirmed":
        return {
            "qualification": "confirmed",
            "status_label": "CONFIRMED",
            "status_reason": "This profile resolves to a confirmed version claim.",
        }
    return {
        "qualification": "unmarked",
        "status_label": "",
        "status_reason": str(preview.get("reason") or "No retained confirmation is available."),
    }


def _runtime_runs_dir(runs_dir):
    if runs_dir is not None:
        return Path(runs_dir)
    configured = os.environ.get("ADA2_DWARF_RUNS_DIR", "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[1] / "runs"


def _manifest_identity(run_dir, manifest):
    plan_path = run_dir / "launch" / "plan.json"
    try:
        stored_plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        stored_plan = None
    if isinstance(stored_plan, dict):
        scenario = stored_plan.get("scenario") or {}
        profile = stored_plan.get("profile") or {}
        return {
            "scenario_id": scenario.get("id"),
            "profile_id": profile.get("id"),
            "implementation": (scenario.get("target") or {}).get("implementation"),
            "version": (scenario.get("target") or {}).get("version"),
        }
    return {
        "scenario_id": (manifest.get("scenario") or {}).get("id"),
        "profile_id": (manifest.get("profile") or {}).get("id"),
        "implementation": (manifest.get("target") or {}).get("implementation"),
        "version": (manifest.get("target") or {}).get("version"),
    }


def _matches_plan(identity, plan):
    scenario = plan.get("scenario") or {}
    target = scenario.get("target") or {}
    profile = plan.get("profile") or {}
    return (
        identity.get("scenario_id") == scenario.get("id")
        and identity.get("profile_id") == profile.get("id")
        and identity.get("implementation") == target.get("implementation")
        and identity.get("version") == target.get("version")
    )


def historical_runtime_estimate(plan, *, runs_dir=None):
    root = _runtime_runs_dir(runs_dir)
    if not root.is_dir():
        return {
            "available": False,
            "sample_count": 0,
            "basis": "no-matching-retained-runs",
        }
    candidates = sorted(
        (path for path in root.glob("*/manifest.json") if path.is_file()),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )[:20]
    durations = []
    for path in candidates:
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if manifest.get("exit_status") != "pass":
            continue
        if not _matches_plan(_manifest_identity(path.parent, manifest), plan):
            continue
        value = (manifest.get("resource_snapshot") or {}).get("wall_time_seconds")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            durations.append(float(value))
    if not durations:
        return {
            "available": False,
            "sample_count": 0,
            "basis": "no-matching-retained-runs",
        }
    return {
        "available": True,
        "median_seconds": float(statistics.median(durations)),
        "minimum_seconds": min(durations),
        "maximum_seconds": max(durations),
        "sample_count": len(durations),
        "basis": "matching-retained-runs",
    }
