# Local wiring campaign: gap and failure tracker

Generated 2026-11-08 19:09 EST from the campaign ledger and the run bundles. Hand-kept causes live in
`docs/plans/wiring-gap-notes.json`; regenerate after every run. Scenarios kept out of the campaign plan as declared gaps
(opcert frozen pins, two-release mixed minors, pcap/sudo, byzantine tmux relaunch) are listed in
`docs/plans/2026-11-08-local-wiring-campaign-plan.md`. Latest outcome per scenario:

| Outcome | Scenarios |
|---|---|
| error | 3 |
| fail | 60 |
| pass | 52 |
| prepare-failed | 6 |

| Status | Group | Count |
|---|---|---|
| rerun-pending | CBOR dataset | 1 |
| rerun-pending | Rust runtime tools | 1 |
| rerun-pending | bundle-tool fixtures | 3 |
| rerun-pending | checkpoint evidence | 1 |
| rerun-pending | install defaults | 1 |
| rerun-pending | profile-a helper family | 20 |
| investigate | Amaru mixed compose startup gate | 6 |
| investigate | ledger differential controls | 4 |
| investigate | profile prepare failures | 6 |
| review | review list | 3 |
| review | smoke-scenario assertion verdicts | 17 |
| declared-gap | byzantine/amaru tmux relaunch | 1 |
| declared-gap | superseded | 1 |
| removed | removed from the repo | 4 |

## rerun-pending: CBOR dataset

Cause: dataset not mounted into the controller
Fix: DWARF_CBOR_DATASET_DIR mount (deploy 2ef565b8)

- `cardano-amaru-cbor-dataset-auxiliary-data-differential` (fail, run 20261108T185159Z-83ade908): cardano_cbor_dataset_differential_clean: expected a clean, non-vacuous pinned CBOR dataset differential run that reached the reference and candidate for every required category

## rerun-pending: Rust runtime tools

Cause: controller had no cargo
Fix: F98 host-built extractor (deploy 2ef565b8)

- `cardano-lsq-extract-debug-epoch-state` (fail, run 20261108T185226Z-4592aee8): runtime_cardano_lsq_extract: [Errno 2] No such file or directory: 'cargo'

## rerun-pending: bundle-tool fixtures

Cause: April demo runs not shipped
Fix: f56f9ecc fixtures

- `runtime-bundle-chain-verify-example-smoke` (fail, run 20261108T185206Z-831e6d83): bundle_chain_verify_clean: expected runtime_bundle_chain_verify to report an all-verified chain
- `runtime-bundle-summary-compose-example-smoke` (fail, run 20261108T185213Z-57fc4abe): bundle_summary_compose_completed_clean: expected runtime_bundle_summary_compose to emit a non-empty summary bundle
- `runtime-forensic-snapshot-example-smoke` (fail, run 20261108T185220Z-5234aa74): forensic_snapshot_emitted_clean: expected runtime_forensic_snapshot to emit a non-empty snapshot bundle

## rerun-pending: checkpoint evidence

Cause: checkpoint health reported only as a boolean
Fix: F93 per-node health (deployed after rerun #2)

- `runtime-substrate-checkpoint-resume-recovers-example-smoke` (fail, run 20261108T185736Z-cf01522f): substrate_checkpoint_recorded_clean: expected runtime_substrate_checkpoint to emit a non-empty checkpoint and restart the substrate cleanly

## rerun-pending: install defaults

Cause: raw LocalTxSubmission helper image not pulled
Fix: F89c pre-pull (deployed after rerun #2)

- `governance-zero-denominator-admission-differential-amaru-cardano-node` (fail, run 20261108T194020Z-b01f3652): runtime_governance_zero_denominator_admission: raw LocalTxSubmission helper image inspection failed

## rerun-pending: profile-a helper family

Cause: missing Rust tools, removed fetch-chain-headers, host-loopback endpoints, 125-byte socket path
Fix: F94 short socket path + F98 tools and container endpoints (deploys 2ef565b8+)

- `m3-runtime-blockfetch-historical-range-burst` (fail, run 20261108T202135Z-2d2aad18): load_events_are_ok: fail
- `m3-runtime-blockfetch-multi-peer-historical-range` (fail, run 20261108T202208Z-9537720a): load_events_are_ok: fail
- `m3-runtime-blockfetch-port-delay-bounded-success` (fail, run 20261108T202242Z-8ea1eac8): fail
- `m3-runtime-blockfetch-port-drop-isolated-peer` (fail, run 20261108T201827Z-0181185b): fail
- `m3-runtime-blockfetch-port-drop-timeout` (fail, run 20261108T202315Z-43ff4c04): fail
- `m3-runtime-blockfetch-stale-single-point-rejection` (fail, run 20261108T202348Z-722fcd02): load_events_are_ok: fail
- `m3-runtime-chainsync-historical-point-burst` (fail, run 20261108T202422Z-c3f4f1f1): load_events_are_ok: fail
- `m3-runtime-node2-copied-state-recovery` (fail, run 20261108T202455Z-7957b149): load_events_are_ok: fail
- `m3-runtime-profile-restart-tip-recovery` (error, run none): preflight_failed
- `phase3-runtime-generated-haskell3-connection-state-freeze-rollup-capability-demo` (fail, run 20261108T203749Z-ca1d21cd): load_events_are_ok: fail
- `phase3-runtime-generated-haskell3-freeze-recover-port-drop-capability-demo` (fail, run 20261108T203826Z-10a57844): fail
- `phase3-runtime-generated-haskell3-gc-capture-primitive` (fail, run 20261108T203904Z-c33b1d49): load_events_are_ok: fail
- `phase3-runtime-profile-a-copied-state-chainsync-divergence-primitive` (fail, run 20261108T202723Z-75e8774a): load_events_are_ok: fail
- `phase3-runtime-profile-a-copied-state-divergence-primitive` (fail, run 20261108T202926Z-33f3a15d): load_events_are_ok: fail
- `phase3-runtime-profile-a-copied-state-postremediation-blockfetch-primitive` (fail, run 20261108T203130Z-b888d17b): load_events_are_ok: fail
- `phase3-runtime-profile-a-observability-port-delay-timeout-rollup-capability-demo` (error, run none): preflight_failed
- `phase3-runtime-profile-a-partition-rejoin-capability-demo` (fail, run 20261108T201901Z-fd85695c): load_events_are_ok: fail
- `phase3-runtime-profile-a-pcap-capture-primitive` (fail, run 20261108T203333Z-962b1677): load_events_are_ok: fail
- `phase3-runtime-profile-a-restart-postrecovery-blockfetch-primitive` (fail, run 20261108T203406Z-f44de3a3): load_events_are_ok: fail
- `phase3-runtime-profile-a-syscall-trace-primitive` (fail, run 20261108T203609Z-3197a9ed): load_events_are_ok: fail

## investigate: Amaru mixed compose startup gate

Cause: Amaru+cardano mixed compose: ready_node_count=0 at the startup gate on 42fdc8f0; the later plutus-onchain runs on the same shape pass on 343ee0c1
Fix: re-run on 343ee0c1 first; read failed-substrate-logs.txt if it repeats

- `cbor-strictness-witness-arity-vkey-noncurve-differential-amaru-cardano-node` (fail, run 20261108T192654Z-16a203a3): runtime_compose_substrate: compose_substrate_docker raise RuntimeError( RuntimeError: amaru compose substrate did not intersect, advance, and converge before the startup gate failed (ready_node_count=0, blocking_signals={}; substrate torn d
- `conway-gov-scriptcontext-proposals-votes-fidelity-amaru-cardano-node` (fail, run 20261108T192216Z-29c38dbd): runtime_compose_substrate: compose_substrate_docker raise RuntimeError( RuntimeError: amaru compose substrate did not intersect, advance, and converge before the startup gate failed (ready_node_count=0, blocking_signals={}; substrate torn d
- `conway-phase1-validation-rules-differential-amaru-cardano-node` (fail, run 20261108T193126Z-372813fd): runtime_compose_substrate: compose_substrate_docker raise RuntimeError( RuntimeError: amaru compose substrate did not intersect, advance, and converge before the startup gate failed (ready_node_count=0, blocking_signals={}; substrate torn d
- `ledger-block-apply-isvalid-false-donation-value-creation-amaru-cardano-node` (fail, run 20261108T194334Z-465490e0): runtime_compose_substrate: compose_substrate_docker raise RuntimeError( RuntimeError: amaru compose substrate did not intersect, advance, and converge before the startup gate failed (ready_node_count=0, blocking_signals={}; substrate torn d
- `ledger-epoch-boundary-donation-treasury-realization-amaru-cardano-node` (fail, run 20261108T194821Z-bd5ce162): runtime_compose_substrate: compose_substrate_docker raise RuntimeError( RuntimeError: amaru compose substrate did not intersect, advance, and converge before the startup gate failed (ready_node_count=0, blocking_signals={}; substrate torn d
- `runtime-substrate-txsubmission-unexpected-body-mixed-amaru` (fail, run 20261108T184729Z-9998652d): runtime_compose_substrate: compose_substrate_docker raise RuntimeError( RuntimeError: amaru compose substrate did not intersect, advance, and converge before the startup gate failed (ready_node_count=0, blocking_signals={}; substrate torn d

## investigate: ledger differential controls

Cause: the control did not complete (likely the same tx-submission init-delay race as F97c)
Fix: apply the F97c propagation wait to these runners

- `ledger-submit-empty-redeemers-container-differential-amaru-cardano-node` (fail, run 20261108T195249Z-ebc2f523): redeemer_container_differential_matches: fresh ledger-control evidence is incomplete or inconclusive
- `ledger-submit-inline-datum-tag24-trailing-differential-amaru-cardano-node` (fail, run 20261108T195705Z-323703b1): inline_datum_tag24_boundary_matches: inline-datum boundary evidence is incomplete or inconclusive
- `ledger-submit-multiasset-compact-boundary-differential-amaru-cardano-node` (fail, run 20261108T200039Z-127add6f): runtime_multiasset_compact_boundary: compact-boundary controls are incomplete; boundary case was not submitted
- `ledger-submit-uplc-deep-delay-stack-safety-differential-amaru-cardano-node` (fail, run 20261108T200555Z-a281e253): uplc_deep_delay_stack_safety_matches: UPLC deep-delay evidence is incomplete or inconclusive

## investigate: profile prepare failures

Cause: topology apply failed in the controller
Fix: none yet

- `cardano-n2n-handshake-version-table-forward-compat-1112` (prepare-failed, run none): TopologyActionError: controller runtime failed
- `client-example-cbor-decoding-amaru-portable-20260918` (prepare-failed, run none): readiness blocked: {}
- `client-example-plutus-vm-amaru-portable-20260918` (prepare-failed, run none): TopologyActionError: controller runtime failed
- `consensus-chainhold-upstream-differential` (prepare-failed, run none): readiness blocked: {}
- `phase3-runtime-haskell-preview2-baseline-delay-loss-recheck-capability-demo` (prepare-failed, run none): TopologyActionError: controller runtime failed
- `phase3-runtime-haskell-preview2-baseline-drop-reset-recheck-capability-demo` (prepare-failed, run none): TopologyActionError: controller runtime failed

## review: review list

Cause: the assertion fails because the known coin-overflow finding reproduces; verdict wording needs a decision (regression scenario reports the finding as failure)
Fix: none yet

- `ledger-submit-value-coin-i64-overflow-regression-latest-amaru` (fail, run 20261108T201358Z-e68e548e): value_coin_overflow_regression_passed: fresh-funded value-overflow security finding observed
- `runtime-substrate-byzantine-blockfetch-example-smoke` (fail, run 20261108T183548Z-c3e5ddf0): peer_eviction_within_seconds: expected honest nodes to evict byzantine peers within the allowed window
- `runtime-substrate-compound-panic-bootstrap-example-smoke` (fail, run 20261108T190255Z-c268b791): bootstrap_assumptions_safe: expected bootstrap assumptions to remain explicit and downgrade-free

## review: smoke-scenario assertion verdicts

Cause: the scenario ran and an assertion failed; decide whether the node, the scenario or the assertion is wrong
Fix: none yet

- `runtime-substrate-blockfetch-range-pressure-example-smoke` (fail, run 20261108T190726Z-b45cd0db): blockfetch_range_pressure_bounded: expected BlockFetch range pressure to stay inside configured resource bounds
- `runtime-substrate-bootstrap-topology-concentration-example-smoke` (fail, run 20261108T190819Z-e64504c2): topology_bootstrap_diversity_preserved: expected bootstrap topology to preserve the minimum honest peer diversity floor
- `runtime-substrate-compound-eclipse-recovery-example-smoke` (fail, run 20261108T185326Z-203ba281): honest_peer_set_uncompromised: expected each honest node to retain the minimum honest-peer count without capture and with real non-zero tip evidence
- `runtime-substrate-compound-eclipse-txsubmission-example-smoke` (fail, run 20261108T183639Z-d1a27369): honest_peer_set_uncompromised: expected each honest node to retain the minimum honest-peer count without capture and with real non-zero tip evidence
- `runtime-substrate-compound-keepalive-topology-capture-example-smoke` (fail, run 20261108T191108Z-464b3a75): honest_peer_set_uncompromised: expected each honest node to retain the minimum honest-peer count without capture and with real non-zero tip evidence
- `runtime-substrate-compound-local-query-recovery-example-smoke` (fail, run 20261108T185512Z-397f51e0): k_bound_rollback_recovered: expected a within-k rollback to recover consistently with non-trivial connectivity and observed slot transitions
- `runtime-substrate-compound-mempool-relay-epoch-boundary-example-smoke` (fail, run 20261108T191158Z-68480dc6): epoch_boundary_timing_within_bounds: expected epoch boundary to occur within the configured slot window
- `runtime-substrate-compound-parser-overlay-forging-example-smoke` (fail, run 20261108T190328Z-50c0f3df): parser_bounds_enforced: expected parser and deserialization bounds to be enforced before unbounded work
- `runtime-substrate-compound-snapshot-pause-txsubmission-example-smoke` (fail, run 20261108T183718Z-b6b14213): snapshot_captured_clean: expected runtime_snapshot_capture to emit a non-empty snapshot and restart the node cleanly
- `runtime-substrate-compound-stake-snapshot-hf-boundary-example-smoke` (fail, run 20261108T185233Z-84fca154): stake_snapshot_freeze_consistent: expected stake snapshot to remain byte-stable across the freeze window
- `runtime-substrate-compound-time-skew-epoch-boundary-example-smoke` (fail, run 20261108T191529Z-1582b313): epoch_boundary_timing_within_bounds: expected epoch boundary to occur within the configured slot window
- `runtime-substrate-duplex-promotion-pressure-example-smoke` (fail, run 20261108T191234Z-e24ec912): duplex_promotion_slot_limit_enforced: expected duplex promotion pressure to respect the configured hard limit
- `runtime-substrate-eclipse-topology-example-smoke` (fail, run 20261108T185641Z-cc3f279c): honest_peer_set_uncompromised: expected each honest node to retain the minimum honest-peer count without capture and with real non-zero tip evidence
- `runtime-substrate-local-submit-stress-example-smoke` (fail, run 20261108T191307Z-1ae0a9ca): local_submit_availability_preserved: expected local-submit stress to preserve node availability and queue limits
- `runtime-substrate-mux-ingress-overrun-example-smoke` (fail, run 20261108T191352Z-5b700816): mux_ingress_overrun_scoped: expected mux ingress overrun to be scoped to the offending bearer
- `runtime-substrate-resource-sync-bandwidth-throttle-example-smoke` (fail, run 20261108T192122Z-49c9e9b6): substrate_quorum_observed: expected quorum_fraction >= 1.0 with real non-zero tip evidence
- `runtime-substrate-snapshot-mid-run-corrupt-recovery-example-smoke` (fail, run 20261108T184223Z-192b55cb): txsubmission_window_enforced: expected negotiated txid window to reject overflow cleanly

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
