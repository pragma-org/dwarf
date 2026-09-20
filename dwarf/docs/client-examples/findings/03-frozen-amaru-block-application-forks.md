# Card 04 frozen-window finding: same-height Amaru fork switches

Status: The Amaru block-application leg cannot be accepted from the retained rehearsal.

Child explanation: The node saw two different blocks for the same place in the chain. The frozen check says that every next block number must be larger, so the test stopped.

## Exact boundary

Run `20260920T114304Z-a8ac5c5e` used the exact Amaru `nanoseconds-v2` target and the frozen Card 04 scenario. The run retained 58 adopted-block records, 69 accepted application samples, and 58 temporal correlations.

The target emitted two real same-height changes:

- Height 851 and slot 4188 changed from block `8a9839569b9385c6e93735d19cf531422dcee94fbd83e6581385f3a46eb98818` to block `8ac264ecfb69c2fb7a0ed5790b951d4b7fea4b66e5b5ee00f6a2718ebb9d5148`.
- Height 856 and slot 4231 changed from block `c50e93ef2c6433b0a73fab1517e94b5003a9c9853280b3fdd1ee68a6cc7650ef` to block `35c7941f062eabf6aa242d1b5d81094183cb52a04dfbcacbeacf840cc2456bf9`.

The raw target log also contains `state.switch_to_fork`. Therefore, these records are not duplicate collector output.

## Frozen result

The proof checks were:

- `minimum_adopted_block_range_observed: true`
- `application_samples_correlated: true`
- `all_application_samples_correlated: false`
- `monotonic_height: false`

The frozen contract says to stop with failure on a non-monotonic target height. DWARF did not remove, sort, or rewrite the fork records.

## Retained evidence

- Manifest SHA-256: `2d40926822ee0260b735537ca65e0fece174911d4e0d68505d8dd1d6819b8363`
- Controlled proof SHA-256: `e86ecb56d637bc2e8ffeb5c99b90863e64c18779ffa84fb70638017f4fa2df7b`
- Measurement report SHA-256: `69e6cac76311f1102568008d86c8f4757ca6107e9277b2d5d31cbf5eaf66f7ec`

## Required decision

Card 04 remains incomplete for Amaru. An authorized contract decision must define whether a canonical-chain-only sequence can replace the current adopted-event sequence. This finding does not change the frozen monotonic assertion.
