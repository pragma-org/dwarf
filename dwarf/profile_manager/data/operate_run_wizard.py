"""Catalog-backed data and read-only resolution for the local run wizard."""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Any
from urllib.parse import urlsplit

from profile_manager.data.catalog_definitions import list_definitions
from profile_manager.data.scenarios import _list_scenarios_for_compare
from profile_manager.deployment_versions import profile_deployment_version_preview
from profile_manager.profiles import load_profiles
from profile_manager.run_presentation import profile_qualification
from profile_manager.run_plan import (
    RunPlanError,
    RunPlanRequest,
    digest_run_plan,
    resolve_run_plan,
)


_PREFERRED_DEFAULT = "runtime-substrate-honest-baseline-docker-mode-example-smoke"

_RETAINED_MEASUREMENT_PROOF = {
    "client-example-cbor-decoding-amaru-d3a6dafc-regression": "20260920T235440Z-050046a4",
    "client-example-cbor-decoding-cardano-patched": "20260920T132629Z-ea000d37",
    "client-example-plutus-vm-amaru-onchain-v2": "20260921T204537Z-ff5a800a",
    "client-example-plutus-vm-cardano": "20260921T202611Z-27eeadb5",
    "client-example-invalid-mini-protocol-amaru": "20260920T072858Z-2cc3bb0c",
    "client-example-invalid-mini-protocol-cardano": "20260920T073447Z-ab81bfb7",
    "client-example-block-application-amaru-canonical-v3": "20260921T035546Z-9747122c",
    "client-example-block-application-cardano-canonical-v2": "20260921T021935Z-3b58eafc",
    "client-example-restart-recovery-sync-amaru": "20260921T045619Z-15e864a0",
    "client-example-restart-recovery-sync-cardano": "20260921T045807Z-8e2bbb0e",
    "client-example-simple-transfer-amaru": "20260921T194749Z-26542a57",
    "client-example-simple-transfer-cardano": "20260921T195334Z-e713d0de",
}
_RECOMMENDED_DEMOS = {
    "client-example-block-application-amaru-canonical-v3",
    "client-example-cbor-decoding-cardano-patched",
}
_SHORT_TITLES = {
    "client-example-cbor-decoding-amaru-d3a6dafc-regression": "CBOR decoding fix regression",
    "client-example-cbor-decoding-cardano-patched": "CBOR decoding conformance",
    "client-example-plutus-vm-amaru-onchain-v2": "Live Plutus V2 conformance",
    "client-example-plutus-vm-cardano": "Plutus VM conformance",
    "client-example-invalid-mini-protocol-amaru": "Invalid Handshake containment",
    "client-example-invalid-mini-protocol-cardano": "Invalid Handshake containment",
    "client-example-block-application-amaru-canonical-v3": "Canonical block progress",
    "client-example-block-application-cardano-canonical-v2": "Canonical block progress",
    "client-example-restart-recovery-sync-amaru": "Restart recovery and synchronization",
    "client-example-restart-recovery-sync-cardano": "Restart recovery and synchronization",
    "client-example-simple-transfer-amaru": "Simple transfer measurement",
    "client-example-simple-transfer-cardano": "Simple transfer measurement",
}


def _short_title(row: dict[str, Any]) -> str:
    if row["id"] in _SHORT_TITLES:
        return _SHORT_TITLES[row["id"]]
    title = str(row.get("title") or row["id"])
    if " — " in title and title.casefold().startswith("client example"):
        title = title.split(" — ", 1)[1]
    return title


def _scenario_presentation(
    row: dict[str, Any], profile_shapes: dict[str, tuple[int, int]], measurement_ids: set[str]
) -> dict[str, Any]:
    profile_id = row.get("profile")
    cardano_count, amaru_count = profile_shapes.get(profile_id, (0, 0))
    if cardano_count and amaru_count:
        implementation_key, implementation, filter_group = "mixed", "Mixed", "mixed"
    elif row.get("runtime") != "devnet" and not profile_id:
        implementation_key, implementation, filter_group = "other", "Framework / Other", "other"
    elif row.get("target_impl") == "amaru":
        implementation_key, implementation, filter_group = "amaru", "Amaru", "amaru"
    else:
        implementation_key, implementation, filter_group = "cardano-node", "Cardano-node", "cardano-node"
    measurement_profile = row.get("measurement_profile")
    if not measurement_profile or measurement_profile == "none":
        measurement_state = "no-measurement-profile"
    elif measurement_profile in measurement_ids:
        measurement_state = "measurement-enabled"
    else:
        measurement_state = "measurement-compatibility-unknown"
    retained_run_id = _RETAINED_MEASUREMENT_PROOF.get(row["id"])
    if retained_run_id:
        proof_state = "retained-proof"
    elif measurement_state == "measurement-enabled" and profile_id in profile_shapes:
        proof_state = "supported-without-retained-proof"
    else:
        proof_state = "unconfirmed-unsupported"
    limitation = (
        "This retained exact-target run is not an automatic Amaru-versus-Cardano benchmark."
        if retained_run_id
        else "No retained non-vacuous measurement proof is documented for this exact scenario."
    )
    return {
        **row,
        "short_title": _short_title(row),
        "implementation": implementation,
        "implementation_key": implementation_key,
        "filter_group": filter_group,
        "measurement_state": measurement_state,
        "proof_state": proof_state,
        "recommended_demo": row["id"] in _RECOMMENDED_DEMOS,
        "deployment_profile": profile_id,
        "purpose": str(row.get("title") or row["id"]),
        "limitation": limitation,
        "retained_run_id": retained_run_id,
        "evidence_url": f"/operate/runs/{retained_run_id}" if retained_run_id else None,
    }


def run_wizard_catalog() -> dict[str, Any]:
    raw_scenarios = _list_scenarios_for_compare()
    loaded_profiles = load_profiles()
    profile_shapes = {
        profile.id: (profile.node_count, profile.amaru_node_count)
        for profile in loaded_profiles
    }
    measurement_records = list_definitions("measurement-profiles")
    measurement_ids = {record.definition_id for record in measurement_records}
    scenarios = [
        _scenario_presentation(row, profile_shapes, measurement_ids)
        for row in raw_scenarios
    ]
    scenario_ids = {row["id"] for row in scenarios}
    requested_default = os.environ.get("ADA2_DWARF_RUN_DEFAULT_SCENARIO", "").strip()
    default_scenario_id = (
        requested_default
        if requested_default in scenario_ids
        else _PREFERRED_DEFAULT
        if _PREFERRED_DEFAULT in scenario_ids
        else (scenarios[0]["id"] if scenarios else None)
    )
    profiles = []
    for profile in loaded_profiles:
        try:
            qualification = profile_qualification(
                profile_deployment_version_preview(profile.id)
            )
        except Exception:
            qualification = profile_qualification(None)
        profiles.append({
            "id": profile.id,
            "label": profile.label,
            "haskell_count": profile.node_count,
            "amaru_count": profile.amaru_node_count,
            "version_policy": profile.version_policy,
            "measurement_target_mode": profile.measurement_target_mode,
            **qualification,
        })
    measurement_profiles = [
        {
            "id": record.definition_id,
            "title": record.data.get("title")
            or record.data.get("label")
            or record.definition_id,
            "implementation": record.data.get("implementation"),
            "measurement_count": len(record.data.get("measurements") or []),
        }
        for record in measurement_records
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
        "default_plan_digest": digest_run_plan(default_plan) if default_plan else None,
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
    response = {"ok": True, "plan": plan, "plan_digest": digest_run_plan(plan)}
    return (200, "application/json; charset=utf-8", json.dumps(response).encode("utf-8"))
