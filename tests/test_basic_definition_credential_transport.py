from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

import pytest

from profile_manager import dashboard
from profile_manager.data import operate_profiles_new
from profile_manager.data.operate_scenarios_new import list_templates_with_preview
from profile_manager.profile_templates import list_templates as list_profile_templates
from profile_manager.templating import render
from profile_manager.views.operate_primitives_new import render_operate_primitives_new
from profile_manager.views.operate_scenarios_new import render_operate_scenarios_new
from profile_manager.views.operate_targets import render_operate_targets
from profile_manager.views.scenarios import render_operate_scenarios


ROOT = Path(__file__).resolve().parents[1]
TOKEN = "body-token"


@pytest.fixture
def isolated_catalog_roots(monkeypatch, tmp_path):
    roots = {
        "scenarios": tmp_path / "scenarios",
        "profiles": tmp_path / "profiles",
        "manifests": tmp_path / "manifests",
        "runs": tmp_path / "runs",
        "state": tmp_path / "state",
        "bundles": tmp_path / "bundles",
    }
    monkeypatch.setenv("ADA2_DWARF_SCENARIOS_DIR", str(roots["scenarios"]))
    monkeypatch.setenv("ADA2_DWARF_PROFILES_DIR", str(roots["profiles"]))
    monkeypatch.setenv("ADA2_DWARF_MANIFESTS_DIR", str(roots["manifests"]))
    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(roots["runs"]))
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(roots["state"]))
    monkeypatch.setenv("ADA2_DWARF_BUNDLES_DIR", str(roots["bundles"]))
    monkeypatch.setattr(operate_profiles_new, "PROFILE_ROOT", roots["profiles"])
    return roots


@pytest.fixture
def dashboard_url(isolated_catalog_roots):
    handler = dashboard.serve_dashboard_handler_factory(TOKEN)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


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


def test_new_definition_links_and_forms_keep_token_out_of_user_facing_urls():
    rendered = (
        (render_operate_scenarios_new(token=TOKEN), "/operate/scenarios/new"),
        (
            render(
                "operate/targets_new.j2",
                token=TOKEN,
                manifests_dir="/tmp/manifests",
                implementations=[],
                languages=[],
                input_formats=[],
                decoder_types=[],
            ),
            "/operate/targets/new",
        ),
        (
            render(
                "operate/profiles_new.j2",
                token=TOKEN,
                profile_root="/tmp/profiles",
                templates=["mixed-minimal"],
            ),
            "/operate/profiles/new",
        ),
        (render_operate_primitives_new(token=TOKEN), "/operate/primitives/new"),
    )

    for html, action in rendered:
        assert "?token=" not in html
        assert f'action="{action}"' in html
        assert f'type="hidden" name="token" value="{TOKEN}"' in html

    targets = render_operate_targets(token=TOKEN)
    assert "?token=" not in targets
    assert 'href="/operate/targets/new"' in targets

    scenario_route = dashboard.render_route_html(
        "/operate/scenarios", token=TOKEN
    )
    assert scenario_route is not None
    assert "?token=" not in scenario_route
    assert f'id="paste-scenario-token" value="{TOKEN}"' in scenario_route

    for route in (
        "/operate/targets",
        "/operate/scenarios/new",
        "/operate/targets/new",
        "/operate/profiles/new",
    ):
        route_html = dashboard.render_route_html(route, token=TOKEN)
        assert route_html is not None
        assert "?token=" not in route_html
        if route.endswith("/new"):
            assert f'data-token="{TOKEN}"' in route_html


def _valid_definition_cases(roots):
    scenario_template = list_templates_with_preview()[0]["slug"]
    profile_template = list_profile_templates()[0]
    return (
        (
            "/operate/scenarios/new",
            {"template": scenario_template, "name": "credential-scenario"},
            "/operate/scenarios/credential-scenario",
            roots["scenarios"] / "credential-scenario.yaml",
        ),
        (
            "/operate/targets/new",
            {
                "id": "credential-target",
                "binary": "bin/credential-target",
                "implementation": "amaru",
                "language": "rust",
                "input_format": "stdin_bytes",
                "decoder_type": "CBOR codec",
            },
            "/operate/targets/credential-target",
            roots["manifests"] / "credential-target.yaml",
        ),
        (
            "/operate/profiles/new",
            {"template": profile_template, "name": "credential-profile"},
            "/operate/profiles/credential-profile",
            roots["profiles"] / "credential-profile" / "profile.yaml",
        ),
        (
            "/operate/primitives/new",
            {"family": "load", "name": "credential_primitive"},
            "/operate/primitives",
            roots["state"]
            / "primitive-scaffold"
            / "credential_primitive"
            / "primitives"
            / "registry.json",
        ),
    )


def test_new_definition_dispatchers_persist_and_redirect_with_body_token(
    dashboard_url, isolated_catalog_roots
):
    for path, fields, location, persisted in _valid_definition_cases(
        isolated_catalog_roots
    ):
        status, actual_location, _body = _post(
            dashboard_url, path, {"token": TOKEN, **fields}
        )
        assert (status, actual_location) == (303, location)
        assert persisted.is_file()
        if "primitives" not in path:
            readback = dashboard.render_route_html(location, token=TOKEN)
            assert readback is not None
            assert (fields.get("name") or fields.get("id")) in readback


@pytest.mark.parametrize(
    "submitted",
    ({"token": "wrong-token"}, {"token": ""}),
)
def test_new_definition_dispatchers_do_not_fallback_from_supplied_body_token(
    dashboard_url, submitted
):
    for path in (
        "/operate/scenarios/new",
        "/operate/targets/new",
        "/operate/profiles/new",
        "/operate/primitives/new",
    ):
        status, location, _body = _post(
            dashboard_url, f"{path}?token={TOKEN}", submitted
        )
        assert (status, location) == (403, None)


def test_new_definition_dispatchers_preserve_legacy_query_clients(
    dashboard_url, isolated_catalog_roots
):
    for path, fields, location, _persisted in _valid_definition_cases(
        isolated_catalog_roots
    ):
        legacy_fields = dict(fields)
        if "name" in legacy_fields:
            prefix = "legacy_" if path == "/operate/primitives/new" else "legacy-"
            legacy_fields["name"] = f"{prefix}{legacy_fields['name']}"
            location = location.replace(
                fields["name"], legacy_fields["name"]
            ) if fields["name"] in location else location
        elif "id" in legacy_fields:
            legacy_fields["id"] = f"legacy-{legacy_fields['id']}"
            location = location.replace(fields["id"], legacy_fields["id"])
        status, actual_location, _body = _post(
            dashboard_url, f"{path}?token={TOKEN}", legacy_fields
        )
        assert (status, actual_location) == (303, location)


def test_scenario_paste_uses_clean_endpoint_and_body_token_transport():
    html = render_operate_scenarios(token=TOKEN)
    script = (ROOT / "dwarf/dashboard/static/js/paste-scenario.js").read_text(
        encoding="utf-8"
    )

    assert "?token=" not in html
    assert 'data-endpoint="/api/scenario/paste"' in html
    assert f'type="hidden" id="paste-scenario-token" value="{TOKEN}"' in html
    assert "new URLSearchParams" in script
    assert "scenario_yaml" in script


def test_scenario_paste_dispatch_round_trips_wrapped_and_legacy_yaml(
    isolated_catalog_roots,
):
    source_path = ROOT / "dwarf/scenarios/runtime-bundle-timeline-example-smoke.yaml"
    source = json.loads(source_path.read_text(encoding="utf-8"))
    source["id"] = "credential-paste-scenario"
    source["title"] = "Credential paste scenario"
    scenario_bytes = (json.dumps(source, indent=2) + "\n").encode("utf-8")
    wrapped = urlencode(
        {"token": TOKEN, "scenario_yaml": scenario_bytes.decode("utf-8")}
    ).encode("utf-8")
    accepted = dashboard.dispatch_scenario_request(
        method="POST",
        path="/api/scenario/paste",
        body=wrapped,
        expected_token=TOKEN,
        scenarios_dir=isolated_catalog_roots["scenarios"],
    )
    assert accepted is not None and accepted[0] == 200
    wrapped_path = (
        isolated_catalog_roots["scenarios"]
        / "pending"
        / "credential-paste-scenario.yaml"
    )
    assert wrapped_path.read_bytes() == scenario_bytes
    assert json.loads(wrapped_path.read_text(encoding="utf-8")) == source

    rejected = dashboard.dispatch_scenario_request(
        method="POST",
        path=f"/api/scenario/paste?token={TOKEN}",
        body=urlencode(
            {"token": "wrong-token", "scenario_yaml": scenario_bytes.decode("utf-8")}
        ).encode("utf-8"),
        expected_token=TOKEN,
        scenarios_dir=isolated_catalog_roots["scenarios"],
    )
    assert rejected is not None and rejected[0] == 401

    blank_rejected = dashboard.dispatch_scenario_request(
        method="POST",
        path=f"/api/scenario/paste?token={TOKEN}",
        body=urlencode(
            {"token": "", "scenario_yaml": scenario_bytes.decode("utf-8")}
        ).encode("utf-8"),
        expected_token=TOKEN,
        scenarios_dir=isolated_catalog_roots["scenarios"],
    )
    assert blank_rejected is not None and blank_rejected[0] == 401

    legacy_source = dict(source)
    legacy_source["id"] = "credential-legacy-paste-scenario"
    legacy_bytes = (json.dumps(legacy_source, indent=2) + "\n").encode("utf-8")
    legacy = dashboard.dispatch_scenario_request(
        method="POST",
        path=f"/api/scenario/paste?token={TOKEN}",
        body=legacy_bytes,
        expected_token=TOKEN,
        scenarios_dir=isolated_catalog_roots["scenarios"],
    )
    assert legacy is not None and legacy[0] == 200
    legacy_path = (
        isolated_catalog_roots["scenarios"]
        / "pending"
        / "credential-legacy-paste-scenario.yaml"
    )
    assert legacy_path.read_bytes() == legacy_bytes
    assert json.loads(legacy_path.read_text(encoding="utf-8")) == legacy_source
