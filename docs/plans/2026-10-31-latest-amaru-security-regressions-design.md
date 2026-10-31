# Latest Amaru Security Regressions Design

## Goal

Run decisive security regressions against the operator-selected Amaru
`10.11.20261029` source build while preserving every historical finding and its
frozen target identity.

## Approaches considered

1. **Add latest-release regression variants and bind their inline substrate to
   the selected operator registration.** This preserves historical scenarios,
   gives each run exact source/image provenance, and uses DWARF's normal
   Prepare/Run/Evidence flow. This is the selected approach.
2. Change historical finding scenarios in place. This is smaller, but it would
   destroy the meaning of retained `20260925` evidence and invert old expected
   outcomes.
3. Launch one-off containers and submit fixtures directly. This is fast, but it
   bypasses DWARF's run ledger, controls, assertions, and identity evidence.

## Scope

The first executable case is the value-coin `i64` boundary crash regression.
It uses the operator-selected Amaru as the sole security target and a catalog
Cardano node only as the honest producer required to create the private
devnet. One fail-closed workload constructs a valid included control and two
fresh signed transactions from isolated generated-genesis inputs. The
`i64::MAX` boundary control must reject while the target remains live before
the `i64::MAX + 1` mutant is submitted. Both rejection oracles require the
value-conservation diagnostic; decode, spent-input, fee, conflict, and unknown
client errors remain inconclusive. A fixed 1 ADA fee prevents the CBOR-size
change from becoming a fee oracle. After the mutant, the boundary transaction
is resubmitted to detect a dead mempool even when the container remains live.

The governance, ExUnits, and unregistered-DRep candidates are not promoted into
this first live batch. Governance and ExUnits require a qualified exact mixed
pair. The retained unregistered-DRep fixture lacks an independent fresh-funded
control on the generated substrate, so a solo run would be masked or
inconclusive.

Collateral-witness and invalid-donation block-apply regressions remain out of
this batch because the public block forge/serve primitives are explicit stubs or
external dependencies. A green result from those paths would not be security
evidence.

## Identity and evidence contract

Inline `resolved-plan` substrate nodes may resolve either to a registry image
manifest or an operator-local image config ID. Registry nodes retain
`image_digest`; local nodes retain `image_config_id`, executable digest,
registration ID, controller installation ID, and source revision. The launch
materializer accepts exactly one of those two identity shapes and fails closed
on missing or mixed fields.

The run-plan resolver must pass `operator_registration_id` into inline
substrate version resolution. Inline nodes marked `supporting` do not widen the
target resolution scope, so the selected Amaru registration is permitted by the
existing Amaru-only gate; cardano-node remains catalog-resolved production
infrastructure. The retained
plan and materialized scenario must both name source revision
`23031e0ed4423b9cb899da030d515cb69d203749` and config image ID
`sha256:a3d96666253abb713f53a1c1f7c0b5cb8b75a406036b7fe9ce116f26be7a71f1`.

## Stop conditions

Stop a run and preserve evidence if the selected identity changes, the boundary
control does not cleanly reject, either node becomes unresponsive, the malformed
transaction is accepted or crashes the target, or DWARF cannot classify a
submission outcome. A failed
security assertion is a retained result, not a framework retry trigger.

## Verification

Focused tests first reproduce the current bug: inline scenarios did not forward
the selected local registration and launch freezing required a registry
manifest digest. After the repair, tests verify registry and local-config
materialization separately, target/supporting scope, exact CBOR coin mutation,
strict rejection classification, post-mutant service health, fresh-input
assessment, and scenario semantic validation. Deployment verification then checks
the rendered plan before Prepare, runs one control/case at a time, and inspects
the retained manifest, assertions, exact identities, node health, and cleanup.
