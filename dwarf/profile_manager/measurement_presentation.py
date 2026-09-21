"""Presentation-only normalization for retained DWARF measurement reports.

The raw report remains the evidence authority.  This module assigns a shared
client vocabulary and display shape without changing any retained value.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


_CATALOG_PATH = (
    Path(__file__).resolve().parents[1]
    / "measurements"
    / "presentation-v1.json"
)
_SOURCE_VALUES = {"stock", "external", "patched", "reserved"}
_METRIC_TYPES = {"distribution", "scalar", "count", "unavailable"}


def load_measurement_presentation_catalog(path: Path | None = None) -> dict[str, Any]:
    """Load and minimally validate the versioned presentation vocabulary."""
    catalog_path = Path(path) if path is not None else _CATALOG_PATH
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    if catalog.get("schema_version") != "v1":
        raise ValueError("measurement presentation catalog must use schema_version v1")
    categories = catalog.get("categories")
    concepts = catalog.get("concepts")
    sources = catalog.get("source_labels")
    if not isinstance(categories, list) or not isinstance(concepts, list):
        raise ValueError("measurement presentation catalog requires categories and concepts")
    if set(sources or {}) != _SOURCE_VALUES:
        raise ValueError("measurement presentation catalog source labels are incomplete")
    category_ids = {item.get("id") for item in categories if isinstance(item, dict)}
    seen: set[str] = set()
    for concept in concepts:
        if not isinstance(concept, dict) or not concept.get("id"):
            raise ValueError("every measurement concept requires an id")
        if concept["id"] in seen:
            raise ValueError(f"duplicate measurement concept {concept['id']}")
        seen.add(concept["id"])
        if concept.get("category") not in category_ids:
            raise ValueError(f"unknown category for measurement concept {concept['id']}")
        if concept.get("metric_type") not in _METRIC_TYPES:
            raise ValueError(f"invalid metric type for measurement concept {concept['id']}")
        for binding in concept.get("bindings") or []:
            if binding.get("source") not in _SOURCE_VALUES:
                raise ValueError(f"invalid source for measurement concept {concept['id']}")
    return catalog


def _binding_for(concept: dict[str, Any], target: dict[str, Any]) -> dict[str, Any] | None:
    implementation = str(target.get("implementation") or "")
    mode = str(target.get("mode") or "")
    candidates = []
    for binding in concept.get("bindings") or []:
        if binding.get("implementation") not in {implementation, "*"}:
            continue
        modes = binding.get("modes") or []
        if modes and mode not in modes:
            continue
        candidates.append(binding)
    if not candidates:
        return None
    candidates.sort(key=lambda item: item.get("implementation") == "*", reverse=False)
    return candidates[0]


def _humanize(raw_id: str) -> str:
    return raw_id.replace(".", " ").replace("_", " ").replace("-", " ").strip().capitalize()


def _metric_type(raw: dict[str, Any]) -> str:
    if raw.get("status") not in {"available", "insufficient-samples"}:
        return "unavailable"
    if raw.get("median") is not None or raw.get("sample_count") is not None:
        return "distribution"
    unit = str(raw.get("unit") or "").lower()
    if unit in {"events", "blocks", "transactions", "operations", "tx", "fds", "threads"}:
        return "count"
    return "scalar"


def _source_for_fallback(target: dict[str, Any]) -> str:
    return "patched" if target.get("mode") == "patched" else "stock"


def _evidence_label(sample_count: int | None) -> tuple[str, str]:
    if sample_count is None:
        return "Not sample-based", "neutral"
    if sample_count < 5:
        return "Very small sample", "weak"
    if sample_count < 30:
        return "Small sample", "limited"
    return "Useful sample", "useful"


def _display_value(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "—" if value is None else str(value)
    number = float(value)
    if not math.isfinite(number):
        return "—"
    magnitude = abs(number)
    if magnitude >= 1_000_000_000_000:
        return f"{number:.3g}"
    if number.is_integer():
        return f"{int(number):,}"
    if magnitude >= 1_000:
        return f"{number:,.2f}".rstrip("0").rstrip(".")
    if magnitude >= 1:
        return f"{number:.3f}".rstrip("0").rstrip(".")
    return f"{number:.3g}"


def _outcomes(raw: Any, unit: str) -> list[dict[str, Any]]:
    if not isinstance(raw, dict):
        return []
    source = raw.get("by_outcome") if isinstance(raw.get("by_outcome"), dict) else raw
    rows = []
    for outcome, distribution in sorted(source.items()):
        if not isinstance(distribution, dict):
            continue
        rows.append(
            {
                "outcome": outcome,
                "status": distribution.get("status") or "unavailable",
                "sample_count": distribution.get("sample_count"),
                "median": distribution.get("median"),
                "p95": distribution.get("p95"),
                "p99": distribution.get("p99"),
                "unit": distribution.get("unit") or unit,
            }
        )
    return rows


def _technical_values(raw: dict[str, Any]) -> list[dict[str, Any]]:
    excluded = {
        "status", "unit", "reason", "value", "sample_count", "mean", "median",
        "minimum", "maximum", "p95", "p99", "all", "by_outcome",
    }
    rows = []
    for key, value in raw.items():
        if key in excluded or value is None:
            continue
        shown = json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else value
        rows.append({"key": key, "label": _humanize(key), "value": shown})
    return rows


def _primary_support(metric_type: str, raw: dict[str, Any], derivation: str) -> str:
    if metric_type == "distribution":
        count = raw.get("sample_count")
        return f"n={count} observed timings" if isinstance(count, int) else derivation
    if raw.get("duration_seconds") is not None:
        return f"Derived over {raw['duration_seconds']} seconds. {derivation}".strip()
    return derivation


def _primary(metric_type: str, concept: dict[str, Any], raw: dict[str, Any]) -> tuple[str, Any]:
    statistic = str(concept.get("primary_statistic") or "value")
    if metric_type == "distribution":
        return "Median", raw.get("median")
    if metric_type == "count":
        return "Event count", raw.get(statistic, raw.get("value"))
    if metric_type == "scalar":
        labels = {
            "delta": "Change",
            "peak": "Peak",
            "offered_rate": "Offered rate",
            "rate_per_second": "Rate",
            "value": "Measured value",
        }
        value = raw.get(statistic)
        if value is None:
            value = raw.get("value", raw.get("delta", raw.get("offered_rate")))
        return labels.get(statistic, "Measured value"), value
    return "Unavailable", None


def _card(
    concept: dict[str, Any],
    binding: dict[str, Any],
    raw_metrics: dict[str, Any],
    target: dict[str, Any],
) -> dict[str, Any]:
    metric_ids = [metric_id for metric_id in binding.get("metric_ids") or [] if metric_id in raw_metrics]
    source = binding["source"]
    if source == "reserved":
        primary_id = metric_ids[0] if metric_ids else ""
        raw = raw_metrics.get(primary_id) if primary_id else {}
        raw = raw if isinstance(raw, dict) else {}
        reason = (
            raw.get("reason")
            or binding.get("derivation")
            or "No DWARF collector currently implements this concept."
        )
        return {
            "concept_id": concept["id"],
            "title": concept["title"],
            "description": concept["description"],
            "category": "unavailable",
            "metric_type": "unavailable",
            "source": source,
            "status": "reserved",
            "primary_label": "Implementation status",
            "primary_value": None,
            "primary_display": "—",
            "primary_unit": "",
            "primary_support": reason,
            "sample_count": None,
            "evidence_label": "Not implemented",
            "evidence_tone": "neutral",
            "reason": reason,
            "raw_metric_ids": metric_ids,
            "outcomes": [],
            "statistics": {},
            "technical_values": _technical_values(raw),
            "derivation": reason,
            "target": dict(target),
        }

    primary_id = next(
        (metric_id for metric_id in metric_ids if not metric_id.endswith("_by_outcome")),
        metric_ids[0] if metric_ids else "",
    )
    raw = raw_metrics.get(primary_id) if primary_id else {}
    raw = raw if isinstance(raw, dict) else {}
    if isinstance(raw.get("all"), dict):
        primary = raw["all"]
    else:
        primary = raw
    status = str(primary.get("status") or raw.get("status") or "unavailable")
    metric_type = concept["metric_type"] if status in {"available", "insufficient-samples"} else "unavailable"
    sample_count = primary.get("sample_count") if metric_type == "distribution" else None
    sample_count = sample_count if isinstance(sample_count, int) and sample_count > 0 else None
    primary_label, primary_value = _primary(metric_type, concept, primary)
    reason = primary.get("reason") or raw.get("reason") or ""
    evidence_label, evidence_tone = _evidence_label(sample_count)
    companion = next(
        (raw_metrics[metric_id] for metric_id in metric_ids if metric_id.endswith("_by_outcome")),
        raw,
    )
    unit = str(primary.get("unit") or raw.get("unit") or "")
    derivation = str(binding.get("derivation") or "")
    return {
        "concept_id": concept["id"],
        "title": concept["title"],
        "description": concept["description"],
        "category": "unavailable" if metric_type == "unavailable" else concept["category"],
        "metric_type": metric_type,
        "source": source,
        "status": status,
        "primary_label": primary_label,
        "primary_value": primary_value,
        "primary_display": _display_value(primary_value),
        "primary_unit": unit,
        "primary_support": reason if metric_type == "unavailable" else _primary_support(metric_type, primary, derivation),
        "sample_count": sample_count,
        "evidence_label": "Unavailable" if metric_type == "unavailable" else evidence_label,
        "evidence_tone": "unavailable" if metric_type == "unavailable" else evidence_tone,
        "reason": reason,
        "raw_metric_ids": metric_ids,
        "outcomes": _outcomes(companion, unit),
        "statistics": {
            "mean": primary.get("mean"),
            "minimum": primary.get("minimum"),
            "maximum": primary.get("maximum"),
            "p95": primary.get("p95"),
            "p99": primary.get("p99"),
            "sample_count": primary.get("sample_count"),
        },
        "technical_values": _technical_values(primary),
        "derivation": derivation,
        "target": dict(target),
    }


def _fallback_card(metric_id: str, raw_value: Any, target: dict[str, Any]) -> dict[str, Any]:
    raw = raw_value if isinstance(raw_value, dict) else {}
    primary = raw.get("all") if isinstance(raw.get("all"), dict) else raw
    metric_type = _metric_type(primary)
    status = str(primary.get("status") or "unavailable")
    unit = str(primary.get("unit") or raw.get("unit") or "")
    sample_count = primary.get("sample_count") if metric_type == "distribution" else None
    sample_count = sample_count if isinstance(sample_count, int) and sample_count > 0 else None
    if metric_type == "distribution":
        primary_label, primary_value = "Median", primary.get("median")
    elif metric_type == "count":
        primary_label, primary_value = "Event count", primary.get("value")
    elif metric_type == "scalar":
        primary_label = "Measured value"
        primary_value = primary.get("value", primary.get("delta", primary.get("offered_rate")))
    else:
        primary_label, primary_value = "Unavailable", None
    evidence_label, evidence_tone = _evidence_label(sample_count)
    reason = str(primary.get("reason") or raw.get("reason") or "")
    return {
        "concept_id": metric_id.replace("_", "-"),
        "title": _humanize(metric_id),
        "description": "A retained DWARF measurement that has not yet been assigned a canonical client description.",
        "category": "unavailable" if metric_type == "unavailable" else "protocol-ledger",
        "metric_type": metric_type,
        "source": _source_for_fallback(target),
        "status": status,
        "primary_label": primary_label,
        "primary_value": primary_value,
        "primary_display": _display_value(primary_value),
        "primary_unit": unit,
        "primary_support": reason,
        "sample_count": sample_count,
        "evidence_label": "Unavailable" if metric_type == "unavailable" else evidence_label,
        "evidence_tone": "unavailable" if metric_type == "unavailable" else evidence_tone,
        "reason": reason,
        "raw_metric_ids": [metric_id],
        "outcomes": _outcomes(raw, unit),
        "statistics": {
            "mean": primary.get("mean"),
            "minimum": primary.get("minimum"),
            "maximum": primary.get("maximum"),
            "p95": primary.get("p95"),
            "p99": primary.get("p99"),
            "sample_count": primary.get("sample_count"),
        },
        "technical_values": _technical_values(primary),
        "derivation": "",
        "target": dict(target),
    }


def build_measurement_presentation(
    raw_metrics: dict[str, Any],
    target_identity: dict[str, Any],
    *,
    catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return canonical cards and groups for one retained measurement report."""
    catalog = catalog or load_measurement_presentation_catalog()
    metrics = raw_metrics if isinstance(raw_metrics, dict) else {}
    cards: list[dict[str, Any]] = []
    consumed: set[str] = set()
    for concept in catalog["concepts"]:
        binding = _binding_for(concept, target_identity)
        if binding is None:
            continue
        present = [metric_id for metric_id in binding.get("metric_ids") or [] if metric_id in metrics]
        if not present and binding.get("source") != "reserved":
            continue
        card = _card(concept, binding, metrics, target_identity)
        card["source_label"] = catalog["source_labels"][card["source"]]
        cards.append(card)
        consumed.update(present)

    for metric_id, raw in metrics.items():
        if metric_id in consumed:
            continue
        if metric_id.endswith("_by_outcome") and metric_id.removesuffix("_by_outcome") in consumed:
            continue
        card = _fallback_card(metric_id, raw, target_identity)
        card["source_label"] = catalog["source_labels"][card["source"]]
        cards.append(card)

    category_order = {item["id"]: index for index, item in enumerate(catalog["categories"])}
    cards.sort(key=lambda item: (category_order.get(item["category"], 999), item["title"].lower()))
    groups = []
    for category in catalog["categories"]:
        group_cards = [card for card in cards if card["category"] == category["id"]]
        if group_cards:
            groups.append({**category, "cards": group_cards, "count": len(group_cards)})
    return {
        "cards": cards,
        "groups": groups,
        "categories": [
            {**category, "count": sum(card["category"] == category["id"] for card in cards)}
            for category in catalog["categories"]
            if any(card["category"] == category["id"] for card in cards)
        ],
    }
