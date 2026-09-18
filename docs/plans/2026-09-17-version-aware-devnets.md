# Version-aware Real-node Devnets Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add explicit release discovery, selection, compatibility, runtime qualification, UI, and evidence support for Cardano-only, Amaru-only, and mixed real-node devnets, then determine the newest proven working release or pair for each scope.

**Architecture:** A checked-in catalog owns immutable release identities and scope-specific verification records. Profiles choose `latest-confirmed`, `latest-stable`, or an exact release/pair; a resolver freezes that intent into exact deployment provenance. Qualification uses additive fresh-volume projects and writes evidence back only after the full real-node runtime contract passes.

**Tech Stack:** Python 3, JSON Schema, Jinja templates, vanilla JavaScript/CSS, Docker Compose, pytest, Playwright, GitHub release/registry APIs, real Cardano-node and Amaru processes.

---

### Task 1: Define and validate the central version catalog

**Files:**
- Create: `dwarf/versions/catalog.json`
- Create: `dwarf/profile_manager/version_catalog.py`
- Create: `tests/test_version_catalog.py`

**Step 1: Write failing tests**

Cover catalog schema validation, unique release identities, allowed statuses,
scope-specific confirmation, independent default flags, immutable digest
requirements, compatibility-pair lookup, and rejection of contradictory
defaults or unscoped claims.

**Step 2: Run the focused test and observe RED**

Run: `pytest -q tests/test_version_catalog.py`

Expected: failure because the catalog loader/resolver does not exist.

**Step 3: Implement the smallest catalog model**

Add typed validation helpers, release/pair lookup, status resolution, default
resolution, and deterministic JSON serialization. Seed the catalog with
source-audited releases and only evidence-backed historical confirmations.

**Step 4: Run focused tests and observe GREEN**

Run: `pytest -q tests/test_version_catalog.py`

**Step 5: Commit**

Commit locally with message `feat: add version compatibility catalog`.

### Task 2: Add deterministic release refresh and artifact discovery

**Files:**
- Create: `dwarf/scripts/refresh_version_catalog.py`
- Create: `tests/test_refresh_version_catalog.py`
- Modify: `dwarf/versions/catalog.json`

**Step 1: Write failing tests**

Use injected HTTP/registry responses to prove stable-release ordering,
prerelease/nightly classification, source-revision capture, digest capture,
preservation of human verification records, and reviewable deterministic
output. Prove discovery never sets `confirmed`.

**Step 2: Run RED**

Run: `pytest -q tests/test_refresh_version_catalog.py`

**Step 3: Implement refresh command**

Fetch official Cardano-node and Amaru release metadata, resolve exact Git
revisions, inspect configured image manifests when available, and emit a
candidate catalog or diff without overwriting verification evidence.

**Step 4: Run GREEN and a live read-only refresh**

Run the focused test, then run the command against official endpoints in
preview mode and retain the response timestamp/source revisions.

**Step 5: Commit**

Commit locally with message `feat: discover node release metadata`.

### Task 3: Extend profiles with version policy and compatibility selection

**Files:**
- Modify: `dwarf/spec/v1/profile.schema.json`
- Modify: `dwarf/profile_manager/profiles.py`
- Modify: `dwarf/profile_manager/profile_templates.py`
- Modify: shipped profile templates under `dwarf/profile_manager/templates/`
- Modify: `tests/test_definition_catalogs.py`
- Modify: `tests/test_operate_profile_templates.py`
- Create: `tests/test_profile_version_resolution.py`

**Step 1: Write failing schema/model/resolver tests**

Cover the three policies, exact single-node releases, exact mixed pairs,
legacy profiles, stable-but-unknown warnings, incompatible/blocked hard stops,
and default resolution by deployment scope.

**Step 2: Run RED**

Run the three focused test files.

**Step 3: Implement profile fields and resolution**

Add policy and exact-selection fields, resolve them through the catalog, and
freeze exact identities into the deployment plan without rewriting profile
source. Preserve legacy profiles through an explicit compatibility path.

**Step 4: Run GREEN**

Run focused tests and existing profile/catalog tests.

**Step 5: Commit**

Commit locally with message `feat: resolve profile node versions`.

### Task 4: Make Docker/runtime version resolution truthful

**Files:**
- Modify: `dwarf/scripts/runtime_install_version.py`
- Modify: `dwarf/scripts/runtime_substrate_common.py`
- Modify: `dwarf/scripts/runtime_compose_substrate.py`
- Modify: existing runtime metadata/bundle writers as located by tests
- Create: `tests/test_runtime_version_artifacts.py`
- Modify: relevant compose/runtime tests

**Step 1: Write failing runtime tests**

Prove a missing Docker image is not reported as present, an image digest is
captured from inspection, requested and node-reported versions must agree, and
unknown acknowledgement plus resolved provenance reaches runtime evidence.

**Step 2: Run RED**

Run: `pytest -q tests/test_runtime_version_artifacts.py`

**Step 3: Implement exact artifact checks**

Inspect/pull configured image references, capture repo digests/image IDs,
verify node identity after launch, and write requested policy, exact resolution,
catalog revision, status, acknowledgement, and evidence links to run metadata.
Do not accept tag text as proof that an image exists.

**Step 4: Run GREEN and regression tests**

Run focused runtime, deployment-contract, and scenario validation suites.

**Step 5: Commit**

Commit locally with message `fix: enforce exact runtime version provenance`.

### Task 5: Add deployment preview and acknowledgement gates

**Files:**
- Modify: dashboard deployment preview/action handlers in `dwarf/profile_manager/`
- Modify: applicable deployment templates and JavaScript
- Create: `tests/test_deployment_version_gate.py`
- Modify: `tests/test_dashboard_deployment_contract.py`

**Step 1: Write failing tests**

Cover preview of exact identities and compatibility evidence; deployment of a
confirmed selection; explicit one-run acknowledgement for unknown stable
selections; and hard rejection of incompatible/blocked selections.

**Step 2: Run RED**

Run focused deployment tests.

**Step 3: Implement preview and gate**

Present the resolved catalog record before mutation. Record acknowledgement in
the run/deployment record without changing catalog status.

**Step 4: Run GREEN**

Run focused and deployment regression tests.

**Step 5: Commit**

Commit locally with message `feat: gate deployments by version evidence`.

### Task 6: Add version-aware profile and catalog UI

**Files:**
- Modify: `dwarf/profile_manager/views/operate_definition_edit.py`
- Modify: `dwarf/profile_manager/data/operate_profiles.py`
- Modify: profile list/detail/editor templates
- Create: `dwarf/profile_manager/views/operate_versions.py`
- Create: `dwarf/dashboard/templates/operate/versions.j2`
- Modify: dashboard routing/navigation/CSS/JavaScript
- Create: `tests/test_operate_versions.py`
- Modify: visual/accessibility/navigation tests

**Step 1: Write failing route/render tests**

Require release and pair selectors, status/default badges, exact digest/source
details, warning/block copy, accessible labels/help, filterable catalog rows,
and consistent profile list/detail/preview rendering.

**Step 2: Run RED**

Run focused dashboard tests.

**Step 3: Implement UI**

Populate structured selectors from the central catalog while preserving raw
JSON/YAML editing. Add contextual help and an Operate versions catalog.

**Step 4: Run GREEN**

Run focused route, render, accessibility, and navigation suites.

**Step 5: Commit**

Commit locally with message `feat: expose version support in dashboard`.

### Task 7: Document the operator and qualification workflow

**Files:**
- Create: `dwarf/dashboard/templates/learn/versions.j2`
- Modify: dashboard routing/navigation
- Modify: `dwarf/docs/` version/deployment documentation
- Modify: applicable README/operator documentation
- Create or modify: documentation/render tests

**Step 1: Write failing documentation tests**

Require definitions of stable/confirmed/default/unknown/incompatible/blocked,
policy examples, exact-pair examples, evidence gates, refresh workflow, known
limitations, and the claim boundary for Amaru-only operation.

**Step 2: Run RED**

Run focused docs/route tests.

**Step 3: Implement documentation**

Add the Learn page and repository docs; link profile/editor/catalog surfaces to
the relevant section.

**Step 4: Run GREEN**

Run focused and freshness/internal-link tests.

**Step 5: Commit**

Commit locally with message `docs: explain version-qualified devnets`.

### Task 8: Build an additive qualification harness

**Files:**
- Create: `dwarf/scripts/qualify_node_versions.py`
- Create: qualification templates/config under `dwarf/versions/qualification/`
- Create: `tests/test_qualify_node_versions.py`
- Modify: runtime helpers only where required by failing tests

**Step 1: Write failing harness tests**

Cover newest-to-oldest ordering, unique project/volume names, preservation of
the active known-good topology, fresh-state enforcement, every runtime gate,
failure classification, teardown, evidence retention, and catalog-update
generation without automatic confirmation.

**Step 2: Run RED**

Run: `pytest -q tests/test_qualify_node_versions.py`

**Step 3: Implement qualification harness**

Reuse the existing real-node substrate/runtime helpers and current upstream
mixed topology contracts. Generate a reviewable verification-record patch only
after all scope-specific gates pass.

**Step 4: Run GREEN**

Run focused tests and a dry run that performs no deployment mutation.

**Step 5: Commit**

Commit locally with message `feat: add real-node version qualification`.

### Task 9: Qualify Cardano-only releases newest-to-oldest

**Files:**
- Modify: `dwarf/versions/catalog.json` after evidence review
- Add: retained qualification evidence outside public source, with portable
  references in the catalog
- Modify: local notes/workbenches

**Step 1: Establish candidate order and exact artifacts**

Start at the newest stable release and move backwards only after classifying a
failed attempt against official sources and workbench history.

**Step 2: Run fresh real-node qualification**

Require startup, exact version identity, network/genesis consistency, sustained
block progress, peer formation, clean observation, and teardown.

**Step 3: Review evidence and update catalog**

Mark only the exact passing release/digest `confirmed` for `cardano-only` and
record newer failures with precise status/reason/evidence.

**Step 4: Re-run resolver tests**

Prove `latest-confirmed` selects the newly established exact release.

### Task 10: Qualify Amaru-only releases newest-to-oldest

**Files:** Same evidence/catalog/workbench surfaces as Task 9.

**Step 1: Define the honest Amaru contract**

Determine from current Amaru source/docs whether standalone fresh-chain block
production is supported. If not, use and label the strongest relay/consumer
contract with an external honest source.

**Step 2: Run newest-to-oldest qualifications**

Require exact identity, bootstrap completion, sustained chain adoption, peer
health, no panic/restart loop, and clean teardown.

**Step 3: Classify against upstream facts and update catalog**

Do not misclassify known bootstrap/listener constraints as new findings.

**Step 4: Re-run resolver tests**

Prove the Amaru-only default resolves to the newest exact passing release.

### Task 11: Qualify mixed pairs newest-to-oldest

**Files:** Same evidence/catalog/workbench surfaces as Task 9.

**Step 1: Preserve the successful upstream topology contract**

Use the current upstream `cardano_amaru` topology as the baseline and retain its
bootstrap producer, stores, markers, isolated consumer, and fault exclusions.

**Step 2: Test current releases, then move backwards deliberately**

Exercise explicit Cardano/Amaru pairs. Change one dimension at a time where
possible so failures are attributable.

**Step 3: Require full mixed readiness**

In addition to generic gates, prove the consumer is fed by Amaru and converges
with the producer; alternate Cardano paths must not make the check vacuous.

**Step 4: Review and update catalog**

Set the mixed default only to the newest exact pair that passed every gate.
Leave the prior healthy deployment running until this review is complete.

### Task 12: Full verification, UI review, and handoff

**Files:**
- Modify: local notes and `dwarf-latest`/`moog` workbenches
- Create: final local qualification report
- Create: clean public-repository tarball containing only intended files

**Step 1: Run full automated verification**

Run all focused tests, the broader pytest suite, schema validation, route/link
crawl, secret scan, archive hygiene checks, and Docker Compose rendering.

**Step 2: Perform two browser review cycles**

Use Playwright/screenshots on desktop and mobile for profile list/detail/editor,
version catalog, preview/gate states, run provenance, and Learn documentation.
Fix every confirmed issue and repeat the entire set.

**Step 3: Run exact end-to-end default deployments**

Through DWARF, with fresh volumes, run Cardano-only, Amaru-only, and mixed
defaults and retain all required exact evidence. Confirm the original healthy
topology was not broken.

**Step 4: Review changes before claiming completion**

Inspect `git diff`, catalog claims against evidence, exact source/image
identities, logs, and current official issues/docs. Run the verification suite
again after any correction.

**Step 5: Update workbenches and prepare archive**

Record evidence-backed status in both applicable workbenches. Produce a minimal
tar.gz with no secrets, Apple metadata, caches, build output, runtime state, or
unrelated changes. Report its full local path and stop before public push or
any paid/live Antithesis submission.
