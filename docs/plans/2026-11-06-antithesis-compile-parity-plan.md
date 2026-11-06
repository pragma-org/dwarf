# Antithesis Compile Parity (AT-1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Antithesis run wizard stops compiling nodes and instead selects a release the operator already prepared on the Versions page; the artifact row it produces satisfies the existing artifact-row contract, is gated by provenance, preload and extraction checks that run in the controller, and binds patched (including auto-ported) taps through the same identity object the local track uses.

**Architecture:** A new controller operation `antithesis.artifact-adapt` re-tags the prepared image, runs the gates (binary-identity provenance, ld.so.preload exercise for dynamic binaries, Amaru extraction probe) and returns the row fields; a dashboard adapter `antithesis_artifacts.from_registration` drives it and writes `node-artifacts/<id>/status.json` so every existing reader stays untouched. `bind_measurements` builds the patched identity with `release_build_identity.instrumented_identity`. The wizard card "Experimental node source, binary or image" becomes "Select prepared build"; `node-start` and the dashboard-side builds are deleted.

**Tech Stack:** Python 3 (stdlib only), Jinja2 templates, vanilla JS dashboard, Docker CLI via the controller's `DockerRuntime._run`, pytest.

**Spec:** `docs/plans/2026-11-05-antithesis-track-parity-design.md` — Piece 1, BC1/BC2, "Decisions from the user", AT-1 scope block.

## Scope decisions made in this plan (surface at handoff)

- **Local-checkout source kind (`source: {kind:'bundle', path}` on `release.prepare`) is NOT in this plan.** It is independent of the adapter and needs the controller to read a host path through its existing version-imports mount; it becomes plan AT-1c right after this one lands. Consequence: between AT-1 and AT-1c the Antithesis wizard cannot build an arbitrary checkout. Nothing on cardano-box uses that path today.
- **Preload exercise applies only to dynamically linked binaries.** A static Amaru binary has no dynamic loader, so `/etc/ld.so.preload` is never read; the row records `preload_check.state = 'not-applicable-static'`. The coverage variant (AT-1b) is dynamic and will be exercised.
- **The Amaru extraction probe keeps running the existing `probe_amaru`**, but from inside the controller process (the controller already imports `profile_manager.antithesis_artifacts`). No second extraction implementation.
- **`antithesis_image_build._worker`'s `docker image inspect` (package image build) stays where it is** until AT-3 moves the package build and push into the controller.
- **`runtime_probe_log_sha256` is the digest of the adapt's own probe log** (the identity `--version` run for cardano-node, `version-probe.log` for Amaru), not the registration's `smoke_evidence_sha256` the design names: the ledger record does not carry that value, and the adapt's log is the evidence that was actually produced for this artifact. Recorded after the final review.
- **The provenance and preload gates run at adapt time, not at build time (recorded after the final review).** The design puts them "in the core (all tracks) … at package time". AT-1 runs them in `antithesis.artifact-adapt`, so the Versions page can show a Cardano release as prepared that would later be blocked when selected for Antithesis. Both gates already take a `BuildContext`; lifting them into `release_builds._build_variant` so the Versions row carries `preload_check`/`provenance_check` is follow-on **AT-1d**, kept out of this plan because it changes the local track's build outcome right before merge. Cost if wrong: an operator learns about a preload-incompatible Cardano build one step later than the design intends; no paid run is affected because the adapt still blocks it.

## Global Constraints

- Git author `JG <forky@1timeuse.com>`; no `Co-Authored-By`, no `Claude-Session`, no Anthropic attribution lines. Never push.
- Extend existing primitives; never add a standalone tool. Every Docker/git invocation introduced by this plan runs in the controller (`dwarf-fw` has no socket).
- UX: the wizard selects a prepared release. No new build trigger anywhere; the Versions page gains no button.
- Artifact row contract (readers unchanged): `id` 32 hex, `state ∈ {building, complete, failed}` (+ `interrupted` synthesised by `read`), `implementation`, `version`, `mode ∈ {stock, patched}`, `source_revision`, `reference`, `image_id`, `image_digest`, `executable_digest`, `runtime_probe_log_sha256`, `runtime_interface`, optional `patch_set_sha256`, `measurement_revision`, `toolchain`, `build_result_sha256`, `compatibility`.
- `id = sha256(registration_id.encode()).hexdigest()[:32]`; `reference = 'dwarf-antithesis-node-' + id + ':prepared'`.
- Secrets never appear in rows, logs or API replies; controller failures go through `_bounded_failure`'s allow-list.
- Tests: `PYTHONPATH=dwarf python3 -m pytest -q -p no:cacheprovider <file>` from the worktree root `/Users/nigel/dwarf-project/dwarf-fable/V10-FRESH`. The full suite has 66 pre-existing failures (list in the E-pass log); a task is green when its own files pass and the full-suite failure set is unchanged.
- TDD: each step below that writes a test is run and seen to fail before the implementation step.

## Review Focus

1. A registration deleted or re-appended (ledger revision moved) between the wizard listing and the select click: `from_registration` must fail the row with `message` naming the registration id, never write a half row. (Task 3 test `test_from_registration_unknown_registration_fails_row`.)
2. The prepared image no longer exists locally (pruned) although the ledger row exists: the controller must fail with code `image-missing`, stage `adapt`, and the dashboard row `state='failed'` with that message. (Task 2 test `test_adapt_blocks_when_image_missing`.)
3. Binary `--version` output that `parse_binary_identity` cannot read: `provenance_check.state = 'unreadable'` and the adapt is blocked, not silently passed. (Task 2 test `test_adapt_blocks_on_unreadable_identity`.)
4. Selecting a registration whose `implementation` differs from the fieldset it was chosen in (hand-edited request): the dashboard handler rejects with 422 before any controller call. (Task 5 test `test_node_select_rejects_implementation_mismatch`.)
5. Re-selecting the same registration while a previous adapt is still `building`: `from_registration` returns the existing row (202), it does not start a second controller execution. (Task 3 test `test_from_registration_returns_building_row_without_second_call`.)

---

### Task 1: Controller contract for `antithesis.artifact-adapt`

**Files:**
- Modify: `dwarf/controller/contracts.py:25-40` (enum), `:50-93` (`_PARAMETERS`), `:94-117` (`_TIMEOUT_CEILINGS`), `:118-127` (`_MUTATIONS`), `:277-444` (`_validate_parameters`)
- Modify: `dwarf/controller/service.py:765` (routing set), `:838` (terminal status map), `:487,495` (interrupted-record reconcile), `:561-568,591-597,612-627` (receipt validation), `:173-188` (`artifact_failures` allow-list)
- Modify: `dwarf/controller/docker_runtime.py:254-300` (runtime hook)
- Test: `tests/test_controller_contracts.py`, `tests/test_controller_service.py`

**Interfaces:**
- Produces: `Operation.ANTITHESIS_ARTIFACT_ADAPT = "antithesis.artifact-adapt"`; parameters exactly `{implementation, registration_id, config_image_id, executable_digest, source_revision, artifact_id, execution_id}`; timeout ceiling `1800`; it is a mutation (idempotency key required); terminal status `"adapted"`; runtime hook `DockerRuntime.adapt_antithesis_artifact(self, parameters, execution_id, *, timeout, cancel_event)` delegating to `controller.antithesis_adapt.adapt_artifact(runtime, parameters, execution_id, timeout, cancel_event)` (Task 2); progress hook `antithesis_adapt_progress(self, execution_id)` and cleanup `cleanup_antithesis_adapt(self, parameters, execution_id)` following the `release_build_progress` / `cleanup_release_build` shapes.
- Allow-listed failures `(code, stage)` added to `artifact_failures`: `('image-missing','adapt')`, `('image-identity-mismatch','adapt')`, `('provenance-mismatch','adapt')`, `('provenance-unreadable','adapt')`, `('preload-exercise-failed','adapt')`, `('extraction-probe-failed','adapt')`.

- [ ] **Step 1: Write the failing contract tests** in `tests/test_controller_contracts.py` next to the `release.prepare` case (:556-567):

```python
def test_antithesis_artifact_adapt_contract():
    request = parse_request({**BASE, 'operation': 'antithesis.artifact-adapt',
        'idempotency_key': 'adapt-1', 'timeout_seconds': 1800,
        'parameters': {'implementation': 'amaru', 'registration_id': 'registration-release-build-' + 'a'*32 + '-instrumented',
                       'config_image_id': 'sha256:' + 'b'*64, 'executable_digest': 'sha256:' + 'c'*64,
                       'source_revision': 'd'*40, 'artifact_id': 'e'*32, 'execution_id': 'antithesis-adapt-' + 'f'*32}})
    assert request.operation is Operation.ANTITHESIS_ARTIFACT_ADAPT

def test_antithesis_artifact_adapt_requires_idempotency_key():  # ContractError without idempotency_key
def test_antithesis_artifact_adapt_rejects_extra_field():      # ContractError on parameters + {'mode': 'stock'}
def test_antithesis_artifact_adapt_timeout_ceiling():          # timeout_seconds 1801 -> ContractError
```

- [ ] **Step 2: Run them**: `PYTHONPATH=dwarf python3 -m pytest -q -p no:cacheprovider tests/test_controller_contracts.py -k antithesis_artifact_adapt` — Expected: FAIL (`ContractError: unknown operation`).

- [ ] **Step 3: Implement the contract** in `contracts.py`: enum member, `_PARAMETERS` frozenset, ceiling 1800, `_MUTATIONS` entry, `_validate_parameters` branch using `_identifier` for `implementation` (must be `amaru` or `cardano-node`), `_text` for `registration_id`, `_digest` for `config_image_id` and `executable_digest`, `_SOURCE_REVISION` regex for `source_revision`, `^[0-9a-f]{32}$` for `artifact_id`, `^antithesis-adapt-[0-9a-f]{32}$` for `execution_id`.

- [ ] **Step 4: Run Step 1 tests** — Expected: PASS.

- [ ] **Step 5: Write the failing service test** in `tests/test_controller_service.py` beside the `release.prepare` case (:2625-2662): `test_antithesis_artifact_adapt_dispatches_to_runtime` — `FakeRuntime.adapt_antithesis_artifact` records `(parameters, execution_id)` and returns `{'status': 'adapted', 'artifact_id': ...}`; dispatch returns 202 with `execution_id`; after the worker thread joins, `<state>/controller/executions/<id>.json` has `state` terminal and `result.status == 'adapted'`. Add `test_antithesis_artifact_adapt_failure_is_allow_listed`: runtime raises `DockerRuntimeError('image sha256:… not present', code='image-missing', stage='adapt')` → execution record `failure.code == 'image-missing'` and the message is the original text, not `"controller runtime failed"`.

- [ ] **Step 6: Run them** — Expected: FAIL (`AttributeError`/unhandled operation).

- [ ] **Step 7: Implement routing** in `service.py`: add the operation to the set at :765; in `_artifact_worker` select `self.runtime.adapt_antithesis_artifact` and terminal status `"adapted"`; extend the reconcile, receipt and `_status` progress hooks; add the six `(code, stage)` pairs to `artifact_failures`. Add the three delegating hooks to `docker_runtime.py` (`from controller.antithesis_adapt import adapt_artifact, read_progress, cleanup`).

- [ ] **Step 8: Run** `tests/test_controller_service.py tests/test_controller_contracts.py` — Expected: PASS, no other test in these files changes result.

- [ ] **Step 9: Commit** — `git commit -m "feat(controller): antithesis.artifact-adapt contract and dispatch"`.

---

### Task 2: Controller adapter: re-tag, provenance gate, preload exercise, extraction probe

**Files:**
- Create: `dwarf/controller/antithesis_adapt.py`
- Create: `dwarf/controller/preload/preload.c` (the exercise DSO source, ~20 lines, shipped in the controller image, never in a SUT image)
- Modify: `dwarf/controller/release_builds.py:22` (`_ROLES` += `'identity-version'`, `'preload-compile'`, `'preload'`), `:196-217` (`BuildContext.container` gains `mounts: Sequence[tuple[Path, str]] = ()` → `-v host:container:ro` each)
- Test: `tests/test_antithesis_adapt.py` (new), `tests/test_release_build_runtime.py` (container mounts)

**Interfaces:**
- Consumes: `BuildContext(runtime, selected, execution_id, timeout, cancellation)` and `.container(variant, role, image, command, *, work=None, entrypoint=None, mounts=())` returning the combined output string; `runtime.inspect_image(image, *, implementation)`; `runtime._run(argv, timeout)`; `version_provenance.parse_binary_identity(text)` and `revisions_agree(a, b)`; `profile_manager.antithesis_artifacts.probe_amaru(image, directory, expected_digest)`; `DockerRuntimeError(message, *, code, stage)`.
- Produces:
  - `adapt_artifact(runtime, parameters, execution_id, timeout, cancel_event) -> dict` returning `{'status': 'adapted', 'artifact_id', 'reference', 'image_id', 'executable_digest', 'runtime_probe_log_sha256', 'runtime_interface', 'provenance_check': {'state': 'ok', 'label': <version>, 'binary_commit': <40hex>}, 'preload_check': {'state': 'ok'|'not-applicable-static', 'dso_sha256': 'sha256:…'|None, 'log_sha256': 'sha256:…'|None}, 'bootstrap_probe': 'version-and-run-help'|None}`.
  - `elf_interpreter(path: Path) -> str | None` — parses ELF64 program headers, returns the `PT_INTERP` string or `None` (static).
  - `read_progress(runtime, execution_id) -> dict`, `cleanup(runtime, execution_id) -> None`, state under `<runtime.state_dir>/antithesis-adapts/<execution_id>/` mirroring `release_builds._directory`.
- Gate order and failure codes (each raises `DockerRuntimeError(message, code=…, stage='adapt')`):
  1. `inspect_image(config_image_id)` → `image-missing` if the inspect fails; `image-identity-mismatch` if the returned `config_image_id` differs.
  2. `docker tag <config_image_id> <reference>` via `runtime._run`.
  3. Identity run: `container(variant, 'identity-version', reference, ['--version'], entrypoint=<binary path>)` where binary is `/usr/local/bin/amaru` or `/usr/local/bin/cardano-node`; `parse_binary_identity(output)` `None` → `provenance-unreadable`; `not revisions_agree(identity['git_commit'], source_revision)` → `provenance-mismatch`; the log file's sha256 is `runtime_probe_log_sha256` for cardano-node.
  4. Executable copy: reuse the `create`/`cp` sequence of `_check_packaged_executable` (release_builds.py:465-484) to obtain the packaged binary at `<dir>/packaged-executable`; sha256 must equal `executable_digest` (else `image-identity-mismatch`); `elf_interpreter(path)` decides static vs dynamic.
  5. Preload exercise (dynamic only): compile `preload.c` with `container(variant, 'preload-compile', <builder image>, ['cc','-shared','-fPIC','-o','/work/preload/libdwarf-preload.so','/work/preload/preload.c','-lpthread'], work=<dir>)`, where the builder image is the SUT image itself when `cc` exists in it, else the pinned Cardano builder image already used by `release_builds._cardano_driver`; write `<dir>/preload/ld.so.preload` containing `/usr/lib/libdwarf-preload.so`; run `container(variant, 'preload', reference, ['--version'], entrypoint=<binary>, mounts=[(<dir>/preload/libdwarf-preload.so, '/usr/lib/libdwarf-preload.so'), (<dir>/preload/ld.so.preload, '/etc/ld.so.preload')])`; output must contain the marker line `dwarf-preload-constructor-ran` AND a parseable identity, else `preload-exercise-failed` with the first 300 chars of output in the message.
  6. Amaru only: `probe_amaru(reference, <dir>/probe, expected_digest=executable_digest)`; `runtime_probe_log_sha256` = sha256 of `<dir>/probe/version-probe.log`; any exception → `extraction-probe-failed`.
- `preload.c` fixed content: a `__attribute__((constructor))` function that calls `pthread_self()` and writes `dwarf-preload-constructor-ran\n` to fd 2 with `write(2)`.

- [ ] **Step 1: Write failing tests** in `tests/test_antithesis_adapt.py` using the `runtime` SimpleNamespace fixture pattern of `tests/test_release_build_runtime.py:18-28` and `monkeypatch` on `antithesis_adapt.BuildContext.container` / `runtime._run` / `antithesis_adapt.probe_amaru`:

```python
def test_elf_interpreter_static_and_dynamic(tmp_path):   # minimal hand-built ELF64 headers: one with PT_INTERP '/lib64/ld-linux-x86-64.so.2', one without
def test_adapt_amaru_static_row_fields():                 # dynamic=False -> preload_check.state == 'not-applicable-static', probe_amaru called once, bootstrap_probe == 'version-and-run-help', reference/ image_id/ runtime_probe_log_sha256 present
def test_adapt_cardano_dynamic_runs_preload_exercise():   # container called with role 'preload' and two ro mounts; output with marker -> preload_check.state == 'ok', dso_sha256 and log_sha256 are 'sha256:'+64hex
def test_adapt_blocks_when_preload_dependency_missing():  # 'preload' output 'error while loading shared libraries: libpthread.so.0' -> DockerRuntimeError code 'preload-exercise-failed'
def test_adapt_blocks_on_provenance_mismatch():           # --version reports a different git_commit -> code 'provenance-mismatch', no tag left behind (cleanup called)
def test_adapt_blocks_on_unreadable_identity():           # --version output 'amaru 0.1' -> code 'provenance-unreadable'
def test_adapt_blocks_when_image_missing():               # inspect_image raises -> code 'image-missing'
def test_adapt_rejects_executable_digest_mismatch():      # copied binary hash != executable_digest -> code 'image-identity-mismatch'
```

- [ ] **Step 2: Run** `PYTHONPATH=dwarf python3 -m pytest -q -p no:cacheprovider tests/test_antithesis_adapt.py` — Expected: FAIL (`ModuleNotFoundError: controller.antithesis_adapt`).

- [ ] **Step 3: Add `mounts` to `BuildContext.container`** with a failing test first in `tests/test_release_build_runtime.py`: `test_container_adds_readonly_mounts` asserts argv contains `-v <host>:<container>:ro` for each pair and still contains `--network=none --read-only --cap-drop=ALL`. Run → FAIL → implement → PASS.

- [ ] **Step 4: Implement `antithesis_adapt.py`** per the Interfaces block (signatures and gate order as given; progress file `progress.json` with `stage` updates named after the gates).

- [ ] **Step 5: Run Step 1 tests** — Expected: PASS.

- [ ] **Step 6: Run** `tests/test_release_build_runtime.py tests/test_controller_service.py` — Expected: PASS (unchanged).

- [ ] **Step 7: Commit** — `git commit -m "feat(controller): adapt prepared releases for Antithesis with provenance, preload and extraction gates"`.

---

### Task 3: Dashboard adapter `antithesis_artifacts.from_registration`

**Files:**
- Modify: `dwarf/profile_manager/antithesis_artifacts.py` (add `from_registration`, `_adapt_worker`, `_row_from_registration`; keep `read`, `state_directory`, `binary_context`, `probe_amaru`, `_probe`, `selected_artifacts`, `binding_selection`, `recipe_rank`, `experimental_driver`)
- Test: `tests/test_antithesis_artifacts.py`

**Interfaces:**
- Consumes: `version_discovery.load_operator_registration(registration_id) -> {'revision', 'registration'}`; `topology_actions.controller_request(operation, parameters, timeout_seconds, idempotency_key=None)`; `controller('execution.status', {'execution_id'}, 10)` polling as in `release_preparations.py:126`; `antithesis_cardano_bridge.contract()`; Task 1 operation and Task 2 result dict.
- Produces:
  - `from_registration(registration_id: str, *, controller=None) -> dict` — returns the row. Behaviour: `id = sha256(registration_id)[:32]`; if `read(id)` exists and `state in {'building','complete'}` return it unchanged (no controller call); otherwise write the initial row `{id, state:'building', registration_id, implementation, version, mode, source_revision, created_at, message:'adapting prepared release'}` and start a daemon thread `_adapt_worker(directory, registration, row, lock, controller)`.
  - `_row_from_registration(registration: dict, result: dict) -> dict` (pure) — the complete row: `state:'complete'`, `registration` (full record, needed by Task 4), `mode` = `'patched'` if `registration['source_build']['variant'] == 'instrumented'` else `'stock'`, `source_revision = registration['provenance']['source_revision']`, `reference`, `image_id = image_digest = registration['config_image_id']`, `executable_digest`, `runtime_interface = registration['runtime_interface']`, `runtime_probe_log_sha256`, `provenance_check`, `preload_check`, `bootstrap_probe`; copies `patch_set_sha256`, `measurement_revision`, `build_result_sha256` from `source_build` when present; `compatibility = contract()` only for `implementation == 'cardano-node' and mode == 'patched'`; `message:'prepared release adapted'`.
  - Worker: sends `antithesis.artifact-adapt` with `execution_id = 'antithesis-adapt-' + id`, `idempotency_key = execution_id`, `timeout_seconds = 1800`; polls `execution.status` every 2 s until the record `state != 'running'`; on terminal success writes `_row_from_registration(...)`, on failure writes `state:'failed'`, `message = failure['message']` (allow-listed text), and `failure.txt`. The row is saved in `finally`, like `_worker` today.

- [ ] **Step 1: Write failing tests** in `tests/test_antithesis_artifacts.py` (state root via `monkeypatch.setattr(a, 'state_directory', lambda: tmp_path)`; registration fixtures copied from `tests/test_release_build_identity.py:11-35`; `controller` fake records calls and returns `{'status':'accepted','execution_id':...}` then a terminal status record):

```python
def test_row_from_registration_matches_contract():            # all contract keys present; id == sha256(registration_id)[:32]; reference == 'dwarf-antithesis-node-'+id+':prepared'; mode 'patched' for instrumented
def test_row_from_registration_cardano_patched_has_compatibility():  # == antithesis_cardano_bridge.contract(); stock cardano has no 'compatibility'
def test_from_registration_writes_complete_row(tmp_path):      # thread joined; read(id)['state']=='complete'; selected_artifacts({'amaru': id}, 'instrumented') returns it
def test_from_registration_unknown_registration_fails_row():   # load_operator_registration raises -> read(id)['state']=='failed', message contains registration_id, no controller call
def test_from_registration_returns_building_row_without_second_call():
def test_from_registration_failed_controller_message_is_bounded():  # execution.status failure {'code':'provenance-mismatch','message':...} -> row state failed, message == that text
```

- [ ] **Step 2: Run** `PYTHONPATH=dwarf python3 -m pytest -q -p no:cacheprovider tests/test_antithesis_artifacts.py -k "from_registration or row_from_registration"` — Expected: FAIL (`AttributeError`).

- [ ] **Step 3: Implement** the three functions. `controller` defaults to `topology_actions.controller_request`; the worker uses the same lock files (`worker.lock`, root `.lock`) as `start` so `read`'s `interrupted` logic keeps working.

- [ ] **Step 4: Run the file** — Expected: PASS (including pre-existing `probe_amaru` tests at :142-157).

- [ ] **Step 5: Commit** — `git commit -m "feat(antithesis): adapt operator-prepared releases into node artifact rows"`.

---

### Task 4: Patched identity through `instrumented_identity`

**Files:**
- Modify: `dwarf/profile_manager/antithesis_measurements.py:103-121`
- Test: `tests/test_antithesis_measurements.py`

**Interfaces:**
- Consumes: artifact rows carrying `registration` (Task 3); `release_build_identity.instrumented_identity(registration, identity)` and `validate_collector_identity(identity)`.
- Produces: in `bind_measurements`, when `artifact.get('registration')` is present and `artifact['mode'] == 'patched'`: `identity.update(identity_kind='operator-local-config', image_config_id=artifact['image_id'], controller_installation_id=registration['controller_installation_id'], executable_digest=artifact['executable_digest'])` then `identity = instrumented_identity(registration, identity)`. The `experimental_instrumentation` branch (:110-113) is kept for old rows and only taken when no `registration` is present. `antithesis_measurements` no longer sets `non_authoritative_performance` differently; everything else unchanged.

- [ ] **Step 1: Write failing tests** in `tests/test_antithesis_measurements.py` (row fixture via `monkeypatch.setattr(a, 'read', lambda identifier: row)` as at :99):

```python
def test_patched_identity_uses_instrumented_identity():   # identity has identity_kind 'operator-local-config', 'verified_instrumentation', 'source_build'; validate_collector_identity(identity) passes
def test_auto_ported_amaru_registration_binds_patched_taps():  # registration.source_build has auto_ported True + ported_from_source_revision; build_amaru_patched_factories is called with an identity whose source_build.auto_ported is True and the bound measurement list is non-empty
def test_legacy_experimental_row_still_binds():           # row without 'registration' but with experimental_instrumentation keeps today's behaviour
```

- [ ] **Step 2: Run** — Expected: FAIL (no `verified_instrumentation`).
- [ ] **Step 3: Implement** the branch in `bind_measurements`.
- [ ] **Step 4: Run** `tests/test_antithesis_measurements.py tests/test_antithesis_bridge_auto_ported.py` — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(antithesis): bind patched taps through instrumented_identity"`.

---

### Task 5: Wizard "Select prepared build" and retirement of `node-start`

**Files:**
- Modify: `dwarf/profile_manager/data/antithesis_run_wizard.py:12-32` (`antithesis_draft_catalog` gains `prepared_builds`)
- Modify: `dwarf/profile_manager/data/antithesis_package.py:27,53-61` (`node-select` replaces `node-start`; `node-status` unchanged)
- Modify: `dwarf/dashboard/templates/operate/antithesis_run_wizard.j2:71-88`
- Modify: `dwarf/dashboard/static/js/antithesis-artifacts.js`
- Modify: `dwarf/profile_manager/antithesis_artifacts.py` — delete `validate_request`, `start`, `_worker`, `_source`, `_inspect`, `_base`, `_experimental_amaru`, `cardano_builder_dockerfile`, `_cardano_source`; keep `experimental_driver` (controller uses it) and `binary_context`
- Modify: `dwarf/profile_manager/experimental_amaru_instrumentation.py` only if something there is now unused (check with `git grep`)
- Test: `tests/test_antithesis_package.py`, `tests/test_lab_entry.py`, `tests/test_antithesis_artifacts.py` (delete tests of removed functions), `tests/browser/test_antithesis_artifacts.cjs` (patch `from_registration`/`read` instead of `start`/`read`), `tests/test_antithesis_release_parity.py` (re-point or delete the cases that compared the dashboard build with the controller build; record the ruling)

**Interfaces:**
- Produces: `prepared_build_options(ledger_state: dict | None = None) -> dict[str, list[dict]]` in `data/antithesis_run_wizard.py` — keyed by implementation, each entry `{registration_id, version, mode ('stock'|'patched'), source_revision, label}` where `label = f"{version} · {mode}"` plus `" · auto-ported from {ported_from_source_revision[:12]}"` when `source_build.auto_ported`, plus `" · proven by run {run_id}"` when the ledger's `auto_port_proofs` contains a proof for that registration (match on the proof's registration id field; read `operator_version_decisions.append_auto_port_proof`, :1065, for the exact key). Ledger read via `OperatorVersionDecisionLedger().read()`.
- `node-select` request shape `{token, implementation, registration_id}` → 422 (`PackageError`, field `registration_id`) when the registration's `implementation` differs from the posted one; else `from_registration(registration_id)`; reply 202 while `building`, 200 otherwise, body `{'ok': True, 'artifact': row}` exactly as `node-start` did.
- Template: the `<details>` card at :71 becomes `<fieldset data-antithesis-node="{{implementation}}">` with a `<select data-node-registration>` populated from `catalog.prepared_builds[implementation]` (option value = registration_id, text = label, `data-mode`), a button `data-node-select` "Use prepared build", status `data-node-status`, and a line "No prepared build for this implementation — prepare one on the Versions page" with a link to `/operate/versions` when the list is empty. The build-variant select (:89) stays; the JS disables options whose `data-mode` does not match the chosen variant.
- JS: `antithesis-artifacts.js` posts `node-select`, keeps polling `node-status`, keeps writing `root.antithesisNodeArtifacts[impl] = row.id` when `row.state === 'complete'` (:48-49) so `antithesis-package.js` and `antithesis-preparation.js` are untouched.

- [ ] **Step 1: Write failing tests**:

```python
# tests/test_lab_entry.py (or the test file that renders the wizard)
def test_wizard_lists_prepared_builds_per_implementation():  # ledger with one amaru stock + one instrumented auto-ported registration -> two <option> under data-antithesis-node="amaru", labels contain 'auto-ported from'
def test_wizard_without_prepared_builds_points_to_versions_page():
# tests/test_antithesis_package.py
def test_node_select_starts_adapter_and_returns_202():        # monkeypatch from_registration -> building row
def test_node_select_rejects_implementation_mismatch():       # 422, no from_registration call
def test_node_start_action_is_gone():                          # 'node-start' -> 404/unknown action
```

- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement** `prepared_build_options`, the `node-select` handler, the template and the JS; delete the retired functions and their tests; run `git grep -n "node-start\|_experimental_amaru\|validate_request\|cardano_builder_dockerfile" -- dwarf tests` and resolve every hit (keep `experimental_driver` and `binary_context`).
- [ ] **Step 4: Run** `tests/test_antithesis_package.py tests/test_lab_entry.py tests/test_antithesis_artifacts.py tests/test_antithesis_release_parity.py tests/test_antithesis_preparation.py tests/test_antithesis_image_build.py` — Expected: PASS; then the browser test `node tests/browser/test_antithesis_artifacts.cjs` — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(antithesis): wizard selects prepared builds; retire node-start builds"`.

---

### Task 6: Shared recipe selection for the CI track

**Files:**
- Modify: `dwarf/profile_manager/candidate_builds.py:224-245` (`_compatible_patch`)
- Test: `tests/test_candidate_builds.py` (or the file that covers `plan_candidate_builds`; locate with `git grep -n "_compatible_patch\|plan_candidate_builds" -- tests`)

**Interfaces:**
- Consumes: `measurement_patch_port.select_base_recipes(implementation, *, targets_root=None) -> list[tuple[Path, dict]]`.
- Produces: `_compatible_patch(candidate, assets)` iterates `select_base_recipes(candidate.implementation, targets_root=<assets root>)` in that order (newest base release first, then `recipe_rank`) and returns the first `(manifest, inputs)` that passes the existing predicate (`amaru_patch.verify_preimages` for Amaru; `cabal.project.freeze` pins for Cardano). The predicate code is unchanged; only the candidate ordering source changes.

- [ ] **Step 1: Write failing test** `test_compatible_patch_orders_by_select_base_recipes`: two manifests where today's listing order differs from `select_base_recipes` order and both verify; assert the newer base release wins.
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the file plus `tests/test_measurement_patch_port.py`** — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "refactor(ci): select patch recipes through select_base_recipes"`.

---

### Task 7: Full suite, deploy to cardano-box, dashboard check

**Files:**
- Modify: `docs/plans/2026-11-05-e-pass-log.md` (or the E-pass log the B.1 work used; locate with `ls docs/plans | grep -i e-pass`) — append AT-1 entries.

- [ ] **Step 1: Full suite** — `PYTHONPATH=dwarf python3 -m pytest -q -p no:cacheprovider tests > /tmp/at1-suite.log; tail -3 /tmp/at1-suite.log` — Expected: the failure set equals the 66 pre-existing failures (diff the `FAILED` lines against the recorded list; no new names).
- [ ] **Step 2: Deploy** the branch to cardano-box with the recipe in memory note `cardano-box-v10-deploy.md` (bundle → scp → `~/dwarf-public-20261105` fast-forward on a branch → installer), and confirm `DWARF_SOURCE_REVISION` in `dwarf-fw` equals the branch head.
- [ ] **Step 3: Dashboard check** (Playwright, as a user): open `/run/antithesis`; the Amaru fieldset lists the prepared registrations from the Versions page (stock and instrumented, with the auto-ported label); choose the instrumented one with build variant `instrumented`; status reaches `complete`; Preview a package with the curated scenario; the preview's `node_artifacts.amaru` has `reference == 'dwarf-antithesis-node-<id>:prepared'`, `provenance_check.state == 'ok'`, `preload_check.state == 'not-applicable-static'`, `bootstrap_probe == 'version-and-run-help'`; the measurements binding lists patched Amaru taps. Repeat for cardano-node patched: `preload_check.state == 'ok'`.
- [ ] **Step 4: Record** the outcome (ids, states, any fix-then-redeploy loop) in the E-pass log; commit `docs: E-pass log — AT-1 adapter proven on cardano-box`.
