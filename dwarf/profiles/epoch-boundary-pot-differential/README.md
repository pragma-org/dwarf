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

## Live-run status / substrate limitation (2026-10-03)

The primitive + assertion + both scenarios are built, validated (gate at 339 scenarios), and the
engine is verified on both verdicts (identical transition -> CONFORMANT; amaru treasury Δ +5000000
vs cardano 0 -> POT_TRANSITION_DIVERGENCE).

The **live** crossing is currently substrate-walled: the leader-election nonce changes at the epoch
boundary, so forging the block that crosses into epoch 4 (slot >= 1600) requires amaru's computed
**epoch-4 active nonce**, not the epoch-3 nonce. The forge chain reaches height 238 / slot 1598 (the
last epoch-3 leader before the boundary) and stops there by design rather than improvise a nonce.
Completing the live crossing is a focused follow-up (extract amaru's epoch-4 active nonce) and is
entangled with finding-#3 (amaru epoch-boundary active-nonce off-by-one).

This does not affect the donation value-creation finding, which is independently CONFIRMED via the
stable donations pot (donations=5000000 vs GOLDEN baseline 0, hash-verified, reproduced) — see
dwarf/docs/finding-amaru-donation-isvalid-false-value-creation.md. The treasury-field realization at
the boundary is the extra downstream link, not required for the finding.
