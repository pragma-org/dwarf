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
- **F24 fix deployed (`84260d80`) — Cardano 11.1.3 auto-port succeeded live.** Retry preparation 13:23 → stock `ready` 13:49 → port-check: candidate 0 (11.1.2 / nanoseconds-v5) `ported` with per-patch preimages (`0004` a2e89d6c…, `0006` b422d76d…, `0007` dc9a26a9… for `DwarfMeasurement.hs`) → instrumented compile passed the exact-preimage checks and is `ready` 14:16 (`auto_ported`, ported from `fef83fed`, composite patch set `1d45c191…`, measurement revision `nanoseconds-v5-antithesis1`). Both implementations now auto-port end to end from the dashboard.
- **Cardano patched run attempt (step 4b):** `/run` from the 11.1.3 row's "Use instrumented build" → scenario `cardano-measurement-workload-stock-portable` (default profile `profile-n-cardano-latest-confirmed`), registration = 11.1.3 instrumented, `cardano-security-patched`; acknowledgement + consent → plan valid (13 taps) → Prepare deployed the 3-node Cardano topology in <1 min → Run → `launch-7ad6d0e5fe174c2e0ca3adea` → run `20261105T141821Z-b935ec4f` **died in 170 ms** inside the controller's scenario runner: wizard "Run completed without a canonical evidence identity"; controller audit `run_adapter_identity_missing: scenario runner returned no canonical run identity`, adapter stdout/stderr fully `[redacted]`; run dir has inputs/launch/env but no `log.ndjson`; ledger entry left `running`.
- **F25 — the Antithesis composite bridge pins the packaged 11.1.2 (B.1 design bug, FIXED `bb3fff9b`).** Replaying `bridge.build_factories(target_identity=…)` for the 11.1.3 identity inside `dwarf-controller` raised `Antithesis Cardano compatibility identity differs`: `_base_identity` required `source_revision == fef83fed` and `version == '11.1.2'`, and its base-form validation asks `build_manifest` for `BASE_HASH`/`nanoseconds-v5`, which no derived composite recipe answered. Fix: an identity whose registration says `auto_ported` + `ported_from_source_revision == fef83fed` is accepted as the correction base at another revision; `bridge.base_form_of(manifest)` (inverse of `composite_manifest`) lets `build_manifest` answer the base-form request for a derived composite. Pinned by `tests/test_antithesis_bridge_auto_ported.py` and `test_build_manifest_answers_the_base_form_for_a_derived_composite_recipe`.
- **Diagnosability gap (not fixed):** the controller redacts the scenario runner's stdout/stderr in the audit and logs nothing itself, so a runner crash before `log.ndjson` leaves no readable cause anywhere; both F22-class and F25-class failures had to be reproduced by replaying code inside the container. A bounded, redacted-but-not-blanked tail (exception type + message line) in the execution record would have named both in seconds. The abandoned run's ledger entry also stays `running` (no reconciliation ran).
- **F25 fix deployed (`bb3fff9b`) — Cardano runtime proof closed the loop live.** `/run` again (Prepare reused the 3-node profile-n topology in 6 s) → `launch-e993729a2ad18ee1c930e29c` → run `20261105T151718Z-f5e86e96`: `exit_status pass`, verdict pass, 15:17:18 → 15:22:37, target cardano-node **patched** 11.1.3, 57 metrics. Dashboard recorded the proof: derived recipe `auto-ported-proven`, `proven_by_run = 20261105T151718Z-f5e86e96`, patched-only concepts covered `block-application-time`, `protocol-decode-time`; ledger `auto_port_proofs` now holds amaru (16) and cardano-node (2). Versions rows read "auto-ported from 23031e0ed442 · proven by run 20261105T123349Z-41454fc8" (Amaru) and "auto-ported from fef83fed01d7 · proven by run 20261105T151718Z-f5e86e96" (Cardano).

### B.1 live validation — result
Both implementations: catalog version with no committed recipe → **Download & build** → nearest-base multi-candidate port → instrumented compile through the unchanged exact-preimage drivers → registration + persisted derived recipe → **Export for review** bundle → `/run` with the patched measurement profile → **"proven by run"**. Seven live findings, five fixed on `main` today (F19 nearest base `01db4faf`, F22 shared recipe root `477c21ff`, F23 dashboard-side proof `cf41c70a`, F24 per-patch preimages `84260d80`, F25 composite bridge `bb3fff9b`), two documented (F20 patched-pinned profiles, F21 wizard exclusivity by design) plus the controller diagnosability gap. The dev path for a new unconfirmed build: an existing non-pinned profile + Local inspected version = the registration.

## 2026-11-06 — AT-1 compile parity on cardano-box (Antithesis selects prepared releases)

Branch `feat/antithesis-track-parity` (design `docs/plans/2026-11-05-antithesis-track-parity-design.md`, plan `docs/plans/2026-11-06-antithesis-compile-parity-plan.md`). Deployed by git bundle to `~/dwarf-public-20261105` (branch checkout) and `install.sh` with the usual bind/port/build env; five deploys (9e6b13cc → 0081d9e9 → 6c737f5e → 04528ca7 → 8cd85e6f), each fix-then-redeploy from the dashboard.

- **Wizard.** `/run/antithesis` → Node versions → "Select prepared build" lists the operator ledger: Amaru 10.11.20261029 stock/patched, 10.11.20261022 stock and patched "auto-ported from 23031e0ed442 · proven by run 20261105T123349Z-41454fc8"; Cardano 11.1.2 stock/patched, 11.1.3 stock and patched "auto-ported from fef83fed01d7 · proven by run 20261105T151718Z-f5e86e96". Build variant filters the options (patched disabled under Stock and vice versa). No build is triggered from this page.
- **Cardano 11.1.3 patched → adapted on the first deploy** (`antithesis-adapt-1ff9c572…`, 1.6 s): `--version` reports `git rev 938cba99…` = registered source (`provenance_check: ok`); packaged executable digest matches the registration; binary is dynamic (`/lib64/ld-linux-x86-64.so.2`) so the **preload exercise ran**: fixture DSO compiled in the Cardano toolchain image, `/etc/ld.so.preload` + `/usr/lib/libdwarf-preload.so` mounted read-only into the stopped image, `--version` still answered and the constructor marker appeared in `preload.log` (`preload_check: ok`, dso + log digests recorded). Spec C02 is now exercised before any paid run.
- **F26 — extraction probe bind path (fixed `0081d9e9`).** Amaru adapt failed `exit 125`: `bind source path does not exist: /var/dwarf/controller-state/antithesis-adapts/…/probe/extracted` — `probe_amaru` handed the daemon the controller-internal path. `probe_amaru(host_directory=…)`; the adapter passes `context._host_path(probe)`.
- **F27 — retry replayed the failed execution (fixed `6c737f5e`).** After redeploy the same message came back and no new `antithesis-adapts/` directory appeared: the adapter used a deterministic execution id (= idempotency key), so the controller returned the cached failed record. One fresh execution id per attempt, recorded on the row.
- **F28 — modern Amaru CLI (fixed `04528ca7`).** Fresh execution: extraction and `--version` probes passed (`Amaru 10.11.0 (ddd3fdf+dirty)`), interface probe failed `unrecognized subcommand 'run'`. The probe hard-coded `run --help`; it now reads `--help` and probes `node run --help` when the executable groups commands under `node`, the same rule `release_builds` applies.
- **Amaru 10.11.20261022 patched (auto-ported) → adapted** (`antithesis-adapt-4dcd…` replaced by the final attempt): provenance ok on the 7-hex prefix, static binary (`preload_check: not-applicable-static`), `bootstrap_probe: version-and-run-help`, reference `dwarf-antithesis-node-790ee483…:prepared`.
- **Preview.** `version_binding: applied`; `node_artifacts` rows carry the contract (`reference`, `image_id`, `executable_digest`, `runtime_probe_log_sha256`, `provenance_check`, `preload_check`, `bootstrap_probe`, Cardano `compatibility = bridge.contract()`); compose: `amaru-target-extract` = the Amaru artifact, `relay1/relay2/sm-cardano-victim` = the Cardano artifact.
- **F29 — dashboard image lacked the pinned workload package (fixed `8cd85e6f`).** With taps selected, `workload_binding` was `recorded` with `workload-source-contract-changed` and every tap a `collector-not-implemented` gap: `dwarf-fw.Dockerfile` copied only `antithesis/cardano_amaru_relay_bootstrap_control/`, not `antithesis/cardano_amaru_miniprotocol_security/` that `antithesis_workload_binding.SOURCE` pins by content. Reproduces on `main`; a fresh install could never bind the one supported scenario's workload or any tap. Dockerfile now copies it (120 KB, no symlinks); contract test pins the COPY.
- **After F29:** `workload_binding: applied`; `measurement_binding: applied` with `amaru-patched-protocol-decode` on `sm-amaru-victim` (identity `operator-local-config`, `verified_instrumentation`, `source_build.auto_ported`, ported from 23031e0e) and `cardano-patched-protocol-decode` on `sm-cardano-victim` (auto-ported from fef83fed, composite v5-antithesis1). Taps the builds do not serve (`cardano-patched-header-validation`, `-blockfetch-handler-queue` for the 11.1.3 composite; `amaru-patched-header-validation`, pinned to the 0918 source) stay honest gaps — identical to the local track.

### AT-1 live validation — result
Both implementations' operator-prepared patched (auto-ported) builds are selected in the Antithesis wizard, adapted by the controller through the provenance / preload / extraction gates, bound as node artifacts, and bind their patched taps through the same identity object the local run uses. Four live findings, all fixed on the branch (F26 host-side probe mount, F27 fresh execution per attempt, F28 `node run`, F29 workload package in the dashboard image). Not exercised here: package image build/push/submit (AT-3) and a paid run (AT-5).

## 2026-11-06 — AT-3 self-serve tail on cardano-box (build, push, make-public links, checklist)

Branch `feat/antithesis-tail` (plan `docs/plans/2026-11-06-antithesis-tail-plan.md`). Three deploys (03f73fc7 → e4984ec6 → 3565da75), fix-then-redeploy from the dashboard.

- **Settings.** `GHCR prefix` and `GHCR publish PAT` appear in the wizard's Antithesis settings card and in Operate settings; saving `ghcr.io/j-gainsec/dwarf` through the wizard pre-fills the publication card's prefix on the next load.
- **F30 — mount target created root-owned (fixed `2f04a3d1`).** The controller's new read-only bind of `state/antithesis/preparations` made Docker create the host directory as root on a box that had never prepared a package; Prepare failed with the generic 503 (`PermissionError` inside `prepare_package`). The installer now pre-creates it with the runtime ownership like the other mounted state directories; the box directory was chowned once.
- **Prepare** then staged the curated mixed package with both adapted patched builds (7 findings, as before).
- **F31 — controller docker CLI lacks `image inspect --platform` (fixed `e4984ec6`).** The first controller build failed its node-artifact platform check with `unknown flag: --platform` (CLI 26.1 in the controller image vs 29.9 on the host). Fallback to a plain inspect with the same Os/Architecture/Id checks.
- **F32 — controller docker CLI has no buildx (fixed `3565da75`).** The legacy builder rejected the workload Dockerfile's `COPY --chmod`. When `docker buildx version` fails the controller rewrites that directive in its own copy of the Dockerfile to `COPY` + `RUN chmod -R` (the equivalence `release_builds` already uses for Cardano images); the dashboard's package is untouched and the result records `legacy_builder`. Follow-on: ship a pinned buildx plugin in the framework image.
- **Build images** → `built` on the controller (`antithesis-build-…`, workload `dwarf-sm` image built, both node artifacts verified), receipt `build_host: DWARF controller`.
- **Handoff** under the pre-filled prefix: five destinations `ghcr.io/j-gainsec/dwarf-{amaru-target-extract,relay1,relay2,sm-cardano-victim,sm-workload}:61ca62e7…`.
- **Push #1 refused the stored token.** The PAT in the box's secrets directory is dead (`ghcr.io` answers `denied` to it on the host too); the controller reported `registry-login-failed`, the field was cleared, no token reached any row, and the handoff stayed pushable. **Push #2** with the account's current token (`write:packages`) → `awaiting-visibility`: all five images pushed by the controller (`login exit 0`, five `push-*.log` with digests), the PAT never stored.
- **Verify public images** listed exactly the five new package names as private, each with its `users/j-gainsec/packages/container/<name>/settings` link (and the org form), and the wizard re-verified on its own 15 s later. These are new package names, so the one manual "make public" click is owed once per name; after that every later push to these names is public immediately.
- **Readiness** (source = built package, target typed) rendered the grouped prerequisite checklist and blocked on **Configuration** — this fresh install has no MOOG settings (no `token_id`); the row links to Operate settings. Correct: MOOG configuration is an external prerequisite, and the Git commit of the package is still the manual step the design deferred.

### AT-3 live validation — result
From the dashboard, with no Docker on the dashboard container: settings → prepare → controller build → controller push with a one-time PAT → visibility links + automatic re-verify → grouped readiness. Three live findings fixed on the branch (F30 mount ownership, F31 inspect fallback, F32 legacy-builder rewrite). Open for the operator: the five "make public" clicks, MOOG settings on this install, and the manual package commit.

## 2026-11-06 — AT-4 results on cardano-box (control channel, MOOG proxy reads, result collection)

Branch `feat/antithesis-results` (plan `docs/plans/2026-11-06-antithesis-results-plan.md`), deploys df0b181e → 17b19eff → a1c235af → 03972513 → 6ece1c05, fix-then-redeploy from the dashboard.

- **Install state.** The fresh V10 install had no SSH control channel (shim mode off, the compose key mount an empty placeholder, config `host 127.0.0.1 / ssh_user dwarf`). Provisioned with `delivery/control-plane/provision-control-channel.sh` (`DEPLOY_HOST=192.168.30.16 DEPLOY_USER=nigel`, this install's state dir) and re-ran `install.sh`, which detected the staged key and started the dashboard in shim mode; host/SSH user and the MOOG setup (binary, MPFS host, token id, secrets root, wallet file, GitHub user/repo) were saved through Operate settings. The shim's `id` is refused (`verb-not-allowed`); its dry-run renders the generated commands from the saved config.
- **F33 — wizard MOOG reads bypassed the shim (fixed `17b19eff`).** Readiness/refresh sent full `moog facts test-runs …` shell commands, which the restricted shim rejects. One builder per command in `profile_manager.moog` (argument-validated) now serves the dashboard and the shim; the wizard sends fixed verbs `moog-test-runs`, `moog-test-run`, `moog-antithesis-{runs,run,properties,events}`. Live: `moog-test-runs j-gainsec` through the container's channel returned the requester's 59 test-run facts; `moog-antithesis-run/properties` returned the 2026-10-20 run row and its 50 properties (45 passing, 5 failing).
- **F34 — readiness returned on a Git mismatch before observing MOOG (fixed `a1c235af`).** A package that differs from the public commit blocked readiness before the requester facts were read, so a retained attempt could never be linked from a different package. The mismatch is now a `public_git` check; the facts are still observed. Live: readiness for the retained attempt (`cyber-castellum/dwarf` @ `14b95fbf…`, `antithesis/cardano_amaru_miniprotocol_selected_validation`, `j-gainsec`, try 1) reported the grouped checklist (package ok, public_git error, three private curated images, requester checks, `duplicate` error with the existing test run `03c042dc…`) and enabled Refresh.
- **F35 — requester probe refused in shim mode (fixed `03972513`).** The probe was an inline SSH script (reading a hard-coded legacy config path). `moog.build_moog_requester_probe_command` + verb `moog-requester-probe`; live probe answered `{credentials: false, public_key: …}` (no transport PAT saved on this install; the wallet key resolved).
- **Refresh (link-existing + result collection).** Refresh reserved `external-antithesis-3c6aaab678d317391cb7a6ab` with the package's scenario bound (`cardano-amaru-miniprotocol-security-local` in `inputs/run-service-plan.json`), linked the MOOG test run, reconciled the terminal phase, wrote `external/report-link.json` (0600, the only file holding the signed report URL — `auth=` appears nowhere else in the run directory, the wizard row or the run page) and searched the proxy for the Antithesis run.
- **F36 — the proxy ignores paging (fixed `6ece1c05`).** `moog antithesis runs --cursor` returns the newest page again (moog v0.5.1.3) and `--limit` fails above 100, so only the newest 100 runs (back to 2026-10-24) are reachable; the retained 2026-10-20 run is outside the window and none of this requester's attempts is inside it. The lookup now reads 100 per page, stops on a repeated page (shim audit: two listing calls, not five) and the wizard says `antithesis_run_not_found (searched the newest 100 runs; the proxy ignores paging cursors, older runs are unreachable)`; the ledger stays `inconclusive · no_verified_result_bundle` and the run page explains it.
- **F37 — properties cannot page either.** `properties` has no cursor flag and the October run's table carries a `next_cursor`; a truncated table can prove a failure but not a pass → `inconclusive · properties_truncated` for a would-be pass.
- **F38 (open).** `install.sh` does not refresh the installed shim on a redeploy when the channel already exists; it was copied by hand this pass.

### AT-4 live validation — result
From the dashboard through the restricted control channel: settings → readiness (existing attempt observed) → Refresh (scenario-bound link, private report link, proxy lookup with honest limits) → run page. The verdict/bundle/properties path is proven end to end by unit, run_service and browser tests with the sanitised proxy fixtures and by the live proxy reads; proving it on a real run needs an Antithesis run inside the proxy's 100-run window, i.e. the next submitted run (AT-5).

### AT-4 review fix pass — live re-check (deploy 343ccb93)
- Refresh on the linked run: still `antithesis_run_not_found` with the honest window text; `external/status.json` now carries the matched MOOG fact with `value.url` removed (`report_available: true`); no `auth=` in any receipt, row or page.
- The run page still lists two receipt errors for this run: `link receipt does not contain exactly the matched external fact` (the link receipt was written before the fix and is immutable; a run linked after `343ccb93` carries the fact) and **F39 (open, pre-existing):** `ledger evidence path does not match this run directory` — in the container deployment the wizard's reservation records the process path (`/var/dwarf/runs/<id>`) while the page requires the declared host namespace (`DWARF_HOST_RUNS_DIR=/home/nigel/.local/share/dwarf/runs`) whenever that variable is set; nothing in `run_service` maps reservations to the host namespace, so every dashboard-reserved external run on a container install shows `Receipt integrity: fail`. Design call for AT-3b/AT-5: store host paths from the container, or accept the process namespace when the plan's declared runs dir equals the process runs dir.

## 2026-11-06 — AT-2 generated workload on cardano-box (catalog parity)

Branch `feat/antithesis-catalog-parity` (plan `docs/plans/2026-11-06-antithesis-catalog-parity-plan.md`), deploys 3490a487 → 1285ac87 → 9994f6d0, fix-then-redeploy from the dashboard. Scenario `runtime-substrate-mempool-failure-containment-mixed-amaru` (a substrate scenario, not the curated recipe), both patched prepared builds (cardano 11.1.3 `1ff9c572…`, amaru 10.11.20261022 `790ee483…`) adapted on the controller as in AT-1.

- **Preview** took the generated path: `Antithesis support: partial · 7 primitives, 1 declared gaps` (`runtime_mempool_failure_probe` is not in the matrix → unsupported), engine summary "DWARF scenario runner (dwarf-workload) executes this scenario inside the package", selection table scenario `partial` / topology `applied` / versions `applied` / build variant `applied`; 20 files including `environment/dwarf-workload/{Dockerfile, test/v1/dwarf/*.py, package/{scenario.yaml, runtime.json, compose-report.json, install-report.json, capabilities.json, expected-revision}}`; the compose carries `sm-cardano-seed`, `sm-cardano-victim` and `dwarf-workload` (label `com.dwarf.build-network: none`, `user: 0:0`, state and evidence volumes mounted read-only under `/inputs/<service>`), no `sm-workload`.
- **F40 — dwarf-workload image build failed on the chmod step (fixed `1285ac87`).** `COPY test/ /opt/antithesis/test/v1/dwarf/` nested the Test Composer scripts one level too deep, so `RUN chmod 0755 /opt/antithesis/test/v1/dwarf/*.py` matched nothing under the legacy builder. The Dockerfile asset now copies to `/opt/antithesis/test/`; a test simulates COPY semantics and checks the glob and the catalog path.
- **F41 — chmod "Operation not permitted" (fixed `9994f6d0`).** With the path fixed the step failed again because the controller base image runs as `USER dwarf`; the copied files are root-owned. The workload image now sets `USER root` (the compose service already runs as `0:0` for the node sockets); the same test pins it.
- **Build** → `built` on the controller: five images (`amaru-target-extract`, `relay1`, `relay2`, `sm-cardano-victim` from the prepared node artifacts; `dwarf-workload` built, `Successfully built ff1f0e2ba278`, row `network: none`, 1.13 GB on the controller lineage, legacy builder honoured `--network none`).
- **Smoke of the workload image on the box (operator action, not a DWARF feature):** `docker run --rm --network none -e DWARF_ANTITHESIS_READY_TIMEOUT=5 -e ANTITHESIS_OUTPUT_DIR=/tmp/out …` → the image's `expected-revision` equals its `release-identity.json` revision (`9994f6d0…`), the entrypoint ran `run_service.execute_run(backend='antithesis')`, the readiness probe timed out after 5 s (no nodes), **no `setup_complete` was emitted**, `/status/exit` = 3, `/status/done` written, container exit 3, and `sdk.jsonl` holds exactly one line: `dwarf_scenario_completed` (Sometimes) with `condition: false`, `exit_status: precondition_failed`.
- **Handoff + push** with the stored publish PAT: five destinations under `ghcr.io/j-gainsec/dwarf-*:775d1719…`; the four existing names (`amaru-target-extract`, `relay1`, `relay2`, `sm-cardano-victim`) verified public immediately; the new name **`dwarf-dwarf-workload` is private** and the wizard shows its settings link and re-verifies every 15 s — one manual "make public" click owed to the operator, once for this name.
- **Readiness** on the generated package was not reached live: the Git step needs the ZIP committed to the MOOG target repository (`pragma-org/dwarf`, user ruling 2026-11-06), which waits on the repository whitelist (requested from the Cardano Foundation agent; `register-role` tx `7a2693d5…` sent). The `scenario_capabilities` row (warn for partial, error with the reason for none) and the relaxed `generated_selections` rule are covered by `tests/test_antithesis_submission.py`.

### AT-2 live validation — result
From the dashboard: a non-curated catalog scenario previews with an honest support level and gap list, prepares, builds its `dwarf-workload` image on the controller with `--network none`, pushes, and the image proves its revision, readiness gate and SDK outcome contract when run. Two live findings fixed on the branch (F40 path, F41 user). Open: the `dwarf-dwarf-workload` make-public click; the first real run (AT-5) proves the mounted-socket/log observation route and feeds AT-2b (full landing).

### AT-2 review fix pass — live re-check (deploy 8b81fd13)
Fresh-reviewer findings fixed and redeployed: the preparation receipt now carries the classification (live: `capabilities: partial`, gaps `runtime_mempool_failure_probe` and `mempool_failure_contained` — the assertion whose evidence producer cannot run is itself declared unsupported, so the first paid run cannot fail it as a false SUT finding); the workload joins every network a mapped service lives on (live: `{default, amaru-consumer-net}` because `node2 → amaru-relay-1`); the readiness probe's result is merged into the package's compose report by the package-mode `runtime_compose_substrate` so `all_nodes_started_clean` can pass; attached layouts map the first target-implementation node onto the victim clone; the run page no longer lists declared primitive gaps under "Not exported by the platform". Package names unchanged; the `dwarf-dwarf-workload` make-public click is still owed.

## 2026-11-07 — AT-2 v2: scenario-rendered rig on cardano-box (first build rejected, corrected)

The user rejected the first AT-2 build (every generated package projected onto the fixed `mixed-control-v1` rig: cardano-only tests started the Amaru side, demanded an Amaru version and depended on an Amaru bootstrap; roles, counts and edges were lost). Plan `docs/plans/2026-11-07-antithesis-catalog-parity-v2-plan.md`; deploys 91e1c648 → 939d2c65 on branch `feat/antithesis-catalog-parity`. Generated packages now render the scenario's own devnet from the control rig's blocks (`antithesis_generated_rig.py`): the Cardano Foundation `configurator` generates genesis for `poolCount = N` inside the environment, every cardano node is a producer with its own topology file from the scenario's edges, Amaru nodes use the rig's `amaru-relay-bootstrap` wrapper against the producer their first edge names, releases bind per node, and the readiness gate has local-run parity (real block over the mounted socket, Amaru bootstrap sentinel).

- **Previews through the dashboard API (deploy 91e1c648):** `m3-runtime-node2-copied-state-bounded-divergence` (profile-a, cardano 10.7.1) → services exactly `configurator, tracer, log-tailer, node1, node2, node3, dwarf-workload`, support `full`, `testnet.yaml poolCount: 3`, three `topology/node<i>.json`, ready timeout 900 s, faultable `node1..node3`, no Amaru service, no Amaru version asked. `client-example-txsubmission-window-amaru-portable-20260918` (amaru-only, Amaru 10.11.20260918) → `bootstrap-cardano` (supporting, catalog default cardano-node 11.1.2) + `amaru1` (+ `amaru-target-extract`, `dwarf-measurements`), support `partial` with 7 declared gaps, timeout 2400 s, faultable `amaru1`. `runtime-substrate-mempool-failure-containment-mixed-amaru` → `node1` (cardano) + `node2` (Amaru peered to `node1.example:3001`) + extract + workload, 2 gaps. Handoff renderer `scenario-rig`; topology row names the producer/Amaru counts and the faultable services.
- **Prepare + build (controller, `--network none`):** cardano-only `61166dd9…` → build `51d84b89…` `built` in 20 s (`dwarf-workload` `9a7fd35d…`, 1.13 GB); after F42 `e0606131…` → `c22a3dc6…` (`0ed67410…`); honest baseline `1289b1a1…` → (`fc97b08b…`).
- **Operator compose smoke of the rendered package on the box** (`docker compose -p at2v2 … up -d --no-build` with the built workload image; not a DWARF feature): the configurator exited 0 with generated genesis for three pools; the three producers forged (`cardano-cli query tip` over the mounted socket from inside the workload: block 97 at slot 530 after ≈4 min); the workload's readiness recorded `ready: [node1, node2, node3]` and emitted `setup_complete` only after that (first package: ≈5 s after start on the second run, block 2 at slot 12); it then waited for `/status/go` (`waiting_for_driver` every 60 s).
- **F42 — `$HOME` inside the workload (fixed 939d2c65).** Released, the scenario's `load_shell_command` ran `python3 $HOME/dwarf-fw/scripts/…` with `HOME=/root` (the image runs as root since F41) → exit 2 → `load_events_are_ok` fail → `/status/exit` 1, SDK `dwarf_load_events_are_ok condition:false`, `dwarf_scenario_completed` with `exit_status: fail`: honest, never a false pass. The Dockerfile now sets `ENV HOME=/home/dwarf` (test). Re-run: `$HOME` resolves, but that scenario family (four `m3-runtime-node2-copied-state-*` scenarios) names `dwarf-fw/scripts/…`, a host-layout path that does not exist in the controller tree either (`dwarf-fw/dwarf/scripts/…`); it fails the same way under the native controller run. Catalog artifact, not a package bug; left as is.
- **End-to-end pass on a rendered devnet:** `runtime-substrate-honest-baseline-docker-mode-example-smoke` (2 cardano producers, cardano 10.7.1): readiness → `setup_complete` after 10 s, driver released, `runtime_multi_node_observation` ran over the mounted sockets (`per_node_dir_count: 2`, `correlated-timeline.json`, `observation-summary.json`), assertions `all_nodes_started_clean` pass, `all_nodes_responsive` pass, `peer_connectivity_observed` skipped (`primitive_skipped`, policy unsupported) → manifest `pass {pass: 2, skipped: 1, total: 3}`, `/status/exit` 0, SDK lines `dwarf_all_nodes_started_clean` true, `dwarf_skipped_peer_connectivity_observed`, `dwarf_all_nodes_responsive` true, `dwarf_scenario_completed` with `exit_status: pass`. Teardown `down -v`.
- **Catalog reach with matrix v1 (fact, not a defect of the rig):** 136 devnet scenarios run on public networks (`public-network`, refused with the reason), 83 cardano-only / 33 amaru-only / 14 mixed classify `none/no-runnable-load` because their load primitives need `docker exec`; 15 rig scenarios and 13 attached ones reach `partial`/`full`. AT-2b (re-routing those primitives to the mounted socket/log routes the rig now provides) is the gate to real coverage.

### AT-2 v2 live validation — result
A non-curated catalog scenario now previews as its own devnet (only the containers it declares), prepares, builds its workload image on the controller, boots on the box with in-package genesis, passes a readiness gate with local-run parity, and runs its scenario to an honest pass or fail with SDK outcomes. Open: F42 fixed; the `dwarf-dwarf-workload` make-public click; the pragma-org/dwarf whitelist (first real Antithesis run); AT-2b re-routing for the docker-exec primitives; an Amaru rig boot smoke (bootstrap needs three completed epochs, ≈10-15 min) not yet run on the box.

### AT-2 v2 — mixed package smoke and review fix pass (deploy 939d2c65 → 42daa052)
- **Mixed package booted on the box** (`runtime-substrate-mempool-failure-containment-mixed-amaru`, cardano 10.7.1 + Amaru 10.11.20260918 through `amaru-target-extract`): `node2`'s `amaru-relay-bootstrap` wrapper refreshed `/live` from `node1`'s ChainDB, waited for the chain to cross the third epoch boundary (`tip_epoch=2 (need >=3)` at t=10 min), committed its bundle (`.bootstrap-complete`, `chain.testnet_42.db`, `ledger.testnet_42.db`, three snapshots) and exec'd `amaru run` listening on 3000; the workload's readiness recorded `ready: [node1, node2]` after 645 s (TCP + socket + real block for `node1`, TCP + sentinel for `node2`) and emitted `setup_complete` only then. Released, the scenario skipped `runtime_mempool_failure_probe` and `mempool_failure_contained` (declared unsupported), ran `runtime_multi_node_observation` with Amaru tips from the mounted `amaru.log` (11/11 samples, block 270 at slot 1309, in step with the producer) → `all_nodes_responsive` pass, manifest `pass {pass: 1, skipped: 1}`, `/status/exit` 0, SDK `dwarf_all_nodes_responsive` true, `dwarf_skipped_mempool_failure_contained`, `dwarf_scenario_completed` with `exit_status: pass`. Torn down with `down -v`.
- **Fresh review (whole branch):** 1 Critical, 4 Important, 7 Minor, all Critical/Important fixed RED→GREEN in `42daa052`: **C1** Amaru-bearing rigs now pin `ExperimentalProtocolsEnabled` exactly as the local runtime does (profile pin wins, otherwise `false` for cardano-node 11.1+, whose NodeToNodeV_16 offer breaks Amaru's handshake; the rig's `testnet.yaml` ships `true`, so an amaru-only rig on the catalog default 11.1.2 would have passed readiness and never synced); **I1** topology files use `useLedgerAfterSlot: -1` and `PeerSharing` follows the profile (profile-a is peer-sharing-disabled); profiles with `plutus_v2_genesis`/`kes_genesis_override` are declared `profile-option-not-rendered` instead of silently differing; **I2** an explicit cardano-node selection binds the supporting producer of an amaru-only rig instead of being flagged unused; **I3** an unresolved supporting release leaves the binding `partial` with one honest gap; **I4** a hung or garbled `cardano-cli query tip` counts as "not ready" instead of aborting the gate. Minors deferred (ledger).

### AT-2 v2 — amaru-only on cardano-node 11.1.2, package validation, F43–F45 (deploys 42daa052 → 27051c28 → 842fcd84)
- **F43 — profile scenarios with declared measurements aborted at prepare inside the package (fixed 27051c28).** The amaru-only package (`client-example-txsubmission-window-amaru-portable-20260918`, cardano-node 11.1.2 default supporting producer, `ExperimentalProtocolsEnabled: false` rendered as the fix pass intends) exited 2 within 15 s: `fresh deployed runtime is unavailable: /opt/dwarf/cardano-profiles/<profile>/runtime.json` — the runner's local measurement preparation looked for the deployed profile runtime, which does not exist in the package. In package mode the runner now records `measurement_auto_attach_skipped` ("delegated to the Antithesis package collector (dwarf-measurements)") and runs; the `dwarf-measurements` sidecar collects from the mounted evidence volumes. Test: `test_package_mode_delegates_scenario_measurements_to_the_package_collector`.
- **Package validation (user prompt: "there is also some type of verification").** Dashboard validation on the preparations and on the built honest-baseline package: **F44 — the rendered compose carried `networks.default.internal: ${INTERNAL_NETWORK:-false}`** copied from the rig source, which the preflight rejects (`C01 compose-interpolation`, `C05 internal-network`); the rig now ships no networks block, as the control-rig path does (fixed 842fcd84; after redeploy: static validation 0 fail). Remaining rows: `C06 hostname-map` warns on every service (`<id>.example` hostnames, the rig convention; live-proven resolvable: Amaru synced from `node1.example:3001`), `C15 image-digest` warn on `dwarf-workload` until publication pins the pushed digest, `C03 image-build` fails only on the pre-build preparation (the built package carries the image reference). Artifacts mode: `C01 compose-schema` fail and `C02 local-image-platform` not_checked — **F45: the validator runs the dashboard container's Docker CLI 26.1 / Compose 2.26.1, which lacks `--no-env-resolution` and `docker image inspect --platform`** (`compose-schema.log: unknown flag: --no-env-resolution`); the same compose passes `docker compose config --no-interpolate --no-env-resolution --quiet` on the host (Compose v5.6.1) for the rendered packages and the vendored rig. Environment limitation of the framework image (same family as the buildx follow-on F31/F32), not a package defect; follow-on: newer Docker CLI in the framework image.
- **snouty validate (official Antithesis local validator, 0.6.1 on the box)** against the built cardano-only package (`~/.local/share/dwarf/state/antithesis/builds/5e3035ee…/package`, images pinned, `docker-compose` symlink present): configurator generated genesis for two pools, both producers started, **`Setup-complete event detected. Found 0 first, 1 driver, 1 anytime, 0 eventually, 0 finally test commands. Setup validation successful.`** snouty refuses packages whose services still carry `build:` without a local image (the pre-build preparation), so it must run on the built package. snouty did not execute the driver (by design).
- **F47 — configurator ignores the testnet.yaml `PeerSharing` key (fixed f5c02630).** snouty's node log showed `ncPeerSharing = PeerSharingEnabled` although the rendered `testnet.yaml` said `PeerSharing: false`; running the configurator alone proved `configurator.sh` writes `.PeerSharing` from its own `PEER_SHARING` env (default true) and that `ExperimentalProtocolsEnabled` from `testnet.yaml` **is** honoured (`false` in the generated `config.json`, so the C1 pin is real). The rig now sets `PEER_SHARING` on the configurator service from the profile.
- **F46 — `runtime_verify_exact_target` inside the package (fixed 27051c28 → matrix, commit "F46").** The amaru-only package on cardano-node 11.1.2 with the pin: Amaru bootstrapped and synced in step with its producer (`tip.adopt` block 288 at slot 1479 = producer tip), readiness after 645 s, `measurement_auto_attach_skipped` recorded (F43); the run then ended `error` with no assertions because `runtime_verify_exact_target` needs the frozen local launch identity (resolved-plan fields + measurement context) the package does not carry. Matrix v1 now marks it `unsupported` (skipped with a gap; the version binding still records what was bound) until AT-2b adds the package identity route.
- **Amaru-only re-run with F46/F47 (deploy f5c02630):** generated node config carries `PeerSharing: false` and `ExperimentalProtocolsEnabled: false` (both pins live-proven), readiness after 690 s, Amaru synced in step (block 309 at slot 1507 on both), `runtime_verify_exact_target` and the window marks skipped by policy; **snouty validate passed on the built amaru-only package** (`Setup-complete event detected`, 1 driver + 1 anytime). The run still ended `error`: **F48 — profile scenarios have no `runtime_compose_substrate` step, so nothing seeded `substrate_runtime_metadata_path` inside the package** and `runtime_txsubmission_window_pressure` raised "requires substrate runtime metadata". In package mode the runner now seeds the shared state (and the prepared-runtime handle attribute) with the package `runtime.json` (fixed 727f5cc6; test `test_package_mode_seeds_the_package_runtime_as_the_deployed_substrate`).
- **F48 corrected (b5605b51).** The diagnostic re-run captured the exact error: `runtime_txsubmission_window_pressure requires exactly one endpoint mode: substrate runtime metadata or profile_id` — the first F48 seeded the substrate state for a profile scenario, which the profile-endpoint primitives reject. The runner now only sets the prepared-runtime handle to the package `runtime.json` (the same route a local measurement preparation uses), and `_measurement_target_identity` falls back to the package's own target (`runtime.json: target_implementation` + the first non-supporting node of that implementation) when no measurement context exists. The `dwarf_scenario_completed` SDK event now carries the last `phase_error` (F49), so a real run's report says why a run ended `error`.
- **F50 — frozen launch-plan route inside the package (fixed, commit "F50").** With F49 the SDK event finally named the error: `frozen execution target identity is unavailable: retained launch plan parent is unsafe`. `execute_run` always carries a run-service plan digest, so the client-target resolver took the frozen-launch-plan route, which needs the retained launch store that does not exist in the package. The resolver now prefers the package's own target identity when the package carries one. Final amaru-only smoke with this image in progress.
- **Final amaru-only smoke (deploy 996369bd, F50):** readiness after 690 s, driver released, `runtime_txsubmission_window_pressure` resolved its target from the package (`endpoint_source: prepared-runtime`, `amaru1.example:3000`, `runtime_identity_path: /opt/dwarf/package/runtime.json`) and ran; its probe script exited 1 (traceback in `runtime_txsubmission_probe.py`, recorded as `unexpected_exit`), the window marks and health probes were skipped by policy, `txsubmission_window_enforced` / `unrelated_peer_session_usable` skipped, `target_progress_continues` / `no_target_fatal_signal` **failed honestly** because their evidence producer (`runtime_target_health_and_progress`, unsupported) wrote no proof → manifest `fail {fail: 2, skipped: 2}`, `/status/exit` 1, SDK `dwarf_scenario_completed` true with `exit_status: fail`. The lifecycle is complete for all three devnet shapes; the remaining gaps are per-primitive (AT-2b): the txsubmission probe inside the package, and assertions whose evidence producer is skipped should themselves be skipped with a gap instead of failing.

### AT-2 v2 live validation — result
All three devnet shapes render, prepare, build, pass the dashboard's static validation, pass `snouty validate` (cardano-only and amaru-only built packages), boot on the box with in-package genesis, pass a readiness gate with local-run parity, and run their scenario to an honest outcome: cardano-only pass (2-node honest baseline), mixed pass (mempool scenario with Amaru tips from the mounted log), amaru-only on cardano-node 11.1.2 honest fail (probe crash + producer-less assertions). Nine live findings fixed on the branch (F42–F44, F46–F50) and one environment limit recorded (F45). Open: the first real Antithesis run (whitelist), AT-2b primitive landing, the `dwarf-dwarf-workload` make-public click, a newer Docker CLI in the framework image for the artifacts validator.

## 2026-11-07 — AT-5 pre-submit chain on cardano-box (deploy 996369bd, mixed v2 package)
User prompt after the GHCR `dwarf-dwarf-workload` package was made public: "is there any other fixes/things to do prior to getting whitelist on antithesis" → "go". Everything AT-5 lists before `submit`, driven through the dashboard API as the wizard does (`scratchpad/at5_chain.py`).

- **Prepare + build (fresh on 996369bd):** `runtime-substrate-mempool-failure-containment-mixed-amaru`, cardano-node 10.7.1 + Amaru 10.11.20260918, preparation `e94a278a…`, build `d3b8e0a9…` → one controller-built image (`dwarf-workload`, `--network=none`); every node image is a public upstream digest (intersectmbo cardano-node + tracer, CF configurator + log-tailer, lambdasistemi amaru-bootstrap-producer, pragma-org/amaru). No measurement collector because the scenario declares no taps.
- **Publication handoff `f1805da1…`** (prefix `ghcr.io/j-gainsec/dwarf`): push succeeded with the saved PAT in 30 s; `verify-images` → `images-public` on the first try (the only pushed name was already public, so a v2 stock package needs **zero** new make-public clicks). Exported ZIP: 15 files, 23.9 KB, compose rewritten to `ghcr.io/j-gainsec/dwarf-dwarf-workload@sha256:4bbf8215…`.
- **Publish commit:** the ZIP contents committed verbatim as `antithesis/generated/runtime-substrate-mempool-failure-containment-mixed-amaru/` (nested `generated/` keeps wizard packages apart from the curated snake_case packages) → pragma-org/dwarf main `5cdc25fe` (author JG, fast-forward over 9f99a6c0; local main synced). Inspection before commit: no executable files, no key material (the only `skey` hits are the configurator's in-container key paths).
- **`verify-git`** against that commit: "Public Git assets match" (exact blob hashes and modes) → handoff state `review`.
- **Readiness** (source = publication, target = pragma-org/dwarf @ 5cdc25fe, requester j-gainsec, try 1) → **blocked on exactly two checks**: `facts_white_list` and `moog_repo_whitelisted` ("could not be verified"). Green: package, public_git, all 7 public_image rows, facts_roles, facts_users, github_codeowners, github_profile_vkey, moog_role_registered, moog_user_registered, requester_vkey, requester_wallet_funded, requester_wallet_metadata, transport_credentials, selected_wallet, duplicate (no prior attempt). `scenario_capabilities` warns `partial` (3 lifecycle steps delegated to the package, 2 unsupported primitives skipped as declared gaps).
- **Result:** the first DWARF-generated run is staged end to end; the only missing input is the CF agent's white-list entry for pragma-org/dwarf. When it lands: re-run readiness on handoff `f1805da1…` (same target, try 1) and submit from the wizard.

## 2026-11-07 — F39 / F45 / F38 fixed and live-proven on cardano-box (deploys 76d83094 → 42601906)
User prompt: "go" on the pre-whitelist list (F39 option 1 recommended, then F45, F38).

- **F39 — container reservations now declare the host runs namespace (fixed 76d83094).** `run_service.reserve_external_run` records the ledger `evidence_path`, the plan's `storage.runs_dir` and the provenance paths in `DWARF_HOST_RUNS_DIR` when it is set, while the evidence is still written under the process runs directory; every process-side reader (`record_external_submission`, `link_existing_external_run`, `_antithesis_report_verdict`, `reconcile_external_status`, the three readers in `antithesis_submission`) maps the declared path back through the new `run_service.external_evidence_dir`. Old reservations keep their recorded process path (the earlier `external-antithesis-3c6aaab6…` still shows the error; it is immutable). Live, inside the deployed `dwarf-fw` with a throwaway state root: ledger `evidence_path /home/nigel/.local/share/dwarf/runs/external-antithesis-f30809c4…`, plan `storage.runs_dir /home/nigel/.local/share/dwarf/runs`, evidence under `/tmp/f39/runs/…`, run page without the `ledger evidence path` error (only the two queued-state errors remain: no bound external test id, no matched fact), submission receipt written into the process directory. Tests: `test_external_reservation_records_declared_host_namespace_from_a_container`, `test_external_evidence_dir_maps_the_declared_host_path_back_to_the_process`, `test_external_receipts_are_written_under_the_process_run_dir_in_a_container`, `test_dashboard_reserved_external_run_passes_receipt_integrity_on_a_container_install`.
- **F45 — Docker CLI inside the framework image (fixed 42601906).** `dwarf-fw.Dockerfile` drops Debian's `docker.io`/`docker-cli`/`docker-compose` (26.1.5 / 2.26.1) and installs `docker-ce-cli=5:29.9.2-1~debian.13~trixie` + `docker-compose-plugin=5.6.1-1~debian.13~trixie` from `download.docker.com` (deb822 source, signed by the vendored `infrastructure/docker/docker-archive-keyring.asc`, fingerprint `9DC8 5822 9FC7 DD38 854A E2D8 8D81 803C 0EBF CD88`, sha256 pinned in the test). The controller image inherits it (`COPY --from=framework / /`). Live: the rebuilt container reports `Docker version 29.9.2` / `Compose v5.6.1`; artifacts-mode validation of the mixed build `d3b8e0a9…` completes with **0 fail / 36 pass / 9 warn / 18 not_checked** — the compose-schema check that failed on `--no-env-resolution` now passes.
- **F51 (open, surfaced by F45) — the artifacts validator's Docker calls run in `dwarf-fw`, which has no Docker socket by design (BC2).** `local-image-*.log`, `candidate-build.log`: `failed to connect to the docker API at unix:///var/run/docker.sock … no such file or directory`, so C01 candidate-image and the C02 local-image-platform rows stay `not_checked`. The image inspection, candidate build/create and cleanup in `antithesis_package_validation` must become controller operations (as `antithesis_image_build` / publication already are). Static mode and `snouty validate` are unaffected.
- **F38 — installer refreshes the shim (fixed 42601906).** `install.sh`'s "preserve shim mode" branch now copies the release's `dwarf-deploy-shim.py` over the installed shim when the two differ (key, `authorized_keys`, config untouched). Live: a marker line was appended to `~/dwarf-control/dwarf-deploy-shim` before the deploy; the install log shows `refreshed the installed control shim at /home/nigel/dwarf-control/dwarf-deploy-shim`, the marker is gone and the installed shim matches the release byte for byte.

## 2026-11-07 — F51 fixed and live-proven (deploy 82eb265f): artifacts validation runs its Docker work in the controller
User prompt: "can f51 be fixed properly?" → "yes. and make sure the local deployment and the github actions/runners deployments hasn't been broken or regressed".

- **Fix (82eb265f).** New controller operation `antithesis.package-validate` (`controller/antithesis_validate.py`; contracts, service dispatch/receipts/progress/restart-cleanup, runtime hooks). The dashboard stages the exact package archive under version-imports (`binary_imports.stage_package_archive`, same seam as the staged registry credential: private dir, descriptor, 30-minute expiry, digest-bound) and polls `execution.status`; the controller verifies and unpacks the archive, inspects every service image for linux/amd64, runs `compose config --quiet --no-interpolate --no-env-resolution`, builds the pinned MOOG config-image candidate with `--network none`, reads each packaged file back from a never-started container, compares bytes and modes, removes container and image, and returns **facts**. `antithesis_package_validation` maps the facts onto the existing C01/C02/C15 rows with the same policy texts; static mode is untouched; a controller that cannot inspect leaves the rows `not_checked`, never `pass`. Tests: `tests/test_antithesis_validate.py` (11, controller module with a fake runtime), contract + service dispatch/failure/interrupted-cleanup tests, and the validator tests rewritten against a fake controller that reads the staged archive (`tests/test_antithesis_package_validation.py`).
- **Live (mixed build `d3b8e0a9…`, artifacts mode):** before F51 (deploy 42601906) 0 fail / 36 pass / 9 warn / **18 not_checked** with every `local-image-platform` row and `candidate-image` unchecked and `candidate-cleanup` warned; after F51 **0 fail / 44 pass / 8 warn / 11 not_checked** — all seven `local-image-platform` rows pass, `compose-schema` pass, `candidate-files` pass (51 members read back and compared), `candidate-platform` ok, cleanup confirmed. The 11 remaining `not_checked` rows are the contract's execution-only checks (C08–C14, binary-runtime, experiment-evidence, candidate-base, non-GHCR public-image). Docker logs live only in the controller job directory; the dashboard validation directory holds no Docker output; no `dwarf-antithesis-check-*` container, `dwarf-antithesis-config-check:*` image or `validate-*` staging directory is left behind.
- **Regression checks for the user's question.** Full suite on the F51 tree: 65 failed / 5368 passed = exactly the known pre-existing set (the earlier `test_from_registration_re_adapts…` flake did not recur). GitHub Actions: `dwarf-release.yml` builds `dwarf-fw.Dockerfile` for linux/amd64 **and** linux/arm64 and runs `delivery/tests/test_installer_contract.sh` (passes locally); the amd64 image built on cardano-box three times today (F45, F38, F51 deploys) and the arm64 image built on this Mac with the installer's build arguments and reports `Docker 29.9.2` / `Compose v5.6.1` on aarch64 (two earlier arm64 attempts failed only on `files.pythonhosted.org` read timeouts at the unchanged pip step). `dwarf-security-campaign.yml` builds the same two Dockerfiles on amd64; `dwarf-validate.yml` runs `validate_scenarios.py` (same single pre-existing schema failure). `delivery/tests/test_delivery_contract.sh` fails on this Mac checkout before and after today's commits (`runs` directory check against the tracked legacy state files) and no workflow runs it — pre-existing, not a regression. Local deployment path on the box: `install.sh` preserved the control channel, refreshed the shim once (F38 proof), and the dashboard + controller came up with the new CLI each time.

## 2026-11-07 — Fresh review of F39/F45/F38/F51, fix pass (3a854d3a), AT-2b evidence-producer rule (a7414272)
User prompt: "ok anything else to do prior to antiehsis allowlist" → "go" (review first, then the AT-2b honesty items, then AT-1b).

- **Review (independent reader, range 71c7cfbb..e8ba40ed):** no Critical; four Important; ten Minor; verdict "with fixes". Important: (I1) the controller ran `compose config` and the candidate build on packages the static contract had blocked; (I2) one Docker timeout failed the whole validation instead of one fact; (I3) a cancelled or timed-out candidate leaked its container and image because cleanup went through the job deadline; (I4) the F38 refresh sat behind `ADA2_DWARF_CONTROL_SHIM != 1`, so operators who set the flag in `.env` as `provision-control-channel.sh` instructs never got it, `deploy.sh` never refreshed, and the installer read an `INSTALL_DIR` variable nothing defines. All four fixed with tests first (`test_statically_blocked_package_gets_image_facts_only`, `test_image_inspect_timeout_marks_that_image_not_checked_and_keeps_the_rest`, `test_candidate_build_timeout_is_a_fact_and_still_cleans_up`, `test_cancel_during_candidate_still_removes_container_and_image`, the three `refresh_control_shim` behavioural tests). Minors taken because they change what an operator gets: M5 explicit `--runs-dir` outside the canonical root is no longer rewritten to the host namespace; M8 the dashboard requires the readback rows to cover every packaged file with its own digest; M9 transient status errors are retried, a persistent one cancels the controller job; M10 the staged archive is created inside the `try`; M11 the shim is replaced by rename with a `.prev` copy; M13 dead guard. Minors deferred: M6 (provenance paths other than launch/plan still process-namespaced; nothing reads them), M7 (legacy process-path ledgers keep failing the integrity check on purpose; docstring now says so), M12 (replaced by the behavioural tests), M14 ("controller busy" wording when a validation queues behind a long build). Declined-to-judge items ruled: Compose remote `include` resolution is moot once blocked packages never reach `compose config`; no buildx plugin — CLI 29 falls back to the legacy builder and the live builds prove it; Docker pool retention is the vendor's policy; refreshed shim vs older `dwarf-control.conf` — the refresh changes only the shim, config compatibility is the release's contract as before.
- **Live (deploy 3a854d3a):** F38 on the env-flag path — the shim was staled before the deploy and `ADA2_DWARF_CONTROL_SHIM=1` exported: `refreshed the installed control shim at /home/nigel/dwarf-control/dwarf-deploy-shim (previous copy kept as ….prev)` from `install.sh`, then `already matches this release` from `deploy.sh`; the marker is gone and the shim matches the release. F51 regression: artifacts validation of the mixed build `d3b8e0a9…` again 0 fail / 44 pass / 8 warn / 11 not_checked; no leftover container, image or staged archive.
- **AT-2b (a) — assertions follow their skipped evidence producer (a7414272).** The capability matrix links `target_progress_continues` and `no_target_fatal_signal` to `runtime_target_health_and_progress`, and `unrelated_peer_session_usable` to `runtime_peer_session_health`; `classify()` marks such an assertion `unsupported` with gap `assertion-evidence-unsupported` naming the producer when the producer is present in the scenario and skipped there, and the package skip policy skips it. A producer absent from the scenario leaves the assertion's own status (a missing proof is then a scenario defect). On the amaru-only scenario the manifest will read `skipped {…: 4}` instead of `fail {fail: 2, skipped: 2}` for the same evidence. Packages prepared before this commit carry the old `capabilities.json` and must be prepared again to pick it up.

## 2026-11-07/08 — AT-2b (b): the txsubmission probe inside the package, and what it uncovered (F52, F53, F53b)
Order of events, each step live on cardano-box with the package smoke recipe (`scratchpad/pkg_smoke.sh`, `collect_smoke.sh`; DWARF-only compose projects, torn down with volumes).

- **The traceback (smoke `at2v2-amaru-f52`, Amaru 10.11.20260918).** `runtime_txsubmission_probe.py` exited 1 at `run_probe`: `valid handshake proposal did not produce a data response; got timeout`. TCP to `amaru1.example:3000` connected; the proposal got no reply in 2 s.
- **Not a warm-up effect (smoke `f52b`).** A handshake loop inside the workload for 10 minutes after readiness: 20 attempts, all `timeout`, while the Amaru log adopted tips to slot 6181 — the node was converged. The readiness gate still gained local-run parity from this (below), but it could not be the fix.
- **Not package-specific.** The same proposal to the live substrate's Amaru relay (`profile-o…amaru-relay-1`, `/target/amaru` = Amaru 10.11.0 (ea1f34e4), port 3000) timed out for every version table tried (v7, v10, v11–v14) — every one of those frames still carried the probe's responder mode bit, which is what the final smokes identified as the whole cause (below). My interim reading that the 09-18 build "never services inbound connections" was wrong and is withdrawn by smoke `a4`.
- **F52 — readiness parity for Amaru (9bd3571d + bec0143a).** The package gate accepted TCP + bootstrap sentinel; the local compose gate waits for intersect/advance/converge from the Amaru log (`runtime_compose_substrate._wait_for_amaru_progress`). `antithesis_workload.wait_ready` now applies `_parse_amaru_runtime_progress` to the mounted log and stops on a blocking signal. First live try timed out after 2400 s: the control rig runs Amaru with `AMARU_WITH_JSON_TRACES=true` and the parser only knew the key=value text form; it now reads the JSON `chainsync.intersect_found` current/highest and `tip.adopt` slot fields too. Live (`at2v2-smoke-a2`): ready after 685 s with the node converged.
- **AT-2b (a) in-package (smoke a2):** the four assertions came back `skipped` — and the manifest read **`pass {skipped: 4}`** with the probe failed. Vacuous. Fixed in 2854d84f: `txsubmission_window_enforced` was simply unlisted in the matrix (default unsupported) although it only reads the probe's result file → listed as `runs` with `runtime_txsubmission_window_pressure` as its evidence producer; `classify()` refuses a scenario whose assertions are all skipped (`no-evaluable-assertion`, 11 catalog scenarios now refused up front, 7 full / 20 partial remain); `finish()` turns a pass with every assertion skipped into `precondition_failed` with an explicit reason. Live (`at2v2-smoke-a3`, 09-18 Amaru): **`fail {fail: 1, skipped: 3}`**, `txsubmission_window_enforced` fails ("expected negotiated txid window to reject overflow cleanly"), exit 1, SDK `dwarf_scenario_completed` with `exit_status: fail`. Honest.
- **F53 — the probe never negotiated a session (fcecbdab).** Against the live cardano-node 11.1.2 producer the probe's proposal (`8200a10a82182af4` = node-to-node **version 10** only) is answered with `82028200820e0f` = **MsgRefuse VersionMismatch [14, 15]**; a {13, 14} table with four-field version data is accepted (`83010e84182af400f4`). The probe treated any reply as `"data"` and continued, so the window, batch, unexpected-body and mempool-failure modes sent their frames to a node that had already closed: the close was read as "rejected" → vacuous pass for batch/unexpected-body/mempool-failure; the window mode raised one step later. The probe also set the mux mode bit on its own frames (`(1 if initiator else 0) << 31`; the spec marks responder frames with that bit); cardano-node tolerates both settings (tested), Amaru's mux does not route responder-marked frames on an inbound connection — which is the exact silence seen inside the package on Amaru 10.11.20261029 (smoke `b`: `manager.peer.accepted` for the workload's connection, then nothing). The serve-transaction mode already did everything right (own comment: "the helper's `initiator` flag names the responder bit"). Fix: propose versions 13, 14, 15 with `[magic, false, 0, false]`, send every frame as initiator, require MsgAccept and name the versions a refusing node offers, read a clean close as `eof`. `runtime_hardening_probe` and `runtime_exposure_probe` share the constant and the helper and negotiate current versions now; `runtime_amaru_measurement_calibration.build_handshake_frame` dropped its `initiator=False` workaround.
- **F53b — the four `ShimPeerMalformed*` primitives (ca387dc2)** carried the same inverted bit and version-10 proposal, and the blockfetch/txsubmission variants accepted any reply as a session → the same vacuous class. Fixed the same way; tests proven red on the old code.
- **Reproduce via DWARF (F53):** on any host with a cardano-node 10.x/11.x N2N port, `python3 -c 'import sys; sys.path.insert(0,"dwarf"); from scripts.runtime_txsubmission_probe import *; …'` sending `8200a10a82182af4` in a mux SDU to the port returns `82028200820e0f`; the catalog scenarios `client-example-txsubmission-*`, `*-batch-pressure*`, `*-unexpected-body*`, `*-mempool-failure-probe*` run through the local runner or the package and, before fcecbdab, recorded `handshake_response_kind: data` with `handshake_response_hex: 82028200…` in `outputs/txsubmission-window/result.json`.
- **Smoke `at2v2-smoke-b2` — Amaru 10.11.20261029 (23031e0), package built with F53 (fcecbdab), deploy ca387dc2.** Handshake experiment from inside the workload after readiness: every proposal with the responder bit set → `timeout` (Amaru's mux does not route responder-marked frames on an inbound connection); with the initiator bit clear → immediate answers: v10 → `82028200850b0c0d0e0f` (MsgRefuse VersionMismatch, node offers 11–15), v11…v15 → `83010b…`/`…0f…` MsgAccept with four-field data, multi-version tables → the highest common version. The scenario itself: probe `handshake accepted` (v15, `83010f84182af400f4`), request-txids `data`, overflow reply → `eof`; **`txsubmission_window_enforced` pass**, manifest `pass {pass: 1, skipped: 3}`, exit 0. The txsubmission window scenario runs honestly end to end on Amaru 10-29 inside the package; the three producer-dependent assertions are declared skips.
- **Smoke `at2v2-smoke-a4` — Amaru 10.11.20260918, package built with F53.** The same picture as 10-29: responder-marked frames → `timeout` for every version; initiator frames → v10 refused with `82028200850b0c0d0e0f` (node offers 11–15), v11…v15 accepted. Probe `exit 0`, **`txsubmission_window_enforced` pass**, manifest `pass {pass: 1, skipped: 3}`, exit 0. So both Amaru builds serve inbound node-to-node connections and simply drop frames whose mux mode bit claims to come from a responder, while cardano-node tolerates them. The whole AT-2b (b) failure was the probe (F53); nothing in the package or in Amaru needed to change for it.
- **Open:** re-running the local txsubmission/hardening families on cardano-node to re-establish their evidence under a negotiated session; the Amaru handshake refusal of version 10 (`VersionMismatch`) is correct protocol behaviour and no finding.

## 2026-11-08 — Local wiring campaign, first batch: four local-path defects (F54–F57)
Plan `docs/plans/2026-11-08-local-wiring-campaign-plan.md`. The four patched measurement targets were rebuilt with DWARF's trusted builders (19 min, records in `state/measurement-targets`), then 12 of 119 runs executed before I stopped the driver: every run failed within seconds, none in a node or substrate. Scenario runs execute inside `dwarf-controller` (Docker socket, topology runtimes, per-run `docker network connect` to the profile network); `dwarf-fw` has neither the socket nor the controller volume. The 2026-11-05 "local path end to end" covered library runs and four stock profile-bound measurement runs only.

- **F54 — exact-target check refuses every resolved-plan scenario on a patched profile (fixed).** `deployment_versions.build_measurement_target_identity` derives `image_manifest_digest` from the digest-pinned reference (`dwarf/amaru-measurement@sha256:…`) and the run plan freezes it into `runtime_verify_exact_target`; `measurement_execution._patched_amaru_identity` only carried that field for *imported* records and the cardano patched branch never did → `exact target identity mismatch: image_manifest_digest` (run `20261108T065332Z-9d7e5d52`, window scenario on profile-z, 3 s). Fix: both runtime identities carry the reference digest for locally built records; imported records unchanged. Tests: `test_prepare_scenario_measurements_resolves_exact_live_patched_runtime` (expected identity now includes the field), `test_prepare_cardano_measurements_resolves_exact_live_patched_runtime`.
- **F55 — fourteen runtime primitives read a host-era literal path (fixed for 14, docker-mode actions remain host-era for 7).** `ShimPeer{RawBytes,MalformedHandshake,MalformedBlockfetch,MalformedTxsubmission,InvalidCbor}`, `FaultLocalPort{Drop,Delay}`, `FaultNodeFreeze`, `Runtime{ConnectionState,HaskellGcCapture,ResourceProfile,SyscallTrace}` took `params["runtime_metadata_path"]` verbatim (`~/cardano-profiles/<profile>/runtime.json`, the 2026-09 host layout), matched nodes by `name` (controller records key `id`) and connected to `127.0.0.1:<published port>` (the host loopback, unreachable from the controller). Runs `20261108T065426Z…` to `…065701Z…` on profile-i: `shim_peer_* runtime metadata missing`, `fault_node_freeze runtime metadata missing`. Fix: `primitives._resolve_runtime_metadata_path` (prepared runtime → declared path if it exists → `profiles.runtime_root(profile)/runtime.json`, fails closed naming every candidate), `_runtime_metadata_node` (`id` or `name`), `_runtime_node_endpoint` (`container_ip` + `container_listen_address` port; an explicit non-loopback `target_host` still wins; the catalog's `127.0.0.1` is the host-era default). The five shim primitives use all three; the others resolve the path and node. **Remaining host-era behaviour, not changed:** `fault_local_port_*` act with `iptables`/`tc` on the host loopback, `fault_node_freeze` sends SIGSTOP to a host pid, and the four capture helpers read `/proc` of a host pid — inside the controller these now fail at the action with the true reason instead of a phantom path. Porting them to Docker (`pause`, network impairment on the container, `docker exec`/`nsenter` capture) is a sub-project. Tests: `tests/test_runtime_metadata_resolution.py` (13), F53 shim tests adjusted for the `handle` argument.
- **F56 — self-composed mixed devnet fails the Amaru startup gate (fixed).** `runtime-substrate-mempool-failure-containment-mixed-amaru` (resolved-plan, cardano-node 11.1.2 + Amaru 10.11.20260918) resolved its images and died at `_compose_substrate_docker`'s gate with `blocking_signals={'node2': ['handshake-decode-error']}` (run `20261108T065726Z-a1bd6c03`): the compose path never pinned `ExperimentalProtocolsEnabled`, which profile deploys (`qualify_node_versions.cardano_experimental_protocols_policy`, 2026-09-23) and the Antithesis rig renderer (2026-11-07) already do. Fix: `_pin_cardano_experimental_protocols` reuses that policy on the generated `configuration.yaml` for Amaru-bearing devnets with cardano-node ≥ 11.1, honours an explicit `substrate.cardano_experimental_protocols` (new, validated, flows through `normalize_substrate`/`allocate_node_plan`), leaves cardano-only and pre-11.1 devnets byte-identical, and records the decision in `compose-report.json`. Tests in `tests/test_runtime_metadata_resolution.py`.
- **F57 — 126 self-composing scenarios are frozen to the host-devnet model (structural, user decision pending).** 108 `compose_mode: host` + 18 `docker` substrates with `identity_binding: frozen` (the default) resolve nodes through `resolve_binary_for_node` (`/home/dwarf/.local/bin/cardano-node-<ver>` …) or local `dwarf/cardano-node:<ver>` tags; neither exists on a dashboard install, so `runtime_install_version` reports `not_attempted`/`image-missing` and the run ends in 4 s (`20261108T065713Z…`, `…065717Z…`, `…065722Z…`). Only the 15 `resolved-plan` substrates go through `run_plan._scenario_substrate_version_profile` and get catalog images. 112 of 274 primitives are reachable only through frozen substrates. Option a: port the 126 to `resolved-plan` + `docker`, keeping each node's exact version (the mixed-minor scenarios pin old versions on purpose); option b: declare the 112 primitives a local-path gap. The user chose the three fixes and option a; the port is planned separately before any catalog file changes.
- **Not a regression.** The compose mounts, these primitives and the frozen substrates are unchanged since before F38/F45/F51 (git log), and no scenario of these classes had ever run on this installation.

## 2026-11-08 — Second layer after deploy 65baece3 (F58–F60)
Three proof runs through the dashboard on the F54–F56 deploy, each one step deeper than before:

- **F54 proven, F58 found (window scenario, run `20261108T084311Z-0be738d2`).** `runtime_verify_exact_target` matched, the probe negotiated **MsgAccept v15** (`83010f84182af400f4`) to `10.192.7.10:3000` through the prepared runtime, request-txids answered, overflow reply `eof` — the F53 comparison for the Amaru window scenario holds on the local path. Then `runtime_mark_hostile_window` failed: `hostile measurement window requires exactly one start and one end marker`. The `measurements/windows.ndjson` has `run`, `default` and `baseline` markers only: `runtime_protocol_decode_cases` writes `hostile` start/end around its workload, the four `runtime_txsubmission_*`/`runtime_mempool_failure_probe` primitives never did, and the window scenario is the only hostile-window scenario built on the probe family. Fix: the probe base writes the same markers whenever a measurement runtime is active (none → nothing to mark). Tests: `test_txsubmission_window_pressure_marks_the_hostile_window_around_the_probe`, `test_txsubmission_probe_without_a_measurement_runtime_marks_nothing_and_still_runs`.
- **F55 proven, F59 found (profile-i shim scenario, run `20261108T084401Z-b5f1f89b`).** `shim_peer_malformed_blockfetch` resolved the controller's retained runtime, connected to `10.192.8.2:3001`, got **MsgAccept v15**, sent the malformed BlockFetch frame and the node **reset** the connection — the primitive did its job. The scenario still failed on `load_events_are_ok`: `completed 1, required_completed 2`. The assertion took `max(min_completed, min_event_count)` with `min_event_count` defaulting to 2 as the number of *completed* events, so a single-shot load (one started, one completed) could never pass; 75 catalog scenarios have exactly one load primitive. The 11-05 measurement runs passed only because they set `min_event_count: 3` and their load emits three completions. Fix: `min_event_count` guards the number of load-phase events (non-vacuity), `min_completed` the completed-with-outcome count; the explicit 3/3 contract is unchanged. Tests in `tests/test_local_path_window_and_load_events.py`.
- **F61 — a deploy dies on one failed GitHub download.** Deploying a04a9c75 failed twice in the controller image build: `urllib.error.HTTPError: HTTP Error 504: Gateway Time-out` fetching `cardano-node-11.1.2-linux-amd64.tar.gz` from GitHub releases, which answered 504 from the box and from the Mac for about an hour (08:50–09:16Z). The sha256-pinned fetch had one attempt. Fix: five attempts with a 30 s × attempt backoff, honest failure after the fifth, checksum gate unchanged (`test_controller_image_retries_transient_support_archive_failures`). The release workflow builds the same Dockerfile, so it gains the same tolerance.
- **Third layer on deploy 89848611 (proof runs `20261108T0942…`):** the profile-i shim scenario **passed** (`20261108T094407Z-fca80561`, 1/1) — F55 and F59 proven on a dashboard-deployed profile. The window scenario cleared the exact-target check, the probe (MsgAccept v15, overflow `eof`), the hostile and recovery windows, then died in the probes: **F62** — `runtime_target_health_and_progress` and `runtime_peer_session_health` read the `target_health`/`peer_session` sections that `runtime_protocol_decode_cases` writes; the txsubmission probe family never produced them, and the window scenario is the only hostile-window scenario built on that family. Fix: with `evidence_capture: true` in prepared-runtime mode the probe family records both sections around its probe, built from the prepared target container and the runtime's independent honest peer (the calibration leg's `_independent_peer`) and observed with the same `_observe_protocol_target`/`_observe_protocol_peer` the probes re-observe with; the window scenario gets `evidence_capture: true` on its load and `report_path: outputs/txsubmission-window/result.json` on both probes (the probe schemas already allowed `report_path`; the probe-family schema gains `evidence_capture`). The mixed self-composed scenario passed the compose gate (F56+F60 effective: `all_nodes_responsive` pass) and failed in `runtime_mempool_failure_probe`: **F63** — in substrate-runtime mode `runtime_txsubmission_probe.py` connected to `127.0.0.1:<published port>` (`ConnectionRefusedError`); like the F55 shim fix, `_node_endpoint` now prefers the node's `container_ip` + `container_listen_address` port, an explicit non-loopback `target_host` keeps the published port, and Amaru nodes are looked up too. Tests: `tests/test_local_path_probe_evidence.py` (4).
- **Regression check of the 11-05 runs:** a plain request for those three scenarios now resolves the catalog image and the dashboard correctly asks for a destructive **REDEPLOY** (`observed_identity_drift`: profile-n runs the operator-built `dwarf/release-cardano-node` image, profile-o the 10-22 Amaru under `latest-confirmed`). Not a regression: the 11-05 runs were requested with `operator_registration_id` + `measurement_profile`, so the rerun uses the exact request bodies retained in their launch plans.
- **F64 — a healthy two-day-old Amaru profile looks undeployed.** With the original request bodies the 11-05 Amaru runs still asked for a redeploy: the controller's `topology.health` for profile-o and profile-r failed outright, so the dashboard saw no persisted plan (`persisted_mismatched_fields` = every field) and chose `redeploy`. Cause: the Amaru relays had accumulated **8.1 GB and 7.5 GB** of JSON trace logs (`AMARU_WITH_JSON_TRACES`, consensus=debug; 31 GiB across all profile containers, no rotation) and `runtime_amaru_control_substrate._compose_logs` read the whole project log (`docker compose logs --no-color --timestamps`, no bound): 7 GB in 60 s and still streaming, past the controller's 120 s request cap. `docker logs --tail 8000` on the same container answers in 70 ms. Fix: the health/evidence read takes `--tail 20000` (proven bootstrap evidence is remembered, so a tail is enough for the signals a health check classifies), and the pre-redeploy evidence capture in `redeploy_cardano_amaru_topology` is bounded to 50000 lines. Test: `test_compose_logs_are_read_with_a_bounded_tail`. Open, for the user: the generated compose models set no log rotation, so a relay's log grows without bound (disk) — a `json-file` `max-size`/`max-file` policy would cap it but also caps how far back trace-based evidence can reach; not changed here.
- **F65 — container log rotation for locally generated runtimes (user: "let's set log rotation").** `runtime_substrate_common.CONTAINER_LOG_ROTATION` (json-file, `max-size 512m`, `max-file 8` ≈ a day of Amaru debug trace per relay) is applied by `_docker_compose_body` (self-composed and controller-owned cardano devnets) and by `prepare_runtime_model` (Amaru-control profile runtimes) after the shared transform; the shared `transform_compose_model` is also what Antithesis packages use and keeps the platform's driver (tested). Only newly deployed runtimes get it: a running container's logging driver cannot change, so profile-o/profile-r keep their 8 GB logs until redeployed.
- **F66 — the controller never joined a run-owned substrate network (mixed scenario, run `20261108T104435Z-822db488` on 87197ee6).** With F63 the mempool probe addressed node2's container IP and **timed out**: the controller connects itself to a *profile's* network for profile-bound runs (`_attach_run_network`), but a self-composed substrate has no profile and its network is created during the run, so nothing attached the controller (before F63 the published loopback port was refused instead — unreachable either way). Fix: `runtime_compose_substrate._attach_controller_to_substrate_network` joins the controller (`HOSTNAME`) to `<project>-net` right after `compose up` when the substrate is controller-owned, records `controller_attachment` in `runtime.json`, and `runtime_teardown_substrate` disconnects it before `compose down` (a network with an attached container cannot be removed). Host runs attach nothing. Tests: `tests/test_local_path_substrate_network.py` (4).
- **F67 (OPEN, evidence validity) — Amaru drops an inbound N2N peer it has no local use for, right after the handshake; the window verdict was a race.** Window run `20261108T104345Z-44405730` on 87197ee6: Amaru accepted the handshake, logged `manager.peer.local_use_applied local_use=none`, then `manager.peer.connection_died` / `connection_died_handled outcome=peer_removed` within 0.3 ms, so the probe's request-txids read `eof` and it failed honestly. The relay's log for the **served** probe at 09:43 (`20261108T094256Z-9582955a`) shows the **same** three events at the same spacing; that probe merely read Amaru's first request-txids before the close landed and then recorded the close as `txsubmission_response_kind: eof` = "overflow rejected". The in-package smokes b2/a4 of 2026-11-07/08 report the identical signature (`request-txids data`, overflow reply `eof`). So on Amaru the `txsubmission_window_enforced` pass is not evidence that the txid window is enforced: the session would have closed whatever the reply. Needed before any claim: a control probe that answers *inside* the window and shows whether the session survives a second request-txids; if Amaru closes regardless, the assertion must demand a protocol-level refusal on Amaru or declare the window check unsupported for Amaru. Not changed in this pass; the earlier "window scenario passes on both Amaru builds" statements are withdrawn to "the probe negotiates and Amaru closes the session; the window itself is unproven".
- **Deploy bfa87d7c (F62, F63, F65, F66) — live results.** Window scenario `20261108T112027Z-a1d7a7e1`: **pass 4/4** — exact target matched, probe MsgAccept v15 → request-txids data → overflow `eof`, hostile window marked, and the F62 evidence is in the result (`target_health` tip 6774→6775 with the relay observer, `peer_session` amaru-consumer usable before/during/after) so `target_progress_continues`, `unrelated_peer_session_usable` and `no_target_fatal_signal` evaluate on real evidence. **Caveat F67 stands:** `txsubmission_window_enforced` passed because the probe read Amaru's first request before the post-handshake close this time. Mixed self-composed scenario `20261108T111755Z-a654c1c2`: F66 proven — `runtime.json` records `controller_attachment` and `runtime_mempool_failure_probe` reached node2 at `10.192.9.2:5001` (exit 0, `node_stayed_up: true`, `protocol_session_survived: true`); `mempool_failure_contained` fails on `fatal_error_contained: false` — the next layer, under analysis. Regression on the same deploy line: `amaru-measurement-workload-stock-portable` pass 5/5 (profile-o, 1472 s), `amaru-measurement-e2e-stock` pass 1/1 (profile-r), both with their 11-05 request bodies. Signed bundles of all these runs are on DevStorage.
- **Campaign restart 12:50Z on bfa87d7c (user-approved) — 12 runs, 6 pass / 5 fail, stopped for fixes.** The five F53 shim scenarios on profile-i and the window scenario passed. Failures, each a wiring defect, none a node behaviour:
  - **F68** `FaultLocalPortDrop._rules` and `FaultNodeFreeze._resolve_target_pid` referenced `handle` out of scope (my F55 edit) → `NameError` on every freeze/port-drop fault. Fixed by threading the handle; tests `test_port_drop_rules_read_the_target_port_through_the_handle`, `test_node_freeze_pid_resolution_reads_the_node_through_the_handle`.
  - **F69** `runtime-substrate-byzantine-blockfetch-example-smoke` and `runtime-substrate-honest-baseline-example-smoke` declared `runtime: library` while composing a substrate (the registry refuses; the gate only warned because the primitive is on the legacy warning list) and had **no substrate block at all** (the primitive requires `scenario.substrate`) — unrunnable by construction. Authored: `runtime: devnet`, a three-node cardano 10.7.1 `testnet_42` docker substrate with full-mesh topology (roles honest; the byzantine behaviour is the shim peer, as the assertions already assume), `runtime_install_version` + compose in `setup`, `runtime_teardown_substrate` in `teardown`. Gate PASS.
  - **F70 (dominant: 101 of the campaign's substrate nodes are cardano 10.7.1)** `cardano-node 10.7.1` exited with `Error in creating the NodeConfiguration: Missing LogMetrics`: the `TurnOnLogMetrics` compatibility step for releases < 11 ran only on the custom-testnet branch; the `network: preview` scenarios copy the public-network config and got neither it nor the interop pin. Fixed with `_apply_cardano_config_policies` on all three branches (custom testnet, docker public, multihost public), recorded in the compose report. Version-agnostic: the predicate is the release major, the pin is the shared policy.
  - **F71** 48 primitives default their helper to `/home/dwarf/dwarf-fw/scripts/<helper>.py` and scenarios declare `~/cardano-profiles/<profile>/...`, `~/dwarf-fw/runs`, `/opt/dwarf/cardano-profiles/...` (host layout; inside the controller scripts live at `DWARF_ROOT/scripts`, runs at the storage runs root, profile runtimes at `profiles.runtime_root`). `_resolve_runtime_path` now maps the legacy layout onto the installed one (`_resolve_legacy_host_layout_path`), every helper_script / runtime_root / runs_dir read goes through it, and the F55 declared-path step too. `test_every_helper_script_default_resolves_to_an_installed_script` proves all 33 helper scripts exist under the installed layout. Shell steps get `DWARF_SCRIPTS_DIR`, `DWARF_RUNTIME_METADATA_PATH`, `DWARF_PROFILE_RUNTIME_ROOT` and `DWARF_ROOT/scripts` on `PYTHONPATH`; the two capability-demo scenarios' inline python reads them (legacy fallback kept).
  - **F72 (declared gap)** `runtime-substrate-cardano-node-mixed-minor-*` (two cardano releases 10.6.1 + 10.7.1 in one substrate) fail `runtime_install_version node_count=2 satisfied=false`: the resolver binds one release per implementation (F57). Removed from the campaign (117 runs), listed as a declared gap with the opcert primitives.
  - **F73** a failed post-`up` verification left the crash-looping containers and the controller's network attachment behind (network undeletable, disk growth — the user's exact concern). `_compose_substrate_docker` now tears the substrate down (detach controller, `compose down --volumes --remove-orphans`) and reports it in the error. The campaign sweep also lowercases the project name (compose does) and detaches the controller first; 7 leftover containers and 2 stuck networks from the failed runs were removed by hand.
- **F67 follow-up:** the window scenario passed again in the campaign (`20261108T125037Z-350a0fba`), still subject to the race caveat.
- **profile-n stalled by my runner (10:16Z).** The regression runner inherited the campaign driver's automatic confirmation of previewed destructive topology actions and confirmed a `repair` of profile-n (observed image drift: the deployed node runs the operator-built `dwarf/release-cardano-node` image while a plain request resolves the catalog image). The repair recreated the producers; all three sit at block 29733 / slot 595606 with `syncProgress 24.47` and do not advance (`node_chain_not_synchronized`). One-off runners no longer confirm destructive actions (`allow_destructive=False`); restoring profile-n needs a redeploy, which is the user's call.
- **F56 not yet effective, F60 found (mixed self-composed scenario, run `20261108T084405Z-72cee9e0`).** The pin was applied (`configuration.yaml` written at create-env time), then `runtime_amaru_bootstrap_synth` rewrote `configuration.yaml` from the producer image's pinned `config.json` 85 s later (`ExperimentalProtocolsEnabled: true` again) and node2 logged `failed to decode message from network err=decode error: expected array length 4 for label 0, got: Some(5)` — the V16 five-field handshake. Fix: the pin is re-applied after the bootstrap synthesis (a network setting, not chain state), last before `docker compose up`.
