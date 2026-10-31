# Latest Amaru Security Regressions Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Bind inline security scenarios to the selected local Amaru build, add four regression variants, deploy them, and retain real-node results.

**Architecture:** Extend the existing scenario-substrate resolver and launch materializer with a distinct operator-local config identity shape. Preserve historical finding scenarios and create resolved-plan regression variants that reuse their fixtures and real primitives.

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

### Task 3: Add executable latest-release regression scenarios

**Files:**
- Create: `dwarf/scenarios/ledger-submit-value-coin-overflow-regression-amaru-cardano-node.yaml`
- Create: `dwarf/scenarios/ledger-arithmetic-exunits-overflow-regression-amaru-cardano-node.yaml`
- Create: `dwarf/scenarios/ledger-submit-updatedrep-unregistered-regression-amaru-cardano-node.yaml`
- Preserve: `dwarf/scenarios/governance-zero-denominator-*.yaml`
- Test: `tests/test_latest_amaru_security_regressions.py`

1. Write semantic tests requiring resolved-plan identities, valid controls where supported, reject/parity expectations, health observation, and teardown.
2. Run the focused test and verify it fails because the variants do not exist.
3. Add the minimal scenario variants by reusing audited fixtures and primitives.
4. Validate each scenario semantically and run the focused test.
5. Commit the regression scenarios.

### Task 4: Verify and deploy the DWARF change

**Files:**
- No additional source files.

1. Run the focused resolver, launch-store, scenario, and browser contract tests.
2. Verify a clean tracked tree and exact commit.
3. Build clean framework/controller images on `cardano-box` from the committed archive.
4. Confirm no active controller execution, replace only the two DWARF app containers, and verify health/revision labels.

### Task 5: Execute the four-case batch

**Files:**
- Retained runtime evidence under the DWARF run store.

1. Resolve and inspect the nonzero governance control with the exact local build.
2. Prepare once, run the control, and inspect terminal evidence.
3. Run the zero-denominator malformed case only if its control is valid.
4. Run value overflow, ExUnits overflow, and unregistered-DRep regressions sequentially.
5. After each run, verify exact target identity, assertion results, independent node health, and owned-resource cleanup before proceeding.
6. Report canonical run URLs and distinguish framework failures, regression passes, and security findings.
