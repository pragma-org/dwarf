"""Measurement comparison across node implementations and exact builds.

Pure builders over retained run evidence (manifest, chain entry, schema-v1
measurement report and selection). Groups are exact builds taken from one
coherent retained source; rows are compatibility fingerprints; every cell
keeps its state explicit and traces back to the contributing runs and their
bundles. Runs whose provenance is incoherent are excluded and listed; runs
whose provenance is incomplete are shown but never aggregated with others.
Nothing here collapses unlike measurements into a score.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode

STATES = (
    "available",
    "observed-zero",
    "partial",
    "unavailable",
    "not-measured",
    "invalid",
    "insufficient-samples",
    "incompatible",
    "conflict",
)
AGGREGATION_RULE = "median-of-run-primary-values"
GROUP_COLORS = 8
DISAGREEMENT_SPREAD = 0.25
IDENTITY_FIELDS = ("implementation", "version", "source_revision", "image_digest")
# Keys of the retained scenario's ``target`` block that carry the operator-selected
# exact build identity. They are the comparison dimension, so they are removed
# from the scenario behavior digest; every other scenario field stays in it.
SCENARIO_TARGET_IDENTITY_FIELDS = IDENTITY_FIELDS
FINGERPRINT_PARTS = (
    "metric_id",
    "unit",
    "scenario_id",
    "scenario_behavior_sha256",
    "runtime",
    "mode",
    "measurement_profile_id",
    "measurement_profile_digest",
    "collector_digests",
    "profile_id",
    "profile_sha256",
    "report_schema_version",
)
# Parts a complete fingerprint must carry; deployment profile is required only for devnet runs.
REQUIRED_FINGERPRINT_PARTS = (
    "scenario_id",
    "scenario_behavior_sha256",
    "runtime",
    "mode",
    "measurement_profile_id",
    "measurement_profile_digest",
    "report_schema_version",
)
# Bars (build groups) also split by mode so stock and patched builds of one release are told apart.
GROUP_IDENTITY_FIELDS = IDENTITY_FIELDS + ("mode",)
ROLLUP_MODES = ("exact", "version", "run")
FILTER_KEYS = (
    "impl", "version", "scenario", "category", "metric", "outcome", "since", "until", "baseline", "rollup",
    # Picked run ids (comma-separated): lifts the one-workload gate and keys rows by catalog concept.
    "runs",
    # Display preferences: carried in every link and form, never part of aggregation or provenance.
    "show_unmeasured", "show_provenance", "show_samples", "show_evidence", "show_revisions",
)
_SEVERITY = {"invalid": 3, "insufficient-samples": 2, "unavailable": 1}
_NUMERIC_STATES = {"available", "observed-zero"}
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


# --- catalog ---------------------------------------------------------------


def _catalog_index(catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    from profile_manager.measurement_presentation import load_measurement_presentation_catalog

    from profile_manager.measurement_presentation import is_companion_metric

    catalog = catalog or load_measurement_presentation_catalog()
    by_metric: dict[str, dict[str, str]] = {}
    concepts: dict[str, dict[str, Any]] = {}
    bindings: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for concept in catalog["concepts"]:
        direction = concept.get("comparison_direction") or "neutral"
        concepts[concept["id"]] = {
            "title": concept["title"],
            "category": concept["category"],
            "comparison_direction": direction,
            "unit": concept.get("unit"),
            "metric_type": concept.get("metric_type"),
            "compare_as": concept.get("compare_as"),
            "equivalence": concept.get("equivalence") or "exact",
        }
        for binding in concept.get("bindings") or []:
            metric_ids = list(binding.get("metric_ids") or [])
            primary = next((m for m in metric_ids if not is_companion_metric(m)), None)
            for metric_id in metric_ids:
                by_metric.setdefault(metric_id, {"title": concept["title"], "category": concept["category"], "comparison_direction": direction})
                bindings.setdefault(binding["implementation"], {}).setdefault(metric_id, []).append(
                    {
                        "concept": concept["id"],
                        "modes": list(binding.get("modes") or []),
                        # Existing bindings predate `boundary`; their derivation sentence names the source just as well.
                        "boundary": binding.get("boundary") or binding.get("derivation") or "",
                        "unit_equivalent": bool(binding.get("unit_equivalent")),
                        "primary": metric_id == primary,
                        # Ordered alternatives of the same concept (bare and collector-prefixed ids):
                        # a record contributes through the first one it carries in a fitting unit.
                        "metric_ids": metric_ids,
                    }
                )
    categories = [{"id": item["id"], "label": item["label"]} for item in catalog["categories"] if item["id"] != "unavailable"]
    categories.append({"id": "uncategorized", "label": "Other metrics"})
    return {
        "by_metric": by_metric,
        "categories": categories,
        "order": {c["id"]: i for i, c in enumerate(categories)},
        "concepts": concepts,
        "bindings": bindings,
        "implementation_labels": dict(catalog.get("implementation_labels") or {}),
        "unit_short": dict(catalog.get("unit_short") or {}),
        "unit_full": dict(catalog.get("unit_full") or {}),
    }


def _raw_unit(row: dict[str, Any]) -> str:
    return str(row.get("unit") or "")


def _matching_bindings(index: dict[str, Any], record: dict[str, Any], metric_id: str, row: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Bindings of this raw id for the record's implementation (then ``*``) whose mode and unit fit."""
    from profile_manager.measurement_presentation import canonical_unit, is_companion_metric

    matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
    raw_unit = canonical_unit(_raw_unit(row), index)
    for implementation in (record.get("implementation") or "", "*"):
        for binding in (index.get("bindings") or {}).get(implementation, {}).get(metric_id, []):
            concept = index["concepts"].get(binding["concept"])
            if concept is None:
                continue
            if binding["modes"] and record.get("mode") and record["mode"] not in binding["modes"]:
                continue
            if not is_companion_metric(metric_id) and not _unit_fits(index, concept, binding, raw_unit):
                continue
            matches.append((concept, binding))
        if matches:
            break
    return matches


def _unit_fits(index: dict[str, Any], concept: dict[str, Any], binding: dict[str, Any], raw_unit: str) -> bool:
    """The source concept's unit and its compare_as target's unit both accept the raw unit (or the binding says equivalent)."""
    from profile_manager.measurement_presentation import canonical_unit

    if binding.get("unit_equivalent"):
        return True
    target = index["concepts"].get(concept.get("compare_as") or "") or {}
    for unit in (concept.get("unit"), target.get("unit")):
        if unit and canonical_unit(unit, index) != raw_unit:
            return False
    return True


def is_antithesis_record(record: dict[str, Any]) -> bool:
    """Antithesis imports keep their own concept table; they never join catalog concept rows."""
    return record.get("runtime") == "antithesis"


def describe_metric(index: dict[str, Any], record: dict[str, Any], metric_id: str, row: dict[str, Any]) -> dict[str, str]:
    """Title, category and direction for one raw metric of one record, resolved through its bindings (unit-aware)."""
    for concept, _binding in _matching_bindings(index, record, metric_id, row):
        return {"title": concept["title"], "category": concept["category"], "comparison_direction": concept["comparison_direction"]}
    known = index["by_metric"].get(metric_id)
    if known:
        return {"title": known["title"], "category": known["category"], "comparison_direction": known.get("comparison_direction") or "neutral"}
    return {"title": _humanize(metric_id), "category": "uncategorized", "comparison_direction": "neutral"}


def _record_primary(index: dict[str, Any], record: dict[str, Any], concept: dict[str, Any], binding: dict[str, Any]) -> str | None:
    """The first id of the binding that this record carries, non-companion and in a fitting unit."""
    from profile_manager.measurement_presentation import canonical_unit, is_companion_metric

    metrics = record.get("metrics") or {}
    for candidate in binding.get("metric_ids") or []:
        if is_companion_metric(candidate) or candidate not in metrics:
            continue
        if not _unit_fits(index, concept, binding, canonical_unit(_raw_unit(metrics[candidate]), index)):
            continue
        return candidate
    return None


def catalog_concept(index: dict[str, Any], record: dict[str, Any], metric_id: str, row: dict[str, Any]) -> dict[str, Any] | None:
    """Concept-keyed row identity for a primary raw metric; None for companions, alternates and unbound ids."""
    from profile_manager.measurement_presentation import canonical_unit, is_companion_metric

    if is_antithesis_record(record):
        return None
    for concept, binding in _matching_bindings(index, record, metric_id, row):
        if is_companion_metric(metric_id) or _record_primary(index, record, concept, binding) != metric_id:
            continue
        source_id = binding["concept"]
        row_id = concept.get("compare_as") or source_id
        row_concept = index["concepts"].get(row_id) or concept
        unit = row_concept.get("unit") or canonical_unit(_raw_unit(row), index)
        return {
            "id": row_id,
            "title": row_concept["title"],
            "category": row_concept["category"],
            "comparison_direction": row_concept["comparison_direction"],
            "boundary": binding["boundary"],
            "approximate": concept.get("equivalence") == "approximate",
            "raw_unit": _raw_unit(row),
            "source_concept": source_id,
            "unit": unit,
            "key": json.dumps(["catalog-concept-v1", row_id, unit], separators=(",", ":")),
        }
    return None


def _humanize(metric_id: str) -> str:
    words = [w for w in metric_id.replace("_", "-").split("-") if w]
    text = " ".join(words)
    return text[:1].upper() + text[1:]


# --- metric cells ----------------------------------------------------------


def _metric_cell(name: str, raw: Any) -> dict[str, Any]:
    """One metric of one run, normalized once for every comparison."""
    from profile_manager.data.operate_run import _measurement_metric_row

    from profile_manager.measurement_presentation import sum_verdict_dict

    row = _measurement_metric_row(name, raw)
    metric = raw if isinstance(raw, dict) else {}
    primary = metric.get("all") if isinstance(metric.get("all"), dict) else metric
    value = row.get("value")
    verdict_total = None
    if isinstance(value, dict):
        # A verdict dict such as {"accepted": 205, "rejected": 0} reads as its sum.
        value = verdict_total = sum_verdict_dict(value)
    elif value is None and isinstance(primary.get("value"), dict):
        value = verdict_total = sum_verdict_dict(primary.get("value"))
    if value is None and _number(primary.get("absolute_delta")) is not None:
        value = _number(primary.get("absolute_delta"))
    if value is None and _number(primary.get("peak")) is not None:
        # Gauges such as the workload backlog read as their peak (catalog primary_statistic).
        value = _number(primary.get("peak"))
    if value is None:
        value = row.get("median")
    if value is None and _number(primary.get("sample_count")) == 0 and (_number(primary.get("offered_count")) or 0) > 0:
        # An empty distribution whose collector states what it could have sampled is an
        # observed zero: no event of this kind among offered_count opportunities.
        value = 0.0
    # A counter's own count is positive evidence even when the row carries a
    # normal value (the shared row builder only maps offered_count for rates).
    count = _number(primary.get("count"))
    if count is None:
        count = _number(primary.get("offered_count"))
    if count is None:
        count = _number(row.get("count"))
    if count is None and verdict_total is not None:
        # The summed verdicts are the counter's own count: positive evidence for the cell.
        count = verdict_total
    sample_count = _number(row.get("sample_count")) or 0.0
    has_delta_evidence = _number(primary.get("baseline")) is not None and _number(primary.get("hostile")) is not None
    row["primary_value"] = _number(value)
    row["count"] = count
    row["positive_evidence"] = sample_count > 0 or (count or 0.0) > 0 or has_delta_evidence
    return row


def cell_state(row: dict[str, Any] | None) -> str:
    """A9: every state stays distinct and zero needs positive evidence."""
    if row is None:
        return "not-measured"
    status = row.get("status") or "unavailable"
    if status == "available":
        value = row.get("primary_value")
        if value is None:
            return "unavailable"
        if value == 0:
            return "observed-zero" if row.get("positive_evidence") else "unavailable"
        return "available"
    if status == "unavailable":
        # A collector marks an empty distribution unavailable; with its offered count it is an observed zero.
        if row.get("primary_value") == 0 and row.get("positive_evidence"):
            return "observed-zero"
        return "unavailable"
    if status == "insufficient-samples":
        return "insufficient-samples"
    return "invalid"


# --- identity and evidence -------------------------------------------------


def _coherent_identity(sources: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    """Pick the exact-build identity from one retained source and check the
    others against it. Fields are never mixed across sources (A7)."""
    cleaned = [(name, {key: _text(src.get(key)) for key in IDENTITY_FIELDS}) for name, src in sources]
    chosen = next(((name, fields) for name, fields in cleaned if all(fields.values())), None)
    if chosen is None:
        chosen = next(((name, fields) for name, fields in cleaned if fields["implementation"] and fields["version"]), None)
    if chosen is None:
        return {
            **{key: "" for key in IDENTITY_FIELDS},
            "identity_source": None,
            "identity_missing": list(IDENTITY_FIELDS),
            "provenance_state": "incomplete",
            "provenance_reason": "no retained source names the target implementation and version",
        }
    name, fields = chosen
    disagreements = []
    for other_name, other in cleaned:
        if other_name == name:
            continue
        for key in IDENTITY_FIELDS:
            if other[key] and fields[key] and other[key] != fields[key]:
                disagreements.append(f"{key} ({name}={fields[key][:12]} vs {other_name}={other[key][:12]})")
    missing = [key for key in IDENTITY_FIELDS if not fields[key]]
    if disagreements:
        state, reason = "incoherent", "retained sources disagree on " + "; ".join(disagreements)
    elif missing:
        state, reason = "incomplete", "exact-build identity missing " + ", ".join(missing)
    else:
        state, reason = "complete", None
    return {**fields, "identity_source": name, "identity_missing": missing, "provenance_state": state, "provenance_reason": reason}


def _evidence_state(run_id: str, manifest: dict[str, Any], chain: dict[str, Any]) -> tuple[str, str | None]:
    """Bind the chain entry's manifest hash to the loaded manifest (A8)."""
    from profile_manager import forensic

    raw = chain.get("manifest_hash")
    if raw is None:
        return "absent", None
    if not isinstance(raw, str) or not _HEX64.match(raw):
        return "malformed", None
    if chain.get("run_id") != run_id or manifest.get("run_id") != run_id:
        return "mismatched-run", None
    if forensic._sha256_hex(forensic._canonical_json(manifest)) != raw:
        return "unbound", None
    return "bound", raw


def scenario_behavior_sha256(run_dir: Path, scenario: dict[str, Any]) -> str:
    """Digest of the retained scenario with only the selected exact target
    identity removed, so builds of one workload compare on one scale.

    The retained ``scenario.yaml`` is trusted only when it hashes to the
    manifest's ``scenario.sha256``; otherwise the exact manifest hash is used,
    which can never make two different scenarios compatible."""
    declared = _text(scenario.get("sha256"))
    if not declared:
        return ""
    try:
        body = (Path(run_dir) / "scenario.yaml").read_bytes()
    except OSError:
        return declared
    if hashlib.sha256(body).hexdigest() != declared:
        return declared
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return declared
    if not isinstance(document, dict):
        return declared
    target = document.get("target")
    if isinstance(target, dict):
        document = dict(document)
        document["target"] = {key: value for key, value in target.items() if key not in SCENARIO_TARGET_IDENTITY_FIELDS}
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def collect_measurement_run_records(runs_dir: Path, *, source: str = "local", run_ids: list[str] | None = None, include_unmeasured: bool = False) -> list[dict[str, Any]]:
    """One record per run directory that retains a manifest and a measurement report."""
    runs_dir = Path(runs_dir)
    records: list[dict[str, Any]] = []
    if not runs_dir.is_dir():
        return records
    candidates = (runs_dir / run_id for run_id in set(run_ids)) if run_ids is not None else runs_dir.iterdir()
    for run_dir in sorted(p for p in candidates if p.is_dir()):
        manifest = _read_json(run_dir / "manifest.json", None)
        report = _read_json(run_dir / "measurements" / "report.json", None)
        if report is None and include_unmeasured:
            report = {}
        if not isinstance(manifest, dict) or not isinstance(report, dict):
            continue
        run_id = run_dir.name
        chain = _dict(_read_json(run_dir / "chain.json", {}))
        selection = _dict(_read_json(run_dir / "measurements" / "selection.json", {}))
        resolution = _dict(selection.get("resolution"))
        identity = _coherent_identity(
            [
                ("report", _dict(report.get("target_identity"))),
                ("selection", _dict(resolution.get("target_identity"))),
                ("manifest", _dict(manifest.get("target"))),
            ]
        )
        mode = next(
            (_text(src.get("mode")) for src in (_dict(report.get("target_identity")), _dict(resolution.get("target_identity"))) if _text(src.get("mode"))),
            "",
        )
        scenario = _dict(manifest.get("scenario"))
        profile = _dict(manifest.get("profile"))
        measurement_profile = _dict(resolution.get("profile"))
        collector_digests: list[str] = []
        collector_missing: list[str] = []
        for item in resolution.get("resolved") or []:
            if not isinstance(item, dict) or item.get("enabled", True) is False:
                continue
            digest = _text(item.get("definition_digest"))
            if digest:
                collector_digests.append(digest)
            else:
                collector_id = _text(item.get("id")) or "unnamed"
                collector_digests.append(f"missing:{collector_id}")
                collector_missing.append(collector_id)
        evidence_state, evidence_identity = _evidence_state(run_id, manifest, chain)
        metrics = {str(name): _metric_cell(str(name), raw) for name, raw in _dict(report.get("measurements")).items()}
        records.append(
            {
                "run_id": run_id,
                "sources": [source],
                "manifest_hash": chain.get("manifest_hash"),
                "evidence_state": evidence_state,
                "evidence_identity": evidence_identity,
                "scenario_id": _text(scenario.get("id")) or _text(report.get("scenario")),
                "scenario_sha256": _text(scenario.get("sha256")),
                "scenario_behavior_sha256": scenario_behavior_sha256(run_dir, scenario),
                **identity,
                "mode": mode,
                "runtime": _text(manifest.get("runtime")),
                "profile_id": _text(profile.get("id")),
                "profile_sha256": _text(profile.get("sha256")),
                "measurement_profile_id": _text(measurement_profile.get("id")),
                "measurement_profile_digest": _text(measurement_profile.get("definition_digest")),
                "measurement_profile_source": _text(measurement_profile.get("source")),
                "collector_digests": sorted(collector_digests),
                "collector_missing": sorted(collector_missing),
                "report_schema_version": _text(report.get("schema_version")),
                "exit_status": _text(manifest.get("exit_status")),
                "verdict": _text(_dict(manifest.get("execution")).get("security_verdict")),
                "started_at": _text(manifest.get("started_at")),
                "ended_at": _text(manifest.get("ended_at")),
                "run_url": f"/operate/runs/{run_id}",
                "bundle_url": f"/runs/{run_id}/bundle",
                "metrics": metrics,
            }
        )
    return records


def dedupe_records(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """A8: identical, bound evidence identity merges; anything else with the
    same run id is a conflict and is excluded from aggregation."""
    by_id: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_id.setdefault(record["run_id"], []).append(record)
    kept: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for run_id in sorted(by_id):
        group = by_id[run_id]
        sources = sorted({s for r in group for s in r["sources"]})
        if len(group) == 1:
            kept.append(group[0])
            continue
        unbound = [r["evidence_state"] for r in group if r["evidence_state"] != "bound"]
        identities = {r["evidence_identity"] for r in group}
        if not unbound and len(identities) == 1:
            merged = dict(group[0])
            merged["sources"] = sources
            kept.append(merged)
            continue
        reason = f"evidence identity not bound ({unbound[0]})" if unbound else "manifest hashes differ"
        conflicts.append(
            {
                "run_id": run_id,
                "state": "conflict",
                "reason": reason,
                "manifest_hashes": sorted({str(r.get("manifest_hash")) for r in group if r.get("manifest_hash") is not None}),
                "sources": sources,
                "run_url": f"/operate/runs/{run_id}",
                "_records": group,
            }
        )
    return kept, conflicts


# --- fingerprints and groups ----------------------------------------------


def fingerprint_parts(record: dict[str, Any], metric_id: str, row: dict[str, Any]) -> dict[str, Any]:
    parts = {key: record.get(key, "") for key in FINGERPRINT_PARTS if key not in ("metric_id", "unit")}
    parts["metric_id"] = metric_id
    parts["unit"] = row.get("unit") or ""
    return {key: parts[key] for key in FINGERPRINT_PARTS}


def _explicit_none_profile(record: dict[str, Any]) -> bool:
    """The resolver records a scenario that selects no measurement profile as
    ``{"id": "none", "source": "explicit-none"}``; there is no definition to
    digest, and the enabled collectors' digests still pin what was measured."""
    return record.get("measurement_profile_id") == "none" and record.get("measurement_profile_source") == "explicit-none"


def provenance_missing(record: dict[str, Any], parts: dict[str, Any]) -> list[str]:
    optional = {"measurement_profile_digest"} if _explicit_none_profile(record) else set()
    missing = [key for key in REQUIRED_FINGERPRINT_PARTS if not parts.get(key) and key not in optional]
    if record.get("runtime") == "devnet":
        missing.extend(key for key in ("profile_id", "profile_sha256") if not parts.get(key))
    missing.extend(f"collector_digests:{collector}" for collector in record.get("collector_missing") or [])
    return missing


def compatibility_fingerprint(record: dict[str, Any], metric_id: str, row: dict[str, Any]) -> tuple[str, dict[str, Any], list[str]]:
    parts = fingerprint_parts(record, metric_id, row)
    missing = provenance_missing(record, parts)
    hashed = dict(parts)
    if missing:
        # Incomplete provenance can never establish compatibility with another
        # run: the fingerprint is pinned to this run so nothing aggregates.
        hashed["incomplete_run"] = record["run_id"]
    digest = hashlib.sha256(json.dumps(hashed, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return digest, parts, missing


def group_key(record: dict[str, Any], rollup: str = "exact") -> str:
    if rollup == "run":
        return f"run|{record['run_id']}"
    if record.get("provenance_state") != "complete":
        return f"incomplete|{record['run_id']}"
    return "|".join(record.get(key) or "" for key in GROUP_IDENTITY_FIELDS)


def _group_label(record: dict[str, Any], rollup: str) -> str:
    base = f"{record.get('implementation') or 'unknown'} {record.get('version') or 'unknown version'}".strip()
    if record.get("mode"):
        base += f" · {record['mode']}"
    if rollup == "run":
        return f"{base} · run {record['run_id']}"
    if rollup == "version":
        return base
    bits = [base]
    if record.get("source_revision"):
        bits.append(record["source_revision"][:12])
    if record.get("image_digest"):
        bits.append(record["image_digest"][:19])
    if record.get("identity_missing"):
        bits.append("identity incomplete (missing " + ", ".join(record["identity_missing"]) + ")")
    return " · ".join(bits)


# --- filters ---------------------------------------------------------------


def parse_comparison_filters(query: str) -> dict[str, str]:
    qs = parse_qs(query or "", keep_blank_values=True)
    filters = {key: (qs.get(key) or [""])[0][:256] for key in FILTER_KEYS}
    if filters["rollup"] not in ROLLUP_MODES:
        filters["rollup"] = "exact"
    return filters


def format_value(value: float | int | None) -> str:
    """Exact, locale-free presentation of a measurement value: integers keep
    thousands separators, values >= 1 keep up to three decimals, smaller values
    keep four significant digits. Never rounds a non-zero value to "0"."""
    number = _number(value)
    if number is None:
        return ""
    if number == int(number) and abs(number) < 1e15:
        return f"{int(number):,}"
    if abs(number) >= 1:
        text = f"{number:,.3f}"
    else:
        decimals = 3 - math.floor(math.log10(abs(number)))
        if decimals > 12:
            return f"{number:.4g}"
        text = f"{number:.{decimals}f}"
    return text.rstrip("0").rstrip(".")


def comparison_href(filters: dict[str, str], **overrides: str) -> str:
    """Route href that preserves every applied filter (in FILTER_KEYS order),
    with blanks and the default roll-up omitted."""
    merged = {key: (filters or {}).get(key, "") or "" for key in FILTER_KEYS}
    merged.update({key: value or "" for key, value in overrides.items() if key in FILTER_KEYS})
    params = [(key, merged[key]) for key in FILTER_KEYS if merged[key] and not (key == "rollup" and merged[key] == "exact")]
    return "/operate/measurements/compare" + ("?" + urlencode(params) if params else "")


_IMPL_LABELS = {"amaru": "Amaru", "cardano-node": "Cardano"}
_SCENARIO_NOISE = {"amaru", "cardano", "node", "client", "example", "stock", "experimental", "qualification", "instrumented", "scenario"}
_SCENARIO_WORDS = {"tap": "taps"}


def impl_label(implementation: str) -> str:
    """Short node name for dense rows: known implementations get their product
    name, anything else a title-cased fallback, never an empty string."""
    key = (implementation or "").strip().lower()
    if key in _IMPL_LABELS:
        return _IMPL_LABELS[key]
    return key.replace("-", " ").replace("_", " ").title() or "Unknown"


def scenario_label(scenario_id: str) -> str:
    """Concise human label derived from a scenario id (implementation names,
    dates, numbers and qualification noise dropped). The exact id is kept
    beside it wherever the label is shown."""
    tokens = [t for t in re.split(r"[-_./]+", (scenario_id or "").lower()) if t]
    kept = [
        _SCENARIO_WORDS.get(t, t)
        for t in tokens
        if t not in _SCENARIO_NOISE and not re.fullmatch(r"v?\d+(\.\d+)*", t)
    ]
    if not kept:
        return scenario_id
    label = " ".join(kept)
    return label[0].upper() + label[1:]


# Unit words shown abbreviated beside values; the full name travels as the title.
_UNIT_SHORT = {
    "operations": "ops", "operation": "op", "blocks": "bl", "block": "bl", "transactions": "tx", "transaction": "tx",
    "seconds": "s", "second": "s", "sec": "s", "milliseconds": "ms", "millisecond": "ms", "microseconds": "us",
    "microsecond": "us", "µs": "us", "nanoseconds": "ns", "nanosecond": "ns", "minutes": "min", "minute": "min",
    "bytes": "B", "byte": "B", "batches": "batch", "events": "evt", "event": "evt", "tasks": "task",
}
_UNIT_FULL = {
    "ops": "operations", "op": "operation", "bl": "blocks", "tx": "transactions", "s": "seconds", "ms": "milliseconds",
    "us": "microseconds", "ns": "nanoseconds", "min": "minutes", "B": "bytes", "batch": "batches", "evt": "events", "task": "tasks",
}


def unit_presentation(unit: str) -> dict[str, str]:
    """Compact unit text plus its full name: ``operations/s`` → ``ops/s`` /
    ``operations per second``. Unknown words pass through unchanged."""
    parts = [part.strip() for part in (unit or "").split("/") if part.strip()]
    shorts: list[str] = []
    labels: list[str] = []
    for index, part in enumerate(parts):
        short = _UNIT_SHORT.get(part.lower(), part)
        label = _UNIT_FULL.get(short, _UNIT_FULL.get(part.lower(), part))
        if index and label.endswith("s") and len(label) > 2:
            label = label[:-1]  # denominators read singular: "per second"
        shorts.append(short)
        labels.append(label)
    return {"short": "/".join(shorts), "label": " per ".join(labels)}


def _percent(value: float, low: float, high: float) -> float:
    return round((value - low) / (high - low) * 100.0, 2)


def _present_row(row: dict[str, Any], groups: list[dict[str, Any]]) -> None:
    """Attach the overview presentation to a row: one shared scale for every
    build's bar (zero always on the scale so a single, negative or observed-zero
    value stays readable), per-state counts, and one incompatibility summary
    for the whole metric instead of a reason repeated in every cell."""
    cells = [row["cells"][g["key"]] for g in groups]
    values = [c["value"] for c in cells if c["value"] is not None]
    low = min([0.0, *values])
    high = max([0.0, *values])
    if high == low:
        high = low + 1.0
    bars = []
    for group, cell in zip(groups, cells):
        value = cell["value"]
        bar = {
            "group_key": group["key"], "color": group["color"], "label": group["label"], "label_short": group["label_short"],
            "state": cell["state"], "value": value, "value_text": cell["value_text"],
            "start_percent": None, "end_percent": None, "marker_percent": None,
            "run_count": cell["run_count"], "run_total": cell["run_total"],
            "sample_count_total": cell["sample_count_total"], "delta_percent": cell["delta_percent"],
            "minimum_text": cell["minimum_text"], "maximum_text": cell["maximum_text"],
        }
        if value is not None:
            bar["start_percent"] = _percent(min(0.0, value), low, high)
            bar["end_percent"] = _percent(max(0.0, value), low, high)
            bar["marker_percent"] = _percent(value, low, high)
        bars.append(bar)
    row["plot"] = {"scale_min": low, "scale_max": high, "zero_percent": _percent(0.0, low, high), "bars": bars}
    counts: dict[str, int] = {}
    for cell in cells:
        counts[cell["state"]] = counts.get(cell["state"], 0) + 1
    row["state_counts"] = counts
    incompatible = [(g, c) for g, c in zip(groups, cells) if c["state"] == "incompatible"]
    if incompatible:
        fields = {field for _, c in incompatible for field in (c["incompatible_with"] or [])}
        row["incompatibility"] = {
            "reason": "unit" if any(c["reason"] == "unit" for _, c in incompatible) else "configuration",
            "fields": [k for k in FINGERPRINT_PARTS if k in fields],
            "group_keys": [g["key"] for g, _ in incompatible],
        }
    else:
        row["incompatibility"] = None


def picked_runs(filters: dict[str, str]) -> list[str]:
    return [run_id.strip() for run_id in (filters.get("runs") or "").split(",") if run_id.strip()]


def _record_matches(record: dict[str, Any], filters: dict[str, str]) -> bool:
    if filters.get("impl") and record.get("implementation") != filters["impl"]:
        return False
    if filters.get("version") and record.get("version") != filters["version"]:
        return False
    picks = picked_runs(filters)
    if picks:
        if record.get("run_id") not in picks:
            return False
    elif filters.get("scenario") and record.get("scenario_id") != filters["scenario"]:
        return False
    if filters.get("outcome") and filters["outcome"] not in (record.get("exit_status"), record.get("verdict")):
        return False
    if filters.get("since") and (record.get("ended_at") or "") < filters["since"]:
        return False
    if filters.get("until") and (record.get("ended_at") or "") > filters["until"]:
        return False
    return True


# --- aggregation -----------------------------------------------------------


def _empty_cell(state: str = "not-measured") -> dict[str, Any]:
    return {
        "state": state, "value": None, "minimum": None, "maximum": None, "run_count": 0, "run_total": 0,
        "sample_count_total": 0, "contributors": [], "reason": None, "incompatible_with": None,
        "alternatives": [], "delta_percent": None,
    }


def _aggregate(contributors: list[dict[str, Any]]) -> dict[str, Any]:
    numeric = [c for c in contributors if c["state"] in _NUMERIC_STATES]
    values = [c["value"] for c in numeric]
    cell = _empty_cell()
    cell.update(
        run_count=len(numeric),
        run_total=len(contributors),
        sample_count_total=sum(int(c.get("sample_count") or 0) for c in numeric),
        contributors=contributors,
    )
    if values:
        cell["value"] = float(statistics.median(values))
        cell["minimum"] = float(min(values))
        cell["maximum"] = float(max(values))
    if numeric and len(numeric) == len(contributors):
        cell["state"] = "observed-zero" if all(v == 0 for v in values) else "available"
    elif numeric:
        cell["state"] = "partial"
    else:
        cell["state"] = max((c["state"] for c in contributors), key=lambda s: _SEVERITY.get(s, 0))
    return cell


def _contributor(record: dict[str, Any], row: dict[str, Any], state: str, fingerprint: str) -> dict[str, Any]:
    return {
        "run_id": record["run_id"],
        "run_url": record["run_url"],
        "bundle_url": record["bundle_url"],
        "sources": list(record["sources"]),
        "evidence_state": record.get("evidence_state"),
        "state": state,
        "value": row.get("primary_value") if state in _NUMERIC_STATES else None,
        "sample_count": int(row.get("sample_count") or 0) if row.get("sample_count") is not None else None,
        "unit": row.get("unit") or "",
        "status": row.get("status"),
        "reason": row.get("reason") or None,
        "fingerprint": fingerprint,
    }


def build_measurement_comparison(
    records: list[dict[str, Any]],
    *,
    filters: dict[str, str] | None = None,
    catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    filters = {key: (filters or {}).get(key, "") or "" for key in FILTER_KEYS}
    rollup = filters["rollup"] if filters["rollup"] in ROLLUP_MODES else "exact"
    index = _catalog_index(catalog)
    deduped, conflicts = dedupe_records(records)

    picks = picked_runs(filters)

    option_sets: dict[str, set[str]] = {"implementations": set(), "versions": set(), "scenarios": set(), "categories": set(), "metrics": set()}
    for record in deduped:
        option_sets["implementations"].add(record["implementation"])
        option_sets["versions"].add(record["version"])
        option_sets["scenarios"].add(record["scenario_id"])
        for metric_id, row in record["metrics"].items():
            mapped = catalog_concept(index, record, metric_id, row) if picks else None
            option_sets["metrics"].add(mapped["id"] if mapped else metric_id)
            option_sets["categories"].add((mapped or describe_metric(index, record, metric_id, row))["category"])

    matching = [r for r in deduped if _record_matches(r, filters)]
    scenario_records: dict[str, list[dict[str, Any]]] = {}
    for r in deduped:
        if r["provenance_state"] != "incoherent" and _record_matches(r, {**filters, "scenario": ""}):
            scenario_records.setdefault(r["scenario_id"], []).append(r)
    scenario_links = []
    for sid, recs in sorted(scenario_records.items()):
        dates = sorted({r["ended_at"][:10] for r in recs if re.match(r"^\d{4}-\d{2}-\d{2}", r.get("ended_at") or "")})
        scenario_links.append(
            {
                "id": sid,
                "label": scenario_label(sid),
                "run_count": len(recs),
                "date_range": "" if not dates else (dates[0] if dates[0] == dates[-1] else f"{dates[0]} – {dates[-1]}"),
                "versions": sorted({f"{r['implementation']} {r['version']}".strip() for r in recs}),
                "href": comparison_href(filters, scenario=sid),
                "selected": filters["scenario"] == sid,
            }
        )
    focus_required = not filters["scenario"] and not picks and len(scenario_links) > 1
    excluded_runs = [
        {
            "run_id": r["run_id"], "state": r["provenance_state"], "reason": r["provenance_reason"],
            "run_url": r["run_url"], "bundle_url": r["bundle_url"], "sources": list(r["sources"]),
        }
        for r in matching
        if r["provenance_state"] == "incoherent"
    ]
    selected = [r for r in matching if r["provenance_state"] != "incoherent"]
    visible_conflicts = [
        {key: value for key, value in c.items() if key != "_records"}
        for c in conflicts
        if any(_record_matches(r, filters) for r in c["_records"])
    ]

    groups_by_key: dict[str, dict[str, Any]] = {}
    for record in selected:
        key = group_key(record, rollup)
        group = groups_by_key.setdefault(
            key,
            {
                "key": key,
                "implementation": record["implementation"],
                "version": record["version"],
                "mode": record.get("mode") or "",
                "source_revision": record["source_revision"],
                "image_digest": record["image_digest"],
                "identity_complete": record["provenance_state"] == "complete",
                "identity_missing": list(record["identity_missing"]),
                "identity_source": record["identity_source"],
                "impl_label": impl_label(record["implementation"]),
                "revision_short": (record["source_revision"] or "")[:12],
                "label": _group_label(record, rollup),
                "rollup_blocked": [],
                "run_ids": [],
                "run_count": 0,
                "metric_ids": set(),
            },
        )
        group["run_ids"].append(record["run_id"])
        group["run_count"] += 1
    groups = [groups_by_key[key] for key in sorted(groups_by_key)]
    by_version: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for group in groups:
        by_version.setdefault((group["implementation"], group["version"]), []).append(group)
    for position, group in enumerate(groups):
        group["color"] = f"mc-color-{position % GROUP_COLORS}"
        siblings = by_version[(group["implementation"], group["version"])]
        base = f"{group['implementation']} {group['version']}".strip()
        if group["mode"]:
            base += f" · {group['mode']}"
        same_mode = [s for s in siblings if s["mode"] == group["mode"]]
        group["label_short"] = base if len(same_mode) == 1 else f"{base} · build {group['source_revision'][:12] or group['run_ids'][0]}"
        if rollup == "version" and len(siblings) > 1:
            group["rollup_blocked"] = [
                field for field in ("source_revision", "image_digest")
                if len({s[field] for s in siblings}) > 1
            ] or ["identity"]
        group["run_ids"] = sorted(group["run_ids"])
    # Short labels must tell builds apart: singletons with incomplete identity
    # (never aggregated) are named by their run, identical sources by image.
    for stage in ("run", "image", "key"):
        counts: dict[str, int] = {}
        for group in groups:
            counts[group["label_short"]] = counts.get(group["label_short"], 0) + 1
        for group in groups:
            if counts[group["label_short"]] <= 1:
                continue
            if stage == "run" and (not group["identity_complete"] or rollup == "run"):
                group["label_short"] += f" · run {group['run_ids'][0]}"
            elif stage == "image" and group["image_digest"]:
                group["label_short"] += f" · image {group['image_digest'][-12:]}"
            elif stage == "key":
                group["label_short"] += f" · {group['key']}"

    rows_by_fp: dict[str, dict[str, Any]] = {}
    metric_rows_by_group: dict[tuple[str, str], list[str]] = {}
    for record in selected:
        gkey = group_key(record, rollup)
        for metric_id, row in record["metrics"].items():
            mapped = catalog_concept(index, record, metric_id, row) if picks else None
            if picks and mapped is None and not is_antithesis_record(record) and _matching_bindings(index, record, metric_id, row):
                # A companion or a non-primary alternate of a concept this record already contributes through.
                continue
            info = mapped or describe_metric(index, record, metric_id, row)
            display_id = mapped["id"] if mapped else metric_id
            if filters["category"] and info["category"] != filters["category"]:
                continue
            if filters["metric"] and filters["metric"] not in (metric_id, display_id):
                continue
            fp, parts, missing = compatibility_fingerprint(record, metric_id, row)
            row_key = mapped["key"] if mapped else fp
            entry = rows_by_fp.setdefault(
                row_key,
                {
                    "fingerprint": row_key,
                    "fingerprint_parts": {"concept": mapped["id"], "runtime": parts["runtime"], "unit": mapped["unit"]} if mapped else parts,
                    "provenance_complete": not missing,
                    "provenance_missing": missing,
                    "metric_id": display_id,
                    "title": info["title"],
                    "category": info["category"],
                    "category_label": next((c["label"] for c in index["categories"] if c["id"] == info["category"]), info["category"]),
                    "comparison_direction": info.get("comparison_direction") or "neutral",
                    "concept_grouped": bool(mapped),
                    "approximate": False,
                    "unit": mapped["unit"] if mapped else parts["unit"],
                    "scenario_id": parts["scenario_id"],
                    "runtime": parts["runtime"],
                    "mode": parts["mode"],
                    "measurement_profile_id": parts["measurement_profile_id"],
                    "collector_digests": list(parts["collector_digests"]),
                    "cells": {},
                    "_contributors": {},
                },
            )
            if mapped:
                entry["provenance_missing"] = sorted(set(entry["provenance_missing"]) | set(missing))
                entry["provenance_complete"] = not entry["provenance_missing"]
                entry["approximate"] = entry["approximate"] or mapped["approximate"]
            contributor = _contributor(record, row, cell_state(row), fp)
            contributor.update(
                metric_id=metric_id,
                raw_unit=_raw_unit(row),
                boundary=mapped["boundary"] if mapped else "",
                approximate=bool(mapped and mapped["approximate"]),
                source_concept=mapped["source_concept"] if mapped else None,
            )
            entry["_contributors"].setdefault(gkey, []).append(contributor)
            metric_rows_by_group.setdefault((gkey, display_id), []).append(row_key)
            groups_by_key[gkey]["metric_ids"].add(metric_id)

    rows = list(rows_by_fp.values())
    contributors_by_fp = {fp: row.pop("_contributors") for fp, row in rows_by_fp.items()}
    for row in rows:
        for group in groups:
            contributors = contributors_by_fp[row["fingerprint"]].get(group["key"])
            if contributors:
                cell = _aggregate(sorted(contributors, key=lambda c: c["run_id"]))
                if len({c["fingerprint"] for c in contributors}) > 1:
                    cell["mixed_provenance"] = True
                row["cells"][group["key"]] = cell
                continue
            cell = _empty_cell()
            alternatives = sorted({fp for fp in metric_rows_by_group.get((group["key"], row["metric_id"]), []) if fp != row["fingerprint"]})
            if alternatives:
                differing: set[str] = set()
                traced = []
                for fp in alternatives:
                    other = rows_by_fp[fp]
                    other_diff = [k for k in FINGERPRINT_PARTS if k != "metric_id" and other["fingerprint_parts"].get(k) != row["fingerprint_parts"].get(k)]
                    differing.update(other_diff)
                    for contributor in contributors_by_fp[fp].get(group["key"], []):
                        traced.append({**contributor, "incompatible_with": other_diff})
                ordered = [k for k in FINGERPRINT_PARTS if k in differing]
                cell.update(
                    state="incompatible",
                    reason="unit" if "unit" in differing else "configuration",
                    incompatible_with=ordered,
                    alternatives=alternatives,
                    contributors=sorted(traced, key=lambda c: c["run_id"]),
                    run_total=len(traced),
                )
            row["cells"][group["key"]] = cell

    rows.sort(key=lambda r: (index["order"].get(r["category"], 999), r["title"].lower(), r["scenario_id"], r["unit"], r["fingerprint"]))

    measured_metric_ids = {
        row["metric_id"]
        for row in rows
        if any(cell["value"] is not None for cell in row["cells"].values())
    }
    unmeasured_metric_ids = {row["metric_id"] for row in rows} - measured_metric_ids
    if filters["show_unmeasured"] != "1":
        rows = [row for row in rows if row["metric_id"] not in unmeasured_metric_ids]

    baseline = filters["baseline"] if filters["baseline"] in groups_by_key else ""
    if baseline:
        for row in rows:
            base_value = row["cells"][baseline]["value"]
            for cell in row["cells"].values():
                if cell["value"] is not None and base_value not in (None, 0):
                    cell["delta_percent"] = round((cell["value"] - base_value) / base_value * 100.0, 3)

    state_totals: dict[str, int] = {state: 0 for state in STATES}
    for row in rows:
        for cell in row["cells"].values():
            cell["value_text"] = format_value(cell["value"])
            cell["minimum_text"] = format_value(cell["minimum"])
            cell["maximum_text"] = format_value(cell["maximum"])
        unit = unit_presentation(row["unit"])
        row["unit_short"] = unit["short"]
        row["unit_label"] = unit["label"]
        _present_row(row, groups)
        for state, count in row["state_counts"].items():
            state_totals[state] = state_totals.get(state, 0) + count

    disagreements = []
    for row in rows:
        if row.get("approximate"):
            continue
        numeric = {key: cell for key, cell in row["cells"].items() if cell["value"] is not None}
        if len(numeric) < 2:
            continue
        values = [cell["value"] for cell in numeric.values()]
        low, high = min(values), max(values)
        spread = (high - low) / low if low > 0 else (1.0 if high > 0 else 0.0)
        if spread > DISAGREEMENT_SPREAD:
            disagreements.append(
                {
                    "metric_id": row["metric_id"], "title": row["title"], "unit": row["unit"], "scenario_id": row["scenario_id"],
                    "fingerprint": row["fingerprint"], "spread_percent": round(spread * 100.0, 1),
                    "group_values": {key: numeric[key]["value"] for key in sorted(numeric)},
                    "groups": {
                        key: {
                            "value": cell["value"],
                            "contributors": [
                                {"run_id": c["run_id"], "run_url": c["run_url"], "bundle_url": c["bundle_url"], "value": c["value"]}
                                for c in cell["contributors"] if c["state"] in _NUMERIC_STATES
                            ],
                        }
                        for key, cell in sorted(numeric.items())
                    },
                }
            )

    for group in groups:
        group["measured_metric_count"] = len(group["metric_ids"])
        group["metric_ids"] = sorted(group["metric_ids"])

    summary = {
        "run_count": len(selected),
        "implementation_count": len({g["implementation"] for g in groups}),
        "version_count": len({(g["implementation"], g["version"]) for g in groups}),
        "scenario_count": len({r["scenario_id"] for r in selected}),
        "group_count": len(groups),
        "complete_identity_count": sum(1 for g in groups if g["identity_complete"]),
        "incomplete_identity_count": sum(1 for g in groups if not g["identity_complete"]),
        "metric_count": len({r["metric_id"] for r in rows}),
        "excluded_run_count": len(excluded_runs),
        "conflict_count": len(visible_conflicts),
    }

    return {
        "aggregation_rule": AGGREGATION_RULE,
        "summary": summary,
        "scenario_links": scenario_links,
        "focus_required": focus_required,
        "href_all_scenarios": comparison_href(filters, scenario=""),
        "unmeasured_metric_count": len(unmeasured_metric_ids),
        "href_show_unmeasured": comparison_href(filters, show_unmeasured="1"),
        "state_totals": state_totals,
        "disagreement_spread_percent": DISAGREEMENT_SPREAD * 100,
        "states": list(STATES),
        "rollup": rollup,
        "rollup_modes": list(ROLLUP_MODES),
        "groups": groups,
        "rows": rows,
        "disagreements": disagreements,
        "conflicts": visible_conflicts,
        "excluded_runs": excluded_runs,
        "baseline": baseline,
        "filters": {
            **{key: sorted(values) for key, values in option_sets.items()},
            "category_labels": {c["id"]: c["label"] for c in index["categories"]},
            "applied": filters,
        },
        "record_count": len(deduped),
        "run_count": len(selected),
        "picked_run_count": len(selected) if picks else 0,
        "picked_scenario_count": len({r["scenario_id"] for r in selected}) if picks else 0,
    }
