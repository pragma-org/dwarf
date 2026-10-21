# GitHub Campaign Fan-Out Design

**Date:** 2026-10-21

## Goal

Replace the single long GitHub-hosted campaign job with isolated runner jobs so
independent DWARF workloads execute in parallel, retain separate failure
evidence, and still publish one verified campaign result.

## Current problem

The candidate is built once, but one campaign job then performs calibration,
security controls, security scenarios, measurement controls, measurement
scenarios, and stock replays in sequence. The failed run spent 50 minutes on
the candidate build and another 1 hour 46 minutes in this serial campaign job.
It did not hit the six-hour job timeout. It ended with `harness-invalid` and
incomplete measurements.

The serial layout makes one harness problem delay every later workload. It also
hides useful completed evidence until the entire job ends.

## Selected architecture

The workflow has six stages:

1. **Discover** validates changed candidates and freezes trusted inputs.
2. **Build** compiles stock and compatible patched candidates once. It retains
   immutable OCI archives, binary hashes, dependency identities, and logs.
3. **Prepare** validates the build transfer, freezes the campaign manifests,
   builds the trusted controller once, and emits an immutable preparation
   artifact.
4. **Calibrate** uses two isolated matrix jobs for stock and patched variants.
   A selector verifies both retained results and records the primary execution
   mode. A missing or failed patched lane selects stock; stock calibration must
   succeed.
5. **Execute** expands the selected manifest into one matrix job per unique
   workload. It reuses the existing local scenario, measurement and bundle
   commands. Work runs in three dependent phases: Amaru solo, Cardano solo, then
   mixed. Independent items inside a phase run in parallel. Security entries,
   measurement recipes not already exercised by a selected security entry, and
   required stock security replays are separate work items. Each runner restores
   the exact prepared controller and candidate artifacts, runs only its assigned
   local work item, and uploads its complete or failed checkpoint plus canonical
   run archives.
6. **Aggregate and publish** downloads every shard, verifies its campaign,
   build, work-item, archive, and checkpoint identities, rejects duplicate or
   missing work items, merges the evidence, evaluates the full frozen manifest,
   and passes only a complete combined result to the existing publication job.

Each phase matrix uses `fail-fast: false`. One runner failure therefore does not
cancel sibling evidence-producing jobs. A later topology phase waits for the
preceding phase to finish and preserves its failure without suppressing independent
later evidence. The aggregation job always runs after the phase jobs,
but publication still requires a valid complete campaign result.

## Work-item model

A work item is a small immutable JSON object derived only from the frozen
campaign manifest:

- `calibration:<mode>` for stock or patched calibration;
- `security:<entry-id>` for one primary security entry;
- `measurement:<topology>:<node>:<scenario>:<mode>` for one grouped measurement
  recipe not covered by a selected security execution; or
- `stock-replay:<entry-id>` for one required stock replay of a patched-primary
  security entry.

Each object includes the full campaign digest and a digest of its own canonical
body. The runner accepts only a work item present in the prepared matrix. It
cannot select arbitrary candidate commands, paths, scenarios, or images.

A security work item retains its normal control and the tested scenario in the
same runner because their evidence must share the frozen execution contract.
A grouped measurement work item similarly retains its control and measured run
in the same runner. This removes cross-runner control ambiguity.

## Measurement coverage

The planner computes measurement work from the frozen required tap cells. If a
security work item executes the same topology, scenario, target node, and mode,
its attached measurement cells belong to that security work item. Remaining
implemented cells are grouped by their executable recipe and receive their own
runner. Missing measurement runtimes remain explicit and make the aggregate
campaign incomplete; they are never silently omitted.

This design changes scheduling only. It does not treat confirmed node versions
as restrictions, change measurement definitions, or weaken evidence predicates.

## Artifact and trust boundaries

Candidate source remains data until the trusted build executor processes it.
All runner jobs use read-only repository authority. Candidate builds and the
trusted controller are produced once and transferred as immutable artifacts.
Every runner validates hashes and image identities after restoration.

Job-local Docker registries remain isolated. A runner loads the retained OCI
archive, tags it into its local registry, and confirms the same manifest and
configuration identities recorded by preparation. No mutable registry tag is
an authority.

Each runner creates an ephemeral integrity key for its own run archives. The
aggregator validates the archive bytes and embedded signatures before combining
evidence. The existing publication key remains available only to the isolated
publication job.

## Failure behavior

- A build failure stops calibration and execution because no candidate identity
  exists.
- Failed patched calibration selects stock when stock calibration is valid.
- Failed stock calibration blocks execution.
- A workload failure produces a retained shard marked failed or incomplete;
  sibling jobs continue.
- Missing, duplicate, conflicting, or identity-mismatched shards make
  aggregation `harness-invalid`.
- Publication runs only when the combined campaign satisfies the existing
  readiness, cleanup, security-authority, and measurement-completeness gates.

## Runtime and cost controls

The candidate and trusted controller are each built once. Matrix jobs are
bounded to one work item and have shorter timeouts. `max-parallel` limits peak
hosted-runner use while still reducing wall-clock time. The initial value is six,
which matches the automatic security selection and can be adjusted after
retained timing evidence.

The first implementation preserves the current selected security and
measurement contracts. Reducing the automatic suite to a smaller release gate
is a separate policy change and is not required for fan-out.

## Acceptance criteria

- The workflow contains distinct prepare, calibration, selection, execution,
  aggregation, and publication jobs.
- Candidate and controller build steps occur once, outside the execution matrix.
- Every matrix job accepts exactly one manifest-derived work item.
- Phase dependencies enforce Amaru solo, Cardano solo, then mixed.
- A failed matrix job does not cancel sibling jobs.
- Aggregation detects missing, duplicate, conflicting, or altered shard data.
- The aggregate state remains compatible with the current publication verifier.
- Focused unit and workflow-contract tests pass without launching an Actions run.
