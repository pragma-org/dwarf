"""Read-only catalog and detail routes for plugin candidates."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urlsplit

from profile_manager.data.asset_catalog import is_safe_asset_id
from profile_manager.data.operate_plugins import plugin_catalog_payload, plugin_detail
from profile_manager.templating import render


def render_operate_plugins(*, plugin_roots: Iterable[Path] | None = None) -> str:
    return render(
        "operate/plugins.j2",
        page_title="Plugins",
        density="reading",
        layout="wide",
        active="operate",
        active_sub="plugins",
        **plugin_catalog_payload(plugin_roots=plugin_roots),
    )


def render_operate_plugin_detail(
    catalog_id: str,
    *,
    plugin_roots: Iterable[Path] | None = None,
) -> str | None:
    detail = plugin_detail(catalog_id, plugin_roots=plugin_roots)
    if detail is None:
        return None
    return render(
        "operate/plugin_detail.j2",
        page_title=detail["plugin_id"],
        density="reading",
        layout="wide",
        active="operate",
        active_sub="plugins",
        plugin=detail,
    )


def dispatch_plugin_request(
    path: str,
    *,
    plugin_roots: Iterable[Path] | None = None,
) -> str | None:
    parts = urlsplit(path).path.strip("/").split("/")
    if len(parts) not in {2, 3} or parts[:2] != ["operate", "plugins"]:
        return None
    if len(parts) == 2:
        return render_operate_plugins(plugin_roots=plugin_roots)
    catalog_id = unquote(parts[2])
    if not is_safe_asset_id(catalog_id):
        return None
    return render_operate_plugin_detail(catalog_id, plugin_roots=plugin_roots)
