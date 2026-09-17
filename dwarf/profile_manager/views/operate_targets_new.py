"""View for /operate/targets/new — structured/raw target builder."""

from __future__ import annotations

def render_operate_targets_new(token: str | None = None) -> str:
    from profile_manager.views.operate_definition_edit import render_operate_definition_edit

    return render_operate_definition_edit("targets", None, token=token) or ""
