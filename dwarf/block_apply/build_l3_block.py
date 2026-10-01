#!/usr/bin/env python3
"""Stage the L3-state block body (M2 positive control) in segments format for cod-forge.
L3 tx (l3-donation-bal.tx) is is_valid=FALSE -> block invalid_transactions=[0].
body_segments = [ [l3_tx_body], [l3_tx_wits], aux_map, [0] ].
"""
import json, cbor2, hashlib
from pathlib import Path

FIX = Path("/home/nigel/dwarf-pragma/antithesis/cardano_amaru_adversarial/fixture")
import sys
sys.path.insert(0, str(FIX)); sys.path.insert(0, str(FIX / "collateral"))
from redeemers import split_tx  # noqa: E402

OUT = Path("/home/nigel/forge-work/pathb/outputs/forged-block")
raw = bytes.fromhex(json.loads((FIX / "donation_invalid" / "l3-donation-bal.tx").read_text())["cborHex"])
top = cbor2.loads(raw)
print("full tx elems:", len(top), "| is_valid(elem2):", top[2], "| aux(elem3) type:", type(top[3]).__name__)

b, w, tail = split_tx(raw)
tx_body = cbor2.loads(b)
tx_wits = cbor2.loads(w)
aux = top[3]  # element 3 = auxiliary_data (map/None)

assert top[2] is False, f"expected is_valid=false, got {top[2]}"

tx_bodies = [tx_body]
tx_wits_list = [tx_wits]
aux_map = {} if aux is None else {0: aux}
invalid = [0]  # L3 tx is is_valid=false -> listed in invalid_transactions

body_segs = [tx_bodies, tx_wits_list, aux_map, invalid]
seg_hex = cbor2.dumps(body_segs).hex()
txid = hashlib.blake2b(b, digest_size=32).hexdigest()

out = {
    "description": "L3-state (donation on is_valid=false) block body. amaru applies -> collateral consumed + donation credited to treasury (the credit-on-invalid divergence).",
    "body_segments_cbor_hex": seg_hex,
    "l3_txid": txid,
    "n_txs": 1,
    "invalid_transactions": [0],
    "note_for_codforge": "compute Conway segwit body_hash + body_size over body_segments_cbor_hex; forge header committing them; slot=eligible 97b0 slot (1211 worked); prev f4d474b8; height 234",
}
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "l3-block-segments.json").write_text(json.dumps(out, indent=2) + "\n")
print("wrote", OUT / "l3-block-segments.json")
print("l3_txid", txid, "| invalid_transactions", invalid, "| aux_map", "present" if aux_map else "empty")
dec = cbor2.loads(bytes.fromhex(seg_hex))
print("segments decode OK: n_bodies", len(dec[0]), "n_wits", len(dec[1]), "aux", type(dec[2]).__name__, "invalid", dec[3])
print("has donation(key22):", tx_body.get(22), "| collateral(key13):", 13 in tx_body, "| total_coll(key17):", 17 in tx_body)
