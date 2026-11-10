"""Collector interface helpers for version-pinned DWARF measurements.

Collectors are intentionally small adapters. They receive a bounded context,
write only inside their run-bundle directory, and return normalized samples to
the reporting layer. Concrete Amaru collectors live in later modules.
"""
from typing import Any

EMPTY_TRACE_REASON = (
    "no node trace record was captured in the telemetry window; a zero here would not "
    "be a reading"
)


def withhold_without_source(measurements: dict[str, Any], *, source_record_count: int) -> dict[str, Any]:
    """An empty trace window measures nothing: no counter may read as an observed zero.

    Metrics that are already unavailable keep their own reason; every other metric
    becomes unavailable with the empty-window reason.
    """
    if source_record_count:
        return measurements
    withheld = {}
    for name, metric in measurements.items():
        if isinstance(metric, dict) and metric.get("status") == "unavailable" and metric.get("reason"):
            withheld[name] = metric
            continue
        unit = metric.get("unit") if isinstance(metric, dict) else None
        withheld[name] = {"status": "unavailable", "unit": str(unit or "events"), "value": None,
                          "reason": EMPTY_TRACE_REASON}
    return withheld

