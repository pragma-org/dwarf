#!/usr/bin/env python3
"""Assemble a 2-tx block body segments file for cod-forge: body = [setup-A, setup-B] (intra-block
chained; B spends A#1). segments = [[A_body,B_body],[A_wits,B_wits], aux_map{}, invalid[]]. Both valid."""
import json, hashlib, sys
from pathlib import Path
import cbor2
FIXROOT = Path(sys.argv[1]); TXDIR = Path(sys.argv[2]); OUTFILE = Path(sys.argv[3])
sys.path.insert(0, str(FIXROOT))
from txedit import split_tx  # noqa

def load_body_wits(name):
    raw = bytes.fromhex(json.loads((TXDIR / name).read_text())["cborHex"])
    top = cbor2.loads(raw)
    b, w, _ = split_tx(raw)
    return cbor2.loads(b), cbor2.loads(w), (top[2] if len(top) >= 3 else True), b

ab, aw, av, ab_bytes = load_body_wits("setup-mint-A.tx")
bb, bw, bv, bb_bytes = load_body_wits("setup-mint-B.tx")
assert av and bv, "both must be is_valid"
segs = [[ab, bb], [aw, bw], {}, []]
seg_hex = cbor2.dumps(segs).hex()
out = {
    "description": "multiasset-overflow setup block body = [setup-mint-A, setup-mint-B] (intra-block chained)",
    "label": "multiasset-setup",
    "body_segments_cbor_hex": seg_hex,
    "txids": [hashlib.blake2b(ab_bytes, digest_size=32).hexdigest(),
              hashlib.blake2b(bb_bytes, digest_size=32).hexdigest()],
    "n_txs": 2, "is_valid": True, "invalid_transactions": [],
    "note_for_codforge": ("forge header committing body_hash+size over body_segments_cbor_hex; "
                          "same params as the exunits forge: parent slot=1000 hash=181e9b48... height=213, "
                          "forge slot=1209 height=214 pool=97b0 forced-nonce=3a5e3601 body-hash-consistent. "
                          "Applying this block gives amaru two i64::MAX-X UTxOs: A#0 and B#0."),
}
OUTFILE.write_text(json.dumps(out, indent=2) + "\n")
dec = cbor2.loads(bytes.fromhex(seg_hex))
print(f"wrote {OUTFILE}")
print(f"n_bodies={len(dec[0])} n_wits={len(dec[1])} aux={dec[2]} invalid={dec[3]}")
print(f"A txid={out['txids'][0]}")
print(f"B txid={out['txids'][1]}")
