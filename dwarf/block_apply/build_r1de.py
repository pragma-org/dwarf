#!/usr/bin/env python3
"""R1d (output min-UTxO / value-size) + R1e (validity-interval/TTL) knife-edges — cheaper
confirm-negatives. Each spends funded 9708b921, funding-witnessed, 4-element Conway tx."""
import json, cbor2, hashlib, sys
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "antithesis/cardano_amaru_adversarial/fixture"
sys.path.insert(0, str(FIX / "collateral"))
from redeemers import split_tx  # noqa: E402
OUT = Path("/home/nigel/forge-work/pathb/outputs/forged-block/r1de")


def rvk(k):
    return k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def main():
    seed = bytes.fromhex(json.loads((FIX / "funding" / "payment.skey").read_text())["cborHex"])[2:]
    pay = Ed25519PrivateKey.from_private_bytes(seed)
    raw = bytes.fromhex(json.loads((FIX / "mempool" / "mp-base.tx").read_text())["cborHex"])
    b, w, tail = split_tx(raw)
    base = cbor2.loads(b); tmpl = cbor2.loads(w)
    inputs, out0, fee = base[0], base[1][0], base[2]
    addr = bytes(out0[0]); val = out0[1] if isinstance(out0[1], int) else out0[1][0]
    orig = tmpl[0]; tag = orig.tag if isinstance(orig, cbor2.CBORTag) else None
    wrap = lambda lst: cbor2.CBORTag(tag, lst) if tag is not None else lst

    def fin(label, body):
        body_b = cbor2.dumps(body); h = hashlib.blake2b(body_b, digest_size=32).digest()
        wits = {0: wrap([[rvk(pay), pay.sign(h)]])}
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"{label}-preflight.cbor").write_bytes(cbor2.dumps([body, wits, True, None]))
        print(f"[{label}] keys {sorted(body)}")

    # R1d
    fin("output-below-min-utxo", {0: inputs, 1: [[addr, 1]], 2: fee})  # 1 lovelace output << min-UTxO
    # value-too-big: output with 200 distinct 32-byte asset names under one policy -> serialized value > maxValueSize(5000)
    policy = bytes(28)
    assets = {bytes([i]) + bytes(31): 1 for i in range(200)}
    fin("value-too-big", {0: inputs, 1: [[addr, [val, {policy: assets}]]], 2: fee})
    # R1e
    fin("ttl-in-past", {0: inputs, 1: [[addr, val]], 2: fee, 3: 1})                       # TTL=slot 1 (long past tip 1000)
    fin("validity-interval-inverted", {0: inputs, 1: [[addr, val]], 2: fee, 3: 1, 8: 999999})  # start 999999 > end 1


if __name__ == "__main__":
    main()
