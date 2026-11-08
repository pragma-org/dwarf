# Local wiring campaign — plan (2026-11-08)

**Goal.** Prove every registered primitive is wired and runs honestly through the local path after F53/F53b, by running the smallest catalog set that covers all 274 primitives, through the dashboard exactly as a user does.

**Status.** Prepared, not started. Starts on the user's word.

## Scope

- Registry: 274 primitives; the catalog uses every one of them.
- Greedy cover: 112 scenarios cover all 274 (85 cardano-node targets, 27 Amaru). The 14 scenarios whose evidence F53/F53b invalidated are run first; 10 of those are already in the cover, 4 are added, so the campaign is 122 runs.
- Substrates (no compile, ever: node images are prepared once):
  - 52 self-composing devnet scenarios (39 cardano-node, 13 Amaru): each brings up and tears down its own devnet with `runtime_compose_substrate`. Cardano ≈ 3–6 min each; Amaru ≈ 12–15 min each (bootstrap).
  - 43 profile-bound scenarios on 12 profiles: the profile's topology runtime is deployed by the wizard's Prepare the first time and stays up afterwards, so each profile is composed once. Memory per deployed profile runtime on the box: ≈0.3 GB cardano-only, ≈1.9 GB with Amaru; the box has 27 GB with 20 GB free, so all can stay deployed.
  - 12 library + 2 single-node scenarios: no substrate.
- Serialised: the dashboard's mutating lock allows one prepare/launch at a time, so the campaign runs one scenario at a time. Estimated wall time ≈ 7–8 h (self-composing ≈ 6 h of it). Fits an overnight run.

## Dry run (read-only, 2026-11-08)

`local_campaign.py dry-run all` resolved 112/122 plans against the live dashboard (deploy ca387dc2):

- 2 need the explicit **unknown-version acknowledgement** gate (profile-g preview2 proof, cardano preview build); the driver applies it and records it.
- **10 are blocked**: their measurement profile needs a **patched target build** that is not prepared on cardano-box — Amaru `aedfe797` + patch set `47787f15` (profile-z / zb / za), Amaru `b159172f` + `4c22d7b0` (profile-u), Amaru `d3a6dafc` + `4c22d7b0` (profile-x), cardano-node `fef83fed` + `1c52fa42` (profile-v). These 10 are the only scenarios reaching **27 primitives** (block-application sampling, canonical chain progress, cbor conformance/roundtrip, handshake/opcert/plutus case families, controlled transfers/plutus/chain-progress windows, the window marks, protocol decode cases, exact-target verification, chain-progress wait, peer-session usability). One of them is the F53 Amaru window scenario (`client-example-txsubmission-window-amaru-portable-20260918`, profile-z).
- **Decision for the user:** (a) prepare the four patched targets once through Versions → Prepare (compiles once per target; the box did Amaru patched builds before) and keep the 10 in the campaign, or (b) run the 112 now and leave the 27 primitives as a declared gap until the patched targets exist.

## Execution

- Driver: `scratchpad/local_campaign.py run <batch|all>` — per scenario: `POST /api/run/resolve` (apply gates) → `POST /api/run/prepare` (SSE; deploys the profile runtime if needed; confirms a previewed destructive topology action exactly once) → `POST /api/run/start` (SSE until `done`) → `GET /api/runs` for the verdict. Timeouts: prepare 40 min, run 45 min. Resume-safe: scenarios already in the ledger are skipped.
- Order: `0-f53-affected` → profile-bound batches (substrates persist; each profile deployed once) → library → single-node → self-composing cardano → self-composing Amaru.
- Ledger: `campaign-ledger.jsonl` (batch, scenario, profile, gates, prepare stages, launch id, exit code, run id, verdict, seconds); `campaign-status.json` summary; `campaign.log`.

## Monitoring

- A `Monitor` on `campaign.log` for `RESULT|PREPARE-FAILED|ERROR|DASHBOARD-UNREACHABLE|campaign finished`, re-armed every 30 minutes.
- `local_campaign.py status` for the per-batch tally; the dashboard's `/operate/runs` page and each run page for detail.
- Stop rules: the driver stops on an unreachable dashboard; a prepare failure or run failure is recorded and the campaign continues. Three consecutive prepare failures on one profile → I stop and report.

## Comparison

- Each run's verdict and assertion summary against (1) its `evidence_intent` in the catalog, (2) the previous ledger entry for the same scenario on the box where one exists (7 local runs so far), (3) for the 14 F53 scenarios: the recorded `handshake_response_kind` must read `accepted` with a version 13–15 reply, never `data` with a refusal.

## Batches

### `0-f53-affected` (14)

- `client-example-txsubmission-window-amaru-portable-20260918` — **blocked: needs patched target build**
- `phase3-runtime-generated-haskell3-node1-invalid-cbor`
- `phase3-runtime-generated-haskell3-node1-malformed-blockfetch`
- `phase3-runtime-generated-haskell3-node1-malformed-handshake`
- `phase3-runtime-generated-haskell3-node1-malformed-txsubmission`
- `phase3-runtime-generated-haskell3-node1-wrong-version-handshake`
- `phase3-runtime-generated-haskell3-rich-capability-demo`
- `runtime-substrate-byzantine-blockfetch-example-smoke`
- `runtime-substrate-cardano-node-mixed-minor-txsubmission-window-pressure-example-smoke`
- `runtime-substrate-compound-eclipse-txsubmission-example-smoke`
- `runtime-substrate-compound-snapshot-pause-txsubmission-example-smoke`
- `runtime-substrate-mempool-failure-containment-mixed-amaru`
- `runtime-substrate-snapshot-mid-run-corrupt-recovery-example-smoke`
- `runtime-substrate-txsubmission-unexpected-body-mixed-amaru`

### `1-library` (12)

- `amaru-cbor-auxiliary-data-fuzz-structured`
- `cardano-amaru-cbor-dataset-auxiliary-data-differential`
- `cardano-node-cov-applyblock-aflpp-smoke`
- `runtime-bundle-chain-verify-example-smoke`
- `runtime-bundle-export-sarif-example-smoke`
- `runtime-bundle-summary-compose-example-smoke`
- `runtime-bundle-timeline-example-smoke`
- `runtime-forensic-snapshot-example-smoke`
- `amaru-cbor-auxiliary-data-fuzz`
- `edge-cases-cbor-tx-body-amaru`
- `runtime-credential-ceremony-example-smoke`
- `runtime-substrate-honest-baseline-example-smoke`

### `2-single-node` (2)

- `runtime-bundle-diff-example-divergence`
- `cardano-lsq-extract-debug-epoch-state`

### `3-self-compose-cardano` (39)

- `runtime-substrate-compound-stake-snapshot-hf-boundary-example-smoke`
- `runtime-substrate-compound-eclipse-recovery-example-smoke`
- `runtime-substrate-cardano-node-mixed-minor-byzantine-flagship-example-smoke`
- `runtime-substrate-compound-local-query-recovery-example-smoke`
- `runtime-substrate-eclipse-topology-example-smoke`
- `runtime-substrate-checkpoint-resume-recovers-example-smoke`
- `runtime-substrate-compound-panic-bootstrap-example-smoke`
- `runtime-substrate-compound-parser-overlay-forging-example-smoke`
- `runtime-substrate-real-adapter-blocking-work-starvation-example-smoke`
- `consensus-threshold-delay-probe`
- `runtime-substrate-blockfetch-continuity-failure-example-smoke`
- `runtime-substrate-blockfetch-invalid-block-cbor-example-smoke`
- `runtime-substrate-blockfetch-invalid-range-example-smoke`
- `runtime-substrate-blockfetch-range-mismatch-example-smoke`
- `runtime-substrate-blockfetch-range-pressure-example-smoke`
- `runtime-substrate-bootstrap-topology-concentration-example-smoke`
- `runtime-substrate-chainsync-nonincrementing-height-example-smoke`
- `runtime-substrate-chainsync-nonmonotonic-slot-example-smoke`
- `runtime-substrate-chainsync-parent-discontinuity-example-smoke`
- `runtime-substrate-compound-keepalive-topology-capture-example-smoke`
- `runtime-substrate-compound-mempool-relay-epoch-boundary-example-smoke`
- `runtime-substrate-duplex-promotion-pressure-example-smoke`
- `runtime-substrate-era-transition-example-smoke`
- `runtime-substrate-local-submit-stress-example-smoke`
- `runtime-substrate-mempool-failure-containment-example-smoke`
- `runtime-substrate-mux-ingress-overrun-example-smoke`
- `runtime-substrate-plutus-phase2-donointervene-retry-clean-example-smoke`
- `runtime-substrate-praos-header-assertion-example-smoke`
- `runtime-substrate-serdes-txsubmission-unexpected-body-example-smoke`
- `runtime-substrate-compound-time-skew-epoch-boundary-example-smoke`
- `runtime-substrate-localtxmonitor-fault-example-smoke`
- `runtime-substrate-peersharing-fault-example-smoke`
- `runtime-substrate-plutus-phase2-exunits-cost-model-string-undercharge-mixed-amaru`
- `runtime-substrate-plutus-phase2-isvalid-mismatch-rejected-example-smoke`
- `runtime-substrate-real-adapter-handshake-pressure-example-smoke`
- `runtime-substrate-real-adapter-runtime-starvation-example-smoke`
- `runtime-substrate-resource-disk-full-during-sync-example-smoke`
- `runtime-substrate-resource-slow-loris-chainsync-example-smoke`
- `runtime-substrate-resource-sync-bandwidth-throttle-example-smoke`

### `4-self-compose-amaru` (13)

- `conway-gov-scriptcontext-proposals-votes-fidelity-amaru-cardano-node`
- `cbor-strictness-witness-arity-vkey-noncurve-differential-amaru-cardano-node`
- `conway-phase1-validation-rules-differential-amaru-cardano-node`
- `conway-scriptcontext-txinfo-fidelity-differential-amaru-cardano-node`
- `governance-zero-denominator-admission-differential-amaru-cardano-node`
- `ledger-block-apply-isvalid-false-donation-value-creation-amaru-cardano-node`
- `ledger-epoch-boundary-donation-treasury-realization-amaru-cardano-node`
- `ledger-submit-empty-redeemers-container-differential-amaru-cardano-node`
- `ledger-submit-inline-datum-tag24-trailing-differential-amaru-cardano-node`
- `ledger-submit-multiasset-compact-boundary-differential-amaru-cardano-node`
- `ledger-submit-transaction-array-arity-differential-amaru-cardano-node`
- `ledger-submit-uplc-deep-delay-stack-safety-differential-amaru-cardano-node`
- `ledger-submit-value-coin-i64-overflow-regression-latest-amaru`

### `5-profile-profile-a-haskell-peersharing-disabled` (23)

- `phase3-runtime-profile-a-observability-port-delay-timeout-rollup-capability-demo`
- `m3-runtime-blockfetch-port-drop-isolated-peer`
- `phase3-runtime-profile-a-bundle-dedupe-primitive`
- `phase3-runtime-profile-a-bundle-export-primitive`
- `phase3-runtime-profile-a-partition-rejoin-capability-demo`
- `runtime-bundle-attestation-example-smoke`
- `m3-runtime-blockfetch-historical-range-burst`
- `m3-runtime-blockfetch-multi-peer-historical-range`
- `m3-runtime-blockfetch-port-delay-bounded-success`
- `m3-runtime-blockfetch-port-drop-timeout`
- `m3-runtime-blockfetch-stale-single-point-rejection`
- `m3-runtime-chainsync-historical-point-burst`
- `m3-runtime-node2-copied-state-recovery`
- `m3-runtime-profile-restart-tip-recovery`
- `phase3-runtime-profile-a-bundle-sign-primitive`
- `phase3-runtime-profile-a-bundle-triage-primitive`
- `phase3-runtime-profile-a-copied-state-chainsync-divergence-primitive`
- `phase3-runtime-profile-a-copied-state-divergence-primitive`
- `phase3-runtime-profile-a-copied-state-postremediation-blockfetch-primitive`
- `phase3-runtime-profile-a-pcap-capture-primitive`
- `phase3-runtime-profile-a-resource-profile-primitive`
- `phase3-runtime-profile-a-restart-postrecovery-blockfetch-primitive`
- `phase3-runtime-profile-a-syscall-trace-primitive`

### `5-profile-profile-zb-mixed-1112-amaru-20260918-nanoseconds-v3` (4)

- `client-example-invalid-mini-protocol-amaru-20260918-mixed-1112` — **blocked: needs patched target build**
- `opcert-soak-counter-edge-mixed-1112-amaru-20260918` — **blocked: needs patched target build**
- `opcert-header-validation-cases-mixed-1112-amaru-20260918` — **blocked: needs patched target build**
- `client-example-simple-transfer-amaru-20260918-mixed-1112` — **blocked: needs patched target build**

### `5-profile-profile-i-generated-haskell3` (4)

- `phase3-runtime-generated-haskell3-capability-demo`
- `phase3-runtime-generated-haskell3-connection-state-freeze-rollup-capability-demo`
- `phase3-runtime-generated-haskell3-freeze-recover-port-drop-capability-demo`
- `phase3-runtime-generated-haskell3-gc-capture-primitive`

### `5-profile-profile-u-amaru-measurement-nanoseconds-v2` (2)

- `client-example-block-application-amaru-canonical-v2` — **blocked: needs patched target build**
- `client-example-block-application-amaru` — **blocked: needs patched target build**

### `5-profile-profile-g-haskell-preview2-proof` (2)

- `phase3-runtime-haskell-preview2-baseline-delay-loss-recheck-capability-demo` — gate: unknown-version acknowledgement
- `phase3-runtime-haskell-preview2-baseline-drop-reset-recheck-capability-demo` — gate: unknown-version acknowledgement

### `5-profile-profile-o-amaru-target-latest-confirmed` (1)

- `amaru-measurement-workload-stock-portable`

### `5-profile-profile-za-amaru-20260918-plutus-v2` (1)

- `client-example-plutus-vm-amaru-onchain-v2-20260918` — **blocked: needs patched target build**

### `5-profile-none` (1)

- `consensus-chainhold-upstream-differential`

### `5-profile-profile-x-amaru-cbor-fix-regression-nanoseconds-v2` (1)

- `client-example-cbor-decoding-amaru-d3a6dafc-regression` — **blocked: needs patched target build**

### `5-profile-profile-v-cardano-measurement-nanoseconds-v2` (1)

- `cardano-n2n-handshake-version-table-forward-compat-1112` — **blocked: needs patched target build**

### `5-profile-profile-r-amaru-measurement-stock-control` (1)

- `amaru-measurement-boundary-control-stock`

### `5-profile-profile-c-mixed-haskell-amaru-minimal` (1)

- `phase1-runtime-live-amaru-parity-baseline`
