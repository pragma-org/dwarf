# Dedicated simple-transfer measurement card design

## Scope

Add one client-example card with two separate real-node legs: Amaru and Cardano-node. Each leg submits a frozen set of simple signed payment transactions and retains every attempted outcome. The change reuses the existing scenario, primitive, measurement-profile, collector, report, evidence, and Learn systems.

The Amaru leg measures only Amaru at revision `d3a6dafcced78f5809a96619e883cf04911d2bdc`. Its qualified, digest-pinned Cardano producer supplies signed transactions through the existing supported topology. It is delivery infrastructure, not a second measured target and not a comparison. The Cardano-node leg measures Cardano-node 11.1.2. Both use the existing `nanoseconds-v2` patched targets; no node patch is added.

## Workload and evidence

Use one new `runtime_controlled_simple_transfers` primitive and script. The frozen corpus contains at least 30 attempts per leg, with successful simple payments and deliberate expected rejections that do not change the payment-transfer purpose. Each attempt records its ID, transaction ID when available, signed transaction byte count, start/end timestamps, elapsed nanoseconds and fractional microseconds, terminal outcome, response evidence, mempool evidence when observable, and inclusion/adoption evidence when observable.

The primitive emits an existing `workload_accounting` event with totals and one row for every attempt. The current external accounting collector remains authoritative. Extend it additively for explicit timeout counts and offered-byte totals if the new red tests show those fields are missing. Missing protocol stages stay unavailable with a reason; they never become zero.

Each script result also retains exact target identity, scenario/workload identity, seed, dataset digest, runtime timestamps, target health, chain progress, and logs. The normal DWARF run directory and export bundle retain these artifacts.

## Additive product integration

Add two version-pinned scenarios, one acceptance contract, one primitive schema/registry entry, and a frozen workload manifest. Add the scenarios to existing catalog compatibility and defaults only where the current derived catalog expects explicit metadata. Add evidence rules and Learn links through the existing measurement-coverage mapping; do not create a second coverage table or result UI.

The report uses existing measurement result components. Scenario metadata and retained output identify this as the simple-transfer card. Measurement thresholds remain non-gating by default. The card explicitly does not claim an automatic Amaru-versus-Cardano benchmark.

## Verification

Write contract, primitive, collector, scenario, and presentation tests first and confirm that they fail. Then implement the smallest passing change. Run focused and full relevant tests, scenario/schema validation, profile rendering, and browser checks. Run both scenarios through DWARF, require at least 30 correlated attempts and non-vacuous real-target evidence in each retained run, export and inspect each bundle, then review, commit, push internal main, deploy, and verify live.

## Stop conditions

Stop if the real transaction path needs a new node patch, if the Amaru topology cannot prove delivery to Amaru, or if either leg cannot retain at least 30 real attempts with correlated outcomes. Do not substitute static configuration or container startup for runtime proof.
