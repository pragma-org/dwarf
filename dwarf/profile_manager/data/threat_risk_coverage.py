"""Discovery adapter for the canonical Threat Model and Risk Register join."""
from __future__ import annotations

from typing import Iterable


def _readable_label(section: str, row: dict) -> str:
    if section == "threats":
        title = row.get("vector") or row.get("why") or row["id"]
    else:
        title = row.get("title") or row.get("desc") or row["id"]
    return f"{row['id']} — {str(title).strip()}"


def threat_risk_discovery_index(
    coverage: dict, *, scenario_ids: Iterable[str]
) -> dict:
    """Reverse the vetted coverage join without inventing new relationships."""
    exact_ids = set(scenario_ids)
    by_scenario = {
        scenario_id: {
            "threat_model_ids": [],
            "risk_register_ids": [],
            "threat_model_search": [],
            "risk_register_search": [],
            "coverage_surfaces": [],
        }
        for scenario_id in sorted(exact_ids)
    }
    for row in coverage.get("scenarios") or []:
        scenario_id = row.get("id")
        if scenario_id in exact_ids:
            by_scenario[scenario_id]["coverage_surfaces"] = sorted(
                {str(value) for value in row.get("surfaces") or [] if value}
            )
    facets = {"threat_model": [], "risk_register": []}
    for section, facet_key, ids_key, search_key in (
        ("threats", "threat_model", "threat_model_ids", "threat_model_search"),
        ("risks", "risk_register", "risk_register_ids", "risk_register_search"),
    ):
        mapped_ids: set[str] = set()
        for row in coverage.get(section) or []:
            label = _readable_label(section, row)
            scenario_row_ids = sorted(
                {
                    item.get("id")
                    for item in row.get("scenarios") or []
                    if item.get("id") in exact_ids
                }
            )
            facets[facet_key].append(
                {"value": row["id"], "label": label, "count": len(scenario_row_ids)}
            )
            for scenario_id in scenario_row_ids:
                mapped_ids.add(scenario_id)
                by_scenario[scenario_id][ids_key].append(row["id"])
                by_scenario[scenario_id][search_key].append(label)
        unmapped = exact_ids - mapped_ids
        facets[facet_key].append(
            {"value": "unmapped", "label": "Unmapped", "count": len(unmapped)}
        )
        for scenario_id in unmapped:
            by_scenario[scenario_id][ids_key] = ["unmapped"]
    return {"by_scenario": by_scenario, "facets": facets}
