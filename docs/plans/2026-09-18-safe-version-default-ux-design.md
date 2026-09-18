# Safe Version Defaults and Version-Aware UX Design

## Objective

Make `latest-confirmed` the fail-safe default everywhere DWARF accepts a
deployment profile, expose version qualification from the Operate and Learn
landing pages, and make exact runtime identity visible before and after a run.
No omitted profile policy may fall through to an ambient host binary, mutable
image tag, or locally changing source tree.

## Decisions

### One version authority

The deployment profile is the single authority for runtime node versions.
Scenarios select a profile and inherit its resolved runtime identity. Targets
identify a test surface and implementation; they do not select the node binary
used by a scenario run. Scenario and target pages disclose this boundary and
link to the profile/version UI instead of introducing competing selectors.

### Safe implicit policy

An absent or blank `version_policy` is normalized to `latest-confirmed`.
Resolution records whether that value was explicit or supplied by DWARF as the
safe implicit default. `latest-confirmed` continues to use the checked-in,
evidence-backed default and never follows release discovery. `latest-stable`
and `exact` remain deliberate operator choices; unknown selections require a
one-run acknowledgement and incompatible or blocked selections cannot launch.

### Version choice does not choose topology

The profile's existing fields continue to determine its deployment adapter and
topology class: generated/local Cardano, mixed, Amaru target, Preview/Preview2,
Preprod, closed devnet, or consensus threshold. Version resolution supplies
immutable artifact identity to that adapter. It must not route a profile to a
different adapter merely because the profile now has a version policy.

Built-in profiles are migrated individually. Local Cardano, Amaru-target, and
mixed profiles use the corresponding evidence-backed defaults. Attached public
network profiles receive an immutable selection but do not inherit a false
claim that a local-devnet qualification proves their public-network contract;
that deployment context remains explicitly unqualified and acknowledgement-
gated until direct evidence exists.

### Current defaults

- Cardano-only: Cardano-node `11.1.2`.
- Amaru target: Amaru `10.11.20260912` with supporting Cardano-node `10.7.1`.
- Mixed: Cardano-node `10.7.1` with Amaru `10.11.0`.

## Operator experience

### Landing pages

`/operate` gains a Node versions tile showing the three current default
selections and linking to `/operate/versions`. `/learn` gains a
Version-qualified devnets tile explaining policy/status semantics and linking
to `/learn/versions`. Both reuse the existing tile systems and responsive
styles.

### Profile builder

The structured editor presents a dedicated Node versions section before
advanced deployment fields. `latest-confirmed` is selected by default for every
creation template. The section shows a live, read-only resolution preview with
scope, exact releases, supporting Cardano release, source revisions, immutable
digests, compatibility status, catalog revision, reason, evidence, related
issues, and acknowledgement/blocking state.

Only relevant exact selectors are visible:

- Cardano-only: Cardano-node release.
- Amaru target: Amaru release and disclosed supporting Cardano release.
- Mixed: compatibility pair, with both exact component releases.

Raw JSON/YAML remains available as an advanced editing mode and is subject to
the same server-side resolver and gate.

### Scenarios, targets, previews, and runs

Scenario editing shows the selected profile and its current exact resolution.
Legacy `target.version` remains descriptive target metadata and is labelled as
non-authoritative for deployment. Target pages explain that deployed node
identity comes from the profile. Deployment preview exposes explicit versus
implicit policy and the complete immutable resolution. Run evidence preserves
the effective catalog snapshot and exact identity already shown by the run
inspector.

## Failure handling

- Catalog-resolution failure blocks preview and deployment with a visible
  reason.
- Unknown public-network or release context requires a one-run acknowledgement.
- Incompatible and blocked records cannot be acknowledged around.
- Official-source discovery failure remains visible but does not invalidate
  retained checked-in defaults.
- Existing topology/configuration inputs are never discarded by version
  resolution.

## Verification strategy

Implementation is test-driven. Regression tests first prove the missing
landing cards and unsafe omitted-policy behavior. Further tests cover every
entry point, adapter preservation, conditional editor fields, scenario/target
authority disclosure, gating, and immutable provenance. Runtime verification
uses fresh roots/volumes through DWARF for representative Cardano-only,
Amaru-target, and mixed deployments without touching the retained control.
Public-network adapters are exercised only within safe bounds and are not
called fully qualified without real progress evidence. Two desktop/mobile
Playwright review cycles cover the complete affected surface before deployment.

