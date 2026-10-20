# Patched-First Hosted Campaign Design

## Goal

Run each accepted source candidate through DWARF with the richest compatible
measurement instrumentation, without compiling once per scenario and without
presenting an instrumented binary as the submitted stock binary.

## Decision

The hosted campaign uses a patched-first policy:

1. Build the stock candidate once.
2. Build one patched variant when a trusted patch matches source preimages and
   toolchain requirements.
3. Run a short paired calibration against stock and patched artifacts.
4. When calibration passes, run the selected scenario suite with the patched
   candidate and collect passive plus internal measurements.
5. Replay security-relevant outcomes against the stock artifact with the same
   retained input before assigning an authoritative stock verdict.
6. When the patch is unavailable, fails to build, or fails calibration, run the
   stock suite and mark patched measurements unavailable with the concrete
   reason.

The workflow never treats stock evidence as evidence for an internal patched
tap. Known compatible versions are evidence history, not an allowlist.

## Build and Runtime Model

Every source-tree digest has at most one stock and one patched build in the
initial implementation. Both receipts bind the submitted commit, source-tree
digest, trusted recipe, builder digest, executable digest, runtime image digest,
and (for the patched variant) patch-set digest. Two nodes and multiple scenarios
reuse those images.

The candidate's implementation determines the useful topologies. An Amaru
candidate runs in Amaru-only and Amaru/Cardano mixed topologies; a Cardano
candidate runs in Cardano-only and mixed topologies. A campaign containing both
candidate implementations can also run the candidate-to-candidate mixed pair.
An implementation is never scheduled in an unrelated homogeneous topology.

Mixed execution carries a mode per node. A patched candidate may therefore run
beside a stock reference node. Resolution, qualification, launch materialization,
collectors, and evidence must bind each node to its own exact mode and artifact.
A single topology-wide mode must not overwrite these bindings.

## Calibration Gate

Calibration uses the same topology inputs and a bounded normal workload. It
requires both artifacts to start, reach readiness, make chain progress, and
produce the expected accepted control outcome. The receipt records elapsed time,
resource samples, external outcomes, and both artifact identities. Thresholds
are declared in trusted configuration.

Calibration failure does not reject the submitted candidate. It selects stock
fallback and records patched measurements as unavailable. It must not trigger an
unbounded retry or another candidate build.

## Security Authority

Patched execution is the primary observation lane because it exposes internal
boundaries. A security finding or other reportable security classification is
authoritative for the submitted candidate only after exact-input stock replay.
The report retains both results when they disagree. A patched pass remains an
instrumented observation until its security assertions receive stock replay.

The initial hosted workflow may replay all six bounded security cases on stock.
This reuses the existing stock image and costs runtime, not compilation. Later
optimization can replay only the assertions needed for publication if that
still preserves pass authority.

The implemented comparison does not equate a matching seed with a matching
input. It requires the complete retained, same-boundary input-digest set to be
equal for the patched and stock result of each security entry. Instrumentation
attachments may differ between the two manifests; the frozen security recipe,
scenario definition, execution contract, candidate source and per-run contract
digests must still agree. The replay phase runs security entries and their
normal controls only. It does not repeat measurement cells or compile either
variant.

## Evidence and Failure States

The campaign status records the selected primary mode, calibration decision,
fallback reason, build identities, patch identity, scenario attempts, and stock
replay linkage. Publication distinguishes:

- `patched-primary-stock-confirmed`
- `patched-primary-stock-disagrees`
- `stock-fallback-patch-unavailable`
- `stock-fallback-patch-build-failed`
- `stock-fallback-calibration-failed`

Missing patched evidence remains incomplete. It does not erase valid stock
security or passive measurement results.

## Delivery Slices

The first slice implements policy and evidence contracts, per-node mode
selection, and fail-closed stock fallback. The next slice executes and transfers
the compatible Amaru patched build using the existing immutable builder. The
final slice adds paired calibration and stock replay to the hosted workflow.
Cardano candidate compilation follows the same contracts when its hosted builder
is available; unsupported Cardano patched builds remain explicit rather than
being simulated.

## Implemented Hosted Flow

The planner emits a patched-primary manifest and a separate immutable stock
manifest. Runtime qualification selects artifacts by explicit mode rather than
catalog order. The campaign runs stock calibration, patched calibration, one
selected full campaign, and, only when patched is selected, a stock security
replay. The selected state retains the replay archive receipts and a digested
authority record. Publication refuses patched-primary output when the replay is
missing, incomplete, or does not retain the same input set. A disagreement is
published explicitly with the stock classification; it is never collapsed into
the patched result.

## Focused Verification

Contract tests cover patched selection, missing/incompatible patch fallback,
calibration failure, per-node mixed bindings, and stock replay authority. One
short hosted smoke campaign validates the integrated lane before another full
campaign is started.
