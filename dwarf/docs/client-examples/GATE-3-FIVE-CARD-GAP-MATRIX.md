# Gate 3 — five-card evidence and gap matrix

Status: reviewed evidence baseline before Gate 3 implementation.

Date: 2026-09-19.

Authoritative DWARF commit: `25f34b13767fa0cee63792a89cf0f6bc6071daf7`.

## Classification rules

- `proven`: retained runtime evidence proves the exact stated boundary.
- `reusable`: the component exists and is relevant, but it does not prove the frozen final requirement.
- `missing`: the exact required component or retained evidence does not exist.
- `accepted unavailable`: the frozen contract permits the value to be unavailable with a stated reason.

An existing definition is not runtime proof. A collector that finalizes with zero samples is not proof of its measurement. A similar primitive is not the contract-named primitive unless it produces the same required evidence.

Child explanation: `proven` means that DWARF did it and saved the proof. `reusable` means that DWARF has a useful part. `missing` means that DWARF must still build or run it.

## Controlling evidence

| Evidence | Status | Exact source |
|---|---|---|
| Original five-example goal | proven | Preserved goal SHA-256 `fdb26bf4085287161244cc3a4399c96c1c978a7f4fbba7801c238b9dd8b40d90`. |
| Client conformance and non-functional request | proven | Gist revision `0fddd26d23dc70acafffd4dd9777d39ed054c700`; GitHub Gist API response SHA-256 `d38556b71b40de79b7128ed62a5282c9af4291d8eb0584a9f09f771da5f94738`; last updated `2026-09-17T12:18:51Z`. |
| Five frozen cards | proven | `dwarf/docs/client-examples/contracts/01-cbor-decoding.yaml` through `05-restart-recovery-sync.yaml`; schema and contract checks are in `tests/test_client_example_acceptance_cards.py:43-120`. |
| Amaru exact patched target | proven | Profile pin: `dwarf/profiles/profile-q-amaru-measurement-patched/profile.yaml:2-15`; patch manifest: `dwarf/targets/amaru/measurement-patches/b159172f25a9c389f82f20bca4f15e3032791638/manifest.json:4-16,39-99`; retained run `20260919T135417Z-cca4cc8d`, `measurements/report.json.target_identity`. |
| Cardano-node exact patched target | proven | Profile pin: `dwarf/profiles/profile-t-cardano-measurement-patched/profile.yaml:2-15`; patch manifest: `dwarf/targets/cardano-node/measurement-patches/fef83fed01d7926f3de83b3b917be5a4a48768b5/manifest.json:4-35,37-87`; retained run `20260919T032200Z-59f94558`, `measurements/report.json.target_identity`. |
| Report, raw evidence, SARIF state, bundles, and dashboard routes | proven | Gate 2 runs `20260919T135417Z-cca4cc8d` and `20260919T142302Z-f856f303`; `dwarf/docs/client-examples/GATE-2-AMARU-PATCHED-PROOF.md`. |
| Contract-named scenario legs | missing | No `client-example-*` scenario file exists in `dwarf/scenarios/`. All ten legs remain marked `additive-required` in the cards. |
| Contract-named primitives and assertions | missing | None of the 26 names used by the five cards exists in `dwarf/primitives/registry.json`. Existing similar primitives are listed below as reusable where applicable. |

## Card 01 — CBOR decoding conformance and decode cost

| Required item | Status | Exact existing evidence | Missing piece |
|---|---|---|---|
| Qualified dataset identity and selection | reusable | The existing scenario pins repository revision `a7561cd063550c2218898571520f14c3674efe91`, Conway `plutus_data`, 25 samples in each of four categories, and seed `0xA7561CD0`: `dwarf/scenarios/cardano-amaru-cbor-dataset-plutus-data-differential.yaml:12-31`. The runner rejects any other repository, revision, era, or rule: `dwarf/scripts/runtime_cardano_cbor_dataset_differential.py:21-54`. | Retain the exact 100 selected input hashes in each final run and bind them to the new exact-release adapters. |
| Current library differential primitive | reusable | `runtime_cardano_cbor_dataset_differential` is registered at `dwarf/primitives/registry.json:1447-1455` and executes both typed decoder processes. `cardano_cbor_dataset_differential_clean` is registered at `dwarf/primitives/registry.json:291-299`. | The frozen names `runtime_version_pinned_cbor_conformance`, `cbor_conformance_clean`, and `cbor_roundtrip_consistent` are missing. |
| Amaru production adapter at exact frozen revision | missing | The current adapter manifest identifies old revision `bbce06e56ba3bfc915840922fd37c868cc6000be`: `dwarf/targets/manifests/amaru-cbor-decode-plutus-data.yaml:2-17`. | Build a fail-closed adapter from Amaru revision `b159172f25a9c389f82f20bca4f15e3032791638`; retain executable digest and build provenance. |
| Cardano production adapter at exact frozen revision | missing | The current adapter uses CHaP package identities instead of Cardano-node revision `fef83fed…`: `dwarf/targets/manifests/cardano-node-cbor-decode-plutus-data.yaml:2-17`. | Build a fail-closed adapter from the exact Cardano-node dependency set pinned by the 11.1.2 target; retain executable digest and build provenance. |
| Decode outcome parity for 100 inputs | reusable | The existing differential runner classifies `ok`, `clean_error`, and `crash` and retains duration per process: `dwarf/scripts/runtime_cardano_cbor_dataset_differential.py:170-209`. | Re-run through exact-release adapters. Existing evidence cannot be promoted because its target revisions do not match the card. |
| Decode-encode-decode-encode byte equality | missing | Current target protocol returns only `OK` or `ERR`; decode-only output is defined in `dwarf/targets/manifests/amaru-cbor-decode-plutus-data.yaml:9-17` and `dwarf/targets/manifests/cardano-node-cbor-decode-plutus-data.yaml:9-17`. | Exact adapters must return canonical encoded bytes and prove stable second-encode equality for every successful decode. |
| Production codec decode distribution, `n >= 100` | missing | The existing runner retains process wall-clock `duration_ms`, not the frozen production codec entry-to-result boundary in microseconds. | Add exact in-adapter timing around the production codec only and export per-input accepted/rejected samples. |
| Live accepted Handshake decode, `n >= 30` | reusable | Amaru run `20260919T135417Z-cca4cc8d` retained 40 supported accepted attempts and non-zero `mux-cbor-item`, `mini-protocol-decode`, and `handshake-negotiation` samples. Cardano run `20260919T032200Z-59f94558` retained 493 protocol decode samples, but its workload contains only the unsupported-version case. | Add the exact 100-attempt live cases to both final legs and retain outcome-specific sample floors. |
| Live rejected Handshake decode, `n >= 30` | reusable | Amaru workload result `outputs/amaru-measurement-calibration/result.json.workload_identity.cases` and `.attempts.by_case` retain 40 malformed and 40 unsupported cases. | Extend the generic workload helper so Cardano receives the same unsupported and malformed cases, and both final legs meet the frozen 100-attempt case counts. |
| Exact target, health, and progress assertions | reusable | Gate 2 Amaru helper retains target state, fatal signals, and tip before/after in `outputs/amaru-measurement-calibration/result.json.target_health`. Existing observation assertions include `all_nodes_responsive` and `peer_connectivity_observed` at `dwarf/primitives/registry.json:27-35,898-906`. | Add the contract-named exact-target, health, progress, containment, and round-trip assertions for both implementations. |
| Required collectors | reusable | All eight implementation-specific collector definitions exist. Exact patched decode and resource collectors have finalized in retained runs. | Select them in the final scenarios and meet every sample floor; definitions alone are not final evidence. |
| Unrelated metrics | accepted unavailable | The card permits metrics unrelated to decode, resources, workload accounting, target health, or progress to remain unavailable with reasons. | No core CBOR requirement can use this exception. |
| Final scenarios and retained reports | missing | No `client-example-cbor-decoding-{implementation}-patched` scenario exists. | Add both scenario legs and run them through deployed DWARF. |

Verdict: partial. Dataset qualification and live Amaru decode evidence are strong reusable foundations. The exact-release codec adapters, canonical round-trip output, exact codec timing, Cardano malformed live case, final assertions, and final scenarios are still required.

Child explanation: DWARF has the correct box of 100 messages, but the two exact-version readers and the repeat-writing check are not built yet.

## Card 02 — Plutus VM conformance and execution cost

| Required item | Status | Exact existing evidence | Missing piece |
|---|---|---|---|
| Controlled valid and invalid scripts | reusable | `dwarf/corpora/cardano-measurement/always-succeeds-v2.plutus` and `always-fails-v2.plutus` are retained. Cardano run `20260919T032200Z-59f94558`, `outputs/cardano-measurement-calibration/result.json.plutus_workload`, retains both script hashes, two transaction IDs, one accepted result, and one rejected result. | Expand to the frozen program set and bind each case to Plutus version and cost-model digest. |
| Existing live Plutus probe | reusable | `runtime_plutus_phase2_submit_probe` and `runtime_plutus_phase2_differential_observation` are registered at `dwarf/primitives/registry.json:2177-2204`. The existing smoke scenario uses a Cardano 10.7.1 two-node substrate, not either exact frozen target: `dwarf/scenarios/runtime-substrate-plutus-phase2-differential-amaru-cardano-node-example-smoke.yaml:3-50`. | Add exact target-specific workloads. Do not promote the old smoke scenario. |
| Exact-revision Cardano Plutus timing | reusable | Cardano run `20260919T032200Z-59f94558` finalized `cardano-patched-ledger-plutus-stages` with four `plutus_vm` samples. The patch times accepted and rejected evaluator outcomes: `dwarf/targets/cardano-node/measurement-patches/fef83fed01d7926f3de83b3b917be5a4a48768b5/0003-plutus-vm-measurement.patch:46-64,78-90`. | The card requires at least 30 samples per outcome. Current evidence has four total. |
| Exact-revision Amaru Plutus timing | missing | `amaru-stock-plutus-execution` supports the exact frozen source and maps four stock span stages: `dwarf/profile_manager/measurement_collectors/amaru_stock.py:630-636`. | Retained Amaru runs have zero VM samples. Add a controlled workload that reaches the real Amaru evaluator. |
| Result parity | missing | Existing Cardano evidence proves only its own two live outcomes. The older differential primitive compares admission decisions, not exact evaluator output for the frozen program set. | Build exact-revision evaluator adapters and retain normalized result values for both implementations. |
| CPU and memory budget parity | missing | The current Cardano patch emits outcome and duration only: `dwarf/targets/cardano-node/measurement-patches/fef83fed01d7926f3de83b3b917be5a4a48768b5/0003-plutus-vm-measurement.patch:56-64`. The current Amaru collector exports stage durations only: `dwarf/profile_manager/measurement_collectors/amaru_stock.py:630-636`. | Exact adapters must retain CPU budget, memory budget, Plutus version, and cost-model digest. |
| VM wall-clock by outcome, `n >= 30` | reusable | Both measurement definitions exist. Cardano has four retained samples; Amaru has zero. | Drive at least 30 valid and 30 invalid program evaluations through each exact evaluator boundary. |
| Target resources and workload accounting | reusable | Resource and workload collectors finalized in the retained target runs. | Correlate samples to the exact Plutus window and meet the card’s evidence floor. |
| Functional assertions | missing | `plutus_phase2_differential_equivalent` exists at `dwarf/primitives/registry.json:928-936`, but it checks admission equivalence only. | Add `plutus_result_and_budget_match` and `plutus_live_outcomes_observed` against retained evaluator records. |
| Unrelated metrics | accepted unavailable | Protocol queue, restart, sync, fork-switch, and epoch metrics can remain unavailable with stated reasons. | Result, budget, timing, target health, and workload identity cannot use this exception. |
| Final scenarios and retained reports | missing | No `client-example-plutus-vm-{implementation}` scenario exists. | Add both scenario legs and run them through deployed DWARF. |

Verdict: partial. Cardano proves that exact live VM timing can be collected, but the sample is very small. Result/budget conformance and the real Amaru workload are the principal gaps.

Child explanation: DWARF can see a few Cardano programs run. It must still check many programs, count their allowed work, and make Amaru run the same programs.

## Card 03 — invalid mini-protocol containment and node cost

| Required item | Status | Exact existing evidence | Missing piece |
|---|---|---|---|
| Exact Amaru Handshake cases | reusable | Run `20260919T135417Z-cca4cc8d` proves reachability and classification for 40 unsupported, 40 malformed, and 40 supported attempts. Workload digest: `sha256:cb649d0b4f89d338615371178cff9676684c9b41a5c8a9fdce34bdc04846c5a9`. | The frozen card requires 100 unsupported and 100 malformed attempts under workload identity `fixed-handshake-invalid-cases-v1`. Run that exact count with baseline, hostile, and recovery windows. |
| Exact Amaru internal boundaries | reusable | The Gate 2 run retains 120 ingress, 80 decode, and 80 negotiation samples across `mux-cbor-item`, `mini-protocol-decode`, and `handshake-negotiation`. Patch boundaries are declared at `dwarf/targets/amaru/measurement-patches/b159172f25a9c389f82f20bca4f15e3032791638/manifest.json:76-99`. | Bind new samples from the exact frozen 200-attempt workload to final card windows and resource evidence. |
| Exact Cardano protocol boundary | reusable | Run `20260919T032200Z-59f94558` retains 493 `protocol_receive_decode` samples from exact 11.1.2 patched code. Patch boundary and outcomes are declared at `dwarf/targets/cardano-node/measurement-patches/fef83fed01d7926f3de83b3b917be5a4a48768b5/manifest.json:66-73`. | Extend the workload to supported, unsupported, and malformed cases and retain exact per-case reachability. |
| Malformed Cardano CBOR case | missing | The retained Cardano workload contains only `unsupported-version-refusal`; its workload digest is `sha256:0378c3987cd4f2440fa91e8b6dca2d49af6ffb742fb44361d7ea4591ae2bd21f`. | Add the same bounded malformed payload and classification used by the Amaru leg. |
| Baseline, hostile, drain, and recovered windows | missing | Measurement runtime can write and distribute phase markers: `dwarf/profile_manager/measurement_runtime.py:135-198`. Existing scenarios expose only coarse scenario phases; the card’s named marker primitives are absent. | Add explicit marker primitives and retain non-overlapping windows around the hostile workload. |
| Target CPU and RSS per window, `n >= 30` | reusable | Resource collectors sample the exact process and retain raw samples: `dwarf/profile_manager/measurement_collectors/amaru_resources.py:76-204`. Retained Cardano run has 78/79 CPU/RSS samples; retained Amaru protocol run has 13/14. | Add window attribution and extend each final workload to at least 30 samples in every required window. |
| Target health, fatal signal, and progress | reusable | Amaru helper evidence includes running before/after, unchanged restarts, no OOM, no fatal signals, expected classifications, and honest chain progress. | Add contract-named shared primitives and repeat the proof under both exact final workloads. |
| Unrelated peer session remains usable | missing | Existing `peer_connectivity_observed` can verify observed edges, but Gate 2 did not exercise a separate honest session during and after the hostile cases. | Add an independent honest peer/session check before, during, and after the hostile window. |
| Required collectors | reusable | All required collectors exist. The Amaru patched run finalized 14 collectors with no errors. Cardano patched run finalized 12 with no errors. | Final scenario selection and sample floors remain required. |
| Unrelated metrics | accepted unavailable | Ledger, Plutus, epoch, and blockfetch-queue metrics can remain unavailable with stated reasons. | Protocol, resources, health, progress, and unrelated-peer requirements cannot use this exception. |
| Final scenarios and retained reports | missing | No `client-example-invalid-mini-protocol-{implementation}` scenario exists. | Add both final scenario legs. |

Verdict: closest to acceptance. Amaru’s core hostile workload and internal measurements are proven. Shared windows, resource attribution, unrelated-peer proof, the equivalent Cardano case set, and final scenarios remain.

Child explanation: Amaru already stopped the good, unsupported, and broken Handshake messages correctly. DWARF must now watch another honest friend at the same time and measure the node before, during, and after the attack.

## Card 04 — block application and chain progress

| Required item | Status | Exact existing evidence | Missing piece |
|---|---|---|---|
| Amaru block-application collector | reusable | Exact stock telemetry mapping is implemented at `dwarf/profile_manager/measurement_collectors/amaru_stock.py:637-642`. Run `20260919T135417Z-cca4cc8d` retained four samples; stock run `20260918T234213Z-64959688` also retained four. | Produce at least 30 samples in one controlled adopted-block window. |
| Cardano block-application collector | reusable | Exact patched collector maps the block-application stage at `dwarf/profile_manager/measurement_collectors/cardano_patched.py:186-204`. Run `20260919T032200Z-59f94558` retained 29 samples. | Produce at least 30 correlated samples in the final controlled window. Do not round 29 up to 30. |
| Adopted block identities and monotonic range | missing | Existing sync collectors retain start/end height and hash, but not one identity per application sample. | Retain at least 30 adopted block hashes/heights and correlate each application sample to the same target and window. |
| Controlled chain-progress workload | missing | Existing target topologies forge and follow real blocks, and `runtime_multi_node_observation` is reusable. The exact `runtime_controlled_chain_progress_window` primitive is absent. | Add a bounded 30-block window with warm-up, start/end markers, timeout, and monotonic progress checks. |
| Target resource distribution, `n >= 30` | reusable | Cardano resource run has enough whole-run samples; Amaru runs do not. Raw samples are retained by the resource collectors. | Bind at least 30 samples to the exact 30-block window for each target. |
| No panic, fatal exit, OOM, or restart | reusable | Gate 2 Amaru helper already performs these checks. `panic_path_contained` exists at `dwarf/primitives/registry.json:840-848`. | Add exact shared assertions for both final legs and classify known background signatures. |
| Unrelated metrics | accepted unavailable | Plutus, restart-readiness, protocol queue, and epoch-transition metrics can remain unavailable when not reached. | Block application, adopted blocks, resources, health, and progress cannot use this exception. |
| Final scenarios and retained reports | missing | No `client-example-block-application-{implementation}` scenario exists. | Add both scenario legs and retain final runs. |

Verdict: partial. Both exact targets already emit the correct timing class. Cardano is one sample short, but both implementations still need a controlled adopted-block range and exact event correlation.

Child explanation: DWARF can time some blocks now. It must watch at least 30 named blocks and prove that every time belongs to one of those blocks.

## Card 05 — restart, recovery, and synchronization

| Required item | Status | Exact existing evidence | Missing piece |
|---|---|---|---|
| Restart collector contract | reusable | The collector requires `restart_started`, `listener_ready`, `chain_progress_ready`, and `peer_role_ready` in order: `dwarf/profile_manager/measurement_collectors/amaru_external.py:318-380`. Equivalent Cardano definition exists. | Emit these events from observed real target state for both implementations. |
| Retained restart-readiness measurement | missing | All cited retained runs report `restart_readiness.status=unavailable` with all three readiness gates missing. | Run one real exact-target restart and retain all gates with monotonic timestamps. |
| Existing restart primitive | reusable | `runtime_profile_restart_recovery` is registered at `dwarf/primitives/registry.json:2354-2362`, but it runs a fixed legacy Profile A helper: `dwarf/profile_manager/primitives.py:13656-13758`; the helper restarts the whole Profile A session: `dwarf/scripts/runtime_profile_recovery_check.py:267-304`. | Add `runtime_real_target_restart_and_readiness` for one selected profile target. Do not reuse the metadata-only or whole-session behavior as final proof. |
| Existing node restart fault | reusable | `runtime_restart_node` is registered at `dwarf/primitives/registry.json:2394-2402` and can restart named runtime nodes. | Couple restart execution to exact target identity and the three observed readiness gates. |
| Sync-speed collector | reusable | `SyncSpeedCollector` retains two monotonic tip observations, heights, hashes, configured expected endpoints, and peer policy: `dwarf/profile_manager/measurement_collectors/amaru_external.py:384-473`. Retained runs show available rates. | Existing runs have `expected_start_height=null` and `expected_end_height=null`; they are not controlled post-restart ranges. Add exact endpoints and require the end height/hash. |
| Controlled peer policy | reusable | Frozen profiles and topologies exist. The Amaru control uses one support producer; Cardano uses a three-node mesh. | Retain the selected peers and required peer-role evidence in each final run. |
| Pre- and post-restart progress | missing | Gate 2 proves ordinary progress only. The older restart finding scenario is known-failing background and targets Cardano 10.7.1 plus Amaru 10.11: `dwarf/scenarios/consensus-restart-rollback-in-future-differential.yaml:19-35,63-96`. | Require five blocks before restart, all readiness gates, then five blocks after readiness for the exact frozen target. |
| Recovery resources, `n >= 30` | reusable | Resource collectors retain raw exact-process samples. | Keep collection active from restart start through the controlled end point and retain at least 30 samples. |
| Known Amaru restart signatures | proven | The frozen card cites `dwarf/docs/finding-amaru-restart-rollback-in-future-crash.md`. | Classify matching `RollbackPointInFuture` or `EADDRINUSE` signals as background. Do not report them as new. Unclassified fatal behavior still fails. |
| Unrelated metrics | accepted unavailable | Plutus, protocol decode, blockfetch queue, and epoch-transition metrics can remain unavailable with a reason. | Restart gates, exact sync range, resources, and progress cannot use this exception. |
| Final scenarios and retained reports | missing | No `client-example-restart-recovery-sync-{implementation}` scenario exists. | Add both scenario legs and retain final runs. |

Verdict: partial. The collectors and old recovery mechanisms are reusable. No retained run proves the frozen real-target restart boundary because the required readiness events and controlled post-restart range do not exist yet.

Child explanation: DWARF knows what “ready again” must mean, but no final test has pressed restart and checked all three green lights yet.

## Frozen scenario and primitive index

The exact names in this section are the contract interface. Similar existing names are foundations only.

| Card | Exact scenario legs | Status |
|---|---|---|
| 01 | `client-example-cbor-decoding-amaru-patched`; `client-example-cbor-decoding-cardano-patched` | missing |
| 02 | `client-example-plutus-vm-amaru`; `client-example-plutus-vm-cardano` | missing |
| 03 | `client-example-invalid-mini-protocol-amaru`; `client-example-invalid-mini-protocol-cardano` | missing |
| 04 | `client-example-block-application-amaru`; `client-example-block-application-cardano` | missing |
| 05 | `client-example-restart-recovery-sync-amaru`; `client-example-restart-recovery-sync-cardano` | missing |

| Exact primitive or assertion | Cards | Status | Reusable foundation or required action |
|---|---|---|---|
| `runtime_verify_exact_target` | 01–05 | missing | Use the existing profile/version/patch identity resolvers and fail closed on any mismatch. |
| `runtime_mark_baseline_window` | 03 | missing | Use `MeasurementRuntime.mark_phase`; add a declarative primitive. |
| `runtime_mark_hostile_window` | 03 | missing | Use `MeasurementRuntime.mark_phase`; add a declarative primitive. |
| `runtime_mark_recovery_window` | 03 | missing | Use `MeasurementRuntime.mark_phase`; add a declarative primitive. |
| `runtime_target_health_and_progress` | 01–05 | missing | Reuse Gate 2 target health and tip checks behind one implementation-neutral primitive. |
| `runtime_peer_session_health` | 03, 05 | missing | Reuse multi-node connection observation and add explicit before/during/after evidence. |
| `runtime_wait_for_chain_progress` | 04, 05 | missing | Reuse exact tip probes and bounded waiting. |
| `runtime_version_pinned_cbor_conformance` | 01 | missing | Wrap the new exact-release codec adapters and frozen dataset. |
| `runtime_protocol_decode_cases` | 01, 03 | missing | Generalize the proven Amaru calibration workload and Cardano helper without changing existing scenarios. |
| `runtime_version_pinned_plutus_conformance` | 02 | missing | Wrap exact-release evaluator adapters and frozen programs. |
| `runtime_controlled_plutus_transactions` | 02 | missing | Extend the exact target workloads to produce valid and invalid real transactions. |
| `runtime_controlled_chain_progress_window` | 04 | missing | Add a bounded adopted-block range with exact identities. |
| `runtime_real_target_restart_and_readiness` | 05 | missing | Restart one selected real target and emit all three observed readiness gates. |
| `runtime_controlled_sync_range` | 05 | missing | Bind start/end heights, hashes, timestamps, and peer policy. |
| `cbor_conformance_clean` | 01 | missing | Evaluate all 100 exact outcomes with no crash, hang, or mismatch. |
| `cbor_roundtrip_consistent` | 01 | missing | Evaluate stable second-encode equality for every successful decode. |
| `invalid_protocol_cases_contained` | 01, 03 | missing | Reuse exact attempt transcripts; require no hang or unclassified result. |
| `plutus_result_and_budget_match` | 02 | missing | Compare normalized results plus CPU and memory budgets by version and cost model. |
| `plutus_live_outcomes_observed` | 02 | missing | Require correlated accepted and rejected real transactions. |
| `target_progress_continues` | 01–05 | missing | Reuse exact tips; require bounded monotonic post-workload progress. |
| `unrelated_peer_session_usable` | 03 | missing | Require the independent honest session to remain or recover within the card bound. |
| `no_target_fatal_signal` | 03–05 | missing | Reuse target state/log checks and the known-background classification list. |
| `minimum_adopted_block_range_observed` | 04 | missing | Require at least 30 exact adopted block identities. |
| `block_application_samples_correlated` | 04 | missing | Require at least 30 application events tied to the controlled target/window/block range. |
| `restart_readiness_complete` | 05 | missing | Require restart start plus listener, progress, and peer-role gates in order. |
| `controlled_sync_range_complete` | 05 | missing | Require the retained exact end height and hash under the frozen peer policy. |

## Frozen measurement and collector index

| Card | Exact required measurement IDs | Current classification |
|---|---|---|
| 01 | `production_codec_decode`; `live_protocol_receive_decode_accepted`; `live_protocol_receive_decode_rejected` | missing codec-only distribution; live paths reusable |
| 02 | `plutus_vm_by_outcome`; `plutus_cpu_budget`; `plutus_memory_budget` | timing reusable for Cardano; Amaru timing and both budget results missing |
| 03 | `handshake_ingress_by_outcome`; `handshake_decode_and_state_by_outcome`; `handshake_negotiation_by_outcome`; `target_cpu_percent`; `target_rss_bytes` | Amaru protocol distributions reusable; Cardano case coverage and all window-scoped resources missing |
| 04 | `block_application`; `adopted_blocks`; `target_resources` | application and raw resource collectors reusable; correlated floor missing |
| 05 | `restart_readiness`; `sync_speed`; `recovery_resource_cost` | sync collector reusable; exact restart and recovery window evidence missing |

| Collector | Definition | Retained evidence status | Classification |
|---|---|---|---|
| `amaru-patched-protocol-decode` | `dwarf/measurements/amaru-patched-protocol-decode.yaml` | Finalized with non-zero three-boundary samples in `20260919T135417Z-cca4cc8d`. | reusable |
| `amaru-stock-network` | `dwarf/measurements/amaru-stock-network.yaml` | Finalized in retained Amaru runs. | reusable |
| `amaru-stock-resources` | `dwarf/measurements/amaru-stock-resources.yaml` | Finalized with raw process samples; no required per-window final distribution yet. | reusable |
| `amaru-external-workload-accounting` | `dwarf/measurements/amaru-external-workload-accounting.yaml` | Finalized with 120 classified attempts in the Gate 2 runs. | reusable |
| `amaru-stock-plutus-execution` | `dwarf/measurements/amaru-stock-plutus-execution.yaml` | Finalized with zero VM samples in cited runs. | reusable |
| `amaru-stock-block-epoch` | `dwarf/measurements/amaru-stock-block-epoch.yaml` | Finalized with four application samples in cited runs. | reusable |
| `amaru-external-sync-speed` | `dwarf/measurements/amaru-external-sync-speed.yaml` | Finalized with start/end observations, but no frozen expected endpoints. | reusable |
| `amaru-external-restart-readiness` | `dwarf/measurements/amaru-external-restart-readiness.yaml` | Finalized as unavailable because all readiness gates were absent. | reusable |
| `cardano-patched-protocol-decode` | `dwarf/measurements/cardano-patched-protocol-decode.yaml` | Finalized with 493 decode samples in `20260919T032200Z-59f94558`; final case set incomplete. | reusable |
| `cardano-stock-network` | `dwarf/measurements/cardano-stock-network.yaml` | Finalized in the retained Cardano run. | reusable |
| `cardano-stock-resources` | `dwarf/measurements/cardano-stock-resources.yaml` | Finalized with 78/79 CPU/RSS samples; no required final window attribution. | reusable |
| `cardano-external-workload-accounting` | `dwarf/measurements/cardano-external-workload-accounting.yaml` | Finalized with 100 unsupported-version attempts. | reusable |
| `cardano-patched-ledger-plutus-stages` | `dwarf/measurements/cardano-patched-ledger-plutus-stages.yaml` | Finalized with four VM and 29 block-application samples. | reusable |
| `cardano-stock-ledger-block-epoch` | `dwarf/measurements/cardano-stock-ledger-block-epoch.yaml` | Finalized; authoritative patched stage data is still required for the card boundary. | reusable |
| `cardano-external-sync-speed` | `dwarf/measurements/cardano-external-sync-speed.yaml` | Finalized with two tip observations, but no frozen expected endpoints. | reusable |
| `cardano-external-restart-readiness` | `dwarf/measurements/cardano-external-restart-readiness.yaml` | Finalized as unavailable because all readiness gates were absent. | reusable |

No required collector is accepted as unavailable. Only unrelated metrics named by each card can use the `accepted unavailable` classification.

## Cross-card status

| Area | Classification | Cards affected | Decision |
|---|---|---|---|
| Exact version-pinned target profiles and patch identities | proven | 01–05 | Reuse without changing target versions. |
| Measurement resolution, collector lifecycle, reports, evidence bundles, GUI, and exports | proven | 01–05 | Reuse. Do not build another reporting path. |
| Exact final scenario legs | missing | 01–05 | Add ten new scenario files; do not modify proven scenarios. |
| Exact target verification | missing | 01–05 | Implement once and reuse in every leg. |
| Health, progress, fatal-signal, and peer-session evidence | reusable | 01–05 | Implement one shared evidence helper and thin primitive/assertion wrappers. The exact contract primitives remain missing. |
| Explicit baseline, hostile, recovery, and controlled-progress windows | missing | 03–05 | Add one general marker/window mechanism and reuse it. |
| Window-scoped process resources | reusable | 03–05 | Attribute raw resource samples to retained windows; the correlation/report slice is missing. Do not replace the resource collector. |
| Exact-release CBOR adapters and canonical round trips | missing | 01 | Add fail-closed adapters. |
| Exact-release Plutus result and budget adapters | missing | 02 | Add fail-closed adapters. |
| Amaru live Plutus workload | missing | 02 | Add controlled valid and invalid transactions. |
| Cardano supported/unsupported/malformed Handshake case set | missing | 01, 03 | Extend the existing calibration helper once. |
| Controlled 30-block adoption range | missing | 04 | Add one progress-window primitive and event correlation. |
| Exact-target restart gates and controlled sync range | missing | 05 | Add one target lifecycle helper used by both implementations. |

## Minimum implementation sequence

### Batch G3-A — shared proof controls and final invalid-protocol scenarios

Implement first because it has the highest cross-card value and completes the card closest to acceptance.

1. Add `runtime_verify_exact_target`, `runtime_target_health_and_progress`, and `runtime_peer_session_health`.
2. Add explicit `runtime_mark_baseline_window`, `runtime_mark_hostile_window`, and `runtime_mark_recovery_window` support.
3. Add window attribution for the existing raw CPU, RSS, network, disk, FD, and thread samples.
4. Add `invalid_protocol_cases_contained`, `target_progress_continues`, `unrelated_peer_session_usable`, and `no_target_fatal_signal`.
5. Reuse the proven Amaru case-set helper.
6. Generalize the case helper without changing existing scenarios. Run the final Card 03 legs with exactly 100 unsupported and 100 malformed attempts per implementation. Preserve support for the accepted case needed by Card 01.
7. Add the two additive `client-example-invalid-mini-protocol-*` scenarios.
8. Run focused tests and one short end-to-end rehearsal for each implementation before the next batch.

This batch must not change existing calibration scenarios or their retained evidence.

### Batch G3-B — controlled progress, restart, and synchronization

1. Add `runtime_wait_for_chain_progress` and `runtime_controlled_chain_progress_window`.
2. Correlate at least 30 block-application events with retained adopted block identities.
3. Add `runtime_real_target_restart_and_readiness` and emit all required readiness events from observed state.
4. Add `runtime_controlled_sync_range` with exact start/end heights, hashes, timestamps, and peer policy.
5. Add the Card 04 and Card 05 assertions and four additive scenario legs.

### Batch G3-C — exact conformance adapters

1. Build revision-locked CBOR adapters with outcome, canonical round-trip output, and codec-only timing.
2. Build revision-locked Plutus evaluator adapters with result, CPU budget, memory budget, Plutus version, cost-model digest, and VM-only timing.
3. Add the controlled Amaru and Cardano live Plutus workloads.
4. Add Card 01 and Card 02 assertions and four additive scenario legs.

## Scope decision

Do not implement every partial measurement in the broader client program before the five runs. Implement the missing requirements in Batches G3-A through G3-C. Then rehearse the exact five examples. Weekly automation, mixed-node measurement comparison, stable release thresholds, full stress coverage, deep-chain-switch instrumentation, and presentation preparation remain deferred.

Technical conclusion: the five examples are not ready for final runs. Gate 2 proved the measurement foundation. Gate 3 must now add the shared proof controls and the card-specific adapters or workloads identified above. The first valid end-to-end target is Card 03, not all five at once.

Child explanation: Do not try the five final races yet. First add the missing traffic lights and measuring lines. Test the attack-message race first because most of it already works. Then finish the block/restart races and the two conformance races.
