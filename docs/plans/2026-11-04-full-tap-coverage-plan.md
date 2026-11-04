# Full Tap Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every measurement tap concept has a value on at least one run of each implementation in the Pages default set, except the three documented exceptions, proven by reruns of the four operator-build workload legs and guarded by a coverage test.

**Architecture:** Collector fixes (in-container process probe, reset-tolerant counters, evidence-backed zero counters), workload additions (double spend, mempool-visibility join, cardano peer submission entry, Amaru N2N rejection serve), cardano patched tap identity (capabilities by recipe revision, operator evidence in place of the probe log, taps enabled in the patched profile), a concept-level coverage rule wired into the Pages staging, then four reruns on cardano-box and a replaced Pages set.

**Tech Stack:** Python 3 (stdlib, pytest), Docker CLI via subprocess, cardano-cli inside the target image, raw Ouroboros mux/TxSubmission2 CBOR in the probe, cardano-box drivers (`~/prepare.sh`, `~/runplan.sh`, `~/workload-queue.sh`, `~/tapreport.py`).

**Spec:** `docs/plans/2026-11-04-full-tap-coverage-design.md`

## Global Constraints

- Repo `/Users/nigel/dwarf-project/dwarf-fable/V10-FRESH`, branch `feat/full-tap-coverage` (already carries the design and the cherry-picked `19d6412b`, `203056ec`). Commit author `JG <forky@1timeuse.com>`. No push until the user asks.
- Python tests: `cd <repo> && PYTHONPATH=dwarf python3.14 -m pytest -q -p no:cacheprovider <files>`. Box image cross-check: scratchpad `boxtest.sh <files>`. Baseline failures on this Mac and on `main`: 68 (list in scratchpad `suite-main.txt`); none of them may grow.
- Framing in every docstring and scenario text: authorized defensive conformance testing; nothing invents a number. A zero counts only with an evidence denominator (`offered_count`).
- Entry-node boundaries are labelled `boundary_node`; peer-submitted records drop `submit_to_protocol_response` and `submit_to_chain_adoption`.
- No host PID namespace for the dashboard; process stats come from `docker exec <id> sh -c` reading `/proc` (POSIX sh, `cat`, no `grep`).
- Scenario files stay shared between stock and patched reruns; patched-only taps are enabled through `dwarf/measurement-profiles/cardano-security-patched.yaml`.
- Box: deploy with `bash delivery/scripts/build-image.sh && bash delivery/scripts/deploy.sh` from `~/dwarf-v10-fresh-sync` on the branch; a profile-flag-only change does not trigger a redeploy (prepare must see an image change); restart gate 900 s; Amaru redeploy waits for epoch ≥ 3 (~20 min).
- Real reports for fixtures: `/private/tmp/claude-501/-Users-nigel-dwarf-project-dwarf-v4/0d1bdaff-3ff1-4cb0-acec-ed645746aea6/scratchpad/exports-workload/_x/<run>/<run>/measurements/...` and `tests/fixtures/workload_run_metric_ids.json`.

## Review Focus

1. `docker exec` fails on a target (distroless image, exited container): the stats sample is still recorded with process fields None and an `errors` note; the collector never aborts. Test in Task 1.
2. Counter series with a reset at the last sample, or a single sample: delta is the sum of positive steps (0 for one sample, status unavailable with reason "at least two monotonic counter samples are required"). Test in Task 1.
3. A double-spend submit that unexpectedly succeeds: the record's `outcome` is `accepted-unexpected`, it is not counted as rejected, and the run still completes. Test in Task 3.
4. Mempool-visibility join when the same tx id appears twice in the trace (resubmission): only the first event at or after `submit_started_at_ns` counts; events before it never match. Test in Task 3.
5. An operator identity with neither `runtime_probe_log_sha256` nor `verified_instrumentation.evidence_sha256`: the three patched taps still refuse to prepare. Test in Task 5.

---

### Task 1: Resources — in-container process probe and reset-tolerant counters

**Files:**
- Modify: `dwarf/profile_manager/measurement_collectors/cardano_resources.py:158-219` (`_docker_container_sample`)
- Modify: `dwarf/profile_manager/measurement_collectors/amaru_resources.py:14-58` (`_distribution`, `_counter_delta`)
- Test: `tests/test_amaru_resource_collector.py`

**Interfaces:**
- Produces: `PROC_PROBE_SCRIPT: str` (POSIX sh; prints lines `pid=<n>`, `clk_tck=<n>`, `vmrss_kb=<n>`, `threads=<n>`, `fds=<n>`, `utime=<ticks>`, `stime=<ticks>`; chooses PID 1 when `/proc/1/comm` is `amaru` or `cardano-node`, else the first `/proc/[0-9]*/comm` equal to `cardano-node` or `amaru`; `clk_tck` from `getconf CLK_TCK` else 100). `_proc_probe(identity, *, docker_run) -> dict` returning `{"rss_bytes", "threads", "fd_count", "cpu_time_seconds", "probe_mode": "docker-exec-proc"}` or `{"probe_mode": "unavailable", "error": "<text>"}` on any failure (non-zero exit, unparsable output). `_docker_container_sample` merges the probe result; on failure the four fields stay None and `sample["errors"] = [error]`.
- Produces: `_counter_delta(samples, field, unit, *, scope=None)` result gains `"resets": int` and `"delta_rule": "sum-of-steps-with-resets"`; `delta` = sum of max(0, step) across consecutive samples, where a negative step counts as a reset and the sum continues from the new value; `status` is `available` whenever ≥2 points and duration > 0. `_distribution` with no samples returns `reason: "no samples from this source"`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_amaru_resource_collector.py`; reuse the file's `docker_run` fake pattern from `test_cardano_resource_collector_samples_exact_owned_container_cgroup`)

```python
def test_docker_sample_reads_process_stats_inside_the_target(tmp_path):
    def docker_run(command, **_kwargs):
        if command[:2] == ["docker", "stats"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps({"ID": "abc123", "Name": "amaru-relay-1", "CPUPerc": "12.5%", "PIDs": "28", "MemUsage": "1.2GiB / 8GiB", "NetIO": "684kB / 20kB", "BlockIO": "9.66MB / 0B"}) + "\n")
        if command[:2] == ["docker", "exec"]:
            assert command[2] == "abc123def" and command[3:5] == ["sh", "-c"]
            return SimpleNamespace(returncode=0, stdout="pid=1\nclk_tck=100\nvmrss_kb=204800\nthreads=29\nfds=31\nutime=1234\nstime=66\n")
        raise AssertionError(command)
    sample = _docker_container_sample({"container_id": "abc123def", "container_name": "amaru-relay-1"}, docker_run=docker_run)
    assert sample["rss_bytes"] == 204800 * 1024 and sample["threads"] == 29 and sample["fd_count"] == 31
    assert sample["cpu_time_seconds"] == 13.0 and sample["probe_mode"] == "docker-exec-proc"
    assert sample["network_rx_bytes"] == 684000 and sample["container_tasks"] == 28

def test_docker_sample_survives_a_failed_process_probe(tmp_path):
    def docker_run(command, **_kwargs):
        if command[:2] == ["docker", "stats"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps({"ID": "abc123", "Name": "n", "CPUPerc": "1%", "PIDs": "3", "MemUsage": "1MiB / 8GiB", "NetIO": "0B / 0B", "BlockIO": "0B / 0B"}) + "\n")
        return SimpleNamespace(returncode=126, stdout="", stderr="exec: sh: not found")
    sample = _docker_container_sample({"container_id": "abc123", "container_name": "n"}, docker_run=docker_run)
    assert sample["rss_bytes"] is None and sample["probe_mode"] == "unavailable"
    assert "sh: not found" in sample["errors"][0]

def test_counter_delta_sums_steps_across_a_reset():
    samples = [{"monotonic_seconds": t, "network_rx_bytes": v} for t, v in ((0, 1000), (1, 1500), (2, 2000), (3, 100), (4, 400))]
    result = _counter_delta(samples, "network_rx_bytes", "bytes")
    assert result["status"] == "available" and result["delta"] == 1300 and result["resets"] == 1
    assert result["delta_rule"] == "sum-of-steps-with-resets" and result["rate_per_second"] == 325.0
    last_reset = _counter_delta(samples[:3] + [{"monotonic_seconds": 3, "network_rx_bytes": 0}], "network_rx_bytes", "bytes")
    assert last_reset["delta"] == 1000 and last_reset["resets"] == 1
    single = _counter_delta(samples[:1], "network_rx_bytes", "bytes")
    assert single["status"] == "unavailable" and single["reason"] == "at least two monotonic counter samples are required"

def test_distribution_without_samples_names_its_reason():
    assert _distribution([], "rss_bytes", "bytes")["reason"] == "no samples from this source"
```
Import `_docker_container_sample` from `cardano_resources` and `_counter_delta`, `_distribution` from `amaru_resources` at the top of the test file.

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=dwarf python3.14 -m pytest -q -p no:cacheprovider tests/test_amaru_resource_collector.py -k "process_stats or failed_process_probe or sums_steps or names_its_reason"`
Expected: FAIL (`KeyError: 'probe_mode'`, `assert ... == 1300`, missing `reason`).

- [ ] **Step 3: Implement** `PROC_PROBE_SCRIPT`, `_proc_probe`, the merge in `_docker_container_sample` (cardano_resources.py) and the new `_counter_delta`/`_distribution` (amaru_resources.py). The exec command is `["docker", "exec", identity["container_id"], "sh", "-c", PROC_PROBE_SCRIPT]` with `timeout=_DOCKER_STATS_TIMEOUT_SECONDS`.

- [ ] **Step 4: Run the file**

Run: `PYTHONPATH=dwarf python3.14 -m pytest -q -p no:cacheprovider tests/test_amaru_resource_collector.py tests/test_cardano_measurement_factories.py tests/test_amaru_measurement_factories.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add dwarf/profile_manager/measurement_collectors/cardano_resources.py dwarf/profile_manager/measurement_collectors/amaru_resources.py tests/test_amaru_resource_collector.py
git commit -m "collectors(resources): in-container process probe; counters survive restarts"
```

---

### Task 2: Accounting evidence — backlog, zero counters, entry-node stages

**Files:**
- Modify: `dwarf/profile_manager/primitives.py:14016-14040` (workload_accounting payload), `dwarf/scripts/runtime_controlled_plutus_transactions.py:270-310` (`_plutus_workload_accounting`), `dwarf/profile_manager/measurement_collectors/amaru_external.py:57-65,150-185,280-330`, `dwarf/profile_manager/measurement_report.py:120-140` (`backlog_summary`), `dwarf/profile_manager/measurement_collectors/amaru_stock.py:573-578` (`_count` for mempool), `cardano_stock.py:226-232,300-310`
- Test: `tests/test_amaru_external_collectors.py`, `tests/test_controlled_plutus_transactions.py`, `tests/test_amaru_stock_collector.py`, `tests/test_cardano_stock_collector.py`

**Interfaces:**
- Produces: `_plutus_workload_accounting(records, implementation, *, boundary_node=None) -> dict` — attempts carry `boundary_node` (string or None); `submit_to_protocol_response_*` and `submit_to_chain_adoption_*` are copied for every implementation when the record has them, `record.get("relay_forwarded") is not False`, `not record.get("fallback_errors")` and `record.get("submission_entry", "target") == "target"` or the implementation is `amaru` (whose entry is always labelled); payload `backlog` = `max(1, in_flight)` per attempt event, emitted as one `workload_accounting` event per attempt (`attempts` holds that attempt only, cumulative totals kept in the final event as today), `timed_out` key present.
- Produces: `_value_metric(value, unit, *, reason=None, offered_count=None)` adds `offered_count` when given; `rejected_operations`/`timed_out_operations` get `offered_count=attempted`; each stage distribution gets `boundary_node` (the single label among contributing attempts, else None); `backlog_summary` empty result adds `reason: "no backlog samples were retained"`. Stock `mempool_rejected` gets `offered_count` = number of `mempool-received`/`mempool-attempt`/`mempool-accepted` events (`_count(..., offered_count=...)`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_controlled_plutus_transactions.py
def test_amaru_attempts_keep_entry_node_stages_with_their_label():
    record = {**_RECORD, "submit_to_protocol_response_nanos": 5000, "submit_to_protocol_response_micros": 5.0,
              "submit_to_chain_adoption_nanos": 9000, "submit_to_chain_adoption_micros": 9.0, "relay_forwarded": True, "fallback_errors": []}
    out = _plutus_workload_accounting([record], "amaru", boundary_node="amaru-consumer")
    attempt = out["attempts"][0]
    assert attempt["boundary_node"] == "amaru-consumer" and attempt["submit_to_protocol_response_nanos"] == 5000
    dropped = _plutus_workload_accounting([{**record, "fallback_errors": ["resubmitted"]}], "amaru", boundary_node="amaru-consumer")["attempts"][0]
    assert "submit_to_protocol_response_nanos" not in dropped
    peer = _plutus_workload_accounting([{**record, "submission_entry": "peer"}], "cardano-node", boundary_node="node2")["attempts"][0]
    assert "submit_to_protocol_response_nanos" not in peer and "submit_to_block_inclusion_nanos" in peer
    assert out["timed_out"] == 0 and out["backlog"] == 0

# tests/test_amaru_external_collectors.py
def test_zero_counters_carry_their_offered_count_and_backlog_curve(tmp_path):
    ... build two workload_accounting events 1 s apart with backlog 1 then 0, attempted 20, rejected 1, timed_out 0 ...
    report = collector.finalize(...)["measurements"]
    assert report["timed_out_operations"] == {"status": "available", "unit": "operations", "value": 0, "offered_count": 20}
    assert report["rejected_operations"]["offered_count"] == 20
    assert report["backlog"]["status"] == "available" and report["backlog"]["peak"] == 1
    assert report["submit_to_protocol_response"]["boundary_node"] == "amaru-consumer"

def test_backlog_summary_without_points_explains_itself():
    assert backlog_summary([])["reason"] == "no backlog samples were retained"

# tests/test_amaru_stock_collector.py and tests/test_cardano_stock_collector.py
def test_mempool_rejected_zero_carries_received_count(...):
    assert metrics["mempool_rejected"] == {"status": "available", "unit": "events", "value": 0, "offered_count": 40}
```
Use each file's existing fixture helpers for events/records.

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=dwarf python3.14 -m pytest -q -p no:cacheprovider tests/test_controlled_plutus_transactions.py tests/test_amaru_external_collectors.py tests/test_amaru_stock_collector.py tests/test_cardano_stock_collector.py -k "entry_node or offered_count or backlog_summary or received_count"`
Expected: FAIL (`TypeError: unexpected keyword boundary_node`, missing keys).

- [ ] **Step 3: Implement** per the Interfaces block. In `primitives.py` the per-attempt events: loop over `attempts` emitting `workload_accounting` with `attempts=[row]`, `backlog=1` for the row and a final cumulative event with `backlog=0`; keep totals identical to today. The `boundary_node` passed by the primitive is `self.params.get("submission_entry", "target")` mapped to `"amaru-consumer"`/`"p1"` (Amaru) or `"node1"`/`"node2"` (cardano, Task 4 defines the param; default `"node1"`).

- [ ] **Step 4: Run the four files**

Run: `PYTHONPATH=dwarf python3.14 -m pytest -q -p no:cacheprovider tests/test_controlled_plutus_transactions.py tests/test_amaru_external_collectors.py tests/test_amaru_stock_collector.py tests/test_cardano_stock_collector.py tests/test_amaru_measurement_e2e_scenario.py tests/test_cardano_measurement_e2e_scenario.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add dwarf/profile_manager/primitives.py dwarf/scripts/runtime_controlled_plutus_transactions.py dwarf/profile_manager/measurement_collectors/amaru_external.py dwarf/profile_manager/measurement_report.py dwarf/profile_manager/measurement_collectors/amaru_stock.py dwarf/profile_manager/measurement_collectors/cardano_stock.py tests/
git commit -m "measurement(accounting): backlog curve, evidence-backed zero counters, labelled entry-node stages"
```

---

### Task 3: Workload records — submit timestamp, double spend, mempool-visibility join

**Files:**
- Modify: `dwarf/scripts/runtime_cardano_measurement_calibration.py:509-700` (wrapper prints `submit_started_at_ns=`), `:738-960` (`run_plutus_workload`), new helpers in `dwarf/scripts/runtime_controlled_plutus_transactions.py`
- Test: `tests/test_cardano_measurement_e2e_scenario.py`, `tests/test_controlled_plutus_transactions.py`

**Interfaces:**
- Produces: wrapper script line `printf 'submit_started_at_ns=%s\n' "$(date +%s%N)" >&2` emitted only when `"$1 $2 $3" = "conway transaction submit"`; `_checked` already captures stderr; `parse_submit_started_at(stderr: str) -> int | None`.
- Produces: `run_plutus_workload(..., double_spend_every: int = 20)` — after every `double_spend_every` spends (and at the end of the batch), resubmits the last accepted spend tx file; records `{"attempt_id": "plutus-<index>-double-spend", "outcome": "rejected", "chain_outcome": "rejected-phase1", "spend_transaction_id": <id>, "rejected_reason": <first stderr line>, "transaction_bytes": n, "elapsed_*", "submit_to_block_inclusion_*" absent}`; when the submit unexpectedly succeeds the record has `outcome: "accepted-unexpected"` and the run continues. Every spend record gains `submit_started_at_ns`.
- Produces: `join_mempool_visibility(records: list[dict], events: Iterable[dict]) -> int` in `runtime_controlled_plutus_transactions.py` — `events` are normalised stock mempool events (`{kind, tx_id, timestamp}` as produced by `amaru_stock`/`cardano_stock` normalisers); for each record with `spend_transaction_id` and `submit_started_at_ns`, the first `mempool-received`/`mempool-attempt`/`mempool-accepted` event with that id and timestamp ≥ submit time sets `submit_to_mempool_visibility_nanos/_micros`; returns the number joined. `load_mempool_events(implementation, trace_paths) -> list[dict]` reuses `amaru_stock`'s and `cardano_stock`'s public normalisers (export `normalize_trace_line` from each if not already public). `_plutus_workload_accounting` copies `submit_to_mempool_visibility_*` when present.
- Produces: the cardano helper's `run_leg` runs the join after the workload against `output_dir/raw/node1.ndjson` (already captured) and the Amaru helper against the relay's JSON log it already captures for the stock collectors (`logs/amaru-relay-1/*.json` or the `docker logs` capture used by `_latest_tip`); the result is written into `plutus_workload["records"]` before the report.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_controlled_plutus_transactions.py
def test_mempool_visibility_joins_first_event_after_submit_only():
    records = [{"spend_transaction_id": "aa", "submit_started_at_ns": 1_000_000_000}, {"spend_transaction_id": "bb", "submit_started_at_ns": 5_000_000_000}]
    events = [{"kind": "mempool-received", "tx_id": "aa", "timestamp": "1970-01-01T00:00:00.500Z"},   # before submit: ignored
              {"kind": "mempool-received", "tx_id": "aa", "timestamp": "1970-01-01T00:00:01.250Z"},
              {"kind": "mempool-received", "tx_id": "aa", "timestamp": "1970-01-01T00:00:02.000Z"},   # resubmission: ignored
              {"kind": "mempool-accepted", "tx_id": "cc", "timestamp": "1970-01-01T00:00:06.000Z"}]
    assert join_mempool_visibility(records, events) == 1
    assert records[0]["submit_to_mempool_visibility_nanos"] == 250_000_000 and "submit_to_mempool_visibility_nanos" not in records[1]

def test_parse_submit_started_at_reads_the_wrapper_line():
    assert parse_submit_started_at("warning: x\nsubmit_started_at_ns=1762200000123456789\n") == 1762200000123456789
    assert parse_submit_started_at("") is None

# tests/test_cardano_measurement_e2e_scenario.py (fake cli pattern already used there for run_plutus_workload)
def test_plutus_workload_records_a_rejected_double_spend_per_batch(tmp_path, fake_cli):
    ... fake cli: submit of a tx file already submitted exits 1 with "TxValidationErrorInCardanoEra ... BadInputsUTxO" ...
    result = run_plutus_workload(..., transaction_count=2, double_spend_every=2)
    rejected = [r for r in result["records"] if r["attempt_id"].endswith("-double-spend")]
    assert len(rejected) == 1 and rejected[0]["outcome"] == "rejected" and rejected[0]["chain_outcome"] == "rejected-phase1"
    assert "BadInputsUTxO" in rejected[0]["rejected_reason"]
    assert all("submit_started_at_ns" in r for r in result["records"] if not r["attempt_id"].endswith("-double-spend"))

def test_double_spend_that_is_accepted_is_flagged_not_counted(tmp_path, fake_cli_accepting_everything):
    result = run_plutus_workload(..., transaction_count=2, double_spend_every=2)
    assert [r["outcome"] for r in result["records"] if r["attempt_id"].endswith("-double-spend")] == ["accepted-unexpected"]
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=dwarf python3.14 -m pytest -q -p no:cacheprovider tests/test_controlled_plutus_transactions.py tests/test_cardano_measurement_e2e_scenario.py -k "mempool_visibility or submit_started_at or double_spend"`
Expected: FAIL (`ImportError: join_mempool_visibility`, `TypeError: double_spend_every`).

- [ ] **Step 3: Implement** per the Interfaces block. The double-spend submit uses `_run` (not `_checked`) to capture the failure; `rejected_reason` is the first non-empty stderr line truncated to 200 chars.

- [ ] **Step 4: Run the files**

Run: `PYTHONPATH=dwarf python3.14 -m pytest -q -p no:cacheprovider tests/test_controlled_plutus_transactions.py tests/test_cardano_measurement_e2e_scenario.py tests/test_amaru_measurement_e2e_scenario.py tests/test_amaru_measurement_calibration_runtime.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add dwarf/scripts/runtime_cardano_measurement_calibration.py dwarf/scripts/runtime_amaru_measurement_calibration.py dwarf/scripts/runtime_controlled_plutus_transactions.py tests/
git commit -m "workload(plutus): in-container submit timestamps, phase-1 double spend, mempool-visibility join"
```

---

### Task 4: Cardano submission entry (peer) with split batch

**Files:**
- Modify: `dwarf/scripts/runtime_cardano_measurement_calibration.py:1167-1260` (`run_leg`), `:1429-1447` (CLI), `dwarf/profile_manager/primitives.py:13896-13900` (cardano flags), `dwarf/primitives/load/runtime_cardano_measurement_calibration.schema.json`, `dwarf/scenarios/cardano-measurement-workload-stock-portable.yaml` (calibration params)
- Test: `tests/test_cardano_measurement_e2e_scenario.py`, `tests/test_cardano_measurement_profile.py`

**Interfaces:**
- Consumes: `_independent_peer(runtime, node) -> (peer_id, container, socket_path)`, `_containerized_cardano_workload(node=...)`, Task 3's record fields.
- Produces: `run_leg(..., submission_entry: str = "target", plutus_split: float = 0.5)`; CLI `--submission-entry {target,peer}` and `--plutus-split` (0..1); with `peer`, `run_plutus_workload` is called twice inside the leg: `ceil(n * (1 - split))` transactions through node1 (records `submission_entry: "target"`, `boundary_node: "node1"`) and the rest through the peer node (`submission_entry: "peer"`, `boundary_node: "<peer_id>"`), records concatenated with distinct attempt ids (`plutus-t-<i>`, `plutus-p-<i>`). Primitive emits `--submission-entry`/`--plutus-split` from params `submission_entry`/`plutus_split` when present (`_CARDANO_WORKLOAD_FLAGS`, same shape as `_AMARU_WORKLOAD_FLAGS`); schema adds both (enum, number 0..1). Scenario: `submission_entry: peer`.

- [ ] **Step 1: Write the failing tests**

```python
def test_cardano_leg_splits_plutus_batch_between_target_and_peer(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(helper, "run_plutus_workload", lambda **kw: calls.append(kw["node"]["id"]) or {"records": [{"attempt_id": "x", "outcome": "accepted"}], "scripts": {}})
    ... runtime.json with node1 (target) and node2; monkeypatch _containerized_cardano_workload to a no-op context ...
    helper.run_leg(runtime_root=root, output_dir=tmp_path / "o", attempt_count=1, timeout_seconds=1, plutus_transactions=4, submission_entry="peer", plutus_split=0.5, ...)
    assert calls == ["node1", "node2"]

def test_cardano_calibration_primitive_passes_submission_entry_flags():
    arguments, logged = _cardano_workload_flag_arguments({"submission_entry": "peer", "plutus_split": 0.25})
    assert arguments == ["--submission-entry", "peer", "--plutus-split", "0.25"] and logged == {"submission_entry": "peer", "plutus_split": 0.25}

def test_cardano_workload_scenario_submits_through_the_peer():
    scenario = json.loads(Path("dwarf/scenarios/cardano-measurement-workload-stock-portable.yaml").read_text())
    calibration = next(step for step in scenario["load"] if step["primitive"] == "runtime_cardano_measurement_calibration")
    assert calibration["submission_entry"] == "peer"
```
Plus a schema validation test (`validate_scenarios.py` strict mode) in `tests/test_cardano_measurement_profile.py`.

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=dwarf python3.14 -m pytest -q -p no:cacheprovider tests/test_cardano_measurement_e2e_scenario.py tests/test_cardano_measurement_profile.py -k "peer or submission_entry"`
Expected: FAIL (`TypeError: submission_entry`, `ImportError: _cardano_workload_flag_arguments`).

- [ ] **Step 3: Implement** per the Interfaces block.

- [ ] **Step 4: Run the files and the scenario validator**

Run: `PYTHONPATH=dwarf python3.14 -m pytest -q -p no:cacheprovider tests/test_cardano_measurement_e2e_scenario.py tests/test_cardano_measurement_profile.py tests/test_amaru_measurement_e2e_scenario.py && PYTHONPATH=dwarf python3.14 dwarf/scripts/validate_scenarios.py --strict dwarf/scenarios/cardano-measurement-workload-stock-portable.yaml`
Expected: tests pass; validator exits 0.

- [ ] **Step 5: Commit**

```bash
git add dwarf/scripts/runtime_cardano_measurement_calibration.py dwarf/profile_manager/primitives.py dwarf/primitives/load/runtime_cardano_measurement_calibration.schema.json dwarf/scenarios/cardano-measurement-workload-stock-portable.yaml tests/
git commit -m "calibration(cardano): peer submission entry with a split Plutus batch"
```

---

### Task 5: Cardano patched tap identity — capabilities, operator evidence, profile, severities

**Files:**
- Modify: `dwarf/profile_manager/measurement_execution.py:105-108` (add `CARDANO_REVISION_CAPABILITIES`), `dwarf/profile_manager/release_build_identity.py:29-55` (`instrumented_identity`), `dwarf/profile_manager/measurement_collectors/cardano_patched.py:355-363` (`prepare`), `amaru_patched.py:505-520`, `dwarf/measurement-profiles/cardano-security-patched.yaml`, `dwarf/scripts/runtime_compose_substrate.py:264`, `dwarf/profile_manager/antithesis_measurements.py` (embedded severities), `dwarf/scenarios/cardano-measurement-workload-stock-portable.yaml:24` (blocker text)
- Test: `tests/test_release_build_identity.py`, `tests/test_cardano_patched_txsubmission_residence.py`, `tests/test_runtime_versioned_compose.py`, `tests/test_antithesis_measurements.py`, `tests/test_cardano_measurement_profile.py`

**Interfaces:**
- Produces: `CARDANO_REVISION_CAPABILITIES = {"nanoseconds-v3": {"cardano-patched-header-validation-instrumentation"}, "nanoseconds-v4": {... + "cardano-patched-blockfetch-queue"}, "nanoseconds-v5": {... + "cardano-patched-txsubmission-residence"}, "nanoseconds-v5-antithesis1": same as v5}` plus `cardano-patch-identity` for every patched identity; `instrumented_identity` unions them into `verified_instrumentation.capabilities` by `manifest["measurement_revision"]`.
- Produces: `operator_evidence_accepted(identity) -> bool` in `cardano_patched.py` (true when `identity_kind == "operator-local-config"` and `verified_instrumentation.evidence_sha256` matches `sha256:[0-9a-f]{64}`); `prepare()` requires `runtime_probe_log_sha256` **or** `operator_evidence_accepted`; same in `amaru_patched.prepare`.
- Produces: profile gains the three taps (`enabled: true`, empty parameters, gate disabled); severities map `{"KeepAlive.Remote": "Debug", "Mempool": "Debug"}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_release_build_identity.py
def test_operator_composite_build_gains_patched_tap_capabilities():
    registration = json.load(open(SCRATCH / "instrumented-registrations.json"))[1]   # copy into tests/fixtures/operator_registrations.json first
    identity = instrumented_identity(registration, {...expected keys as in the earlier manual check..., "identity_kind": "operator-local-config"})
    caps = set(identity["verified_instrumentation"]["capabilities"])
    assert {"cardano-patched-header-validation-instrumentation", "cardano-patched-blockfetch-queue", "cardano-patched-txsubmission-residence", "cardano-patch-identity"} <= caps
    assert identity["toolchain"] == {"cabal": "3.16.0.0", "ghc": "9.6.7"} and identity["measurement_revision"] == "nanoseconds-v5-antithesis1"

# tests/test_cardano_patched_txsubmission_residence.py
def test_operator_identity_prepares_with_evidence_digest_instead_of_probe_log(tmp_path):
    identity = {... composite identity with compatibility, toolchain, identity_kind "operator-local-config", verified_instrumentation {"evidence_sha256": "sha256:"+"a"*64}, no runtime_probe_log_sha256 ...}
    collector = <build via antithesis_cardano_bridge.build_factories(...)[txsubmission id]>  # the factory path cardano_factory uses
    collector.prepare(context)   # must not raise
    without = {**identity, "verified_instrumentation": {}}
    with pytest.raises(ValueError, match="runtime_probe_log_sha256"):
        <collector with `without`>.prepare(context)

# tests/test_runtime_versioned_compose.py: severities assertion gains "Mempool": "Debug"
# tests/test_cardano_measurement_profile.py
def test_patched_profile_enables_the_three_patched_taps():
    ids = {m["id"] for m in yaml.safe_load(Path("dwarf/measurement-profiles/cardano-security-patched.yaml").read_text())["measurements"] if m["enabled"]}
    assert {"cardano-patched-header-validation", "cardano-patched-blockfetch-handler-queue", "cardano-patched-txsubmission-residence"} <= ids
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=dwarf python3.14 -m pytest -q -p no:cacheprovider tests/test_release_build_identity.py tests/test_cardano_patched_txsubmission_residence.py tests/test_runtime_versioned_compose.py tests/test_cardano_measurement_profile.py -k "capabilities or evidence_digest or severit or three_patched"`
Expected: FAIL on the capability set, the prepare ValueError, the severities map, the profile ids.

- [ ] **Step 3: Implement** per the Interfaces block; update the scenario blocker sentence to "cardano-patched-header-validation, -blockfetch-handler-queue and -txsubmission-residence run from the patched measurement profile; they are enabled only for patched identities." Update `tests/test_antithesis_measurements.py` severity expectations where the config program embeds the map.

- [ ] **Step 4: Run the files**

Run: `PYTHONPATH=dwarf python3.14 -m pytest -q -p no:cacheprovider tests/test_release_build_identity.py tests/test_release_build_runtime.py tests/test_cardano_patched_txsubmission_residence.py tests/test_runtime_versioned_compose.py tests/test_cardano_measurement_profile.py tests/test_antithesis_measurements.py tests/test_cardano_measurement_factories.py`
Expected: all pass except the three `test_antithesis_measurements.py` failures that are identical on `main` (named in the ledger).

- [ ] **Step 5: Commit**

```bash
git add dwarf/profile_manager dwarf/measurement-profiles/cardano-security-patched.yaml dwarf/scripts/runtime_compose_substrate.py dwarf/scenarios/cardano-measurement-workload-stock-portable.yaml tests/
git commit -m "measurement(cardano): operator builds get patched tap capabilities; taps in the patched profile; Mempool traces at Debug"
```

---

### Task 6: Amaru BlockFetch pressure — 200-block controlled sync

**Files:**
- Modify: `dwarf/scenarios/amaru-measurement-workload-stock-portable.yaml:65-71` (`minimum_blocks: 200`, `timeout_seconds: 900`), the scenario's total budget if the validator enforces one (`timeout_seconds` on the scenario ≤ 2400 after the change; reduce the calibration leg's `timeout_seconds` by the same amount if needed)
- Test: `tests/test_amaru_measurement_e2e_scenario.py`

- [ ] **Step 1: Write the failing test** — `test_amaru_workload_scenario_syncs_two_hundred_blocks`: the `runtime_controlled_sync_range` step has `minimum_blocks == 200` and `timeout_seconds == 900`.
- [ ] **Step 2: Run** `PYTHONPATH=dwarf python3.14 -m pytest -q -p no:cacheprovider tests/test_amaru_measurement_e2e_scenario.py -k two_hundred` — Expected: FAIL (20 != 200).
- [ ] **Step 3: Edit the scenario.**
- [ ] **Step 4: Run** the file and `PYTHONPATH=dwarf python3.14 dwarf/scripts/validate_scenarios.py --strict dwarf/scenarios/amaru-measurement-workload-stock-portable.yaml` — Expected: pass, exit 0.
- [ ] **Step 5: Commit** `git commit -am "scenarios(amaru): 200-block controlled sync for BlockFetch queue pressure"`

---

### Task 7: TxSubmission probe `serve-transaction` mode and the Amaru rejection step

**Files:**
- Modify: `dwarf/scripts/runtime_txsubmission_probe.py` (new mode, encoders), `dwarf/scripts/runtime_amaru_measurement_calibration.py` (`_run_plutus_workload_leg` calls the probe for each double-spend record), `dwarf/profile_manager/primitives.py` (`_AMARU_WORKLOAD_FLAGS` gains `("reject_via_n2n", "--reject-via-n2n", int)`), `dwarf/primitives/load/runtime_amaru_measurement_calibration.schema.json`, `dwarf/scenarios/amaru-measurement-workload-stock-portable.yaml`
- Test: `tests/test_txsubmission_probe.py` (new or existing file for the probe), `tests/test_amaru_measurement_e2e_scenario.py`

**Interfaces:**
- Produces: `_build_reply_txids_payload_for(txid: bytes, size: int) -> bytes` (one entry, `[1, [[txid, size]]]`), `_build_reply_txs_payload(tx_bodies: list[bytes]) -> bytes` (`[3, [body...]]`, each body as a CBOR byte string; sizes > 255 need the encoder extended to 2-byte lengths `0x59`), `_parse_request_txs(payload) -> list[bytes]`. `run_probe(mode="serve_transaction", tx_body: bytes, tx_id: bytes, ...)`: handshake, wait for `MsgRequestTxIds` (blocking, `[0, ack, req]`), reply with the single id, wait for `MsgRequestTxs` naming it, reply with the body, then read until timeout and record `txsubmission_response_kind`; result body `{"served": True, "requested_body": bool, "post_serve_response_kind": str}`. The tx id is the Blake2b-256 of the transaction body as cardano-cli reports it (`spend_transaction_id` hex) — the probe takes it as hex on the command line (`--tx-id-hex`, `--tx-file`).
- Produces: Amaru leg: for each record with `chain_outcome == "rejected-phase1"`, when `reject_via_n2n` is 1, run the probe against `amaru-relay-1` (port from runtime metadata, `target_node="amaru-relay-1"`), store `{"n2n_serve": result}` on the record; the Amaru stock mempool trace then carries `transaction.rejected` for that id. Scenario sets `reject_via_n2n: 1`.
- Verification order: the plan runs the new mode first against cardano node1 on the box (its mempool trace must show `TraceMempoolAddedTx` for a fresh valid tx served over N2N), then against Amaru.

- [ ] **Step 1: Write the failing tests** — payload builders (`[1, [[id, size]]]` bytes exact for a 32-byte id and size 300), `_parse_request_txs` on a hand-encoded `[2, [id]]`, and an in-process fake responder socket (threading) that sends `MsgRequestTxIds`, then `MsgRequestTxs`, asserting the probe sends the body and reports `served` and `requested_body` True. Scenario test: `reject_via_n2n == 1` and the primitive emits `--reject-via-n2n 1`.
- [ ] **Step 2: Run** them — Expected: FAIL (`ValueError: unsupported mode: serve_transaction`).
- [ ] **Step 3: Implement** per the Interfaces block. Mode is not in `MODE_CASES` (no malformation); `build_result_body` gains the `serve_transaction` branch.
- [ ] **Step 4: Run** `PYTHONPATH=dwarf python3.14 -m pytest -q -p no:cacheprovider tests/test_txsubmission_probe.py tests/test_amaru_measurement_e2e_scenario.py tests/test_amaru_measurement_calibration_runtime.py` and the strict validator on the Amaru scenario — Expected: pass.
- [ ] **Step 5: Commit** `git add dwarf/scripts/runtime_txsubmission_probe.py dwarf/scripts/runtime_amaru_measurement_calibration.py dwarf/profile_manager/primitives.py dwarf/primitives/load/runtime_amaru_measurement_calibration.schema.json dwarf/scenarios/amaru-measurement-workload-stock-portable.yaml tests/ && git commit -m "probe(txsubmission): serve a transaction over N2N so Amaru's mempool rejection is exercised"`

---

### Task 8: The coverage rule — test and Pages staging gate

**Files:**
- Create: `dwarf/measurements/coverage-exceptions-v1.json` (`{"schema_version": "v1", "exceptions": [{"implementation": "amaru", "modes": ["stock","patched"], "concept": "header-arrival-offset", "reason": "..."}, {"implementation": "amaru", "modes": ["patched"], "concept": "header-validation-time", "reason": "..."}, {"implementation": "cardano-node", "modes": ["stock"], "concept": "epoch-transition-time", ...}, ... plutus-execution-time, handler-queue-residence, txsubmission-residence for cardano stock, "mempool-sync-time" and "handler-queue-residence"(amaru patched queue_residence → concept handler-queue-residence) listed as provisional with "provisional": true]}`)
- Create: `dwarf/profile_manager/tap_coverage.py` with `coverage_report(report_metrics: dict, implementation: str, mode: str, *, catalog_index=None, exceptions=None) -> {"covered": [...concept ids], "missing": [{"concept", "ids", "reason"}], "excepted": [...], "unbound_ids": [...]}` using `measurement_compare._catalog_index` bindings (an id counts when its primary value is numeric or the verdict sum is, or `value == 0` with `offered_count > 0` or `sample_count > 0`), and `rule_holds(report) -> bool` (no `missing`, no `unbound_ids`).
- Modify: `dwarf/scripts/ci_stage_pages_campaigns.py:30-70` (`validate_defaults`: entries may carry `"coverage_rule": true` or `"coverage_exception": "<reason>"`; `stage()` runs `rule_holds` on the archive's `measurements/report.json` for `coverage_rule` entries and fails with the missing list), `docs/portable-defaults.js` (ignore the two new keys)
- Test: `tests/test_tap_coverage_rule.py` (new), `tests/test_ci_stage_pages_campaigns.py`

- [ ] **Step 1: Write the failing tests** — `coverage_report` on the four real reports (fixture: extract `measurements/report.json` of each 2026-11-04 archive from `docs/evidence/runs/` in the test) lists exactly the gaps from the design §1 table for each (assert `"restart-readiness" in covered`, `"workload-backlog" in missing` for all four today, `"header-arrival-offset" in excepted` for Amaru); `rule_holds` is False today for all four; a synthetic complete report returns True; `validate_defaults` accepts `coverage_rule: true` and `coverage_exception`, and `stage()` refuses an archive whose report fails the rule when the entry says `coverage_rule: true`.
- [ ] **Step 2: Run** — Expected: FAIL (`ModuleNotFoundError: tap_coverage`).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `PYTHONPATH=dwarf python3.14 -m pytest -q -p no:cacheprovider tests/test_tap_coverage_rule.py tests/test_ci_stage_pages_campaigns.py tests/test_measurement_compare.py` — Expected: pass.
- [ ] **Step 5: Commit** `git commit -m "coverage: concept-level tap coverage rule with exceptions; Pages staging enforces it"`

---

### Task 9: Deploy to cardano-box and rerun the four legs until the rule holds

**Files:** none in the repo except fixes found during the reruns (each fix is its own TDD commit on the branch). Box artefacts: `~/dwarf-exports/<run>-bundle-export.tar.gz`, scratchpad `exports-coverage/`.

- [ ] **Step 1: Sync and deploy.** `git bundle` the branch to the box, `git fetch` into `~/dwarf-v10-fresh-sync`, checkout `feat/full-tap-coverage`, run `bash delivery/scripts/build-image.sh && bash delivery/scripts/deploy.sh`. Expected: dashboard healthy at http://192.168.30.16:8787, `/api/health` reports the new controller image.
- [ ] **Step 2: Probe check (Task 7).** From the controller container run the probe's `serve_transaction` mode against cardano node1 with a fresh valid tx (build it with the workload's cardano-cli wrapper), confirm `TraceMempoolAddedTx` for its id in node1's trace; then against `amaru-relay-1` with a double spend, confirm `transaction.rejected` in the relay log. Expected: both observed; otherwise fix the encoding (TDD) before the legs run.
- [ ] **Step 3: Prepare and run the four legs** with `~/prepare.sh` / `~/workload-queue.sh` (requests `~/dwarf-plans/{amaru,cardano}-workload-{stock,patched}.request.json`, patched requests add the three taps through the profile with consent). Expected: all four `pass`.
- [ ] **Step 4: Rule check on the exports.** `PYTHONPATH=dwarf python3.14 -m profile_manager.tap_coverage <export>` (add a `__main__` that prints the report) for each; Expected: `missing: []` and `unbound_ids: []` for all four, with `excepted` listing only §6 concepts. Any `missing` → systematic-debugging, fix, rerun that leg.
- [ ] **Step 5: Record** the per-leg metric counts with `~/tapreport.py` in the ledger.

---

### Task 10: Replace the Pages set, acceptance, docs

**Files:**
- Modify: `docs/evidence/default-runs.json` (four new workload entries with `coverage_rule: true`, Antithesis entry with `coverage_exception: "Antithesis snapshot: bounded trace window, few taps"`, the five 2026-10-17 entries removed), `docs/evidence/runs/` (archives added/removed), `docs/plans/2026-11-04-full-tap-coverage-design.md` (status), memory note
- Test: `tests/test_ci_stage_pages_campaigns.py`, `tests/test_tap_coverage_rule.py` (now asserts `rule_holds` True on the four retained archives)

- [ ] **Step 1:** Copy the four exports into `docs/evidence/runs/`, regenerate the manifest (sizes, sha256), remove the five old archives, run `PYTHONPATH=dwarf python3.14 dwarf/scripts/ci_stage_pages_campaigns.py --check` (or the staging dry run the script offers) — Expected: exit 0.
- [ ] **Step 2:** Flip `tests/test_tap_coverage_rule.py` to assert the rule holds on every `coverage_rule` archive; run the suite subset — Expected: pass.
- [ ] **Step 3:** Acceptance in the browser (scratchpad `compare-acceptance.cjs` adapted): pick the four runs; with "Include unmeasured metrics" on, every concept card has at least one value per implementation except the §6 exceptions; the summary line's hidden count equals the number of excepted concepts. Screenshot to the scratchpad.
- [ ] **Step 4:** Commit evidence and docs: `git add docs/evidence docs/plans && git commit -m "evidence: Pages set with full tap coverage (four workload reruns, Antithesis snapshot)"`. Update the memory note. Final review per executing-plans, then finishing-a-development-branch (merge/push on the user's word).
