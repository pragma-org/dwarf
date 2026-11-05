# DWARF Run-Surface Parity — Design

**Status:** DRAFT for approval (2026-11-04)

**Framing:** Authorized defensive conformance testing for PRAGMA. This design
is about where conformance scenarios can be run and how their measurement
output is ingested. It changes orchestration and evidence plumbing, not the
scenarios or the node implementations under test.

## Intent and why

DWARF runs conformance scenarios against cardano-node and Amaru and retains
their measurement output as portable evidence. Today three run surfaces
diverge:

- **Dashboard / local** (`run_service.py`, deployed on the box, documented on
  the moog workbench): resolves a `RunRequest` and executes the full
  measurement legs. This is the complete flow.
- **Antithesis** (`antithesis_package.py`, `bind_workload`, `bind_measurements`):
  shares the collector code but attaches a live, measured workload for exactly
  one scenario (mini-protocol security, mixed-control topology). Every other
  scenario is planning-only; taps the workload does not feed are recorded as
  implementation gaps. Its Pages archive carries the "few taps" coverage
  exception.
- **GitHub Actions** (`dwarf-security-campaign.yml` on hosted runners;
  `dwarf-devnet-smoke.yml` on a self-hosted runner that is not registered):
  the campaign runs a subset of local scenarios and a measurement suite and
  exports evidence; the smoke runs a curated devnet subset.

The user wants parity: the **full scenario catalog** addressable from **any**
surface (dashboard, Antithesis, GitHub Actions), with every run's output
**ingested into one shared output** (the portable run bundle, the evidence
set, the viewer, and the run ledger). GitHub Actions is **hosted only**; its
runners cannot run the heavy multi-node, epoch-warmup scenarios.

The point that bites now: results are not uniformly comparable or uniformly
reachable. A scenario you can run on the box cannot be driven from CI or
Antithesis without bespoke wiring, and when a surface cannot run something it
either fails, silently omits it, or is simply absent. The user wants one
catalog, one interface, one output, and an honest record of what each surface
actually did.

### The concrete workflow this must support (user, 2026-11-04)

The parity is in service of one developer-facing story:

1. An Amaru developer cuts a **new version**. They add it to DWARF (extending
   the existing `Versions → Prepare` flow and the version-provenance gate) and
   run **prebuilt or new scenarios locally** against it.
2. They then **select a version of cardano-node and a scenario and run it on
   Antithesis** — same catalog, different surface, a different version on the
   other axis.
3. Both runs' outputs **are added together and compared** in DWARF's own
   cross-implementation compare view, **on top of the standard report**, to
   surface actual divergences/bugs. Antithesis is simply one of the surfaces a
   run can come from (an alternative to local or CI), not the comparison engine.

So the axes a run is addressed by are not only (surface × scenario): they are
**(surface × implementation × version × scenario/topology/primitive/measurement
set)**. "Add a version, then run it anywhere, then compare any two outputs" is
the shape. DWARF already has the version-add flow (`Versions → Prepare`,
stock + instrumented images, provenance gate) and a cross-implementation
compare view; parity extends those across surfaces rather than reinventing them.

## Current state — validated by code sweep (2026-11-04)

A four-part read of the V10-FRESH worktree established what already exists, so
this design completes and consolidates rather than inventing.

**Built-in creators (web dashboard).** Six of eight artifact creators are
already real, no-agent, end-to-end:

- **Self-serve today:** scenario (`/operate/scenarios/new` + `scenario
  validate --semantic` + verify), target manifest (`operate_targets_new.py`,
  real manifest not a stub; dev supplies the binary), profile file
  (host-specific values hand-filled before deploy), corpus inputs
  (`operate_corpora.py`, upload seed bytes, live immediately), grammar tokens
  (`operate_grammars.py`, clone → edit → validated save), and measurement-tap
  *definitions*/profiles (`/operate/measurements/new`, full schema + semantic
  validation). All share the unified create/validate path
  (`catalog_definitions.py:save_definition` → `parse_and_validate_definition`).
- **The only two holes (definition self-serve, live implementation is code +
  image rebuild):** a new **primitive** — the dash only scaffolds a
  non-functional placeholder (`primitive_scaffold.py`); making it live needs a
  hand-written executor, a core edit to `profile_manager/primitives.py`, a
  registry merge and an image rebuild. A new **measurement collector** — the
  tap definition is self-serve but the collector code in
  `measurement_collectors/*.py` (bound in `amaru_factory.py`/`cardano_factory.py`)
  has no creator; a definition referencing an unregistered collector resolves to
  absent at run time. These two are the "an agent must step in" friction.

**Three run tracks (entry points).**

- **Local `/run`** — 6-stage wizard (Topology → Versions → Scenario → Taps →
  Prepare → Run), fully self-serve; `execute_run` only runs `backend="local"`.
  The reference track.
- **`/run-antithesis`** — explicitly a *draft builder*: "catalog presence is
  not remote support." Exactly **one** scenario is wired remotely
  (`cardano-amaru-miniprotocol-security-local`, mixed-control) and only **stock**
  taps bind; all else is planning-only. Build/preview/download are self-serve,
  but the tail — build image → push GHCR → **manually make public on GitHub** →
  submit to MOOG — needs host Docker/GHCR creds and a manual visibility toggle.
- **GitHub `testplease/`** — drop an **Amaru** source tree, push, and
  `dwarf-security-campaign.yml` builds and runs it sharded, automatically.
  Gaps: a changed **Cardano** source tree is recognised but **not built or run**
  (stock 11.1.2 only), and the dev **imports the result artifact into the viewer
  by hand**.

**Version/compile flow.** All three tracks share the same low-level build
scripts and patch manifests, but wrap them in **three separate orchestrators**
that have drifted:

- Local (`controller/release_builds.py`) compiles **both** stock and
  instrumented in one action — the reference.
- Antithesis (`antithesis_artifacts.py`) builds one mode per request;
  instrumented needs a manual `node-start` first or halts at
  `patched-build-not-implemented`; stock falls back to the plain upstream image.
- GitHub (`ci_amaru_stock_build.py`) builds Amaru stock automatically, patched
  only in `--full` scope, and **never compiles Cardano from source**.

**The shared seams that make consolidation cheap:** the `RunRequest`/`backend`
seam in `run_service.py`; the unified creator path in `catalog_definitions.py`;
the common builder scripts + patch manifests under `scripts/` and `targets/`;
the provenance gate in `version_provenance.py` (already shared by all tracks);
the portable bundle + `ci_stage_pages_campaigns.py` ingestion; and the existing
cross-implementation compare view.

## Goals

1. **One catalog, every surface.** Every scenario under `dwarf/scenarios` is
   addressable from the dashboard/local surface, the Antithesis surface, and
   the GitHub Actions surface.
2. **One run interface.** Extend the existing `RunRequest` / `backend` /
   `origin` seam in `run_service.py` so each surface is a backend (or
   origin), sharing one resolve path and one run-ledger contract.
3. **One output.** Every executed run, on any surface, emits the portable run
   bundle (`measurements/report.json` plus shared artifacts) and is ingested
   the same way: the evidence set (`docs/evidence`), the viewer, and the run
   ledger. Output is byte-comparable in schema across surfaces.
4. **Declared surface capability.** Each surface has a declared capability
   profile. A scenario the surface can run, runs. A scenario it cannot run
   records a documented **surface-capability gap** in the same output, modelled
   on `coverage-exceptions-v1.json`, so the catalog is uniformly addressable
   and nobody is misled about what a small runner executed.
5. **Version is a run axis, addable by a developer.** A run is addressed by
   (surface × implementation × version × scenario). A developer can add a new
   implementation version to DWARF (extending `Versions → Prepare` and the
   provenance gate) and then run prebuilt or new scenarios against it from any
   surface. The capability matrix and the run ledger carry the version, so
   "amaru vX on local" and "cardano vY on Antithesis" are distinct, comparable
   rows.
6. **Two outputs comparable.** Because every surface emits the same bundle
   schema, any two runs — whatever surface and version they came from — can be
   added and compared in DWARF's existing cross-implementation compare view, on
   top of each run's standard conformance-and-measurement report, to surface
   actual divergences/bugs. This reuses the established compare mechanism; no
   surface is a special comparison engine.

## Non-goals

- Raising hosted-runner resources, or adding nested virtualization. Hosted
  stays small.
- A dispatch/offload path from CI to the box or Antithesis. The user chose the
  declared-capability-gap model over offload; CI does not execute heavy
  scenarios by proxy.
- Reinventing collectors, the measurement report schema, the viewer, or the
  evidence-staging pipeline. Extend the existing primitives (HARD RULE:
  "Extend DWARF, never reinvent").
- Registering or standing up a self-hosted CI runner. The design assumes
  hosted-only and treats the self-hosted path as out of scope until a runner
  exists.

## Anti-goals (what makes this a failure even if it "works")

- A surface silently skipping a scenario it cannot run.
- A surface reporting success without producing the uniform bundle.
- Per-surface divergent output formats, so results cannot be compared.
- A surface-capability gap that hides a real measurement gap, or vice versa
  (the two gap kinds must stay distinct and both visible).

## Constraints (verbatim where fixed)

- **Extend, never reinvent.** Build on `RunRequest`/`ExternalRunRequest`,
  `coverage-exceptions-v1.json`, the portable bundle, `ci_stage_pages_campaigns`,
  and the viewer.
- **Seam already exists.** `run_service.py` defines
  `_BACKENDS = {"local", "antithesis", "moog", "external"}`, an `origin` axis
  (`cli`, `ci`, `external`, ...), `ExternalRunRequest.launch` for
  externally-executed runs, and a run-ledger contract that records
  `kind` and `backend`. Only `local` executes today; other backends raise
  "execution is not implemented by the local Run Service".
- **Catalog size.** 368 scenario files under `dwarf/scenarios` (many are CBOR
  decode/fuzz; others are multi-node devnet/consensus/mini-protocol).
- **Hosted-only CI.** Heavy multi-node, epoch-warmup scenarios cannot execute
  on GitHub-hosted runners.
- **Output contract.** The portable run bundle with `measurements/report.json`
  is the universal artifact; it is ingested into `docs/evidence/default-runs.json`
  and `runs/`, enforced by `ci_stage_pages_campaigns.py`, and read by the
  viewer. The Antithesis snapshot is already ingested as an archive with a
  `coverage_exception`.
- **Author/push policy.** Git author `JG <forky@1timeuse.com>`, no
  Anthropic attribution lines or trailers. Push public only when asked;
  internal only when asked.
- **Reply/record style.** Factual, conformance-and-measurement framing.

## The approach that was decided

1. **Surface-capability model.** A declared capability descriptor per surface
   (`local`/box, `antithesis`, `github-hosted`) stating the topology classes
   and resource tiers it can run (for example: single-node, mixed two-node,
   multi-node mesh, epoch-warmup-required). A scenario declares its
   requirements. A matcher decides, per (surface, scenario), **run** or
   **gap**. Shape and discipline mirror `coverage-exceptions-v1.json`
   (schema-versioned, validated, well-formed-tested).

2. **Surface-capability-gap record.** When a surface cannot run a scenario, the
   gap is recorded in a new catalog-level artifact (see Decision D1), named
   distinctly (`surface_capability`) so it never blurs with the existing
   measurement/adapter gaps (see Decision D3). The gap names the surface, the
   scenario, and the unmet requirement. The viewer and the ledger surface it.

3. **Unified output and ingestion.** Every surface that executes a scenario
   emits the portable bundle and is ingested through the one pipeline. Where a
   surface runs through an adapter rather than `run_service` directly (CI
   campaign, Antithesis), the adapter's contract is: produce the same bundle,
   or a surface-capability-gap record, and nothing else.

4. **One run interface over the existing seam.** Address every scenario through
   `RunRequest`/`ExternalRunRequest` with the surface as `backend`/`origin`.
   `local` keeps executing in-process. `antithesis` and `github-hosted` either
   execute through the seam or remain adapters that satisfy the output contract
   in (3); which of the two is a build decision (below), not an output
   difference.

## Left to the builder

- The exact schema of the surface-capability profile and the scenario
  requirement fields. Reuse scenario topology fields already present in the
  YAML where they exist; add the minimum new fields otherwise.
- The resource-tier taxonomy: a precise, testable definition of "heavy"
  (node count, epoch-warmup flag, memory/time envelope) that the matcher reads.
- Whether the CI campaign and the Antithesis package route through
  `run_service`'s backend seam, or remain adapters that emit the uniform bundle
  and gap records. The output contract is identical either way; pick the path
  that reuses the most existing code.
- How the viewer renders a surface-capability gap (reuse the coverage-exception
  rendering, with a distinct label).
- Test strategy per TDD: a well-formed-ness test for the capability catalog, a
  matcher truth-table test over representative scenarios per surface, an
  ingestion test that a gap record round-trips into the evidence set and the
  viewer, and a parity test that one scenario addressed through each surface
  yields the same bundle schema (or a gap).

## Track capabilities and invariants (what must not change)

The three tracks differ for real reasons — trust boundary, resources, delivery
context — not only drift. Any consolidation (sub-project B especially) preserves
these per-track invariants. The local and Antithesis blocks are defined when
their turn comes; the GitHub block is defined now, at the user's request.

### GitHub (hosted) track — declared capability

Seven workflows under `.github/workflows/`. The hosted track runs **bounded
work only**:

- **Definition validation** — `dwarf-validate.yml`: wallet-free, infra-free,
  every push/PR; proves scenario/bundle/measurement definitions well-formed.
  Executes nothing (10 min).
- **Library-tier fuzz / bug-finding** — `dwarf-fuzz.yml`: actually executes the
  Amaru CBOR / mini-protocol **decoder** fuzz over every registered decoder
  target, failing on panic/abort/hang. Bounded budget (500 inputs on PR, 20 000
  nightly, dispatch-configurable), 45 min, emits canonical run bundles + ledger.
  No devnet, no wallet, no Antithesis. This is real bug-finding, but library-tier
  only.
- **Stock measurement / conformance campaign** — `dwarf-security-campaign.yml`:
  a **selected, calibrated, small** scenario subset run against
  `node-measurements-v1.yaml`; triggered by a `testplease/` push or dispatch
  with `campaign_scope ∈ {stock-amaru(default), stock-cardano, stock-solo,
  full}`; `STOCK_ONLY` unless scope is `full`, and `full`/official selection is
  **review-gated** (real execution blocked until a reviewed selection pins
  releases/peers/artifacts). Candidate-build job ~85 min.
- **Build / publish** — `dwarf-amaru-builder.yml` (stock builder image, dispatch),
  `dwarf-release.yml` (release payload incl. scenario catalog), `pages.yml`
  (portable results viewer).

**What the hosted GitHub track CANNOT do (declared `surface_capability` gaps):**
heavy multi-node / devnet / consensus scenarios, epoch-warmup, wallet-requiring
flows, unbounded fuzz budgets, and anything requiring the paid Antithesis
service. Heavy stateful bug-finding is deliberately routed to the **self-hosted**
`dwarf-devnet-smoke.yml` runner (currently unregistered) — out of scope here.
Cardano-from-source build is currently **absent** (a gap sub-project B may close,
within the time/resource envelope).

**Invariants — must survive any consolidation:**

1. **Untrusted-source isolation.** Candidate source is checked out separately
   from the trusted runner tooling at a pinned `trusted_revision`, and candidate
   code is never executed at intake. A shared compile core must keep this split.
2. **Wallet-free / infra-free validation gate** stays exactly that — it must not
   grow a runtime dependency.
3. **Cost control via scope.** `STOCK_ONLY` by default and `full`/official
   selection review-gated are deliberate; patched/full builds stay opt-in.
4. **No dependency on the paid Antithesis service** from any hosted workflow.
5. **Artifact-export-then-import.** Hosted CI cannot write the box's evidence
   store; it uploads a bundle the operator imports. Auto-ingest (sub-project D)
   improves this flow without requiring hosted CI to reach into the box.
6. **Runner time/resource envelope** (10–90 min per job) is a hard ceiling; the
   capability matcher treats anything beyond it as a `surface_capability` gap.

**Near-term GH intent (user, 2026-11-04):** the GitHub track needs little
*extension*; the priority is to **exercise and validate it more** so its
existing capability is proven, not to grow it. (Cardano-from-source is the one
possible extension, deferred.) This is an E-pass concern, not a B/D build-out.

### Antithesis track — declared capability and invariants

Remote execution through MOOG. One scenario is wired end-to-end today:
`cardano-amaru-miniprotocol-security-local` on topology template `mixed-control-v1`
(two Cardano + two Amaru relays plus a control rig). It is **not** a plain local
run: it adds a configurator/seed bootstrap, six adversary/workload services
(`sm-cardano-*`, `sm-amaru-*`, `sm-workload`), a digest-pinned `sidecar` control
gate, "victim" clones of the relay targets, a build-required workload image, and
runtime-evidence gating (`antithesis_topology.py`, `antithesis_workload_binding.py`).

**Invariants — must not change:**

1. **SHA-256 fail-closed pinning.** The scenario's raw bytes, the 7 workload
   source files (Dockerfile/observer/entrypoint/3 tests), and the `sidecar`
   service digest are pinned; any drift silently emits an implementation-gap and
   attaches no workload (`antithesis_workload_binding.py:16-27,146-161`). The
   generated service inventory must equal baseline ∪ additions exactly.
2. **Single wired binding.** Workload attaches only to that one scenario on
   `mixed-control-v1`; everything else is planning-only until sub-project D
   wires it.
3. **Fault-exclusion labels** (`com.antithesis.exclude_from_faults =
   network,kill,pause,stop`) on control services (`antithesis_topology.py:30`).
4. **`linux/amd64` only**, with image identity (id/OS/arch) re-verified at every
   hop (build → tag → push).
5. **Single-attempt, persist-before-mutate push** and an `O_EXCL` submission
   claim + external-run reservation (`antithesis_publication.py:139`,
   `antithesis_submission.py:506-520`).
6. **Submission preconditions** (all external, DWARF only *reads* them): a
   non-shim MOOG channel with a real token; fresh (<600 s) `ready` readiness; a
   public GitHub commit whose blob hashes match the package; public GHCR images
   verified anonymously; requester wallet/registration/role/repo-whitelist facts
   all `ok`.
7. **No secrets in API rows, receipts, archives, or the command field** — secrets
   live only in private state (`antithesis_submission.py:531-534`).

**Not 1:1 with local / gaps sub-project D (and B) address:** the full control rig
vs just-nodes; `sm-workload` is build-required and the local probe orchestration
+ exit-code assertions are **not translated**; GHCR `docker login` is out of band
(assumed pre-existing on the host); **making GHCR packages public is 100% manual
(no API call exists)**; committing the export ZIP to a public repo is manual;
MOOG registration/funding/whitelisting are external prerequisites; `instrumented`
needs a pre-prepared patched artifact or it degrades to intent-only; and readiness
verifies *delivery only*, never compatibility or runtime success.

### Antithesis platform requirements (literal) — and how the wired scenario meets them

This is the part that was missing: the concrete things **Antithesis itself**
requires of a test package, taken from Antithesis's docs and the
`antithesishq/antithesis-skills` conventions, mapped to the one scenario that
truly works (`antithesis/cardano_amaru_miniprotocol_security/`). This is the
**template sub-project D generalizes** to the rest of the catalog. (It belongs
on a workbench too; see note below.)

**Platform requirements (canonical):**

1. **Config image.** A `config` image built `FROM scratch` with
   `COPY docker-compose.yaml /docker-compose.yaml` at the root; volume paths are
   relative to it; the default logging driver must not be changed (Antithesis
   reads it).
2. **`setup_complete` lifecycle signal.** Testing (fault injection) does not
   start until one process emits `setup_complete` — via the SDK or a single
   JSONL line `{"antithesis_setup":{"status":"complete",...}}` to
   `$ANTITHESIS_OUTPUT_DIR/sdk.jsonl`. Only the first one counts; **no container
   may exit non-zero before it**.
3. **Test composer.** An executable `singleton_driver_<name>.<ext>` (with a
   shebang) under `/opt/antithesis/test/v1/<name>/` in some container; runs once
   per timeline.
4. **Fault scoping via label.** `com.antithesis.exclude_from_faults` marks
   containers the platform must **not** fault; everything without it is
   faultable (the SUT).
5. **Hermetic.** No internet during a run; images must run with no external
   network.
6. **Image delivery.** Public images are referenced directly (pulled
   anonymously); custom images must be public or pushed to the Antithesis tenant
   registry. The SDK is `antithesis` (pinned `0.3.1` here).

**How the wired scenario satisfies them — the working pattern:**

- **Control vs victim split (the key mechanism).** Every *control* service
  carries `com.antithesis.exclude_from_faults: "network,kill,pause,stop"` (via
  the `*fault-exclude` anchor): the configurator, tracer stack, 3 producers
  `p1/p2/p3`, `relay1/relay2`, `amaru-relay-1/2`, `amaru-consumer`, `sidecar`,
  `log-tailer`. The **victims deliberately omit the label** —
  `sm-cardano-victim` and `sm-amaru-victim` are the two SUTs Antithesis is
  allowed to kill/pause/partition; `sm-cardano-seed` likewise.
- **Deterministic reference chain.** A protected control cluster produces a
  real chain (genesis start-times normalized by the configurator) that both
  victims sync from — so divergence is attributable to the SUT, not to setup.
- **Paired deterministic adversary.** `sm-cardano-fuzzer` and `sm-amaru-fuzzer`
  (image `dwarf-adversary`) drive identical state-machine fuzz at each victim
  with the **same seed** `0x20260907`.
- **Differential workload gates `setup_complete`.** `sm-workload` (image
  `dwarf-sm`, `/opt/antithesis/test/v1/mixed-miniprotocol-security/` baked in,
  SDK `antithesis==0.3.1`) compares the two fuzz transcripts and only writes
  `/status/prefault-ready` when the seed matches, coverage is complete, and both
  targets are reachable; the `sidecar` driver waits on that gate, then runs —
  i.e. faults begin only after a proven-identical pre-fault baseline.
- **Determinism everywhere.** All images pinned by `@sha256:` digest; internal
  bridge networks (`internal: ${INTERNAL_NETWORK}`).

**What generalizing to the catalog (sub-project D) therefore means:** for each
scenario, DWARF must emit this shape — config image + compose with the
control/victim fault-label split + a `/opt/antithesis/test/v1/<name>/` singleton
driver + an SDK workload that gates `setup_complete` and carries that scenario's
probes/assertions. The current binding hard-pins exactly one instance (SHA-256
fail-closed); D turns that into a parameterized generator while keeping the
invariants. This also explains the **GHCR-public** requirement: Antithesis pulls
the images anonymously, so they must be public (or in its tenant registry) —
which is what the PAT early-win automates.

> **Workbench note (user):** these literal Antithesis requirements and the
> working pattern should live on a workbench, and be reconciled against the
> `antithesishq/antithesis-skills` repo and `antithesis.com/docs` (authoritative
> for the config-image, `snouty validate`/`launch`, and SDK assertion specifics,
> which a hosted doc fetch only partly surfaced). Part of the E validation pass.

### Early win candidate — automate GHCR publish via a scoped PAT

The user's ask (add a `write:packages` PAT field to the Antithesis config and a
collapsible card in the `/run-antithesis` wizard with a save-vs-one-time
checkbox) is **feasible, MODERATE effort**, and self-contained — it does not
touch the compile core. Today there is no `docker login` in the push path and no
visibility API call; the config already carries a `github_pat` + a
`docker_config_path`. Two insertion points in `antithesis_publication.py::_push`:
`docker login ghcr.io --password-stdin` into a throwaway `DOCKER_CONFIG`, then a
new authenticated `PATCH .../packages/container/<name>` to set visibility public
(a new authenticated HTTP client — the existing anonymous `_get` strips auth on
redirect and must not be reused).

**Load-bearing requirements (hard constraints, from the security review):**

- A **separate, dedicated field** (`ghcr_publish_pat`), not an overload of
  `github_pat` — different scope (`write:packages`), highest blast radius, the
  first registry-*write* credential in this flow.
- **One-time use is the default**; "save" is an explicit opt-in. A saved PAT
  lands in `var/state/config.yaml` **unencrypted** (same as today's secrets); the
  dropdown copy must say so.
- **`--password-stdin` mandatory** (never on argv/logs); the PAT must never reach
  `status.json`/`receipt.json`, the package archive, the command field, or the
  publication logs; the visibility call must not log the `Authorization` header.
- Respects Antithesis invariant 7 above.

Files touched: `operate_config.py`, `moog.py` (field + secret list),
`antithesis_publication.py` (login + visibility), `data/antithesis_package.py`
(publication-action allowlist for a one-time `pat`), `_antithesis_settings.j2`
(auto-renders the saved field), `antithesis_run_wizard.j2` + `antithesis-publication.js`
(the dropdown). It can ship ahead of or inside sub-project D.

## Scope: independent sub-projects

The validated state shows this is **several independent projects**, not one.
Each should be planned and built on its own; each produces working, testable
software and is useful alone. The order is the user's call (see the open
question); the dependencies between them are noted.

- **A. Capability model + surface-capability gaps + surfacing.** The honest
  (surface × implementation × version × scenario) capability matrix, one
  matcher (D1/D2), the `surface_capability` gap record (D3), its validation,
  ingestion, and viewer rendering. Foundation: makes the divergence the sweeps
  found visible and honest before any plumbing changes. Deliverable alone.
- **B. One compile service across the three tracks.** Consolidate the three
  drifted orchestrators (`controller/release_builds.py`,
  `antithesis_artifacts.py`, `ci_amaru_stock_build.py`) behind **one shared
  compile path**: fetch a version → build **both stock and patched** → pass the
  shared provenance gate. They already share the builder scripts and patch
  manifests, so this is consolidation, not new compilers. Closes: Antithesis
  instrumented auto-build and DWARF-compiled stock; GitHub Cardano-from-source
  and patched-on-normal-push. This is the heart of "compile stock + patched on
  whichever track the dev chose."
  **B.1 — REQUIRED: auto-port measurement patches to new revisions (user,
  2026-11-05).** Today `_recipe()` in `controller/release_builds.py` builds
  both variants in one job, but `instrumented` raises
  `BuildBlocked('No instrumentation patch for this exact source revision')`
  whenever no hand-maintained manifest exists under
  `dwarf/targets/<impl>/measurement-patches*/<revision>/manifest.json`. So a
  genuinely new upstream version gets stock only. The user's requirement is that
  a new version is patched **automatically**. Specified behavior:

  0. **UX unchanged (user, 2026-11-05):** builds are still started only by the
     existing per-release **"Download & build"** control on the Versions page
     (`versions.j2` → `/api/versions/prepare`). B.1 adds **no** auto-build on
     fetch/discovery and no new button; it changes only what the `instrumented`
     leg does after the dev clicks.
  1. **Trigger (inside that click):** no exact-revision manifest for the
     `instrumented` variant.
  2. **Port attempt:** pick the newest committed patch series for that
     implementation (max `recipe_rank` over all `measurement-patches*/*/
     manifest.json`), and attempt to apply its patches to the new source
     revision (3-way / `--check` first).
  3. **Verify, never assume:** build instrumented, then run the existing
     `_instrumentation_check` (compiled markers present). The shared provenance
     gate (label == binary `git_commit`) still applies.
  4. **On success:** register the build with a **derived manifest** marked
     `auto_ported: true`, recording the base recipe identity
     (`patch_set_sha256`, base revision), the new revision, and per-patch apply
     results. The UI/report mode label is visibly provisional, e.g.
     `patched (auto-ported from <base-revision>)` — never presented as a
     hand-verified patch set. Persist the derived manifest so re-runs are
     reproducible, and offer **"promote to committed manifest"** (human review)
     — that is how the hand-maintained set grows.
  4b. **Runtime proof promotes the recipe automatically (user, 2026-11-05).**
     Compiled markers prove the patches compiled in, not that the taps emit
     data. So the derived recipe starts as `auto-ported-candidate` and becomes
     **`auto-ported-proven`** without human action when a completed run that
     used that build with its patched measurement profile passes the existing
     tap coverage rule for the patched-only concepts (each bound patched
     concept carries a value or a documented exception). The promotion is
     recorded on the derived manifest (`validation.status`,
     `validation.runtime_qualified = true`, `validation.proven_by_run =
     <run_id>`) through the existing post-run compatibility-decision path, and
     the UI label changes to `patched (auto-ported from <base>) · proven by run
     <id>`. "Export for review" stays as the way to commit it into the
     repository, now exporting a proven recipe; a candidate that never proves
     stays visibly provisional.
  5. **On failure** (patches do not apply, or markers missing): honest
     `BuildBlocked` with the concrete reason, plus a declared gap record; the
     stock build is unaffected.
  6. **Every track** gets this through the shared compile core (B), not three
     copies.

  Touches: `release_builds.py::_recipe` + `_instrumentation_check`,
  `scripts/build_{amaru,cardano}_measurement_target.py` (apply logic),
  `release_preparations._register`, `version_provenance.py`, the Versions UI
  mode label. Tests: recipe-fallback selection; apply-success → derived
  manifest + markers; apply-failure → `BuildBlocked` + gap with stock intact;
  provenance gate on an auto-ported build. Effort: moderate.

- **C. Creator completeness — no agent needed.** Close the only two self-service
  holes the sweep found: a new **primitive** and a new **measurement collector**
  must become creatable to a *runnable* state from the dashboard (or the
  first-time code+rebuild friction removed), not a scaffold that needs a core
  edit. The other six creators are already end-to-end; this finishes "built-in
  support for everything." Largely independent of A/B/D.
- **D. Run-track parity + standardized auto-ingest.** Antithesis: wire the full
  scenario/tap catalog (not one scenario, not stock-only taps) and make the
  build → publish → submit tail self-serve. `testplease/`: build Cardano
  candidates like Amaru and auto-ingest results into the viewer. Every surface
  emits the one portable bundle through the `RunRequest`/`ExternalRunRequest`
  seam into one output (ledger + evidence + compare), so "pick surface,
  implementation, version, scenario" is one request shape. Consumes A's gap
  record and B's compile service.
- **E. End-to-end validation pass.** The user's "validate end to end" ask:
  confirm the streamlined flow actually works across local / GitHub / Antithesis
  for conformance, measurement, and bug-finding, and reconcile the docs,
  workbenches, Serena and codegraph (likely stale) against the real code. This
  can run first as an audit that *produces* A's matrix as its artifact, or last
  as the acceptance gate — the user chooses.

Recommended order (for the user to confirm or change): **A** or **E-as-audit**
first (the honest map), then **B** (compile is the biggest functional gap and
unblocks D), then **D** (track parity), then **C** (last agent-friction), with
**E-as-gate** closing it. Each is brainstormed/planned on its own when its turn
comes; this design is the umbrella, not a single plan.

## Decisions from the builder-check (2026-11-04)

A fresh reviewer read this design as a builder and raised five questions. Four
are settled here as design decisions; the fifth (public publication scope) is
for the user.

- **D1 — Where a gap lives.** Gaps are not shoehorned into
  `docs/evidence/default-runs.json` rows, which are executed-run bundles with a
  `report.json`, size and sha256. A surface-capability gap is a scenario that
  was *not* run, so it has no bundle. It lives in a **new, schema-versioned
  catalog-level artifact**: a (surface × scenario) capability matrix committed
  under `dwarf/measurements/` (shape and discipline like
  `coverage-exceptions-v1.json`). The viewer learns to read this matrix.
  `default-runs.json` is unchanged in meaning.

- **D2 — Static matrix and runtime verdict share one matcher.** There is one
  matcher function. Sub-project A ships it as a standalone tool that produces
  the committed static matrix over the catalog (deliverable alone, the honest
  offline record). Sub-projects B and C call the **same** matcher live at run
  time, so a surface emits an authoritative per-attempt gap when it actually
  declines to run. The static matrix is the offline projection; the runtime
  record is authoritative.

- **D3 — Two gap taxonomies, split by cause.**
  - `surface_capability` gap = the surface *physically cannot* run the scenario
    (resources or topology), even when DWARF is fully built. Declared, stable.
  - `implementation-gap` (the existing Antithesis/collector issues) = DWARF has
    not wired the adapter, collector or workload yet. Fixable by building.
  Consequence: Antithesis's current ~367 planning-only scenarios are
  **implementation-gaps** that sub-project B closes, **not** surface-capability
  gaps. `surface_capability` is reserved for true physical limits (for example,
  hosted CI versus a multi-node mesh). This keeps the anti-goal's "never blur"
  honest.

- **D4 — "One output" is one contract, not one capped file.**
  `ci_stage_pages_campaigns.py` caps the default set (10 archives, ~950 MiB)
  and coverage-gates every entry, so the full 368-scenario catalog cannot all
  become `default-runs.json` rows. `default-runs.json` therefore stays the
  **curated, capped, coverage-gated showcase**. The full-catalog parity record
  lives in the **run ledger plus the capability matrix** (D1). "Ingested into
  the same output" means one bundle/ledger/matrix contract, uniform across
  surfaces, not one capped file.

- **D5 — Local is a surface with a profile; the catalog is the registry.**
  The local/box surface is *not* unconstrained: some scenarios carry
  `promotion_blockers` the box itself cannot meet (for example a 100-node
  mesh), so local has its own capability profile and emits surface-capability
  gaps like any other surface. "Full catalog" means the active set the existing
  scenario/primitive **registry** recognises (reuse it; do not hand-filter a
  parity list). `promotion_blockers` map naturally to surface-capability gaps
  on every surface until unblocked. The resource-tier taxonomy (left to the
  builder) reuses existing YAML signals — `runtime` class, `substrate.nodes`,
  `substrate.compose_mode`, `host_strategy`, `promotion_blockers` — and adds a
  minimal explicit resource-envelope field only where inference is insufficient,
  rather than backfilling all 368 files.

- **Decided, not asked (the reviewer's lower-priority items):** whether
  `github-hosted` is a new `_BACKENDS` value or `backend=local`+`origin=ci`, and
  whether CI/Antithesis route through the seam or stay adapters, remain build
  decisions per "pick the path that reuses the most code."

## Open questions resolved during brainstorming

- Parity bar: run the same scenarios from any surface, output ingested into one
  place. (User, 2026-11-04.)
- Reach: full scenario catalog. (User, 2026-11-04.)
- CI runners: hosted only. (User, 2026-11-04.)
- Heavy scenarios on CI: declared capability gap, not offload, not out-of-scope.
  (User, 2026-11-04.)
- Capability-matrix publication: build the matrix and the viewer's ability to
  show it; whether it appears on the public Pages site is the normal push
  decision, made later at publish time. The build is not blocked on it, and the
  viewer work targets a view that can be shown publicly or on the dashboard.
  (User, 2026-11-04.)
