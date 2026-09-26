# Phase-1 reference ledger — durable funding key + re-bake recipe

**Why this exists.** The original phase-1 baked reference ledger was funded by an *ephemeral*
key (payment-hash `f4c0a3b5…`, funded via genesis `initialFunds`) whose `.skey` and
`prepare-underfee.sh` were never committed and are now lost — so the underfee fixtures could not
be regenerated and no new phase-1 tx (e.g. native-script cases) could be crafted. This directory
fixes that permanently: the funding key is **committed**, and the funded UTxO is a deterministic
function of the committed genesis + committed key (no ephemeral runtime state).

## Committed funding key (testnet / devnet only — no mainnet value)

- `payment.skey` / `payment.vkey` — the funding keypair (committed so it is never lost again).
- payment key hash: `e5a5ddb03fe37059627fede71458401e0dd97bb371b3bd7cce0d2327`
- enterprise testnet address: `addr_test1vrj6thds8l3hqktz0lk7w9zcgq0qmktmkdcm80tuecxjxfcjg8ye6`
- genesis `initialFunds` key (hex, `0x60` enterprise header + key hash):
  `60e5a5ddb03fe37059627fede71458401e0dd97bb371b3bd7cce0d2327` = `200000000000000` lovelace.

Regenerate the vkey/hash/address from the committed `payment.skey` with
`cardano-cli key verification-key` / `address key-hash` / `address build --testnet-magic 42`.

## Paired genesis (`genesis/`)

The 5 genesis files from the re-bake (`prepare-rebake.sh` output), with the funding address in
shelley `initialFunds`. This is the EXACT genesis that produces the funded UTxO below.

- profile: networkMagic 42, k=20, epochLength 400, slotLength 0.5s, activeSlotsCoeff 0.2,
  slotsPerKESPeriod 129600, maxKESEvolutions 62, protocolVersion major 10 (Conway).
- systemStart: `2026-09-26T14:45:53Z` (Amaru `AMARU_GLOBAL_SYSTEM_START=1790433953000` ms).
- cardano-cli shelley genesis hash: `8b6dfc7c215c5a0bfdee17f56ac020d4b18f79da78572d6f5cfddeab7ea43398`

An enterprise (no-stake) initialFund address is **fully spendable**; its absence from the
stake-distribution (which governs rewards/leader math) does not affect UTxO consumption — verified
live (Amaru accepted a spend of the UTxO below). config.json references genesis by *file*, not
hash (grep-verified: no repo consumer pins a genesis hash), so the hash change needs no rewiring —
only re-baking both stores under this genesis.

## Funded UTxO

- **`9708b921e619b34a6fafc375ebd46bf65d77eed427f953eabed2b9da9212c4a1#0` = 200000000000000 lovelace**
  (replaces the old `c0a35ac5…#0`). Confirmed **UNSPENT** in BOTH baked stores:
  the frozen cardano reference (epoch 3, Conway) and Amaru's `testnet_42` store (tip slot ~1199).
  The UTxO id is stable across re-bakes (it is `f(genesis initialFunds + committed key)`), so it
  survives changes to pool keys / systemStart.

## Re-bake recipe (`../prepare-rebake.sh`)

`prepare-rebake.sh` (in the bundle root) documents + drives the full bake: configurator `0f9570b`
→ inject this initialFund + forward systemStart → forge ≥3 epochs (3-node k=20 cluster) →
snapshot a **frozen, non-forging** cardano reference (no KES/VRF/opcert keys — a differential
reference must NEVER forge, or every run mutates the ledger and the gate stops being repeatable) →
`make-store.sh` (db-analyser boundary points → `amaru snapshot create` → `amaru node bootstrap`)
for the Amaru `testnet_42` store, from the SAME genesis → regenerate the underfee fixtures →
re-verify `workload/mixed_phase1.py`. Rebuild the ~1.5 GB state/images LOCALLY; do NOT commit the
big artifacts and do NOT push to GHCR (ops territory).

## Regression gate (re-verified 2026-09-26 — GREEN, non-vacuous)

`fixture/static/{corpus.json,underfee*.tx,minimum-exact.tx}` spend the funded UTxO above. Against
the frozen cardano reference + Amaru `testnet_42`:

- `underfee-minus-100/-2/-1` (fee 164081/164179/164180) → BOTH `phase1_reject`
  (cardano: real `FeeTooSmallUTxO Mismatch {supplied 164180, expected 164181}`).
- `minimum-exact` (fee 164225) → BOTH `accepted` (202/202).
- Non-vacuous: 1 real ACCEPTED + 3 real PHASE1_REJECT. Repeatable (frozen reference never mines;
  restart clears the mempool between runs).

### Min-fee size-accounting divergence (CLOSED — see `dwarf/docs/finding-amaru-minfee-isvalid-size-divergence-CLOSED.md`)

The accepted case is set to **164225** (not cardano's exact min **164181**) because it was built
against **amaru 10.11.20260807**, which charged for tx size 201 (min 164225 = 44×201+155381, the
1-byte Alonzo `IsValid` flag INCLUDED) while cardano charges for size 200 (min 164181, `IsValid`
excluded per cardano-ledger `toCBORForSizeComputation`) — a 44-lovelace band [164181, 164224] that
cardano accepts and amaru 807 rejected.

**Update — FIXED in amaru 10.11.20260918 (ea1f34e4): the divergence is gone (gap 0).** 0918 amaru
accepts exactly at 164181, matching cardano; this is a CLOSED/historical finding, not a live bug.
`minimum-exact=164225` still keeps the gate green on BOTH 807 and 0918 (both accept ≥164181), so it
stays a version-robust *agreeing* regression check; the divergence band is documented in the
CLOSED finding, not folded into the gate.

## Blast radius / regression gate (consumers of the old UTxO / genesis)

- `fixture/static/{corpus.json,underfee*.tx,minimum-exact.tx}` — REGENERATED against the new UTxO
  (done). `minimum-plus-1.tx` was dropped (a second accepted case would need a second dedicated
  UTxO; the single genesis initialFund funds one).
- `reports/amaru-mempool-min-fee-size-evidence/minimum-plus-44.tx` — references the old UTxO but is
  a **frozen evidence record** of a delivered finding; left as historical (not repointed).
- No other repo consumer pins the old genesis hash or UTxO (grep-verified 2026-09-26).
