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

The 5 genesis files copied from the prior reference bake, with ONE change: the funding address
added to shelley `initialFunds`. Everything else (systemStart `2026-08-20T04:08:52Z`,
networkMagic 42, k=20, epochLength 400, slotsPerKESPeriod 129600, maxKESEvolutions 62, staking)
is **preserved**, so the Amaru era-history / global-parameters (`amaru-runtime/era-history.json`,
`global-parameters.json`, `d807-amaru/globals.env`) remain valid — the only downstream change is
the funded UTxO and the genesis hash.

- old shelley genesis hash: `c18693fa21f6290ff2315d7499ad59050e1cf157678db584822cdf416c3eeb37`
- new shelley genesis hash: `cfbe2d072d21549bb99f7b5bda5227679c93b600f5f92db14a7393086cc7e873`

Nothing in the repo pins a genesis hash (config.json references genesis by *file*, not hash;
grep confirms no consumer pins the old hash), so the hash change requires no wiring updates —
only re-baking both stores under the new genesis.

## Re-bake recipe (`prepare-rebake.sh` — TODO, pending option B)

Both baked stores must be re-baked under the new (funded) genesis, since they must share one
genesis:
1. Cardano reference: run a cardano-node from `genesis/` (configurator image 0f9570b for keys/pool
   setup as needed), forge >= 3 epochs, snapshot the chain DB -> `reference-image/state`.
2. Amaru store: `relay-image/make-store.sh` (db-analyser boundary points -> `amaru snapshot create`
   -> `amaru node bootstrap`) against the same cluster/genesis -> Amaru baked `testnet_42` store.
3. Rebuild `reference-image` + amaru relay image LOCALLY (do NOT commit the ~1.5GB state/images;
   do NOT push to GHCR — that is ops territory). Local reproducibility from this recipe is the bar.

## Funded UTxO (record after bake)

- new funded UTxO id (replaces the old `c0a35ac5…#0`): **TODO — record after the bake and confirm unspent.**

## Blast radius / regression gate (consumers of the old UTxO / genesis)

- `fixture/static/{corpus.json,underfee*.tx,minimum-*.tx}` — the underfee phase-1 corpus spends the
  old `c0a35ac5…#0`; **REGENERATE** against the new funded UTxO and re-verify `workload/mixed_phase1.py`
  classifies ACCEPTED / PHASE1_REJECT / DECODE_REJECT correctly (the regression gate).
- `reports/amaru-mempool-min-fee-size-evidence/minimum-plus-44.tx` — references the old UTxO but is a
  **frozen evidence record** of a delivered finding; leave as historical (not repointed).
- No other repo consumer pins the old genesis hash or UTxO (grep-verified 2026-09-26).
