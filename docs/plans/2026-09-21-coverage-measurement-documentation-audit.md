# Coverage and Measurement Documentation Audit Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development for each behavior change and superpowers:verification-before-completion before each push.

**Goal:** Correct DWARF coverage and measurement documentation for the original five frozen cards, additive Card 06, and completed Card 02 workload accounting.

**Architecture:** Continue to derive scenario and coverage totals from the existing catalogs. Generalize the existing evidence ledger name, add only contract-backed evidence-rule joins, and update the current Learn templates and README without adding another coverage model.

**Tech Stack:** Python, YAML/JSON Schema, Jinja, vanilla JavaScript/CSS, pytest, Docker, Playwright/Chromium.

---

### Task 1: Freeze the stale behavior as failing tests

**Files:**
- Modify: `tests/test_measurement_coverage_learn.py`
- Modify: `tests/test_dashboard_measurement_discovery_repair.py`
- Modify: `tests/test_public_readme_contract.py`

1. Add assertions for `client_card_evidence`, cards 01–06, Card 02 External accounting joins, exact Plutus totals, additive Card 06 wording, derived 268-scenario totals, relative/public links, and zero private references.
2. Run the focused tests and confirm that failures identify the stale naming, missing Card 02 mapping, and missing README text.

### Task 2: Generalize the evidence ledger and page copy

**Files:**
- Modify: `dwarf/profile_manager/data/client_example_evidence.py`
- Modify: `dwarf/profile_manager/views/coverage.py`
- Modify: `dwarf/profile_manager/views/threat_coverage.py`
- Modify: `dwarf/profile_manager/data/measurement_coverage.py`
- Modify: `dwarf/dashboard/templates/learn/coverage.j2`
- Modify: `dwarf/dashboard/templates/learn/measurement_coverage.j2`
- Modify: `dwarf/dashboard/templates/learn/landing.j2`
- Modify: `dwarf/profile_manager/data/threat_risk_coverage.html`

1. Rename the general data flow and visible wording to client-card evidence.
2. Preserve five-frozen-card language only where it names the original program.
3. Run focused tests and confirm green.

### Task 3: Join the fresh Card 02 evidence

**Files:**
- Modify: `dwarf/measurement-coverage/v1.yaml`
- Modify: `dwarf/dashboard/templates/learn/measurements.j2`
- Test: `tests/test_measurement_coverage_learn.py`

1. Add the two contract-backed Card 02 evidence rules.
2. Assert both 60-attempt run identities and only supported tap mappings.
3. Confirm RR-031 and TM-036 receive grouped, non-duplicated retained evidence.

### Task 4: Update concise public documentation

**Files:**
- Modify: `README.md`
- Modify: `dwarf/docs/client-examples/README.md` only if the audit finds a missing boundary
- Modify: applicable Card 02 status/contract text only if needed
- Test: `tests/test_public_readme_contract.py`

1. Describe the original five frozen cards and separate additive Card 06.
2. State the two separate Plutus accounting totals and non-comparison boundary.
3. Link the measurement and coverage routes using deployment-relative paths or public repository paths.

### Task 5: Verify and deliver internally

1. Run focused tests, full tests, scenario/schema validation, and offline profile rendering.
2. Render and inspect all affected pages at desktop and mobile sizes. Exercise search, filters, view switching, disclosures, links, empty states, console checks, and overflow checks.
3. Audit the exact diff and exclude runtime state, bundles, caches, secrets, and unrelated files.
4. Commit and push to internal V7-PRAGMA `main` without force.
5. Build and deploy the exact commit. Verify the live routes and image/source identities.

### Task 6: Reconcile and deliver publicly

1. Create a separate clean clone from the latest public `main`; do not use a worktree.
2. Apply only the reviewed public-safe source, documentation, and test changes while preserving public-only behavior.
3. Run the full suite, scenario validation, public safety audit, and browser checks on the exact candidate.
4. Re-check the remote public head. Push normally only if it has not moved.
5. Verify the remote commit and public README contents.
