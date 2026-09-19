"""Catalog-backed data and read-only resolution for the local run wizard."""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Any
from urllib.parse import urlsplit

from profile_manager.data.catalog_definitions import list_definitions
from profile_manager.data.scenarios import _list_scenarios_for_compare
from profile_manager.profiles import load_profiles
from profile_manager.run_plan import RunPlanError, RunPlanRequest, resolve_run_plan


_PREFERRED_DEFAULT = "runtime-substrate-honest-baseline-docker-mode-example-smoke"


def run_wizard_catalog() -> dict[str, Any]:
    scenarios = _list_scenarios_for_compare()
    scenario_ids = {row["id"] for row in scenarios}
    requested_default = os.environ.get("ADA2_DWARF_RUN_DEFAULT_SCENARIO", "").strip()
    default_scenario_id = (
        requested_default
        if requested_default in scenario_ids
        else _PREFERRED_DEFAULT
        if _PREFERRED_DEFAULT in scenario_ids
        else (scenarios[0]["id"] if scenarios else None)
    )
    profiles = [
        {
            "id": profile.id,
            "label": profile.label,
            "haskell_count": profile.node_count,
            "amaru_count": profile.amaru_node_count,
            "version_policy": profile.version_policy,
            "measurement_target_mode": profile.measurement_target_mode,
        }
        for profile in load_profiles()
    ]
    measurement_profiles = [
        {
            "id": record.definition_id,
            "title": record.data.get("title")
            or record.data.get("label")
            or record.definition_id,
            "implementation": record.data.get("implementation"),
            "measurement_count": len(record.data.get("measurements") or []),
        }
        for record in list_definitions("measurement-profiles")
    ]
    default_plan = None
    default_error = None
    if default_scenario_id:
        try:
            default_plan = resolve_run_plan(
                RunPlanRequest(scenario_id=default_scenario_id)
            )
        except RunPlanError as exc:
            default_error = exc.as_dict()
    return {
        "default_scenario_id": default_scenario_id,
        "scenarios": scenarios,
        "profiles": profiles,
        "measurement_profiles": measurement_profiles,
        "version_policies": ["latest-confirmed", "latest-stable", "exact"],
        "default_plan": default_plan,
        "default_error": default_error,
    }


def dispatch_run_resolve_request(*, method: str, path: str, body: bytes):
    if urlsplit(path).path != "/api/run/resolve":
        return None
    if method != "POST":
        return (405, "application/json; charset=utf-8", b'{"ok":false,"error":{"message":"use POST"}}')
    if len(body) > 4096:
        return (413, "application/json; charset=utf-8", b'{"ok":false,"error":{"field":"request","message":"request body is too large"}}')
    try:
        value = json.loads(body.decode("utf-8"))
        request = RunPlanRequest.from_mapping(value)
        plan = resolve_run_plan(request)
    except (UnicodeDecodeError, json.JSONDecodeError):
        response = {
            "ok": False,
            "error": {"field": "request", "message": "request body must be valid JSON"},
        }
        return (400, "application/json; charset=utf-8", json.dumps(response).encode("utf-8"))
    except RunPlanError as exc:
        response = {"ok": False, "error": exc.as_dict()}
        return (400, "application/json; charset=utf-8", json.dumps(response).encode("utf-8"))
    response = {"ok": True, "plan": plan}
    return (200, "application/json; charset=utf-8", json.dumps(response).encode("utf-8"))
