# DWARF presentation evidence ledger

Ledger date: 2026-09-10

This ledger separates executable definitions from valid runtime evidence. A
scenario appearing in the catalog or threat map is not counted as tested until
a run proves that its intended workload reached the intended target and that
its oracle was non-vacuous.

## Evidence states

| State | Meaning |
|---|---|
| Proven locally | A real-node run completed locally with target identity, workload activity, and assertions/evidence sufficient for the stated claim. |
| Proven in Antithesis | A completed Antithesis run shows the intended target/workload/property was reached and the relevant harness contract held. |
| Finding | Evidence supports a node, interoperability, or conformance defect. |
| Partial | Some intended dimensions are valid, while another required condition remains unproven or failed for an unrelated reason. |
| Harness-invalid | Harness behavior prevents the affected result from being attributed to the system under test. |
| Infrastructure-invalid | Build, registry, setup, or platform failure prevented test signal. |
| Known / duplicate | Reproduces an already documented upstream issue or previously classified DWARF finding. |
| Untested | No evidence meeting the stated claim exists. |

States apply to claims, not whole repositories. One run can prove protocol
reachability while being harness-invalid for recovery.

## Current mixed mini-protocol campaign

### Identity

| Field | Value |
|---|---|
| Scenario directory | `antithesis/cardano_amaru_miniprotocol_security` |
| Public source revision | `63a62fadfaacddb7b1f586bafa62ae74af47cddc` |
| Moog test-run ID | `a2405de88ffa86d1d3320db18f245c55c51baabaea13a8c505c518c8cf85377a` |
| Antithesis run ID | `e9dc8b8143abf7152300a1a2ca290096-60-7` |
| Requested duration | 60 minutes |
| Started | `2026-09-09T13:14:28Z` |
| Completed | `2026-09-09T14:48:02Z` |
| Antithesis version | `v60-7` |
| Config image digest | `sha256:5738a638b153fd03974296b03df659c6457c4b311552e18ee9f0a48d6c560659` |
| Stateful N2N / DWARF adversary digest | `sha256:c5e35065b9a58c337cd770ef0317bf11c1057a5869d654f7873275d3c8fe1aa1` |
| State-machine workload digest | `sha256:9fcea8709a6426c84fca6008435ae26a292734484b2006db6f99a331110bfb5a` |
| Cardano-node digest | `sha256:3275d357053d21f3220f74b0854fd584e1fe322dfa1bbb78effd760c3191d14c` |
| Amaru/bootstrap-producer digest | `sha256:aabaf9e1fc1f58045329e14c1127c5424ba4794855d39bce05e3b426b7025c36` |
| Deterministic workload seed | `0x20260907` |

### What the run validly proves

- The environment built without Antithesis build errors or warnings.
- All ten images were `amd64`; all expected functional containers joined the
  Antithesis network.
- Both the Cardano-node and Amaru victims were reached.
- The stateful N2N workload served ChainSync, BlockFetch, TxSubmission2,
  and KeepAlive traffic at large non-zero counts (roughly 959k observations per
  protocol across explored histories).
- The identical pre-fault transcript was matched, with complete coverage of all
  24 protocol/class cells: four protocols multiplied by WrongAgency,
  OutOfState, PrematureTerminal, PostTerminal, Flood, and Duplicate.
- The per-target/per-cell reachability properties shown in the first result
  page pass with approximately 15,428–15,429 examples each.
- `mixed_sm_cardano_no_panic_or_fatal_termination` and
  `mixed_sm_amaru_no_panic_or_fatal_termination` each pass with 19,972
  examples.
- Antithesis ran the fault injector, full VM parallelism, and a virtual-time to
  wall-time ratio of approximately 0.90.

This is valid Antithesis proof that the mixed state-machine fuzzer was real and
non-vacuous. It is not a clean containment/recovery result.

### Why the containment result is not valid for the intended contract

`mixed_sm_illegal_sessions_contained` has 13,624 passing examples and 1,245
counterexamples. At the representative counterexample
`input_hash=-7047584660228149819`, `vtime=707.2873949829955`, the observer
reported:

- both targets reachable;
- complete Cardano and Amaru cell coverage;
- no fatal signal in either victim;
- both victims recovered;
- the Amaru-fed consumer recovered and converged;
- honest control progress continued;
- `unrelated_peers_usable=false` only because control producer `p2` was
  unavailable (`cardano-cli ... Connection refused`).

The Compose labels were parsed into exclusion lists containing bare service
names such as `p2` and `sm-workload`, while Antithesis node-fault events address
the explicit container names `dwarf-control-p2` and `dwarf-sm-workload`.
Antithesis therefore killed/stopped containers intended to be protected. The
same service-name/container-name mismatch was already documented in the
official 2026-W36 report for `cardano_amaru_kes_security`.

Classification:

- Protocol delivery and required-cell coverage: **Proven in Antithesis**.
- No observed Cardano/Amaru victim panic in reached histories: **Proven in
  Antithesis for this run**.
- Containment plus unrelated-peer availability: **Harness-invalid**.
- Recovery/convergence under the intended protected-control contract:
  **Partial; must be rerun after the exclusion-name repair**.
- `cluster fork depth < k` (9 counterexamples): **Harness-invalid pending the
  same exclusion repair**.
- One `No Antithesis errors` ENODEV event: **Antithesis-platform signal**, not a
  node finding.
- One control Amaru relay exit and recurring workload exit-code 2 events:
  **triage required / known-background candidates**, not new findings from this
  ledger.

## Strong local mixed mini-protocol evidence

| Field | Value |
|---|---|
| Run | `20260908T100054Z-80498541` |
| Scenario | `cardano-amaru-miniprotocol-security-local` |
| Seed | `0x20260907` |
| Started / ended | `2026-09-08T10:00:54.544Z` / `2026-09-08T10:15:50.168Z` |
| Wall time | `895.624s` |
| Framework result | pass; one `load_events_are_ok` assertion passed |
| Target version label | `10.7.1+Amaru-v10.11-lineage` |
| Stateful N2N adversary digest | `sha256:c5e35065b9a58c337cd770ef0317bf11c1057a5869d654f7873275d3c8fe1aa1` |
| Final post-setup cases | Cardano 2,600; Amaru 910 |
| Required cells | 24/24 for each target |
| Consumer evidence | 10 matching samples |
| Final tips | Cardano victim, Amaru victim, consumer, p1, p2, p3 all hash-equal at block 288 / slot 1613 |
| Fatal signals | none classified for Cardano or Amaru |
| Final evaluation | classifiable `true`, safe `true`, recovered all `true`, converged `true` |

The first classifiable sample appeared only around elapsed 711 seconds. This run
is strong fallback evidence, but this exact scenario is too slow for a
seven-minute live demo.

## Representative evidence stories

### 1. Amaru epoch-transition rewards panic — finding, fixed upstream

DWARF's mixed adversarial soak found a deterministic Amaru panic at epoch
transition due to a total-rewards mismatch of 1,020 ADA. The official W34
`upstream_amaru_control` run reproduced the same class 2,249 times without
active faults. The root cause was a pool leader reward paid to a never-registered
reward account. Amaru `v10.11.20260807` contains the matching fix.

Presentation state: **Finding → independently reproduced → fixed upstream →
post-fix regression evidence**.

Primary sources:

- `reports/amaru-epoch-transition-rewards-evidence/`
- `dwarf/docs/finding-amaru-epoch-transition-rewards-discrepancy.md`
- official `Antithesis-Report-2026-W34`

### 2. Amaru minimum-fee boundary divergence — finding, follow-up required

Official W34 run `bb2ba66e1d863efbad2e0666e27d9276-59-13` exercised a
fault-free phase-1 boundary: Cardano-node accepted fee 164181 (exact minimum)
and 164182 (minimum plus one), while Amaru returned HTTP 400 for both. Both
rejected 164180 (minimum minus one). The boundary workload was scheduled in one
of four project runs and diverged in that run.

Presentation state: **Finding, valid zero-fault differential; reproduction
count/closure status must be stated as limited**.

Primary source: official `Antithesis-Report-2026-W34`, lines for
`cardano_amaru_adversarial`.

### 3. Four-hour chain-selection differential — high-value negative result

The real mixed Cardano/Amaru mesh ran 466 iterations across honest reorg,
within-k partition recovery, epoch boundary, VRF tiebreak, and
private-fork-under-delay regimes. It recorded zero genuine selection
divergences; nine stage-1 flags were classified as benign one-slot propagation
jitter.

Presentation state: **Proven locally for the stated regimes and revisions; not
a universal consensus proof**.

Primary sources:

- `reports/campaign-reports/dwarf-consensus-chain-selection-differential-campaign.html`
- `dwarf/docs/consensus-4h-campaign-results.md`

## Additional classified findings and results

| Item | Scope | Evidence state | Current interpretation |
|---|---|---|---|
| False-leadership / forged-VRF differential | Consensus validation | Proven locally | Both implementations rejected; differential agreed. Separate setup panic classified elsewhere. |
| Genesis `epochLength=0` / `slotLength=0` | Startup/config validation | Finding | Cardano-node uncaught arithmetic exception; other malformed fields rejected cleanly. |
| Bootstrap trust-source differential | Bootstrap/trust | Partial/model gap | Cardano can rebuild from genesis; Amaru uses imported snapshots and compiled anchors. No exploit claim. |
| State-lifecycle differential | Restart/recovery | Partial/model gap | Different storage/rebuild models; normal packaged Amaru path crossed 49+ epochs cleanly, refuting the original first-boundary hypothesis. |
| Three-element transaction array | CBOR/transaction decode | Finding → fixed | Amaru accepted non-canonical arity at decode; fixed in `v10.11.20260730`; mempool/block impact was not established. |
| Trailing transaction bytes | Submit API decode | Finding, low severity | Still present at `v10.11.20260730`; mempool ingress only, canonical relay, no demonstrated exhaustion. |
| Mutated block-fetch CBOR | Mixed adversarial block handling | Proven locally | 512 mutated-block rejections, zero forged adoptions; honest chain continued. |
| Amaru consumer sync stall | Mixed synchronization | Known / duplicate | Reproducible and likely the same as upstream Amaru issue #736; not a new standalone vulnerability. |
| Custom-testnet bootstrap wall | Bootstrap/capability | Finding + harness enablement | Includes a real tvar-reader bug and a broader custom-network bootstrap capability gap. |
| Amaru listener `EADDRINUSE` | Network fault/restart | Known finding | W36 confirmed crash path under transient accept failure; treat as background until a fixed build is tested. |
| KES adversarial non-adoption | KES / block validation | Partial Antithesis | W36 says the KES security property held; fork-depth result was invalidated by broken exclusions and observer timeout was a workload bug. |

## Official weekly-report coverage

| Report | DWARF content |
|---|---|
| W30–W33 | No `pragma-org/dwarf` / `j-gainsec` section found in the published pages searched. |
| W34 | Four DWARF projects: one build-blocked runtime; mixed adversarial fee finding plus harness issues; independent Amaru epoch panic; cardano-node DWARF setup issues. |
| W35 | One DWARF mixed runtime run fully blocked by dingo networkMagic 164 collision; zero workload signal. |
| W36 | KES mixed run: real Amaru listener crash; KES non-adoption held; observer robustness issue; service-name/container-name exclusion mismatch invalidated fork-depth result. |
| Current W37 candidate | Mixed mini-protocol run `e9dc...` completed and was non-vacuous, but repeated the W36 exclusion mismatch; no weekly report published yet at audit time. |

## Run-count interpretation

The live dashboard currently has 90 retained run directories: 54 framework
pass, 31 framework fail, and 5 framework error, with 65 passing and 32 failing
retained assertions. These are not finding counts. A passing run can be too thin
for a security claim; a failing run can be a successful bug discovery; and an
error can be wholly outside the system under test.

The historical census is broader than the live retention window. It proves
1,893 unique local run IDs with nonempty manifest, scenario, NDJSON log, and
assertion files; 13 additional workbench-only run IDs explicitly classified as
`tier_1_live_confirmed`; and 39 DWARF-owned Antithesis runs. The strict total is
1,945 traceable executions, presented as **1,900+** to avoid false precision.
The census also found 576 distinct scenario IDs and 11 archives satisfying the
current portable evidence bundle contract. Seven weaker workbench references,
preserved copies, upstream runs, raw Compose attempts, and internal fuzz
iterations remain excluded.

## Claims explicitly prohibited by this ledger

- “242 scenarios have been tested.”
- “51 security tests passed.”
- “The mixed mini-protocol Antithesis campaign passed.”
- “The fork-depth counterexamples are Cardano or Amaru consensus bugs.”
- “All networking or mini-protocol behavior is covered.”
- “DWARF currently measures node-level throughput or internal validation
  latency.”
- “The public GitHub revision is the exact revision running on `cardano-box`.”

## Immediate evidence action

Repair the fault-exclusion identity contract by making the generated exclusion
values match the actual Antithesis container identities (or removing explicit
`container_name` where safe), then locally inspect the generated launch
parameters and run one focused Antithesis regression. The rerun needs separate
assertions for control progress, unrelated-peer availability, per-target
recovery, consumer recovery, and convergence so a single aggregate failure
cannot hide the failed subcondition.
