# Full tap coverage on the Pages evidence set — design

Date: 2026-11-04. Repo: V10-FRESH. Status: IMPLEMENTED on `feat/full-tap-coverage` (2026-11-04).

Outcome: the Pages default set is four reruns from the fixed collectors — amaru stock
`20261104T161325Z-5eb100bf` (52 concepts covered), amaru patched `20261104T165404Z-54138a1b`
(68), cardano-node stock `20261104T171547Z-f38474f0` (43), cardano-node patched
`20261104T172204Z-c1faac51` (57) — plus the Antithesis snapshot under a documented
`coverage_exception`. Every bound concept carries a value or an entry in
`dwarf/measurements/coverage-exceptions-v1.json`; `profile_manager/tap_coverage.py` is the rule
and Pages staging enforces it on every `coverage_rule` archive. Beyond §4, the reruns surfaced
and fixed: the cardano image has no grep and runs the node under the dynamic loader (process
probe matches the cmdline with shell patterns); cardano-node abbreviates mempool trace txids to
8 hex characters (the visibility join matches by prefix); the resolve step ignored a local
build's verified instrumentation capabilities; the catalog never bound the patched
queue/txsubmission bound-estimate ids (bound now; a built binding supersedes its reserved
placeholder); and cardano-node 11.1.2 defaults to the legacy V1 inbound TxSubmission logic,
so measurement configs set `TxSubmissionLogicVersion: TxSubmissionLogicV2` for both cardano
modes to exercise the v5 residence contract. Zero-occurrence distributions count when their
collector states an `offered_count`. Collectors retain each raw node trace once per run,
content-addressed under `measurements/shared/`, after eight per-collector copies inflated an
archive to 418 MB and the viewer (256 MB expansion limit) refused it.
Follows `docs/plans/2026-11-03-operator-build-workload-measurements-design.md` (the four
workload runs) and `docs/plans/2026-11-04-cross-implementation-compare-design.md` (the
Compare page that shows them side by side).

## 1. Intent

The user's standing requirement: the GitHub Pages default evidence set must come from
however many runs it takes so that every measurement tap has a value. After the four
2026-11-04 workload runs on operator-built Amaru 10.11.20261029 and cardano-node 11.1.2
(stock and patched), 15–19 taps per run still have no value, and six older archives on the
site are thin by construction. Three investigations traced every gap to its cause. This
design closes every gap that DWARF can close, states the three that it cannot, and replaces
the Pages set with runs where the rule holds.

Framing: authorized defensive conformance testing of Amaru and cardano-node for PRAGMA.
Nothing here invents a number: a tap gets a value only when the node or the workload
produced the evidence for it.

## 2. Goals, non-goals, anti-goals

Goals
- Every tap concept has a value on at least one run of each implementation, except the
  three documented exceptions in §6.
- The gaps are closed by fixing DWARF collectors and the workload, then rerunning the four
  legs on cardano-box. No numbers are edited.
- The Pages default set holds only runs that satisfy the rule, plus the Antithesis snapshot.
- A test asserts the rule on the retained reports, so it cannot regress silently.

Non-goals
- No new patches for Amaru (the header-validation tap for 20261029 is a separate Rust
  job, §6). No change to what stock cardano-node exposes.
- No reruns of releases whose images are gone from cardano-box (§7).
- No Antithesis rerun (task 2, Antithesis parity, stays open).

Anti-goals
- Treating "0 with no evidence" as a value. A zero counter counts only when the run offered
  operations the counter could have counted.
- Attributing an entry-node timing to the target. Boundaries measured at the consumer or at
  node2 are labelled as such.
- Sharing host PID namespace with the dashboard container to get process stats.

## 3. Verified facts

- Resources: `ManagedContainerResourceCollector` (`cardano_resources.py:158-219`) samples
  one `docker stats --no-stream` per second and hard-codes `cpu_time_seconds`, `rss_bytes`,
  `fd_count`, `threads` to None; `_counter_delta` (`amaru_resources.py:26-58`) compares
  the first and last samples only. In run 572908c9 `network_rx_bytes` drops from 684000 to
  180 and `disk_read_bytes` from 9660000 to 0 at sample 141: the restart leg resets Docker's
  counters. Both target images have `sh` and `cat` but no `grep`; Amaru is PID 1, cardano-node
  is a child of a `bash` wrapper. The dashboard container has the Docker socket and no host
  PID namespace.
- Accounting: the calibration primitive emits `"backlog": None` (`primitives.py:14038`);
  `backlog_summary` returns unavailable with no reason when no point is numeric. Zero
  counters are `{status, unit, value}` with no `count`/`offered_count`, so `cell_state`
  reads them as unavailable (`measurement_compare.py` positive-evidence rule).
  `_plutus_workload_accounting` copies `submit_to_protocol_response` and
  `submit_to_chain_adoption` only for `cardano-node`
  (`runtime_controlled_plutus_transactions.py:295-298`); the Amaru leg records them but
  they are dropped because the entry is the consumer node. No producer writes
  `submit_to_mempool_visibility`. Both stock mempool collectors key events by transaction
  id (`amaru_stock.py:167-169`, `cardano_stock.py:43-52,130-132`) with timestamps; the
  workload keeps only monotonic clocks.
- Mempool rejection: the only "rejected" transaction is the phase-2-invalid spend
  (`--script-invalid`), which the mempool accepts and the chain includes as invalid.
  Nothing submits a phase-1-invalid transaction.
- Cardano patched taps: the operator build is registered with
  `patch_set_sha256 = 1d45c191…`, `measurement_revision = nanoseconds-v5-antithesis1`
  (the composite v5 + `0008-antithesis-header-import.patch`). **Two fixes that the box ran
  from `deploy/box-workload-v7` never reached `main`:** `203056ec` (the controller applies
  the import correction to local instrumented builds and `build_manifest` resolves the
  composite through the bridge) and `19d6412b` (libsodium `config.sub` in the cardano
  toolchain image). On `main` the box's cardano registration raises "no matching
  exact-source patch recipe"; with `203056ec` cherry-picked it resolves with `toolchain`
  and the bridge `compatibility` contract. Its verified capabilities are still only
  `cardano-patched-protocol-decode` and `cardano-patched-ledger-plutus-stages`
  (`CARDANO_PATCHED_CAPABILITIES`, `measurement_execution.py:105-108`). The three taps
  require `cardano-patched-header-validation-instrumentation` /
  `cardano-patched-blockfetch-queue` / `cardano-patched-txsubmission-residence` plus
  `cardano-patch-identity`. `supports_*` (`cardano_patched.py:284-310`) accept only the
  base v3/v4/v5 shas; the bridge's `_base_identity` maps the composite back to v5 but only
  when `engine == 'antithesis'`. `prepare()` (`:355-363`) also requires
  `runtime_probe_log_sha256`, which no operator registration carries (the registration
  carries `instrumentation_evidence_sha256` and `build_result_sha256`). The v5 recipe's
  patches 0005/0006/0007 contain the three emitters; the collectors read
  `logs/<node>/cardano-measurement.ndjson`, which the patched run already produced.
- Trace severities: `CARDANO_MEASUREMENT_TRACE_SEVERITIES = {"KeepAlive.Remote": "Debug"}`
  (`runtime_compose_substrate.py:264`); `Mempool` runs at Info, and
  `TraceMempoolSynced` had no samples.
- Cardano TxSubmission residence only has samples for transactions that reach node1 from
  a peer; the cardano leg submits to node1 directly over N2C. The Amaru leg already has
  `submission_entry` (p1 | amaru-consumer); `_independent_peer`
  (`runtime_cardano_measurement_calibration.py:1133`) names node2 and its socket.
- Images on cardano-box: only Amaru 10.11.20261029 (stock, instrumented) and cardano-node
  11.1.2 (stock, instrumented). Amaru 20260912/20260918/20260925 and cardano-node 10.7.1
  are gone, so their 2026-10-17 handshake-only runs cannot be rerun.
- Amaru `header_slot_start_to_reception`: this release logs header receipt and slot start
  in different clock domains (collector reason text). Amaru patch set `ca10dff8…`
  (nanoseconds-v3) has no header-validation tap; `supports_amaru_header_validation`
  accepts only the 20260918 nanoseconds-v4 set.

## 4. The how (decided)

### 4.1 Resources collector
`_docker_container_sample` gains a second probe per sample:
`docker exec <id> sh -c '<script>'` where the script finds the node process (PID 1 when
`/proc/1/comm` is `amaru` or `cardano-node`, else the first `/proc/[0-9]*/comm` equal to
`cardano-node`), then prints `/proc/<pid>/status` (VmRSS, Threads), the count of
`/proc/<pid>/fd` entries and `/proc/<pid>/stat` fields 14+15. CPU time =
(utime+stime)/CLK_TCK with CLK_TCK read from `getconf CLK_TCK` once, defaulting to 100
when `getconf` is absent. The four fields fill `rss_bytes`, `threads`, `fd_count`,
`cpu_time_seconds`; a failed exec leaves them None with `sample.errors` noting it, never
aborting the Docker stats sample. A `probe_mode` field records `docker-exec-proc` or
`unavailable`.

`_counter_delta` sums positive step deltas; a drop between consecutive samples counts as
a reset and the sum continues from the new value; the result adds `"resets": n` and
`"delta_rule": "sum-of-steps-with-resets"`. `_distribution` returns
`reason: "no samples from this source"` when empty.

### 4.2 Workload accounting
- Primitive payload: `backlog` = number of in-flight attempts at the time of the event
  (the Plutus workload is closed-loop, so 0 or 1); the event is emitted once per attempt
  with `time` so `backlog_summary` has a curve. `backlog_summary` adds `reason` when empty.
- Zero counters carry evidence: `rejected_operations`, `timed_out_operations` get
  `offered_count = attempted`; `mempool_rejected` (both stock collectors) gets
  `offered_count` = number of mempool receive/attempt events seen. The viewer and Python
  model already read `offered_count` as positive evidence.
- `_plutus_workload_accounting` copies `submit_to_protocol_response` and
  `submit_to_chain_adoption` for Amaru too, with `boundary_node: "<submission entry>"` on
  the attempt, and only when `relay_forwarded` is not False and `fallback_errors` is empty.
  The external collector copies `boundary_node` into each stage distribution as
  `boundary_node` so the catalog hover shows "measured at amaru-consumer".
- `submit_to_mempool_visibility`: the generated wrapper in `_containerized_cardano_workload`
  prints `submit_started_at_ns=<time since epoch>` immediately before the `cardano-cli
  transaction submit` call, and `run_plutus_workload` parses it into the record (not the
  helper-side clock: that would mostly measure the container start-up, 356–626 ms per
  submit in the retained runs). After the leg, the helper joins each spend id with the first
  mempool-received or mempool-accepted event for that id in the stock mempool trace of the
  node that receives it (Amaru: `amaru-relay-1` JSON log `transaction.received`; cardano:
  node1 `TraceMempoolAddedTx`/`AttemptingAdd`), and sets `submit_to_mempool_visibility_nanos`
  on the record. Unmatched ids leave the stage absent (reason unchanged).
- Phase-1-invalid transaction: after each batch's last spend, `run_plutus_workload`
  resubmits the previous accepted spend transaction file (its inputs are now consumed). The
  submit must fail; the record is `{"attempt_id": "plutus-<n>-double-spend", "outcome":
  "rejected", "chain_outcome": "rejected-phase1", ...}` with `rejected_reason` = the CLI
  error's first line. One per batch of 20, so `rejected_operations` ≥ 1 on every leg. On
  the cardano leg the submit goes over N2C to node1, whose stock mempool trace shows
  `TraceMempoolRejectedTx`, so `mempool_rejected` ≥ 1. On the Amaru leg the N2C entry is a
  cardano-node (consumer or p1) whose own mempool refuses the double spend, and
  `amaru-relay-1` never sees it; see §4.7 for how the Amaru mempool rejection is exercised.
- Peer-submitted records (§4.4) drop `submit_to_protocol_response` and
  `submit_to_chain_adoption`: only the target's own socket is authoritative for them. Each
  stage distribution is therefore built from one `boundary_node` and labelled with it
  (`node1` on the cardano leg, `amaru-consumer` on the Amaru leg).

### 4.3 Cardano patched tap identity
- Land the two deploy-only fixes first (`19d6412b`, `203056ec`, cherry-picked onto the
  feature branch; their tests pass, the three remaining failures in
  `test_antithesis_measurements.py` are identical on `main`). The composite then resolves
  on every checkout through `bridge.is_composite_build` / `composite_manifest`; no
  on-disk recipe is needed.
- The bridge already maps the composite identity back to v5 for `engine in {antithesis,
  local}` and `cardano_factory` builds patched factories through the bridge, so the three
  `supports_*` checks see the base v5 sha. What still blocks them: capabilities and the
  probe-log field.
- `instrumented_identity` adds the capabilities granted by the recipe revision:
  v3+ → `cardano-patched-header-validation-instrumentation`, v4+ →
  `cardano-patched-blockfetch-queue`, v5+ → `cardano-patched-txsubmission-residence`, all
  patched → `cardano-patch-identity`. Table `CARDANO_REVISION_CAPABILITIES` in
  `measurement_execution.py`.
- `prepare()` in the three collectors accepts, for `identity_kind == operator-local-config`,
  `verified_instrumentation.evidence_sha256` in place of `runtime_probe_log_sha256`: the
  operator path proves the compiled instrumentation by its evidence bundle; the probe log
  is an artefact of the release-catalog build path. Same relaxation in Amaru's header
  validation gate for symmetry.
- The three taps are enabled through the measurement profile
  `dwarf/measurement-profiles/cardano-security-patched.yaml` (the box request expands it
  with consent), not through the scenario: the scenario file is shared by the stock and
  patched reruns, and an explicit unavailable tap would fail the stock resolution. Scenario
  blocker text updated.
- `CARDANO_MEASUREMENT_TRACE_SEVERITIES` gains `"Mempool": "Debug"`.

### 4.4 Cardano submission entry
`runtime_cardano_measurement_calibration.run_leg` gains `submission_entry`
(`target` | `peer`, default `target`) and `plutus_split` (`0..1`, default 0.5 when
`submission_entry == "peer"`): the first half of the batch is submitted through node1
(authoritative protocol response), the second half through node2's socket
(`_independent_peer`) so node1's TxSubmission inbound and the patched residence tap see
them. Records carry `submission_entry` and `boundary_node`. The scenario sets
`submission_entry: peer`.

### 4.5 BlockFetch queue pressure (Amaru patched `queue_residence`)
`runtime_controlled_sync_range` in the Amaru workload scenario goes from 20 to 200 blocks
so the catch-up fetch has pending requests. No restart hold-down: `restart_readiness` keeps
its current meaning (the primitive's `docker restart --time 30` is a termination grace,
and the two implementations must stay comparable on that card). If `queue_residence`
still has no samples after the rerun, it is reported with its reason and listed in §6.

### 4.6 The rule as a test
`tests/test_tap_coverage_rule.py` is concept-level: using the catalog index
(`measurement_compare._catalog_index`, `catalog_concept`), for each retained workload
report in `docs/evidence/runs/` every concept bound for that implementation and mode has a
numeric primary value on at least one of its bound ids (verdict dicts summed, observed
zeros with evidence count), or the concept is in the per-implementation-and-mode exception
list of §6 with the exact reason string. Reserved placeholder ids
(`handler_queue_residence`, `txsubmission_residence`) therefore pass on the patched run
through their patched alternates and are exceptions on the stock run only. A second
assertion checks that no raw id of the report is unbound. The Pages
staging script refuses an archive that fails the rule unless the manifest entry carries
`"coverage_exception": "<reason>"` (used for the Antithesis snapshot).

### 4.7 Amaru mempool rejection over N2N
`dwarf/scripts/runtime_txsubmission_probe.py` already handshakes with Amaru and answers
`MsgRequestTxIds` with ids. It gains a `serve-transaction` mode: reply with one id and
size, answer the following `MsgRequestTxs` with the CBOR body given on the command line,
then wait for the relay's mempool decision window and close. The Amaru Plutus leg calls
it once per batch with the double-spend transaction (its body is already signed and on
disk), so `amaru-relay-1` evaluates and rejects it: `transaction.rejected` appears in its
mempool trace and `mempool_rejected` ≥ 1. This is the one wire-level addition in the plan
and it is built last; if it does not land, Amaru `mempool_rejected` stays an
evidence-backed observed zero (0 of N received) and is listed in §6 as "not exercised".

## 5. Runs and the Pages set

Four reruns on cardano-box with the new code deployed (`build-image.sh` + `deploy.sh`,
then the prepare/run/export drivers). The Pages manifest keeps the Antithesis snapshot
(with `coverage_exception`) and replaces the four workload archives. The five 2026-10-17
handshake-only archives come off the default set: their images are gone, so they can
never satisfy the rule; they remain in git history and importable. Cap stays 10.

## 6. Exceptions that remain (and why)

| Implementation | Tap | Reason |
| --- | --- | --- |
| Amaru (both) | `header_slot_start_to_reception` | Release logs receipt and slot start in different clocks. Needs an Amaru change. |
| Amaru patched | `amaru-patched-header-validation.*` | Patch set nanoseconds-v3 for 20261029 has no header tap; porting the 20260918 v4 tap is a separate Rust job. |
| cardano stock only | `epoch_transition_duration`, `plutus_vm_duration`, `handler_queue_residence`, `txsubmission_residence` | Stock node boundaries, reserved by contract; the patched run carries `epoch_transition`, `plutus_vm`, `queue_residence_*`, `request_to_terminal_*`. |

| cardano-node (both) | `mempool_sync_duration` | The reruns retained no TraceMempoolSynced record from 11.1.2 even at Mempool Debug (measured, 2026-11-04). |
| Amaru patched | `queue_residence` | The 200-block controlled sync queued no BlockFetch requests behind one another (measured, 2026-11-04). |

| Amaru (both) | `mempool_rejected` | §4.7 fallback, measured: the served double spend was taken over N2N (ids and body requested) but the relay trace records no mempool decision for it; the counter is an evidenced zero of 40, and the consumer-socket refusal is counted in `rejected_operations`. |

The first two were provisional (severity and pressure assumptions); the reruns refuted the
assumptions, so they stay as measured exceptions.

## 7. Left to the builder

Exact shell for the in-container probe (POSIX sh only); where the mempool-visibility join
runs (end of leg in the helper, reading the same trace files the collectors read); the
double-spend record's field names beyond those fixed above; test fixtures (the four real
reports are in the scratchpad fixture file and the retained archives).
