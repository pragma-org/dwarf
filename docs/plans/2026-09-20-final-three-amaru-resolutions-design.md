# Final three Amaru resolutions design

Date: 2026-09-20
Status: approved

## Goal

Complete the three remaining Amaru legs in the frozen five-example program without changing accepted Card 03 evidence, weakening a security assertion, or replacing a proven object in place.

Child explanation: Keep every old test result. Add one fixed-node check, one chain that can run Plutus V2, and one correct way to understand short forks.

## Chosen approach

Use additive, versioned objects for all new behavior. Preserve the frozen `b159172f25a9c389f82f20bca4f15e3032791638` Card 01 run as a completed execution with a security finding and a failed assertion. Pin the Card 01 regression to exact Amaru revision `d3a6dafcced78f5809a96619e883cf04911d2bdc`. Add a measurement-specific Card 02 topology whose generated on-chain parameters contain Plutus V2. Replace the Card 04 raw-event monotonic rule with a versioned canonical-progress contract while retaining all raw chain-selection events.

The rejected alternatives are:

1. Mutate the frozen targets, scenarios, or old evidence. This would break reproducibility.
2. Convert the Card 01 assertion to a pass because the mismatch is expected. This would hide a real conformance finding.
3. Add only an off-chain Plutus V2 cost-model file. This would not let the chain validate Plutus V2 transactions.
4. Sort or discard Card 04 fork events. This would destroy security-relevant evidence.

## Card 01: completed finding and fixed-revision regression

DWARF will separate execution completion from the security verdict. A run can finish all required phases and have classification `completed_with_security_finding` while its assertion summary and scenario exit status still contain a failure. This classification is available only when the scenario declares an exact finding contract and the retained failed assertion, target revision, and finding identifier all match. An incomplete run or a different failed assertion cannot receive this classification.

The old Amaru run remains byte-for-byte unchanged. Reports link it to the existing byte-string-bound finding. The dashboard shows both facts together: execution complete; security finding present. SARIF and assertion semantics remain unchanged.

A new target family will pin source revision `d3a6dafcced78f5809a96619e883cf04911d2bdc`. It will have its own conformance adapter manifest, measurement patch manifest, executable digest, image digest, build logs, profile, and scenario. The new scenario will run the same 100-input corpus and the same live protocol cases. It must pass outcome parity, stable second encoding, target health, and chain progress. The old failure and new pass will link to one finding lifecycle record with explicit source revisions.

## Card 02: on-chain Plutus V2 topology

A new Amaru-only measurement profile will reuse the proven Cardano-producer and Amaru-consumer lifecycle. It will not modify an existing profile. Before any producer starts, its configurator will add a digest-pinned Plutus V2 cost model to every generated `alonzo-genesis.json`. It will also make a narrow Conway governance change: only the technical DRep threshold is zero, the committee is empty with a zero threshold and minimum size, and the constitution has no guardrail script. Other DRep thresholds, pool thresholds, and the real governance-action deposit stay unchanged. All producer Alonzo and Conway genesis files must remain identical after configuration.

The chain will start in Conway as the proven Amaru bootstrap requires. A one-shot service will submit a real protocol-parameter governance action for the pinned model. The service will retain the action, signed transaction, transaction hash, anchor identity, pre-update parameters, and post-enactment parameters. It will complete only after the on-chain model is active. The Amaru consumer seed cannot start until this service completes successfully.

The deployment gate will retain and verify:

- each generated Alonzo and Conway genesis digest;
- the exact cost-model source and digest;
- the governance action, signed update transaction, and transaction hash;
- live queried protocol parameters and their digest;
- proof that live `costModels.PlutusV2` equals the pinned model;
- Cardano and Amaru revisions and image digests;
- the normal producer/consumer readiness gates.

The workload will submit exactly 30 valid and 30 expected-invalid Plutus V2 transactions. Evidence will contain transaction body and signed-transaction hashes, transaction IDs, submission results, inclusion or execution outcomes, exposed budgets, target health, and post-workload chain progress. Missing on-chain V2 parameters, no inclusion, wrong IsValid outcome, identity mismatch, or lost progress fails closed.

## Card 04: raw selection and canonical progress

The new proof has two lossless layers.

`raw_chain_selection` retains every adopted block observation, same-height hash switch, rollback or fork event, application sample, excluded sample, and timestamp. It is never sorted or deduplicated for verdict purposes.

`canonical_progress` derives the final selected path and records its start, end, height delta, identities, convergence point, oscillation episodes, and correlation coverage. A same-height hash switch is an observed fork transition, not an automatic failure.

The canonical contract passes only when:

- the canonical path advances by at least 30 blocks in the bounded window;
- the final selected path converges before the recovery deadline;
- oscillation is bounded by explicit maximum episode and transition counts;
- required application samples correlate to retained raw block identities;
- the target has no panic, fatal exit, OOM, or unexpected restart.

It fails on no progress, non-convergence, continuing or excessive oscillation, missing correlations, or a fatal health signal. The first implementation uses conservative scenario parameters that are stated in the contract and retained in evidence. Both Amaru and Cardano-node will run the same contract in separate implementation-specific executions.

## Data flow

```text
exact scenario + exact profile
        |
        v
version-pinned deployment -----> retained identity and topology proof
        |
        v
real workload / chain window --> lossless raw evidence
        |
        v
assertions + finding metadata --> manifest and measurement report
        |
        v
dashboard inspection + export/import verification
```

## Compatibility and migration

All schema changes are additive. Old manifests, scenarios, profiles, runs, and bundles remain valid. Old Card 04 runs keep the v1 strict-monotonic verdict. New Card 04 scenarios use canonical-progress-v2 identifiers. Card 03 remains on whole-microseconds-v1. New Card 01, Card 02, and Card 04 measurement objects use nanoseconds-v2 and preserve integer `elapsed_nanos`, legacy integer microseconds, and fractional human-facing microseconds.

## Error handling

Every new identity or evidence field fails closed when required by its scenario. Build scripts reject a revision mismatch, manifest digest mismatch, dirty source, or missing output. The Plutus topology rejects different genesis bodies, a live parameter mismatch, or an absent V2 model. Canonical derivation rejects malformed identities, events outside the bounded window, ambiguous final selection, missing correlations, or unbounded oscillation. Finding classification never changes the assertion result and is unavailable if the run did not complete.

## Test strategy

Each behavior follows red-green-refactor:

- run classification tests cover a completed finding, ordinary failure, incomplete execution, wrong assertion, and legacy manifests;
- Card 01 tests cover exact revision locks, manifest digests, same-corpus identity, old/new linkage, and full expected outcome parity;
- Card 02 tests cover configurator ordering, exact genesis mutation, live V2 equality, digest retention, transaction identities, valid/invalid inclusion, budgets, and progress;
- Card 04 tests cover straight progress, one bounded same-height switch, rollback and recovery, excessive oscillation, non-convergence, missing correlations, no progress, and fatal health;
- schema, scenario, report, dashboard, export/import, full regression, and browser tests cover integration.

Real evidence is accepted only after exact-target verification, all required collectors finalize, bundle verification succeeds, dashboard routes export successfully, and desktop and mobile renders pass.

## Card 02 accepted implementation record

Run `20260921T013953Z-565b77c3` proves the chosen additive design. Conway governance update transaction `f2a39979f67f1853abfe59cbe1c92e8e015ea997bc09c8beb69c45dd7183c84e` activated the pinned 175-entry Plutus V2 model in epoch 2. The live workload retained 30 included-valid and 30 included-invalid transactions, 60 unique attempt identities, 60 unique lock transaction identities, and 60 unique spend transaction identities. Amaru advanced from block 335 to block 662 with no restart, OOM, fatal signal, or unexpected exit.

The run verifier returned `OK`. Bundle-local verification passed across 575 files, and archive import recomputed the signed manifest digest `436232f557906fbb58661b528db1ac6d37c73ac21506e7daef9dce9ad2956927`. The accepted bundle SHA-256 is `bf5604de608889cabc4ea30242a2236aaccf12cd06c07999c14ee26a8a3c7ed0`.

## Delivery boundaries

Commit and review each card as a separate batch. Before every internal push, verify the exact internal origin, run the relevant tests, inspect the full commit and diff, and exclude `dwarf/state/chain-head.json`, bundle archives, temporary files, caches, build outputs, and secrets. Never force-push. Do not push publicly. Do not run Antithesis or Moog. Gate 6 remains deferred.
