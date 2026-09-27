# Reference-input resolution phase-1 differential family

Reachable-on-frozen-chain slice of the reference-inputs / inline-datum / reference-script surface:
**reference-INPUT resolution**, where the referenced UTxOs already exist in the ledger set.
Extends `workload/mixed_phase1.py` (transport); driver `workload/reference_differential.py`.

> **STATUS (2026-09-27): 5/5 AGREE.** Amaru v10.11.20260925 (`eaf8ac3f`) is CONFORMANT with
> cardano-node 11.1.2 (`fef83fed`) on reference-input resolution — verdict, reason class, and the
> named offending input all match. Graded on the dedicated pair; evidence
> `fixture/reference_inputs/graded-2026-09-27.json`.

## Reachability

On the frozen (non-forging) chain, a tx can only reference UTxOs already in the ledger set — the
funding UTxO and the genesis `initialFunds`, all address+coin only (no datums, no reference
scripts). And Amaru rejects mempool-chaining, so a script-locked-inline-datum UTxO or a
reference-script UTxO cannot be created and then referenced within the mempool. So this family
covers reference-input **resolution**; reference-**script** execution and inline-datum **spends**
need those UTxOs pre-mined before the freeze (a substrate ask, tracked separately).

## Cases & result

All spend the funding UTxO `9708b921…#0`; fee 300000 ≫ min. Accept controls consume the funding
UTxO (single-use, one per mempool reset); rejects are idempotent.

| case | expected | cardano-node 11.1.2 | Amaru 0925 | result |
|---|---|---|---|---|
| ref-ok | accept | 202 | 202 (same tx id) | AGREE |
| ref-duplicate (same ref twice) | accept | 202 | 202 | AGREE (set dedup) |
| ref-many (3 distinct refs) | accept | 202 | 202 | AGREE |
| ref-nonexistent (`0000…#0`) | reject | `BadInputsUTxO(0000…#0)` | `unknown (but required) transaction input or reference input: 0000…#0` | AGREE + same input |
| ref-equals-spend (funding as spend **and** ref) | reject | `BabbageNonDisjointRefInputs(9708b921…#0)` | `inputs included in both reference inputs and spent inputs: [9708b921…#0]` | AGREE + same input |

`ref-equals-spend` confirms Amaru enforces the same spend/reference **disjointness** rule as
cardano-node, and both name the same offending input.

## Oracle

`reference_differential.py`: verdict parity (accept/accept or reject/reject); on a shared reject,
reason-class parity **and input parity** (both name the same offending input). Fail-closed:
masked or unavailable → INCONCLUSIVE.

Note — this driver uses its **own** verdict classifier, not the shared `mixed_phase1` one: a bad
**reference** input legitimately produces cardano `BadInputsUTxO`, which is the real verdict here,
so masking on `BadInputsUTxO` would be wrong. It masks **only** on the mempool-conflict phrasing
("all inputs are spent" / "probably already been included"); the pair is reset before each run so
the funding UTxO is present and a reject is attributable to the reference input.

Substrate caveat: after a reset, Amaru needs a moment before the Ouroboros system-start check
passes (a too-early restart exits with `Process start must be after Ouroboros system start time`);
that surfaces as an `unavailable`/INCONCLUSIVE reading, not a false result — re-grade after the
node is up.

## Reproduce

```
# reference UTxOs are existing ledger UTxOs (funding + genesis initialFunds); query the node:
docker exec pair2-cardano-ref cardano-cli conway query utxo --whole-utxo --testnet-magic 42 \
  --socket-path /state/db/node.socket
# build each tx spending 9708b921…#0 with --read-only-tx-in-reference <ref>; the reject cases use
# a nonexistent ref (0000…#0) and the funding UTxO as its own reference.
cd workload && python3 reference_differential.py --corpus ../fixture/reference_inputs --amaru … --cardano …
python3 reference_differential.py --corpus ../fixture/reference_inputs --single ref-ok …  # one accept per reset
```
