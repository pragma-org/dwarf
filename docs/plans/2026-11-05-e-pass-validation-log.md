# E-pass validation log — fresh install → versions → builds (2026-11-05)

Dashboard-driven, user-faithful validation of DWARF on cardano-box, from a
purged host. Everything below was done through the web dashboard
(`https://dwarf.gainpalfam.com`) with Playwright, except the README install
itself (a shell command by design). Findings are numbered; fixes are committed
on their own branches and noted.

## Starting state

- cardano-box purged: all DWARF containers/volumes/networks, the two locally
  built images, and the 24 GB runtime root removed. Upstream digest-pinned node
  images kept (cache). Existing source `~/dwarf-v10-fresh-sync` kept.
- Fresh public clone: `~/dwarf-public-20261105` from `github.com/pragma-org/dwarf`
  at `76ff4ccd` (newest public `main`), full checkout, clean tree.

## Install

- **F1 — Installer not resilient to snapshot.debian.org 504s (FIXED).** Two
  consecutive `install.sh` runs failed at
  `infrastructure/docker/dwarf-fw.Dockerfile:54`: `apt-get` fetching the pinned
  `libevent-core-2.1-7t64_2.1.12-stable-10+b1_amd64.deb` from
  `snapshot.debian.org/archive/debian/20260429T000000Z` got `504 Gateway
  Timeout` (two different Fastly edges). Fix on branch
  `fix/installer-apt-snapshot-retries` @ `eea35c70`: `Acquire::Retries "5"`,
  `Acquire::http::Timeout "120"`, and a bounded 5-attempt retry around the
  pinned install; **pinned versions unchanged**. Test:
  `test_runtime_image_retries_transient_snapshot_mirror_failures`. Applied to
  the box clone via `git am` (clone HEAD `3d30c17b`) so the source-build
  verifier saw a clean tree. Attempt 3 installed cleanly (exit 0). *Honest
  note:* the retry loop did not have to fire in attempt 3 — the mirror answered
  first time — so the fix is tested-in but not yet exercised by a live 504.
- **F2 — Bind must be set for a proxied host (documented, not a bug).** The
  `dwarf.gainpalfam.com` proxy lives on another LAN host and reaches the box at
  `192.168.30.16:8787`; the default `127.0.0.1` bind is unreachable through it.
  Install run as `DWARF_DASHBOARD_BIND=192.168.30.16 DWARF_DASHBOARD_PORT=8787`.
  Result: `dwarf-fw` + `dwarf-controller` healthy, listening on
  `192.168.30.16:8787`, `/operate` reports revision `3d30c17b`.
- Pre-existing, unrelated test failure on clean `main`, reported not fixed:
  `tests/test_dashboard_deployment_contract.py::test_remote_control_channel_refresh_preserves_configured_endpoint`
  (expects `known_hosts` in OPERATIONS.md).

## Dashboard first contact

- Fresh-install landing: 0 runs; seeded catalogs — 368 scenarios, 274
  primitives, 40 targets, 33 profiles, 255 corpus inputs, 74 grammar tokens, 34
  measurement taps, 18 risk candidates, 10 profile templates, 6 evidence
  bundles.
- Auth model confirmed: pages view without a token; `getToken()` reads
  `?token=` from the URL and the Versions view embeds it in forms. Operator path
  = open `/operate/versions?token=<dashboard-token>`.

## Versions → Fetch Latest Version (both implementations)

- Amaru fetch: `POST /api/versions/refresh → 202`; `refresh-status.json`
  `state: success`, `amaru: ok`, new versions **10.11.20261029** (revision
  `23031e0ed442…`, has a committed nanoseconds-v3 manifest) and
  **10.11.20261022**. UI after reload: "Verified. Source check: ok." Amaru rows
  17 → 19, both new rows offer **Download & build**.
- Cardano fetch: `state: success`, new versions **11.2.0** (prerelease) and
  **11.1.3**; Download & build offered on 11.2.0 / 11.1.3 / 11.1.2 / 11.1.1.
- **F3 — UX: the page reloads before an async refresh finishes.** The refresh
  returns 202 and the page reloads ~0.9 s later, so the first reload showed
  "Source check: checking" / "No successful fetch" and no new rows; a second
  reload showed the results. Suggest polling `refresh-status` until it leaves
  `checking` before reloading. Minor.
- **F4 — UX: raw JSON parse error surfaced on a proxy 502.** One
  `GET /api/versions/download/status` returned `502` from the proxy (empty
  body; the request never reached `dwarf-fw`). `release-downloads.js` showed
  "Couldn’t check availability. Failed to execute 'json' on 'Response':
  Unexpected end of JSON input" with a Retry button. Should read as a
  transient-availability message. Minor; transient; not reproduced since.

## Versions → Download & build

- amaru 10.11.20261029 (`23031e0e`): controller execution
  `release-build-5745fe91e4be40ea9582ed99965765cd`; stock `building ·
  source-build` in `dwarf-build-…-stock-compile`; instrumented `pending`
  (manifest exists → expected to build). Page shows "Preparing source builds.
  You can leave this page and return." and polls `prepare/status` (202).
- cardano-node 11.1.2 (`fef83fed`, the one Cardano revision with an
  instrumented recipe): Download & build accepted (`POST /api/versions/prepare
  → 202`; controller `idempotency/release-build-43058848… 202 accepted` at
  06:14:14 with an `executions/` record) but **queued** — the controller runs
  `release.prepare` jobs one at a time, so its `release-builds/` directory
  appears only after the Amaru job finishes. Host: 16 CPUs / 27 GB; build
  containers are capped at 2 CPUs / 8 GB each.
- **F5 — Controller records a waiting build as `running`.**
  `controller/service.py` writes the execution record with `state: "running"`
  *before* `_artifact_worker` blocks on the controller-wide `_mutation_lock`
  (+ a process lock). So the 11.1.2 record said `running` while no build
  directory or container existed; the dashboard row faithfully mirrored it
  ("Preparing source builds…"). A distinct `queued`/`waiting-lock` state (and
  row text) would be truthful. Minor, but it misleads an operator about what
  the box is doing.
- **F6 — One mutation lock serializes builds, library preparation, topology
  apply *and* run execution.** Verified in `controller/service.py`: the same
  `_mutation_lock` is acquired by `_artifact_worker` (release.prepare,
  image.download, artifact.package), the library.prepare worker,
  `_run_execute` (run.execute) and `_apply_with_profile_lock` (topology.apply).
  Consequence for a dev: click two Download & build buttons (one a multi-hour
  Cardano compile) and **nothing can run** — `/run` → Prepare/Run waits on that
  lock for the whole compile with no warning. Observed live: with the Amaru
  instrumented build compiling and Cardano 11.1.2 queued, no scenario can
  execute until both finish.
- **F13 — Build containers were pinned to 2 CPUs / 8 GB regardless of host
  (FIXED, configurable).** `release_builds.BuildContext.container()` hardcoded
  `--cpus 2 --memory 8g --pids-limit 512`, the Cardano toolchain Dockerfile had
  `$(nproc)` rewritten to `2`, the Amaru driver set `CARGO_BUILD_JOBS=2`, and
  `library_target_builds._default_builder` repeated the same literals — so on
  the 16-CPU / 27 GB box dedicated to DWARF every node compile used ~1/8 of the
  machine (the Amaru stock build took 23 min). Fix on branch
  `feat/build-resource-limits`: one shared `controller/build_resources.py`
  (`DWARF_BUILD_CPUS`, `DWARF_BUILD_MEMORY`; defaults unchanged for shared
  hosts; CPUs capped to the host; invalid → defaults; pids-limit scales 256/CPU;
  compiler jobs = CPUs) consumed by all four sites, plumbed through
  `common.sh` → compose → controller, documented in INSTALL.md. Tests:
  `tests/test_build_resources.py`, container/driver/toolchain/library-builder
  tests, and a deployment-contract test for the plumbing. *Deployment
  correction:* the first redeploy showed the controller env **without** the
  knobs — they had been added under the `dwarf-fw` service (its env contains
  `DWARF_CONTROLLER_URL: http://dwarf-controller:8790`, which a text search
  mistook for the controller block). Fixed in `ba8a6c66` and the contract test
  now parses the compose YAML and asserts the keys on `dwarf-controller`
  specifically. Lesson recorded: verify runtime env on the box, not just the
  file text. *Redeploy #2 (box clone `a6cbc273`):* `install.sh` exit 0,
  controller env now `DWARF_BUILD_CPUS=16`, `DWARF_BUILD_MEMORY=20g`, stack
  healthy, dashboard 200. The restart interrupted the 2-CPU Cardano 11.1.2
  build (as expected); both it and Amaru 10.11.20261022 were re-clicked from
  the dashboard afterwards. **Runtime proof:** the toolchain recipe for the new
  Cardano execution reads `make -j"16"`, and the compile container
  `dwarf-build-17c9c544…-stock-compile` was inspected live with
  `NanoCpus=16000000000`, `Memory=21474836480` (20g), `PidsLimit=4096` — the
  knob reached the container. (Also observed: a build interrupted by a
  controller restart now reads "Preparation interrupted. Retry to start a new
  build." in the row, with the button re-enabled — the right behaviour.)
- **F15 — Proxy 502s on concurrent status polls.** Loading the Versions page
  fires one `POST /api/versions/prepare/status` per downloadable row at once;
  two of them came back `502` from the proxy at t≈157 ms (the dashboard never
  logged them); a third occurred on a later load. Same family as F4
  (`download/status`). The dashboard is **not** serial — it runs
  `ThreadingHTTPServer` (`dashboard.py:6150`) — so the likelier cause is that a
  status poll was slow — **measured and ruled out**: a single poll for the
  row with two ready builds answers in 80–110 ms (proxy and direct alike; a
  no-build row in 20 ms), and bursts of 12 and 24 concurrent polls returned
  **200 every time**, direct and via the proxy (same-row polls serialize at
  ≈55 ms each on `release_preparation`'s per-key `flock`, harmless). All three
  observed 502s coincide with the dashboard **restarting or reloading** (the
  refresh-triggered reload at 06:12; page loads at 07:10 and 07:22 seconds
  after redeploys while `dwarf-fw` was still `health: starting`). Downgraded to
  **Low**: a normal readiness window; the only improvement is the row JS showing
  "dashboard restarting, retrying…" instead of a raw JSON/502 error.
- *Test-suite note:* the first full-suite run on `feat/build-resource-limits`
  showed 66 failures; a controlled rerun showed **0**, and none of the
  touched-module suites ever failed — the 66 were transient (host contention),
  not regressions. Commit `e0893588` was made only after the clean rerun.
- **F17 — The Cardano compile is single-threaded even with 16 CPUs allowed.**
  With the container at `NanoCpus=16e9`, `docker stats` shows `cpu=100.46%`
  and the top process is one `ghc-9.6.7` at 96.5 % — i.e. one core. The
  toolchain stage did use the knob (`make -j"16"`), but the recipe's build
  command (`cabal build --builddir=… -w ghc-9.6.7 exe:cardano-node` in
  `dwarf/targets/cardano-node/measurement-patches*/<rev>/manifest.json`) passes
  no `-j` to GHC and no `--jobs` to cabal, so module compilation inside each
  package is serial. Fix = add `--ghc-options=-j<cpus>` (and cabal `--jobs`)
  derived from `build_resources()['jobs']` when the driver runs the recipe —
  but note the build command is part of the recipe identity (`recipe_sha256`
  is recorded on every build), so this is a deliberate recipe/driver change,
  not a knob tweak. Medium (hours of wall-clock on a dedicated host).
  **FIXED (driver-side, recipe untouched) in `084ef6d1` on
  `feat/build-resource-limits`:** the driver already appended a hardcoded
  `-j2`; it now takes `build_jobs` from the host's request (set from
  `DWARF_BUILD_CPUS`) and adds `-j<jobs>` + `--ghc-options=-j<jobs>`,
  defaulting to 2, recording the flags as `effective_build_flags` in the build
  evidence. Tests: `test_build_command_uses_host_build_jobs`,
  `test_cardano_compile_request_carries_configured_build_jobs`. Deployed to
  the box only after the in-flight scenario run finishes (a controller
  redeploy would interrupt it).
- **F16 — After a controller restart the interrupted build's `progress.json`
  still says `building`.** The controller's execution ledger records the
  interruption and the dashboard row correctly reads "Preparation interrupted.
  Retry to start a new build." (good), but the retained
  `release-builds/<id>/progress.json` is never rewritten, so anyone reading the
  build directory sees a build that is still "building" forever. Low; the
  startup cleanup could mark the retained progress as `interrupted`.
- **F14 — `.env` is honoured by `common.sh` but not git-ignored.** The installer
  requires a clean checkout and `.gitignore` has no `.env` entry, so an operator
  cannot persist host settings (bind, build limits) in `.env` without dirtying
  the tree; they must be exported at every `install.sh`/`deploy.sh` run. Low;
  fix = add `.env` to `.gitignore` (and say so in INSTALL.md).
- **F12 — A build cannot be cancelled from the dashboard.** The controller
  implements `execution.cancel` and the dashboard has a `cancel` action
  handler, but only the topology execution on `/operate/status` wires it
  (`topology-health.js:403`). The Versions row for a running/queued build
  offers no Cancel, so a dev who realises a multi-hour compile is blocking
  every run (F6) has no in-UI remedy. Medium (usability), direct consequence
  of F5/F6; fix = a Cancel control on the build row calling the existing
  `execution.cancel`. Worth either a per-class
  lock (builds vs topology) or an explicit "a build is holding the controller"
  banner. Medium (usability), not a correctness bug — the serialization itself
  is safe.
- **06:36 — amaru 10.11.20261029 stock variant `ready`** (≈23 min from
  click); instrumented variant started compiling in
  `dwarf-build-…-instrumented-compile` (the committed nanoseconds-v3 manifest
  for `23031e0e` is being applied — the exact path B.1 generalises).
- **~06:58 — amaru 10.11.20261029 build `prepared`: stock `ready` AND
  instrumented `ready` (`mode: patched`, `measurement_revision:
  nanoseconds-v3`).** One Download & build click produced both variants from
  the committed `23031e0e` manifest on a 2-CPU-capped container in ≈45 min
  total. This is the exact path B.1 generalises to revisions without a
  manifest. **Registration proven:** on the next Versions poll the dashboard
  registered both variants in `state/version-catalog/operator-decisions-v1.json`
  — the instrumented registration carries `patch_set_sha256 ca10dff8c451…`
  (the v3 manifest's digest) and `measurement_revision nanoseconds-v3`, i.e.
  `_register` → `build_manifest` resolved the committed recipe — and the row
  shows **Use stock build** / **Use instrumented build** links into `/run`
  (`measurement_profile=amaru-security-default` / `-patched`).
- 06:59:31 — Cardano 11.1.2 build took the lock and started compiling (stock
  first), still on the 2-CPU cap at this point.
- **08:44 — cardano-node 11.1.2 build `prepared`: stock `ready` AND
  instrumented `ready` (`mode: patched`, `measurement_revision:
  nanoseconds-v5-antithesis1` — `_recipe` applied the committed v5 manifest
  plus the composite import correction).** 07:22:52 → 08:44:04 ≈ 81 min with
  the toolchain stage at `-j16` but the Haskell compile on one core (F17).
  **Registration proven:** ledger now holds 4 registrations; the Cardano
  instrumented one carries `patch_set_sha256 1d45c191ead7…` (=
  `antithesis_cardano_bridge.COMPOSITE_HASH`) and
  `measurement_revision nanoseconds-v5-antithesis1`; the row offers **Use
  stock build / Use instrumented build** into `/run`.
- *Observed once, unreproduced:* the Download & build click for amaru
  10.11.20261022 issued ~1 s after the Cardano retry click produced **no**
  `POST /api/versions/prepare` (one 202 only in that minute) and no handle;
  the row later read as never started. Re-click to be attempted after the
  first scenario run, watching the request log. Logged so it is not forgotten.
- Held for later (memory headroom): amaru 10.11.20261022; a *new* version with
  no manifest (expected: stock builds, instrumented `BuildBlocked` — the B.1
  gap).

## Built-in creators (dashboard-side, exercised while the controller builds)

- **Catalog hot-reload evidence:** after the two creations below the Operate
  deck reads **369 Scenarios** (was 368) and **34 Profiles** (was 33);
  Primitives stay at 274 because a scaffold is not a live primitive (by
  design). Dev-created definitions are immediately addressable.

- **Scenario creator — blank path (`/operate/scenarios/new`).** A structured
  authoring form: contract (id, title, implementation, target version,
  runtime, profile), execution limits (duration, seed, iterations, shrink),
  and per-phase **Add primitive** pickers already filtered to the chosen
  runtime/implementation (library · amaru correctly disables setup/faults/
  teardown). Adding `cbor_edge_cases` with no parameters and pressing
  **Validate** returned `cbor_edge_cases.target_id: is required` and kept
  **Save** disabled — the params-schema gate works and guides the author.
  Correct behaviour, not a defect.
- **Scenario creator — clone path ("Start from").** Selecting
  `amaru-cbor-tx-body-fuzz` navigated to `?template=amaru-cbor-tx-body-fuzz`
  and hydrated the form server-side (embedded JSON, no authenticated fetch):
  runtime `library`, implementation `amaru`, load `cbor_fuzz_target`, probe
  `parser_exit_status`, assertion `parse_succeeds_or_clean_error`. Set id
  `e2e-check-amaru-cbor-tx-body-fuzz` + title, **Validate** → "Valid. Ready
  to save.", **Save** → `POST /api/catalog/scenarios/validate 200`,
  `POST /api/catalog/scenarios/e2e-check-amaru-cbor-tx-body-fuzz/save 200`,
  redirected to `/operate/scenarios/e2e-check-amaru-cbor-tx-body-fuzz`. On the
  box: `state/scenarios/e2e-check-amaru-cbor-tx-body-fuzz.yaml` (917 B) with
  `target {amaru, any}`, `runtime library`, load `cbor_fuzz_target`, probe
  `parser_exit_status`, assertion `parse_succeeds_or_clean_error`. **Scenario
  creator: self-serve end to end, confirmed.** Run of this scenario is queued
  behind the controller builds (F6).
- **Profile creator (`/operate/profiles/new`) — self-serve end to end,
  confirmed.** Template picker switched in place (no navigation) to
  `generated-mixed`: `haskell_count 2`, `amaru_count 1`, `network_magic 42`,
  `topology_pattern local-mesh`, `shared_genesis true`, `version_policy
  latest-confirmed`; no placeholder host paths required for a generated
  profile. Set id `e2e-check-generated-mixed` + label → **Validate** → "Valid.
  Ready to save." and the editor resolved the policy to the confirmed pair
  (Cardano-node 11.1.2 · Amaru 10.11.20260918, all mixed readiness gates
  passed). **Save** → `POST /api/catalog/profiles/validate 200`,
  `POST /api/catalog/profiles/e2e-check-generated-mixed/save 200`, redirected to
  `/operate/profiles/e2e-check-generated-mixed`; on the box
  `state/profiles/e2e-check-generated-mixed/profile.yaml` matches the model.
- **Target creator (`/operate/targets/new`) — self-serve end to end,
  confirmed, with one data-quality trap.** Real manifest form (id, binary,
  harness, implementation/language, input delivery, decoder surface,
  readiness note, upstream commit, invariants, expected outcomes, complements,
  caveat). Filled like a dev would: id
  `e2e-check-amaru-cbor-decode-tx-body-20261029`, the existing harness binary
  `dwarf/targets/amaru/target/release/amaru-cbor-decode-tx-body`,
  `upstream_commit 23031e0ed442…`, three invariants. **Validate** → "Valid.
  Ready to save."; **Save** → `POST /api/catalog/targets/validate 200`,
  `…/e2e-check-amaru-cbor-decode-tx-body-20261029/save 200`; manifest written
  to `state/targets/manifests/<id>.yaml`.
- **F11 — Array fields accept and persist malformed input silently.** Entering
  the invariants as a JSON array (`["a", "b", "c"]`) was stored as **one**
  list element whose text is that JSON string. *Correction after reading the
  editor:* array fields are a textarea with placeholder **"One value per
  line"** (`definition_editor.j2:76`, split on `\n`), so the convention is
  shown and this was partly author error. Residual finding: an element that
  itself parses as a JSON array (or contains `", "`) is accepted without a
  warning, so the mistake persists silently. A one-line guard would catch it.
  Low. Repaired via the target's edit page: `/operate/targets/<id>/edit`
  loaded the definition (id read-only), one-value-per-line invariants →
  **Validate** "Valid. Ready to save." → **Save** `200`; the manifest now holds
  three distinct invariants. **Edit flow confirmed.** (Observation: the target
  detail page does not display the invariants list — only the editor shows
  them.)
- **Grammar creator (`/operate/grammars/<id>`) — clone → edit → save,
  self-serve end to end, confirmed.** Shipped grammars are read-only ("Clone
  before editing"). **Clone to runtime** on `repo--amaru-cargo-fuzz-handshake`
  created `runtime--amaru-cargo-fuzz-handshake--72b3569a` with a "Validated
  runtime editor" (structured + raw; validates the generation-structure schema
  and every mutation-token line before any write). Appended one libFuzzer token
  `"\x84\x04"` → **Validate and save** `200`; on the box
  `state/grammars/amaru-cargo-fuzz-handshake/dict.txt` holds it (tokens 12 →
  13) and `git status` of the clone stays clean — shipped files untouched.
- **Corpus import (`/operate/corpora/runtime--overlay`) — self-serve end to
  end, confirmed.** Shipped corpora are read-only (export only); the writable
  `runtime--overlay` unit exposes **Import an input / Import**, promote,
  deduplicate, disable. Imported a 3-byte CBOR seed (`82 00 a0`) → `POST
  /api/corpora/runtime--overlay/actions 200`; page: `READY · INPUTS 1 · BYTES 3
  · DUPLICATE INPUTS 0`; on the box `state/corpora/e2e-check-seed.cbor` has the
  exact bytes (sha256 `fc6e90d0…`).
- **Not exercised in this pass:** the measurement-*profile* creator
  (`/operate/measurement-profiles/new`, tap selection) and target
  *registration* via `/operate/targets` (inspect an existing image). Both use
  the same unified `save_definition` path proven above; listed so the gap is
  explicit, not implied closed.
- **Primitive creator (`/operate/primitives/new`) — scaffold only, confirmed
  live (the sub-project C hole).** The page says so itself: "Generate starter
  files… Nothing becomes live — the scaffold still has to be implemented,
  tested, merged…". Family `probe`, name `e2e_check_probe`, **Scaffold** →
  wrote `state/primitive-scaffold/e2e_check_probe/{scripts/runtime_e2e_check_probe.py,
  primitives/registry.json, tests/test_runtime_e2e_check_probe.py}`; the
  executor returns `"status": "scaffold-placeholder"`, the registry entry is
  bound to the generic `ProbePrimitive` with `scaffold_note: "Replace the
  default class binding in profile_manager.primitives before using this
  primitive in a runnable scenario."`; catalog count unchanged at 274. A dev
  cannot get a runnable primitive from the dashboard — exactly what sub-project
  C must close.
- **Measurement definition creator (`/operate/measurements/new`) — gate
  works; definition is self-serve, a new collector is not.** Form covers the
  tap contract (id, title, description, output schema, compatibility,
  collection authority external/stock-telemetry/compiler-coverage/patched-node,
  required capabilities, default-enabled, overhead, collector, emitted
  artifacts, correlation identifiers, threshold gate). **Validate** on the
  defaults → `'required_capabilities' is a required property`, Save disabled —
  the schema gate guides the author. (The browser console shows this as a `422`
  on `/api/catalog/measurements/validate`: that is the validator's reply, not an
  error.) As the code sweep found, this creates the
  tap *definition* only; the collector code it points at
  (`measurement_collectors/*.py` + factory binding) has no creator, so a
  genuinely new metric source still needs code + image rebuild (sub-project C,
  second hole).
- **F10 — UX: no confirmation after Scaffold.** The action redirected to
  `/operate/primitives` with no visible message naming the scaffold path or
  next steps (`_action_result.j2` exists but nothing rendered). A one-line
  "Scaffold written to … — not live until implemented" would stop a dev
  wondering whether anything happened. Minor.
- **F7 — "Start from" drops `?token=` from the URL (harmless here).**
  `scenario-editor.js:454-455` does `location.assign('/operate/scenarios/new?
  template=…')` without `token`. It is harmless because the GET handler passes
  `token=expected_token` into every page render, so the editor's `data-token`
  is repopulated server-side. Cosmetic inconsistency only; preserving the query
  would be tidier.
- **F8 — Observed once, not reproduced: template loaded blank.** On the very
  first "Start from" navigation after install, the form rendered with every
  phase "No primitives." and no error; the identical URL loaded correctly on
  every later attempt (with and without `?token=`). Suspect a first-load
  catalog/hot-reload cache race in `_new_scenario(template)`. Needs a repro
  before it is a finding; logged so it is not forgotten.
- **F9 — Deployment note (not a code bug): every page GET embeds the real
  mutating token.** `render_route_html(self.path, token=expected_token)` (and
  the sibling `render_operate_*` calls) put the dashboard token into page HTML
  for any GET, by design ("the token is not a replacement for network access
  control"). Behind a public reverse proxy such as `dwarf.gainpalfam.com` the
  proxy's own authentication is therefore the real gate. Worth stating in
  OPERATIONS.md next to the reverse-proxy guidance.

## `/run` wizard — first scenario run (by hand, as a user)

- Entry: the **Use stock build** link on the amaru 10.11.20261029 row
  (server-rendered without `?token=`; the wizard embeds the token itself via
  `data-dashboard-token`, so the link works — no F7-class issue here).
- Pre-fill was correct: Local inspected version **10.11.20261029 · stock build
  · amaru · linux/amd64 · source declared · qualification unverified**,
  measurement profile **Amaru security measurements — stock default · 11
  taps**, profile/version policy at their defaults; all four fresh builds
  (amaru 20261029 stock/instrumented, cardano 11.1.2 stock/instrumented) are
  offered as local inspected versions.
- **Qualification gate (correct behaviour):** Prepare was disabled with "The
  selected stable release has not passed this deployment contract; explicit
  one-run acknowledgement is required." (the `/api/run/resolve` 400 in the
  console is this refusal) until the checkbox **"Acknowledge this exact
  unconfirmed or local version for this run"** was ticked. A fresh build is
  never run silently.
- **Second gate, also correct:** resolve then reported "measurement
  amaru-stock-header-lifecycle requires experimental consent" — all 11 default
  stock taps show *Unknown compatibility* for a build with no prior evidence,
  so an explicit **"Try experimental taps — 11 selected taps have unknown
  compatibility. Missing or invalid output will remain unavailable, not
  successful."** consent is required before a plan can be prepared.
- **F18 — UX: the consent control is hidden inside a collapsed group and the
  blocking message doesn't point to it.** The checkbox lives inside
  "Individual compatible taps" (collapsed to "Individual taps · 11 selected");
  the alert and the disabled Prepare button only repeat the requirement. A
  user following the dashboard's own "Use stock build" link has to guess to
  expand that group. Fix = link the alert to the control or render the consent
  next to the Prepare button when `experimental_consent_required` is set.
  Low–medium (first-run friction, exactly the kind that makes a dev reach for
  help).
- **Prepare started (08:48 UTC).** With both consents given the headline became
  "Configuration is valid. Prepare must still prove the real topology." and
  Prepare enabled. Plan: scenario *Amaru stock measurements — real-node
  end-to-end proof* (`amaru-measurement-e2e-stock`), runtime `devnet`, scenario
  version 10.11.20261029 (the fresh stock build), profile
  `profile-r-amaru-measurement-stock-control`, 2 unique primitives · 11 taps ·
  3 readiness checks, seed `0xA11CE501`, execution "Local real-node DWARF
  engine". SSE progress: `plan: resolved` → `topology_preview: planned` →
  `topology: accepted` → `topology: running…` (controller deploying the real
  nodes). **Prepare completed:** controller `topology.apply applied` 08:48:09 →
  09:01:48 (≈14 min on a fresh host, including image start-up and genesis
  bootstrap); 12 containers up — `p1/p2/p3`, `relay1/relay2`,
  `amaru-relay-1/2`, `amaru-consumer`, `sidecar`, `tracer`, `tracer-sidecar`,
  `log-tailer`; wizard: `topology: applied · ready: ready · done: ready`;
  **Run prepared scenario** enabled.
- **Run started (09:02 UTC).** One click on *Run prepared scenario*: launch
  `launch-375556ba5e333528d4e94512`, plan digest `sha256:6bc8371f…`; preflight
  `profile:profile-r-amaru-measurement-stock-control passed
  (desired_observed_and_health_match)` and `deployed-version-identity passed`;
  state "Running · the canonical Run Service is executing the measured
  scenario. Measurements and evidence are being retained." Outcome below.

## Next

- Wait for builds → verify both variants `ready` and registered (`Use stock /
  instrumented build` links → `/run`).
- `/run` wizard: topology → versions → scenario → taps → prepare → run; then
  purge/redeploy discipline for the second scenario; then script the click
  sequence for repeats.

## 2026-11-05 — first scenario run outcome, merge, redeploy

- Run `20261105T090245Z-fa43a47d` (`amaru-measurement-e2e-stock`, profile `profile-r-amaru-measurement-stock-control`, amaru 10.11.20261029 stock): `exit_status: pass`, security verdict `pass`, 09:02:45 → 09:02:59 UTC (14 s), ledger `completed/pass`, 57 metrics in `measurements/report.json`, 1 assertion. Framework commit `a6cbc273` (box clone before B.1). The run finished within seconds of launch; the watcher built in the previous session never fired because `state/launches/<id>` is a directory of inputs (plan/preflight/profile/scenario), not a status file — run state lives in `state/run-ledger/entries/<run>.json` and `runs/<run>/manifest.json`.
- B.1 auto-port (8 commits) merged to local `main` → `4f9ee94c` (fast-forward; `origin/main` 3ddba1ef is an ancestor). Deployed to cardano-box by git bundle → `~/dwarf-public-20261105` reset to `4f9ee94c` → `delivery/scripts/install.sh` with `DWARF_DASHBOARD_BIND=192.168.30.16 DWARF_DASHBOARD_PORT=8787 DWARF_BUILD_CPUS=16 DWARF_BUILD_MEMORY=20g` (also carries F17 `084ef6d1`). Result: see below.
- Redeploy result: `install.sh` exit 0; `dwarf-controller`/`dwarf-fw` healthy; controller `DWARF_SOURCE_REVISION=4f9ee94c`, `DWARF_BUILD_CPUS=16`, `DWARF_BUILD_MEMORY=20g`; port driver present in the image; prior builds (amaru 20261029, cardano 11.1.2) still `ready`. Catalog now also lists cardano-node 11.1.3 (`938cba99`) and 11.2.0 (`dac0d7ab`), neither with a committed recipe.
- **First live auto-port (F19)** — Versions → Amaru 10.11.20261022 (`ddd3fdfc`, no committed recipe) → Download & build (POST `/api/versions/prepare` → 202 this time; the earlier no-POST click was not reproduced). Build `release-build-4fb2dfe2…`: stock `ready` after 9 min (16 CPUs); instrumented entered `port-check` 11:08:20 and finished `blocked` 11:09:05 — honest, with the per-patch `git apply --check` rejects rendered in the row and Build log. Root cause is a plan assumption, not a port bug: `select_base_recipe` picks the highest `-vN` series (v4 @ `aedfe797`, base release 10.11.20260918), which rejects on 5 files; a local check shows the v3 series @ `23031e0e` (base release 10.11.20261029, the adjacent revision) applies cleanly to `ddd3fdfc` (1/1), while every other committed base rejects. Newest *series* ≠ nearest *base*. Proposed refinement: port tries every committed recipe, ordered newest base release first (tie → highest series), in one port container (one source fetch, N checks), records all attempts in `port_attempt`, and ports the first that applies.
- **Nearest-base port (F19 fix) deployed and proven live** — `main` → `01db4faf` (port tries every committed series, newest base release first, in one container; all attempts recorded). Redeployed (installer exit 0, controller `01db4faf`). Retry preparation for Amaru 10.11.20261022: stock `ready` 11:27, port-check → candidate 0 (`10.11.20261029` / nanoseconds-v3) `ported` on the first attempt with two re-pinned preimages (`crates/amaru-ledger/src/state.rs`, `crates/amaru-observability/src/schemas.rs`), instrumented compiled through the exact-preimage driver and `ready` 11:37 (`auto_ported`, `patch_set ca10dff8…`). Versions row: heading **"Instrumented · passive + compiled taps · auto-ported from 23031e0ed442"**, Provisional note, **Export for review** → `Exported: /var/dwarf/bundles/derived-recipe-amaru-ddd3fdfc1c01.tar.gz` (manifest.json, port-result.json, patches/0001-dwarf-measurement-nanoseconds-v3.patch, DESTINATION.txt → `dwarf/targets/amaru/measurement-patches-nanoseconds-v3/ddd3fdfc…/`, README "Not yet proven by a run"). Ledger: registration `…f1942754…-instrumented` with `auto_ported: true`, `ported_from 23031e0e`; derived recipe persisted under `state/derived-measurement-patches/amaru/ddd3fdfc…/` as `auto-ported-candidate`.
- **F20 — patched-pinned profiles cannot resolve from the dashboard build flow.** Following "Use instrumented build" into `/run` and picking the scenario `amaru-measurement-boundary-proof-patched` (bound to `profile-q-amaru-measurement-patched`, which pins `amaru 10.11.20260912` + `measurement_patch_set f0e1aebc…`) fails resolve with "patched Amaru target is not built for source b159172f… and patch set f0e1aebc…" and withholds Prepare. `resolve_patched_amaru_target` requires a `state/measurement-targets/` record that only the legacy `build_*_measurement_target.py` path writes; the dashboard's Download & build registers operator-local builds instead (none of those records exist on a fresh install), and the wizard's **Local inspected version** override does not re-pin a patched profile. Consequence: the shipped patched-proof/calibration scenarios are unreachable for a dev on a fresh install; the reachable patched path is a portable scenario (e.g. `amaru-measurement-workload-stock-portable`) + a non-pinned profile (`profile-o`) + the instrumented registration + `amaru-security-patched` — exactly how the retained 2026-11-04 patched run was made. Medium: either the wizard should re-pin the profile's measurement identity from the chosen registration, or registration should also write the measurement-target record. Belongs to sub-project C/D, not B.1.
- **Patched run attempt (step 3) via `/run`:** scenario `amaru-measurement-workload-stock-portable` (Measurement validation → "Stock measurements — operator-selected release — workload", Amaru-only), Deployment profile `Latest confirmed Amaru devnet` (profile-o), Local inspected version = the 20261022 instrumented registration, Measurement profile `amaru-security-patched`; acknowledgement + experimental-taps consent → plan valid ("10 unique primitives · 15 measurement taps · 3 readiness checks", scenario version 10.11.20261022). **Prepare refused**: `plan: resolved … readiness: blocked … TopologyActionError: controller planning failed during prepare_topology`; controller audit `refusal-11d08175…`: `DockerRuntimeError: requested identity differs from the catalog-resolved topology`. The controller logs nothing for the inner exception (gap: the wrapper at `docker_runtime.py:1386` swallows the cause and the controller emits no log line). Replaying `build_deployment_version_preview(profile-o, registration=…instrumented)` inside `dwarf-controller` exposed it: `DeploymentVersionGateError: Compiled instrumentation has no matching exact-source patch recipe` — the stock pair (profile-r + 20261022 stock) previews fine with `amaru1 = 10.11.20261022` + local image, so the registration override itself works.
- **F22 — derived recipes were invisible to the controller (B.1 deployment bug, FIXED `477c21ff`).** `derived_recipe_root()` used `storage_roots().state`; the dashboard writes under `/var/dwarf/state` (host `state/`) while the controller runs with `ADA2_DWARF_STATE_DIR=/var/dwarf/controller-state` and mounts only `state/{version-catalog,profiles,scenarios,measurements,measurement-profiles}` (ro). The registration ledger resolves through `ADA2_DWARF_VERSION_CATALOG_STATE_DIR` and is therefore shared; the derived recipe was not. Fix: derived recipes live beside the ledger (`<version-catalog state>/version-catalog/derived-measurement-patches/`), resolved via `OperatorVersionDecisionLedger().root`; no compose change; controller reads, dashboard writes (`_register`, `mark_proven`). The recipe already on the box re-persists at the new path on the next Versions poll (`_register` saves when none is found). Unit tests could not catch this: they set one `ADA2_DWARF_RUNTIME_ROOT` for both roles.
- **F21 — wizard: a local registration and an exact catalog version are mutually exclusive.** Selecting a Local inspected version clears Version policy/Amaru version and vice versa; "Create new" (bounded generated profile) requires `amaru_version` ("amaru_version is required when Amaru nodes are selected") and so cannot be combined with a local build. Not a blocker once F22 is fixed ("Use existing" + registration is the path), but a generated topology cannot run a local build. Low.
- **F22 fix deployed (`477c21ff`)**; Versions poll re-persisted the recipe at `state/version-catalog/derived-measurement-patches/amaru/ddd3fdfc…/`; the controller's planning preview now resolves `amaru1 = 10.11.20261022` with the local instrumented image. `/run` again (same selections): Prepare → `topology: accepted → running → applied` 11:55:14 → 12:08:54 (profile-o, 12 containers); **Run prepared scenario** → `launch-d2759123ca471ea5c44ec609` → run `20261105T120941Z-e9280f8b`: `exit_status pass`, verdict pass, 12:09:41 → 12:23:50; report `target_identity`: amaru **patched** 10.11.20261022, registration `…f1942754…-instrumented`, executable digest = the registration's (`29ebaec9…`), 86 metrics; `proof_from_report` on it: proven, 16 patched-only concepts covered, `handler-queue-residence` excepted, `header-validation-time` absent. **The first auto-ported build carried real data on its patched-only taps.**
- **F23 — the proof was not recorded (B.1 deployment bug).** The run service executes inside `dwarf-controller` (`run.execute`), where the shared `version-catalog` mount is read-only (`os.access(... W_OK) == False`), so `mark_proven`'s atomic write failed inside `_prove_auto_ported_recipe`'s swallow-and-warn; the controller surfaces no log line for it (the same silence as the planning wrapper). Derived recipe still `auto-ported-candidate`, `auto_port_proofs` empty. Fix direction: record the proof on the dashboard side, which owns version-catalog state, when it receives the run's terminal result; keep the run-service hook for executions whose state is writable (CLI/local).
- **F23 fixed (`cf41c70a`)**: the proof step is one public `measurement_patch_port.record_run_proof(run_dir, run_id, exit_status)` (idempotent; one `auto_port_proofs` row per run id); the run-service hook delegates to it (CLI/local executions), and the dashboard's `/api/run/start` stream calls it with `<runs>/<run_id>` as soon as the controller reports the run (the runs volume is shared rw; the dashboard owns version-catalog state). Pinned by `test_installed_run_start_records_auto_port_proof_from_the_retained_run` and `test_record_run_proof_is_a_public_idempotent_step`. The already-completed run `…e9280f8b` is not re-evaluated retroactively; the next passed patched run on the build proves it.
- F21 is by design: `test_run_wizard_keeps_local_registration_and_catalog_pins_mutually_exclusive` pins the exclusivity. Downgraded to a documented limitation: a generated ("Create new") topology cannot run a local build; use an existing profile.
- **F23 fix deployed (`cf41c70a`) and the runtime proof closed the loop live.** `/run` again (same selections; Prepare reused the deployed profile-o topology in 16 s: `readiness: ready · done: ready`) → `launch-f30c97e4d3f969f4bc5fbfe7` → run `20261105T123349Z-41454fc8`: `exit_status pass`, verdict pass, 12:33:49 → 12:46:57, target amaru patched 10.11.20261022, 86 metrics. The dashboard recorded the proof on the controller's result: derived recipe `auto-ported-proven`, `proven_by_run = 20261105T123349Z-41454fc8`, one `auto_port_proofs` ledger row (16 patched-only concepts covered, `handler-queue-residence` excepted, `header-validation-time` absent). This is B.1's step 4b, end to end, from a dev's seat: Download & build → auto-port → instrumented build → scenario run with the patched profile → "proven by run".
- **Cardano 11.1.3 live auto-port (step 4):** Download & build 12:47 → stock `ready` 13:14 (26 min with 16 CPUs — F17 proven live) → port-check: candidate 0 (11.1.2 / nanoseconds-v5) `ported` (all 7 patches dependency-rooted → `inherited`; GHC 9.6 ok) → instrumented compile **failed** at `verify_preimages`: `preimage digest mismatch for api/lib/Ouroboros/Network/DwarfMeasurement.hs: expected dc9a26a9…, got a2e89d6c…`. Same archives, same patch bytes, same builder/driver loop as the passing 11.1.2 build; the file after `0001` is exactly `0001`'s added lines (a2e8…).
- **F24 — per-patch preimage states collapsed (B.1 design bug).** The committed v5 manifest lists `DwarfMeasurement.hs` under `0004` (a2e8…), `0006` (b422…) and `0007` (dc9a…) — each the state after the previous patch. The port driver collected preimages as one flat path→digest map (last wins), and `derive_manifest` wrote that map into every patch, so the derived `0004` expected `0007`'s digest. Amaru passed only because its series is a single patch. The row shows the honest compile failure. Fix: preimages per patch, computed right before each patch applies with patches applied in sequence (and the source reset between candidates), carried as `patch_preimages`; `derive_manifest` consumes them per patch; regression pinned on the real v5 manifest.
