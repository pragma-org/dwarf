import gzip
import importlib
import io
import json
import tarfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "dwarf/profiles/templates"


def _data_module():
    return importlib.import_module("profile_manager.data.operate_profile_templates")


def _view_module():
    return importlib.import_module("profile_manager.views.operate_profile_templates")


def _learn_view_module():
    return importlib.import_module("profile_manager.views.learn_profile_templates")


def _tree_state(root: Path) -> list[tuple[str, bytes]]:
    return [(str(path.relative_to(root)), path.read_bytes()) for path in sorted(root.rglob("*")) if path.is_file()]


def test_profile_template_catalog_lists_every_shipped_template():
    module = _data_module()
    expected = {path.stem for path in TEMPLATES.glob("*.yaml")}

    rows = module.profile_template_catalog_rows(templates_dir=TEMPLATES)

    assert {row["id"] for row in rows} == expected
    assert len(rows) == 10
    assert all(row["status"] == "valid" for row in rows)
    assert {
        row["rendered_data"].get("version_policy") for row in rows
    } == {"latest-confirmed"}


def test_profile_template_detail_extracts_substitutions_node_mix_and_assumptions():
    module = _data_module()

    detail = module.profile_template_detail("mixed-minimal", templates_dir=TEMPLATES)

    assert detail is not None
    assert set(detail["required_substitutions"]) == {
        "PROFILE_ID",
        "PROFILE_LABEL",
        "REMOTE_RUNTIME_ROOT",
        "COMPOSE_PROJECT",
    }
    assert detail["node_mix"] == {"cardano-node": 1, "amaru": 1}
    assert detail["node_type"] == "mixed"
    assert detail["network_magic"] == 42
    assert detail["peer_sharing"] is False
    assert detail["rendered_data"]["id"] == "preview-mixed-minimal"
    assert "{{" not in detail["rendered_source"]
    assert detail["validation_error"] is None


def test_template_preview_is_read_only_and_legacy_renderer_still_writes(tmp_path):
    templates = importlib.import_module("profile_manager.profile_templates")
    before = _tree_state(TEMPLATES)

    preview = templates.render_template_source(
        template_name="generated-mixed",
        profile_name="preview-generated-mixed",
        templates_dir=TEMPLATES,
    )

    assert _tree_state(TEMPLATES) == before
    assert '"id": "preview-generated-mixed"' in preview
    output = tmp_path / "profile.yaml"
    templates.render_template(
        template_name="generated-mixed",
        profile_name="written-profile",
        output_path=output,
        templates_dir=TEMPLATES,
    )
    assert json.loads(output.read_text(encoding="utf-8"))["id"] == "written-profile"


def test_unknown_and_traversing_template_names_are_rejected(tmp_path):
    templates = importlib.import_module("profile_manager.profile_templates")

    for name in ("missing", "../registry", "a/b", "._hidden"):
        try:
            templates.render_template_source(
                template_name=name,
                profile_name="safe-profile",
                templates_dir=TEMPLATES,
            )
        except FileNotFoundError:
            pass
        else:
            raise AssertionError(f"unsafe or unknown template was accepted: {name}")


def test_invalid_template_is_listed_with_diagnostics(tmp_path):
    module = _data_module()
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "broken.yaml").write_text(
        '{"id":"{{PROFILE_ID}}","label":"{{PROFILE_LABEL}}","peer_sharing":"not-bool"}\n',
        encoding="utf-8",
    )

    rows = module.profile_template_catalog_rows(templates_dir=templates)

    assert len(rows) == 1
    assert rows[0]["id"] == "broken"
    assert rows[0]["status"] == "invalid"
    assert rows[0]["validation_error"]
    assert rows[0]["node_type"] == "invalid"
    assert rows[0]["assumptions"] == []


def test_profile_template_discovery_ignores_apple_and_hidden_noise(tmp_path):
    templates = importlib.import_module("profile_manager.profile_templates")
    root = tmp_path / "templates"
    root.mkdir()
    (root / "valid-template.yaml").write_text("{}\n", encoding="utf-8")
    (root / "._valid-template.yaml").write_text("noise\n", encoding="utf-8")
    (root / ".hidden.yaml").write_text("noise\n", encoding="utf-8")

    assert templates.list_templates(templates_dir=root) == ["valid-template"]


def test_profile_template_routes_render_read_only_detail_and_builder_link():
    views = _view_module()
    learn_views = _learn_view_module()

    index = views.render_operate_profile_templates()
    detail = views.render_operate_profile_template_detail("mixed-minimal")
    learn = learn_views.render_learn_profile_templates()

    assert '<h1>Profile templates</h1>' in index
    assert 'aria-label="Search profile templates"' in index
    assert 'href="/api/assets/profile-templates/export"' in index
    assert 'href="/operate/profile-templates/mixed-minimal"' in index
    assert '<h1>mixed-minimal</h1>' in detail
    assert 'href="/operate/profiles/new?template=mixed-minimal"' in detail
    assert "Rendered preview" in detail
    assert "Required substitutions" in detail
    assert "Save template" not in detail
    assert '<h1>Profile-template reference</h1>' in learn
    assert "template is not a deployed profile" in learn.lower()
    assert "read-only" in learn.lower()
    assert "use template" in learn.lower()


def test_dashboard_dispatches_profile_template_routes_and_builder_preselection():
    dashboard = importlib.import_module("profile_manager.dashboard")

    assert "10 templates" in dashboard.render_route_html("/operate/profile-templates")
    assert "preview-mixed-minimal" in dashboard.render_route_html(
        "/operate/profile-templates/mixed-minimal"
    )
    assert "/api/assets/profile-templates/mixed-minimal/export" in dashboard.render_route_html(
        "/operate/profile-templates/mixed-minimal"
    )
    builder = dashboard.render_route_html(
        "/operate/profiles/new?template=mixed-minimal"
    )
    assert '<option value="mixed-minimal" selected>' in builder
    assert "/opt/dwarf/profiles/new-profile" in builder
    assert "/opt/dwarf/cardano-profiles/new-profile" not in builder
    assert dashboard.render_route_html("/operate/profile-templates/not-found") is None
    assert dashboard.render_route_html("/operate/profile-templates/%2e%2e") is None
    assert "Profile-template reference" in dashboard.render_route_html(
        "/learn/profile-templates"
    )


def test_live_http_profile_builder_preserves_template_query_selection():
    dashboard = importlib.import_module("profile_manager.dashboard")
    handler = dashboard.serve_dashboard_handler_factory("dwarf")
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        with urlopen(
            f"http://127.0.0.1:{port}/operate/profiles/new?template=mixed-minimal"
        ) as response:
            html = response.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert '<option value="mixed-minimal" selected>' in html
    assert "/opt/dwarf/profiles/new-profile" in html
    assert "/opt/dwarf/cardano-profiles/new-profile" not in html


def test_profile_template_archive_and_source_download_are_exact_and_clean():
    dashboard = importlib.import_module("profile_manager.dashboard")

    first = dashboard.dispatch_api_request("/api/assets/profile-templates/export")
    second = dashboard.dispatch_api_request("/api/assets/profile-templates/export")
    source = dashboard.dispatch_api_request(
        "/api/assets/profile-templates/mixed-minimal/download"
    )

    assert first[0:2] == (200, "application/gzip")
    assert first[2] == second[2]
    with gzip.GzipFile(fileobj=io.BytesIO(first[2]), mode="rb") as zipped:
        with tarfile.open(fileobj=zipped, mode="r:") as archive:
            names = archive.getnames()
        assert len(names) == 11
    assert "DWARF-EXPORT-MANIFEST.json" in names
    assert names == sorted(names)
    assert not any("._" in name or "__pycache__" in name for name in names)
    assert source[0:2] == (200, "application/yaml; charset=utf-8")
    assert source[2] == (TEMPLATES / "mixed-minimal.yaml").read_bytes()
    assert source[3]["Content-Disposition"].endswith('"mixed-minimal.yaml"')


def test_profile_templates_do_not_invent_relationships_from_scaffold_names():
    module = importlib.import_module("profile_manager.data.operate_profile_templates")

    assert all(row["relationships"] == [] for row in module.profile_template_catalog_rows())


def test_navigation_landings_and_api_reference_include_profile_templates():
    dashboard = importlib.import_module("profile_manager.dashboard")
    learn_api = importlib.import_module("profile_manager.data.learn_api")
    nav = importlib.import_module("profile_manager.data.sub_nav")
    routes = {
        route
        for group in learn_api.html_route_groups()
        for route in group["routes"]
    }

    assert {"/operate/profile-templates", "/operate/profile-templates/<id>"} <= routes
    assert "/learn/profile-templates" in routes
    assert any(item["url"] == "/operate/profile-templates" for item in nav.OPERATE_SUB_NAV)
    assert any(item["url"] == "/learn/profile-templates" for item in nav.LEARN_SUB_NAV)
    assert 'href="/operate/profile-templates"' in dashboard.render_route_html("/operate")
    assert 'href="/learn/profile-templates"' in dashboard.render_route_html("/learn")
