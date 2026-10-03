# Clean-negative: amaru chain-selection tiebreak CONVERGES with cardano Conway Praos (no chain-split)

**Hypothesis (user-picked, to verify):** amaru's equal-length fork-choice tiebreak has an
"amaru-specific" `<=5`-slot gate (`cmp_tip` returns `Ordering::Equal` beyond it => no switch); if
cardano's Ouroboros Praos tiebreak lacks that gate (or uses a different max slot distance), the two
implementations would adopt different tips for the same competing fork = **chain split / reorg divergence**.

**Verdict: REFUTED at the source level. CONVERGENT.** amaru's `cmp_tip` is a point-for-point match of
cardano 11.1.2's Conway Praos chain order (`RestrictedVRFTiebreaker 5`). The hypothesized chain-split
does not exist; amaru correctly implements the Conway Praos tiebreak. Source-anchored at the running
revisions (no stale-line risk).

## amaru (consensus rev 9eb5971f) — `crates/amaru-consensus/src/stages/select_chain/mod.rs:491`

```rust
pub fn cmp_tip(a: Option<&Header>, b: Option<&Header>) -> Ordering {
    // (None/Some handling ...)
    a.block_height().cmp(&b.block_height()).then_with(|| {
        let a_leader = vrf::Derivation::Leader.derive_tagged_vrf_output(a.vrf_output());
        let b_leader = vrf::Derivation::Leader.derive_tagged_vrf_output(b.vrf_output());
        if a_leader == b_leader {
            a.op_cert_seq().cmp(&b.op_cert_seq())
        } else if (a.slot() - b.slot()).abs() <= 5 {
            b_leader.cmp(&a_leader)          // lower leader-VRF wins
        } else {
            Ordering::Equal                  // slots >5 apart => no tiebreak, keep current
        }
    })
}
```

## cardano-node 11.1.2 — `ouroboros-consensus-4.2.1.0`

- `ouroboros-consensus-protocol/.../Praos/Common.hs` `comparePraos`: equal block-no => VRF tiebreak with
  **lower VRF preferred** (`compare on Down . ptvTieBreakVRF`); `vrfArmed` =
  `RestrictedVRFTiebreaker maxDist -> slotDist <= maxDist`; when the VRF is **not armed** (slots farther
  than maxDist) and issue-no not armed => `ShouldNotSwitch EQ` (keep current). `issueNoArmed` = same slot
  AND same issuer.
- `ouroboros-consensus-cardano/src/shelley/.../Ledger/Config.hs:90-93`:
  `shelleyVRFTiebreakerFlavor = isBeforeConway ? UnrestrictedVRFTiebreaker : RestrictedVRFTiebreaker 5`
  ("5 slots is the usual maximum propagation delay"). The devnet is Conway (era 6) => **maxDist = 5**.

## Point-for-point

| rule | amaru `cmp_tip` | cardano Conway `comparePraos` | match |
|------|-----------------|-------------------------------|-------|
| primary order | longer `block_height` wins | higher block-no wins | YES |
| VRF tiebreak direction | `b_leader.cmp(a_leader)` — lower wins | `Down . ptvTieBreakVRF` — lower wins | YES |
| slot gate | `abs(slotA-slotB) <= 5` | `slotDist <= maxDist`, `maxDist = 5` | YES (both 5, inclusive) |
| outside the window | `Ordering::Equal` (no switch) | `ShouldNotSwitch EQ` (no switch) | YES |
| same-slot tiebreak | `op_cert_seq` when leader-VRF equal | `issueNo` when same slot + same issuer | equivalent (VRF-equal is the collision edge of same-slot/issuer) |

## Conclusion

Conformance clean-negative. amaru's equal-length chain-selection tiebreak is the Conway Praos
`RestrictedVRFTiebreaker 5`, identical to cardano-node 11.1.2 in gate value, VRF direction, and
keep-current-outside-window behavior. No chain-split divergence from this tiebreak.

**Live runtime confirmation: skipped (intentional).** Observing `cmp_tip` at runtime requires a two-tip
fork, i.e. a dual-serve / two-peer substrate plus a 2nd competing-height-214 forge — not genuinely
cheap, and the source is anchored at the correct running revisions (amaru 9eb5971f, ouroboros 4.2.1.0),
so a live touch is nice-to-have, not required.

**Deferred follow-on (sharper, separate build):** the k-deep rollback immutability
(`InvalidRollback { rollback_point, max_point }`, errors.rs:51; `consensus_security_param` k=20) — a
true deep-reorg security property — needs a serve `RollBackward` extension (wire-side) + a ~k-deep
substrate; tracked as a follow-on, not covered here.
