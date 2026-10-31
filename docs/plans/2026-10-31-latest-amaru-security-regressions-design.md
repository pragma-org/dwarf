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

The first executable batch contains four cases whose primitives perform real
node interactions in the public installation:

- value accumulation overflow: target and reference must reject; both remain
  responsive;
- zero-denominator governance admission: run the matched nonzero control first,
  then the malformed case with conclusive per-node outcomes;
- ExUnits redeemer-sum overflow: the selected Amaru must preserve validation
  parity with cardano-node;
- unregistered DRep update: target and reference must reject; both remain
  responsive.

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
substrate version resolution. The selected Amaru registration replaces only the
Amaru member; cardano-node remains the catalog-resolved reference. The retained
plan and materialized scenario must both name source revision
`23031e0ed4423b9cb899da030d515cb69d203749` and config image ID
`sha256:a3d96666253abb713f53a1c1f7c0b5cb8b75a406036b7fe9ce116f26be7a71f1`.

## Stop conditions

Stop a run and preserve evidence if the selected identity changes, a normal
control fails, either node becomes unresponsive, the malformed transaction is
accepted unexpectedly, or DWARF cannot classify a submission outcome. A failed
security assertion is a retained result, not a framework retry trigger.

## Verification

Focused tests must first reproduce the current bug: selecting a local build for
an inline resolved-plan scenario still yields the old catalog Amaru. After the
repair, tests verify registry and local-config materialization separately and
semantic validation for all new scenarios. Deployment verification then checks
the rendered plan before Prepare, runs one control/case at a time, and inspects
the retained manifest, assertions, exact identities, node health, and cleanup.
