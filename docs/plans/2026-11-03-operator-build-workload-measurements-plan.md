# Operator-build workload measurements — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two portable workload scenarios (Amaru, cardano-node) for operator-prepared builds that make every local measurement tap record samples, and a refreshed Pages comparison set built from their four runs.

**Architecture:** Extend the existing calibration helpers and load primitives (handshake calibration + Plutus lock/spend workload + epoch wait), chain the existing restart and sync-range primitives after them in two new scenario files, fix one collector contract, raise the Pages size caps, then run on cardano-box and commit the archives.

**Tech Stack:** Python 3.12 (`dwarf/`), pytest in the `dwarf-test:local` Docker image on cardano-box, cardano-cli inside node containers, JSON/YAML scenario catalog.

**Spec:** `docs/plans/2026-11-03-operator-build-workload-measurements-design.md`

## Global Constraints

- Git author for every commit: `-c user.name=JG -c user.email=forky@1timeuse.com`; end messages with the session attribution lines. Push only when asked.
- Branch: `feat/operator-build-workload-measurements` in `/Users/nigel/dwarf-project/dwarf-fable/V10-FRESH` (based on `main` `d9a06b61`). The three fixes on `fix/local-cardano-instrumented-correction` (`166ff342`) are a runtime prerequisite on the box, not part of this branch.
- Tests run on cardano-box, not the Mac (no `jsonschema` locally). Sync and run:
  ```bash
  rsync -a --delete --exclude .git /Users/nigel/dwarf-project/dwarf-fable/V10-FRESH/ cardano-box:dwarf-v10-test/
  ssh cardano-box 'docker run --rm --entrypoint python3 -v /home/nigel/dwarf-v10-test:/src:ro -w /src -e PYTHONPATH=/src/dwarf dwarf-test:local -m pytest -q -p no:cacheprovider <tests…>'
  ```
  Baseline: 15 pre-existing failures in the full suite (git/patch tooling absent in the image); a task passes when its named tests pass and no new failure appears in the files it touches.
- Measurements stay non-gating; no security claim.
- Run ceiling 2400 s; Pages caps after Task 1: 95 MiB per archive, 950 MiB total, 10 archives.
- New calibration parameters default to "off" and must leave today's `workload_identity` digest unchanged when unset.
- Box drive scripts (`~/prepare.sh`, `~/runplan.sh`, `~/queue.sh`, `~/tapreport.py`) and API shapes are reused unchanged.

## Review Focus

1. A Plutus transaction submitted through `amaru-consumer` that Amaru never forwards: the run must fall back to p1 after `inclusion_fallback_seconds`, record `relay_forwarded: false`, and still finish. (Task 5 test `test_plutus_workload_falls_back_to_producer_after_inclusion_timeout`.)
2. The epoch wait on a devnet whose next boundary is more than `epoch_timeout_seconds` away: the leg must fail with a clear `RuntimeError`, not hang past the primitive timeout. (Task 6 test `test_wait_for_epoch_transitions_times_out_with_reason`.)
3. Old scenarios that do not set the new parameters: their `workload_identity` digest and CLI must be byte-identical to before. (Task 6 test `test_workload_identity_digest_unchanged_without_workload_options`.)
4. An operator-local cardano image referenced by bare image id (`sha256:<64hex>`) must be accepted by the containerized workload only when it equals the inspected image id; any other non-digest reference stays rejected. (Task 4 test `test_containerized_workload_accepts_operator_local_image_id_only_when_inspected_matches`.)
5. A `manager.peer.accepted` span emits `new`, `enter`, `exit`, `close` records; `peer_connected` must count 1 per span, and `manager.peer.connected` messages from older binaries must still count. (Task 2 test.)

---

### Task 1: Raise the Pages archive caps

**Files:**
- Modify: `dwarf/scripts/ci_stage_pages_campaigns.py:21-22`
- Modify: `docs/portable-defaults.js:9-10`
- Test: `tests/test_ci_stage_pages_campaigns.py`

**Interfaces:**
- Produces: `_MAX_ARCHIVE_BYTES = 95 * 1024 * 1024`, `_MAX_TOTAL_BYTES = 950 * 1024 * 1024` (Python) and the same two values in the viewer. `_MAX_ARCHIVES` stays 10.

- [ ] **Step 1: Write the failing test** in `tests/test_ci_stage_pages_campaigns.py`

```python
def test_pages_caps_allow_full_workload_bundles_and_match_viewer():
    assert api._MAX_ARCHIVE_BYTES == 95 * 1024 * 1024
    assert api._MAX_TOTAL_BYTES == 950 * 1024 * 1024
    assert api._MAX_ARCHIVES == 10
    viewer = (ROOT / "docs/portable-defaults.js").read_text(encoding="utf-8")
    assert "var MAX_ARCHIVE_BYTES = 95 * 1024 * 1024;" in viewer
    assert "var MAX_TOTAL_BYTES = 950 * 1024 * 1024;" in viewer
    assert "var MAX_ARCHIVES = 10;" in viewer


def test_pages_rejects_archive_above_github_file_limit():
    row = {"url": "runs/run-1-" + "a" * 64 + ".tar.gz", "size_bytes": 95 * 1024 * 1024 + 1,
           "sha256": "b" * 64, "run_id": "run-1", "implementation": "amaru", "version": "v1"}
    with pytest.raises(ValueError, match="archive size"):
        api.validate_defaults({"schema_version": api._SCHEMA, "archives": [row]})
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_ci_stage_pages_campaigns.py -q`; expected: the two new tests FAIL on the cap values.
- [ ] **Step 3: Change the two constants in the Python module and the two `var` lines in the viewer.** Add a one-line comment in each: `// 95 MiB: GitHub rejects files over 100 MB` and the 1 GB Pages soft limit for the total.
- [ ] **Step 4: Run** `pytest tests/test_ci_stage_pages_campaigns.py tests/test_campaign_pages_workflow.py -q`; expected PASS (the existing count test still expects 6 on this branch).
- [ ] **Step 5: Commit** — `git add dwarf/scripts/ci_stage_pages_campaigns.py docs/portable-defaults.js tests/test_ci_stage_pages_campaigns.py && git commit -m "pages: raise default archive caps to 95 MiB / 950 MiB for full workload bundles"`

### Task 2: `peer_connected` counts `manager.peer.accepted` spans

**Files:**
- Modify: `dwarf/profile_manager/measurement_collectors/amaru_stock.py:26-60,109-121`
- Test: `tests/test_amaru_stock_collector.py`

**Interfaces:**
- Produces: `_normalize_json_event` returns kind `peer-connected` for a record whose `target == "amaru::protocols"`, `span.name == "manager.peer.accepted"` and `fields.message == "new"`; returns `None` for the `enter`/`exit`/`close` records of that span. Existing `("amaru::protocols", "manager.peer.connected")` message mapping unchanged.

- [ ] **Step 1: Write the failing test** (use the file's `_collect_from_paths` helper)

```python
def test_peer_connected_counts_each_accepted_span_once_and_keeps_legacy_message(tmp_path):
    span = {"name": "manager.peer.accepted", "target": "amaru::protocols"}
    records = [
        {"timestamp": "2026-11-03T21:52:57.641000Z", "level": "DEBUG", "fields": {"conn_id": 1, "message": m, "peer": "10.192.2.4:3001"},
         "target": "amaru::protocols", "span": span, "id": 49540420534796292}
        for m in ("new", "enter", "exit", "close")
    ] + [
        {"timestamp": "2026-11-03T21:52:58.000000Z", "level": "DEBUG",
         "fields": {"message": "manager.peer.connected", "peer": "10.192.2.5:3001"}, "target": "amaru::protocols"},
    ]
    trace = tmp_path / "peers.ndjson"
    trace.write_text("".join(json.dumps(r) + "\n" for r in records))
    network = _collect_from_paths(tmp_path / "net", "amaru-stock-network", json_paths=[trace], otlp_paths=[])
    assert network["measurements"]["peer_connected"] == {"status": "available", "unit": "events", "value": 2}
```

- [ ] **Step 2: Run** `pytest tests/test_amaru_stock_collector.py -q -k peer_connected`; expected FAIL with value 0.
- [ ] **Step 3: Implement.** Add module constant `_JSON_SPAN_OPEN_KINDS = {("amaru::protocols", "manager.peer.accepted"): "peer-connected"}`. In `_normalize_json_event`, after computing `kind` from the two existing maps and before `if kind is None`, add: when `kind is None` and the record has a `span` dict whose `(target, span.name.lower())` is in `_JSON_SPAN_OPEN_KINDS`, set `kind` to that value only if `fields.get("message") == "new"`. Update the comment block above `_JSON_MESSAGE_KINDS` to note the 20261029 rename.
- [ ] **Step 4: Run** `pytest tests/test_amaru_stock_collector.py tests/test_amaru_measurement_source_contract.py -q`; expected PASS.
- [ ] **Step 5: Commit** — `git commit -am "collectors(amaru): count manager.peer.accepted spans as peer_connected"`

### Task 3: cardano profile-n opts into measurement traces

**Files:**
- Modify: `dwarf/profiles/profile-n-cardano-latest-confirmed/profile.yaml`
- Test: `tests/test_cardano_measurement_profile.py`

**Interfaces:**
- Produces: profile JSON gains `"cardano_measurement_traces": true` (already accepted by `profiles.py:212`, consumed by `runtime_compose_substrate.py:1363`).

- [ ] **Step 1: Write the failing test**

```python
def test_operator_latest_cardano_profile_opts_into_measurement_traces():
    profile = json.loads(Path("dwarf/profiles/profile-n-cardano-latest-confirmed/profile.yaml").read_text())
    assert profile["cardano_measurement_traces"] is True
    amaru = json.loads(Path("dwarf/profiles/profile-o-amaru-target-latest-confirmed/profile.yaml").read_text())
    assert amaru["amaru_json_traces"] is True
```

- [ ] **Step 2: Run** `pytest tests/test_cardano_measurement_profile.py -q`; expected FAIL with KeyError.
- [ ] **Step 3: Add the key** after `"shared_genesis": true` in the profile file (keep the file's two-space JSON style).
- [ ] **Step 4: Run** `pytest tests/test_cardano_measurement_profile.py tests/test_cardano_measurement_resolution.py tests/test_profile_version_resolution.py tests/test_operate_profile_templates.py -q`; expected PASS.
- [ ] **Step 5: Commit** — `git commit -am "profiles: enable cardano measurement traces on profile-n-cardano-latest-confirmed"`

### Task 4: Containerized workload accepts the Amaru consumer and operator-local images

**Files:**
- Modify: `dwarf/scripts/runtime_cardano_measurement_calibration.py:288-330` (`_verify_workload_container`), `:457-475` (`_containerized_cardano_workload` image check)
- Create: function `amaru_entry_node(runtime, *, service) -> dict` in the same file (below `_owned_socket_volume`)
- Test: `tests/test_cardano_measurement_e2e_scenario.py`

**Interfaces:**
- Produces:
  - `_verify_workload_container(runtime, node)`: when `node.get("component") == "support"`, expected labels are `io.dwarf.managed=true`, `io.dwarf.component=support`, `io.dwarf.topology=<profile_id>`, `com.docker.compose.project`, `com.docker.compose.service`; `io.dwarf.implementation`, `io.dwarf.node_id` and `ada2.managed` are not required. Default (`component` absent or `"node"`) unchanged.
  - `_containerized_cardano_workload`: `image` may be `"@sha256:"`-pinned (unchanged) **or** a bare `sha256:<64 hex>` image id, in which case `_verify_workload_container` already proves `inspected["Image"] == expected_id`; any other form raises `RuntimeError("Cardano-node workload image is not digest-pinned")` as today. When `node["image_id"]` is absent and `image_ref` is a bare id, use `image_ref` as the expected id.
  - `amaru_entry_node(runtime: dict, *, service: str = "amaru-consumer") -> dict` returns `{"id": service, "service_name": service, "container_name": svc["container"], "container_id": <docker inspect Id>, "image_ref": svc["expected_image"], "image_id": svc["expected_image_id"], "container_socket_path": "/state/node.socket", "socket_path": "/state/node.socket", "socket_mount_destination": "/state", "socket_mount_type": "volume", "socket_volume_key": f"{service}-state", "component": "support"}` from `runtime["identity"]["services"][service]`; raises `RuntimeError("Amaru runtime has no <service> entry node")` when the service is missing or `matched` is not True.

- [ ] **Step 1: Write the failing tests** (monkeypatch `calibration._inspect_container`; build label dicts by hand)

```python
def test_verify_workload_container_accepts_support_component_labels(monkeypatch):
    inspected = {"Id": "c" * 64, "Name": "/consumer", "Names": [], "Image": "sha256:" + "3" * 64,
                 "State": {"Running": True},
                 "Config": {"Image": "ghcr.io/intersectmbo/cardano-node@sha256:" + "3" * 64, "Labels": {
                     "io.dwarf.managed": "true", "io.dwarf.component": "support",
                     "io.dwarf.topology": "profile-o-amaru-target-latest-confirmed",
                     "com.docker.compose.project": "proj", "com.docker.compose.service": "amaru-consumer"}}}
    monkeypatch.setattr(calibration, "_inspect_container", lambda _id: inspected)
    runtime = {"profile_id": "profile-o-amaru-target-latest-confirmed", "compose_project": "proj"}
    node = {"id": "amaru-consumer", "service_name": "amaru-consumer", "container_name": "consumer",
            "container_id": "c" * 64, "image_ref": "ghcr.io/intersectmbo/cardano-node@sha256:" + "3" * 64,
            "image_id": "sha256:" + "3" * 64, "component": "support"}
    assert calibration._verify_workload_container(runtime, node) is inspected
    node_as_node = dict(node, component="node")
    with pytest.raises(RuntimeError, match="ownership labels"):
        calibration._verify_workload_container(runtime, node_as_node)


def test_containerized_workload_accepts_operator_local_image_id_only_when_inspected_matches(monkeypatch, tmp_path):
    # image_ref is a bare image id (operator-local build); accepted when _verify_workload_container
    # returns an inspect whose Image equals it; a tag-only reference is still rejected.
    ...  # arrange runtime/node with image_ref "sha256:" + "d" * 64 and no image_id,
         # monkeypatch _verify_workload_container, _owned_socket_volume, _mount_source,
         # _owned_run_output_relative, _cleanup_workload_containers; enter the context and
         # assert workload_runtime["support_binaries"]["cardano-cli"] exists;
         # then image_ref "ghcr.io/intersectmbo/cardano-node:11.1.2" -> pytest.raises(RuntimeError, match="digest-pinned")


def test_amaru_entry_node_binds_consumer_socket_volume(monkeypatch):
    monkeypatch.setattr(calibration, "_inspect_container", lambda _id: {"Id": "e" * 64})
    runtime = {"identity": {"services": {"amaru-consumer": {"container": "consumer-1", "matched": True,
               "expected_image": "ghcr.io/intersectmbo/cardano-node@sha256:" + "3" * 64, "expected_image_id": "sha256:" + "3" * 64}}}}
    node = calibration.amaru_entry_node(runtime)
    assert node["socket_volume_key"] == "amaru-consumer-state" and node["component"] == "support"
    assert node["container_id"] == "e" * 64 and node["image_id"] == "sha256:" + "3" * 64
    with pytest.raises(RuntimeError, match="entry node"):
        calibration.amaru_entry_node({"identity": {"services": {}}})
```

- [ ] **Step 2: Run** `pytest tests/test_cardano_measurement_e2e_scenario.py -q -k "support_component or operator_local_image or entry_node"`; expected FAIL (AttributeError / RuntimeError).
- [ ] **Step 3: Implement the three changes** in `runtime_cardano_measurement_calibration.py` per the Interfaces block. Keep every existing error message text.
- [ ] **Step 4: Run** `pytest tests/test_cardano_measurement_e2e_scenario.py tests/test_controlled_plutus_transactions.py -q`; expected PASS.
- [ ] **Step 5: Commit** — `git commit -am "calibration(cardano): support-node workload entry and operator-local image ids"`

### Task 5: `run_plutus_workload` gains script language and producer fallback

**Files:**
- Create: `dwarf/corpora/cardano-measurement/always-fails-v3.plutus` (`{"type": "PlutusScriptV3", "description": "DWARF revision-retained rejected-outcome measurement workload (V3)", "cborHex": "4746010000222601"}`)
- Modify: `dwarf/scripts/runtime_cardano_measurement_calibration.py:44-50` (script constants), `:633-820` (`run_plutus_workload`)
- Test: `tests/test_cardano_measurement_e2e_scenario.py`

**Interfaces:**
- Produces:
  - `PLUTUS_SCRIPTS = {"v2": (PLUTUS_SCRIPT, PLUTUS_FAIL_SCRIPT), "v3": (ROOT/"corpora/redeemer-container/live-controls/always-succeeds-v3.plutus", ROOT/"corpora/cardano-measurement/always-fails-v3.plutus")}`.
  - `select_plutus_language(*, cli: str, socket_path: str, network_magic: int) -> str` runs `cardano-cli query protocol-parameters --socket-path … --testnet-magic …`, returns `"v2"` if `costModels.PlutusV2` is present, else `"v3"` if `PlutusV3` is present, else raises `RuntimeError("live protocol parameters have no PlutusV2 or PlutusV3 cost model")`.
  - `run_plutus_workload(*, runtime_root, runtime, node, output_dir, transaction_count, script_language: str = "v2", fallback_submit: Callable[[Path], None] | None = None, inclusion_fallback_seconds: float = 30.0) -> dict`. Result gains `"script_language"`; each record gains `"relay_forwarded": bool | None` (`None` when `fallback_submit` is None; `True` when the lock and spend both appeared without fallback; `False` otherwise). Script file names in the work dir become `always-succeeds-<lang>.plutus` / `always-fails-<lang>.plutus`.
  - Fallback semantics: each inclusion wait (`_wait_for_utxo` for lock and accepted spend, `_wait_for_utxo_key_absent` for rejected spend) first waits `inclusion_fallback_seconds`; on `RuntimeError` and `fallback_submit` set, call `fallback_submit(tx_path)` then wait again with the default 60 s. Without `fallback_submit`, behaviour is unchanged (single 60 s wait).

- [ ] **Step 1: Write the failing tests.** Drive `run_plutus_workload` with a fake `cli` (monkeypatch `calibration._checked`, `_query_utxo`, `_wait_for_utxo`, `_wait_for_utxo_key_absent`, `_transaction_id`) as the existing tests do.

```python
def test_select_plutus_language_prefers_v2_then_v3(monkeypatch):
    for models, expected in ({"PlutusV1": [], "PlutusV2": []}, "v2"), ({"PlutusV1": [], "PlutusV3": []}, "v3"):
        monkeypatch.setattr(calibration, "_checked", lambda cmd, **_k: SimpleNamespace(stdout=json.dumps({"costModels": models})))
        assert calibration.select_plutus_language(cli="cardano-cli", socket_path="/s", network_magic=42) == expected
    monkeypatch.setattr(calibration, "_checked", lambda cmd, **_k: SimpleNamespace(stdout=json.dumps({"costModels": {"PlutusV1": []}})))
    with pytest.raises(RuntimeError, match="cost model"):
        calibration.select_plutus_language(cli="cardano-cli", socket_path="/s", network_magic=42)


def test_plutus_workload_uses_v3_control_scripts_when_selected(monkeypatch, tmp_path):
    ...  # transaction_count=1, script_language="v3": assert the copied script files are
         # always-succeeds-v3.plutus / always-fails-v3.plutus and result["script_language"] == "v3"
         # and result["scripts"]["accepted"]["sha256"] == sha256 of the V3 corpus file


def test_plutus_workload_falls_back_to_producer_after_inclusion_timeout(monkeypatch, tmp_path):
    calls = []
    outcomes = iter([RuntimeError("did not appear"), ({"lock#0": {"value": {"lovelace": 5000000}}}, 1.0), ({"spend#0": {}}, 1.0)])

    def fake_wait(**_kwargs):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(calibration, "_wait_for_utxo", fake_wait)
    ...  # transaction_count=1, fallback_submit=calls.append, inclusion_fallback_seconds=0.1
    assert len(calls) == 1 and calls[0].name == "lock-0.tx"
    assert result["records"][0]["relay_forwarded"] is False


def test_plutus_workload_without_fallback_keeps_single_wait_and_null_relay_flag(monkeypatch, tmp_path):
    ...  # assert _wait_for_utxo called with default timeout and record["relay_forwarded"] is None
```

- [ ] **Step 2: Run** `pytest tests/test_cardano_measurement_e2e_scenario.py -q -k "plutus"`; expected FAIL.
- [ ] **Step 3: Implement** per the Interfaces block. Factor the "wait, maybe fall back, wait again" into a private `_await_inclusion(wait: Callable[[float], T], *, tx_path, fallback_submit, inclusion_fallback_seconds) -> tuple[T, bool]` so the three wait sites share it.
- [ ] **Step 4: Run** `pytest tests/test_cardano_measurement_e2e_scenario.py tests/test_cardano_calibration_definitions.py tests/test_controlled_plutus_transactions.py -q`; expected PASS.
- [ ] **Step 5: Commit** — `git add dwarf/corpora/cardano-measurement/always-fails-v3.plutus dwarf/scripts/runtime_cardano_measurement_calibration.py tests/test_cardano_measurement_e2e_scenario.py && git commit -m "calibration(cardano): plutus workload language selection and producer fallback"`

### Task 6: Amaru calibration helper runs the workload and waits for an epoch

**Files:**
- Modify: `dwarf/scripts/runtime_amaru_measurement_calibration.py` (`build_workload_identity`, `run_leg`, `_parser`, `main`)
- Test: `tests/test_amaru_measurement_calibration_runtime.py`

**Interfaces:**
- Consumes: Task 4 `amaru_entry_node`, `_containerized_cardano_workload`; Task 5 `run_plutus_workload`, `select_plutus_language`; `scripts.runtime_controlled_plutus_transactions._prepare_amaru_transaction_producer(runtime, output_dir, stack, *, runtime_root, controller)` and `_plutus_workload_accounting(records, "amaru")` (imported **inside** `run_leg` to avoid the module-level import cycle); `_controller_container()` from the cardano helper.
- Produces:
  - `extract_latest_epoch(log_text: str) -> int | None`: last `tip.update`/`tip.adopt` record whose `fields.epoch` is a non-bool int ≥ 0.
  - `_wait_for_epoch_transitions(container: str, *, count: int, timeout_seconds: float, poll_seconds: float = 2.0) -> dict` → `{"epoch_before": int, "epoch_after": int, "elapsed_seconds": float}`; raises `RuntimeError("Amaru target did not cross <count> epoch boundary(ies) within <timeout> s (epoch <before> -> <last>)")` on timeout; raises `RuntimeError("Amaru target logs contain no epoch")` if no epoch is readable at start.
  - `build_workload_identity(*, attempt_count, seed=SEED, case_set=DEFAULT_CASE_SET, plutus_transactions: int = 0, submission_entry: str = "amaru-consumer", epoch_transitions: int = 0)`: body unchanged when all three are default; otherwise adds `"plutus_transactions"`, `"submission_entry"`, `"epoch_transitions"` keys (so the digest changes only when a workload is requested).
  - `run_leg(..., plutus_transactions: int = 0, submission_entry: str = "amaru-consumer", inclusion_fallback_seconds: float = 30.0, epoch_transitions: int = 0, epoch_timeout_seconds: float = 260.0)`. Order inside: handshake attempts → Plutus workload → epoch wait → existing progress wait → capture. Result gains `"plutus_workload"` (the `run_plutus_workload` result or `None`) and `"epoch_wait"` (the dict above or `None`).
  - Plutus wiring: `controller = _controller_container()`; `producer_runtime, producer_node = _prepare_amaru_transaction_producer(runtime, output_dir, stack, runtime_root=runtime_root, controller=controller)` (this is the p1 path, giving the funded genesis.1 key and a cli bound to p1's socket). If `submission_entry == "p1"`, run the workload on it directly. If `"amaru-consumer"`, additionally enter `_containerized_cardano_workload(runtime_root=…, runtime={**producer_runtime, "workload_key_root": producer_runtime["workload_key_root"]}, node=amaru_entry_node(runtime), output_dir=output_dir, controller=controller)` and run the workload on the consumer cli/socket with `fallback_submit=` a closure that submits via the producer cli (`conway transaction submit --socket-path <p1 socket> --testnet-magic <magic> --tx-file <tx>`). `script_language = select_plutus_language(cli=<entry cli>, socket_path=<entry socket>, network_magic=…)`. When `controller is None` (direct host run), the consumer path is unsupported: raise `RuntimeError("amaru-consumer submission entry requires the controller container")`.
  - CLI: `--plutus-transactions` (0..100), `--submission-entry {p1,amaru-consumer}`, `--inclusion-fallback-seconds` (>0), `--epoch-transitions` (0..5), `--epoch-timeout-seconds` (>0, ≤ 900), validated in `main` like the existing bounds.

- [ ] **Step 1: Write the failing tests**

```python
def test_workload_identity_digest_unchanged_without_workload_options():
    baseline = calibration.build_workload_identity(attempt_count=100, case_set="accepted-and-rejected-v1")
    body = {k: v for k, v in baseline.items() if k != "workload_digest"}
    assert set(body) == {"scenario_id", "seed", "attempt_count", "mini_protocol", "case_set", "cases",
                         "network_magic", "transport", "target_port", "mux_direction_bit", "response_cap_bytes"}
    expected = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert baseline["workload_digest"] == f"sha256:{expected}"
    with_workload = calibration.build_workload_identity(attempt_count=100, case_set="accepted-and-rejected-v1", plutus_transactions=20, epoch_transitions=1)
    assert with_workload["plutus_transactions"] == 20 and with_workload["submission_entry"] == "amaru-consumer"
    assert with_workload["workload_digest"] != baseline["workload_digest"]


def test_extract_latest_epoch_reads_tip_update_epoch_field():
    text = "\n".join(json.dumps(r) for r in [
        {"fields": {"message": "tip.update", "epoch": 3, "slot": 1201, "block_height": 234, "header_hash": "9b" * 32}, "target": "amaru::ledger"},
        {"fields": {"message": "other"}}, {"fields": {"message": "tip.update", "epoch": 4, "slot": 1601, "block_height": 300, "header_hash": "aa" * 32}}])
    assert calibration.extract_latest_epoch(text) == 4
    assert calibration.extract_latest_epoch("") is None


def test_wait_for_epoch_transitions_returns_when_epoch_advances(monkeypatch):
    epochs = iter([3, 3, 4])
    monkeypatch.setattr(calibration, "_latest_epoch", lambda _c: next(epochs))
    monkeypatch.setattr(calibration.time, "sleep", lambda _s: None)
    result = calibration._wait_for_epoch_transitions("c", count=1, timeout_seconds=10)
    assert (result["epoch_before"], result["epoch_after"]) == (3, 4)


def test_wait_for_epoch_transitions_times_out_with_reason(monkeypatch):
    monkeypatch.setattr(calibration, "_latest_epoch", lambda _c: 3)
    clock = iter([0.0, 0.0, 5.0, 11.0, 12.0])
    monkeypatch.setattr(calibration.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(calibration.time, "sleep", lambda _s: None)
    with pytest.raises(RuntimeError, match="did not cross 1 epoch"):
        calibration._wait_for_epoch_transitions("c", count=1, timeout_seconds=10)


def test_run_leg_runs_plutus_workload_through_consumer_with_producer_fallback(monkeypatch, tmp_path):
    ...  # monkeypatch _compose_container, _container_ip, run_attempts, _latest_tip, _wait_for_progress,
         # _container_state, _capture_logs, classify_log_signals, _controller_container -> {"Id": "x"},
         # the lazily imported _prepare_amaru_transaction_producer (patch on
         # scripts.runtime_controlled_plutus_transactions), amaru_entry_node,
         # _containerized_cardano_workload (a contextmanager yielding (runtime, node)),
         # select_plutus_language -> "v3", run_plutus_workload (record kwargs), _wait_for_epoch_transitions.
         # Assert run_plutus_workload got script_language="v3", a callable fallback_submit,
         # inclusion_fallback_seconds=30.0, node["id"] == "amaru-consumer"; assert result["epoch_wait"]
         # is the patched dict and result["workload_identity"]["plutus_transactions"] == 2.


@pytest.mark.parametrize("argv", [["--plutus-transactions", "101"], ["--epoch-transitions", "6"], ["--submission-entry", "relay1"]])
def test_helper_rejects_out_of_range_workload_options(argv, tmp_path):
    with pytest.raises(SystemExit):
        calibration.main(["leg", "--runtime-root", str(tmp_path), "--output-dir", str(tmp_path / "o"), "--attempts", "30", *argv])
```

- [ ] **Step 2: Run** `pytest tests/test_amaru_measurement_calibration_runtime.py -q`; expected FAIL on the new tests only.
- [ ] **Step 3: Implement** per Interfaces. Add `_latest_epoch(container) -> int | None` beside `_latest_tip` (same `docker logs --tail 8000` read, then `extract_latest_epoch`). Use `contextlib.ExitStack` in `run_leg` for the two workload contexts.
- [ ] **Step 4: Run** `pytest tests/test_amaru_measurement_calibration_runtime.py tests/test_amaru_calibration_definitions.py tests/test_amaru_measurement_e2e_scenario.py tests/test_client_example_invalid_protocol.py -q`; expected PASS.
- [ ] **Step 5: Commit** — `git commit -am "calibration(amaru): plutus workload via consumer entry and epoch-transition wait"`

### Task 7: Primitive parameters and schema

**Files:**
- Modify: `dwarf/profile_manager/primitives.py:13752-13990` (`RuntimeAmaruMeasurementCalibration.run`)
- Modify: `dwarf/primitives/load/runtime_amaru_measurement_calibration.schema.json`
- Test: `tests/test_amaru_measurement_e2e_scenario.py`

**Interfaces:**
- Consumes: Task 6 CLI flags; `_plutus_workload_accounting` from `scripts.runtime_controlled_plutus_transactions` (lazy import).
- Produces: scenario params `plutus_transactions` (integer 0..100), `submission_entry` (enum `p1`, `amaru-consumer`), `inclusion_fallback_seconds` (number > 0), `epoch_transitions` (integer 0..5), `epoch_timeout_seconds` (number > 0, ≤ 900) accepted by the schema (`additionalProperties: false` stays). The primitive passes each through as `--<kebab-name>` only when present, logs them in `started` and `completed` payloads, and when `report["plutus_workload"]["records"]` exists appends `_plutus_workload_accounting(records, "amaru")["attempts"]` to the `workload_accounting` payload's `attempts`, adding their counts to `attempted`/`successful`/`rejected`/`bytes`. The cardano subclass is untouched.

- [ ] **Step 1: Write the failing tests**

```python
def test_calibration_schema_accepts_workload_options_and_rejects_unknown():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    props = schema["properties"]
    assert props["plutus_transactions"] == {"type": "integer", "minimum": 0, "maximum": 100}
    assert props["submission_entry"] == {"type": "string", "enum": ["p1", "amaru-consumer"]}
    assert props["epoch_transitions"] == {"type": "integer", "minimum": 0, "maximum": 5}
    assert props["epoch_timeout_seconds"] == {"type": "number", "exclusiveMinimum": 0, "maximum": 900}
    assert props["inclusion_fallback_seconds"] == {"type": "number", "exclusiveMinimum": 0}
    assert schema["additionalProperties"] is False


def test_amaru_calibration_primitive_passes_workload_flags_and_merges_plutus_accounting(monkeypatch, tmp_path):
    ...  # instantiate RuntimeAmaruMeasurementCalibration with params {profile_id, attempts: 30,
         # plutus_transactions: 2, epoch_transitions: 1}; monkeypatch subprocess.run to record argv and
         # write output_dir/result.json containing {"plutus_workload": {"records": [<two records with the
         # fields _plutus_workload_accounting requires>]}} and attempts.ndjson with one handshake row;
         # assert "--plutus-transactions", "2" and "--epoch-transitions", "1" in argv and
         # "--submission-entry" not in argv; find the logged workload_accounting payload:
         # attempted == 3, len(attempts) == 3, attempts[1]["input_id"] == "plutus-0000".
```

- [ ] **Step 2: Run** `pytest tests/test_amaru_measurement_e2e_scenario.py -q`; expected FAIL.
- [ ] **Step 3: Implement** the schema entries and the primitive changes per Interfaces (helper `_workload_flag_arguments(params) -> list[str]` keeps `run` readable).
- [ ] **Step 4: Run** `pytest tests/test_amaru_measurement_e2e_scenario.py tests/test_amaru_calibration_definitions.py tests/test_run_plan.py -q`; expected PASS.
- [ ] **Step 5: Commit** — `git commit -am "primitives: workload options for runtime_amaru_measurement_calibration"`

### Task 8: The two workload scenarios

**Files:**
- Create: `dwarf/scenarios/amaru-measurement-workload-stock-portable.yaml`, `dwarf/scenarios/cardano-measurement-workload-stock-portable.yaml`
- Test: `tests/test_amaru_measurement_e2e_scenario.py`, `tests/test_cardano_measurement_e2e_scenario.py`

**Interfaces:**
- Consumes: Task 7 params; existing primitives `runtime_real_target_restart_and_readiness`, `runtime_controlled_sync_range`, probes `runtime_target_health_and_progress`, `runtime_peer_session_health`, assertions `load_events_are_ok`, `restart_readiness_complete`, `controlled_sync_range_complete`, `target_progress_continues`, `no_target_fatal_signal`.
- Produces: scenario ids `amaru-measurement-workload-stock-portable` and `cardano-measurement-workload-stock-portable`. Content is the matching `*-e2e-stock-portable` file with: `id`/`title` changed (title suffix "— workload"), tag `workload` added, promotion blocker "This bounded proof validates that every local tap records under a transaction, epoch, restart and sync workload; it does not establish a performance threshold or vulnerability.", `measurements: [{"id": "amaru-stock-header-validation" | "cardano-stock-header-validation", "enabled": true, "parameters": {}, "threshold_gate": {"enabled": false, "thresholds": []}}]`, and:
  - Amaru load: `[{primitive: runtime_amaru_measurement_calibration, profile_id, attempts: 100, case_set: accepted-and-rejected-v1, response_timeout_seconds: 2, plutus_transactions: 20, submission_entry: amaru-consumer, epoch_transitions: 1, epoch_timeout_seconds: 260, timeout_seconds: 1500, expected_helper_exit: 0}, {primitive: runtime_real_target_restart_and_readiness, profile_id, timeout_seconds: 300, poll_interval_seconds: 1}, {primitive: runtime_controlled_sync_range, profile_id, minimum_blocks: 20, timeout_seconds: 240, poll_interval_seconds: 1, peer_policy: single-controlled-producer}]`.
  - cardano load: the calibration entry keeps `observation_seconds: 2, trace_timeout_seconds: 20` and adds `plutus_transactions: 20, epoch_observation_seconds: 60, timeout_seconds: 1500`; then the same restart entry; then sync range with `peer_policy: three-node-controlled-local-mesh`.
  - probes: `[{primitive: runtime_target_health_and_progress, report_path: outputs/client-example-proof/controlled-sync-range.json, progress_reference: load-start}, {primitive: runtime_peer_session_health, report_path: outputs/client-example-proof/controlled-sync-range.json}]`.
  - assertions: `[{load_events_are_ok, min_completed: 3, min_event_count: 3}, {restart_readiness_complete}, {controlled_sync_range_complete, minimum_blocks: 20}, {target_progress_continues}, {no_target_fatal_signal}]`.

- [ ] **Step 1: Write the failing tests** (one per file, same shape)

```python
def test_amaru_workload_scenario_extends_e2e_with_transactions_epoch_restart_and_sync():
    base = json.loads((ROOT / "dwarf/scenarios/amaru-measurement-e2e-stock-portable.yaml").read_text())
    doc = json.loads((ROOT / "dwarf/scenarios/amaru-measurement-workload-stock-portable.yaml").read_text())
    parsed = scenario_from_body((json.dumps(doc) + "\n").encode())
    assert parsed.id == "amaru-measurement-workload-stock-portable"
    assert (parsed.profile, parsed.measurement_profile, parsed.target) == (base["profile"], base["measurement_profile"], base["target"])
    assert [m.id for m in parsed.measurements] == ["amaru-stock-header-validation"]
    load = doc["load"]
    assert [l["primitive"] for l in load] == ["runtime_amaru_measurement_calibration", "runtime_real_target_restart_and_readiness", "runtime_controlled_sync_range"]
    assert load[0] == {**base["load"][0], "plutus_transactions": 20, "submission_entry": "amaru-consumer", "epoch_transitions": 1, "epoch_timeout_seconds": 260, "timeout_seconds": 1500}
    assert load[2]["minimum_blocks"] == 20 and load[2]["peer_policy"] == "single-controlled-producer"
    assert [a["primitive"] for a in doc["assertions"]] == ["load_events_are_ok", "restart_readiness_complete", "controlled_sync_range_complete", "target_progress_continues", "no_target_fatal_signal"]
    assert "workload" in doc["tags"]
```

- [ ] **Step 2: Run** both test files `-q -k workload`; expected FAIL (file missing).
- [ ] **Step 3: Create the two scenario files** per Interfaces (JSON-in-YAML style of the siblings; no absolute paths).
- [ ] **Step 3b: Optional cardano patched taps (design 4.5).** In the test image run `python3 -c` against `profile_manager.measurement_collectors.cardano_patched` with the operator identity (`implementation: cardano-node`, `source_revision: fef83fed01d7926f3de83b3b917be5a4a48768b5`, `measurement_revision: nanoseconds-v5`, `patch_set_sha256: 95991a736b2e187ace2ec9873f0f10ffdc0ebd432269857dfbc2c74041371d0c`, `mode: patched`) and print `supports_cardano_header_validation`, `supports_cardano_blockfetch_queue`, `supports_cardano_txsubmission_residence`. For each that returns True, add `cardano-patched-header-validation` / `cardano-patched-blockfetch-handler-queue` / `cardano-patched-txsubmission-residence` to the cardano scenario's `measurements` and extend the cardano test's expected id list. For each False, add one line to the scenario's `promotion_blockers` naming the tap as a manifest `unbuilt-contract` boundary.
- [ ] **Step 4: Run** `python3 dwarf/scripts/validate_scenarios.py` (inside the test image: `-m scripts.validate_scenarios` from `/src/dwarf`, or the invocation `tests/test_validate_scenarios*.py` uses) and the two test files; expected: 0 failures, tests PASS.
- [ ] **Step 5: Commit** — `git add dwarf/scenarios/*-measurement-workload-stock-portable.yaml tests/ && git commit -m "scenarios: portable workload measurement scenarios for operator builds"`

### Task 9: Run on cardano-box

**Files:**
- Box: `~/dwarf-v10-fresh-sync` (checkout `77d7c5de` + this branch applied), `~/dwarf-plans/*.request.json`, `~/dwarf-exports/`
- Produces: four passing runs, their tap reports, four bundle exports + signatures.

- [ ] **Step 1: Apply the branch on the box** — `git format-patch main..HEAD` locally, `scp` to the box, `git am` into `~/dwarf-v10-fresh-sync` (clean tree required; same JG author), then `bash delivery/scripts/build-image.sh && bash delivery/scripts/deploy.sh`. Verify `docker ps` shows `dwarf-fw` and `dwarf-controller` healthy and the deployed revision equals the branch head.
- [ ] **Step 2: Redeploy profile-n** so the trace flag applies: write `~/dwarf-plans/cardano-workload-stock.request.json` (copy of `cardano-stock.request.json` with `scenario_id: cardano-measurement-workload-stock-portable`), run `~/prepare.sh cardano-workload-stock` (confirms the destructive redeploy). Check `TraceOptions` in `.../profile-n-cardano-latest-confirmed/current/env/configuration.yaml` now lists `BlockFetch.Client`, `Mempool`, `KeepAlive.Remote` at `Info`.
- [ ] **Step 3: Run cardano stock** — `~/runplan.sh cardano-workload-stock`; expected `exit_status` pass; `tapreport.py` shows samples for `mempool_accepted`, `blockfetch_duration`, `keepalive_rtt`, `protocol_events`, `ledger_samples`, `restart_readiness`, `header_validation`, `submit_to_block_inclusion`.
- [ ] **Step 4: Run cardano patched** — request with `measurement_profile: cardano-security-patched` and the patched registration id; `~/prepare.sh` then `~/runplan.sh`; expected additionally `block_application`, `epoch_transition`, `plutus_vm`, `protocol_receive_decode` samples.
- [ ] **Step 5: Run Amaru stock then patched** (profile-o needs no redeploy for stock; the patched prepare redeploys as before and waits ~20 min for bootstrap). Expected new samples: `fork_switch`, `mempool_received/accepted/evicted`, `mempool_tx_count`, `transfer_validation`, `virtual_machine_*`, `epoch_transition`, `keepalive_round_trip`, `peer_connected`, `restart_readiness`, `header_validation`, `submit_to_block_inclusion`; patched adds `advertised_to_terminal` (txsubmission residence) samples. Record `relay_forwarded` counts from `outputs/amaru-measurement-calibration/plutus-workload/result.json`.
- [ ] **Step 6: Export** each run: `GET /runs/<id>/bundle?token=` and `/bundle/signature` into `~/dwarf-exports/`; note sizes. If any archive exceeds 95 MiB, stop and report (design 4.6 fallback needs the user's go).
- [ ] **Step 7: Save the four tap reports** to `~/dwarf-exports/<run>.tapreport.txt` and copy exports + reports to the scratchpad.

### Task 10: Pages comparison set

**Files:**
- Modify: `docs/evidence/default-runs.json`, `docs/evidence/runs/` (on a new branch `evidence/operator-workload-2026-11-03` created from this feature branch, starting from the `docs/evidence/` state of `evidence/operator-builds-2026-11-03`)
- Modify: `tests/test_ci_stage_pages_campaigns.py:21` (archive count)

**Interfaces:**
- Produces: manifest with the four handshake-only entries (`20261103T213519Z-5f7cab16`, `20261103T220135Z-487bfc61`, `20261103T221410Z-034dba77`, `20261103T220925Z-a05f0b97`) replaced by the four workload runs; 10 entries total; archives copied as `docs/evidence/runs/<run-id>-bundle-export.tar.gz` with `size_bytes` and `sha256` from the file.

- [ ] **Step 1:** `git checkout -b evidence/operator-workload-2026-11-03 && git checkout evidence/operator-builds-2026-11-03 -- docs/evidence tests/test_ci_stage_pages_campaigns.py`; remove the four handshake archives and their manifest rows; add the four new ones.
- [ ] **Step 2: Validate** — `python3 -c "from scripts import ci_stage_pages_campaigns as api; api.validate_defaults(api.load('docs/evidence/default-runs.json'))"` inside the test image, and `pytest tests/test_ci_stage_pages_campaigns.py tests/test_campaign_pages_workflow.py -q`; expected PASS with 10 archives.
- [ ] **Step 3: Commit** — `git add docs/evidence tests/test_ci_stage_pages_campaigns.py && git commit -m "evidence: Pages comparison set from operator-build workload runs (Amaru 10.11.20261029, cardano-node 11.1.2)"`
- [ ] **Step 4: Update memory** (`operator-build-measurement-runs-2026-11-03.md`): run ids, per-tap table, archive sizes, branches; nothing pushed unless asked.
