# Antithesis Track Parity — Design

**Status:** DRAFT for approval (2026-11-05). Sub-projects **B (Antithesis
leg)** and **D (Antithesis leg)** of
`docs/plans/2026-11-04-run-surface-parity-design.md`, plus the GHCR
early-win from that document. Everything here was checked against the code at
`main` `132a6dbd` by three read-only sweeps on 2026-11-05; file:line
citations refer to that commit.

## Intent and why

**User statement (2026-11-05):** "antithesis track is done when its as much on
par with the local track as possible, aka to what antithesis supports."

The local track is the reference: on `/run` a dev picks an implementation and
version, has stock **and** patched builds from one `Versions → Download &
build` click (auto-ported when no hand-written recipe exists, proven by a
real run), picks any catalog scenario, runs it, and gets one output: a run
ledger entry, a portable evidence bundle, and a row in the compare view.

Today `/run/antithesis` is a draft builder. Exactly one scenario
(`cardano-amaru-miniprotocol-security-local` on `mixed-control-v1`) reaches
submission; it needs a second, separate node compile with its own rules and
no auto-port; the tail stops twice for manual GitHub clicks; and no run ever
gets a verdict because nothing retrieves results (`antithesis_submission.py:
571-585` keeps only phase/outcome; `run_service.py:884-885` reconciles to
`inconclusive`).

"Where it needs to be" = the same dev workflow with the same output, and an
honest, visible declared gap wherever Antithesis cannot do what the local
runner does. The user confirmed the four pieces and their order
(2026-11-05): compile parity → catalog parity → self-serve tail → results
back. They also said yes to Antithesis coverage instrumentation of the node
binaries as a build option; its scope is bounded below by what the platform
documents.

## Goals

1. **One compile.** The Antithesis wizard consumes the same prepared releases
   as the local wizard (stock + patched, auto-ported when needed, proven by
   runs). It never compiles a node itself.
2. **Any catalog scenario.** Every scenario under `dwarf/scenarios` can be
   emitted as a valid Antithesis package. Scenarios whose primitives have no
   Antithesis translation are emitted with declared gaps, not refused and not
   silently truncated.
3. **Self-serve tail.** From "package built" to "submitted" without leaving
   the wizard: GHCR login, push, make-public, public commit, readiness,
   submit. External prerequisites that DWARF can only read (MOOG
   registration, wallet funding, whitelist) stay external and are shown as
   such.
4. **One output.** A finished Antithesis run lands in the run ledger with a
   real verdict, a run directory with the same bundle layout as a local run
   (minus what Antithesis cannot export, declared), and a row in the compare
   view keyed by the same concepts.
5. **Node coverage instrumentation as a build variant** for Amaru (documented
   Rust path). cardano-node coverage is a bounded spike (see "Left to the
   builder" and "Decisions for the user").

## Non-goals

- Replacing the hand-built mixed-rig package. It stays as the curated path
  with all its pins; the generated path is additive.
- Translating DWARF fault primitives into Antithesis faults. Antithesis owns
  faults; DWARF declares the delegation.
- A dispatch path from GitHub Actions to Antithesis (the user chose no hosted
  dependency on the paid service; parity doc §"GitHub track").
- Retrieving the Antithesis `/evidence` volume. The platform offers no bulk
  artifact export; measurements that only exist in that volume are a
  declared gap unless they can be streamed through the SDK event channel.
- Changing MOOG itself.

## Anti-goals (what makes this a failure even if it "works")

- A package that passes `snouty`-style validation but whose workload gates
  `setup_complete` on nothing real, so faults start against an unready SUT.
- A "pass" verdict derived from "all commands completed" (spec.md §7
  explicitly warns: an assertion can fail while its command returns 0).
- A second copy of the compile logic, the recipe lookup, or the provenance
  gate living under `antithesis_*`.
- A secret (GHCR PAT, MOOG PAT, wallet) reaching `status.json`,
  `receipt.json`, the package, the command field, or a log.
- Presenting an auto-ported or coverage-instrumented build as hand-verified.
- Breaking any of the seven Antithesis-track invariants in the parity doc
  (SHA-256 fail-closed pinning of the curated package, single wired binding
  for that package, fault-exclusion labels, linux/amd64, single-attempt push
  + `O_EXCL` claim, external preconditions read-only, no secrets in rows).

## Constraints (verbatim where fixed)

- **Extend, never reinvent.** Build on `release_preparations`, the operator
  ledger, `antithesis_package.preview_package`, `antithesis_topology`,
  `antithesis_workload_binding`, `antithesis_measurements`,
  `antithesis_publication`, `antithesis_submission`, `run_service`
  (`ExternalRunRequest`, `reserve_external_run`, `_external_result_verdict`),
  the portable bundle layout, `measurement_compare`, and the existing
  wizard. No standalone tools.
- **UX:** builds start only from the existing `Versions → Download & build`
  control. The Antithesis wizard gains no build button; it selects.
- **Antithesis platform requirements** (parity doc §"Antithesis platform
  requirements"): `FROM scratch` config image carrying `docker-compose.yaml`
  at root; one `setup_complete` emitter; Test Composer catalog baked under
  `/opt/antithesis/test/v1/<name>/`; `com.antithesis.exclude_from_faults`
  on control services; hermetic (no network, no pulls, no `pull_policy`, no
  custom logging driver); all images digest-pinned, public on GHCR or in the
  tenant registry; x86-64 only; `/symbols` at image root for instrumented
  modules; no `O_DIRECT`.
- **Runtime preload compatibility (spec.md C02).** Antithesis writes
  `/usr/lib/libvoidstar.so` into `/etc/ld.so.preload` of every container.
  Node images must resolve that preload's dependencies.
- **Author/push policy.** Git author `JG <forky@1timeuse.com>`, no
  attribution trailers, no push without the user's word.
- **Validation discipline.** Everything driven through the web dashboard as
  a dev would, on cardano-box; fix-then-redeploy; no out-of-DWARF
  workarounds.

## Current state (facts from the sweeps)

**Compile.** Three orchestrators drift (`controller/release_builds.py`,
`profile_manager/antithesis_artifacts.py`, `scripts/ci_amaru_stock_build.py`).
None calls `version_provenance`. The Antithesis one builds one mode per
request, has no auto-port, refuses Cardano stock without an exact recipe,
builds its Cardano image straight from the driver-written context (which the
controller deliberately refuses, `release_builds.py:439-441`), runs no marker
scan or `docker cp` identity check, and uses an "experimental" Amaru template
(`nanoseconds-v3`) that emits `experimental_instrumentation` so Amaru patched
taps can bind off-catalog. A controller variant row already carries
everything the Antithesis binders read from an artifact row except: a 32-hex
`id`, `state:'complete'` (controller says `ready`), `reference` (tag),
`image_id`, `runtime_probe_log_sha256`, and Cardano `compatibility`
(`antithesis_measurements.py:102-113`, `binding_selection`
`antithesis_artifacts.py:269-291`, `antithesis_harness.py:172`).

**Node-image contracts that are Antithesis-specific and must survive:**

- *Preload (C02).* `scripts/antithesis_cardano_source.py:17-54` stages the
  builder's libc6 family (`libpthread/libdl/librt/libutil/libanl`) next to
  the Cardano binary and fails on any `ldd … not found`
  (`runtime_library_strategy: node-ldd-plus-builder-libc6-v1`). The
  controller already runs the same driver, so local Cardano builds carry it.
  Amaru has no equivalent (static musl, no interpreter, verified by
  `build_amaru_measurement_target.verify_static_binary`). **Nothing anywhere
  exercises a stopped image with a dependency-bearing preload**, which spec
  C02 asks for.
- *Amaru `extracted-binary`.* The harness extracts `/usr/local/bin/amaru`
  from the artifact image into the client's bootstrap producer image
  (`CONTROL_AMARU_IMAGE`, `qualify_node_versions.transform_compose_model:
  369-406`); `probe_amaru` (`antithesis_artifacts.py:225-250`) runs the real
  extraction and `--version` / `run --help` inside that image. The
  controller's `_artifact_dockerfile` meets the declarative contract
  (`ENTRYPOINT ["/usr/local/bin/amaru"]`, `CMD ["run"]`, `USER amaru`) and
  asserts `runtime_interface == 'extracted-binary'` (`release_builds.py:
  550`), but only Antithesis runs the extraction itself.

**Catalog.** `preview_package` (`antithesis_package.py:176-339`) → three fixed
topology templates (`antithesis_topology.TEMPLATES`, counts 2/0, 0/2, 2/2,
magic 42, inventory must equal `CARDANO_SERVICES | AMARU_SERVICES`) → control
rig via `antithesis_harness.emit` or the native path
`antithesis_generator.render_bundle` (cardano-node only, one `cbor_fuzz*` load
or an aflpp campaign, four assertion primitives mapped to Sometimes/Reachable)
→ `bind_native_versions` (relay2 only) → `bind_workload` (hard-pins one
scenario by raw-bytes SHA-256, eight source files, the `sidecar` digest, and
the service inventory) → `bind_measurements` (stock taps always; patched only
when `identity.mode=='patched'` and the factories accept the identity).
Scenario fields consumed: `id`, raw bytes, `target.*`, `attach.topology`,
`measurement_profile`, `measurements`, and on the native path `seed`,
`load[]`, `assertions[].primitive`. Ignored: `setup`, `faults`, `probes`,
`phases`, `teardown`, assertion parameters, `iterations`, `shrink`,
`substrate`, `profile`, `schedule`. The tap coverage rule is never applied to
Antithesis results. The curated workload image `dwarf-sm` is rebuilt from
`build: {context: ./miniprotocol}` at package time, so it must be pushed.

**Tail.** Prepare → Build → Handoff (dev types a GHCR prefix) → Push (uses
whatever `docker login` exists on the host; `docker_config_path` is never
read) → **manual: make packages public in the GitHub UI** → Verify images
(anonymous) → **manual: extract ZIP into a public repo and commit** → Verify
Git (anonymous blob SHA check) → optional package validation → Readiness
(SSH to the requester host; checks configuration, package, generated
selections, public git, public images, requester facts, duplicate) → Submit
(`O_EXCL` claim, `reserve_external_run`, `moog requester create-test`) →
Refresh (`moog facts test-runs`). Secrets live on the requester host and in
`state/config.yaml` (masked fields in `data/operate_config.py:27-52`).
**Corrected 2026-11-06 (host sweep):** cardano-box is the MOOG requester
host. `moog` 0.5.1.3 is installed (`~/bin/moog`), the requester wallet,
passphrase, oracle material and a classic GHCR push token live under
`~/moog-secrets/` (mode 600; `ghcr.token` is what SUBMIT-RUNBOOK feeds to
`docker login --password-stdin`), GitHub OAuth for `moog antithesis` reads is
at `~/.config/moog/github-oauth.json`, and the full `moog:` block (token id,
GitHub PAT, launch URL, registry, duration) sits in the legacy
`~/dwarf-v4/var/state/config.yaml`. The V10 deployment has no
`state/config.yaml` of its own, but the live `dwarf-fw` container already
receives every `DWARF_MOOG_*` / `MOOG_*` value through compose env from
`~/dwarf-public-20261105/delivery`. So MOOG is configured today; what is
missing is only the GHCR prefix/PAT fields and the Docker seam (BC2).

**Results.** `_refresh` keeps `{test_run_id, phase, outcome,
report_available, observed_at}` and deliberately drops the signed report URL.
`reconcile_external_status` runs with no result bundle → `inconclusive`
(`verdict_reason: no_verified_result_bundle`). `_reserve` passes no
`scenario_path`, so the ledger entry has `scenario: null` and the signed
bundle import path (`_external_result_verdict`, `run_service.py:869-1035`)
would fail with `result_bundle_scenario_unbound` even if called. The one
committed Antithesis archive (`docs/evidence/runs/antithesis-taps-20261022.tar.gz`)
was supplied by hand, has no run/MOOG/Antithesis id, and the browser imports
it as a separate measurement view; `measurement_compare.is_antithesis_record`
keeps it out of the catalog concept rows.

**Instrumentation.** Antithesis documents coverage instrumentation for Go,
C/C++, Java, JavaScript, Rust, .NET; "other LLVM languages: contact us".
Rust: crate `antithesis-instrumentation = "0.1"` (separate from
`antithesis_sdk`), `use antithesis_instrumentation as _;` in the code,
`-Ccodegen-units=1 -Cpasses=sancov-module
-Cllvm-args=-sanitizer-coverage-level=3
-Cllvm-args=-sanitizer-coverage-trace-pc-guard
-Cllvm-args=-sanitizer-coverage-pc-table -Clink-args=-Wl,--build-id` on
target `x86_64-unknown-linux-gnu`; with LTO the sancov pass moves to the
linker (`-Clinker-plugin-lto`, lld, `--lto-newpm-passes=lto<O3>,sancov-module`
…). Verify: `nm | grep antithesis_load_libvoidstar`, `readelf -S | grep
sancov`. Unstripped binary (or debug link) under `/symbols`. The result is a
**dynamic gnu binary with a dependency on `libvoidstar.so`**. Amaru today is
`cargo +nightly zigbuild --release --target x86_64-unknown-linux-musl` with
`lto = true`, `codegen-units = 1`, and the verifier rejects anything not
statically linked. cardano-node: DWARF's GHC SanitizerCoverage plugin
(`antithesis/components/dwarf-adversary/coverage-docker/sancov/GhcSancov.cpp`,
edge trace-pc-guard, no pc-table) links the AFL runtime through a `-no-pie`
static link; switching to libvoidstar needs a C shim, pc-table, `--build-id`,
and has unknowns (static/no-PIE vs the preloaded library; no Haskell shim;
"contact us").

## The approach that was decided

### Piece 1 — Compile parity: Antithesis selects prepared releases

**Decision: the Antithesis track stops compiling.** The "Prepare experimental
node" step in the wizard is replaced by **"Select prepared build"**, listing
the operator ledger's registrations for the chosen implementation/version
(stock and patched variants, with the same `auto-ported from … · proven by
run …` labels the Versions page shows). `antithesis_artifacts.start`
(`node-start`) and `_experimental_amaru` are retired; `node-artifacts/` rows
are produced by a new adapter, not by a build.

- **Adapter `antithesis_artifacts.from_registration(registration_id)`** maps a
  ledger registration + its variant row to the artifact-row contract: `id` =
  first 32 hex of `sha256(registration_id)`, `state:'complete'`, `reference`
  = a stable tag the controller already produces (`dwarf/release-<impl>:<24hex>`,
  `release_builds.py:526`; the adapter re-tags under
  `dwarf-antithesis-node-<id>:prepared` so existing consumers are untouched),
  `image_id` = `config_image_id`, `runtime_probe_log_sha256` = the smoke
  evidence digest the controller stores (`smoke_evidence_sha256`), Cardano
  `compatibility` = `bridge.contract()` when the composite applies. All other
  fields copy through (`mode`, `version`, `source_revision`,
  `executable_digest`, `toolchain`, `build_result_sha256`,
  `patch_set_sha256`, `measurement_revision`, `runtime_interface`). Rows are
  written under the existing `node-artifacts/<id>/status.json` so
  `selected_artifacts`, `binding_selection`, `antithesis_harness`,
  `antithesis_version_binding` and `antithesis_measurements` need no change
  in what they read.
- **Patched identity without the experimental path.** `bind_measurements`
  builds the identity the way the local collectors already accept it:
  `release_build_identity.instrumented_identity(...)` → `identity_kind:
  'operator-local-config'`, `image_config_id`, `verified_instrumentation`,
  `source_build` (incl. `auto_ported`, `ported_from_source_revision`). This
  is the same object the local run passes to `amaru_patched._validate_identity`
  / `bridge._base_identity`, so auto-ported recipes bind on Antithesis
  exactly when they bind locally. `experimental_instrumentation` stays
  readable for old rows and is no longer produced.
- **Local-directory source** (the one Antithesis-only input: "build this
  checkout") moves into the shared core as a source kind on
  `release.prepare`: `source: {kind: 'bundle', path}` → the controller
  bundles with `git bundle create --all` and the drivers fetch
  `/work/source.bundle` (the `experimental_driver(local=True)` rewrite
  already exists, `antithesis_artifacts.py:306-311`; Cardano gets the same
  via `clone_exact_source` from a bundle). The Versions page gains no new
  button; this is reached through the existing version-import flow that
  already accepts a local path for inspection.
- **Shared gates added to the core (all tracks):**
  1. `version_provenance` (label == binary `git_commit`) at package time —
     today no orchestrator calls it.
  2. **Preload exercise (C02):** after packaging, run the stopped image once
     with a synthetic `/etc/ld.so.preload` naming a small DSO that has a
     constructor and a dependency on `libpthread.so.0`; the constructor must
     execute (`--version` still succeeds, marker file written). Applies to
     Amaru and Cardano images; the DSO is built in the builder container and
     never shipped. Failure blocks the variant with a concrete reason.
  3. **Amaru extraction probe** (`probe_amaru`) runs once per Amaru artifact
     at adapter time against the pinned `CONTROL_AMARU_IMAGE`; its log digest
     becomes the row's `runtime_probe_log_sha256`.
- **Controller fields added:** `image_tag`, `smoke_evidence_sha256` already
  exists; `preload_check` `{state, dso_sha256, log_sha256}`;
  `provenance_check` `{state, label, binary_commit}`.
- **CI track** (`ci_amaru_stock_build.py`) is out of scope here except that
  its `_compatible_patch` lookup is replaced by `measurement_patch_port.
  select_base_recipes` + exact glob so the three tracks share recipe
  selection. (The rest of the GitHub leg stays with sub-project B's GitHub
  work.)

### Piece 1b — Coverage-instrumented variant (`antithesis-instrumented`)

A **third build variant**, built by the same core when the release is
prepared, never selected by the local wizard, preferred by the Antithesis
wizard when present.

- **Amaru (in scope).** Recipe = the implementation's selected patch series
  (exact or auto-ported) **plus** a committed instrumentation layer under
  `dwarf/targets/amaru/antithesis-instrumentation/v1/`: a patch adding
  `antithesis-instrumentation = "0.1"` to the `amaru` binary crate and `use
  antithesis_instrumentation as _;` to `main.rs`, a `.cargo/config.toml`
  fragment with the documented LTO flag set (Amaru's release profile is
  `lto = true`), target `x86_64-unknown-linux-gnu`, linker `lld`. Builder:
  the existing `BUILDER_BASE` driver gains `lld` + `llvm-tools` installs
  (same apt step that installs clang today). **Verifier rules for this
  variant replace the static-musl rules:** ELF with interpreter, gnu
  target, `NT_GNU_BUILD_ID` present, `__sancov_guards`/`__sancov_pcs`
  sections present, symbol `antithesis_load_libvoidstar` present, no `jmp
  … __sanitizer_cov_trace_pc_guard` double-instrumentation, `ldd` closure
  resolves except `libvoidstar.so`. Image: `_artifact_dockerfile` plus
  `COPY symbols/amaru /symbols/amaru` (unstripped) and the stub
  `libvoidstar.so` under `/usr/lib/` (**source of the stub: left to the
  builder; see below**). The preload exercise (above) is the local proof
  that the preloaded real library will load first.
- **Extraction contract impact.** The extracted dynamic binary runs inside
  the client's bootstrap image; `probe_amaru` therefore gates this variant
  too (`ldd` inside `CONTROL_AMARU_IMAGE` must resolve; `--version` must
  run). If it does not, the variant is `unavailable` with reason
  `bootstrap-image-glibc-incompatible` and the wizard falls back.
- **cardano-node (spike, not in the parity plan).** Declared gap
  `node-coverage-not-instrumented` for cardano-node until a spike proves the
  GHC route with Antithesis (needs: C shim from
  `antithesis_instrumentation.h`, `PCTable = true`, `--build-id`, a link
  that tolerates the preloaded library). The spike is time-boxed and reports
  back; it does not block any other piece.
- **MOOG request.** `moog.py` carries only `no_instrumentation`
  (`--no-instrumentation`). The wizard sets it **false** only when every
  SUT image is the `antithesis-instrumented` variant; otherwise true, with a
  visible note. Readiness records which variant each target image is.

### Piece 2 — Catalog parity: DWARF's own runner is the workload

**Decision: generalise by putting the DWARF scenario runner inside the
package**, not by translating primitives one at a time into bespoke
services. For every scenario that is not the curated one, the generated
package is:

1. **SUT + control from the local topology.** The compose is projected from
   the same profile/topology the local run would use
   (`DockerRuntime` topology → compose model, which already produces the
   `profile-n-…` containers on the box), through
   `antithesis_topology.compile_topology`'s labelling: services the
   scenario's `target` names are **victims** (no fault label); everything
   else (producers, relays the scenario does not target, configurator,
   tracer, measurement helpers, the workload) is **control** and carries
   `com.antithesis.exclude_from_faults: network,kill,pause,stop`. The three
   fixed templates remain for the curated package; generated packages take
   their shape from the profile (sub-project D's "parameterised generator").
2. **Workload = `dwarf-workload` image.** One digest-pinned image built from
   the framework tree (the same `delivery/` Dockerfile lineage as `dwarf-fw`,
   minus the dashboard) with `cardano-profile`, the primitive registry,
   corpora, grammars and `cardano-cli` baked in. It runs the scenario through
   the existing `RunRequest` seam with `backend='antithesis'` (today any
   non-local backend raises, `run_service.py:490-493`): same `prepare`, same
   primitives, same bundle writer. Its Test Composer catalog is
   `/opt/antithesis/test/v1/dwarf/singleton_driver_scenario.py` (runs once
   per timeline, executes the scenario) plus `anytime_health.py` (SUT
   reachability). `setup_complete` is emitted by the workload **after**
   `prepare` succeeds and the SUT answers the scenario's own readiness probe
   (never before). Assertion outcomes are written as fallback-SDK JSONL
   (`always`/`sometimes` events, one atomic line each) to
   `$ANTITHESIS_OUTPUT_DIR/sdk.jsonl`, exactly as the Haskell adversary does
   today (`SDK.hs:109-115`), and the run bundle's `assertions.json` is the
   same list.
3. **Declared gaps, computed statically at preview.** A new
   `antithesis_capabilities.classify(scenario)` walks the scenario's
   primitives against a committed matrix
   (`dwarf/profile_manager/data/antithesis-capabilities-v1.json`): `runs`,
   `runs-with-note`, `delegated` (fault primitives → platform faults),
   `unsupported` (needs the Docker socket, host networking, or the local
   probe orchestration). The preview shows the scenario's support level
   (**full / partial / none**) with the list, the package manifest carries
   the gap records (`kind: 'implementation-gap'`, `code`, `primitive`,
   `message`), and the ledger entry carries the same list so the compare
   view can label the run. "None" scenarios still emit a package (the
   runner will skip those primitives with gap events) unless the scenario
   has no runnable load at all, in which case the wizard blocks submission
   with the reason. Initial matrix from the sweep: setup/teardown/load run
   (they are in-container already); `load_shell_command` runs when its
   command's tool is in the image; probes `parser_exit_status` runs,
   `runtime_peer_session_health` and `runtime_target_health_and_progress`
   run over the network only (note); faults `fault_local_port_delay/drop`,
   `fault_node_freeze`, `runtime_byzantine_cardano_node`,
   `runtime_network_partition` are delegated; `expect_exit` and
   `load_events_are_ok` run; phases run; `fuzz_iterations` override runs.
4. **Measurements.** `bind_measurements` is reused as is for the SUT
   services; the stock/patched/instrumented identity comes from Piece 1. The
   measurement runtime (`antithesis_measurement_runtime.py`) keeps writing
   snapshots to the evidence volume **and** the workload now emits the
   measurement `report.json` concepts as SDK `details` events at the end of
   the timeline, so the report carries them (see Piece 4). The tap coverage
   rule (`tap_coverage.rule_holds`) is applied to those concepts in
   reconciliation, with the Antithesis window exception kept.
5. **Curated package unchanged.** `bind_workload`'s pins and the mixed rig
   stay exactly as they are; the generated path is selected whenever the
   scenario is not the pinned one, and the preview says which path was
   used.

**AT-2 first build (2026-11-06) — REJECTED by the user on 2026-11-07.** The first
build projected every scenario onto the fixed `mixed-control-v1` rig with the
curated victim clones. That lost the scenario (roles ignored, three nodes per
implementation at most, the rig's edges instead of the scenario's), started the
Amaru side for cardano-only tests, demanded an Amaru version pick for them, and
made a cardano-only test's readiness depend on an Amaru bootstrap. The lifecycle
inside the package (entrypoint, readiness gate, driver handshake, SDK events, skip
policy, capability matrix and its plumbing, package-mode primitives, network-off
build) stands and is reused.

**AT-2 corrected (2026-11-07; plan `docs/plans/2026-11-07-antithesis-catalog-parity-v2-plan.md`).**
The generated package renders **the scenario's own devnet** from the control
rig's proven building blocks instead of projecting onto a fixed rig:

- **Nodes = the scenario's nodes.** `substrate.nodes` as declared (ids, impl,
  role, edges); a `profile` expands exactly as the local run does
  (`node1..N` cardano producers, `amaru1..M` consumers, `bootstrap-cardano`
  supporting producer for amaru-only); `attach.topology: cardano_amaru` keeps the
  rig by name. Public-network substrates and profiles (`preview`, `preprod`,
  `mainnet`) are `none` with reason `public-network`: the platform has no
  internet.
- **Every cardano node is a producer**, as in the local Docker runtime: the
  Cardano Foundation `configurator` generates genesis and pool keys **inside the
  environment** at run time for `poolCount = N`; each node mounts its own
  `/configs/<i>`; its topology file is rendered from the scenario's edges.
  `tracer` and `log-tailer` stay (node logs reach the platform's log capture);
  `tracer-sidecar`, `sidecar`, `amaru-consumer*` and the victim clones are not
  rendered.
- **Amaru nodes use the rig's `amaru-relay-bootstrap` wrapper** against the
  producer their first edge names (`/live` = that producer's ChainDB, `AMARU_PEER`
  = that producer); the selected Amaru release is assembled exactly as the rig
  does (`amaru-target-extract` + `/target/amaru-launch` for modern releases, the
  image itself for legacy ones). The launcher passes `AMARU_RUN_EXTRA_ARGS` so the
  node serves N2N on `3000` and the submit API on `3011`.
- **Faultable = every node of the target implementation that is not a supporting
  node** (producers included; the platform's faults are bounded and the chain
  survives a paused producer in a 3-producer devnet). Everything else carries the
  fault-exclusion label. `no-faultable-sut` applies to `library`, `single-node`
  and a devnet whose target implementation has no node.
- **Versions per node from the one release per implementation** the user
  selected (prepared artifacts from Piece 1 included). A cardano-only test asks
  for no Amaru version; an amaru-only test's supporting producer uses the
  catalog's default cardano-node release and is shown as supporting.
- **Readiness = local-run parity**: TCP + socket + a real block over the mounted
  socket for every cardano node (`cardano-cli query tip`), TCP + the
  `.bootstrap-complete` sentinel for every Amaru node; `setup_complete` only
  after that. The ready timeout is 900 s for cardano-only rigs and 2400 s when
  Amaru must bootstrap (three completed epochs at 200 s each).
- The package `runtime.json` keeps node ids as service names
  (`<id>.example:<3001|3000>`, `/inputs/<id>/node.socket`, `/inputs/<id>/amaru.log`)
  and no container fields; the rest of the first build (entrypoint, SDK events,
  skip policy, capability plumbing, package-mode primitives, `--network none`
  build, `FROM dwarf/controller:current`) is unchanged.

### Piece 3 — Self-serve tail

- **GHCR publish PAT** exactly as the parity doc's early-win section
  specifies: dedicated `ghcr_publish_pat` field (`write:packages`), one-time
  by default with explicit "save" opt-in and the unencrypted-storage copy;
  `docker login ghcr.io --password-stdin` into a throwaway `DOCKER_CONFIG`
  inside `_push`; a new authenticated client for `PATCH
  /user/packages/container/<name>` (or `/orgs/...`) → `visibility: public`;
  never log the header; never persist the PAT in any row.
- **GHCR prefix** is a configured field (`ghcr_prefix`, Operate settings)
  pre-filled on the wizard push card and editable per submission; see
  "GHCR destination" under the user's decisions.
- **Public commit of the package** (DEFERRED 2026-11-06 — no repo named;
  manual ZIP route stays as today) would become a wizard step: a dedicated
  `package_publish_pat` (`contents:write`) plus a configured `repo` and
  `branch`; the step creates one commit containing `antithesis/packages/
  <handoff_id>/…` through the GitHub contents/git-data API, shows the commit
  SHA, and then runs the existing `_verify_git` against it. **Each
  invocation requires an explicit confirm checkbox in the wizard** ("This
  publishes the package to <repo>"), because it is an outward push. The
  manual route (download ZIP, commit yourself) stays available.
- **Prerequisite panel.** Readiness already queries the requester facts;
  the wizard renders them as a checklist with "external: fix outside DWARF"
  against each failing one, and a direct link to the Operate settings for
  the ones that are configuration.
- **Docker login for the push** no longer depends on an ambient host login.

### Piece 4 — Results back into the one output

- **Bind the scenario at reservation.** `_reserve` passes `scenario_path`,
  so the ledger entry has `scenario` and the bundle import path can run.
- **Keep the report, privately.** `_refresh` stores the signed report URL in
  the run directory's private `external/report-link.json` (mode 0600, never
  in API rows, as `spec.md §7` requires), fetches the report once when the
  phase is terminal, and saves a sanitised snapshot: the properties table
  (each property's name, kind, pass/fail, encounter counts), the SDK
  details events (which carry DWARF's assertion list and the measurement
  concepts from Piece 2), fault events summary, and the Environment notes
  compose digest. Format: whatever the report endpoint returns is stored
  raw under `external/report/` (private) plus a normalised
  `external/result.json`.
- **Machine-readable sources already exist (corrected 2026-11-06).** The
  console triage report is HTML behind GitHub SSO and is kept raw only. The
  normaliser's inputs are (1) `moog antithesis runs/run/properties/events`
  (corrected 2026-11-06 during AT-4: moog.py did NOT wrap `moog antithesis`;
  AT-4 added `remote.fetch_antithesis` + `moog.build_moog_antithesis_command`
  and the control-shim read verbs `moog-antithesis-*`, `moog-test-run(s)`;
  `properties` takes only `--run-id` in moog v0.5.1.3; the join MOOG test run
  → Antithesis run is `description.testRunId`, not the fact URL; the OAuth
  file on cardano-box authorises; samples under
  `~/moog-secrets/requester/measured-20261021-*` and `validation-20261020-*`,
  sanitised copies in `tests/fixtures/antithesis_proxy/`), and (2) the in-environment
  `dwarf-measurements` service output, whose shape is already fixed and
  sampled: `/evidence/<service>/snapshot.json` + `measurements/report.json`
  (`schema_version: v1`, `target_identity.engine: 'antithesis'`;
  `antithesis_measurement_runtime.py:94-133`;
  `docs/evidence/runs/antithesis-taps-20261022.tar.gz`, 128 entries;
  importer `docs/antithesis-import.js:60-147`). Piece 4 does not wait for a
  paid run to know its input format.
- **Verdict.** `reconcile_external_status` receives a result bundle built
  from `external/result.json`: `pass` only when every DWARF `always`
  assertion passed, every `sometimes` was encountered, and the properties
  table shows no failing property; `fail` when any failed; `inconclusive`
  with a concrete `verdict_reason` otherwise (report unavailable, setup
  never completed, properties missing). This goes through the existing
  `_external_result_verdict` seam, extended with a `source: 'antithesis-
  report'` branch beside the signed-bundle branch.
- **Bundle.** The run directory gets the local layout where data exists:
  `manifest.json` (with `runtime: 'antithesis'`, `exit_status` from the
  verdict, timestamps from MOOG), `assertions.json`, `scenario.yaml`,
  `launch/`, `measurements/report.json` rebuilt from the SDK details
  concepts, and `gaps.json` for everything the platform could not export
  (`chain.json`, `probes/`, `metrics/`, raw collector output, the evidence
  volume). `collect_measurement_run_records` then picks it up for the
  compare view; `is_antithesis_record` stops excluding records that carry a
  real ledger id and uses the concept table directly.
- **Browser import** (`antithesis-import.js`) stays for hand-supplied
  archives and is no longer the primary path.

## Decisions from the builder-check (2026-11-05, my calls)

- **BC1 — Lifecycle inside the package.** Antithesis starts Test Composer
  commands only after `setup_complete`, so a driver cannot be the thing
  that runs `prepare`. The `dwarf-workload` container's **entrypoint** is
  the single scenario process: it runs `run_scenario` with a new
  `after_prepare` hook. The hook (1) runs the scenario's readiness probe
  against the SUT, (2) emits `setup_complete`, (3) blocks on `/status/go`.
  The Test Composer catalog is `singleton_driver_scenario.py`, which writes
  `/status/go`, then waits for `/status/done` and exits with the scenario's
  outcome. One process, one bundle, no two-half merge. `prepare` for
  `profile`/`attach` scenarios cannot launch or inspect Docker inside the
  package; the package carries a synthesized `runtime.json` projected from
  the compose (service names, ports, magic, node versions), and
  `runtime_attach_topology` accepts a pre-written `runtime.json` as its
  source. That is a bounded extension of the same primitive, not a second
  prepare.
- **BC2 — Docker access.** The deployed dashboard (`dwarf-fw`) is
  `read_only`, `cap_drop: ALL`, no socket (`delivery/docker-compose.dwarf.yml:
  88-106`); only `dwarf-controller` has the socket. The existing Antithesis
  build/probe/push code shells out to `docker` from the dashboard and so
  cannot work on the box deployment as installed (to be logged as an E-pass
  finding). **Every Docker operation in this design runs in the
  controller** through the existing `controller(action, params, timeout)`
  seam: `antithesis.artifact-adapt` (re-tag, extraction probe, preload
  exercise), `antithesis.image-build`, `antithesis.publish` (login + push).
  The dashboard keeps the rows and the UI. The controller redacts adapter
  stdout/stderr as it does for runs.
- **BC3 — Report format before a run (revised 2026-11-06).** AT-4's
  normaliser is written against the `moog antithesis run/properties/logs`
  output and the requester facts/log samples on cardano-box, plus the fixed
  `dwarf-measurements` snapshot format (see Piece 4). The console HTML
  report is retained raw and never parsed. Only the per-property field names
  of the proxy output are confirmed against the first real run.
- **Path fix:** the dashboard image recipe is
  `infrastructure/docker/dwarf-fw.Dockerfile`, not under `delivery/`.

## Track capabilities and invariants (what must not change)

All seven Antithesis invariants from the parity doc hold. In addition:

- The curated mixed package's pins (`SCENARIO_SHA256`, eight
  `SOURCE_SHA256`, `sidecar` digest, inventory equality) are untouched.
- No run may be submitted with `generated_selections` not applied; the new
  capability gaps are **not** counted as "not applied" (they are declared
  partial support), but a scenario with no runnable load is.
- The `dwarf-workload` image is digest-pinned in the compose and built with
  `--network=none`; its contents are listed in the package manifest with
  digests.
- `antithesis-instrumented` variants are labelled `coverage: antithesis
  (experimental)` everywhere a mode is shown.

## Decisions from the user (2026-11-05)

- **Acceptance (answered).** Paid runs are fine; **every validation run is
  1 hour** (`duration_hours: 1`) for now. **The validation standard is the
  local track's own evidence standard** (`docs/plans/2026-11-04-full-tap-
  coverage-design.md`): one run per implementation × mode in which **every
  supported tap carries a value or a documented exception**
  (`tap_coverage.rule_holds`), plus the mixed differential. Locally that took
  the four portable measurement-workload scenarios
  (`amaru-measurement-workload-{stock,patched}-portable`,
  `cardano-measurement-workload-{stock,patched}-portable`), built precisely
  so that one run values every tap of that implementation and mode. On
  Antithesis the same four scenarios go through the generated path. Minimum
  successful runs, each 1 hour:
  1. **Curated mixed package**, unchanged — regression check that Pieces 1-4
     broke nothing (stock taps, as today).
  2. **Amaru stock** workload scenario — generated path, adapter, stock
     identity.
  3. **Amaru patched** workload scenario — `antithesis-instrumented` variant
     built from an **auto-ported** recipe; proves the adapter, auto-port on
     Antithesis, the coverage variant, the extraction contract and the
     preload gate together; patched-only taps must carry data (this run also
     promotes the derived recipe to `auto-ported-proven` on the Antithesis
     surface through the same `record_run_proof`).
  4. **Cardano stock** workload scenario.
  5. **Cardano patched** workload scenario — Cardano identity path, libc
     staging, composite bridge.
  6. **Mixed generated** (`attach: cardano_amaru` family, one differential
     scenario) — both implementations as SUTs in one package, both tap sets
     bound; depends on the faultable-SUT decision below.
  "Successful" means: `setup_complete` reached, every Test Composer command
  ran, the DWARF properties were encountered, the run reconciled into the
  ledger with a `pass`/`fail` verdict (never `inconclusive`), and the
  coverage rule holds for that implementation × mode with Antithesis
  exceptions reduced to those that are physically unavoidable (the existing
  wholesale "bounded trace window" exception is replaced by per-tap
  justifications or removed). Budget two to five extra 1-hour runs for
  fix-then-redeploy; a run whose cause is a DWARF defect is fixed,
  redeployed and repeated before the next path is attempted.
- **GHCR (answered).** DWARF pushes images itself: a dedicated
  `write:packages` PAT, addable in the DWARF config (Operate settings) or in
  the Antithesis run wizard card, does `docker login ghcr.io
  --password-stdin` + push inside the controller. GitHub has **no API to set
  a container package public** (verified against the Packages REST API), so
  visibility stays a manual click, **once per image name**: generated image
  names are stable per (implementation, role, variant), not per submission,
  so after the first push of a name and its one-time public toggle every
  later push to that name is already public. The wizard shows the exact
  package URL for any name whose anonymous verify fails and polls the verify
  until it passes. **Re-verified 2026-11-06:** no REST or GraphQL
  operation changes package visibility (PATCH returns 404; community
  discussions #33310/#55094), visibility is not inherited from a linked
  public repo or the OCI source label, and only organisations have a
  "Package creation" default. A PAT cannot drive the web UI; a browser
  automation would need a logged-in session cookie, a full-account
  credential with no scope, so it is **rejected** for DWARF. Once per image
  name stays. The existing classic token in `~/moog-secrets/ghcr.token` on
  cardano-box is the value to enter into the new PAT field.
  **Approved 2026-11-06:** the push still happens from the wizard; for every
  image name whose anonymous verify fails the wizard shows a direct link to
  the package's visibility settings page
  (`https://github.com/users/<owner>/packages/container/<name>/settings`,
  or `/orgs/<org>/packages/container/<name>/settings` when the prefix names
  an organisation), opened in a new tab, and keeps polling the anonymous
  verify until it passes. No other manual step remains in the push card.
- **GHCR destination (answered 2026-11-06).** Images keep going to GHCR
  under `ghcr.io/j-gainsec`, as they do today. The destination is no longer
  typed per handoff only: a `ghcr_prefix` field (for example
  `ghcr.io/j-gainsec`) lives in the DWARF config (Operate settings) and is
  shown, pre-filled and editable, on the Antithesis run wizard's push card.
  The wizard value for one submission overrides the config value for that
  submission only. No GitHub repo decision was taken for the package files
  themselves, so the **public commit of the package stays the manual ZIP
  route exactly as today** (download, commit to a MOOG-whitelisted public
  repo, Verify Git). The automated public-commit step in Piece 3 is
  **deferred** (not in AT-3) until a repo is named; the wizard still shows the
  manual step and its anonymous verify.

- **Faultable SUT policy (answered: "refuse and show why").** When the
  projected package has no faultable service (no topology at all: the
  `library` and `single-node` scenarios; or every node of the target
  implementation is also a protected block producer), the preview classifies
  the scenario as `no-faultable-sut`, shows the reason and which services
  would have been protected, and **submission is blocked** with that reason
  in the readiness checklist. The package is still previewable so the dev
  can see the gap. **Mixed profiles are not expected to hit this rule
  (corrected 2026-11-06).** The public mixed layout already exists and is
  vendored: `cardano-foundation/cardano-node-antithesis/testnets/cardano_amaru`
  (last repinned 2026-10-27 by the Daily Amaru bot) excludes every service
  from faults except `amaru-relay-1/2`, and DWARF ships it as
  `antithesis/cardano_amaru_dwarf` (+ `_runtime`, `_kes_security`,
  `_relay_bootstrap_control`) with the same victim layout, while the curated
  `cardano_amaru_miniprotocol_security` adds a cardano victim clone
  (`sm-cardano-victim`) beside the Amaru one. The generated mixed package
  therefore projects onto the existing `mixed-control-v1` template
  (`antithesis_topology.py:25-29`): Amaru relays are victims; a cardano
  victim clone is added when the scenario targets cardano-node; the rest is
  control. The `no-faultable-sut` refusal applies to `library`,
  `single-node` and the degenerate all-producers case only. Victim-clone
  injection for cardano-node in *non-mixed* generated profiles stays a
  follow-on. Note: `spec.md:79` says the control relays are excluded, but
  the miniprotocol compose (:169, :197) carries no label on them; AT-2 fixes
  the compose to match the spec.

  **Corrected 2026-11-07 (user rejection of the first AT-2 build).** Generated
  packages no longer project onto a fixed rig. The scenario's own devnet is
  rendered (see "AT-2 corrected" under Piece 2): a cardano-only scenario starts
  only Cardano containers, an amaru-only scenario starts its Amaru nodes plus
  the one supporting producer the local run also uses, and mixed scenarios start
  exactly their declared nodes. Faultable services are the target
  implementation's non-supporting nodes, producers included; the
  `no-faultable-sut` refusal applies to `library`, `single-node` and a devnet
  without a node of the target implementation. Public-network scenarios are
  refused with `public-network`. "Use the proven version pair" is not a rule:
  any catalogued release binds, and an incompatible pair fails honestly at the
  readiness gate (exit 3, no `setup_complete`).

## Approval

Approved by the user on 2026-11-06 ("with that and the rest determine[d],
can we get started"), with the GHCR once-per-name click accepted as-is.

## Decisions for the user (closed 2026-11-06)

1. **Publish target** — GHCR part answered (see "GHCR destination").
   The git part is parked: no repo named, so the package commit stays
   manual. Naming a repo later re-opens the automated commit step as its
   own small sub-project.
2. **cardano-node coverage instrumentation on Antithesis** — my call
   unless overruled: a time-boxed spike **after** the parity plan lands,
   declared gap `node-coverage-not-instrumented` for cardano-node until then
   (no documented Haskell route; "contact us"). This is Antithesis-only:
   the local AFL/sancov coverage harness for cardano-node is unaffected.

## Left to the builder

- **Resolved 2026-11-06:** the stub `libvoidstar.so` is published by
  Antithesis at `github.com/antithesishq/antithesis_stub` (release v2.0.0,
  2026-08-18, one asset `libvoidstar.so`). The docs say to install it at
  `/usr/lib/libvoidstar.so` in the image for local testing; Antithesis
  replaces it with the real library through `/etc/ld.so.preload`. The
  builder downloads it by URL and pins its sha256 in the instrumentation
  layer like every other pin. Local SDK output goes to
  `ANTITHESIS_SDK_LOCAL_OUTPUT` / `ANTITHESIS_OUTPUT_DIR`.
- Exact report endpoint format from MOOG's signed URL (HTML vs JSON, and
  whether the "run/properties proxy" named in `spec.md §7` returns JSON).
  Capture it on the first real run; the normaliser is written against the
  captured sample and the raw form is always retained.
- The `dwarf-workload` image recipe (which of `delivery/`'s stages to reuse,
  how corpora/grammars are trimmed to the selected scenario to keep the
  image small).
- The capability matrix's initial contents beyond the sweep's list;
  anything not listed is `unsupported` until classified.
- How the preload-exercise DSO is built (a 20-line C file compiled in the
  builder container with `-shared -lpthread`).
- Wizard copy and layout for the three new cards (Select prepared build,
  Publish package to GitHub, Prerequisites checklist).

## Scope: independent sub-projects (in the user's order)

- **AT-1 Compile parity** — adapter, retire `node-start`/experimental path,
  bundle source kind, provenance + preload + extraction gates in the core,
  patched identity via `instrumented_identity`. Tests: adapter row equals
  contract; auto-ported Amaru patched taps bind on Antithesis; preload
  exercise fails on a missing dependency; provenance mismatch blocks.
- **AT-1b Amaru coverage variant** — instrumentation layer, gnu/lld build,
  new verifier rules, `/symbols`, extraction probe gate,
  `no_instrumentation` wiring. Tests: symbol/section checks on a built
  binary (fixture ELF), verifier rejects static/musl for this variant,
  wizard prefers the variant and declares the gap otherwise.
- **AT-2 Catalog parity** — generated path, `dwarf-workload` image,
  `backend='antithesis'` in `run_service`, capability matrix + preview
  support level, SDK JSONL emission, measurement concepts as details.
  Tests: any catalog scenario previews; gap list matches matrix; curated
  pins untouched; `setup_complete` only after prepare.
- **AT-3 Tail** — GHCR PAT card + `ghcr_prefix` config/wizard field +
  login + push in the controller + visibility URL/poll; prerequisite
  checklist; manual ZIP commit step kept with its anonymous verify
  (automated public commit deferred). Tests: secrets never in rows/logs;
  one-time PAT not persisted; wizard prefix overrides config for one
  submission only; push happens via the controller seam, never from
  `dwarf-fw`.
- **AT-4 Results** — scenario bound at reservation, report fetch + private
  link, normaliser, verdict branch, bundle writer, compare view inclusion.
  Tests: verdict rules; inconclusive reasons; bundle layout; compare picks
  the run.
- **AT-5 Validation pass (E)** — dashboard-driven on cardano-box: prepare
  a release (both variants, one auto-ported), select it on
  `/run/antithesis`, preview a non-curated scenario (gap list shown), build,
  push, make public, publish commit, readiness; then, per decision 1,
  submit and reconcile. Findings logged in
  `docs/plans/2026-11-05-e-pass-validation-log.md`.

Each is planned (`superpowers:writing-plans`) and executed natively when its
turn comes; AT-1 first.
