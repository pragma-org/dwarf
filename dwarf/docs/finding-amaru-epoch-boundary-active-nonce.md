# Finding: Amaru rejects the valid first block of an epoch — epoch-transition active-nonce derivation mismatch

**Component:** `amaru` consensus / Praos header validation
(`crates/amaru-ouroboros/src/praos/nonce.rs`, `crates/amaru-ouroboros-traits/src/praos/nonces.rs`)
**Type:** Consensus divergence — Amaru computes a different epoch (active) nonce than cardano-node at an
epoch boundary, so it rejects (`Invalid VRF proof`) the valid first block of the new epoch that
cardano-node accepts.
**Severity:** MEDIUM-HIGH (see Severity & reachability). Consensus-availability: an Amaru node that
crosses an epoch boundary by single-peer forward-sync cannot follow the chain.
**Status:** Reproduced live on the latest supported pair; root-caused to the active-nonce boundary
combine; exact cardano reference-hash confirmation pending (see Open).
**Provenance (ground-truthed via `--version`):** amaru `v10.11.20260925` (git `eaf8ac3f`) — built from
source, incl. two DWARF substrate-enablement patches (below); cardano-node `11.1.2` (git `fef83fed…`).
Custom testnet `testnet_42` (k=20, f=0.2, epochLength=400 slots, randomness stability window 300).
**Cross-reference:** a step beyond [[gap1-bootstrap-trust-source]] — gap1 = Amaru's different nonce
*trust source*; this = Amaru's epoch-transition active-nonce *combine input* diverging while the
accumulated candidate nonce AGREES with cardano-node.

## Summary

At the epoch 2→3 boundary, Amaru validates the first block of epoch 3 (slot 1200) and rejects it with
`Invalid VRF proof: VRF proof verification failed`. cardano-node accepts and applies the same block.
The block is valid (forged by cardano-node; cardano consumers sync across it normally).

The VRF check depends on the **epoch (active) nonce**. Amaru's accumulated **candidate** nonce is
byte-identical to cardano-node's, but the **active** nonce Amaru derives at the boundary differs, so
the VRF proof — valid under the correct epoch-3 nonce — fails.

## Evidence

### 1. Reproduction (real, not a store-build artifact)

- Built a **native** 0925 Amaru store (0925 `snapshot create` + 0925 `node bootstrap`, no cross-version
  binary, no chain-db migration; `import.utxo size=7`, nonces imported). Served with 0925, sole peer =
  a live cardano-node producer.
- Amaru forward-syncs (sends `RequestNext`, receives+validates headers) then **rejects slot 1200**:
  `Failed to validate header at 1200.79e0995f…(234): Invalid VRF proof`. Reproduced every reconnect,
  on both the native store and an independent d807-bootstrapped+migrated store.
- cardano-node (11.1.2), same sole-peer setup, forward-syncs across 1200 to the tip. So transport +
  the block are fine; the rejection is Amaru-side.

### 2. Candidate nonce AGREES; active nonce DIFFERS (the discriminator)

Same chain (`amaru dev ledger nonces get` vs `cardano-cli query protocol-state`):
- Amaru candidate @ epoch-2 tip = `e113e77a…379691c0`
- cardano candidateNonce = `e113e77a…379691c0`  → **identical** (accumulation + stability-window freeze correct).
- cardano epoch-1 `epochNonce` = `e113e77a…` (confirms `e113e77a` is a real active nonce cardano uses).

So Amaru's nonce *evolution* matches cardano-node. This rules out [[gap1-bootstrap-trust-source]]'s
"different trust root" as the cause here — the divergence is only in the epoch-boundary active combine.

### 3. Byte-proof of Amaru's active-nonce combine (instrumented `evolve_nonces`)

Validating block 1200:
```
epoch=3 slot=1200
candidate       = e113e77a9f2f7f78691427fd2262542a4736c6dc03c8993feeb82f05379691c0
fed_hash        = 95ca422744a553291736dcda1facffd733c1423ef18822b492e4c5a3b45a5875   (previous_epoch_tail_parent_hash)
header_parent   = f4d474b8498a97a884f84b32ceede03bf392abae870a2b30b566f722192ff7bd   (epoch-2 LAST block = block 1200's parent)
computed_active = 3a5e36011eec01657c4478a9b6f021d8d0eced3232b2796b79f40d0cd0620ebe
```
- Formula confirmed by recomputation: `blake2b256(candidate ‖ fed_hash) == computed_active`.
- The active nonce Amaru uses for the epoch-3 VRF check is `3a5e3601…`.
- Because the block's VRF proof is valid (cardano accepts) but fails under `3a5e3601…`, **Amaru's
  active nonce is wrong**, while its candidate is right → the defect is the **combine input** `fed_hash`.
- Amaru feeds `previous_epoch_tail_parent_hash` (`95ca4227…`), which is **not** the epoch-2 last block
  (`f4d474b8…`). Leading hypothesis: an off-by-one on the boundary reference block (feeding the tail
  block's *parent* hash rather than the reference block's *own* header hash).

## Root cause (code)

`crates/amaru-ouroboros/src/praos/nonce.rs` `evolve_nonces`, epoch-change branch:
```rust
let (active, tail) = match previous_epoch_tail_parent_hash {
    Some(previous_epoch_tail_parent_hash) =>
        (parent_nonces.next_active(previous_epoch_tail_parent_hash), header.parent()…),
    None => (parent_nonces.active, parent_nonces.tail),
};
```
`next_active(h) = blake2b256(candidate ‖ h)` (`amaru-ouroboros-traits/src/praos/nonces.rs:29`).
The active nonce is `blake2b256(candidate ‖ previous_epoch_tail_parent_hash)`. The candidate is correct,
so the divergence is the hash `h` passed here vs the reference block hash cardano-node's ledger uses for
the epoch nonce.

## Severity & reachability

- The defect is in network-agnostic consensus code and triggers for **any** header that crosses an
  epoch boundary during forward-sync (`previous_epoch_tail_parent_hash = Some`) — it is **not** a
  bootstrap-only artifact. A live Amaru that forward-synced epoch 2 computes the same wrong active nonce
  and would reject block 1200 identically.
- It is **latent in normal Amaru deployments** because they follow the chain via the
  `amaru-bootstrap-producer` (re-snapshot near-tip), not single-peer forward-sync across a boundary
  (which is separately impaired — `pragma-org/amaru#736`). It becomes reachable exactly on the
  forward-sync-across-a-boundary path.
- Hence **MEDIUM-HIGH**: high impact (a node that hits it cannot follow the chain across an epoch),
  conditionally reached. Downgrade to MEDIUM if the reference-hash turns out custom-testnet/config
  specific (the stability-window governs which block is the "tail"); upgrade toward HIGH if confirmed
  general.

## Reproduction

1. Native 0925 store from a k=20/epoch-400 cardano chain, tip at an epoch boundary (bootstrap `--epoch N`).
2. `amaru node run` with a single live cardano-node producer peer (advancing past the boundary).
3. Observe `Invalid VRF proof` at the first slot of epoch N; instrument `evolve_nonces` (eprintln of
   candidate/fed_hash/computed_active) to capture the combine; compare candidate to
   `cardano-cli query protocol-state`.

## Recommendation

Verify the reference block hash fed to `next_active` at an epoch boundary matches cardano-node's ledger
rule for the epoch nonce (the last block of the previous epoch before the randomness stability window,
by its **own** header hash). Fix the `previous_epoch_tail_parent_hash` selection if it is off by one
block. Add a differential test: forward-sync an Amaru node across an epoch boundary from a cardano-node
peer and assert the first block of the new epoch is adopted.

## Open (to finalize classification)

- Capture cardano-node's exact epoch-3 `epochNonce` for the *same* chain and the reference block hash
  `H` s.t. `blake2b256(candidate ‖ H) = epochNonce`, to confirm `H` == the epoch-2 last block's own
  hash and that Amaru feeds its parent (nails the off-by-one). (Blocked in-session by db-analyser
  truncation flakiness / live-consumer sync speed; divergence already proven by the VRF failure.)
- Confirm whether the reference-block selection is config-sensitive (stability window) → decides
  MEDIUM vs MEDIUM-HIGH/HIGH.

## Patches used to enable the native 0925 store (substrate only; not part of the finding)

- `crates/amaru-kernel/src/cardano/network_name.rs` — `as_era_history` loads a testnet era-history from
  `AMARU_ERA_HISTORY` (upstream restricts bootstrap era-history to S3 mainnet/preprod/preview).
- `crates/amaru-bootstrap/src/cardano_node/tvar.rs` — `import_tvar_utxo` checks the definite-map length
  before probing for CBOR Break (currency of `finding-amaru-tvar-definite-map-decode-bug`, still present
  in 0925 `eaf8ac3f`).
Plus the diagnostic `eprintln!` in `evolve_nonces` (byte-proof only).
