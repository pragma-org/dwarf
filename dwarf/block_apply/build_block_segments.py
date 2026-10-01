#!/usr/bin/env python3
"""Generic single-tx -> block-body-segments builder for cod-forge M2.
Usage: build_block_segments.py <path/to.tx> <label> <out-name.json>
Emits body_segments = [ [tx_body], [tx_wits], aux_map, invalid ] where invalid=[0] iff is_valid=false.
"""
import json, cbor2, hashlib, sys
from pathlib import Path

FIX = Path("/home/nigel/dwarf-pragma/antithesis/cardano_amaru_adversarial/fixture")
sys.path.insert(0, str(FIX)); sys.path.insert(0, str(FIX / "collateral"))
from redeemers import split_tx  # noqa: E402

OUT = Path("/home/nigel/forge-work/pathb/outputs/forged-block")


def main():
    txpath, label, outname = sys.argv[1], sys.argv[2], sys.argv[3]
    raw = bytes.fromhex(json.loads(Path(txpath).read_text())["cborHex"])
    top = cbor2.loads(raw)
    is_valid = top[2] if len(top) >= 3 else True
    aux = top[3] if len(top) >= 4 else None
    b, w, _ = split_tx(raw)
    tx_body, tx_wits = cbor2.loads(b), cbor2.loads(w)
    aux_map = {} if aux is None else {0: aux}
    invalid = [] if is_valid else [0]
    segs = [[tx_body], [tx_wits], aux_map, invalid]
    seg_hex = cbor2.dumps(segs).hex()
    txid = hashlib.blake2b(b, digest_size=32).hexdigest()
    out = {
        "description": f"{label} block body from {Path(txpath).name}",
        "label": label, "body_segments_cbor_hex": seg_hex, "txid": txid,
        "n_txs": 1, "is_valid": bool(is_valid), "invalid_transactions": invalid,
        "note_for_codforge": "compute segwit body_hash+size over body_segments_cbor_hex; forge header; slot 1211; prev f4d474b8; height 234",
    }
    (OUT / outname).write_text(json.dumps(out, indent=2) + "\n")
    dec = cbor2.loads(bytes.fromhex(seg_hex))
    print(f"wrote {OUT/outname} | label={label} txid={txid[:16]} is_valid={is_valid} invalid={invalid} "
          f"segments(n_bodies={len(dec[0])},aux={type(dec[2]).__name__})")


if __name__ == "__main__":
    main()
