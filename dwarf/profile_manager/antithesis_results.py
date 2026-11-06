"""Antithesis results back into DWARF's one output (AT-4).

Inputs are the MOOG proxy's `moog antithesis run` row and `properties` table
(see tests/fixtures/antithesis_proxy). Outputs are a sanitised result document,
DWARF verdict rules over it, and the local run-bundle layout so the external
run reads like any other run in the Results and Compare views.

Nothing here touches Docker or the network; the callers fetch through
`profile_manager.remote.fetch_antithesis`.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

RESULT_SCHEMA = "dwarf.antithesis-result.v1"

# Antithesis SDK property names start with the assertion kind. Everything else
# in the table is a platform property (build, images, container exits …).
_ALWAYS_PREFIXES = ("Always: ", "Unreachable: ")
_SOMETIMES_PREFIXES = ("Sometimes: ", "Reachable: ")


def property_kind(name: Any) -> str:
    """'always' | 'sometimes' for DWARF SDK properties, 'platform' for the rest."""
    if not isinstance(name, str):
        return "platform"
    if name.startswith(_ALWAYS_PREFIXES):
        return "always"
    if name.startswith(_SOMETIMES_PREFIXES):
        return "sometimes"
    return "platform"


# DWARF's workloads name SDK assertions with snake_case identifiers
# (`mixed_sm_workload_command_invoked`, see antithesis_conventions.py); the
# platform's own SDK-prefixed properties use prose ("Always: Peak memory usage").
_DWARF_MESSAGE = re.compile(r"^[a-z][a-z0-9_]*$")


def property_source(name: Any) -> str:
    """'dwarf' for a DWARF SDK assertion, 'antithesis' for a platform property."""
    if property_kind(name) == "platform":
        return "antithesis"
    message = name.split(": ", 1)[1] if isinstance(name, str) and ": " in name else ""
    return "dwarf" if _DWARF_MESSAGE.match(message) else "antithesis"


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def normalise_result(run: Mapping[str, Any], properties: Mapping[str, Any] | None, *, test_run_id: str) -> dict[str, Any]:
    """The sanitised result: run identity and status, environment versions, and
    one row per property with name, kind, status and counts. Report links, the
    launch description, parameters, examples and counterexamples are dropped."""
    environment = ((run.get("results") or {}).get("environment") or {}) if isinstance(run.get("results"), Mapping) else {}
    images = []
    for image in environment.get("images") or []:
        if isinstance(image, Mapping):
            images.append({"name": _text(image.get("name")), "tag": _text(image.get("tag"))})
    rows = []
    data = properties.get("data") if isinstance(properties, Mapping) else None
    for item in data or []:
        if not isinstance(item, Mapping):
            continue
        name = _text(item.get("name")) or ""
        status = _text(item.get("status")) or "Unknown"
        rows.append({
            "id": _text(item.get("id")),
            "name": name,
            "kind": property_kind(name),
            "source": property_source(name),
            "status": status,
            "passed": status == "Passing",
            "example_count": _int(item.get("example_count")),
            "counterexample_count": _int(item.get("counterexample_count")),
        })
    return {
        "schema_version": RESULT_SCHEMA,
        "test_run_id": test_run_id,
        "antithesis_run_id": _text(run.get("run_id")),
        "run_status": _text(run.get("status")) or "unknown",
        "created_at": _text(run.get("created_at")),
        "completed_at": _text(run.get("completed_at")),
        "environment": {"antithesis_version": _text(environment.get("antithesis_version")), "images": images},
        "properties": rows,
        "properties_available": isinstance(properties, Mapping),
        "properties_truncated": bool(isinstance(properties, Mapping) and properties.get("next_cursor")),
        "dwarf_property_count": sum(1 for row in rows if row["source"] == "dwarf"),
        "failing": [row["name"] for row in rows if not row["passed"]],
    }


def result_verdict(result: Mapping[str, Any]) -> tuple[str, str]:
    """DWARF's verdict over the properties table.

    pass only when every DWARF `always` property passed, every `sometimes`
    was encountered, no property at all is failing, and at least one DWARF
    property exists (a table of platform properties alone proves nothing)."""
    if result.get("run_status") != "completed":
        return "inconclusive", "antithesis_run_not_terminal"
    rows = [row for row in result.get("properties") or [] if isinstance(row, Mapping)]
    if not result.get("properties_available", True) or not rows:
        return "inconclusive", "properties_unavailable"
    dwarf = [row for row in rows if row.get("source") == "dwarf"]
    if not dwarf:
        return "inconclusive", "no_dwarf_assertions"
    if any(not row.get("passed") for row in dwarf):
        return "fail", "assertions_failed"
    if any(not row.get("passed") for row in rows):
        return "fail", "platform_property_failed"
    if any(row.get("kind") == "sometimes" and _int(row.get("example_count")) == 0 for row in dwarf):
        # A `sometimes` that was never encountered is the vacuous case the SDK itself reports as failing.
        return "fail", "sometimes_not_encountered"
    return "pass", "verified_antithesis_report"


def result_assertions(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    """assertions.json rows: every property, DWARF ones tagged `source: dwarf`.
    A `sometimes` property that was never encountered is a failed assertion."""
    assertions = []
    for row in result.get("properties") or []:
        if not isinstance(row, Mapping):
            continue
        kind = row.get("kind") or "platform"
        source = row.get("source") or property_source(row.get("name"))
        unencountered = source == "dwarf" and kind == "sometimes" and _int(row.get("example_count")) == 0
        assertions.append({
            "id": row.get("id"),
            "name": row.get("name"),
            "kind": kind,
            "source": source,
            "result": "pass" if bool(row.get("passed")) and not unencountered else "fail",
        })
    return assertions
