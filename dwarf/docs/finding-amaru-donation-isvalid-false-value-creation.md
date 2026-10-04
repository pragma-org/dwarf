# Finding (CANDIDATE, advisory-first): amaru credits a treasury DONATION from a phase-2-FAILED (is_valid=false) tx — value creation / consensus divergence

**Status:** CONFIRMED (live, pristine, hash-verified) 2026-10-03. advisory-first — HOLD public until the user files a GHSA.
stabilization). advisory-first — HOLD public until the user files a GHSA.
**Severity:** HIGH -> CRITICAL (value creation / treasury inflation + mixed-network treasury &
consensus divergence; is_valid=false is a block-apply consensus path).
**Target:** amaru v10.11 eaf8ac3f (git_dirty=false) vs cardano-node 11.1.2. Conway, protocol v10.
**Context:** authorized conformance testing, local testnet_42, testnet-only keys, coordinated
disclosure to PRAGMA.

## Summary

Conway rule: a phase-2-FAILED transaction (is_valid=false) consumes ONLY its collateral; the entire
body — outputs, mint, withdrawals, certificates, and the treasury DONATION — is DROPPED. amaru applies
the is_valid=false transaction (collateral consumed — proven) but ALSO credits the body's treasury
donation to its donations pot, which is applied to the treasury at the epoch boundary. cardano-node
drops the donation. Result: amaru's treasury is credited a donation that was never funded (the body's
inputs are returned, only collateral is taken) — value created from nothing, and amaru's treasury
diverges from cardano-node on a mixed network (is_valid=false is a block-application/consensus path).

## Source chain (the smoking gun = a missing is_valid gate; asymmetry vs the adjacent fields)

In ONE function (`crates/amaru-ledger/src/rules/transaction/phase_one/mod.rs`) with `is_valid: bool`
(param, line 135) in scope:
- line 188: `if is_valid && let Some(provided) = transaction_body.treasury_value { ... }` — treasury_value IS is_valid-gated.
- line 210: `context.add_fees(if is_valid { fees } else { collateral });` — fees IS is_valid-gated (collateral on the failed path).
- lines 313-316: `if let Some(donation) = transaction_body.donation.map(u64::from) { context.produce_lovelace(donation); context.add_donation(donation); }` — the DONATION is NOT is_valid-gated. It runs for is_valid=false too.

Then:
- `context/default/validation.rs:100`: `add_donation` -> `self.state.donations += donation`.
- `state.rs:814`: `let fragment = VolatileFragment::from(context)` — `fragment.donations = context.donations`, NO is_valid gate at the fragment build.
- `state.rs:522` (epoch boundary): `self.volatile.transition(.. volatile_view.donations()? ..)` — the accumulated
  volatile donations are applied to the TREASURY.

So for an is_valid=false tx, the donation flows unconditionally: phase_one -> context.donations ->
VolatileFragment.donations -> volatile_donations -> treasury. The adjacent fees/treasury_value fields
in the SAME function ARE gated; the donation is not — a missing-gate oversight.

## cardano-node / Conway reference

A phase-2-failed tx collects collateral only; the body (including the donation) is dropped. cardano's
treasury is unchanged by the failed tx's donation. (ledger spec: Conway UTXOS / the IsValid=False path.)

## What is PROVEN (live) vs pending

PROVEN (live, pair1, eaf8ac3f):
- amaru APPLIES the is_valid=false tx: the collateral 9708b921...#0 is consumed (behavioral: after
  applying the forged is_valid=false block 214, re-submitting any tx spending 9708b921#0 returns
  "unknown (but required) transaction input" = consumed). So the collateral-only path is engaged.
- add_donation runs unconditionally in phase_one (source, correct rev; phase_one is executed for
  is_valid=false txs).
- OUTPUT facet is CORRECTLY dropped (companion test: the is_valid=false tx's output f9ed9757#0 is NOT
  created — spend-test returns "unknown input" on a confirmed-applied block). This proves amaru's
  body-drop exists for UTxO, and isolates the donation (a separate pot-credit path) as the incomplete
  case.
- SUBMIT facet is clean/balanced (prior result): both nodes count the donation in value conservation
  (balanced -> both accept, unbalanced -> both reject). No submit-observable divergence — the
  divergence is in the committed STATE, not the validation check.

PENDING (verify-don't-predict — not yet claimed confirmed):
- The committed-state treasury/donations credit. The leak lives in volatile_donations (in-memory); it
  reaches the readable stable donations pot only after k-deep stabilization (consensus_security_param
  = 20 -> ~20 blocks) and the treasury only at the epoch boundary. The boot pots.dump reads the STABLE
  store (db.pots().donations) and shows 0 until stabilization. Confirmation is in progress via a
  pristine k-deep stabilization forge (serve a ~20-block chain extending block 214 within epoch 3, so
  block-214's donation stabilizes into the stable donations pot, then read pots.dump = 5000000 on the
  UNMODIFIED binary). No amaru instrumentation is used (never-modify-amaru; evidence must be pristine).

## Reproduce via DWARF

Fixture `antithesis/.../fixture/donation_invalid/l3-donation-bal.tx` (is_valid=false, always-fails
PlutusV3 mint, treasury donation 5000000 (body key 22), balanced, input=collateral=9708b921#0). Forge
it into a block at the funded tip (pathb bridge, slot 1209 / height 214 / parent 1000 181e9b48), apply
to amaru, then (to read the committed credit) extend with a ~20-block chain to stabilize block 214 and
read `pots.dump donations`. cardano reference: is_valid=false drops the donation (treasury unchanged).

## Remediation

Gate the donation on `is_valid` in `phase_one/mod.rs:313-316`, matching the adjacent fees (line 210)
and treasury_value (line 188): only `produce_lovelace(donation)` / `add_donation(donation)` on the
valid path; on the is_valid=false path the donation (like the rest of the body) must be dropped.

## GHSA submission block (DRAFT — finalise with the template; public HELD until filed)

- **Title:** amaru credits a treasury donation from a phase-2-failed (is_valid=false) transaction,
  creating value / diverging the treasury from cardano-node (missing is_valid gate).
- **Severity / CVSS:** High->Critical. CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:N/I:H/A:N (scope-changed
  integrity: value creation + consensus/treasury divergence) — confirm with PRAGMA.
- **CWE:** CWE-840 (Business Logic Errors) / CWE-670 (Always-Incorrect Control Flow) — a validity-path
  guard omitted for one field.
- **Affected:** amaru (observed v10.11 eaf8ac3f), Conway, protocol v10.
- **Description:** On the is_valid=false (collateral-only) path, amaru's phase_one unconditionally runs
  `produce_lovelace(donation)` + `add_donation(donation)` (mod.rs:313-316) — unlike the adjacent fees
  (mod.rs:210) and treasury_value (mod.rs:188), which are is_valid-gated. The donation flows to
  `state.donations` -> VolatileFragment.donations -> treasury at the epoch boundary, so a phase-2-failed
  tx credits amaru's treasury with a donation that was never funded (body inputs returned; only
  collateral consumed). cardano-node drops it. On a mixed network this diverges the treasury and the
  consensus state. PoC: l3-donation-bal.tx (is_valid=false, donation 5 ADA) forged + applied; collateral
  consumed (apply proven); committed donations credit confirmed by k-deep stabilization read.
- **Fix:** add the is_valid gate to the donation in phase_one (mod.rs:313-316).

## Corroboration: the WITHDRAWAL sibling is correctly is_valid-gated — the donation is the lone omission

The reward-withdrawal state effect IS correctly gated, which pinpoints the donation as a single missed
guard rather than a systemic pattern:

- `rules/transaction/phase_one/withdrawals.rs:97`: `context.consume_lovelace(amount)` — the balance
  (value-conservation) side, UNGATED (runs on both paths). This is correct, and is the exact analog of
  the donation's `produce_lovelace(donation)` (also correctly ungated).
- `rules/transaction/phase_one/withdrawals.rs:104`: `if is_valid && amount > 0 { context.withdraw_from(credential); }`
  — the STATE effect (debiting the reward account) is is_valid-GATED. On is_valid=false the reward
  account is NOT debited. Correct.

The donation's state effect `add_donation(donation)` (phase_one/mod.rs:315) is the exact structural
analog of withdrawals' `withdraw_from` — but it is NOT wrapped in an `if is_valid` guard. So the same
function/codebase shows the correct pattern (withdrawal: ungated balance + GATED state effect) that the
donation fails to follow (donation: ungated balance + UNGATED state effect). This makes the finding a
precise, single missing-gate with an in-codebase reference for the fix: wrap `add_donation` in
`if is_valid` exactly as `withdraw_from` is. (Withdrawal facet = conformant / clean-negative.)

## STATUS UPGRADE: CONFIRMED (live, pristine, unmodified binary, hash-verified chain) — 2026-10-03

The committed-state donation leak is CONFIRMED by a pristine stabilization read on the UNMODIFIED amaru
eaf8ac3f binary (no instrumentation), against a hash-verified chain. Candidate -> CONFIRMED.

Setup: cod-plutus multi-block responder served the 21-block chain [214 = l3-donation-bal (is_valid=false,
donation 5000000, hash 3058554e), 215..234 = empty extenders under the correct epoch-3 nonce 85e36f9d];
cod-plutus/cod-forge built it. amaru (pair1, fresh GOLDEN) synced it.

Hash-verified evidence (rules out the stale prior-run 234):
- amaru chainsync: `intersect_found ... highest="[1550, h'd605a0b44f85bf964b757c8d320beff486bdd4c5705bb4f83d1e9c561fe8ea76', 234]"`
  = OUR chain tip (slot 1550, hash d605a0b4, height 234). The stale prior-run tip
  (block_height=234 slot=1211 hash 15781810) appears 0 times in the apply log. So the chain amaru synced
  is definitively ours.
- amaru applied block 214 = 3058554e (our is_valid=false donation block). Collateral 9708b921#0 consumed
  (behavioral: re-submitting a 9708b921#0-spender -> "unknown (but required) transaction input").
- Fresh GOLDEN baseline verified clean: a throwaway boot of funded-store-GOLDEN reads
  `pots.dump ... donations=0` (rules out a contaminated-store artifact).
- Restart (--keep-store, dead peer) re-opens the persisted on-disk store at the stabilized point
  [1209, 3058554e, 214] and the boot pots.dump reads:
  `pots.dump treasury=0 reserves=43600000000000000 fees=200000000000000 donations=5000000`.
- Reproduced across runs.

Mechanism: amaru applies block 214 (is_valid=false) -> its donation is credited to the volatile
donations aggregate (per the unguarded add_donation). amaru's synced header-chain to 234 (OUR d605a0b4)
makes block 214 immutable/k-deep -> amaru STABILIZES block 214, persisting its donation into the STABLE
donations pot (db.pots().donations = 5000000). The collateral (9708b921#0, 2e14) is correctly forfeited
to fees (fees=2e14); the donation (5e6) is NOT dropped -> it persists. Since an is_valid=false tx's body
inputs are returned (only collateral consumed), the 5e6 donations credit has no funding source = value
created from nothing; it is applied to the treasury at the epoch boundary. cardano-node drops the
donation (treasury unchanged on the is_valid=false path).

VERDICT: CONFIRMED CRITICAL value-creation / treasury-inflation + consensus-divergence on amaru eaf8ac3f
(Conway, pv10). Root cause unchanged: the lone missing is_valid gate at phase_one/mod.rs:313-316
(add_donation), vs the is_valid-gated fees (:210), treasury_value (:188), and the sibling withdraw_from
(withdrawals.rs:104). Remediation: wrap add_donation in `if is_valid`. Evidence pristine (unmodified
binary; stable-pot read after chain-driven stabilization; GOLDEN verified clean; stale-chain ruled out
by hash). advisory-first -- HOLD public until the user files the GHSA.
