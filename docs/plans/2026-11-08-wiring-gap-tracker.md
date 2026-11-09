# Local wiring campaign: gap and failure tracker

Generated 2026-11-09 07:30 EST from the campaign ledger and the run bundles. Hand-kept causes live in
`docs/plans/wiring-gap-notes.json`; regenerate after every run. Scenarios kept out of the campaign plan as declared gaps
(opcert frozen pins, two-release mixed minors, pcap/sudo, byzantine tmux relaunch) are listed in
`docs/plans/2026-11-08-local-wiring-campaign-plan.md`. Latest outcome per scenario:

| Outcome | Scenarios |
|---|---|
| error | 1 |
| fail | 20 |
| pass | 98 |
| prepare-failed | 2 |

| Status | Group | Count |
|---|---|---|
| void-pass | weak proxy fault modes | 7 |
| rerun-pending | CBOR dataset | 1 |
| open | block-apply bridge | 3 |
| open | duplex hard limit | 1 |
| open | profile prepare failures | 2 |
| open | weak proxy fault modes | 1 |
| review | decision needed | 1 |
| review | known upstream-fixed Amaru bugs | 1 |
| review | review list | 1 |
| review | smoke-scenario assertion verdicts | 6 |
| declared-gap | byzantine/amaru tmux relaunch | 1 |
| declared-gap | superseded | 1 |
| removed | removed from the repo | 4 |

## void-pass: weak proxy fault modes

Cause: VOID PASS (weak mode): runtime_chainsync_blockfetch_fault flipped the last byte of the first segment of ANY protocol (usually the handshake) and set "rejected" = node healthy + any proxy traffic; the semantic fields (slot/height deltas, served_blocks, candidate_chain_advanced) were constants
Fix: craft the named violation with the F130 proxy: replay an earlier genuinely-signed header (chain-sync modes) or block (range/continuity), corrupt the range request; rejection = target drops the connection

- `runtime-substrate-blockfetch-continuity-failure-example-smoke` (pass, run 20261108T190424Z-49954d02): pass
- `runtime-substrate-blockfetch-invalid-block-cbor-example-smoke` (pass, run 20261109T113615Z-5b45ab96): pass
  - Fix: F130 (e7a9218b): only MsgBlock corrupted; rejection = target drops the connection; rerun pending
- `runtime-substrate-blockfetch-invalid-range-example-smoke` (pass, run 20261108T190555Z-1ba0e315): pass
- `runtime-substrate-blockfetch-range-mismatch-example-smoke` (pass, run 20261108T190640Z-9d2f3cdb): pass
- `runtime-substrate-chainsync-nonincrementing-height-example-smoke` (pass, run 20261108T190853Z-27f8174e): pass
- `runtime-substrate-chainsync-nonmonotonic-slot-example-smoke` (pass, run 20261108T190938Z-7e1ed1e1): pass
- `runtime-substrate-chainsync-parent-discontinuity-example-smoke` (pass, run 20261108T191023Z-2a8f0763): pass

## rerun-pending: CBOR dataset

Cause: dataset is mounted, but the reference verifier image cardano-cbor-dataset:a7561cd is absent on the box
Fix: building cardano-cbor-dataset:a7561cd on the box from Dockerfile.dwarf (snapshot apt fix, pins unchanged), 2026-11-09

- `cardano-amaru-cbor-dataset-auxiliary-data-differential` (fail, run 20261109T082945Z-88c52cd5): cardano_cbor_dataset_differential_clean: expected a clean, non-vacuous pinned CBOR dataset differential run that reached the reference and candidate for every required category

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

## open: duplex hard limit

Cause: real now (F126): node accepted 24 duplex bearers vs hard_limit 8, but 8 exists only in the probe; the node runs the default AcceptedConnectionsLimit (hard 512)
Fix: add a substrate setting that writes AcceptedConnectionsLimit into the node config; the probe reads the node limit

- `runtime-substrate-duplex-promotion-pressure-example-smoke` (fail, run 20261109T111206Z-67de298e): duplex_promotion_slot_limit_enforced: expected duplex promotion pressure to respect the configured hard limit

## open: profile prepare failures

Cause: F109 works: profile-g now deploys. But profile-g ("haskell-preview2-proof") runs on the PUBLIC preview network, so readiness stays semantic_health_not_ready (cold nodes cannot sync): same class F96 fixed for scenarios
Fix: Option A (kept as a public-preview profile; moving it to a private devnet would remove its purpose): profile-g stays deployed between runs (campaign KEEP_PROFILES) and syncs the public preview chain; its 2 scenarios rerun once the node reports semantic health. 2026-11-09 10:29: syncing, slot 1.47M, epoch 18

- `phase3-runtime-haskell-preview2-baseline-delay-loss-recheck-capability-demo` (prepare-failed, run none): readiness blocked: {"stage": "blocked", "reason_code": "semantic_health_not_ready", "availability_state": "ready", "semantic_health": false, "topology_identity": false}
- `phase3-runtime-haskell-preview2-baseline-drop-reset-recheck-capability-demo` (prepare-failed, run none): readiness blocked: {"stage": "blocked", "reason_code": "semantic_health_not_ready", "availability_state": "ready", "semantic_health": false, "topology_identity": false}

## open: weak proxy fault modes

Cause: after F127: 0 range requests went through the proxy (target fetched elsewhere); observed_peak_blocks_in_memory is a constant
Fix: craft the named violation with the F130 proxy: replay an earlier genuinely-signed header (chain-sync modes) or block (range/continuity), corrupt the range request; rejection = target drops the connection; measure per-batch peak from the proxy

- `runtime-substrate-blockfetch-range-pressure-example-smoke` (fail, run 20261109T111035Z-cde1dc3e): blockfetch_range_pressure_bounded: expected BlockFetch range pressure to stay inside configured resource bounds

## review: decision needed

Cause: after F134/F137 only ExperimentalProtocolsEnabled=true remains (cardano-testnet default on cardano-only devnets; Amaru-bearing devnets already pin false)
Fix: ask: pin ExperimentalProtocolsEnabled=false on cardano-only devnets too (public-network default)

- `runtime-substrate-compound-panic-bootstrap-example-smoke` (fail, run 20261109T122756Z-4bd6fd65): bootstrap_assumptions_safe: expected bootstrap assumptions to remain explicit and downgrade-free

## review: known upstream-fixed Amaru bugs

Cause: NOT NOVEL: Amaru 10.11.20260918 (aedfe797, 2026-09-18) aborts with "thread 'ledger' has overflowed its stack" on the Delay^28000 vector; upstream fixed it in d9bb18e7 "fix: decode uplc with smaller stack" (2026-09-19), first in v10.11.20260925. DWARF detects the pre-fix build correctly (evidence: run 90c557db, F104 node log + exit 134)
Fix: rerun against a post-0925 Amaru to prove the fix; scenario should report "target crashed" as its own outcome

- `ledger-submit-uplc-deep-delay-stack-safety-differential-amaru-cardano-node` (fail, run 20261109T034926Z-90c557db): uplc_deep_delay_stack_safety_matches: UPLC deep-delay evidence is incomplete or inconclusive

## review: review list

Cause: regression reproduces the known Amaru coin-overflow DoS on 10.11.20260918: after the i64-max+1 case the Amaru submit API stops answering ("fresh-funded value-overflow security finding observed"). The scenario fails because the bug is still present, not because DWARF is broken
Fix: none in DWARF; decide whether this regression should report "finding still present" as its own outcome. F104 keeps the Amaru log on the next run

- `ledger-submit-value-coin-i64-overflow-regression-latest-amaru` (fail, run 20261109T035625Z-9592c658): value_coin_overflow_regression_passed: fresh-funded value-overflow security finding observed

## review: smoke-scenario assertion verdicts

Cause: the scenario ran and an assertion failed; decide whether the node, the scenario or the assertion is wrong
Fix: none yet

- `runtime-substrate-compound-eclipse-recovery-example-smoke` (fail, run 20261109T085452Z-bd242424): reconnection_clean: expected the reconnected node to rejoin and match the honest quorum tip with non-trivial connectivity and real reconnect telemetry
- `runtime-substrate-compound-local-query-recovery-example-smoke` (fail, run 20261109T003112Z-310d139c): k_bound_rollback_recovered: expected a within-k rollback to recover consistently with non-trivial connectivity and observed slot transitions
- `runtime-substrate-compound-mempool-relay-epoch-boundary-example-smoke` (fail, run 20261109T004846Z-627ca532): epoch_boundary_timing_within_bounds: expected epoch boundary to occur within the configured slot window
- `runtime-substrate-compound-stake-snapshot-hf-boundary-example-smoke` (fail, run 20261109T002755Z-f717fb32): stake_snapshot_freeze_consistent: expected stake snapshot to remain byte-stable across the freeze window
- `runtime-substrate-compound-time-skew-epoch-boundary-example-smoke` (fail, run 20261109T005228Z-8bf30a31): epoch_boundary_timing_within_bounds: expected epoch boundary to occur within the configured slot window
- `runtime-substrate-eclipse-topology-example-smoke` (fail, run 20261109T085657Z-4e64f09e): ledger_peer_stake_weight_preserved: expected stake-weight delta <= 0.05

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
- **prepare-availability-before-deploy** (with profile prepare failures): run/prepare deploys a profile (~15 min) and only then reports availability requires-build; the availability check should run before the deploy.
- **txsubmission-window-cardano-node** (after the controller volume action): runtime_txsubmission_probe window_pressure never receives MsgRequestTxIds from a cardano-node target (3 scenarios: eclipse-txsubmission, snapshot-pause-txsubmission, snapshot-mid-run-corrupt), even after the init delay (F119, run 0571a907: waited 47 s, then 2 s timeout). The same mode passes against Amaru, and unexpected_body passes against cardano-node, so the connection works. Next: enable Net.ConnectionManager/InboundGovernor/TxSubmission traces for one run and read the release's ouroboros-network tx-submission inbound logic (unpromoted inbound peer / TxSubmissionLogicV2) before changing the probe.
- **epoch-and-ledger-producers** (after the easier groups): 13 unwired assertions in 5 scenarios (3 epoch-boundary compound smokes, eclipse-topology ledger peers, local-query chain switch). runtime_epoch_boundary_probe.py measures Amaru (snapshot / pots.dump log lines) but returns observation_overrides unchanged and uses other keys than the assertions read; it has no cardano-node path. Plan: one producer contract writing the assertion keys (epoch_boundary, stake_snapshot, reward_calculation, leadership_schedule) with source + per-node results; cardano-node via its own image cardano-cli LSQ (tip+genesis epochLength, stake-snapshot hashes, ADA conservation from ledger-state, leadership-schedule twice); Amaru via its log lines mapped to the same keys; Amaru leadership = adopted blocks came from elected leaders (header-validation VRF, already tapped); boundaries are observed naturally on testnet_42 (short epochs). Then switch the 5 scenarios to resolved-plan.
- **byzantine-isolation-primitive** (done): byzantine_isolation_observed has no scenario since F130 (its 'no link either way' expectation does not hold for cardano-node: inbound accepted, local roots retried); test_every_builtin_primitive_has_a_source_backed_scenario_reference fails until it is retired or given a correct use. DONE: retired (F135), user decision 2026-11-09.
- **F133-override-audit** (in progress): F133: observation overrides can no longer replace or supply measurement keys. 14 passing scenarios that use override-writing scripts are re-run (#26) to confirm their passes are real.
