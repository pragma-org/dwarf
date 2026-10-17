#!/usr/bin/env python3
"""Build signed redeemer-container controls without changing transaction bodies."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def _head(data: bytes, offset: int) -> tuple[int, int, int | None]:
    if offset >= len(data):
        raise ValueError("truncated CBOR item")
    first = data[offset]
    major, additional = first >> 5, first & 0x1F
    offset += 1
    if additional < 24:
        return major, offset, additional
    widths = {24: 1, 25: 2, 26: 4, 27: 8}
    if additional == 31:
        return major, offset, None
    width = widths.get(additional)
    if width is None or offset + width > len(data):
        raise ValueError("unsupported or truncated CBOR length")
    return major, offset + width, int.from_bytes(data[offset : offset + width], "big")


def _item_end(data: bytes, offset: int) -> int:
    major, content, value = _head(data, offset)
    if major in (0, 1, 7):
        if value is None:
            raise ValueError("unexpected indefinite scalar")
        return content
    if major in (2, 3):
        if value is not None:
            end = content + value
            if end > len(data):
                raise ValueError("truncated CBOR string")
            return end
        cursor = content
        while cursor < len(data) and data[cursor] != 0xFF:
            child_major, _, child_value = _head(data, cursor)
            if child_major != major or child_value is None:
                raise ValueError("invalid indefinite CBOR string chunk")
            cursor = _item_end(data, cursor)
        if cursor >= len(data):
            raise ValueError("unterminated CBOR string")
        return cursor + 1
    if major in (4, 5):
        cursor = content
        remaining = None if value is None else value * (2 if major == 5 else 1)
        if remaining is None:
            while cursor < len(data) and data[cursor] != 0xFF:
                cursor = _item_end(data, cursor)
            if cursor >= len(data):
                raise ValueError("unterminated CBOR collection")
            return cursor + 1
        for _ in range(remaining):
            cursor = _item_end(data, cursor)
        return cursor
    if major == 6:
        if value is None:
            raise ValueError("indefinite CBOR tag")
        return _item_end(data, content)
    raise ValueError("unsupported CBOR item")


def split_signed_transaction(payload: bytes) -> tuple[bytes, bytes, bytes, bytes]:
    major, cursor, count = _head(payload, 0)
    if major != 4 or count != 4 or cursor != 1:
        raise ValueError("signed transaction must be a definite four-item CBOR array")
    items = []
    for _ in range(4):
        end = _item_end(payload, cursor)
        items.append(payload[cursor:end])
        cursor = end
    if cursor != len(payload):
        raise ValueError("signed transaction has trailing bytes")
    return tuple(items)  # type: ignore[return-value]


def _map_entries(raw: bytes) -> list[tuple[int, bytes, bytes]]:
    major, cursor, count = _head(raw, 0)
    if major != 5 or count is None or count >= 24:
        raise ValueError("witness set must be a small definite CBOR map")
    entries = []
    for _ in range(count):
        key_start = cursor
        key_end = _item_end(raw, key_start)
        key_major, key_content, key = _head(raw, key_start)
        if key_major != 0 or key is None or key_content != key_end:
            raise ValueError("witness key must be an unsigned integer")
        value_end = _item_end(raw, key_end)
        entries.append((key, raw[key_start:key_end], raw[key_end:value_end]))
        cursor = value_end
    if cursor != len(raw):
        raise ValueError("witness set has trailing bytes")
    return entries


def _rewrite_witness_map(raw: bytes, *, redeemers: bytes | None) -> bytes:
    entries = [entry for entry in _map_entries(raw) if entry[0] != 5]
    if redeemers is not None:
        entries.append((5, b"\x05", redeemers))
    entries.sort(key=lambda entry: entry[0])
    if len(entries) >= 24:
        raise ValueError("rewritten witness set is too large")
    return bytes([0xA0 | len(entries)]) + b"".join(key + value for _, key, value in entries)


def witness_map_keys(payload: bytes) -> tuple[int, ...]:
    return tuple(key for key, _, _ in _map_entries(split_signed_transaction(payload)[1]))


def _signed_with_witnesses(payload: bytes, witnesses: bytes) -> bytes:
    body, _, is_valid, auxiliary = split_signed_transaction(payload)
    return b"\x84" + body + witnesses + is_valid + auxiliary


def _read_envelope(path: Path) -> tuple[dict[str, Any], bytes]:
    body = json.loads(path.read_text(encoding="utf-8"))
    payload = bytes.fromhex(str(body["cborHex"]))
    split_signed_transaction(payload)
    return body, payload


def _write_case(destination: Path, case_id: str, template: dict[str, Any], payload: bytes) -> dict[str, Any]:
    filename = f"{case_id}.tx"
    envelope = {
        "type": template.get("type", "Tx ConwayEra"),
        "description": case_id,
        "cborHex": payload.hex(),
    }
    (destination / filename).write_text(
        json.dumps(envelope, sort_keys=True) + "\n", encoding="utf-8"
    )
    return {
        "case_id": case_id,
        "tx_file": filename,
        "cbor_size": len(payload),
        "cbor_sha256": hashlib.sha256(payload).hexdigest(),
    }


def build_redeemer_container_corpus(
    *, no_script_source: Path, valid_script_source: Path, destination: Path
) -> dict[str, Any]:
    no_script_envelope, no_script = _read_envelope(no_script_source)
    valid_script_envelope, valid_script = _read_envelope(valid_script_source)
    no_script_witnesses = split_signed_transaction(no_script)[1]
    valid_script_witnesses = split_signed_transaction(valid_script)[1]
    if 5 in witness_map_keys(no_script):
        raise ValueError("no-script source unexpectedly contains redeemers")
    if 5 not in witness_map_keys(valid_script):
        raise ValueError("valid script source must contain redeemers")
    destination.mkdir(parents=True, exist_ok=False)
    phase1_both = {"target": "phase1_reject", "reference": "phase1_reject"}
    decoder_split = {"target": "phase1_reject", "reference": "decode_reject"}
    variants = [
        ("no-script-redeemers-omitted", no_script_envelope, no_script, phase1_both),
        (
            "no-script-redeemers-empty-list",
            no_script_envelope,
            _signed_with_witnesses(no_script, _rewrite_witness_map(no_script_witnesses, redeemers=b"\x80")),
            decoder_split,
        ),
        (
            "no-script-redeemers-empty-map",
            no_script_envelope,
            _signed_with_witnesses(no_script, _rewrite_witness_map(no_script_witnesses, redeemers=b"\xa0")),
            decoder_split,
        ),
        ("script-redeemer-valid-control", valid_script_envelope, valid_script, phase1_both),
        (
            "script-redeemers-omitted",
            valid_script_envelope,
            _signed_with_witnesses(valid_script, _rewrite_witness_map(valid_script_witnesses, redeemers=None)),
            phase1_both,
        ),
    ]
    cases = []
    for case_id, envelope, payload, expected in variants:
        case = _write_case(destination, case_id, envelope, payload)
        case["expected"] = dict(expected)
        cases.append(case)
    manifest = {
        "schema_version": 1,
        "purpose": "redeemer-container-decoder-differential",
        "sources": {
            "no_script": str(no_script_source),
            "valid_script": str(valid_script_source),
        },
        "cases": cases,
    }
    (destination / "corpus.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-script-source", type=Path, required=True)
    parser.add_argument("--valid-script-source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    build_redeemer_container_corpus(
        no_script_source=args.no_script_source,
        valid_script_source=args.valid_script_source,
        destination=args.destination,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
