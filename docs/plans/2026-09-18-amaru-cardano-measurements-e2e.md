# Amaru and Cardano-node Measurements End-to-End Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prove the existing Amaru measurement system through the deployed DWARF GUI, implement equivalent version-pinned Cardano-node 11.1.2 measurements, prove Cardano through the same GUI/evidence path, and stop before mixed-node work.

**Architecture:** Reuse DWARF's implementation-neutral Measurement catalogs, lifecycle, correlation, report, evidence, and frontend. Repair shared gaps discovered by one real Amaru proof, then add Cardano-specific stock/external/resource/coverage/patched collectors and one representative Cardano proof. Optional measurements remain non-gating; every timed attempt is retained regardless of outcome.

**Tech Stack:** Python 3, pytest, YAML/JSON catalogs, Jinja/vanilla JavaScript dashboard, Docker Compose, Cardano-node 11.1.2 new tracing and Prometheus/EKG, Haskell source patches/build tooling, DWARF NDJSON/evidence/bundle pipeline, Playwright/browser screenshots.

---

## Constraints

- Work directly in `/home/nigel/dwarf-pragma` on `cardano-box`; no worktree.
- Use test-driven development and inspect each red failure before implementation.
- Do not modify existing scenarios destructively; add new scenarios/profiles/targets.
- Do not disturb retained mixed/control topologies.
- Do not launch Antithesis/Moog or push publicly.
- Do not add secrets, machine-specific paths, caches, build outputs, `._*`, or `.DS_Store`.
- Do not stage `dwarf/state/chain-head.json` unless its runtime-only change becomes an explicitly required source change.
- Push only fully verified batches to the internal remote; if credentials remain unavailable, record one failed attempt and queue the exact commit.
- Stop after Cardano-node proof. Do not implement mixed-node measurements.

## Batch 0: Exact Cardano-node 11.1.2 source and visibility audit

### Task 0.1: Write the failing source-contract test

**Files:**

- Create: `tests/test_cardano_measurement_source_contract.py`
- Create: `dwarf/measurements/audit/cardano-node-11.1.2.json`
- Modify: `docs/plans/2026-09-18-amaru-cardano-measurements-e2e-design.md`

**Step 1: Write the failing test**

Require an audit record that matches `dwarf/versions/catalog.json` for version,
source revision, OCI reference/digest, and Cardano-only qualification. Require
exact source files/constructors/namespaces/fields/units for every claimed stock
signal and explicit source-backed gaps for each proposed patch. Require records
for ChainSync, BlockFetch, TxSubmission2, KeepAlive, mempool, ChainDB, ledger,
Plutus, epoch, restart/sync, resources, and coverage.

**Step 2: Verify RED**

Run:

```bash
/home/nigel/.venvs/dwarf-moog-fix/bin/python -m pytest -q tests/test_cardano_measurement_source_contract.py
```

Expected: fail because the audit record does not exist.

**Step 3: Perform the read-only exact-source audit**

Use a disposable checkout of official `IntersectMBO/cardano-node` at
`fef83fed01d7926f3de83b3b917be5a4a48768b5`, including the pinned network,
consensus, ledger, and Plutus dependencies named by `cabal.project.freeze` or
project files. Inspect new tracing configuration and generated trace docs.
Record only source-supported boundaries; classify missing boundaries instead
of inferring them from metric names.

**Step 4: Add the minimal audit record and design addendum**

Include exact upstream paths, trace constructors, emitted fields, units,
configuration prerequisites, correlation keys, and visibility gaps. Separate
node-process, container/namespace, and DWARF-controller metrics.

**Step 5: Verify GREEN and commit**

Run focused tests, version catalog tests, `git diff --check`, JSON validation,
and a secret/path scan. Commit the audited source contract.

## Batch 1: Prove and repair the Amaru end-to-end workflow

### Task 1.1: Define one self-contained Amaru proof contract

**Files:**

- Create: `tests/test_amaru_measurement_e2e_scenario.py`
- Create: `dwarf/scenarios/amaru-measurement-e2e-stock.yaml`
- Modify only if required: `dwarf/scripts/runtime_amaru_measurement_calibration.py`
- Modify only if required: `dwarf/profile_manager/primitives.py`

**Step 1: Write failing tests**

Require an additive real-node scenario pinned to Amaru `10.11.20260912`, exact
stock profile, `amaru-security-default`, a non-vacuous fixed-seed workload,
fresh-runtime setup, teardown, outcome-independent timed attempts, and no
machine-specific path. Require at least one accepted/rejected/timeout-capable
terminal classification without requiring a particular hostile outcome.

**Step 2: Verify RED**

Run the focused test and confirm failure is the missing scenario/runtime
contract.

**Step 3: Implement the smallest portable scenario/helper repair**

Prefer existing version-qualified profile deployment and calibration helpers.
If the existing scenario runner cannot materialize the exact runtime, add one
bounded setup adapter rather than a second deployment engine. Do not change
existing calibration scenarios.

**Step 4: Verify GREEN**

Run the focused scenario, measurement selection, resolver, runtime, report,
forensic, bundle, and dashboard contract tests.

### Task 1.2: Launch through deployed DWARF and repair shared defects

**Files:**

- Test first for each defect in the relevant existing test module.
- Possible modifications: `dwarf/profile_manager/scenario.py`
- Possible modifications: `dwarf/profile_manager/measurement_runtime.py`
- Possible modifications: `dwarf/profile_manager/measurement_report.py`
- Possible modifications: `dwarf/profile_manager/data/operate_run.py`
- Possible modifications: `dwarf/dashboard/templates/operate/run.j2`

**Step 1: Deploy the exact committed image**

Rebuild/redeploy DWARF from the authoritative checkout and record image/source
identity. Preserve state and retained evidence.

**Step 2: Launch from the GUI with fresh runtime state**

Use the actual `/operate` scenario workflow. Do not substitute a direct script
run for proof.

**Step 3: For every failure, use a red-green repair cycle**

Add a failing regression test reproducing the observed defect, verify it fails,
implement the minimal fix, rerun focused tests, rebuild/redeploy, and repeat the
same GUI action.

**Step 4: Verify the proof artifacts**

Require exact target identity, real target reachability, non-zero attempts,
elapsed time for every attempt, collector states, normalized distributions,
compact-table values or honest unavailable reasons, transcript/provenance,
manifest hashes, SARIF/attestation presence according to existing policy, and
a portable bundle downloadable through the GUI.

**Step 5: Commit the verified Amaru E2E batch**

Record run ID, artifact digests, sample counts, claims/non-claims, and teardown
evidence in the plan and workbench.

#### Amaru gate evidence — complete 2026-09-18

- DWARF commit and deployed image revision:
  `df255de7775f070465dbbc6e1ae3cb3358cf5a4a`; dashboard image ID
  `sha256:34f15ac62508f43d3ab2d5bd05aaa1c1d52001112af048c8e96fc3c85bdf91bd`.
- GUI-launched run: `20260918T234213Z-64959688`, pass, one of one
  assertion passed, fixed seed `0xA11CE501`.
- Exact target: Amaru `10.11.20260912`, source
  `b159172f25a9c389f82f20bca4f15e3032791638`, OCI digest
  `sha256:45d46a6ba7147bfa95d96c103820542a9e3ac3602c4c316cc0d04bbd6d71489e`.
- All 11 selected collectors finalized with zero collector errors. The bounded
  trace export read 3,851 real node records, normalized 39 supported records,
  rejected zero valid records, and was not truncated or incomplete.
- The workload retained all 100 hostile handshake attempts and their terminal
  rejected outcomes. Offered rate was 9.382 operations/s; attempt latency was
  median 101,108 us, p95 102,037 us, p99 102,166 us.
- Real node samples included four header-to-fetch observations (median 831 us),
  four BlockFetch observations (median 628.5 us), four paired node JSON
  `block.prepare` spans (median 6 us), four paired `block.apply` spans
  (median 136 us), four tip updates, and an independent monotonic tip probe
  showing height 1,410 to 1,416 over 10.662 seconds (0.56275 blocks/s).
- Real target resource samples included 11 CPU and 12 RSS observations plus
  explicitly labeled process-network-namespace RX/TX deltas. These are not
  controller-process values.
- Measurement summary: 49 rows, 28 available and 21 honestly unavailable.
  Missing epoch, VM, mempool, restart, and other boundaries were not rendered
  as zero. Threshold gates remained disabled/non-gating.
- The retained testnet uses `system_start: 0`; therefore the node-reported
  slot-start-to-header interval represents absolute synthetic slot age and is
  not a comparable propagation-latency baseline. Raw evidence is retained, but
  this value is excluded from benchmark claims.
- Portable bundle download passed gzip inspection and contained no `._*`,
  `.DS_Store`, cache, or bytecode entries. Archive SHA-256:
  `4897730b504a1419a327c42cb3b03e3bef53cf105c94571bcbea6f9f192d947a`.
- Core artifact SHA-256 values: manifest
  `f8b1560ab4bc93c6edc82442fb31b1b4b304186d93de5d509cbb492e70e25461`,
  summary `ba6a95482893ec7419c4687c4f269d5907c36ce0e01b7baa5fb131a2be1fd1e4`,
  report `e0d3ffb61d819391595b414d4561e199ec2f7eff92be1f9040a3b82e3f4c6231`,
  runtime `1912d7b117f724b2300b654033a3ae8bc5ce60909d50cb8946e1a9e8d5ff9e37`,
  selection `19e4355af46c5ec83d2cf3fd26073a16cedc7dc14efb4e9b2fc4f8d379d41049`.

The Amaru gate is complete. Batch 0/Cardano source audit is now the active
implementation boundary; mixed-node work remains prohibited in this scope.

## Batch 2: Cardano Measurement and Measurement Profile catalogs

### Task 2.1: Add Cardano definitions

**Files:**

- Create: `tests/test_cardano_measurement_catalog.py`
- Create: `dwarf/measurement-profiles/cardano-security-default.yaml`
- Create: `dwarf/measurement-profiles/cardano-security-patched.yaml`
- Create additive definitions under: `dwarf/measurements/`

**Step 1: Write failing catalog tests**

Require definitions pinned to Cardano-node `11.1.2` / `fef83fed...`, with
implementation `cardano-node`, exact supported target modes, existing output
schema, explicit capabilities, overhead, lifecycle, failure behavior,
artifacts, correlation IDs, and non-gating defaults.

Initial definitions:

- `cardano-stock-chain-lifecycle`
- `cardano-stock-blockfetch`
- `cardano-stock-txsubmission-mempool`
- `cardano-stock-ledger-block-epoch`
- `cardano-stock-plutus-execution`
- `cardano-stock-network`
- `cardano-stock-resources`
- `cardano-external-restart-readiness`
- `cardano-external-sync-speed`
- `cardano-external-workload-accounting`
- `cardano-coverage-production-paths`
- only source-audited patched definitions, expected to include protocol decode,
  BlockFetch queue/handler, TxSubmission2 residence, and missing ledger/Plutus
  stage timing where stock traces lack the requested boundary

**Step 2: Verify RED, add definitions, verify GREEN**

Use existing catalog validators. Do not add a Cardano-specific catalog loader.
Run generic catalog/editor/import/export regressions and commit.

### Task 2.2: Resolve Cardano target modes and exact identities

**Files:**

- Modify: `dwarf/profile_manager/deployment_versions.py`
- Modify: `dwarf/profile_manager/measurement_resolution.py`
- Modify: `dwarf/profile_manager/measurement_targets.py`
- Create: `tests/test_cardano_measurement_resolution.py`

Write failing tests for stock, coverage, and patched Cardano identity; explicit
incompatibility; skipped passive defaults; exact image/build digest; and no
Amaru regression. Implement only generic branches required for a second
implementation.

## Batch 3: Cardano stock telemetry collectors

### Task 3.1: Normalize exact Cardano traces and metrics

**Files:**

- Create: `dwarf/profile_manager/measurement_collectors/cardano_stock.py`
- Create: `dwarf/profile_manager/measurement_collectors/cardano_factory.py`
- Create: `dwarf/scripts/runtime_cardano_measurement_collector.py`
- Create: `dwarf/assets/measurements/cardano-stock-fef83fed.json`
- Create: `tests/test_cardano_stock_collector.py`

**Step 1: Write source-derived fixtures and failing tests**

Fixtures must use exact 11.1.2 machine-formatted trace shapes. Test terminal
outcomes, units, peer/header/block/transaction correlation, incomplete streams,
unknown constructors, malformed lines, and deduplication. Test combined and
per-outcome timing so rejected or invalid operations are not discarded.

**Step 2: Verify RED**

Confirm missing collector/factory failures rather than fixture errors.

**Step 3: Implement minimal parsers and collectors**

Consume existing node output/Prometheus or forwarded traces and write bounded
raw/normalized artifacts into the run bundle. Do not introduce a monitoring
stack or parallel database.

**Step 4: Verify GREEN**

Run focused collector tests plus shared correlation/report/runtime tests.

## Batch 4: Cardano external and real-resource measurements

### Task 4.1: Generalize workload, restart, sync, and resource collectors

**Files:**

- Create or modify: `dwarf/profile_manager/measurement_collectors/cardano_external.py`
- Create or modify: `dwarf/profile_manager/measurement_collectors/cardano_resources.py`
- Modify: `dwarf/scripts/runtime_resource_profile.py`
- Create: `tests/test_cardano_external_measurements.py`
- Create: `tests/test_runtime_resource_profile_cardano.py`

**Step 1: Write failing tests**

Cover Cardano container/PID resolution, CPU, RSS, disk IO, FD, threads,
container/cgroup and namespace RX/TX labeling, restart readiness gates, sync
start/end points, workload rates, backlog, and every attempt's time/outcome.

**Step 2: Implement shared adapters**

Reuse generic workload accounting and distribution helpers. Keep target process
identity explicit so controller metrics cannot be mislabeled as node metrics.

**Step 3: Component proof**

Run a short exact stock 11.1.2 topology outside the GUI only as a component
gate. Prove the real target PID/container was sampled and retain exact evidence.
This does not satisfy final E2E proof.

## Batch 5: Cardano compiler-coverage target

### Task 5.1: Register existing production harnesses without performance claims

**Files:**

- Create: `tests/test_cardano_coverage_target.py`
- Create: `dwarf/scripts/build_cardano_coverage_target.py` only if the existing
  build path cannot produce the required registry record
- Create additive registry manifest under:
  `dwarf/targets/cardano-node/coverage-targets/fef83fed01d7926f3de83b3b917be5a4a48768b5/`
- Modify only if generic support is missing: `dwarf/scripts/runtime_cargo_fuzz_campaign.py`
  or the existing Cardano AFL++ campaign adapter

**Step 1: Write failing identity/linkage tests**

Require exact source revision, harness source and digest, toolchain/build flags,
executable/image digest, campaign/corpus/input linkage, portable paths, and
explicit non-authoritative performance labeling.

**Step 2: Build and run one bounded real campaign**

Use a production-library entry point already supported by DWARF. Retain exact
coverage output and verify the measurement result schema. Do not claim security
or performance from coverage alone.

## Batch 6: Revision-locked Cardano patched measurement target

### Task 6.1: Add a fail-closed patch builder

**Files:**

- Create: `tests/test_cardano_measurement_patch.py`
- Create: `dwarf/scripts/build_cardano_measurement_target.py`
- Create: `dwarf/targets/cardano-node/measurement-patches/fef83fed01d7926f3de83b3b917be5a4a48768b5/manifest.json`
- Create bounded patch files in the same directory

**Step 1: Write failing builder tests**

Require exact clean source revision, zero-offset patch application, expected
patch digest, toolchain/build flags, build-log digest, executable/image digest,
and refusal of dirty/wrong source.

**Step 2: Add only audited instrumentation gaps**

Use Cardano's existing tracing abstractions. Emit bounded machine-formatted
events with monotonic duration, terminal outcome, and available correlation IDs.
Do not duplicate stock traces or add broad debug logging.

**Step 3: Build from a disposable exact checkout and smoke the real node**

Retain build provenance and prove the built node starts and advances in a fresh
Cardano-only topology.

### Task 6.2: Add patched collectors

**Files:**

- Create: `dwarf/profile_manager/measurement_collectors/cardano_patched.py`
- Create: `tests/test_cardano_patched_collector.py`

Write failing tests from exact emitted records, then implement normalization,
correlation, unavailable states, and outcome-independent distributions.

## Batch 7: Cardano stock-versus-patched overhead calibration

### Task 7.1: Create equivalent stock and patched calibration legs

**Files:**

- Create: `tests/test_cardano_measurement_calibration.py`
- Create: `dwarf/scripts/runtime_cardano_measurement_calibration.py`
- Create: `dwarf/profiles/profile-s-cardano-measurement-stock-control/profile.yaml`
- Create: `dwarf/profiles/profile-t-cardano-measurement-patched/profile.yaml`
- Create: `dwarf/scenarios/cardano-measurement-overhead-calibration-stock.yaml`
- Create: `dwarf/scenarios/cardano-measurement-overhead-calibration-patched.yaml`

**Step 1: Write failing parity tests**

Require identical workload identity, seed, hardware, source revision, warm-up,
attempt count, timeout, outcomes, units, and runner digest. Require at least 30
common samples and fail closed on mismatches.

**Step 2: Implement and run both real legs**

Retain every attempt and its outcome/time. Report median/p95/p99 deltas and
mark patched performance unavailable where a common comparable metric does not
exist.

## Batch 8: Cardano end-to-end GUI proof

### Task 8.1: Add one representative self-contained scenario

**Files:**

- Create: `tests/test_cardano_measurement_e2e_scenario.py`
- Create: `dwarf/scenarios/cardano-measurement-e2e-stock.yaml`

Write a failing contract test requiring exact Cardano 11.1.2, the stock
measurement profile, fresh version-qualified substrate, non-vacuous workload,
outcome-independent timings, real-node assertions, and teardown. Implement the
smallest additive scenario that exercises stock/external/resource measurements
and links available coverage/calibration evidence.

### Task 8.2: Launch and verify through the deployed GUI

Repeat the Amaru E2E procedure with Cardano-node. Require actual image/source
identity, real target reachability, non-zero timed attempts, selected tap data,
report rendering, non-gating collector behavior, portable bundle, and clean
teardown. Repair each discovered defect via its own red-green cycle.

#### Cardano-node gate evidence — complete 2026-09-19

- Exact target: Cardano-node `11.1.2`, source
  `fef83fed01d7926f3de83b3b917be5a4a48768b5`, patched OCI digest
  `sha256:c74c3deafac54ed4d30da21952fce579c3293919993d31081e18787605757b8c`,
  executable digest
  `sha256:2777c36dbfdc4b8e57b9c839f055a65709e3049800030817a97a3943c712f738`,
  build-result digest
  `sha256:528f665ef18f4af7c74c3216511cbe4aebb2aa5f0615ee4896868daeb975e269`,
  and patch-set digest
  `7a948067c6b957b277400675cf95e32864ed8d92cd130fbadb673775249b5cc1`.
- A fresh three-node canonical runtime reached healthy/converged state using
  that exact image. The retained stock control topology was not replaced or
  disturbed.
- GUI-launched run `20260919T032200Z-59f94558` passed with seed
  `0xCA4DA001`; one of one assertion passed and the tamper chain verified.
- The workload retained all 100 unsupported-version handshake attempts and
  their terminal rejected outcomes. Attempt latency was median 181 us, p95
  250 us, and p99 302 us.
- The same run submitted real accepted and rejected Plutus scripts. The
  accepted transaction was included valid and the rejected script transaction
  was included invalid; both submission and inclusion intervals remain in the
  retained workload artifact.
- Node instrumentation produced 493 protocol receive/decode samples (median
  300 us), 29 block-application samples (median 36 us), four Plutus VM samples
  (median 52 us, retained by accepted/rejected outcome), and two epoch-transition
  samples (median 168 us).
- Independent tip probes observed block height 165 to 194 over 77.582 seconds
  (0.3738 blocks/s). Stock traces retained BlockFetch, ChainDB, network,
  mempool, and transaction lifecycle evidence alongside the patched events.
- Actual target-process/namespace sampling retained 78 CPU percentage samples,
  79 RSS/FD/thread samples, CPU-time and disk deltas, and network RX/TX deltas
  explicitly scoped to the process network namespace.
- All 12 selected collectors finalized with zero collector errors. The compact
  report rendered 35 of 48 rows available and left 13 honestly unavailable.
- A GUI-downloadable portable bundle passed gzip and forbidden-file inspection.
  Archive SHA-256:
  `f8f278b3b64aaf872af3cb8d59e52c6a23b9df57c98797f8d82419d111555448`.
- Core artifact SHA-256 values: manifest
  `1988ebdc9c04f7e9abc48e4921e75e8d0b0027d58ee147507841b4984c4ddd50`,
  report `187313d215384a866525ddf12955b94bb9ddef5bd160f639f5ef6af587000473`,
  runtime `e335c219a3dfd02635fb92e617237015f0f7a9444b322fea9cb01c53bfbecc35`,
  selection `24dfa90029996686d582503e6d3c3592b16156068c80459785acc12175de7184`,
  and calibration result
  `b5d5b684a21bee42a23fca1f6ab0190138b87bab51c718fff314c68a04385355`.
- The paired stock/patched calibration retained 100 common handshake samples
  per leg. Stock/patched median was 164.5/159.0 us (-3.343%); p95 was 248/249
  us (+0.403%). This authorizes only the common external handshake comparison;
  patched internal stage values remain revision-specific.
- The first GUI pass exposed one optional resource-collector error caused by
  the explicit packaged ELF loader. A red regression test was added, the PID
  resolver was repaired to select the actual loader-wrapped cardano-node child,
  and the exact GUI scenario was repeated to obtain the clean run above.

## Batch 9: Frontend and Learn parity

### Task 9.1: Prove Cardano catalog, compatibility, and result rendering

**Files:**

- Modify tests first in dashboard/route/definition/run-result modules.
- Modify as required: `dwarf/profile_manager/data/operate_measurements.py`
- Modify as required: `dwarf/dashboard/templates/operate/measurements*.j2`
- Modify as required: `dwarf/dashboard/templates/operate/run.j2`
- Modify as required: `dwarf/profile_manager/data/learn_docs.py`
- Modify as required: `dwarf/dashboard/templates/learn/measurements*.j2`

Write failing tests for Cardano filters, mode/version compatibility, unavailable
reasons, provenance, overhead warnings, distributions, throughput, resources,
transcripts, coverage linkage, and export controls. Reuse existing components;
do not add implementation-specific pages.

### Task 9.2: Update technical documentation

Document stock/coverage/patched Cardano modes, exact supported revision,
measurement selection, interpretation, troubleshooting, claims/non-claims,
and the explicit absence of mixed comparison in this scope.

## Batch 10: Browser QA, regression proof, workbench, and delivery status

### Task 10.1: Run full verification

Run:

- every new focused test;
- the established complete `tests/` suite;
- representative existing Amaru, Cardano, scenario, target, profile, bundle,
  SARIF, attestation, and dashboard tests;
- compile/import and JSON/YAML validation;
- `git diff --check`;
- a secret scan and forbidden-file/path scan; and
- clean-tree review excluding the known runtime-only chain-head file.

### Task 10.2: Perform two browser-review cycles

Use Playwright/screenshots against the deployed site at desktop and mobile
sizes. Review measurement catalog/detail/editor, scenario selection, launch,
live status, Amaru result, Cardano result, charts/tables, long identifiers,
unavailable/error states, evidence links, and exports. Fix each defect test-first
and repeat the complete cycle.

### Task 10.3: Update workbench and internal status

Update `dwarf-latest` overview and persistent runbook with exact commits, target
identities, GUI run IDs, sample counts, artifact hashes, calibration deltas,
browser evidence, claims/non-claims, and the explicit stop before mixed work.
Push verified commits to internal Git if credentials work; otherwise record the
queued commits and one precise credential failure.

### Task 10.4: Stop at the approved boundary

Report whether Amaru and Cardano-node measurement workflows are each proven end
to end. List any honest unavailable values and deferred five-example coverage.
Do not start Cardano/Amaru mixed measurements.
