# GitHub Campaign Fan-Out Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Execute each frozen DWARF campaign workload in an isolated GitHub-hosted matrix job and merge the retained shards into one publishable campaign result.

**Architecture:** Keep discovery and candidate compilation as single jobs, add a preparation artifact containing exact manifests and controller identity, calibrate stock and patched modes independently, then create a dynamic work-item matrix from the selected manifest. Each execution job runs one manifest-derived item and the aggregator verifies and merges all shards before the existing publication boundary.

**Tech Stack:** GitHub Actions YAML, Python 3.12, Docker/OCI archives, pytest, DWARF campaign evidence contracts.

---

### Task 1: Define immutable campaign work items

**Files:**
- Create: `dwarf/profile_manager/campaign_work_items.py`
- Test: `tests/test_campaign_work_items.py`

**Steps:**

1. Write failing tests for stable calibration, security, grouped measurement, and stock-replay work items.
2. Test that security-attached measurement recipes are not scheduled twice and missing runtimes remain represented as blockers.
3. Implement canonical work-item IDs, per-item digests, deterministic ordering, and matrix JSON generation.
4. Run `PYTHONPATH=dwarf python3 -m pytest -q tests/test_campaign_work_items.py` and expect all tests to pass.
5. Commit only the work-item module and its test.

### Task 2: Execute exactly one work item

**Files:**
- Modify: `dwarf/scripts/ci_security_campaign.py`
- Modify: `dwarf/scripts/ci_hosted_campaign_runtime.py`
- Test: `tests/test_ci_security_campaign.py`
- Test: `tests/test_ci_hosted_campaign_runtime.py`

**Steps:**

1. Write failing tests proving a requested work item runs only its security entry or grouped measurement recipe.
2. Add explicit calibration and stock-replay item handling while retaining the existing control-plus-scenario atomic pair.
3. Make the shard command return success according to its assigned item, without claiming the full campaign is complete.
4. Require the work-item document and digest at both the outer and inner controller boundaries.
5. Run the two focused test files and expect all tests to pass.
6. Commit the runtime changes and tests.

### Task 3: Prepare and select the fan-out matrix

**Files:**
- Create: `dwarf/scripts/ci_campaign_fanout.py`
- Modify: `dwarf/scripts/ci_hosted_campaign_plan.py`
- Test: `tests/test_ci_campaign_fanout.py`
- Test: `tests/test_ci_hosted_campaign_plan.py`

**Steps:**

1. Write failing tests for preparation matrix output, calibration selection, patched fallback, and stock-calibration blocking.
2. Emit calibration work items with the frozen plan.
3. Verify retained calibration shard identities and select the primary manifest.
4. Emit one execution matrix item per unique selected workload plus required stock replays.
5. Run the focused planner/fan-out tests and expect all tests to pass.
6. Commit the planner and selector changes.

### Task 4: Verify and merge shard evidence

**Files:**
- Modify: `dwarf/scripts/ci_campaign_fanout.py`
- Test: `tests/test_ci_campaign_fanout.py`

**Steps:**

1. Write failing tests for a complete merge and rejection of missing, duplicate, conflicting, altered, or wrong-campaign shards.
2. Implement archive-path rebasing after Actions artifact download.
3. Recompute file hashes, checkpoint digests, full evidence evaluation, and aggregate status.
4. Write the aggregate runtime layout expected by `ci_publish_campaign.py`.
5. Run `PYTHONPATH=dwarf python3 -m pytest -q tests/test_ci_campaign_fanout.py` and expect all tests to pass.
6. Commit the merger and tests.

### Task 5: Replace the serial Actions campaign job

**Files:**
- Modify: `.github/workflows/dwarf-security-campaign.yml`
- Modify: `tests/test_security_campaign_workflow.py`
- Modify: `dwarf/docs/security-campaign-automation.md`

**Steps:**

1. Update workflow-contract tests for prepare, calibration matrix, selection, execution matrix, aggregation, and publication dependencies.
2. Add `fail-fast: false`, `max-parallel: 6`, per-item artifact names, and bounded job timeouts.
3. Build and save the trusted controller once in prepare; load and validate it in each runner.
4. Make aggregation run after every matrix result and make publication require successful aggregation.
5. Update the operator document with the new job layout and failure semantics.
6. Run `PYTHONPATH=dwarf python3 -m pytest -q tests/test_security_campaign_workflow.py` and expect all tests to pass.
7. Commit the workflow, contract tests, and documentation.

### Task 6: Focused integration verification

**Files:**
- Verify only; no expected source changes.

**Steps:**

1. Run the five focused test files changed by this implementation.
2. Run `git diff --check`.
3. Inspect the workflow as parsed YAML and confirm the candidate jobs have read-only repository permissions and publication is the only write-capable job.
4. Review the final diff for accidental candidate-command execution, mutable image authority, version restrictions, or unrelated files.
5. Do not launch a GitHub Actions run until the user separately authorizes it.
