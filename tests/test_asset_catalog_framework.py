import gzip
import importlib
import io
import tarfile


def _data_module():
    return importlib.import_module("profile_manager.data.asset_catalog")


def _view_module():
    return importlib.import_module("profile_manager.views.operate_asset_catalog")


def _fixture_catalog(tmp_path):
    assets = _data_module()
    items = (
        assets.CatalogItem(
            catalog="fixtures",
            item_id="alpha",
            label="Alpha",
            source_path="dwarf/fixtures/alpha.yaml",
            status="valid",
            facets={"family": ("load",), "runtime": ("devnet",)},
            summary="A valid fixture item.",
            raw_text="id: alpha\n",
            export_path="dwarf/fixtures/alpha.yaml",
            relationships=({"label": "Scenario", "url": "/operate/scenarios/alpha"},),
        ),
        assets.CatalogItem(
            catalog="fixtures",
            item_id="platform-noise",
            label="Platform noise",
            source_path="dwarf/fixtures/._alpha.yaml",
            status="ignored",
            facets={},
            summary="Must never be exported.",
            raw_text="noise\n",
            export_path="dwarf/fixtures/._alpha.yaml",
        ),
        assets.CatalogItem(
            catalog="fixtures",
            item_id="cache-noise",
            label="Cache noise",
            source_path="dwarf/fixtures/__pycache__/alpha.pyc",
            status="ignored",
            facets={},
            summary="Must never be exported.",
            raw_text="cache\n",
            export_path="dwarf/fixtures/__pycache__/alpha.pyc",
        ),
    )
    return assets.AssetCatalog(
        slug="fixtures",
        label="Fixture assets",
        singular_label="Fixture asset",
        description="Test-only catalog.",
        active_sub="fixtures",
        load_items=lambda: items,
    )


def test_registry_allow_lists_catalogs_and_rejects_duplicates(tmp_path):
    assets = _data_module()
    registry = assets.AssetCatalogRegistry()
    catalog = _fixture_catalog(tmp_path)

    registry.register(catalog)

    assert registry.get("fixtures") is catalog
    assert registry.get("not-a-catalog") is None
    try:
        registry.register(catalog)
    except ValueError as exc:
        assert "already registered" in str(exc)
    else:
        raise AssertionError("duplicate catalog registration must fail")


def test_asset_catalog_unknown_kind_is_not_found(tmp_path):
    assets = _data_module()
    views = _view_module()
    registry = assets.AssetCatalogRegistry()
    registry.register(_fixture_catalog(tmp_path))

    assert views.dispatch_asset_catalog_request(
        "/operate/not-a-catalog", registry=registry
    ) is None


def test_asset_detail_rejects_traversal(tmp_path):
    assets = _data_module()
    views = _view_module()
    registry = assets.AssetCatalogRegistry()
    registry.register(_fixture_catalog(tmp_path))

    assert views.dispatch_asset_catalog_request(
        "/operate/fixtures/../registry.json", registry=registry
    ) is None
    assert views.dispatch_asset_catalog_request(
        "/operate/fixtures/%2e%2e", registry=registry
    ) is None


def test_shared_index_and_detail_render_accessible_catalog_content(tmp_path):
    assets = _data_module()
    views = _view_module()
    registry = assets.AssetCatalogRegistry()
    registry.register(_fixture_catalog(tmp_path))

    index = views.dispatch_asset_catalog_request(
        "/operate/fixtures", registry=registry
    )
    detail = views.dispatch_asset_catalog_request(
        "/operate/fixtures/alpha", registry=registry
    )

    assert index is not None
    assert "<h1>Fixture assets</h1>" in index
    assert 'aria-label="Search fixture assets"' in index
    assert 'href="/api/assets/fixtures/export"' in index
    assert 'href="/operate/fixtures/alpha"' in index
    assert 'data-asset-catalog="fixtures"' in index
    assert detail is not None
    assert "<h1>Alpha</h1>" in detail
    assert "Complete source" in detail
    assert "dwarf/fixtures/alpha.yaml" in detail
    assert 'href="/api/assets/fixtures/alpha/export"' in detail
    assert 'aria-labelledby="asset-relationships-heading"' in detail
    assert 'href="/operate/scenarios/alpha"' in detail


def test_asset_archive_is_deterministic_and_excludes_noise(tmp_path):
    assets = _data_module()
    catalog = _fixture_catalog(tmp_path)

    first = assets.deterministic_asset_archive(catalog)
    second = assets.deterministic_asset_archive(catalog)

    assert first == second
    with gzip.GzipFile(fileobj=io.BytesIO(first), mode="rb") as gz:
        with tarfile.open(fileobj=gz, mode="r:") as archive:
            names = archive.getnames()
            assert names == [
                "DWARF-EXPORT-MANIFEST.json",
                "dwarf/fixtures/alpha.yaml",
            ]
            assert archive.extractfile(names[1]).read() == b"id: alpha\n"
            manifest = archive.extractfile(names[0]).read()
            assert b'"catalog": "fixtures"' in manifest
            assert b'"object_id": "alpha"' in manifest


def test_asset_archive_rejects_unsafe_export_path(tmp_path):
    assets = _data_module()
    item = assets.CatalogItem(
        catalog="fixtures",
        item_id="unsafe",
        label="Unsafe",
        source_path="../secret",
        status="invalid",
        facets={},
        summary="",
        raw_text="secret\n",
        export_path="../secret",
    )
    catalog = assets.AssetCatalog(
        slug="fixtures",
        label="Fixtures",
        singular_label="Fixture",
        description="",
        active_sub="fixtures",
        load_items=lambda: (item,),
    )

    try:
        assets.deterministic_asset_archive(catalog)
    except assets.UnsafeAssetPathError as exc:
        assert "unsafe export path" in str(exc)
    else:
        raise AssertionError("traversing archive paths must be rejected")


def test_asset_archive_rejects_windows_style_paths(tmp_path):
    assets = _data_module()

    for unsafe_path in (r"..\secret", r"C:\secret", "C:/secret"):
        item = assets.CatalogItem(
            catalog="fixtures",
            item_id="unsafe",
            label="Unsafe",
            source_path=unsafe_path,
            status="invalid",
            facets={},
            summary="",
            raw_text="secret\n",
            export_path=unsafe_path,
        )
        catalog = assets.AssetCatalog(
            slug="fixtures",
            label="Fixtures",
            singular_label="Fixture",
            description="",
            active_sub="fixtures",
            load_items=lambda: (item,),
        )

        try:
            assets.deterministic_asset_archive(catalog)
        except assets.UnsafeAssetPathError:
            pass
        else:
            raise AssertionError(f"Windows-style path must be rejected: {unsafe_path}")


def test_asset_download_dispatch_has_safe_headers_and_statuses(tmp_path):
    assets = _data_module()
    views = _view_module()
    registry = assets.AssetCatalogRegistry()
    registry.register(_fixture_catalog(tmp_path))

    exported = views.dispatch_asset_download_request(
        "/api/assets/fixtures/export", registry=registry
    )
    downloaded = views.dispatch_asset_download_request(
        "/api/assets/fixtures/alpha/download", registry=registry
    )
    individual = views.dispatch_asset_download_request(
        "/api/assets/fixtures/alpha/export", registry=registry
    )
    missing = views.dispatch_asset_download_request(
        "/api/assets/fixtures/missing/download", registry=registry
    )

    assert exported[0:2] == (200, "application/gzip")
    assert "attachment" in exported[3]["Content-Disposition"]
    assert downloaded[0:3] == (
        200,
        "text/plain; charset=utf-8",
        b"id: alpha\n",
    )
    assert downloaded[3]["Content-Disposition"].endswith('"alpha.yaml"')
    assert individual[0:2] == (200, "application/gzip")
    with tarfile.open(fileobj=io.BytesIO(individual[2]), mode="r:gz") as archive:
        assert archive.getnames() == [
            "DWARF-EXPORT-MANIFEST.json",
            "dwarf/fixtures/alpha.yaml",
        ]
    assert missing[0] == 404


def test_asset_item_ids_are_single_url_safe_segments():
    assets = _data_module()

    for value in ("alpha", "alpha-1", "alpha_beta", "alpha.beta"):
        assert assets.is_safe_asset_id(value)
    for value in ("", ".", "..", "a/b", "a\\b", "%2e%2e", "a b"):
        assert not assets.is_safe_asset_id(value)


def test_dashboard_delegates_asset_html_and_download_routes(monkeypatch, tmp_path):
    assets = _data_module()
    views = _view_module()
    dashboard = importlib.import_module("profile_manager.dashboard")
    registry = assets.AssetCatalogRegistry()
    registry.register(_fixture_catalog(tmp_path))
    monkeypatch.setattr(views, "DEFAULT_REGISTRY", registry)

    html = dashboard.render_route_html("/operate/fixtures")
    response = dashboard.dispatch_api_request("/api/assets/fixtures/export")

    assert html is not None
    assert "Fixture assets" in html
    assert response is not None
    assert response[0:2] == (200, "application/gzip")


def test_api_reference_documents_shared_asset_download_contract():
    learn_api = importlib.import_module("profile_manager.data.learn_api")
    paths = {entry["path"] for entry in learn_api.ENDPOINTS}

    assert "/api/assets/<catalog>/export /api/assets/<catalog>/<id>/export" in paths
    assert "/api/assets/<catalog>/<id>/download" in paths
