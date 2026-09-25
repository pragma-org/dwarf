# Measurement Report Redesign Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the run measurement table with a responsive, shared-concept report and a separate exact raw-data view.

**Architecture:** A versioned JSON presentation catalog binds canonical client concepts to implementation-specific retained metric IDs. A Python presentation layer produces tested card groups and raw rows; Jinja renders the prepared model without interpreting measurement semantics.

**Tech Stack:** Python, Jinja2, JSON, vanilla JavaScript, DWARF forensic-noir CSS, pytest, Playwright browser verification.

---

### Task 1: Specify the presentation contract

**Files:**
- Create: `dwarf/measurements/presentation-v1.json`
- Create: `dwarf/profile_manager/measurement_presentation.py`
- Test: `tests/test_measurement_presentation.py`

1. Write failing tests for catalog validation, shared Amaru/Cardano titles, source badges, reserved bindings, and unknown-metric fallback.
2. Run `pytest -q tests/test_measurement_presentation.py` and confirm the tests fail because the module and catalog do not exist.
3. Add the smallest loader and canonical concept catalog needed to pass.
4. Re-run the focused tests and confirm they pass.

### Task 2: Build type-aware cards

**Files:**
- Modify: `dwarf/profile_manager/measurement_presentation.py`
- Test: `tests/test_measurement_presentation.py`

1. Write failing tests for distribution, scalar/rate, count, unavailable, and reserved cards.
2. Cover median-first output, scalar derivation, event-count output, no false `0 samples`, unavailable reasons, and evidence labels at `n=2`, `n=4`, `n=29`, and `n=30`.
3. Implement type selection, primary-value formatting data, evidence labels, source details, companion-outcome merging, category grouping, and raw rows.
4. Run the focused tests until green.

### Task 3: Integrate the run data model

**Files:**
- Modify: `dwarf/profile_manager/data/operate_run.py`
- Modify: `tests/test_measurement_frontend.py`

1. Write failing tests proving the run section exposes five distinct summary facts, grouped cards, the target identity, and a raw-view URL.
2. Integrate the presentation builder without changing retained reports or report generation.
3. Verify both existing frontend data tests and the new contract tests pass.

### Task 4: Render the concept report

**Files:**
- Modify: `dwarf/dashboard/templates/operate/run.j2`
- Create: `dwarf/dashboard/templates/operate/run_measurements_raw.j2`
- Create: `dwarf/dashboard/templates/operate/_partials/_measurement_report.j2`
- Create: `dwarf/dashboard/static/js/measurement-report.js`
- Modify: `dwarf/dashboard/static/css/base.css`
- Modify: `tests/test_measurement_frontend.py`

1. Write failing rendered-HTML tests for the claim boundary, glossary, source badges, category controls, cards, native disclosures, raw-view link, and unchanged downloads.
2. Replace the wide default table with the prepared concept-card partial.
3. Add responsive two-column/one-column styles and the external search/category controller.
4. Keep unavailable cards visible and provide explicit empty-filter feedback.
5. Run focused frontend tests until green.

### Task 5: Add the raw operator route

**Files:**
- Modify: `dwarf/profile_manager/views/operate_run.py`
- Modify: `dwarf/profile_manager/dashboard.py`
- Create: `dwarf/dashboard/templates/operate/run_measurements_raw.j2`
- Modify: `tests/test_measurement_frontend.py`

1. Write failing route tests for `/operate/runs/<id>/measurements/raw`, not-found behavior, back navigation, exact raw IDs, and report downloads.
2. Add the renderer and route before the general run-detail route.
3. Run the route tests and confirm they pass.

### Task 6: Verify regressions and presentation

**Files:**
- Test: `tests/test_measurement_presentation.py`
- Test: `tests/test_measurement_frontend.py`

1. Run focused tests.
2. Run the complete pytest suite.
3. Run `git diff --check` and repository hygiene/secret checks.
4. Commit only source, tests, and design documentation; exclude `dwarf/state/chain-head.json`.
5. Build and deploy the exact commit to `cardano-box`.
6. Use Playwright against both acceptance runs at desktop, tablet, and mobile widths.
7. Verify search, each category control, disclosures, raw route, downloads, and zero page-level horizontal overflow.
8. Verify the deployed container revision equals the committed revision, then push only to the internal `V7-PRAGMA` origin.

