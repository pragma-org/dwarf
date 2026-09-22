"""Render-time join for measurement applicability and retained evidence."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import re
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
CLIENT_READINESS_DEFINITIONS = {
    "Ready": "Compatible real-node scenario, applicable implemented taps, non-vacuous retained evidence, and a documented /run recipe.",
    "Almost ready": "The scenario and taps exist, but one bounded integration, evidence, or recipe step is missing.",
    "Partial": "Only part of the requested boundary is observable, or only indirect or proxy evidence exists.",
    "Not implemented": "No honest compatible measurement path exists.",
}
TAP_GROUPS = (
    ("workload-outcomes", "Workload and outcomes"),
    ("latency-processing", "Latency and processing"),
    ("throughput-chain", "Throughput and chain"),
    ("resources", "Resources"),
    ("protocol-ledger", "Protocol and ledger"),
    ("unavailable-reserved", "Unavailable and reserved"),
)


def _first_sentence(value: str) -> str:
    """Return a compact human-first summary without changing source text."""
    text = " ".join(str(value or "").split())
    match = re.match(r"(.+?[.!?])(?:\s|$)", text)
    return match.group(1) if match else text


def _tap_group(tap: dict[str, Any]) -> str:
    if tap["source"] == "Reserved" or tap["status"] in {"Unavailable", "Reserved"}:
        return "unavailable-reserved"
    text = f"{tap['id']} {tap['title']}".lower()
    if any(term in text for term in ("cpu", "memory", "rss", "resource", "disk", "i/o", "network")):
        return "resources"
    if any(term in text for term in ("latency", "duration", "elapsed", "processing", "decode", "encode")):
        return "latency-processing"
    if any(term in text for term in ("throughput", "rate", "chain head", "chain-head", "block adoption", "slot")):
        return "throughput-chain"
    if any(term in text for term in ("protocol", "ledger", "plutus", "cbor", "fork", "rollback", "chain selection")):
        return "protocol-ledger"
    return "workload-outcomes"


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
    evidence_by_run: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    tap_rows = []
    for measurement_id in measurement_ids:
        definition = measurement_defs[measurement_id]
        source = _source(definition)
        tap_evidence = [
            item for (scenario_id, tap_id), item in evidence_index.items()
            if scenario_id in scenario_ids and tap_id == measurement_id
        ]
        for item in tap_evidence:
            key = (item["run_id"], item["scenario_id"], item["implementation"], item["url"])
            grouped = evidence_by_run.setdefault(key, {**item, "measurements": []})
            grouped["measurements"].append({
                "id": measurement_id,
                "title": definition.get("title") or measurement_id,
            })
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

    evidence = []
    for item in evidence_by_run.values():
        item["measurements"] = sorted(item["measurements"], key=lambda value: value["title"])
        item["measurement_count"] = len(item["measurements"])
        evidence.append(item)

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
    tap_groups = []
    for group_id, group_title in TAP_GROUPS:
        grouped_taps = [tap for tap in tap_rows if _tap_group(tap) == group_id]
        if grouped_taps:
            tap_groups.append({"id": group_id, "title": group_title, "taps": grouped_taps})
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
        "meaning": _first_sentence(description),
        "scenario_families": families,
        "scenarios": linked_scenarios,
        "scenario_count": len(scenarios),
        "implementations": sorted(implementations) or ["Amaru", "Cardano-node"],
        "measurements": tap_rows,
        "tap_groups": tap_groups,
        "tap_summary": {
            "applicable": len(tap_rows),
            "verified": sum(tap["status"] == "Verified" for tap in tap_rows),
        },
        "sources": sorted({tap["source"] for tap in tap_rows}),
        "prerequisite": rule["prerequisite"],
        "status": status,
        "evidence": sorted(evidence, key=lambda item: (item["card_id"], item["implementation"], item["run_id"])),
        "can_prove": rule["can_prove"],
        "coverage_statement": _first_sentence(rule["can_prove"]),
        "non_claims": rule["non_claims"],
        "search_text": search_text,
    }


def measurement_coverage_payload() -> dict[str, Any]:
    """Join current catalogs, mappings, and accepted evidence without caching."""
    mapping = _load_mapping()
    threat_data = current_threat_coverage_data(include_measurements=False)
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
    evidence_index = _evidence_index(mapping, threat_data["client_card_evidence"])
    surface_rules = {item["id"]: item for item in mapping["surface_rules"]}
    family_rules = {item["id"]: item for item in mapping["family_rules"]}
    entry_rules = {item["id"]: item for item in mapping["entry_rules"]}

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
            measurement_ids = sorted(
                set(measurement_ids)
                | set((entry_rules.get(source_row["id"]) or {}).get("measurements") or [])
            )
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

    client_rows = []
    for rule in mapping["client_requirement_rules"]:
        implementation_rows = {}
        all_scenarios = []
        all_sources = set()
        all_statuses = []
        for implementation, config in rule["implementations"].items():
            selected_scenarios = [
                scenario_by_id[scenario_id]
                for scenario_id in config["scenarios"]
                if scenario_id in scenario_by_id
            ]
            linked_scenarios = [_scenario_link(item) for item in selected_scenarios]
            all_scenarios.extend(linked_scenarios)
            evidence_by_run = {}
            taps = []
            for measurement_id in config["measurements"]:
                definition = measurement_defs[measurement_id]
                source = _source(definition)
                all_sources.add(source)
                matching = [
                    item for (scenario_id, tap_id), item in evidence_index.items()
                    if scenario_id in config["scenarios"] and tap_id == measurement_id
                ]
                tap_status = "Verified" if any(item["verified"] for item in matching) else (
                    "Reserved" if source == "Reserved" else "Applicable"
                )
                taps.append({
                    "id": measurement_id,
                    "title": definition.get("title") or measurement_id,
                    "source": source,
                    "status": tap_status,
                    "url": f"/operate/measurements/{measurement_id}",
                })
                for item in matching:
                    if item["verified"]:
                        evidence_by_run[(item["run_id"], item["scenario_id"])] = item
            evidence = sorted(evidence_by_run.values(), key=lambda item: item["run_id"])
            implemented = any(tap["source"] != "Reserved" for tap in taps)
            verified = any(tap["status"] == "Verified" for tap in taps)
            if config["boundary"] == "none" or not selected_scenarios or not implemented:
                readiness = "Not implemented"
            elif config["boundary"] == "partial":
                readiness = "Partial"
            elif verified and config["recipe_documented"]:
                readiness = "Ready"
            else:
                readiness = "Almost ready"
            all_statuses.append(readiness)
            implementation_rows[implementation] = {
                "label": "Amaru" if implementation == "amaru" else "Cardano-node",
                "status": readiness,
                "measurements": taps,
                "scenarios": linked_scenarios,
                "scenario_count": len(linked_scenarios),
                "evidence": evidence,
                "recipe_documented": config["recipe_documented"],
                "limitation": config["limitation"],
                "missing_step": config["missing_step"],
            }
        status_rank = {"Ready": 3, "Almost ready": 2, "Partial": 1, "Not implemented": 0}
        overall = min(all_statuses, key=lambda value: status_rank[value])
        search_text = " ".join([
            rule["id"], rule["title"], rule["kind"], rule["description"], rule["interpretation"],
            *(scenario["id"] for scenario in all_scenarios),
            *(tap["id"] for impl in implementation_rows.values() for tap in impl["measurements"]),
        ]).lower()
        client_rows.append({
            "id": rule["id"], "title": rule["title"], "kind": rule["kind"],
            "description": rule["description"], "meaning": _first_sentence(rule["description"]),
            "source_url": rule["source_url"], "interpretation": rule["interpretation"],
            "implementations": implementation_rows, "implementation_names": ["Amaru", "Cardano-node"],
            "status": overall, "sources": sorted(all_sources), "search_text": search_text,
            "scenario_count": len({item["id"] for item in all_scenarios}),
            "measurement_count": len({
                tap["id"]
                for implementation in implementation_rows.values()
                for tap in implementation["measurements"]
            }),
            "verified_measurement_count": len({
                tap["id"]
                for implementation in implementation_rows.values()
                for tap in implementation["measurements"]
                if tap["status"] == "Verified"
            }),
        })
    views["client_requirements"] = client_rows

    labels = {
        "threats": "Threats",
        "risks": "Risks",
        "scenario_families": "Scenario families",
        "surfaces": "Node/protocol surfaces",
        "client_requirements": "Client requirements",
    }
    overview = []
    for view_id, label in labels.items():
        rows = views[view_id]
        if view_id == "client_requirements":
            mapped = sum(any(impl["scenario_count"] for impl in row["implementations"].values()) for row in rows)
            verified = sum(any(impl["status"] == "Ready" for impl in row["implementations"].values()) for row in rows)
            gaps = sum(any(impl["status"] == "Not implemented" for impl in row["implementations"].values()) for row in rows)
        else:
            mapped = sum(row["scenario_count"] > 0 for row in rows)
            verified = sum(row["status"] == "Verified" for row in rows)
            gaps = sum(row["status"] == "Unavailable" for row in rows)
        overview.append({
            "id": view_id,
            "label": label,
            "mapped": mapped,
            "verified": verified,
            "gaps": gaps,
        })

    return {
        "mapping_id": mapping["id"],
        "catalog_scenario_count": len(scenario_by_id),
        "views": views,
        "overview": overview,
        "statuses": list(STATUSES),
        "sources": list(SOURCES),
        "state_definitions": STATE_DEFINITIONS,
        "client_readiness_definitions": CLIENT_READINESS_DEFINITIONS,
        "known_gaps": ["TM-030", "TM-031", "RR-027"],
    }
