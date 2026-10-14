from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from profile_manager import dashboard
from profile_manager.data.runs import run_history_page
from profile_manager.run_ledger import RunLedger


def _timestamp(index: int) -> str:
    value = datetime(2026, 10, 8, tzinfo=timezone.utc) + timedelta(seconds=index)
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _entry(ledger: RunLedger, index: int, *, kind: str = "scenario") -> str:
    run_id = f"20261008T000000Z-{index:08x}"
    ledger.create(
        run_id=run_id,
        kind=kind,
        origin="test",
        backend="local",
        scenario={"id": f"scenario-{index}"} if kind == "scenario" else None,
        queued_at=_timestamp(index),
    )
    return run_id


def test_ledger_history_has_true_total_and_stable_newest_first_cursor_pages(tmp_path):
    ledger = RunLedger(tmp_path / "state")
    expected = [_entry(ledger, index) for index in range(205)]
    _entry(ledger, 1000, kind="maintenance")
    _entry(ledger, 1001, kind="deployment")

    first = run_history_page(
        runs_dir=tmp_path / "runs",
        state_dir=tmp_path / "state",
        limit=100,
    )
    second = run_history_page(
        runs_dir=tmp_path / "runs",
        state_dir=tmp_path / "state",
        limit=100,
        cursor=first["next_cursor"],
    )
    third = run_history_page(
        runs_dir=tmp_path / "runs",
        state_dir=tmp_path / "state",
        limit=100,
        cursor=second["next_cursor"],
    )

    assert first["scope"] == "scenario"
    assert first["total"] == 205
    assert [row["run_id"] for row in first["runs"]] == list(reversed(expected))[:100]
    assert len(second["runs"]) == 100
    assert len(third["runs"]) == 5
    assert third["next_cursor"] is None
    all_ids = [row["run_id"] for page in (first, second, third) for row in page["runs"]]
    assert all_ids == list(reversed(expected))
    assert len(all_ids) == len(set(all_ids))


def test_history_defaults_to_scenarios_and_other_kinds_are_explicit(tmp_path):
    ledger = RunLedger(tmp_path / "state")
    scenario_id = _entry(ledger, 1, kind="scenario")
    ci_id = _entry(ledger, 2, kind="ci")
    external_id = _entry(ledger, 3, kind="external")
    maintenance_id = _entry(ledger, 4, kind="maintenance")
    deployment_id = _entry(ledger, 5, kind="deployment")

    scenario_page = run_history_page(
        runs_dir=tmp_path / "runs", state_dir=tmp_path / "state", limit=20
    )
    ci_page = run_history_page(
        runs_dir=tmp_path / "runs", state_dir=tmp_path / "state", limit=20, scope="ci"
    )
    external_page = run_history_page(
        runs_dir=tmp_path / "runs", state_dir=tmp_path / "state", limit=20, scope="external"
    )
    operations_page = run_history_page(
        runs_dir=tmp_path / "runs",
        state_dir=tmp_path / "state",
        limit=20,
        scope="operations",
    )

    assert [row["run_id"] for row in scenario_page["runs"]] == [scenario_id]
    assert [row["run_id"] for row in ci_page["runs"]] == [ci_id]
    assert [row["run_id"] for row in external_page["runs"]] == [external_id]
    assert {row["run_id"] for row in operations_page["runs"]} == {
        maintenance_id,
        deployment_id,
    }
    assert all(row["verdict"] is None for row in operations_page["runs"])


def test_running_entry_updates_in_place_without_history_duplication(tmp_path):
    ledger = RunLedger(tmp_path / "state")
    run_id = _entry(ledger, 1)
    ledger.transition(run_id, "running", started_at=_timestamp(2))

    running = run_history_page(
        runs_dir=tmp_path / "runs", state_dir=tmp_path / "state", limit=20
    )
    assert [(row["run_id"], row["state"]) for row in running["runs"]] == [
        (run_id, "running")
    ]

    ledger.transition(run_id, "completed", ended_at=_timestamp(3), verdict="pass")
    completed = run_history_page(
        runs_dir=tmp_path / "runs", state_dir=tmp_path / "state", limit=20
    )
    assert [(row["run_id"], row["state"], row["verdict"]) for row in completed["runs"]] == [
        (run_id, "completed", "pass")
    ]


def test_cursor_page_uses_materialized_indexes_and_hydrates_only_selected_manifests(
    tmp_path, monkeypatch
):
    runs_dir = tmp_path / "runs"
    state_dir = tmp_path / "state"
    ledger = RunLedger(state_dir)
    for index in range(205):
        run_id = _entry(ledger, index)
        run_dir = runs_dir / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "manifest.json").write_text(
            json.dumps({"run_id": run_id, "scenario": {"id": f"scenario-{index}"}}),
            encoding="utf-8",
        )

    first = run_history_page(
        runs_dir=runs_dir,
        state_dir=state_dir,
        limit=20,
    )
    assert first["total"] == 205
    assert len(first["runs"]) == 20

    manifest_reads = []
    original_read_text = type(runs_dir).read_text

    def counted_read_text(path, *args, **kwargs):
        if path.name == "manifest.json":
            manifest_reads.append(path)
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(type(runs_dir), "read_text", counted_read_text)
    monkeypatch.setattr(
        RunLedger,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("cursor pages must not reparse ledger entry files")
        ),
    )
    second = run_history_page(
        runs_dir=runs_dir,
        state_dir=state_dir,
        limit=20,
        cursor=first["next_cursor"],
    )
    assert len(second["runs"]) == 20
    assert len(manifest_reads) == 20


def test_history_normalizes_timezone_offsets_before_sorting(tmp_path):
    ledger = RunLedger(tmp_path / "state")
    older = ledger.create(
        run_id="offset-older",
        kind="scenario",
        origin="test",
        backend="local",
        queued_at="2026-10-08T00:00:00+05:00",
    )["run_id"]
    newer = ledger.create(
        run_id="utc-newer",
        kind="scenario",
        origin="test",
        backend="local",
        queued_at="2026-10-07T23:00:00Z",
    )["run_id"]

    page = run_history_page(
        runs_dir=tmp_path / "runs", state_dir=tmp_path / "state", limit=20
    )

    assert [row["run_id"] for row in page["runs"]] == [newer, older]


def test_outcome_filter_is_applied_before_cursor_slicing(tmp_path):
    ledger = RunLedger(tmp_path / "state")
    failed_id = _entry(ledger, 0)
    ledger.transition(failed_id, "failed", verdict="fail", ended_at=_timestamp(1))
    for index in range(1, 106):
        _entry(ledger, index)

    page = run_history_page(
        runs_dir=tmp_path / "runs",
        state_dir=tmp_path / "state",
        limit=100,
        outcome="fail",
    )

    assert page["total"] == 1
    assert page["scope_total"] == 106
    assert [row["run_id"] for row in page["runs"]] == [failed_id]


def test_history_keeps_manifest_only_legacy_runs_without_overriding_ledger(tmp_path):
    runs_dir = tmp_path / "runs"
    state_dir = tmp_path / "state"
    ledger = RunLedger(state_dir)
    ledger_id = _entry(ledger, 2)
    legacy_id = "20261008T000000Z-legacy001"
    legacy_dir = runs_dir / legacy_id
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": legacy_id,
                "scenario": {"id": "legacy-scenario"},
                "runtime": "library",
                "exit_status": "pass",
                "started_at": _timestamp(0),
                "ended_at": _timestamp(1),
                "assertion_summary": {"total": 1, "pass": 1, "fail": 0},
            }
        )
    )

    page = run_history_page(runs_dir=runs_dir, state_dir=state_dir, limit=20, scope="all")

    assert page["total"] == 2
    by_id = {row["run_id"]: row for row in page["runs"]}
    assert by_id[ledger_id]["ledger_backed"] is True
    assert by_id[legacy_id]["ledger_backed"] is False
    assert by_id[legacy_id]["run_kind"] == "external"
    assert by_id[legacy_id]["verdict"] == "pass"


def test_runs_api_exposes_page_metadata_and_validates_cursor(tmp_path, monkeypatch):
    runs_dir = tmp_path / "runs"
    state_dir = tmp_path / "state"
    bundles_dir = tmp_path / "bundles"
    ledger = RunLedger(state_dir)
    for index in range(3):
        _entry(ledger, index)
    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(runs_dir))
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(state_dir))
    monkeypatch.setenv("ADA2_DWARF_BUNDLES_DIR", str(bundles_dir))

    status, content_type, body = dashboard.dispatch_api_request(
        "/api/runs?limit=2", runs_dir=runs_dir, bundles_dir=bundles_dir
    )
    payload = json.loads(body)

    assert status == 200
    assert content_type == "application/json; charset=utf-8"
    assert len(payload["recent_runs"]) == 2
    assert payload["page"]["total"] == 3
    assert payload["page"]["scope"] == "scenario"
    assert payload["page"]["next_cursor"]

    status, _, body = dashboard.dispatch_api_request(
        f"/api/runs?limit=2&scope=operations&cursor={payload['page']['next_cursor']}",
        runs_dir=runs_dir,
        bundles_dir=bundles_dir,
    )
    assert status == 400
    assert json.loads(body) == {"error": "invalid run-history cursor"}

    status, _, body = dashboard.dispatch_api_request(
        "/api/runs?limit=2&cursor=not-a-cursor",
        runs_dir=runs_dir,
        bundles_dir=bundles_dir,
    )
    assert status == 400
    assert json.loads(body) == {"error": "invalid run-history cursor"}

    status, _, body = dashboard.dispatch_api_request(
        "/api/runs?scope=unknown",
        runs_dir=runs_dir,
        bundles_dir=bundles_dir,
    )
    assert status == 400
    assert json.loads(body) == {
        "error": "scope must be scenario, ci, external, operations, or all"
    }


def test_runs_http_routes_accept_inconclusive_outcome(tmp_path, monkeypatch):
    runs_dir = tmp_path / "runs"
    state_dir = tmp_path / "state"
    bundles_dir = tmp_path / "bundles"
    ledger = RunLedger(state_dir)
    inconclusive_id = _entry(ledger, 10, kind="external")
    failed_id = _entry(ledger, 11, kind="external")
    ledger.transition(inconclusive_id, "running")
    ledger.transition(inconclusive_id, "completed", verdict="inconclusive")
    ledger.transition(failed_id, "running")
    ledger.transition(failed_id, "failed", verdict="fail")
    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(runs_dir))
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(state_dir))
    monkeypatch.setenv("ADA2_DWARF_BUNDLES_DIR", str(bundles_dir))

    status, _, body = dashboard.dispatch_api_request(
        "/api/runs?scope=external&outcome=inconclusive",
        runs_dir=runs_dir,
        bundles_dir=bundles_dir,
    )
    payload = json.loads(body)

    assert status == 200
    assert [row["run_id"] for row in payload["recent_runs"]] == [inconclusive_id]
    html = dashboard.render_route_html(
        "/operate/runs?scope=external&outcome=inconclusive"
    )
    assert inconclusive_id in html
    assert failed_id not in html


def test_runs_html_renders_true_total_running_state_and_next_page(tmp_path, monkeypatch):
    runs_dir = tmp_path / "runs"
    state_dir = tmp_path / "state"
    ledger = RunLedger(state_dir)
    ids = [_entry(ledger, index) for index in range(205)]
    ledger.transition(ids[-1], "running", started_at=_timestamp(300))
    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(runs_dir))
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(state_dir))
    monkeypatch.setenv("ADA2_DWARF_BUNDLES_DIR", str(tmp_path / "bundles"))

    body = dashboard.render_route_html("/operate/runs")

    assert "205 in this history" in body
    assert "Next 100 runs" in body
    assert ids[-1] in body
    assert "running" in body
    assert "Operations (0)" in body


def test_runs_html_keeps_operational_history_explicit(tmp_path, monkeypatch):
    runs_dir = tmp_path / "runs"
    state_dir = tmp_path / "state"
    ledger = RunLedger(state_dir)
    scenario_id = _entry(ledger, 1, kind="scenario")
    operation_id = _entry(ledger, 2, kind="deployment")
    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(runs_dir))
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(state_dir))
    monkeypatch.setenv("ADA2_DWARF_BUNDLES_DIR", str(tmp_path / "bundles"))

    default_body = dashboard.render_route_html("/operate/runs")
    operations_body = dashboard.render_route_html("/operate/runs?scope=operations")

    assert scenario_id in default_body
    assert operation_id not in default_body
    assert operation_id in operations_body
    assert scenario_id not in operations_body
    assert "deployment" in operations_body
