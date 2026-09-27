# Negative result: Amaru single-peer forward-sync does not sustainably follow a chain

**Component:** `amaru` consensus — chain-sync / block-fetch (`crates/amaru-consensus/src/stages/track_peers/`)
**Type:** Reachability / negative result (currency datapoint). NOT a new finding — `pragma-org/amaru#736`
already tracks single-peer forward-sync; this characterizes its shape and its consequence for
block-level differential testing.
**Provenance:** amaru `v10.11.20260925` (git `eaf8ac3f`) vs cardano-node `11.1.2` (`fef83fed`), custom
testnet `testnet_42` (k=20, f=0.2). 2026-09-27.
**Cross-reference:** extends [[finding-amaru-epoch-boundary-active-nonce]] (finding #3, the
boundary-specific VRF case). This does **not** affect #3 (see below).

## Observation

Amaru, as a single-peer chain-sync client against a live cardano-node producer, does **not** sustainably
adopt forward-synced blocks. On each attempt it completes the handshake, `chainsync.intersect_found` at
its tip, `chainsync.roll_backward` to that tip, sends `RequestNext`
(`track_peers/mod.rs:945`, confirmed in source) — and then **stalls**: no `roll_forward`, no header
validation, tip frozen.

Reproduced across every configuration:
- amaru bootstrapped to a **mid-epoch** tip (slot 1000, block 213) — so the next block is within the
  same epoch, avoiding the finding-#3 boundary — via a truncated snapshot point;
- **correct chain** (intersect_found succeeded, so amaru's tip is on the producer's chain);
- peer-tip **gap 224 blocks < `max_peer_lead` (1000)**;
- **fresh** live producer tip (near wall-clock).

The only time amaru ever processed a forward-synced header was when the producer led by only ~37 blocks:
it validated exactly **one** header (the next block) and then rejected it on the finding-#3 VRF path. That
was a single header at a tiny gap, not sustained adoption. Whenever the peer leads by more than ~a few
dozen blocks, amaru stalls after `roll_backward`.

## Consequence

The block-level **crafted-block adoption differential** (does amaru adopt a block cardano-node rejects,
or vice-versa — opcert / KES / body-hash / VRF / header / oversized-body violations) is **not reachable
via single-peer forward-sync** on this substrate: amaru cannot be driven to adopt/validate a served block
to compare against cardano-node. This reinforces why Amaru deployments follow the chain via the
`bootstrap-producer` (periodic snapshot near-tip) rather than single-peer forward-sync.

Block-level differential coverage therefore stays at: finding #3 (epoch-boundary VRF, the one header that
does get validated) + the submit-path families. The crafted-block surface is deferred pending a
non-forward-sync injection path (or an upstream #736 fix).

## Does NOT affect finding #3

Finding #3 (epoch-boundary active-nonce mismatch) is source-traced (`evolve_nonce` /
`next_active`) and **byte-confirmed** from the bootstrapped store nonces (`amaru dev ledger nonces get`)
+ cardano-node `protocol-state` — independent of chain forward-sync or snapshot-point extraction. The
reachability wall here is orthogonal.

## Reusable substrate facts (for anyone rebuilding these devnets)

1. **The forge is NOT deterministic across `systemStart`.** Changing `systemStart` changes the
   shelley-genesis (hence the genesis-hash-derived initial nonce), which changes the VRF leader schedule
   and therefore all block hashes. So snapshot points and bootstrapped stores from one forge cannot be
   reused against a different forge (`intersect_not_found`). Always derive points from the forge you will
   serve from.
2. **db-analyser `--show-slot-block-no` writes its `BlockNo … SlotNo … hash` lines to STDERR**, not
   stdout. Capture with `2>&1`; a `2>/dev/null` silently drops them (looks like "0 blocks").
