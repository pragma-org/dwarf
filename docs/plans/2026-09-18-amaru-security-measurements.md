# Amaru-first security measurements: implementation plan

> Execute this plan in the authoritative `cardano-box` DWARF checkout, directly on
> `main`, with no worktree. Use test-driven development, review every batch,
> and push only verified batches to the `internal` remote. Never push publicly
> and never launch Antithesis/Moog.

**Design:** `docs/plans/2026-09-18-amaru-security-measurements-design.md`
**Workbench:** `dwarf-latest / obj_89d14c031cf44235a20e654e`
**Starting commit:** `25e1ad0867d8ebac8628707c73b388a5654efa21`

## Implementation status

- Batch 0 — complete at `bfd7fe0`; exact Amaru source and signal audit pinned.
- Batch 1 — complete at `74d2b50`; measurement and measurement-profile
  catalogs, schemas, editors, and pinned definitions added.
- Batch 2 — complete at `c44d5a1`; scenario selection, compatibility
  resolution, and evidence retention added.
- Batch 3 — complete at `f3124b5`; lifecycle isolation, real phase
  windows, opt-in gates, deterministic distributions/rates/backlog/recovery,
  normalized bundle reports, multi-identifier correlation, and bounded/redacted
  full protocol transcripts are covered by the regression suite.
- Batch 4 — complete locally on 2026-09-18; stock JSON/OTLP normalization,
  outcome-independent attempt timing, workload/restart/sync collectors, and
  actual Amaru process resource sampling are implemented. The exact pinned
  `10.11.20260912` / `b159172...` topology passed all qualification gates, and
  the component proof sampled the real `/target/amaru` PID with no collector
  errors. The established `tests/` regression suite passes 715 tests.
- Batch 5 — complete locally on 2026-09-18; the fail-closed revision-locked
  builder, audited decode/BlockFetch/TxSubmission2 probes, fresh stock and
  patched topologies, strict common-metric calibration, and bounded
  outcome-independent transcripts are proven. Batch 6 is next.
- Internal push — queued because the rebooted host has no usable credential for
  the unrelated-history internal remote. No public push has been attempted.

## Batch 0 — pin the audit and executable requirements

### Task 0.1: Add source-contract tests

**Files**

- Create `tests/test_amaru_measurement_source_contract.py`
- Create `dwarf/measurements/audit/amaru-10.11.20260912.json`

**Red**

Write tests requiring the audit record to contain the exact Amaru release,
source revision, image digest, build mode, stock signal inventory, identified
visibility gaps, DWARF baseline commit, Cuddle boundary, dataset pin, and
qualified-surface limitation. Assert that every stock signal names its source
file and that every proposed patch names a missing signal rather than a
duplicate.

Run:

```bash
python3 -m pytest -q tests/test_amaru_measurement_source_contract.py
```

Expected: fail because the audit record does not exist.

**Green**

Add the audited JSON record and make the focused test pass. Compare the release
record against `dwarf/versions/catalog.json` rather than copying an unverified
value into test code.

**Review and verify**

```bash
python3 -m pytest -q tests/test_amaru_measurement_source_contract.py tests/test_version_catalog.py tests/test_version_docs.py
git diff --check
git status --short
```

Commit and push this verified documentation/audit batch to `internal`.

## Batch 1 — first-class Measurement and Measurement Profile catalogs

### Task 1.1: Define schemas and loader

**Files**

- Create `dwarf/spec/v1/measurement.schema.json`
- Create `dwarf/spec/v1/measurement-profile.schema.json`
- Create `dwarf/spec/v1/measurement-result.schema.json`
- Create `dwarf/profile_manager/measurements.py`
- Modify `dwarf/profile_manager/data/catalog_definitions.py`
- Modify `dwarf/profile_manager/views/operate_definition.py`
- Modify `dwarf/profile_manager/views/operate_definition_edit.py`
- Create `tests/test_measurement_catalog.py`

**Red**

Test valid/invalid IDs, all four collection modes, exact compatibility fields,
capabilities, default state, overhead, lifecycle, failure behavior, artifacts,
correlation IDs, and threshold-gate default. Test deterministic list/load/save,
safe paths, import/export, and profile reference validation. Test that
`fail-run` is rejected unless threshold gating is explicitly supported.

**Green**

Implement catalog loading and generic editor support. Keep existing scenario,
target, and profile behavior byte-for-byte compatible.

### Task 1.2: Seed the Amaru catalog

**Files**

- Create additive definitions under `dwarf/measurements/`
- Create `dwarf/measurement-profiles/amaru-security-default.yaml`
- Create `dwarf/measurement-profiles/amaru-security-patched.yaml`
- Extend `tests/test_measurement_catalog.py`

Initial definitions:

- `amaru-stock-header-lifecycle`
- `amaru-stock-fork-switch`
- `amaru-stock-mempool`
- `amaru-stock-ledger-rules`
- `amaru-stock-plutus-execution`
- `amaru-stock-block-epoch`
- `amaru-stock-network`
- `amaru-stock-resources`
- `amaru-external-restart-readiness`
- `amaru-external-sync-speed`
- `amaru-external-workload-accounting`
- `amaru-coverage-production-paths`
- `amaru-patched-protocol-decode`
- `amaru-patched-blockfetch-queues`
- `amaru-patched-txsubmission-residence`

Every definition is pinned to `10.11.20260912` / `b159172...`; patched and
coverage definitions are not default-enabled.

**Batch gate**

Run focused tests, existing definition-catalog tests, scenario validation
tests, `git diff --check`, and inspect every new definition. Commit/push only
after the suite passes.

## Batch 2 — scenario/run selection and compatibility resolution

### Task 2.1: Extend the scenario contract without changing old scenarios

**Files**

- Modify `dwarf/spec/v1/schema.json`
- Modify `dwarf/spec/v1/schema.yaml`
- Modify `dwarf/spec/v1/README.md`
- Modify `dwarf/profile_manager/scenario.py`
- Create `tests/test_scenario_measurements.py`

**Red**

Test optional `measurement_profile` and `measurements`, individual enable/
disable, bounded parameters, threshold opt-in, duplicate rejection, unknown ID,
and old scenario compatibility.

**Green**

Parse immutable measurement selections into the Scenario model. Do not start a
collector yet.

### Task 2.2: Add version/mode capability resolver

**Files**

- Create `dwarf/profile_manager/measurement_resolution.py`
- Modify `dwarf/profile_manager/deployment_versions.py`
- Create `tests/test_measurement_resolution.py`

**Red**

Cover exact version/revision/digest, stock/coverage/patched mode, required
capabilities, explicit launch override, scenario/profile/default resolution,
“none”, skipped defaults, and human-readable incompatibility reasons.

**Green**

Return requested/resolved/skipped/incompatible/disabled entries with immutable
definition digests. Explicit incompatible requests fail before execution;
incompatible defaults are skipped and explained.

### Task 2.3: Preserve selections in run evidence

**Files**

- Modify `dwarf/profile_manager/forensic.py`
- Create `tests/test_measurement_manifest.py`

Test preservation of definitions, resolver result, collector states, gate
configuration, and exact target identity in manifest/evidence. Ensure old runs
still render.

**Batch gate**

Run all new tests plus `tests/test_definition_catalogs.py`,
`tests/test_profile_version_resolution.py`, scenario tests, forensic/bundle
tests, and representative existing scenarios in dry/local library mode.

## Batch 3 — measurement runtime and normalized reporting

### Task 3.1: Add lifecycle manager and collector interface

**Files**

- Create `dwarf/profile_manager/measurement_runtime.py`
- Create `dwarf/profile_manager/measurement_collectors/__init__.py`
- Modify `dwarf/profile_manager/scenario.py`
- Create `tests/test_measurement_runtime.py`

**Red**

Use fake collectors to prove resolve→prepare→start→phase markers→stop→finalize,
bounded artifact paths, collector error isolation, explicit threshold gating,
teardown on runner exceptions, and that scenario assertions remain
authoritative by default.

**Green**

Integrate the manager after topology preflight and around actual phases. Emit
run-clock window markers and preserve collector errors without hiding them.

### Task 3.2: Add distribution and window aggregation

**Files**

- Create `dwarf/profile_manager/measurement_report.py`
- Create `tests/test_measurement_report.py`

**Red**

Test sample count, mean, median, nearest-rank p95/p99, units, warm-up exclusion,
rejection reasons, unavailable states, throughput rates, peak/backlog growth,
drain/recovery time, baseline-versus-hostile degradation, and insufficient
sample handling. Include fixed golden samples.

**Green**

Write normalized `summary.json`, aggregate `report.json`, readable report, and
compact table into the existing run bundle. Never substitute zero for missing.

### Task 3.3: Add correlation and bounded transcript writer

**Files**

- Create `dwarf/profile_manager/measurement_correlation.py`
- Create `dwarf/profile_manager/protocol_transcript.py`
- Create `tests/test_measurement_correlation.py`
- Create `tests/test_protocol_transcript.py`

Test transaction/header/block/peer/trace/span/fault/window joins, missing-ID
behavior, session and byte limits, payload hashes, truncation records, and
redaction.

**Batch gate**

Run the focused suite plus scenario, forensic, bundle export, SARIF, and
attestation regression tests. Inspect a generated synthetic test bundle before
commit/push.

## Batch 4 — stock Amaru collectors and real target resources

### Task 4.1: Collect stock OTLP/JSON signals

**Files**

- Create `dwarf/profile_manager/measurement_collectors/amaru_stock.py`
- Create `dwarf/scripts/runtime_amaru_measurement_collector.py`
- Add an additive collector configuration under `dwarf/assets/measurements/`
- Create `tests/test_amaru_stock_collector.py`

Build fixtures from the exact `b159172...` trace/metric schemas. Test header,
fork, mempool, ledger, Plutus, block/epoch, connection, KeepAlive, mux, and
resource normalization plus trace/span correlation and incomplete export.

For every attempt-level measurement, retain elapsed time regardless of terminal
outcome and render combined plus per-outcome distributions. Accepted,
rejected, malformed, duplicate, timeout, disconnected, and unclassified
attempts must remain visible; never discard their timing because they are not
goodput.

Do not run a general monitoring stack as a new report store. The collector may
receive OTLP and/or bounded JSON traces, but writes the selected normalized and
raw evidence into the DWARF run bundle.

### Task 4.2: Generalize node resource sampling to Amaru

**Files**

- Modify `dwarf/scripts/runtime_resource_profile.py`
- Modify its primitive schema only if required
- Create `tests/test_runtime_resource_profile_amaru.py`

Test Docker PID, pid-file, and process-table resolution for both
`cardano-node` and `amaru`. Add CPU time, network RX/TX, disk IO, RSS, FD, and
thread sampling where Linux `/proc` or cgroup data provides it. Record
unavailable fields honestly.

### Task 4.3: Workload accounting, restart, and sync collectors

**Files**

- Create collector modules/tests for offered work, restart readiness, and
  controlled sync speed
- Reuse existing topology health/readiness and runtime metadata helpers

Define readiness as listener available plus live chain progress and required
peer/role state. Define sync speed over retained start/end block points and
elapsed monotonic time. Preserve the controlled chain range and peer policy.

**Batch gate**

Run fixtures, focused integration tests, representative existing resource and
topology scenarios, then one short real stock-Amaru collector proof outside the
GUI only as a component gate. This is not yet end-to-end completion.

**Evidence — 2026-09-18**

- Exact qualification:
  `state:version-qualifications/20260918T165505Z-dwarf-qual-amaru-10-7-1-10-11-20260912-33ba7660`
- Result SHA-256:
  `35a80a758c8147ec5e3b5412ff6a5286b91dc62e0a66cdf5da6afe11e7a493c9`
- Classification: `passed-all-gates`; all ten required gates passed, including
  exact identity, fresh state, chain progress, peer formation, consumer
  convergence, Amaru-only consumer path, no fatal signatures/restart loop, and
  clean teardown.
- Real-process component proof:
  `state:measurement-component-proofs/20260918T172100Z-amaru-10.11.20260912-resources-success`
- Collector result SHA-256:
  `5fba3d094f3736bc2e7f14a324500d5e077383715fa3a730178e3b3aa2280b06`
- Six samples targeted host PID `3425210`, verified as `/target/amaru run ...`.
  CPU time/rate, CPU percentage, RSS, threads, and network-namespace RX/TX
  counters were available. This host denied `/proc/<pid>/fd` and
  `/proc/<pid>/io`; those fields are explicitly unavailable, not zero or
  inferred. The replay ended with zero remaining project containers/volumes.
- Verification: 37 focused collector/runtime tests passed before the live gate;
  the established repository suite then passed 715 tests. Running unscoped
  root pytest still encounters the pre-existing duplicate module-name
  collection conflict in two Antithesis package test directories, so the
  repository's established `tests/` suite is the regression authority here.

## Batch 5 — revision-locked patched Amaru target

### Task 5.1: Add fail-closed patch builder

**Files**

- Create `dwarf/targets/amaru/measurement-patches/b159172f25a9c389f82f20bca4f15e3032791638/manifest.json`
- Create bounded patch files in that directory
- Create `dwarf/scripts/build_amaru_measurement_target.py`
- Create `tests/test_amaru_measurement_patch.py`

**Red**

Test exact clean source revision, patch digest, zero-offset application,
toolchain/build flags, build-log preservation, executable/image digest,
refusal of wrong/dirty source, and no machine-specific paths.

**Green**

Build from a disposable exact source checkout. Never mutate a pre-existing
developer checkout of Amaru.

### Task 5.2: Instrument only audited gaps

Patch the pinned production source to emit bounded native trace/metric events
for protocol decode, BlockFetch handler/queue, and TxSubmission2 in-flight/
residence boundaries. Reuse Amaru's observability macros and correlation
fields. Do not add scheduling/mailbox/lock probes without a demonstrated
attribution gap.

### Task 5.3: Paired observer-overhead calibration

**Files**

- Create `dwarf/profile_manager/measurement_collectors/amaru_patched.py`
- Create calibration scenario/profile definitions and tests

Run identical fixed-seed workloads on stock and patched builds from
`b159172...`. Report distributions and deltas. Mark performance results
unavailable if sample count or identity parity fails.

**Batch gate**

Run source-contract tests, builder failure tests, patch unit/integration tests,
real build, target smoke, and paired calibration. Review every patch hunk
against the exact upstream source before commit/push.

**Evidence — 2026-09-18**

- Exact source: Amaru `10.11.20260912` at
  `b159172f25a9c389f82f20bca4f15e3032791638`; patch set
  `7ce3356d53535b22b82abf10166f9fa8ccfcd40b49bd8e298895ba1c027c0532`.
- Retained build:
  `state:measurement-target-builds/amaru-b159172-batch5-musl`; build-result
  SHA-256 `8de9ee83493e30c57293cbee06e6defb7c195f37479eed3938a03d95530be600`.
  The static executable SHA-256 is
  `f70cf6be3be754a51fda53b101c4e4e4ab07c4b0ff28f8034ba335d58b744caa`;
  the local immutable image ID is
  `sha256:680ae0df46b0081c2477b9f76cd7c1d89f1e8e08ff781cd6037a2f8218a83d1b`.
  Image smoke and exact bootstrap-wrapper compatibility probes passed.
- Both fresh deployments passed every required runtime gate: exact identity,
  fresh state, required services, peer formation, chain progress, isolated
  Amaru-fed consumer convergence, no restart loop, and no fatal signal.
  Deployment evidence SHA-256 values are
  `8bd21708caf172a4c76201f0b07de1bd01a49ac76f58900a27214380fba24051`
  (patched) and
  `7c8c7f22c24379847f06b97872ff4df0b47ec590fd948a97c20e1abb91917a18`
  (stock).
- Patched leg:
  `state:measurement-calibrations/20260918T211600Z-patched-final-v3`;
  result SHA-256
  `a7862d545fd16a33de4dd597fa962d3ef02215935fd62ed5d26ac3e957362e89`.
- Stock leg:
  `state:measurement-calibrations/20260918T213100Z-stock-final-v3`;
  result SHA-256
  `bbedba85aa78efd31979dbdb89d3654a0063b7419082aa4827984a7f24cec6c6`.
- Strict pair:
  `state:measurement-calibrations/20260918T213200Z-paired-final-v3`;
  result SHA-256
  `08db82be0965fd83ea1fa3efb7d0a39aca72b51a1b9ca159b776fb33d5326639`.
  Source/version, workload digest, runner SHA-256, timing policy, hardware,
  units, sample minimum, and terminal outcome counts all matched. Each side
  retained 40/40 timed rejection outcomes and 40 bounded wire transcripts.
  The common handshake-refusal metric reported patched deltas of +0.1504%
  mean, +0.2200% median, +0.1353% p95, and -0.0185% p99.
- Claim boundary: this calibrates only the named common external handshake
  metric. Protocol-decode, BlockFetch, and TxSubmission2 internal timings
  remain `requires-paired-calibration` until their own stock/patched surface
  workloads run in the corresponding security examples. A deliberately paired
  earlier stock artifact was rejected with exit 2 for workload and runner
  mismatch, proving the gate fails closed.
- The exact patch compiled, passed 137 upstream tests with five upstream
  ignores, passed Rust formatting, ran in the proven wrapper, and emitted real
  `amaru::protocols/measurement.*` events. No protected control service was
  stopped; the retained control remained at 12 running services after both
  exact teardowns.

## Batch 6 — compiler coverage linkage

### Task 6.1: Add a distinct coverage target mode

**Files**

- Add version-qualified coverage target/build definitions
- Modify existing coverage aggregation only where needed
- Create `tests/test_amaru_measurement_coverage.py`

Test retained build identity, campaign/corpus/input linkage, path artifacts,
and mandatory `non_authoritative_performance` labels. Reuse existing
cargo-fuzz/libFuzzer and coverage aggregation rather than adding another
fuzzer.

### Task 6.2: Preserve CDDL/dataset qualification gates

Extend tests to refuse unqualified Cardano dataset rules and prove the pinned
Conway `plutus_data` boundary remains intact.

## Batch 7 — five additive Amaru security examples

For every example, first add a failing static contract test, then the scenario,
then a component/integration test, and only later its real GUI proof.

### Task 7.1: ChainSync

Create an Amaru-only real-node scenario that reuses the full ChainSync
state-machine generator and adds intersection/rollback churn. Require target
reachability, nonzero valid and illegal message classes, unrelated progress,
containment, recovery, transcript, and selected measurement samples.

### Task 7.2: BlockFetch

Create an Amaru-only real-node range/pipeline pressure scenario with bounded
stall/body-withholding or malformed-block cases. Require non-vacuous message
classes, no panic/fatal termination, honest progress, recovery, transcript,
stock fetch timing, and patched queue timing when selected.

### Task 7.3: TxSubmission2

Create an Amaru-only invalid/duplicate/body-withholding scenario. Require
offered attempt/input accounting, received/accepted/rejected correlation,
mempool/backlog series, unrelated peer usability, post-pressure drain and
recovery, and patched in-flight residence when selected.

### Task 7.4: KeepAlive

Create an Amaru-only state/timing abuse scenario scoped to one peer. Require
non-vacuous illegal cases, target reachability, tested-peer containment,
unrelated-peer usability, RTT/connection series, and recovery.

### Task 7.5: CBOR/Plutus corpus plus live replay

Create one orchestrated example with clearly separated stages:

1. Cuddle-derived valid seeds and DWARF mutation;
2. qualified pinned dataset Plutus-data inputs;
3. real production-library execution with compiler coverage; and
4. replay through a real pinned Amaru live ingress wherever that input is
   accepted.

Do not label non-ingress library cases as live-node evidence.

**Files for Batch 7**

- Add only new scenario files under `dwarf/scenarios/`
- Add only new primitive schemas/registry entries where no existing primitive
  can express the workload or assertion
- Add focused tests under `tests/`
- Add example documentation under `dwarf/docs/measurements/examples/`

**Batch gate**

Validate every scenario, run focused primitive tests, and regression-test the
existing mixed mini-protocol, topology, CBOR-dataset, and bundle scenarios.

## Batch 8 — Operate frontend

### Task 8.1: Measurement catalog, detail, editor, import/export

**Files**

- Create data/view/template modules following existing catalog patterns
- Modify `dwarf/profile_manager/dashboard.py`
- Modify `dwarf/profile_manager/data/sub_nav.py`
- Modify `dwarf/dashboard/static/js/definition-editor.js` only when the generic
  editor cannot express a schema field
- Create `tests/test_operate_measurements.py`

Test list/filter, detail, new/edit, raw escape hatch, validation, compatibility,
disabled/default/incompatible state, deterministic download/export, unsafe
input, long IDs, and empty state.

### Task 8.2: Scenario/run selection

Modify the structured scenario builder and GUI launch form to select a profile,
individual taps, none, and threshold opt-in. Show compatibility against the
resolved Amaru version/mode before launch. Preserve current default scenario
launch behavior.

### Task 8.3: Live and result rendering

Extend live run and run detail data/templates for collector state, compact
table, scoreboards, distributions, throughput/goodput, resources, degradation,
recovery, transcript, coverage, provenance, and overhead warnings. Every value
links to a retained artifact/download.

**Batch gate**

Run focused route/template tests, dashboard navigation/accessibility/visual
contracts, and representative current pages before commit/push.

## Batch 9 — Learn frontend and technical documentation

### Task 9.1: Add Learn content

**Files**

- Create `dwarf/dashboard/templates/learn/measurements.j2`
- Create matching data/view modules
- Modify sub-navigation and dashboard routes
- Create `tests/test_learn_measurements.py`

Cover concepts/schema, stock/coverage/patched modes, CDDL/corpus/fuzz/coverage,
the five examples, report interpretation, troubleshooting, and claim
boundaries. Add contextual links from all new builders/result views.

### Task 9.2: Update durable docs and notes

Update DWARF technical docs, current project notes, this plan's evidence table,
and the pinned workbench overview/runbook. Do not claim runtime proof before it
exists.

## Batch 10 — exact GUI-driven end-to-end proof

### Task 10.1: Deploy verified DWARF build

Deploy the exact internal commit to the existing Docker deployment on
`cardano-box`. Record deployed commit/image digest and verify `/operate/status`
plus topology health.

### Tasks 10.2–10.6: Run the five examples through the GUI

For each example:

1. start from its `/operate/scenarios/<id>` or launch workflow;
2. select the exact `10.11.20260912` Amaru target mode and measurement profile;
3. use fresh state/volumes where the example contract requires them;
4. launch through the GUI;
5. monitor the live page and collector states;
6. inspect the final result page;
7. download/export the evidence bundle through the GUI; and
8. verify bundle integrity and every example-specific proof gate.

Record the run ID, scenario/measurement digests, target image/source identity,
target reachability, non-vacuous workload counts, signal sample counts,
coverage linkage, assertions, measurement non-gating behavior, result URLs,
and export checksums in the workbench runbook.

### Task 10.7: Explicit threshold-gate control proof

Run a bounded control showing that the same measurement condition is
non-gating by default and gating only after explicit opt-in. Do not manufacture
a security assertion failure to prove this.

## Batch 11 — regression and browser review

### Task 11.1: Full relevant regression

Run the complete available Python suite plus representative existing scenario,
target/profile builder, bundle, topology, version, CBOR dataset, and dashboard
workflows. Classify unrelated pre-existing failures with evidence; do not hide
them by narrowing the command.

### Task 11.2: Browser review cycle 1

Use the deployed site at desktop and mobile widths. Capture screenshots for:

- catalog/list, detail, new/edit raw and structured modes;
- version incompatibility and collector error states;
- scenario/run selection;
- live collector state;
- each result panel and downloads;
- Learn overview/examples/troubleshooting.

Check overflow, field size, dropdowns, descriptions, validation, long IDs,
tables/charts, empty/error states, nav, and export controls. Fix via TDD and
repeat affected tests.

### Task 11.3: Browser review cycle 2

Repeat independently at desktop and mobile widths after all cycle-1 changes.
Retain screenshots and route/status evidence.

## Batch 12 — completion audit and delivery

### Task 12.1: Requirement-by-requirement audit

Build a completion matrix from the controlling goal. For every requirement,
link an exact test, source file, run ID, artifact, screenshot, route, or
workbench record. Missing or indirect evidence is incomplete.

### Task 12.2: Final internal push and workbench update

Review the complete diff for secrets, machine-specific home paths, credentials, `._`,
`.DS_Store`, caches, build output, unrelated changes, and public remote use.
Run `git diff --check`, verify the deployed commit equals the reviewed commit,
push the fully proven final batch only to `internal`, and update the pinned
workbench completion gates and runbook.

The goal is complete only when all five GUI workflows have evidence-backed
reports containing the requested functional scoreboards, critical
distributions, compact table, security impact, coverage linkage, and portable
run evidence.
