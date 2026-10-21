# GitHub Stock-Solo Proof Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a manual GitHub Actions scope that proves the existing Amaru and Cardano stock measurement paths in order without running or claiming the incomplete full campaign.

**Architecture:** The existing candidate builder receives a stock-only switch. Campaign fan-out freezes a two-item scoped inventory and aggregation evaluates that inventory as a limited execution/evidence proof. The workflow transports the scope through discovery, gates Cardano on Amaru success, skips mixed execution and full publication, and retains the verified proof artifact.

**Tech Stack:** Python 3.12, pytest, GitHub Actions YAML, existing DWARF CLI/runtime/bundle pipeline.

---

### Task 1: Stock-only candidate build

**Files:**
- Modify: `dwarf/scripts/ci_amaru_stock_build.py`
- Test: `tests/test_ci_amaru_stock_build.py`

1. Add a failing test proving stock-only selection omits the patched builder.
2. Run the focused test and confirm the missing behavior fails.
3. Add a trusted `--stock-only` CLI switch and retain the selected variants in the summary.
4. Run the focused test and confirm it passes.

### Task 2: Scoped work-item selection and aggregation

**Files:**
- Modify: `dwarf/scripts/ci_campaign_fanout.py`
- Test: `tests/test_ci_campaign_fanout.py`

1. Add failing tests for a stock-solo inventory containing one Amaru and one Cardano measurement item, retained deferred blockers, and no mixed/security items.
2. Add a failing aggregation test proving two successful scoped shards return `scoped-complete` without a security claim.
3. Implement `--scope full|stock-solo`, exact scoped inventory validation, and scoped aggregation status.
4. Run the focused fan-out tests.

### Task 3: Workflow dispatch and ordering

**Files:**
- Modify: `.github/workflows/dwarf-security-campaign.yml`
- Test: `tests/test_security_campaign_workflow.py`

1. Add failing structural tests for the dispatch choice, stock-only build argument, selector scope, strict Amaru-to-Cardano gate, mixed skip, and publication skip.
2. Update the workflow and pass the validated scope through discovery.
3. Run workflow syntax and focused structural tests.

### Task 4: Documentation and verification

**Files:**
- Modify: `dwarf/docs/security-campaign-automation.md`

1. Document the exact stock-solo claim and exclusions.
2. Run the focused stock build, fan-out, hosted runtime, and workflow tests.
3. Parse workflow YAML and run `git diff --check`.
4. Commit and package only changed public files; mirror the commit to the internal worktree.
