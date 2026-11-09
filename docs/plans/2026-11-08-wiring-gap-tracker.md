# Local wiring campaign: gap and failure tracker

Generated 2026-11-09 00:06 EST from the campaign ledger and the run bundles. Hand-kept causes live in
`docs/plans/wiring-gap-notes.json`; regenerate after every run. Scenarios kept out of the campaign plan as declared gaps
(opcert frozen pins, two-release mixed minors, pcap/sudo, byzantine tmux relaunch) are listed in
`docs/plans/2026-11-08-local-wiring-campaign-plan.md`. Latest outcome per scenario:

| Outcome | Scenarios |
|---|---|
| error | 2 |
| fail | 40 |
| pass | 72 |

| Status | Group | Count |
|---|---|---|
| investigate | checkpoint evidence | 1 |
| open | CBOR dataset | 1 |
| open | LSQ extract scenario | 1 |
| open | block-apply bridge | 3 |
| open | host-model helpers on container profiles | 8 |
| review | known upstream-fixed Amaru bugs | 1 |
| review | profile-a behaviour verdicts | 1 |
| review | review list | 3 |
| review | smoke-scenario assertion verdicts | 17 |
| declared-gap | byzantine/amaru tmux relaunch | 1 |
| declared-gap | superseded | 1 |
| removed | removed from the repo | 4 |

## investigate: checkpoint evidence

Cause: checkpoint wrote 155 entries, but health_after_resume reports every node unhealthy with probed_endpoint null (multihost-docker probe)
Fix: check the resume health probe against compose substrates

- `runtime-substrate-checkpoint-resume-recovers-example-smoke` (fail, run 20261109T003404Z-b8acbc59): substrate_checkpoint_recorded_clean: expected runtime_substrate_checkpoint to emit a non-empty checkpoint and restart the substrate cleanly

## open: CBOR dataset

Cause: dataset is mounted, but the reference verifier image cardano-cbor-dataset:a7561cd is absent on the box
Fix: build it once on the box from ~/cardano-cbor-dataset (dwarf/docs/cardano-cbor-dataset-integration.md, pinned a7561cd); after the campaign so the build does not load the box

- `cardano-amaru-cbor-dataset-auxiliary-data-differential` (fail, run 20261109T002711Z-d2fd42ac): cardano_cbor_dataset_differential_clean: expected a clean, non-vacuous pinned CBOR dataset differential run that reached the reference and candidate for every required category

## open: LSQ extract scenario

Cause: extractor (F98 record) starts, but the scenario has no substrate and hard-codes /tmp/ada2-testnet42-socket/node1.sock from the old host setup
Fix: give the scenario a testnet_42 substrate and take the socket from runtime metadata (runtime_metadata_path + target_node)

- `cardano-lsq-extract-debug-epoch-state` (fail, run 20261109T002747Z-eac8e61b): load_events_are_ok: fail

## open: block-apply bridge

Cause: forge_block and serve_crafted_block are "deferred" stubs (external cod-forge and wire tools); fixtures are not staged into the run dir (prop3.tx not found)
Fix: port the forge/serve bridge into DWARF; stage fixtures; 11 more block-apply scenarios are outside the campaign for the same reason

- `conway-gov-scriptcontext-proposals-votes-fidelity-amaru-cardano-node` (fail, run 20261109T005441Z-b4582ddd): runtime_tx_submit_differential: [Errno 2] No such file or directory: '/var/dwarf/runs/20261109T005441Z-b4582ddd/fixture/gov_scriptcontext/prop3.tx'
- `ledger-block-apply-isvalid-false-donation-value-creation-amaru-cardano-node` (fail, run 20261109T025734Z-9b44faec): fail
  - Cause: same deferred forge/serve bridge
  - Fix: same as above
- `ledger-epoch-boundary-donation-treasury-realization-amaru-cardano-node` (fail, run 20261109T030110Z-b7f049cf): fail
  - Cause: same deferred forge/serve bridge
  - Fix: same as above

## open: host-model helpers on container profiles

Cause: runtime_connection_state.py needs lsof/ss and host pids; the controller has neither the tools nor the node pid namespace
Fix: read connection state from inside the node container (docker exec ss) or from the taps

- `phase3-runtime-generated-haskell3-connection-state-freeze-rollup-capability-demo` (fail, run 20261109T020449Z-e57722f2): load_events_are_ok: fail
- `phase3-runtime-generated-haskell3-gc-capture-primitive` (fail, run 20261109T020619Z-5f7f0c94): load_events_are_ok: fail
  - Cause: runtime_haskell_gc_capture.py restarts the node through tmux and waits on a host pid (timed out on pid 3810297)
  - Fix: restart through the controller container lifecycle; read GC stats from the container
- `phase3-runtime-profile-a-copied-state-chainsync-divergence-primitive` (fail, run 20261109T040526Z-fa691ea6): load_events_are_ok: fail
  - Cause: runtime_profile_recovery_check.py copied-state modes: "stale source node1 is not behind node2: 3119 >= 3119"; the stale-copy setup (replace_node_db on env_root/node-data) does not act on container nodes
  - Fix: port the copy/stop steps to the controller container lifecycle
- `phase3-runtime-profile-a-copied-state-divergence-primitive` (fail, run 20261109T040613Z-db7d59b9): load_events_are_ok: fail
  - Cause: runtime_profile_recovery_check.py copied-state modes: "stale source node1 is not behind node2: 3119 >= 3119"; the stale-copy setup (replace_node_db on env_root/node-data) does not act on container nodes
  - Fix: port the copy/stop steps to the controller container lifecycle
- `phase3-runtime-profile-a-copied-state-postremediation-blockfetch-primitive` (error, run none): preflight_failed
  - Cause: runtime_profile_recovery_check.py copied-state modes: "stale source node1 is not behind node2: 3119 >= 3119"; the stale-copy setup (replace_node_db on env_root/node-data) does not act on container nodes
  - Fix: port the copy/stop steps to the controller container lifecycle
- `phase3-runtime-profile-a-partition-rejoin-capability-demo` (fail, run 20261109T014153Z-ccdfc454): load_events_are_ok: fail
  - Cause: runtime_partition_rejoin_check.py calls sudo (absent in the controller); its chainsync-multi-peer step also hit the header-count bug (F100)
  - Fix: port the helper to container faults (F95/F99 netns path) instead of host sudo
- `phase3-runtime-profile-a-pcap-capture-primitive` (fail, run 20261109T040714Z-14c6cc22): load_events_are_ok: fail
  - Cause: runtime_pcap_capture.py calls sudo
  - Fix: capture in the node netns via the F99 helper pattern (tcpdump with NET_RAW only)
- `phase3-runtime-profile-a-syscall-trace-primitive` (fail, run 20261109T040826Z-50e6f54c): load_events_are_ok: fail
  - Cause: runtime_syscall_trace.py calls sudo
  - Fix: trace from a helper sharing the node pid namespace (SYS_PTRACE only)

## review: known upstream-fixed Amaru bugs

Cause: NOT NOVEL: Amaru 10.11.20260918 (aedfe797, 2026-09-18) aborts with "thread 'ledger' has overflowed its stack" on the Delay^28000 vector; upstream fixed it in d9bb18e7 "fix: decode uplc with smaller stack" (2026-09-19), first in v10.11.20260925. DWARF detects the pre-fix build correctly (evidence: run 90c557db, F104 node log + exit 134)
Fix: rerun against a post-0925 Amaru to prove the fix; scenario should report "target crashed" as its own outcome

- `ledger-submit-uplc-deep-delay-stack-safety-differential-amaru-cardano-node` (fail, run 20261109T034926Z-90c557db): uplc_deep_delay_stack_safety_matches: UPLC deep-delay evidence is incomplete or inconclusive

## review: profile-a behaviour verdicts

Cause: helper raised "node2 unexpectedly recovered to slot 4826 within the bounded window": the node recovered faster than the scenario expects
Fix: decide: is fast recovery a pass, or must the window be tighter

- `m3-runtime-node2-copied-state-recovery` (fail, run 20261109T014538Z-e25ae015): load_events_are_ok: fail

## review: review list

Cause: regression reproduces the known Amaru coin-overflow DoS on 10.11.20260918: after the i64-max+1 case the Amaru submit API stops answering ("fresh-funded value-overflow security finding observed"). The scenario fails because the bug is still present, not because DWARF is broken
Fix: none in DWARF; decide whether this regression should report "finding still present" as its own outcome. F104 keeps the Amaru log on the next run

- `ledger-submit-value-coin-i64-overflow-regression-latest-amaru` (fail, run 20261109T035625Z-9592c658): value_coin_overflow_regression_passed: fresh-funded value-overflow security finding observed
- `runtime-substrate-byzantine-blockfetch-example-smoke` (fail, run 20261109T000948Z-c47ad3c4): peer_eviction_within_seconds: expected honest nodes to evict byzantine peers within the allowed window
  - Cause: node3 design of the smoke scenario questioned
  - Fix: none yet
- `runtime-substrate-compound-panic-bootstrap-example-smoke` (fail, run 20261109T004340Z-e727b128): bootstrap_assumptions_safe: expected bootstrap assumptions to remain explicit and downgrade-free
  - Cause: bootstrap_assumptions_safe verdict needs a human look
  - Fix: none yet

## review: smoke-scenario assertion verdicts

Cause: the scenario ran and an assertion failed; decide whether the node, the scenario or the assertion is wrong
Fix: none yet

- `runtime-substrate-blockfetch-range-pressure-example-smoke` (fail, run 20261109T004532Z-3cec42fb): blockfetch_range_pressure_bounded: expected BlockFetch range pressure to stay inside configured resource bounds
- `runtime-substrate-bootstrap-topology-concentration-example-smoke` (fail, run 20261109T004650Z-8302e1c9): topology_bootstrap_diversity_preserved: expected bootstrap topology to preserve the minimum honest peer diversity floor
- `runtime-substrate-compound-eclipse-recovery-example-smoke` (fail, run 20261109T002903Z-2f996774): honest_peer_set_uncompromised: expected each honest node to retain the minimum honest-peer count without capture and with real non-zero tip evidence
- `runtime-substrate-compound-eclipse-txsubmission-example-smoke` (fail, run 20261109T001036Z-fdf1447c): honest_peer_set_uncompromised: expected each honest node to retain the minimum honest-peer count without capture and with real non-zero tip evidence
- `runtime-substrate-compound-keepalive-topology-capture-example-smoke` (fail, run 20261109T004740Z-6db5acf6): honest_peer_set_uncompromised: expected each honest node to retain the minimum honest-peer count without capture and with real non-zero tip evidence
- `runtime-substrate-compound-local-query-recovery-example-smoke` (fail, run 20261109T003112Z-310d139c): k_bound_rollback_recovered: expected a within-k rollback to recover consistently with non-trivial connectivity and observed slot transitions
- `runtime-substrate-compound-mempool-relay-epoch-boundary-example-smoke` (fail, run 20261109T004846Z-627ca532): epoch_boundary_timing_within_bounds: expected epoch boundary to occur within the configured slot window
- `runtime-substrate-compound-parser-overlay-forging-example-smoke` (fail, run 20261109T004430Z-0229c50c): parser_bounds_enforced: expected parser and deserialization bounds to be enforced before unbounded work
- `runtime-substrate-compound-snapshot-pause-txsubmission-example-smoke` (fail, run 20261109T001131Z-ddd743d5): snapshot_captured_clean: expected runtime_snapshot_capture to emit a non-empty snapshot and restart the node cleanly
- `runtime-substrate-compound-stake-snapshot-hf-boundary-example-smoke` (fail, run 20261109T002755Z-f717fb32): stake_snapshot_freeze_consistent: expected stake snapshot to remain byte-stable across the freeze window
- `runtime-substrate-compound-time-skew-epoch-boundary-example-smoke` (fail, run 20261109T005228Z-8bf30a31): epoch_boundary_timing_within_bounds: expected epoch boundary to occur within the configured slot window
- `runtime-substrate-duplex-promotion-pressure-example-smoke` (fail, run 20261109T004938Z-c2f6ebb1): duplex_promotion_slot_limit_enforced: expected duplex promotion pressure to respect the configured hard limit
- `runtime-substrate-eclipse-topology-example-smoke` (fail, run 20261109T003254Z-e66cb942): honest_peer_set_uncompromised: expected each honest node to retain the minimum honest-peer count without capture and with real non-zero tip evidence
- `runtime-substrate-local-submit-stress-example-smoke` (fail, run 20261109T005030Z-3669ad1d): local_submit_availability_preserved: expected local-submit stress to preserve node availability and queue limits
- `runtime-substrate-mux-ingress-overrun-example-smoke` (fail, run 20261109T005139Z-a89c84af): mux_ingress_overrun_scoped: expected mux ingress overrun to be scoped to the offending bearer
- `runtime-substrate-resource-sync-bandwidth-throttle-example-smoke` (fail, run 20261109T005330Z-3fef1ccb): substrate_quorum_observed: expected quorum_fraction >= 1.0 with real non-zero tip evidence
- `runtime-substrate-snapshot-mid-run-corrupt-recovery-example-smoke` (fail, run 20261109T001659Z-b2ee8cf6): txsubmission_window_enforced: expected negotiated txid window to reject overflow cleanly

## declared-gap: byzantine/amaru tmux relaunch

Cause: needs the host tmux Amaru relaunch model
Fix: none yet

- `phase1-runtime-live-amaru-parity-baseline` (error, run none): controller run failed

## declared-gap: superseded

Cause: assertion deliberately superseded by the opcert header scenarios
Fix: remove or repoint the smoke scenario

- `runtime-substrate-praos-header-assertion-example-smoke` (fail, run 20261108T191455Z-13ba101e): praos_header_assertion_rejected: superseded by runtime_opcert_header_cases; header rejection is proven by the opcert header scenarios

## removed: removed from the repo

Cause: scenario no longer exists
Fix: none yet

- `conway-scriptcontext-txinfo-fidelity-differential-amaru-cardano-node` (fail, run 20261108T193551Z-262f0847): runtime_compose_substrate: compose_substrate_docker raise RuntimeError( RuntimeError: amaru compose substrate did not intersect, advance, and converge before the startup gate failed (ready_node_count=0, blocking_signals={}; substrate torn d
- `runtime-substrate-plutus-phase2-donointervene-retry-clean-example-smoke` (fail, run 20261108T191424Z-fc2fc36c): plutus_phase2_donotintervene_retry_clean: expected DoNotIntervene retry behavior to remain within the configured budget
- `runtime-substrate-plutus-phase2-exunits-cost-model-string-undercharge-mixed-amaru` (fail, run 20261108T191612Z-0208dcc1): runtime_compose_substrate: compose_substrate_docker raise RuntimeError( RuntimeError: amaru compose substrate did not intersect, advance, and converge before the startup gate failed (ready_node_count=0, blocking_signals={}; substrate torn d
- `runtime-substrate-plutus-phase2-isvalid-mismatch-rejected-example-smoke` (fail, run 20261108T192051Z-ff1c394f): plutus_phase2_isvalid_mismatch_rejected: expected phase-2 IsValid mismatch to be rejected with ValidationTagMismatch

## follow-ups (not scenario failures)

- **self-compose-taps-missing** (after scenarios work): Corrected 2026-11-08: the runs carry NO measurements/ folder at all. Taps are not attached on self-compose substrates: 0 taps in 18 passing self-compose-cardano, 5 self-compose-amaru, 4 plutus-onchain runs (profile runs do carry them). Also: 9 catalog taps never ran in a passing run (all 5 cardano-patched, 2 coverage-production-paths, amaru-patched-header-validation, cardano-stock-header-validation); cardano-external-restart-readiness ran 21 times, always unavailable.
- **loopback-probe-audit** (with host-model helpers): runtime_topology_fault.py:169 connect_ex(127.0.0.1, port) may have the same unreachable-loopback problem as F106; check it when porting host-model helpers.

## not yet run (no ledger row; usually in the current rerun)

- `cardano-n2n-handshake-version-table-forward-compat-1112`
- `client-example-cbor-decoding-amaru-portable-20260918`
- `client-example-plutus-vm-amaru-portable-20260918`
- `consensus-chainhold-upstream-differential`
- `phase3-runtime-generated-haskell3-freeze-recover-port-drop-capability-demo`
- `phase3-runtime-haskell-preview2-baseline-delay-loss-recheck-capability-demo`
- `phase3-runtime-haskell-preview2-baseline-drop-reset-recheck-capability-demo`
