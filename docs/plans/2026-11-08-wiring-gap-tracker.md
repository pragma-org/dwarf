# Local wiring campaign: gap and failure tracker

Generated 2026-11-08 22:00 EST from the campaign ledger and the run bundles. Hand-kept causes live in
`docs/plans/wiring-gap-notes.json`; regenerate after every run. Scenarios kept out of the campaign plan as declared gaps
(opcert frozen pins, two-release mixed minors, pcap/sudo, byzantine tmux relaunch) are listed in
`docs/plans/2026-11-08-local-wiring-campaign-plan.md`. Latest outcome per scenario:

| Outcome | Scenarios |
|---|---|
| error | 3 |
| fail | 38 |
| pass | 60 |
| prepare-failed | 13 |

| Status | Group | Count |
|---|---|---|
| void-pass | bundle-tool fixtures | 1 |
| rerun-pending | Rust runtime tools | 1 |
| rerun-pending | bundle-tool fixtures | 1 |
| rerun-pending | container port faults | 4 |
| investigate | checkpoint evidence | 1 |
| investigate | profile prepare failures | 15 |
| open | CBOR dataset | 1 |
| open | LSQ extract scenario | 1 |
| open | block-apply bridge | 1 |
| open | host-model helpers on container profiles | 3 |
| review | profile-a behaviour verdicts | 1 |
| review | review list | 2 |
| review | smoke-scenario assertion verdicts | 17 |
| declared-gap | byzantine/amaru tmux relaunch | 1 |
| declared-gap | superseded | 1 |
| removed | removed from the repo | 4 |

## void-pass: bundle-tool fixtures

Cause: FALSE PASS found: both compared files were absent (missing_in_both) and the diff still counted as a divergence
Fix: F101 d20696c7: runs_dir resolves; bundle_diff_completed_clean fails when a compared file is absent. Must rerun: the earlier pass is void

- `runtime-bundle-diff-example-divergence` (pass, run 20261108T134540Z-6ff1865d): pass

## rerun-pending: Rust runtime tools

Cause: new pallas chain-sync tool worked (exit 0) but fetched 5 headers; the check counts exactly 2 (Amaru BOOTSTRAP_HEADERS_PER_POINT = 2)
Fix: F100 6e6fa76d: wrapper passes max 2

- `m3-runtime-chainsync-historical-point-burst` (fail, run 20261109T014503Z-71ff1722): load_events_are_ok: fail

## rerun-pending: bundle-tool fixtures

Cause: runs_dir dwarf/corpora/bundle-runs was passed unresolved; the controller runs helpers from /, so the bundle was "missing"
Fix: F101 d20696c7

- `runtime-bundle-chain-verify-example-smoke` (fail, run 20261109T002720Z-b24b5702): bundle_chain_verify_clean: expected runtime_bundle_chain_verify to report an all-verified chain

## rerun-pending: container port faults

Cause: netns helper ran non-root: iptables "Permission denied (you must be root)"
Fix: F99 d1babd0e

- `m3-runtime-blockfetch-port-drop-isolated-peer` (fail, run 20261109T014118Z-a82d8749): fail
- `m3-runtime-blockfetch-port-drop-timeout` (fail, run 20261109T014403Z-642208ad): fail
- `phase3-runtime-generated-haskell3-freeze-recover-port-drop-capability-demo` (fail, run 20261109T020539Z-005d48a4): fail
  - Cause: netns helper ran non-root: iptables refused
- `phase3-runtime-profile-a-observability-port-delay-timeout-rollup-capability-demo` (fail, run 20261109T014032Z-f24cc8ce): load_events_are_ok: fail
  - Cause: netns helper ran as the controller image user (non-root): tc failed with RTNETLINK EPERM
  - Fix: F99 d1babd0e: helper runs as uid 0, --cap-drop ALL --cap-add NET_ADMIN, no-new-privileges; proven live read-only on profile-n

## investigate: checkpoint evidence

Cause: checkpoint wrote 155 entries, but health_after_resume reports every node unhealthy with probed_endpoint null (multihost-docker probe)
Fix: check the resume health probe against compose substrates

- `runtime-substrate-checkpoint-resume-recovers-example-smoke` (fail, run 20261109T003404Z-b8acbc59): substrate_checkpoint_recorded_clean: expected runtime_substrate_checkpoint to emit a non-empty checkpoint and restart the substrate cleanly

## investigate: profile prepare failures

Cause: profile-g and profile-v prepare fail in 1 s; the controller redacts the cause ("controller runtime failed"), and the profile cannot be removed ("remove is not allowed by the current topology snapshot")
Fix: reproduce one prepare after rerun #4 and read the controller log

- `cardano-n2n-handshake-version-table-forward-compat-1112` (prepare-failed, run none): TopologyActionError: controller runtime failed
- `client-example-cbor-decoding-amaru-portable-20260918` (prepare-failed, run none): readiness blocked: {}
  - Cause: readiness "blocked" after ~2 min per scenario (profile-a x8 after one fault scenario, za/z Amaru 0918 profiles after ~16 min, chainhold self-compose at once); the driver logged an empty payload
  - Fix: driver now saves the full readiness payload (scratchpad prepare-blocked/); rerun to read reason_code
- `client-example-plutus-vm-amaru-portable-20260918` (prepare-failed, run none): readiness blocked: {}
  - Cause: readiness "blocked" after ~2 min per scenario (profile-a x8 after one fault scenario, za/z Amaru 0918 profiles after ~16 min, chainhold self-compose at once); the driver logged an empty payload
  - Fix: driver now saves the full readiness payload (scratchpad prepare-blocked/); rerun to read reason_code
- `consensus-chainhold-upstream-differential` (prepare-failed, run none): readiness blocked: {}
  - Cause: readiness "blocked" after ~2 min per scenario (profile-a x8 after one fault scenario, za/z Amaru 0918 profiles after ~16 min, chainhold self-compose at once); the driver logged an empty payload
  - Fix: driver now saves the full readiness payload (scratchpad prepare-blocked/); rerun to read reason_code
- `m3-runtime-blockfetch-port-delay-bounded-success` (error, run none): preflight_failed
  - Cause: m3 blockfetch delay/stale scenarios end "preflight_failed" before a run id exists (rerun #3 on F98 343ee0c1)
  - Fix: read the preflight payload on the next run
- `m3-runtime-blockfetch-stale-single-point-rejection` (error, run none): preflight_failed
  - Cause: m3 blockfetch delay/stale scenarios end "preflight_failed" before a run id exists (rerun #3 on F98 343ee0c1)
  - Fix: read the preflight payload on the next run
- `m3-runtime-profile-restart-tip-recovery` (prepare-failed, run none): readiness blocked: {}
  - Cause: readiness "blocked" after ~2 min per scenario (profile-a x8 after one fault scenario, za/z Amaru 0918 profiles after ~16 min, chainhold self-compose at once); the driver logged an empty payload
  - Fix: driver now saves the full readiness payload (scratchpad prepare-blocked/); rerun to read reason_code
- `phase3-runtime-haskell-preview2-baseline-delay-loss-recheck-capability-demo` (prepare-failed, run none): TopologyActionError: controller runtime failed
- `phase3-runtime-haskell-preview2-baseline-drop-reset-recheck-capability-demo` (prepare-failed, run none): TopologyActionError: controller runtime failed
- `phase3-runtime-profile-a-copied-state-chainsync-divergence-primitive` (prepare-failed, run none): readiness blocked: {}
  - Cause: readiness "blocked" after ~2 min per scenario (profile-a x8 after one fault scenario, za/z Amaru 0918 profiles after ~16 min, chainhold self-compose at once); the driver logged an empty payload
  - Fix: driver now saves the full readiness payload (scratchpad prepare-blocked/); rerun to read reason_code
- `phase3-runtime-profile-a-copied-state-divergence-primitive` (prepare-failed, run none): readiness blocked: {}
  - Cause: readiness "blocked" after ~2 min per scenario (profile-a x8 after one fault scenario, za/z Amaru 0918 profiles after ~16 min, chainhold self-compose at once); the driver logged an empty payload
  - Fix: driver now saves the full readiness payload (scratchpad prepare-blocked/); rerun to read reason_code
- `phase3-runtime-profile-a-copied-state-postremediation-blockfetch-primitive` (prepare-failed, run none): readiness blocked: {}
  - Cause: readiness "blocked" after ~2 min per scenario (profile-a x8 after one fault scenario, za/z Amaru 0918 profiles after ~16 min, chainhold self-compose at once); the driver logged an empty payload
  - Fix: driver now saves the full readiness payload (scratchpad prepare-blocked/); rerun to read reason_code
- `phase3-runtime-profile-a-pcap-capture-primitive` (prepare-failed, run none): readiness blocked: {}
  - Cause: readiness "blocked" after ~2 min per scenario (profile-a x8 after one fault scenario, za/z Amaru 0918 profiles after ~16 min, chainhold self-compose at once); the driver logged an empty payload
  - Fix: driver now saves the full readiness payload (scratchpad prepare-blocked/); rerun to read reason_code
- `phase3-runtime-profile-a-restart-postrecovery-blockfetch-primitive` (prepare-failed, run none): readiness blocked: {}
  - Cause: readiness "blocked" after ~2 min per scenario (profile-a x8 after one fault scenario, za/z Amaru 0918 profiles after ~16 min, chainhold self-compose at once); the driver logged an empty payload
  - Fix: driver now saves the full readiness payload (scratchpad prepare-blocked/); rerun to read reason_code
- `phase3-runtime-profile-a-syscall-trace-primitive` (prepare-failed, run none): readiness blocked: {}
  - Cause: readiness "blocked" after ~2 min per scenario (profile-a x8 after one fault scenario, za/z Amaru 0918 profiles after ~16 min, chainhold self-compose at once); the driver logged an empty payload
  - Fix: driver now saves the full readiness payload (scratchpad prepare-blocked/); rerun to read reason_code

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

## open: host-model helpers on container profiles

Cause: runtime_connection_state.py needs lsof/ss and host pids; the controller has neither the tools nor the node pid namespace
Fix: read connection state from inside the node container (docker exec ss) or from the taps

- `phase3-runtime-generated-haskell3-connection-state-freeze-rollup-capability-demo` (fail, run 20261109T020449Z-e57722f2): load_events_are_ok: fail
- `phase3-runtime-generated-haskell3-gc-capture-primitive` (fail, run 20261109T020619Z-5f7f0c94): load_events_are_ok: fail
  - Cause: runtime_haskell_gc_capture.py restarts the node through tmux and waits on a host pid (timed out on pid 3810297)
  - Fix: restart through the controller container lifecycle; read GC stats from the container
- `phase3-runtime-profile-a-partition-rejoin-capability-demo` (fail, run 20261109T014153Z-ccdfc454): load_events_are_ok: fail
  - Cause: runtime_partition_rejoin_check.py calls sudo (absent in the controller); its chainsync-multi-peer step also hit the header-count bug (F100)
  - Fix: port the helper to container faults (F95/F99 netns path) instead of host sudo

## review: profile-a behaviour verdicts

Cause: helper raised "node2 unexpectedly recovered to slot 4826 within the bounded window": the node recovered faster than the scenario expects
Fix: decide: is fast recovery a pass, or must the window be tighter

- `m3-runtime-node2-copied-state-recovery` (fail, run 20261109T014538Z-e25ae015): load_events_are_ok: fail

## review: review list

Cause: node3 design of the smoke scenario questioned
Fix: none yet

- `runtime-substrate-byzantine-blockfetch-example-smoke` (fail, run 20261109T000948Z-c47ad3c4): peer_eviction_within_seconds: expected honest nodes to evict byzantine peers within the allowed window
- `runtime-substrate-compound-panic-bootstrap-example-smoke` (fail, run 20261109T004340Z-e727b128): bootstrap_assumptions_safe: expected bootstrap assumptions to remain explicit and downgrade-free
  - Cause: bootstrap_assumptions_safe verdict needs a human look

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

## not yet run (no ledger row; usually in the current rerun)

- `ledger-block-apply-isvalid-false-donation-value-creation-amaru-cardano-node`
- `ledger-epoch-boundary-donation-treasury-realization-amaru-cardano-node`
- `ledger-submit-empty-redeemers-container-differential-amaru-cardano-node`
- `ledger-submit-inline-datum-tag24-trailing-differential-amaru-cardano-node`
- `ledger-submit-multiasset-compact-boundary-differential-amaru-cardano-node`
- `ledger-submit-uplc-deep-delay-stack-safety-differential-amaru-cardano-node`
- `ledger-submit-value-coin-i64-overflow-regression-latest-amaru`
