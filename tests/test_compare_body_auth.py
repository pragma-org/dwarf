from pathlib import Path
from urllib.parse import urlencode

from profile_manager import dashboard


def _drain(response):
    assert response is not None
    status, content_type, body = response
    if status == 200:
        list(body)
    return status, content_type


def test_basic_compare_posts_private_body_token_to_query_free_endpoint():
    html = dashboard.render_route_html("/operate/compare", token="test-secret")
    script = Path("dwarf/dashboard/static/js/compare-runner.js").read_text(
        encoding="utf-8"
    )

    assert 'type="hidden"' in html
    assert 'data-compare-token' in html
    assert 'value="test-secret"' in html
    assert "/api/scenario/compare?token=" not in html
    assert 'fetch("/api/scenario/compare"' in script
    assert "new URLSearchParams" in script
    assert "/api/scenario/compare?token=" not in script


def test_compare_body_auth_precedes_legacy_query_auth(monkeypatch):
    monkeypatch.setattr(
        "profile_manager.scenario.compare_preflight",
        lambda _path: {
            "ready": True,
            "code": "ready",
            "message": "Compare requirements are available.",
            "requirement": None,
        },
    )
    built = []

    def builder(action, **kwargs):
        built.append((action, kwargs))
        return ["/usr/bin/true"]

    accepted = dashboard.dispatch_mutating_request(
        method="POST",
        path="/api/scenario/compare",
        body=urlencode(
            {"token": "secret", "path": "/catalog/scenario.yaml"}
        ).encode(),
        expected_token="secret",
        cli_command_builder=builder,
    )
    assert _drain(accepted) == (200, "text/event-stream; charset=utf-8")
    assert built == [
        (
            "compare",
            {
                "profile": None,
                "test_id": None,
                "approve": False,
                "scenario_path": "/catalog/scenario.yaml",
                "run_origin": "dashboard",
            },
        )
    ]

    rejected = dashboard.dispatch_mutating_request(
        method="POST",
        path=(
            "/api/scenario/compare?token=secret"
            "&path=%2Fcatalog%2Fquery-scenario.yaml"
        ),
        body=urlencode(
            {"token": "wrong", "path": "/catalog/body-scenario.yaml"}
        ).encode(),
        expected_token="secret",
        cli_command_builder=builder,
    )
    assert rejected is not None and rejected[0] == 401
    assert len(built) == 1

    legacy = dashboard.dispatch_mutating_request(
        method="POST",
        path=(
            "/api/scenario/compare?token=secret"
            "&path=%2Fcatalog%2Flegacy-scenario.yaml"
        ),
        body=b"",
        expected_token="secret",
        cli_command_builder=builder,
    )
    assert _drain(legacy) == (200, "text/event-stream; charset=utf-8")
    assert built[-1][1]["scenario_path"] == "/catalog/legacy-scenario.yaml"
