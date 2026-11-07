# AT-2 Catalog Parity Implementation Plan

> **Superseded 2026-11-07 for Tasks 4-5 and the "always mixed" projection:** the user rejected
> the fixed-rig projection; `docs/plans/2026-11-07-antithesis-catalog-parity-v2-plan.md` renders
> the scenario's own devnet instead. Tasks 1-3 and 6-11 of this plan stand as built.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Any catalog scenario (not only the curated mini-protocol one) previews, prepares, builds and submits as an Antithesis package in which DWARF's own scenario runner is the workload, with the scenario's unsupported primitives declared as gaps instead of silently dropped.

**Architecture:** The generated package projects the scenario's nodes onto the existing control harness (`mixed-control-v1` or `cardano-control-v1`), adds the curated victim clones for the target implementation, and adds one `dwarf-workload` service built from the installed controller image. Inside the package the workload runs `run_service.execute_run(backend='antithesis')`; the substrate lifecycle primitives read package-shipped reports instead of calling Docker, node observation goes over mounted node sockets and log volumes, and a committed capability matrix decides, at preview time and at run time, which primitives run, which are delegated to the platform, and which are skipped with a gap event. Outcomes are written as fallback-SDK JSONL so AT-4's normaliser sees them as DWARF properties.

**Tech Stack:** Python 3.11+ (stdlib + PyYAML), Jinja2 templates, vanilla JS, Docker compose v2, pytest, Playwright (node) browser tests.

**Spec:** `docs/plans/2026-11-05-antithesis-track-parity-design.md` — Piece 2, BC1, BC2, "Track capabilities and invariants", the faultable-SUT policy (corrected 2026-11-06) and the AT-2 scope block.

## Facts the spec got wrong (verified in code 2026-11-06, rulings below)

- `compile_topology` labels **every** service with the fault exclusion, including the targets (`antithesis_topology.py:119-121`). Victims are produced only by the curated clone path (`antithesis_workload_binding.py:176-199`: `sm-cardano-seed`, `sm-cardano-victim`, `sm-amaru-victim`). The generated path reuses that clone mechanism; it does not strip labels from `relay1`/`amaru-relay-1`.
- In the curated compose the unlabelled services are `amaru-relay-1` (:169), `amaru-relay-2` (:197), `sm-cardano-seed` (:283), `sm-cardano-victim` (:319), `sm-amaru-victim` (:348). `spec.md:80` says the Amaru relays are excluded. Task 1 labels them and re-traces the one pin.
- Almost no devnet observation primitive is "network only": `runtime_multi_node_observation` reaches Cardano tips through `docker exec … cardano-cli` **or** a local `cardano-cli` over a socket path when the node record has no `container_name` (`dwarf/scripts/runtime_multi_node_observation.py:120-150`), and Amaru tips through a mounted `log_path` file before `docker logs` (`:236-258`). Connection state needs `docker inspect/exec` (`:396-450`). The package therefore mounts node state and evidence volumes into the workload and ships a `runtime.json` **without** container fields.
- Scenarios have no readiness-probe field. The workload's readiness probe is TCP reachability of every mapped node plus socket presence for Cardano victims.
- 141 of 310 devnet scenarios declare `substrate.nodes` (ids `node1`, `node2`, …, impl, role, edges) and run `runtime_install_version` → `runtime_compose_substrate` → … → `runtime_teardown_substrate`; 156 declare a `profile` (node list synthesised by `profiles.versioned_substrate_for_profile`); 13 use `attach.topology: cardano_amaru`. The generated path handles the three shapes with one node→service mapping.
- The run handle has no `backend` attribute. Package mode is signalled by `DWARF_ANTITHESIS_PACKAGE_DIR` (same pattern as `DWARF_CONTROLLER_HOST_STATE_DIR`).
- The installed images are `dwarf/framework:current` (776 MB, no cardano-cli) and `dwarf/controller:current` (1.13 GB, has `cardano-cli`, `cardano-node`, `cardano-testnet` under `/usr/local/bin`, framework tree at `/home/dwarf/dwarf-fw`). The workload image is `FROM dwarf/controller:current`; the entrypoint verifies the release revision against the package's expected revision.
- `antithesis_image_build._plan` rejects any `build` key other than `context`/`dockerfile`, so the `--network=none` request travels as a service label `com.dwarf.build-network: none`.

## Global Constraints

- Extend existing primitives, never reinvent (user rule). Every Docker operation runs in the controller (BC2).
- Curated mixed package pins untouched: `SCENARIO_SHA256`, the eight `SOURCE_SHA256` entries, the sidecar digest, inventory equality. Task 1 re-traces **only** `SOURCE_SHA256['docker-compose.yaml']` for the label fix and records the new value in the file comment.
- `dwarf-workload` image: digest-pinned in the compose after push (existing publication path), built with `--network=none`, its package-side contents listed with digests in the package manifest.
- `setup_complete` only after prepare succeeded and the readiness probe passed; assertion outcomes as fallback-SDK JSONL, one atomic line each, to `$ANTITHESIS_OUTPUT_DIR/sdk.jsonl` (fallbacks `ANTITHESIS_SDK_LOCAL_OUTPUT`, `/tmp/antithesis_sdk.jsonl`, exactly as `antithesis_conventions.SETUP_COMPLETE_SH`).
- DWARF property names must match AT-4's rule: SDK prefix + snake_case message `^[a-z][a-z0-9_]*$`. Every workload property id starts with `dwarf_`.
- Capability matrix at `dwarf/profile_manager/data/antithesis-capabilities-v1.json`; anything not listed is `unsupported`.
- Refuse-and-show-why only for `library`, `single-node`, and the degenerate all-producers case; mixed projects onto `mixed-control-v1`.
- No run may be submitted with `generated_selections` not applied; declared capability gaps do not count as "not applied", a scenario with no runnable load does.
- TDD; git author `JG <forky@1timeuse.com>`; no Anthropic attribution or `Claude-Session` trailers; no push.
- Test command: `PYTHONPATH=dwarf /opt/homebrew/bin/python3.14 -m pytest -q -p no:cacheprovider <files>`; browser tests `PATH=<scratchpad>/pybin:$PATH NODE_PATH=<scratchpad>/pwdeps/node_modules node tests/browser/<f>.cjs`.

## Review Focus

1. A profile scenario with 3 Cardano nodes and target cardano-node must map `node1` to `sm-cardano-victim`, `node2`/`node3` to `relay1`/`relay2`, and a 4-node profile must produce `topology-capacity-exceeded`, not a silent drop (Task 4).
2. A scenario whose only load primitive is unsupported must classify as `none` and be blocked at readiness with `no-runnable-load`; one with an unsupported probe but a runnable load must classify `partial` and submit (Tasks 2, 9).
3. The workload must never emit `setup_complete` when the readiness probe times out; it must write `/status/done` with exit 3 so the driver exits non-zero (Task 7).
4. A skipped assertion must not count as a pass: the verdict falls to `no-passing-assertions` when every assertion was skipped (Task 6).
5. The curated package's pins and preview output must be byte-identical except the two new labels; the generated path must never be chosen for `cardano-amaru-miniprotocol-security-local` (Tasks 1, 8).

---

### Task 1: Curated compose fault labels match spec.md

**Files:**
- Modify: `antithesis/cardano_amaru_miniprotocol_security/docker-compose.yaml:169-225`
- Modify: `dwarf/profile_manager/antithesis_workload_binding.py:17-19`
- Test: `tests/test_antithesis_miniprotocol_binding.py`

**Interfaces:**
- Produces: the curated compose where `amaru-relay-1` and `amaru-relay-2` carry `labels: *fault-exclude`; the new `SOURCE_SHA256['docker-compose.yaml']` value.

- [ ] **Step 1: Write the failing test**

```python
def test_curated_amaru_relays_are_fault_excluded():
    compose = yaml.safe_load((workload.SOURCE/'docker-compose.yaml').read_text())
    for name in ('amaru-relay-1', 'amaru-relay-2'):
        assert compose['services'][name]['labels'] == {'com.antithesis.exclude_from_faults': 'network,kill,pause,stop'}
    victims = {n for n, s in compose['services'].items() if 'labels' not in s}
    assert victims == {'sm-cardano-seed', 'sm-cardano-victim', 'sm-amaru-victim'}
```

- [ ] **Step 2: Run it** — Expected: FAIL (`KeyError: 'labels'` on amaru-relay-1).
- [ ] **Step 3: Add `labels: *fault-exclude` to `amaru-relay-1` and `amaru-relay-2`** (after their `hostname:` lines, same form as `relay1`). Recompute `sha256sum antithesis/cardano_amaru_miniprotocol_security/docker-compose.yaml` and set `SOURCE_SHA256['docker-compose.yaml']`; update the comment line 17 to "Reviewed recipe at cb72871; compose re-traced 2026-11-06 for the amaru-relay fault labels (spec.md:80)".
- [ ] **Step 4: Run** `tests/test_antithesis_miniprotocol_binding.py tests/test_antithesis_package.py tests/test_antithesis_measurements.py` — Expected: PASS (the binding's equality check at `antithesis_workload_binding.py:166` compares control clones; relays now equal `_service(..., control=True)`).
- [ ] **Step 5: Commit** `fix(antithesis): label the curated Amaru relays as fault-excluded per spec.md`

### Task 2: Capability matrix and classifier

**Files:**
- Create: `dwarf/profile_manager/data/antithesis-capabilities-v1.json`
- Create: `dwarf/profile_manager/antithesis_capabilities.py`
- Test: `tests/test_antithesis_capabilities.py`

**Interfaces:**
- Produces: `load_matrix() -> dict` (`{schema_version:1, default:'unsupported', primitives:{name:{status, note}}}`); `STATUSES = ('runs','runs-with-note','delegated','delegated-fault','unsupported')`; `classify(scenario) -> dict` with keys `level` (`full|partial|none`), `primitives` (list of `{phase, primitive, status, note}` in scenario order, phases flattened), `gaps` (list of `{kind:'implementation-gap', code, primitive, phase, message}` for `unsupported` and `delegated-fault`), `runnable_load` (bool), `reason` (`None`, `'no-runnable-load'`, `'no-faultable-sut'`); `skip_policy(classification) -> dict[str,str]` mapping primitive name → status for every non-`runs` entry.

Matrix v1 contents (from the Docker-use sweep; `unsupported` means it needs `docker exec/logs/run`, host pids or host tools):

| status | primitives |
|---|---|
| runs | `all_nodes_responsive`, `peer_connectivity_observed`, `substrate_quorum_observed`, `all_nodes_started_clean`, `load_events_are_ok`, `parser_exit_status`, `target_progress_continues`, `no_target_fatal_signal`, `invalid_protocol_cases_contained`, `submit_outcome_matches`, `mempool_failure_contained` |
| runs-with-note | `runtime_multi_node_observation` (tip over mounted socket/log; connection state unavailable), `runtime_substrate_tip_warmup` (same tip route), `runtime_verify_exact_target` (identity from package), `runtime_tx_submit_differential` (submit API host from package runtime.json), `runtime_txsubmission_window_pressure` (target host from package runtime.json), `load_shell_command` (tool must exist in the workload image) |
| delegated | `runtime_install_version`, `runtime_compose_substrate`, `runtime_teardown_substrate`, `runtime_attach_topology` |
| delegated-fault | `fault_local_port_drop`, `fault_local_port_delay`, `fault_node_freeze`, `runtime_byzantine_cardano_node`, `runtime_network_partition` |
| unsupported (explicit, with the reason as note) | `runtime_target_health_and_progress`, `runtime_wait_for_chain_progress`, `runtime_peer_session_health`, `runtime_protocol_decode_cases`, `runtime_amaru_measurement_calibration`, `runtime_tracer_capture`, `runtime_generated_node_freeze_check`, `runtime_generated_node_recovery_check`, `runtime_real_target_restart_and_readiness`, `runtime_controlled_chain_progress_window`, `runtime_controlled_sync_range`, `runtime_snapshot_capture`, `runtime_simulate_peer_set_capture`, `runtime_opcert_header_soak`, `forge_block`, `serve_crafted_block`, `block_apply_differential`, `runtime_plutus_phase2_differential_observation`, `runtime_preview_parity_baseline` |

Level rule: `none` when `scenario.runtime in ('library','single-node')` (reason `no-faultable-sut`) or no load primitive is `runs`/`runs-with-note` (reason `no-runnable-load`); `full` when no entry is `unsupported`; otherwise `partial`. Gap codes: `primitive-unsupported-on-antithesis`, `fault-delegated-to-platform`.

- [ ] **Step 1: Write the failing tests** (`tests/test_antithesis_capabilities.py`), using `scenario_from_body` on inline YAML:

```python
def test_substrate_observation_scenario_is_partial_with_delegated_lifecycle():
    c = classify(scenario_from_body(SUBSTRATE_MEMPOOL_YAML))   # install/compose/teardown + multi_node_observation + mempool probe
    assert c['level'] == 'partial' and c['runnable_load'] is True and c['reason'] is None
    assert [p['status'] for p in c['primitives'] if p['primitive']=='runtime_compose_substrate'] == ['delegated']
    assert {g['code'] for g in c['gaps']} == {'primitive-unsupported-on-antithesis'}

def test_library_scenario_is_none_no_faultable_sut():
    assert classify(scenario_from_body(LIBRARY_YAML))['reason'] == 'no-faultable-sut'

def test_unsupported_only_load_is_none_no_runnable_load():
    assert classify(scenario_from_body(OPCERT_SOAK_YAML))['reason'] == 'no-runnable-load'

def test_unknown_primitive_defaults_to_unsupported_and_fault_is_delegated():
    c = classify(scenario_from_body(FREEZE_YAML))  # load_shell_command + fault_node_freeze + unknown_primitive_x
    statuses = {p['primitive']: p['status'] for p in c['primitives']}
    assert statuses['fault_node_freeze'] == 'delegated-fault' and statuses['unknown_primitive_x'] == 'unsupported'
    assert skip_policy(c) == {'fault_node_freeze':'delegated-fault', 'unknown_primitive_x':'unsupported', 'load_shell_command':'runs-with-note'}

def test_matrix_names_exist_in_registry():
    registry = json.load(open('dwarf/primitives/registry.json'))['primitives']
    assert set(load_matrix()['primitives']) - set(registry) == {'forge_block','serve_crafted_block','block_apply_differential'} - set(registry)
```

- [ ] **Step 2: Run** — Expected: FAIL `ModuleNotFoundError: antithesis_capabilities`.
- [ ] **Step 3: Write the matrix JSON and `antithesis_capabilities.py`** (`load_matrix`, `classify`, `skip_policy`; phases flattened via `scen.phases or [default phase]` exactly as `scenario.py:1405-1420`; load-phase runnable test uses the `load` list only).
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat(antithesis): capability matrix v1 and scenario classifier`

### Task 3: Fallback SDK writer

**Files:**
- Create: `dwarf/profile_manager/antithesis_sdk.py`
- Test: `tests/test_antithesis_sdk.py`

**Interfaces:**
- Produces: `output_path(environ=os.environ) -> Path` (order from `SETUP_COMPLETE_SH`); `emit_assert(kind, identifier, condition, *, details=None, environ=None) -> None` writing one line `{"antithesis_assert":{"id","message"(=id),"condition","display_type","hit":true,"must_hit":true,"assert_type","details","location":{"file":"","function":"","class":"","begin_line":0,"begin_column":0}}}` with `(assert_type, display_type)` in `always/Always`, `sometimes/Sometimes`, `reachability/Reachable`; `setup_complete(environ=None)` writing the exact `antithesis_setup` line; `PROPERTY_ID = re.compile(r'^dwarf_[a-z0-9_]+$')` enforced on `identifier` (ValueError otherwise). Writes are `open(..., 'a')` + single `write` + `flush` (one line per call); `OSError` swallowed like `SDK.hs:109-121`.

- [ ] **Step 1: Failing tests**: line shape equals the Haskell shape byte-for-byte for `always`; `setup_complete` line equals the string in `antithesis_conventions.SETUP_COMPLETE_SH`; env precedence (`ANTITHESIS_OUTPUT_DIR` > `ANTITHESIS_SDK_LOCAL_OUTPUT` > `/tmp/antithesis_sdk.jsonl`); invalid id raises; details with a non-JSON value is stringified.
- [ ] **Step 2: Run** — Expected: FAIL import.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat(antithesis): fallback SDK JSONL writer`

### Task 4: Node projection and the generated workload binding

**Files:**
- Create: `dwarf/profile_manager/antithesis_generated_workload.py`
- Create: `dwarf/profile_manager/antithesis_assets/dwarf-workload/Dockerfile`, `.../test/v1/dwarf/singleton_driver_scenario.py`, `.../test/v1/dwarf/anytime_health.py`
- Modify: `dwarf/profile_manager/antithesis_workload_binding.py` — extract `add_victims(files, resolved, implementations) -> dict` from the body of `bind_workload` (:176-199: `sm-cardano-seed`, `sm-cardano-victim`, `sm-amaru-victim`, their volumes) so both binders call it; `bind_workload` behaviour unchanged.
- Test: `tests/test_antithesis_generated_workload.py`

**Interfaces:**
- Consumes: `classify` (Task 2); `harness.resolve_default`/`topology.resolve_topology`/`harness.emit`/`harness.bind_versions` (existing); `profiles.versioned_substrate_for_profile` is NOT called (needs the catalog); node lists come from `scenario_nodes(scenario)`.
- Produces:
  - `scenario_nodes(scenario) -> list[{id, impl, role}]`: `substrate.nodes` as declared; `profile` → `find_profile(id)` counts → `node1..nodeN` (`cardano-node`, role `producer`) then `amaru1..amaruM` (`amaru`, role `consumer`), plus `bootstrap-cardano` (`cardano-node`, `bootstrap-producer`) for amaru-only; `attach.topology == 'cardano_amaru'` → the six Cardano + two Amaru names from `adapt_cardano_amaru_runtime.CARDANO_NODES/AMARU_NODES` with `name == id`.
  - `template_for(scenario, nodes) -> 'mixed-control-v1' | 'cardano-control-v1'` (mixed when any amaru node or `target.implementation == 'amaru'`).
  - `project_nodes(scenario, nodes) -> {'services': {node_id: service}, 'victims': [service...], 'issues': [...]}`: target-impl nodes in declared order → `['sm-cardano-victim','relay1','relay2']` or `['sm-amaru-victim','amaru-relay-1','amaru-relay-2']`; other-impl nodes → `['relay1','relay2','p1','p2','p3']` minus used, or `['amaru-relay-1','amaru-relay-2']` minus used; overflow → issue `topology-capacity-exceeded`; attach scenarios map name→name.
  - `runtime_metadata(scenario, projection, versions) -> dict`: schema of path A (`compose_mode:'docker'`, `compose_project:'antithesis'`, `network:'testnet_42'`, `network_magic:42`, `support_binaries:{'cardano-cli':'/usr/local/bin/cardano-cli'}`, `nodes`, `haskell_nodes`, `amaru_nodes`, `topology:{edges}` from substrate or full mesh) where each node is `{id, name:<service>, impl, implementation, version, role, image, listen_address:'<service>.example:<3001|3000>', port, socket_path:'/inputs/<service>/node.socket' (cardano) | None, log_path:'/inputs/<service>/amaru.log' (amaru victim) | None, submit_api_url:'http://<service>.example:3011/api/submit/tx' (amaru victim) | None, container_listen_address:'<service>.example:<port>', observation_transport:'antithesis-package'}` and **no** `container_name`/`container_socket_path`/`container_ip`.
  - `compose_report(runtime) -> dict` (`compose_mode`, `healthy: None`, `node_count`, `network_magic`, `support_binaries`, `nodes[{id,impl,role,version,listen_address,healthy:None}]`, `delegated:'antithesis-package'`), `install_report(runtime) -> dict` (`satisfied: True`, `nodes{id:{impl, requested_version, resolved_version, role}}`, `delegated`), `expected_revision() -> str` (`DWARF_SOURCE_REVISION` env or `release_identity`).
  - `bind_generated_workload(files, scenario, resolved, version_binding, classification) -> dict` returning the same shape as `bind_workload` (`status`, `source:'dwarf-workload'`, `targets` = victim services, `issues`, `template`, `projection`, `runtime_metadata`, `image_contents` = `[{path, sha256}]` of every `environment/dwarf-workload/**` file). It: calls `add_victims`; adds `environment/dwarf-workload/{Dockerfile, test/v1/dwarf/*.py, package/scenario.yaml, package/runtime.json, package/compose-report.json, package/install-report.json, package/capabilities.json, package/expected-revision}`; adds service `dwarf-workload` to the compose: `build:{context:'./dwarf-workload'}`, `platform:'linux/amd64'`, `container_name:'dwarf-workload'`, `hostname:'dwarf-workload.example'`, `init:true`, `user:'0:0'`, `environment:{DWARF_ANTITHESIS_PACKAGE_DIR:'/opt/dwarf/package', DWARF_ANTITHESIS_READY_TIMEOUT:'900'}`, `labels:{com.antithesis.exclude_from_faults:FAULT_EXCLUSION, com.dwarf.build-network:'none'}`, `volumes`: `dwarf-workload-evidence:/evidence` plus `<state volume>:/inputs/<service>:ro` for every mapped Cardano service (`sm-cardano-state`, `relay1-state`, `relay2-state`, `p1-state`…) and `sm-amaru-runtime-evidence:/inputs/sm-amaru-victim:ro`; `depends_on` every victim with `condition: service_healthy` and `sidecar` with `service_started`; adds volume `dwarf-workload-evidence`. Status `applied` when `classification['level'] != 'none'` and no `topology-capacity-exceeded`; otherwise `recorded` with the issues.
- Dockerfile (asset, copied verbatim): `FROM dwarf/controller:current`, `LABEL io.dwarf.role=antithesis-workload`, `COPY test/ /opt/antithesis/test/v1/dwarf/`, `COPY package/ /opt/dwarf/package/`, `RUN chmod 0755 /opt/antithesis/test/v1/dwarf/*.py` (legacy builder-safe), `ENV DWARF_ANTITHESIS_PACKAGE_DIR=/opt/dwarf/package`, `WORKDIR /home/dwarf/dwarf-fw`, `ENTRYPOINT ["python3","dwarf/cardano-profile","antithesis","workload"]`.
- `singleton_driver_scenario.py`: create `/status/go`; poll `/status/done` every 2 s forever; `sys.exit(int(open('/status/exit').read()))`. `anytime_health.py`: read `/opt/dwarf/package/runtime.json`, TCP-connect each `listen_address` (3 s), `emit_assert('sometimes','dwarf_nodes_reachable', all_ok, details={'unreachable':[...]})` via `profile_manager.antithesis_sdk`, exit 0.

- [ ] **Step 1: Failing tests** (`request()` from `tests/test_antithesis_package.py` style, scenario YAML fixtures inline):
  - substrate 2-node mixed (cardano target): services map `{'node1':'sm-cardano-victim','node2':'amaru-relay-1'}`, template mixed, victims `['sm-cardano-victim']`.
  - profile-a 3 nodes: `{'node1':'sm-cardano-victim','node2':'relay1','node3':'relay2'}`, template cardano; 4-node substrate → issue `topology-capacity-exceeded`, status `recorded`.
  - attach `cardano_amaru`: name→name, victims by target.
  - runtime.json node for `sm-cardano-victim` has `socket_path == '/inputs/sm-cardano-victim/node.socket'`, no `container_name`; amaru victim has `log_path` and `submit_api_url`.
  - compose after binding: `dwarf-workload` service has `build=={'context':'./dwarf-workload'}`, label `com.dwarf.build-network == 'none'`, mounts `sm-cardano-state:/inputs/sm-cardano-victim:ro`, `depends_on['sm-cardano-victim']['condition']=='service_healthy'`; `image_contents` lists the Dockerfile and both test scripts with sha256.
  - curated scenario id → `bind_generated_workload` raises `ValueError('curated scenario uses bind_workload')`.
- [ ] **Step 2: Run** — Expected: FAIL import.
- [ ] **Step 3: Implement** (`add_victims` extraction first, run `tests/test_antithesis_miniprotocol_binding.py` to confirm the curated path is unchanged, then the new module and assets).
- [ ] **Step 4: Run** new tests + `tests/test_antithesis_miniprotocol_binding.py` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(antithesis): generated dwarf-workload binding with node projection`

### Task 5: Preview takes the generated path

**Files:**
- Modify: `dwarf/profile_manager/antithesis_package.py:204-276` (path selection), `:306-324` (manifest/handoff)
- Modify: `dwarf/profile_manager/antithesis_measurements.py:85-96` (accept `workload['source']=='dwarf-workload'` exactly like the curated binding — no code change expected beyond reading `targets`; verify)
- Modify: `dwarf/dashboard/static/js/antithesis-package.js:45-60, 88-98`; `dwarf/dashboard/templates/operate/antithesis_run_wizard.j2` (package card: add `<p id="antithesis-package-capabilities" role="status"></p>` and `<ul id="antithesis-package-capability-list" hidden>`)
- Test: `tests/test_antithesis_package.py`, `tests/browser/test_antithesis_package.cjs`

**Interfaces:**
- Consumes: Task 2 `classify`, Task 4 `bind_generated_workload`, `template_for`.
- Produces: `preview_package` result and manifest gain `capabilities` (the classification dict) and `workload_binding.source in ('curated-miniprotocol','dwarf-workload','none')`; `engine_handoff.inputs` row `scenario` reads `applied` for `full`, `partial` for `partial`, `recorded` for `none` with the reason in `detail`; new gap codes `scenario-capability-none` (field `scenario_id`, message carries the reason) and the classification gaps appended to `issues`.

Path rule in `preview_package`: for `scenario-default` topology and a non-curated scenario, `control = topology.resolve_topology({... 'template_id': template_for(scenario, nodes) ...})` (same shape as `harness.resolve_default`); the native `antithesis_generator` branch (`:235-267`) is kept only for scenarios the classifier marks `none` with reason `no-runnable-load` **and** whose load is exactly one `cbor_fuzz*` primitive (existing adversary path); everything else goes through the control harness + `bind_generated_workload`. Curated id keeps `bind_workload`.

- [ ] **Step 1: Failing tests**: `preview_package(request(scenario_id='runtime-substrate-mempool-failure-containment-mixed-amaru'))` → `workload_binding['source']=='dwarf-workload'`, `capabilities['level']=='partial'`, `'environment/dwarf-workload/Dockerfile' in files`, `engine_handoff` scenario row `partial`; a profile-a scenario → template cardano, no amaru services in compose; `cardano-node-cov-handshake-aflpp-smoke` (library) → `capabilities['reason']=='no-faultable-sut'`, gap `scenario-capability-none`, `workload_binding['status']=='recorded'`; curated id → `source=='curated-miniprotocol'` and the existing miniprotocol tests unchanged. Browser: selecting the mempool scenario shows `#antithesis-package-capabilities` text starting with `Antithesis support: partial` and a list item containing `fault-delegated-to-platform` or `primitive-unsupported-on-antithesis`.
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement** (preview, manifest, JS: render `result.capabilities.level`, `reason`, and `gaps[].message`).
- [ ] **Step 4: Run** `tests/test_antithesis_package.py tests/test_antithesis_miniprotocol_binding.py tests/test_antithesis_measurements.py tests/test_antithesis_version_binding.py tests/test_antithesis_amaru_binding.py` + browser — Expected: PASS.
- [ ] **Step 5: Commit** `feat(antithesis): preview any catalog scenario through the generated workload path`

### Task 6: Runner: backend 'antithesis', after_prepare hook, skip policy

**Files:**
- Modify: `dwarf/profile_manager/run_service.py:30, 118-120, 488-511` ; `dwarf/profile_manager/scenario.py:1086-1093, 1398-1400, 1425-1440, 1469-1480, 1513-1528, 2280-2306`
- Test: `tests/test_run_service.py`, `tests/test_scenario_topology_preflight.py` (pattern), new `tests/test_scenario_antithesis_policy.py`

**Interfaces:**
- Produces: `RunRequest` unchanged; `execute_run` accepts `backend in ('local','antithesis')`; for `antithesis` it passes `after_prepare=request_hooks['after_prepare']` and `primitive_policy=request_hooks['primitive_policy']` obtained from a new module-level seam `run_service.ANTITHESIS_HOOKS: Callable[[RunRequest], dict] | None` (set by Task 7's workload; `None` → both hooks `None`). `_ORIGINS` gains `'antithesis'`.
- `run_scenario(..., after_prepare=None, primitive_policy=None)`: `after_prepare(handle)` is called once, after `observer.start()` (`:1399`) and before the phase loop; an exception from it ends the run with `exit_status='precondition_failed'` (same path as the preflight failure, `:1183-1332`). `primitive_policy: dict[str,str] | None`: in `_run_phase`, `_run_faults_for_phase`, the probe loop and `_run_phase_assertions`, a primitive whose policy is `unsupported` or `delegated-fault` is not instantiated; the run logs `event='primitive_skipped'` with `payload={'policy': status, 'phase': ...}`; a skipped assertion records `handle.assertion_result(primitive=..., params=..., evaluated_value=None, data_points_used=0, result='skipped', note=status)` and does not change `overall`. `forensic.RunHandle.assertion_result` accepts `result='skipped'` and `assertion_summary` counts `{total, pass, fail, skipped}`; `scenario_verdict.evaluate_scenario_verdict` treats `skipped` as not passing (so all-skipped → `no-passing-assertions`).

- [ ] **Step 1: Failing tests**: invert `test_execute_run_refuses_non_local_backend_before_engine_execution` into `test_execute_run_accepts_antithesis_backend_and_passes_hooks` (monkeypatch `run_scenario` recorder, set `run_service.ANTITHESIS_HOOKS`, assert `kwargs['backend']=='antithesis'`, `kwargs['after_prepare'] is hook`, `kwargs['primitive_policy']=={'fault_node_freeze':'delegated-fault'}`); keep a refusal test for `backend='moog'`. `test_scenario_antithesis_policy.py` (pattern of `tests/test_scenario_topology_preflight.py::_run`): a scenario with `load_shell_command` + `fault_node_freeze` + unknown assertion under policy → `events` contain two `primitive_skipped`, `assertions.json` has one `skipped` row, manifest `assertion_summary.skipped == 1`; `after_prepare` raising → `exit_status == 'precondition_failed'` and no phase ran; verdict test: `evaluate_scenario_verdict(exit_status='pass', assertions=[{'result':'skipped'}], ...)` → `'no-passing-assertions' in reason_codes`.
- [ ] **Step 2: Run** — Expected: FAIL (`TypeError: unexpected keyword 'after_prepare'`).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `tests/test_run_service.py tests/test_scenario_antithesis_policy.py tests/test_scenario_topology_preflight.py tests/test_scenario_verdict.py tests/test_forensic*.py` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(run-service): antithesis backend with after_prepare hook and primitive skip policy`

### Task 7: Workload entrypoint (BC1 lifecycle)

**Files:**
- Create: `dwarf/profile_manager/antithesis_workload.py`
- Modify: `dwarf/profile_manager/cli.py:366-370, 1586` (subcommand `antithesis workload [--package DIR] [--runs-dir DIR] [--status-dir DIR]`)
- Test: `tests/test_antithesis_workload_entrypoint.py`

**Interfaces:**
- Consumes: Task 3 `antithesis_sdk`, Task 6 hooks seam, Task 2 `skip_policy`.
- Produces: `main(argv, environ=os.environ) -> int`: reads `<package>/expected-revision` and compares with `release_identity` revision (mismatch → write `/status/exit`=4, `/status/done`, return 4, **no** `setup_complete`); loads `<package>/capabilities.json` → `primitive_policy`; sets `run_service.ANTITHESIS_HOOKS`; calls `execute_run(RunRequest(scenario_path=<package>/scenario.yaml, origin='antithesis', backend='antithesis', runs_dir, state_dir))`; on return writes `finish(handle_dir, status)`.
  `after_prepare(handle)`: `wait_ready(runtime, timeout)` — every node: TCP connect to `listen_address`; Cardano nodes with `socket_path`: `Path(socket_path).exists()`; poll 5 s until `DWARF_ANTITHESIS_READY_TIMEOUT` → raise `RuntimeError('antithesis readiness probe timed out: <unready ids>')`; rewrite `<package>/compose-report.json` node `healthy` flags into the run's `outputs/substrate-compose/compose-report.json`; `antithesis_sdk.setup_complete()`; then block until `/status/go` exists (poll 1 s, log every 60 s via `handle.log(event='waiting_for_driver')`).
  `finish(run_dir, exit_status)`: for each `assertions.json` row → `emit_assert('always', 'dwarf_' + primitive, result=='pass', details={result, evaluated_value (str, ≤512 chars)})` except `skipped` rows → `emit_assert('reachability','dwarf_skipped_'+primitive, True, details={'policy': note})`; `emit_assert('sometimes','dwarf_scenario_completed', exit_status in ('pass','fail'), details={'exit_status': exit_status})`; if `measurements/report.json` exists → `emit_assert('reachability','dwarf_measurements_reported', True, details={'measurements': compact rows ≤ 64})`; write `/status/exit` (`0` pass, `1` fail, `2` error/other, `3` precondition_failed) then `/status/done`.
- Identifier derivation: `'dwarf_' + re.sub(r'[^a-z0-9_]', '_', primitive.lower())`.

- [ ] **Step 1: Failing tests**: with `execute_run` monkeypatched to write a fake bundle (`assertions.json` rows pass/fail/skipped, `manifest.json` exit_status) → sdk.jsonl has `always dwarf_all_nodes_responsive condition true`, `always dwarf_mempool_failure_contained condition false`, `reachability dwarf_skipped_x`, `sometimes dwarf_scenario_completed`, `/status/exit == '1'`, `/status/done` exists; readiness: with a fake runtime of one node whose port a test listener opens after 1 s → `setup_complete` line present **after** the listener opened and `/status/go` created by the test → `after_prepare` returns; timeout case (`DWARF_ANTITHESIS_READY_TIMEOUT=1`, closed port) → raises, no `setup_complete` line; revision mismatch → return 4 and no sdk lines.
- [ ] **Step 2: Run** — Expected: FAIL import.
- [ ] **Step 3: Implement** + CLI wiring.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat(antithesis): dwarf-workload entrypoint with readiness gate, driver handshake and SDK outcomes`

### Task 8: Delegated lifecycle primitives and package-mode node access

**Files:**
- Modify: `dwarf/profile_manager/primitives.py:4717-4795` (`RuntimeInstallVersion.run`), `:4866-5013` (`RuntimeComposeSubstrate.run`), `:5111-…` (`RuntimeTeardownSubstrate.run`), `:4797-4864` (`RuntimeAttachTopology.run`), `:15325-15441` (`_resolve_client_runtime_target` Amaru branch)
- Modify: `dwarf/scripts/runtime_multi_node_observation.py:396-450` (connection state without container access)
- Test: `tests/test_primitives_antithesis_package_mode.py`, `tests/test_runtime_multi_node_observation.py` (existing file if present, else new)

**Interfaces:**
- Produces: helper `antithesis_package_mode() -> Path | None` (`DWARF_ANTITHESIS_PACKAGE_DIR`) in `primitives.py`; in package mode: `RuntimeInstallVersion` copies `<pkg>/install-report.json` to `output_dir/install-report.json`, sets `bound_state['substrate_install_report*']`, logs `event='delegated_to_package'`, no subprocess; `RuntimeComposeSubstrate` copies `<pkg>/runtime.json` and `<pkg>/compose-report.json` into `output_dir`, sets `substrate_runtime_root=str(output_dir)`, `substrate_runtime_metadata_path` and `substrate_bundle_runtime_metadata_path` to `output_dir/runtime.json`, `substrate_compose_report`, logs one `node_started` per node and `completed` with `outcome='ok'`; `RuntimeTeardownSubstrate` writes `teardown-report.json {delegated:'antithesis-package'}`; `RuntimeAttachTopology` copies `<pkg>/runtime.json` to `output_dir/runtime.json` and logs `completed` with `runtime_metadata_path`. `_resolve_client_runtime_target` Amaru branch: when the node record carries `observation_transport == 'antithesis-package'`, `listen_host` is the record's `listen_address` host and no `docker inspect` is attempted. `runtime_multi_node_observation._observe_connection_state`: when the node has no `container_name` and `observation_transport == 'antithesis-package'`, return `{'available': False, 'reason': 'no process access inside the Antithesis package', 'pid_scope': 'none'}` instead of running docker.

- [ ] **Step 1: Failing tests**: with `DWARF_ANTITHESIS_PACKAGE_DIR` pointing at a tmp package (the three reports + runtime.json from Task 4's `runtime_metadata`), a fake handle (pattern of existing primitive tests) → `RuntimeComposeSubstrate(params).run(handle, rng)` makes **no** subprocess call (monkeypatch `subprocess.run` to raise), `bound_state['substrate_compose_report']['delegated']=='antithesis-package'`, `output_dir/runtime.json` exists; `RuntimeInstallVersion` likewise; teardown writes the report; attach copies; without the env var the primitives still build their commands (existing tests). Observation: `_observe_connection_state(node_without_container)` returns `available False` and never calls docker (monkeypatch `_run` to raise).
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the new tests + `tests/test_primitives*.py` subset that covers these classes + `tests/test_runtime_multi_node_observation*.py` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(primitives): package-mode substrate lifecycle and node access without Docker`

### Task 9: Readiness, handoff and build honour the generated path

**Files:**
- Modify: `dwarf/profile_manager/antithesis_submission.py:250-336` (new check row + generated_selections rule)
- Modify: `dwarf/profile_manager/antithesis_image_build.py:94-127` (`_plan` keeps `network` from the service label), `dwarf/controller/antithesis_publish.py:160-200` (`--network=none` when `item['network']=='none'`)
- Modify: `dwarf/profile_manager/antithesis_publication.py` (image rows: `image_contents` from `workload_binding` copied into the handoff receipt under `workload_image_contents`)
- Test: `tests/test_antithesis_submission.py`, `tests/test_antithesis_image_build.py`, `tests/test_antithesis_publish.py`, `tests/test_antithesis_publication.py`

**Interfaces:**
- Produces: readiness check `scenario_capabilities` (group `Package`, label `Antithesis support`): `ok` for `full`, `warn` for `partial` (detail = count of delegated/unsupported), `error` for `none` with the reason text (`No runnable load on Antithesis: …` / `No faultable SUT: library and single-node scenarios have no topology to fault`); `generated_selections` passes when `workload_binding.status=='applied'` for source `dwarf-workload` (classification gaps are not "not applied"). `_plan` rows gain `network: 'none' | None` read from `service.labels['com.dwarf.build-network']` (only `'none'` accepted, anything else → `PackageError('Unsupported build network.')`); controller adds `'--network', 'none'` to the build argv for those rows and records `network` in the built-image row.

- [ ] **Step 1: Failing tests**: readiness with a generated receipt (`capabilities.level='partial'`, `workload_binding={'status':'applied','source':'dwarf-workload'}`) → no `generated_selections` error and a `scenario_capabilities` warn row; level `none` → error row with the reason and `state=='blocked'`; `_plan` on a compose with the label → `builds[0]['network']=='none'`; label `host` → `PackageError`; controller `build_images` (existing fake-docker test harness in `tests/test_antithesis_publish.py`) → argv contains `--network none` for that service only.
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the four files — Expected: PASS.
- [ ] **Step 5: Commit** `feat(antithesis): readiness, handoff and controller build honour the generated workload`

### Task 10: Run page and compare label the capability gaps

**Files:**
- Modify: `dwarf/profile_manager/antithesis_submission.py` (`_reserve`: copy `capabilities` from the receipt into the external-run ledger entry `external.capabilities` and `gaps.json` seed), `dwarf/profile_manager/antithesis_results.py:198-205` (`_GAPS` default list is replaced by the ledger's capability gaps when present), `dwarf/profile_manager/data/operate_run.py` (`external['capabilities']`), `dwarf/dashboard/templates/operate/external_run.j2` (section "Antithesis support" after the properties table: level, reason, gap list), `dwarf/profile_manager/data/measurement_compare.py` (record field `antithesis_support`)
- Test: `tests/test_antithesis_submission.py`, `tests/test_antithesis_results.py`, `tests/test_external_run_dashboard.py`, `tests/test_measurement_compare.py`

- [ ] **Step 1: Failing tests**: reservation stores `external.capabilities.level`; `write_result_bundle` writes the capability gaps into `gaps.json` (`kind:'implementation-gap'`, `code`, `primitive`, `message`) and keeps the platform `_GAPS`; run page renders `Antithesis support: partial` and one `<li>` per gap; compare record carries `antithesis_support`.
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the four files — Expected: PASS.
- [ ] **Step 5: Commit** `feat(antithesis): carry capability gaps into the ledger, result bundle, run page and compare`

### Task 11: Live proof on cardano-box (E-pass entry) and docs

**Files:**
- Modify: `docs/plans/2026-11-05-e-pass-validation-log.md` (new section "2026-11-06 — AT-2 generated workload on cardano-box"), `docs/plans/2026-11-05-antithesis-track-parity-design.md` (Piece 2 note: victims are the curated clones; observation over mounted sockets/logs; matrix v1 facts)

- [ ] **Step 1: Deploy** the branch to the box (bundle → `~/dwarf-public-20261105` → copy shim → `install.sh` with the usual env), as in AT-3/AT-4.
- [ ] **Step 2: Drive the wizard as a user**: scenario `runtime-substrate-mempool-failure-containment-mixed-amaru`, both patched prepared builds, preview → support level and gap list shown; prepare; build (expect six images: five curated-lineage + `dwarf-workload`, the latter with `--network none` in the controller log); publication handoff → push (new package name `dwarf-dwarf-workload` → one make-public click owed to the user; record it); verify images; download ZIP; readiness (expect `scenario_capabilities` warn, `public_git` error until the commit, MOOG rows as AT-4 left them).
- [ ] **Step 3: Local smoke of the workload image on the box through the controller**: not possible without the full compose (Antithesis starts it) — instead run `docker run --rm -e DWARF_ANTITHESIS_READY_TIMEOUT=5 -e ANTITHESIS_OUTPUT_DIR=/tmp/out -v /tmp/out:/tmp/out dwarf-antithesis-<id>-<n>:prepared` on the box **through the shim verb `antithesis-workload-smoke`** (add the read-only verb to `delivery/control-plane/dwarf-deploy-shim.py` in this task, with a 120 s ceiling and the image reference validated against `^dwarf-antithesis-[0-9a-f]{32}-[0-9]{1,2}:prepared$`) and confirm: revision check passes, readiness times out at 5 s, `sdk.jsonl` has no `setup_complete`, exit 3. Record the exact output in the E-pass log.
- [ ] **Step 4: Commit** `docs: E-pass log — AT-2 generated workload proof on cardano-box` (plus the shim verb if added: `feat(control-plane): antithesis-workload-smoke read verb`).

---

## Self-review notes

- Spec coverage: Piece 2.1 (Tasks 4, 5), 2.2 (Tasks 4, 6, 7), 2.3 (Tasks 2, 5, 9, 10), 2.4 (Task 5 reuses `bind_measurements` unchanged; the `details` event is Task 7's `dwarf_measurements_reported`), 2.5 (Task 5 path rule + Task 1 pin), BC1 (Tasks 6, 7, 8), BC2 (Task 9), invariants (Tasks 1, 4, 9), faultable-SUT policy (Task 2 reason + Task 9 readiness).
- **AT-2b "full landing" (user decision 2026-11-06: partial first, then full):** once one real run proves the socket/log observation route, re-route the `unsupported` matrix entries that only need tip, log or restart access to the same package routes (in order of catalog use: `runtime_target_health_and_progress` 53, `runtime_wait_for_chain_progress` 13, `runtime_peer_session_health` 13, `runtime_controlled_sync_range` 7, `runtime_controlled_chain_progress_window` 8, `runtime_generated_node_freeze/recovery_check` 16 via platform faults + TCP), so the common substrate/profile scenarios classify `full`. Primitives that need `docker run`/host spawn stay declared gaps.
- Deferred (follow-ons, not in this plan): cardano victim clone in non-mixed generated profiles (the cardano template already uses the curated clone, so only the "non-mixed amaru" case is open); Amaru tip observation for non-victim Amaru relays (stdout only); the `docker exec`-class primitives (matrix `unsupported`); controller image for the workload base being `:current` rather than a digest (the revision check guards it; digest pin lands with the publication push).
