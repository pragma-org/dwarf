"""Guided local-run view."""
from __future__ import annotations

from profile_manager.data.operate_run_wizard import (
    run_wizard_browser_bootstrap,
    run_wizard_catalog,
)
from profile_manager.templating import render
from profile_manager.data.scenarios import _list_scenarios_for_compare
from profile_manager.data.threat_risk_coverage import threat_risk_discovery_index
from profile_manager.views.threat_coverage import current_threat_coverage_data


def render_operate_run_wizard(
    *, token: str | None = None, requested_scenario_id: str | None = None
) -> str:
    scenario_ids = {row["id"] for row in _list_scenarios_for_compare()}
    threat_risk_index = threat_risk_discovery_index(
        current_threat_coverage_data(include_measurements=False),
        scenario_ids=scenario_ids,
    )
    wizard = run_wizard_catalog(
        requested_scenario_id=requested_scenario_id,
        threat_risk_index=threat_risk_index,
    )
    return render(
        "operate/run_wizard.j2",
        page_title="Start a run",
        density="reading",
        layout="wide",
        active="operate",
        active_sub="run",
        wizard=wizard,
        wizard_bootstrap=run_wizard_browser_bootstrap(wizard),
        token=token or "",
    )
