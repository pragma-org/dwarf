# DWARF full-metrics compatibility audit and run recipes

Evidence cut: 2026-09-21. This audit applies to Amaru 10.11.20260912 and Cardano-node 11.1.2 measurement definitions in V7-PRAGMA.

## What “full metrics” means

“Full metrics” means that one run produces non-vacuous evidence for every implemented measurement definition for its implementation. The run must satisfy each collector's workload and evidence prerequisites. It must retain workload-attributable raw or normalized records, correlations, and a meaningful positive sample, event, delta, readiness result, or timing distribution.

### Non-vacuous exercise rule

Configured is not exercised. A collector state of `finalized`, an `available` flag, a zero count, or a configured tap does not prove that the workload exercised the metric. An `unavailable` result is honest evidence of absence, but it is not exercised evidence. Resource and time-series measurements require positive samples or a counter delta inside the bounded workload window. Timing measurements require at least one correlated terminal event. Readiness measurements require the named lifecycle transition.

**Plain meaning:** Putting every ruler on the table does not mean that the test touched every ruler. A ruler counts only when it records the event that it was built to measure.

## Scenario key

The following supported client scenarios are the workload candidates in this audit.

| Key | Exact scenario |
|---|---|
| A1 | `client-example-cbor-decoding-amaru-d3a6dafc-regression` |
| A2 | `client-example-plutus-vm-amaru-onchain-v2` |
| A3 | `client-example-invalid-mini-protocol-amaru` |
| A4 | `client-example-block-application-amaru-canonical-v3` |
| A5 | `client-example-restart-recovery-sync-amaru` |
| C1 | `client-example-cbor-decoding-cardano-patched` |
| C2 | `client-example-plutus-vm-cardano` |
| C3 | `client-example-invalid-mini-protocol-cardano` |
| C4 | `client-example-block-application-cardano-canonical-v2` |
| C5 | `client-example-restart-recovery-sync-cardano` |

## Amaru mechanical compatibility map

| Implemented measurement | Workload and evidence prerequisite | Scenarios that satisfy the workload prerequisite | Retained exercise state |
|---|---|---|---|
| `amaru-stock-header-lifecycle` | Live headers with Observe, Select, Fetch, or Adopt records and header correlations | A1, A2, A3, A4, A5 | Exercised in A4 run `20260921T035546Z-9747122c` |
| `amaru-stock-fork-switch` | Live chain selection; a switch or bounded canonical progress record | A4 | Exercised in A4 |
| `amaru-stock-mempool` | Real submitted transactions and correlated admission or terminal outcomes | A2 | Not non-vacuous in retained client runs |
| `amaru-stock-ledger-rules` | Real transaction validation with named rule or phase-one spans | A2 | Not non-vacuous in retained client runs |
| `amaru-stock-plutus-execution` | Real Plutus acquire, decode, build, or evaluate records correlated to a transaction | A2 | Not non-vacuous in retained client runs; Card 02 security evidence is separate |
| `amaru-stock-block-epoch` | Live block application or epoch-boundary records correlated to block hashes | A4, A5 | Exercised in A4 |
| `amaru-stock-network` | Live peer or mini-protocol traffic with connection/protocol records | A1, A2, A3, A4, A5 | Exercised in A4 |
| `amaru-stock-resources` | A live target process plus bounded workload window and positive process/container samples | A1, A2, A3, A4, A5 | Exercised in A4 |
| `amaru-external-restart-readiness` | A controlled real-target restart with listener, progress, and peer readiness gates | A5 | Exercised in A5 run `20260921T045619Z-15e864a0`; not exercised in A4 |
| `amaru-external-sync-speed` | Two retained real chain-tip points spanning controlled progress | A4, A5 | Exercised in A4 |
| `amaru-external-workload-accounting` | Retained DWARF offered-attempt and terminal-outcome events correlated to inputs | A1, A2, A3 | Not non-vacuous in retained client runs |
| `amaru-patched-protocol-decode` | Live revision-locked protocol receive/decode events with terminal outcomes | A1, A3; incidental protocol traffic can appear in A4/A5 | Exercised in A4; A1/A3 are the direct workloads |
| `amaru-patched-blockfetch-queues` | Revision-locked BlockFetch enqueue, dequeue, or residence events | A4, A5 | Exercised in A4 |
| `amaru-patched-txsubmission-residence` | Revision-locked TxSubmission2 queue/residence events correlated to transactions | A2 | Not non-vacuous in retained client runs |
| `amaru-coverage-production-paths` | An Amaru coverage build, coverage campaign ID, corpus IDs, and retained coverage artifacts | No supported client scenario | Reserved/unprofiled; not exercised by the five client scenarios |
| `amaru-stock-header-validation` | Stock consensus traces with per-header accepted/rejected verdicts or opcert rejection reasons in the window | No profile selects it this cycle | Implemented stock collector, unprofiled; not exercised by the five client scenarios |
| `amaru-patched-header-validation` | A future revision-locked header-validation patch | No supported client scenario | Reserved scaffold; returns unavailable with a reason, not exercised |

## Cardano-node mechanical compatibility map

| Implemented measurement | Workload and evidence prerequisite | Scenarios that satisfy the workload prerequisite | Retained exercise state |
|---|---|---|---|
| `cardano-stock-blockfetch` | Live BlockFetch client/server events correlated to peer and block | C1, C2, C3, C4, C5 | Exercised in C1 run `20260920T132629Z-ea000d37` |
| `cardano-stock-chain-lifecycle` | Live ChainSync/ChainDB header and block records | C1, C2, C3, C4, C5 | Exercised in C1 |
| `cardano-stock-ledger-block-epoch` | Live block/ledger application or epoch records | C1, C2, C3, C4, C5 | Exercised in C1 |
| `cardano-stock-txsubmission-mempool` | Real transaction submission and mempool terminal events correlated to a transaction | C2 | Not non-vacuous in retained client runs |
| `cardano-stock-plutus-execution` | Real Plutus transaction outcomes plus retained workload budget events | C2 | Not non-vacuous in retained client runs; Card 02 security evidence is separate |
| `cardano-stock-network` | Live connection, KeepAlive, or mini-protocol records | C1, C2, C3, C4, C5 | Exercised in C1 |
| `cardano-stock-resources` | A live node plus bounded workload window and positive process/cgroup samples | C1, C2, C3, C4, C5 | Exercised in C1 |
| `cardano-external-restart-readiness` | A controlled real-node restart with listener, local-state-query, peer, progress, and workload gates | C5 | Exercised in C5 run `20260921T045807Z-8e2bbb0e`; not exercised in C1 |
| `cardano-external-sync-speed` | Two retained node-tip points spanning controlled progress | C4, C5; continued-progress probes in C1/C2/C3 can also expose it | Exercised in C1 |
| `cardano-external-workload-accounting` | Retained offered-attempt and terminal-outcome events correlated to input or transaction | C1, C2, C3 | Not non-vacuous in retained client runs |
| `cardano-patched-protocol-decode` | Revision-locked live mini-protocol decode records with terminal outcomes | C1, C3 | Exercised in C1 |
| `cardano-patched-ledger-plutus-stages` | Revision-locked block application, epoch, or Plutus-stage records | C2, C4; normal block traffic can expose block stages in C1/C3/C5 | Exercised in C1 |
| `cardano-patched-blockfetch-handler-queue` | Revision-locked BlockFetch handler queue records | C4, C5 | Reserved/unprofiled for the current target; not exercised |
| `cardano-patched-txsubmission-residence` | Revision-locked TxSubmission queue/residence records | C2 | Reserved/unprofiled for the current target; not exercised |
| `cardano-coverage-production-paths` | A coverage target, campaign and corpus IDs, and retained coverage artifacts | Coverage smoke scenarios, not C1–C5 | Reserved/unprofiled; not exercised by the five client scenarios |
| `cardano-stock-header-validation` | Stock ChainSync and ChainDB traces with per-header accepted/rejected verdicts or OCERT rejection reasons in the window | No profile selects it this cycle | Implemented stock collector, unprofiled; not exercised by the five client scenarios |
| `cardano-patched-header-validation` | A future revision-locked header-validation patch | No supported client scenario | Reserved scaffold; returns unavailable with a reason, not exercised |

## Highest-coverage real-node client paths

No single retained scenario exercises every implemented metric.

- Amaru recommendation: A4 with `profile-y-amaru-block-application-nanoseconds-v3`, `amaru-security-patched`, and run `20260921T035546Z-9747122c`. It exercised 8 of 14 configured collectors, or 8 of 17 implemented Amaru definitions when the unprofiled coverage and header-validation definitions are included. Missing configured collectors are restart readiness, workload accounting, mempool, ledger rules, TxSubmission residence, and Plutus execution. A5 adds restart readiness but does not make one run complete.
- Cardano-node recommendation: C1 with `profile-v-cardano-measurement-nanoseconds-v2`, `cardano-security-patched`, and run `20260920T132629Z-ea000d37`. It exercised 8 of 12 configured collectors, or 8 of 17 implemented Cardano definitions when five reserved/unprofiled definitions are included. Missing configured collectors are restart readiness, workload accounting, Plutus execution, and TxSubmission/mempool. C5 adds restart readiness but does not make one run complete.

The reports can truthfully say **all collectors configured** or **all configured collectors finalized**. They cannot say **all metrics exercised**.

**Plain meaning:** The recommended run fills the most boxes on one worksheet. Some boxes need a different kind of event, such as a restart or a transaction, so one worksheet does not fill them all.

## Exact `/run` recipe — Amaru

This recipe browser-tests the broadest retained Amaru path. Do not start it until its deployment profile is active and ready.

1. **Step 1 — Scenario:** In **Find a scenario (Optional)**, enter `client-example-block-application-amaru-canonical-v3`. In **Scenario (Required)**, select **Client example 04 — canonical block progress — Amaru nanoseconds v3 · devnet**.
2. **Step 2 — Target and runtime:** Confirm target `amaru` version `10.11.20260912` and runtime `devnet`.
3. **Step 3 — Profile:** Leave **Deployment profile (Optional override)** at **Scenario default**. Confirm `profile-y-amaru-block-application-nanoseconds-v3`.
4. **Step 4 — Node versions:** Leave **Version policy** at **Profile default**. The resolved policy is `exact`. Leave **Cardano-node version (Exact only)** and **Amaru version (Exact only)** empty because the scenario resolves Amaru `10.11.20260912` at source `b159172f25a9c389f82f20bca4f15e3032791638`. Do not select **Allow this one run to use a catalogued but unconfirmed version**.
5. **Step 5 — Measurements:** Leave **Measurement profile** at **Scenario or compatible default**. Confirm `amaru-security-patched` and 14 taps on `nanoseconds-v3`.
6. **Step 6 — Primitives:** Read the read-only list. It must include `runtime_verify_exact_target`, `runtime_wait_for_chain_progress`, `runtime_controlled_chain_progress_window`, `runtime_target_health_and_progress`, `canonical_chain_progress_complete`, `block_application_samples_correlated`, `target_progress_continues`, and `no_target_fatal_signal`.
7. **Step 7 — Run settings:** Confirm seed `0xB10C0004`, iterations **scenario default**, and execution **Local real-node DWARF engine**. The workload has a 30-second warm-up and a 180-second measurement window. The retained run took about 221 seconds. Allow about 4 minutes after the profile is ready; deployment time is additional.
8. **Step 8 — Readiness:** Confirm `framework`, `profile:profile-y-amaru-block-application-nanoseconds-v3`, and `deployed-version-identity`. The live dashboard currently uses profile-v, so this recipe is not ready until profile-y is deployed through the supported DWARF lifecycle. Open **Open detailed deployment status** and require honest healthy/ready results.
9. **Step 9 — Review:** Review the exact target identity, 14 resolved measurements, primitives, seed, and readiness contract. The current backend label is `Local` with state `supported-unconfirmed`; this label is a wizard-retention limitation, not proof that the accepted run is absent.
10. **Step 10 — Run:** The mutating control is **Start local run**. Do not press it for a browser-only check. If authorized later, a completed run opens at `/operate/runs/<run_id>`. Success signals are four passing security assertions, canonical progress of at least 30 blocks, at least 30 block-application correlations, continued progress, no fatal signal, and retained measurement reports. View or download evidence on that run route.

**Plain meaning:** Pick the Amaru block-progress recipe, keep its saved defaults, and start only when the matching group of nodes is ready.

## Exact `/run` recipe — Cardano-node

1. **Step 1 — Scenario:** In **Find a scenario (Optional)**, enter `client-example-cbor-decoding-cardano-patched`. In **Scenario (Required)**, select **Client example 01 — CBOR decoding conformance — Cardano-node · devnet**.
2. **Step 2 — Target and runtime:** Confirm target `cardano-node` version `11.1.2` and runtime `devnet`.
3. **Step 3 — Profile:** Leave **Deployment profile (Optional override)** at **Scenario default**. Confirm `profile-v-cardano-measurement-nanoseconds-v2`.
4. **Step 4 — Node versions:** Leave **Version policy** at **Profile default**. The resolved policy is `exact`. Leave both exact-version entry fields empty because the scenario resolves Cardano-node `11.1.2` at source `fef83fed01d7926f3de83b3b917be5a4a48768b5`. Do not select the unconfirmed-version checkbox.
5. **Step 5 — Measurements:** Leave **Measurement profile** at **Scenario or compatible default**. Confirm `cardano-security-patched` and 12 taps on `nanoseconds-v2`.
6. **Step 6 — Primitives:** Require `runtime_verify_exact_target`, `runtime_version_pinned_cbor_conformance`, `runtime_protocol_decode_cases`, `runtime_target_health_and_progress`, `cbor_conformance_clean`, `cbor_roundtrip_consistent`, `invalid_protocol_cases_contained`, and `target_progress_continues`.
7. **Step 7 — Run settings:** Confirm seed `0xA7561CD0`, iterations **scenario default**, and execution **Local real-node DWARF engine**. The retained run took about 244 seconds. Allow about 4–5 minutes when profile-v is already ready. Primitive timeouts are safety ceilings, not the estimate.
8. **Step 8 — Readiness:** Confirm `framework`, `profile:profile-v-cardano-measurement-nanoseconds-v2`, and `deployed-version-identity`. Profile-v is the current healthy managed topology, but read the fresh status before start.
9. **Step 9 — Review:** Review the exact target, 12 measurements, primitives, seed, and plan. The local backend currently says `supported-unconfirmed` even though retained accepted run `20260920T132629Z-ea000d37` exists; treat the UI state as a known confirmation-matching limit.
10. **Step 10 — Run:** The action is **Start local run**. Do not press it during browser verification. A later authorized run opens at `/operate/runs/<run_id>`. Success signals are a clean 100-input corpus, consistent round trips, contained invalid live cases, continued progress, and retained raw and normalized evidence. View and download evidence on the run route.

Do not launch Antithesis. The wizard marks Antithesis and GitHub Actions unsupported for these exact plans. This audit did not start a paid or external run.

**Plain meaning:** Pick the Cardano CBOR recipe, keep its exact saved version and measurement set, check that profile-v is healthy, and start only when a real run is approved.

## Claim limits

This audit does not convert availability, zero values, collector finalization, or scenario security evidence into metric exercise. It does not claim a mixed-node benchmark, complete workload coverage, production performance, or support for reserved taps. It does not change any security assertion or accepted Card 03 evidence.
