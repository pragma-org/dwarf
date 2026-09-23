"""Basic view — "Evidence Deck" data helpers.

Read-only joins over existing extractors; nothing is synthesized. The deck
vocabulary is fixed by what the data can support:

- holo card  : an entry whose measurement status is Verified by accepted,
               retained evidence (or a run whose hash chain verifies).
- card back  : mapped / configured only — a scenario or tap exists, which is
               NOT runtime proof.
- empty slot : a GAP — no scenario mapped, or the evidence boundary is absent.
"""
from __future__ import annotations

from typing import Any

SUIT = {"pass": "♦", "fail": "▲", "error": "✕"}


def deck_suit(status: str | None) -> str:
    return SUIT.get(status or "", "■")


def deck_runs(limit: int = 100) -> dict[str, Any]:
    """Recent-run outcome summary for the PAST card and the runs hand."""
    from profile_manager.data.operate_runs import operate_run_rows

    rows = operate_run_rows(limit=limit)
    counts = {"pass": 0, "fail": 0, "error": 0, "other": 0}
    for row in rows:
        status = row.get("exit_status") or ""
        counts[status if status in counts else "other"] += 1
    return {"rows": rows, "total": len(rows), "counts": counts}


def _entry(row: dict[str, Any], kind: str, gap_ids: set[str]) -> dict[str, Any]:
    taps = row.get("tap_summary") or {}
    status = row.get("status") or "Unavailable"
    scenario_count = int(row.get("scenario_count") or 0)
    is_gap = row["id"] in gap_ids or scenario_count == 0 or status == "Unavailable"
    if is_gap:
        tier = "gap"
    elif status == "Verified":
        tier = "verified"
    else:
        tier = "mapped"
    return {
        "id": row["id"],
        "kind": kind,
        "title": row.get("title") or row["id"],
        "description": row.get("description") or "",
        "status": status,
        "tier": tier,
        "applicable": int(taps.get("applicable") or 0),
        "verified": int(taps.get("verified") or 0),
        "scenario_count": scenario_count,
        "scenarios": [
            (s.get("id") if isinstance(s, dict) else s) for s in (row.get("scenarios") or [])
        ][:12],
        "implementations": row.get("implementations") or [],
        "can_prove": row.get("coverage_statement") or row.get("can_prove") or "",
        "non_claims": row.get("non_claims") or "",
        "prerequisite": row.get("prerequisite") or "",
    }


def deck_binder() -> dict[str, Any]:
    """Threat (TM) and risk (RR) binder: one slot per entry, tiered honestly."""
    from profile_manager.data.measurement_coverage import measurement_coverage_payload
    from profile_manager.views.threat_coverage import current_threat_coverage_data

    payload = measurement_coverage_payload()
    meta = (current_threat_coverage_data(include_measurements=False) or {}).get("meta") or {}
    gap_ids = set(meta.get("tm_gaps") or []) | set(meta.get("rr_gaps") or [])
    threats = [_entry(r, "TM", gap_ids) for r in payload["views"].get("threats") or []]
    risks = [_entry(r, "RR", gap_ids) for r in payload["views"].get("risks") or []]
    entries = threats + risks
    tiers = {"verified": 0, "mapped": 0, "gap": 0}
    for e in entries:
        tiers[e["tier"]] += 1
    return {
        "threats": threats,
        "risks": risks,
        "entries": entries,
        "tiers": tiers,
        "gaps": [e for e in entries if e["tier"] == "gap"],
        "tm_mapped": sum(1 for e in threats if e["tier"] != "gap"),
        "rr_mapped": sum(1 for e in risks if e["tier"] != "gap"),
        "overview": payload.get("overview") or [],
        "statuses": payload.get("statuses") or [],
        "state_definitions": payload.get("state_definitions") or {},
    }


def deck_gaps() -> list[dict[str, Any]]:
    """Cheap GAP list for the FUTURE card (no measurement join)."""
    from profile_manager.views.threat_coverage import current_threat_coverage_data

    data = current_threat_coverage_data(include_measurements=False) or {}
    meta = data.get("meta") or {}
    titles = {r["id"]: (r.get("vector") or r.get("title") or "") for r in (data.get("threats") or [])}
    titles.update({r["id"]: (r.get("title") or "") for r in (data.get("risks") or [])})
    ids = list(meta.get("tm_gaps") or []) + list(meta.get("rr_gaps") or [])
    return [{"id": i, "title": titles.get(i, "")} for i in ids]
