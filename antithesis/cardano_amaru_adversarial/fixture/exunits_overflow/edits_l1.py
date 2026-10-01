"""L1 - ExUnits total summed without overflow protection (consensus split), from l1-base.tx.

l1-base.tx mints one token under each of 3 distinct always-succeeds PlutusV3 policies (m1/m2/m3),
so its redeemer map has 3 entries (1,0)/(1,1)/(1,2). amaru sums the 3 declared ex_units with a
plain u64 `+` (crates/amaru-kernel/src/cardano/ex_units.rs:31, fold in traits/has_ex_units.rs:20-22;
release profile has no overflow-checks). That summed value feeds the per-tx limit
(phase_one/scripts.rs:229), the per-block limit, and the min-fee plutus term (fees.rs:61).
cardano-node sums ex_units as Natural (cannot wrap) and rejects ExUnitsTooBigUTxO.

We set two redeemers to [i64::MAX, i64::MAX] and the third to a real, sufficient budget, so:
  mem column   = (2^63-1) + (2^63-1) + 2_000_000   = 2^64 + 1_999_998  -> wraps to 1_999_998 (< 14_000_000 max)
  steps column = (2^63-1) + (2^63-1) + 800_000_000 = 2^64 + 799_999_998 -> wraps to 799_999_998 (< 14_000_000_000 max)
Each per-script budget stays a valid positive i64, so phase-2 runs each trivial always-true script
far under budget -> is_valid=true. amaru: wrapped sum < limit + tiny plutus fee -> ACCEPT (202).
cardano: 2^64+... > limit -> REJECT (400 ExUnitsTooBigUTxO). That accept-vs-reject is the split.

Cases:
  l1-honest-over  (control)  3x [6_000_000, 6_000_000_000] -> honest sum 18e6 mem / 18e9 steps > max
                             -> BOTH reject ExUnitsTooBigUTxO (no wrap; proves both enforce the limit).
  l1-wrap-accept  (headline) [i64max,i64max],[i64max,i64max],[2e6, 8e8] -> wraps small
                             -> amaru ACCEPT (is_valid), cardano REJECT ExUnitsTooBigUTxO. DIVERGENCE.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import cbor2

HERE = Path(__file__).resolve().parent
FIX = HERE.parent  # .../fixture
sys.path.insert(0, str(FIX))
sys.path.insert(0, str(FIX / "collateral"))
import txedit  # noqa: E402
from txedit import rewrap, vkey  # noqa: E402
from redeemers import integrity_hash, split_tx  # noqa: E402

I64_MAX = 2 ** 63 - 1
KEYS = [(1, 0), (1, 1), (1, 2)]


def main() -> None:
    raw = bytes.fromhex(json.loads((HERE / "l1-base.tx").read_text())["cborHex"])
    body_b, wits_b, tail = split_tx(raw)
    body = cbor2.loads(body_b)
    wits = cbor2.loads(wits_b)
    pay = txedit.key(HERE, "payment")
    pp = json.loads((HERE / "pparams.json").read_text())

    old_hash = body[11]
    assert integrity_hash(wits[5], pp) == old_hash, "cannot reproduce cardano-cli script_data_hash"
    assert set(wits[5].keys()) == set(KEYS), f"unexpected redeemer keys {list(wits[5].keys())}"
    # confirm the base vkey witness is the funding key (deterministic Ed25519)
    h0 = hashlib.blake2b(body_b, digest_size=32).digest()
    base_vk_sig = {(bytes(v), bytes(s)) for v, s in txedit.items(wits[0])}
    assert (vkey(pay), pay.sign(h0)) in base_vk_sig, "base not signed by funding key"

    hash_field = b"\x0b\x58\x20" + old_hash  # key 11, bytes(32)
    assert body_b.count(hash_field) == 1

    def case(name: str, exunits: list[list[int]], desc: str) -> None:
        w = copy.deepcopy(wits)
        for k, exu in zip(KEYS, exunits):
            w[5][k] = [w[5][k][0], exu]  # keep redeemer data, replace ex_units [mem, steps]
        new_hash = integrity_hash(w[5], pp)
        new_body = body_b.replace(hash_field, b"\x0b\x58\x20" + new_hash)
        h = hashlib.blake2b(new_body, digest_size=32).digest()
        w[0] = rewrap(wits[0], [[vkey(pay), pay.sign(h)]])
        tx = b"\x84" + new_body + cbor2.dumps(w) + tail
        # round-trip guard: the tx must split back cleanly and preserve script keys 5 and 7
        cb, cw, _ = split_tx(tx)
        cwd = cbor2.loads(cw)
        assert cbor2.loads(cb)[11] == new_hash and 5 in cwd and 7 in cwd
        (HERE / f"{name}.tx").write_text(json.dumps(
            {"type": "Tx ConwayEra", "description": desc, "cborHex": tx.hex()}, indent=4) + "\n")
        print("wrote", name, "size", len(tx), "exunits", exunits)

    case("l1-honest-over",
         [[6_000_000, 6_000_000_000]] * 3,
         "honest ex_units sum 18e6 mem / 18e9 steps > max: both reject ExUnitsTooBigUTxO (control, no wrap)")
    case("l1-wrap-accept",
         [[I64_MAX, I64_MAX], [I64_MAX, I64_MAX], [2_000_000, 800_000_000]],
         "ex_units sum wraps u64 to ~(2e6 mem, 8e8 steps) < max: amaru ACCEPT (is_valid) vs cardano ExUnitsTooBigUTxO")


if __name__ == "__main__":
    main()
