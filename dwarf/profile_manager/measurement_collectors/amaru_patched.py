"""Collectors and calibration gates for the exact DWARF-patched Amaru target."""
from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

from profile_manager.measurement_correlation import correlate_measurement_events
from profile_manager.measurement_report import distribution_summary


AMARU_SOURCE_REVISION = "b159172f25a9c389f82f20bca4f15e3032791638"
AMARU_MEASUREMENT_PATCH_SHA256 = (
    "7ce3356d53535b22b82abf10166f9fa8ccfcd40b49bd8e298895ba1c027c0532"
)
DEFAULT_MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_SOURCE_PATHS = 16
PATCHED_MEASUREMENT_IDS = (
    "amaru-patched-protocol-decode",
    "amaru-patched-blockfetch-queues",
    "amaru-patched-txsubmission-residence",
)

_EVENT_KINDS = {
    "protocol_decode": "protocol-decode",
    "blockfetch_handler": "blockfetch-handler",
    "blockfetch_block_decode": "blockfetch-block-decode",
    "blockfetch_queue_residence": "blockfetch-queue-residence",
    "txsubmission_depth": "txsubmission-depth",
    "txsubmission_residence": "txsubmission-residence",
    "txsubmission_blocking_residence": "txsubmission-blocking-residence",
}


def _bounded_read(path: Path, *, max_bytes: int, start: int = 0) -> tuple[bytes, bool]:
    size = path.stat().st_size
    if start > size:
        start = 0
    available = max(0, size - start)
    with path.open("rb") as stream:
        stream.seek(start)
        data = stream.read(max_bytes)
    return data, available > len(data)


def _event_name(record: Mapping[str, Any]) -> str | None:
    span = record.get("span")
    if isinstance(span, dict) and isinstance(span.get("name"), str):
        return span["name"].lower()
    fields = record.get("fields")
    if isinstance(fields, dict) and isinstance(fields.get("message"), str):
        return fields["message"].split(".")[-1].lower()
    return None


def _is_measurement_record(record: Mapping[str, Any]) -> bool:
    fields = record.get("fields")
    target = record.get("target")
    message = fields.get("message") if isinstance(fields, dict) else None
    return target == "amaru::protocols::measurement" or (
        target == "amaru::protocols"
        and isinstance(message, str)
        and message.startswith("measurement.")
    )


def _normalize_event(record: dict[str, Any]) -> dict[str, Any] | None:
    fields = record.get("fields")
    target = record.get("target")
    if not _is_measurement_record(record):
        return None
    name = _event_name(record)
    if name not in _EVENT_KINDS or not isinstance(fields, dict):
        return None
    event: dict[str, Any] = {
        "kind": _EVENT_KINDS[name],
        "timestamp": record.get("timestamp"),
        "target": target,
        "name": name,
        "fields": fields,
    }
    for source, destination in (
        ("peer", "peer_id"),
        ("id", "tx_id"),
        ("request_id", "request_id"),
        ("message_type", "message_type"),
    ):
        if fields.get(source) is not None:
            event[destination] = str(fields[source])
    if record.get("id") is not None:
        event["span_id"] = str(record["id"])
    if record.get("parent_id") is not None:
        event["parent_span_id"] = str(record["parent_id"])
    return event


def _load_sources(
    sources: Iterable[tuple[str, bytes, bool]],
) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    rejected: Counter[str] = Counter()
    ignored_count = 0
    source_count = 0
    truncated_sources = []
    raw_sources = []
    for name, raw, truncated in sources:
        raw_sources.append((name, raw))
        if truncated:
            truncated_sources.append(name)
        for line in raw.splitlines():
            if not line.strip():
                continue
            source_count += 1
            try:
                record = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                rejected["invalid-json"] += 1
                continue
            normalized = _normalize_event(record) if isinstance(record, dict) else None
            if normalized is None:
                if isinstance(record, dict) and _is_measurement_record(record):
                    rejected["unsupported-event"] += 1
                else:
                    ignored_count += 1
            else:
                events.append(normalized)
    return {
        "schema_version": "v1",
        "source_revision": AMARU_SOURCE_REVISION,
        "patch_set_sha256": AMARU_MEASUREMENT_PATCH_SHA256,
        "source_record_count": source_count,
        "normalized_record_count": len(events),
        "ignored_record_count": ignored_count,
        "rejected_record_count": sum(rejected.values()),
        "rejection_reasons": dict(sorted(rejected.items())),
        "truncated_sources": sorted(truncated_sources),
        "events": events,
        "_raw_sources": raw_sources,
    }


def load_amaru_patched_telemetry(
    json_trace_paths: Iterable[str | Path],
    *,
    max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
) -> dict[str, Any]:
    paths = [Path(path) for path in json_trace_paths]
    if not paths or len(paths) > MAX_SOURCE_PATHS:
        raise ValueError(f"between 1 and {MAX_SOURCE_PATHS} trace paths are required")
    if max_source_bytes <= 0 or max_source_bytes > 64 * 1024 * 1024:
        raise ValueError("max_source_bytes must be within 1..67108864")
    sources = []
    for path in paths:
        raw, truncated = _bounded_read(path, max_bytes=max_source_bytes)
        sources.append((str(path), raw, truncated))
    result = _load_sources(sources)
    result.pop("_raw_sources", None)
    return result


def _samples(
    events: Iterable[dict[str, Any]], kind: str, field: str, *, unit: str = "us"
) -> dict[str, Any]:
    return distribution_summary(
        [
            {"value": event["fields"].get(field), "unit": unit}
            for event in events
            if event["kind"] == kind and event["fields"].get(field) is not None
        ],
        unit=unit,
    )


def _by_outcome(
    events: Iterable[dict[str, Any]],
    kind: str,
    field: str,
    outcome_field: str = "outcome",
    *,
    unit: str = "us",
) -> dict[str, dict[str, Any]]:
    matching = [
        event
        for event in events
        if event["kind"] == kind
        and event["fields"].get(field) is not None
        and isinstance(event["fields"].get(outcome_field), str)
        and event["fields"][outcome_field]
    ]
    outcomes = sorted({event["fields"][outcome_field] for event in matching})
    return {
        outcome: distribution_summary(
            [
                {"value": event["fields"][field], "unit": unit}
                for event in matching
                if event["fields"][outcome_field] == outcome
            ],
            unit=unit,
        )
        for outcome in outcomes
    }


def _build_measurements(
    measurement_id: str, events: list[dict[str, Any]]
) -> dict[str, Any]:
    if measurement_id == "amaru-patched-protocol-decode":
        return {
            "protocol_decode": _samples(events, "protocol-decode", "decode_micros"),
            "protocol_decode_by_decode_outcome": _by_outcome(
                events, "protocol-decode", "decode_micros", "decode_outcome"
            ),
            "protocol_total": _samples(events, "protocol-decode", "total_micros"),
            "protocol_total_by_state_outcome": _by_outcome(
                events, "protocol-decode", "total_micros", "state_outcome"
            ),
        }
    if measurement_id == "amaru-patched-blockfetch-queues":
        return {
            "handler": _samples(events, "blockfetch-handler", "handler_micros"),
            "handler_by_outcome": _by_outcome(
                events, "blockfetch-handler", "handler_micros"
            ),
            "block_decode": _samples(
                events, "blockfetch-block-decode", "elapsed_micros"
            ),
            "block_decode_by_outcome": _by_outcome(
                events, "blockfetch-block-decode", "elapsed_micros"
            ),
            "queue_residence": _samples(
                events, "blockfetch-queue-residence", "elapsed_micros"
            ),
            "queue_residence_by_outcome": _by_outcome(
                events, "blockfetch-queue-residence", "elapsed_micros"
            ),
            "inflight_depth": _samples(
                events, "blockfetch-handler", "inflight_depth", unit="requests"
            ),
            "pending_depth": _samples(
                events, "blockfetch-handler", "pending_depth", unit="requests"
            ),
        }
    if measurement_id == "amaru-patched-txsubmission-residence":
        return {
            "advertised_to_terminal": _samples(
                events, "txsubmission-residence", "advertised_to_terminal_micros"
            ),
            "advertised_to_terminal_by_outcome": _by_outcome(
                events, "txsubmission-residence", "advertised_to_terminal_micros"
            ),
            "advertised_to_body": _samples(
                events, "txsubmission-residence", "advertised_to_body_micros"
            ),
            "body_to_terminal": _samples(
                events, "txsubmission-residence", "body_to_terminal_micros"
            ),
            "pending_depth": _samples(
                events, "txsubmission-depth", "pending_depth", unit="transactions"
            ),
            "inflight_depth": _samples(
                events, "txsubmission-depth", "inflight_depth", unit="transactions"
            ),
            "unacked_depth": _samples(
                events, "txsubmission-depth", "unacked_depth", unit="transactions"
            ),
            "blocking_residence": _samples(
                events, "txsubmission-blocking-residence", "elapsed_micros"
            ),
            "blocking_residence_by_outcome": _by_outcome(
                events, "txsubmission-blocking-residence", "elapsed_micros"
            ),
        }
    return {}


def _validate_identity(identity: Mapping[str, Any]) -> None:
    if identity.get("implementation") != "amaru" or identity.get("mode") != "patched":
        raise ValueError("patched collector requires an Amaru target in patched mode")
    if identity.get("source_revision") != AMARU_SOURCE_REVISION:
        raise ValueError(
            "patched collector source revision does not match the exact audited Amaru source revision"
        )
    if identity.get("patch_set_sha256") != AMARU_MEASUREMENT_PATCH_SHA256:
        raise ValueError("patched collector patch-set identity does not match")
    digest = identity.get("image_digest")
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        raise ValueError("patched collector requires an immutable image digest")


class AmaruPatchedCollector:
    """Collect native patched-node events from runtime-owned trace paths."""

    def __init__(
        self,
        entry: dict[str, Any],
        *,
        json_trace_paths: Iterable[str | Path],
        target_identity: Mapping[str, Any],
        include_existing: bool = False,
        allow_missing_at_start: bool = False,
        max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
    ) -> None:
        self.entry = entry
        self.measurement_id = entry["id"]
        self.json_paths = [Path(path) for path in json_trace_paths]
        self.target_identity = json.loads(json.dumps(target_identity))
        self.include_existing = include_existing
        self.allow_missing_at_start = allow_missing_at_start
        self.max_source_bytes = max_source_bytes
        self._offsets: dict[Path, int] = {}
        self._markers: list[dict[str, Any]] = []

    def prepare(self, context) -> None:
        if self.measurement_id not in PATCHED_MEASUREMENT_IDS:
            raise ValueError(f"unsupported patched measurement: {self.measurement_id}")
        if not self.json_paths or len(self.json_paths) > MAX_SOURCE_PATHS:
            raise ValueError(f"between 1 and {MAX_SOURCE_PATHS} trace paths are required")
        if self.max_source_bytes <= 0 or self.max_source_bytes > 64 * 1024 * 1024:
            raise ValueError("max_source_bytes must be within 1..67108864")
        _validate_identity(self.target_identity)
        for path in self.json_paths:
            if not path.is_file() and not self.allow_missing_at_start:
                raise FileNotFoundError(f"Amaru patched telemetry source is unavailable: {path}")
        context.collector_dir.mkdir(parents=True, exist_ok=True)

    def start(self, context) -> None:
        self._offsets = {
            path: (
                0
                if self.include_existing or not path.exists()
                else path.stat().st_size
            )
            for path in self.json_paths
        }

    def on_marker(self, marker, context) -> None:
        self._markers.append(dict(marker))

    def stop(self, context) -> None:
        return None

    def finalize(self, context) -> dict[str, Any]:
        sources = []
        for path in self.json_paths:
            raw, truncated = _bounded_read(
                path, max_bytes=self.max_source_bytes, start=self._offsets.get(path, 0)
            )
            sources.append((str(path), raw, truncated))
        loaded = _load_sources(sources)
        raw_sources = loaded.pop("_raw_sources")
        raw_path = context.artifact_path("raw/amaru-json.ndjson")
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(b"\n".join(raw for _name, raw in raw_sources))
        normalized_path = context.artifact_path("normalized.ndjson")
        normalized_path.write_text(
            "".join(
                json.dumps(event, sort_keys=True) + "\n"
                for event in loaded["events"]
            ),
            encoding="utf-8",
        )
        correlations = correlate_measurement_events(loaded["events"])
        context.write_json("correlations.json", correlations)
        result = {
            "schema_version": "v1",
            "measurement_id": self.measurement_id,
            "source_revision": AMARU_SOURCE_REVISION,
            "patch_set_sha256": AMARU_MEASUREMENT_PATCH_SHA256,
            "target": self.target_identity,
            "evidence_class": "patched-node",
            "performance_authority": "requires-paired-calibration",
            "measurements": _build_measurements(
                self.measurement_id, loaded["events"]
            ),
            "export": {
                "source_record_count": loaded["source_record_count"],
                "normalized_record_count": loaded["normalized_record_count"],
                "ignored_record_count": loaded["ignored_record_count"],
                "rejected_record_count": loaded["rejected_record_count"],
                "rejection_reasons": loaded["rejection_reasons"],
                "truncated_sources": loaded["truncated_sources"],
                "incomplete": bool(
                    loaded["rejected_record_count"] or loaded["truncated_sources"]
                ),
            },
            "artifacts": {
                "raw_json": raw_path.relative_to(context.run_dir).as_posix(),
                "normalized": normalized_path.relative_to(context.run_dir).as_posix(),
                "correlations": context.artifact_path("correlations.json")
                .relative_to(context.run_dir)
                .as_posix(),
                "result": context.artifact_path("result.json")
                .relative_to(context.run_dir)
                .as_posix(),
            },
        }
        context.write_json("result.json", result)
        return result


def build_amaru_patched_factories(
    *,
    json_trace_paths: Iterable[str | Path],
    target_identity: Mapping[str, Any],
    include_existing: bool = False,
    allow_missing_at_start: bool = False,
    max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
) -> dict[str, Any]:
    paths = tuple(Path(path) for path in json_trace_paths)
    identity = json.loads(json.dumps(target_identity))

    def factory(entry: dict[str, Any]) -> AmaruPatchedCollector:
        return AmaruPatchedCollector(
            entry,
            json_trace_paths=paths,
            target_identity=identity,
            include_existing=include_existing,
            allow_missing_at_start=allow_missing_at_start,
            max_source_bytes=max_source_bytes,
        )

    return {measurement_id: factory for measurement_id in PATCHED_MEASUREMENT_IDS}


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    converted = float(value)
    return converted if math.isfinite(converted) else None


def paired_overhead_calibration(
    stock: Mapping[str, Any],
    patched: Mapping[str, Any],
    *,
    minimum_samples: int = 30,
    expected_implementation: str = "amaru",
    expected_source_revision: str = AMARU_SOURCE_REVISION,
    expected_patch_set_sha256: str = AMARU_MEASUREMENT_PATCH_SHA256,
) -> dict[str, Any]:
    """Compare common real-node metrics only after strict paired-run parity gates."""
    reasons: list[str] = []
    stock_target = stock.get("target") if isinstance(stock.get("target"), dict) else {}
    patched_target = patched.get("target") if isinstance(patched.get("target"), dict) else {}
    if stock_target.get("mode") != "stock" or patched_target.get("mode") != "patched":
        reasons.append("target modes must be stock and patched")
    identity_fields = ("implementation", "version", "source_revision")
    if any(stock_target.get(field) != patched_target.get(field) for field in identity_fields):
        reasons.append("stock and patched source identity must match")
    if patched_target.get("implementation") != expected_implementation:
        reasons.append("patched implementation is not the audited implementation")
    if patched_target.get("source_revision") != expected_source_revision:
        reasons.append("patched source revision is not the audited revision")
    if patched_target.get("patch_set_sha256") != expected_patch_set_sha256:
        reasons.append("patched patch-set identity is not the audited patch")
    if stock.get("workload_identity") != patched.get("workload_identity"):
        reasons.append("paired workload identity must match exactly")
    parity_fields = (
        ("runner", "runner identity"),
        ("timing_policy", "timing policy"),
        ("hardware", "hardware identity"),
    )
    for field, label in parity_fields:
        left = stock.get(field)
        right = patched.get(field)
        if (
            not isinstance(left, Mapping)
            or not left
            or not isinstance(right, Mapping)
            or not right
            or left != right
        ):
            reasons.append(f"{label} must match exactly")
    stock_attempts = stock.get("attempts")
    patched_attempts = patched.get("attempts")
    if (
        not isinstance(stock_attempts, Mapping)
        or not isinstance(patched_attempts, Mapping)
        or stock_attempts.get("total") != patched_attempts.get("total")
        or stock_attempts.get("outcomes") != patched_attempts.get("outcomes")
    ):
        reasons.append("terminal outcome counts must match exactly")

    stock_metrics = stock.get("measurements") if isinstance(stock.get("measurements"), dict) else {}
    patched_metrics = patched.get("measurements") if isinstance(patched.get("measurements"), dict) else {}
    common = sorted(set(stock_metrics) & set(patched_metrics))
    if not common:
        reasons.append("no common measurement exists for calibration")

    metrics: dict[str, Any] = {}
    for name in common:
        left = stock_metrics[name]
        right = patched_metrics[name]
        if not isinstance(left, dict) or not isinstance(right, dict):
            continue
        left_count = left.get("sample_count")
        right_count = right.get("sample_count")
        if (
            not isinstance(left_count, int)
            or not isinstance(right_count, int)
            or left_count < minimum_samples
            or right_count < minimum_samples
        ):
            reasons.append(
                f"minimum sample count {minimum_samples} not met for {name}"
            )
            continue
        if left.get("unit") != right.get("unit"):
            reasons.append(f"measurement unit mismatch for {name}")
            continue
        row: dict[str, Any] = {
            "unit": left.get("unit"),
            "stock_sample_count": left_count,
            "patched_sample_count": right_count,
        }
        valid = True
        for statistic in ("mean", "median", "p95", "p99"):
            baseline = _finite_number(left.get(statistic))
            instrumented = _finite_number(right.get(statistic))
            if baseline is None or instrumented is None or baseline == 0:
                valid = False
                reasons.append(f"finite non-zero {statistic} is required for {name}")
                break
            row[f"stock_{statistic}"] = baseline
            row[f"patched_{statistic}"] = instrumented
            row[f"{statistic}_percent_delta"] = round(
                ((instrumented - baseline) / baseline) * 100, 9
            )
        if valid:
            metrics[name] = row

    available = not reasons and bool(metrics)
    return {
        "schema_version": "v1",
        "status": "available" if available else "unavailable",
        "performance_authority": (
            "common-metric-calibrated" if available else "not-calibrated"
        ),
        "calibration_scope": sorted(metrics) if available else [],
        "claim_boundary": (
            "Only the listed common externally observed metrics are calibrated; "
            "patched internal measurements require a surface-specific paired workload."
        ),
        "minimum_samples": minimum_samples,
        "runner": stock.get("runner") if available else None,
        "timing_policy": stock.get("timing_policy") if available else None,
        "hardware": stock.get("hardware") if available else None,
        "attempts": stock.get("attempts") if available else None,
        "workload_identity": stock.get("workload_identity") if available else None,
        "metrics": metrics if available else {},
        "reasons": reasons,
    }
