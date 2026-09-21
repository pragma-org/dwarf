# Final Three Amaru Resolutions Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Complete the remaining Card 01, Card 02, and Card 04 Amaru requirements with additive versioned targets, topology, evidence, reporting, and retained real-node runs.

**Architecture:** Preserve every v1 object and accepted run. Add an orthogonal completion/finding classification, an exact fixed-revision Card 01 target, an on-chain Plutus V2 Amaru topology, and canonical-progress-v2 chain evidence. Keep assertion verdicts authoritative and raw evidence lossless.

**Tech Stack:** Python 3, pytest, JSON Schema, YAML/JSON scenarios and profiles, Rust/Cargo Amaru adapters and measurement patches, Docker Compose, Cardano CLI, Jinja dashboard templates, Playwright/Chromium verification.

---

### Task 1: Record the approved contracts

**Files:**
- Modify: `dwarf/docs/client-examples/contracts/01-cbor-decoding.yaml`
- Modify: `dwarf/docs/client-examples/contracts/02-plutus-vm.yaml`
- Modify: `dwarf/docs/client-examples/contracts/04-block-application.yaml`
- Modify: `dwarf/docs/client-examples/findings/01-frozen-amaru-cbor-byte-string-bound.md`
- Modify: `dwarf/docs/client-examples/findings/02-frozen-amaru-no-plutus-v2-cost-model.md`
- Modify: `dwarf/docs/client-examples/findings/03-frozen-amaru-block-application-forks.md`
- Test: `tests/test_client_example_acceptance_cards.py`

1. Add failing tests for additive revisions, exact `d3a6dafc` identity, finding semantics, on-chain V2 identity, and canonical-progress-v2 requirements.
2. Run the focused tests and confirm they fail for the missing contract fields.
3. Add the minimum contract and finding updates. Preserve historical targets, runs, and verdicts.
4. Run the focused tests and scenario validator.
5. Review and commit the contract batch.

### Task 2: Classify a completed execution with a security finding

**Files:**
- Modify: `dwarf/spec/v1/scenario.schema.json`
- Modify: `dwarf/profile_manager/scenario.py`
- Modify: `dwarf/profile_manager/forensic.py`
- Modify: `dwarf/profile_manager/data/operate_run.py`
- Modify: `dwarf/dashboard/templates/operate/run.j2`
- Modify: `dwarf/dashboard/templates/learn/measurements.j2`
- Test: `tests/test_scenario.py`
- Test: `tests/test_evidence_defaults.py`
- Test: `tests/test_measurement_frontend.py`

1. Add failing tests that require `completed_with_security_finding` only for a completed run whose declared finding ID, exact target revision, and failed assertion match.
2. Confirm ordinary failures, incomplete runs, wrong assertions, and legacy bundles do not receive the classification.
3. Add the scenario declaration and derived manifest/report classification without changing `exit_status`, assertion summaries, or SARIF mapping.
4. Add dashboard presentation that displays both execution completion and the failed security assertion.
5. Run focused backend and template tests, review the diff, and commit.

### Task 3: Build the exact Card 01 fixed-revision regression target

**Files:**
- Create: `dwarf/targets/amaru/conformance-adapters/d3a6dafcced78f5809a96619e883cf04911d2bdc/manifest.json`
- Create: `dwarf/targets/amaru/conformance-adapters/d3a6dafcced78f5809a96619e883cf04911d2bdc/cbor/Cargo.toml`
- Create: `dwarf/targets/amaru/conformance-adapters/d3a6dafcced78f5809a96619e883cf04911d2bdc/cbor/src/main.rs`
- Create: `dwarf/targets/amaru/measurement-patches-nanoseconds-v2/d3a6dafcced78f5809a96619e883cf04911d2bdc/manifest.json`
- Create: `dwarf/targets/amaru/measurement-patches-nanoseconds-v2/d3a6dafcced78f5809a96619e883cf04911d2bdc/0001-dwarf-measurement-nanoseconds-v2.patch`
- Modify: `dwarf/scripts/build_amaru_conformance_adapter.py`
- Modify: `dwarf/scripts/build_amaru_measurement_target.py`
- Test: `tests/test_amaru_conformance_adapter.py`
- Test: `tests/test_amaru_measurement_patch.py`

1. Add failing tests for manifest-selected exact revisions and fail-closed digest/application behavior.
2. Confirm the tests fail because the builders are fixed to `b159172f`.
3. Make the builders accept only the revision declared by a verified manifest.
4. Port the nanosecond instrumentation to `d3a6dafc` without importing the byte-string fix as a DWARF patch.
5. Build from exact clean source and retain toolchain, adapter-set, patch-set, executable, image, build-log, and build-result digests.
6. Run the same 100-input corpus directly against the new adapter and confirm 100 expected outcomes plus stable second encoding.
7. Review and commit the target batch.

### Task 4: Add and run the Card 01 regression scenario

**Files:**
- Create: `dwarf/profiles/profile-x-amaru-cbor-fix-regression-nanoseconds-v2/profile.yaml`
- Create: `dwarf/scenarios/client-example-cbor-decoding-amaru-d3a6dafc-regression.yaml`
- Modify: `dwarf/scripts/runtime_version_pinned_cbor_conformance.py`
- Test: `tests/test_version_pinned_cbor_conformance.py`
- Test: `tests/test_version_pinned_cbor_primitives.py`
- Test: `tests/test_profile_version_resolution.py`

1. Add failing tests for the exact regression target, unchanged corpus digest, old/new finding links, and all four Card 01 assertions.
2. Add the profile, scenario, and manifest-selected runtime adapter resolution.
3. Run focused tests and all scenario validation.
4. Deploy the exact target and run the complete regression scenario through DWARF.
5. Verify the run, raw evidence, report, route, and bundle export/import. Retain all digests.
6. Review and commit the accepted Card 01 batch.

### Task 5: Add the on-chain Plutus V2 Amaru topology

Status: implementation and evidence complete in run `20260921T013953Z-565b77c3`; final route inspection remains part of Task 8.

**Files:**
- Create: `dwarf/profiles/profile-w-amaru-measurement-plutus-v2/profile.yaml`
- Modify: `dwarf/scripts/runtime_amaru_control_substrate.py`
- Modify: `dwarf/scripts/runtime_controlled_plutus_transactions.py`
- Create: `dwarf/scenarios/client-example-plutus-vm-amaru-onchain-v2.yaml`
- Test: `tests/test_runtime_amaru_control_substrate.py`
- Test: `tests/test_controlled_plutus_transactions.py`
- Test: `tests/test_profile_version_resolution.py`

1. Add failing tests for configurator-before-producer ordering, exact V2 genesis insertion, narrowly scoped Conway governance, identical generated genesis, live parameter equality, and digest retention.
2. Add failing workload tests for transaction hashes and IDs, 30 valid inclusions, 30 expected-invalid inclusions, budgets where exposed, health, and progress.
3. Add the additive profile, a one-shot on-chain protocol-parameter action, and a fail-closed seed dependency. Keep the proven all-Conway Amaru bootstrap path.
4. Retain generated Alonzo and Conway genesis, the cost-model source, governance action, signed update transaction, update transaction hash, live protocol parameters, revisions, images, and hashes in deployment evidence.
5. Run focused tests and all scenario validation.
6. Deploy the new topology, prove live V2 parameters, and run the exact Card 02 scenario.
7. Verify the run, report, raw evidence, route, and bundle export/import. Review and commit.

Completion evidence: all three security assertions passed; the 60-transaction live workload split into 30 included-valid and 30 included-invalid outcomes; the exact Amaru target advanced from block 335 to block 662 without restart, OOM, fatal signal, or unexpected exit. `cardano-profile verify` returned `OK`. Bundle-local verification and archive import matched the signed manifest across 575 files. Manifest SHA-256: `436232f557906fbb58661b528db1ac6d37c73ac21506e7daef9dce9ad2956927`. Bundle SHA-256: `bf5604de608889cabc4ea30242a2236aaccf12cd06c07999c14ee26a8a3c7ed0`.

### Task 6: Implement canonical-progress-v2

**Files:**
- Modify: `dwarf/profile_manager/primitives.py`
- Create: `dwarf/primitives/assertion/canonical_chain_progress_complete.schema.json`
- Create: `dwarf/scenarios/client-example-block-application-amaru-canonical-v2.yaml`
- Create: `dwarf/scenarios/client-example-block-application-cardano-canonical-v2.yaml`
- Modify: `dwarf/primitives/registry.json`
- Modify: `dwarf/docs/primitives-reference.md`
- Modify: `dwarf/docs/primitives-reference.html`
- Test: `tests/test_client_example_progress_restart.py`

1. Add one failing test each for straight progress, a bounded same-height switch, rollback recovery, excessive oscillation, continuing oscillation, non-convergence, no progress, missing correlation, and fatal health.
2. Confirm each test fails for the intended missing canonical behavior.
3. Add lossless raw event retention and the minimum canonical-path derivation.
4. Add explicit bounded-progress, convergence, oscillation, correlation, and health checks.
5. Register the assertion and additive scenarios; retain old v1 scenarios unchanged.
6. Run focused tests and all scenario validation. Review and commit.

### Task 7: Run both Card 04 implementations

Precision amendment: the Cardano canonical-v2 run is retained on nanoseconds-v2. The first Amaru canonical-v2 run is a retained canonical-progress rehearsal only because it exposed that the stock `block.apply` span lacked monotonic nanoseconds. Add the revision-locked nanoseconds-v3 Amaru target, profile, scenario, collector preference, exact conversion tests, and artifact digests. Then rerun only the Amaru leg and require raw `elapsed_nanos` in every accepted application sample before promotion.

**Files:**
- Modify: `dwarf/docs/client-examples/04-BLOCK-APPLICATION-STATUS.md`
- Modify: `dwarf/docs/client-examples/04-CARDANO-BLOCK-APPLICATION-PROOF.md`
- Modify: `dwarf/docs/client-examples/findings/03-frozen-amaru-block-application-forks.md`

1. Run the Cardano canonical-v2 scenario and require all assertions and evidence gates to pass.
2. Run the Amaru canonical-v2 scenario and require bounded fork evidence, convergence, progress, correlations, and health to pass.
3. Verify both runs and export/import both bundles.
4. Record exact identities, counts, distributions, raw event digests, reports, and bundle digests.
5. Review and commit the Card 04 evidence batch.

### Task 8: Final integration, dashboard, and delivery

**Files:**
- Modify: `dwarf/docs/client-examples/README.md`
- Modify: `dwarf/docs/client-examples/01-CARDANO-CBOR-DECODING-PROOF.md`
- Modify: `dwarf/docs/client-examples/02-PLUTUS-VM-STATUS.md`
- Modify: `dwarf/docs/client-examples/04-BLOCK-APPLICATION-STATUS.md`
- Modify: relevant `/learn` documentation and workbench status artifacts

1. Run the full pytest suite and require zero failures.
2. Validate every scenario and require zero schema or semantic failures.
3. Rebuild and deploy the dashboard from the reviewed source commit.
4. Verify health, scenario, profile, retained-run, and bundle routes.
5. Render every new and final route at desktop and mobile sizes; reject overflow, missing identity, browser errors, broken assets, or inaccessible controls.
6. Perform a requirement-by-requirement completion audit against the exact active goal.
7. Update the persistent workbench runbook and status object with read-back digest verification.
8. Before each push, confirm the internal origin, tests, exact commit and diff, exclusions, and clean intended scope. Push without force to internal `V7-PRAGMA` only.
9. Confirm no public push, Antithesis run, Moog run, or Gate 6 work occurred.
