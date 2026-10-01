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

---

## UPDATE — cardano-side cross-check (2026-09-27): computed-nonce divergence byte-confirmed; simple off-by-one NOT confirmed

Obtained cardano-node's epoch-2 protocol-state (frozen node, truncated immutable to epoch 2):
```
epoch 2:  epochNonce(η2)      = b0fede8787…441d5e7   == amaru's epoch-2 active  (imported nonce CORRECT)
          candidateNonce      = e113e77a…379691c0    == amaru's candidate       (accumulation CORRECT)
          lastEpochBlockNonce = 95ca4227…b45a5875    == amaru's fed_hash        (see below)
```
Key results (all values from the SAME chain — forge is deterministic from genesis+keys, so the
"pair" and "live" runs are byte-identical: block 1200 hash = 79e0995f on both):

1. **Amaru's imported nonces are correct.** η2 (b0fede87) and the candidate (e113e77a) match cardano
   byte-for-byte. Amaru only diverges once it must **self-compute** an epoch nonce (the first
   forward-synced boundary, epoch 2→3) rather than use a bootstrap-imported one.
2. **Amaru's self-computed epoch-3 active nonce is byte-reproduced:**
   `computed_active 3a5e3601… == blake2b256(candidate e113e77a… ‖ epoch-2 lastEpochBlockNonce 95ca4227…)`.
   So Amaru combines the candidate with the **epoch-2** `lastEpochBlockNonce`.
3. **cardano-node's η3 = 44f12751… could NOT be reproduced** by combining the candidate with ANY
   available nonce field (epoch-2/epoch-3 `lab`/`lastEpochBlockNonce`/`evolving`, η2, or the epoch-2
   last block header hash f4d474b8…) under blake2b(a‖b), blake2b(b‖a), XOR, or hash-to-nonce wrappings
   (exhaustive pairwise search: no match). cardano's epoch nonce uses a reference not in these fields —
   most likely the header hash of the last block **before the randomness stability window** (~slot 899
   for this 400-slot epoch / 300 window), which was not captured.

**Revised conclusion.** The DIVERGENCE is byte-confirmed: Amaru's self-computed epoch-3 active nonce
(3a5e3601, = candidate ‖ epoch-2 lastEpochBlockNonce) ≠ cardano-node's η3 (44f12751), so the valid
block's VRF fails. The earlier "off-by-one (parent vs own header hash)" was a HYPOTHESIS and is **not
confirmed** — cardano's η3 is not reproduced by the epoch-2 last block (f4d474b8) either. The precise
correct construction (which reference nonce/hash + combine) needs a cardano ledger-spec / amaru-source
cross-check, not just these runtime values. What is certain: Amaru's epoch-nonce **computation**
(`evolve_nonces`/`next_active`) produces a value cardano-node disagrees with, and it is masked in normal
operation because bootstrap **imports** the correct nonces (only self-computation at a forward-synced
boundary exposes it).

**Severity unchanged: MEDIUM-HIGH.** The divergence and its reachability (any single-peer forward-sync
across an epoch boundary; latent under bootstrap-producer deployments) are confirmed; only the exact
fix input remains open.

**Recommendation (updated).** Cross-check `evolve_nonces`/`next_active` against the cardano ledger
epoch-nonce rule (`newEpochNonce`: candidate ⭒ hashHeaderToNonce(last-block-before-stability-window)).
Amaru feeding the epoch-2 `lastEpochBlockNonce` suggests either a wrong reference selection or a
one-epoch-stale `previous_epoch_tail_parent_hash`. Confirm with a differential unit test seeded from a
cardano-derived boundary.

---

## SOURCE CROSS-CHECK (2026-09-27) — precise root cause: stale (wrong-epoch) reference block

Traced the exact reference amaru feeds, in `crates/amaru-consensus/src/store.rs` `evolve_nonce`:
```rust
// epoch boundary only:
let previous_epoch_tail_parent_hash = if epoch > parent_nonces.epoch {
    self.store.load_header(&parent_nonces.tail)?.parent_hash()   // parent of parent_nonces.tail
} else { None };
```
and `Nonces::next_active(h) = blake2b256(candidate ‖ h)` (`amaru-ouroboros-traits/src/praos/nonces.rs:29`),
with `tail` set at each boundary to `header.parent()` = the previous epoch's last block
(`amaru-ouroboros/src/praos/nonce.rs:53`, carried unchanged within an epoch).

**Index trace at the epoch 2→3 boundary (block 1200), against the byte-proof values:**
- `parent_nonces` = nonces at block 1199 (epoch 2). Its `tail` was set when crossing into epoch 2, so
  `parent_nonces.tail = 842e12ed…` = the **last block of EPOCH 1** (slot 798).
- `previous_epoch_tail_parent_hash = load_header(842e12ed).parent_hash() = 95ca4227…` — the **parent of
  epoch 1's last block** (a block at ~slot 797, still epoch 1).
- `epoch-3 active = blake2b256(candidate e113e77a ‖ 95ca4227) = 3a5e3601…` (matches the runtime).

So Amaru derives the **epoch-3** active nonce from an **epoch-1** block reference. Per the Cardano/Praos
rule (`newEpochNonce = candidateNonce ⭒ hashHeaderToNonce(last block of the IMMEDIATELY-previous epoch
before the randomness stability window)`), the epoch-3 nonce must reference an **epoch-2** block. Amaru's
reference is a full epoch stale (and takes the block's *parent* rather than its own header hash).

This is why the divergence is masked until the first self-computed boundary: bootstrap **imports** the
correct nonces (η2 = b0fede87 matched), so `parent_nonces.tail` is only *used* to self-derive a nonce
at the first forward-synced epoch crossing — where the stale reference produces `3a5e3601 ≠ η3 44f12751`
and the valid block's VRF fails.

(Note the combine is raw `blake2b256(candidate ‖ hash)`. Even with the correct epoch-2 reference nonce
`blake2b256(candidate ‖ 7631da8e)` = `8a58f3d3` ≠ η3, so cardano's `⭒`/`hashHeaderToNonce` construction
may also differ from raw concatenation — but the **wrong-epoch reference selection above is the primary,
verified defect**; confirm the exact combine when fixing.)

## Precise recommendation (filable)

In `store.rs::evolve_nonce`, the epoch-boundary reference must be the **last block of the
immediately-previous epoch** (before the randomness stability window), by its **own** header hash, fed
through the Praos `hashHeaderToNonce` + `⭒` combine — not `parent_of(parent_nonces.tail)` where
`parent_nonces.tail` lags a full epoch. Concretely: amaru feeds an epoch-1 block for the epoch-3 nonce;
it must feed the epoch-2 pre-stability block. Add a differential unit test seeded from a cardano-derived
epoch boundary asserting the computed active nonce equals `cardano-cli query protocol-state`'s
`epochNonce` for that epoch.

## Confound discipline (what was ruled out)

An earlier proxy-path symptom — amaru handshaking then stalling right after chainsync
intersect_found — was traced to a re-sign artifact of the live-proxy re-signing every header,
NOT an amaru forward-sync defect. Amaru single-peer forward-sync of real (unmodified) headers
works; that false symptom was therefore correctly NOT filed as a single-peer-forward-sync
issue (pragma-org/amaru#736 class). The finding above (epoch-transition active-nonce) is the real,
reproduced defect.

## Live adversarial confirmation — amaru ADOPTS a buggy-nonce block at the boundary (2026-09-30)

The reject above is one side of the bug. This is the other side, demonstrated LIVE: amaru **adopts**, at
the same epoch 2→3 boundary, a block whose VRF is valid ONLY under amaru's *wrong* active nonce — a
block cardano-node rejects. Together the two directions prove the active-nonce derivation is wrong, not
merely stricter.

**Setup.** Pair1 amaru (eaf8ac3f), frozen at tip `[1199, f4d474b8…f722192ff7bd, height 233]` (epoch-2
last block). A single forged block was served over one socket (amaru dials its upstream `:3001` as the
chain-sync initiator; the serve answers RollForward + block-fetch). The block was forged by pool
`97b0f582…` at **slot 1211** (epoch 3), height 234, prevHash `f4d474b8…`, with the VRF computed over
`mkInputVRF(1211, eta0)` using amaru's OWN buggy epoch-3 active nonce
`eta0 = 3a5e36011eec01657c4478a9b6f021d8d0eced3232b2796b79f40d0cd0620ebe`
(= `blake2b256(candidate e113e77a… ‖ tail_parent 95ca4227…)`, the derivation this finding identifies as
wrong; cardano's correct epoch-3 nonce differs).

**Result — ADOPT (amaru log `/tmp/amaru-pair1.log`, 2026-09-30 21:58:24):**
```
chainsync.intersect_found peer="192.168.30.16:3001" current="[1199, f4d474b8…, 233]"
                                                     highest="[1211, 2304558b6afe05e970c0ba0d2a470bd4cbb52b5d62ce73ed02eab7b4872fa697, 234]"
epoch_transition.compute … from=2 into=3
tip.adopt slot=1211 header_hash="2304558b…872fa697" block_height=234 max_block_height=234
```
Zero `Invalid VRF proof` entries; amaru stayed up (submit API 400). Across subsequent reconnects amaru's
`current` tip is persistently `[1211, 2304558b…, 234]` — it committed the forged block as its chain tip.

**Interpretation.** amaru accepted an epoch-3 header whose VRF only verifies under its buggy active nonce
`3a5e3601…`; cardano-node (using the correct nonce) rejects that same header. This is the exact mirror of
the earlier live reject, where amaru rejected the *correct-nonce* first-block-of-epoch that cardano
accepts. So amaru's epoch-boundary active-nonce is provably wrong in **both** directions — it rejects
valid blocks and adopts blocks valid only under its own miscomputed nonce.

**Status upgrade:** finding #3 is now LIVE-reproduced adversarially (not only source-proven). This also
proves the single-socket serve→header-adopt bridge end-to-end. Forge/serve provenance: pool 97b0 keys
(blocklevel/keys), forced nonce 3a5e3601, block.json point_hash 2304558b… (blake2b256 of the forged
header). Empty (milestone-1) body — header-level adoption; body block-fetch + ledger-apply is the
milestone-2 step for crafted-body block-apply findings.
