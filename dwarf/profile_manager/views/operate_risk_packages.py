"""Operate views for Milestone-1 risk work packages."""

from __future__ import annotations

from urllib.parse import unquote, urlsplit

from profile_manager.data.asset_catalog import is_safe_asset_id
from profile_manager.data.operate_risk_packages import risk_package_catalog_rows, risk_package_detail
from profile_manager.templating import render


def render_operate_risk_packages() -> str:
    rows = risk_package_catalog_rows()
    return render(
        "operate/risk_packages.j2",
        page_title="Risk work packages",
        density="dense",
        layout="wide",
        active="operate",
        active_sub="risk-packages",
        rows=rows,
        candidate_count=len({candidate for row in rows for candidate in row["candidate_ids"]}),
        evidence_count=sum(len(row["evidence"]) for row in rows),
        available_evidence_count=sum(row["available_evidence_count"] for row in rows),
    )


def render_operate_risk_package_detail(package_id: str) -> str | None:
    detail = risk_package_detail(package_id)
    if detail is None:
        return None
    return render(
        "operate/risk_package_detail.j2",
        page_title=detail["label"],
        density="reading",
        layout="wide",
        active="operate",
        active_sub="risk-packages",
        package=detail,
    )


def dispatch_risk_package_request(path: str) -> str | None:
    parts = [unquote(part) for part in urlsplit(path).path.strip("/").split("/")]
    if len(parts) not in {2, 3} or parts[:2] != ["operate", "risk-packages"]:
        return None
    if len(parts) == 2:
        return render_operate_risk_packages()
    if not is_safe_asset_id(parts[2]):
        return None
    return render_operate_risk_package_detail(parts[2])
