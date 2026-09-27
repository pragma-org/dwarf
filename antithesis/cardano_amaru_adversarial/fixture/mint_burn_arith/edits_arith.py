"""Uncovered value-conservation / arithmetic knife-edges, derived from mint-valid.tx.

Extends the mint/burn family (does NOT duplicate its int64-MIN burn, minUTxO, maxValueSize, or
asset surplus/deficit). Adds:
  conserve-ada-plus1   output ADA = balanced + 1  -> creates 1 lovelace (ValueNotConserved). amaru-accept = CRITICAL.
  conserve-ada-minus1  output ADA = balanced - 1  -> destroys 1 lovelace (ValueNotConserved).
  mint-int64-max       mint 2^63-1 MINT, output carries it (value-conserved) -> valid max boundary.
  mint-int64-over      mint 2^63 MINT (out of signed-int64 range), output carries it -> both must reject; amaru-accept = CRITICAL value creation via overflow.
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import cbor2

HERE = Path("/tmp/plutus-wt/antithesis/cardano_amaru_adversarial/fixture/mint_burn")
sys.path.insert(0, str(HERE.parent))
import txedit
from txedit import items, signed_wits, split_tx

MINT = b"MINT"
INT64_MAX = 2 ** 63 - 1
INT64_OVER = 2 ** 63


def main():
    body, wits_b, tail = split_tx(txedit.load(HERE, "mint-valid.tx"))
    wits = cbor2.loads(wits_b)
    pay, pol = txedit.key(HERE, "payment"), txedit.key(HERE, "policy")
    txedit.self_check(body, wits, [pay, pol])
    policy_script = items(wits[1])[0]
    base = cbor2.loads(body)
    pid = next(iter(base[9]))
    out0 = base[1][0]
    legacy = isinstance(out0, list)
    out_addr = out0[0]
    out_val = out0[1]
    ada = out_val[0] if isinstance(out_val, list) else out_val

    def output(value):
        return [out_addr, value] if legacy else {0: out_addr, 1: value}

    def case(name, b, desc):
        enc = cbor2.dumps(b)
        txedit.write(HERE, name, enc, signed_wits(enc, wits, [pay, pol], [policy_script]), tail, desc)
        print("wrote", name)

    # --- ADA-side value conservation off-by-one (mint 10 + output 10 MINT unchanged; only ADA off) ---
    tok = {pid: {MINT: 10}}
    b = dict(base); b[1] = [output([ada + 1, tok])]; b[9] = {pid: {MINT: 10}}
    case("conserve-ada-plus1", b, "output ADA = balanced + 1 lovelace (creates value); mint 10 = output 10 MINT")
    b = dict(base); b[1] = [output([ada - 1, tok])]; b[9] = {pid: {MINT: 10}}
    case("conserve-ada-minus1", b, "output ADA = balanced - 1 lovelace (destroys value)")

    # --- int64 mint boundary + overflow (value-conserved: minted == output) ---
    b = dict(base); b[9] = {pid: {MINT: INT64_MAX}}; b[1] = [output([ada, {pid: {MINT: INT64_MAX}}])]
    case("mint-int64-max", b, "mint 2^63-1 MINT, output carries it (valid signed-int64 max)")
    b = dict(base); b[9] = {pid: {MINT: INT64_OVER}}; b[1] = [output([ada, {pid: {MINT: INT64_OVER}}])]
    case("mint-int64-over", b, "mint 2^63 MINT (out of signed-int64 range); amaru-accept = CRITICAL")


if __name__ == "__main__":
    main()
