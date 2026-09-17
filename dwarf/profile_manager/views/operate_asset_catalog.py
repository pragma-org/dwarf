"""Shared Operate index/detail and source-download dispatch for asset catalogs."""

from __future__ import annotations

from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from profile_manager.data.asset_catalog import (
    DEFAULT_REGISTRY,
    AssetCatalog,
    AssetCatalogRegistry,
    CatalogItem,
    catalog_archive_filename,
    deterministic_asset_archive,
    is_safe_asset_id,
)
from profile_manager.templating import render


_CATALOG_NOT_FOUND_LABELS = {
    "scenarios": ("Scenarios", "Scenario"),
    "targets": ("Targets", "Target"),
    "profiles": ("Profiles", "Profile"),
    "primitives": ("Primitives", "Primitive"),
    "profile-templates": ("Profile templates", "Profile template"),
    "testcases": ("Test cases", "Test case"),
    "testcase-buckets": ("Test-case buckets", "Test-case bucket"),
    "corpora": ("Fuzz corpora", "Corpus"),
    "grammars": ("Grammars and dictionaries", "Grammar or dictionary"),
    "risk-packages": ("Risk packages", "Risk package"),
    "plugins": ("Plugins", "Plugin"),
}


def _attachment(filename: str) -> dict[str, str]:
    safe = filename.replace('"', "").replace("\r", "").replace("\n", "")
    return {"Content-Disposition": f'attachment; filename="{safe}"'}


def _row(item: CatalogItem) -> dict:
    return {
        "id": item.item_id,
        "label": item.label,
        "url": f"/operate/{item.catalog}/{item.item_id}",
        "source_path": item.source_path,
        "status": item.status,
        "summary": item.summary,
        "facets": [
            {"label": key.replace("_", " ").title(), "values": values}
            for key, values in sorted(item.facets.items())
        ],
    }


def render_asset_catalog(catalog: AssetCatalog) -> str:
    items = catalog.items()
    return render(
        "operate/asset_catalog.j2",
        page_title=catalog.label,
        density="dense",
        layout="wide",
        active="operate",
        active_sub=catalog.active_sub,
        catalog=catalog,
        rows=[_row(item) for item in items],
        empty=not items,
        export_url=f"/api/assets/{catalog.slug}/export",
    )


def render_asset_detail(catalog: AssetCatalog, item: CatalogItem) -> str:
    fields = [
        {"key": key.replace("_", " ").title(), "value": ", ".join(values)}
        for key, values in sorted(item.facets.items())
    ]
    return render(
        "operate/asset_detail.j2",
        page_title=item.label,
        density="reading",
        layout="wide",
        active="operate",
        active_sub=catalog.active_sub,
        catalog=catalog,
        item=item,
        fields=fields,
        download_url=f"/api/assets/{catalog.slug}/{item.item_id}/download",
        export_url=f"/api/assets/{catalog.slug}/{item.item_id}/export",
    )


def render_asset_not_found(path: str) -> str | None:
    """Render a themed 404 only for a known catalog and safe item id."""

    parsed = urlsplit(path)
    encoded_parts = parsed.path.strip("/").split("/")
    if len(encoded_parts) != 3 or encoded_parts[0] != "operate":
        return None
    parts = [unquote(part) for part in encoded_parts]
    labels = _CATALOG_NOT_FOUND_LABELS.get(parts[1])
    if labels is None or not is_safe_asset_id(parts[2]):
        return None
    catalog_label, singular_label = labels
    return render(
        "operate/asset_not_found.j2",
        page_title=f"{singular_label} not found",
        density="reading",
        layout="wide",
        active="operate",
        active_sub=parts[1],
        catalog_slug=parts[1],
        catalog_label=catalog_label,
        singular_label=singular_label,
        item_id=parts[2],
    )


def dispatch_asset_catalog_request(
    path: str, *, registry: AssetCatalogRegistry | None = None
) -> str | None:
    """Render one registered `/operate/<catalog>[/<id>]` route."""

    registry = DEFAULT_REGISTRY if registry is None else registry
    parsed = urlsplit(path)
    encoded_parts = parsed.path.strip("/").split("/")
    if len(encoded_parts) not in {2, 3} or encoded_parts[0] != "operate":
        return None
    parts = [unquote(part) for part in encoded_parts]
    catalog = registry.get(parts[1])
    if catalog is None:
        return None
    if len(parts) == 2:
        return render_asset_catalog(catalog)
    if not is_safe_asset_id(parts[2]):
        return None
    item = catalog.find(parts[2])
    return render_asset_detail(catalog, item) if item is not None else None


def dispatch_asset_download_request(
    path: str, *, registry: AssetCatalogRegistry | None = None
):
    """Dispatch deterministic bulk export and exact-source downloads."""

    registry = DEFAULT_REGISTRY if registry is None else registry
    parsed = urlsplit(path)
    encoded_parts = parsed.path.strip("/").split("/")
    if len(encoded_parts) not in {4, 5} or encoded_parts[:2] != ["api", "assets"]:
        return None
    parts = [unquote(part) for part in encoded_parts]
    catalog = registry.get(parts[2])
    if catalog is None:
        return (404, "text/plain; charset=utf-8", b"not found\n")
    if len(parts) == 4 and parts[3] == "export":
        return (
            200,
            "application/gzip",
            deterministic_asset_archive(catalog),
            _attachment(catalog_archive_filename(catalog)),
        )
    if len(parts) == 5 and parts[4] == "download":
        if not is_safe_asset_id(parts[3]):
            return (400, "text/plain; charset=utf-8", b"invalid asset id\n")
        item = catalog.find(parts[3])
        if item is None:
            return (404, "text/plain; charset=utf-8", b"not found\n")
        return (
            200,
            item.content_type,
            item.source_bytes,
            _attachment(PurePosixPath(item.export_path).name),
        )
    if len(parts) == 5 and parts[4] == "export":
        if not is_safe_asset_id(parts[3]):
            return (400, "text/plain; charset=utf-8", b"invalid asset id\n")
        item = catalog.find(parts[3])
        if item is None:
            return (404, "text/plain; charset=utf-8", b"not found\n")
        return (
            200,
            "application/gzip",
            deterministic_asset_archive(catalog, item_id=item.item_id),
            _attachment(f"{item.item_id}.tar.gz"),
        )
    return (400, "text/plain; charset=utf-8", b"invalid asset request\n")
