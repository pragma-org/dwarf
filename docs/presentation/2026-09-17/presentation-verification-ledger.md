# DWARF presentation verification ledger

Verification date: 2026-09-10

Purpose: mechanically trace every material statement in the September 17 main
deck, walkthrough, demo, and handout to a current deployment fact, retained run,
published report, source contract, or explicitly labeled proposal.

Status vocabulary:

- **VERIFIED** — checked against the cited primary evidence during this pass.
- **VERIFIED, BOUNDED** — the statement is true only with the stated scope.
- **PROPOSAL** — a decision request, not current capability or accepted policy.
- **BLOCKED** — a required precondition is currently absent.
- **CORRECTED** — wording was narrowed during this pass.

## Deployment and inventory

| ID | Presentation statement | Status | Mechanical evidence | Exact source |
|---|---|---|---|---|
| D01 | The live dashboard is on the configured deployment host. | VERIFIED | Container `dwarf-fw` maps the configured dashboard port; `/healthz` reports `status: ok`. | `docker inspect dwarf-fw`; `/healthz` |
| D02 | Host is Ryzen 7 6800U, 8 cores / 16 threads, 27 GiB RAM. | VERIFIED | Fresh `lscpu` and `free -h` on `dwarf-host-a`. | host inspection, 2026-09-10 |
| D03 | Docker is 29.6.0; root volume is 915 GiB with 473 GiB free. | VERIFIED | Fresh client/server version and `df -h /`. | host inspection, 2026-09-10 |
| D04 | Runs, bundles, and state are persistent. | VERIFIED | Live container mounts point at `~/.local/share/dwarf/{runs,bundles,state}`; directories remain populated across container restart. | `delivery/docker-compose.dwarf.yml`; live container inspection |
| D05 | There are 242 scenario definitions. | VERIFIED | The inspected `/operate` deployment displayed 242 and the scenario catalog contained the demo candidate. | `/operate`; `/operate/scenarios` |
| D06 | There are 206 registered primitives. | VERIFIED | `jq '.primitives | length'` on the registry inside the running image returns 206. | `/home/dwarf/dwarf-fw/dwarf/primitives/registry.json` in `dwarf-fw` |
| D07 | The inspected dashboard indexed 90 retained run directories. | VERIFIED | Filesystem count was 90; `/metrics` totaled 54 pass + 31 fail + 5 error after the rehearsal. This is a retention-window count, not the lifetime total. | `$DWARF_RUNTIME_ROOT/runs`; `/metrics` |
| D08 | Retained assertions totaled 65 pass and 32 fail. | VERIFIED | Prometheus counters returned those values after the rehearsal. | `/metrics` |
| D09 | Live image is revision `0cf2ca4`; public `main` is `63a62fa`; they are not byte-identical. | VERIFIED | Live OCI revision label is `0cf2ca4`; `git ls-remote` reports public `main` at `63a62fadfaacddb7b1f586bafa62ae74af47cddc`. | `docker inspect dwarf-fw`; public GitHub `refs/heads/main`, checked 2026-09-10 |
| D10 | Inventory counts are not coverage or finding counts. | VERIFIED, BOUNDED | Catalog items are executable definitions; retained run exit state includes harness/infrastructure results and does not encode finding novelty. | `deployment-and-feature-audit.md`; `evidence-ledger.md` |
| D11 | DWARF has 1,945 strictly traceable executions across retained local records, live-confirmed workbench records, and DWARF-owned Antithesis runs. | VERIFIED | 1,893 unique manifest-backed local runs + 13 workbench-only tier-1 live-confirmed runs + 39 DWARF-owned Antithesis runs. Preserved copies were deduplicated by canonical run ID. | `execution-census.html`; dwarf-host-a run manifests; `dwarf`/`dwarf-latest` workbenches; Moog Antithesis records |

## Product and operator workflow

| ID | Presentation statement | Status | Mechanical evidence | Exact source |
|---|---|---|---|---|
| P01 | The core Learn/Operate route set renders. | VERIFIED, BOUNDED | Fresh GETs returned 200 for the exact walkthrough route: `/operate`, `/learn/getting-started`, `/operate/status`, `/operate/scenarios`, `/operate/profiles`, `/operate/targets`, the retained fallback run, and `/operate/bundles`. “Renders” does not prove each mutation path. | live dashboard, 2026-09-10 |
| P02 | An operator can select a scenario and launch it through the GUI. | VERIFIED, BOUNDED | The exact HTTP endpoint invoked by the Run button launched the candidate three times and produced retained manifests. A physical browser click remains a human/day-of rehearsal item, not an untested backend path. | `dwarf/profile_manager/views/scenarios.py`; rehearsal runs `20260910T055940Z-1eb0692a`, `20260910T060441Z-d3a411bb`, `20260910T060723Z-08304f2f` |
| P03 | Run output, assertions, and bundles are retained and inspectable. | VERIFIED | The fallback run page returns 200 and contains manifest, events, assertion, metrics, output, and exact scenario. Eleven unique archives satisfy the current portable-bundle contract; one additional integrity-valid historical archive lacks `manifest.json` and is excluded. | `/operate/runs/20260908T100054Z-80498541`; historical dwarf-host-a bundle census |
| P04 | DWARF supports the Moog submission path. | VERIFIED, BOUNDED | A GUI launch previously produced an on-chain request and Antithesis run identity. Submission is not represented as successful test completion. | deployment audit; retained Moog request/run evidence |
| P05 | The mixed campaigns shown use real node processes and normal interfaces. | CORRECTED | The original absolute “no simulated node” wording was narrowed to the campaigns shown. Their Compose/runtime artifacts use real Cardano-node and Amaru images, TCP/N2N/N2C/submit/query interfaces, and real process/network faults. | scenario Compose files; run manifests; image digests |
| P06 | “All 42 pages” was a defensible exact count. | CORRECTED | The number was unnecessary and ambiguous across dynamic/create routes. Deck now says the implemented route set was checked, while the audit retains the explicit route inventory. | `deployment-and-feature-audit.md`; corrected deck slide 5 |
| P07 | The complete presenter walkthrough includes every current Learn and Operate navigation route. | VERIFIED | The guide contains all 17 `LEARN_SUB_NAV` paths and all 18 `OPERATE_SUB_NAV` paths, plus registered current pages and the dynamic run, comparison, and authoring surfaces. Legacy aliases are labeled supporting rather than first-class navigation. | `dwarf/profile_manager/data/sub_nav.py`; `dwarf/profile_manager/dashboard.py`; `walkthrough-guide.html` |
| P08 | Scenario authoring can be demonstrated without changing the live catalog. | VERIFIED, BOUNDED | `/operate/scenarios/new` renders a GET-based template preview before its separate POST create action. Scenario save overwrites by YAML body ID; profile, target, and primitive forms are immediate write actions, so the presenter explains but does not submit them. | `scenarios_new.j2`; `scenarios_edit.j2`; `profiles_new.j2`; `targets_new.j2`; `primitives_new.j2` |
| P09 | All proposed future operating-model material follows the walkthrough and demo. | VERIFIED | Main slides 1–16 contain current product/evidence and the leave-deck transition. The guide hands directly to the demo; the demo returns to slide 17. Slides 17–21 are visibly labeled proposal/decision content. | `dwarf-presentation-deck.html`; `walkthrough-guide.html`; `demo-guide.html` |

## Tested, partial, and untested evidence

| ID | Presentation statement | Status | Mechanical evidence | Exact source |
|---|---|---|---|---|
| E01 | Amaru epoch rewards panic was reproduced 2,249 times without active faults. | VERIFIED | Official W34 calls the failure deterministic, reports 2,249 identical crashes, and shows `active_faults: {}`. | official `Antithesis-Report-2026-W34`, `upstream_amaru_control` section |
| E02 | The matching Amaru fix shipped in `v10.11.20260807`. | VERIFIED, BOUNDED | Source diff documented the change from `unclaimed_rewards()` to `total_unclaimed_rewards()` and the never-registered pool-owner case matching the observed 1,020 ADA discrepancy. | `dwarf/docs/finding-amaru-epoch-transition-rewards-discrepancy.md` |
| E03 | At fee 164181 and 164182 Cardano accepted while Amaru rejected, with no faults. | VERIFIED | Official W34 reports HTTP 202 for Cardano, HTTP 400 for Amaru, exact minimum 164181, plus-one 164182, and `active_faults: {}`; it also states only one of four runs scheduled this boundary. | official `Antithesis-Report-2026-W34`, `cardano_amaru_adversarial` section |
| E04 | Five mixed chain-selection regimes ran 466 iterations with zero genuine divergence. | VERIFIED, BOUNDED | Campaign totals are 466; 457 direct passes plus nine stage-1 flags. Each flag was a one-slot difference between Amaru relays while Cardano still matched one relay. | `reports/campaign-reports/dwarf-consensus-chain-selection-differential-campaign.html`; `dwarf/docs/consensus-4h-campaign-results.md` |
| E05 | Current mixed mini-protocol Antithesis delivery reached both targets and all 24 cells per target. | VERIFIED, BOUNDED | Authenticated report inspection recorded positive per-target reachability and the 4 protocols × 6 illegal classes for run `e9dc8b8143abf7152300a1a2ca290096-60-7`. | `evidence-ledger.md`; Antithesis run ID `e9dc…-60-7` |
| E06 | No-panic properties had 19,972 passing examples per implementation. | VERIFIED, BOUNDED | Exact property totals were recorded from the authenticated run report. This means no observed panic in reached histories, not universal absence. | `evidence-ledger.md`; Antithesis run ID `e9dc…-60-7` |
| E07 | Containment had 13,624 passing examples and 1,245 counterexamples. | VERIFIED | Exact aggregate recorded from the run report. | `evidence-ledger.md`; Antithesis run ID `e9dc…-60-7` |
| E08 | The containment/recovery verdict was invalidated by protected-control identity mismatch. | VERIFIED | Representative counterexample has recovered victims/consumer and continued honest progress, but `unrelated_peers_usable=false` because `p2` was unavailable. Bare exclusion service names did not match explicit `dwarf-*` container names. | `evidence-ledger.md`; scenario Compose labels; W36 precedent |
| E09 | Node-level throughput and internal stage attribution are not implemented. | VERIFIED | Current telemetry covers dashboard counters, host load, DWARF process, retained artifacts, and selected external probes—not correlated node-level offered/admitted/included/adopted work. | `metrics-and-operating-program.md`; retained `metrics/summary.json` |

## Live demo and deterministic fallback

| ID | Presentation statement | Status | Mechanical evidence | Exact source |
|---|---|---|---|---|
| M01 | Demo candidate is `consensus-epoch-boundary-differential`. | VERIFIED | The live catalog scenario ran through the exact GUI backend three times against a fresh mixed topology; all three completed and passed. | scenario SHA-256 `7bda1027e1232aab80a6309ce5dce39072e381934d990b71700dd6058acc83c5`; three rehearsal runs |
| M02 | The current candidate is repeatable inside the seven-minute demo allocation. | VERIFIED | Three runs completed in 150.796 s, 150.807 s, and 150.811 s; median 150.807 s; each produced one passing `chain_select_differential` assertion and an HTTP-200 detail page. | runs `20260910T055940Z-1eb0692a`, `20260910T060441Z-d3a411bb`, `20260910T060723Z-08304f2f` |
| M03 | The candidate requests six tip observations and injects zero faults. | VERIFIED | Each rehearsal recorded 300 successful tip samples (50 × 6) and zero failed observations; 900 successful samples total. The scenario is a clean mixed-chain observation, not an adversarial campaign. | three retained observation summaries; candidate source |
| M04 | The candidate directly observes native Amaru tip selection. | CORRECTED / NO | It observes `amaru-consumer`, a Cardano-node fed through native Amaru relays. Native Amaru relays are registered for logs/connection context, but their tips are not directly queried. Demo guide states the inference explicitly. | `dwarf/scripts/adapt_cardano_amaru_runtime.py` |
| M05 | The candidate is technically ready on the current box. | VERIFIED, BOUNDED | Exact-name `p1/p2/p3/relay1/relay2/amaru-consumer/amaru-relay-*` containers were staged fresh; all remained running with zero restarts/OOM. Three exact-backend launches passed, and Amaru relay scans found no panic, fatal, or `EADDRINUSE`. Day-of topology health must still be rechecked. | fresh `docker ps`/`docker inspect`; rehearsal record, 2026-09-10 |
| M06 | Strong fallback run is independently inspectable. | VERIFIED | `/operate/runs/20260908T100054Z-80498541` returns 200; manifest and output retain seed, images, counts, cells, tips, and verdict inputs. | retained run `20260908T100054Z-80498541` |
| M07 | Fallback run: 895.624 s, Cardano 2,600, Amaru 910, 24/24 each, ten matching samples, block 288/slot 1613, recovery/convergence true. | VERIFIED | Values match `manifest.json` and `outputs/cardano-amaru-miniprotocol-security/result.json`; all six final hashes match. | same retained run |
| M08 | Fallback is unsuitable as the seven-minute live path. | VERIFIED | First classifiable sample appears at elapsed 711 s. | retained load event and `result.json` |

## Proposed program and security process

| ID | Presentation statement | Status | Mechanical evidence | Exact source |
|---|---|---|---|---|
| R01 | 20,000-block minimum and relative thresholds are accepted policy. | PROPOSAL | Every occurrence says proposed/provisional and requests stakeholder agreement. | `metrics-and-operating-program.md`; deck slides 9–10; handout |
| R02 | Amaru weekly and Cardano release-based cadence is active. | PROPOSAL | Presented as a requested operating cadence, not a current scheduler state. | same sources |
| R03 | Full protocol transcripts and bounded native instrumentation exist today. | PROPOSAL / NO | They are the future evidence layer. The presentation explicitly says node-level throughput is design-only. | `metrics-and-operating-program.md` |
| R04 | Root security process exists without publishing secrets. | VERIFIED | Root `SECURITY.md` defines private-first reporting, ownership, lifecycle, required record, and fixed-build verification; secret scan found no credential material in presentation artifacts. | `SECURITY.md`; `security-process-and-accountability.md` |

## Presentation artifact QA

| Artifact | Desktop | Mobile | Interaction/content status |
|---|---|---|---|
| Main deck | Rendered at 1440×900 | Rendered at 390×844 | 21 main slides + six appendices; no horizontal overflow. Keyboard/hash navigation, outline, notes, elapsed clock, full-screen, and print CSS retained. New authoring and proposal slides were visually inspected. |
| Product walkthrough guide | Rendered at 1440×900 | Rendered at 390×844 | No horizontal overflow. Start/install, all current Learn and Operate routes, safe authoring, demo handoff, preflight, fallbacks, cheatsheet, persistent checks/notes, and export present; tab and collapse behavior verified. |
| Live demo guide | Rendered | Rendered | Candidate gate, exact route, proof boundary, hard abort, fallback, and export present. |
| Capability / coverage / metrics / security objects | Rendered | Rendered | Interactive filters/tabs and evidence labels preserved. |
| Audience handout | Both evidence and program views rendered | Evidence view rendered | Tabs and print/save control present; print CSS exposes both views on separate pages. |

Fresh route checks returned HTTP 200 for every URL used in the walkthrough and
fallback. The workbench object list and each newly uploaded object were also
retrievable after upload.

## Remaining gates before the presentation can be called fully rehearsed

The technical demo gate is passed: fresh topology, real progress, the exact
Run-button backend, retained evidence, and the seven-minute budget were proved
three consecutive times. The remaining gates are human delivery gates:

1. Perform one physical browser-click rehearsal and confirm the visible route,
   button state, new run ID, and completed result page match the proven backend.
2. Rehearse the hard-abort timer and fallback route without live debugging.
3. Perform a full remote rehearsal of the four-part flow and record actual section
   times; completeness is the controlling requirement rather than a fixed duration.
4. Freeze exact HTML files, evidence references, hashes, and a deterministic
   offline copy only after the full rehearsal passes.

Current verdict: the live demo is **technically ready**. The complete
presentation is not yet delivery-rehearsed or frozen.
