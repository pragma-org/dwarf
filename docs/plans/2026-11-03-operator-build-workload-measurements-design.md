# Operator-build workload measurements — design

Date: 2026-11-03. Status: draft for approval.

## 1. Intent

The four portable e2e runs of 2026-11-03 (operator-built Amaru 10.11.20261029 and
cardano-node 11.1.2, stock and patched, on cardano-box) attach every tap in the
`*-security-default` / `*-security-patched` profiles, but only the taps that a
100-attempt N2N handshake load can trigger record samples. Mempool, ledger
rules, Plutus VM, epoch transition, fork switch, keepalive, tx-submission
residence and restart readiness stay empty on both implementations.

This design adds a **portable workload scenario per implementation** for
operator-prepared builds so that every tap the local engine can serve records
samples in one run, and the resulting four archives replace the handshake-only
set in the Pages default data set.

The work frames as authorized defensive conformance testing for PRAGMA on a
local devnet. It makes no security claim; measurements stay non-gating.

## 2. Goals, non-goals, anti-goals

Goals

- One Amaru scenario and one cardano-node scenario, both `*-portable`
  (operator registration selected at run time, `acknowledge_unknown_versions`),
  that produce samples for every tap listed in section 7 as "expected to fill".
- Reuse the existing calibration helpers, primitives, collectors and Pages
  flow. No parallel tooling.
- Keep the handshake calibration inside the new scenarios so the new archives
  are a superset of the current comparison set.
- Fit inside the controller's 2400 s run ceiling with margin.

Non-goals

- Antithesis or GitHub Actions tap parity (task 2, separate design).
- Performance thresholds, vulnerability claims, or a mixed Amaru/cardano pair.
- Changing the eight-container Amaru substrate or the three-producer Cardano
  substrate.

Anti-goals (would make this a failure even if it "works")

- A tap that reports samples only because a collector contract was loosened
  to match unrelated events.
- A scenario that passes only on the pinned catalog profiles
  (`profile-z…`, `profile-za…`) and not on the operator-latest profiles.
- A run that exceeds the 2400 s ceiling or needs a manual step mid-run.

## 3. Verified constraints (cardano-box, V10-FRESH `77d7c5de` + the three fixes)

Substrate facts

| Item | Amaru (`profile-o-amaru-target-latest-confirmed`) | cardano-node (`profile-n-cardano-latest-confirmed`) |
| --- | --- | --- |
| Measured node | `amaru-relay-1`, N2N :3000, upstream `p1:3001`, inbound peers `amaru-consumer`, `amaru-relay-2` | `node1`, N2N :3001, N2C `/env/socket/node1/sock`, peers node2/node3 (local mesh) |
| Epoch | 400 slots × 0.5 s ≈ 200 s, k = 8, f = 0.2 | 500 slots × 0.1 s = 50 s, k = 5, f = 0.05 |
| Funded key | configurator `/tmp/testnet/utxos/keys/genesis.1.{vkey,skey}` (200 000 000 ADA, verified live) | `env/utxo-keys/utxo1/utxo.{vkey,skey}` (15 000 003 ADA, verified live) |
| Tx entry points | p1 N2C `/state/node.socket` (p1-state volume); `amaru-consumer` N2C `/state/node.socket` (amaru-consumer-state volume, synced, tip query works) | node1 N2C socket; node2/node3 sockets |
| Plutus cost models live | PlutusV1, PlutusV3 only (no V2) | V1, V2, V3 |
| Node trace config | `AMARU_LOG=debug`, JSON traces on (`amaru_json_traces: true`) | default severities; `cardano_measurement_traces` **false** (profile flag absent) |
| cardano-cli | in the cardano-node images and in `dwarf/controller:current` | same |

Collector contract facts (checked against the live 20261029 log and source)

- Amaru emits, as `fields.message` under `target amaru::consensus`:
  `perf.header.lifecycle` (matched), `perf.fork.switch` (matched, 344 in the
  relay's lifetime, none inside a 13 s window), and the `mempool.transaction.*`
  / `mempool.state.update` family (schemas present; nothing emitted without
  transactions).
- `keepalive.peer.round_trip` (`amaru::protocols`) is emitted about every
  10 s per upstream peer; a 13 s window with warm-up sees none.
- `epoch_transition.apply` spans are emitted at every boundary (matched).
- `manager.peer.accepted` is a **span** under `amaru::protocols`; the collector
  only knows `manager.peer.connected`, so `peer_connected` is always 0.
- Amaru runs tx-submission in both roles: responder for `amaru-consumer` and
  `amaru-relay-2`, initiator toward `p1` (protocol 4 decode events seen in
  both roles). Transactions submitted to `amaru-consumer` therefore enter
  `amaru-relay-1`'s mempool over N2N, and Amaru offers them to `p1`.
- The Amaru run-level trace window (`outputs/amaru-measurement-run/raw/
  amaru-relay-1.ndjson`) spans from measurement start to finalize, so every
  load entry is inside it. The Cardano stock taps read the calibration leg's
  own capture (`outputs/cardano-measurement-calibration/raw/node1.ndjson`,
  from the leg's `started_at` to its end), so Cardano workload must run
  **inside** the calibration leg; restart readiness reads `events/
  target-hooks.ndjson` and is independent of that window.
- The Cardano calibration primitive already accepts `plutus_transactions`
  (1..100) and `epoch_observation_seconds` (≤120) and submits always-succeeds /
  always-fails PlutusV2 lock+spend pairs through `cardano-cli` beside the
  target. The Amaru helper has no workload option.
- `runtime_real_target_restart_and_readiness` works for both targets
  (`docker restart --time 30`, then listener, chain-progress and peer-role
  gates). Its peer for Amaru is `amaru-consumer`; for cardano it is node2.
- The deployment gate refuses operator registrations only for mixed-scope
  profiles; both profiles here are single-scope.
- Amaru patch set nanoseconds-v3 for `23031e0e` has no header-validation
  instrumentation. cardano-node nanoseconds-v5 for `fef83fed` declares
  header-validation, blockfetch-handler-queue and txsubmission-residence as
  `status: unbuilt-contract`; their collectors register only when
  `supports_cardano_*` identity checks pass.

## 4. Design

### 4.1 Two new portable scenarios

`dwarf/scenarios/amaru-measurement-workload-stock-portable.yaml` and
`dwarf/scenarios/cardano-measurement-workload-stock-portable.yaml`, copied
from the e2e portable scenarios (same `target.version: resolved-plan`, same
profile, same `evidence_intent: candidate`, tags + `workload`). As today, the
patched legs run the same scenario with `measurement_profile` overridden to
`*-security-patched` at request time.

Scenario-level `measurements:` adds the header-validation taps that the
profiles omit: `amaru-stock-header-validation` and
`cardano-stock-header-validation` (both are catalog definitions and have
factories; they parse `perf.header.lifecycle` / `AddedToCurrentChain`).

Load order, Amaru:

1. `runtime_amaru_measurement_calibration` — `attempts: 100`,
   `case_set: accepted-and-rejected-v1` (unchanged), plus the new parameters
   `plutus_transactions: 20`, `submission_entry: amaru-consumer`,
   `epoch_transitions: 1`, `timeout_seconds: 1500`.
2. `runtime_real_target_restart_and_readiness` — `profile_id`, `timeout_seconds: 300`.
3. `runtime_controlled_sync_range` — `minimum_blocks: 20`,
   `peer_policy: single-controlled-producer`, `timeout_seconds: 240`.

Load order, cardano-node:

1. `runtime_cardano_measurement_calibration` — `attempts: 100`,
   `case_set: accepted-and-rejected-v1`, `plutus_transactions: 20`,
   `epoch_observation_seconds: 60` (one full 50 s epoch is guaranteed),
   `timeout_seconds: 1500`.
2. `runtime_real_target_restart_and_readiness` — as above.
3. `runtime_controlled_sync_range` — `minimum_blocks: 20`,
   `peer_policy: three-node-controlled-local-mesh` (or the policy the
   primitive accepts for the cardano profile; builder confirms).

Assertions: the existing `load_events_are_ok`, plus `restart_readiness_complete`,
`controlled_sync_range_complete` (`minimum_blocks: 20`),
`target_progress_continues`, `no_target_fatal_signal` (all exist and are used
by the client-example restart scenarios).

Probes: `runtime_target_health_and_progress` and `runtime_peer_session_health`
with `report_path: outputs/client-example-proof/controlled-sync-range.json`
(as in the restart scenarios).

Time budget (worst case): handshake 15 s + 20 Plutus pairs ≈ 400 s +
epoch wait ≤ 260 s (Amaru) / 60 s (cardano) + restart ≤ 300 s + sync ≤ 240 s
≈ 1 200 s. Ceiling 2 400 s.

### 4.2 Amaru calibration helper: workload options

Extend `dwarf/scripts/runtime_amaru_measurement_calibration.py` `leg` and the
primitive `RuntimeAmaruMeasurementCalibration` (new params are optional;
defaults keep today's behaviour and today's `workload_identity` digest when
unset):

- `--plutus-transactions N` (0..100, default 0). After the handshake attempts,
  run the **existing** `run_plutus_workload` from the cardano helper. Producer
  and keys come from the existing `_prepare_amaru_transaction_producer` in
  `runtime_controlled_plutus_transactions.py` (genesis.1 key from the
  configurator container, `cardano-cli` wrapper with `--volumes-from`), moved
  or imported so both scripts share it. The `plutus_v2.verified` requirement
  stays in the conformance script and is **not** applied here; this workload
  measures taps, not cost-model conformance.
- `--submission-entry {p1,amaru-consumer}` (default `amaru-consumer`). With
  `amaru-consumer`, UTxO queries, builds and submits go through the
  consumer's socket (`amaru-consumer-state` volume) so each transaction enters
  `amaru-relay-1`'s mempool over N2N tx-submission. Inclusion is awaited on
  the same socket. If a transaction is not included within
  `inclusion_fallback_seconds` (default 30), the same signed transaction is
  submitted to p1 and the record gets `relay_forwarded: false`; otherwise
  `relay_forwarded: true`. The per-transaction wait stays bounded by the
  existing 60 s `_wait_for_utxo` timeout.
- Plutus language: `run_plutus_workload` gains a `script_language` choice.
  The Amaru leg picks it from the live protocol parameters' `costModels`:
  V2 if present, else V3. V3 uses `corpora/redeemer-container/live-controls/
  always-succeeds-v3.plutus` and a new retained
  `corpora/cardano-measurement/always-fails-v3.plutus` (same UPLC `error`
  program as the V2 file, declared `PlutusScriptV3`). The cardano profile keeps
  V2.
- `--epoch-transitions K` (default 0). After the workload, poll the relay's
  `tip.update` records and return once the `epoch` field has advanced by K
  (timeout `epoch_timeout_seconds`, default 260 = one 200 s epoch + margin).
  This replaces a blind sleep and records `epoch_before`/`epoch_after` in
  `result.json`.
- `workload_identity` includes the new parameters when set.
- The primitive passes `plutus_transactions`, `submission_entry`,
  `inclusion_fallback_seconds`, `epoch_transitions`, `epoch_timeout_seconds`
  through, logs them in `started`/`completed`, and folds the Plutus accounting
  records into the `workload_accounting` event (so `amaru-external-workload-
  accounting` gets `submit_to_*` stages, as the Plutus primitive already does).

### 4.3 cardano-node: trace flag and leg parameters

- Add `"cardano_measurement_traces": true` to
  `dwarf/profiles/profile-n-cardano-latest-confirmed/profile.yaml`. This is the
  cardano counterpart of `amaru_json_traces: true` that profile-o already
  carries; it raises the audited namespaces (BlockFetch.Client/Server,
  TxSubmission.TxInbound/TxOutbound, Mempool, KeepAlive.Remote, ChainDB,
  Forge.Loop, LedgerMetrics, Resources) to Info/DDetailed at deploy. Without it
  `blockfetch_duration`, `keepalive_rtt`, `protocol_events` and
  `ledger_samples` cannot fill. Requires one redeploy of profile-n.
- The cardano leg needs no helper change for Plutus (V2 is live) or epoch
  (sleep ≥ 50 s). Raise the primitive's `timeout_seconds` in the scenario.

### 4.4 Collector contract fix

`dwarf/profile_manager/measurement_collectors/amaru_stock.py`: add
`("amaru::protocols", "manager.peer.accepted")` as a `peer-connected` kind,
counted once per span (`fields.message == "new"`), keeping the old
`manager.peer.connected` message key. One unit test with a 20261029-shaped
record. Fork switch and keepalive need no contract change (verified).

### 4.5 Optional cardano patched taps

If `supports_cardano_header_validation / _blockfetch_queue /
_txsubmission_residence` accept the operator `fef83fed` nanoseconds-v5
identity, add `cardano-patched-header-validation`,
`cardano-patched-blockfetch-handler-queue` and
`cardano-patched-txsubmission-residence` to the cardano scenario's
`measurements:`. If they do not (manifest says `unbuilt-contract`), leave
them out and record that as a boundary in the run notes. Builder decides from
the identity check at implementation time.

### 4.6 Pages comparison set

Replace the four handshake-only archives in `docs/evidence/default-runs.json`
with the four workload archives (same 10-entry cap; size cap 10 MB each,
25 MB total — the longer window makes the Amaru archives larger, so the
builder checks sizes after export and trims `raw/` if needed via the export
path's existing limits, not by hand-editing archives).

### 4.7 Tests

- Helper: `build_case_plan`/`workload_identity` digest unchanged when new
  options are unset; identity includes them when set; epoch wait returns on
  `epoch` advance and times out cleanly; submission-entry fallback path.
- Primitive: new params plumbed and logged (extend
  `tests/test_amaru_measurement_e2e_scenario.py` pattern).
- Scenarios: validate with `scripts/validate_scenarios.py`; the portable
  scenario tests (`test_amaru_measurement_e2e_scenario.py`,
  `test_cardano_measurement_e2e_scenario.py`) get a workload sibling.
- Collector: `peer_connected` counts `manager.peer.accepted` spans.
- Pages: `tests/test_ci_stage_pages_campaigns.py` count stays 10.

## 5. Expected outcome per tap

Expected to fill (new): Amaru `fork_switch`, `mempool_*`,
`transfer_validation` + `ledger_rule_*`, `virtual_machine_*`,
`epoch_transition`, `keepalive_round_trip`, `peer_connected`,
`restart_readiness`, `submit_to_*` stages, patched `txsubmission-residence`,
stock `header_validation`; cardano `mempool_*`, `blockfetch_duration`,
`blockfetch_bytes`, `served_blocks`, `keepalive_rtt`, `protocol_events`,
`ledger_samples`, `rollbacks`, `transaction_outcomes`, `restart_readiness`,
`submit_to_*`, patched `block_application`, `epoch_transition`, `plutus_vm`,
`protocol_receive_decode`, stock `header_validation`.

Stay empty by documented boundary (not a defect of this design): cardano stock
`plutus_vm_duration`, `epoch_transition_duration`, `handler_queue_residence`,
`txsubmission_residence`; Amaru `header_slot_start_to_reception` (mixed clock
domains); both `*-coverage-production-paths` (no runtime collector); Amaru
patched header-validation (v3 patch has no such instrumentation).

## 6. Left to the builder

- Exact parameter names/schemas in `dwarf/primitives/load/*.schema.json`.
- Where to host the shared Amaru producer helper (import from
  `runtime_controlled_plutus_transactions` vs. move to the cardano helper).
- How `run_plutus_workload` is parameterised for the consumer socket
  (`node` dict with `socket_volume_key: amaru-consumer-state`).
- `peer_policy` value for the cardano `runtime_controlled_sync_range`.
- Whether `cardano_measurement_traces` should be a new profile instead of a
  flag on profile-n (default: flag on profile-n, redeploy once).
- Drive scripts on cardano-box (`~/prepare.sh`, `~/runplan.sh`, `~/queue.sh`)
  are reused as they are.

## 7. Execution and evidence

1. Implement on branch `feat/operator-build-workload-measurements` (on top of
   main; the three fixes from `fix/local-cardano-instrumented-correction` are
   a prerequisite and get merged first or cherry-picked).
2. Rebuild the DWARF image on cardano-box, redeploy profile-n (trace flag) and
   let profile-o keep running.
3. Run stock and patched for both implementations through `/api/run/...`,
   verify with `~/tapreport.py` that every "expected to fill" tap has samples,
   export bundles, replace the Pages entries, validate, commit to an evidence
   branch.
4. Record the per-tap result table in the run notes and in memory.
