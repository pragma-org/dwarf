# DWARF Landing Page and Local Run Wizard Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a minimal readiness-aware landing page and a safe guided `/run` path that resolves, validates, and launches a real local DWARF scenario through the existing engine.

**Architecture:** Keep execution in the existing scenario engine. Add a server-side launch-plan resolver that accepts bounded catalog identifiers and compatible overrides, then revalidates before launch. Persist topology-health results, use the restricted host shim for host execution, and retain the exact launch plan in run evidence.

**Tech Stack:** Python, Flask-style built-in HTTP server, Jinja, JSON/YAML catalog definitions, semantic HTML, CSS, vanilla JavaScript, Docker, pytest, Playwright.

---

### Task 1: Correct the logo asset

**Files:**
- Modify: `dwarf/dashboard/static/dwarf-logo.png`
- Test: `tests/test_dashboard_visual_contract.py`

1. Add a failing visual-contract test for an RGBA logo without the bottom word-mark region.
2. Run the focused test and confirm that the existing 2730 × 1536 asset fails.
3. Use the image-edit workflow to remove only the outlined `Dwarf` word. Preserve the complete flask and transparency.
4. Inspect the edited image at original resolution.
5. Run the focused test and confirm that it passes.
6. Commit the asset and test.

### Task 2: Persist topology-health results

**Files:**
- Modify: `dwarf/profile_manager/data/operate_topology_health.py`
- Modify: `dwarf/profile_manager/data/operate_status.py`
- Test: `tests/test_topology_health.py`

1. Add failing tests that complete a probe, create a new module state, and recover the last result from the configured state directory as cached evidence.
2. Add failing tests for a malformed persisted file, stale age metadata, and a fresh check replacing the cached result.
3. Run the focused tests and confirm the expected failures.
4. Store completed results atomically at `<state>/topology-health/dashboard-latest.json`.
5. Load the file defensively when no process result exists. Add `freshness: cached|current`, `result_age_seconds`, and `previous_is_current: false` for cached data.
6. Keep the existing single-flight lock and in-memory behavior.
7. Run the focused tests and commit.

### Task 3: Add the minimal landing page

**Files:**
- Create: `dwarf/profile_manager/data/landing.py`
- Create: `dwarf/profile_manager/views/landing.py`
- Create: `dwarf/dashboard/templates/landing.j2`
- Create: `dwarf/dashboard/static/js/landing.js`
- Modify: `dwarf/dashboard/static/css/base.css`
- Modify: `dwarf/profile_manager/dashboard.py`
- Modify: `dwarf/dashboard/templates/operate/status.j2`
- Test: `tests/test_landing_and_run_wizard.py`
- Test: `tests/test_dashboard_navigation_accessibility.py`

1. Add failing route and rendering tests for `/`, the animated accessible `DWARF` label, corrected logo, four status pills, ownership links, footer links, and `/run` Enter destination.
2. Add a failing test that the status page no longer contains the flask image but retains the detailed health panel and repair controls.
3. Run the focused tests and confirm the expected failures.
4. Remove `/` from the `/operate` alias map and render a dedicated landing template.
5. Build server-side initial states for Framework, Control channel, Active topology, and Node versions from existing config, topology, and version data.
6. Add the character-on, hold, dim, fade, and repeat animation. Use `aria-label="DWARF"` and a static reduced-motion state.
7. Link `Owned by PRAGMA`, `Created by Jon "GainSec" Gaines`, and `Open Source` exactly as specified with safe external-link attributes.
8. Start a fresh read-only topology check after page load and update only the topology pill and result age.
9. Preserve the detailed health and repair controls on `/operate/status`.
10. Run the focused tests and commit.

### Task 4: Define the run-plan resolver

**Files:**
- Create: `dwarf/profile_manager/run_plan.py`
- Test: `tests/test_run_plan.py`

1. Add failing tests for a default scenario resolution, profile inheritance, `latest-confirmed` version resolution, implicit measurement profile selection, primitive inventory, and scenario defaults.
2. Add failing tests for incompatible profiles, unknown versions without acknowledgement, incompatible measurements, unregistered primitives, unsupported runtimes, and broken assertion-producer dependencies.
3. Run the focused tests and confirm the expected failures.
4. Implement immutable `RunPlanRequest` and normalized resolution output.
5. Load all records again on the server. Accept identifiers and bounded scalar overrides only. Reject paths, commands, unknown fields, and unbounded parameter objects.
6. Reuse `load_scenario`, profile/version resolution, measurement resolution, primitive registry validation, and existing dependency validation.
7. Compute scenario-specific readiness requirements. Do not require mixed topology readiness for unrelated scenarios.
8. Run the focused tests and commit.

### Task 5: Persist launch plans and restrict host execution

**Files:**
- Create: `dwarf/profile_manager/launch_store.py`
- Modify: `delivery/control-plane/dwarf-deploy-shim.py`
- Modify: `delivery/control-plane/provision-control-channel.sh`
- Modify: `dwarf/profile_manager/remote.py`
- Test: `tests/test_run_plan.py`
- Test: `tests/test_dashboard_deployment_contract.py`
- Test: `delivery/tests/test_delivery_contract.sh`

1. Add failing tests for strict launch identifiers, atomic plan writes, path traversal rejection, immutable snapshots, and the new restricted `launch <id>` control-shim verb.
2. Run the focused tests and confirm the expected failures.
3. Store plans below a fixed configured launch root with mode 0600 files and atomic rename.
4. Add a restricted shim verb that accepts only `launch-<lowercase hex>` identifiers and resolves only within that root.
5. Materialize exact scenario and derived profile snapshots for the existing scenario engine. Do not accept an arbitrary browser path or command.
6. Copy the launch plan and resolved inputs into the resulting run bundle.
7. Run the focused tests and commit.

### Task 6: Add read-only run-wizard data APIs

**Files:**
- Create: `dwarf/profile_manager/data/operate_run_wizard.py`
- Create: `dwarf/profile_manager/views/operate_run_wizard.py`
- Modify: `dwarf/profile_manager/dashboard.py`
- Test: `tests/test_landing_and_run_wizard.py`

1. Add failing tests for `GET /run`, scenario options, default selection, compatible profile/version/measurement choices, and primitive phase inventory.
2. Add failing API tests for `POST /api/run/resolve` with valid and invalid request bodies.
3. Run the focused tests and confirm the expected failures.
4. Build wizard data from existing catalog extractors. Do not duplicate catalogs in JavaScript.
5. Add the token-free read-only resolve endpoint with a bounded JSON body and structured field errors.
6. Return the normalized plan, warnings, readiness requirements, and exact evidence identities.
7. Run the focused tests and commit.

### Task 7: Build the responsive wizard interface

**Files:**
- Create: `dwarf/dashboard/templates/operate/run_wizard.j2`
- Create: `dwarf/dashboard/static/js/run-wizard.js`
- Modify: `dwarf/dashboard/static/css/base.css`
- Test: `tests/test_landing_and_run_wizard.py`

1. Add failing presentation-contract tests for all ten wizard stages, default selections, optional/required labels, summaries, errors, and accessible navigation.
2. Run the focused tests and confirm the expected failures.
3. Implement scenario-first progressive disclosure. Show conditional profile, version, and topology controls only when applicable.
4. Show primitives read-only in normal mode. Link advanced customization to the existing structured scenario editor.
5. Resolve the plan after each dependency-changing selection and replace downstream options from the server response.
6. Preserve selections when validation fails. Focus the first invalid field and announce the error.
7. Implement desktop, tablet, and mobile layouts without page-level overflow.
8. Run the focused tests and commit.

### Task 8: Add final readiness and launch streaming

**Files:**
- Modify: `dwarf/profile_manager/dashboard.py`
- Modify: `dwarf/profile_manager/run_plan.py`
- Modify: `dwarf/dashboard/static/js/run-wizard.js`
- Test: `tests/test_landing_and_run_wizard.py`
- Test: `tests/test_scenario_topology_preflight.py`

1. Add failing tests that start rejects stale plans, missing tokens, failed required health, incompatible current versions, and concurrent mutations.
2. Add a passing contract test for a library scenario that does not require mixed-topology health.
3. Run the focused tests and confirm the expected failures.
4. Add `POST /api/run/start` as a token-gated SSE action. Re-resolve and validate the complete plan before writing or executing it.
5. Run the fresh scenario-specific preflight. Preserve its evidence whether it passes or fails.
6. Invoke the existing engine locally or through the restricted launch verb. Stream output and detect the run ID.
7. Redirect or link to `/operate/runs/<run-id>` after completion.
8. Run the focused tests and commit.

### Task 9: Documentation and navigation

**Files:**
- Modify: `dwarf/profile_manager/data/sub_nav.py`
- Modify: `dwarf/dashboard/templates/_base.j2`
- Modify: `dwarf/dashboard/templates/learn/operator_runbook.j2`
- Modify: `dwarf/dashboard/templates/learn/overview.j2`
- Test: `tests/test_dashboard_navigation_accessibility.py`
- Test: `tests/test_version_docs.py`

1. Add failing tests for the Start run navigation entry and the operator documentation.
2. Document wizard defaults, version labels, measurement behavior, primitive customization, readiness gates, and direct CLI equivalence.
3. Explain that version pinning prevents drift but does not prove liveness.
4. Run the focused tests and commit.

### Task 10: Full verification and real local proof

**Files:**
- Test: complete `tests/` suite
- Verify: live `/`, `/run`, `/operate/status`, and retained run page

1. Run `git diff --check`.
2. Run the complete repository suite and require zero failures.
3. Run the delivery shell contract tests.
4. Build and deploy the exact tested image on `cardano-box` without disturbing unrelated retained topologies.
5. Verify landing and wizard rendering at desktop, tablet, and mobile widths with Playwright.
6. Verify animation, reduced motion, keyboard flow, status refresh, external links, wizard defaults, validation errors, and no page-level overflow.
7. Use the wizard to launch one compatible smoke-sized real local scenario through DWARF.
8. Confirm the bundle retains launch plan, exact versions, primitive inventory, measurements, preflight evidence, logs, assertions, and report links.
9. Confirm existing direct scenario launch, schedule, status repair, Antithesis, catalog editors, and run reports still work.
10. Push only to the internal V7-PRAGMA repository after verification. Do not push publicly.
