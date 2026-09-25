# DWARF Measurement Program Technical Debrief Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build, verify, publish, and retain a new interactive DWARF measurement-program technical debrief without changing existing workbench objects or measurement evidence.

**Architecture:** A single dependency-free HTML file contains semantic content, forensic-noir CSS, and progressive-enhancement JavaScript. A focused pytest contract verifies the artifact, while a Chromium audit verifies the published object at desktop and mobile sizes. Publication uses the existing internal workbench API and verifies exact source bytes after readback.

**Tech Stack:** HTML5, CSS, browser JavaScript, pytest, Chromium/Playwright, internal DWARF workbench API.

---

### Task 1: Freeze the artifact contract

**Files:**
- Create: `tests/test_measurement_technical_debrief.py`

1. Write tests for the exact title, ten required sections, directly adjacent child explanations, all ten accepted run IDs, historical finding linkage, exact node revisions, four source classes, coverage states, inventory links, reproducibility data, accessible controls, keyboard shortcuts, print styling, and responsive overflow rules.
2. Run `python3 -m pytest -q tests/test_measurement_technical_debrief.py`.
3. Confirm the tests fail because the HTML source does not exist.

### Task 2: Implement the standalone debrief

**Files:**
- Create: `docs/workbench/dwarf-measurement-program-technical-debrief.html`

1. Add semantic landmarks, sticky navigation, executive summary, traceability, tap architecture, five card dossiers, findings, usage steps, linked inventory, coverage ledger, reproducibility identities, and next work.
2. Place a labeled child-friendly explanation directly after every technical section.
3. Add dependency-free search, status filters, collapsible details, live status, and keyboard controls.
4. Add accessible focus behavior, print expansion, responsive layouts, contained table scrolling, and `overflow-x: clip` at page level.
5. Run the focused test and correct only evidence-backed failures until it passes.

### Task 3: Review and regression-test the source

**Files:**
- Review: `docs/workbench/dwarf-measurement-program-technical-debrief.html`
- Review: `tests/test_measurement_technical_debrief.py`
- Review: both plan documents

1. Validate HTML structure and local fragment targets.
2. Check every external and repository link.
3. Search for secrets, unsupported claims, and accidental references to temporary artifacts.
4. Run the focused test and the relevant documentation/frontend tests.
5. Review the exact staged diff and confirm known dirty files remain excluded.

### Task 4: Commit and push the reviewed source

**Files:**
- Commit only the two plan documents, HTML source, and focused test.

1. Confirm the origin is the internal V7-PRAGMA URL.
2. Confirm tests pass and review `git diff --cached`.
3. Commit without staging `dwarf/state/chain-head.json`, bundles, task directories, caches, build outputs, or secrets.
4. Push normally to internal `main`; never force-push.

### Task 5: Publish a new workbench object

1. POST a new HTML object to `dwarf-latest` with the exact approved title. Do not update or delete any prior object.
2. Read the object through the workbench API and compare its content byte-for-byte with the committed source.
3. Record the object ID, URL, byte count, SHA-256 digest, and ETag.

### Task 6: Browser and publication verification

1. Use Chromium to open the published object at 1440×900 and 390×844.
2. Verify search, every filter, collapsible controls, `/`, `Escape`, and `Alt+1` through `Alt+4`.
3. Verify no page-level horizontal overflow, no console/page errors, valid internal fragments, and successful linked-resource responses.
4. Render desktop and mobile screenshots and inspect them for clipping, overlap, readability, and control visibility.
5. Verify print media exposes all evidence and suppresses interactive chrome.
6. Recheck repository status, pushed commit identity, source SHA-256, and retained dirty files.

### Task 7: Final program audit

1. Complete the remaining ten-run provenance audit, Gate 2 evidence check, acceptance-card schema check, and prohibited-scope check.
2. Confirm existing workbench status, runbook, goal, and completion-proof objects still render and retain their identities.
3. Mark the active goal complete only if the final audit has no unresolved non-deferred requirement.
