"""The tap coverage rule for retained measurement reports.

Concept-level: every measurement concept bound for a run's implementation and mode
(through ``presentation-v1.json``) must carry a value on at least one of its bound raw
ids, or be a documented exception in ``coverage-exceptions-v1.json``. A zero counts only
with an evidence denominator (``offered_count``, ``count`` or ``sample_count``), a
verdict dict counts as its sum. Reserved placeholder ids therefore pass on a patched run
through the concept's patched alternates and are exceptions on the stock run only.

This is a rule about retained evidence, never about what a node must achieve: a tap
without a value is a gap in the measurement, not a finding against the node.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

from profile_manager.measurement_presentation import (
    canonical_unit,
    is_companion_metric,
    sum_verdict_dict,
)

_EXCEPTIONS_PATH = Path(__file__).resolve().parents[1] / "measurements" / "coverage-exceptions-v1.json"


def load_coverage_exceptions(path: Path | None = None) -> dict[str, Any]:
    document = json.loads(Path(path or _EXCEPTIONS_PATH).read_text(encoding="utf-8"))
    if document.get("schema_version") != "v1" or not isinstance(document.get("exceptions"), list):
        raise ValueError("coverage exceptions catalog must use schema_version v1 with an exceptions list")
    for row in document["exceptions"]:
        if (row.get("implementation") not in {"amaru", "cardano-node"} or not row.get("concept")
                or not isinstance(row.get("modes"), list) or not row["modes"]
                or not set(row["modes"]) <= {"stock", "patched"} or not row.get("reason")):
            raise ValueError(f"coverage exception is invalid: {row}")
    return document


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def metric_has_value(raw: Any) -> bool:
    """A retained metric carries a value: a finite primary (value/rate/delta/median,
    a verdict dict summed) that is non-zero, or a zero backed by evidence."""
    if not isinstance(raw, dict):
        return False
    primary = raw.get("all") if isinstance(raw.get("all"), dict) else raw
    if not isinstance(primary, dict):
        return False
    value = primary.get("value")
    if isinstance(value, dict):
        value = sum_verdict_dict(value)
    # Primary statistics in catalog order of preference: counters (delta), rates, gauges (peak), distributions.
    for key in ("offered_rate", "delta", "absolute_delta", "peak", "median"):
        if value is None:
            value = primary.get(key)
    number = _number(value)
    if number is None:
        return False
    if number != 0:
        return True
    evidence = (_number(primary.get("offered_count")) or 0) + (_number(primary.get("count")) or 0) + (_number(primary.get("sample_count")) or 0)
    return evidence > 0


def _raw_unit(raw: Any) -> str:
    if not isinstance(raw, dict):
        return ""
    primary = raw.get("all") if isinstance(raw.get("all"), dict) else raw
    return str((primary or {}).get("unit") or raw.get("unit") or "")


def _bindings_for(index: dict[str, Any], implementation: str, mode: str):
    """(concept id, raw id, binding) for every binding that applies to this implementation and mode."""
    for impl in (implementation, "*"):
        for raw_id, entries in (index.get("bindings") or {}).get(impl, {}).items():
            for binding in entries:
                if binding["modes"] and mode not in binding["modes"]:
                    continue
                yield binding["concept"], raw_id, binding


def coverage_report(
    report_metrics: dict[str, Any], implementation: str, mode: str, *,
    catalog_index: dict[str, Any] | None = None, exceptions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from profile_manager.data.measurement_compare import _catalog_index

    index = catalog_index or _catalog_index()
    exceptions = exceptions or load_coverage_exceptions()
    excepted_concepts = {
        row["concept"]: row for row in exceptions["exceptions"]
        if row["implementation"] == implementation and mode in row["modes"]
    }
    metrics = report_metrics if isinstance(report_metrics, dict) else {}
    present_ids: dict[str, list[str]] = {}
    bound_ids: set[str] = set()
    for concept_id, raw_id, binding in _bindings_for(index, implementation, mode):
        bound_ids.add(raw_id)
        if raw_id not in metrics or is_companion_metric(raw_id):
            continue
        concept = index["concepts"].get(concept_id) or {}
        unit = concept.get("unit")
        if unit and not binding.get("unit_equivalent") and canonical_unit(unit, index) != canonical_unit(_raw_unit(metrics[raw_id]), index):
            continue
        present_ids.setdefault(concept_id, []).append(raw_id)
    covered, missing, excepted = [], [], []
    for concept_id in sorted(present_ids):
        ids = present_ids[concept_id]
        if any(metric_has_value(metrics[raw_id]) for raw_id in ids):
            covered.append(concept_id)
        elif concept_id in excepted_concepts:
            excepted.append({"concept": concept_id, "ids": ids, "reason": excepted_concepts[concept_id]["reason"],
                             "provisional": bool(excepted_concepts[concept_id].get("provisional"))})
        else:
            missing.append({"concept": concept_id, "ids": ids, "reason": _first_reason(metrics, ids)})
    unbound = sorted(raw_id for raw_id in metrics if raw_id not in bound_ids and not is_companion_metric(raw_id))
    return {"implementation": implementation, "mode": mode, "covered": covered, "missing": missing,
            "excepted": excepted, "unbound_ids": unbound}


def _first_reason(metrics: dict[str, Any], ids: list[str]) -> str:
    for raw_id in ids:
        raw = metrics.get(raw_id)
        primary = raw.get("all") if isinstance(raw, dict) and isinstance(raw.get("all"), dict) else raw
        reason = (primary or {}).get("reason") if isinstance(primary, dict) else None
        if reason:
            return str(reason)
    return ""


def report_identity(report: dict[str, Any]) -> tuple[str, str]:
    identity = report.get("target_identity") if isinstance(report, dict) else None
    identity = identity if isinstance(identity, dict) else {}
    implementation = str(identity.get("implementation") or "")
    mode = str(identity.get("mode") or "stock")
    if implementation not in {"amaru", "cardano-node"}:
        raise ValueError("report carries no usable target identity")
    return implementation, mode


def rule_holds(report: dict[str, Any], **options: Any) -> bool:
    implementation, mode = report_identity(report)
    result = coverage_report(report.get("measurements") or {}, implementation, mode, **options)
    return not result["missing"] and not result["unbound_ids"]


def report_from_archive(archive: Path) -> dict[str, Any]:
    """The retained ``measurements/report.json`` inside a portable run bundle."""
    import tarfile

    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            if member.name.endswith("/measurements/report.json") or member.name == "measurements/report.json":
                handle = tar.extractfile(member)
                if handle is None:
                    break
                return json.loads(handle.read())
    raise ValueError(f"no measurements/report.json in {archive.name}")


def main(argv: list[str] | None = None) -> int:
    paths = [Path(p) for p in (argv if argv is not None else sys.argv[1:])]
    if not paths:
        print("usage: tap_coverage <bundle.tar.gz | report.json> ...", file=sys.stderr)
        return 2
    exit_code = 0
    for path in paths:
        report = report_from_archive(path) if path.suffix in {".gz", ".tgz"} else json.loads(path.read_text(encoding="utf-8"))
        implementation, mode = report_identity(report)
        result = coverage_report(report.get("measurements") or {}, implementation, mode)
        print(json.dumps({"path": str(path), **result}, indent=1))
        if result["missing"] or result["unbound_ids"]:
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
