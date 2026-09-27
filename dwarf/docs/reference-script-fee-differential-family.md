# Conway reference-script fee differential family

New Conway logic: spending via a **reference script** adds a size-based fee surcharge
(`minFeeRefScriptCostPerByte = 15`). Re-implemented in Amaru — if it computes the surcharge (or
the min-fee it feeds) differently, a tx at the fee boundary is accepted by one node and rejected
(`FeeTooSmall`) by the other: a fee-**consensus** divergence (and under-charging is a spam/DoS
economics issue). Driver `workload/reffee_differential.py`.

> **STATUS (2026-09-27): 4/4 AGREE, no fee-consensus divergence.** Amaru v10.11.20260925
> (`eaf8ac3f`) computes the reference-script-inclusive minimum fee **equal to cardano-node 11.1.2
> (`fef83fed`) to the lovelace**. Graded on refpair; evidence `fixture/refscript_fee/graded-2026-09-27.json`.

## Cases & result

Spend script UTxO `A_inline` via the reference script `C_ok` (ok.plutus). ex-units fixed
(3e9 steps, 3e6 mem) so the only fee variable is the size + ref-script surcharge.

| case | fee | expected | result |
|---|---|---|---|
| reffee-exact | 559523 = cardano's computed minimum (1 ref script) | accept | AGREE (both accept, same tx id) |
| reffee-under | 559522 = minimum − 1 | reject | AGREE — both `FeeTooSmall`; amaru states "declared fee 559522 **below minimum 559523**" = cardano's exactly |
| reffee-2ref-exact | 561197 = minimum with a **2nd** reference-script input | accept | AGREE (both accept) |
| reffee-2ref-under | 561196 = minimum − 1 | reject | AGREE — both `FeeTooSmall` |

The knife-edge holds both ways: at the exact minimum both accept; one lovelace under, both reject.
Amaru's stated minimum (`559523`) equals cardano's `FeeTooSmall` expected (`559523`) to the
lovelace, and the two-reference-script size accounting matches too (`561197`).

## Substrate boundary

refpair's reference scripts are tiny (~7 bytes), so only the **linear** tier of the ref-script
fee is exercised. The **tiered / exponential growth** (which kicks in past ~25 600 reference-script
bytes) is not reachable without a large reference-script UTxO baked into the substrate — documented
as substrate-limited (would need a big ref-script output in a re-bake, like the governance families).

## Reproduce

```
# spend A_inline via C_ok (reference script); read cardano's exact min from a FeeTooSmall probe, then:
fixture/refscript_fee/…  (build at fee = min and min-1, single- and two-ref)
/home/nigel/rebake-refscript/reset-refpair.sh
cd workload && python3 reffee_differential.py --corpus ../fixture/refscript_fee --amaru :3214 --cardano :8114
python3 reffee_differential.py --corpus ../fixture/refscript_fee --control reffee-exact …   # accepts, per reset
```
Exit 0 all agree / 1 divergence / 2 inconclusive. Keys testnet-only.
