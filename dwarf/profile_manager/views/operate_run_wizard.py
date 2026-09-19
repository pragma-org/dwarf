"""Guided local-run view."""
from __future__ import annotations

from profile_manager.data.operate_run_wizard import run_wizard_catalog
from profile_manager.templating import render


def render_operate_run_wizard(*, token: str | None = None) -> str:
    return render(
        "operate/run_wizard.j2",
        page_title="Start a run",
        density="reading",
        layout="wide",
        active="operate",
        active_sub="run",
        wizard=run_wizard_catalog(),
        token=token or "",
    )
