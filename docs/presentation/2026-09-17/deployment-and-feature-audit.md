# DWARF deployment and feature audit

Audit date: 2026-09-14

Purpose: establish exactly what the September 17 presentation may claim about
the deployed product and its operator workflow. This is a presentation evidence
source, not a substitute for individual run evidence.

## Bottom line

The current `dwarf-host-a` dashboard is live and the full implemented Learn and
Operate route set renders successfully. The strongest product claim is that
DWARF can select and execute real-node scenarios, stream the action through the
dashboard, retain run evidence, inspect/export bundles, compare implementations,
and submit a validated scenario through the Moog request path. Those paths have
runtime evidence.

Creation/scaffolding, scheduling, notifications, external plugin loading, crash
aggregation, and static-analysis aggregation do not all have equivalent recent
operator-driven runtime proof. Catalog browse/detail/export and the structured
definition builders have focused tests and live route/visual proof; that still
does not turn a definition, plugin, or derived view into execution evidence.

The deployed revision must be read from `/learn/status` and the running image's
OCI label at rehearsal time. Do not copy a revision or catalog count from this
document into the deck: the delivery build embeds the exact source revision and
the dashboard derives inventory from the mounted source catalogs.

## Proof vocabulary

| State | Meaning |
|---|---|
| Proven end-to-end | The operator action has current real execution evidence and a retained result or externally verifiable state transition. |
| Working, not recently proven | The live page renders and the implementation/test contract exists, but this audit did not perform a fresh destructive or state-changing workflow. |
| Display/aggregation | The page correctly derives or displays available state; it does not itself prove the underlying security behavior. |
| Incomplete | A useful path exists but a required external completion, populated data source, or final proof is absent. |
| Unavailable | The route or capability does not exist in the deployed product. |

## Exact deployed system

| Item | Verified value | Evidence |
|---|---|---|
| Host | `dwarf-host-a`, Linux `6.8.0-138-generic`, x86_64 | `uname -srmo` on 2026-09-10 |
| Processor | AMD Ryzen 7 6800U, 8 cores / 16 threads, 16 MiB L3 | `lscpu` |
| Memory | 27 GiB RAM, 8 GiB swap | `free -h` |
| Storage | 915 GiB root filesystem, 473 GiB available at audit | `df -h /` |
| Docker | client/server `29.6.0` | `docker version` |
| Dashboard container | `dwarf-fw`, restart policy `unless-stopped` | `docker inspect` |
| Dashboard port | `0.0.0.0:8787 -> 8787/tcp` | `docker inspect`, `ss -ltn` |
| Deployed image | `dwarf/framework:internal-<revision>` | read from live container inspection at rehearsal |
| Deployed image ID | content-addressed SHA-256 | read from `docker inspect` at rehearsal |
| Deployed source revision | full Git revision | `/learn/status` and OCI image label must agree |
| Local registry | `registry:2` on port `5000` | `docker ps`, `ss -ltn` |
| Runtime root | `~/.local/share/dwarf` | deployment configuration and mounts |
| Runs | 98 retained run directories, 366 MiB | live filesystem snapshot on 2026-09-14 |
| Exported bundles | 12 `.tar.gz` bundles, 3.1 MiB | live filesystem snapshot; excludes retention probe file |
| State | 3.1 MiB | live filesystem snapshot |
| Health endpoint | `/healthz` returned `status: ok` | live HTTP request |
| Metrics endpoint | `/metrics` returned Prometheus exposition | live HTTP request |

Persistent mounts are:

- `~/.local/share/dwarf/runs` to `/var/dwarf/runs`
- `~/.local/share/dwarf/bundles` to `/var/dwarf/bundles`
- `~/.local/share/dwarf/state` to `/var/dwarf/state`
- a read-only deploy key and `known_hosts` from the runtime state directory

The host also contains old mixed-network test containers. They are not part of
the `dwarf-fw` dashboard service and should be cleaned or deliberately retained
before the demo; their presence must not be represented as the current dashboard
substrate. At audit time the status page correctly reported no actively composed
substrate.

## Repository and image relationship

The running image proves only the revision embedded in that image. Before the
walkthrough, compare the full OCI revision, local `HEAD`, and the intended remote
branch; record the values in rehearsal notes rather than freezing them here.
Source-only catalog exports repeat that revision in
`DWARF-EXPORT-MANIFEST.json`, alongside reproducible timestamps, object IDs,
portable paths, sizes, and SHA-256 hashes.

## Operator workflow audit

| Workflow | Current status | Exact source/evidence | Presentation treatment |
|---|---|---|---|
| Install package | Working, not recently clean-room proven | `delivery/scripts/install.sh`, current installed service | Explain the one-command path; do not claim a fresh public-clone install was performed during this audit. |
| Build image | Proven on deployed internal revision | OCI image, build script, image label | Show the pinned revision and image ID. |
| Deploy dashboard | Proven end-to-end | Running `dwarf-fw`, `/healthz`, port 8787 | Safe main-deck claim. |
| Configure runtime | Working, not recently proven | `/operate/config`, `/operate/config/edit`, saved config present | Show masked form/read-only resolution; never expose secrets. |
| Register target | Working, not recently proven | `/operate/targets/new`, schema-backed POST handler | Show catalog only in main walkthrough. |
| Create/edit scenario | Working, not recently proven | `/operate/scenarios/new`, `/operate/scenarios/<id>/edit`, validation/save handlers | Explain capability; do not mutate the catalog live. |
| Create profile | Working, not recently proven | `/operate/profiles/new`, schema-backed POST handler | Explain reusable configuration; do not create live. |
| Scaffold primitive | Working, not recently proven | `/operate/primitives/new`, POST handler | Explain that the scaffold still requires implementation, review, and rebuild. |
| Inspect/export reusable assets | Proven read-only end-to-end | primitive, template, testcase/bucket, corpus, grammar, risk-package, and plugin catalog routes plus deterministic exports | Safe to show; inventory and relationships are not runtime proof. |
| Run scenario through GUI | Proven end-to-end, backend bounded | exact Run-button endpoint plus three retained mixed real-node rehearsals | Physical browser click remains a human/day-of rehearsal step. |
| Monitor run | Proven end-to-end | `/operate/runs/<id>/live`, log tail SSE, retained run state | Show briefly. |
| Inspect/export evidence | Proven end-to-end | 98 retained runs, 12 exported archives, run inspector/output endpoints at audit | Core walkthrough proof. |
| Compare implementations | Proven end-to-end, narrow evidence set | `/operate/compare`, one retained comparison shown by dashboard | Present as supported with limited current retained examples. |
| Schedule runs | Working, not recently proven | create/pause/resume/run-now/delete handlers; scheduler thread | Current schedule is empty; do not imply recurring execution is active. |
| Antithesis build/validate/preflight/submit | Proven through Moog request submission; final campaign result separate | `/operate/antithesis`, live on-chain submission receipt and run ID | Show workflow and receipt only; do not equate submission with a successful completed campaign. |
| Notifications | Working, not recently proven | `/operate/notifications`, notification configuration code | Appendix only until delivery is exercised. |
| Plugins | Working registry/discovery; external plugin flow not proven | `/operate/plugins`, plugin loader and authoring guide | Appendix only. |
| Coverage/crash/timeline/static analysis | Display/aggregation | corresponding Operate pages and retained artifacts | Use as evidence navigation, not as independent coverage proof. |

## Operate route inventory

The public exact-image route audit on 2026-09-14 covered 74 routes with 148
desktop/mobile checks and no failures. Dynamic detail rows used source-backed
objects; absence of a row was not converted into a fabricated success.

| Route | Operator action | Classification | Caveat |
|---|---|---|---|
| `/operate` | See current run, bundle, comparison, catalog, and tool summary | Display/aggregation | Counts are inventory, not security coverage. |
| `/operate/status` | Inspect dashboard, substrate, profile, Moog, and wallet health | Working, not recently proven | Substrate is currently idle; Moog oracle is disabled/inactive. |
| `/operate/targets` | Browse registered targets | Display/aggregation | Catalog presence does not mean a target is runtime-proven. |
| `/operate/targets/new` | Register a decode target | Working, not recently proven | Do not create during presentation. |
| `/operate/scenarios` | Browse, validate, run, and access scenario actions | Proven end-to-end | The source-derived count is definitions, not proven tests. |
| `/operate/scenarios/new` | Create from a template | Working, not recently proven | Persistence path is implemented; not freshly exercised here. |
| `/operate/scenarios/<id>/edit` | Validate and save an existing scenario | Working, not recently proven | Dynamic route; use only with a rehearsed scenario. |
| `/operate/profiles` | Browse reusable profiles | Display/aggregation | Profile existence does not prove its target is currently available. |
| `/operate/profiles/new` | Create a deployment profile | Working, not recently proven | No live creation during audit. |
| `/operate/primitives` and `/<id>` | Inspect registry, schema, executor provenance, references, and exact source | Proven read-only end-to-end | Registry presence is not execution evidence. |
| `/operate/primitives/new` | Scaffold a primitive | Working, not recently proven | Generated files still require implementation, review, and rebuild. |
| `/operate/profile-templates` and `/<id>` | Inspect, download, or export immutable topology scaffolds | Proven read-only end-to-end | Clone into a profile before deployment. |
| `/operate/testcases` and `/<id>` | Inspect and operate on lifecycle records | Proven implementation and route contract | Mutable triage metadata points to immutable retained artifacts. |
| `/operate/testcase-buckets` and `/<id>` | Inspect derived testcase groups | Proven read-only end-to-end | Buckets are derived; no independent bucket file exists. |
| `/operate/corpora` and `/<id>` | Inspect/export exact bytes; manage bounded runtime overlay | Proven catalog/export; mutation contract tested | Shipped corpora remain immutable. |
| `/operate/grammars` and `/<id>` | Inspect/export structure/token pairs; clone/edit runtime copies | Proven catalog/export; mutation contract tested | A grammar guides generation but does not run a workload. |
| `/operate/risk-packages` and `/<id>` | Inspect/export planning packages and resolved evidence | Proven read-only end-to-end | Not a run bundle, confirmed finding, or accepted risk. |
| `/operate/runs` | Search/filter retained runs | Proven end-to-end | Run pass/fail is framework outcome, not node-bug count. |
| `/operate/runs/<id>` | Inspect manifest, assertions, logs, probes, outputs | Proven end-to-end | Use exact evidence references. |
| `/operate/runs/<id>/live` | Stream an in-progress run | Proven end-to-end | Demo must have a pre-proven fallback. |
| `/operate/schedule` | Create and manage cron entries | Working, not recently proven | No scheduled entries currently exist. |
| `/operate/antithesis` | Build, validate, preflight, submit, poll | Proven through submission | Paid/live launch requires explicit approval; completion is separate evidence. |
| `/operate/bundles` | Browse, import, and export evidence bundles | Proven end-to-end | Twelve actual archives at audit; the hidden retention probe is not a bundle. |
| `/operate/compare` | Run/read cross-implementation comparisons | Proven end-to-end, narrow | One retained comparison currently displayed. |
| `/operate/coverage` | Aggregate coverage artifacts across bundles | Display/aggregation | Must not be converted into a coverage percentage without a denominator. |
| `/operate/audit` | Retro-classify retained runs | Working, not recently proven | Classification does not change what a run actually exercised. |
| `/operate/crashes` | Group candidate crashes by signature | Display/aggregation | Empty/low-volume states do not prove crash absence. |
| `/operate/timeline` | Correlate events across bundles | Display/aggregation | Limited to emitted/retained events. |
| `/operate/static-analysis` | Show latest clippy/audit/deny artifacts | Display/aggregation | Not a substitute for current upstream CI. |
| `/operate/contract` | Show delivery/contract progress | Display/aggregation | Project-management view, not runtime evidence. |
| `/operate/plugins` and `/<id>` | Statically inspect/export plugin declarations | Proven non-executing inventory/export | Runtime loading is not sandboxed; no plugin marketplace. |
| `/operate/config` | Inspect resolved config and open editor | Working, not recently proven | Secrets are masked; do not screen-share private values. |
| `/operate/notifications` | Configure/view event rules | Working, not recently proven | No fresh webhook/email/Slack delivery proof. |

## Learn route inventory

All Learn routes below rendered HTTP 200. “Rendered” means navigable product
documentation, not an independent proof that every documented operation was
freshly executed.

| Route | Content | Accuracy/use decision |
|---|---|---|
| `/learn` | Documentation landing | Use as walkthrough index. |
| `/learn/getting-started` | Install-to-first-run path | Use after stale count/path language is reconciled with current deployment. |
| `/learn/overview` | Product overview | Use for product boundary, not test counts. |
| `/learn/concepts` | Scenario/profile/target/primitive concepts | Use to explain vocabulary. |
| `/learn/architecture` | Controller, runtime, evidence architecture | Use for the real-node execution boundary. |
| `/learn/consensus` | Consensus model/context | Appendix; do not imply all described properties are covered. |
| `/learn/coverage` | Generated catalog and artifact matrices | Label as inventory/declared coverage, not proof. |
| `/learn/threat-coverage` | Risk/threat-to-scenario mapping | Label listed scenarios as candidates until a valid run is linked. |
| `/learn/status` | Documentation/status map | Supporting navigation. |
| `/learn/examples` | Source-backed scenario examples plus one bounded example for every reusable asset catalog | Definitions and relationships only; not execution proof. |
| `/learn/primitives` | Registry, schema, executor, compatibility, and authoring contract | Use with the Operate primitive catalog. |
| `/learn/profile-templates` | Template purpose, schema, source, immutability, and clone lifecycle | Distinguish templates from deployed profiles. |
| `/learn/testcases` | Testcase/bucket lifecycle, provenance, replay, minimization, compare, and promotion | Distinguish lifecycle state from findings. |
| `/learn/corpora` | Exact-byte corpus roles, provenance, and runtime-overlay boundary | Shipped bytes remain immutable. |
| `/learn/grammars` | Structure/token semantics, validation, clone, and edit boundary | Grammars guide generation; they do not execute tests. |
| `/learn/risk-packages` | Planning-package schema, evidence references, blockers, and status boundaries | Do not call these evidence bundles or findings. |
| `/learn/api` | Dashboard/API reference | Appendix/operator handoff. |
| `/learn/cli` | CLI reference | Use as GUI-equivalent fallback. |
| `/learn/glossary` | Terms | Appendix. |
| `/learn/faq` | Common questions | Appendix. |
| `/learn/troubleshooting` | Failure handling | Demo fallback source. |
| `/learn/operator-runbook` | Operations and evidence flow | Use after exact demo path rehearsal. |
| `/learn/developer-onboarding` | Repository extension path | Appendix. |
| `/learn/plugin-authoring` | Plugin contract and security model | Appendix; plugin execution is unsandboxed. |
| `/learn/walkthroughs` | First run, bundle, fuzz target, comparison | Useful starting point, but current first-run narration is CLI-centric rather than the exact GUI demo. |

## Catalog and telemetry facts allowed in the deck

- Scenario, profile, target, primitive, profile-template, testcase/bucket,
  corpus, grammar, risk-package, and plugin counts are source-derived by the
  live dashboard. Capture them immediately before the presentation; do not use
  this audit as a second inventory.
- The primitive family breakdown is source-derived from
  `dwarf/primitives/registry.json`; its totals describe available operations,
  not how many were exercised by a particular run.
- The 2026-09-14 snapshot contains 98 retained run directories: 62 framework
  pass, 31 framework fail, and 5 framework error.
- Retained assertions total 66 pass and 32 fail in that snapshot.
- `/metrics` exposes dashboard request counts, retained run/assertion counts,
  bundle bytes/count, substrate-compose counts, and dashboard uptime.
- The separate historical census proves 1,945 traceable executions: 1,893
  manifest-backed local runs, 13 workbench-only tier-1 live-confirmed runs, and
  39 DWARF-owned Antithesis runs. The presentation rounds this to 1,900+.
- The same census found 576 distinct scenario IDs and 11 archives satisfying
  the portable evidence bundle contract at census time. The live bundle
  directory's 12 archives are a newer current-state set and must be validated
  individually before being added to that historical contract count.

None of those counts may be described as “bugs found,” “tests passed,” or
“threats covered” without the evidence ledger. Scenario and target catalog
counts especially overstate real-world proof if presented alone.

## Presentation checks that remain live-state dependent

1. A fresh exact-name mixed topology was staged and the candidate passed three
   consecutive technical rehearsals. Recheck topology health immediately before
   the demo; the status surface may still report the separately managed
   substrate as idle because this topology is externally composed.
2. The dashboard implements no `HEAD` handler, so automated HEAD checks return
   501 before tools retry with GET. Normal GET navigation is healthy; this is not
   a demo blocker.

## Recommended main walkthrough subset

Use only the surfaces with a clear proof role:

1. `/operate` — current deployment and inventory.
2. `/learn/getting-started` or `/learn/walkthroughs` — operator entry path.
3. `/operate/status` — real deployment boundary and current state.
4. `/operate/scenarios` — select the rehearsed scenario.
5. `/operate/runs` and one exact `/operate/runs/<id>` — retained evidence.
6. `/operate/bundles` or one output download — portable evidence.
7. `/operate/antithesis` only as a short pipeline view; never launch paid work in
   the presentation.

Profiles, targets, coverage, comparison, and threat coverage belong in the
guided walkthrough or appendix only if time remains. Creation forms,
notifications, plugins, scheduling, and static-analysis pages should not consume
main-path time.

## Source references

- `INSTALL.md`
- `OPERATIONS.md`
- `delivery/docker-compose.dwarf.yml`
- `delivery/scripts/status.sh`
- `dwarf/profile_manager/dashboard.py`
- `dwarf/profile_manager/data/*`
- `dwarf/profile_manager/views/*`
- `dwarf/dashboard/templates/learn/*`
- `dwarf/dashboard/templates/operate/*`
- `/var/dwarf/runs/*/manifest.json` inside the deployed container
- `/healthz`, `/api/status`, and `/metrics` on the deployed dashboard
