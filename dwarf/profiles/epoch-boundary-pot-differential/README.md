# Profile: epoch-boundary-pot-differential

Differential of the **pot transition across an epoch boundary** between amaru and cardano-node.

Where `invalid-tx-body-leak` compares a single post-apply snapshot, this profile forges a chain that
**crosses an epoch boundary** and compares the *delta* each node applies to the comparable pots
(`treasury`, `reserves`, `fees`) as the epoch rolls. A divergence means amaru's epoch-boundary pot
accounting differs from the cardano-node reference.

## Substrate
- 1 Amaru node + 1 cardano-node (mixed), forged-block bridge (PATH-B), network magic 42, testnet-only keys.
- A multi-block chain is served that advances the tip **past the next epoch boundary** so both nodes
  perform the epoch transition; empty extender blocks are used except where a scenario seeds a
  specific effect (e.g. the confirmed is_valid=false donation).

## Flow
1. Read pots on both nodes **before** the boundary (snapshot A) — amaru `pots.dump`, cardano
   `cardano-cli conway query ledger-state`.
2. Serve the boundary-crossing chain; confirm both nodes adopt and apply past the boundary.
3. Read pots on both nodes **after** the boundary (snapshot B); for amaru use a `--keep-store`
   restart so the persisted post-transition store is read.
4. `epoch_boundary_pot_differential transition-diff` computes each node's Δ(A→B) and diffs amaru's
   transition against cardano's. Verdict `CONFORMANT` or `POT_TRANSITION_DIVERGENCE`.

## Known risk
amaru's documented `RewardsSummaryNotReady` / dormant-epoch fragility lives at the boundary. A crash
there is a **flagged outcome** (classified crash-vs-clean), not a harness failure.
