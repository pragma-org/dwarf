from __future__ import annotations

import socket
import threading
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

from profile_manager import dashboard
from profile_manager.templating import render, set_current_view
from profile_manager.views.operate_scenarios_new import render_operate_scenarios_new


@pytest.fixture
def dashboard_url():
    handler = dashboard.serve_dashboard_handler_factory("view-test-token")
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _get(base_url: str, path: str, *, cookie: str | None = None):
    headers = {"Cookie": cookie} if cookie else {}
    with urlopen(Request(base_url + path, headers=headers)) as response:
        return response.read().decode("utf-8"), response.headers


def _post(base_url: str, path: str):
    request = Request(base_url + path, data=b"", method="POST")
    try:
        with urlopen(request) as response:
            return response.status, response.read().decode("utf-8")
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def _multipart_token(value: str | None):
    boundary = "dwarf-test-boundary"
    parts = []
    if value is not None:
        parts.extend([
            f"--{boundary}\r\n",
            'Content-Disposition: form-data; name="token"\r\n\r\n',
            value,
            "\r\n",
        ])
    parts.append(f"--{boundary}--\r\n")
    return (
        f"multipart/form-data; boundary={boundary}",
        "".join(parts).encode("utf-8"),
    )


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _redirect(base_url: str, path: str):
    try:
        build_opener(_NoRedirect).open(base_url + path)
    except HTTPError as exc:
        return exc.code, exc.headers.get("Location")
    raise AssertionError(f"{path} did not redirect")


def test_no_view_preference_defaults_to_basic(dashboard_url):
    body, headers = _get(dashboard_url, "/operate/runs")

    assert 'data-ui="basic"' in body
    assert headers.get("Set-Cookie") is None


def test_access_log_redacts_query_values(dashboard_url, capsys):
    _post(dashboard_url, "/api/run/prepare?token=access-log-secret&view=basic")

    access_log = capsys.readouterr().out
    assert "/api/run/prepare?[query-redacted]" in access_log
    assert "access-log-secret" not in access_log
    assert "view=basic" not in access_log


@pytest.mark.parametrize(
    "request_line",
    [
        b"GET\t/?token=raw-tab-secret\tHTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
        b"GET /?token=raw-http09-secret\r\n",
    ],
)
def test_access_log_redacts_queries_from_accepted_raw_request_lines(
    dashboard_url, capsys, request_line
):
    endpoint = urlsplit(dashboard_url)
    with socket.create_connection((endpoint.hostname, endpoint.port), timeout=2) as client:
        client.sendall(request_line)
        while client.recv(8192):
            pass

    access_log = capsys.readouterr().out
    assert "/?[query-redacted]" in access_log
    assert "raw-tab-secret" not in access_log
    assert "raw-http09-secret" not in access_log


def test_explicit_advanced_selects_bento_without_persisting_cookie(dashboard_url):
    body, headers = _get(dashboard_url, "/operate/runs?view=advanced")

    assert 'data-ui="bento"' in body
    assert headers.get("Set-Cookie") is None

    next_body, _ = _get(dashboard_url, "/operate/runs")
    assert 'data-ui="basic"' in next_body


def test_stored_bento_cookie_does_not_change_basic_default(dashboard_url):
    body, _headers = _get(
        dashboard_url, "/operate/runs", cookie="dwarf_view=bento"
    )

    assert 'data-ui="basic"' in body


def test_explicit_basic_ignores_bento_cookie_without_replacing_it(dashboard_url):
    body, headers = _get(
        dashboard_url,
        "/operate/runs?view=basic",
        cookie="dwarf_view=bento",
    )

    assert 'data-ui="basic"' in body
    assert headers.get("Set-Cookie") is None


@pytest.mark.parametrize("view", ["", "unknown", "bento"])
def test_only_explicit_advanced_can_enable_advanced(dashboard_url, view):
    body, _ = _get(dashboard_url, f"/operate/runs?view={view}", cookie="dwarf_view=bento")
    assert 'data-ui="basic"' in body


@pytest.mark.parametrize("path", ["/", "/run", "/operate", "/operate/runs", "/operate/measurements", "/learn", "/learn/glossary"])
@pytest.mark.parametrize("query", ["", "?view=basic", "?view=advanced"])
def test_routes_have_no_view_toggle_and_default_to_basic(dashboard_url, path, query):
    body, _ = _get(dashboard_url, path + query, cookie="dwarf_view=bento")
    expected = "bento" if query == "?view=advanced" else "basic"
    assert f'data-ui="{expected}"' in body
    assert 'class="view-toggle"' not in body
    assert 'aria-label="View density"' not in body
    assert '?view=advanced"' not in body


def test_direct_render_without_request_context_defaults_to_basic():
    set_current_view(None)
    try:
        body = render(
            "operate/run_not_found.j2",
            page_title="Run not found",
            run_id="missing-run",
        )
    finally:
        set_current_view(None)

    assert 'data-ui="basic"' in body
    assert "Possible reasons" in body


def test_basic_keeps_backup_and_complete_run_evidence_out_of_advanced_only_css():
    root = Path(__file__).resolve().parents[1] / "dwarf" / "dashboard" / "templates" / "operate"
    status = (root / "status.j2").read_text(encoding="utf-8")
    run = (root / "run.j2").read_text(encoding="utf-8")

    assert '<section class="maintenance"' in status
    assert '<section class="maintenance adv-only"' not in status
    for section in (
        "manifest", "version-provenance", "execution-definition", "chain",
        "provenance", "export", "actions", "assertions", "probes", "log-tail",
    ):
        opening = run.split(f'data-run-section="{section}"', 1)[0].rsplit("<details", 1)[-1]
        assert "adv-only" not in opening, section
    assert "?view=advanced" not in run


def test_basic_versions_exposes_source_specific_refresh_controls(dashboard_url):
    body, _ = _get(dashboard_url, "/operate/versions")

    assert 'data-ui="basic"' in body
    assert body.count('<button class="dk-btn" type="button" data-version-refresh') == 2
    assert 'data-version-refresh data-implementation="amaru"' in body
    assert 'data-version-refresh data-implementation="cardano-node"' in body
    assert 'data-endpoint="/api/versions/refresh"' in body
    assert 'data-version-refresh-token value="view-test-token"' in body


def test_versions_get_never_schedules_release_discovery(dashboard_url, monkeypatch):
    calls = []

    def unexpected(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("GET must not schedule upstream release discovery")

    monkeypatch.setattr(
        "profile_manager.version_discovery.ensure_release_refresh", unexpected
    )

    first, _ = _get(dashboard_url, "/operate/versions")
    second, _ = _get(dashboard_url, "/operate/versions")
    run, _ = _get(dashboard_url, "/run")

    assert "Node Versions" in first
    assert "Node Versions" in second
    assert 'id="run-wizard"' in run
    assert calls == []


@pytest.mark.parametrize(
    ("path", "expected_status"),
    [
        ("/api/bundle/import", 401),
        ("/operate/scenarios/new", 403),
    ],
)
def test_legacy_mutating_routes_reject_missing_token(
    dashboard_url, path, expected_status
):
    status, body = _post(dashboard_url, path)

    assert status == expected_status
    assert "token" in body.lower()


def test_basic_bundle_import_form_carries_the_mutation_token(dashboard_url):
    body, _ = _get(dashboard_url, "/operate/bundles")

    assert 'data-ui="basic"' in body
    assert 'action="/api/bundle/import"' in body
    assert 'name="token" value="view-test-token"' in body
    assert "/api/bundle/import?token=" not in body


@pytest.mark.parametrize(
    ("body_token", "query_token", "expected"),
    [
        ("expected", None, True),
        ("", "expected", False),
        ("wrong", "expected", False),
        ("\u00e9", "expected", False),
        (None, "expected", True),
        (None, None, False),
    ],
)
def test_bundle_import_multipart_token_precedence(body_token, query_token, expected):
    content_type, body = _multipart_token(body_token)
    path = "/api/bundle/import"
    if query_token is not None:
        path += f"?token={query_token}"

    ok, _ = dashboard.check_multipart_form_token(
        path, content_type, body, expected="expected"
    )

    assert ok is expected


def test_bundle_import_route_authenticates_multipart_body_before_import(dashboard_url):
    content_type, body = _multipart_token("view-test-token")
    request = Request(
        dashboard_url + "/api/bundle/import",
        data=body,
        headers={"Content-Type": content_type},
        method="POST",
    )
    with pytest.raises(HTTPError) as exc_info:
        urlopen(request)
    assert exc_info.value.code == 400
    assert "missing required upload field" in exc_info.value.read().decode("utf-8")

    wrong_type, wrong_body = _multipart_token("wrong")
    wrong = Request(
        dashboard_url + "/api/bundle/import?token=view-test-token",
        data=wrong_body,
        headers={"Content-Type": wrong_type},
        method="POST",
    )
    with pytest.raises(HTTPError) as wrong_exc:
        urlopen(wrong)
    assert wrong_exc.value.code == 401

    non_ascii_type, non_ascii_body = _multipart_token("\u00e9")
    non_ascii = Request(
        dashboard_url + "/api/bundle/import?token=view-test-token",
        data=non_ascii_body,
        headers={"Content-Type": non_ascii_type},
        method="POST",
    )
    with pytest.raises(HTTPError) as non_ascii_exc:
        urlopen(non_ascii)
    assert non_ascii_exc.value.code == 401


def test_bundle_import_route_rejects_non_ascii_boundary_cleanly(dashboard_url):
    request = Request(
        dashboard_url + "/api/bundle/import?token=view-test-token",
        data=b"not a multipart body",
        headers={"Content-Type": 'multipart/form-data; boundary="\u00e9"'},
        method="POST",
    )
    with pytest.raises(HTTPError) as exc_info:
        urlopen(request)
    assert exc_info.value.code == 400
    assert "Missing multipart boundary" in exc_info.value.read().decode("utf-8")


def test_legacy_scenario_create_form_uses_clean_action_and_body_token():
    body = render_operate_scenarios_new(token="token with + & /?")

    assert '?token=' not in body
    assert 'action="/operate/scenarios/new"' in body
    assert 'name="token" value="token with + &amp; /?"' in body


@pytest.mark.parametrize(
    ("legacy", "canonical"),
    [("/tests", "/run"), ("/scenarios", "/operate/scenarios")],
)
def test_legacy_run_and_scenario_pages_redirect_to_basic_canonical_routes(
    dashboard_url, legacy, canonical
):
    assert _redirect(dashboard_url, legacy) == (302, canonical)


def test_basic_scenario_catalog_hands_runs_to_canonical_wizard_and_exposes_staging(
    dashboard_url,
):
    body, _ = _get(dashboard_url, "/operate/scenarios")

    assert '<form class="run-scenario" method="get" action="/run"' in body
    assert 'name="scenario"' in body
    assert "/api/scenario/run" not in body
    assert '?token=' not in body
    assert 'data-endpoint="/api/scenario/paste"' in body
    assert 'id="paste-scenario-token" value="view-test-token"' in body
    paste_opening = body.split('id="paste-scenario"', 1)[0].rsplit("<details", 1)[-1]
    assert "adv-only" not in paste_opening
