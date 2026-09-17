import gzip
import hashlib
import importlib
import io
import json
import tarfile
from pathlib import Path


def _tree_state(root: Path) -> list[tuple[str, bytes]]:
    if not root.exists():
        return []
    return [
        (str(path.relative_to(root)), path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def _seed_lifecycle(tmp_path: Path) -> tuple[Path, Path, list[dict]]:
    lifecycle = importlib.import_module("profile_manager.testcase_lifecycle")
    state_dir = tmp_path / "state"
    state_root = state_dir / "testcases"
    runs_dir = tmp_path / "runs"
    state_root.mkdir(parents=True)

    artifact = runs_dir / "run-a" / "outputs" / "afl" / "default" / "queue" / "id-000001"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"\x82\x01\x02")
    (runs_dir / "run-b").mkdir(parents=True)
    (runs_dir / "run-b" / "manifest.json").write_text('{"exit_status":"fail"}\n', encoding="utf-8")

    fuzz = lifecycle.build_testcase_records(
        run_id="run-a",
        producer="afl",
        target_implementation="amaru",
        triage={
            "interesting_cases": [
                {
                    "kind": "queue",
                    "reason": "novel-path",
                    "relative_path": "id-000001",
                    "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                    "size_bytes": 3,
                    "metadata": {"scenario_id": "scenario-a"},
                }
            ]
        },
        source_root="outputs/afl/default",
        replay_harness="afl-tx-body",
        replay_target_id="amaru-cbor-decode-tx-body",
        replay_targets=["amaru", "cardano-node"],
    )[0]
    fuzz.update(
        {
            "replay_state": "complete",
            "replay_results": [{"target": "amaru", "run_id": "run-replay", "exit_status": "pass"}],
            "minimization_state": "complete",
            "minimization_results": [
                {
                    "status": "complete",
                    "target": "amaru",
                    "tool": "oracle",
                    "minimized_path": "outputs/minimized/id-000001",
                    "original_size": 3,
                    "minimized_size": 2,
                }
            ],
            "compare_state": "complete",
            "compare_result": {
                "agreed": False,
                "comparison_path": "compare/run-a.json",
                "runs": {"amaru": "run-replay", "cardano-node": "run-compare"},
            },
            "promotion": {
                "state": "candidate",
                "summary": "Needs review",
                "source": "operator",
            },
            "promotion_history": [
                {"state": "candidate", "summary": "Needs review", "source": "operator"}
            ],
        }
    )
    run_issue = lifecycle.build_run_issue_record(
        run_id="run-b",
        producer="scenario",
        target_implementation="cardano-node",
        classification="runtime_anomaly",
        triage_reason="node-exit",
        source_artifact_path="manifest.json",
        metadata={"scenario_id": "scenario-b", "runtime": "local-devnet"},
    )
    missing = lifecycle.build_run_issue_record(
        run_id="run-missing",
        producer="scenario",
        target_implementation="amaru",
        classification="runtime_anomaly",
        triage_reason="missing-retained-artifact",
        source_artifact_path="manifest.json",
    )
    unsafe = lifecycle.build_run_issue_record(
        run_id="run-a",
        producer="scenario",
        target_implementation="amaru",
        classification="runtime_anomaly",
        triage_reason="unsafe-test-record",
        source_artifact_path="../../outside",
    )
    records = [fuzz, run_issue, missing, unsafe]
    for record in records:
        (state_root / f"{record['case_id']}.json").write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    (state_root / "index.ndjson").write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )
    buckets = lifecycle.build_bucket_rows(records)
    (state_root / "buckets.ndjson").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in buckets),
        encoding="utf-8",
    )
    (state_root / "replay-queue.ndjson").write_text(
        json.dumps({"queue_id": "rq-1", "case_id": fuzz["case_id"], "state": "complete"}) + "\n",
        encoding="utf-8",
    )
    (state_root / "compare-queue.ndjson").write_text(
        json.dumps({"queue_id": "cq-1", "case_id": fuzz["case_id"], "state": "complete"}) + "\n",
        encoding="utf-8",
    )
    (state_root / "tc-malformed.json").write_text("{not json\n", encoding="utf-8")
    (state_root / "._tc-noise.json").write_text("noise\n", encoding="utf-8")
    (state_root / ".DS_Store").write_text("noise\n", encoding="utf-8")
    return state_dir, runs_dir, records


def test_lifecycle_reader_is_read_only_and_reports_malformed_records(tmp_path):
    lifecycle = importlib.import_module("profile_manager.testcase_lifecycle")
    state_dir, _runs_dir, records = _seed_lifecycle(tmp_path)
    before = _tree_state(tmp_path)

    snapshot = lifecycle.read_testcase_state(state_dir=state_dir)

    assert _tree_state(tmp_path) == before
    assert len(snapshot["records"]) == len(records)
    assert len(snapshot["malformed_records"]) == 1
    assert snapshot["malformed_records"][0]["case_id"] == "tc-malformed"
    assert {row["bucket_id"] for row in snapshot["bucket_rows"]} == {
        row["bucket_id"] for row in lifecycle.build_bucket_rows(records)
    }
    assert snapshot["replay_queue"][0]["state"] == "complete"
    assert snapshot["compare_queue"][0]["state"] == "complete"


def test_lifecycle_reader_normalizes_missing_case_id_and_rejects_unsafe_ids(tmp_path):
    lifecycle = importlib.import_module("profile_manager.testcase_lifecycle")
    root = tmp_path / "state" / "testcases"
    root.mkdir(parents=True)
    (root / "tc-filename.json").write_text(
        json.dumps({"classification": "queue", "triage_reason": "seed"}) + "\n",
        encoding="utf-8",
    )
    (root / "tc-bad:id.json").write_text(
        json.dumps({"case_id": "tc-bad:id", "classification": "queue"}) + "\n",
        encoding="utf-8",
    )

    snapshot = lifecycle.read_testcase_state(state_dir=tmp_path / "state")

    assert snapshot["records"][0]["case_id"] == "tc-filename"
    assert [row["case_id"] for row in snapshot["malformed_records"]] == ["tc-bad:id"]


def test_missing_lifecycle_state_is_empty_and_not_created(tmp_path):
    lifecycle = importlib.import_module("profile_manager.testcase_lifecycle")
    state_dir = tmp_path / "missing-state"

    snapshot = lifecycle.read_testcase_state(state_dir=state_dir)

    assert snapshot["records"] == []
    assert snapshot["bucket_rows"] == []
    assert not state_dir.exists()


def test_testcase_catalog_matches_lifecycle_and_never_mutates_get_state(tmp_path):
    module = importlib.import_module("profile_manager.data.operate_testcases")
    state_dir, runs_dir, records = _seed_lifecycle(tmp_path)
    before = _tree_state(tmp_path)

    rows = module.testcase_catalog_rows(state_dir=state_dir, runs_dir=runs_dir)
    buckets = module.testcase_bucket_rows(state_dir=state_dir, runs_dir=runs_dir)

    assert _tree_state(tmp_path) == before
    assert len(rows) == len(records) + 1
    assert {row["id"] for row in rows if row["status"] != "malformed"} == {
        record["case_id"] for record in records
    }
    assert {row["bucket_id"] for row in buckets} == {
        record["bucket_id"] for record in records
    }
    assert next(row for row in rows if row["id"] == "tc-malformed")["status"] == "malformed"
    assert next(row for row in rows if row["triage_reason"] == "missing-retained-artifact")["status"] == "artifact-missing"
    assert next(row for row in rows if row["triage_reason"] == "unsafe-test-record")["status"] == "unsafe-path"


def test_testcase_detail_preserves_lifecycle_history_and_relationships(tmp_path):
    module = importlib.import_module("profile_manager.data.operate_testcases")
    state_dir, runs_dir, records = _seed_lifecycle(tmp_path)
    case_id = records[0]["case_id"]

    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    (scenarios / "scenario-a.yaml").write_text("id: scenario-a\n", encoding="utf-8")
    detail = module.testcase_detail(
        case_id,
        state_dir=state_dir,
        runs_dir=runs_dir,
        scenarios_dir=scenarios,
    )

    assert detail["source_run_url"] == "/operate/runs/run-a"
    assert detail["scenario_url"] == "/operate/scenarios/scenario-a"
    assert detail["artifact_available"] is True
    assert detail["sha256"] == hashlib.sha256(b"\x82\x01\x02").hexdigest()
    assert detail["replay_results"][0]["run_id"] == "run-replay"
    assert detail["minimization_results"][0]["tool"] == "oracle"
    assert detail["compare_result"]["agreed"] is False
    assert detail["promotion_history"][0]["state"] == "candidate"
    assert "testcase replay" in detail["cli_commands"][0]
    assert "testcase minimize" in " ".join(detail["cli_commands"])
    assert "testcase compare" in " ".join(detail["cli_commands"])
    assert detail["retained_files"]
    assert {(item["catalog"], item["id"], item["resolved"]) for item in detail["relationships"]} == {
        ("runs", "run-a", True),
        ("scenarios", "scenario-a", True),
    }

    missing = module.testcase_detail(
        case_id,
        state_dir=state_dir,
        runs_dir=runs_dir,
        scenarios_dir=tmp_path / "missing-scenarios",
    )
    assert next(item for item in missing["relationships"] if item["catalog"] == "scenarios")["resolved"] is False


def test_bucket_membership_and_detail_match_lifecycle_projection(tmp_path):
    module = importlib.import_module("profile_manager.data.operate_testcases")
    lifecycle = importlib.import_module("profile_manager.testcase_lifecycle")
    state_dir, runs_dir, records = _seed_lifecycle(tmp_path)
    expected = lifecycle.build_bucket_rows(records)

    rows = module.testcase_bucket_rows(state_dir=state_dir, runs_dir=runs_dir)
    for expected_bucket in expected:
        detail = module.testcase_bucket_detail(
            expected_bucket["bucket_id"], state_dir=state_dir, runs_dir=runs_dir
        )
        assert detail is not None
        assert detail["case_ids"] == expected_bucket["case_ids"]
        assert {row["id"] for row in detail["members"]} == set(expected_bucket["case_ids"])


def test_testcase_exact_download_archive_and_artifact_containment(tmp_path, monkeypatch):
    dashboard = importlib.import_module("profile_manager.dashboard")
    state_dir, runs_dir, records = _seed_lifecycle(tmp_path)
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(state_dir))
    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(runs_dir))
    case_id = records[0]["case_id"]
    unsafe_id = next(row["case_id"] for row in records if row["triage_reason"] == "unsafe-test-record")

    source = dashboard.dispatch_api_request(f"/api/assets/testcases/{case_id}/download")
    artifact = dashboard.dispatch_api_request(f"/api/assets/testcases/{case_id}/artifact")
    unsafe = dashboard.dispatch_api_request(f"/api/assets/testcases/{unsafe_id}/artifact")
    first = dashboard.dispatch_api_request("/api/assets/testcases/export")
    second = dashboard.dispatch_api_request("/api/assets/testcases/export")

    assert source[0:2] == (200, "application/json; charset=utf-8")
    assert json.loads(source[2])["case_id"] == case_id
    assert artifact[0:3] == (200, "application/octet-stream", b"\x82\x01\x02")
    assert unsafe[0] == 400
    assert first[2] == second[2]
    with gzip.GzipFile(fileobj=io.BytesIO(first[2]), mode="rb") as zipped:
        with tarfile.open(fileobj=zipped, mode="r:") as archive:
            names = archive.getnames()
    assert len(names) == len(records) + 2
    assert "DWARF-EXPORT-MANIFEST.json" in names
    assert not any("._" in name or ".DS_Store" in name or "__pycache__" in name for name in names)

    bucket_id = records[0]["bucket_id"]
    bucket = dashboard.dispatch_api_request(
        f"/api/assets/testcase-buckets/{bucket_id}/download"
    )
    assert bucket[0:2] == (200, "application/json; charset=utf-8")
    bucket_body = bucket[2].decode("utf-8")
    assert str(tmp_path) not in bucket_body
    assert "raw_source" not in bucket_body
    assert json.loads(bucket_body)["case_ids"]


def test_testcase_routes_learn_reference_and_empty_state(tmp_path, monkeypatch):
    dashboard = importlib.import_module("profile_manager.dashboard")
    state_dir, runs_dir, records = _seed_lifecycle(tmp_path)
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(state_dir))
    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(runs_dir))
    case_id = records[0]["case_id"]
    bucket_id = records[0]["bucket_id"]

    index = dashboard.render_route_html("/operate/testcases")
    detail = dashboard.render_route_html(f"/operate/testcases/{case_id}")
    buckets = dashboard.render_route_html("/operate/testcase-buckets")
    bucket = dashboard.render_route_html(f"/operate/testcase-buckets/{bucket_id}")
    learn = dashboard.render_route_html("/learn/testcases")

    assert "Testcases" in index and case_id in index
    assert "Lifecycle history" in detail and "CLI actions" in detail
    assert f"/api/assets/testcases/{case_id}/export" in detail
    assert "Testcase buckets" in buckets and bucket_id in buckets
    assert "Bucket signature" in bucket and case_id in bucket
    assert f"/api/assets/testcase-buckets/{bucket_id}/export" in bucket
    for term in ("testcase", "crash", "finding", "bucket", "corpus input", "bundle"):
        assert term in learn.lower()
    assert 'href="/learn/cli#cli-testcase"' in learn
    assert 'href="/learn/cli#testcase"' not in learn
    assert dashboard.render_route_html("/operate/testcases/not-found") is None
    assert dashboard.render_route_html("/operate/testcase-buckets/not-found") is None

    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(tmp_path / "empty"))
    assert "No testcase records" in dashboard.render_route_html("/operate/testcases")
    assert "No testcase buckets" in dashboard.render_route_html("/operate/testcase-buckets")


def test_testcase_catalogs_remain_compatible_but_are_hidden_from_primary_navigation(tmp_path, monkeypatch):
    dashboard = importlib.import_module("profile_manager.dashboard")
    learn_api = importlib.import_module("profile_manager.data.learn_api")
    nav = importlib.import_module("profile_manager.data.sub_nav")
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(tmp_path / "state"))
    routes = {route for group in learn_api.html_route_groups() for route in group["routes"]}

    assert {
        "/operate/testcases",
        "/operate/testcases/<id>",
        "/operate/testcase-buckets",
        "/operate/testcase-buckets/<id>",
    } <= routes
    assert "/learn/testcases" in routes
    assert any(
        endpoint["path"] == "/api/assets/testcases/<id>/artifact"
        for endpoint in learn_api.ENDPOINTS
    )
    assert not any(item["url"] == "/operate/testcases" for item in nav.OPERATE_SUB_NAV)
    assert not any(item["url"] == "/operate/testcase-buckets" for item in nav.OPERATE_SUB_NAV)
    assert not any(item["url"] == "/learn/testcases" for item in nav.LEARN_SUB_NAV)
    assert 'href="/operate/testcases"' not in dashboard.render_route_html("/operate")
    assert 'href="/operate/testcase-buckets"' not in dashboard.render_route_html("/operate")
    assert 'href="/learn/testcases"' not in dashboard.render_route_html("/learn")


def test_visual_audit_covers_testcase_indexes_and_dynamic_details():
    root = Path(__file__).resolve().parents[1]
    source = (root / "tools/dashboard_visual_audit.js").read_text(encoding="utf-8")

    for route in (
        "/operate/testcases",
        "/operate/testcase-buckets",
        "/learn/testcases",
    ):
        assert repr(route) in source
    assert "source.endsWith('/testcases')" in source
    assert "source.endsWith('/testcase-buckets')" in source


def test_testcase_term_grid_is_deliberate_on_desktop_and_mobile():
    root = Path(__file__).resolve().parents[1]
    template = (root / "dwarf/dashboard/templates/learn/testcases.j2").read_text(
        encoding="utf-8"
    )
    css = (root / "dwarf/dashboard/static/css/base.css").read_text(encoding="utf-8")

    assert "testcase-term-grid" in template
    assert ".testcase-term-grid" in css
    assert "repeat(3, minmax(0, 1fr))" in css
    assert "@media (max-width: 640px)" in css


def test_testcase_bucket_status_pills_wrap_inside_mobile_cards():
    root = Path(__file__).resolve().parents[1]
    template = (
        root / "dwarf/dashboard/templates/operate/testcase_buckets.j2"
    ).read_text(encoding="utf-8")
    css = (root / "dwarf/dashboard/static/css/base.css").read_text(encoding="utf-8")

    assert 'class="asset-pill-row"' in template
    assert ".asset-pill-row" in css
    assert "flex-wrap: wrap" in css
