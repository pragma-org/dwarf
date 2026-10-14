"""Catalog-backed data and read-only resolution for the local run wizard."""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Any
from urllib.parse import urlsplit

from profile_manager.data.catalog_definitions import list_definitions
from profile_manager.data.scenario_discovery import (
    RECOMMENDED_SCENARIO_IDS,
    RETAINED_SCENARIO_EVIDENCE,
    group_scenario_subjects,
    group_scenario_families,
    scenario_discovery,
    scenario_discovery_facets,
)

from profile_manager.data.scenarios import _list_scenarios_for_compare
from profile_manager.deployment_versions import (
    DeploymentVersionGateError,
    operator_registration_capability_context_from_observation,
    operator_registration_release,
    profile_deployment_version_preview,
)
from profile_manager.profiles import load_profiles
from profile_manager.run_presentation import profile_qualification
from profile_manager.run_plan import (
    RunPlanError,
    RunPlanRequest,
    digest_run_plan,
    resolve_run_plan,
)
from profile_manager.version_catalog import load_version_catalog
from profile_manager.version_discovery import (
    load_operator_registration,
    load_operator_registrations,
)


_PREFERRED_DEFAULT = "amaru-measurement-e2e-stock"
_OPTIONAL_GOLDEN_QUICKSTART = "cardano-measurement-e2e-stock"
_BROWSER_SCENARIO_FIELDS = (
    "id",
    "selection_id",
    "short_title",
    "friendly_title",
    "search_text",
    "implementation_key",
    "implementation",
    "measurement_state",
    "measurement_profile",
    "deployment_profile",
    "target_version",
    "proof_state",
    "purpose",
    "limitation",
    "evidence_url",
    "retained_run_id",
    "context_links",
    "subject_key",
    "subject_child_key",
    "subject_path",
    "threat_model_ids",
    "risk_register_ids",
)

_PROFILE_PICKER_PURPOSES = {
    "block-apply-adversary": "Block application — forged-block bridge",
    "epoch-boundary-pot-differential": "Epoch-boundary pots — differential",
    "invalid-tx-body-leak": "Invalid transaction body — leak detection",
    "profile-a-haskell-peersharing-disabled": "Peer sharing off — Cardano baseline",
    "profile-b-haskell-peersharing-enabled": "Peer sharing on — Cardano baseline",
    "profile-c-mixed-haskell-amaru-minimal": "Minimal mixed runtime",
    "profile-d-amaru-preview-proof": "Preview proof of life — Amaru",
    "profile-e-haskell-preview-proof": "Preview proof of life — Cardano",
    "profile-f-amaru-preview2-proof": "Preview 2 proof of life — Amaru",
    "profile-g-haskell-preview2-proof": "Preview 2 proof of life — Cardano",
    "profile-h-generated-mixed-haskell2-amaru1": "Generated mixed targets — 2 Cardano + 1 Amaru",
    "profile-i-generated-haskell3": "Generated Cardano targets — 3",
    "profile-j-haskell-preprod-proof": "Pre-production proof of life — Cardano",
    "profile-k-amaru-preprod-proof": "Pre-production proof of life — Amaru",
    "profile-l-amaru-closed-devnet": "Closed devnet — Amaru",
    "profile-m-consensus-threshold": "Consensus threshold — 3 honest + 1 adversary",
    "profile-n-cardano-latest-confirmed": "Latest confirmed Cardano devnet",
    "profile-o-amaru-target-latest-confirmed": "Latest confirmed Amaru devnet",
    "profile-opcert-aged-kes-cardano-1112": "Operational certificate — aged / short KES",
    "profile-p-mixed-latest-confirmed": "Latest confirmed mixed pair",
    "profile-q-amaru-measurement-patched": "Patched measurements — Amaru target",
    "profile-r-amaru-measurement-stock-control": "Stock measurements — Amaru control",
    "profile-s-cardano-measurement-stock-control": "Stock measurements — Cardano control",
    "profile-t-cardano-measurement-patched": "Patched measurements — Cardano target",
    "profile-u-amaru-measurement-nanoseconds-v2": "Nanosecond v2 measurements — Amaru 20260912",
    "profile-v-cardano-measurement-nanoseconds-v2": "Nanosecond v2 measurements — Cardano 11.1.2",
    "profile-w-amaru-measurement-plutus-v2": "Plutus V2 · nanosecond v2 — Amaru 20260912",
    "profile-x-amaru-cbor-fix-regression-nanoseconds-v2": "CBOR fix regression · nanosecond v2 — Amaru d3a6dafc",
    "profile-y-amaru-block-application-nanoseconds-v3": "Block application · nanosecond v3 — Amaru 20260912",
    "profile-z-amaru-20260918-nanoseconds-v3": "Nanosecond v3 measurements — Amaru 20260918",
    "profile-za-amaru-20260918-plutus-v2": "Plutus V2 · nanosecond v3 — Amaru 20260918",
    "profile-zb-mixed-1112-amaru-20260918-nanoseconds-v3": "Nanosecond v3 measurements — mixed 11.1.2 / 20260918",
    "profile-zc-mixed-1112-amaru-20260918-v16-repro": "Node-to-node v16 handshake — experimental mixed pair",
}
_PROFILE_PICKER_GROUP_ORDER = (
    "Amaru test target",
    "Cardano test target",
    "Mixed test targets",
    "Other profiles",
)


def profile_picker_presentation(profile: Any) -> dict[str, str]:
    """Return UI-only picker metadata without changing deployment identity."""
    picker_label = _PROFILE_PICKER_PURPOSES.get(profile.id)
    if picker_label is None:
        return {"picker_group": "Other profiles", "picker_label": profile.label}
    if profile.node_count and profile.amaru_node_count:
        picker_group = "Mixed test targets"
    elif profile.amaru_node_count:
        picker_group = "Amaru test target"
    else:
        picker_group = "Cardano test target"
    return {"picker_group": picker_group, "picker_label": picker_label}


def run_wizard_browser_bootstrap(wizard: dict) -> dict:
    """Return browser state without duplicating server-rendered card metadata."""
    return {
        **wizard,
        "scenarios": [
            {key: row.get(key) for key in _BROWSER_SCENARIO_FIELDS}
            for row in wizard.get("scenarios") or []
        ],
        "scenario_subjects": [
            {
                "key": subject["key"],
                "title": subject["title"],
                "variant_count": subject["variant_count"],
                "children": [
                    {
                        "key": child["key"],
                        "title": child["title"],
                        "variant_count": child["variant_count"],
                        "families": [
                            {
                                "key": family["key"],
                                "title": family["title"],
                                "variant_ids": [row["id"] for row in family["variants"]],
                            }
                            for family in child["families"]
                        ],
                    }
                    for child in subject["children"]
                ],
            }
            for subject in wizard.get("scenario_subjects") or []
        ],
    }

def _scenario_presentation(
    row: dict[str, Any], profile_shapes: dict[str, tuple[int, int]], measurement_ids: set[str],
    threat_risk: dict[str, Any] | None = None,
) -> dict[str, Any]:
    discovery = scenario_discovery(
        row,
        profile_shapes=profile_shapes,
        recommended_ids=RECOMMENDED_SCENARIO_IDS,
        retained_evidence=RETAINED_SCENARIO_EVIDENCE,
        threat_risk=threat_risk,
    )
    raw_profile_id = row.get("profile")
    profile_id = raw_profile_id if isinstance(raw_profile_id, str) else None
    measurement_profile = row.get("measurement_profile")
    if not measurement_profile or measurement_profile == "none":
        measurement_state = "no-measurement-profile"
    elif measurement_profile in measurement_ids:
        measurement_state = "measurement-enabled"
    else:
        measurement_state = "measurement-compatibility-unknown"
    retained_run_id = RETAINED_SCENARIO_EVIDENCE.get(row["id"])
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
        **discovery,
        "short_title": discovery["friendly_title"],
        "filter_group": discovery["implementation_key"],
        "measurement_state": measurement_state,
        "proof_state": proof_state,
        "recommended_demo": row["id"] in RECOMMENDED_SCENARIO_IDS,
        "deployment_profile": profile_id,
        "purpose": discovery["purpose"],
        "limitation": limitation,
        "retained_run_id": retained_run_id,
        "evidence_url": f"/operate/runs/{retained_run_id}" if retained_run_id else None,
    }


def run_wizard_catalog(
    *,
    capability_context: dict[str, Any] | None = None,
    requested_scenario_id: str | None = None,
    threat_risk_index: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw_scenarios = _list_scenarios_for_compare()
    loaded_profiles = load_profiles()
    profile_shapes = {
        profile.id: (profile.node_count, profile.amaru_node_count)
        for profile in loaded_profiles
    }
    measurement_records = list_definitions("measurement-profiles")
    measurement_definitions = {
        record.definition_id: record.data
        for record in list_definitions("measurements")
    }
    measurement_ids = {record.definition_id for record in measurement_records}
    scenarios = [
        _scenario_presentation(
            row,
            profile_shapes,
            measurement_ids,
            (threat_risk_index or {}).get("by_scenario", {}).get(row["id"]),
        )
        for row in raw_scenarios
    ]
    scenario_ids = {row["id"] for row in scenarios}
    quickstart_plan = None
    if _OPTIONAL_GOLDEN_QUICKSTART in scenario_ids:
        try:
            quickstart_plan = resolve_run_plan(
                RunPlanRequest(scenario_id=_OPTIONAL_GOLDEN_QUICKSTART),
                capability_context=capability_context,
            )
        except RunPlanError:
            quickstart_plan = None
    quickstart_recommended = bool(
        quickstart_plan
        and quickstart_plan["availability"]["state"] in {"ready", "pullable"}
        and quickstart_plan["measurements"]["resolved"]
        and not quickstart_plan["measurements"]["incompatible"]
    )
    scenarios = [
        {
            **row,
            "recommended": bool(
                quickstart_recommended and row["id"] == _OPTIONAL_GOLDEN_QUICKSTART
            ),
            "quickstart_recommended": bool(
                quickstart_recommended and row["id"] == _OPTIONAL_GOLDEN_QUICKSTART
            ),
        }
        for row in scenarios
    ]
    scenario_subjects = group_scenario_subjects(scenarios)
    scenario_families = [
        {
            "key": family["key"],
            "title": family["title"],
            "variant_ids": [variant["id"] for variant in family["variants"]],
            "variant_count": len(family["variants"]),
            "security_goals": family["security_goals"],
            "implementations": family["implementations"],
            "execution_types": family["execution_types"],
            "maintenance_hidden": family["maintenance_hidden"],
        }
        for family in group_scenario_families(scenarios)
    ]
    scenario_facets = scenario_discovery_facets(scenarios)
    if threat_risk_index:
        scenario_facets.update(threat_risk_index["facets"])
    configured_default = os.environ.get("ADA2_DWARF_RUN_DEFAULT_SCENARIO", "").strip()
    requested_default = (
        requested_scenario_id
        if requested_scenario_id in scenario_ids
        else configured_default
    )
    default_scenario_id = (
        requested_default
        if requested_default in scenario_ids
        else _PREFERRED_DEFAULT
        if _PREFERRED_DEFAULT in scenario_ids
        else None
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
            **profile_picker_presentation(profile),
            **qualification,
        })
    profile_groups = [
        {
            "label": label,
            "profiles": [row for row in profiles if row["picker_group"] == label],
        }
        for label in _PROFILE_PICKER_GROUP_ORDER
        if any(row["picker_group"] == label for row in profiles)
    ]
    measurement_profiles = [
        {
            "id": record.definition_id,
            "title": record.data.get("title")
            or record.data.get("label")
            or record.definition_id,
            "description": record.data.get("description") or "",
            "implementation": record.data.get("implementation"),
            "measurement_count": len(record.data.get("measurements") or []),
            "taps": [
                {
                    "id": selection["id"],
                    "enabled": selection.get("enabled") is True,
                    "title": (
                        measurement_definitions.get(selection["id"], {}).get("title")
                        or selection["id"]
                    ),
                    "description": measurement_definitions.get(
                        selection["id"], {}
                    ).get("description") or "",
                    "implementation": (
                        measurement_definitions.get(selection["id"], {})
                        .get("compatibility", {})
                        .get("implementation")
                    ),
                }
                for selection in record.data.get("measurements") or []
            ],
        }
        for record in measurement_records
    ]
    version_catalog = load_version_catalog()
    versions = []
    for release in version_catalog["releases"]:
        artifact = next(
            (
                item for item in release.get("artifacts") or []
                if item.get("kind") in {"oci", "oci-derived"}
            ),
            {},
        )
        platforms = artifact.get("platforms") or []
        versions.append({
            "implementation": release["implementation"],
            "version": release["version"],
            "channel": release["channel"],
            "source_revision": release["source_revision"],
            "artifact_state": artifact.get("availability") or "unknown",
            "artifact_digest": artifact.get("digest"),
            "platforms": platforms,
            "downloadable": bool(
                artifact.get("availability") == "available"
                and artifact.get("public") is True
                and artifact.get("digest")
                and platforms
            ),
            "support": {
                scope: (release.get("verification") or {}).get(scope, {}).get(
                    "status", "unsupported"
                )
                for scope in ("cardano-only", "amaru-only", "mixed")
            },
        })
    default_plan = None
    default_error = None
    if default_scenario_id:
        try:
            default_plan = (
                quickstart_plan
                if default_scenario_id == _OPTIONAL_GOLDEN_QUICKSTART and quickstart_plan
                else resolve_run_plan(
                    RunPlanRequest(scenario_id=default_scenario_id),
                    capability_context=capability_context,
                )
            )
        except RunPlanError as exc:
            default_error = exc.as_dict()
    operator_registration_error = None
    try:
        operator_overlay = load_operator_registrations()
        operator_registrations = [
            {
                "registration_id": row["registration_id"],
                "implementation": row["implementation"],
                "version": row["version"],
                "config_image_id": row["config_image_id"],
                "manifest_digest": row.get("manifest_digest"),
                "platform": row["platform"],
                "source_declared": bool(
                    (row.get("provenance") or {}).get("source_revision")
                ),
                "qualification": (row.get("provenance") or {}).get(
                    "qualification", "unverified"
                ),
            }
            for row in operator_overlay["registrations"]
        ]
        operator_overlay_revision = operator_overlay["revision"]
    except (OSError, ValueError, RuntimeError) as exc:
        operator_registrations = []
        operator_overlay_revision = None
        operator_registration_error = str(exc)
    return {
        "default_scenario_id": default_scenario_id,
        "scenarios": scenarios,
        "scenario_subjects": scenario_subjects,
        "scenario_families": scenario_families,
        "scenario_facets": scenario_facets,
        "scenario_counts": {
            "variants": len(scenarios),
            "families": sum(
                len(child["families"])
                for subject in scenario_subjects
                for child in subject["children"]
            ),
            "hidden_health": sum(
                row["maintenance"]["state"] == "hidden-by-default"
                for row in scenarios
            ),
        },
        "profiles": profiles,
        "profile_groups": profile_groups,
        "measurement_profiles": measurement_profiles,
        "versions": versions,
        "version_catalog_revision": version_catalog.get("updated_at"),
        "version_policies": ["latest-confirmed", "latest-stable", "exact"],
        "operator_registrations": operator_registrations,
        "operator_overlay_revision": operator_overlay_revision,
        "operator_registration_error": operator_registration_error,
        "default_plan": default_plan,
        "default_plan_digest": digest_run_plan(default_plan) if default_plan else None,
        "default_error": default_error,
        "quickstart": (
            {
                "scenario_id": _OPTIONAL_GOLDEN_QUICKSTART,
                "availability": quickstart_plan["availability"],
                "measurement_profile": (
                    (quickstart_plan["measurements"].get("profile") or {}).get("id")
                ),
                "measurement_count": len(quickstart_plan["measurements"]["resolved"]),
                "recommended": quickstart_recommended,
            }
            if quickstart_plan
            else None
        ),
    }


def operator_registration_capability_context(
    request: RunPlanRequest, *, request_controller=None
) -> dict[str, Any] | None:
    registration_id = request.operator_registration_id
    if registration_id is None:
        return None
    try:
        selected = load_operator_registration(registration_id)
        registration = selected["registration"]
        operator_registration_release(registration)
    except (KeyError, TypeError, ValueError) as exc:
        raise RunPlanError(str(exc), field="versions") from exc
    from profile_manager.topology_actions import TopologyActionError, controller_request

    inspect = request_controller or controller_request
    parameters = {
        "implementation": registration["implementation"],
        "version": registration["version"],
        "image": registration["image_reference"],
    }
    source_revision = (registration.get("provenance") or {}).get(
        "source_revision"
    )
    if source_revision is not None:
        parameters["declared_source_revision"] = source_revision
    try:
        observed = inspect("image.inspect", parameters, 20)
    except TopologyActionError as exc:
        raise RunPlanError(
            "The selected local image could not be inspected by the controller.",
            field="availability",
        ) from exc
    try:
        return operator_registration_capability_context_from_observation(
            registration, observed
        )
    except DeploymentVersionGateError as exc:
        raise RunPlanError(
            str(exc),
            field="availability",
        ) from exc


def dispatch_run_resolve_request(
    *, method: str, path: str, body: bytes, request_controller=None
):
    if urlsplit(path).path != "/api/run/resolve":
        return None
    if method != "POST":
        return (405, "application/json; charset=utf-8", b'{"ok":false,"error":{"message":"use POST"}}')
    if len(body) > 4096:
        return (413, "application/json; charset=utf-8", b'{"ok":false,"error":{"field":"request","message":"request body is too large"}}')
    try:
        value = json.loads(body.decode("utf-8"))
        request = RunPlanRequest.from_mapping(value)
        capability_context = operator_registration_capability_context(
            request, request_controller=request_controller
        )
        plan = resolve_run_plan(request, capability_context=capability_context)
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
