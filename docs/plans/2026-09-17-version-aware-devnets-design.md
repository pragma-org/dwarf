# Version-aware Real-node Devnets Design

## Goal

Make Cardano-only, Amaru-only, and mixed Cardano/Amaru deployments explicit
about the exact node versions they use, whether those versions have actually
passed the required runtime contract, and why a particular release or release
pair is the default.

DWARF must continue to run real node implementations. This work adds version
selection, qualification, and evidence; it does not add simulation or replace
the existing scenario engine.

## Source-of-truth model

Version knowledge lives in one checked-in catalog rather than being copied
into every profile. The catalog records:

- released and experimental versions for Cardano-node and Amaru;
- immutable source revisions and image digests where artifacts exist;
- artifact availability and acquisition provenance;
- scope-specific verification records for `cardano-only`, `amaru-only`, and
  `mixed` deployments;
- compatible Cardano/Amaru pairs, including the topology revision and evidence
  that established compatibility; and
- known blockers, incompatibilities, upstream issues, and the date each fact
  was checked.

`default` is an explicit policy choice, not a synonym for `confirmed`.
Verification status is one of `confirmed`, `unknown`, `incompatible`, or
`blocked`, and is always scoped to a deployment type. A release confirmed for
a Cardano-only devnet is not thereby confirmed for a mixed topology.

The checked-in catalog is deterministic input to a run. A separate refresh
command discovers current GitHub releases, source revisions, and registry
metadata and writes a reviewable candidate snapshot. Merely discovering a
release never marks it confirmed.

## Profile selection model

Profiles select versions through one of three policies:

- `latest-confirmed`: resolve the catalog's current default for the profile's
  deployment scope;
- `latest-stable`: resolve the newest stable release even when it remains
  unqualified, with an explicit warning and confirmation gate; or
- `exact`: select an exact release or mixed compatibility pair.

The out-of-box defaults use `latest-confirmed`. This means a new stable release
can appear in the selector without silently replacing a working deployment.
Experimental `main`/nightly revisions remain selectable only through an exact,
explicit choice and are never automatic defaults.

For mixed deployments, the unit of selection is a compatibility pair. The
profile does not independently choose two versions and then imply they are
compatible. An exact unlisted pair is `unknown`; a listed rejected pair is
`incompatible`; a pair with an external prerequisite that prevents a valid
test is `blocked`.

## Qualification contract

Candidates are attempted newest-to-oldest independently for:

1. Cardano-only devnets;
2. Amaru-only devnets; and
3. mixed Cardano/Amaru devnets.

Each attempt uses a unique project and fresh volumes. It records the DWARF
revision, upstream topology/source revisions, requested versions, resolved
source revisions, immutable image IDs/digests, configuration/genesis hashes,
timestamps, logs, health samples, chain tips, and the pass/fail reason.

The existing healthy pre-staged mixed deployment is not replaced while
qualifying candidates. A candidate becomes `confirmed` only when its exact
runtime contract passes end to end:

- every expected real process/container starts;
- node-reported identity matches the requested version;
- genesis, network magic, era history, and topology agree;
- producer progress is real and sustained;
- expected peer sessions form;
- the Amaru-fed consumer advances from Amaru's path rather than an alternate
  Cardano peer;
- the observation interval completes without panic, fatal termination, or
  restart loop; and
- teardown succeeds without affecting the retained known-good deployment.

An Amaru-only lane must state what it actually proves. If the available Amaru
release cannot independently forge a fresh chain, the lane is recorded as an
Amaru relay/consumer qualification with an external honest source; it is not
mislabelled as standalone block production.

Failures are classified only after checking current Amaru documentation/wiki,
source, issues and pull requests, Cardano-node source/issues/releases, the
cardano-node-antithesis repository and reports, and the applicable
`dwarf-latest` and `moog` workbenches. A known upstream constraint is retained
as a classified background signal rather than presented as a new finding.

## Runtime enforcement and evidence

Version resolution must inspect the real artifact. Docker mode may not infer
that `dwarf/<implementation>:<version>` exists from its name. It must inspect
or pull the exact configured reference, capture the immutable image identity,
and verify the node's reported version after launch.

Deployment preview shows the policy, resolved version(s), status, source
revision, image digest, compatibility record, and warning/block reason. An
`unknown` stable selection requires an explicit one-run acknowledgement.
`incompatible` and `blocked` selections cannot deploy. The acknowledgement is
recorded in the run evidence and does not mutate the catalog.

Run artifacts and bundles retain both the requested policy and exact resolved
identities so that a historical run remains reproducible after defaults move.

## Dashboard and documentation

The profile list, profile detail, structured builder, raw editor, preview, and
run views expose the same version semantics. Status badges use distinct text
in addition to color. Field help explains the difference between stable,
confirmed, compatible, and default.

An Operate version catalog gives operators a filterable view of releases,
compatibility pairs, evidence, and blockers. A matching Learn page documents
policy resolution, status meaning, qualification gates, refresh workflow, and
examples for Cardano-only, Amaru-only, and mixed profiles.

Desktop and mobile renderings are verified in two review cycles. Existing
profiles without version-policy fields retain their prior behavior through a
documented legacy resolution path; they are not silently rewritten.

## Initial evidence posture

The existing healthy pre-staged mixed topology remains the retained known-good
operational fallback. Its exact image identities and evidence are imported as
historical catalog facts only where the retained evidence proves them.

The current upstream `cardano_amaru` topology is the starting mixed candidate
because it preserves the required bootstrap producer, private stores, startup
markers, isolated Amaru-fed consumer, and fault-exclusion contracts. Its
release-sensitive Cardano `10.7.1` pin is treated as intentional evidence, not
as a version to blindly replace.

Current newest candidates at the start of qualification are Cardano-node
`11.1.2` and Amaru `v10.11.20260912`. They begin as `unknown`; release recency
alone is not runtime proof.

## Non-goals

- No Antithesis or Moog submission.
- No public push.
- No replacement of the existing healthy topology before a candidate passes.
- No claim that a release is compatible based only on successful image build,
  Compose rendering, container startup, or static validation.
- No node-source changes solely to implement version selection.
