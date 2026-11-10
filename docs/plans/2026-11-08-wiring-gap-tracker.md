# Local wiring campaign: gap and failure tracker

Generated 2026-11-09 19:20 EST from the campaign ledger and the run bundles. Hand-kept causes live in
`docs/plans/wiring-gap-notes.json`; regenerate after every run. Scenarios kept out of the campaign plan as declared gaps
(opcert frozen pins, two-release mixed minors, pcap/sudo, byzantine tmux relaunch) are listed in
`docs/plans/2026-11-08-local-wiring-campaign-plan.md`. Latest outcome per scenario:

| Outcome | Scenarios |
|---|---|
| error | 4 |
| fail | 1 |
| pass | 116 |

| Status | Group | Count |
|---|---|---|
| validated-by-detection | validated by detection | 1 |
| removed | removed from the repo | 4 |

## validated-by-detection: validated by detection

Cause: VALIDATED (user, 2026-11-09): the run fails because it correctly detects an open Amaru bug. On the latest Amaru v10.11.20261029+23031e0 a coin above i64::MAX panics the node (value.rs:337 'Lovelace exceeds i64::MAX'); earlier also on 10.11.20260912
Fix: none in DWARF; finding still open upstream

- `ledger-submit-value-coin-i64-overflow-regression-latest-amaru` (fail, run 20261109T183751Z-499334eb): value_coin_overflow_regression_passed: fresh-funded value-overflow security finding observed

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
