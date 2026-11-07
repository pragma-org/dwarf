# AT-2 Catalog Parity v2 (scenario-rendered rig) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the first AT-2 build's fixed-rig projection with a package that renders the scenario's own devnet (its nodes, roles, edges, versions) from the control rig's run-time building blocks, so a cardano-only test starts only Cardano containers, an amaru-only test starts its Amaru nodes plus one supporting producer, and mixed tests start exactly their declared nodes.

**Architecture:** A new `antithesis_generated_rig` module turns a scenario into a devnet description (`scenario_devnet`), renders a compose from the vendored `cardano_amaru_relay_bootstrap_control` building blocks (configurator with `poolCount = N`, tracer, log-tailer, one producer per cardano node, the `amaru-relay-bootstrap` wrapper per Amaru node), and binds the selected releases per node. `bind_generated_workload` attaches `dwarf-workload` to that rig; the workload's readiness gate gains local-run parity (real block over the socket, Amaru bootstrap sentinel). The projection, template and victim-clone code of the first build is deleted. Everything downstream (entrypoint, SDK, skip policy, capabilities plumbing, package-mode primitives, build/push, results) is unchanged.

**Tech Stack:** Python 3.11+ (stdlib + PyYAML), pytest; Docker compose v2 on cardano-box for the live proof.

**Spec:** `docs/plans/2026-11-05-antithesis-track-parity-design.md` — Piece 2 "AT-2 corrected (2026-11-07)" and the corrected faultable-SUT policy.

## Facts verified in code 2026-11-07

- The native Docker runtime compose (`runtime_compose_substrate._docker_compose_body`) is not shippable: genesis comes from `cardano-testnet create-env` on the controller host, Amaru bootstrap state from host-side synthesis, start times refreshed at compose time, all bind-mounted from host paths. In-environment generation exists only in the rig: the Cardano Foundation `configurator` image (`/configurator.sh`: `genesis-cli.py testnet.yaml`, one `/configs/<i>` per pool for `poolCount`, ring topology per pool that a bind-mounted `topology.json` overrides, `set_start_time = now`) and the `amaru-relay-bootstrap` wrapper (bootstraps from `/live` = a producer's ChainDB with `bootstrap-producer`, needs ≥ 3 completed epochs, then `exec $AMARU_BIN run --network --ledger-dir --chain-dir --era-history --peer-address $AMARU_PEER`; no listen/submit flags, default N2N port 3000, no submit API).
- Every cardano node in a DWARF devnet is a producer (`_docker_compose_body` gives each one pool keys; `cardano-testnet` pool count = node count). Roles (`honest`, `observer`, `submitter`, `sybil`, …) are scenario semantics, not producer/relay.
- Devnet catalog: 310 scenarios; 141 substrate-declared (123 on `preview`, 18 on `testnet_42`, shapes up to 4 cardano + 3 amaru), 156 profile-based (19 on public `preview` profiles; the rest `testnet_42` local-mesh: cardano 3, amaru-only 0+1, mixed 2+1 and 1+1), 13 attach. Substrate nodes never declare a per-node version. Explicit fault primitives with a `target_node` are rare; platform faults are the fault source.
- `compile_topology` pins `poolCount == 3`; the generated rig renders its own `testnet.yaml` and never calls `compile_topology`/`resolve_topology`. `harness.bind_versions` refuses any compose without both Amaru relays; the generated rig has its own binder. `transform_compose_model` (qualify_node_versions) holds the proven modern-Amaru assembly (`amaru-target-extract` + launcher); it is refactored into a reusable helper, behaviour unchanged. `antithesis_harness._EXTRACTION_COMMAND_SHA256` pins the extraction command text, which embeds `launcher_script()`; Task 3 changes the launcher and re-traces that pin.
- `bind_measurements` assumes the curated evidence volume names (`sm-<impl>-runtime-evidence`) and a `/configs` + `/tracer` mount on cardano targets; the rig keeps those mounts and the workload binding publishes `evidence_volumes` per service.

## Global Constraints

- Extend, never reinvent: the rig blocks come from `antithesis_topology._source()` (digest-checked), the Amaru assembly from `qualify_node_versions`, release selection from `antithesis_artifacts.binding_selection` + `antithesis_versions.stock_image_candidates`, tip checks from `runtime_multi_node_observation._query_tip_once`.
- Curated package byte-identical (all `SOURCE_SHA256` pins, inventory); attach scenarios keep the mixed rig path of the first build.
- `setup_complete` only after readiness; property ids `dwarf_*`; capability matrix file unchanged except wording.
- TDD; git author `JG <forky@1timeuse.com>`; no attribution trailers; no push.
- Test command: `PYTHONPATH=dwarf /opt/homebrew/bin/python3.14 -m pytest -q -p no:cacheprovider <files>`.

## Review Focus

1. A profile-a scenario (3 cardano producers, target cardano-node) renders exactly `configurator`, `tracer`, `log-tailer`, `node1..node3`, `dwarf-workload` and nothing Amaru; `testnet.yaml` says `poolCount: 3`; all three producers are faultable (Task 2, 4).
2. An amaru-only profile renders `bootstrap-cardano` (fault-excluded, supporting, catalog default cardano release) + `amaru1` bootstrapping from it; the version binder asks for no cardano-node selection (Task 2, 3).
3. A substrate on `preview` classifies `none` with `public-network` and renders no compose; the readiness row blocks (Task 1, 5).
4. Readiness must not pass on TCP alone: a listening port with no block and no sentinel keeps the gate waiting until the timeout, then exit 3 with no `setup_complete` (Task 4).
5. The curated miniprotocol and the 13 attach scenarios preview exactly as before (existing tests), and `cardano-amaru-miniprotocol-security-local` never takes the rig path (Task 5).

---

### Task 1: Scenario devnet description and the public-network refusal

**Files:**
- Create: `dwarf/profile_manager/antithesis_generated_rig.py` (first part)
- Modify: `dwarf/profile_manager/antithesis_capabilities.py` (`classify`)
- Test: `tests/test_antithesis_generated_rig.py`, `tests/test_antithesis_capabilities.py`

**Interfaces:**
- Produces `scenario_devnet(scenario) -> dict` with keys `shape` (`'cardano-only' | 'amaru-only' | 'mixed' | 'attach' | 'none'`), `network` (str or None), `nodes` (list of `{id, impl, role, supporting: bool}`), `edges` (list of `{from, to}`), `target` (implementation str), `faultable` (list of node ids), `issues` (list of `{code, field: 'scenario_id', kind: 'implementation-gap', message}`).
  - substrate: nodes as declared (`supporting False`), edges as declared, empty edges → full mesh; `network` from `substrate.network`; a network not starting with `testnet_` → issue `public-network`, shape `none`.
  - profile: `find_profile`; `profile.public_network` → issue `public-network`; nodes `node1..N` (`cardano-node`, `producer`), `amaru1..M` (`amaru`, `consumer`), amaru-only adds `bootstrap-cardano` (`cardano-node`, `bootstrap-producer`, `supporting True`) first; edges full mesh; unknown profile → issue `profile-unknown`, shape `none`.
  - attach `cardano_amaru` → shape `attach`, nodes = the first build's attach list, edges `[]`.
  - `faultable` = ids with `impl == target` and not supporting; empty on a devnet shape → issue `no-faultable-sut`, shape `none`.
  - shape: `mixed` if both implementations among non-supporting nodes else by the one present.
- `classify(scenario)` gains reason `public-network` (level `none`, before the primitive walk) when `scenario.runtime == 'devnet'` and `scenario_devnet(scenario)['issues']` contains `public-network`.

- [ ] **Step 1: Failing tests** in `tests/test_antithesis_generated_rig.py` (fixtures: `body()`/`substrate()` helpers moved from `tests/test_antithesis_generated_workload.py`): substrate 2 cardano + 1 amaru target cardano → shape `mixed`, faultable `['node1','node2']`, edges as declared; substrate `network: preview` → shape `none`, issue code `public-network`; profile `profile-a-haskell-peersharing-disabled` → 3 producers, shape `cardano-only`, 6 mesh edges; profile `profile-z-amaru-20260918-nanoseconds-v3` → `bootstrap-cardano` supporting first, `amaru1`, shape `amaru-only`, faultable `['amaru1']`; profile `profile-e-haskell-preview-proof` → `public-network`; attach scenario `cardano-amaru-relay-bootstrap-control-local` → shape `attach`; substrate cardano-only with target amaru → `no-faultable-sut`. In `tests/test_antithesis_capabilities.py`: `classify(preview substrate scenario)['reason'] == 'public-network'` and level `none`.
- [ ] **Step 2: Run** — Expected: FAIL (`ImportError`).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** both files — Expected: PASS.
- [ ] **Step 5: Commit** `feat(antithesis): scenario devnet description with public-network refusal`

### Task 2: Render the scenario rig from the control rig's blocks

**Files:**
- Modify: `dwarf/profile_manager/antithesis_generated_rig.py`
- Test: `tests/test_antithesis_generated_rig.py`

**Interfaces:**
- Produces `render_rig(devnet, scenario_id) -> {'compose': dict, 'files': {relpath: text}, 'services': {node_id: node_id}, 'source_revision': str}`; raises `antithesis_topology.TopologyError` when the rig source changed (`_source()` failure) or the devnet shape is not `cardano-only | amaru-only | mixed`.
  - `files`: `environment/docker-compose.yaml` (dumped from `compose`, `sort_keys=False`), `environment/testnet.yaml` (source text with the single `poolCount: 3` line replaced by `poolCount: <N>`), `environment/tracer-config.yaml` (source), `environment/topology/<id>.json` per cardano node (`localRoots[0].accessPoints` = `{address: '<peer>.example', port: 3001 | 3000}` for each edge from the node, `valency` = count, `advertise false`, `trustable true`, `publicRoots []`, `useLedgerAfterSlot 0`), `environment/amaru-runtime/era-history.json` + `global-parameters.json` (source) when any Amaru node.
  - `compose['services']`: `configurator` (source service; volumes `./testnet.yaml:/testnet.yaml:ro` + `<id>-configs:/configs/<i>` per cardano node in slot order; command text with `/configs/[123]` replaced by `/configs/*`); `tracer`, `log-tailer` (source); per cardano node `<id>`: deep copy of source `p1` with `container_name <id>`, `hostname <id>.example`, volumes `[<id>-configs:/configs:ro, ./topology/<id>.json:/configs/configs/topology.json:ro, <id>-state:/state, <id>-evidence:/evidence, tracer:/tracer]`, `depends_on` `configurator: service_completed_successfully`, `tracer: service_started`; per Amaru node `<id>`: deep copy of source `amaru-relay-1` with `container_name`, `hostname`, `environment.RELAY_NAME = <id>`, `AMARU_PEER = '<producer>.example:3001'` (producer = the first edge target with impl cardano-node, else the first cardano node), `AMARU_RUN_EXTRA_ARGS = '--listen-address 0.0.0.0:3000 --submit-api-address 0.0.0.0:3011'`, volumes `[<producer>-state:/live:ro, <producer>-configs:/cardano/config:ro, ./amaru-runtime:/amaru-runtime:ro, amaru-startup:/startup, <id>-state:/srv/amaru, <id>-evidence:/evidence]`, `entrypoint ['/bin/bash','-euo','pipefail','-c']`, `command ['exec > >(tee -a /evidence/amaru.log) 2>&1\nexec amaru-relay-bootstrap\n']`, `depends_on {<producer>: service_started}`, no `networks` key.
  - Every service: `platform: linux/amd64`, `init: true`; labels `com.antithesis.exclude_from_faults: network,kill,pause,stop` on every service except the ids in `devnet['faultable']` (those get `labels: {}`).
  - `compose['volumes']`: `tracer`, `<id>-configs`, `<id>-state`, `<id>-evidence` per cardano; `<id>-state`, `<id>-evidence` per Amaru; `amaru-startup` when any Amaru (all `{}`); `compose['networks'] = {'default': {'name': 'dwarf-<scenario_id>', 'driver': 'bridge', 'internal': '${INTERNAL_NETWORK:-false}'}}`.
  - `source_revision` = the `_source()` revision digest.

- [ ] **Step 1: Failing tests**: profile-a → services exactly `{configurator, tracer, log-tailer, node1, node2, node3}`, `testnet.yaml` contains `poolCount: 3` once, configurator mounts three config volumes and its command contains `/configs/*`, `node2` topology file lists `node1.example` and `node3.example`, no service has `labels['com.antithesis.exclude_from_faults']` for node1..3 and all support services have it; amaru-only profile → `bootstrap-cardano` fault-excluded, `amaru1` with `AMARU_PEER == 'bootstrap-cardano.example:3001'`, `/live` from `bootstrap-cardano-state`, `amaru-runtime` files present; mixed substrate (node1 cardano, node2 amaru, edges both ways) → `node2.AMARU_PEER == 'node1.example:3001'`; a shape `none` devnet → `TopologyError`; monkeypatched `_source` raising → `TopologyError`.
- [ ] **Step 2: Run** — Expected: FAIL (`AttributeError: render_rig`).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat(antithesis): render the scenario devnet from the control rig blocks`

### Task 3: Per-node version binding (releases, modern Amaru assembly, launcher args)

**Files:**
- Modify: `dwarf/scripts/runtime_amaru_cli.py` (`launcher_script`: `run|bootstrap` branch appends `${AMARU_RUN_EXTRA_ARGS:-}` unquoted after `"$@"` for `run` only)
- Modify: `dwarf/scripts/qualify_node_versions.py` (extract `amaru_target_extract_service(amaru_image, scope) -> dict` from `transform_compose_model` lines 370-406; `transform_compose_model` calls it)
- Modify: `dwarf/profile_manager/antithesis_harness.py` (`_EXTRACTION_COMMAND_SHA256` re-traced; comment names the launcher change)
- Modify: `dwarf/profile_manager/antithesis_generated_rig.py` (`bind_rig_versions`)
- Test: `tests/test_antithesis_generated_rig.py`, `tests/test_runtime_amaru_cli.py` (new or existing), existing `tests/test_antithesis_amaru_binding.py`, `tests/test_qualify_node_versions*.py` must stay green

**Interfaces:**
- Produces `bind_rig_versions(files, intent, devnet, rendered, *, scenario_default=None) -> dict` with the `harness.bind_versions` report shape (`status`, `source: 'generated-rig'`, `policy`, `catalog_digest`, `selected_releases`, `services`, `issues`, `compatibility: 'not-evaluated'`, `runtime_identity: 'unverified'`, `target_contract`, `release_resolution`, `node_artifacts`):
  - `targets` = implementations of non-supporting nodes; `binding_selection(catalog, intent, targets, scenario_default=)`; same gap codes as the harness binder (`patched-build-not-implemented` when `build_variant != 'stock'` without artifacts for every target, `version-catalog-unavailable`, `missing-exact-version`, `ambiguous-stock-images`/`missing-stock-image`, `amaru-runtime-interface-not-implemented`, `unverified-image-reference`, `unused-target-selection`).
  - cardano node: `compose.services[id].image = reference`; supporting `bootstrap-cardano`: release = `version_catalog.resolve_default(catalog, 'cardano-only')['release']`, row `supporting: True`.
  - Amaru node, modern (`artifacts[amaru].runtime_interface == 'extracted-binary'` or `uses_modern_amaru_runtime_interface(reference)`): `image = CONTROL_AMARU_IMAGE`, env `AMARU_BIN=/target/amaru-launch`, `AMARU_MIGRATE_CHAIN_DB=true`, volume `{type: volume, source: amaru-target-bin, target: /target, read_only: true}`, `depends_on[amaru-target-extract] = {condition: service_completed_successfully, required: true}`; one `amaru-target-extract` service from `amaru_target_extract_service(reference, scope)` with `platform`, `init`, `container_name`, `hostname`, label fault-exclusion; volume `amaru-target-bin: {}`. Legacy: `image = reference`, env `AMARU_BIN`/`AMARU_MIGRATE_CHAIN_DB` removed, `AMARU_RUN_EXTRA_ARGS` removed (no submit API).
  - `services` rows: `{service: id, role, implementation, baseline_image: None, image, status: 'applied', requested_version, source_revision, provenance: 'catalog-declaration', artifact, supporting}` plus `target_binary {mode, image, path, source_service}` for Amaru and the `amaru-target-extract` row (`role: 'artifact-source'`).
  - `target_contract = {scope: devnet['shape'], services: devnet['faultable'], template_revision: rendered['source_revision'], evidence: 'emitted-configuration', bootstrap_policy: 'in-package genesis; selected Amaru binary is the relay runtime', assembly_source_digest}`.
  - `status`: `applied` when every target bound and no unused selection, `partial` when some, `recorded` otherwise; writes `files[COMPOSE]` when anything applied.

- [ ] **Step 1: Failing tests**: launcher text contains `${AMARU_RUN_EXTRA_ARGS:-}` only on the `run` path; `amaru_target_extract_service('img@sha256:…','mixed')` equals the dict `transform_compose_model` emits (compare against a call on a minimal baseline); harness `_assembled_amaru` still accepts the transformed rig (existing amaru binding tests); `bind_rig_versions` on the profile-a rig with the `catalog` fixture (`selection('cardano')`-style exact intent for cardano-node) → all three producers carry the release's stock reference, status `applied`, no Amaru row; amaru-only rig with exact amaru `10.11.20260918` → `amaru1.image == CONTROL_AMARU_IMAGE`, `amaru-target-extract` present with the selected reference, `bootstrap-cardano` bound to the catalog default with `supporting True`; exact policy without an amaru selection on a mixed rig → `missing-exact-version` gap, status `partial`.
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement**; re-trace `_EXTRACTION_COMMAND_SHA256` by computing the new command digest in the test (`test_extraction_pin_matches_launcher`).
- [ ] **Step 4: Run** new tests + `tests/test_antithesis_amaru_binding.py tests/test_antithesis_version_binding.py tests/test_qualify_node_versions*.py tests/test_runtime_amaru_cli*.py` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(antithesis): bind releases per scenario node on the generated rig`

### Task 4: Workload on the rig, measurement evidence volumes, readiness parity

**Files:**
- Modify: `dwarf/profile_manager/antithesis_generated_workload.py` (delete `TEMPLATE`, slot constants, `template_for`, `project_nodes`, `resolve_generated_topology`, `scenario_nodes` profile/substrate branches; keep the attach projection as `attach_projection(scenario)`; `runtime_metadata(scenario, devnet, version_binding, compose)`; `bind_generated_workload(files, scenario, version_binding, classification, *, devnet, rendered=None)`)
- Modify: `dwarf/profile_manager/antithesis_workload_binding.py` (delete `add_victims`; keep `add_clone_services`, `rebind_victims`, `VICTIM_SOURCES`, `CLONE_VOLUMES` for the attach path)
- Modify: `dwarf/profile_manager/antithesis_measurements.py` (`evidence = workload.get('evidence_volumes', {}).get(service) or 'sm-…-runtime-evidence'`)
- Modify: `dwarf/profile_manager/antithesis_workload.py` (`wait_ready` cardano: TCP + socket + `_query_tip_once` real progress; Amaru: TCP + `bootstrap_sentinel` exists), `dwarf/profile_manager/data/antithesis-capabilities-v1.json` (wording: "mounted node log", not "victim log")
- Test: `tests/test_antithesis_generated_workload.py` (rewritten), `tests/test_antithesis_measurements.py`, `tests/test_antithesis_workload_entrypoint.py`

**Interfaces:**
- `runtime_metadata`: node records `{id, name: id, impl, implementation, role, supporting, version, image, listen_address '<id>.example:<3001|3000>', port, container_listen_address, socket_path '/inputs/<id>/node.socket' | None, log_path '/inputs/<id>/amaru.log' | None, submit_api_url 'http://<id>.example:3011/api/submit/tx' (modern Amaru only, else None), bootstrap_sentinel '/inputs/<id>-state/.bootstrap-complete' (Amaru) | None, observation_transport 'antithesis-package'}`; `topology.edges = devnet['edges']`.
- `bind_generated_workload` on a rig: mounts `<id>-state:/inputs/<id>:ro` (cardano), `<id>-evidence:/inputs/<id>:ro` + `<id>-state:/inputs/<id>-state:ro` (Amaru), `dwarf-workload-evidence:/evidence`; `depends_on` every node service `service_started` and `configurator` `service_completed_successfully`; env `DWARF_ANTITHESIS_READY_TIMEOUT` `'900'` without Amaru, `'2400'` with; report gains `evidence_volumes {id: '<id>-evidence'}`, `targets = devnet['faultable']`, `template: 'scenario-rig'`, `rig: {shape, source_revision}`; `projection` only for attach. Attach path unchanged from the first build.
- `wait_ready(runtime, timeout, *, cardano_cli=None, …)`: a cardano node is ready when TCP answers, the socket exists and `_tip_has_real_chain_progress(_query_tip_once(cardano_cli, socket_path, network_magic))`; an Amaru node when TCP answers and `Path(bootstrap_sentinel).exists()` (sentinel None → TCP only). `after_prepare` passes `runtime['support_binaries']['cardano-cli']` and `runtime['network_magic']`.

- [ ] **Step 1: Failing tests**: profile-a rig binding → workload mounts `node1-state:/inputs/node1:ro` ×3, `depends_on['node1'] == {'condition': 'service_started'}`, `depends_on['configurator']`, `READY_TIMEOUT == '900'`, `targets == ['node1','node2','node3']`, `evidence_volumes['node1'] == 'node1-evidence'`; amaru-only → `amaru1` record has `bootstrap_sentinel`, `log_path`, `submit_api_url` (modern) and the workload mounts `amaru1-evidence:/inputs/amaru1:ro` and `amaru1-state:/inputs/amaru1-state:ro`, timeout `'2400'`; `bind_measurements` with `evidence_volumes` uses `node1-evidence` for the trace mount; `wait_ready` with a listening port but a tip fake returning `{'block': 0}` keeps waiting then raises; with `{'slot': 5, 'block': 1, 'hash': 'ab'}` returns; Amaru node with the port open and no sentinel raises at the deadline; attach scenario tests from the first build still pass.
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the three files + `tests/test_antithesis_miniprotocol_binding.py` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(antithesis): dwarf-workload rides the scenario rig with local-run readiness parity`

### Task 5: Preview takes the rig path; projection code removed; docs

**Files:**
- Modify: `dwarf/profile_manager/antithesis_package.py` (`preview_package` lines 206-330: generated devnet shapes → `render_rig` + `bind_rig_versions` + `bind_generated_workload(devnet=…, rendered=…)` with `control=None`; attach → existing control path; shape `none` → `workload_binding` recorded with the devnet issues, no compose; handoff `topology` row text; `generator_source_digest` from the rig module when the rig path ran)
- Modify: `dwarf/profile_manager/antithesis_submission.py` (`generated_selections` unchanged; readiness `scenario_capabilities` error text for `public-network`: "This scenario runs on a public network; Antithesis has no internet.")
- Modify: `dwarf/dashboard/static/js/antithesis-package.js` only if the capabilities text needs the new reason (verify; no change expected)
- Modify: `docs/plans/2026-11-06-antithesis-catalog-parity-plan.md` (header note: superseded by this plan for Tasks 4-5), `docs/plans/2026-11-05-e-pass-validation-log.md` (section for Task 6)
- Test: `tests/test_antithesis_package.py`, `tests/test_antithesis_submission.py`, `tests/test_antithesis_capabilities.py`

- [ ] **Step 1: Failing tests**: `preview_package(request(scenario_id='m3-runtime-blockfetch-port-drop-timeout', versions=exact cardano))` → compose services exactly `{configurator, tracer, log-tailer, node1, node2, node3, dwarf-workload}`, `workload_binding['template'] == 'scenario-rig'`, `version_binding['source'] == 'generated-rig'`, no `amaru` key demanded; amaru-only scenario with exact amaru → services include `bootstrap-cardano`, `amaru1`, `amaru-target-extract`, `dwarf-workload`; mixed substrate scenario → `node1`, `node2`, extract, workload; a `preview` substrate scenario → `capabilities['reason'] == 'public-network'`, no `environment/docker-compose.yaml`, readiness row `scenario_capabilities` error with the public-network text; `cardano-amaru-relay-bootstrap-control-local` (attach) → unchanged first-build result (`template == 'mixed-control-v1'` path, victim clone); curated id → `source == 'curated-miniprotocol'`.
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement**; delete dead code and the first build's projection tests; update `tests/test_antithesis_generated_workload.py` fixtures.
- [ ] **Step 4: Run** `tests/test_antithesis_package.py tests/test_antithesis_submission.py tests/test_antithesis_capabilities.py tests/test_antithesis_generated_rig.py tests/test_antithesis_generated_workload.py tests/test_antithesis_miniprotocol_binding.py tests/test_antithesis_amaru_binding.py tests/test_antithesis_measurements.py tests/test_antithesis_preparation.py tests/test_antithesis_publication.py` and the browser test — Expected: PASS; then the full suite against the 66-item baseline.
- [ ] **Step 5: Commit** `feat(antithesis): preview renders the scenario rig; projection removed`

### Task 6: Live proof on cardano-box

- [ ] Deploy the branch to the box (bundle → `~/dwarf-public-20261105` → shim copy → `install.sh` → re-scp the dashboard token).
- [ ] Through the dashboard API: preview `m3-runtime-blockfetch-port-drop-timeout` (cardano-only), `client-example-block-application-amaru-canonical-v3-20260918` (amaru-only), `runtime-substrate-mempool-failure-containment-mixed-amaru` (mixed); record service lists, support level, gaps.
- [ ] Prepare + build the cardano-only package's images on the controller (`--network none` for the workload).
- [ ] Operator smoke on the box: `docker compose -f <package>/environment/docker-compose.yaml up` for the cardano-only package with the built workload image; expected: configurator exits 0, three producers forge, the workload logs `antithesis_ready` and the SDK output carries `setup_complete`; the workload then waits for `/status/go`. Stop and remove. Same for the mixed package if time allows (Amaru bootstrap ≈ 10-15 min).
- [ ] E-pass log entry (commands, outputs, findings), memory update, commit `docs(antithesis): AT-2 v2 live proof on cardano-box`.

## Self-review notes

- Spec coverage: nodes/roles/edges (T1/T2), in-package genesis (T2), Amaru wrapper + modern assembly + extra args (T2/T3), faultable rule (T1/T2), per-node versions + supporting producer (T3), readiness parity + timeouts (T4), public-network refusal (T1/T5), curated/attach untouched (T4/T5), live proof (T6).
- Type consistency: `devnet` dict shape fixed in T1 and consumed unchanged by T2-T5; `rendered` dict (`compose`, `files`, `services`, `source_revision`) from T2 consumed by T3/T4/T5.
- Review Focus 1-5 pinned in T1, T2, T3, T4, T5 tests respectively.
