# DWARF node metrics and operating program

Status: presentation proposal; implementation is deferred

Date: 2026-09-10

## Decision to request on September 17

Agree on a small, repeatable real-node measurement program rather than calling
DWARF a general benchmark suite. The program should answer two questions:

1. Did a Cardano-node or Amaru change preserve correctness and recovery?
2. Under the same pinned workload and hardware, did useful node work materially
   regress under normal and adversarial conditions?

DWARF already supplies the orchestration, real nodes, phases, load/fault
controls, deterministic seeds, assertions, evidence bundles, and comparison
workflow. The missing work is the node/protocol observation layer: canonical
wire transcripts, node telemetry capture, correlation, and selected
DWARF-owned instrumented builds.

This is not implemented today. Current host, process, and Antithesis platform
metrics are supporting context, not node-level goodput.

## Proposed v0 metric bundles

| Bundle | Required measurement | Exact useful-work signal | Adversarial companion | Primary tags |
|---|---|---|---|---|
| Block validation | At least 20,000 pinned blocks; blocks/s; bytes/s; validation p50/p95/p99 | Blocks fully validated, with block hash and outcome | Malformed, forged, and boundary blocks | `ledger`, `consensus`, `performance` |
| Block application | Apply rate and latency; adopted tip; rollback/reapply count | Validated block applied to canonical state | Rollback, fork, delayed fetch, invalid predecessor | `ledger`, `consensus`, `recovery` |
| Block transfer | Useful blocks/s and bytes/s over real BlockFetch | Requested block range served and received | Malformed protocol traffic, peer churn, bandwidth loss | `network`, `consensus`, `performance` |
| Four block-path times | Queue-to-first-byte; wire transfer; receive-to-validation; validation-to-adoption | Correlated block hash across the four boundaries | BlockFetch faults and competing peers | `network`, `consensus`, `performance` |
| VM / Plutus | Scripts/s; execution units/s; p50/p95/p99; failures by reason | Script evaluation and resulting transaction/block outcome | Expensive/invalid scripts and mixed blocks | `ledger`, `vm`, `performance` |
| Epoch transition | Transition wall time; validation/application stall; resource peak | First post-boundary block adopted and service restored | Reward/stake boundary cases, restart near boundary | `ledger`, `consensus`, `recovery` |
| Restart / recovery | Exit-to-ready; ready-to-tip; backlog drain; peer/session restoration | Node serving and caught up to the independent observer | Crash, pause, partition, disk/resource pressure | `recovery`, `operations`, `network` |
| Sync speed | Blocks/s, bytes/s, slot-distance closed/s, time to target tip | Independently observed catch-up to a pinned chain point | Slow/malicious peers, churn, interruption | `consensus`, `network`, `recovery` |
| Real-life mixed load | Transaction and block goodput, latency, backlog, resources | Offered → admitted → included → adopted correlation | Invalid transaction pressure and network faults | `ledger`, `network`, `performance` |
| Distributed cluster | Per-role progress, propagation delta, convergence, recovery | All required roles reach the same observed chain point | Partitions, peer loss, asymmetric delay | `consensus`, `network`, `recovery` |

The four block-path times are proposed definitions, not an existing Cardano or
Amaru standard. The teams should confirm or rename those boundaries before
instrumentation is implemented.

## Evidence layers

Every reported node metric needs all applicable layers:

1. **DWARF wire transcript** — every relevant send/receive with connection,
   mini-protocol, message order, timestamp, length, hash, outcome, and natural
   transaction/block identity. Metadata is unsampled; malformed payloads are
   retained byte-for-byte.
2. **Existing node telemetry** — Cardano tracing/metrics and Amaru
   OpenTelemetry/JSON events at the exact pinned revisions.
3. **DWARF-owned instrumented builds** — real production node code with bounded,
   observational counters/histograms for missing critical-path stages.
4. **Independent outcome observation** — N2C/N2N queries and block/tip
   observations proving inclusion, adoption, progress, and convergence.
5. **Resource context** — CPU, RSS, network, disk I/O, FDs, threads, and cgroup
   counters. These explain a result but never substitute for useful work.

Tier 4 is included in the design from the start. Stock and instrumented binaries
must be built from the same pinned revision and run against the same scenario,
seed, corpus, topology, and rate schedule. Instrumentation is accepted only if
the stock/instrumented pair preserves protocol outcomes and its overhead is
reported.

## Provisional acceptance gate

This is a proposal for stakeholder agreement, not an already accepted Cardano
or Amaru policy.

### Absolute gates

- Zero new correctness, adoption, or consensus divergence.
- Zero unexpected node panic, fatal termination, or permanent stall.
- Every required workload cell and target is reached after setup completes.
- Exact source revision, image digest, seed, corpus, configuration, hardware,
  transcript schema, and dropped-event counts are retained.
- The independent observer confirms the claimed final outcome.
- Queues/backlogs stop growing and drain after fault removal.

### Relative performance gates

Use three clean repetitions on the same host class and compare the median run
against the last accepted baseline:

- throughput/goodput regression: no worse than 10%;
- p95 latency regression: no worse than 15%;
- p95 restart/recovery regression: no worse than 20%;
- peak RSS and CPU-per-unit-work regression: no worse than 15%;
- 20,000 successfully processed blocks minimum for the block-validation gate.

Any threshold failure is a review gate, not an automatic vulnerability. The
team may accept a regression only with an explicit owner, rationale, expiry or
follow-up, and retained comparison evidence. These initial percentages should
be recalibrated after enough clean baseline runs exist to measure normal
variance.

## Operating cadence

| Trigger | Amaru | Cardano-node | Required bundle |
|---|---|---|---|
| Weekly | Full v0 smoke/performance set on pinned weekly candidate | Not required initially | correctness, block validation/application/transfer, restart/sync |
| Release candidate / new release | Full suite | Full suite | all mandatory bundles plus mixed differential |
| Consensus, ledger, networking, or VM change | Relevant tagged bundles | Relevant tagged bundles | tags selected by changed subsystem |
| Security fix | Reproducer plus neighboring regression cases | Same when affected | `security` plus owning subsystem tags |
| Harness change | Contract/preflight and one known-good control | Same | harness validation; no new SUT claim until control passes |

Start Amaru weekly because it is moving faster. Initially run Cardano-node on a
new release or release candidate, plus on demand when the common baseline or an
affected subsystem changes.

## Test categories and tags

Bundles are operational groups. Tags are many-to-many selectors attached to
scenarios, workloads, assertions, reports, and findings.

### Mandatory category

- `correctness` — ledger acceptance, block adoption, chain agreement.
- `consensus` — selection, fork/rollback, KES, epoch boundary, density.
- `ledger` — transaction/block rules, state transitions, rewards.
- `network` — N2N mini-protocols, peer lifecycle, transfer, partitions.
- `recovery` — restart, catch-up, backlog drain, convergence.
- `performance` — useful work, latency, resources, relative regression.

### Optional qualifiers

- `vm`, `security`, `adversarial`, `differential`, `mixed-net`, `local`,
  `antithesis`, `release-gate`, `weekly`, `known-regression`.

Every scenario needs at least one owning subsystem tag and one intent tag. For
example, the future honest-plus-adversarial BlockFetch scenario would carry
`network`, `consensus`, `performance`, `adversarial`, `differential`, and
`mixed-net`.

## Report shape

```text
Scenario: mixed-blockfetch-goodput
Window: baseline | fault | recovery | steady-state
Targets: cardano-node <rev> | amaru <rev>

Offered: requests/s, blocks/s, bytes/s
Useful: blocks received, validated, applied, adopted
Latency: queue→first-byte, wire, validate, adopt p50/p95/p99
Backlog: peak, growth rate, drain time
Recovery: serve-ready, tip-caught-up, peer-session restored

Cardano: useful work, latency, CPU/RSS/RX/TX/disk per unit work
Amaru:   useful work, latency, CPU/RSS/RX/TX/disk per unit work
Delta:   baseline, adversity degradation, recovery difference

Provenance: source/image/patch/seed/corpus/hardware/schema/drop counts
Verdict: pass | review | invalid, with exact failed gate
```

## Claims this program still cannot make

- Production-network or global Cardano TPS.
- Benchmark-grade capacity across uncontrolled hardware.
- Comparable performance when topology roles or workloads differ.
- Finality beyond the explicit observation horizon.
- Adoption inferred only from socket writes, HTTP responses, or mempool entry.
- Exact saturation without a controlled rate sweep.
- Production CPU efficiency from Antithesis virtualized execution.
- Absence of instrumentation bias without the paired stock run.
- Internal causation for a path that was not traced or instrumented.

## Smallest future implementation sequence

1. Freeze transcript and node-event correlation schemas.
2. Add the transcript once in the shared stateful N2N transport.
3. Add persistent honest control lanes in new additive scenarios.
4. Enable and prove existing Cardano and Amaru telemetry at pinned revisions.
5. Add bounded native Tier 4 counters/histograms through DWARF-owned patch sets.
6. Build paired stock/instrumented images and quantify instrumentation bias.
7. Aggregate by baseline, fault, recovery, and steady-state windows into the
   existing NDJSON/evidence bundles.
8. Prove locally through DWARF before any paid Antithesis submission.
