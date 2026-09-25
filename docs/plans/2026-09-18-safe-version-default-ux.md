# Safe Version Defaults and Version-Aware UX Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. The user explicitly requires direct work on `cardano-box:/home/nigel/dwarf-pragma` `main`, no worktree, and no subagents.

**Goal:** Eliminate ambient/unpinned profile deployment, preserve every existing topology contract, and expose complete version selection and qualification throughout the DWARF UI.

**Architecture:** Normalize missing policy to an evidence-backed `latest-confirmed` selection, but keep topology classification independent from version resolution. Pass exact immutable artifacts into the selected topology adapter, make the profile the only runtime-version authority, and surface resolution in landing pages, profile/scenario previews, target guidance, and retained run evidence.

**Tech Stack:** Python 3, Jinja2, JSON Schema, vanilla JavaScript/CSS, pytest, Docker Compose, Playwright, DWARF CLI/runtime adapters.

---

### Task 1: Lock the unsafe-default and navigation regressions

**Files:**
- Modify: `tests/test_profile_version_resolution.py`
- Modify: `tests/test_dashboard_visual_contract.py`
- Modify: `tests/test_operate_versions.py`

**Steps:**
1. Add a failing resolver test asserting omitted and blank policies resolve as `latest-confirmed`, include `policy_source: implicit-default`, and select the current scope-specific exact default.
2. Add failing landing-page tests requiring `/operate/versions` and `/learn/versions` cards with current-default copy.
3. Run the exact new tests and confirm they fail because the resolver returns `legacy` and the links are absent.
4. Do not change production code in this task.

### Task 2: Normalize policy without changing topology

**Files:**
- Modify: `dwarf/profile_manager/profiles.py`
- Modify: `dwarf/profile_manager/version_catalog.py`
- Modify: `dwarf/profile_manager/deployment_versions.py`
- Modify: `tests/test_profile_version_resolution.py`
- Modify: `tests/test_deployment_version_gate.py`
- Modify: `tests/test_profile_versioned_deploy.py`

**Steps:**
1. Add tests that distinguish `explicit` from `implicit-default` policy sources.
2. Add tests proving adapter/topology classification is unchanged when an omitted policy is normalized.
3. Confirm the tests fail on the current `profile.version_policy != "legacy"` adapter switch.
4. Normalize missing/blank policy to `latest-confirmed` in one shared path.
5. Introduce an independent topology/deployment-adapter classification used by dry-run and real deployment.
6. Ensure version resolution supplies exact releases/digests to the existing adapter rather than selecting the adapter.
7. Run focused resolver, gate, and deployment tests until green.

### Task 3: Migrate and classify every shipped profile

**Files:**
- Modify: `dwarf/profiles/profile-a-*/profile.yaml` through `dwarf/profiles/profile-m-*/profile.yaml`
- Modify: `dwarf/profiles/templates/*.yaml`
- Modify: `tests/test_profile_version_resolution.py`
- Modify: `tests/test_operate_profile_templates.py`

**Steps:**
1. Add a failing inventory test requiring all 16 shipped profiles and every creation template to declare a safe policy.
2. Encode expected topology class and honest qualification context for each profile in the test.
3. Confirm the test identifies the 13 omitted policies and all `latest-stable` creation defaults.
4. Migrate local Cardano, Amaru-target, and mixed profiles to explicit `latest-confirmed`.
5. Give public-network profiles immutable resolution while retaining an unqualified public-network contract and acknowledgement requirement; never label local evidence as public-network confirmation.
6. Change every creation template to `latest-confirmed`.
7. Run the inventory, template, schema, and deployment-resolution tests.

### Task 4: Add landing-page cards

**Files:**
- Modify: `dwarf/profile_manager/views/operate.py`
- Modify: `dwarf/profile_manager/views/learn.py`
- Modify: `dwarf/dashboard/templates/operate/landing.j2`
- Modify: `dwarf/dashboard/templates/learn/landing.j2`
- Modify: `tests/test_dashboard_visual_contract.py`
- Modify: `tests/test_operate_versions.py`

**Steps:**
1. Use the failing Task 1 tests as the red state.
2. Add a small shared/read-only summary derived from the effective version catalog.
3. Render a Node versions tile on `/operate` with the three exact defaults.
4. Render a Version-qualified devnets tile on `/learn` with concise policy guidance.
5. Reuse existing tile classes; add CSS only if screenshot review proves it necessary.
6. Run focused landing and version tests.

### Task 5: Build the structured profile version section

**Files:**
- Modify: `dwarf/profile_manager/views/operate_definition_edit.py`
- Modify: `dwarf/dashboard/templates/operate/definition_editor.j2`
- Modify: `dwarf/dashboard/static/css/base.css`
- Modify: `tests/test_definition_catalogs.py`
- Modify: `tests/test_operate_profile_templates.py`
- Modify: `tests/test_dashboard_visual_contract.py`

**Steps:**
1. Add failing rendered-HTML tests for the dedicated version section, default policy, contextual links, conditional exact selectors, and resolution panel.
2. Add a JSON-safe profile-resolution descriptor containing catalog revision, releases, pairs, qualification details, and current defaults.
3. Group version fields ahead of advanced fields.
4. Add client-side conditional visibility based on node mix and policy.
5. Add a live read-only preview for exact resolved identities and gate state.
6. Keep raw JSON/YAML synchronized with structured edits and subject to server validation.
7. Verify keyboard labels, tooltip behavior, no duplicate descriptions, and mobile wrapping.
8. Run focused builder and visual-contract tests.

### Task 6: Expose inherited versions in scenario and target UX

**Files:**
- Modify: `dwarf/profile_manager/views/operate_definition_edit.py`
- Modify: `dwarf/dashboard/templates/operate/scenario_editor.j2`
- Modify: `dwarf/dashboard/templates/operate/definition_editor.j2`
- Modify: target detail/list templates as identified by the existing catalog view
- Modify: `tests/test_definition_catalogs.py`
- Modify: `tests/test_dashboard_visual_contract.py`

**Steps:**
1. Add failing tests requiring scenario profile-resolution disclosure and target authority guidance.
2. Extend scenario profile options with scope, policy source, exact resolution, and status.
3. Show inherited runtime versions beside the selected scenario profile.
4. Label legacy scenario `target.version` as descriptive metadata, not a deployment pin.
5. Add target guidance and links to `/operate/profiles`, `/operate/versions`, and `/learn/versions`.
6. Confirm no scenario or target field becomes a second runtime-version authority.
7. Run focused scenario/target tests.

### Task 7: Complete preview, CLI/API, and evidence consistency

**Files:**
- Modify: `dwarf/profile_manager/deployment_versions.py`
- Modify: `dwarf/profile_manager/dashboard.py`
- Modify: `dwarf/profile_manager/cli.py`
- Modify: `dwarf/profile_manager/evidence.py`
- Modify: `dwarf/profile_manager/data/operate_run.py`
- Modify: `dwarf/dashboard/templates/operate/profiles.j2`
- Modify: `dwarf/dashboard/templates/operate/run.j2`
- Modify: relevant tests under `tests/test_deployment_version_gate.py`, `tests/test_evidence_defaults.py`, and `tests/test_profile_versioned_deploy.py`

**Steps:**
1. Add failing tests that compare omitted-policy behavior through raw resolver, CLI, API preview, dashboard deploy, dry-run, and evidence serialization.
2. Require complete preview fields: policy source, scope, releases, support release, revisions, digests, pair status, catalog revision, reason, evidence/issues, acknowledgement/block state.
3. Thread the same preview object through every entry point.
4. Retain the full effective catalog snapshot in the forensic bundle.
5. Keep unknown acknowledgement one-run-only and incompatible/blocked hard failures.
6. Run all focused version, CLI, dashboard, and evidence tests.

### Task 8: Documentation and operator claim boundaries

**Files:**
- Modify: `INSTALL.md`
- Modify: `OPERATIONS.md`
- Modify: `dwarf/docs/version-qualified-devnets.md`
- Modify: `dwarf/docs/version-qualification-status-2026-09-18.md`
- Modify: `dwarf/dashboard/templates/learn/versions.j2`
- Modify: `tests/test_version_docs.py`

**Steps:**
1. Add failing documentation assertions for implicit safe default, topology independence, public-network qualification boundary, and profile authority.
2. Document current defaults and exact behavior for omitted, latest-confirmed, latest-stable, and exact policies.
3. Explain that scenarios inherit and targets do not pin runtime binaries.
4. Document migration behavior and operator acknowledgement boundaries.
5. Run documentation tests and link checks.

### Task 9: Full static verification and first UI review cycle

**Files:**
- Modify only files implicated by verified failures.

**Steps:**
1. Run all focused suites.
2. Run the complete pytest suite.
3. Run JSON-schema and scenario validation.
4. Render Docker Compose.
5. Build a candidate dashboard image from the reviewed commit.
6. Launch an isolated review container without replacing production.
7. Run Playwright desktop/mobile flows for every required page and warning state.
8. Inspect screenshots manually and repair only confirmed defects using new failing tests where behavior changes.
9. Repeat the focused and full suites.

### Task 10: Real runtime proof through DWARF

**Files:**
- Preserve evidence under the configured DWARF state/evidence paths; do not add runtime artifacts to Git.

**Steps:**
1. Confirm the retained 12-container control is healthy and record its identity without changing it.
2. Create fresh, isolated representative profiles that exercise implicit-default Cardano-only, Amaru-target, and mixed behavior through the actual CLI/dashboard control path.
3. Verify exact image digests and node-reported versions, required services, peer formation, real progress, absence of fatal/restart loops, convergence where applicable, and clean removal.
4. Exercise public-network adapter/config preservation within safe bounds; do not call a dry run runtime proof.
5. Preserve exact commands, revisions, digests, timestamps, logs, and evidence paths.
6. Remove transient deployments and reconfirm the retained control is unchanged.

### Task 11: Production deployment and second UI review cycle

**Files:**
- Modify only files implicated by verified production defects.

**Steps:**
1. Commit the runtime-proven source.
2. Rebuild and deploy `dwarf-fw` from that exact commit.
3. Verify the image revision label equals Git HEAD.
4. Confirm both landing cards and all relevant routes return 200.
5. Confirm cached automatic refresh and authenticated manual refresh still work.
6. Repeat the complete desktop/mobile Playwright matrix against `https://dwarf.gainpalfam.com`.
7. Crawl internal Operate/Learn links and fix any confirmed failure.
8. Run the complete automated suite again after any repair.

### Task 12: Workbenches, internal push, and public handoff archive

**Files:**
- Update project notes and existing documentation only; do not add secrets or runtime evidence to Git.

**Steps:**
1. Read `https://bench.gainpalfam.com/api/agent/conventions`.
2. Add evidence-backed status to `dwarf-latest` and `moog`, including defaults, migration, tested/untested boundaries, topology preservation, runtime evidence, and remaining public-network limits.
3. Run final `git diff --check`, full pytest, schema validation, scenario validation, Compose rendering, route crawl, and repository hygiene checks.
4. Secret-scan the intended public payload.
5. Commit verified work and push only to the authorized internal Git repository.
6. If PRAGMA has approved the PAT, use only `git push --dry-run` to verify public write access; do not perform a real public push without explicit user approval.
7. Produce a minimal tar.gz from the exact `origin/main..HEAD` intended file set.
8. Prove the archive has no `.git`, `._*`, `.DS_Store`, caches, build outputs, runtime state, secrets, absolute paths, or parent traversal.
9. Copy the archive to a user-accessible local path and report its full path and SHA-256.

