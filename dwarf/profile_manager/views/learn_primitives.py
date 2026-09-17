"""Learn reference for the primitive authoring and execution contract."""

from __future__ import annotations

from profile_manager.data.operate_primitives import primitive_catalog_payload
from profile_manager.templating import render


def render_learn_primitives() -> str:
    payload = primitive_catalog_payload()
    return render(
        "learn/primitives.j2",
        page_title="Primitive reference",
        density="reading",
        layout="wide",
        active="learn",
        active_sub="primitives",
        primitive_count=len(payload["rows"]),
        family_counts=payload["family_counts"],
        errors=payload["errors"],
    )
