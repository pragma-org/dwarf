#!/usr/bin/env python3
"""R1c: witness / arity leniency. Vary the witness set on a tx spending funded 9708b921 (value-balanced
body, so the ONLY deviation is the witnesses) and see whether amaru accepts an improperly-witnessed
spend that cardano rejects.

Cases:
  missing-witness   : empty witness set {}                      -> cardano MissingVKeyWitnessesUTXOW
  wrong-key-witness : witness from a FRESH key (not the funder) -> cardano missing required witness
  extra-witness     : valid funder witness + an extra fresh one -> usually ALLOWED (expect AGREE)
  duplicate-witness : the funder witness listed twice           -> expect AGREE
  vkey-arity-3elem  : vkey witness as a 3-element array [vk,sig,x] -> malformed arity (witness-arity leniency reconfirm)
"""
import json, cbor2, hashlib, sys
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "antithesis/cardano_amaru_adversarial/fixture"
sys.path.insert(0, str(FIX / "collateral"))
from redeemers import split_tx  # noqa: E402
OUT = Path("/home/nigel/forge-work/pathb/outputs/forged-block/r1c")


def rvk(k):
    return k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def main():
    seed = bytes.fromhex(json.loads((FIX / "funding" / "payment.skey").read_text())["cborHex"])[2:]
    pay = Ed25519PrivateKey.from_private_bytes(seed)
    raw = bytes.fromhex(json.loads((FIX / "mempool" / "mp-base.tx").read_text())["cborHex"])
    b, w, tail = split_tx(raw)
    base = cbor2.loads(b); tmpl = cbor2.loads(w)
    inputs, out0, fee = base[0], base[1][0], base[2]
    body = {0: inputs, 1: [[bytes(out0[0]), (out0[1] if isinstance(out0[1], int) else out0[1][0])]], 2: fee}
    body_b = cbor2.dumps(body)
    h = hashlib.blake2b(body_b, digest_size=32).digest()
    orig = tmpl[0]
    tag = orig.tag if isinstance(orig, cbor2.CBORTag) else None

    def wrap(lst):
        return cbor2.CBORTag(tag, lst) if tag is not None else lst

    fresh = Ed25519PrivateKey.generate()
    fund_wit = [rvk(pay), pay.sign(h)]
    fresh_wit = [rvk(fresh), fresh.sign(h)]
    cases = {
        "missing-witness": {},
        "wrong-key-witness": {0: wrap([fresh_wit])},
        "extra-witness": {0: wrap([fund_wit, fresh_wit])},
        "duplicate-witness": {0: wrap([fund_wit, fund_wit])},
        "vkey-arity-3elem": {0: wrap([[rvk(pay), pay.sign(h), b"\x00"]])},
    }
    OUT.mkdir(parents=True, exist_ok=True)
    for label, wits in cases.items():
        (OUT / f"{label}-preflight.cbor").write_bytes(cbor2.dumps([body, wits, True, None]))
        print(f"[{label}] written")


if __name__ == "__main__":
    main()
