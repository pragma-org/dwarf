# Latest Amaru Security Regressions Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Bind inline security scenarios to the selected local Amaru build, run the first decisive Amaru-only crash regression, and retain real-node results.

**Architecture:** Extend the existing scenario-substrate resolver and launch materializer with a distinct operator-local config identity shape. Preserve historical finding scenarios and add a resolved-plan, fresh-funded regression workload.

**Tech Stack:** Python 3, pytest, JSON-shaped YAML scenarios, Docker Compose, Playwright dashboard workflow.

---

### Task 1: Reproduce the scenario-substrate identity bug

**Files:**
- Modify: `tests/test_mixed_scenario_resolved_versions.py`

1. Add a test registration with the exact local-config identity fields.
2. Resolve an inline `resolved-plan` scenario with `operator_registration_id`.
3. Assert that the selected Amaru binding uses the registration rather than the catalog default.
4. Run the focused test and verify that it fails on the old catalog Amaru identity.

### Task 2: Bind local registrations without digest conflation

**Files:**
- Modify: `dwarf/profile_manager/run_plan.py`
- Modify: `dwarf/profile_manager/launch_store.py`
- Modify: `dwarf/scripts/runtime_substrate_common.py`
- Test: `tests/test_mixed_scenario_resolved_versions.py`
- Test: `tests/test_run_plan.py`

1. Pass the selected operator registration into inline substrate preview resolution.
2. Emit one of two exact binding shapes: registry manifest or operator-local config.
3. Materialize and normalize the local identity without writing the config ID into a manifest-digest field.
4. Verify the failing test passes and existing resolved-pair tests remain green.
5. Commit the resolver repair.

### Task 3: Add the executable latest-release crash regression

**Files:**
- Create: `dwarf/scenarios/ledger-submit-value-coin-i64-overflow-regression-latest-amaru.yaml`
- Modify: `dwarf/profile_manager/submit_primitives.py`
- Modify: submit primitive schemas
- Test: `tests/test_submit_security_regressions.py`

1. Treat a marked Cardano producer as supporting infrastructure so the target resolution remains Amaru-only.
2. Add a fresh-funded workload that constructs three independently signed transactions from generated-genesis credentials.
3. Require a valid included control, then the `i64::MAX` value-conservation rejection and liveness gate before submitting the `i64::MAX + 1` mutant.
4. Use a fixed high fee, fail closed on non-value diagnostics, and resubmit the boundary after the mutant to prove the mempool still responds.
5. Retain exact inputs, body/payload hashes, responses, and independent container health.
6. Validate the scenario semantically and commit it.

### Task 4: Verify and deploy the DWARF change

**Files:**
- No additional source files.

1. Run the focused resolver, launch-store, scenario, and browser contract tests.
2. Verify a clean tracked tree and exact commit.
3. Build clean framework/controller images on `cardano-box` from the committed archive.
4. Confirm no active controller execution, replace only the two DWARF app containers, and verify health/revision labels.

### Task 5: Execute the first decisive case

**Files:**
- Retained runtime evidence under the DWARF run store.

1. Resolve and inspect the value-overflow scenario with the exact local build and catalog supporting producer.
2. Prepare and run through the normal dashboard flow.
3. Verify the boundary control result before interpreting the mutant.
4. Verify exact target identity, assertion results, independent node health, and owned-resource cleanup.
5. Report the canonical run URL and distinguish framework failure, regression pass, or security finding.
