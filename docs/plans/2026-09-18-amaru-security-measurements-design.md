# Amaru-first security measurements: design

**Status:** accepted implementation design
**Date:** 2026-09-18
**DWARF baseline:** `25e1ad0867d8ebac8628707c73b388a5654efa21`
**Controlling workbench:** `dwarf-latest / obj_89d14c031cf44235a20e654e`
**Client contract:** `https://gist.github.com/KtorZ/9e5fd34eb993d0bbc5158984e8be5792`

## Outcome

DWARF will gain a reusable, version-qualified **Measurement** catalog that is
independent from Scenario. A scenario continues to define security intent,
workload, faults, and assertions. A measurement definition selects one bounded
observer or node signal, explains its compatibility and overhead, and writes
normalized evidence into the existing run bundle.

The first populated implementation is Amaru-only. It runs real pinned Amaru
production code and real Amaru nodes. It does not substitute a model for the
node. Synthetic inputs may drive the production library or live-node ingress,
and every report distinguishes those two evidence classes.

The client-facing result has two simultaneous views:

1. functional scoreboards for ledger rules, CBOR consistency, Plutus results
   and budgets, header validation, chain selection, and SNAP-boundary stake
   distribution; and
2. distributions and retained samples for Transfer, Block Application,
   Virtual Machine, Epoch Transition, Time to Restart, Sync Speed, header
   Observe/Select/Fetch/Adopt, deep chain-switch, resource cost, degradation,
   and recovery.

No value will be copied from the client example or inferred from a general
performance claim. An unavailable or statistically insufficient result remains
visibly unavailable.

## Audited source baseline

### Exact Amaru target

The newest stable release and the currently confirmed DWARF Amaru-only default
agree at the time of this audit:

| Identity | Value | Authority |
|---|---|---|
| Release | `10.11.20260912` | GitHub releases API |
| Tag | `v10.11.20260912` | `pragma-org/amaru` |
| Source revision | `b159172f25a9c389f82f20bca4f15e3032791638` | tag ref and DWARF version catalog |
| OCI image | `ghcr.io/pragma-org/amaru:v10.11.20260912` | DWARF version catalog |
| OCI digest / local image ID | `sha256:45d46a6ba7147bfa95d96c103820542a9e3ac3602c4c316cc0d04bbd6d71489e` | catalog plus `docker image inspect` |
| Amaru-only qualification | confirmed/default | `dwarf/versions/catalog.json` |
| Supporting producer | Cardano-node `10.7.1` | Amaru-only qualification evidence |

Every measurement result must retain all five executable identities: release,
source revision, image reference, immutable digest, and target mode. The
instrumented variant additionally retains patch digest, build flags, build-log
digest, and built image digest.

### Stock Amaru visibility at the pinned revision

The source at `b159172...` already exposes the following. DWARF must consume it
instead of recreating it:

| Surface | Existing source signal | What it can prove |
|---|---|---|
| Header lifecycle | `amaru_consensus_header_total` and duration histograms; `amaru::consensus::perf::header/lifecycle` | terminal outcome plus slot-start→observe, observe→fetch request, fetch request→receive, observe→adopt/invalidate/abandon |
| Fork switch | `amaru_consensus_fork_switch_duration_microseconds`; `amaru::consensus::perf::fork/switch` | detected fork→applied/abandoned duration and outcome |
| Mempool | insertion counter by origin/result, byte/tx gauges, revalidation duration; received/accepted/rejected/evicted trace events keyed by tx ID | admitted/rejected/duplicate/full, current backlog, revalidation, included-in-adopted-block or invalidated eviction |
| Ledger rule stages | `amaru::ledger::rules/phase_one` optional per-rule microsecond fields and `phase_two` context time | phase-one rule attribution and common phase-two context cost |
| Transaction | `amaru::ledger::transaction/validate` keyed by tx ID | transaction validation span |
| Plutus | `amaru::ledger::transaction::script/execute` with acquire/decode/build/evaluate microseconds | real VM-stage execution timing by redeemer qualifier |
| Block/epoch | block `prepare` and `apply`, block validation-context creation, epoch-transition `apply` including overlay flush | real ledger block and epoch work at existing span boundaries |
| Chain/tip | chain selection events and adopted-tip updates with hashes, heights, slots, tx count | selection/adoption correlation and progress |
| Networking | connection gauges, served-block counter, KeepAlive peer RTT trace, mux protocol/byte events | peer availability, served blocks, RTT, protocol traffic |
| Process | CPU, RSS/footprint/virtual memory, disk read/write, host memory, open files | node-emitted resource series |
| Export | OTLP metrics/traces/logs and JSON traces | live collection without node source changes |

The stock source does **not** expose all requested boundaries. In particular,
the pinned revision does not provide a complete per-message protocol transcript,
exact successful CBOR decode duration for every live protocol surface, every
BlockFetch internal handler/queue boundary, or continuous TxSubmission2
pending/in-flight queue residence. These are the first patch candidates.

### Current DWARF visibility and extension points

DWARF already provides the execution and evidence substrate:

- version-aware profiles and an exact release/digest catalog;
- scenario schema and runner lifecycle;
- primitive registry and additive target manifests;
- run-local `log.ndjson`, normalized `events/`, `metrics/`, `probes/`,
  `outputs/`, manifest, hash chain, automatic SARIF, attestation, and portable
  bundle paths;
- dashboard catalog/detail/editor patterns for scenario, target, and profile;
- run detail, live tail, downloads, and bundle export;
- process/host observers and runtime metric NDJSON hooks;
- real Amaru CBOR/library shims and live Cardano/Amaru topology controls.

Current generic telemetry observes the DWARF controller process and host.
`runtime_resource_profile` targets a node but currently assumes
`cardano-node` process discovery. Existing runtime scripts emit useful ad hoc
metrics, but no typed reusable measurement catalog, compatibility resolver,
distribution aggregator, or client report exists. This is the missing layer.

Linux CPU time, RSS, disk IO, file-descriptor, and thread values are sampled
from the resolved Amaru PID. `/proc/<pid>/net/dev` is a network-namespace
counter, not a per-process byte counter, and must be labeled that way in every
result. It is valid for container/network-namespace cost comparison but not for
claiming that every observed byte was emitted by Amaru itself.

### Existing security surfaces

DWARF already has Amaru target manifests for:

- live-protocol decoders: Handshake, ChainSync, BlockFetch, TxSubmission2,
  KeepAlive, PeerSharing, LocalStateQuery, LocalTxSubmission, and
  LocalTxMonitor; and
- typed CBOR/library surfaces: transaction body, Plutus data, block header,
  certificate, auxiliary data, block, and submit-API transaction.

The existing generative N2N state-machine engine has full models for
ChainSync, BlockFetch, TxSubmission2, and KeepAlive. Handshake and PeerSharing
have useful decode/live fragments but not the same full state-machine claim.
The first four protocol examples therefore use the four full models.

### CDDL, corpus, fuzzing, and coverage boundary

- Cuddle/CDDL supplies structurally valid CBOR seeds and scaffolding.
- DWARF mutation/state-machine engines supply adversarial exploration.
- The pinned `r2rationality/cardano-cbor-dataset` source is
  `a7561cd063550c2218898571520f14c3674efe91`.
- Only Conway `plutus_data` is currently qualified against that dataset's own
  typed verifier. Other advertised rules, including transaction body, cannot
  be promoted until they pass their own qualification.
- Compiler coverage proves production paths executed. It is neither a
  correctness result nor an unbiased latency source.
- Library-tier execution is real production-library execution, but it does
  not prove a live node, mini-protocol, mempool, adoption, or recovery path.

## Architecture

### Catalog objects

#### Measurement

Stored under `dwarf/measurements/<id>.yaml`, validated by
`dwarf/spec/v1/measurement.schema.json`.

Required contract:

```yaml
spec_version: v1
id: amaru-stock-header-lifecycle
title: Amaru header lifecycle
description: Observe stock Amaru header outcomes and timing boundaries.
output_schema: dwarf/spec/v1/measurement-result.schema.json
compatibility:
  implementations: [amaru]
  versions: [10.11.20260912]
  source_revisions: [b159172f25a9c389f82f20bca4f15e3032791638]
  target_modes: [stock]
collection:
  mode: stock-telemetry
  collector: amaru-otel
  capabilities: [amaru.otlp.metrics, amaru.otlp.traces]
  lifecycle: [prepare, start, phase-marker, stop, finalize]
  failure_behavior: continue
default_enabled: true
overhead_class: passive-export
artifacts:
  - metrics/measurements/amaru-stock-header-lifecycle/samples.ndjson
  - metrics/measurements/amaru-stock-header-lifecycle/summary.json
correlation_ids: [run_id, phase, peer_id, header_hash, span_id, trace_id]
threshold_gate:
  supported: true
  default_enabled: false
```

Collection modes are an enum: `external`, `stock-telemetry`,
`compiler-coverage`, and `patched-node`. Failure behavior defaults to
`continue`; `fail-run` is legal only when the launch explicitly enables a
threshold gate.

#### Measurement profile

Stored under `dwarf/measurement-profiles/<id>.yaml`. It is a reusable ordered
selection of measurement IDs plus bounded per-measurement parameters. It does
not contain a scenario and does not alter scenario semantics.

The default Amaru security profile enables compatible passive stock taps and
external resource observation. Patched and coverage taps are explicit because
they change the target build or make performance numbers non-authoritative.

#### Scenario/run selection

The scenario schema gains optional `measurement_profile` and `measurements`
fields. A launch request may override that selection without rewriting the
scenario. Resolution order is:

1. explicit launch selection;
2. explicit scenario selection;
3. scenario measurement profile;
4. compatible default-enabled passive measurements;
5. no optional measurements when the operator selects “none”.

The resolver records the requested, resolved, skipped, incompatible, and
disabled sets. It fails closed on a requested incompatible patch or coverage
build, but passive collector failure remains a measurement error and does not
change the security result unless an explicit threshold gate was enabled.

### Runtime lifecycle

`MeasurementManager` wraps existing collectors without becoming a second
scenario engine:

1. `resolve`: validate target implementation/version/revision/mode and required
   capabilities.
2. `prepare`: create run-local artifact directories and preserve definitions.
3. `start`: connect to the real target after setup has materialized runtime
   metadata.
4. `mark_window`: write baseline, hostile, drain, recovered, and scenario phase
   markers using the run clock.
5. `collect`: stream bounded raw signals and correlation IDs.
6. `stop`: flush exporters and record collector health.
7. `finalize`: normalize samples, calculate distributions, and render the
   aggregate report.

The manager never swallows a security assertion or rewrites scenario outcome.
Its own status is `complete`, `partial`, `unavailable`, `incompatible`, or
`error`. Threshold-gate results are a separate manifest field and are applied
only when the run request explicitly opts in.

### Evidence layout

All files stay inside the existing forensic run bundle:

```text
metrics/measurements/<measurement-id>/
  definition.yaml
  compatibility.json
  collector.json
  raw.ndjson
  samples.ndjson
  summary.json
outputs/measurement-report/
  report.json
  report.md
  compact-table.json
  functional-scoreboards.json
  observer-overhead.json
  transcript-index.json
outputs/protocol-transcripts/<session-id>.ndjson
outputs/coverage/<campaign-id>/...
```

`manifest.json` gains a measurement summary and exact selected definitions.
The bundle exporter, hash chain, attestation, and existing arbitrary-output
download route then cover these artifacts without a new report store.

Raw protocol payload retention is bounded by session, byte count, and
allow-listed metadata. Full DWARF-controlled test-session transcripts retain
message direction, protocol, state, agency, type, length, timestamp,
correlation ID, and bounded payload/hash. Secrets and unrelated traffic are
never retained.

### Normalized result contract

Every timed series contains:

- `sample_count`, `mean`, `median`, `p95`, `p99`, and `units`;
- warm-up policy and warm-up sample count;
- accepted, excluded, and rejected sample counts with reasons;
- dataset/corpus ID and digest, scenario ID/digest, hardware identity, run ID;
- Amaru release, source revision, image/build digest, and target mode;
- measurement definition digest and collector version;
- baseline/hostile/drain/recovered window;
- observer-overhead status where applicable.

Attempt timing is outcome-independent. Every operation that reaches a measured
boundary retains its elapsed time and terminal classification, including
accepted, rejected, malformed, duplicate, timeout, disconnected, and
unclassified attempts. Reports expose the combined attempted population and a
separate latency distribution for each outcome. A rejected operation is never
dropped merely because it did not become goodput; if the relevant boundary was
not observed, the timing is reported as unavailable rather than zero.

The compact table has rows for Transfer, Block Application, Virtual Machine,
Epoch Transition, Time to Restart, and Sync Speed. Each populated value links
to its measurement detail and source artifact. Header lifecycle and deep
chain-switch remain first-class detail panels even though the supplied compact
table does not require those rows.

### Correlation

The normalized envelope supports `input_id`, `tx_id`, `block_hash`,
`header_hash`, `peer_id`, `connection_id`, `protocol`, `message_type`,
`trace_id`, `span_id`, `coverage_campaign_id`, `fault_id`, and `window_id`.
Unavailable identifiers are omitted, never fabricated.

For transaction goodput, DWARF correlates offered input IDs and submitted tx
hashes with Amaru mempool received/accepted/rejected events and later
included-in-adopted-block evictions. A socket write or submit-API response is
not reported as chain adoption.

## Amaru execution variants

### Stock

The authoritative security target and preferred performance source. Enable
only the already supported OpenTelemetry/JSON trace flags and use the pinned
production image digest.

### Compiler coverage

Builds the real production libraries/harnesses with coverage instrumentation.
It contributes path/linkage evidence only. Its latency and throughput samples
are marked `non_authoritative` and excluded from compact performance values.

### Patched measurement build

DWARF owns a minimal patch set tied to the exact source revision. The builder:

1. refuses a dirty source or mismatched `HEAD`;
2. verifies the expected patch digest;
3. applies with zero fuzz/offset tolerance;
4. builds with recorded toolchain and flags;
5. retains stdout/stderr and resulting executable/image digests; and
6. labels the result as `patched-node`.

Initial patch scope:

- successful/failed live protocol CBOR decode duration where stock spans do
  not expose it;
- BlockFetch handler, decode, queue depth, and queue residence boundaries;
- TxSubmission2 pending/in-flight depth and request→body→insertion residence;
- scheduling/mailbox/lock attribution only if the first paired results leave a
  specific client metric unexplained.

Each patched performance workload runs against stock and patched builds from
the same source revision. Observer overhead is reported per metric. A behavior
seen only in the patched build cannot be classified as an Amaru vulnerability.

## Five security examples

All five are additive definitions and preserve existing scenarios.

1. **ChainSync state/agency and rollback churn** — real Amaru node, generated
   illegal state/agency sequences plus controlled intersections/rollbacks;
   attach stock header/fork, resource, liveness, transcript, and optional
   patched decode taps.
2. **BlockFetch range/pipeline pressure** — invalid ranges, pipeline pressure,
   withholding/stalls, and malformed blocks against real Amaru; attach stock
   header/fetch, resource, recovery, transcript, and patched handler/queue taps.
3. **TxSubmission2 admission/backpressure** — invalid, duplicate, and
   body-withholding pressure; correlate offered inputs through mempool outcome
   and adopted-block eviction; attach stock mempool, resource, recovery,
   transcript, and patched in-flight/residence taps.
4. **KeepAlive timing/state abuse** — illegal timing/state behavior on one
   tested peer while an unrelated peer remains usable; attach RTT, connection,
   resource, liveness/recovery, and transcript taps.
5. **CBOR/Plutus corpus exploration** — Cuddle seeds, only the qualified
   Cardano dataset Plutus-data surface, DWARF mutation/cargo-fuzz coverage, and
   replay through an applicable real Amaru ingress. Report library coverage and
   live-node evidence separately.

Each example documents purpose, dataset/seed, version/mode, surface, launch
instructions, functional and non-functional expectations, security
assertions, attached measurements, actual retained report, legitimate claims,
and explicit non-claims.

## Frontend

Add `/operate/measurements` as a peer to Scenarios, Primitives, Targets, and
Profiles:

- filterable catalog/list and deterministic export;
- detail with compatibility, mode, overhead, lifecycle, artifacts, and source;
- structured create/edit with raw JSON/YAML escape hatch;
- import/export and server-side validation;
- version/mode compatibility explanation;
- measurement-profile selection and individual overrides in scenario/run
  workflows;
- live collector states; and
- run-result compact table, functional scoreboards, distributions, goodput,
  resources, degradation, recovery, transcript, coverage, provenance, and
  overhead warning.

Add `/learn/measurements` with concepts, schema, stock/coverage/patched modes,
CDDL/corpus/fuzz/coverage relationships, all five examples, interpretation,
troubleshooting, and claim boundaries. Builders and result pages link directly
to the relevant sections.

## Security result and measurement result remain separate

The scenario's assertions remain the authority for containment, liveness,
recovery, crashes, resource exhaustion, and divergence. Measurements explain
what happened and how much it cost. By default:

- a collector failure does not fail the scenario;
- a slow or degraded measurement does not fail the scenario;
- an unavailable internal boundary is shown as unavailable; and
- only an explicitly enabled threshold gate may convert a named measurement
  condition into a gate result.

## Legitimate claims

After complete proof, DWARF may claim that it:

- ran the exact retained Amaru production or instrumented build;
- delivered the retained workload to the intended real target;
- observed named stock or patched boundaries and calculated distributions from
  retained samples;
- correlated offered work with the node outcomes actually exposed;
- measured degradation and recovery across explicitly marked windows; and
- exercised the production paths shown by the retained coverage build.

It may not claim:

- unobserved internal latency or contention;
- global Amaru/Cardano performance from one host/run/dataset;
- Cardano-node or mixed parity during this Amaru-only delivery;
- correctness from code coverage;
- live-node behavior from library-only execution;
- chain adoption from submission success or mempool admission; or
- an Amaru vulnerability based only on an instrumented build.

## Completion gate

Completion requires all catalog/runtime/UI/documentation work plus exact local
GUI execution of all five examples on `cardano-box`, fresh state where
applicable, retained evidence proving target reachability and non-vacuous taps,
representative regression tests, and two screenshot-led desktop/mobile review
cycles. Static validation, startup, telemetry presence, or direct scripts are
not substitutes for those five GUI-run proofs.
