# GitHub Stock-Solo Proof Design

## Objective

Provide a bounded GitHub-hosted proof that the submitted Amaru stock candidate and the selected Cardano stock release can execute DWARF's existing local measurement scenarios and produce verified portable evidence.

## Scope

Manual dispatch accepts `stock-solo` or `full`. `stock-solo` builds only the Amaru stock variant, performs its existing stock calibration, runs the existing Amaru stock measurement shard, then runs the existing Cardano stock measurement shard. Cardano starts only after Amaru succeeds. Mixed, patched, compiler-coverage, Plutus-gap, release publication, and Pages-default updates are excluded from this proof.

Automatic `dwarf/testplease/**` pushes retain the existing `full` behavior.

## Evidence and claims

The selector freezes a stock-solo inventory containing only the two executable solo measurement recipes. Original campaign blockers remain retained as deferred full-campaign blockers. Aggregation verifies exact work-item membership, checkpoint digests, run success, cleanup, and signed archive receipts. A successful result is labelled `stock-solo` and `scoped-complete`; it does not claim full measurement completeness or a security result.

## Failure behavior

The build fails if no immutable stock image is produced. Cardano does not start when Amaru fails. Aggregation fails if either shard is missing, unsuccessful, has cleanup residue, or has invalid evidence. Full publication is disabled for stock-solo proof runs.
