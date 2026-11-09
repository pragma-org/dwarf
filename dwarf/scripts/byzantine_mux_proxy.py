from __future__ import annotations

import argparse
import hashlib
import json
import selectors
import signal
import socket
import socketserver
import os
import threading
import time
from pathlib import Path


HEADER_LENGTH = 8
RESPONDER_PROTOCOL_BIT = 0x8000
CHAINSYNC_PROTOCOL_ID = 2
BLOCKFETCH_PROTOCOL_ID = 3


def parse_mux_segment_header(header: bytes) -> dict[str, int]:
    if len(header) != HEADER_LENGTH:
        raise ValueError(f"expected {HEADER_LENGTH} header bytes, got {len(header)}")
    timestamp = int.from_bytes(header[0:4], "big")
    protocol_word = int.from_bytes(header[4:6], "big")
    payload_length = int.from_bytes(header[6:8], "big")
    return {
        "timestamp": timestamp,
        "protocol": _normalize_protocol_id(protocol_word),
        "mini_protocol_num": _normalize_protocol_id(protocol_word),
        "initiator": 0 if (protocol_word & RESPONDER_PROTOCOL_BIT) else 1,
        "raw_protocol": protocol_word,
        "payload_length": payload_length,
        "segment_length": HEADER_LENGTH + payload_length,
    }


def _normalize_protocol_id(protocol: int) -> int:
    return int(protocol) & ~RESPONDER_PROTOCOL_BIT


def _read_cbor_head(data: bytes, offset: int = 0) -> tuple[int, int, int] | None:
    if offset >= len(data):
        return None
    initial = data[offset]
    major = initial >> 5
    additional = initial & 0x1F
    if additional < 24:
        return major, additional, offset + 1
    if additional == 24:
        if offset + 1 >= len(data):
            return None
        return major, data[offset + 1], offset + 2
    if additional == 25:
        if offset + 2 >= len(data):
            return None
        return major, int.from_bytes(data[offset + 1 : offset + 3], "big"), offset + 3
    return None


_CBOR_WIDTHS = {24: 1, 25: 2, 26: 4, 27: 8}


def cbor_item_end(data, offset: int = 0) -> int | None:
    """Offset just past the CBOR item at ``offset``; None while the item is incomplete (F144).

    Lets the proxy split a mini-protocol byte stream into whole messages: a mux segment can carry
    several messages (coalesced StartBatch/Block/BatchDone) or part of one (a large block)."""
    if offset >= len(data):
        return None
    initial = data[offset]
    major, info = initial >> 5, initial & 0x1F
    if info == 31:
        if major not in (2, 3, 4, 5):
            raise ValueError("unexpected CBOR break / indefinite head")
        position = offset + 1
        while True:
            if position >= len(data):
                return None
            if data[position] == 0xFF:
                return position + 1
            position = cbor_item_end(data, position)
            if position is None:
                return None
            if major == 5:
                position = cbor_item_end(data, position)
                if position is None:
                    return None
    if info < 24:
        argument, position = info, offset + 1
    elif info in _CBOR_WIDTHS:
        width = _CBOR_WIDTHS[info]
        if offset + 1 + width > len(data):
            return None
        argument, position = int.from_bytes(data[offset + 1:offset + 1 + width], "big"), offset + 1 + width
    else:
        raise ValueError("reserved CBOR additional information")
    if major in (0, 1, 7):
        return position
    if major in (2, 3):
        return position + argument if position + argument <= len(data) else None
    if major in (4, 5):
        for _ in range(argument * (2 if major == 5 else 1)):
            position = cbor_item_end(data, position)
            if position is None:
                return None
        return position
    return cbor_item_end(data, position)  # major 6: one tagged item


def split_messages(buffer: bytearray) -> list[bytes]:
    """Pop every complete CBOR message off the front of ``buffer``; a partial tail stays (F144)."""
    messages = []
    while buffer:
        end = cbor_item_end(buffer, 0)
        if end is None:
            break
        messages.append(bytes(buffer[:end]))
        del buffer[:end]
    return messages


def classify_segment_message(*, protocol: int, payload: bytes) -> str | None:
    if _normalize_protocol_id(protocol) == CHAINSYNC_PROTOCOL_ID:
        head = _read_cbor_head(payload, 0)
        if head is None:
            return None
        major, length, cursor = head
        if major != 4:
            return None
        head = _read_cbor_head(payload, cursor)
        if head is None:
            return None
        key_major, key, _cursor = head
        if key_major != 0:
            return None
        mapping = {
            (1, 0): "request_next",
            (1, 1): "await_reply",
            (3, 2): "roll_forward",
            (3, 3): "roll_backward",
            (2, 4): "find_intersect",
            (3, 5): "intersect_found",
            (2, 6): "intersect_not_found",
            (1, 7): "done",
        }
        return mapping.get((length, key))
    if _normalize_protocol_id(protocol) != BLOCKFETCH_PROTOCOL_ID:
        return None
    head = _read_cbor_head(payload, 0)
    if head is None:
        return None
    major, length, cursor = head
    if major != 4:
        return None
    head = _read_cbor_head(payload, cursor)
    if head is None:
        return None
    key_major, key, _cursor = head
    if key_major != 0:
        return None
    mapping = {
        (3, 0): "request_range",
        (1, 1): "client_done",
        (1, 2): "start_batch",
        (1, 3): "no_blocks",
        (2, 4): "block",
        (1, 5): "batch_done",
    }
    return mapping.get((length, key))


def _cbor():
    try:
        import cbor2  # noqa: PLC0415 - available in the controller/framework image
        return cbor2
    except ImportError:  # pragma: no cover
        return None


def _unwrap_tag24(value):
    cbor2 = _cbor()
    if cbor2 is not None and isinstance(value, cbor2.CBORTag) and value.tag == 24:
        return value.value
    return value


def decode_roll_forward(payload: bytes) -> dict | None:
    """slot, block number, parent and header hash of a ChainSync MsgRollForward (F142).

    N2N headers are [era, #6.24(header bytes)]; header = [header_body, signature] and
    header_body starts [block_number, slot, prev_hash, ...]. None when it does not decode.
    """
    cbor2 = _cbor()
    if cbor2 is None:
        return None
    try:
        msg = cbor2.loads(payload)
        wrapped = msg[1]
        header_bytes = _unwrap_tag24(wrapped[1] if isinstance(wrapped, list) else wrapped)
        if not isinstance(header_bytes, (bytes, bytearray)):
            return None
        body = cbor2.loads(header_bytes)[0]
        prev = body[2]
        return {"block_no": int(body[0]), "slot": int(body[1]),
                "prev_hash": prev.hex() if isinstance(prev, (bytes, bytearray)) else None,
                "header_hash": hashlib.blake2b(bytes(header_bytes), digest_size=32).hexdigest()}
    except Exception:  # noqa: BLE001 - a non-decodable message is reported as None
        return None


def decode_block_digest(payload: bytes) -> str | None:
    """blake2b-256 of a BlockFetch MsgBlock body, to tell two served blocks apart (F142)."""
    cbor2 = _cbor()
    if cbor2 is None:
        return None
    try:
        msg = cbor2.loads(payload)
        body = _unwrap_tag24(msg[1])
        raw = body if isinstance(body, (bytes, bytearray)) else cbor2.dumps(body)
        return hashlib.blake2b(bytes(raw), digest_size=32).hexdigest()
    except Exception:  # noqa: BLE001
        return None


class ProxyState:
    def __init__(
        self,
        *,
        upstream_host: str,
        upstream_port: int,
        output_dir: Path,
        mutation_mode: str,
        mutation_direction: str,
        mutation_protocol: str,
        mutate_after_segments: int,
        mutation_message: str | None = None,
    ) -> None:
        self.mutation_message = mutation_message or None
        self._connection_counter = 0
        self._replay_store: list[bytes] = []
        self._replay_done = False
        self._flip_done = False
        self._live: dict[int, dict] = {}
        self.upstream_host = upstream_host
        self.upstream_port = upstream_port
        self.output_dir = output_dir
        self.mutation_mode = mutation_mode
        self.mutation_direction = mutation_direction
        self.mutation_protocol = mutation_protocol
        self.mutate_after_segments = mutate_after_segments
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.started_at = time.time()
        self.stats = {
            "intercepted_segments": 0,
            "mutated_segments": 0,
            "client_to_server_segments": 0,
            "server_to_client_segments": 0,
            "connections_seen": 0,
            "chainsync_messages_observed": 0,
            "chainsync_roll_backward_count": 0,
            "chainsync_roll_forward_count": 0,
            "rollback_then_forward_count": 0,
            "blockfetch_messages_observed": 0,
            "block_range_requests_observed": 0,
            "blocks_fetched": 0,
            "mutation_mode": mutation_mode,
            "mutation_direction": mutation_direction,
            "mutation_protocol": mutation_protocol,
            "mutate_after_segments": mutate_after_segments,
            "mutation_message": mutation_message or None,
            "connections": [],
            "replays": [],
            # F144: per-message block-fetch measurement and the real injections' evidence.
            "blockfetch_batches": [],
            "peak_blocks_per_batch": None,
            "peak_outstanding_range_requests": None,
            "fork_switches": [],
            "header_corruptions": [],
        }
        self._streams: dict[tuple, bytearray] = {}
        self._blockfetch: dict = {}
        self._header_corrupt_seen = 0
        self._header_corrupt_done = False
        self.events_path = output_dir / "proxy-events.ndjson"
        self.stats_path = output_dir / "proxy-stats.json"
        self._pending_inbound_chainsync_rollbacks = 0
        self._chainsync_fork_switch_triggered = False

    def note_connection(self) -> dict:
        """Open a connection record (F130): its mutations and who closed it, and when."""
        with self.lock:
            self.stats["connections_seen"] += 1
            self._connection_counter += 1
            record = {"id": self._connection_counter, "opened_at": time.time(), "mutated_segments": 0,
                      "first_mutation_at": None, "closed_at": None, "closed_by": None, "drop_seconds": None}
            self._live[record["id"]] = record
            self._write_stats_locked()
            return record

    def note_mutation(self, record: dict | None) -> None:
        if record is None:
            return
        with self.lock:
            record["mutated_segments"] += 1
            if record["first_mutation_at"] is None:
                record["first_mutation_at"] = time.time()
            self._write_stats_locked()

    def close_connection(self, record: dict, *, closed_by: str) -> None:
        with self.lock:
            if record.get("closed_at") is not None:
                return
            record["closed_at"] = time.time()
            record["closed_by"] = closed_by
            if record["first_mutation_at"] is not None:
                record["drop_seconds"] = round(record["closed_at"] - record["first_mutation_at"], 3)
            self._live.pop(record.get("id"), None)
            self.stats["connections"] = (self.stats["connections"] + [dict(record)])[-64:]
            self._write_stats_locked()

    def note_segment(self, *, direction: str, protocol: int, payload_length: int, mutated: bool) -> None:
        with self.lock:
            self.stats["intercepted_segments"] += 1
            key = "client_to_server_segments" if direction == "outbound" else "server_to_client_segments"
            self.stats[key] += 1
            if mutated:
                self.stats["mutated_segments"] += 1
            self.events_path.parent.mkdir(parents=True, exist_ok=True)
            with self.events_path.open("a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {
                            "ts": time.time(),
                            "direction": direction,
                            "protocol": protocol,
                            "payload_length": payload_length,
                            "mutated": mutated,
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
            self._write_stats_locked()

    def _count_message_locked(self, *, direction: str, protocol: int, message_kind: str | None) -> None:
        if message_kind is None:
            return
        normalized_protocol = _normalize_protocol_id(protocol)
        if normalized_protocol == CHAINSYNC_PROTOCOL_ID:
            self.stats["chainsync_messages_observed"] += 1
            if message_kind == "roll_backward":
                self.stats["chainsync_roll_backward_count"] += 1
                if direction == "inbound":
                    self._pending_inbound_chainsync_rollbacks += 1
            elif message_kind == "roll_forward":
                self.stats["chainsync_roll_forward_count"] += 1
                if direction == "inbound" and self._pending_inbound_chainsync_rollbacks > 0:
                    self.stats["rollback_then_forward_count"] += 1
                    self._pending_inbound_chainsync_rollbacks -= 1
        elif normalized_protocol == BLOCKFETCH_PROTOCOL_ID:
            self.stats["blockfetch_messages_observed"] += 1
            if message_kind == "request_range":
                self.stats["block_range_requests_observed"] += 1
            elif message_kind == "block":
                self.stats["blocks_fetched"] += 1

    def split_stream(self, *, record: dict | None, direction: str, header: dict, payload: bytes) -> list[bytes] | None:
        """Whole messages this segment completed on its (connection, direction, protocol word) stream.

        None when the stream does not frame as CBOR (its buffer is dropped); callers then fall back
        to the first-message classification of the segment (F144)."""
        key = ((record or {}).get("id"), direction, header.get("raw_protocol", header["protocol"]))
        with self.lock:
            buffer = self._streams.setdefault(key, bytearray())
            buffer.extend(payload)
            try:
                return split_messages(buffer)
            except (ValueError, IndexError):
                buffer.clear()
                return None

    def _observe_blockfetch_locked(self, *, record: dict | None, direction: str, message_kind: str | None,
                                   message: bytes) -> None:
        """Per-connection batch accounting: blocks served between StartBatch and BatchDone (F144)."""
        connection_id = (record or {}).get("id")
        conn = self._blockfetch.setdefault(connection_id, {"open": None, "ranges": [], "outstanding": 0})
        if direction == "outbound" and message_kind == "request_range":
            span = decode_range_request(message) or {}
            conn["ranges"].append(span)
            conn["outstanding"] += 1
            self.stats["peak_outstanding_range_requests"] = max(
                self.stats.get("peak_outstanding_range_requests") or 0, conn["outstanding"])
            for corruption in self.stats["header_corruptions"]:
                if corruption.get("connection_id") == connection_id and corruption.get(
                        "corrupted_header_hash") in {span.get("from_hash"), span.get("to_hash")}:
                    corruption["block_requested"] = True
            return
        if direction != "inbound":
            return
        if message_kind == "start_batch":
            conn["open"] = 0
        elif message_kind == "block" and conn["open"] is not None:
            conn["open"] += 1
        elif message_kind in {"batch_done", "no_blocks"}:
            span = conn["ranges"].pop(0) if conn["ranges"] else {}
            conn["outstanding"] = max(0, conn["outstanding"] - 1)
            if message_kind == "batch_done" and conn["open"] is not None:
                blocks = conn["open"]
                self.stats["blockfetch_batches"] = (self.stats["blockfetch_batches"] + [{
                    "connection_id": connection_id, "blocks": blocks,
                    "from_slot": span.get("from_slot"), "to_slot": span.get("to_slot")}])[-256:]
                self.stats["peak_blocks_per_batch"] = max(self.stats.get("peak_blocks_per_batch") or 0, blocks)
            conn["open"] = None

    def note_segment_message(
        self,
        *,
        direction: str,
        protocol: int,
        payload_length: int,
        mutated: bool,
        message_kind: str | None,
        original_message_kind: str | None = None,
        messages: list[tuple[str | None, bytes]] | None = None,
        record: dict | None = None,
    ) -> None:
        with self.lock:
            self.stats["intercepted_segments"] += 1
            key = "client_to_server_segments" if direction == "outbound" else "server_to_client_segments"
            self.stats[key] += 1
            if mutated:
                self.stats["mutated_segments"] += 1
            if messages is None:
                self._count_message_locked(direction=direction, protocol=protocol, message_kind=message_kind)
            else:
                # F144: every whole message the segment completed, not only its first one.
                for kind, body in messages:
                    self._count_message_locked(direction=direction, protocol=protocol, message_kind=kind)
                    if _normalize_protocol_id(protocol) == BLOCKFETCH_PROTOCOL_ID:
                        self._observe_blockfetch_locked(record=record, direction=direction, message_kind=kind,
                                                        message=body)
            self.events_path.parent.mkdir(parents=True, exist_ok=True)
            event = {
                "ts": time.time(),
                "direction": direction,
                "protocol": protocol,
                "normalized_protocol": _normalize_protocol_id(protocol),
                "mini_protocol_num": _normalize_protocol_id(protocol),
                "initiator": 0 if (protocol & RESPONDER_PROTOCOL_BIT) else 1,
                "payload_length": payload_length,
                "mutated": mutated,
            }
            if message_kind is not None:
                event["message_kind"] = message_kind
            if messages is not None and len(messages) != 1:
                event["message_kinds"] = [kind for kind, _body in messages]
            if original_message_kind is not None:
                event["original_message_kind"] = original_message_kind
            with self.events_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, sort_keys=True) + "\n")
            self._write_stats_locked()

    def should_mutate(self, *, direction: str, protocol: int, message_kind: str | None = None) -> bool:
        if self.mutation_mode == "pass_through":
            return False
        if self.mutation_direction not in {direction, "both"}:
            return False
        if self.mutation_protocol != "any" and int(self.mutation_protocol) != protocol:
            return False
        if self.mutation_message is not None and message_kind != self.mutation_message:
            return False
        if self.mutation_mode == "flip_once":
            if self._flip_done or (self.stats["intercepted_segments"] + 1) < self.mutate_after_segments:
                return False
            self._flip_done = True
            return True
        if self.mutation_mode in {"chainsync_fork_switch_once", "corrupt_header_signature"}:
            # F144: these inject through ForkSwitchSession / _corrupt_header_signature, never a byte flip.
            return False
        return (self.stats["intercepted_segments"] + 1) >= self.mutate_after_segments

    def stop(self) -> None:
        with self.lock:
            self.stop_event.set()
            self._write_stats_locked()

    def _write_stats_locked(self) -> None:
        body = dict(self.stats)
        # F142b: still-open connections are evidence too (their records were written only on close).
        body["connections"] = list(self.stats.get("connections") or []) + [dict(r) for r in getattr(self, "_live", {}).values()]
        body["uptime_seconds"] = round(time.time() - self.started_at, 3)
        self.stats_path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic replace: the fault step polls this file while the proxy runs (F130).
        tmp_path = self.stats_path.with_name(self.stats_path.name + ".tmp")
        tmp_path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp_path, self.stats_path)


def _rebuild_segment(segment: bytes, payload: bytes) -> bytes:
    return segment[:6] + len(payload).to_bytes(2, "big") + payload


def _replay_earlier(state: ProxyState, *, segment: bytes, header: dict, message_kind: str | None) -> tuple[bytes, dict | None]:
    """Store genuine messages of the selected kind; once enough are stored, replace the next one with
    the oldest (F142). The replay is measured: slot / height deltas and the parent mismatch."""
    payload = segment[HEADER_LENGTH:]
    with state.lock:
        if state._replay_done or len(state._replay_store) < max(1, state.mutate_after_segments):
            state._replay_store.append(bytes(payload))
            del state._replay_store[:-16]
            return segment, None
        previous = state._replay_store[-1]
        replayed = state._replay_store[0]
        if message_kind == "roll_forward":
            # F142b: after forks the store is not in slot order; replay the lowest-slot header below the tip.
            prev_info = decode_roll_forward(previous)
            candidates = [(info["slot"], stored) for stored in state._replay_store[:-1]
                          for info in [decode_roll_forward(stored)]
                          if info and prev_info and info["slot"] < prev_info["slot"]]
            if not candidates:
                state._replay_store.append(bytes(payload))
                del state._replay_store[:-16]
                return segment, None
            replayed = min(candidates)[1]
        if replayed == payload:
            return segment, None
        state._replay_done = True
    info = {"message_kind": message_kind, "protocol": header["protocol"]}
    if message_kind == "roll_forward":
        old, prev_sent, current = decode_roll_forward(replayed), decode_roll_forward(previous), decode_roll_forward(payload)
        info.update({"replayed": old, "previous": prev_sent, "displaced": current})
        if old and prev_sent:
            info["slot_delta"] = old["slot"] - prev_sent["slot"]
            info["height_delta"] = old["block_no"] - prev_sent["block_no"]
            info["parent_mismatch"] = old["prev_hash"] != prev_sent["header_hash"]
    elif message_kind == "block":
        info.update({"replayed_digest": decode_block_digest(replayed), "displaced_digest": decode_block_digest(payload)})
        info["block_mismatch"] = info["replayed_digest"] != info["displaced_digest"]
    with state.lock:
        state.stats["replays"].append(info)
    return _rebuild_segment(segment, replayed), info


def decode_range_request(payload: bytes) -> dict | None:
    """Slots and hashes of a BlockFetch MsgRequestRange [0, point, point] (F144)."""
    cbor2 = _cbor()
    if cbor2 is None:
        return None
    try:
        msg = cbor2.loads(payload)
        lower, upper = msg[1], msg[2]
        return {"from_slot": int(lower[0]), "from_hash": bytes(lower[1]).hex(),
                "to_slot": int(upper[0]), "to_hash": bytes(upper[1]).hex()}
    except Exception:  # noqa: BLE001 - a non-decodable request is reported as None
        return None


def _corrupt_header_signature(state: ProxyState, *, segment: bytes, record: dict | None) -> tuple[bytes, bool]:
    """Flip the last byte of the embedded header of a genuine RollForward, once (F144).

    A Shelley-era header is [header_body, kes_signature]; its last byte belongs to the KES
    signature, so the header body (slot, VRF, opcert) stays genuine and the CBOR shape and length
    are unchanged. Praos header validation must reject it; the hashes are recorded so the target's
    log and any block request for the corrupted header can be matched."""
    payload = segment[HEADER_LENGTH:]
    try:
        end = cbor_item_end(payload, 0)
    except (ValueError, IndexError):
        return segment, False
    first = payload[:end] if end else payload
    if classify_segment_message(protocol=CHAINSYNC_PROTOCOL_ID, payload=first) != "roll_forward":
        return segment, False
    with state.lock:
        if state._header_corrupt_done:
            return segment, False
        state._header_corrupt_seen += 1
        if state._header_corrupt_seen < max(1, state.mutate_after_segments):
            return segment, False
    cbor2 = _cbor()
    info = decode_roll_forward(first)
    if cbor2 is None or info is None:
        return segment, False
    msg = cbor2.loads(first)
    wrapped = msg[1]
    header_bytes = bytes(_unwrap_tag24(wrapped[1] if isinstance(wrapped, list) else wrapped))
    start = payload.find(header_bytes)
    if start < 0:
        return segment, False
    corrupted = bytearray(header_bytes)
    corrupted[-1] ^= 0x01
    new_payload = payload[:start] + bytes(corrupted) + payload[start + len(header_bytes):]
    with state.lock:
        state._header_corrupt_done = True
        state.stats["header_corruptions"].append({
            "connection_id": (record or {}).get("id"),
            "at": time.time(),
            "slot": info["slot"],
            "block_no": info["block_no"],
            "original_header_hash": info["header_hash"],
            "corrupted_header_hash": hashlib.blake2b(bytes(corrupted), digest_size=32).hexdigest(),
            "corrupted_field": "kes_signature_last_byte",
            "block_requested": False,
        })
    return segment[:HEADER_LENGTH - 2] + len(new_payload).to_bytes(2, "big") + new_payload, True


def _mutate_segment(segment: bytes, *, state: ProxyState, direction: str, record: dict | None = None) -> tuple[bytes, bool]:
    header = parse_mux_segment_header(segment[:HEADER_LENGTH])
    original_payload = segment[HEADER_LENGTH:]
    original_message_kind = classify_segment_message(protocol=header["protocol"], payload=original_payload)
    # F142: what the upstream answered to a corrupted range request on this connection.
    if (record is not None and record.get("range_request_corrupted") and direction == "inbound"
            and _normalize_protocol_id(header["protocol"]) == BLOCKFETCH_PROTOCOL_ID):
        if record.get("range_response") is None and original_message_kind in {"no_blocks", "start_batch"}:
            record["range_response"] = original_message_kind
        elif original_message_kind == "block" and record.get("range_response") == "start_batch" and not record.get("range_batch_done"):
            record["blocks_after_bad_range"] = int(record.get("blocks_after_bad_range", 0)) + 1
        elif original_message_kind == "batch_done":
            record["range_batch_done"] = True
    mutated = False
    if state.mutation_mode == "replay_earlier":
        if (state.mutation_direction in {direction, "both"}
                and (state.mutation_protocol == "any" or int(state.mutation_protocol) == header["protocol"])
                and (state.mutation_message is None or original_message_kind == state.mutation_message)):
            segment, info = _replay_earlier(state, segment=segment, header=header, message_kind=original_message_kind)
            mutated = info is not None
    elif state.mutation_mode == "corrupt_header_signature":
        if direction == "inbound" and header["protocol"] == CHAINSYNC_PROTOCOL_ID and not header["initiator"]:
            segment, mutated = _corrupt_header_signature(state, segment=segment, record=record)
    elif (
        state.should_mutate(
            direction=direction,
            protocol=header["protocol"],
            message_kind=original_message_kind,
        )
        and header["payload_length"] > 0
    ):
        payload = bytearray(segment[HEADER_LENGTH:])
        payload[-1] ^= 0x01
        segment = segment[:HEADER_LENGTH] + bytes(payload)
        mutated = True
        if record is not None and original_message_kind == "request_range":
            record["range_request_corrupted"] = True
            record.setdefault("blocks_after_bad_range", 0)
    if mutated:
        state.note_mutation(record)
    message_kind = classify_segment_message(protocol=header["protocol"], payload=segment[HEADER_LENGTH:])
    messages = None
    if header["protocol"] in (CHAINSYNC_PROTOCOL_ID, BLOCKFETCH_PROTOCOL_ID):
        split = state.split_stream(record=record, direction=direction, header=header, payload=segment[HEADER_LENGTH:])
        if split is not None:
            messages = [(classify_segment_message(protocol=header["protocol"], payload=m), m) for m in split]
    state.note_segment_message(
        direction=direction,
        protocol=header["protocol"],
        payload_length=parse_mux_segment_header(segment[:HEADER_LENGTH])["payload_length"],
        mutated=mutated,
        message_kind=message_kind,
        original_message_kind=original_message_kind,
        messages=messages,
        record=record,
    )
    return segment, mutated


MAX_SEGMENT_PAYLOAD = 12288


def _frames(header_prefix: bytes, payload: bytes) -> bytes:
    """Mux segments carrying ``payload`` under a 6-byte timestamp+protocol-word prefix."""
    out = bytearray()
    for start in range(0, max(1, len(payload)), MAX_SEGMENT_PAYLOAD):
        chunk = payload[start:start + MAX_SEGMENT_PAYLOAD]
        out += header_prefix[:6] + len(chunk).to_bytes(2, "big") + chunk
    return bytes(out)


class ForkSwitchSession:
    """A real ChainSync fork switch on one proxied connection (F144).

    After the follower (the downstream node's ChainSync client) has taken ``depth`` consecutive
    genuine headers from the upstream, the proxy answers the next RollForward with a RollBackward
    to the point ``depth`` headers back, serves the genuine headers it already relayed again and
    then the displaced one. The follower's extra requests are answered from that queue (not
    forwarded), so the upstream session stays consistent. What the follower does next -- keeps
    requesting, or drops the connection -- is the measurement.
    """

    TERMINAL = {"roll_forward", "roll_backward", "intersect_found", "intersect_not_found"}

    def __init__(self, state: ProxyState, record: dict, *, send_downstream, send_upstream) -> None:
        self.state = state
        self.record = record
        self.send_downstream = send_downstream
        self.send_upstream = send_upstream
        self.lock = threading.Lock()
        self.depth = max(1, int(state.mutate_after_segments))
        self.client_buffer = bytearray()
        self.server_buffer = bytearray()
        self.client_prefix = b"\x00" * 4 + CHAINSYNC_PROTOCOL_ID.to_bytes(2, "big")
        self.server_prefix = b"\x00" * 4 + (CHAINSYNC_PROTOCOL_ID | RESPONDER_PROTOCOL_BIT).to_bytes(2, "big")
        self.credit = 0                # follower requests not yet answered
        self.swallow = 0               # follower requests answered from the queue instead of the upstream
        self.queue: list[tuple[bytes, str]] = []
        self.anchor = None             # decoded point of the last intersection / rollback
        self.chain: list[dict] = []    # consecutive headers relayed since the anchor
        self.evidence: dict | None = None

    @staticmethod
    def handles(direction: str, header: dict) -> bool:
        if header["protocol"] != CHAINSYNC_PROTOCOL_ID:
            return False
        # The follower is the downstream initiator; its server is the upstream responder.
        return bool(header["initiator"]) if direction == "outbound" else not header["initiator"]

    def on_segment(self, direction: str, segment: bytes) -> None:
        payload = segment[HEADER_LENGTH:]
        with self.lock:
            if direction == "outbound":
                self.client_prefix = segment[:6]
                self.client_buffer.extend(payload)
                for message in split_messages(self.client_buffer):
                    self._on_client_message(message)
            else:
                self.server_prefix = segment[:6]
                self.server_buffer.extend(payload)
                for message in split_messages(self.server_buffer):
                    self._on_server_message(message)
            self._drain()

    def _note(self, direction: str, message: bytes, *, mutated: bool = False) -> None:
        kind = classify_segment_message(protocol=CHAINSYNC_PROTOCOL_ID, payload=message)
        self.state.note_segment_message(direction=direction, protocol=CHAINSYNC_PROTOCOL_ID,
                                        payload_length=len(message), mutated=mutated, message_kind=kind,
                                        messages=[(kind, message)], record=self.record)

    def _on_client_message(self, message: bytes) -> None:
        kind = classify_segment_message(protocol=CHAINSYNC_PROTOCOL_ID, payload=message)
        if kind in {"request_next", "find_intersect"}:
            self.credit += 1
        if self.evidence is not None and kind == "request_next":
            if self.evidence["rollback_delivered_at"] is not None:
                self.evidence["requests_after_rollback"] += 1
            if self.evidence["displaced_delivered_at"] is not None:
                self.evidence["requests_after_displaced"] += 1
        if kind == "request_next" and self.swallow > 0:
            self.swallow -= 1
            self.evidence["swallowed_requests"] += 1
            self._note("outbound", message)
            return
        self.send_upstream(_frames(self.client_prefix, message))
        self._note("outbound", message)

    def _on_server_message(self, message: bytes) -> None:
        kind = classify_segment_message(protocol=CHAINSYNC_PROTOCOL_ID, payload=message)
        cbor2 = _cbor()
        if kind in {"intersect_found", "roll_backward"} and cbor2 is not None:
            try:
                point = list(cbor2.loads(message)[1])
            except Exception:  # noqa: BLE001
                point = None
            kept = [i for i, h in enumerate(self.chain) if point == [h["slot"], h["hash_bytes"]]]
            if kept:
                self.chain = self.chain[: kept[-1] + 1]
            else:
                self.anchor, self.chain = point, []
        elif kind == "roll_forward":
            info = decode_roll_forward(message)
            if self.chain:
                parent = self.chain[-1]["hash"]
            elif isinstance(self.anchor, list) and len(self.anchor) == 2:
                parent = bytes(self.anchor[1]).hex()
            else:
                parent = None
            if info is not None and parent is not None and info["prev_hash"] == parent:
                if self._should_inject():
                    self._inject(message, info, cbor2.loads(message)[2])
                    self.chain.append(self._entry(info, message))
                    return
                self.chain = (self.chain + [self._entry(info, message)])[-64:]
            else:
                # Not consecutive with what was followed (or from origin): start a new run here.
                self.anchor, self.chain = None, ([self._entry(info, message)] if info else [])
        self.queue.append((message, "upstream"))

    @staticmethod
    def _entry(info: dict, message: bytes) -> dict:
        return {"slot": info["slot"], "block_no": info["block_no"], "hash": info["header_hash"],
                "hash_bytes": bytes.fromhex(info["header_hash"]), "message": message}

    def _should_inject(self) -> bool:
        if self.state._chainsync_fork_switch_triggered or len(self.chain) < self.depth:
            return False
        return len(self.chain) > self.depth or self.anchor is not None

    def _inject(self, displaced: bytes, info: dict, tip) -> None:
        cbor2 = _cbor()
        replayed = self.chain[-self.depth:]
        if len(self.chain) > self.depth:
            base = self.chain[-self.depth - 1]
            point = [base["slot"], base["hash_bytes"]]
        else:
            point = list(self.anchor)
        self.state._chainsync_fork_switch_triggered = True
        self.queue.append((cbor2.dumps([3, point, tip]), "rollback"))
        self.queue.extend((entry["message"], "replay") for entry in replayed)
        self.queue.append((displaced, "displaced"))
        self.swallow += self.depth + 1
        self.evidence = {
            "connection_id": self.record.get("id"),
            "injected_at": time.time(),
            "rollback_depth": self.depth,
            "rollback_point": {"slot": int(point[0]), "hash": bytes(point[1]).hex()},
            "replayed": [{"slot": e["slot"], "block_no": e["block_no"], "hash": e["hash"]} for e in replayed],
            "displaced": {"slot": info["slot"], "block_no": info["block_no"], "hash": info["header_hash"]},
            "rollback_delivered_at": None,
            "replays_delivered": 0,
            "displaced_delivered_at": None,
            "requests_after_rollback": 0,
            "requests_after_displaced": 0,
            "forwards_after_rollback": 0,
            "swallowed_requests": 0,
        }
        with self.state.lock:
            self.state.stats["fork_switches"].append(self.evidence)
        self.state.note_mutation(self.record)

    def _drain(self) -> None:
        while self.queue and self.credit > 0:
            message, origin = self.queue.pop(0)
            kind = classify_segment_message(protocol=CHAINSYNC_PROTOCOL_ID, payload=message)
            if kind in self.TERMINAL:
                self.credit -= 1
            self.send_downstream(_frames(self.server_prefix, message))
            ev = self.evidence
            if ev is not None:
                now = time.time()
                if origin == "rollback":
                    ev["rollback_delivered_at"] = now
                elif kind == "roll_forward" and ev["rollback_delivered_at"] is not None:
                    ev["forwards_after_rollback"] += 1
                    if origin == "replay":
                        ev["replays_delivered"] += 1
                    elif origin == "displaced":
                        ev["displaced_delivered_at"] = now
            self._note("inbound", message, mutated=origin == "rollback")


class _LockedSender:
    """sendall on one socket from both relay threads (the fork switch answers from either side)."""

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.lock = threading.Lock()

    def __call__(self, data: bytes) -> None:
        with self.lock:
            self.sock.sendall(data)


def _relay_segments(source: socket.socket, target, *, state: ProxyState, direction: str,
                    record: dict | None = None, session: ForkSwitchSession | None = None) -> None:
    send = target if callable(target) else target.sendall
    buffer = bytearray()
    while not state.stop_event.is_set():
        try:
            chunk = source.recv(65536)
        except socket.timeout:
            # An idle peer is not a closed peer (F130): keep relaying.
            continue
        if not chunk:
            break
        buffer.extend(chunk)
        while len(buffer) >= HEADER_LENGTH:
            header = parse_mux_segment_header(buffer[:HEADER_LENGTH])
            total_length = header["segment_length"]
            if len(buffer) < total_length:
                break
            segment = bytes(buffer[:total_length])
            del buffer[:total_length]
            if session is not None and session.handles(direction, header):
                session.on_segment(direction, segment)
                continue
            segment, mutated = _mutate_segment(segment, state=state, direction=direction, record=record)
            send(segment)
    if buffer:
        send(bytes(buffer))


class MuxProxyHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        state: ProxyState = self.server.proxy_state
        record = state.note_connection()
        # outbound reads the downstream (the node under test); inbound reads the upstream.
        reader_side = {"outbound": "downstream", "inbound": "upstream"}
        try:
            upstream = socket.create_connection((state.upstream_host, state.upstream_port), timeout=10.0)
        except OSError:
            state.close_connection(record, closed_by="upstream-unreachable")
            raise
        with upstream:
            upstream.settimeout(1.0)
            self.request.settimeout(1.0)
            stop = threading.Event()
            senders = {"outbound": _LockedSender(upstream), "inbound": _LockedSender(self.request)}
            session = None
            if state.mutation_mode == "chainsync_fork_switch_once":
                session = ForkSwitchSession(state, record, send_downstream=senders["inbound"],
                                            send_upstream=senders["outbound"])

            def pump(source: socket.socket, target: socket.socket, direction: str) -> None:
                try:
                    _relay_segments(source, senders[direction], state=state, direction=direction, record=record,
                                    session=session)
                except (ConnectionError, OSError):
                    pass
                finally:
                    if not state.stop_event.is_set():
                        state.close_connection(record, closed_by=reader_side[direction])
                    stop.set()
                    try:
                        target.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

            outbound = threading.Thread(target=pump, args=(self.request, upstream, "outbound"), daemon=True)
            inbound = threading.Thread(target=pump, args=(upstream, self.request, "inbound"), daemon=True)
            outbound.start()
            inbound.start()
            while not stop.is_set() and not state.stop_event.is_set():
                time.sleep(0.1)
            if state.stop_event.is_set():
                state.close_connection(record, closed_by="proxy")


class ThreadedMuxProxy(socketserver.ThreadingTCPServer):
    allow_reuse_address = True

    def __init__(self, server_address, request_handler_class, *, proxy_state: ProxyState):
        self.proxy_state = proxy_state
        super().__init__(server_address, request_handler_class)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", required=True, type=int)
    parser.add_argument("--upstream-address", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--mutation-mode", default="flip_payload_byte")
    parser.add_argument("--mutation-direction", default="outbound")
    parser.add_argument("--mutation-protocol", default="any")
    parser.add_argument("--mutate-after-segments", type=int, default=1)
    parser.add_argument("--mutation-message", default=None, help="mutate only this message kind (e.g. block)")
    args = parser.parse_args(argv)

    upstream_host, upstream_port_text = str(args.upstream_address).rsplit(":", 1)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    state = ProxyState(
        upstream_host=upstream_host,
        upstream_port=int(upstream_port_text),
        output_dir=output_dir,
        mutation_mode=str(args.mutation_mode),
        mutation_direction=str(args.mutation_direction),
        mutation_protocol=str(args.mutation_protocol),
        mutate_after_segments=max(1, int(args.mutate_after_segments)),
        mutation_message=args.mutation_message,
    )

    def _handle_signal(_signum, _frame):
        state.stop()
        server.shutdown()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    server = ThreadedMuxProxy((args.listen_host, int(args.listen_port)), MuxProxyHandler, proxy_state=state)
    print(f"listening={args.listen_host}:{args.listen_port} upstream={args.upstream_address}", flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        state.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
