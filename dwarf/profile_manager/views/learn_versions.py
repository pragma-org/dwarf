"""Learn page for release policy and real-node qualification semantics."""
from __future__ import annotations

from profile_manager.templating import render


def render_learn_versions() -> str:
    return render(
        "learn/versions.j2",
        page_title="Version-qualified devnets",
        density="reading",
        layout="wide",
        active="learn",
        active_sub="versions",
    )
