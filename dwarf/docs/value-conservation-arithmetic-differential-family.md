# Value-conservation / arithmetic knife-edge differential (extends the mint/burn family)

The worst-case security class: any path where Amaru **accepts value creation** (or an
overflow/underflow) that cardano-node rejects is CRITICAL — minting ada or assets from nothing.
This targets only the arithmetic edges **not** already covered by the mint/burn family (which
covers int64-MIN burn, minUTxO boundary, maxValueSize, and asset surplus/deficit). Same tooling
(`fixture/mint_burn/edits.py` → `edits_arith.py`, cbor2 body edits + re-sign) and the shared
`mint_burn_differential` oracle (verdict → reason-class → parity, fail-closed).

> **STATUS (2026-09-27): 4/4 AGREE, no CRITICAL divergence.** Amaru v10.11.20260925 (`eaf8ac3f`)
> rejects value creation and signed-int64 overflow exactly as cardano-node 11.1.2 does. Graded on
> pair4; evidence `fixture/mint_burn_arith/graded-2026-09-27.json`.

## Cases & result

| case | edit | expected | result |
|---|---|---|---|
| conserve-ada-plus1 | output ADA = balanced **+1** lovelace (mint 10 == output 10 MINT; only ADA off) | reject | AGREE — both `ValueNotConserved` (amaru `balance = (-1, [])`) |
| conserve-ada-minus1 | output ADA = balanced **−1** lovelace | reject | AGREE — both `ValueNotConserved` (amaru `balance = (1, [])`) |
| mint-int64-over | mint **2^63** MINT (out of signed-int64 range), output carries it | decode_reject | AGREE — both decode-reject; **no overflow acceptance** |
| mint-int64-max | mint **2^63-1** MINT, output carries it (value-conserved) | accept | AGREE — both accept (valid max boundary) |

The CRITICAL directions are safe: creating 1 lovelace (`+1`) and minting an out-of-int64 quantity
(`2^63`) are both **rejected by both** nodes; neither node overflows the signed-64-bit accumulator
into an accepted value. The valid maximum (`2^63-1`) is accepted by both.

## Not separately reachable / already covered (noted, not duplicated)

- **mint + burn of the same asset netting to zero / negative** is not separately expressible: the
  Conway mint field holds one signed net quantity per asset, so "mint +5 and burn −5" is `mint 0`
  (covered by `mint-zero-qty`), and a net-negative with no token input reduces to a burn with no
  input (covered by `burn-nonexistent` / `burn-int64-min`).
- **maxValueSize / token-bundle limit** — covered by the mint/burn family's `value-too-big`.
- **minUTxO exact boundary** — covered by `minada-token-at-min` / `minada-token-below`.

## Reproduce

```
# derive from mint-valid.tx (needs fixture/funding/payment.skey staged into fixture/mint_burn/keys/):
python3 fixture/mint_burn_arith/edits_arith.py    # cbor2 in a venv (e.g. /tmp/collat-venv)
python3 fixture/mint_burn_arith/manifest.py
/home/nigel/reset-pair.sh pair4                    # FULL reset (spends the shared 9708b921)
cd workload && python3 mint_burn_differential.py --corpus ../fixture/mint_burn_arith --amaru :3213 --cardano :8113
python3 mint_burn_differential.py --corpus ../fixture/mint_burn_arith --control mint-int64-max …   # accept, after a reset
```
Exit 0 all agree / 1 divergence / 2 inconclusive. Keys testnet-only.
