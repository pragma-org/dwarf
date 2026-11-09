"""Forge Praos/Conway blocks from a devnet pool's own headers (block-apply bridge).

A forged block reuses a REAL header the devnet's producer made for one of its
own leader slots: the slot, the VRF proof/output, the issuer and the opcert stay
untouched (so leadership and the opcert rules hold), and only the body hash /
body size (a crafted body) or the parent hash (re-parenting a descendant onto
the forged block) change. The header body is then re-signed with the pool's own
KES key (``praos_kes``) at the header's KES evolution.

Byte handling: every untouched CBOR item is kept as the exact bytes the node
sent (transactions keep their ids); only the header body is re-encoded, and
only after checking that it re-encodes canonically.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

import cbor2

from scripts import praos_kes

# Conway header body fields: [block_number, slot, prev_hash, issuer_vkey, vrf_vkey,
# vrf_result, block_body_size, block_body_hash, operational_cert, protocol_version]
HB_BLOCK_NUMBER, HB_SLOT, HB_PREV_HASH = 0, 1, 2
HB_BODY_SIZE, HB_BODY_HASH, HB_OPCERT = 6, 7, 8
EMPTY_BODY_SEGMENTS = (b"\x80", b"\x80", b"\xa0", b"\x80")


def blake2b256(data: bytes) -> bytes:
    return hashlib.blake2b(data, digest_size=32).digest()


# --------------------------------------------------------------------------- #
# Minimal CBOR item scanner (exact byte boundaries, no re-encoding)
# --------------------------------------------------------------------------- #

class Incomplete(ValueError):
    """The buffer ends before the CBOR item does."""


def _head(data: bytes, offset: int) -> tuple[int, int | None, int]:
    """(major, argument or None for indefinite, offset after the head)."""
    if offset >= len(data):
        raise Incomplete("no head byte")
    initial = data[offset]
    major, info = initial >> 5, initial & 0x1F
    if info < 24:
        return major, info, offset + 1
    widths = {24: 1, 25: 2, 26: 4, 27: 8}
    if info in widths:
        end = offset + 1 + widths[info]
        if end > len(data):
            raise Incomplete("truncated head")
        return major, int.from_bytes(data[offset + 1:end], "big"), end
    if info == 31 and major in (2, 3, 4, 5, 7):
        return major, None, offset + 1
    raise ValueError(f"unsupported CBOR additional info {info} at {offset}")


def item_end(data: bytes, offset: int = 0) -> int:
    """Offset just past the CBOR data item starting at ``offset``."""
    major, arg, cursor = _head(data, offset)
    if major in (0, 1):
        return cursor
    if major in (2, 3):
        if arg is None:
            while True:
                if cursor >= len(data):
                    raise Incomplete("indefinite string")
                if data[cursor] == 0xFF:
                    return cursor + 1
                cursor = item_end(data, cursor)
        if cursor + arg > len(data):
            raise Incomplete("truncated string")
        return cursor + arg
    if major in (4, 5):
        per_entry = 1 if major == 4 else 2
        if arg is None:
            while True:
                if cursor >= len(data):
                    raise Incomplete("indefinite container")
                if data[cursor] == 0xFF:
                    return cursor + 1
                cursor = item_end(data, cursor)
        for _ in range(arg * per_entry):
            cursor = item_end(data, cursor)
        return cursor
    if major == 6:
        return item_end(data, cursor)
    # major 7: simple values / floats; the head already covered the payload
    if arg is None:
        raise ValueError("unexpected break byte")
    return cursor


def array_items(data: bytes) -> list[bytes]:
    """Raw bytes of each element of the CBOR array ``data`` (definite or indefinite)."""
    major, arg, cursor = _head(data, 0)
    if major != 4:
        raise ValueError("not a CBOR array")
    items = []
    if arg is None:
        while data[cursor] != 0xFF:
            end = item_end(data, cursor)
            items.append(data[cursor:end])
            cursor = end
        cursor += 1
    else:
        for _ in range(arg):
            end = item_end(data, cursor)
            items.append(data[cursor:end])
            cursor = end
    if cursor != len(data):
        raise ValueError("trailing bytes after CBOR array")
    return items


def _container_head(major: int, count: int) -> bytes:
    """Definite-length CBOR head (array major 4 / map major 5) for ``count`` entries."""
    if count < 24:
        return bytes([(major << 5) | count])
    for info, width in ((24, 1), (25, 2), (26, 4), (27, 8)):
        if count < (1 << (8 * width)):
            return bytes([(major << 5) | info]) + count.to_bytes(width, "big")
    raise ValueError("container too large")


def encode_array(raw_items: list[bytes]) -> bytes:
    """Definite-length CBOR array of already-encoded items."""
    return _container_head(4, len(raw_items)) + b"".join(raw_items)


def tag24(raw: bytes) -> bytes:
    return cbor2.dumps(cbor2.CBORTag(24, raw))


# --------------------------------------------------------------------------- #
# Transactions -> block body segments
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class TxParts:
    body: bytes
    witnesses: bytes
    is_valid: bool
    auxiliary: bytes | None


def split_transaction(tx: bytes) -> TxParts:
    """Split a Conway transaction ``[body, witnesses, is_valid, aux|null]``."""
    items = array_items(tx)
    if len(items) != 4:
        raise ValueError(f"expected a 4-element Conway transaction, got {len(items)} elements")
    is_valid = cbor2.loads(items[2])
    if not isinstance(is_valid, bool):
        raise ValueError("transaction is_valid field is not a bool")
    aux = None if items[3] == b"\xf6" else items[3]
    return TxParts(items[0], items[1], is_valid, aux)


def body_segments(transactions: list[TxParts]) -> tuple[bytes, bytes, bytes, bytes]:
    """[tx_bodies, tx_witnesses, {index: aux}, [invalid indices]] as raw CBOR segments."""
    bodies = encode_array([t.body for t in transactions])
    witnesses = encode_array([t.witnesses for t in transactions])
    aux_entries = [(i, t.auxiliary) for i, t in enumerate(transactions) if t.auxiliary is not None]
    aux_map = _container_head(5, len(aux_entries)) + b"".join(cbor2.dumps(i) + raw for i, raw in aux_entries)
    invalid = cbor2.dumps([i for i, t in enumerate(transactions) if not t.is_valid])
    return bodies, witnesses, aux_map, invalid


def body_hash(segments: tuple[bytes, bytes, bytes, bytes]) -> bytes:
    return blake2b256(b"".join(blake2b256(s) for s in segments))


def body_size(segments: tuple[bytes, bytes, bytes, bytes]) -> int:
    return sum(len(s) for s in segments)


# --------------------------------------------------------------------------- #
# Headers
# --------------------------------------------------------------------------- #

def header_hash(header: bytes) -> bytes:
    return blake2b256(header)


def decode_header(header: bytes) -> tuple[list, bytes]:
    """(header body as a list, KES signature); refuses a non-canonical body encoding."""
    items = array_items(header)
    if len(items) != 2:
        raise ValueError("header is not [header_body, signature]")
    body = cbor2.loads(items[0])
    if not isinstance(body, list) or len(body) != 10:
        raise ValueError("not a Praos (Babbage/Conway) header body")
    if cbor2.dumps(body) != items[0]:
        raise ValueError("header body does not re-encode canonically; refusing to re-sign")
    return body, cbor2.loads(items[1])


def header_fields(header: bytes) -> dict:
    body, _sig = decode_header(header)
    return {
        "block_number": int(body[HB_BLOCK_NUMBER]),
        "slot": int(body[HB_SLOT]),
        "prev_hash": body[HB_PREV_HASH].hex() if body[HB_PREV_HASH] else None,
        "issuer_vkey": body[3].hex(),
        "body_size": int(body[HB_BODY_SIZE]),
        "body_hash": body[HB_BODY_HASH].hex(),
        "kes_vkey": body[HB_OPCERT][0].hex(),
        "opcert_kes_period": int(body[HB_OPCERT][2]),
        "hash": header_hash(header).hex(),
    }


def kes_evolution(header_body: list, slots_per_kes_period: int) -> int:
    return int(header_body[HB_SLOT]) // int(slots_per_kes_period) - int(header_body[HB_OPCERT][2])


def resign_header(
    header: bytes,
    *,
    kes_sign_key: bytes,
    slots_per_kes_period: int,
    prev_hash: bytes | None = None,
    segments: tuple[bytes, bytes, bytes, bytes] | None = None,
) -> bytes:
    """Re-sign ``header`` after replacing its parent and/or body commitment.

    The pool's KES key must be the key the header's opcert certifies; that is
    checked against the opcert hot key so a wrong key fails here, not at the node.
    """
    body, old_sig = decode_header(header)
    period = kes_evolution(body, slots_per_kes_period)
    hot_vk = bytes(body[HB_OPCERT][0])
    if praos_kes.derive_ver_key(kes_sign_key) != hot_vk:
        raise ValueError("KES key does not match the header's opcert hot key")
    if not praos_kes.verify(hot_vk, period, cbor2.dumps(body), old_sig):
        raise ValueError("source header's own KES signature does not verify; wrong slotsPerKESPeriod?")
    if prev_hash is not None:
        body[HB_PREV_HASH] = bytes(prev_hash)
    if segments is not None:
        body[HB_BODY_SIZE] = body_size(segments)
        body[HB_BODY_HASH] = body_hash(segments)
    message = cbor2.dumps(body)
    signature = praos_kes.sign(kes_sign_key, period, message)
    return encode_array([message, cbor2.dumps(signature)])


# --------------------------------------------------------------------------- #
# Blocks
# --------------------------------------------------------------------------- #

def split_block(block: bytes) -> tuple[bytes, tuple[bytes, bytes, bytes, bytes]]:
    """(header, body segments) of a Conway block ``[header, bodies, wits, aux, invalid]``."""
    items = array_items(block)
    if len(items) != 5:
        raise ValueError(f"expected a 5-element block, got {len(items)}")
    return items[0], (items[1], items[2], items[3], items[4])


def assemble_block(header: bytes, segments: tuple[bytes, bytes, bytes, bytes]) -> bytes:
    return encode_array([header, *segments])


# --------------------------------------------------------------------------- #
# Wire substitution (used by byzantine_mux_proxy in ``forge_substitute`` mode)
# --------------------------------------------------------------------------- #

CHAINSYNC = 2
BLOCKFETCH = 3


def _point_hash(point_raw: bytes) -> bytes | None:
    point = cbor2.loads(point_raw)
    if isinstance(point, list) and len(point) == 2 and isinstance(point[1], (bytes, bytearray)):
        return bytes(point[1])
    return None


def _map_point(point_raw: bytes, mapping: dict[bytes, bytes]) -> bytes:
    h = _point_hash(point_raw)
    if h is None or h not in mapping:
        return point_raw
    slot_raw = array_items(point_raw)[0]
    return encode_array([slot_raw, cbor2.dumps(mapping[h])])


def _map_tip(tip_raw: bytes, mapping: dict[bytes, bytes]) -> bytes:
    point_raw, block_no_raw = array_items(tip_raw)
    return encode_array([_map_point(point_raw, mapping), block_no_raw])


def _brief(fields: dict) -> dict:
    return {"slot": fields["slot"], "block_number": fields["block_number"], "real_hash": fields["hash"]}


class ForgeSubstitution:
    """Serve a forged block in place of one real block, and re-parent its descendants.

    Shared by every proxied connection so all consumers see the same fork. The
    forge target is the first real header that (a) is at or after ``armed_slot``,
    (b) has an empty body (so dropping its body loses no transaction), and
    (c) is signed under the opcert our KES key certifies. Its body is replaced by
    ``transactions``; every later header whose parent was rewritten is re-signed
    onto the forged chain (same slot, VRF and body), so the fork stays adoptable
    until the epoch nonce it feeds is used (two epochs on).
    ``armed_slot`` is None until the driver arms the forge (setup confirmed on chain).
    """

    def __init__(self, *, kes_sign_key: bytes, slots_per_kes_period: int, transactions: list[bytes],
                 armed_slot: int | None = None, record=None):
        import threading  # noqa: PLC0415

        self.kes_sign_key = kes_sign_key
        self.slots_per_kes_period = int(slots_per_kes_period)
        self.tx_parts = [split_transaction(tx) for tx in transactions]
        self.segments = body_segments(self.tx_parts)
        self.armed_slot = armed_slot
        self.lock = threading.RLock()
        self.real_to_forged: dict[bytes, bytes] = {}
        self.forged_to_real: dict[bytes, bytes] = {}
        self.forged_headers: dict[bytes, bytes] = {}   # real hash -> forged header bytes
        self.target_real: bytes | None = None
        self.events: list[dict] = []
        self._record = record or (lambda event: None)

    # ----------------------------------------------------------------- state
    def arm(self, slot: int) -> None:
        with self.lock:
            if self.armed_slot is None:
                self.armed_slot = int(slot)
                self._note({"kind": "armed", "slot": int(slot)})

    def _note(self, event: dict) -> None:
        self.events.append(event)
        self._record(event)

    def summary(self) -> dict:
        with self.lock:
            target = self.target_real
            return {
                "armed_slot": self.armed_slot,
                "target_real_hash": target.hex() if target else None,
                "forged_hash": self.real_to_forged[target].hex() if target else None,
                "forged_count": len(self.real_to_forged),
                "crafted_tx_count": len(self.tx_parts),
                "crafted_invalid_indices": cbor2.loads(self.segments[3]),
                "crafted_body_hash": body_hash(self.segments).hex(),
                "crafted_body_size": body_size(self.segments),
                "served_target_blocks": sum(1 for e in self.events if e.get("kind") == "served_block" and e.get("target")),
            }

    # ------------------------------------------------------------- headers
    def _forge_header(self, header: bytes) -> bytes | None:
        """Forged replacement for a real header, or None to pass it through."""
        real = header_hash(header)
        with self.lock:
            if real in self.forged_headers:
                return self.forged_headers[real]
            fields = header_fields(header)
            parent = bytes.fromhex(fields["prev_hash"]) if fields["prev_hash"] else None
            if parent is not None and parent in self.real_to_forged:
                forged = resign_header(header, kes_sign_key=self.kes_sign_key,
                                       slots_per_kes_period=self.slots_per_kes_period,
                                       prev_hash=self.real_to_forged[parent])
                kind = "reparented"
            elif (self.target_real is None and self.armed_slot is not None
                  and fields["slot"] >= self.armed_slot):
                if fields["body_size"] != body_size(EMPTY_BODY_SEGMENTS):
                    self._note({"kind": "skipped", "reason": "non-empty real body", **_brief(fields)})
                    return None
                try:
                    forged = resign_header(header, kes_sign_key=self.kes_sign_key,
                                           slots_per_kes_period=self.slots_per_kes_period,
                                           segments=self.segments)
                except ValueError as exc:
                    self._note({"kind": "skipped", "reason": str(exc), **_brief(fields)})
                    return None
                self.target_real = real
                kind = "forged"
            else:
                return None
            forged_hash = header_hash(forged)
            self.real_to_forged[real] = forged_hash
            self.forged_to_real[forged_hash] = real
            self.forged_headers[real] = forged
            self._note({"kind": kind, **_brief(fields), "forged_hash": forged_hash.hex(),
                        "forged_body_hash": header_fields(forged)["body_hash"]})
            return forged

    # ------------------------------------------------------------ messages
    def transform(self, protocol: int, from_responder: bool, message: bytes) -> bytes:
        """Rewrite one complete mini-protocol message; anything unknown passes through."""
        try:
            items = array_items(message)
            key = cbor2.loads(items[0])
        except Exception:  # noqa: BLE001 - not ours to judge; relay verbatim
            return message
        if protocol == CHAINSYNC:
            return self._chainsync(from_responder, key, items, message)
        if protocol == BLOCKFETCH:
            return self._blockfetch(from_responder, key, items, message)
        return message

    def _chainsync(self, from_responder, key, items, message):
        if from_responder and key == 2 and len(items) == 3:          # MsgRollForward
            era_raw, wrapped_raw = array_items(items[1])
            wrapped = cbor2.loads(wrapped_raw)
            if not (isinstance(wrapped, cbor2.CBORTag) and wrapped.tag == 24):
                return message
            forged = self._forge_header(bytes(wrapped.value))
            header_raw = items[1] if forged is None else encode_array([era_raw, tag24(forged)])
            with self.lock:
                tip = _map_tip(items[2], self.real_to_forged)
            return encode_array([items[0], header_raw, tip])
        if from_responder and key in (3, 5) and len(items) == 3:     # RollBackward / IntersectFound
            with self.lock:
                return encode_array([items[0], _map_point(items[1], self.real_to_forged),
                                     _map_tip(items[2], self.real_to_forged)])
        if from_responder and key == 6 and len(items) == 2:          # IntersectNotFound
            with self.lock:
                return encode_array([items[0], _map_tip(items[1], self.real_to_forged)])
        if not from_responder and key == 4 and len(items) == 2:      # FindIntersect
            with self.lock:
                points = [_map_point(p, self.forged_to_real) for p in array_items(items[1])]
            return encode_array([items[0], encode_array(points)])
        return message

    def _blockfetch(self, from_responder, key, items, message):
        if not from_responder and key == 0 and len(items) == 3:      # MsgRequestRange
            with self.lock:
                return encode_array([items[0], _map_point(items[1], self.forged_to_real),
                                     _map_point(items[2], self.forged_to_real)])
        if from_responder and key == 4 and len(items) == 2:          # MsgBlock
            wrapped = cbor2.loads(items[1])
            if not (isinstance(wrapped, cbor2.CBORTag) and wrapped.tag == 24):
                return message
            era_raw, block_raw = array_items(bytes(wrapped.value))
            header, segments = split_block(block_raw)
            real = header_hash(header)
            with self.lock:
                forged = self.forged_headers.get(real)
                is_target = real == self.target_real
                if forged is None:
                    return message
                if is_target:
                    segments = self.segments
                self._note({"kind": "served_block", "real_hash": real.hex(),
                            "forged_hash": header_hash(forged).hex(), "target": is_target})
            block = assemble_block(forged, segments)
            return encode_array([items[0], tag24(encode_array([era_raw, block]))])
        return message
