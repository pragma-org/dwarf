import importlib
import io
import json
import tarfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BUILTIN_REGISTRY = ROOT / "dwarf/primitives/registry.json"


def _write_plugin(root: Path, directory: str, manifest=None, files=None) -> Path:
    plugin = root / directory
    plugin.mkdir(parents=True)
    if manifest is not None:
        (plugin / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    for relative, content in (files or {}).items():
        path = plugin / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return plugin


def _primitive(name: str) -> dict:
    return {
        "primitives": {
            name: {
                "class": "CborFuzzTarget",
                "module": "profile_manager.primitives",
                "family": "load",
                "runtimes": ["library"],
                "supports": ["cardano-node", "amaru"],
                "version": "1.0.0",
            }
        }
    }


def _payload(plugin_roots):
    module = importlib.import_module("profile_manager.data.operate_plugins")
    return module.plugin_catalog_payload(
        plugin_roots=plugin_roots,
        builtin_registry_path=BUILTIN_REGISTRY,
    )


def test_safe_inventory_classifies_manifest_registry_entrypoint_and_mixed_plugins(tmp_path):
    root = tmp_path / "plugins"
    _write_plugin(
        root,
        "manifest-only",
        {"plugin_id": "manifest-only", "dwarf_api_version": "v1"},
    )
    _write_plugin(
        root,
        "registry-only",
        {
            "plugin_id": "registry-only",
            "dwarf_api_version": "v1",
            "registry": "registry.json",
        },
        {"registry.json": json.dumps(_primitive("registry_load"))},
    )
    marker = tmp_path / "entrypoint-executed"
    entrypoint_source = (
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed')\n"
        "def register(registry):\n"
        "    registry['dynamic_load'] = {'family': 'load'}\n"
    )
    _write_plugin(
        root,
        "entrypoint-only",
        {
            "plugin_id": "entrypoint-only",
            "dwarf_api_version": "v1",
            "entrypoint": "entrypoint.py",
        },
        {"entrypoint.py": entrypoint_source},
    )
    _write_plugin(
        root,
        "mixed",
        {
            "plugin_id": "mixed",
            "dwarf_api_version": "v1",
            "entrypoint": "entrypoint.py",
            "registry": "registry.json",
        },
        {
            "entrypoint.py": entrypoint_source,
            "registry.json": json.dumps(_primitive("mixed_static_load")),
        },
    )

    payload = _payload([root])
    rows = {row["plugin_id"]: row for row in payload["plugins"]}

    assert not marker.exists(), "dashboard inventory must never execute an entrypoint"
    assert rows["manifest-only"]["load_mode"] == "manifest-only"
    assert rows["manifest-only"]["status"] == "attention"
    assert rows["registry-only"]["load_mode"] == "registry-only"
    assert rows["registry-only"]["static_primitive_count"] == 1
    assert rows["registry-only"]["runtime_contributions_unknown"] is False
    assert rows["entrypoint-only"]["load_mode"] == "entrypoint-only"
    assert rows["entrypoint-only"]["static_primitive_count"] == 0
    assert rows["entrypoint-only"]["runtime_contributions_unknown"] is True
    assert rows["mixed"]["load_mode"] == "registry-and-entrypoint"
    assert rows["mixed"]["static_primitive_count"] == 1
    assert rows["mixed"]["runtime_contributions_unknown"] is True


def test_invalid_api_and_missing_declared_files_are_isolated_per_plugin(tmp_path):
    root = tmp_path / "plugins"
    _write_plugin(
        root,
        "bad-api",
        {"plugin_id": "bad-api", "dwarf_api_version": "v999"},
    )
    _write_plugin(
        root,
        "missing-entrypoint",
        {
            "plugin_id": "missing-entrypoint",
            "dwarf_api_version": "v1",
            "entrypoint": "absent.py",
        },
    )
    _write_plugin(
        root,
        "missing-registry",
        {
            "plugin_id": "missing-registry",
            "dwarf_api_version": "v1",
            "registry": "absent.json",
        },
    )
    _write_plugin(
        root,
        "healthy",
        {
            "plugin_id": "healthy",
            "dwarf_api_version": "v1",
            "registry": "registry.json",
        },
        {"registry.json": json.dumps(_primitive("healthy_load"))},
    )

    rows = {row["plugin_id"]: row for row in _payload([root])["plugins"]}

    assert set(rows) == {"bad-api", "missing-entrypoint", "missing-registry", "healthy"}
    assert rows["bad-api"]["status"] == "invalid"
    assert any("v999" in error and "v1" in error for error in rows["bad-api"]["errors"])
    assert rows["missing-entrypoint"]["status"] == "invalid"
    assert any("does not exist" in error for error in rows["missing-entrypoint"]["errors"])
    assert rows["missing-registry"]["status"] == "invalid"
    assert any("does not exist" in error for error in rows["missing-registry"]["errors"])
    assert rows["healthy"]["status"] == "declared"

    primitive_catalog = importlib.import_module(
        "profile_manager.data.operate_primitives"
    ).primitive_catalog_payload(
        registry_path=BUILTIN_REGISTRY,
        scenarios_dir=tmp_path / "scenarios",
        plugin_roots=[root],
    )
    healthy = next(
        row for row in primitive_catalog["rows"] if row["id"] == "healthy_load"
    )
    assert healthy["plugin_id"] == "healthy"
    assert any("bad-api" in error for error in primitive_catalog["errors"])


def test_manifest_and_registry_parse_errors_remain_visible_without_hiding_siblings(tmp_path):
    root = tmp_path / "plugins"
    _write_plugin(root, "bad-manifest", files={"plugin.json": "{not json"})
    _write_plugin(
        root,
        "bad-registry",
        {
            "plugin_id": "bad-registry",
            "dwarf_api_version": "v1",
            "registry": "registry.json",
        },
        {"registry.json": "{not json"},
    )
    _write_plugin(
        root,
        "healthy",
        {
            "plugin_id": "healthy",
            "dwarf_api_version": "v1",
            "registry": "registry.json",
        },
        {"registry.json": json.dumps(_primitive("healthy_load"))},
    )

    rows = {row["directory_name"]: row for row in _payload([root])["plugins"]}

    assert rows["bad-manifest"]["status"] == "invalid"
    assert any("manifest" in error.lower() for error in rows["bad-manifest"]["errors"])
    assert rows["bad-registry"]["status"] == "invalid"
    assert any("registry" in error.lower() for error in rows["bad-registry"]["errors"])
    assert rows["healthy"]["status"] == "declared"


def test_missing_manifest_candidate_is_visible(tmp_path):
    root = tmp_path / "plugins"
    _write_plugin(root, "no-manifest", files={"README.md": "candidate"})

    row = _payload([root])["plugins"][0]

    assert row["directory_name"] == "no-manifest"
    assert row["status"] == "invalid"
    assert any("plugin.json" in error for error in row["errors"])


def test_empty_manifest_and_escaped_registry_are_invalid_without_reading_outside_root(tmp_path):
    root = tmp_path / "plugins"
    _write_plugin(root, "empty", {})
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(_primitive("outside_load")), encoding="utf-8")
    _write_plugin(
        root,
        "escape",
        {
            "plugin_id": "escape",
            "dwarf_api_version": "v1",
            "registry": "../../outside.json",
        },
    )

    rows = {row["plugin_id"]: row for row in _payload([root])["plugins"]}

    assert rows["empty"]["status"] == "invalid"
    assert any("expected 'v1'" in error for error in rows["empty"]["errors"])
    assert rows["escape"]["status"] == "invalid"
    assert any("escapes the plugin root" in error for error in rows["escape"]["errors"])
    assert rows["escape"]["static_primitive_count"] == 0


def test_duplicate_plugin_ids_have_distinct_routes_and_visible_discrepancies(tmp_path):
    root = tmp_path / "plugins"
    for directory, primitive in (("one", "one_load"), ("two", "two_load")):
        _write_plugin(
            root,
            directory,
            {
                "plugin_id": "duplicate",
                "dwarf_api_version": "v1",
                "registry": "registry.json",
            },
            {"registry.json": json.dumps(_primitive(primitive))},
        )

    rows = _payload([root])["plugins"]

    assert len({row["catalog_id"] for row in rows}) == 2
    assert all(
        any("duplicate plugin_id" in item for item in row["discrepancies"])
        for row in rows
    )


def test_static_primitives_validate_and_link_while_collisions_are_explicit(tmp_path):
    root = tmp_path / "plugins"
    _write_plugin(
        root,
        "known",
        {
            "plugin_id": "known",
            "dwarf_api_version": "v1",
            "registry": "registry.json",
        },
        {
            "registry.json": json.dumps(
                {
                    "primitives": {
                        **_primitive("known_load")["primitives"],
                        **_primitive("cbor_fuzz_target")["primitives"],
                        "invalid_family": {
                            **_primitive("unused")["primitives"]["unused"],
                            "family": "not-a-family",
                        },
                    }
                }
            )
        },
    )

    row = _payload([root])["plugins"][0]
    primitives = {item["id"]: item for item in row["primitives"]}

    assert primitives["known_load"]["url"] == "/operate/primitives/known_load"
    assert primitives["known_load"]["status"] == "declared"
    assert primitives["known_load"]["resolved"] is True
    assert primitives["cbor_fuzz_target"]["status"] == "collision"
    assert "cbor_fuzz_target" in row["collisions"]
    assert primitives["invalid_family"]["status"] == "invalid"
    assert primitives["invalid_family"]["resolved"] is False
    assert {item["id"] for item in row["relationships"]} == {
        "known_load", "cbor_fuzz_target", "invalid_family"
    }
    assert row["status"] == "invalid"


def test_plugin_detail_exposes_exact_sources_trust_and_static_visibility(tmp_path):
    root = tmp_path / "plugins"
    plugin = _write_plugin(
        root,
        "mixed",
        {
            "plugin_id": "mixed",
            "dwarf_api_version": "v1",
            "version": "2.1.0",
            "description": "Mixed declaration",
            "entrypoint": "entrypoint.py",
            "registry": "registry.json",
        },
        {
            "entrypoint.py": "def register(registry):\n    pass\n",
            "registry.json": json.dumps(_primitive("mixed_static_load")),
        },
    )
    module = importlib.import_module("profile_manager.data.operate_plugins")
    payload = _payload([root])
    catalog_id = payload["plugins"][0]["catalog_id"]

    detail = module.plugin_detail(
        catalog_id,
        plugin_roots=[root],
        builtin_registry_path=BUILTIN_REGISTRY,
    )

    assert detail is not None
    assert detail["plugin_root"] == str(plugin.resolve())
    assert detail["manifest_path"] == str((plugin / "plugin.json").resolve())
    assert detail["manifest_source"]["plugin_id"] == "mixed"
    assert detail["entrypoint_path"] == str((plugin / "entrypoint.py").resolve())
    assert detail["registry_path"] == str((plugin / "registry.json").resolve())
    assert detail["dwarf_api_version"] == "v1"
    assert detail["runtime_contributions_unknown"] is True
    assert "same privileges" in detail["trust_warning"]


def test_plugin_individual_and_bulk_exports_are_deterministic_source_only(tmp_path):
    root = tmp_path / "plugins"
    marker = tmp_path / "entrypoint-executed"
    _write_plugin(
        root,
        "mixed",
        {
            "plugin_id": "mixed",
            "dwarf_api_version": "v1",
            "entrypoint": "entrypoint.py",
            "registry": "registry.json",
        },
        {
            "entrypoint.py": f"raise RuntimeError({str(marker)!r})\n",
            "registry.json": json.dumps(_primitive("mixed_load")),
            "credentials.json": "do-not-export",
            "._noise": "do-not-export",
        },
    )
    module = importlib.import_module("profile_manager.data.operate_plugins")

    first = module.deterministic_plugin_archive(
        plugin_roots=[root], builtin_registry_path=BUILTIN_REGISTRY
    )
    second = module.deterministic_plugin_archive(
        plugin_roots=[root], builtin_registry_path=BUILTIN_REGISTRY
    )
    assert first == second
    assert not marker.exists()
    with tarfile.open(fileobj=io.BytesIO(first), mode="r:gz") as archive:
        assert archive.getnames() == [
            "DWARF-EXPORT-MANIFEST.json",
            "dwarf/plugins/mixed/entrypoint.py",
            "dwarf/plugins/mixed/plugin.json",
            "dwarf/plugins/mixed/registry.json",
        ]
        manifest = json.loads(archive.extractfile("DWARF-EXPORT-MANIFEST.json").read())
        assert manifest["object_ids"] == ["mixed"]
        assert b"do-not-export" not in archive.extractfile("DWARF-EXPORT-MANIFEST.json").read()

    response = module.dispatch_plugin_api_request(
        "/api/plugins/mixed/export",
        plugin_roots=[root],
        builtin_registry_path=BUILTIN_REGISTRY,
    )
    assert response[0:2] == (200, "application/gzip")


def test_plugin_views_render_catalog_detail_diagnostics_and_no_authoring_controls(tmp_path):
    root = tmp_path / "plugins"
    _write_plugin(
        root,
        "registry-only",
        {
            "plugin_id": "registry-only",
            "dwarf_api_version": "v1",
            "registry": "registry.json",
        },
        {"registry.json": json.dumps(_primitive("registry_load"))},
    )
    views = importlib.import_module("profile_manager.views.operate_plugins")

    index = views.render_operate_plugins(plugin_roots=[root])
    detail = views.render_operate_plugin_detail(
        "registry-only",
        plugin_roots=[root],
    )

    assert '<h1>Plugins</h1>' in index
    assert 'href="/operate/plugins/registry-only"' in index
    assert "Trust boundary" in index
    assert '<h1>registry-only</h1>' in detail
    assert "Manifest and paths" in detail
    assert "Static primitive reconciliation" in detail
    assert 'href="/operate/primitives/registry_load"' in detail
    assert "valid module/class executor" in detail
    assert "not sandboxed" in detail
    assert "<form" not in detail


def test_plugin_route_dispatch_and_api_inventory_include_detail_route(tmp_path):
    views = importlib.import_module("profile_manager.views.operate_plugins")
    learn_api = importlib.import_module("profile_manager.data.learn_api")

    assert views.dispatch_plugin_request("/operate/plugins/not-found", plugin_roots=[]) is None
    assert views.dispatch_plugin_request("/operate/plugins/%2e%2e", plugin_roots=[]) is None
    operate_routes = {
        route
        for group in learn_api.html_route_groups()
        if group["label"] == "Operate"
        for route in group["routes"]
    }
    assert {"/operate/plugins", "/operate/plugins/<id>"} <= operate_routes


def test_plugin_authoring_docs_are_accurate_about_registry_only_and_runtime_entrypoints():
    dashboard = importlib.import_module("profile_manager.dashboard")

    html = dashboard.render_route_html("/learn/plugin-authoring")

    assert "registry-only" in html
    assert "valid module/class executor" in html
    assert "does not execute entrypoints" in html
    assert "runtime registration cannot be safely inventoried" in html
    assert "There is no sandbox" in html


def test_runtime_discovery_remains_fail_fast_and_execution_path_still_registers(tmp_path):
    loader = importlib.import_module("profile_manager.plugin_loader")
    root = tmp_path / "plugins"
    _write_plugin(
        root,
        "runtime",
        {
            "plugin_id": "runtime",
            "dwarf_api_version": "v1",
            "entrypoint": "entrypoint.py",
        },
        {
            "entrypoint.py": (
                "def register(registry):\n"
                "    registry['runtime_entry'] = {'family': 'probe'}\n"
            )
        },
    )

    manifests = loader.discover_plugin_manifests(plugin_roots=[root])
    entries = loader.load_plugin_entries(manifests)

    assert set(entries) == {"runtime_entry"}


def test_runtime_loader_rejects_cross_plugin_registry_and_entrypoint_overwrites(tmp_path):
    loader = importlib.import_module("profile_manager.plugin_loader")
    root = tmp_path / "plugins"
    _write_plugin(
        root,
        "first",
        {
            "plugin_id": "first",
            "dwarf_api_version": "v1",
            "registry": "registry.json",
        },
        {"registry.json": json.dumps(_primitive("shared_load"))},
    )
    _write_plugin(
        root,
        "second",
        {
            "plugin_id": "second",
            "dwarf_api_version": "v1",
            "entrypoint": "entrypoint.py",
        },
        {
            "entrypoint.py": (
                "def register(registry):\n"
                "    registry['shared_load'] = {'family': 'fault'}\n"
            )
        },
    )

    manifests = loader.discover_plugin_manifests(plugin_roots=[root])

    with pytest.raises(ValueError, match="overwrote existing primitive"):
        loader.load_plugin_entries(manifests)
