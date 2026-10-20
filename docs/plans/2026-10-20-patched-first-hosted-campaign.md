# Patched-First Hosted Campaign Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the GitHub-hosted candidate campaign prefer a compatible patched build, fall back cleanly to stock, and bind security authority to stock replay.

**Architecture:** Extend the existing trusted candidate-build and hosted-plan contracts instead of adding a second scheduler. Build receipts describe stock and patched artifacts separately; the planner records a primary-mode decision; the runtime resolves a mode for each node; the campaign checkpoint links patched observations to stock replays. Every transition fails closed and retains its reason.

**Tech Stack:** Python 3.12, GitHub Actions YAML, Docker/Buildx, JSON Schema, pytest.

**Implementation checkpoint (2026-10-20):** Tasks 1-8 are implemented in the
current integration worktree. The planner retains separate stock and patched
manifests, calibration selects one lane without rebuilding, runtime resolution
uses a campaign-owned exact patched target, and patched security classifications
receive a security-only stock replay. Publication requires complete retained
input-digest equality and records confirmed or disagreeing authority. No hosted
smoke or full campaign has been dispatched from this checkpoint.

---

### Task 1: Freeze the patched-first policy contract

**Files:**
- Modify: `dwarf/spec/v1/security-campaign.schema.json`
- Modify: `dwarf/profile_manager/security_campaign.py`
- Test: `tests/test_security_campaign.py`

**Steps:**

1. Add a failing test for a frozen execution policy containing primary mode,
   fallback mode, calibration state, and stock-replay requirement.
2. Run that test and confirm the existing manifest omits the policy.
3. Add the minimal schema and resolver fields with deterministic digest binding.
4. Run the focused resolver/schema tests.
5. Commit the contract.

### Task 2: Select patched or stock from observed build availability

**Files:**
- Modify: `dwarf/scripts/ci_hosted_campaign_plan.py`
- Test: `tests/test_ci_hosted_campaign_plan.py`

**Steps:**

1. Add failing tests for compatible patched selection, missing patched receipt,
   failed patched receipt, and explicit stock fallback reasons.
2. Confirm the current planner always strips patched cells and emits only stock.
3. Replace the stock-only filter with a deterministic primary-mode selector.
4. Preserve patched cells only when an exact patched build observation exists;
   preserve stock cells for fallback and replay.
5. Run the focused planner tests and commit.

### Task 3: Build and retain the compatible Amaru patched variant

**Files:**
- Modify: `dwarf/scripts/ci_amaru_stock_build.py`
- Modify: `.github/workflows/dwarf-security-campaign.yml`
- Test: `tests/test_ci_amaru_stock_build.py`

**Steps:**

1. Add failing tests showing one candidate snapshot produces distinct stock and
   patched receipts and that an incompatible patch records an unavailable lane
   without failing the stock build.
2. Confirm the current executor emits only `stock-0000`.
3. Reuse the immutable builder and frozen source snapshot to execute the trusted
   patched plan in a separate isolated copy. Apply only verified trusted patches.
4. Retain patch-set, executable, image, build-log, and source identities in the
   patched receipt and artifact archive.
5. Rename the workflow artifact and steps from stock-only to candidate variants.
6. Run the focused build-contract tests and commit.

### Task 4: Stage and register both runtime images

**Files:**
- Modify: `dwarf/scripts/ci_hosted_campaign_stage.py`
- Modify: `dwarf/scripts/ci_hosted_campaign_plan.py`
- Test: `tests/test_ci_hosted_campaign_stage.py`
- Test: `tests/test_ci_hosted_campaign_plan.py`

**Steps:**

1. Add failing transfer tests for two independent OCI archives and digests.
2. Extend staging to verify and load each successful lane independently.
3. Register stock and patched images under distinct immutable local-registry
   references.
4. Add both artifacts to the frozen catalog and build observations without
   allowing one mode to satisfy the other.
5. Run focused transfer/planner tests and commit.

### Task 5: Support per-node modes in mixed execution

**Files:**
- Modify: `dwarf/scripts/ci_security_campaign.py`
- Modify: `dwarf/profile_manager/campaign_runtime_evidence.py`
- Test: `tests/test_ci_security_campaign.py`
- Test: `tests/test_campaign_evidence.py`

**Steps:**

1. Replace the existing rejection test with failing tests for patched-candidate
   plus stock-reference resolution and evidence binding.
2. Introduce a per-node mode map derived only from frozen topology identities.
3. Validate each candidate node against the matching build observation and each
   release node against its exact catalog artifact.
4. Keep collectors and measurement cells bound to node ID plus mode.
5. Reject any launch or evidence row that changes a node's frozen mode.
6. Run focused mixed-runtime and evidence tests and commit.

### Task 6: Add the paired calibration decision

**Files:**
- Modify: `dwarf/scripts/ci_security_campaign.py`
- Modify: `dwarf/profile_manager/campaign_evidence.py`
- Test: `tests/test_ci_security_campaign.py`

**Steps:**

1. Add failing tests for calibration pass, execution failure, excessive overhead,
   and identity mismatch.
2. Schedule the existing stock and patched calibration scenarios before primary
   security cells when both candidate builds exist.
3. Evaluate the trusted thresholds and checkpoint one primary-mode decision.
4. On failure, select stock once, retain the reason, and do not retry the build.
5. Run focused calibration/scheduler tests and commit.

### Task 7: Bind security classifications to stock replay

**Files:**
- Modify: `dwarf/scripts/ci_security_campaign.py`
- Modify: `dwarf/profile_manager/campaign_evidence.py`
- Modify: `dwarf/profile_manager/campaign_publication.py`
- Test: `tests/test_ci_security_campaign.py`
- Test: `tests/test_campaign_evidence.py`
- Test: `tests/test_campaign_publication.py`

**Steps:**

1. Add failing tests proving a patched security result is provisional and an
   exact-input stock replay can confirm or disagree with it.
2. Retain the scenario/input digest needed for replay.
3. Schedule stock replay without rebuilding and link both run IDs.
4. Publish the explicit authority state; never collapse disagreement.
5. Run the focused evaluator/publication tests and commit.

Implemented detail: replay compares the complete same-boundary input-digest set
for every target node. It permits measurement attachment differences while
requiring the same security contract, scenario and execution recipe. The stock
phase skips measurement cells and reuses the staged stock artifact.

### Task 8: Integrate the hosted workflow without a long campaign

**Files:**
- Modify: `.github/workflows/dwarf-security-campaign.yml`
- Modify: `docs/operations/github-security-campaign.md`
- Test: `tests/test_ci_amaru_stock_build.py`
- Test: `tests/test_ci_security_campaign.py`

**Steps:**

1. Add a workflow contract test for variant artifacts, calibration-first order,
   and always-retained fallback evidence.
2. Wire the variant artifact through staging, planning, and campaign execution.
3. Run the focused workflow, planner, runtime, evidence, and publication tests.
4. Inspect the final diff for credentials, mutable image references, version
   allowlists, and accidental candidate-controlled commands.
5. Commit the integrated workflow.

### Task 9: Mirror and smoke-test

**Files:**
- Update the same committed files in the internal Gitea repository.

**Steps:**

1. Replay the public commits onto current internal `main` without force-pushing.
2. Run one short hosted smoke candidate that stops after build transfer,
   qualification, calibration, and one control scenario.
3. Review retained primary-mode, fallback, artifact, patch, and replay identities.
4. Only after that smoke passes, dispatch the full six-scenario campaign.
