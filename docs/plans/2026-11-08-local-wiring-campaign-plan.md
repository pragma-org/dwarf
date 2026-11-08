# Local wiring campaign — plan (2026-11-08)

**Goal.** Prove every registered primitive is wired and runs honestly through the local path after F53/F53b, by running the smallest catalog set that covers all 274 primitives, through the dashboard exactly as a user does.

**Status.** Started 2026-11-08 06:39Z (my misreading of "go ahead and do that"; the user had reserved the start for an explicit "run the local path runs"), **stopped at 06:57Z after 12/119 runs, all failed within seconds** — four local-path defects F54–F57, recorded in `docs/plans/2026-11-05-e-pass-validation-log.md`. F54/F55/F56 fixed (tests first); F57 is the frozen-substrate port, planned separately. The failed ledger is kept as `campaign-ledger.failed-20261108T0657Z.jsonl`; the campaign restarts clean on the user's word.

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
- **Decision for the user:** (a) prepare the four patched targets once and keep the 10 in the campaign, or (b) run the 112 now and leave the 27 primitives as a declared gap until the patched targets exist. **The user chose (a).**

## Patched targets built (2026-11-08)

- **Correction.** Versions → Prepare does not build these targets: it builds operator registrations (stock + instrumented variants from the catalog revision). A `measurement_target_mode: patched` profile is resolved by `measurement_targets.resolve_patched_*_target`, which needs a record under `state/measurement-targets/<impl>/<revision>/<patch_set>.json`. Those records are produced only by DWARF's trusted host-side builders `dwarf/scripts/build_amaru_measurement_target.py` and `build_cardano_measurement_target.py` (exact detached checkout, patch and preimage verification, pinned toolchain, static-binary check, image smoke, bootstrap-wrapper probe, record publish). The registry dir is mounted read-only into the controller, so one record serves both dashboard and controller.
- The box had built three of the four in September (`~/work/build-amaru-0918-ns-v3`, `~/dwarf-measurement-builds/amaru-b159172-nanoseconds-v2-20260920`, `~/dwarf-builds/cardano-measurement-fef83fed-nanoseconds-v2-20260920-r3`) but the images had been pruned and the registry of this installation was empty, so all four were rebuilt from the public tree `a447c018` with `~/mt_build_20261108.sh` (toolchains already present: rustup nightly-2026-09-04 + musl target, cargo-zigbuild 0.23.4, Zig 0.15.2 via `~/.venvs/dwarf-moog-fix`, GHC 9.6.7 + cabal 3.16.0.0 via ghcup, warm `~/.cabal/store`).

| Target | Patch set | Build dir | Wall | Executable vs September |
|---|---|---|---|---|
| Amaru `aedfe797` (10.11.20260918) ns-v3 | `47787f15…` | `~/dwarf-measurement-builds/amaru-aedfe797-ns-v3-20261108` | 7 min | identical (`45c96358…`) |
| Amaru `b159172f` (10.11.20260912) ns-v2 | `4c22d7b0…` | `…/amaru-b159172f-ns-v2-20261108` | 6 min | differs (`05233bac…` → `5d0d95b9…`) |
| Amaru `d3a6dafc` (cbor-fix regression build, profile-x) ns-v2 | `4c22d7b0…` | `…/amaru-d3a6dafc-ns-v2-20261108` | 6 min | no local September build to compare |
| cardano-node `fef83fed` (11.1.2) ns-v2 | `1c52fa42…` | `…/cardano-fef83fed-ns-v2-20261108` | 15 min | identical (`3fb83f12…`) |

All four records are in `~/.local/share/dwarf/state/measurement-targets/`; images `dwarf/amaru-measurement:*` and `dwarf/cardano-measurement:11.1.2-fef83fed-1c52fa42` are local. Total 19 minutes, not the 1–2 hours estimated.

- **Second dry run:** 114/122 resolve. The remaining 8 fail a different gate: they carry a **frozen** `runtime_verify_exact_target` pin (`identity_binding` frozen) to the September image id (and, for `b159172f`/`d3a6dafc`, executable digest), and `run_plan._scenario_target_bindings` refuses a plan whose qualified target differs ("Select a matching historical target or a resolved-plan scenario"). The September images no longer exist anywhere, so no historical target can match.
- **Swap.** Six of the eight have `-portable-20260918` siblings with `identity_binding: resolved-plan` that use the same primitives: block-application, cbor-decoding, invalid-mini-protocol, simple-transfer (profile-z) and plutus-vm (profile-za). All five resolve. They replace the 8 frozen runs; profiles zb, u and x drop out, profile-z joins. The campaign is now **119 runs on 13 profile substrates**.
- **Declared gap (8 primitives):** `runtime_opcert_header_cases`, `runtime_opcert_header_soak`, `opcert_case_verdicts_match_expected`, `opcert_verdicts_agree`, `opcert_soak_invariant_holds`, `opcert_soak_reasons_agree`, `opcert_soak_verdicts_agree` (every opcert scenario in the catalog is frozen to the September digests, none has a portable sibling) and `minimum_adopted_block_range_observed` (only in the frozen `client-example-block-application-{amaru,cardano}`). Closing it needs a catalog decision: re-freeze those scenarios to the 2026-11-08 digests (for `aedfe797` and `fef83fed` the executable is byte-identical, only the image id moved) or add portable siblings. Not done without the user.

## F57 — porting the frozen self-composing substrates (plan, awaiting the user's review; no catalog file touched yet)

**Facts.** 126 scenarios carry a `substrate` whose nodes have the default `identity_binding: frozen`: 93 cardano-only, 33 mixed. Node versions: cardano-node 10.7.1 (94 scenarios), 11.1.2 (32), 10.6.1 (5, always next to 10.7.1); Amaru `v10.11.20260925` (31, written with a `v` the catalog does not use), `10.11.20260925` (1), `0.1.2` (1, pre-catalog). 108 are `compose_mode: host`, 18 `docker`. Frozen nodes resolve through `runtime_substrate_common.resolve_binary_for_node` (host binaries under `/home/dwarf/…`) or `dwarf/cardano-node:<version>` local tags; neither exists on a dashboard install. The 15 `resolved-plan` substrates instead go through `run_plan._scenario_substrate_version_profile` → the version resolver → `_scenario_substrate_bindings`, which pins each node's catalog image by digest, and the bound substrate reaches `runtime_install_version`/`runtime_compose_substrate` in docker mode (proven by the mixed-amaru run on 2026-11-08, which resolved both images and failed only on F56).

**What the port changes, and what it must not.**
1. `_scenario_substrate_version_profile` today takes the version from the request or falls back to `latest-confirmed`; it ignores the nodes' declared versions. A ported 10.7.1 scenario would silently run on 11.1.2. The port therefore first extends that function the way `_library_version_profile` already works: when the request pins nothing and every node of an implementation declares the same version, the profile asks for that exact version (`version_policy: exact`, `version_policy_source: scenario`). A request pin still overrides. Test first.
2. Each ported scenario: `identity_binding: resolved-plan` on every node, `compose_mode: docker`, Amaru versions normalised to the catalog spelling (`10.11.20260925`), every other field untouched (ids, roles, topology, faults, assertions, seeds, evidence intent). A `dwarf/scripts/port_frozen_substrates.py` does this deterministically, lists what it changed, and refuses anything outside the rule set; it lives beside `port_measurement_patches.py`.
3. Excluded, kept frozen with a declared `surface_capability` gap: the 5 `mixed-minor` scenarios (two cardano versions in one devnet; the resolver binds one release per implementation) and the one node pinned to Amaru `0.1.2` (not in the catalog). Also kept frozen but flagged for the user: the 8 large meshes (20–100 nodes) — resolvable, but a 100-node devnet on the box is a separate decision.
4. Validation before any run: `validate_scenarios.py` gate, `dwarf-validate` contract, read-only `/api/run/resolve` of every ported scenario against the live dashboard (must resolve to the declared version with digest-pinned images), one Antithesis preview of a ported scenario to show the rig renderer still accepts it, full suite at the known baseline.
5. Images: 10.7.1, 10.6.1, 11.1.2 and Amaru 10.11.20260925 are all `available` in the catalog; the prepare step pulls what is missing (10.7.1 and 11.1.2 are already local).

Public catalog impact: 110 scenario files change two or three fields each; the evidence they produce afterwards is about the same declared versions, resolved from the catalog instead of a host binary.

**Done so far (code only, no catalog file changed):** step 1 is in `run_plan._scenario_substrate_version_profile` (declared uniform versions → `version_policy: exact`, `version_policy_source: scenario`; a request pin wins; two versions for one implementation → `RunPlanError`); step 2 is `dwarf/scripts/port_frozen_substrates.py` (dry run by default, `--apply` to write, `--max-nodes`, `--report`); tests in `tests/test_frozen_substrate_port.py`. **Dry run over the catalog (2026-11-08):** 110 portable (31 of them respell `v10.11.20260925`), 16 skipped with a reason: 5 two-version mixed-minor scenarios, 1 Amaru `0.1.2`, 3 explicit multi-host substrates (10-node mesh, multihost baseline, snapshot-restore-on-divergent-host), 7 meshes of 20–100 nodes. Report: `scratchpad/port-dry-run.json`. **Applied on the user's "apply" (2026-11-08):** 110 files rewritten by the script, nothing else in them changed. The schema gate the `dwarf-validate` workflow runs (`validate_scenarios.py` plus `refresh_version_catalog.py --verify-upstream`) passes on the ported tree: the port introduced no new finding, and the one pre-existing failure (`ledger-submit-value-coin-i64-overflow-regression-latest-amaru`: supporting node role `producer` is not in the schema's role set, nothing at runtime branches on it) was corrected to `honest` so the public workflow is green on push.

## Execution

- Driver: `scratchpad/local_campaign.py run <batch|all>` — per scenario: `POST /api/run/resolve` (apply gates) → `POST /api/run/prepare` (SSE; deploys the profile runtime if needed; confirms a previewed destructive topology action exactly once) → `POST /api/run/start` (SSE until `done`) → `GET /api/runs` for the verdict. Timeouts: prepare 40 min, run 45 min. Resume-safe: scenarios already in the ledger are skipped.
- Order: `0-f53-affected` → profile-bound batches (substrates persist; each profile deployed once) → library → single-node → self-composing cardano → self-composing Amaru.
- Ledger: `campaign-ledger.jsonl` (batch, scenario, profile, gates, prepare stages, launch id, exit code, run id, verdict, seconds); `campaign-status.json` summary; `campaign.log`.

## Declared gaps (not run)

- 8 opcert/block-range primitives: every covering scenario is frozen to September images (see Frozen swap).
- `runtime-substrate-cardano-node-mixed-minor-txsubmission-window-pressure-example-smoke`, `runtime-substrate-cardano-node-mixed-minor-byzantine-flagship-example-smoke`: two cardano releases in one substrate; the resolver binds one release per implementation (F57/F72). Campaign is 117 runs.

- **Restored (user decision 2026-11-08):** the 5 bundle-tool example smokes read 13 recorded April 2026 demo runs, now shipped under `dwarf/corpora/bundle-runs/` (1.0 MB, no secrets; only public signing-key fingerprints), with `runs_dir: dwarf/corpora/bundle-runs`. The bundle tools only read the runs directory. Checked locally: chain verify, summary compose, forensic snapshot and diff pass end to end; the SARIF export writes a schema-valid file.
- 1 CBOR dataset differential (`cardano-amaru-cbor-dataset-auxiliary-data-differential`): dataset at `/home/nigel/cardano-cbor-dataset` on the box host is not mounted into the controller (known since 2026-11-01); mounting a host path into the controller is a deploy decision.
- 5 host-model port faults (`fault_local_port_drop`/`_delay`) on container substrates → F76 refusal; needs a netns sidecar.

- 3 byzantine-proxy scenarios (`runtime_byzantine_peer`, host-model tmux relaunch) → F80; needs a proxy sidecar.
- 1 `cardano-lsq-extract-debug-epoch-state`: needs `cargo` in the controller image (toolchain decision).

- Public-network (`network: preview`) substrates: 46 self-composed scenarios start cold nodes on the real preview network. Cardano keeps tx-submission off during bulk sync (txsubmission probes time out, 5/5 so far), Amaru cannot converge to the advertised peer tip inside the gate (5.4 M slots behind in 180 s). Not wiring; a catalog decision (custom `testnet_42` devnets vs an "advancing" convergence target for public networks).

## Batch 3 (self-composed cardano, 38) — restart #4 on a991dc11, classified 15:52Z

11 pass / 27 fail. Failure classes: **9** byzantine proxy bound to the bridge gateway (F87, fixed, rerun pending); **3** epoch-boundary primitives honestly `applicable: false` (known wall); **1** checkpoint health probe on the controller loopback (F86, fixed, rerun pending); **1** Amaru 10.11.20260925 image missing (fetched via Versions download, rerun pending); **13** single-assertion scenario verdicts on preview substrates with full-mesh peering confirmed — to be read individually in the post-campaign review (`k_bound_rollback_recovered`, `honest_peer_set_uncompromised`, `bootstrap_assumptions_safe`/`panic_path_contained`, `parser_bounds_enforced`, `topology_bootstrap_diversity_preserved`, `duplex_promotion_slot_limit_enforced`, `local_submit_availability_preserved`, `mux_ingress_overrun_scoped`, `plutus_phase2_donotintervene_retry_clean`, `praos_header_assertion_rejected`, `plutus_phase2_isvalid_mismatch_rejected`, `substrate_quorum_observed` ×2). Some of these may be real findings, some preview-sync design effects; none is a wiring defect.

## Batch 4 (self-composed Amaru, 13) — restart #4 on a991dc11, classified 16:36Z

0 pass / 13 fail. **6** Amaru preview convergence gate (cold node on the real preview network, ~5.5 M slots behind in 180 s — catalog/gate decision); **6** install variables empty on this install: `DWARF_LOCALTXSUBMISSION_HELPER_IMAGE` (5, two message variants) and `DWARF_RUN_ARTIFACT_BASE_URL` (1) — F88/F89 default them, rerun pending; **1** substantive verdict for review: `ledger-submit-value-coin-i64-overflow-regression-latest-amaru` — the regression check reports the transaction absent after 5 post-submit reference blocks (relates to the confirmed coin-overflow fee finding; read, do not chase). Custom-testnet Amaru compose passed on every custom-testnet run (install → compose → gate), so the mixed self-composed shape is wired.

## Batch 5a (profile-a, 23) — restart #4 on a991dc11, classified 17:02Z

6 pass / 15 fail / 2 launch errors. **10** legacy profile-a helper config (F91, fixed, rerun pending); **3** host-model port faults (F76, declared); **2** declared tool/capability gaps: `shim_responder_stale_blockfetch` needs the Rust `dwarf-amaru-runtime-blockfetch-stale-server` (not in the controller image), `runtime_pcap_capture` needs `sudo`/raw-socket capture (controller has neither); **2 launch errors**: the first run after the fresh profile deploy hit F90 (fixed, rerun pending) and `phase3-runtime-profile-a-syscall-trace-primitive` was refused by the dashboard preflight (`node_chain_not_converged` after fault-heavy predecessors — correct gate behaviour, rerun). Profile-a was removed by the driver at batch end (verified: 0 containers, 0 networks; the driver's "remove failed" line is the pre-fix false negative).

## First full pass complete — 2026-11-08 18:15Z (restart #4 on a991dc11)

117 ledger rows: **36 pass / 70 fail / 3 launch errors / 8 prepare-failed**; 106 signed run bundles exported to `/Volumes/DevStorage/dwarf-work/bundles/campaign/` (160 files, 576 MB incl. earlier restarts). Every non-pass is classified (batch sections above): fixed-but-undeployed wiring (F85–F91: generated-node pid resolver, container health probe, proxy bind address, two install defaults, compose-time attach gating, legacy profile-a config), declared gaps (opcert frozen pins, mixed-minor two releases, fixture smokes, dataset mount, cargo, port-fault netns sidecar, byzantine tmux relaunch, Rust tool images, pcap capture), the public-network class (cardano txsubmission bulk sync, Amaru convergence gate, profile readiness `node_chain_not_synchronized`), scenario verdicts for review, and driver-side transients (prepare refused as `mutation_in_progress` right after a batch-end removal; first run after a fresh profile deploy hitting F90). Rerun of every non-pass row follows the deploy of 3dbc9b99 with the driver carrying measurement pins and retrying `mutation_in_progress`.

- **Removed (user decision 2026-11-08):** the 4 Plutus phase-2 submit-probe scenarios (`donointervene-retry-clean`, `isvalid-mismatch-rejected`, `exunits-overrun-rejected`, `exunits-cost-model-string-undercharge-mixed-amaru`) with `runtime_plutus_phase2_submit_probe` and its 3 assertions. The probe never submitted anything: it copied the fixture's `expected` block into the result and the assertions graded that copy. The fixtures were never in this repository (older trees have them); restoring them would have produced passes with no evidence. TM-016/TM-036 stay covered by other scenarios. **Open, same pattern:** `runtime_plutus_phase2_differential_observation` (9 scenarios) reads `equivalent` and both nodes' decisions from a fixture file the same way — decision pending.

## Evidence retention and cleanup (user, 2026-11-08: "make sure the evidence/output is saved including the taps … and remove the docker containers or volumes when the scenario is validated end to end")

- Every run keeps its full directory under the runs root (log, assertions, probes, `measurements/` with every tap's collector result, `report.json`, `compact-table.json`, outputs) and its run-ledger entry. The campaign additionally pulls the **signed portable bundle** of every finished run (`GET /runs/<run_id>/bundle`, produced by `runtime_bundle_export` with signature verification) and keeps a copy under `/Volumes/DevStorage/dwarf-work/bundles/campaign/`; the ledger row records the bundle path and size. Done by hand for today's five finished runs (shim pass, e2e-stock pass, profile-o workload pass 5/5, the two failing proofs) → `/Volumes/DevStorage/dwarf-work/bundles/runs/`.
- Self-composing scenarios tear their devnet down themselves (`runtime_teardown_substrate`, `compose down --volumes --remove-orphans`; 0 leftovers on the box today). The campaign checks after each such run that no `dwarf-substrate-<run>` container, volume or network remains and removes any that does.
- Profile runtimes are shared by their batch and **removed by the campaign when the batch is done** (dashboard topology action `remove`, confirmed by the campaign), so no profile substrate outlives the runs that needed it. At the end: `docker image prune -f` and `docker volume prune -f` on the box (dangling only).
- Cleaned today on the box: 147 GB of Docker build cache (left by the day's image builds), 40 stale Antithesis package images (8.6 GB), dangling objects → 278 GB free → 422 GB free. **Not touched, user decision:** `controller-state/release-builds/` holds 35 GB of retained operator source builds from 11-05 (two Amaru builds at 12 GB each); their registrations and receipts reference them.

## Monitoring

- A `Monitor` on `campaign.log` for `RESULT|PREPARE-FAILED|ERROR|DASHBOARD-UNREACHABLE|campaign finished`, re-armed every 30 minutes.
- `local_campaign.py status` for the per-batch tally; the dashboard's `/operate/runs` page and each run page for detail.
- Stop rules: the driver stops on an unreachable dashboard; a prepare failure or run failure is recorded and the campaign continues. Three consecutive prepare failures on one profile → I stop and report.

## Comparison

- Each run's verdict and assertion summary against (1) its `evidence_intent` in the catalog, (2) the previous ledger entry for the same scenario on the box where one exists (7 local runs so far), (3) for the 14 F53 scenarios: the recorded `handshake_response_kind` must read `accepted` with a version 13–15 reply, never `data` with a refusal.

## Batches

### `0-f53-affected` (14)

- `client-example-txsubmission-window-amaru-portable-20260918` — resolves since the `aedfe797` patched target build
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

### `5-profile-profile-zb-mixed-1112-amaru-20260918-nanoseconds-v3` (0 — dropped)

- `client-example-invalid-mini-protocol-amaru-20260918-mixed-1112` — frozen to September image id; replaced by the portable sibling in profile-z
- `opcert-soak-counter-edge-mixed-1112-amaru-20260918` — frozen; no portable sibling → declared gap
- `opcert-header-validation-cases-mixed-1112-amaru-20260918` — frozen; no portable sibling → declared gap
- `client-example-simple-transfer-amaru-20260918-mixed-1112` — frozen; replaced by the portable sibling in profile-z

### `5-profile-profile-z-amaru-20260918-nanoseconds-v3` (4 — added)

- `client-example-block-application-amaru-portable-20260918`
- `client-example-cbor-decoding-amaru-portable-20260918`
- `client-example-invalid-mini-protocol-amaru-portable-20260918`
- `client-example-simple-transfer-amaru-portable-20260918`

### `5-profile-profile-i-generated-haskell3` (4)

- `phase3-runtime-generated-haskell3-capability-demo`
- `phase3-runtime-generated-haskell3-connection-state-freeze-rollup-capability-demo`
- `phase3-runtime-generated-haskell3-freeze-recover-port-drop-capability-demo`
- `phase3-runtime-generated-haskell3-gc-capture-primitive`

### `5-profile-profile-u-amaru-measurement-nanoseconds-v2` (0 — dropped)

- `client-example-block-application-amaru-canonical-v2` — frozen to September image + executable; replaced by the portable sibling in profile-z
- `client-example-block-application-amaru` — frozen; replaced by the portable sibling (its `minimum_adopted_block_range_observed` → declared gap)

### `5-profile-profile-g-haskell-preview2-proof` (2)

- `phase3-runtime-haskell-preview2-baseline-delay-loss-recheck-capability-demo` — gate: unknown-version acknowledgement
- `phase3-runtime-haskell-preview2-baseline-drop-reset-recheck-capability-demo` — gate: unknown-version acknowledgement

### `5-profile-profile-o-amaru-target-latest-confirmed` (1)

- `amaru-measurement-workload-stock-portable`

### `5-profile-profile-za-amaru-20260918-plutus-v2` (1)

- `client-example-plutus-vm-amaru-onchain-v2-20260918` — frozen to September image id; replaced by
- `client-example-plutus-vm-amaru-portable-20260918`

### `5-profile-none` (1)

- `consensus-chainhold-upstream-differential`

### `5-profile-profile-x-amaru-cbor-fix-regression-nanoseconds-v2` (0 — dropped)

- `client-example-cbor-decoding-amaru-d3a6dafc-regression` — frozen to September image + executable; replaced by the portable cbor sibling in profile-z

### `5-profile-profile-v-cardano-measurement-nanoseconds-v2` (1)

- `cardano-n2n-handshake-version-table-forward-compat-1112` — resolves since the `fef83fed` patched target build

### `5-profile-profile-r-amaru-measurement-stock-control` (1)

- `amaru-measurement-boundary-control-stock`

### `5-profile-profile-c-mixed-haskell-amaru-minimal` (1)

- `phase1-runtime-live-amaru-parity-baseline`
