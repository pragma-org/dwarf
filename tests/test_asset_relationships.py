import gzip
import hashlib
import importlib
import io
import json
import tarfile


def _assets():
    return importlib.import_module("profile_manager.data.asset_catalog")


def _archive(body: bytes):
    zipped = gzip.GzipFile(fileobj=io.BytesIO(body), mode="rb")
    return zipped, tarfile.open(fileobj=zipped, mode="r:")


def test_relationships_are_reciprocal_when_source_explicitly_proves_the_link():
    assets = _assets()
    records = {
        "scenarios": [
            {
                "id": "s1",
                "source_path": "dwarf/scenarios/s1.yaml",
                "relationships": [
                    {
                        "catalog": "primitives",
                        "id": "p1",
                        "relation": "uses",
                        "source_path": "dwarf/scenarios/s1.yaml#load[0]",
                    }
                ],
            }
        ],
        "primitives": [
            {
                "id": "p1",
                "source_path": "dwarf/primitives/registry.json#primitives.p1",
                "relationships": [],
            }
        ],
    }

    graph = assets.build_asset_graph(records)

    assert graph.has_edge("scenarios:s1", "primitives:p1")
    assert graph.has_edge("primitives:p1", "scenarios:s1")
    forward = graph.edge("scenarios:s1", "primitives:p1")
    reverse = graph.edge("primitives:p1", "scenarios:s1")
    assert forward.relation == "uses"
    assert reverse.relation == "referenced-by"
    assert forward.source_path == "dwarf/scenarios/s1.yaml#load[0]"
    assert forward.resolved is True


def test_unresolved_explicit_references_remain_visible_but_name_similarity_invents_nothing():
    assets = _assets()
    graph = assets.build_asset_graph(
        {
            "scenarios": [
                {
                    "id": "chain-sync-case",
                    "source_path": "scenario.yaml",
                    "relationships": [
                        {
                            "catalog": "primitives",
                            "id": "missing-primitive",
                            "relation": "uses",
                            "source_path": "scenario.yaml#load[0]",
                        }
                    ],
                }
            ],
            "primitives": [
                {
                    "id": "chain-sync-case-like-name",
                    "source_path": "registry.json",
                    "relationships": [],
                }
            ],
        }
    )

    assert graph.has_edge("scenarios:chain-sync-case", "primitives:missing-primitive")
    assert graph.edge(
        "scenarios:chain-sync-case", "primitives:missing-primitive"
    ).resolved is False
    assert not graph.has_edge(
        "scenarios:chain-sync-case", "primitives:chain-sync-case-like-name"
    )


def test_shared_detail_renders_unresolved_relationship_without_a_dangling_link(tmp_path):
    assets = _assets()
    views = importlib.import_module("profile_manager.views.operate_asset_catalog")
    item = assets.CatalogItem(
        catalog="fixtures",
        item_id="alpha",
        label="Alpha",
        source_path="dwarf/fixtures/alpha.yaml",
        status="valid",
        facets={},
        summary="Fixture",
        raw_text="id: alpha\n",
        export_path="dwarf/fixtures/alpha.yaml",
        relationships=(
            {
                "catalog": "scenarios",
                "id": "missing",
                "label": "Scenario missing",
                "url": "/operate/scenarios/missing",
                "relation": "produced-by",
                "source_path": "dwarf/fixtures/alpha.yaml#scenario_id",
                "resolved": False,
            },
        ),
    )
    catalog = assets.AssetCatalog(
        slug="fixtures",
        label="Fixtures",
        singular_label="Fixture",
        description="",
        active_sub="fixtures",
        load_items=lambda: (item,),
    )

    html = views.render_asset_detail(catalog, item)

    assert "Scenario missing" in html
    assert "unresolved" in html
    assert 'href="/operate/scenarios/missing"' not in html
    assert "dwarf/fixtures/alpha.yaml#scenario_id" in html


def test_export_manifest_is_deterministic_and_records_revision_paths_and_hashes():
    assets = _assets()
    item = assets.CatalogItem(
        catalog="fixtures",
        item_id="alpha",
        label="Alpha",
        source_path="dwarf/fixtures/alpha.yaml",
        status="valid",
        facets={},
        summary="Fixture",
        raw_text="id: alpha\n",
        export_path="dwarf/fixtures/alpha.yaml",
    )
    catalog = assets.AssetCatalog(
        slug="fixtures",
        label="Fixtures",
        singular_label="Fixture",
        description="",
        active_sub="fixtures",
        load_items=lambda: (item,),
    )

    first = assets.deterministic_asset_archive(
        catalog,
        source_revision="revision-test",
        generation_time="2026-09-13T00:00:00Z",
    )
    second = assets.deterministic_asset_archive(
        catalog,
        source_revision="revision-test",
        generation_time="2026-09-13T00:00:00Z",
    )

    assert first == second
    zipped, archive = _archive(first)
    try:
        assert archive.getnames() == [
            "DWARF-EXPORT-MANIFEST.json",
            "dwarf/fixtures/alpha.yaml",
        ]
        manifest = json.loads(
            archive.extractfile("DWARF-EXPORT-MANIFEST.json").read()
        )
    finally:
        archive.close()
        zipped.close()
    assert manifest["catalog"] == "fixtures"
    assert manifest["source_revision"] == "revision-test"
    assert manifest["generated_at"] == "2026-09-13T00:00:00Z"
    assert manifest["object_ids"] == ["alpha"]
    assert manifest["objects"][0]["source_path"] == "dwarf/fixtures/alpha.yaml"
    assert manifest["objects"][0]["sha256"] == hashlib.sha256(b"id: alpha\n").hexdigest()


def test_export_manifest_excludes_platform_cache_and_sensitive_named_sources():
    assets = _assets()
    paths = (
        "dwarf/fixtures/alpha.yaml",
        "dwarf/fixtures/._alpha.yaml",
        "dwarf/fixtures/.DS_Store",
        "dwarf/fixtures/__pycache__/alpha.pyc",
        "dwarf/fixtures/.env",
        "dwarf/fixtures/credentials.json",
        "dwarf/fixtures/id_rsa",
        "dwarf/fixtures/secret-token.txt",
    )
    items = tuple(
        assets.CatalogItem(
            catalog="fixtures",
            item_id=f"item-{index}",
            label=path,
            source_path=path,
            status="valid",
            facets={},
            summary="",
            raw_text="do-not-leak\n" if index else "safe\n",
            export_path=path,
        )
        for index, path in enumerate(paths)
    )
    catalog = assets.AssetCatalog(
        slug="fixtures",
        label="Fixtures",
        singular_label="Fixture",
        description="",
        active_sub="fixtures",
        load_items=lambda: items,
    )

    body = assets.deterministic_asset_archive(catalog)
    zipped, archive = _archive(body)
    try:
        names = archive.getnames()
        manifest = archive.extractfile("DWARF-EXPORT-MANIFEST.json").read()
    finally:
        archive.close()
        zipped.close()

    assert names == ["DWARF-EXPORT-MANIFEST.json", "dwarf/fixtures/alpha.yaml"]
    assert b"do-not-leak" not in manifest


def test_generic_export_builder_supports_multiple_files_for_one_asset():
    assets = _assets()
    sources = [
        assets.ExportSource(
            object_id="grammar-a",
            source_path="dwarf/grammars/a/structure.json",
            export_path="dwarf/grammars/a/structure.json",
            body=b"{}\n",
        ),
        assets.ExportSource(
            object_id="grammar-a",
            source_path="dwarf/grammars/a/dict.txt",
            export_path="dwarf/grammars/a/dict.txt",
            body=b'"x"\n',
        ),
    ]

    body = assets.deterministic_export_archive("grammars", sources)
    zipped, archive = _archive(body)
    try:
        manifest = json.loads(
            archive.extractfile("DWARF-EXPORT-MANIFEST.json").read()
        )
    finally:
        archive.close()
        zipped.close()

    assert manifest["object_ids"] == ["grammar-a"]
    assert [item["export_path"] for item in manifest["files"]] == [
        "dwarf/grammars/a/dict.txt",
        "dwarf/grammars/a/structure.json",
    ]
