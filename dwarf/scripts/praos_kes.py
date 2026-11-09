"""Sum6 KES (Praos header signatures) in pure Python.

The block-apply bridge re-signs a devnet pool's own header after it changes the
header body (new body hash, new parent). Cardano uses ``Sum6KES Ed25519DSIGN
Blake2b_256`` from cardano-crypto-class:

* A Sum_n sign key is ``sk_{n-1} || r1 (32-byte seed) || vk0 || vk1``; the
  Sum_0 (single Ed25519) sign key is its 32-byte seed. Sum6 = 32 + 6*96 = 608.
* A Sum_n verification key is ``blake2b256(vk0 || vk1)``; Sum_0 is the
  Ed25519 verification key.
* A Sum_n signature is ``sig_{n-1} || vk0 || vk1``; Sum_0 is the 64-byte
  Ed25519 signature. Sum6 = 64 + 6*64 = 448.
* A seed expands to ``(blake2b256(0x01 || s), blake2b256(0x02 || s))`` for the
  left and right subtree; the right subtree key is regenerated from ``r1``
  when the evolution passes the half-way point.

``sign`` computes the key for evolution ``t`` directly from the period-0 key
(the form stored in ``kes.skey``) instead of mutating it, like the Haskell
``UnsoundPureSignKeyKES`` the DWARF opcert adversary uses.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from cryptography.exceptions import InvalidSignature

DEPTH = 6
SEED_SIZE = 32
VK_SIZE = 32
ED25519_SIG_SIZE = 64


def _blake2b256(data: bytes) -> bytes:
    return hashlib.blake2b(data, digest_size=32).digest()


def sign_key_size(depth: int) -> int:
    return SEED_SIZE + depth * (SEED_SIZE + 2 * VK_SIZE)


def signature_size(depth: int) -> int:
    return ED25519_SIG_SIZE + depth * 2 * VK_SIZE


def total_periods(depth: int) -> int:
    return 1 << depth


def _ed25519_vk(seed: bytes) -> bytes:
    return Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def expand_seed(seed: bytes) -> tuple[bytes, bytes]:
    return _blake2b256(b"\x01" + seed), _blake2b256(b"\x02" + seed)


def gen_sign_key(seed: bytes, depth: int = DEPTH) -> bytes:
    """Period-0 sign key generated from a 32-byte seed (genKeyKES)."""
    if len(seed) != SEED_SIZE:
        raise ValueError("KES seed must be 32 bytes")
    if depth == 0:
        return bytes(seed)
    r0, r1 = expand_seed(seed)
    sk0 = gen_sign_key(r0, depth - 1)
    vk0 = derive_ver_key(sk0, depth - 1)
    vk1 = derive_ver_key(gen_sign_key(r1, depth - 1), depth - 1)
    return sk0 + r1 + vk0 + vk1


def _split(sk: bytes, depth: int) -> tuple[bytes, bytes, bytes, bytes]:
    inner = sign_key_size(depth - 1)
    sk0 = sk[:inner]
    r1 = sk[inner:inner + SEED_SIZE]
    vk0 = sk[inner + SEED_SIZE:inner + SEED_SIZE + VK_SIZE]
    vk1 = sk[inner + SEED_SIZE + VK_SIZE:inner + SEED_SIZE + 2 * VK_SIZE]
    return sk0, r1, vk0, vk1


def derive_ver_key(sk: bytes, depth: int = DEPTH) -> bytes:
    if len(sk) != sign_key_size(depth):
        raise ValueError(f"Sum{depth} KES sign key must be {sign_key_size(depth)} bytes, got {len(sk)}")
    if depth == 0:
        return _ed25519_vk(sk)
    _sk0, _r1, vk0, vk1 = _split(sk, depth)
    return _blake2b256(vk0 + vk1)


def sign(sk: bytes, period: int, message: bytes, depth: int = DEPTH) -> bytes:
    """Sign ``message`` at evolution ``period`` (relative to the opcert start)."""
    if len(sk) != sign_key_size(depth):
        raise ValueError(f"Sum{depth} KES sign key must be {sign_key_size(depth)} bytes, got {len(sk)}")
    if not 0 <= period < total_periods(depth):
        raise ValueError(f"KES period {period} outside 0..{total_periods(depth) - 1}")
    if depth == 0:
        return Ed25519PrivateKey.from_private_bytes(sk).sign(message)
    sk0, r1, vk0, vk1 = _split(sk, depth)
    half = total_periods(depth - 1)
    if period < half:
        inner = sign(sk0, period, message, depth - 1)
    else:
        inner = sign(gen_sign_key(r1, depth - 1), period - half, message, depth - 1)
    return inner + vk0 + vk1


def verify(vk: bytes, period: int, message: bytes, signature: bytes, depth: int = DEPTH) -> bool:
    if len(signature) != signature_size(depth) or not 0 <= period < total_periods(depth):
        return False
    if depth == 0:
        try:
            Ed25519PublicKey.from_public_bytes(vk).verify(signature, message)
            return True
        except (InvalidSignature, ValueError):
            return False
    inner_size = signature_size(depth - 1)
    inner = signature[:inner_size]
    vk0 = signature[inner_size:inner_size + VK_SIZE]
    vk1 = signature[inner_size + VK_SIZE:inner_size + 2 * VK_SIZE]
    if _blake2b256(vk0 + vk1) != vk:
        return False
    half = total_periods(depth - 1)
    if period < half:
        return verify(vk0, period, message, inner, depth - 1)
    return verify(vk1, period - half, message, inner, depth - 1)


def check_sign_key(sk: bytes, depth: int = DEPTH) -> bool:
    """Every stored right-subtree seed must regenerate its stored vk1 (key integrity)."""
    if depth == 0:
        return len(sk) == SEED_SIZE
    sk0, r1, vk0, vk1 = _split(sk, depth)
    return (derive_ver_key(sk0, depth - 1) == vk0
            and derive_ver_key(gen_sign_key(r1, depth - 1), depth - 1) == vk1
            and check_sign_key(sk0, depth - 1))


def _envelope_bytes(path: Path) -> bytes:
    import cbor2  # noqa: PLC0415 - controller/framework image dependency

    body = json.loads(Path(path).read_text(encoding="utf-8"))
    raw = cbor2.loads(bytes.fromhex(body["cborHex"]))
    if not isinstance(raw, (bytes, bytearray)):
        raise ValueError(f"{path}: cborHex is not a byte string")
    return bytes(raw)


def load_sign_key(path: Path) -> bytes:
    """Read a cardano-cli ``kes.skey`` text envelope (period-0 Sum6 key)."""
    sk = _envelope_bytes(path)
    if len(sk) != sign_key_size(DEPTH):
        raise ValueError(f"{path}: expected a {sign_key_size(DEPTH)}-byte Sum6 KES key, got {len(sk)}")
    return sk


def load_ver_key(path: Path) -> bytes:
    vk = _envelope_bytes(path)
    if len(vk) != VK_SIZE:
        raise ValueError(f"{path}: expected a 32-byte KES verification key")
    return vk
