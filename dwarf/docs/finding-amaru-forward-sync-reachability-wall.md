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


## Consensus-gap reachability (TM-030 density, TM-031 Genesis-GDD, RR-027 eclipse) — probed 2026-09-27

The three remaining pv10 consensus threats were probed against the existing consensus-differential base
(the `cardano_amaru` upstream mesh + `runtime_attach_topology` + `runtime_multi_node_observation` +
`chain_select_differential`). Scenario YAMLs were authored for all three (declared coverage, schema-valid,
existing primitives only): `consensus-praos-density-chain-growth-differential`,
`consensus-genesis-gdd-candidate-selection-differential`,
`consensus-sync-peer-concentration-eclipse-differential`.

**Substrate reachability (what actually runs):** the chain-selection differential machinery IS real and
runs Amaru live — Amaru follows the chain in the `cardano_amaru` **mesh** because it bootstraps from a
migrated chain-DB (`AMARU_MIGRATE_CHAIN_DB=true`, entrypoint `amaru-relay-bootstrap`, upstream `p1`) and
then tracks the producers' chain. This is the **bootstrap-producer** path — NOT the single-peer
forward-sync-from-genesis that stalls (above). Proven historically: `stage1-tiebreak` ran 4 h / 154 iters
reading a native Amaru relay tip vs cardano-node with 0 genuine divergences. So a **freshly deployed**
mesh is required to run these (the currently-deployed mesh had been up 37 h and was dormant — Amaru
emitting only connection-manager span churn, no `roll_forward`/apply, producers not forging; classic
long-uptime devnet freeze, redeploy needed — deploy is owned by the lead).

**Per-gap verdict (verified against amaru `10.11.0`/`aedfe797` source on the mesh):**

- **TM-030 — Praos density / chain-growth: MECHANISM PRESENT, condition-induction WALLED.**
  Amaru *does* implement the chain-growth (>= k-blocks-per-window) rule:
  `amaru-ledger/src/state.rs:606-617` emits `ledger::chain_growth::VIOLATE` ("chain growth violation: less
  than k={k} blocks seen in a window …"), and `volatile/db.rs:366` reasons about epochs with `< k` blocks.
  The `chain_select_differential` oracle is live-capable. The wall is **inducing a genuine
  min-density-violating chain**: the mesh producers forge a healthy chain, so only a *bounded density dip*
  can be induced via `runtime_network_partition` (the authored scenario), not a true sustained
  `< k`-in-window violation. Closest-to-reachable of the three; the authored scenario exercises the
  chain-growth/reconvergence path under a partition-induced density dip and asserts cardano/Amaru agree.

- **TM-031 — Genesis-mode GDD governor: WALLED (no Amaru counterpart).**
  Amaru has **no Genesis-mode GDD** (Genesis Density Disconnect) governor: no `gdd` / `LoE` /
  limit-on-eagerness / chain-sync-jumping / genesis-mode candidate-density-disconnect in the source. Its
  chain selection (`amaru-consensus/src/stages/select_chain/mod.rs:484-486`) is **Praos only** (longer
  chain; VRF + opcert-index tiebreak at equal length; slot-distance ≤ 5). Combined with
  [[gap1-bootstrap-trust-source]] (Amaru cannot validate from genesis; pins leader-election nonces
  in-binary), Amaru does not perform the Genesis-mode candidate-selection that GDD governs, so the GDD
  governor has **nothing to differentially validate** against cardano-node. The authored scenario is
  declared coverage of competing-candidate selection under Praos (partition→heal), explicitly NOT the GDD
  density-disconnect governor.

- **RR-027 — sync-mode peer-set concentration / eclipse: WALLED (two ways).**
  (1) **Harness:** `runtime_simulate_peer_set_capture` is **synthetic** — it injects hardcoded
  observation overrides (`peer_set_capture_detected: False`, fabricated `honest_peer_counts` and
  `topology-tip-120` tips) into the run metadata (`runtime_topology_fault.py`
  `apply_topology_mode`); it does not manipulate the real network or actually eclipse a node. So the
  existing eclipse smoke scenarios assert on stub data.
  (2) **Amaru:** Amaru advertises `PeerSharing::Disabled`
  (`amaru-protocols/.../version_data.rs`, `version_table.rs`) and syncs from **statically configured
  upstream peers** — it has no P2P peer-governor / ledger-peers / big-ledger-peer selection like
  cardano-node's, so there is **no cardano-style eclipse-resistance mechanism to differentially compare**.
  The authored scenario is declared coverage only.

**Net:** closing these three as *runtime* differentials is blocked — TM-031 and RR-027 by the absence of
an Amaru counterpart (Genesis-GDD; P2P peer governor) and RR-027 additionally by a synthetic harness
primitive; TM-030 by density-condition induction on a healthy-forging mesh (the Amaru mechanism itself
exists). All three are authored as schema-valid declared-coverage scenarios so they surface in
`/operate` + `/coverage`; runtime execution of TM-030's partition-induced density-dip agreement check is
possible on a freshly deployed mesh.
