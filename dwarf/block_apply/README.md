# dwarf/block_apply — block-apply bridge

## Live bridge (in-tree; no external forge or wire tool)

Driven by the existing block-apply primitives (`dwarf/profile_manager/block_apply_primitives.py`)
through `dwarf/scripts/runtime_block_apply_bridge.py`, on a self-composed docker devnet with an
Amaru target and a cardano-node producer/reference:

1. `build_block_segments` with `recipe: isvalid-false-donation` builds the crafted transaction live
   from the devnet's genesis funding. A setup transaction (spend + collateral outputs) is submitted
   honestly to the producer and confirmed; the crafted transaction (mint under the always-failing
   Plutus V3 policy in `fixtures/always-fails-v3.plutus`, `is_valid=false`, treasury donation) is
   never submitted.
2. `forge_block` writes the forge plan: the producer pool's own `kes.skey`, proven to be the key
   its `opcert.cert` certifies, and `slotsPerKESPeriod` read from the devnet's genesis.
3. `serve_crafted_block` starts `scripts/byzantine_mux_proxy.py --mutation-mode forge_substitute`
   in front of the producer, repoints the Amaru compose service to it, starts an isolated
   cardano-node reference consumer whose only peer is the proxy, waits until both follow the tip,
   optionally takes mid-epoch pot snapshots, then arms the forge (`arm.json`).
4. The proxy (`scripts/block_forge.ForgeSubstitution`) takes the producer's next EMPTY leader block
   at/after the armed slot, keeps its slot/VRF/opcert, replaces the body with the crafted
   transaction, recomputes body hash/size and re-signs the header with the pool's KES key
   (`scripts/praos_kes.py`, Sum6KES). Every later block is re-parented the same way, so both
   consumers can follow the fork across one epoch boundary (the epoch nonce two epochs on
   depends on the forked hashes, so the fork ends there).
5. `block_apply_differential` records each node's own verdict on the forged hash: Amaru from its
   consensus log (`chain.select_from_block_validation valid=`, `tip.adopt`), the reference from its
   tip and its log. With `pot_snapshots`, Amaru is restarted to dump its stable pots
   (`pots.dump`), the reference's ledger state is queried, and snapshots are only compared when
   both are epoch-aligned (otherwise `inconclusive`).

Evidence: `outputs/block-apply-bridge/` (bridge-state.json, forge-plan.json, proxy/forge-record.json,
pots/*.json) and each step's `result.json`.

## Not yet live-supported

- Recipes other than `isvalid-false-donation` (the other block-apply scenarios still name the
  campaign scripts below and stay `deferred`/legacy).
- `conway-gov-scriptcontext-proposals-votes-fidelity`: its fixture transactions are now staged into
  the run dir, but they spend UTxOs of the 97b0 re-bake; a live rebuild of reg/pplain/prop3/vote is
  needed before its submit differential can say anything about ScriptContext.

## Legacy campaign scripts (kept for the scenarios that still name them)
- build_block_segments.py, build_*.py — body builders from the forge-work campaign
- assemble_blockjson.py, repoint-amaru-pair1.sh, block_apply_differential.sh — pair1 host layout
  (/home/nigel/forge-work/pathb); not usable on a self-composed devnet.
