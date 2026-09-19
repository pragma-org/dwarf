# Run Report Collapsible Sections Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make measurement categories and secondary run evidence panels collapsible and closed by default while keeping essential run status visible.

**Architecture:** Use native `<details>` disclosures in the Jinja templates, shared forensic-noir disclosure styling in `base.css`, and small progressive-enhancement behavior in `measurement-report.js` for bulk controls and filter-driven opening. Preserve all existing view-model data, report semantics, routes, and downloads.

**Tech Stack:** Python, Flask/Jinja, semantic HTML, CSS, vanilla JavaScript, pytest, Playwright.

---

### Task 1: Define the presentation contract

**Files:**
- Modify: `tests/test_measurement_frontend.py`

1. Add assertions that each measurement group is a native `<details>` element without the `open` attribute.
2. Add assertions for accessible Expand all and Collapse all controls.
3. Add assertions that the manifest, version provenance, execution definition, floor preview, assertions, probes, and log tail use closed run-detail disclosures while the status summary remains outside them.
4. Run `PYTHONPATH=dwarf /home/nigel/.venvs/dwarf-moog-fix/bin/pytest -q tests/test_measurement_frontend.py` and confirm the new assertions fail for the missing behavior.

### Task 2: Implement measurement category disclosures

**Files:**
- Modify: `dwarf/dashboard/templates/operate/_partials/_measurement_report.j2`
- Modify: `dwarf/dashboard/static/js/measurement-report.js`
- Modify: `dwarf/dashboard/static/css/base.css`

1. Replace each measurement category section with a closed native `<details>` disclosure.
2. Move the category heading, count, and description into its `<summary>`.
3. Add compact Expand all and Collapse all controls.
4. Update filtering so matching groups open while a query or category filter is active.
5. Style hover, focus, open state, count, and responsive summary layout using existing tokens.
6. Run the focused frontend tests and confirm the measurement contract passes.

### Task 3: Implement run evidence disclosures

**Files:**
- Modify: `dwarf/dashboard/templates/operate/run.j2`
- Modify: `dwarf/dashboard/static/css/base.css`

1. Create a consistent `run-disclosure` pattern using native `<details>` and `<summary>`.
2. Convert secondary evidence panels: manifest, version provenance, execution definition, interesting evidence, tamper chain, attestation chain, SARIF export, replay, diff, operator actions, cross-implementation comparison, substrate evidence, floor preview, assertions, probes, and log tail.
3. Retain useful counts or verdicts in each closed summary.
4. Keep run identity, warning banners, status actions, guide, and summary tiles always visible.
5. Run the focused frontend tests and confirm the run-detail contract passes.

### Task 4: Verify behavior and regressions

**Files:**
- Test: `tests/test_measurement_frontend.py`
- Test: full `tests/` suite

1. Run `git diff --check`.
2. Run the focused measurement presentation tests.
3. Run `PYTHONPATH=dwarf /home/nigel/.venvs/dwarf-moog-fix/bin/pytest -q tests` and require zero failures.
4. Confirm no secrets, AppleDouble files, caches, or unrelated changes were introduced.

### Task 5: Deploy and browser-verify

**Files:**
- Verify live Amaru run: `/operate/runs/20260918T234213Z-64959688`
- Verify live Cardano run: `/operate/runs/20260919T032200Z-59f94558`

1. Deploy the exact tested commit to `dwarf-fw`.
2. Verify both run pages and raw measurement routes return HTTP 200.
3. At desktop and mobile widths, confirm all detailed sections are closed initially, individual sections toggle, measurement bulk controls work, filters reveal matching groups, nested technical details remain usable, downloads remain present, and no page-level overflow occurs.
4. Confirm the deployed image revision equals the tested commit.
5. Push only to the internal V7-PRAGMA origin and verify `origin/main` equals the deployed commit.
