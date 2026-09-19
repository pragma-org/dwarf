# Compact Multi-Backend Run Wizard Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a compact nine-step `/run` wizard that classifies Local, GitHub Actions, and Antithesis honestly, supports a real seed override, performs real readiness checks, and redirects a server-owned local run to live logs without terminating it.

**Architecture:** Extend the immutable run plan with bounded execution and seed inputs, add pure presentation/capability helpers, and add small authenticated APIs for readiness and backend actions. Move local execution into a server-owned background job with file-backed status below the private launch directory. Keep GitHub workflow dispatch and Antithesis bundle preparation separate from paid Moog submission, and preserve all existing run evidence routes.

**Tech Stack:** Python 3 standard library, DWARF profile manager, Jinja templates, vanilla JavaScript, CSS, pytest, Playwright browser audit, Docker deployment on cardano-box.

---

### Task 1: Extend the immutable run request with execution and seed inputs

**Files:**
- Modify: `dwarf/profile_manager/run_plan.py`
- Modify: `dwarf/profile_manager/launch_store.py`
- Test: `tests/test_run_plan.py`

**Step 1: Write failing request-validation tests**

Add tests that prove:

```python
def test_request_accepts_bounded_execution_and_seed_override():
    request = RunPlanRequest.from_mapping({
        "scenario_id": "edge-cases-cbor-tx-body-amaru",
        "execution": "local",
        "seed": "0xC0DE5002",
    })
    assert request.execution == "local"
    assert request.seed == "0xC0DE5002"


@pytest.mark.parametrize("seed", ["-1", "0x", "0xGG", "18446744073709551616"])
def test_request_rejects_invalid_seed_override(seed):
    with pytest.raises(RunPlanError) as raised:
        RunPlanRequest.from_mapping({
            "scenario_id": "edge-cases-cbor-tx-body-amaru",
            "seed": seed,
        })
    assert raised.value.field == "seed"
```

Also reject unknown execution values and any `iterations` request field.

**Step 2: Run the focused tests and verify RED**

Run:

```bash
PYTHONPATH=dwarf pytest -q tests/test_run_plan.py -k 'execution or seed_override or invalid_seed'
```

Expected: failures because `RunPlanRequest` does not yet accept `execution` or `seed`.

**Step 3: Implement bounded parsing**

Add:

- `execution` with allowed values `local`, `github-actions`, and `antithesis`.
- `seed` as `None`, decimal unsigned 64-bit text, or `0x` hexadecimal unsigned 64-bit text.
- A normalized effective seed in `plan["scenario"]["seed"]`.
- A `seed_source` value of `scenario-default` or `operator-override`.

Do not add editable iteration semantics. Keep the recorded scenario value and add an explicit capability note that the top-level field is not consumed by the current engine.

**Step 4: Write and verify a failing materialization test**

```python
def test_launch_materializes_seed_override(tmp_path):
    plan = resolve_run_plan(RunPlanRequest(
        scenario_id="edge-cases-cbor-tx-body-amaru",
        seed="0xC0DE5002",
    ))
    stored = create_launch(plan, root=tmp_path)
    scenario = json.loads(load_launch(stored["launch_id"], root=tmp_path)["scenario_path"].read_text())
    assert scenario["seed"] == "0xC0DE5002"
```

Run it and confirm it fails because `_materialized_scenario` does not write the effective seed.

**Step 5: Materialize the effective seed and verify GREEN**

Set `value["seed"]` from the resolved plan before hashing and writing `scenario.yaml`. Run:

```bash
PYTHONPATH=dwarf pytest -q tests/test_run_plan.py
```

Expected: all tests pass.

**Step 6: Commit**

```bash
git add dwarf/profile_manager/run_plan.py dwarf/profile_manager/launch_store.py tests/test_run_plan.py
git commit -m "feat: add immutable run execution and seed inputs"
```

### Task 2: Add profile qualification and historical runtime presentation facts

**Files:**
- Modify: `dwarf/profile_manager/data/operate_run_wizard.py`
- Create: `dwarf/profile_manager/run_presentation.py`
- Test: `tests/test_landing_run_wizard.py`
- Test: `tests/test_run_plan.py`

**Step 1: Write failing qualification tests**

Test a pure helper that returns `default-confirmed`, `confirmed`, or `unmarked` from a resolved profile preview and whether it is the scenario default. Test that a failure to resolve one profile becomes `unmarked` and does not break the catalog.

**Step 2: Run the tests and verify RED**

```bash
PYTHONPATH=dwarf pytest -q tests/test_landing_run_wizard.py -k 'qualification or profile_option'
```

Expected: failures because the helper and catalog fields do not exist.

**Step 3: Implement qualification metadata**

Build profile-option metadata from the same version-catalog resolver used by deployment. Do not infer confirmation from the profile name. Include:

```python
{
    "qualification": "confirmed" | "unmarked",
    "status_label": "CONFIRMED" | "",
    "status_reason": "...",
}
```

The browser adds `DEFAULT ·` only when the profile is the resolved scenario default.

**Step 4: Write failing duration-evidence tests**

Use temporary retained manifests with matching and nonmatching scenario/profile/version identities. Assert that the helper returns:

```python
{
    "available": True,
    "median_seconds": 12.0,
    "minimum_seconds": 10.0,
    "maximum_seconds": 14.0,
    "sample_count": 3,
    "basis": "matching-retained-runs",
}
```

and returns `available: False` when there is no reliable match.

**Step 5: Implement the smallest evidence-based estimator**

Read at most the latest 20 completed manifests. Require the same scenario ID and, when present, the same profile ID and resolved target versions. Use recorded duration only. Do not extrapolate from iteration count.

**Step 6: Run tests and verify GREEN**

```bash
PYTHONPATH=dwarf pytest -q tests/test_run_plan.py tests/test_landing_run_wizard.py
```

**Step 7: Commit**

```bash
git add dwarf/profile_manager/run_presentation.py dwarf/profile_manager/data/operate_run_wizard.py tests/test_run_plan.py tests/test_landing_run_wizard.py
git commit -m "feat: expose run qualification and duration evidence"
```

### Task 3: Classify backend support from real contracts

**Files:**
- Create: `dwarf/profile_manager/run_backends.py`
- Modify: `dwarf/profile_manager/run_plan.py`
- Test: `tests/test_run_backends.py`

**Step 1: Write failing Local capability tests**

Assert that Local is supported for the existing library, profile-bound devnet, and attached-topology examples. Classify confirmation from retained matching evidence, not from scenario existence.

**Step 2: Write failing GitHub Actions capability tests**

Assert:

- A self-provisioning Cardano devnet scenario maps to `.github/workflows/dwarf-devnet-smoke.yml` with input `scenarios=dwarf/scenarios/<id>.yaml`.
- An attached mixed topology is unsupported because the workflow cannot preserve its substrate contract.
- A library scenario is unsupported when the available fuzz workflow does not execute that selected scenario as such.

**Step 3: Write failing Antithesis capability tests**

Use the actual Antithesis generator mapping functions. Assert that a supported Cardano CBOR scenario reports supported and that an unsupported Amaru or unmapped scenario returns the generator reason without writing a bundle.

**Step 4: Run tests and verify RED**

```bash
PYTHONPATH=dwarf pytest -q tests/test_run_backends.py
```

**Step 5: Implement pure capability classification**

Return, for each backend:

```python
{
    "id": "local",
    "state": "confirmed" | "supported-unconfirmed" | "unsupported",
    "label": "Local",
    "reason": "...",
    "action": "start-local" | "dispatch-github" | "prepare-antithesis" | None,
}
```

Do not invoke GitHub, Moog, Docker, or Antithesis during classification.

**Step 6: Put capability results in the resolved plan and verify GREEN**

Run:

```bash
PYTHONPATH=dwarf pytest -q tests/test_run_backends.py tests/test_run_plan.py
```

**Step 7: Commit**

```bash
git add dwarf/profile_manager/run_backends.py dwarf/profile_manager/run_plan.py tests/test_run_backends.py tests/test_run_plan.py
git commit -m "feat: classify real run backend support"
```

### Task 4: Add read-only readiness API and reusable topology repair presentation

**Files:**
- Modify: `dwarf/profile_manager/dashboard.py`
- Modify: `dwarf/profile_manager/launch_store.py`
- Modify: `dwarf/dashboard/static/js/topology-health.js`
- Test: `tests/test_landing_run_wizard.py`
- Test: `tests/test_topology_health.py`

**Step 1: Write failing readiness API tests**

Test `POST /api/run/readiness` for:

- Framework-only plan returns `ready`.
- Mixed-topology health returns `blocked` with the real reason.
- Profile preflight uses a private transient launch, returns the check result, and removes the transient launch afterward.
- Stale plan digest is rejected.
- The endpoint does not require the mutation token because it performs no external mutation.

**Step 2: Run tests and verify RED**

```bash
PYTHONPATH=dwarf pytest -q tests/test_landing_run_wizard.py -k readiness
```

**Step 3: Implement readiness dispatch**

Reuse `_default_run_preflight`. Add a safe `discard_launch` helper restricted to the configured launch root. Keep start-time preflight unchanged so readiness is checked again immediately before mutation.

**Step 4: Extract reusable redeploy behavior**

Make the status-page redeploy controller accept any panel with the same data contract. Do not duplicate the destructive endpoint or confirmation phrase. The wizard will render its own panel and dialog using the same controller.

**Step 5: Run tests and verify GREEN**

```bash
PYTHONPATH=dwarf pytest -q tests/test_landing_run_wizard.py tests/test_topology_health.py
```

**Step 6: Commit**

```bash
git add dwarf/profile_manager/dashboard.py dwarf/profile_manager/launch_store.py dwarf/dashboard/static/js/topology-health.js tests/test_landing_run_wizard.py tests/test_topology_health.py
git commit -m "feat: expose run readiness and reusable repair flow"
```

### Task 5: Make Local runs server-owned and announce the run identifier early

**Files:**
- Create: `dwarf/profile_manager/run_jobs.py`
- Modify: `dwarf/profile_manager/dashboard.py`
- Modify: `dwarf/profile_manager/scenario.py`
- Modify: `dwarf/profile_manager/cli.py`
- Test: `tests/test_run_jobs.py`
- Test: `tests/test_landing_run_wizard.py`

**Step 1: Write a failing early-announcement test**

Inject a `started_callback` into `run_scenario` and assert it receives the handle before the first scenario phase executes. Test the CLI callback prints `run_id: <id>` with flushing enabled.

**Step 2: Verify RED**

```bash
PYTHONPATH=dwarf pytest -q tests/test_run_jobs.py -k announce
```

**Step 3: Add the callback without changing default library behavior**

The callback is optional and runs immediately after `forensic.start_run`. Existing direct callers retain identical behavior.

**Step 4: Write failing background-job tests**

Test a file-backed job state machine:

```text
created -> preflighting -> running -> completed|failed|blocked
```

Assert that output lines are appended, `run_id:` is parsed, process exit is recorded, and closing the initiating response does not terminate the injected worker.

**Step 5: Implement background job ownership**

Store `job.json` and `job.log` in the private launch directory with mode `0600`. Use one daemon thread per accepted launch. The thread owns the subprocess, updates state atomically, and releases the global mutation lock in `finally`.

Change `POST /api/run/start` to return HTTP 202 JSON containing the launch ID after accepted background start. Add `GET /api/run/jobs/<launch-id>` as a read-only bounded status response. Do not return credentials or the command line.

**Step 6: Verify GREEN and concurrency behavior**

```bash
PYTHONPATH=dwarf pytest -q tests/test_run_jobs.py tests/test_landing_run_wizard.py
```

**Step 7: Commit**

```bash
git add dwarf/profile_manager/run_jobs.py dwarf/profile_manager/dashboard.py dwarf/profile_manager/scenario.py dwarf/profile_manager/cli.py tests/test_run_jobs.py tests/test_landing_run_wizard.py
git commit -m "feat: keep local runs alive across browser navigation"
```

### Task 6: Add guarded GitHub dispatch and Antithesis preparation

**Files:**
- Modify: `dwarf/profile_manager/run_backends.py`
- Modify: `dwarf/profile_manager/dashboard.py`
- Test: `tests/test_run_backends.py`

**Step 1: Write failing GitHub dispatch tests**

Inject the HTTP opener. Assert:

- Only a supported mapped workflow can dispatch.
- The request goes to `repos/<org>/<repo>/actions/workflows/dwarf-devnet-smoke.yml/dispatches`.
- The body contains the selected repository ref and exact scenario path input.
- The token is sent only in the authorization header and never returned.
- Missing token/repo produces a structured blocked response.

**Step 2: Run and verify RED**

```bash
PYTHONPATH=dwarf pytest -q tests/test_run_backends.py -k github
```

**Step 3: Implement token-gated workflow dispatch**

Use the existing private Moog configuration for `github_pat` and `github_repo`. Return the repository workflow URL, not a fabricated workflow-run ID. Require the normal dashboard mutation token.

**Step 4: Write failing Antithesis preparation tests**

Assert that preparation:

- Refuses unsupported scenarios with the classifier reason.
- Runs the native scenario generator into a private state directory.
- Requires generated-bundle verification to pass.
- Returns a handoff URL with scenario and asset directory identifiers.
- Never calls Moog `create-test`.

**Step 5: Implement preparation and verify GREEN**

```bash
PYTHONPATH=dwarf pytest -q tests/test_run_backends.py
```

**Step 6: Commit**

```bash
git add dwarf/profile_manager/run_backends.py dwarf/profile_manager/dashboard.py tests/test_run_backends.py
git commit -m "feat: add guarded external run handoffs"
```

### Task 7: Replace the wizard presentation and resize the landing artwork

**Files:**
- Modify: `dwarf/dashboard/templates/operate/run_wizard.j2`
- Modify: `dwarf/dashboard/static/js/run-wizard.js`
- Modify: `dwarf/dashboard/static/css/base.css`
- Modify: `dwarf/dashboard/templates/operate/antithesis.j2`
- Test: `tests/test_landing_run_wizard.py`
- Test: `tests/test_dashboard_visual_contract.py`

**Step 1: Replace old presentation assertions with failing nine-step assertions**

Assert the ordered steps are:

```python
("scenario", "target", "profile", "versions", "measurements",
 "execution", "settings", "readiness", "review")
```

Assert there is no `data-run-step="primitives"`, and that the effective YAML and resolved-plan disclosures exist in Review.

**Step 2: Add failing behavior-contract assertions**

Check the JavaScript source for:

- Readiness endpoint use.
- `sessionStorage` selection preservation.
- backend-specific action labels.
- background job polling.
- redirect to `/operate/runs/<id>/live`.
- no `innerHTML`.

Check the CSS for bounded controls, compact spacing, responsive one-column layout, and `READY`/`NOT READY` state classes.

**Step 3: Run and verify RED**

```bash
PYTHONPATH=dwarf pytest -q tests/test_landing_run_wizard.py tests/test_dashboard_visual_contract.py -k 'run_wizard or landing_motion'
```

**Step 4: Implement the compact markup**

Use native labels, selects, inputs, buttons, details, and dialog. Give every control a short visible title and explanatory text. Render profile option prefixes server-side. Keep technical identifiers in summaries/details.

**Step 5: Implement browser behavior**

- Debounce plan resolution.
- Run readiness only after a valid plan.
- Disable unsupported backends.
- Render selected backend state and reason.
- Validate the seed before resolution and again server-side.
- Save selection state before redeploy and restore it at load.
- Start the selected backend through its exact endpoint.
- For Local, poll job status and redirect when `run_id` appears.
- For GitHub, open the returned workflow URL.
- For Antithesis, follow the returned handoff URL.

**Step 6: Implement compact responsive CSS and landing sizing**

Use approximately:

```css
.product-landing__wordmark { font-size: clamp(1.25rem, 3.5vw, 2.75rem); }
.product-landing__logo { width: min(100%, 260px); }
```

Apply a proportionate smaller mobile limit. Keep all wizard controls at `max-width: 100%`, use compact padding/gaps, and prevent page-level horizontal scrolling.

**Step 7: Verify GREEN**

```bash
PYTHONPATH=dwarf pytest -q tests/test_landing_run_wizard.py tests/test_dashboard_visual_contract.py
```

**Step 8: Commit**

```bash
git add dwarf/dashboard/templates/operate/run_wizard.j2 dwarf/dashboard/templates/operate/antithesis.j2 dwarf/dashboard/static/js/run-wizard.js dwarf/dashboard/static/css/base.css tests/test_landing_run_wizard.py tests/test_dashboard_visual_contract.py
git commit -m "feat: deliver compact multi-backend run wizard"
```

### Task 8: Update operator documentation

**Files:**
- Modify: `dwarf/dashboard/templates/learn/operator_runbook.j2`
- Modify: `dwarf/docs/ci-validation-gate.md`
- Test: `tests/test_landing_run_wizard.py`

**Step 1: Write failing documentation assertions**

Require the operator runbook to explain:

- The three execution choices do not have identical semantics.
- The top-level iterations field is not an active runtime control.
- Seed overrides are retained for replay.
- GitHub Actions needs a mapped workflow and configured credentials.
- Antithesis preparation does not submit a paid run.
- Local background execution survives browser navigation.

**Step 2: Run and verify RED**

```bash
PYTHONPATH=dwarf pytest -q tests/test_landing_run_wizard.py -k operator_runbook
```

**Step 3: Update documentation and verify GREEN**

```bash
PYTHONPATH=dwarf pytest -q tests/test_landing_run_wizard.py
```

**Step 4: Commit**

```bash
git add dwarf/dashboard/templates/learn/operator_runbook.j2 dwarf/docs/ci-validation-gate.md tests/test_landing_run_wizard.py
git commit -m "docs: explain run wizard execution boundaries"
```

### Task 9: Full verification, deployment, browser audit, and internal push

**Files:**
- Modify only if a verified defect is found.

**Step 1: Run focused tests**

```bash
PYTHONPATH=dwarf pytest -q \
  tests/test_run_plan.py \
  tests/test_run_backends.py \
  tests/test_run_jobs.py \
  tests/test_landing_run_wizard.py \
  tests/test_topology_health.py \
  tests/test_dashboard_visual_contract.py
```

Expected: zero failures.

**Step 2: Run the supported Linux suite**

Run the repository's documented supported test command in the same container/runtime used by the prior 909-test verification. Expected: zero failures. Record the exact count.

**Step 3: Review the complete diff**

```bash
git diff --check
git status --short
git log --oneline --decorate -12
```

Confirm `dwarf/state/chain-head.json` remains unstaged and unchanged by this task.

**Step 4: Deploy the exact checkout to cardano-box**

Use the repository Docker deployment path. Record the deployed Git revision and container image identity. Do not change the retained mixed topology unless the operator explicitly confirms redeploy.

**Step 5: Verify live routes and responsive rendering**

Run the read-only dashboard audit. Use Playwright screenshots for:

- Landing: desktop 1440px, tablet 820px, mobile 390px.
- `/run`: the same three widths.
- Long scenario/profile option selections.
- Ready and not-ready states.
- Review technical disclosures.

Require zero page-level horizontal overflow, no clipped controls, no console errors, and keyboard-accessible controls.

**Step 6: Run one real Local scenario through `/run`**

Use the proven smoke scenario. Confirm:

- Final preflight passes.
- The browser redirects to `/operate/runs/<id>/live` while the run is active.
- Closing the wizard does not stop execution.
- The run completes and appears in the normal inspector.
- Exact launch plan, effective scenario, preflight, logs, assertions, and measurements are retained.

Do not submit a live Antithesis run. Verify Antithesis only through generation, validation, preflight/handoff, and the explicit live-confirmation boundary.

**Step 7: Verify GitHub and Antithesis boundaries**

- Confirm an unsupported backend cannot start.
- Confirm supported-but-unconfirmed is visibly labelled.
- Confirm GitHub dispatch uses only its mapped workflow and does not leak the PAT.
- Confirm Antithesis preparation never performs Moog `create-test`.

**Step 8: Push only to the internal repository**

```bash
git push origin main
```

Confirm internal `origin/main` resolves to the deployed commit. Do not push to the public GitHub repository.

