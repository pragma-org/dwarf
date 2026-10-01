# Mint/burn + multi-asset value phase-1 differential family

A DWARF phase-1 differential family (extends `workload/mixed_phase1.py`, grades through
`stake_pool_differential.grade`). It covers **minting-policy script witnesses, burn and quantity
edges, multi-asset value preservation, the multi-asset min-UTxO, and maxValueSize**, plus
**decode edges** of the Conway mint/multi-asset CDDL. It adds coverage; it is not a finding.

> **STATUS (2026-09-27): GRADED, 19/19 AGREE. Amaru v10.11.20260925 (`eaf8ac3f`) is
> CONFORMANT with cardano-node 11.1.2 (`fef83fed`)** on every mint/burn, multi-asset value and
> decode-edge case:
> - The 11 phase-1 violations are `AGREE`: same verdict, a shared reason class, and the same
>   policy id or amount.
> - The 5 decode edges are `AGREE`: both nodes DECODE-reject. **Amaru's decoder is not lenient
>   on any of them.** There is no accept and no decode-further.
> - The 3 controls are `AGREE`: both nodes accept, with the same tx id.
> - `value-too-big`: both nodes report the same serialised value size, **5289** bytes.
> - The multi-asset min-UTxO boundary is exact on both nodes: 1017160 is accepted, 1017159 is
>   rejected.
>
> There is no verdict divergence and no reason divergence. One precedence-*reporting* difference
> was seen (`mint-script-wrong`, see below). Binaries were verified with `--version`. The run
> used the dedicated, mempool-isolated pair 4, reset before the violation run and before each
> control. Full responses: `fixture/mint_burn/graded-2026-09-27.json`.

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
| minada-asset-below | reject | `output_too_small` at 1017159 | `1017159` |
| value-too-big | reject | `output_too_big` (150 × 32-B names) | `5000` + both reported sizes |
| mint-valid | accept | control | — |
| multiasset-mint-valid | accept | control (multi-asset mint) | — |
| minada-asset-at-min | accept | control (min-UTxO boundary 1017160) | — |

## Result (pair 4, 2026-09-27)

| case | cardano-node 11.1.2 | Amaru 0925 | grade |
|---|---|---|---|
| mint-script-missing | `MissingScriptWitnessesUTXOW [d667c38e…]` | `missing required scripts: missing [d667c38e…]` | AGREE + policy |
| mint-script-wrong | `MissingScriptWitnessesUTXOW` **and** `ExtraneousScriptWitnessesUTXOW` | `missing required scripts` only | AGREE (precedence, see below) |
| extraneous-script-no-mint | `ExtraneousScriptWitnessesUTXOW [d667c38e…]` | `extraneous script witnesses: extra [d667c38e…]` | AGREE + policy |
| burn-nonexistent | `ValueNotConservedUTxO` | `value not preserved: balance = (0, [d667c38e…` | AGREE + policy |
| burn-int64-min | `ValueNotConservedUTxO` (no overflow) | `value not preserved` (no overflow) | AGREE + policy |
| asset-surplus / -deficit / -relabel | `ValueNotConservedUTxO` | `value not preserved` | AGREE + policy |
| unminted-policy-output | `ValueNotConservedUTxO` | `value not preserved: balance = (0, [3b723a2f…` | AGREE + other policy |
| minada-asset-below | `BabbageOutputTooSmallUTxO` (1017159) | `output doesn't contain enough Lovelace` (1017159) | AGREE + amount |
| value-too-big | `OutputTooBigUTxO (5289,5000,…)` | `output value is too large: maximum: 5000, actual: 5289` | AGREE + size 5289 = 5289 |
| mint-zero-qty | `DeserialiseFailure` | `decoding 0 as NonZeroInt` | AGREE (decode) |
| mint-empty-asset-map | `DeserialiseFailure` | `empty map when expecting at least one key/value pair` | AGREE (decode) |
| mint-empty-map | `DeserialiseFailure` | `empty map when expecting at least one key/value pair` | AGREE (decode) |
| asset-name-33b | `DeserialiseFailure` | `expected 32 bytes, got 33` | AGREE (decode) |
| output-zero-qty | `DeserialiseFailure` | `decoding 0 as PositiveCoin` | AGREE (decode) |
| mint-valid | 202 `97e5b1b6…` | 202 `97e5b1b6…` | AGREE (same tx id) |
| multiasset-mint-valid | 202 `81b4ef3b…` | 202 `81b4ef3b…` | AGREE (same tx id) |
| minada-asset-at-min | 202 `9af02405…` | 202 `9af02405…` | AGREE (same tx id) |

**Precedence observation, not a divergence.** In `mint-script-wrong`, cardano-node reports the
full failure set: the policy script is missing, and the supplied script is extraneous. Amaru
reports only the first failure, the missing script. Amaru does enforce the extraneous-script rule
on its own (`extraneous-script-no-mint` AGREE). So this is a difference in how many failures are
reported, not in the rule. It matches the stake-pool family's withdrawal precedence case.

**Harness fix found during grading.** cardano's `OutputTooBigUTxO` response is 11.5 kB, because
it prints the whole oversized value. Its phase-1 markers (`"kind":"ShelleyTxValidationError"`)
sit at the end of the JSON, at byte 11412, past the transport's 4096-byte read. So the first run
fail-closed to INCONCLUSIVE, correctly never a pass. `mixed_phase1._PHASE1_MARKERS` now also
matches `conwayutxowfailure`, which leads cardano's error text. This affects every family: any
large cardano rejection previously went to `unknown`.

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
  `collateral_differential` now passes its table too. Before this change, importing it
  overwrote the stake-pool table for the whole process.
- `mixed_phase1._PHASE1_MARKERS` adds `conwayutxowfailure`, so large cardano rejections classify
  from the start of the text (see the harness fix above).
