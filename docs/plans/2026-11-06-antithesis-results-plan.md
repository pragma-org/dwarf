# Antithesis Results Parity (AT-4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A finished Antithesis run reaches DWARF's one output: the external run gets a real DWARF verdict (pass / fail / inconclusive with a concrete reason) computed from the Antithesis properties table fetched through the MOOG proxy, a local-shaped run bundle, a run page that shows the properties, and inclusion in the measurement compare view — with the signed report URL kept private.

**Architecture:** The dashboard's existing MOOG facts path (`antithesis_submission._refresh`) already learns when a run is terminal. AT-4 adds, at that moment, a result collection step: find the Antithesis run whose `description.testRunId` equals the MOOG test-run id (`moog antithesis runs` through the existing `remote.ssh_command` seam), fetch `run` + `properties`, normalise them into `external/result.json`, write the local bundle layout (`manifest.json`, `chain.json`, `assertions.json`, `scenario.yaml`, `gaps.json`, optional `measurements/report.json`) into the ledger-backed run directory, and feed the manifest through a new `source: antithesis-report` branch of `run_service._external_result_verdict`. The run page and the wizard render the sanitised result; `measurement_compare` stops excluding ledger-bound Antithesis runs.

**Tech Stack:** Python 3.14 (`dwarf/profile_manager`, `dwarf/controller`), Jinja2 templates, vanilla JS wizard, pytest, Playwright `.cjs` browser tests. No Docker anywhere in this plan.

**Spec:** `docs/plans/2026-11-05-antithesis-track-parity-design.md` — Piece 4, BC3 (revised), "Decisions from the user", AT-4 scope block. Facts verified on cardano-box 2026-11-06 are recorded in Task 1's fixtures and in "Facts the spec got wrong" below.

## Facts the spec got wrong (corrected here; ruled, see ledger)

- Piece 4 says "moog.py already wraps `moog antithesis`". It does not; `profile_manager/moog.py` has no Antithesis proxy wrapper. Task 1 adds them in `remote.py` next to `fetch_test_run_facts`, which is the existing proxy-over-ssh primitive.
- The MOOG fact carries the report URL (`value.url`, `…antithesis.com/report/<id>/<id>.html?auth=<secret>`), not an Antithesis run id. The join to the Antithesis run is `moog antithesis runs` → `json.loads(row['description'])['testRunId'] == test_run_id` (verified on the 2026-10-20 run).
- `moog antithesis properties` takes only `--run-id` in moog v0.5.1.3 (no `--limit/--cursor`); it returns `data` + `next_cursor`. Task 1 reads one page and records `properties_truncated` when `next_cursor` is non-null.
- `dwarf-measurements` output is not retrievable as files after a run; only its stdout lines (`progress_summary()` JSON) via `moog antithesis events --q dwarf_measurements` (capped at 50 by the proxy). Task 7 rebuilds a partial `measurements/report.json` from those lines and lists the rest in `gaps.json`.
- `measurement_compare.py` lives at `dwarf/profile_manager/data/measurement_compare.py`; the emitter is `dwarf/scripts/antithesis_measurement_runtime.py`.

## Global Constraints

- Every MOOG/Antithesis call goes through `profile_manager.remote.ssh_command(config, command, timeout=…)` exactly like `fetch_test_run_facts`; the dashboard never shells out directly and never needs Docker.
- The signed report URL (`value.url`) is written only to `<run_dir>/external/report-link.json` with mode 0600 and never appears in any row, API payload, `result.json`, manifest, log line or test fixture committed to the repo.
- Raw proxy payloads are retained under `<run_dir>/external/report/` (dir 0700, files 0600); normalised data is `external/result.json`; the public-facing table shows only property name/kind/status/counts.
- A verdict is `pass` or `fail` only through `run_service._external_result_verdict`; the wizard and the run page display what the ledger records, never a locally computed value.
- Extend existing primitives (`remote.ssh_command`, `run_service`, `RunLedger`, `forensic._canonical_json/_sha256_hex/atomic_write_bytes`, `scenario_verdict.evaluate_scenario_verdict`, `catalog_definitions.load_definition`, `collect_measurement_run_records`); no standalone tool, no new CLI.
- Tests: `PYTHONPATH=dwarf /opt/homebrew/bin/python3.14 -m pytest -q -p no:cacheprovider <files>`; browser `.cjs` with the scratchpad Playwright. Git author `JG <forky@1timeuse.com>`, no attribution trailers, no push.
- Branch: `feat/antithesis-results`, cut from `feat/antithesis-tail` (AT-3 is unmerged and both touch `antithesis_submission.py`).

## Review Focus

1. The MOOG fact URL (or any `?auth=` string) leaks into a row, `result.json`, a manifest, a log or an API payload — the person expects the report link to stay on the host only (`external/report-link.json` 0600).
2. Two Antithesis runs carry the same `testRunId` (a retried launch) — the person expects `inconclusive · antithesis_run_ambiguous`, never a silently chosen run.
3. The proxy is unreachable or returns non-JSON when the run turns terminal — the person expects `inconclusive · report_fetch_failed` with the next Refresh retrying, and no second bundle written over a complete one.
4. The properties table has no DWARF (SDK) property at all (e.g. the workload never started) while every platform property passed — the person expects `inconclusive · no_dwarf_assertions`, not a vacuous pass.
5. MOOG says `finished` while the Antithesis run row is still `in_progress` — the person expects `inconclusive · antithesis_run_not_terminal`, retried on the next Refresh.

---

### Task 1: MOOG Antithesis proxy readers

**Files:**
- Modify: `dwarf/profile_manager/remote.py` (after `fetch_test_run_facts`, ~line 187)
- Test: `tests/test_antithesis_results.py` (new)
- Fixtures: `tests/fixtures/antithesis_proxy/{runs-page-1.json, runs-page-2.json, run-completed.json, run-in-progress.json, properties.json}` — shapes copied from the 2026-10-20 run; `description` keeps the real key shape with a synthetic `testRunId` (`'1'*64`), `links` removed, images reduced to two.

**Interfaces:**
- Produces: `fetch_antithesis(config, moog_config, subcommand, *, run_id=None, limit=None, cursor=None, query=None, timeout=60) -> dict` returning `{'ok': bool, 'payload': parsed JSON | list[dict] for events/logs/build-logs | None, 'exit_code': int, 'error': str}` (error ≤ 200 chars, `''` when ok). Command: `<moog_binary> antithesis <subcommand> [--run-id R] [--limit N] [--cursor C] [--q Q]` with every value `shlex.quote`d; no `MOOG_*` env (the OAuth file authorises).
- Produces: `find_antithesis_run(config, moog_config, test_run_id, *, max_pages=5, limit=50) -> dict` → `{'status': 'found'|'not_found'|'ambiguous'|'error', 'run': row|None, 'matches': int, 'pages': int, 'error': str}` by `json.loads(row['description']).get('testRunId') == test_run_id`; follows `next_cursor` up to `max_pages`; a row whose description is not JSON is skipped.

- [ ] **Step 1: Write the failing tests**

```python
def test_fetch_antithesis_builds_the_proxy_command_without_moog_env(monkeypatch):
    seen = {}
    def ssh(config, command, timeout=None, **kw):
        seen['command'] = command; return CommandResult(0, '{"data":[],"next_cursor":null}', '', command)
    monkeypatch.setattr(remote, 'ssh_command', ssh)
    out = remote.fetch_antithesis(config, moog_config, 'runs', limit=50, cursor='Mg==')
    assert out == {'ok': True, 'payload': {'data': [], 'next_cursor': None}, 'exit_code': 0, 'error': ''}
    assert seen['command'] == "/bin/moog antithesis runs --limit 50 --cursor Mg=="
    assert 'MOOG_' not in seen['command']

def test_fetch_antithesis_reports_exit_codes_and_bad_json(monkeypatch): ...  # exit 3 → ok False, error 'proxy or network failure (exit 3)'; exit 0 + 'nope' → error startswith 'invalid JSON'
def test_fetch_antithesis_parses_ndjson_for_events(monkeypatch): ...        # 'events' → payload list of dicts; blank lines skipped; non-JSON line → error, payload None

def test_find_antithesis_run_joins_on_the_description_test_run_id(monkeypatch):  # page 1 has no match, page 2 has exactly one → found, pages == 2
def test_find_antithesis_run_is_ambiguous_when_two_rows_match(monkeypatch):      # → status 'ambiguous', run None, matches 2
def test_find_antithesis_run_stops_at_max_pages_and_reports_not_found(monkeypatch)
def test_find_antithesis_run_propagates_proxy_errors(monkeypatch)                # → status 'error', error text
```

- [ ] **Step 2: Run to verify they fail** — `pytest tests/test_antithesis_results.py -q` — Expected: FAIL `AttributeError: module 'profile_manager.remote' has no attribute 'fetch_antithesis'`.
- [ ] **Step 3: Implement `fetch_antithesis` and `find_antithesis_run` in `remote.py`** (one line of approach: build argv list, `shlex.join`, call `ssh_command`, map exit codes 2/3/4 to the texts the moog help prints; NDJSON for `events`, `logs`, `build-logs`).
- [ ] **Step 4: Run to verify pass** — Expected: 7 passed.
- [ ] **Step 5: Commit** — `feat(antithesis): read Antithesis runs and properties through the MOOG proxy over ssh`

### Task 2: Result normaliser and verdict rules

**Files:**
- Create: `dwarf/profile_manager/antithesis_results.py`
- Test: `tests/test_antithesis_results.py`

**Interfaces:**
- Produces: `RESULT_SCHEMA = 'dwarf.antithesis-result.v1'`; `property_kind(name) -> 'always'|'sometimes'|'platform'` (`Always:`/`Unreachable:` → always, `Sometimes:`/`Reachable:` → sometimes, else platform; case-sensitive prefix followed by a space).
- Produces: `normalise_result(run, properties, *, test_run_id) -> dict`: `{'schema_version', 'test_run_id', 'antithesis_run_id': run['run_id'], 'run_status': run['status'], 'created_at', 'completed_at', 'environment': {'antithesis_version', 'images': [{'name','tag'}]}, 'properties': [{'id','name','kind','status','passed': status=='Passing','example_count','counterexample_count'}], 'properties_truncated': bool(next_cursor), 'dwarf_property_count', 'failing': [names]}` — never copies `links`, `description`, `parameters`, `counterexamples` or `examples`.
- Produces: `result_verdict(result) -> tuple[str, str]`: `('inconclusive','antithesis_run_not_terminal')` if `run_status != 'completed'`; `('inconclusive','properties_unavailable')` if `properties is None` / empty table; `('inconclusive','no_dwarf_assertions')` if `dwarf_property_count == 0`; `('fail','assertions_failed')` if any DWARF property failing; `('fail','platform_property_failed')` if any platform property failing; `('fail','sometimes_not_encountered')` if a `sometimes` property has `example_count == 0`; else `('pass','verified_antithesis_report')`.
- Produces: `result_assertions(result) -> list[dict]`: one row per property `{'id','name','kind','source': 'dwarf'|'antithesis','result': 'pass'|'fail'}` (a `sometimes` with no examples is `fail`).

- [ ] **Step 1: Write the failing tests** (`test_property_kind_prefixes`, `test_normalise_result_keeps_only_the_sanitised_fields` (asserts `'links' not in json.dumps(result)` and `'auth=' not in …`), `test_result_verdict_rules` parametrised over the seven outcomes above, `test_result_assertions_mark_unencountered_sometimes_as_fail`).
- [ ] **Step 2: Run — Expected: FAIL `ModuleNotFoundError: profile_manager.antithesis_results`.**
- [ ] **Step 3: Implement the module.**
- [ ] **Step 4: Run — Expected: PASS.**
- [ ] **Step 5: Commit** — `feat(antithesis): normalise the Antithesis properties table into a DWARF result with verdict rules`

### Task 3: Local-shaped bundle writer

**Files:**
- Modify: `dwarf/profile_manager/antithesis_results.py`
- Test: `tests/test_antithesis_results.py`

**Interfaces:**
- Consumes: `forensic._canonical_json`, `forensic._sha256_hex`, `forensic.atomic_write_bytes`; `scenario_verdict.evaluate_scenario_verdict`.
- Produces: `write_result_bundle(run_dir: Path, *, entry: Mapping, result: dict, raw: dict[str, Any], scenario_source: bytes | None, measurements: dict | None) -> Path` (returns the manifest path). Writes: `external/report/run.json`, `external/report/properties.json` (from `raw`, dir 0700 files 0600); `external/result.json`; `assertions.json` (= `result_assertions`); `manifest.json` canonical with `{'schema_version': 'v1', 'run_id': run_dir.name, 'kind': 'external', 'runtime': 'antithesis', 'backend': 'antithesis', 'scenario': entry['scenario'] ({id, sha256}), 'external': {'backend': 'antithesis', 'test_run_id', 'antithesis_run_id', 'run_status'}, 'exit_status': 'pass' if run_status == 'completed' else 'fail', 'started_at': created_at, 'ended_at': completed_at, 'assertion_summary': {'total','pass','fail'} over all assertions, 'execution': {'security_verdict': evaluate_scenario_verdict(exit_status, assertions=summary, measurements=None, run_kind='external').security_verdict}, 'result': {'source': 'antithesis-report', 'sha256': sha256(result.json bytes)}, 'measurements': measurements or omitted}`; `chain.json` `{'run_id', 'manifest_hash', 'source': 'antithesis-report'}`; `scenario.yaml` when `scenario_source`; `gaps.json` `{'schema_version': 'v1', 'runtime': 'antithesis', 'missing': [{'path','reason'}]}` listing `probes/`, `metrics/`, `launch/`, the evidence volume and — when `measurements is None` — `measurements/report.json`.
- Rule: the writer refuses (`ValueError('result bundle already complete')`) when `manifest.json` exists and `chain.json` binds it (no overwrite of a complete bundle); callers check first.

- [ ] **Step 1: Failing tests** — `test_write_result_bundle_lays_out_local_shape_and_binds_the_chain` (files exist; `measurement_compare._evidence_state(run_id, manifest, chain) == ('bound', hash)`; `'auth=' not in` any file under run_dir except `external/report-link.json` which this writer never creates), `test_write_result_bundle_refuses_to_overwrite_a_complete_bundle`, `test_bundle_manifest_verdict_agrees_with_result_verdict` (pass/fail cases).
- [ ] **Step 2: RED — Expected: `AttributeError: write_result_bundle`.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: GREEN.**
- [ ] **Step 5: Commit** — `feat(antithesis): write the local run-bundle layout from an Antithesis result`

### Task 4: Verdict branch in run_service

**Files:**
- Modify: `dwarf/profile_manager/run_service.py:869-1121` (`_external_result_verdict`, `reconcile_external_status`)
- Test: `tests/test_run_service.py`

**Interfaces:**
- Consumes: `antithesis_results.result_verdict`, `RESULT_SCHEMA`.
- Produces: `_external_result_verdict(..., result_path: Path | str | None = None)` and `reconcile_external_status(..., result_path=None)`. When `result_path` is given and the three bundle inputs are None: load `result.json` (object, `schema_version == RESULT_SCHEMA`), `manifest.json`, `chain.json` from `Path(entry['evidence_path'])`; checks in order → reasons: `antithesis_result_invalid`, `result_bundle_scenario_unbound` (entry has no scenario), `result_bundle_scenario_identity_mismatch`, `result_bundle_external_identity_mismatch` (result/manifest `test_run_id` ≠ `external_test_run_id` ≠ provenance), `result_bundle_manifest_hash_mismatch`, `antithesis_result_hash_mismatch` (manifest.result.sha256 ≠ sha256(result.json)); then `verdict, reason = result_verdict(result)`; inconclusive → `('inconclusive', None, reason, {'antithesis_result': {'antithesis_run_id', 'dwarf_property_count', 'failing'}})`; else canonical `evaluate_scenario_verdict` must equal `manifest.execution.security_verdict` == verdict (else `result_bundle_verdict_mismatch`) → `(verdict, str(manifest_path), 'verified_antithesis_report', {'antithesis_result': {...}, 'result_bundle_manifest_sha256': hash})`.

- [ ] **Step 1: Failing tests** — `test_antithesis_report_verdict_pass_fail_and_inconclusive` (reserve with `scenario_path` of a real catalog scenario, record submission, write a bundle via Task 3 with a passing result → `completed`/`pass`/`verified_antithesis_report`; failing → `fail`; `no_dwarf_assertions` → `inconclusive`), `test_antithesis_report_requires_scenario_binding` (reserve without scenario → `result_bundle_scenario_unbound`), `test_antithesis_report_rejects_foreign_test_run_id`, `test_terminal_inconclusive_run_accepts_a_later_antithesis_report` (first reconcile without result → inconclusive; second with result → `reconcile_terminal_evidence` path yields `pass`).
- [ ] **Step 2: RED — Expected: `TypeError: unexpected keyword argument 'result_path'`.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: GREEN; existing external tests unchanged.**
- [ ] **Step 5: Commit** — `feat(run-service): verdict from an Antithesis report beside the signed-bundle branch`

### Task 5: Submission binds the scenario and collects the result

**Files:**
- Modify: `dwarf/profile_manager/antithesis_submission.py` (`_reserve` ~419, `_bind_observed_fact` ~461 link-existing reserve, `_refresh` ~588)
- Test: `tests/test_antithesis_submission.py`

**Interfaces:**
- Consumes: Task 1 `find_antithesis_run`, `fetch_antithesis`; Task 2/3; Task 4 `result_path`.
- Produces: `_scenario_record(row) -> DefinitionRecord | None` (scenario id from the source receipt: preparation → `receipt['request']['scenario_id']`; build → `prep.read_preparation(receipt['preparation_id'])`; publication → via its `build_id`; `load_definition('scenarios', id)`; None on `CatalogError`). Both `ExternalRunRequest(...)` constructions pass `scenario_path=record.path`.
- Produces: `collect_result(row, config, fact) -> dict` called from `_refresh` after `reconcile_external_status` when `safe_phase in {'finished','completed','failed','rejected','cancelled','error'}`: (1) if `value.get('url')` → write `<run_dir>/external/report-link.json` `{'url', 'observed_at'}` 0600 (skip if present); (2) if `manifest.json` + bound chain exist → load `result.json` summary; else `find_antithesis_run` → found: `fetch_antithesis('run')` + `fetch_antithesis('properties')` → `normalise_result` → `write_result_bundle(scenario_source=record.source)` → `reconcile_external_status(..., result_path=…)`; (3) every non-found/error path returns `{'state': 'unavailable', 'reason': <code>}` with codes `antithesis_run_not_found|antithesis_run_ambiguous|report_fetch_failed|…` and the row keeps the previous status. Row gets `row['result'] = {'state': 'verified'|'unavailable', 'verdict', 'reason', 'antithesis_run_id', 'failing': names[:10], 'dwarf_property_count', 'run_page': '/operate/runs/' + dwarf_run_id}` — never a URL.
- `_refresh` message when verified: `'DWARF verdict ' + verdict + ' (' + reason + '). Open the run page for the properties table.'`

- [ ] **Step 1: Failing tests** — `test_reserve_binds_the_package_scenario` (ledger entry has `scenario.id == 'cardano-amaru-miniprotocol-security-local'`), `test_refresh_collects_the_result_and_records_a_verdict` (fake `remote.fetch_antithesis`/`find_antithesis_run`; row.result.verdict == 'fail' for the fixture with a failing platform property; `external/report-link.json` mode 0600 holds the URL; `'auth=' not in json.dumps(row)`), `test_refresh_keeps_inconclusive_when_the_proxy_fails_and_retries_next_time`, `test_refresh_does_not_rewrite_a_complete_bundle`, `test_ambiguous_antithesis_runs_are_inconclusive`.
- [ ] **Step 2: RED.** **Step 3: Implement.** **Step 4: GREEN (whole file).**
- [ ] **Step 5: Commit** — `feat(antithesis): bind the scenario at reservation and collect the Antithesis result on refresh`

### Task 6: Run page and wizard show the result

**Files:**
- Modify: `dwarf/profile_manager/data/operate_run.py` (`_external_result_verification` ~370, `_external_run_detail` ~543-820), `dwarf/dashboard/templates/operate/external_run.j2`, `dwarf/dashboard/static/js/antithesis-submission.js`
- Test: `tests/test_external_run_dashboard.py`, `tests/browser/test_antithesis_submission.cjs`

**Interfaces:**
- `_external_result_verification` accepts `verdict_reason == 'verified_antithesis_report'` when `provenance.antithesis_result` is an object, `manifest.external.antithesis_run_id` matches it, and sha256(`external/result.json`) == `manifest.result.sha256`; otherwise the existing bundle rules.
- `run.external['result']` = `{'source': 'antithesis-report', 'antithesis_run_id', 'run_status', 'verdict_reason', 'dwarf_property_count', 'properties': [{'name','kind','status','example_count','counterexample_count'}] (≤ 200 rows), 'report_link_retained': bool, 'gaps': [...]}` or `None`.
- Template: a "Properties" table section; the "Why this run is inconclusive" block gains text for the new reasons; the retained-link line says "The signed report link is retained privately on the host" and never prints it.
- Wizard: after refresh, a line `DWARF verdict: <verdict> · <reason>` with a link to `row.result.run_page`; unavailable → `Result not collected yet: <reason>. Refresh again later.`

- [ ] **Step 1: Failing tests** — `test_external_run_page_shows_the_antithesis_properties_and_hides_the_link`, `test_antithesis_report_verdict_is_verified_on_the_run_page`; browser: refresh on a stubbed terminal fact with stubbed proxy → verdict line + link.
- [ ] **Step 2: RED.** **Step 3: Implement.** **Step 4: GREEN.**
- [ ] **Step 5: Commit** — `feat(dashboard): Antithesis properties and DWARF verdict on the external run page and wizard`

### Task 7: Compare view inclusion and partial measurements

**Files:**
- Modify: `dwarf/profile_manager/data/measurement_compare.py:194-196, 409-470`, `dwarf/profile_manager/antithesis_results.py`, `antithesis_submission.collect_result`
- Test: `tests/test_measurement_compare.py` (existing file; add), `tests/test_antithesis_results.py`

**Interfaces:**
- `collect_measurement_run_records` adds `'external_test_run_id': _text(_dict(manifest.get('external')).get('test_run_id'))`; `is_antithesis_record(record)` = `record.get('runtime') == 'antithesis' and not record.get('external_test_run_id')` (hand-imported archives keep their own concept table; ledger-bound runs join the catalog concepts).
- `rebuild_measurements(events: list[dict], *, scenario: str, target_identity: dict) -> dict | None`: parses `output_text` JSON lines with key `dwarf_measurements`; merges `collectors[*].measurements{name:{status,value,unit}}` (last line per service wins) into `{'schema_version': 'v1', 'scenario', 'target_identity', 'source': 'antithesis-events', 'complete': False, 'measurements': {name: {'status','value','unit'}}}`; `None` when no such line. `collect_result` calls `fetch_antithesis('events', query='dwarf_measurements')` once and passes the result as `measurements`; `target_identity` = `{'engine': 'antithesis', 'implementation', 'mode': 'stock'|'patched', 'version'}` from the source receipt's `version_binding` of the Amaru node (ruling: one target per package for now).

- [ ] **Step 1: Failing tests** — `test_ledger_bound_antithesis_record_joins_catalog_concepts`, `test_hand_imported_antithesis_record_keeps_its_own_table`, `test_rebuild_measurements_from_progress_lines`, `test_rebuild_measurements_returns_none_without_lines`.
- [ ] **Step 2: RED.** **Step 3: Implement.** **Step 4: GREEN.**
- [ ] **Step 5: Commit** — `feat(compare): include ledger-bound Antithesis runs; rebuild partial measurements from proxy events`

### Task 8: Live pass on cardano-box (E-pass)

**Files:** `docs/plans/2026-11-05-e-pass-validation-log.md` (append)

- [ ] Deploy the branch (bundle → `~/dwarf-public-20261105` → `install.sh`).
- [ ] In the dashboard: Operate settings → MOOG (enable, token id, binary `/home/nigel/bin/moog`, mpfs host, secrets root from the box's legacy config) → save.
- [ ] Wizard: readiness for the retained 2026-10-20 attempt (repo `cyber-castellum/dwarf`, directory `antithesis/cardano_amaru_miniprotocol_selected_validation`, commit `14b95fbff2d566ef054bd3499a1a94756eb4fda4`, requester `j-gainsec`, try 1) → the existing fact is matched (link-existing) → Refresh → result collected from run `788998d60b09b96953e830e9f9ae2c7b-62-14` → wizard shows the verdict line → run page shows 50 properties, verdict `fail` (platform `No Antithesis errors` failing; count the DWARF properties) → compare view lists the run.
- [ ] Record findings (F33+) and outcome; commit `docs: E-pass log — AT-4 results proven on cardano-box`.
