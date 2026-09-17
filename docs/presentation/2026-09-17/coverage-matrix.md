# DWARF tested and untested coverage matrix

Status date: 2026-09-10

This matrix answers “what has and has not been tested?” at claim level. It does
not turn catalog inventory into a coverage percentage.

| Domain | Proven locally | Proven in Antithesis | Finding produced | Partial / invalid | Material untested work |
|---|---|---|---|---|---|
| Ledger / phase-1 | Fee boundaries and malformed transaction paths have local/differential evidence | W34 fault-free minimum-fee differential | Amaru valid-fee over-rejection | Boundary sampled in one W34 run; closure not confirmed | Broad rule matrix, phase-2/Plutus cost and state coverage |
| Consensus / chain selection | 466-iteration five-regime mixed campaign; false-leadership rejection | Epoch panic independently reproduced; some mixed topology behavior | Amaru rewards panic fixed upstream | Fork-depth properties repeatedly confounded by sidecar/exclusion defects | Genesis-mode density/GDD, deep long-horizon adversarial regimes, current-build repeat |
| N2N mini-protocol state machines | Run `20260908T100054Z-80498541`: both targets, 48 target/cell combinations, safe recovery/convergence | Run `e9dc...`: both targets and all required cells reached at high counts | None classified from current run | Containment/recovery invalidated by control-peer fault exclusion mismatch | Corrected exclusion regression; peer-session independence by protocol; sustained pressure bounds |
| Transaction submission | 3-element arity, trailing bytes, underfee and exact-fee cases | W34 fee differential | Decode/conformance and fee-boundary findings | 3-element impact beyond decode was open; trailing bytes low severity | Full TxSubmission2 policy/fairness and valid funded inclusion/adoption matrix |
| Block validation | Forged-VRF rejection; mutated block-fetch CBOR rejected | KES non-adoption property held in W36 | No accepted forged block; separate Amaru epoch panic | KES run recovery/fork result confounded | 20,000-block validation baseline; broad block-rule corpus; Plutus-heavy blocks |
| CBOR / decode | Transaction arity, trailing bytes, tvar definite-map and parser targets | No single clean campaign proves the whole decode surface | Multiple Amaru conformance defects; some fixed | Several impacts stop at decode/mempool boundary | Multi-era corpus, duplicate keys, indefinite/stream encodings, protocol-wide canonicality |
| KES | Local mixed KES runs include successful non-adoption/recovery evidence | W36 KES security property held | No KES forgery adoption | Observer timeout and broken exclusions; known listener crash background | Fixed-harness repeat, broader opcert/KES lifecycle, expiry/rotation boundaries |
| Epoch transition | Mixed soak and post-fix runs, including 49+ clean boundaries in normal package path | W34 reproduced rewards panic 2,249 times | Deterministic Amaru panic, fixed in `v10.11.20260807` | Not every store/bootstrap state combination tested | Performance baseline, stake/reward corner cases, incomplete snapshot histories |
| Bootstrap / sync | Mixed topology, custom-testnet repair, trust-source/lifecycle analysis | W35 runtime did not reach workload; other mixed runs bootstrap successfully | tvar reader bug; consumer stall known/duplicate | Amaru requires snapshot/anchor path; sync-stall overlaps #736 | Trust-provenance verification, clean from-genesis alternative, long restart/catch-up matrix |
| Rollback / fork | Reorg, within-k partition, delayed private fork and state lifecycle tests | Some fork signals exist but are not attributable | No confirmed new node consensus bug from recent fork-depth reds | Sidecar accounting and fault exclusions invalidate several Antithesis fork signals | Corrected-harness ≥k stress; chain historicity ≥k+1; persistent partition recovery policy |
| Peer/network faults | Local churn, partitions, malformed protocol delivery | Fault injector and network activity proven; Amaru listener crash under network faults | Amaru `EADDRINUSE` consensus-stage termination | Exclusion identity mismatch affected protected controls | Peer diversity, peer-sharing poisoning, ledger/public-root policy, KeepAlive timing bounds |
| Resource pressure | Narrow request-body/flood check for trailing bytes; host/process evidence exists | Peak memory/full parallelism platform properties | Reconnect loop caused output/CPU waste in W34 | Current DWARF metrics mostly observer/host level, not node internals | CPU/RSS/network/disk per node under controlled load; FD/backlog/mempool saturation; leak campaigns |
| Recovery | Local mini-protocol consumer/targets recovered and converged; lifecycle paths | Individual recovery observations exist | State-model differences documented | Current mixed mini-protocol aggregate invalid because a protected peer was killed | Corrected mixed recovery campaign; backlog drain and time-to-recover distributions |
| Performance / throughput | No defensible node-level throughput report today | Antithesis simulation efficiency only, not node goodput | — | Existing OS metrics observe framework/host more than protocol internals | Offered/accepted/included/adopted rates, protocol transcripts, latency percentiles, node telemetry, instrumented builds |

## Summary suitable for the main deck

### Strongly tested

- Mixed chain-selection behavior for five specified regimes and pinned builds.
- Forged leadership rejection on both implementations.
- Several transaction/CBOR differential boundaries.
- Amaru epoch-transition behavior sufficient to find, reproduce, and verify the
  upstream fix for a deterministic panic.
- Mixed mini-protocol state-machine delivery to both real implementations,
  including every required protocol/departure cell locally and in Antithesis.

### Partially tested

- KES under adversity.
- Mixed recovery and convergence under Antithesis faults.
- Bootstrap/state lifecycle and consumer synchronization.
- Deep fork/rollback behavior when observers or exclusion contracts were not
  reliable.
- Resource pressure and mempool effects.

### Not yet defensibly tested

- A systematic 20,000-block application/validation performance baseline.
- Node-level offered/accepted/included/adopted transaction goodput.
- Full mini-protocol transcripts correlated to node-internal processing.
- VM/Plutus execution throughput and latency.
- Saturation points and backlog-drain curves.
- Comprehensive peer-selection/peer-sharing poisoning and diversity policies.
- Full protocol/ledger/consensus coverage across current Cardano-node and Amaru
  releases.

## Gate for changing a cell to proven

1. Exact source revision, images/digests, scenario, seed, and duration retained.
2. Intended real target identity confirmed.
3. Setup complete before evidence is counted.
4. Workload and every required coverage cell observed at non-zero counts.
5. Fault scope matches the intended containers and identities.
6. Assertions expose their component inputs rather than only one aggregate bit.
7. Logs/probes support attribution to SUT, harness, infrastructure, known issue,
   or platform.
8. Evidence bundle is retained and independently inspectable.
