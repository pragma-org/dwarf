import html
import importlib
import json
import multiprocessing
import re
import shlex
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

import pytest

from test_operate_testcases import _seed_lifecycle


CLI_PREFIX = "./dwarf/cardano-profile "
TOKEN = "testcase-body-token"


def _env(monkeypatch, tmp_path: Path, state_dir: Path, runs_dir: Path) -> None:
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(state_dir))
    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(runs_dir))
    monkeypatch.setenv("ADA2_DWARF_BUNDLES_DIR", str(tmp_path / "bundles"))


def _basic(page: str) -> str:
    # Basic is the default view; Advanced markup lives in the trailing adv-only block.
    return page.split('<div class="adv-only">', 1)[0]


def _parses(command: str) -> None:
    cli = importlib.import_module("profile_manager.cli")
    assert command.startswith(CLI_PREFIX), command
    concrete = re.sub(
        r"<([^>]+)>", lambda m: m.group(1).split("|")[0] if "|" in m.group(1) else "x", command
    )
    cli.build_parser().parse_args(shlex.split(concrete)[1:])


def _hrefs(page: str) -> list[str]:
    return re.findall(r'href="([^"]+)"', page)


def _assert_safe_links(page: str) -> None:
    for href in _hrefs(page):
        query = href.partition("?")[2].lower()
        assert "token" not in query, href
        assert href.startswith("/"), href


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _post(base_url: str, path: str, fields: dict[str, str]):
    request = Request(
        base_url + path,
        data=urlencode(fields).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with build_opener(_NoRedirect).open(request) as response:
            return response.status, response.headers.get("Location"), response.read()
    except HTTPError as exc:
        return exc.code, exc.headers.get("Location"), exc.read()


def _synchronized_lifecycle_write(run_dir, state_dir, record, barrier, results):
    lifecycle = importlib.import_module("profile_manager.testcase_lifecycle")
    original_read = lifecycle.read_testcase_state

    def synchronized_read(**kwargs):
        snapshot = original_read(**kwargs)
        try:
            barrier.wait(timeout=0.25)
        except threading.BrokenBarrierError:
            pass
        return snapshot

    lifecycle.read_testcase_state = synchronized_read
    try:
        lifecycle.write_lifecycle_artifacts(
            run_dir=run_dir, state_dir=state_dir, records=[record]
        )
    except BaseException as exc:
        results.put(repr(exc))
    else:
        results.put(None)


def _hold_testcase_lifecycle_lock(state_dir, ready, release):
    lifecycle = importlib.import_module("profile_manager.testcase_lifecycle")
    with lifecycle._testcase_write_lock(state_dir):
        ready.set()
        release.wait(timeout=3)


def _run_contending_lifecycle_mutation(
    action, run_dir, state_dir, record, barrier, results
):
    lifecycle = importlib.import_module("profile_manager.testcase_lifecycle")
    case_path = Path(state_dir) / "testcases" / f"{record['case_id']}.json"
    original_read_text = Path.read_text
    waited = False

    def synchronized_read_text(path, *args, **kwargs):
        nonlocal waited
        value = original_read_text(path, *args, **kwargs)
        if Path(path) == case_path and not waited:
            waited = True
            try:
                barrier.wait(timeout=0.25)
            except threading.BrokenBarrierError:
                pass
        return value

    Path.read_text = synchronized_read_text
    try:
        if action == "ingest":
            lifecycle.write_lifecycle_artifacts(
                run_dir=run_dir, state_dir=state_dir, records=[record]
            )
        elif action == "replay":
            lifecycle.record_replay_result(
                state_dir=state_dir,
                case_id=record["case_id"],
                target="amaru",
                run_id="replay-amaru-complete",
                exit_status="pass",
                behavior_summary={"signature": "behavior-kept"},
                resource_summary={"signature": "resource-kept"},
            )
        elif action == "compare":
            lifecycle.record_compare_result(
                state_dir=state_dir,
                case_id=record["case_id"],
                comparison_path="compare/kept.json",
                agreed=False,
                runs={"amaru": "replay-amaru", "cardano-node": "replay-cardano"},
                run_outcomes={"amaru": "pass", "cardano-node": "clean_error"},
                behavior_summaries={"amaru": {"signature": "behavior-kept"}},
                resource_summaries={"amaru": {"signature": "resource-kept"}},
            )
        else:
            raise AssertionError(f"unknown contention action: {action}")
    except BaseException as exc:
        results.put(repr(exc))
    else:
        results.put(None)


@pytest.fixture
def testcase_dashboard(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    runs_dir = tmp_path / "runs"
    _env(monkeypatch, tmp_path, state_dir, runs_dir)
    run_id = "retained-run-1"
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "scenario": {"id": "basic-ingest"},
                "target": {"implementation": "amaru"},
                "runtime": "library",
                "exit_status": "fail",
                "assertion_summary": {"pass": 0, "fail": 1},
            }
        ),
        encoding="utf-8",
    )
    dashboard = importlib.import_module("profile_manager.dashboard")
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), dashboard.serve_dashboard_handler_factory(TOKEN)
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {
            "url": f"http://127.0.0.1:{server.server_address[1]}",
            "run_id": run_id,
            "runs_dir": runs_dir,
            "state_dir": state_dir,
            "dashboard": dashboard,
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_entrypoint_commands_are_real_cli_operations():
    data = importlib.import_module("profile_manager.data.operate_testcases")
    entry = data.testcase_lifecycle_entrypoints()
    commands = [item["command"] for group in entry.values() for item in group]
    assert any(" testcase ingest-run " in c for c in commands)
    assert any(" testcase buckets summary" in c for c in commands)
    for command in commands:
        _parses(command)


def test_basic_empty_testcases_page_offers_supported_ingest_path(tmp_path, monkeypatch):
    dashboard = importlib.import_module("profile_manager.dashboard")
    data = importlib.import_module("profile_manager.data.operate_testcases")
    _env(monkeypatch, tmp_path, tmp_path / "empty", tmp_path / "runs")

    page = dashboard.render_route_html("/operate/testcases", token=TOKEN)
    basic = _basic(page)

    assert "0 retained testcase records" in basic
    assert "No testcase records are retained" in basic
    for item in data.testcase_lifecycle_entrypoints()["create"]:
        assert html.escape(item["command"]) in basic
    assert 'href="/operate/runs"' in basic
    assert 'href="/learn/cli#cli-testcase"' in basic
    assert 'href="/operate/testcase-buckets"' in basic
    assert 'action="/operate/testcases/ingest"' in basic
    assert f'type="hidden" name="token" value="{TOKEN}"' in basic
    assert "dashboard does not change lifecycle state" not in basic
    # Nothing to export yet: no export control fabricated for an empty state.
    assert "/api/assets/testcases/export" not in basic
    _assert_safe_links(basic)
    assert not (tmp_path / "empty").exists()


def test_basic_native_ingest_persists_and_redirects_to_readback(testcase_dashboard):
    status, location, body = _post(
        testcase_dashboard["url"],
        "/operate/testcases/ingest",
        {
            "token": TOKEN,
            "run_id": testcase_dashboard["run_id"],
            "classification": "crash",
            "triage_reason": "decoder-abort",
        },
    )

    assert (status, body) == (303, b"")
    assert location is not None and location.startswith("/operate/testcases/tc-")
    case_id = location.rsplit("/", 1)[1]
    record_path = testcase_dashboard["state_dir"] / "testcases" / f"{case_id}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["source_run_id"] == testcase_dashboard["run_id"]
    assert record["classification"] == "crash"
    assert record["triage_reason"] == "decoder-abort"

    detail = _basic(testcase_dashboard["dashboard"].render_route_html(location))
    assert f'href="/operate/runs/{testcase_dashboard["run_id"]}"' in detail
    assert f'href="/operate/testcase-buckets/{record["bucket_id"]}"' in detail
    assert f'href="/api/assets/testcases/{case_id}/export"' in detail

    bucket = _basic(
        testcase_dashboard["dashboard"].render_route_html(
            f'/operate/testcase-buckets/{record["bucket_id"]}'
        )
    )
    assert f'href="/operate/testcases/{case_id}"' in bucket
    assert (
        f'href="/api/assets/testcase-buckets/{record["bucket_id"]}/export"'
        in bucket
    )


@pytest.mark.parametrize("body_token", ["", "wrong-token"])
def test_native_ingest_body_token_cannot_fall_back_to_query(
    testcase_dashboard, body_token
):
    status, location, _body = _post(
        testcase_dashboard["url"],
        f"/operate/testcases/ingest?token={TOKEN}",
        {
            "token": body_token,
            "run_id": testcase_dashboard["run_id"],
            "classification": "crash",
            "triage_reason": "decoder-abort",
        },
    )

    assert (status, location) == (403, None)
    assert not (testcase_dashboard["state_dir"] / "testcases").exists()


@pytest.mark.parametrize(
    ("fields", "expected_status", "expected_message"),
    [
        ({"run_id": "../escape", "classification": "crash", "triage_reason": "bad"}, 400, "invalid run id"),
        ({"run_id": "missing-run", "classification": "crash", "triage_reason": "bad"}, 404, "run not found"),
        ({"run_id": "retained-run-1", "classification": "", "triage_reason": "bad"}, 422, "classification"),
        ({"run_id": "retained-run-1", "classification": "crash", "triage_reason": ""}, 422, "triage reason"),
    ],
)
def test_native_ingest_missing_unsafe_run_and_malformed_issue_fail_closed(
    testcase_dashboard, fields, expected_status, expected_message
):
    status, location, body = _post(
        testcase_dashboard["url"],
        "/operate/testcases/ingest",
        {"token": TOKEN, **fields},
    )

    assert (status, location) == (expected_status, None)
    assert expected_message in body.decode("utf-8").lower()
    assert not (testcase_dashboard["state_dir"] / "testcases").exists()


def test_native_ingest_malformed_nested_manifest_fails_closed(testcase_dashboard):
    manifest_path = (
        testcase_dashboard["runs_dir"]
        / testcase_dashboard["run_id"]
        / "manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["scenario"] = "not-an-object"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    status, location, body = _post(
        testcase_dashboard["url"],
        "/operate/testcases/ingest",
        {
            "token": TOKEN,
            "run_id": testcase_dashboard["run_id"],
            "classification": "crash",
            "triage_reason": "decoder-abort",
        },
    )

    assert (status, location) == (422, None)
    assert "malformed" in body.decode("utf-8").lower()
    assert not (testcase_dashboard["state_dir"] / "testcases").exists()


def test_native_ingest_honors_global_mutation_lock(testcase_dashboard, monkeypatch):
    monkeypatch.setattr(
        testcase_dashboard["dashboard"], "try_acquire_mutating_lock", lambda: False
    )

    status, location, body = _post(
        testcase_dashboard["url"],
        "/operate/testcases/ingest",
        {
            "token": TOKEN,
            "run_id": testcase_dashboard["run_id"],
            "classification": "crash",
            "triage_reason": "decoder-abort",
        },
    )

    assert (status, location) == (409, None)
    assert "another mutating action" in body.decode("utf-8").lower()
    assert not (testcase_dashboard["state_dir"] / "testcases").exists()


def test_native_ingest_reports_cross_process_writer_contention(
    testcase_dashboard, monkeypatch
):
    lifecycle = importlib.import_module("profile_manager.testcase_lifecycle")

    def lock_timeout(**_kwargs):
        raise TimeoutError("testcase lifecycle writer lock timed out")

    monkeypatch.setattr(lifecycle, "ingest_run_issue", lock_timeout)
    status, location, body = _post(
        testcase_dashboard["url"],
        "/operate/testcases/ingest",
        {
            "token": TOKEN,
            "run_id": testcase_dashboard["run_id"],
            "classification": "crash",
            "triage_reason": "decoder-abort",
        },
    )

    assert (status, location) == (409, None)
    assert "lifecycle writer" in body.decode("utf-8").lower()


def test_native_ingest_rejects_symlink_run_entry(testcase_dashboard, tmp_path):
    external = tmp_path / "external-run"
    external.mkdir()
    (external / "manifest.json").write_text(
        json.dumps(
            {
                "target": {"implementation": "amaru"},
                "exit_status": "fail",
            }
        ),
        encoding="utf-8",
    )
    (testcase_dashboard["runs_dir"] / "linked-run").symlink_to(
        external, target_is_directory=True
    )

    status, location, _body = _post(
        testcase_dashboard["url"],
        "/operate/testcases/ingest",
        {
            "token": TOKEN,
            "run_id": "linked-run",
            "classification": "crash",
            "triage_reason": "decoder-abort",
        },
    )

    assert (status, location) == (400, None)
    assert not (testcase_dashboard["state_dir"] / "testcases").exists()


def test_native_ingest_rejects_symlink_manifest(testcase_dashboard, tmp_path):
    manifest_path = (
        testcase_dashboard["runs_dir"]
        / testcase_dashboard["run_id"]
        / "manifest.json"
    )
    external = tmp_path / "external-manifest.json"
    external.write_bytes(manifest_path.read_bytes())
    manifest_path.unlink()
    manifest_path.symlink_to(external)

    status, location, _body = _post(
        testcase_dashboard["url"],
        "/operate/testcases/ingest",
        {
            "token": TOKEN,
            "run_id": testcase_dashboard["run_id"],
            "classification": "crash",
            "triage_reason": "decoder-abort",
        },
    )

    assert (status, location) == (422, None)
    assert not (testcase_dashboard["state_dir"] / "testcases").exists()


def test_native_ingest_rechecks_manifest_inside_canonical_writer(
    testcase_dashboard, monkeypatch, tmp_path
):
    lifecycle = importlib.import_module("profile_manager.testcase_lifecycle")
    original = lifecycle.ingest_run_issue
    manifest_path = (
        testcase_dashboard["runs_dir"]
        / testcase_dashboard["run_id"]
        / "manifest.json"
    )
    external = tmp_path / "swapped-manifest.json"
    external.write_bytes(manifest_path.read_bytes())

    def swap_then_ingest(**kwargs):
        manifest_path.unlink()
        manifest_path.symlink_to(external)
        return original(**kwargs)

    monkeypatch.setattr(lifecycle, "ingest_run_issue", swap_then_ingest)
    status, location, _body = _post(
        testcase_dashboard["url"],
        "/operate/testcases/ingest",
        {
            "token": TOKEN,
            "run_id": testcase_dashboard["run_id"],
            "classification": "crash",
            "triage_reason": "decoder-abort",
        },
    )

    assert (status, location) == (422, None)
    assert not (testcase_dashboard["state_dir"] / "testcases").exists()


def test_native_ingest_rejects_in_place_manifest_rewrite_during_summary(
    testcase_dashboard, monkeypatch
):
    lifecycle = importlib.import_module("profile_manager.testcase_lifecycle")
    original = lifecycle.summarize_run_behavior
    manifest_path = (
        testcase_dashboard["runs_dir"]
        / testcase_dashboard["run_id"]
        / "manifest.json"
    )

    def rewrite_then_summarize(*, run_dir, **kwargs):
        changed = json.loads(manifest_path.read_text(encoding="utf-8"))
        changed["exit_status"] = "pass"
        manifest_path.write_text(json.dumps(changed), encoding="utf-8")
        return original(run_dir=run_dir, **kwargs)

    monkeypatch.setattr(lifecycle, "summarize_run_behavior", rewrite_then_summarize)
    status, location, _body = _post(
        testcase_dashboard["url"],
        "/operate/testcases/ingest",
        {
            "token": TOKEN,
            "run_id": testcase_dashboard["run_id"],
            "classification": "crash",
            "triage_reason": "decoder-abort",
        },
    )

    assert (status, location) == (422, None)
    assert not (testcase_dashboard["state_dir"] / "testcases").exists()


def test_native_ingest_revalidates_manifest_after_writer_lock_acquisition(
    testcase_dashboard, monkeypatch
):
    lifecycle = importlib.import_module("profile_manager.testcase_lifecycle")
    original_lock = lifecycle._testcase_write_lock
    manifest_path = (
        testcase_dashboard["runs_dir"]
        / testcase_dashboard["run_id"]
        / "manifest.json"
    )

    @contextmanager
    def rewrite_after_lock(*args, **kwargs):
        with original_lock(*args, **kwargs):
            changed = json.loads(manifest_path.read_text(encoding="utf-8"))
            changed["exit_status"] = "pass"
            manifest_path.write_text(json.dumps(changed), encoding="utf-8")
            yield

    monkeypatch.setattr(lifecycle, "_testcase_write_lock", rewrite_after_lock)
    status, location, _body = _post(
        testcase_dashboard["url"],
        "/operate/testcases/ingest",
        {
            "token": TOKEN,
            "run_id": testcase_dashboard["run_id"],
            "classification": "crash",
            "triage_reason": "decoder-abort",
        },
    )

    assert (status, location) == (422, None)
    assert not (testcase_dashboard["state_dir"] / "testcases").exists()


def test_native_ingest_rejects_run_directory_rename_symlink_swap(
    testcase_dashboard, monkeypatch
):
    lifecycle = importlib.import_module("profile_manager.testcase_lifecycle")
    original = lifecycle.summarize_run_behavior
    run_dir = testcase_dashboard["runs_dir"] / testcase_dashboard["run_id"]
    moved_dir = testcase_dashboard["runs_dir"] / "moved-retained-run"

    def swap_then_summarize(*, run_dir: Path, **kwargs):
        run_dir.rename(moved_dir)
        run_dir.symlink_to(moved_dir, target_is_directory=True)
        return original(run_dir=run_dir, **kwargs)

    monkeypatch.setattr(lifecycle, "summarize_run_behavior", swap_then_summarize)
    status, location, _body = _post(
        testcase_dashboard["url"],
        "/operate/testcases/ingest",
        {
            "token": TOKEN,
            "run_id": testcase_dashboard["run_id"],
            "classification": "crash",
            "triage_reason": "decoder-abort",
        },
    )

    assert (status, location) == (422, None)
    assert not (testcase_dashboard["state_dir"] / "testcases").exists()


def test_native_ingest_writer_rejects_run_directory_swap(
    testcase_dashboard, monkeypatch
):
    lifecycle = importlib.import_module("profile_manager.testcase_lifecycle")
    original = lifecycle.write_lifecycle_artifacts
    run_dir = testcase_dashboard["runs_dir"] / testcase_dashboard["run_id"]
    moved_dir = testcase_dashboard["runs_dir"] / "writer-moved-run"

    def swap_then_write(**kwargs):
        run_dir.rename(moved_dir)
        run_dir.symlink_to(moved_dir, target_is_directory=True)
        return original(**kwargs)

    monkeypatch.setattr(lifecycle, "write_lifecycle_artifacts", swap_then_write)
    status, location, _body = _post(
        testcase_dashboard["url"],
        "/operate/testcases/ingest",
        {
            "token": TOKEN,
            "run_id": testcase_dashboard["run_id"],
            "classification": "crash",
            "triage_reason": "decoder-abort",
        },
    )

    assert (status, location) == (422, None)
    assert not (testcase_dashboard["state_dir"] / "testcases").exists()
    assert not (moved_dir / "outputs").exists()


def test_native_ingest_replaces_hardlinked_bundle_without_overwriting_alias(
    testcase_dashboard, tmp_path
):
    bundle_dir = (
        testcase_dashboard["runs_dir"]
        / testcase_dashboard["run_id"]
        / "outputs"
        / "scenario"
    )
    bundle_dir.mkdir(parents=True)
    protected = tmp_path / "protected.txt"
    protected.write_text("protected\n", encoding="utf-8")
    (bundle_dir / "testcases.ndjson").hardlink_to(protected)

    status, location, _body = _post(
        testcase_dashboard["url"],
        "/operate/testcases/ingest",
        {
            "token": TOKEN,
            "run_id": testcase_dashboard["run_id"],
            "classification": "crash",
            "triage_reason": "decoder-abort",
        },
    )

    assert status == 303
    assert location is not None and location.startswith("/operate/testcases/tc-")
    assert protected.read_text(encoding="utf-8") == "protected\n"
    assert "decoder-abort" in (bundle_dir / "testcases.ndjson").read_text(
        encoding="utf-8"
    )


def test_exact_reingest_preserves_lifecycle_history_and_queue_progress(
    testcase_dashboard
):
    lifecycle = importlib.import_module("profile_manager.testcase_lifecycle")
    run_id = testcase_dashboard["run_id"]
    record = lifecycle.build_run_issue_record(
        run_id=run_id,
        producer="scenario",
        target_implementation="amaru",
        classification="crash",
        triage_reason="decoder-abort",
        source_artifact_path="manifest.json",
        metadata={"scenario_id": "basic-ingest"},
    )
    record.update(
        {
            "replay_targets": ["amaru", "cardano-node"],
            "replay_harness": "afl-tx-body",
            "replay_target_id": "amaru-cbor-decode-tx-body",
            "replay_state": "pending",
            "compare_state": "pending",
        }
    )
    lifecycle.write_lifecycle_artifacts(
        run_dir=testcase_dashboard["runs_dir"] / run_id,
        state_dir=testcase_dashboard["state_dir"],
        records=[record],
    )
    case_id = record["case_id"]
    for target, replay_run in (
        ("amaru", "replay-amaru"),
        ("cardano-node", "replay-cardano-node"),
    ):
        lifecycle.record_replay_result(
            state_dir=testcase_dashboard["state_dir"],
            case_id=case_id,
            target=target,
            run_id=replay_run,
            exit_status="pass",
            behavior_summary={"signature": f"behavior-{target}"},
            resource_summary={"signature": f"resource-{target}"},
        )
    lifecycle.record_compare_result(
        state_dir=testcase_dashboard["state_dir"],
        case_id=case_id,
        comparison_path="compare/result.json",
        agreed=False,
        runs={"amaru": "replay-amaru", "cardano-node": "replay-cardano-node"},
        run_outcomes={"amaru": "pass", "cardano-node": "pass"},
    )
    lifecycle.record_minimization_result(
        state_dir=testcase_dashboard["state_dir"],
        case_id=case_id,
        target="amaru",
        minimized_path="outputs/minimized/input",
        original_size=10,
        minimized_size=3,
        tool="oracle",
    )
    case_path = testcase_dashboard["state_dir"] / "testcases" / f"{case_id}.json"
    promoted_bucket = json.loads(case_path.read_text(encoding="utf-8"))["bucket_id"]
    lifecycle.promote_bucket(
        state_dir=testcase_dashboard["state_dir"],
        bucket_id=promoted_bucket,
        promotion_state="validated",
        summary="reviewed evidence",
        source="operator",
        actor="reviewer",
    )
    state_root = testcase_dashboard["state_dir"] / "testcases"
    before_record = json.loads(case_path.read_text(encoding="utf-8"))
    before_replay = (state_root / "replay-queue.ndjson").read_bytes()
    before_compare = (state_root / "compare-queue.ndjson").read_bytes()

    result = lifecycle.ingest_run_issue(
        runs_dir=testcase_dashboard["runs_dir"],
        state_dir=testcase_dashboard["state_dir"],
        run_id=run_id,
        classification="crash",
        triage_reason="decoder-abort",
        producer="scenario",
        source_artifact_path="manifest.json",
    )

    assert result["case_id"] == case_id
    assert result["bucket_id"] == before_record["bucket_id"]
    assert json.loads(case_path.read_text(encoding="utf-8")) == before_record
    assert (state_root / "replay-queue.ndjson").read_bytes() == before_replay
    assert (state_root / "compare-queue.ndjson").read_bytes() == before_compare


def test_simultaneous_direct_lifecycle_writers_preserve_all_aggregate_rows(
    tmp_path,
):
    lifecycle = importlib.import_module("profile_manager.testcase_lifecycle")
    state_dir = tmp_path / "configured-state"
    process_context = multiprocessing.get_context("spawn")
    barrier = process_context.Barrier(2)
    records = []
    run_dirs = []
    for suffix in ("a", "b"):
        run_dir = tmp_path / "runs" / f"direct-run-{suffix}"
        run_dir.mkdir(parents=True)
        run_dirs.append(run_dir)
        records.append(
            lifecycle.build_testcase_records(
                run_id=run_dir.name,
                producer="afl",
                target_implementation="amaru",
                triage={
                    "interesting_cases": [
                        {
                            "kind": "crash",
                            "reason": f"decoder-abort-{suffix}",
                            "relative_path": f"id-{suffix}",
                            "sha256": suffix * 64,
                            "size_bytes": 1,
                        }
                    ]
                },
                source_root="outputs/afl/default",
                replay_harness="afl-tx-body",
                replay_target_id="amaru-cbor-decode-tx-body",
                replay_targets=["amaru", "cardano-node"],
            )[0]
        )

    results = process_context.Queue()

    processes = [
        process_context.Process(
            target=_synchronized_lifecycle_write,
            args=(run_dir, state_dir, record, barrier, results),
        )
        for run_dir, record in zip(run_dirs, records)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=3)

    assert all(not process.is_alive() for process in processes)
    assert [process.exitcode for process in processes] == [0, 0]
    assert [results.get(timeout=1) for _process in processes] == [None, None]
    state_root = state_dir / "testcases"
    expected_ids = {record["case_id"] for record in records}
    assert {path.stem for path in state_root.glob("tc-*.json")} == expected_ids
    index = [
        json.loads(line)
        for line in (state_root / "index.ndjson").read_text().splitlines()
    ]
    replay = [
        json.loads(line)
        for line in (state_root / "replay-queue.ndjson").read_text().splitlines()
    ]
    compare = [
        json.loads(line)
        for line in (state_root / "compare-queue.ndjson").read_text().splitlines()
    ]
    buckets = [
        json.loads(line)
        for line in (state_root / "buckets.ndjson").read_text().splitlines()
    ]
    assert {record["case_id"] for record in index} == expected_ids
    assert {item["case_id"] for item in replay} == expected_ids
    assert {item["case_id"] for item in compare} == expected_ids
    assert {
        case_id for bucket in buckets for case_id in bucket["case_ids"]
    } == expected_ids


def test_lifecycle_writer_exception_releases_configured_state_lock(
    tmp_path, monkeypatch
):
    lifecycle = importlib.import_module("profile_manager.testcase_lifecycle")
    state_dir = tmp_path / "configured-state"
    run_dir = tmp_path / "packaged-runs" / "direct-run"
    run_dir.mkdir(parents=True)
    record = lifecycle.build_run_issue_record(
        run_id=run_dir.name,
        producer="scenario",
        target_implementation="amaru",
        classification="crash",
        triage_reason="decoder-abort",
        source_artifact_path="manifest.json",
    )
    original_read = lifecycle.read_testcase_state

    def cancelled_read(**_kwargs):
        raise KeyboardInterrupt("cancelled writer")

    monkeypatch.setattr(lifecycle, "read_testcase_state", cancelled_read)
    with pytest.raises(KeyboardInterrupt, match="cancelled writer"):
        lifecycle.write_lifecycle_artifacts(
            run_dir=run_dir, state_dir=state_dir, records=[record]
        )

    monkeypatch.setattr(lifecycle, "read_testcase_state", original_read)
    lifecycle.write_lifecycle_artifacts(
        run_dir=run_dir, state_dir=state_dir, records=[record]
    )

    state_root = state_dir / "testcases"
    assert (state_dir / ".testcase-lifecycle.lock").is_file()
    assert (state_root / f"{record['case_id']}.json").is_file()
    assert not list((tmp_path / "packaged-runs").rglob(".testcase-lifecycle.lock"))


def test_lifecycle_writer_lock_contention_fails_with_bounded_timeout(tmp_path):
    lifecycle = importlib.import_module("profile_manager.testcase_lifecycle")
    state_dir = tmp_path / "configured-state"
    process_context = multiprocessing.get_context("spawn")
    ready = process_context.Event()
    release = process_context.Event()
    holder = process_context.Process(
        target=_hold_testcase_lifecycle_lock,
        args=(state_dir, ready, release),
    )
    holder.start()
    assert ready.wait(timeout=3)
    try:
        with pytest.raises(TimeoutError, match="writer lock timed out"):
            with lifecycle._testcase_write_lock(state_dir, timeout_seconds=0.01):
                pass
    finally:
        release.set()
        holder.join(timeout=3)

    assert not holder.is_alive()
    assert holder.exitcode == 0
    with lifecycle._testcase_write_lock(state_dir, timeout_seconds=0.1):
        pass


def test_duplicate_ingest_preserves_concurrent_replay_and_compare_completion(
    tmp_path,
):
    lifecycle = importlib.import_module("profile_manager.testcase_lifecycle")
    state_dir = tmp_path / "configured-state"
    run_dir = tmp_path / "runs" / "shared-run"
    run_dir.mkdir(parents=True)
    record = lifecycle.build_testcase_records(
        run_id=run_dir.name,
        producer="afl",
        target_implementation="amaru",
        triage={
            "interesting_cases": [
                {
                    "kind": "crash",
                    "reason": "decoder-abort",
                    "relative_path": "id-1",
                    "sha256": "a" * 64,
                    "size_bytes": 1,
                }
            ]
        },
        source_root="outputs/afl/default",
        replay_harness="afl-tx-body",
        replay_target_id="amaru-cbor-decode-tx-body",
        replay_targets=["amaru", "cardano-node"],
    )[0]
    lifecycle.write_lifecycle_artifacts(
        run_dir=run_dir, state_dir=state_dir, records=[record]
    )
    process_context = multiprocessing.get_context("spawn")
    barrier = process_context.Barrier(3)
    results = process_context.Queue()
    processes = [
        process_context.Process(
            target=_run_contending_lifecycle_mutation,
            args=(action, run_dir, state_dir, record, barrier, results),
        )
        for action in ("ingest", "replay", "compare")
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=5)

    assert all(not process.is_alive() for process in processes)
    assert [process.exitcode for process in processes] == [0, 0, 0]
    assert [results.get(timeout=1) for _process in processes] == [None, None, None]
    state_root = state_dir / "testcases"
    retained = json.loads(
        (state_root / f"{record['case_id']}.json").read_text(encoding="utf-8")
    )
    assert retained["replay_results"] == [
        {
            "target": "amaru",
            "run_id": "replay-amaru-complete",
            "exit_status": "pass",
            "behavior_summary": {"signature": "behavior-kept"},
            "resource_summary": {"signature": "resource-kept"},
        }
    ]
    assert retained["compare_result"] == {
        "agreed": False,
        "comparison_path": "compare/kept.json",
        "runs": {"amaru": "replay-amaru", "cardano-node": "replay-cardano"},
        "run_outcomes": {"amaru": "pass", "cardano-node": "clean_error"},
        "behavior_summaries": {"amaru": {"signature": "behavior-kept"}},
        "resource_summaries": {"amaru": {"signature": "resource-kept"}},
    }
    replay_queue = [
        json.loads(line)
        for line in (state_root / "replay-queue.ndjson").read_text().splitlines()
    ]
    replay_item = next(item for item in replay_queue if item["target"] == "amaru")
    assert replay_item["state"] == "complete"
    assert replay_item["replay_run_id"] == "replay-amaru-complete"
    compare_queue = [
        json.loads(line)
        for line in (state_root / "compare-queue.ndjson").read_text().splitlines()
    ]
    assert compare_queue[0]["state"] == "complete"
    assert compare_queue[0]["comparison_path"] == "compare/kept.json"


def test_promotion_minimization_and_repair_share_lifecycle_writer_lock(
    tmp_path, monkeypatch
):
    lifecycle = importlib.import_module("profile_manager.testcase_lifecycle")
    state_dir = tmp_path / "configured-state"
    run_dir = tmp_path / "runs" / "lock-coverage-run"
    run_dir.mkdir(parents=True)
    record = lifecycle.build_run_issue_record(
        run_id=run_dir.name,
        producer="scenario",
        target_implementation="amaru",
        classification="crash",
        triage_reason="decoder-abort",
        source_artifact_path="manifest.json",
    )
    lifecycle.write_lifecycle_artifacts(
        run_dir=run_dir, state_dir=state_dir, records=[record]
    )
    lock_entries = []
    original_lock = lifecycle._testcase_write_lock

    @contextmanager
    def tracked_lock(locked_state_dir, **kwargs):
        lock_entries.append(Path(locked_state_dir))
        with original_lock(locked_state_dir, **kwargs):
            yield

    monkeypatch.setattr(lifecycle, "_testcase_write_lock", tracked_lock)
    lifecycle.promote_bucket(
        state_dir=state_dir,
        bucket_id=record["bucket_id"],
        promotion_state="validated",
        summary="reviewed",
        source="operator",
    )
    lifecycle.record_minimization_result(
        state_dir=state_dir,
        case_id=record["case_id"],
        target="amaru",
        minimized_path="outputs/minimized/input",
        original_size=10,
        minimized_size=3,
        tool="oracle",
    )
    lifecycle.record_minimization_failure(
        state_dir=state_dir,
        case_id=record["case_id"],
        target="cardano-node",
        tool="oracle",
        error="clean failure",
    )
    lifecycle.repair_state(state_dir=state_dir)

    assert lock_entries == [state_dir, state_dir, state_dir, state_dir]


def test_native_ingest_rejects_active_manifest(testcase_dashboard):
    manifest_path = (
        testcase_dashboard["runs_dir"]
        / testcase_dashboard["run_id"]
        / "manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("exit_status")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    status, location, body = _post(
        testcase_dashboard["url"],
        "/operate/testcases/ingest",
        {
            "token": TOKEN,
            "run_id": testcase_dashboard["run_id"],
            "classification": "crash",
            "triage_reason": "decoder-abort",
        },
    )

    assert (status, location) == (422, None)
    assert "not terminal" in body.decode("utf-8").lower()
    assert not (testcase_dashboard["state_dir"] / "testcases").exists()


def test_native_ingest_preserves_existing_cases_and_queue_entries(testcase_dashboard):
    lifecycle = importlib.import_module("profile_manager.testcase_lifecycle")
    existing = []
    for run_id, relative_path in (("old-run-a", "id-1"), ("old-run-b", "id-2")):
        existing.extend(
            lifecycle.build_testcase_records(
                run_id=run_id,
                producer="afl",
                target_implementation="amaru",
                triage={
                    "interesting_cases": [
                        {
                            "kind": "crash",
                            "reason": "decoder-abort",
                            "relative_path": relative_path,
                            "sha256": "0" * 64,
                            "size_bytes": 1,
                        }
                    ]
                },
                source_root="outputs/afl/default",
                replay_harness="afl-tx-body",
                replay_target_id="amaru-cbor-decode-tx-body",
                replay_targets=["amaru", "cardano-node"],
            )
        )
    lifecycle.write_lifecycle_artifacts(
        run_dir=testcase_dashboard["runs_dir"] / testcase_dashboard["run_id"],
        state_dir=testcase_dashboard["state_dir"],
        records=existing,
    )
    existing_ids = {record["case_id"] for record in existing}
    state_root = testcase_dashboard["state_dir"] / "testcases"
    replay_before = [
        json.loads(line)
        for line in (state_root / "replay-queue.ndjson").read_text().splitlines()
    ]
    replay_before[0].update(
        {
            "state": "complete",
            "replay_run_id": "completed-replay",
            "exit_status": "pass",
            "behavior_summary": {"signature": "kept"},
        }
    )
    (state_root / "replay-queue.ndjson").write_text(
        "".join(json.dumps(entry, sort_keys=True) + "\n" for entry in replay_before),
        encoding="utf-8",
    )
    compare_before = [
        json.loads(line)
        for line in (state_root / "compare-queue.ndjson").read_text().splitlines()
    ]
    compare_before[0].update(
        {
            "state": "complete",
            "comparison_path": "compare/kept.json",
            "agreed": False,
        }
    )
    (state_root / "compare-queue.ndjson").write_text(
        "".join(json.dumps(entry, sort_keys=True) + "\n" for entry in compare_before),
        encoding="utf-8",
    )

    status, location, _body = _post(
        testcase_dashboard["url"],
        "/operate/testcases/ingest",
        {
            "token": TOKEN,
            "run_id": testcase_dashboard["run_id"],
            "classification": "runtime_anomaly",
            "triage_reason": "node-exit",
        },
    )

    assert status == 303
    created_id = location.rsplit("/", 1)[1]
    index = [json.loads(line) for line in (state_root / "index.ndjson").read_text().splitlines()]
    replay = [json.loads(line) for line in (state_root / "replay-queue.ndjson").read_text().splitlines()]
    compare = [json.loads(line) for line in (state_root / "compare-queue.ndjson").read_text().splitlines()]
    assert {record["case_id"] for record in index} == existing_ids | {created_id}
    assert {entry["case_id"] for entry in replay} == existing_ids
    assert {entry["case_id"] for entry in compare} == existing_ids
    assert replay == replay_before
    assert compare == compare_before


def test_basic_empty_buckets_page_explains_derivation_and_links_to_cases(tmp_path, monkeypatch):
    dashboard = importlib.import_module("profile_manager.dashboard")
    _env(monkeypatch, tmp_path, tmp_path / "empty", tmp_path / "runs")

    basic = _basic(dashboard.render_route_html("/operate/testcase-buckets"))

    assert "0 buckets group 0 retained cases" in basic
    assert "No testcase buckets exist" in basic
    assert "derived from testcase records" in basic
    assert 'href="/operate/testcases"' in basic
    assert html.escape(CLI_PREFIX + "testcase ingest-run") in basic
    assert "/api/assets/testcase-buckets/export" not in basic
    _assert_safe_links(basic)


def test_basic_populated_pages_expose_real_exports_and_navigation(tmp_path, monkeypatch):
    dashboard = importlib.import_module("profile_manager.dashboard")
    state_dir, runs_dir, records = _seed_lifecycle(tmp_path)
    _env(monkeypatch, tmp_path, state_dir, runs_dir)
    case_id = records[0]["case_id"]
    bucket_id = records[0]["bucket_id"]

    index = _basic(dashboard.render_route_html("/operate/testcases"))
    buckets = _basic(dashboard.render_route_html("/operate/testcase-buckets"))

    assert f'href="/operate/testcases/{case_id}"' in index
    assert 'href="/api/assets/testcases/export"' in index
    assert html.escape(CLI_PREFIX + "testcase replay-queue run") in index
    assert html.escape(CLI_PREFIX + "testcase compare-queue run") in index
    assert f'href="/operate/testcase-buckets/{bucket_id}"' in buckets
    assert 'href="/api/assets/testcase-buckets/export"' in buckets
    assert html.escape(CLI_PREFIX + "testcase buckets summary") in buckets
    for page in (index, buckets):
        _assert_safe_links(page)
    for href in ("/api/assets/testcases/export", "/api/assets/testcase-buckets/export"):
        status = dashboard.dispatch_api_request(href)[0]
        assert status == 200, href


def test_basic_detail_and_bucket_pages_link_runs_and_show_parseable_actions(tmp_path, monkeypatch):
    dashboard = importlib.import_module("profile_manager.dashboard")
    data = importlib.import_module("profile_manager.data.operate_testcases")
    state_dir, runs_dir, records = _seed_lifecycle(tmp_path)
    _env(monkeypatch, tmp_path, state_dir, runs_dir)
    case_id = records[0]["case_id"]
    bucket_id = records[0]["bucket_id"]

    detail = _basic(dashboard.render_route_html(f"/operate/testcases/{case_id}"))
    bucket = _basic(dashboard.render_route_html(f"/operate/testcase-buckets/{bucket_id}"))

    assert 'href="/operate/runs/run-a"' in detail
    assert f'href="/operate/testcase-buckets/{bucket_id}"' in detail
    assert f'href="/api/assets/testcases/{case_id}/export"' in detail
    for command in data.testcase_detail(case_id)["cli_commands"]:
        assert html.escape(command) in detail
        _parses(command)

    bucket_detail = data.testcase_bucket_detail(bucket_id)
    assert f'href="/operate/testcases/{case_id}"' in bucket
    assert f'href="/api/assets/testcase-buckets/{bucket_id}/export"' in bucket
    assert bucket_detail["cli_commands"]
    for command in bucket_detail["cli_commands"]:
        assert html.escape(command) in bucket
        _parses(command)
    for page in (detail, bucket):
        _assert_safe_links(page)
    assert dashboard.dispatch_api_request(f"/api/assets/testcase-buckets/{bucket_id}/export")[0] == 200
