"""Learn reference for Milestone-1 risk work packages."""

from __future__ import annotations

from profile_manager.data.operate_risk_packages import risk_package_catalog_rows, risk_work_package_schema
from profile_manager.templating import render


def render_learn_risk_packages() -> str:
    rows = risk_package_catalog_rows()
    return render(
        "learn/risk_packages.j2",
        page_title="Risk work packages",
        density="reading",
        layout="wide",
        active="learn",
        active_sub="risk-packages",
        rows=rows,
        schema=risk_work_package_schema(),
    )
