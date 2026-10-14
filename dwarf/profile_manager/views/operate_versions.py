"""Operator view for exact node releases and mixed compatibility pairs."""
from __future__ import annotations

from profile_manager.data.operate_versions import version_catalog_view
from profile_manager.templating import render


def render_operate_versions(*, token: str | None = None) -> str:
    view = version_catalog_view()
    return render(
        "operate/versions.j2",
        page_title="Node versions",
        density="reading",
        layout="wide",
        active="operate",
        active_sub="versions",
        refresh_endpoint="/api/versions/refresh",
        preference_endpoint="/api/versions/preferences",
        binary_import_endpoint="/api/versions/import-binary",
        binary_import_status_endpoint="/api/versions/import-binary/status",
        refresh_token=token,
        **view,
    )
