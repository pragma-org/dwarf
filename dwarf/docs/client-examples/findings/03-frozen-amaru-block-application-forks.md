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

## Exact reproduction

Run `20260920T143735Z-69f67c6a` repeated the exact frozen scenario on a fresh, healthy deployment. It retained 60
adopted-block records, 73 application samples, and 60 correlations. Height 413 and slot 1837 changed from block
`cc8ee5aa924313f61b9ca8e56c1a74b4d93634934efdb4a0a9d38920f67da421` to block
`220f8f4b318dd9fb3f8fa4e2b7c840d5fb881e5406bda855c90aa65c1838d294`. The run again failed the frozen strict
monotonic-height check before assertions.

- Manifest SHA-256: `0fbdf1afe72ad86d7e80d6b656ebcd4336610605ae8230779835e47b613da792`
- Controlled proof SHA-256: `baefccabd04c45c9877d7f5a7b8a44667d5a31da2020df0da00827dee64527f6`
- Measurement report SHA-256: `1e0c2da207f79f1322802138631a7f5b6fb415a41278882906d0931fb62db7e1`
- Verified 79-file bundle SHA-256: `44ac2202e507fe4a28c1f853197c20a7b13751c2f748bc9eba7069bd9d419b28`

## Historical decision boundary

Card 04 was incomplete until the user approved a separate canonical-progress proof. The old runs and their failed strict-monotonic verdicts remain unchanged.

## Approved resolution

The strict monotonic check over raw adoption events is replaced by `canonical-progress-v2`. DWARF must keep every adoption, same-height hash switch, rollback or fork event, and application timing. It derives a separate canonical-progress proof and requires bounded progress, final convergence, complete required correlations, and healthy target state.

A bounded same-height fork switch alone does not fail. No progress, non-convergence, excessive or continuing oscillation, missing correlations, panic, fatal exit, OOM, or unexpected restart fails. The old v1 runs and their verdicts remain unchanged.

Child explanation: Keep every turn the chain takes. Judge whether it settles on one path and keeps moving, not whether it ever took a short detour.

## Accepted resolution evidence

Run `20260921T035546Z-9747122c` used the additive Amaru `nanoseconds-v3` target and canonical-progress-v2 scenario. It passed all four assertions. The window advanced 69 blocks, retained 56 adopted identities and 56 correlations, and kept 16 explicit fork or rollback events. One same-height switch was followed by 36 stable advances. The target had no panic, fatal exit, OOM, or restart.

Every accepted proof timing retains integer nanoseconds and exact fractional microseconds. The standard report contains 69 samples from `patched-monotonic-nanoseconds`.

- Manifest SHA-256: `96408821a8b7b88d5fac594bf706d330993555a68d9e3fabb29828199cf49903`
- Controlled proof SHA-256: `1266fa35d98d4a20225fef5b5b74be583214f7bc3491b63a589b8a33ff397f5b`
- Measurement report SHA-256: `78e9eab912ba410a709591ea363ad94be3847b767f53b57410312135fdaa7c7d`
- Verified 81-file bundle SHA-256: `f17b4892ca45d003a94b7c175d510cbd0890420bdf79420a46ed4dff79c0c808`

Status: Closed by additive canonical-progress-v2 evidence. The historical finding remains reproducible.
