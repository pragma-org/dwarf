# DWARF Landing Page and Local Run Wizard Design

## Goal

Give a new operator one clear entry point and one safe path from an existing DWARF catalog to a real local run. Improve deployment-readiness evidence so a dashboard restart or stale topology cannot create a false ready state.

## Plain-language summary

The first page shows the DWARF flask, four small status lights, and one Enter button. Enter opens a wizard. The wizard selects safe defaults, explains each choice, checks the real deployment, and starts the existing DWARF engine only after the plan is valid.

## Landing page

`/` becomes a dedicated full-screen landing page. `/operate` remains the detailed operator catalog. The landing page contains only:

- animated `DWARF` text;
- the flask image;
- Framework, Control channel, Active topology, and Node versions status pills;
- one `Enter` button that opens `/run`.

The animated word uses the interaction observed in the Battle Ready Armor header: characters appear at 80 ms intervals, the complete word holds, the non-accent state dims over 600 ms, the word fades over 400 ms, and the cycle restarts. The implementation uses the existing DWARF font and color tokens. `prefers-reduced-motion: reduce` displays the complete static word.

The flask image is removed from `/operate/status`. The detailed status tables, fresh-check action, evidence download, and repair action remain on that page.

The source PNG is edited once. Remove only the outlined `Dwarf` word below the flask. Preserve the flask, corks, eye, grin, ouroboros, circuits, colors, and transparent background.

## Landing status model

The landing page displays four independent states. It does not compress different failures into one green or red result.

1. **Framework** — the dashboard rendered and its internal status endpoint responds.
2. **Control channel** — the restricted host control channel is configured and reachable.
3. **Active topology** — a fresh topology probe reports `healthy`, `unhealthy`, `unknown`, `not deployed`, or `checking`.
4. **Node versions** — the resolved local or mixed deployment uses confirmed immutable versions, or reports `unknown` or `blocked`.

The page starts a fresh read-only health probe. It shows the last result while the new probe runs, but labels it as previous. It shows the result age. It never labels a cached or previous result as current.

## Health reliability

The dashboard currently keeps its last topology result in process memory. A dashboard restart resets the UI to `idle`. Correct this behavior:

- persist each completed dashboard health result under the configured DWARF state directory;
- load the persisted result after a restart;
- retain `checked_at`, evidence path, exact container images, and the classification reason;
- mark a loaded result as cached until a fresh check completes;
- start a fresh check from `/`, `/operate/status`, and the run wizard readiness step;
- keep health checks single-flight;
- do not redeploy automatically;
- keep the existing explicit repair control;
- save the exact preflight result in each run bundle.

Version pinning prevents version drift. It does not replace liveness, progress, convergence, lag, or recovery checks.

## `/run` wizard

The wizard is a launch-plan builder. It does not directly edit a catalog scenario or profile. It uses existing DWARF definitions and the existing scenario engine.

### Step 1 — Scenario

Required. Select one existing scenario. Select the recommended demonstration-safe scenario by default when it is available. Provide filters for implementation, runtime, purpose, threat category, and expected duration. Explain what the scenario tests and what evidence it produces.

### Step 2 — Target and runtime

Required and normally derived from the scenario. Show Cardano-node, Amaru, or mixed topology and the required runtime. Permit only compatible alternatives. Do not present decode targets as node deployments.

### Step 3 — Deployment profile

Conditional. Show this step only for scenarios that require a deployment profile or attached topology. Select the scenario profile or the compatible recommended profile by default.

### Step 4 — Node versions

Conditional. Resolve versions through the selected deployment profile. Select `latest-confirmed` by default. Permit confirmed releases and exact releases from the version catalog. Label unknown combinations and require an explicit acknowledgement. Block unresolved or contradictory combinations.

### Step 5 — Measurements

Optional. Select the applicable default measurement profile. Permit another compatible profile, individual measurement overrides, or `none`. Measurement thresholds do not change the security verdict unless the operator explicitly enables a threshold gate.

### Step 6 — Primitives

Required and read-only in the normal path. Show the scenario's setup, load, fault, probe, assertion, and teardown primitives in execution order. The wizard must not permit arbitrary removal of a producer while retaining a dependent assertion.

An advanced `Customize as a new scenario` action creates a new catalog scenario through the existing structured editor. It does not silently change the selected scenario for one run.

### Step 7 — Run controls

Optional. Show only supported controls, such as seed, iterations, duration, and explicit measurement threshold behavior. Preselect the scenario values.

### Step 8 — Readiness

Required. Validate the scenario schema, primitive registry, primitive runtime support, assertion dependencies, deployment profile, version resolution, measurement compatibility, control channel, writable evidence paths, and scenario-specific topology health.

Do not require a mixed topology to be healthy for a library-only or unrelated single-node scenario. Apply the smallest correct readiness contract for the selected scenario.

### Step 9 — Review and start

Show the exact resolved scenario, profile, versions, source revisions, image digests, measurements, primitive inventory, seed, and readiness evidence. One token-gated action starts the run.

### Step 10 — Progress

Stream the existing scenario command. Show setup, load, fault, probe, assertion, and teardown progress. Link to the retained run report when the run ID becomes available.

## Launch-plan architecture

Add a server-side `RunPlan` resolver. The browser sends identifiers and bounded overrides. The server reloads all referenced catalog records and validates compatibility. The client cannot submit arbitrary commands or paths.

The resolver returns a normalized preview with exact identities and readiness requirements. Start validates the plan again, writes an immutable launch-plan artifact under the configured state directory, and invokes the existing scenario runner.

When the restricted control shim is enabled, add a restricted `launch <launch-id>` verb. The identifier must match a strict pattern. The shim resolves the launch only under the configured launch root. It must not accept a user path or a command string.

The retained run bundle contains the launch plan, the exact scenario snapshot, the resolved profile, version provenance, measurement resolution, primitive inventory, and preflight evidence.

## Error behavior

- Keep the operator on the current wizard step when validation fails.
- Identify the field and the exact conflict.
- Do not convert missing evidence to a healthy result.
- Do not start a scenario after a failed required preflight.
- Provide the existing manual repair action for an unhealthy attached topology.
- Permit the operator to select another scenario that does not require the unhealthy topology.

## Verification

Use test-driven development. Verify data resolution, compatibility failures, restricted launch identifiers, persistence across dashboard restart, fresh-check state labels, and the existing scenario engine boundary. Exercise the complete wizard through the live dashboard with a real smoke-sized local run. Verify desktop, tablet, and mobile layouts. Confirm that existing direct scenario launches, status controls, reports, schedules, Antithesis paths, and catalog editors continue to work.
