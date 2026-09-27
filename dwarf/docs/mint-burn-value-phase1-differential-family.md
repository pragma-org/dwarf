# Mint/burn + multi-asset value phase-1 differential family

A DWARF phase-1 differential family (extends `workload/mixed_phase1.py`, grades through
`stake_pool_differential.grade`). It covers **minting-policy script witnesses, burn and quantity
edges, multi-asset value preservation, the multi-asset min-UTxO, and maxValueSize**, plus
**decode edges** of the Conway mint/multi-asset CDDL. It adds coverage; it is not a finding.

> **STATUS (2026-09-27): BUILT, NOT YET GRADED.** Corpus built and pre-checked offline
> (19 cases). Live grading waits for the dedicated pair 4 (cardano-node 11.1.2 + amaru 0925).

## Scope and no-overlap

Not repeated here (already covered elsewhere):
- policy **signature** missing → native-script family (`mintpolicy-sig-missing`);
- int64-max mint accept and ADA-only `OutputTooSmall` → `phase1-differential-coverage-2026-09-26.md`.

This family targets the policy **script** witness, the multi-asset side of value conservation
(ADA is always balanced exactly), and the multi-asset versions of the output rules.

## Reachability

- Every case spends `9708b921…#0`. The committed payment key signs every case, and a fresh
  policy key (`all[sig policy]`, policy id `d667c38e…`) signs every minting case. The fee is
  300000 (1000000 for the 10.9 kB `value-too-big`), well above the minimum. There is no validity
  interval.
- Multi-asset min-UTxO for one token of this policy: **1017160** (from the rebake pparams,
  `utxoCostPerByte` 4310; the ADA-only minimum is 849070).
- **A valid BURN control is INFEASIBLE on this substrate.** The frozen references never forge,
  so no token UTxO can confirm. The mint map carries one net quantity per asset, so a tx cannot
  mint and burn the same asset. A mempool-chained burn was ruled out: Amaru does not admit txs
  that spend unconfirmed outputs, so the test would mix up the burn rule with that known
  difference. Burn semantics are covered by the rejects `burn-nonexistent` and `burn-int64-min`.
  `multiasset-mint-valid` is a valid multi-asset **mint** control, not a burn control.

## Construction

`fixture/mint_burn/build.sh` builds 10 cases with cardano-cli. `edits.py` derives 9 more from
`mint-valid.tx`:
- **Witness-set edits** (body untouched, signatures stay valid): script missing, script wrong,
  script extraneous.
- **Body edits, re-signed in Python (Ed25519)**: the decode edges and the int64-min burn. Each
  of these txs is otherwise VALID: ADA balanced, policy witnessed. So a decoder that accepts the
  edge shows up as an ACCEPT.
- **Self-checks**: `edits.py` aborts unless the cbor2 re-encoding of the unedited body is
  byte-identical, and the Python signatures equal the cardano-cli witnesses.
- **Offline pre-check**: `cardano-cli debug transaction view`, which uses the ledger's Conway
  decoder, rejects exactly the 5 decode-edge txs with the targeted message, and decodes the
  other 14:
  - `mint-zero-qty`: `MultiAsset cannot contain zeros`
  - `mint-empty-asset-map`: `Empty Assets are not allowed`
  - `mint-empty-map`: `TxBody: 'Mint' must be non-empty when supplied`
  - `asset-name-33b`: `asset name exceeds 32 bytes`
  - `output-zero-qty`: `MultiAsset cannot contain zeros`

Rebuild: `PYTHON=<python with cbor2+cryptography> fixture/mint_burn/build.sh`.

## Cases

| case | expected | rule / reason class | parity token |
|---|---|---|---|
| mint-script-missing | reject | `missing_script` | policy id |
| mint-script-wrong | reject | `missing_script` ∩/∪ `extraneous_script` (precedence) | policy id |
| extraneous-script-no-mint | reject | `extraneous_script` | policy id |
| burn-nonexistent | reject | `value_not_conserved` | policy id |
| burn-int64-min | reject | `value_not_conserved` (int64 edge; overflow candidate) | policy id |
| mint-zero-qty | decode_reject | CDDL nonZeroInt64 | — |
| mint-empty-asset-map | decode_reject | CDDL non-empty inner map | — |
| mint-empty-map | decode_reject | CDDL non-empty mint | — |
| asset-name-33b | decode_reject | CDDL asset_name ≤ 32 B | — |
| output-zero-qty | decode_reject | CDDL output positive_coin | — |
| asset-surplus | reject | `value_not_conserved` (mint 10, out 11) | policy id |
| asset-deficit | reject | `value_not_conserved` (mint 10, out 9) | policy id |
| asset-relabel | reject | `value_not_conserved` (mint MINT, out MINTX) | policy id |
| unminted-policy-output | reject | `value_not_conserved` (foreign policy, no mint) | other policy id |
| minada-token-below | reject | `output_too_small` at 1017159 | `1017159` |
| value-too-big | reject | `output_too_big` (150 × 32-B names) | `5000` + both reported sizes |
| mint-valid | accept | control | — |
| multiasset-mint-valid | accept | control (multi-asset mint) | — |
| minada-token-at-min | accept | control (min-UTxO boundary 1017160) | — |

## Oracle (fail-closed)

`workload/mint_burn_differential.py`:
1. **Verdict parity.** Both nodes give the expected verdict: `phase1_reject`, `decode_reject`
   or `accepted`.
2. **Reason-class parity** for rejects: class-set intersection, same as the stake-pool family.
3. **Parity token.** Both responses name the policy id, or the amount the rule is about.
   - The full response is kept (`keep_detail`), because cardano's `ValueNotConservedUTxO`
     Mismatch puts the policy id past the 400-char reason cut.
   - `value-too-big` also compares the value size each node reports. A mismatch is graded
     `REASON-DIVERGENCE`, because it means the two nodes serialise the value differently.
4. **Decode edges.** A decode-edge case where one node DECODE-rejects and the other
   phase-1-rejects is graded `VERDICT-DIVERGENCE` and flagged `decode_leniency`. An ACCEPT on a
   decode edge is a plain `VERDICT-DIVERGENCE`.
5. **Fail-closed.** MASKED or unavailable = `INCONCLUSIVE`. A truncated reason = `REASON-UNVERIFIED`.
   Neither is ever a pass.

Run the violations: `python3 workload/mint_burn_differential.py --amaru URL --cardano URL`.
Run the controls one at a time with `--control CASE`, each after a mempool reset.

Shared-code changes (backward compatible):
- `mixed_phase1.HttpSubmitTransport(keep_detail=)` adds an opt-in `detail` field to each
  observation.
- `stake_pool_differential.grade(case, result, table=None)` accepts a family reason table
  instead of mutating the module global, and supports `expected: decode_reject`.
