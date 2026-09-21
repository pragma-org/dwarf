"""Learn-side guide for first-class DWARF measurements."""
from __future__ import annotations

from profile_manager.templating import render


def render_learn_measurements() -> str:
    return render(
        "learn/measurements.j2",
        page_title="Measurements",
        density="reading",
        layout="wide",
        active="learn",
        active_sub="measurements",
    )
