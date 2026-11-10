"""Bounded collectors for Cardano-node 11.1.2 machine-formatted traces."""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from profile_manager.measurement_collectors import withhold_without_source
from profile_manager.measurement_correlation import correlate_measurement_events
from profile_manager.measurement_report import distribution_summary


CARDANO_SOURCE_REVISION = "fef83fed01d7926f3de83b3b917be5a4a48768b5"
DEFAULT_MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_SOURCE_PATHS = 16
STOCK_MEASUREMENT_IDS = (
    "cardano-stock-chain-lifecycle",
    "cardano-stock-blockfetch",
    "cardano-stock-txsubmission-mempool",
    "cardano-stock-ledger-block-epoch",
    "cardano-stock-plutus-execution",
    "cardano-stock-network",
)


def _bounded_read(path: Path, *, start: int, max_bytes: int) -> tuple[bytes, bool]:
    size = path.stat().st_size
    if start > size:
        start = 0
    available = max(0, size - start)
    with path.open("rb") as stream:
        stream.seek(start)
        body = stream.read(max_bytes)
    return body, available > len(body)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _tx_id(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return None
    for key in ("txid", "txId", "id", "hash"):
        if value.get(key) is not None:
            return str(value[key])
    return None


def _block_hash(data: dict[str, Any]) -> str | None:
    for key in ("tipBlockHash", "block", "hash"):
        value = data.get(key)
        if isinstance(value, str) and value not in {"Genesis", "genesis"}:
            return value
    for key in ("newtip", "tip"):
        value = data.get(key)
        if isinstance(value, dict):
            for nested in ("hash", "block"):
                if value.get(nested) is not None:
                    return str(value[nested])
    return None


def _normalize_record(record: dict[str, Any]) -> dict[str, Any] | None:
    namespace = record.get("ns")
    data = record.get("data")
    if not isinstance(namespace, str) or not isinstance(data, dict):
        return None
    kind = str(data.get("kind") or "")
    normalized_kind = None
    if kind == "DownloadedHeader":
        normalized_kind = "chainsync-downloaded-header"
    elif kind == "RolledBack":
        normalized_kind = "chainsync-rolled-back"
    elif kind == "ValidatedHeader":
        normalized_kind = "chainsync-validated-header"
    elif kind == "ChainSyncServer.Update":
        normalized_kind = "chainsync-server-update"
    elif kind == "AddedToCurrentChain":
        normalized_kind = "chain-adopted"
    elif kind == "TraceAddBlockEvent.SwitchedToAFork":
        normalized_kind = "chain-fork-switch"
    elif kind == "CompletedBlockFetch":
        normalized_kind = "blockfetch-completed"
    elif kind == "BlockFetchServer":
        normalized_kind = "blockfetch-served"
    elif kind in {"AddedFetchRequest", "SendFetchRequest", "StartedFetchBatch", "CompletedFetchBatch", "RejectedFetchBatch", "ClientTerminating"}:
        normalized_kind = "blockfetch-" + kind.lower()
    elif kind == "TraceMempoolAttemptingAdd":
        normalized_kind = "mempool-attempt"
    elif kind == "TraceMempoolAddedTx":
        normalized_kind = "mempool-accepted"
    elif kind == "TraceMempoolRejectedTx":
        normalized_kind = "mempool-rejected"
    elif kind in {"TraceMempoolRemoveTxs", "TraceMempoolManuallyRemovedTxs"}:
        normalized_kind = "mempool-removed"
    elif kind == "TraceMempoolSynced":
        normalized_kind = "mempool-synced"
    elif kind == "AddSample":
        normalized_kind = "keepalive-sample"
    elif kind == "TraceAdoptedBlock":
        normalized_kind = "forge-adopted-block"
    elif kind in {"TraceDidntAdoptBlock", "TraceForgedInvalidBlock"}:
        normalized_kind = "forge-rejected-block"
    elif kind == "LedgerMetrics":
        normalized_kind = "ledger-metrics"
    elif kind == "ResourceStats" or namespace == "Resources":
        normalized_kind = "resource-stats"
    elif namespace.startswith("TxSubmission."):
        normalized_kind = "txsubmission-event"
    elif namespace == "KeepAlive.Remote.Send.KeepAlive":
        normalized_kind = "keepalive-request"
    elif namespace == "KeepAlive.Remote.Receive.KeepAliveResponse":
        normalized_kind = "keepalive-response"
    if normalized_kind is None:
        return None

    event: dict[str, Any] = {
        "kind": normalized_kind,
        "timestamp": record.get("at"),
        "namespace": namespace,
        "node": record.get("host"),
        "thread": record.get("thread"),
        "fields": data,
    }
    transaction_id = _tx_id(data.get("tx"))
    if transaction_id is not None:
        event["tx_id"] = transaction_id
    block_hash = _block_hash(data)
    if block_hash is not None:
        event["block_hash"] = block_hash
    peer = data.get("peer") or data.get("address")
    if isinstance(peer, dict) and peer.get("connectionId") is not None:
        event["peer_id"] = str(peer["connectionId"])
    elif peer is not None:
        event["peer_id"] = str(peer)
    slot = data.get("slot")
    if slot is None and isinstance(data.get("newtip"), dict):
        slot = data["newtip"].get("slot")
    if slot is not None:
        event["slot"] = slot
    if normalized_kind == "blockfetch-completed":
        delay = _number(data.get("delay"))
        if delay is not None and delay >= 0:
            event["duration_micros"] = delay * 1_000_000
            event["timing_source"] = "node-emitted-blockfetch-delay"
        size = _number(data.get("size"))
        if size is not None:
            event["size_bytes"] = size
    elif normalized_kind == "keepalive-sample":
        rtt = _number(data.get("rtt"))
        if rtt is not None and rtt >= 0:
            event["duration_micros"] = rtt * 1_000_000
            event["timing_source"] = "node-emitted-keepalive-rtt"
    elif normalized_kind == "mempool-synced":
        enclosing = data.get("enclosingTime")
        # cardano-node 11.1.x traces an enclosing-time pair: {"edge": "Starting"}, then
        # {"edge": <elapsed seconds>}. Only the second edge is a sample.
        if isinstance(enclosing, dict):
            enclosing = enclosing.get("edge")
        duration = _number(enclosing)
        if duration is not None and duration >= 0:
            event["duration_micros"] = duration * 1_000_000
            event["timing_source"] = "node-emitted-mempool-sync-duration"
    mempool = data.get("mempoolSize")
    if isinstance(mempool, dict):
        for source, destination in (("numTxs", "mempool_tx_count"), ("bytes", "mempool_bytes"), ("numBytes", "mempool_bytes")):
            number = _number(mempool.get(source))
            if number is not None:
                event[destination] = number
    return event


_RUNTIME_TIMESTAMP = re.compile(rb"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z (?=\{)")


def _strip_runtime_timestamp(line: bytes) -> bytes:
    """Drop the `docker logs -t` prefix in front of a JSON trace record, if present."""
    return _RUNTIME_TIMESTAMP.sub(b"", line, count=1)


def _load_cardano_trace_bytes(
    sources: Iterable[tuple[str, bytes, bool]],
) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    ignored = Counter()
    rejected = Counter()
    seen: set[str] = set()
    namespaces: set[str] = set()
    duplicate_count = 0
    source_count = 0
    truncated_sources: list[str] = []
    raw_sources: list[tuple[str, bytes]] = []
    for name, body, truncated in sources:
        raw_sources.append((name, body))
        if truncated:
            truncated_sources.append(name)
        for line in body.splitlines():
            if not line.strip():
                continue
            source_count += 1
            line = _strip_runtime_timestamp(line)
            try:
                record = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                rejected["invalid-json"] += 1
                continue
            if not isinstance(record, dict):
                rejected["non-object"] += 1
                continue
            if isinstance(record.get("ns"), str):
                namespaces.add(record["ns"])
            normalized = _normalize_record(record)
            if normalized is None:
                ignored["unsupported-event"] += 1
                continue
            fingerprint = json.dumps(record, sort_keys=True, separators=(",", ":"))
            if fingerprint in seen:
                duplicate_count += 1
                continue
            seen.add(fingerprint)
            events.append(normalized)
    return {
        "events": events,
        "source_record_count": source_count,
        "normalized_record_count": len(events),
        "ignored_record_count": sum(ignored.values()),
        "ignored_reasons": dict(ignored),
        "rejected_record_count": sum(rejected.values()),
        "rejection_reasons": dict(rejected),
        "duplicate_record_count": duplicate_count,
        "truncated_sources": truncated_sources,
        "observed_namespaces": sorted(namespaces),
        "_raw_sources": raw_sources,
    }


def _unavailable(unit: str, reason: str) -> dict[str, Any]:
    return {"status": "unavailable", "unit": unit, "value": None, "reason": reason}


# The trace namespaces whose records a counter counts. A zero is a reading only when
# the window holds at least one record of one of them: otherwise the namespace was not
# enabled on the node (or the node was silent there) and the zero measures nothing.
_COUNTER_NAMESPACES = {
    "chain_events": ("ChainSync.", "ChainDB.AddBlockEvent.", "Forge.Loop."),
    "adopted_blocks": ("ChainDB.AddBlockEvent.", "Forge.Loop."),
    "rollbacks": ("ChainSync.Client.", "ChainDB.AddBlockEvent."),
    "blockfetch_bytes": ("BlockFetch.Client.",),
    "served_blocks": ("BlockFetch.Server.",),
    "mempool_accepted": ("Mempool.",),
    "mempool_rejected": ("Mempool.",),
    "transaction_outcomes": ("Mempool.",),
    "ledger_samples": ("LedgerMetrics",),
    "protocol_events": ("ChainSync.ServerHeader.", "BlockFetch.", "TxSubmission.", "KeepAlive."),
}


# The namespaces whose records carry each distribution's samples.
_DISTRIBUTION_NAMESPACES = {
    "blockfetch_duration": ("BlockFetch.Client.",),
    "keepalive_rtt": ("KeepAlive.",),
    "mempool_tx_count": ("Mempool.",),
    "mempool_bytes": ("Mempool.",),
    "mempool_sync_duration": ("Mempool.",),
}


def _namespace_names(prefixes: tuple[str, ...]) -> str:
    return ", ".join(prefix.rstrip(".") for prefix in prefixes)


def withhold_untraced_zero_counts(
    measurements: dict[str, Any], observed_namespaces: Iterable[str]
) -> dict[str, Any]:
    observed = tuple(observed_namespaces)
    result = dict(measurements)
    for name, prefixes in _DISTRIBUTION_NAMESPACES.items():
        metric = result.get(name)
        if not isinstance(metric, dict) or metric.get("status") != "unavailable" or metric.get("reason"):
            continue
        if any(namespace.startswith(prefixes) for namespace in observed):
            reason = f"{_namespace_names(prefixes)} records were traced but none carried a sample"
        else:
            reason = (f"the node traced no {_namespace_names(prefixes)} record in this window "
                      "(namespace not enabled, or silent)")
        result[name] = {**metric, "reason": reason}
    for name, prefixes in _COUNTER_NAMESPACES.items():
        metric = result.get(name)
        if not isinstance(metric, dict) or metric.get("status") != "available" or metric.get("value") != 0:
            continue
        if any(namespace.startswith(prefixes) for namespace in observed):
            continue
        result[name] = _unavailable(
            str(metric.get("unit") or "events"),
            "the node traced no " + _namespace_names(prefixes)
            + " record in this window (namespace not enabled, or silent); a zero would not be a reading",
        )
    return result


def _count(
    events: list[dict[str, Any]], kinds: set[str], unit: str = "events", *, offered_count: int | None = None
) -> dict[str, Any]:
    value = sum(event["kind"] in kinds for event in events)
    metric = {"status": "available", "unit": unit, "value": value}
    if offered_count is not None:
        # What the counter could have counted: positive evidence for a zero.
        metric["offered_count"] = offered_count
    return metric


def _mempool_offered_count(events: list[dict[str, Any]]) -> int:
    """Transactions the mempool took a decision on: distinct ids across attempt/accept/reject events."""
    kinds = {"mempool-attempt", "mempool-accepted", "mempool-rejected"}
    seen = {event.get("tx_id") for event in events if event["kind"] in kinds and event.get("tx_id")}
    if seen:
        return len(seen)
    return sum(event["kind"] in {"mempool-accepted", "mempool-rejected"} for event in events)


def _distribution(events: list[dict[str, Any]], *, kinds: set[str], field: str, unit: str) -> dict[str, Any]:
    return distribution_summary(
        [
            {"value": event[field], "unit": unit}
            for event in events
            if event["kind"] in kinds and event.get(field) is not None
        ],
        unit=unit,
    )


def _timestamp_micros(value: Any) -> int | None:
    """Parse an RFC 3339 trace timestamp (nanosecond precision) to integer microseconds."""
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    from datetime import datetime, timezone

    body, _, fraction = value[:-1].partition(".")
    try:
        base = datetime.strptime(body, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    micros = int((fraction + "000000")[:6]) if fraction.isdigit() or not fraction else None
    if micros is None:
        return None
    return int(base.timestamp()) * 1_000_000 + micros


def _keepalive_pairs(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pair each sent KeepAlive with the next KeepAliveResponse on the same connection.

    cardano-node 11.1.x's new tracer exposes the keepalive mini-protocol
    messages but not the client's AddSample round-trip sample, so the round
    trip is derived from the send/receive trace timestamps per connection.
    """
    pending: dict[str, int] = {}
    samples = []
    for event in events:
        peer = event.get("peer_id")
        sent_at = _timestamp_micros(event.get("timestamp"))
        if not isinstance(peer, str) or sent_at is None:
            continue
        if event["kind"] == "keepalive-request":
            pending[peer] = sent_at
        elif event["kind"] == "keepalive-response":
            started = pending.pop(peer, None)
            if started is not None and sent_at >= started:
                samples.append({"value": float(sent_at - started), "unit": "us", "peer_id": peer})
    return samples


def _build_measurements(measurement_id: str, events: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, str]]:
    if measurement_id == "cardano-stock-chain-lifecycle":
        chain_events = _count(events, {"chainsync-downloaded-header", "chainsync-rolled-back", "chainsync-validated-header", "chainsync-server-update", "chain-adopted", "chain-fork-switch", "forge-adopted-block"})
        return {
            "chain_events": chain_events,
            "adopted_blocks": _count(events, {"chain-adopted", "forge-adopted-block"}, "blocks"),
            # Zero rollbacks among N chain events is a reading: the chain events are its evidence.
            "rollbacks": _count(events, {"chainsync-rolled-back", "chain-fork-switch"}, offered_count=chain_events["value"]),
        }, {"chain_events": "stock node trace events, not inferred chain correctness"}
    if measurement_id == "cardano-stock-blockfetch":
        return {
            "blockfetch_duration": _distribution(events, kinds={"blockfetch-completed"}, field="duration_micros", unit="us"),
            "blockfetch_bytes": {
                "status": "available",
                "unit": "bytes",
                "value": sum(event.get("size_bytes", 0) for event in events if event["kind"] == "blockfetch-completed"),
            },
            "served_blocks": _count(events, {"blockfetch-served"}, "blocks"),
            "handler_queue_residence": _unavailable("us", "stock Cardano-node does not emit the audited internal handler queue boundary"),
        }, {"blockfetch_duration": "node-emitted CompletedBlockFetch delay"}
    if measurement_id == "cardano-stock-txsubmission-mempool":
        return {
            "mempool_accepted": _count(events, {"mempool-accepted"}, "transactions"),
            "mempool_rejected": _count(
                events, {"mempool-rejected"}, "transactions",
                offered_count=_mempool_offered_count(events),
            ),
            "mempool_tx_count": _distribution(events, kinds={"mempool-accepted", "mempool-rejected", "mempool-removed"}, field="mempool_tx_count", unit="transactions"),
            "mempool_bytes": _distribution(events, kinds={"mempool-accepted", "mempool-rejected", "mempool-removed"}, field="mempool_bytes", unit="bytes"),
            "mempool_sync_duration": _distribution(events, kinds={"mempool-synced"}, field="duration_micros", unit="us"),
            "txsubmission_residence": _unavailable("us", "stock Cardano-node does not emit a continuous request-to-mempool residence interval"),
        }, {"mempool_accepted": "stock node admission events", "mempool_rejected": "stock node rejection events"}
    if measurement_id == "cardano-stock-ledger-block-epoch":
        return {
            "adopted_blocks": _count(events, {"chain-adopted", "forge-adopted-block"}, "blocks"),
            "ledger_samples": _count(events, {"ledger-metrics"}),
            "epoch_transition_duration": _unavailable("us", "stock traces do not expose one authoritative epoch-transition interval"),
        }, {"adopted_blocks": "stock ChainDB and forge events"}
    if measurement_id == "cardano-stock-plutus-execution":
        return {
            "transaction_outcomes": _count(events, {"mempool-accepted", "mempool-rejected"}, "transactions"),
            "plutus_vm_duration": _unavailable("us", "stock Cardano-node does not expose authoritative UPLC wall-clock stage timing"),
        }, {"transaction_outcomes": "node outcomes only; workload execution budgets are correlated separately"}
    if measurement_id == "cardano-stock-network":
        sampled = _distribution(events, kinds={"keepalive-sample"}, field="duration_micros", unit="us")
        claims = {"protocol_events": "selected stock traces, not a complete protocol transcript"}
        if sampled.get("sample_count"):
            keepalive = sampled
            claims["keepalive_rtt"] = "node-reported AddSample round trips"
        else:
            keepalive = distribution_summary(_keepalive_pairs(events), unit="us")
            claims["keepalive_rtt"] = (
                "message-pair round trips derived from KeepAlive send and "
                "KeepAliveResponse receive trace timestamps per connection; "
                "an upper bound that includes tracer scheduling"
            )
        return {
            "keepalive_rtt": keepalive,
            "protocol_events": _count(events, {"chainsync-server-update", "blockfetch-completed", "blockfetch-served", "txsubmission-event", "keepalive-sample", "keepalive-request", "keepalive-response"}),
        }, claims
    return {}, {}


class CardanoStockCollector:
    def __init__(
        self,
        entry: dict[str, Any],
        *,
        trace_paths: Iterable[str | Path],
        include_existing: bool = False,
        allow_missing_at_start: bool = False,
        max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
    ) -> None:
        self.entry = entry
        self.measurement_id = entry["id"]
        self.trace_paths = [Path(path) for path in trace_paths]
        if len(self.trace_paths) > MAX_SOURCE_PATHS:
            raise ValueError(f"at most {MAX_SOURCE_PATHS} telemetry source paths are allowed")
        if max_source_bytes <= 0 or max_source_bytes > 64 * 1024 * 1024:
            raise ValueError("max_source_bytes must be within 1..67108864")
        self.include_existing = include_existing
        self.allow_missing_at_start = allow_missing_at_start
        self.max_source_bytes = max_source_bytes
        self._offsets: dict[Path, int] = {}

    def prepare(self, context) -> None:
        for path in self.trace_paths:
            if not path.is_file():
                if not self.allow_missing_at_start:
                    raise FileNotFoundError(f"Cardano telemetry source is unavailable: {path}")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
        context.collector_dir.mkdir(parents=True, exist_ok=True)

    def start(self, context) -> None:
        self._offsets = {
            path: 0 if self.include_existing else path.stat().st_size
            for path in self.trace_paths
        }

    def on_marker(self, marker, context) -> None:
        return None

    def stop(self, context) -> None:
        return None

    def finalize(self, context) -> dict[str, Any]:
        sources = []
        for path in self.trace_paths:
            body, truncated = _bounded_read(
                path,
                start=self._offsets.get(path, 0),
                max_bytes=self.max_source_bytes,
            )
            sources.append((str(path), body, truncated))
        loaded = _load_cardano_trace_bytes(sources)
        raw_sources = loaded.pop("_raw_sources")
        # The node trace is shared by every collector of the run: retain it once.
        raw_artifact = context.shared_artifact("cardano-node.ndjson", b"\n".join(body for _name, body in raw_sources))
        normalized_path = context.artifact_path("normalized.ndjson")
        normalized_path.write_text(
            "".join(json.dumps(event, sort_keys=True) + "\n" for event in loaded["events"]),
            encoding="utf-8",
        )
        correlations = correlate_measurement_events(loaded["events"])
        context.write_json("correlations.json", correlations)
        measurements, claims = _build_measurements(self.measurement_id, loaded["events"])
        measurements = withhold_without_source(
            measurements, source_record_count=loaded["source_record_count"]
        )
        measurements = withhold_untraced_zero_counts(measurements, loaded["observed_namespaces"])
        result = {
            "schema_version": "v1",
            "measurement_id": self.measurement_id,
            "source_revision": CARDANO_SOURCE_REVISION,
            "measurements": measurements,
            "claims": claims,
            "export": {
                key: loaded[key]
                for key in (
                    "source_record_count", "normalized_record_count", "ignored_record_count",
                    "ignored_reasons", "rejected_record_count", "rejection_reasons",
                    "duplicate_record_count", "truncated_sources",
                )
            },
            "artifacts": {
                "raw": raw_artifact,
                "normalized": normalized_path.relative_to(context.run_dir).as_posix(),
                "correlations": context.artifact_path("correlations.json").relative_to(context.run_dir).as_posix(),
                "result": context.artifact_path("result.json").relative_to(context.run_dir).as_posix(),
            },
        }
        result["export"]["incomplete"] = bool(
            result["export"]["rejected_record_count"] or result["export"]["truncated_sources"]
        )
        context.write_json("result.json", result)
        return result


def build_cardano_stock_factories(
    *,
    trace_paths: Iterable[str | Path],
    include_existing: bool = False,
    allow_missing_at_start: bool = False,
    max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
) -> dict[str, Any]:
    paths = tuple(Path(path) for path in trace_paths)

    def factory(entry: dict[str, Any]) -> CardanoStockCollector:
        return CardanoStockCollector(
            entry,
            trace_paths=paths,
            include_existing=include_existing,
            allow_missing_at_start=allow_missing_at_start,
            max_source_bytes=max_source_bytes,
        )

    return {measurement_id: factory for measurement_id in STOCK_MEASUREMENT_IDS}


class CardanoHeaderValidationCollector:
    """Report per-header validation verdict counts and reasons from stock traces.

    Fail-closed: when no header-validation verdict is present in the telemetry
    window, the measurement is ``unavailable`` with a reason, never a zero pass.
    """

    def __init__(
        self,
        entry: dict[str, Any],
        *,
        trace_paths: Iterable[str | Path],
        allow_missing_at_start: bool = False,
        max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
    ) -> None:
        self.entry = entry
        self.measurement_id = entry["id"]
        self.trace_paths = [Path(path) for path in trace_paths]
        self.allow_missing_at_start = allow_missing_at_start
        self.max_source_bytes = max_source_bytes
        self._offsets: dict[Path, int] = {}

    def prepare(self, context) -> None:
        for path in self.trace_paths:
            if not path.is_file():
                if not self.allow_missing_at_start:
                    raise FileNotFoundError(f"Cardano telemetry source is unavailable: {path}")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
        context.collector_dir.mkdir(parents=True, exist_ok=True)

    def start(self, context) -> None:
        self._offsets = {path: path.stat().st_size for path in self.trace_paths}

    def on_marker(self, marker, context) -> None:
        return None

    def stop(self, context) -> None:
        return None

    def finalize(self, context) -> dict[str, Any]:
        from scripts.header_validation_parse import parse_cardano_header_events, verdict_by_hash

        lines: list[str] = []
        for path in self.trace_paths:
            body, _truncated = _bounded_read(
                path, start=self._offsets.get(path, 0), max_bytes=self.max_source_bytes
            )
            lines.extend(body.decode("utf-8", "replace").splitlines())
        events = parse_cardano_header_events(lines)
        latest = list(verdict_by_hash(events).values())
        if not latest:
            result = {
                "schema_version": "v1",
                "measurement_id": self.measurement_id,
                "source_revision": CARDANO_SOURCE_REVISION,
                "measurements": {
                    "header_validation": {
                        "status": "unavailable",
                        "reason": "no header-validation verdict observed in telemetry",
                        "unit": "count",
                        "value": None,
                    }
                },
                "verdicts": {"accepted": 0, "rejected": 0},
                "by_reason": {},
                "events": [],
            }
        else:
            verdicts = Counter(event["verdict"] for event in latest)
            by_reason = Counter(
                event["reason"] for event in latest
                if event["verdict"] == "rejected" and event["reason"]
            )
            counts = {"accepted": verdicts.get("accepted", 0), "rejected": verdicts.get("rejected", 0)}
            result = {
                "schema_version": "v1",
                "measurement_id": self.measurement_id,
                "source_revision": CARDANO_SOURCE_REVISION,
                "measurements": {
                    "header_validation": {"status": "available", "unit": "count", "value": counts}
                },
                "verdicts": counts,
                "by_reason": dict(by_reason),
                "events": latest[:500],
            }
        context.write_json("result.json", result)
        return result
