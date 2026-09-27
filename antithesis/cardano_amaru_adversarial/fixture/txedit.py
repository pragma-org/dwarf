"""Shared helpers for fixture edits that cardano-cli cannot express (used by the family edits.py).

Split a signed Conway tx, re-encode an edited body, and re-sign it with the family's keys. Every
caller should run self_check() first: it aborts unless the cbor2 re-encoding of the unedited body
is byte-identical and the Python Ed25519 signatures equal the cardano-cli witnesses (Ed25519 is
deterministic), so an edited case differs from its source ONLY in the edited field.
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path

import cbor2
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parent / "collateral"))
from redeemers import split_tx  # noqa: E402,F401  (re-exported)


def load(root: Path, name: str) -> bytes:
    return bytes.fromhex(json.loads((root / name).read_text())["cborHex"])


def key(root: Path, name: str) -> Ed25519PrivateKey:
    seed = bytes.fromhex(json.loads((root / "keys" / f"{name}.skey").read_text())["cborHex"])[2:]
    return Ed25519PrivateKey.from_private_bytes(seed)


def vkey(k: Ed25519PrivateKey) -> bytes:
    return k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def items(x):
    """A witness/script list, possibly wrapped in a tag-258 set."""
    return list(x.value) if isinstance(x, cbor2.CBORTag) else list(x)


def rewrap(orig, new_list):
    return cbor2.CBORTag(orig.tag, new_list) if isinstance(orig, cbor2.CBORTag) else new_list


def write(root: Path, name: str, body: bytes, wits, tail: bytes, desc: str) -> None:
    """wits: a witness dict to encode, or the witness set's raw bytes (see raw_map)."""
    tx = b"\x84" + body + (wits if isinstance(wits, bytes) else cbor2.dumps(wits)) + tail
    (root / f"{name}.tx").write_text(json.dumps(
        {"type": "Tx ConwayEra", "description": desc, "cborHex": tx.hex()}, indent=4) + "\n")


def signed_wits(body: bytes, template: dict, signers, scripts) -> dict:
    h = hashlib.blake2b(body, digest_size=32).digest()
    w = {0: rewrap(template[0], [[vkey(k), k.sign(h)] for k in signers])}
    if scripts:
        w[1] = rewrap(template.get(1, []), scripts)
    return w


def raw_map(data: bytes) -> dict:
    """{key: raw value bytes} of a definite-length CBOR map with small uint keys (a witness set).

    cbor2 decodes tag-258 sets into Python sets, whose iteration order is hash-randomised per
    process, so re-encoding a decoded witness set is NOT reproducible. Callers that keep a
    witness entry unchanged splice its original bytes back with encode_raw_map instead.
    """
    assert 0xa0 <= data[0] <= 0xb7, "expected a definite map with < 24 entries"
    stream = io.BytesIO(data[1:])
    dec = cbor2.CBORDecoder(stream)
    out = {}
    for _ in range(data[0] - 0xa0):
        k = dec.decode()
        start = stream.tell()
        dec.decode()
        out[k] = data[1 + start:1 + stream.tell()]
    return out


def encode_raw_map(entries: dict) -> bytes:
    """Encode {small uint key: raw value bytes} as a definite CBOR map, keys in the given order."""
    assert len(entries) < 24
    return bytes([0xa0 + len(entries)]) + b"".join(cbor2.dumps(k) + v for k, v in entries.items())


def self_check(body: bytes, wits: dict, signers) -> None:
    assert cbor2.dumps(cbor2.loads(body)) == body, "cbor2 body round-trip is not byte-identical"
    h = hashlib.blake2b(body, digest_size=32).digest()
    cli = {(bytes(v), bytes(s)) for v, s in items(wits[0])}
    assert {(vkey(k), k.sign(h)) for k in signers} == cli, "re-sign self-check failed"
