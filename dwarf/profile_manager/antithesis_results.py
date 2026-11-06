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
    if result.get("properties_truncated"):
        # The proxy returned only the first page of properties (moog v0.5.1.3
        # cannot page them); a failure seen is a failure, but a pass needs the
        # whole table.
        return "inconclusive", "properties_truncated"
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


# ---------------------------------------------------------------------------
# bundle writer

_GAPS = (
    ("probes/", "Antithesis does not export DWARF probe output; the platform's properties table stands in."),
    ("metrics/", "No DWARF metrics collector ran inside the Antithesis environment."),
    ("launch/", "The launch happened through MOOG; see external/launch.json and external/submission.json."),
    ("chain.json (signed)", "The chain entry binds the manifest hash only; there is no local signing step for an external run."),
    ("evidence volume", "The Antithesis evidence volume is not retrievable after the run."),
)
_MEASUREMENTS_GAP = ("measurements/report.json", "No dwarf_measurements lines were retrievable from the Antithesis events.")


def _private_dir(path):
    import os
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)


def _private_write(path, data: bytes):
    import os
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(path, 0o600)


def _json_bytes(value) -> bytes:
    import json
    return json.dumps(value, sort_keys=True, indent=1, ensure_ascii=False).encode("utf-8") + b"\n"


def bundle_complete(run_dir) -> bool:
    """True when manifest.json exists and chain.json binds its hash."""
    import json
    from pathlib import Path

    from profile_manager import forensic

    run_dir = Path(run_dir)
    try:
        manifest = json.loads((run_dir / "manifest.json").read_bytes())
        chain = json.loads((run_dir / "chain.json").read_bytes())
    except (OSError, ValueError):
        return False
    return isinstance(manifest, dict) and isinstance(chain, dict) and \
        chain.get("manifest_hash") == forensic._sha256_hex(forensic._canonical_json(manifest))


def write_result_bundle(run_dir, *, entry: Mapping[str, Any], result: Mapping[str, Any], raw: Mapping[str, Any],
                        scenario_source: bytes | None, measurements: Mapping[str, Any] | None):
    """Write the local run-bundle layout for an Antithesis result into the
    ledger-backed run directory and return the manifest path.

    Raw proxy rows go under external/report/ (0700/0600) with the run row's
    `links` dropped (they carry the signed report URL). A complete bundle is
    never overwritten."""
    import hashlib
    from pathlib import Path

    from profile_manager import forensic
    from profile_manager.scenario_verdict import evaluate_scenario_verdict

    run_dir = Path(run_dir)
    if bundle_complete(run_dir):
        raise ValueError("result bundle already complete")
    run_dir.mkdir(parents=True, exist_ok=True)
    report = run_dir / "external" / "report"
    _private_dir(run_dir / "external")
    _private_dir(report)
    raw_run = {k: v for k, v in (raw.get("run") or {}).items() if k != "links"} if isinstance(raw.get("run"), Mapping) else {}
    _private_write(report / "run.json", _json_bytes(raw_run))
    _private_write(report / "properties.json", _json_bytes(raw.get("properties") if isinstance(raw.get("properties"), Mapping) else {}))
    result_bytes = _json_bytes(dict(result))
    _private_write(run_dir / "external" / "result.json", result_bytes)

    assertions = result_assertions(result)
    summary = {"total": len(assertions), "pass": sum(a["result"] == "pass" for a in assertions), "fail": sum(a["result"] == "fail" for a in assertions)}
    exit_status = "pass" if result.get("run_status") == "completed" else "fail"
    canonical = evaluate_scenario_verdict(exit_status=exit_status, assertions=summary, measurements=None, run_kind="external")
    manifest = {
        "schema_version": "v1",
        "run_id": run_dir.name,
        "kind": "external",
        "runtime": "antithesis",
        "backend": "antithesis",
        "scenario": dict(entry.get("scenario") or {}),
        "external": {"backend": "antithesis", "test_run_id": result.get("test_run_id"),
                     "antithesis_run_id": result.get("antithesis_run_id"), "run_status": result.get("run_status")},
        "exit_status": exit_status,
        "started_at": result.get("created_at"),
        "ended_at": result.get("completed_at"),
        "assertion_summary": summary,
        "execution": {"security_verdict": canonical.security_verdict, "reason_codes": list(canonical.reason_codes)},
        "result": {"source": "antithesis-report", "sha256": hashlib.sha256(result_bytes).hexdigest()},
    }
    missing = [{"path": path, "reason": reason} for path, reason in _GAPS]
    if measurements is not None:
        manifest["measurements"] = dict(measurements)
        (run_dir / "measurements").mkdir(exist_ok=True)
        forensic.atomic_write_bytes(run_dir / "measurements" / "report.json", _json_bytes(dict(measurements)))
    else:
        missing.append({"path": _MEASUREMENTS_GAP[0], "reason": _MEASUREMENTS_GAP[1]})
    forensic.atomic_write_bytes(run_dir / "assertions.json", _json_bytes(assertions))
    if scenario_source is not None:
        forensic.atomic_write_bytes(run_dir / "scenario.yaml", bytes(scenario_source))
    forensic.atomic_write_bytes(run_dir / "gaps.json", _json_bytes({"schema_version": "v1", "runtime": "antithesis", "missing": missing}))
    manifest_bytes = forensic._canonical_json(manifest)
    forensic.atomic_write_bytes(run_dir / "manifest.json", manifest_bytes)
    forensic.atomic_write_bytes(run_dir / "chain.json", _json_bytes({"run_id": run_dir.name, "manifest_hash": forensic._sha256_hex(manifest_bytes), "source": "antithesis-report"}))
    return run_dir / "manifest.json"


# ---------------------------------------------------------------------------
# measurements from the proxy's event stream

# The curated packages name their services after the node they wrap.
_SERVICE_IMPLEMENTATIONS = (("cardano", "cardano-node"), ("amaru", "amaru"))


def _progress_lines(events):
    """The dwarf_measurements progress lines (antithesis_measurement_runtime.progress_summary) in order."""
    import json

    lines = []
    for event in events or []:
        if not isinstance(event, Mapping):
            continue
        try:
            parsed = json.loads(event.get("output_text") or "")
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, Mapping) and isinstance(parsed.get("dwarf_measurements"), str) and parsed["dwarf_measurements"]:
            lines.append(parsed)
    return lines


def antithesis_target_identity(events) -> dict[str, Any]:
    """What the proxy exports about the measured target: the engine and, from
    the measured service's name, the implementation. Version and mode are left
    empty until the catalog parity work binds them (identity stays partial)."""
    implementation = None
    for line in _progress_lines(events):
        service = line["dwarf_measurements"]
        for marker, name in _SERVICE_IMPLEMENTATIONS:
            if marker in service:
                implementation = name
                break
        if implementation:
            break
    return {"engine": "antithesis", "implementation": implementation, "mode": None, "version": None}


def rebuild_measurements(events, *, scenario: str, target_identity: Mapping[str, Any]) -> dict[str, Any] | None:
    """A partial measurements/report.json from the dwarf_measurements progress
    lines in the Antithesis event stream (the service's files are not
    retrievable after the run; the proxy caps events at 50). The last line per
    service wins; None when no line was retrievable."""
    lines = _progress_lines(events)
    if not lines:
        return None
    measurements: dict[str, dict[str, Any]] = {}
    services = set()
    for line in lines:
        services.add(line["dwarf_measurements"])
        collectors = line.get("collectors") if isinstance(line.get("collectors"), Mapping) else {}
        for collector in collectors.values():
            if not isinstance(collector, Mapping):
                continue
            rows = collector.get("measurements") if isinstance(collector.get("measurements"), Mapping) else {}
            for name, cell in rows.items():
                if not isinstance(cell, Mapping):
                    continue
                measurements[str(name)] = {"status": cell.get("status"), "value": cell.get("value"), "unit": cell.get("unit")}
    return {"schema_version": "v1", "scenario": scenario, "target_identity": dict(target_identity),
            "source": "antithesis-events", "complete": False, "services": sorted(services), "measurements": measurements}
