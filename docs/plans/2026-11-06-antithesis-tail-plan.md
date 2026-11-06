# Antithesis Self-Serve Tail (AT-3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A dev pushes an Antithesis package from the run wizard on an installed box: the package images are built and pushed to GHCR by the controller with a `write:packages` PAT entered once (or saved on request), the wizard links straight to the GitHub page where the one manual "make public" click happens and polls until the image is public, and readiness is shown as a prerequisite checklist.

**Architecture:** Two new controller operations, `antithesis.image-build` (unpack the preparation archive, `docker build` the package's `build:` services, inspect node artifacts) and `antithesis.image-publish` (`docker login` with a staged, expiring secret file, tag, push), replace every Docker call the dashboard made in `antithesis_image_build` and `antithesis_publication._push`. The dashboard keeps its rows, receipts and UI and becomes a thin client (start, poll `execution.status`), exactly like `antithesis_artifacts.from_registration`. The PAT never travels as a controller request parameter: it is staged under `state/version-imports/` (0600, owner-checked, expiring) the way uploaded binaries are, and the dashboard deletes it when the operation ends. Config gains `ghcr_prefix` and `ghcr_publish_pat`; the wizard push card pre-fills the prefix and takes the PAT one-time by default.

**Tech Stack:** Python 3 stdlib, Docker CLI via `DockerRuntime._run`, Jinja2 templates, vanilla JS, pytest, Playwright `.cjs` browser tests.

**Spec:** `docs/plans/2026-11-05-antithesis-track-parity-design.md` — Piece 3, BC2, "Decisions from the user" (GHCR answered / GHCR destination / Approved 2026-11-06), AT-3 scope block.

## Scope decisions made in this plan (surface at handoff)

- **The public git commit of the package stays manual** (design: deferred until a repo is named). The wizard's Verify Git step is unchanged.
- **Package validation's Docker calls (`antithesis_package_validation.py:128,165,190,228`) are not moved.** They share the socket problem but are a separate optional step; recorded as follow-on AT-3b.
- **Image names stay per-handoff** (`<prefix>-<service>:<handoff_id>`) for the tag, but the GHCR *package* name is `<prefix>-<service>`, which is stable per service, so the one manual visibility click is once per package name as the design says. No renaming.
- **Owner type for the visibility URL** is not derivable from the prefix; the wizard shows the `users/<owner>` URL and, next to it, the `orgs/<owner>` URL as the alternative. No GitHub API call.
- **The PAT is staged as a file, not sent as a parameter**, because the controller persists every request's parameters in execution records, idempotency hashes and audit receipts.

## Global Constraints

- Git author `JG <forky@1timeuse.com>`; no `Co-Authored-By`, no `Claude-Session`. Never push.
- Extend existing primitives. Every Docker call runs in the controller (`dwarf-fw` has no socket; `delivery/tests/test_delivery_contract.sh:199` enforces it).
- Secrets: the PAT value never appears in any row, receipt, execution record, idempotency file, audit receipt, progress file, log or API reply. Config redaction of `*_pat` is automatic (`operate_config._redact_secrets`); `moog_setup_payload` returns secrets as `value=""` with `configured`.
- Tests: `PYTHONPATH=dwarf /opt/homebrew/bin/python3.14 -m pytest -q -p no:cacheprovider <files>` from the worktree root; browser tests `PATH=<scratchpad>/pybin:$PATH NODE_PATH=<scratchpad>/pwdeps/node_modules node tests/browser/<file>.cjs`. Full suite has 66 pre-existing failures; a task is green when its files pass and the failure set is unchanged.
- Controller operation pattern (from AT-1): enum + `_PARAMETERS` + `_TIMEOUT_CEILINGS` + `_MUTATIONS` + `_validate_parameters` in `contracts.py`; `service.py` dispatch set (:783), `_artifact_start` plan digest (:825), `_artifact_worker` method/terminal-status/`before` (:855-893), reconcile cleanup map (:507-510), `_valid_receipt` (:608-615, :638), `_status` progress (:1184); `docker_runtime.py` delegating hooks; `_bounded_failure` allow-list entries `(code, stage)`.
- Contract regexes: execution ids `antithesis-build-[0-9a-f]{32}` and `antithesis-publish-[0-9a-f]{32}`; preparation/handoff ids `[0-9a-f]{32}`; image references pass `_IMAGE_LOOKUP`; destinations `ghcr.io/` + `REPOSITORY` (`antithesis_publication_network.py:20-21`) + `:` + `[0-9a-f]{32}`.

## Review Focus

1. The staged PAT file outlives its operation (dashboard crash between start and completion): the controller must refuse a secret whose `expires_at` has passed, and a later publish must stage a fresh one. (Task 3 test `test_publish_refuses_an_expired_secret`; Task 5 test `test_push_deletes_the_staged_secret_after_the_operation_ends`.)
2. A `docker login` failure (bad PAT): the operation fails with code `registry-login-failed`, the message never echoes the token or the login output, and the handoff stays pushable (`push_attempted` is reset). (Task 3 `test_publish_login_failure_is_bounded`; Task 5 `test_failed_push_can_be_retried`.)
3. The anonymous verify returns 401/403/404 for one image and 200 for another: the handoff reports exactly which packages still need the click, with their settings URLs, and polling stops as soon as all pass. (Task 6 `test_verify_reports_visibility_required_per_package`.)
4. The prefix typed in the wizard differs from the saved `ghcr_prefix`: the typed one wins for that submission only; the saved value is untouched. (Task 5 `test_push_uses_the_typed_prefix_without_saving_it`.)
5. The build archive on the preparation is tampered with after the dashboard inspected it: the controller re-reads the preparation through `read_preparation` (inventory digests) and refuses on mismatch with `build-source-invalid`. (Task 3 `test_build_refuses_a_preparation_whose_inventory_changed`.)

---

### Task 1: Config fields `ghcr_prefix` and `ghcr_publish_pat`

**Files:**
- Modify: `dwarf/profile_manager/moog.py:18-43` (`DEFAULT_MOOG_CONFIG`), `:91-106` (`normalize_moog_config` key tuple)
- Modify: `dwarf/profile_manager/data/operate_config.py:27-52` (`MOOG_SETUP_FIELDS`, group `Antithesis`)
- Modify: `delivery/docker-compose.dwarf.yml:64-87` (`dwarf-fw` environment: `DWARF_GHCR_PREFIX`, `DWARF_GHCR_PUBLISH_PAT` passthrough)
- Test: `tests/test_moog_integration.py`, `delivery/tests/test_delivery_contract.sh`

**Interfaces:**
- Produces: config keys `moog.ghcr_prefix` (text, env `DWARF_GHCR_PREFIX`, help "GHCR namespace the wizard pushes to, e.g. ghcr.io/j-gainsec") and `moog.ghcr_publish_pat` (`kind:'password'`, `secret:True`, env `DWARF_GHCR_PUBLISH_PAT`, help "GitHub PAT with write:packages; leave blank to enter it per push"); `moog_setup_payload` exposes both in group `Antithesis` (prefix as plain value, PAT masked with `configured`); `apply_moog_setup_form` keeps a blank PAT. Validation: a non-empty prefix must satisfy `antithesis_publication.create_handoff`'s prefix rule (`ghcr.io/` + `REPOSITORY`); expose that rule as `antithesis_publication_network.valid_registry_prefix(value: str) -> bool` for reuse.

- [ ] **Step 1: Write failing tests** in `tests/test_moog_integration.py` beside :324/:356:

```python
def test_ghcr_fields_are_configurable_and_the_pat_is_masked(monkeypatch):
    # env precedence: DWARF_GHCR_PREFIX wins over config; PAT comes back value="" configured=True; group == 'Antithesis'
def test_ghcr_prefix_must_be_a_ghcr_namespace():
    # apply_moog_setup_form(config, {'ghcr_prefix': 'docker.io/x'}) raises ValueError; 'ghcr.io/j-gainsec' accepted; '' accepted
def test_blank_ghcr_pat_keeps_the_saved_value():
```

- [ ] **Step 2: Run** `… tests/test_moog_integration.py -k ghcr` — Expected: FAIL (`KeyError`/unknown field).
- [ ] **Step 3: Implement** the three code changes plus `valid_registry_prefix`; `create_handoff` uses it.
- [ ] **Step 4: Run** `tests/test_moog_integration.py tests/test_antithesis_publication.py` — Expected: PASS. Run `bash delivery/tests/test_delivery_contract.sh` — Expected: PASS (add an assertion that both env names are passed through to `dwarf-fw`).
- [ ] **Step 5: Commit** — `feat(config): ghcr_prefix and ghcr_publish_pat operator fields`.

---

### Task 2: Controller contracts `antithesis.image-build` and `antithesis.image-publish`

**Files:**
- Modify: `dwarf/controller/contracts.py`, `dwarf/controller/service.py`, `dwarf/controller/docker_runtime.py` (hooks + `_run` stdin)
- Test: `tests/test_controller_contracts.py`, `tests/test_controller_service.py`, `tests/test_controller_docker_runtime.py`

**Interfaces:**
- Produces:
  - `Operation.ANTITHESIS_IMAGE_BUILD = "antithesis.image-build"`, parameters exactly `{preparation_id, execution_id}`; ceiling 3600; mutation; terminal status `"built"`; `plan_sha256` for the execution record and receipt = `parameters['preparation_id']` (extend the `parameters.get(...)` chain in `_artifact_start` and `_valid_receipt` with `publish_sha256` then `preparation_id`); `before` = `{preparation_id}`.
  - `Operation.ANTITHESIS_IMAGE_PUBLISH = "antithesis.image-publish"`, parameters exactly `{handoff_id, secret_id, username, images, execution_id}` where `images` is a list (1..32) of `{reference, image_id, destination}` validated element-wise (`reference` `_IMAGE_LOOKUP`, `image_id` `_DIGEST`, `destination` the GHCR destination regex above), `username` `_IDENTIFIER`, `secret_id` `ghcr-[0-9a-f]{24}`; ceiling 3600; mutation; terminal status `"published"`; `publish_sha256` = sha256 of the canonical JSON of `images` (computed in `_validate_parameters` and added to the normalized parameters as `publish_sha256`) so receipts bind the image set; `before` = `{handoff_id, publish_sha256}`.
  - Runtime hooks: `build_antithesis_images(parameters, execution_id, *, timeout, cancel_event)` → `controller.antithesis_publish.build_images(runtime, …)`; `publish_antithesis_images(…)` → `controller.antithesis_publish.publish_images(runtime, …)`; `antithesis_build_progress(execution_id)`, `antithesis_publish_progress(execution_id)`, `cleanup_antithesis_build`, `cleanup_antithesis_publish` (Task 3 implements them).
  - `DockerRuntime._run(..., stdin_path: Path | None = None)`: when given, the child's stdin is that file opened `O_RDONLY | O_NOFOLLOW`; otherwise `DEVNULL` as today.
  - `_bounded_failure` allow-list: `('build-source-invalid','image-build')`, `('image-build-failed','image-build')`, `('artifact-image-missing','image-build')`, `('registry-secret-invalid','image-publish')`, `('registry-login-failed','image-publish')`, `('image-tag-failed','image-publish')`, `('image-push-failed','image-publish')`.
  - `_status` progress for both operations.

- [ ] **Step 1: Write failing contract tests** (`_build_request()`, `_publish_request()` helpers like `_adapt_request`): accepted shapes, extra field rejected, 33 images rejected, bad destination rejected, missing idempotency key rejected, ceilings.
- [ ] **Step 2: Run** — Expected: FAIL (`unsupported operation`).
- [ ] **Step 3: Implement contracts.**
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Write failing service tests**: dispatch to `FakeRuntime.build_antithesis_images` → 202, terminal `applied`, `result.status == 'built'`; same for publish with `'published'`; a publish failure with code `registry-login-failed` keeps its code and the canned message; interrupted records call the cleanup hooks. Write a failing docker-runtime test: `_run(stdin_path=...)` feeds the file to the child (use `docker_binary=sys.executable` with a `-c 'import sys; print(sys.stdin.read())'` script as `tests/test_release_build_runtime.py:228-248` does) and never passes it on argv.
- [ ] **Step 6: Run** — Expected: FAIL.
- [ ] **Step 7: Implement** service dispatch/worker/receipt/reconcile/status entries, the runtime hooks, and `_run` stdin.
- [ ] **Step 8: Run** `tests/test_controller_contracts.py tests/test_controller_service.py tests/test_controller_docker_runtime.py` — Expected: PASS except the two pre-existing docker-runtime failures.
- [ ] **Step 9: Commit** — `feat(controller): antithesis.image-build and antithesis.image-publish contracts`.

---

### Task 3: Controller module `antithesis_publish.py` + preparation mount

**Files:**
- Create: `dwarf/controller/antithesis_publish.py`
- Modify: `delivery/docker-compose.dwarf.yml` (controller: `${RUNTIME_ROOT}/state/antithesis/preparations:/var/dwarf/dashboard-state/antithesis/preparations:ro`, env `DWARF_CONTROLLER_PREPARATIONS_DIR=/var/dwarf/dashboard-state/antithesis/preparations`), `delivery/tests/test_delivery_contract.sh` (assert the mount is `:ro` and the dashboard still has no socket)
- Modify: `dwarf/profile_manager/binary_imports.py` (reuse: `stage_secret(value: str) -> str` writing `version-imports/<secret_id>/{descriptor.json, secret}` 0600 with `expires_at = now + 900 s`, kind `ghcr-publish-pat`; `discard_secret(secret_id)`), `dwarf/controller/docker_runtime.py` (reuse `_verified_staged_artifact`'s owner/`O_NOFOLLOW`/expiry checks as `_verified_staged_secret(secret_id) -> Path`)
- Test: `tests/test_antithesis_publish.py` (new), `tests/test_binary_imports.py` (or the file covering staging)

**Interfaces:**
- Consumes: `profile_manager.antithesis_preparation.read_preparation(identifier, *, state_dir=None)`, `_unpack(archive_bytes)`, `profile_manager.antithesis_image_build._plan(files)`; `runtime._run(argv, timeout, execution_id, output_limit, output_path, stdin_path)`; `runtime.inspect_image`.
- Produces:
  - `build_images(runtime, parameters, execution_id, timeout, cancel_event) -> dict`: reads the preparation from `os.environ['DWARF_CONTROLLER_PREPARATIONS_DIR']` (`read_preparation(preparation_id, state_dir=...)`; inventory mismatch → `build-source-invalid`), unpacks to `<state_dir>/antithesis-builds/<execution_id>/package/`, runs `_plan`, and for each node artifact in `receipt['version_binding']['node_artifacts']` runs the two inspects (`artifact-image-missing` on failure), for each `build:` service runs `docker build --platform linux/amd64 --tag dwarf-antithesis-<execution_id[-32:]>-<index>:prepared --file <dockerfile> .` from `package/<context>` (ceiling 1200 each, log `build-<i>.log`) then inspects it; returns `{'status': 'built', 'preparation_id', 'built_images': [rows exactly as antithesis_image_build._worker builds them today, incl. 'publication': 'local-only'], 'build_host': 'DWARF controller'}`; progress `{status, stage, current_service, detail}`.
  - `publish_images(runtime, parameters, execution_id, timeout, cancel_event) -> dict`: `secret = _verified_staged_secret(secret_id)` (`registry-secret-invalid` when missing/expired/not owned); `config = tempfile.mkdtemp(prefix='dwarf-ghcr-', dir='/tmp')`; `docker --config <config> login ghcr.io -u <username> --password-stdin` with `stdin_path=secret` (`registry-login-failed`; the login output is never copied into progress or the error); for each image: inspect `reference` (must match `image_id`), `docker tag reference destination` (`image-tag-failed`), `docker --config <config> push destination` ceiling 600, log `push-<i>.log` (`image-push-failed`); `shutil.rmtree(config)` in `finally`; returns `{'status': 'published', 'handoff_id', 'pushed': [{'destination', 'pushed_at'}], 'registry': 'ghcr.io'}`.
  - `read_build_progress`, `read_publish_progress`, `cleanup_build`, `cleanup_publish` (containers: none persistent; remove `antithesis-builds/<id>/package` on cleanup; the temp docker config is removed in `finally`).
  - `binary_imports.stage_secret(value) -> secret_id` / `discard_secret(secret_id)`; descriptor `{kind:'ghcr-publish-pat', created_at, expires_at}`.

- [ ] **Step 1: Write failing tests** in `tests/test_antithesis_publish.py` with the `SimpleNamespace` runtime pattern of `tests/test_antithesis_adapt.py`:

```python
def test_build_unpacks_the_preparation_and_builds_each_build_service(runtime, prepared)      # uses a real prepare_package() fixture (test_antithesis_image_build.prepared) staged under a tmp preparations dir
def test_build_refuses_a_preparation_whose_inventory_changed(runtime, prepared)             # edit archive bytes → code build-source-invalid
def test_build_reports_a_missing_node_artifact_image(runtime, prepared)                      # inspect raises → artifact-image-missing
def test_publish_logs_in_with_password_stdin_then_tags_and_pushes(runtime, tmp_path)        # argv has --password-stdin, stdin_path == secret path, PAT bytes never in any argv/progress/log, config dir removed
def test_publish_refuses_an_expired_secret(runtime, tmp_path)                                # registry-secret-invalid
def test_publish_login_failure_is_bounded(runtime, tmp_path)                                 # docker login rc=1 with the PAT echoed in stderr → code registry-login-failed; progress.detail does not contain the PAT
def test_stage_secret_writes_0600_with_expiry_and_discard_removes_it(tmp_path)
```

- [ ] **Step 2: Run** — Expected: FAIL (`ModuleNotFoundError`).
- [ ] **Step 3: Implement** the module, the staging helpers, the compose mount and the delivery contract assertions.
- [ ] **Step 4: Run** the new file plus `tests/test_antithesis_image_build.py tests/test_controller_docker_runtime.py` and `bash delivery/tests/test_delivery_contract.sh` — Expected: PASS (known pre-existing failures excepted).
- [ ] **Step 5: Commit** — `feat(controller): build and publish Antithesis package images in the controller`.

---

### Task 4: Dashboard image build becomes a controller client

**Files:**
- Modify: `dwarf/profile_manager/antithesis_image_build.py:126-261` (`start_build`, `_worker`)
- Test: `tests/test_antithesis_image_build.py`, `tests/browser/test_antithesis_image_build.cjs`

**Interfaces:**
- Consumes: `topology_actions.controller_request`; Task 2 operation; Task 3 result rows.
- Produces: `start_build(preparation_id, *, request_id=None, controller=None)` unchanged in shape for callers; `_worker` sends `antithesis.image-build` with `execution_id = 'antithesis-build-' + uuid4().hex`, polls `execution.status` every `_POLL_SECONDS` (module constant, 2.0), on `applied` rewrites the compose from the returned `built_images` (remove `build:`, set `image:`), writes `receipt.json` exactly as today with `build_host: 'DWARF controller'` and `execution_id`; on failure the row's `message` is the controller `progress.detail` or the canned failure message. No `docker` import remains in the module (delete `_docker`? **No** — `antithesis_artifacts._probe` still uses `builds._docker` inside the controller; keep `_docker`, remove its use from the build path).

- [ ] **Step 1: Rewrite the failing tests**: replace `fake_docker` in the build tests with `_fake_controller(calls, result=...)` (same shape as `tests/test_antithesis_artifacts._fake_controller`); assert the start parameters, the compose rewrite, `receipt.build_host == 'DWARF controller'`, the failure message path, and `test_build_never_invokes_docker_in_the_dashboard` (monkeypatch `builds._docker` to `pytest.fail`).
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `tests/test_antithesis_image_build.py tests/test_antithesis_publication.py tests/test_antithesis_artifacts.py`; update the browser test's server stub to patch `antithesis_image_build.controller_request` instead of `_docker` and run it — Expected: PASS.
- [ ] **Step 5: Commit** — `feat(antithesis): package image build runs in the controller`.

---

### Task 5: Dashboard push becomes a controller client with a one-time PAT

**Files:**
- Modify: `dwarf/profile_manager/antithesis_publication.py:117-174` (`start_operation`, `_push`), `dwarf/profile_manager/data/antithesis_package.py:98-104` (`publication-action` shape gains optional `publish_token`, `save_token`)
- Modify: `dwarf/dashboard/templates/operate/antithesis_run_wizard.j2:193-223` (prefix default = saved `ghcr_prefix`; PAT `type=password` input `#antithesis-publication-pat` with `autocomplete="new-password"`; checkbox `#antithesis-publication-save-pat`, unchecked; copy at :194 replaced), `dwarf/dashboard/static/js/antithesis-publication.js`
- Test: `tests/test_antithesis_publication.py`, `tests/test_antithesis_package.py`, `tests/browser/test_antithesis_publication.cjs`

**Interfaces:**
- Produces: `start_operation(identifier, 'push', *, target=None, request_id=None, publish_token=None, save_token=False, controller=None)`: resolves the token as `publish_token or load_config().moog.ghcr_publish_pat` (none → `PackageError('Enter a GitHub PAT with write:packages or save one in settings.', status=422)`); `secret_id = binary_imports.stage_secret(token)`; the worker sends `antithesis.image-publish` with `username = owner segment of registry_prefix`, `images = [{reference, image_id, destination}]`, `execution_id = 'antithesis-publish-' + uuid4().hex`; polls; on `applied` marks each row `push_completed=True`, state `awaiting-visibility`; on failure resets `push_attempted=False` (the push can be retried) and records the bounded message; `discard_secret(secret_id)` in `finally`. If `save_token` is true, the token is written through `apply_moog_setup_form(load_config(), {'ghcr_publish_pat': token}, partial=True)` + `save_config` **before** staging, and the operation status says `token_saved: True`; otherwise nothing is persisted. Operation status/receipt never contain the token or `secret_id`.
- JS: on Push, reads the PAT field and checkbox, posts them, then clears the PAT field; prefix input pre-filled from `moog_setup` `ghcr_prefix` when empty.

- [ ] **Step 1: Write failing tests**: `test_push_stages_the_token_and_never_records_it`, `test_push_deletes_the_staged_secret_after_the_operation_ends`, `test_failed_push_can_be_retried`, `test_push_uses_the_typed_prefix_without_saving_it`, `test_push_with_save_token_persists_the_pat_through_settings`, `test_push_without_any_token_is_422`, package test for the extended shape.
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement** Python, template and JS.
- [ ] **Step 4: Run** the Python files and the browser test — Expected: PASS.
- [ ] **Step 5: Commit** — `feat(antithesis): GHCR push runs in the controller with a one-time PAT`.

---

### Task 6: Visibility link + polling, readiness checklist

**Files:**
- Modify: `dwarf/profile_manager/antithesis_publication.py:177-203` (`_verify_images`), `dwarf/profile_manager/antithesis_publication_network.py` (`package_settings_urls(destination) -> dict(user=..., org=...)`)
- Modify: `dwarf/profile_manager/antithesis_submission.py:282-349` (`_evaluate`: `CHECK_LABELS` map id → `{group, label}`; `public_image` check per reference instead of one abort), `dwarf/dashboard/static/js/antithesis-submission.js:56-57` (grouped checklist with "external: fix outside DWARF" on `requester_*`/`moog_*`/`github_*` failures and a link to `/operate/config` on `configuration`), `dwarf/dashboard/static/js/antithesis-publication.js` (visibility links + poll `verify-images` every 15 s, max 40 rounds, stop at `images-public`)
- Test: `tests/test_antithesis_publication.py`, `tests/test_antithesis_submission.py`, `tests/browser/test_antithesis_publication.cjs`, `tests/browser/test_antithesis_submission.cjs`

**Interfaces:**
- Produces: `_verify_images` no longer aborts on the first private image: it records `receipt['visibility_required'] = [{service, package, urls: {user, org}, status}]` for every reference whose anonymous read returned 401/403/404 (`PackageError` message prefix "Anonymous public read returned HTTP"), sets state `awaiting-visibility`, and reaches `images-public` only when the list is empty; other errors still fail the operation. `package_settings_urls('ghcr.io/j-gainsec/dwarf-sm-workload:<id>')` → `{'user': 'https://github.com/users/j-gainsec/packages/container/dwarf-sm-workload/settings', 'org': 'https://github.com/orgs/j-gainsec/packages/container/dwarf-sm-workload/settings'}` (package path URL-encoded). Readiness checks gain `group` and `label`; `public_image` yields one check per reference with the reference in `detail`.

- [ ] **Step 1: Write failing tests**: `test_verify_reports_visibility_required_per_package`, `test_package_settings_urls`, `test_readiness_checks_carry_group_and_label`, `test_public_image_checks_are_reported_per_reference`.
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement** Python and JS (browser tests: a `private` control file → link rendered → flip to `ok` → state `images-public` without a manual click; checklist groups rendered).
- [ ] **Step 4: Run** the files and both browser tests — Expected: PASS.
- [ ] **Step 5: Commit** — `feat(antithesis): make-public link with polling and a readiness checklist`.

---

### Task 7: Full suite, deploy, dashboard-driven push

- [ ] **Step 1: Full suite** — Expected: failure set unchanged (66 pre-existing).
- [ ] **Step 2: Deploy** to cardano-box (bundle → `~/dwarf-public-20261105` → `install.sh` with the usual env). The compose change adds the preparations mount; confirm `docker inspect dwarf-controller` shows it `:ro`.
- [ ] **Step 3: Dashboard check as a user**: `/run/antithesis` → select the two adapted patched builds → Preview → Prepare → Build images (controller) → Publication: prefix pre-filled `ghcr.io/j-gainsec` (set in Operate settings first), enter the box's `~/moog-secrets/ghcr.token` value in the PAT field one-time (never printed) → Push → `awaiting-visibility` with the settings link for the workload package → click it in the browser (one manual click, per the user's decision) → polling reaches `images-public` → Verify Git stays manual → Readiness shows the grouped checklist with the requester facts. Record the outcome, the package names pushed and any fix-then-redeploy in the E-pass log.
- [ ] **Step 4: Commit** the E-pass entry.
