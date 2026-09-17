"""Operate catalog and details for shipped profile templates."""

from __future__ import annotations

from urllib.parse import unquote, urlsplit

from profile_manager.data.asset_catalog import is_safe_asset_id
from profile_manager.data.operate_profile_templates import (
    profile_template_catalog_rows,
    profile_template_detail,
)
from profile_manager.templating import render


def render_operate_profile_templates() -> str:
    rows = profile_template_catalog_rows()
    return render(
        "operate/profile_templates.j2",
        page_title="Profile templates",
        density="dense",
        layout="wide",
        active="operate",
        active_sub="profile-templates",
        rows=rows,
    )


def render_operate_profile_template_detail(name: str) -> str | None:
    detail = profile_template_detail(name)
    if detail is None:
        return None
    return render(
        "operate/profile_template_detail.j2",
        page_title=name,
        density="reading",
        layout="wide",
        active="operate",
        active_sub="profile-templates",
        template=detail,
    )


def dispatch_profile_template_request(path: str) -> str | None:
    encoded = urlsplit(path).path.strip("/").split("/")
    if len(encoded) not in {2, 3} or encoded[:2] != ["operate", "profile-templates"]:
        return None
    if len(encoded) == 2:
        return render_operate_profile_templates()
    name = unquote(encoded[2])
    if not is_safe_asset_id(name):
        return None
    return render_operate_profile_template_detail(name)
