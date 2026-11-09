# Local wiring campaign: gap and failure tracker

Generated 2026-11-09 12:48 EST from the campaign ledger and the run bundles. Hand-kept causes live in
`docs/plans/wiring-gap-notes.json`; regenerate after every run. Scenarios kept out of the campaign plan as declared gaps
(opcert frozen pins, two-release mixed minors, pcap/sudo, byzantine tmux relaunch) are listed in
`docs/plans/2026-11-08-local-wiring-campaign-plan.md`. Latest outcome per scenario:

| Outcome | Scenarios |
|---|---|
| error | 5 |
| fail | 15 |
| pass | 100 |
| prepare-failed | 1 |

| Status | Group | Count |
|---|---|---|
| void-pass | weak proxy fault modes | 6 |
| rerun-pending | CBOR dataset | 1 |
| open | block-apply bridge | 3 |
| open | profile prepare failures | 2 |
| open | weak proxy fault modes | 1 |
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
- `runtime-substrate-blockfetch-invalid-range-example-smoke` (pass, run 20261108T190555Z-1ba0e315): pass
- `runtime-substrate-blockfetch-range-mismatch-example-smoke` (pass, run 20261108T190640Z-9d2f3cdb): pass
- `runtime-substrate-chainsync-nonincrementing-height-example-smoke` (pass, run 20261108T190853Z-27f8174e): pass
- `runtime-substrate-chainsync-nonmonotonic-slot-example-smoke` (pass, run 20261108T190938Z-7e1ed1e1): pass
- `runtime-substrate-chainsync-parent-discontinuity-example-smoke` (pass, run 20261108T191023Z-2a8f0763): pass

## rerun-pending: CBOR dataset

Cause: dataset is mounted, but the reference verifier image cardano-cbor-dataset:a7561cd is absent on the box
Fix: building cardano-cbor-dataset:a7561cd on the box from Dockerfile.dwarf (snapshot apt fix, pins unchanged), 2026-11-09

- `cardano-amaru-cbor-dataset-auxiliary-data-differential` (fail, run 20261109T151604Z-1833006a): cardano_cbor_dataset_differential_clean: expected a clean, non-vacuous pinned CBOR dataset differential run that reached the reference and candidate for every required category

## open: block-apply bridge

Cause: forge_block and serve_crafted_block are "deferred" stubs (external cod-forge and wire tools); fixtures are not staged into the run dir (prop3.tx not found)
Fix: port the forge/serve bridge into DWARF; stage fixtures; 11 more block-apply scenarios are outside the campaign for the same reason

- `conway-gov-scriptcontext-proposals-votes-fidelity-amaru-cardano-node` (fail, run 20261109T153133Z-33835cf1): runtime_tx_submit_differential: [Errno 2] No such file or directory: '/var/dwarf/runs/20261109T153133Z-33835cf1/fixture/gov_scriptcontext/prop3.tx'
- `ledger-block-apply-isvalid-false-donation-value-creation-amaru-cardano-node` (fail, run 20261109T153516Z-3cae5421): fail
  - Cause: same deferred forge/serve bridge
  - Fix: same as above
- `ledger-epoch-boundary-donation-treasury-realization-amaru-cardano-node` (fail, run 20261109T153907Z-8fab3645): fail
  - Cause: same deferred forge/serve bridge
  - Fix: same as above

## open: profile prepare failures

Cause: F109 works: profile-g now deploys. But profile-g ("haskell-preview2-proof") runs on the PUBLIC preview network, so readiness stays semantic_health_not_ready (cold nodes cannot sync): same class F96 fixed for scenarios
Fix: Option A (kept as a public-preview profile; moving it to a private devnet would remove its purpose): profile-g stays deployed between runs (campaign KEEP_PROFILES) and syncs the public preview chain; its 2 scenarios rerun once the node reports semantic health. 2026-11-09 10:29: syncing, slot 1.47M, epoch 18

- `phase3-runtime-haskell-preview2-baseline-delay-loss-recheck-capability-demo` (prepare-failed, run none): readiness blocked: {"stage": "blocked", "reason_code": "semantic_health_not_ready", "availability_state": "ready", "semantic_health": false, "topology_identity": false}
- `phase3-runtime-haskell-preview2-baseline-drop-reset-recheck-capability-demo` (fail, run 20261109T155620Z-9c805486): load_events_are_ok: fail

## open: weak proxy fault modes

Cause: after F127: 0 range requests went through the proxy (target fetched elsewhere); observed_peak_blocks_in_memory is a constant
Fix: craft the named violation with the F130 proxy: replay an earlier genuinely-signed header (chain-sync modes) or block (range/continuity), corrupt the range request; rejection = target drops the connection; measure per-batch peak from the proxy

- `runtime-substrate-blockfetch-range-pressure-example-smoke` (fail, run 20261109T152553Z-3d9f5d98): blockfetch_range_pressure_bounded: expected BlockFetch range pressure to stay inside configured resource bounds

## review: known upstream-fixed Amaru bugs

Cause: NOT NOVEL: Amaru 10.11.20260918 (aedfe797, 2026-09-18) aborts with "thread 'ledger' has overflowed its stack" on the Delay^28000 vector; upstream fixed it in d9bb18e7 "fix: decode uplc with smaller stack" (2026-09-19), first in v10.11.20260925. DWARF detects the pre-fix build correctly (evidence: run 90c557db, F104 node log + exit 134)
Fix: rerun against a post-0925 Amaru to prove the fix; scenario should report "target crashed" as its own outcome

- `ledger-submit-uplc-deep-delay-stack-safety-differential-amaru-cardano-node` (fail, run 20261109T154257Z-9f4ac73a): uplc_deep_delay_stack_safety_matches: UPLC deep-delay evidence is incomplete or inconclusive

## review: review list

Cause: regression reproduces the known Amaru coin-overflow DoS on 10.11.20260912 (frozen b159172; earlier note said 0918, corrected 2026-11-09): after the i64-max+1 case the Amaru submit API stops answering ("fresh-funded value-overflow security finding observed"). The scenario fails because the bug is still present, not because DWARF is broken
Fix: none in DWARF; decide whether this regression should report "finding still present" as its own outcome. F104 keeps the Amaru log on the next run

- `ledger-submit-value-coin-i64-overflow-regression-latest-amaru` (fail, run 20261109T155009Z-fc5026ab): value_coin_overflow_regression_passed: fresh-funded value-overflow security finding observed

## review: smoke-scenario assertion verdicts

Cause: the scenario ran and an assertion failed; decide whether the node, the scenario or the assertion is wrong
Fix: none yet

- `runtime-substrate-compound-eclipse-recovery-example-smoke` (fail, run 20261109T151940Z-33822761): chainsync_responder_rollback_then_forward_clean: expected producer fork-switch to yield rollback-then-forward follower behavior
- `runtime-substrate-compound-local-query-recovery-example-smoke` (fail, run 20261109T152147Z-397146f4): k_bound_rollback_recovered: expected a within-k rollback to recover consistently with non-trivial connectivity and observed slot transitions
- `runtime-substrate-compound-mempool-relay-epoch-boundary-example-smoke` (fail, run 20261109T152725Z-59a5e4f8): epoch_boundary_timing_within_bounds: expected epoch boundary to occur within the configured slot window
- `runtime-substrate-compound-stake-snapshot-hf-boundary-example-smoke` (fail, run 20261109T151824Z-b2e095be): stake_snapshot_freeze_consistent: expected stake snapshot to remain byte-stable across the freeze window
- `runtime-substrate-compound-time-skew-epoch-boundary-example-smoke` (fail, run 20261109T153021Z-ac6916db): epoch_boundary_timing_within_bounds: expected epoch boundary to occur within the configured slot window
- `runtime-substrate-eclipse-topology-example-smoke` (fail, run 20261109T152341Z-c20e9221): ledger_peer_stake_weight_preserved: expected stake-weight delta <= 0.05

## declared-gap: byzantine/amaru tmux relaunch

Cause: needs the host tmux Amaru relaunch model
Fix: none yet

- `phase1-runtime-live-amaru-parity-baseline` (error, run none): controller run failed

## declared-gap: superseded

Cause: assertion deliberately superseded by the opcert header scenarios
Fix: remove or repoint the smoke scenario

- `runtime-substrate-praos-header-assertion-example-smoke` (fail, run 20261109T152923Z-37117ba3): praos_header_assertion_rejected: superseded by runtime_opcert_header_cases; header rejection is proven by the opcert header scenarios

## removed: removed from the repo

Cause: scenario no longer exists
Fix: none yet

- `conway-scriptcontext-txinfo-fidelity-differential-amaru-cardano-node` (error, run none): RuntimeError: resolve 400: scenario_id: load.runtime_plutus_phase2_differential_observation is not present in the primitive registry; assertions.plutus_phase2_differential_equivalent is not present in the primitive registry
- `runtime-substrate-plutus-phase2-donointervene-retry-clean-example-smoke` (error, run none): RuntimeError: resolve 400: scenario_id: load.runtime_plutus_phase2_submit_probe is not present in the primitive registry; assertions.plutus_phase2_donotintervene_retry_clean is not present in the primitive registry
- `runtime-substrate-plutus-phase2-exunits-cost-model-string-undercharge-mixed-amaru` (error, run none): RuntimeError: resolve 400: scenario_id: load.runtime_plutus_phase2_submit_probe is not present in the primitive registry; assertions.plutus_phase2_exunits_overrun_rejected is not present in the primitive registry
- `runtime-substrate-plutus-phase2-isvalid-mismatch-rejected-example-smoke` (error, run none): RuntimeError: resolve 400: scenario_id: load.runtime_plutus_phase2_submit_probe is not present in the primitive registry; assertions.plutus_phase2_isvalid_mismatch_rejected is not present in the primitive registry

## follow-ups (not scenario failures)

- **self-compose-taps-missing** (after scenarios work): Corrected 2026-11-08: the runs carry NO measurements/ folder at all. Taps are not attached on self-compose substrates: 0 taps in 18 passing self-compose-cardano, 5 self-compose-amaru, 4 plutus-onchain runs (profile runs do carry them). Also: 9 catalog taps never ran in a passing run (all 5 cardano-patched, 2 coverage-production-paths, amaru-patched-header-validation, cardano-stock-header-validation); cardano-external-restart-readiness ran 21 times, always unavailable.
- **loopback-probe-audit** (with host-model helpers): runtime_topology_fault.py:169 connect_ex(127.0.0.1, port) may have the same unreachable-loopback problem as F106; check it when porting host-model helpers.
- **prepare-availability-before-deploy** (with profile prepare failures): run/prepare deploys a profile (~15 min) and only then reports availability requires-build; the availability check should run before the deploy.
- **txsubmission-window-cardano-node** (after the controller volume action): runtime_txsubmission_probe window_pressure never receives MsgRequestTxIds from a cardano-node target (3 scenarios: eclipse-txsubmission, snapshot-pause-txsubmission, snapshot-mid-run-corrupt), even after the init delay (F119, run 0571a907: waited 47 s, then 2 s timeout). The same mode passes against Amaru, and unexpected_body passes against cardano-node, so the connection works. Next: enable Net.ConnectionManager/InboundGovernor/TxSubmission traces for one run and read the release's ouroboros-network tx-submission inbound logic (unpromoted inbound peer / TxSubmissionLogicV2) before changing the probe.
- **epoch-and-ledger-producers** (after the easier groups): 13 unwired assertions in 5 scenarios (3 epoch-boundary compound smokes, eclipse-topology ledger peers, local-query chain switch). runtime_epoch_boundary_probe.py measures Amaru (snapshot / pots.dump log lines) but returns observation_overrides unchanged and uses other keys than the assertions read; it has no cardano-node path. Plan: one producer contract writing the assertion keys (epoch_boundary, stake_snapshot, reward_calculation, leadership_schedule) with source + per-node results; cardano-node via its own image cardano-cli LSQ (tip+genesis epochLength, stake-snapshot hashes, ADA conservation from ledger-state, leadership-schedule twice); Amaru via its log lines mapped to the same keys; Amaru leadership = adopted blocks came from elected leaders (header-validation VRF, already tapped); boundaries are observed naturally on testnet_42 (short epochs). Then switch the 5 scenarios to resolved-plan.
- **byzantine-isolation-primitive** (done): byzantine_isolation_observed has no scenario since F130 (its 'no link either way' expectation does not hold for cardano-node: inbound accepted, local roots retried); test_every_builtin_primitive_has_a_source_backed_scenario_reference fails until it is retired or given a correct use. DONE: retired (F135), user decision 2026-11-09.
- **F133-override-audit** (in progress): F133: observation overrides can no longer replace or supply measurement keys. 14 passing scenarios that use override-writing scripts are re-run (#26) to confirm their passes are real.
- **experimental-protocols-default-change** (note): CHANGED 2026-11-09: cardano-only devnets now run ExperimentalProtocolsEnabled=false (was cardano-testnet default true, offering N2N V16). Override per scenario with substrate.cardano_experimental_protocols: true. Sample of cardano-only scenarios rerun after deploy to confirm nothing depended on V16.
- **self-compose-taps-final-rerun** (after all scenarios pass (user decision 2026-11-09, option b)): All 280 runs have exported bundles (0 missing). Tap output: 42 of 118 latest runs; 12 library runs have none by design; ~64 runtime runs on self-composed devnets have no taps attached (resolver attaches only to profile/attached runs). Plan: fix tap attachment for self-composed substrates, then one full rerun of all scenarios to capture taps. Also pending later: F138 cardano-only sample check (ExperimentalProtocolsEnabled=false).
