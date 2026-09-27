# Plutus CEK-interpreter: integer division/modulo sign-semantics differential

A **depth** test of the CEK interpreter core, not a smoke test: the classic floor-vs-truncate
re-implementation divergence. Cardano/Plutus spec:
- `divideInteger` = **floored** (toward −∞): `divideInteger(-7,2) = -4`
- `modInteger` = **sign of divisor**: `modInteger(-7,2) = 1`
- `quotientInteger` = **truncated** (toward 0): `quotientInteger(-7,2) = -3`
- `remainderInteger` = **sign of dividend**: `remainderInteger(-7,2) = -1`

Operands are fed via the **redeemer** (runtime values), so aiken cannot constant-fold — the CEK
interpreter must actually compute each result. Validators assert the Cardano-correct value; if
Amaru's VM used the wrong rounding, its result would differ, the assertion would flip, and the
transaction would be accepted by one node and rejected by the other — an `is_valid` **consensus
split**. Driver `workload/plutus_differential.py`.

> **STATUS (2026-09-27): 18/18 AGREE, no divergence.** Amaru v10.11.20260925 (`eaf8ac3f`) uses the
> Cardano-spec sign conventions for all four builtins across every sign combination and the min-int
> edge. Graded on pair2; evidence `fixture/cek_divmod/graded-2026-09-27.json`.

## Cases & result

For each builtin, all three sign combinations `(-/+, +/-, -/-)` assert the spec-correct value and
**accept on both nodes** (same tx id). Four cross-convention **wrong** controls (assert the *other*
convention's value on `-7,2`) **reject on both** — confirming Amaru does not use the wrong rounding
(if it did, these would accept on Amaru while cardano rejects, a split), and that the builtins
genuinely differ by convention.

| builtin | (-7,2) | (7,-2) | (-7,-2) | wrong-control | result |
|---|---|---|---|---|---|
| divideInteger (floored) | -4 | -4 | 3 | assert -3 → reject | all AGREE |
| quotientInteger (truncated) | -3 | -3 | 3 | assert -4 → reject | all AGREE |
| modInteger (÷ sign) | 1 | -1 | -1 | assert -1 → reject | all AGREE |
| remainderInteger (dividend sign) | -1 | 1 | -1 | assert 1 → reject | all AGREE |

**MIN-int edge:** `divideInteger(-2^63, -1)` and `quotientInteger(-2^63, -1)` both assert `2^63`
and accept on both — Plutus integers are arbitrary-precision, so this does **not** overflow (unlike
fixed-width int64 where `MININT / -1` traps); Amaru computes the bignum result identically.

## Reproduce

```
# aiken module cek (divmod_divide/quotient/mod/remainder); redeemer = [a, b, expected]
/home/nigel/reset-pair.sh pair2
cd workload && python3 plutus_differential.py --corpus ../fixture/cek_divmod --amaru :3211 --cardano :8111
python3 plutus_differential.py --corpus ../fixture/cek_divmod --single div-np-ok …   # accepts, one per reset
```
Exit 0 all agree / 1 divergence / 2 inconclusive. Keys testnet-only.
