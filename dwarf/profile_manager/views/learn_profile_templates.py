"""Learn reference for profile templates."""

from __future__ import annotations

from profile_manager.data.operate_profile_templates import profile_template_catalog_rows
from profile_manager.templating import render


def render_learn_profile_templates() -> str:
    rows = profile_template_catalog_rows()
    return render(
        "learn/profile_templates.j2",
        page_title="Profile-template reference",
        density="reading",
        layout="wide",
        active="learn",
        active_sub="profile-templates",
        rows=rows,
    )
