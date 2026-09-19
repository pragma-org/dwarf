"""Collectors for DWARF's exact revision-locked Cardano measurement node."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

from profile_manager.measurement_correlation import correlate_measurement_events
from profile_manager.measurement_report import distribution_summary
from profile_manager.measurement_collectors.amaru_patched import (
    paired_overhead_calibration,
)


CARDANO_SOURCE_REVISION = "fef83fed01d7926f3de83b3b917be5a4a48768b5"
CARDANO_MEASUREMENT_PATCH_SHA256 = (
    "7a948067c6b957b277400675cf95e32864ed8d92cd130fbadb673775249b5cc1"
)
PATCHED_MEASUREMENT_IDS = (
    "cardano-patched-protocol-decode",
    "cardano-patched-ledger-plutus-stages",
)
DEFAULT_MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_SOURCE_PATHS = 16
_OUTCOMES = {
    "accepted", "rejected", "completed", "decode_failure", "size_limit", "timeout"
}


def paired_cardano_overhead_calibration(
    stock: Mapping[str, Any],
    patched: Mapping[str, Any],
    *,
    minimum_samples: int = 30,
) -> dict[str, Any]:
    return paired_overhead_calibration(
        stock,
        patched,
        minimum_samples=minimum_samples,
        expected_implementation="cardano-node",
        expected_source_revision=CARDANO_SOURCE_REVISION,
        expected_patch_set_sha256=CARDANO_MEASUREMENT_PATCH_SHA256,
    )


def _bounded_read(path: Path, *, max_bytes: int, start: int = 0) -> tuple[bytes, bool]:
    size = path.stat().st_size
    if start > size:
        start = 0
    available = max(0, size - start)
    with path.open("rb") as stream:
        stream.seek(start)
        data = stream.read(max_bytes)
    return data, available > len(data)


def _normalize(record: Mapping[str, Any]) -> dict[str, Any] | None:
    if record.get("target") != "cardano-node::measurement":
        return None
    event_name = record.get("event")
    if event_name not in {"protocol_receive_decode", "ledger_stage"}:
        return None
    if (
        event_name == "protocol_receive_decode"
        and record.get("boundary") != "receive-plus-incremental-decode"
    ):
        return None
    duration = record.get("duration_us")
    ended = record.get("ended_monotonic_ns")
    outcome = record.get("outcome")
    if (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or duration < 0
        or isinstance(ended, bool)
        or not isinstance(ended, int)
        or ended < 0
        or outcome not in _OUTCOMES
    ):
        raise ValueError("invalid-measurement-fields")
    event = {
        "kind": (
            "protocol-receive-decode"
            if event_name == "protocol_receive_decode"
            else "ledger-stage"
        ),
        "target": record["target"],
        "outcome": outcome,
        "duration_us": duration,
        "ended_monotonic_ns": ended,
    }
    if event_name == "protocol_receive_decode":
        event.update({
            "protocol": str(record.get("protocol") or "unknown"),
            "state": str(record.get("state") or "unknown"),
            "boundary": record["boundary"],
        })
    else:
        stage = record.get("stage")
        if stage not in {"block-application", "epoch-transition", "plutus-vm"}:
            raise ValueError("invalid-measurement-fields")
        event["stage"] = stage
    return event


def _load_sources(sources: Iterable[tuple[str, bytes, bool]]) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    rejected: Counter[str] = Counter()
    ignored = 0
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
            if not isinstance(record, dict):
                ignored += 1
                continue
            try:
                normalized = _normalize(record)
            except ValueError as error:
                rejected[str(error)] += 1
                continue
            if normalized is None:
                ignored += 1
            else:
                events.append(normalized)
    return {
        "schema_version": "v1",
        "source_revision": CARDANO_SOURCE_REVISION,
        "patch_set_sha256": CARDANO_MEASUREMENT_PATCH_SHA256,
        "source_record_count": source_count,
        "normalized_record_count": len(events),
        "ignored_record_count": ignored,
        "rejected_record_count": sum(rejected.values()),
        "rejection_reasons": dict(sorted(rejected.items())),
        "truncated_sources": sorted(truncated_sources),
        "events": events,
        "_raw_sources": raw_sources,
    }


def load_cardano_patched_telemetry(
    json_trace_paths: Iterable[str | Path], *, max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES
) -> dict[str, Any]:
    paths = [Path(path) for path in json_trace_paths]
    if not paths or len(paths) > MAX_SOURCE_PATHS:
        raise ValueError(f"between 1 and {MAX_SOURCE_PATHS} trace paths are required")
    if max_source_bytes <= 0 or max_source_bytes > 64 * 1024 * 1024:
        raise ValueError("max_source_bytes must be within 1..67108864")
    loaded = _load_sources(
        (str(path), *_bounded_read(path, max_bytes=max_source_bytes)) for path in paths
    )
    loaded.pop("_raw_sources", None)
    return loaded


def _distribution(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    return distribution_summary(
        ({"value": event["duration_us"], "unit": "us"} for event in events),
        unit="us",
    )


def _by_outcome(events: list[dict[str, Any]]) -> dict[str, Any]:
    outcomes = sorted({event["outcome"] for event in events})
    return {
            outcome: _distribution(
                event for event in events if event["outcome"] == outcome
            )
            for outcome in outcomes
    }


def _measurements(
    measurement_id: str, events: list[dict[str, Any]]
) -> dict[str, Any]:
    if measurement_id == "cardano-patched-protocol-decode":
        selected = [event for event in events if event["kind"] == "protocol-receive-decode"]
        return {
            "protocol_receive_decode": _distribution(selected),
            "protocol_receive_decode_by_outcome": _by_outcome(selected),
        }
    result = {}
    for stage, key in (
        ("block-application", "block_application"),
        ("epoch-transition", "epoch_transition"),
        ("plutus-vm", "plutus_vm"),
    ):
        selected = [event for event in events if event.get("stage") == stage]
        result[key] = _distribution(selected)
        result[f"{key}_by_outcome"] = _by_outcome(selected)
    return result


def _validate_identity(identity: Mapping[str, Any]) -> None:
    if identity.get("implementation") != "cardano-node" or identity.get("mode") != "patched":
        raise ValueError("patched collector requires a Cardano-node target in patched mode")
    if identity.get("source_revision") != CARDANO_SOURCE_REVISION:
        raise ValueError("patched collector source revision does not match")
    if identity.get("patch_set_sha256") != CARDANO_MEASUREMENT_PATCH_SHA256:
        raise ValueError("patched collector patch-set identity does not match")
    digest = identity.get("image_digest")
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        raise ValueError("patched collector requires an immutable image digest")


class CardanoPatchedCollector:
    def __init__(
        self,
        entry: dict[str, Any],
        *,
        json_trace_paths: Iterable[str | Path],
        target_identity: Mapping[str, Any],
        include_existing: bool = False,
        max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
    ) -> None:
        self.entry = entry
        self.measurement_id = entry["id"]
        self.json_paths = [Path(path) for path in json_trace_paths]
        self.target_identity = json.loads(json.dumps(target_identity))
        self.include_existing = include_existing
        self.max_source_bytes = max_source_bytes
        self._offsets: dict[Path, int] = {}

    def prepare(self, context) -> None:
        if self.measurement_id not in PATCHED_MEASUREMENT_IDS:
            raise ValueError(f"unsupported patched measurement: {self.measurement_id}")
        _validate_identity(self.target_identity)
        if not self.json_paths or len(self.json_paths) > MAX_SOURCE_PATHS:
            raise ValueError(f"between 1 and {MAX_SOURCE_PATHS} trace paths are required")
        for path in self.json_paths:
            if not path.is_file():
                raise FileNotFoundError(f"Cardano patched telemetry source is unavailable: {path}")
        context.collector_dir.mkdir(parents=True, exist_ok=True)

    def start(self, context) -> None:
        self._offsets = {
            path: 0 if self.include_existing else path.stat().st_size
            for path in self.json_paths
        }

    def on_marker(self, marker, context) -> None:
        return None

    def stop(self, context) -> None:
        return None

    def finalize(self, context) -> dict[str, Any]:
        loaded = _load_sources(
            (
                str(path),
                *_bounded_read(
                    path,
                    max_bytes=self.max_source_bytes,
                    start=self._offsets.get(path, 0),
                ),
            )
            for path in self.json_paths
        )
        raw_sources = loaded.pop("_raw_sources")
        raw_path = context.artifact_path("raw/cardano-measurement.ndjson")
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(b"\n".join(raw for _name, raw in raw_sources))
        normalized_path = context.artifact_path("normalized.ndjson")
        normalized_path.write_text(
            "".join(json.dumps(event, sort_keys=True) + "\n" for event in loaded["events"]),
            encoding="utf-8",
        )
        correlations = correlate_measurement_events(loaded["events"])
        context.write_json("correlations.json", correlations)
        result = {
            "schema_version": "v1",
            "measurement_id": self.measurement_id,
            "source_revision": CARDANO_SOURCE_REVISION,
            "patch_set_sha256": CARDANO_MEASUREMENT_PATCH_SHA256,
            "target": self.target_identity,
            "evidence_class": "patched-node",
            "performance_authority": "requires-paired-calibration",
            "measurements": _measurements(self.measurement_id, loaded["events"]),
            "export": {
                key: loaded[key]
                for key in (
                    "source_record_count", "normalized_record_count",
                    "ignored_record_count", "rejected_record_count",
                    "rejection_reasons", "truncated_sources",
                )
            },
            "artifacts": {
                "raw_json": raw_path.relative_to(context.run_dir).as_posix(),
                "normalized": normalized_path.relative_to(context.run_dir).as_posix(),
                "correlations": context.artifact_path("correlations.json").relative_to(context.run_dir).as_posix(),
                "result": context.artifact_path("result.json").relative_to(context.run_dir).as_posix(),
            },
        }
        result["export"]["incomplete"] = bool(
            loaded["rejected_record_count"] or loaded["truncated_sources"]
        )
        context.write_json("result.json", result)
        return result


def build_cardano_patched_factories(
    *,
    json_trace_paths: Iterable[str | Path],
    target_identity: Mapping[str, Any],
    include_existing: bool = False,
    max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
) -> dict[str, Any]:
    paths = tuple(Path(path) for path in json_trace_paths)
    identity = json.loads(json.dumps(target_identity))

    def factory(entry: dict[str, Any]) -> CardanoPatchedCollector:
        return CardanoPatchedCollector(
            entry,
            json_trace_paths=paths,
            target_identity=identity,
            include_existing=include_existing,
            max_source_bytes=max_source_bytes,
        )

    return {measurement_id: factory for measurement_id in PATCHED_MEASUREMENT_IDS}
