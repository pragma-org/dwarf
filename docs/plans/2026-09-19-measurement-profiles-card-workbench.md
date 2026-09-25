# Measurement Profiles Card and Progress Workbench Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a dedicated Measurement Profiles card to `/operate` and publish an evidence-backed `measurement-first-progress` HTML object on the `dwarf-latest` workbench.

**Architecture:** Reuse the existing landing-page count supplied by `render_operate`, the existing responsive tile component, and the Bench raw-content API. Keep the workbench narrative separate from runtime code and distinguish proven measurement plumbing from incomplete client-program deliverables.

**Tech Stack:** Python/pytest, Jinja, vanilla CSS/HTML, Playwright, Docker, Bench HTTP API.

---

### Task 1: Add the landing-card regression contract

**Files:**
- Modify: `tests/test_measurement_frontend.py`

**Step 1:** Add assertions that rendered `/operate` contains a dedicated
`href="/operate/measurement-profiles"`, the label `Measurement profiles`, the
profile count, and separate copy for the Measurements card.

**Step 2:** Run the focused test and confirm it fails because the dedicated
card is absent.

### Task 2: Add the minimal landing card

**Files:**
- Modify: `dwarf/dashboard/templates/operate/landing.j2`

**Step 1:** Replace the Measurements subtitle with tap/report-specific copy.

**Step 2:** Add one adjacent tile linked to `/operate/measurement-profiles`
using `measurement_profile_count` and existing component classes.

**Step 3:** Re-run the focused frontend tests and confirm they pass.

### Task 3: Publish the progress object

**Files:**
- Create: `docs/workbench/measurement-first-progress.html`

**Step 1:** Write a responsive, DWARF-themed object covering client asks,
measurement architecture, independent Amaru/Cardano proof, exact retained
runs, status by requirement, and remaining work. State that mixed measurement
work has not started.

**Step 2:** Create the `measurement-first-progress` HTML object on
`dwarf-latest`, then fetch it and verify its SHA-256 equals the tracked source.

### Task 4: Verify, deploy, and push internally

**Files:**
- No additional source files expected.

**Step 1:** Run focused frontend tests, the complete established test suite,
JSON/diff/forbidden-file/secret checks.

**Step 2:** Commit only intended files; keep `dwarf/state/chain-head.json`
unstaged.

**Step 3:** Rebuild and deploy the official Docker image, verify exact revision,
health, both landing cards, key measurement pages, and desktop/mobile layout.

**Step 4:** Push `main` only to the internal `V7-PRAGMA` origin and verify the
remote SHA. Do not push the public remote.

