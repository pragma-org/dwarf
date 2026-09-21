import gzip
import importlib
import io
import json
import tarfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "dwarf/primitives/registry.json"
SCENARIOS = ROOT / "dwarf/scenarios"
FAMILIES = {"setup", "load", "probe", "assertion", "fault", "teardown"}


def _data_module():
    return importlib.import_module("profile_manager.data.operate_primitives")


def _view_module():
    return importlib.import_module("profile_manager.views.operate_primitives")


def _learn_view_module():
    return importlib.import_module("profile_manager.views.learn_primitives")


def test_primitive_catalog_reconciles_to_full_registry_and_six_families():
    module = _data_module()
    expected = json.loads(REGISTRY.read_text(encoding="utf-8"))["primitives"]

    rows = module.primitive_catalog_rows(
        registry_path=REGISTRY,
        scenarios_dir=SCENARIOS,
        plugin_roots=[],
    )

    assert {row["id"] for row in rows} == set(expected)
    assert {row["family"] for row in rows} == FAMILIES
    assert all(row["source_type"] == "built-in" for row in rows)
    assert all(row["schema_status"] == "schema-backed" for row in rows)
    assert all(row["executor_status"] == "executor-present" for row in rows)


def test_registry_authoring_comment_describes_current_catalog_not_old_slice():
    payload = json.loads(REGISTRY.read_text(encoding="utf-8"))

    assert len(payload["primitives"]) == 238
    assert "map is empty" not in payload["$comment"]
    assert "Adding a primitive is a code change" in payload["$comment"]


def test_primitive_detail_exposes_exact_contract_and_reverse_scenario_links():
    module = _data_module()

    detail = module.primitive_detail(
        "cbor_fuzz_target",
        registry_path=REGISTRY,
        scenarios_dir=SCENARIOS,
        plugin_roots=[],
    )

    assert detail is not None
    assert detail["family"] == "load"
    assert detail["module"] == "profile_manager.primitives"
    assert detail["class_name"] == "CborFuzzTarget"
    assert detail["version"] == "0.1.0"
    assert detail["params_schema"] == "primitives/load/cbor_fuzz_target.schema.json"
    assert detail["schema_status"] == "schema-backed"
    assert detail["executor_status"] == "executor-present"
    assert {"library"} == set(detail["runtimes"])
    assert {"cardano-node", "amaru"} == set(detail["supports"])
    assert "amaru-cbor-tx-body-fuzz" in detail["referencing_scenarios"]
    assert all(
        relationship["catalog"] == "scenarios"
        and relationship["resolved"] is True
        and relationship["source_path"].endswith("#primitive=cbor_fuzz_target")
        for relationship in detail["relationships"]
    )
    assert detail["registry_source"].startswith("{")
    assert '"cbor_fuzz_target"' in detail["registry_source"]
    assert '"type"' in detail["schema_source"]


def test_every_builtin_primitive_has_a_source_backed_scenario_reference():
    module = _data_module()

    rows = module.primitive_catalog_rows(
        registry_path=REGISTRY,
        scenarios_dir=SCENARIOS,
        plugin_roots=[],
    )

    assert rows
    assert all(row["referencing_scenario_count"] > 0 for row in rows)


def test_static_plugin_metadata_is_catalogued_without_executing_entrypoint(tmp_path):
    module = _data_module()
    plugin_root = tmp_path / "plugins"
    example = plugin_root / "safe-example"
    example.mkdir(parents=True)
    marker = tmp_path / "entrypoint-was-executed"
    (example / "plugin.json").write_text(
        json.dumps(
            {
                "plugin_id": "safe-example",
                "dwarf_api_version": "v1",
                "entrypoint": "plugin.py",
                "registry": "registry.json",
            }
        ),
        encoding="utf-8",
    )
    (example / "plugin.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n",
        encoding="utf-8",
    )
    (example / "schema.json").write_text(
        json.dumps({"type": "object", "properties": {}}), encoding="utf-8"
    )
    (example / "registry.json").write_text(
        json.dumps(
            {
                "primitives": {
                    "plugin_safe_load": {
                        "class": "PluginSafeLoad",
                        "family": "load",
                        "module": "safe_plugin",
                        "params_schema": "schema.json",
                        "runtimes": ["devnet"],
                        "supports": ["amaru"],
                        "version": "1.2.3",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    rows = module.primitive_catalog_rows(
        registry_path=REGISTRY,
        scenarios_dir=SCENARIOS,
        plugin_roots=[plugin_root],
    )
    plugin = next(row for row in rows if row["id"] == "plugin_safe_load")

    assert not marker.exists()
    assert plugin["source_type"] == "plugin"
    assert plugin["plugin_id"] == "safe-example"
    assert plugin["schema_status"] == "schema-backed"
    assert plugin["executor_status"] == "declared-unloaded"
    assert plugin["referencing_scenario_count"] == 0


def test_runtime_plugin_loader_still_executes_entrypoint_after_safe_split(tmp_path):
    loader = importlib.import_module("profile_manager.plugin_loader")
    plugin_root = tmp_path / "plugins"
    example = plugin_root / "runtime-example"
    example.mkdir(parents=True)
    (example / "plugin.json").write_text(
        json.dumps(
            {
                "plugin_id": "runtime-example",
                "dwarf_api_version": "v1",
                "entrypoint": "plugin.py",
                "registry": "registry.json",
            }
        ),
        encoding="utf-8",
    )
    (example / "registry.json").write_text(
        json.dumps({"primitives": {"static_entry": {"family": "load"}}}),
        encoding="utf-8",
    )
    (example / "plugin.py").write_text(
        "def register(registry):\n"
        "    registry['entrypoint_entry'] = {'family': 'probe'}\n",
        encoding="utf-8",
    )

    manifests = loader.discover_plugin_manifests(plugin_roots=[plugin_root])
    static = loader.load_plugin_registry_entries(manifests[0])
    runtime = loader.load_plugin_entries(manifests)

    assert set(static) == {"static_entry"}
    assert set(runtime) == {"static_entry", "entrypoint_entry"}


def test_plugin_collision_is_reported_and_builtin_remains_authoritative(tmp_path):
    module = _data_module()
    plugin_root = tmp_path / "plugins"
    collision = plugin_root / "collision"
    collision.mkdir(parents=True)
    (collision / "plugin.json").write_text(
        json.dumps(
            {
                "plugin_id": "collision",
                "dwarf_api_version": "v1",
                "registry": "registry.json",
            }
        ),
        encoding="utf-8",
    )
    (collision / "registry.json").write_text(
        json.dumps(
            {
                "primitives": {
                    "cbor_fuzz_target": {
                        "class": "Collision",
                        "family": "fault",
                        "module": "collision",
                        "runtimes": ["devnet"],
                        "supports": ["amaru"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    payload = module.primitive_catalog_payload(
        registry_path=REGISTRY,
        scenarios_dir=SCENARIOS,
        plugin_roots=[plugin_root],
    )
    row = next(row for row in payload["rows"] if row["id"] == "cbor_fuzz_target")

    assert row["source_type"] == "built-in"
    assert row["family"] == "load"
    assert any("collision" in error.lower() for error in payload["errors"])


def test_missing_parameter_schema_is_visible_and_never_reported_valid(tmp_path):
    module = _data_module()
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "primitives": {
                    "missing_schema_example": {
                        "class": "CborFuzzTarget",
                        "family": "load",
                        "module": "profile_manager.primitives",
                        "params_schema": "primitives/load/does-not-exist.schema.json",
                        "runtimes": ["library"],
                        "supports": ["amaru"],
                        "version": "0.1.0",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    row = module.primitive_catalog_rows(
        registry_path=registry,
        scenarios_dir=tmp_path / "scenarios",
        plugin_roots=[],
    )[0]

    assert row["schema_status"] == "missing-schema"
    assert row["status"] == "invalid"
    assert row["errors"]


def test_plugin_registry_cannot_escape_its_configured_plugin_root(tmp_path):
    module = _data_module()
    plugin_root = tmp_path / "plugins"
    escaped_registry = tmp_path / "outside.json"
    escaped_registry.write_text(json.dumps({"primitives": {}}), encoding="utf-8")
    plugin = plugin_root / "escape"
    plugin.mkdir(parents=True)
    (plugin / "plugin.json").write_text(
        json.dumps(
            {
                "plugin_id": "escape",
                "dwarf_api_version": "v1",
                "registry": "../../outside.json",
            }
        ),
        encoding="utf-8",
    )

    payload = module.primitive_catalog_payload(
        registry_path=REGISTRY,
        scenarios_dir=SCENARIOS,
        plugin_roots=[plugin_root],
    )

    assert len(payload["rows"]) == 238
    assert any("escapes the plugin root" in error for error in payload["errors"])


def test_primitive_catalog_routes_render_filters_detail_schema_and_learn_reference():
    views = _view_module()
    learn_views = _learn_view_module()

    index = views.render_operate_primitives()
    detail = views.render_operate_primitive_detail("cbor_fuzz_target")
    learn = learn_views.render_learn_primitives()

    assert '<h1>Primitives</h1>' in index
    assert 'aria-label="Search primitives"' in index
    assert 'name="family"' in index
    assert 'name="runtime"' in index
    assert 'name="support"' in index
    assert 'data-label="Contract"' in index
    assert ".primitive-catalog-table tbody" in index
    assert 'href="/api/assets/primitives/export"' in index
    assert 'href="/operate/primitives/cbor_fuzz_target"' in index
    assert '<h1>cbor_fuzz_target</h1>' in detail
    assert "Parameter schema" in detail
    assert "Referencing scenarios" in detail
    assert 'href="/operate/scenarios/amaru-cbor-tx-body-fuzz"' in detail
    assert "Catalog validity is not runtime-execution proof" in detail
    assert '<h1>Primitive reference</h1>' in learn
    for family in FAMILIES:
        assert f">{family}<" in learn
    assert "built-in" in learn
    assert "plugin" in learn
    assert "implement" in learn.lower()
    assert "rebuild" in learn.lower()
    assert "runtime evidence" in learn.lower()


def test_dashboard_dispatches_primitive_index_detail_and_controlled_not_found():
    dashboard = importlib.import_module("profile_manager.dashboard")

    assert "Primitive catalog" in dashboard.render_route_html("/operate/primitives")
    assert "CborFuzzTarget" in dashboard.render_route_html(
        "/operate/primitives/cbor_fuzz_target"
    )
    assert "/api/assets/primitives/cbor_fuzz_target/export" in dashboard.render_route_html(
        "/operate/primitives/cbor_fuzz_target"
    )
    assert dashboard.render_route_html("/operate/primitives/not-registered") is None
    assert dashboard.render_route_html("/operate/primitives/%2e%2e") is None
    assert "Primitive reference" in dashboard.render_route_html("/learn/primitives")


def test_primitive_archive_is_deterministic_complete_and_noise_free():
    dashboard = importlib.import_module("profile_manager.dashboard")

    first = dashboard.dispatch_api_request("/api/assets/primitives/export")
    second = dashboard.dispatch_api_request("/api/assets/primitives/export")

    assert first[0:2] == (200, "application/gzip")
    assert first[2] == second[2]
    with gzip.GzipFile(fileobj=io.BytesIO(first[2]), mode="rb") as zipped:
        with tarfile.open(fileobj=zipped, mode="r:") as archive:
            names = archive.getnames()
        assert len(names) == 239
    assert "DWARF-EXPORT-MANIFEST.json" in names
    assert names == sorted(names)
    assert not any("._" in name or "__pycache__" in name for name in names)


def test_primitive_source_download_returns_one_exact_registry_record():
    dashboard = importlib.import_module("profile_manager.dashboard")

    response = dashboard.dispatch_api_request(
        "/api/assets/primitives/cbor_fuzz_target/download"
    )

    assert response[0:2] == (200, "application/json; charset=utf-8")
    payload = json.loads(response[2])
    assert set(payload) == {"cbor_fuzz_target"}
    assert payload["cbor_fuzz_target"]["class"] == "CborFuzzTarget"
    assert response[3]["Content-Disposition"].endswith(
        '"cbor_fuzz_target.json"'
    )


def test_navigation_landings_api_and_scaffold_lifecycle_are_reconciled():
    dashboard = importlib.import_module("profile_manager.dashboard")
    learn_api = importlib.import_module("profile_manager.data.learn_api")
    nav = importlib.import_module("profile_manager.data.sub_nav")
    operate_new = importlib.import_module(
        "profile_manager.views.operate_primitives_new"
    )

    operate_routes = {
        route
        for group in learn_api.html_route_groups()
        if group["label"] == "Operate"
        for route in group["routes"]
    }
    learn_routes = {
        route
        for group in learn_api.html_route_groups()
        if group["label"] == "Learn"
        for route in group["routes"]
    }
    operate = dashboard.render_route_html("/operate")
    learn = dashboard.render_route_html("/learn")
    scaffold = operate_new.render_operate_primitives_new(token="test")

    assert {"/operate/primitives", "/operate/primitives/<id>"} <= operate_routes
    assert "/learn/primitives" in learn_routes
    assert any(item["url"] == "/operate/primitives" for item in nav.OPERATE_SUB_NAV)
    assert any(item["url"] == "/learn/primitives" for item in nav.LEARN_SUB_NAV)
    assert 'href="/operate/primitives"' in operate
    assert "238" in operate
    assert 'href="/learn/primitives"' in learn
    assert 'href="/learn/primitives#authoring"' in scaffold
    assert "requires implementation" in scaffold.lower()
    assert "rebuild" in scaffold.lower()
    assert "Operate · Primitives · New primitive" in scaffold


def test_scaffold_result_returns_to_primitives_and_never_claims_live_registration(
    monkeypatch, tmp_path
):
    module = importlib.import_module("profile_manager.data.operate_primitives_new")
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(tmp_path))

    status, html = module.handle_create_post(b"family=load&name=example_catalog_primitive")

    assert status == 200
    assert 'href="/operate/primitives?token=dwarf"' in html
    assert "requires implementation" in html.lower()
    assert "rebuild" in html.lower()
    assert "now live" not in html.lower()
