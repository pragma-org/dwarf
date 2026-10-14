import json
from pathlib import Path
from urllib.parse import urlencode

from profile_manager import dashboard


def _dispatch(path: str, body: dict[str, str], *, expected: str = "test-token"):
    return dashboard.dispatch_schedule_request(
        method="POST",
        path=path,
        body=urlencode(body).encode("utf-8"),
        expected_token=expected,
    )


def test_schedule_create_accepts_token_in_post_body(monkeypatch):
    created = {
        "id": "entry-1",
        "name": "UI audit",
        "scenario_id": "scenario-1",
        "scenario_path": "/tmp/scenario-1.yaml",
        "cron": "0 0 31 12 *",
        "status": "pending",
        "enabled": True,
    }
    monkeypatch.setattr(
        "profile_manager.data.schedule_store.create_entry",
        lambda **_kwargs: created,
    )

    status, content_type, payload = _dispatch(
        "/api/schedule/create",
        {
            "token": "test-token",
            "name": "UI audit",
            "scenario_id": "scenario-1",
            "scenario_path": "/tmp/scenario-1.yaml",
            "cron": "0 0 31 12 *",
        },
    )

    assert status == 200
    assert content_type == "application/json; charset=utf-8"
    assert json.loads(payload)["entry"] == created


def test_schedule_action_accepts_body_token_and_rejects_wrong_token(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        "profile_manager.data.schedule_store.pause_entry",
        lambda entry_id: calls.append(entry_id) or {"id": entry_id, "status": "paused"},
    )

    denied = _dispatch(
        "/api/schedule/entry-1/pause",
        {"token": "wrong-token"},
    )
    accepted = _dispatch(
        "/api/schedule/entry-1/pause",
        {"token": "test-token"},
    )

    assert denied[0] == 401
    assert accepted[0] == 200
    assert calls == ["entry-1"]


def test_schedule_auth_rejects_missing_blank_and_bad_body_without_query_fallback():
    missing = dashboard.dispatch_schedule_request(
        method="POST",
        path="/api/schedule/entry-1/pause",
        body=b"",
        expected_token="test-token",
    )
    blank = _dispatch(
        "/api/schedule/entry-1/pause",
        {"token": ""},
    )
    conflicting = _dispatch(
        "/api/schedule/entry-1/pause?token=test-token",
        {"token": "wrong-token"},
    )

    assert missing[0] == 401
    assert blank[0] == 401
    assert conflicting[0] == 401


def test_schedule_basic_ui_posts_credentials_in_body_not_url():
    template = (
        Path(__file__).parents[1]
        / "dwarf"
        / "dashboard"
        / "templates"
        / "operate"
        / "schedule.j2"
    ).read_text(encoding="utf-8")

    assert '<form id="schedule-create-form" class="schedule-form" method="post" action="/api/schedule/create">' in template
    assert "body.set('token', token);" in template
    assert "new URLSearchParams({token: token})" in template
    assert "?token=" not in template


def test_shared_layout_fallback_is_basic_and_dead_native_route_metadata_is_gone():
    root = Path(__file__).parents[1]
    base = (root / "dwarf/dashboard/templates/_base.j2").read_text(
        encoding="utf-8"
    )
    dashboard_source = (root / "dwarf/profile_manager/dashboard.py").read_text(
        encoding="utf-8"
    )

    assert "data-ui=\"{{ ui|default('basic') }}\"" in base
    assert "BASIC_NATIVE_EXACT" not in dashboard_source
    assert "BASIC_NATIVE_PREFIXES" not in dashboard_source
