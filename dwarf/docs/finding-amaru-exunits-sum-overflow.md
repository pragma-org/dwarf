# Finding: Amaru accepts a transaction whose ExUnits sum overflows u64 (consensus-validity split)

**Severity: HIGH** (consensus-validity divergence: amaru would treat as valid a transaction
cardano-node refuses; also a resource-accounting/DoS bypass — each script runs on a budget up to
i64::MAX while the tx is charged a near-zero Plutus fee).
**Status: CONFIRMED, reproducible** (2/2 amaru accept with `--amaru-only` reset between runs; 2/2
cardano non-accept) on the isolated pair1, 2026-09-30.
**Affected:** Amaru `v10.11.0` git `eaf8ac3f` (git_dirty=false, from the running binary).
**Reference:** cardano-node 11.1.2 (`fef83fed`) does **not** accept the transaction.

## Summary

A Conway transaction that carries several Plutus redeemers whose **declared** ExUnits sum to
**≥ 2^64** makes Amaru's per-transaction ExUnits total **wrap around u64** to a small value that
passes the per-tx limit. Amaru then runs each script under its own (huge, un-summed) declared
budget, all trivial scripts pass, and Amaru **accepts the transaction (`is_valid`, HTTP 202)**.
cardano-node sums ExUnits as an unbounded `Natural`, sees the true sum exceed the protocol limit,
and **does not accept** the transaction. This is a consensus-validity divergence: the same bytes are
valid to Amaru and invalid to the reference node.

## Root cause (source, amaru `eaf8ac3f`)

- `crates/amaru-kernel/src/cardano/ex_units.rs:31` — `impl Add for &ExUnits` uses a plain `+` on
  `u64` fields: `ExUnits { mem: self.mem + rhs.mem, steps: self.steps + rhs.steps }`.
- `crates/amaru-kernel/src/traits/has_ex_units.rs:20-22` — `total_ex_units()` folds the redeemers'
  ExUnits through that `+`, starting from `{0,0}`.
- `Cargo.toml [profile.release]` sets `lto`/`codegen-units` only — **no `overflow-checks`** — so the
  fold wraps modulo 2^64 in release builds.
- The wrapped total feeds all three limit/fee checks: the per-tx limit
  (`rules/transaction/phase_one/scripts.rs:229` `provided.mem > max.mem || provided.steps > max.steps`),
  the per-block limit (`rules/block/ex_units.rs:24`), and the min-fee Plutus term
  (`rules/transaction/phase_one/fees.rs:61`). Phase-2 then executes each script with its **own**
  declared budget (not the sum), so with each per-redeemer budget ≤ i64::MAX the scripts run and pass.

cardano-node sums ExUnits as `Natural` and rejects with `ExUnitsTooBigUTxO`.

## Reproduction

Fixtures (`antithesis/cardano_amaru_adversarial/fixture/exunits_overflow/`) mint one token under
each of **3 distinct always-succeeds PlutusV3 minting policies** (`m1`/`m2`/`m3`, built with aiken;
minting needs no committed script UTxO, so it works on a frozen pair), giving a redeemer map with 3
`Mint` entries `(1,0)/(1,1)/(1,2)`. `l1-base.tx` is built + signed by cardano-cli (spends the
funding UTxO `9708b921…c4a1#0` as both `--tx-in` and `--tx-in-collateral`); `edits_l1.py` then
rewrites only the 3 redeemers' ExUnits, recomputes the `script_data_hash` (reusing the collateral
family's `integrity_hash`), and re-signs — round-trip + signature checks in-script.

| case | per-redeemer ExUnits (mem, steps) | honest sum | amaru | cardano |
|---|---|---|---|---|
| `l1-honest-over` (control) | 3 × (6e6, 6e9) | mem 18e6 / steps 18e9 (< 2^64) | reject | reject `ExUnitsTooBigUTxO` |
| `l1-wrap-accept` (headline) | (i64max, i64max), (i64max, i64max), (2e6, 8e8) | mem 2^64+1_999_998 / steps 2^64+799_999_998 | **accept 202** | **not accepted** |

For `l1-wrap-accept`, Amaru's u64 sum wraps to mem 1_999_998 / steps 799_999_998, both below the
`maxTxExecutionUnits` (mem 14e6 / steps 14e9).

Driver `workload/exunits_overflow_differential.py` (reuses `mixed_phase1`), pair1 (amaru `:3210`,
cardano `:8110`):

```
l1-honest-over : AGREE — both reject.
  amaru   400 "transaction execution units exceeded" (provided mem 18000000 / steps 18000000000, max 14000000 / 14000000000)
  cardano 400 ConwayUtxowFailure … ExUnitsTooBigUTxO (supplied 18000000/18000000000, expected 14000000/14000000000)
l1-wrap-accept : ACCEPT-DIVERGENCE
  amaru   202 ACCEPTED (txid 1243e6240cc48ab3ef5738c633f822afde0b79c58378c10002ac3cbad920bf81), 2/2 with --amaru-only reset
  cardano 400 not accepted, 2/2 (see note)
```

## Confounds ruled out

- **Provenance:** running binary logs `git_commit=eaf8ac3f`, `git_dirty=false`.
- **Determinism:** amaru 202 twice (fresh mempool each time via `reset-pair.sh pair1 --amaru-only`);
  cardano non-accept twice.
- **cardano alive + enforcing:** after the wrap submits, `l1-honest-over` to cardano still returns a
  clean `ExUnitsTooBigUTxO` 400 — cardano is up, the funding UTxO was not consumed, and it enforces
  the limit normally.
- **Control isolates the wrap:** the same construction with a sub-2^64 honest sum (`l1-honest-over`)
  is rejected by *both*; only the ≥ 2^64 sum flips Amaru to accept.
- **Not a decoder artifact:** cardano-cli decodes `l1-wrap-accept` fine (`debug transaction view`
  shows ExUnits 9223372036854775807), and the tx is re-signed with a valid witness.

## Coupled observation (cardano error-serialization at the same u64/Word64 seam)

Because any Amaru-wrapping sum is ≥ 2^64, it also exceeds `Word64::MAX` (2^64−1). cardano-node
rejects the wrap tx (the LocalTxSubmission server takes `SingBusy` agency to reply with a *reject*),
but its `ExUnitsTooBigUTxO` error carries the provided total in a `WrapExUnits` (Word64), which
cannot represent the > Word64 sum — so the reject reply fails to (de)serialize and the submit API
surfaces `TxCmdTxSubmitConnectionError … DeserialiseFailure "expected word"` instead of a clean
ledger reject. This is a cardano-side robustness quirk, not an acceptance; the primary finding is
Amaru's silent wrap-and-accept. It also means a clean `ExUnitsTooBigUTxO` from cardano is only
observable below 2^64 (the control), which is precisely where Amaru does *not* wrap — the two
implementations diverge exactly at the u64/Word64 boundary.

## Impact

Amaru would accept, hold, relay, and (on a producing node) could include a transaction that the
reference node considers invalid — a consensus-validity split that can fork Amaru from cardano-node.
Independently, the wrap lets a transaction declare per-script budgets up to i64::MAX while paying a
Plutus fee computed on the wrapped (near-zero) total — a resource-accounting / execution-budget
bypass.

## Remediation

Use checked/saturating addition (or `Natural`/`u128`) for the ExUnits total, and reject on overflow
rather than wrapping; enable `overflow-checks` for the ledger crates. (cardano should also bound the
provided value in the `ExUnitsTooBigUTxO` error so a > Word64 sum is still reportable.)

## Provenance / not filed upstream

Found by the DWARF ledger-lane differential sweep, 2026-09-30, on isolated pair1
(amaru eaf8ac3f `:3210` / cardano-node 11.1.2 `:8110`). Not filed upstream.

## Live re-confirmation (2026-10-01) + block-apply reach

Re-confirmed on the current substrate (isolated pair1, GOLDEN reset, amaru `eaf8ac3f` :3210 / cardano-node 11.1.2 :8110) via `runtime_tx_submit_differential`: `l1-wrap-accept.tx` -> amaru **accept** (HTTP 202, node alive) / cardano **non-accept** => **DIVERGENCE**. Control `l1-honest-over.tx` (sum below 2^64 but above the per-tx limit) -> **both reject** (cardano `ExUnitsTooBigUTxO`) => AGREE. The divergence is specific to the u64-wrapping sum.

Block-apply reach (source-level): amaru's block-apply runs the same phase-one ExUnits total/limit path as mempool submit (`has_ex_units.rs` `total_ex_units` -> `scripts.rs:229` per-tx limit), so a block carrying the wrapping transaction would be accepted and applied by amaru while cardano-node rejects the block — an accept-invalid-block of the same class. A live crafted-block demonstration via the forge/serve bridge has since been reproduced — see **Live block-apply reproduction OBSERVED** below.

## Live block-apply reproduction OBSERVED (2026-10-01)

The block-apply reach above was confirmed live (authorized conformance testing, local devnet, testnet-only keys). A Conway block carrying the single wrapping transaction (`l1-wrap-accept`, txid `1243e624…`, `is_valid=true`, redeemer ExUnits `[i64::MAX, i64::MAX]×2 + [2e6, 8e8]` → sum wraps u64 below the per-tx limit) was forged at slot 1209 / height 214, parent GOLDEN tip `1000/181e9b48` (point_hash `f91ca2dd…`, body_hash `3b973d00…`, body_size 833), and served to amaru over chain-sync + block-fetch.

Sequence (amaru on a fresh GOLDEN snapshot, tip `1000/…/213`):
- `chainsync.intersect_found highest=[1209, f91ca2dd, 214]` → `tip.adopt slot=1209 block_height=214`
- serve `block_served=True` (the 833-byte body was fetched) + `rolled_forward=True` (amaru requested the next block)
- `ledger: epoch_transition … into=3 / apply epoch=3` (block 214 is first-of-epoch-3 → its body was applied)
- amaru **stays alive** — no panic, no validation error (an `is_valid=true` accept is silent).

Definitive apply proof (UTxO-consumption test): re-submitting the wrapping transaction after the block returns amaru `HTTP 400 "unknown (but required) transaction input … 9708b921…#0"`. That funding input was present and spendable on the fresh GOLDEN snapshot (pre-flight submit → amaru 202 ACCEPT), so it is now **spent** — i.e. block 214's wrapping transaction was **ledger-applied**, not merely header-adopted.

Conclusion: amaru fetched and **ledger-applied** a block whose transaction cardano-node rejects (the ExUnits total exceeds the protocol limit; cardano's error-channel Word64 seam surfaces as the `DeserialiseFailure` noted above), and amaru stayed live at tip 214 — an **accept-invalid-block** of the u64-wrap class (root cause `ex_units.rs:31`). On a mixed network this is a consensus-validity divergence at block-apply, not only at submit.

## Reproduce via DWARF

Scenario `ledger-arithmetic-exunits-sum-overflow-consensus-split-amaru-cardano-node` (runtime_tx_submit_differential, expected_target=accept / expected_reference=reject). Fixtures under `antithesis/cardano_amaru_adversarial/fixture/exunits_overflow/` (`l1-wrap-accept.tx` + `l1-honest-over.tx` control). Run: `bash dwarf/block_apply/submit_differential_run.sh antithesis/cardano_amaru_adversarial/fixture/exunits_overflow/l1-wrap-accept.tx pair1 3210 8110`.
