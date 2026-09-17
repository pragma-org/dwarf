"""Operate primitive catalog, detail, and route dispatch."""

from __future__ import annotations

from urllib.parse import unquote, urlsplit

from profile_manager.data.asset_catalog import is_safe_asset_id
from profile_manager.data.operate_primitives import (
    primitive_catalog_payload,
    primitive_detail,
)
from profile_manager.templating import render


def render_operate_primitives() -> str:
    payload = primitive_catalog_payload()
    return render(
        "operate/primitives.j2",
        page_title="Primitives",
        density="dense",
        layout="wide",
        active="operate",
        active_sub="primitives",
        **payload,
    )


def render_operate_primitive_detail(name: str) -> str | None:
    detail = primitive_detail(name)
    if detail is None:
        return None
    return render(
        "operate/primitive_detail.j2",
        page_title=name,
        density="reading",
        layout="wide",
        active="operate",
        active_sub="primitives",
        primitive=detail,
    )


def dispatch_primitive_catalog_request(path: str) -> str | None:
    parsed = urlsplit(path)
    encoded = parsed.path.strip("/").split("/")
    if len(encoded) not in {2, 3} or encoded[:2] != ["operate", "primitives"]:
        return None
    if len(encoded) == 2:
        return render_operate_primitives()
    name = unquote(encoded[2])
    if not is_safe_asset_id(name):
        return None
    return render_operate_primitive_detail(name)
