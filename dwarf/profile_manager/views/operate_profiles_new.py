"""View for /operate/profiles/new — structured/raw deployment profile builder."""

from __future__ import annotations

def render_operate_profiles_new(token: str | None = None) -> str:
    from profile_manager.views.operate_definition_edit import render_operate_definition_edit

    return render_operate_definition_edit("profiles", None, token=token) or ""
