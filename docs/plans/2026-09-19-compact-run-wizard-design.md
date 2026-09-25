# Compact Multi-Backend Run Wizard Design

## Objective

Make `/run` compact, understandable, and operationally honest. The wizard must guide a user from a catalogued scenario to a supported execution path without implying that Local, GitHub Actions, and Antithesis have identical capabilities or approval rules.

The landing-page DWARF wordmark and monster image will also be reduced to approximately half of their current visual size. The header logo is unchanged.

## Design principles

- Keep native browser controls and keyboard navigation.
- Keep every field inside the page width on desktop, tablet, and mobile.
- Prefer catalog facts and runtime checks over claims in display text.
- Never show an editable control that does not affect execution.
- Preserve exact resolved inputs and preflight evidence.
- Preserve the existing scenario, profile, version, measurement, run, and evidence contracts.
- Keep paid Antithesis submission behind its separate explicit confirmation.

## Wizard structure

The wizard has nine compact, standardized sections:

1. Scenario
2. Target
3. Profile
4. Versions
5. Measurements
6. Execution
7. Settings
8. Readiness
9. Review and start

The read-only Primitives card is removed. Primitive inventory remains present in the resolved technical plan and retained launch evidence. The cards use smaller padding, headings, control heights, and gaps. Controls use bounded widths and wrap safely.

## Scenario and profile labels

The existing scenario default remains selected. Profile options receive stable text prefixes:

- `DEFAULT · CONFIRMED` when the profile is the scenario default and its resolved version claim is confirmed.
- `CONFIRMED` when the resolved profile claim is confirmed.
- No prefix when the profile is unconfirmed or has not been tested.

The selected profile summary also shows a colored status badge. Native `<option>` color support is inconsistent, so status is communicated in both option text and the adjacent badge.

## Execution paths

### Local

Local execution uses the real DWARF engine. The launch becomes a server-owned background job so browser navigation cannot terminate it. After the run identifier is available, the browser goes to `/operate/runs/<run-id>/live`. The completed run remains available through the normal inspector.

### GitHub Actions

GitHub Actions is selectable only when the scenario maps to a real manual workflow:

- Supported and confirmed: the workflow mapping exists and retained evidence confirms the path.
- Supported but unconfirmed: the mapping exists, but this deployment has no retained confirmation.
- Unsupported: no workflow preserves the selected scenario semantics.

Dispatch requires configured repository credentials. A successful dispatch links to the GitHub Actions run. A general library-fuzz workflow must not be presented as execution of one selected scenario when that is not what it does.

### Antithesis

Antithesis support is derived from the real generator and validation contract for the selected scenario. Unsupported scenarios are disabled with the generator reason. Supported scenarios can generate and preflight a bundle, then hand off to the existing Antithesis page with the selection preserved. The wizard does not bypass bundle publication, Moog readiness, or the explicit paid live-submit confirmation.

## Settings

The seed is editable. Accepted values are a bounded unsigned decimal integer or `0x` hexadecimal value. An empty value uses the scenario default. The effective seed is materialized into the immutable launch scenario and retained evidence.

The current top-level `iterations` field is not editable because the execution engine validates and records it but does not consume it. The UI states this limitation instead of offering an inert control. Workload counts that belong to primitive parameters remain part of the scenario definition.

The wizard shows a runtime estimate only when retained matching runs provide evidence. It labels the estimate as historical, includes the sample count, and does not invent an estimate when matching evidence is absent.

## Readiness

Resolution and readiness remain separate:

- Resolution proves the selected identifiers form a valid immutable plan.
- Readiness checks the real runtime surfaces needed for that plan.

The section shows:

- `CHECKING` in amber during a real check.
- `READY` in glowing green when all required checks pass.
- `NOT READY` in glowing red when any required check fails.

Individual checks show their result and reason. Start stays disabled until readiness is `READY`, and start repeats the preflight to close the time-of-check/time-of-use gap.

When the required mixed topology is unhealthy or unknown, the existing evidence-preserving redeploy workflow is available inside step 8. Wizard selections are saved in session storage before redeploy and restored afterward. The redeploy remains a separately confirmed destructive action.

## Review and start

Step 9 shows a short human summary followed by expandable technical views:

- Effective scenario YAML (the JSON-structured YAML that will execute).
- Resolved launch-plan JSON.
- Primitive inventory inside the technical plan.

No secret values are included.

The main action changes with the selected backend:

- `Start local run`
- `Dispatch GitHub workflow`
- `Prepare Antithesis run`

Unsupported paths cannot start. Supported-but-unconfirmed paths show a visible warning before action.

## Background local launch

The server owns the subprocess and records launch state below the private launch directory. The start endpoint returns a launch identifier after final preflight and process creation. A read-only launch-status endpoint reports state, output, run identifier, and exit code. The browser polls until a run identifier exists, then opens the existing live-run route. A page close does not kill the job.

Only one mutating action can own the existing mutation lock. The background worker releases it after completion or launch failure. Launch status remains bounded and contains no credentials.

## Compatibility and evidence

- Existing `/api/run/resolve` clients remain valid.
- Existing run routes, downloads, manifests, measurements, and evidence remain unchanged.
- New override and execution fields are validated server-side and included in the plan digest.
- The launch snapshot retains the effective scenario, resolved plan, and final preflight.
- Existing scenarios are not modified.

## Verification

Implementation follows test-driven development. Acceptance includes:

- Unit tests for seed validation, materialization, profile qualification labels, backend capability classification, duration evidence, readiness, and background launch state.
- Route and presentation-contract tests for all nine sections and removal of the Primitives card.
- Regression tests for existing run resolution and evidence links.
- Real local launch through `/run`, with redirect to live logs and a completed evidence bundle.
- Desktop, tablet, and mobile browser screenshots with no page-level horizontal overflow.
- Landing-page visual verification at the reduced sizes.
- GitHub and Antithesis paths verified without bypassing their external authorization and confirmation gates.

