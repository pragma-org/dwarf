#!/usr/bin/env python3
"""Build a CLEAN 4-element Conway tx with a bare STAKE-ADDRESS output (0xe0 header) to isolate the
stake-addr-output divergence from the tx-arity finding.

Base: mp-base (spends funded 9708b921), value-balanced. Only deviation = output[0] address is a bare
stake credential (0xe0 | 28-byte keyhash), which is not a valid payment output. cardano-node
decode-rejects the 0xe0 output; amaru accepts (lenient address decode) -> accept-invalid. Full
4-element tx [body, wits, is_valid=true, aux=null] so cardano's rejection is on the OUTPUT, not arity.

Emits stakeaddr-output-preflight.cbor (+ block-segments for a future forge).
"""
import json, cbor2, hashlib, sys
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "antithesis/cardano_amaru_adversarial/fixture"
sys.path.insert(0, str(FIX / "collateral"))
from redeemers import split_tx  # noqa: E402
OUT = Path("/home/nigel/forge-work/pathb/outputs/forged-block")


def raw_vkey(k):
    return k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def main():
    seed = bytes.fromhex(json.loads((FIX / "funding" / "payment.skey").read_text())["cborHex"])[2:]
    pay = Ed25519PrivateKey.from_private_bytes(seed)
    raw = bytes.fromhex(json.loads((FIX / "mempool" / "mp-base.tx").read_text())["cborHex"])
    b, w, tail = split_tx(raw)
    base = cbor2.loads(b); tmpl_wits = cbor2.loads(w)
    inputs = base[0]; out0 = base[1][0]
    val = out0[1] if isinstance(out0[1], int) else out0[1][0]
    fee = base[2]
    # bare stake-address output: header 0xe0 (stake key-hash, testnet) + 28-byte keyhash
    stake_kh = hashlib.blake2b(raw_vkey(Ed25519PrivateKey.generate()), digest_size=28).digest()
    stake_addr = bytes([0xe0]) + stake_kh  # 29 bytes, NOT a valid payment address
    body = {0: inputs, 1: [[stake_addr, val]], 2: fee}
    body_b = cbor2.dumps(body)
    h = hashlib.blake2b(body_b, digest_size=32).digest()
    def rewrap(orig, lst):
        return cbor2.CBORTag(orig.tag, lst) if isinstance(orig, cbor2.CBORTag) else lst
    wits = {0: rewrap(tmpl_wits[0], [[raw_vkey(pay), pay.sign(h)]])}
    # full 4-element Conway tx (so cardano rejects on the OUTPUT, not arity)
    preflight = cbor2.dumps([body, wits, True, None])
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "stakeaddr-output-preflight.cbor").write_bytes(preflight)
    segs = [[body], [wits], {}, []]
    (OUT / "stakeaddr-output-block-segments.json").write_text(json.dumps({
        "description": "bare stake-address (0xe0) output, 4-element tx: amaru accepts (lenient address "
                       "decode); cardano-node decode-rejects the 0xe0 output (NOT an arity reject).",
        "label": "stakeaddr-output", "body_segments_cbor_hex": cbor2.dumps(segs).hex(),
        "txid": hashlib.blake2b(body_b, digest_size=32).hexdigest(),
        "stake_addr": stake_addr.hex(), "n_txs": 1, "is_valid": True, "invalid_transactions": []}, indent=2) + "\n")
    print("stakeaddr-output txid", hashlib.blake2b(body_b, digest_size=32).hexdigest()[:16],
          "addr", stake_addr.hex()[:10], "tx_elements 4 preflight_bytes", len(preflight))


if __name__ == "__main__":
    main()
