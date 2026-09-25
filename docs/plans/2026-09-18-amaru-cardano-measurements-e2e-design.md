# Amaru and Cardano-node measurements: end-to-end design

**Status:** Amaru and Cardano-node independently proven; stopped before mixed
**Date:** 2026-09-18
**Authoritative checkout:** `/home/nigel/dwarf-pragma` on `cardano-box`
**Starting commit:** `347c99bf4d9f729f7e29f2c1fe9ed10b7f443b23`
**Prior design:** `docs/plans/2026-09-18-amaru-security-measurements-design.md`

## Outcome

Finish the first-class DWARF measurement system as a real, deployed workflow
for Amaru and then add the equivalent Cardano-node implementation. Completion
means an operator can select a compatible measurement profile, launch a real
version-pinned node scenario from the DWARF GUI, and inspect and export retained
measurement evidence without changing the scenario's security result by
default.

This work deliberately stops after Cardano-node proof. It does not implement
mixed-node measurement comparison. It also does not require five new examples
per implementation: one representative, non-vacuous end-to-end scenario per
implementation is the proof gate. Existing and later scenarios may reuse the
same independent taps.

## Exact targets

### Amaru

- Version: `10.11.20260912`
- Source revision: `b159172f25a9c389f82f20bca4f15e3032791638`
- Stock OCI image ID: `sha256:45d46a6ba7147bfa95d96c103820542a9e3ac3602c4c316cc0d04bbd6d71489e`
- Modes already implemented: stock, compiler coverage, and revision-locked
  patched measurement target

### Cardano-node

- Version: `11.1.2`
- Source revision: `fef83fed01d7926f3de83b3b917be5a4a48768b5`
- Stock OCI reference: `ghcr.io/intersectmbo/cardano-node:11.1.2`
- Stock OCI digest: `sha256:6365403f44713d0a046865fb0466503ef207b71beae1b1ffece7f4399356db9f`
- Qualification: current confirmed/default Cardano-only release in DWARF's
  version catalog

Cardano-node `10.7.1` remains the proven mixed-network compatibility pin. It is
not the target of this Cardano-only measurement implementation.

## Approach selected

Use the existing shared Measurement abstractions and prove them end to end
before adding implementation-specific Cardano collectors. Then add Cardano
parity and prove the same workflow again.

Rejected alternatives:

1. Building five complete examples first would delay proof of the reusable
   measurement system and duplicate scenario work.
2. Implementing Cardano stock telemetry only would knowingly omit accurate
   internal boundaries and violate the requirement to use revision-locked node
   patches where external observation is insufficient.
3. Starting mixed comparison immediately would combine two unproven workflows
   and make failures difficult to attribute.

## Architecture

### Shared path

Both implementations use the existing:

- Measurement and Measurement Profile catalogs;
- exact version, source, target-mode, and artifact resolution;
- optional non-gating collector lifecycle;
- phase/window markers;
- normalized distributions and throughput accounting;
- correlation and bounded protocol transcripts;
- existing run manifest, NDJSON, evidence, SARIF, attestation, bundle, and
  export paths; and
- `/operate/measurements`, scenario/run selection, run-result, and `/learn`
  frontend surfaces.

No second runner or report store is introduced.

### Outcome-independent timing

Every operation that reaches a timed boundary is retained with its elapsed time
and terminal outcome. Accepted, rejected, invalid, duplicate, timed-out,
disconnected, and unclassified attempts appear in the combined population and
in per-outcome distributions. Missing boundaries are unavailable, never zero.

### Amaru end-to-end gate

Use one additive, representative real-node scenario with the existing exact
stock Amaru profile and default compatible measurement profile. It must:

1. launch through the deployed DWARF GUI;
2. resolve the exact Amaru identity and use the real target;
3. execute a non-vacuous workload with outcome-independent attempt timing;
4. collect compatible stock, external, workload, and resource taps;
5. render the compact table and detailed distributions;
6. retain transcripts, provenance, collector health, and available coverage
   linkage;
7. leave measurement failures non-gating unless explicitly configured; and
8. expose a portable GUI-downloadable bundle.

This gate fixes shared runtime/report/frontend defects before Cardano work.
The five Amaru security examples remain deferred.

### Cardano-node parity

Add Cardano-specific definitions and adapters while keeping the shared result
schema implementation-neutral.

#### Stock telemetry

Consume Cardano-node 11.1.2's existing new-tracing/forwarding, Prometheus/EKG,
and structured node events before adding source patches. Normalize only
boundaries supported by exact trace constructors or metrics, including where
available:

- ChainSync client/server progress and rollback/intersection events;
- BlockFetch client/server state, completed fetch, delay, size, and served
  block events;
- TxSubmission2 inbound/outbound events and counters;
- mempool accepted/rejected/removed/synchronized events and size metrics;
- KeepAlive RTT and connection events;
- ChainDB selection, adoption, rollback, replay, and tip progress;
- block forging/adoption and ledger metrics; and
- RTS and node resource metrics.

The exact source audit records namespaces, constructors, fields, units,
configuration, and visibility gaps. No metric is inferred from log wording
without a tested parser contract.

#### External measurements

Reuse and generalize workload accounting, restart readiness, controlled sync
speed, and real-process/container resource collectors for Cardano-node. Network
namespace counters remain labeled as namespace values, not per-process truth.

#### Compiler coverage

Register DWARF's existing revision-qualified Cardano native harnesses as a
Cardano coverage target. Coverage proves production-path execution and is not
an authoritative performance source.

#### Patched target

Build a minimal Cardano-node 11.1.2 measurement patch only for audited gaps
needed by the requested client output, such as exact validation/Plutus stage
timing or protocol decode/queue residence that stock traces cannot express.
The builder must reject a wrong or dirty source revision and retain patch,
toolchain, build, executable, and image identity.

Behavior seen only in the patched node is not a Cardano-node vulnerability.
Each performance claim from the patched build requires an identical stock
control and an observer-overhead result.

### Cardano end-to-end gate

One additive representative Cardano-only scenario must prove the same GUI and
evidence path as the Amaru gate. It must use Cardano-node 11.1.2 exactly, reach
the real node, emit timed attempts for all outcomes, collect selected taps,
render retained results, and export a portable evidence bundle. A paired
stock/patched calibration proves the patched observer's overhead separately.

## Client-facing output

The compact report retains rows for:

- Transfer;
- Block Application;
- Virtual Machine;
- Epoch Transition;
- Time to Restart; and
- Sync Speed.

Only observed, statistically sufficient values are populated. Each value links
to its full distribution and retained evidence. Additional protocol, resource,
degradation, recovery, transcript, and coverage panels remain available.

Amaru and Cardano values are independent in this scope. No mixed comparison or
differential conclusion is produced.

## Error and safety behavior

- Explicit incompatible measurement selections fail before execution.
- Compatible optional collector failures produce visible partial/error status
  and do not change security assertions by default.
- Threshold gates remain explicit opt-in.
- Runtime teardown and evidence finalization execute after workload failure.
- Existing scenarios, profiles, primitives, retained topologies, and evidence
  are not modified destructively.
- No Antithesis/Moog launch and no public push occur.

## Verification

Implementation uses red-green-refactor tests for every behavior change. Each
batch receives focused tests, the established repository suite, source/diff and
secret checks, and a reviewed commit. Completion additionally requires:

1. a fresh Amaru GUI run and portable export;
2. a fresh Cardano GUI run and portable export;
3. real stock/patched calibration evidence for Cardano;
4. regression runs for representative existing scenarios and modified routes;
5. two desktop/mobile browser review cycles with screenshots; and
6. workbench, technical documentation, Learn material, notes, and internal Git
   status updated with exact evidence.

After these gates pass, report completion and stop before mixed-node work.

## Independent Cardano-node proof — 2026-09-19

Cardano-node `11.1.2` is proven through the same deployed DWARF GUI and retained
evidence path as Amaru. The exact target used source revision
`fef83fed01d7926f3de83b3b917be5a4a48768b5`, patched image digest
`sha256:c74c3deafac54ed4d30da21952fce579c3293919993d31081e18787605757b8c`,
and patch-set digest
`7a948067c6b957b277400675cf95e32864ed8d92cd130fbadb673775249b5cc1`.

GUI run `20260919T032200Z-59f94558` passed and retained all 100 hostile
handshake outcomes, accepted and rejected Plutus transactions, Cardano stock
traces, patched protocol/decode, block-application, epoch-transition and
Plutus-stage samples, sync progress, and actual node-process/namespace resource
samples. All 12 collectors finalized with zero collector errors. The report
rendered 35 of 48 metric rows as available and left 13 honestly unavailable.

The paired stock/patched calibration used the same fixed workload and 100
common handshake samples per leg. Stock median was 164.5 us and patched median
was 159.0 us; stock p95 was 248 us and patched p95 was 249 us. This calibration
authorizes only the common external handshake metric. Internal patched timings
remain revision-specific and require surface-matched controls before broader
performance claims.

Mixed-node measurement comparison has not started.
