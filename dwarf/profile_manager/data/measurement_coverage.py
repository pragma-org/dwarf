"""Render-time join for measurement applicability and retained evidence."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from profile_manager.data.catalog_definitions import list_definitions
from profile_manager.data.coverage import _scenario_category
from profile_manager.views.threat_coverage import current_threat_coverage_data


DWARF_ROOT = Path(__file__).resolve().parents[2]
MAPPING_PATH = DWARF_ROOT / "measurement-coverage" / "v1.yaml"
SCHEMA_PATH = DWARF_ROOT / "spec" / "v1" / "measurement-coverage-map.schema.json"
STATUSES = ("Applicable", "Configured", "Exercised", "Verified", "Unavailable", "Reserved")
SOURCES = ("Stock", "External", "Patched", "Reserved")
STATE_DEFINITIONS = {
    "Applicable": "The tap matches this implementation and technical boundary. This is not evidence that it ran.",
    "Configured": "A current measurement profile selects the tap. Configuration is not evidence that the workload exercised it.",
    "Exercised": "Retained, correlated, non-vacuous records show that the workload reached the tap. Availability and zero values alone do not qualify.",
    "Verified": "Accepted retained evidence satisfies the named workload prerequisite and evidence contract for this tap.",
    "Unavailable": "The current catalog has no mapped scenario or the required evidence boundary is absent. DWARF does not substitute zero.",
    "Reserved": "The catalog keeps this future source slot, but it is not implemented evidence.",
}


def _load_mapping() -> dict[str, Any]:
    mapping = yaml.safe_load(MAPPING_PATH.read_text(encoding="utf-8"))
    schema = yaml.safe_load(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(mapping, schema)
    return mapping


def _source(definition: dict[str, Any]) -> str:
    mode = str(definition.get("collection_mode") or "")
    description = str(definition.get("description") or "").lower()
    if mode == "stock-telemetry":
        return "Stock"
    if mode == "external":
        return "External"
    if mode == "patched-node" and "reserved" not in description:
        return "Patched"
    return "Reserved"


def _implementation(value: str | None) -> set[str]:
    lowered = str(value or "").lower()
    found = set()
    if "amaru" in lowered:
        found.add("Amaru")
    if "cardano" in lowered:
        found.add("Cardano-node")
    if lowered in {"both", "mixed"}:
        found.update(("Amaru", "Cardano-node"))
    return found


def _scenario_link(row: dict[str, Any]) -> dict[str, str]:
    return {
        "id": row["id"],
        "title": row.get("title") or row["id"],
        "url": f"/operate/scenarios/{row['id']}",
    }


def _evidence_index(mapping: dict[str, Any], cards: list[dict[str, Any]]) -> dict[tuple[str, str], dict]:
    cards_by_id = {card["id"]: card for card in cards}
    index = {}
    for rule in mapping["evidence_rules"]:
        card = cards_by_id[rule["card_id"]]
        leg = next(
            item for item in card["legs"]
            if item["implementation"] == rule["implementation"]
        )
        for measurement_id in rule["measurements"]:
            index[(leg["scenario_id"], measurement_id)] = {
                "card_id": card["id"],
                "card_title": card["title"],
                "implementation": leg["implementation"],
                "scenario_id": leg["scenario_id"],
                "run_id": leg["run_id"],
                "url": leg["run_url"] or leg["evidence_url"],
                "verified": bool(leg["verified"] and card["state"] == "accepted"),
            }
    return index


def _row(
    *,
    identity: str,
    title: str,
    description: str,
    scenarios: list[dict[str, Any]],
    measurement_ids: list[str],
    rule: dict[str, Any],
    measurement_defs: dict[str, dict[str, Any]],
    configured: set[str],
    evidence_index: dict[tuple[str, str], dict],
    force_unavailable: bool = False,
) -> dict[str, Any]:
    scenario_ids = {item["id"] for item in scenarios}
    evidence = []
    seen_evidence = set()
    tap_rows = []
    for measurement_id in measurement_ids:
        definition = measurement_defs[measurement_id]
        source = _source(definition)
        tap_evidence = [
            item for (scenario_id, tap_id), item in evidence_index.items()
            if scenario_id in scenario_ids and tap_id == measurement_id
        ]
        for item in tap_evidence:
            key = (item["run_id"], measurement_id)
            if key not in seen_evidence:
                evidence.append({**item, "measurement_id": measurement_id})
                seen_evidence.add(key)
        if source == "Reserved":
            status = "Reserved"
        elif any(item["verified"] for item in tap_evidence):
            status = "Verified"
        elif tap_evidence:
            status = "Exercised"
        elif measurement_id in configured:
            status = "Configured"
        else:
            status = "Applicable"
        tap_rows.append({
            "id": measurement_id,
            "title": definition.get("title") or measurement_id,
            "source": source,
            "status": status,
            "url": f"/operate/measurements/{measurement_id}",
        })

    implementations = set()
    for scenario in scenarios:
        implementations.update(_implementation(scenario.get("target")))
    for measurement_id in measurement_ids:
        implementations.update(_implementation(
            (measurement_defs[measurement_id].get("compatibility") or {}).get("implementation")
        ))
    tap_statuses = {tap["status"] for tap in tap_rows}
    status = next(
        (candidate for candidate in ("Verified", "Exercised", "Configured", "Applicable") if candidate in tap_statuses),
        "Reserved",
    )
    if force_unavailable or not scenarios:
        status = "Unavailable"
        evidence = []
    families = sorted({_scenario_category(item["id"])[1] for item in scenarios})
    linked_scenarios = [_scenario_link(item) for item in scenarios]
    search_text = " ".join([
        identity, title, description, rule["prerequisite"], rule["can_prove"], rule["non_claims"],
        *families, *implementations,
        *(item["id"] for item in linked_scenarios),
        *(item["title"] for item in linked_scenarios),
        *(tap["id"] for tap in tap_rows),
        *(tap["title"] for tap in tap_rows),
    ]).lower()
    return {
        "id": identity,
        "title": title,
        "description": description,
        "scenario_families": families,
        "scenarios": linked_scenarios,
        "scenario_count": len(scenarios),
        "implementations": sorted(implementations) or ["Amaru", "Cardano-node"],
        "measurements": tap_rows,
        "sources": sorted({tap["source"] for tap in tap_rows}),
        "prerequisite": rule["prerequisite"],
        "status": status,
        "evidence": sorted(evidence, key=lambda item: (item["card_id"], item["implementation"], item["measurement_id"])),
        "can_prove": rule["can_prove"],
        "non_claims": rule["non_claims"],
        "search_text": search_text,
    }


def measurement_coverage_payload() -> dict[str, Any]:
    """Join current catalogs, mappings, and accepted evidence without caching."""
    mapping = _load_mapping()
    threat_data = current_threat_coverage_data()
    scenarios = threat_data["scenarios"]
    scenario_by_id = {item["id"]: item for item in scenarios}
    measurement_defs = {
        record.definition_id: record.data for record in list_definitions("measurements")
    }
    configured = {
        selection["id"]
        for profile in list_definitions("measurement-profiles")
        for selection in profile.data.get("measurements") or []
        if selection.get("enabled")
    }
    evidence_index = _evidence_index(mapping, threat_data["five_card_evidence"])
    surface_rules = {item["id"]: item for item in mapping["surface_rules"]}
    family_rules = {item["id"]: item for item in mapping["family_rules"]}

    def rules_for_surfaces(surface_ids: set[str]) -> tuple[list[str], dict[str, str]]:
        selected = [surface_rules[item] for item in sorted(surface_ids) if item in surface_rules]
        if not selected:
            selected = [surface_rules["other"]]
        measurement_ids = sorted({mid for rule in selected for mid in rule["measurements"]})
        rule = {
            key: " ".join(dict.fromkeys(item[key] for item in selected))
            for key in ("prerequisite", "can_prove", "non_claims")
        }
        return measurement_ids, rule

    views: dict[str, list[dict[str, Any]]] = {"threats": [], "risks": []}
    for section in ("threats", "risks"):
        for source_row in threat_data[section]:
            row_scenarios = source_row.get("scenarios") or []
            row_surfaces = set(source_row.get("surfaces") or [])
            for scenario in row_scenarios:
                row_surfaces.update(scenario.get("surfaces") or [])
            measurement_ids, rule = rules_for_surfaces(row_surfaces or {"other"})
            views[section].append(_row(
                identity=source_row["id"],
                title=source_row.get("title") or source_row.get("vector") or source_row["id"],
                description=source_row.get("desc") or source_row.get("why") or source_row.get("surface") or "Current mapped concern.",
                scenarios=row_scenarios,
                measurement_ids=measurement_ids,
                rule=rule,
                measurement_defs=measurement_defs,
                configured=configured,
                evidence_index=evidence_index,
                force_unavailable=source_row["id"] in {"TM-030", "TM-031", "RR-027"},
            ))

    family_scenarios: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for scenario in scenarios:
        family_scenarios[_scenario_category(scenario["id"])[0]].append(scenario)
    views["scenario_families"] = [
        _row(
            identity=rule["id"], title=rule["title"], description=rule["description"],
            scenarios=family_scenarios.get(rule["id"], []), measurement_ids=rule["measurements"],
            rule=rule, measurement_defs=measurement_defs, configured=configured,
            evidence_index=evidence_index,
        )
        for rule in mapping["family_rules"]
    ]

    views["surfaces"] = []
    for rule in mapping["surface_rules"]:
        row_scenarios = [item for item in scenarios if rule["id"] in (item.get("surfaces") or [])]
        views["surfaces"].append(_row(
            identity=rule["id"], title=rule["title"], description=rule["description"],
            scenarios=row_scenarios, measurement_ids=rule["measurements"], rule=rule,
            measurement_defs=measurement_defs, configured=configured, evidence_index=evidence_index,
        ))

    return {
        "mapping_id": mapping["id"],
        "catalog_scenario_count": len(scenario_by_id),
        "views": views,
        "statuses": list(STATUSES),
        "sources": list(SOURCES),
        "state_definitions": STATE_DEFINITIONS,
        "known_gaps": ["TM-030", "TM-031", "RR-027"],
    }
