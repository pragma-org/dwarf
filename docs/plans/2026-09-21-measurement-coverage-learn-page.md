# Measurement Coverage Learn Page Implementation Plan

> **For Codex:** Use test-driven development and execute each task in order.

**Goal:** Add `/learn/measurement-coverage`, an evidence-aware join of current threats, risks, scenario families, node/protocol surfaces, measurement definitions, profiles, and five-card evidence.

**Architecture:** Keep the existing threat/risk and catalog sources authoritative. Add one versioned, schema-validated applicability map that connects derived scenario families and surfaces to measurement IDs, prerequisites, claim boundaries, and non-claims. Build render-time rows in a Python data module; keep Jinja presentation-only.

**Tech stack:** Python, YAML, JSON Schema, Jinja2, native HTML controls, vanilla JavaScript, pytest, Playwright visual audit.

---

### Task 1: Freeze route and data contracts

**Files:**
- Create: `tests/test_measurement_coverage_learn.py`
- Modify: `tests/test_dashboard_measurement_discovery_repair.py`

1. Add failing tests for the route, landing card, navigation, four views, required fields, evidence semantics, known gaps, relative links, native controls, child explanations, and public-safe text.
2. Add mapping integrity tests for current scenario, measurement, profile, threat, and risk identifiers.
3. Run the focused tests and confirm they fail because the route and mapping do not exist.

### Task 2: Add the versioned applicability source

**Files:**
- Create: `dwarf/spec/v1/measurement-coverage-map.schema.json`
- Create: `dwarf/measurement-coverage/v1.yaml`

1. Define schema-validated surface and scenario-family rules with provenance.
2. Map only existing measurement IDs.
3. Record prerequisites, proof boundaries, and explicit non-claims.
4. Preserve TM-030, TM-031, and RR-027 as evidence gaps.

### Task 3: Build the render-time join

**Files:**
- Create: `dwarf/profile_manager/data/measurement_coverage.py`
- Modify: `dwarf/profile_manager/views/threat_coverage.py`

1. Expose reconciled current threat/risk data as a reusable data function.
2. Load and validate the map, measurement definitions, measurement profiles, 266 current scenarios, and five-card evidence.
3. Produce Threats, Risks, Scenario families, and Node/protocol surfaces without embedding a second table in Jinja.
4. Keep Applicable, Configured, Exercised, Verified, Unavailable, and Reserved distinct.

### Task 4: Render and cross-link the page

**Files:**
- Create: `dwarf/profile_manager/views/learn_measurement_coverage.py`
- Create: `dwarf/dashboard/templates/learn/measurement_coverage.j2`
- Create: `dwarf/dashboard/static/js/measurement-coverage.js`
- Modify: `dwarf/profile_manager/dashboard.py`
- Modify: `dwarf/profile_manager/data/sub_nav.py`
- Modify: `dwarf/profile_manager/data/learn_api.py`
- Modify: `dwarf/dashboard/templates/learn/landing.j2`
- Modify: `dwarf/dashboard/templates/learn/coverage.j2`
- Modify: `dwarf/dashboard/templates/learn/measurements.j2`
- Modify: `dwarf/dashboard/static/css/base.css`

1. Add the route, Learn card, navigation chip, and uncluttered cross-links.
2. Render search, implementation/source/status filters, four view controls, native disclosures, evidence links, empty state, and print styles.
3. Add a labeled child-friendly explanation below every main technical section.

### Task 5: Verify and deliver

1. Run focused and full pytest suites.
2. Validate all scenarios semantically, schemas, mappings, and offline profiles.
3. Run public-safety and secret scans.
4. Browser-test the required routes at desktop, tablet, mobile, and print sizes without launching a workload.
5. Commit and push the reviewed internal batch, build/deploy that exact commit, and verify live.
6. Reconcile the same public-safe files onto current public `main`, confirm zero forbidden publisher matches, test, and push normally without force.
