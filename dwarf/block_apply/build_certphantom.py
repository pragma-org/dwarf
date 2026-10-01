#!/usr/bin/env python3
"""Build CERT-PHANTOM block body: a tx with a StakeDelegation cert for a NEVER-REGISTERED stake
credential delegating to pool 97b0. amaru accepts (bind_left, no registration lookup); cardano
rejects StakeKeyNotRegisteredDELEG. Fresh stake key so the tx is properly witnessed (isolates
registration as the only divergence). is_valid=true, invalid=[]. -> cert-phantom-block-segments.json
"""
import json, cbor2, hashlib
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

FIX = Path("/home/nigel/dwarf-pragma/antithesis/cardano_amaru_adversarial/fixture")
import sys
sys.path.insert(0, str(FIX)); sys.path.insert(0, str(FIX / "collateral"))
from redeemers import split_tx  # noqa: E402
OUT = Path("/home/nigel/forge-work/pathb/outputs/forged-block")
POOL97B0 = bytes.fromhex("97b0f582dcf255b2c776f3540454b99fbf26e48833f275bfcd63bfcd")


def raw_vkey(k):
    return k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def main():
    # funding key (spends 9708b921)
    seed = bytes.fromhex(json.loads((FIX / "funding" / "payment.skey").read_text())["cborHex"])[2:]
    pay = Ed25519PrivateKey.from_private_bytes(seed)
    # fresh, NEVER-REGISTERED stake key
    stake = Ed25519PrivateKey.generate()
    stake_vk = raw_vkey(stake)
    stake_keyhash = hashlib.blake2b(stake_vk, digest_size=28).digest()

    # base body from mp-base (input 9708b921#0, output to funding addr, fee); drop aux(key7); add cert(key4)
    raw = bytes.fromhex(json.loads((FIX / "mempool" / "mp-base.tx").read_text())["cborHex"])
    b, w, tail = split_tx(raw)
    base = cbor2.loads(b)
    tmpl_wits = cbor2.loads(w)
    inputs = base[0]
    out0 = base[1][0]
    fund_addr = bytes(out0[0])
    val = out0[1] if isinstance(out0[1], int) else out0[1][0]
    fee = base[2]
    # StakeDelegation cert (Conway): [2, stake_credential=[0, keyhash], pool_keyhash]
    cert = [2, [0, stake_keyhash], POOL97B0]
    body = {0: inputs, 1: [[fund_addr, val]], 2: fee, 4: [cert]}  # no deposit for delegation
    body_b = cbor2.dumps(body)
    h = hashlib.blake2b(body_b, digest_size=32).digest()

    # witnesses: funding key (for the input) + fresh stake key (for the delegation cert)
    def rewrap(orig, lst):
        return cbor2.CBORTag(orig.tag, lst) if isinstance(orig, cbor2.CBORTag) else lst
    vk_wits = [[raw_vkey(pay), pay.sign(h)], [stake_vk, stake.sign(h)]]
    wits = {0: rewrap(tmpl_wits[0], vk_wits)}

    # block segments: [ [body], [wits], {}, [] ] (is_valid=true)
    segs = [[body], [wits], {}, []]
    seg_hex = cbor2.dumps(segs).hex()
    txid = hashlib.blake2b(body_b, digest_size=32).hexdigest()

    out = {
        "description": "CERT-PHANTOM: StakeDelegation cert for a NEVER-REGISTERED stake credential -> pool 97b0. amaru accepts (bind_left no reg lookup); cardano rejects StakeKeyNotRegisteredDELEG.",
        "label": "cert-phantom", "body_segments_cbor_hex": seg_hex, "txid": txid,
        "stake_keyhash": stake_keyhash.hex(), "pool": POOL97B0.hex(),
        "n_txs": 1, "is_valid": True, "invalid_transactions": [],
        "note_for_codforge": "compute segwit body_hash+size; forge header slot 1211 prev f4d474b8 height 234",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "cert-phantom-block-segments.json").write_text(json.dumps(out, indent=2) + "\n")
    dec = cbor2.loads(bytes.fromhex(seg_hex))
    b0 = dec[0][0]
    print("wrote cert-phantom-block-segments.json | txid", txid[:16])
    print("stake_keyhash (unregistered)", stake_keyhash.hex()[:16], "-> pool 97b0")
    print("cert (body key4):", b0[4], "| n_wits", len(dec[1][0][0].value if isinstance(dec[1][0][0], cbor2.CBORTag) else dec[1][0][0]))


if __name__ == "__main__":
    main()
