"""Operate /bundles view."""
from __future__ import annotations

from profile_manager.data.operate_bundles import (
    operate_bundle_rows,
    status_pill_inventory,
)
from profile_manager.templating import render


def render_operate_bundles() -> str:
    """Render /operate/bundles: dense-table forensic-bundle catalog with filter pills."""
    rows = operate_bundle_rows()
    pills = status_pill_inventory(rows)
    from profile_manager.data.scenario_cards import scenario_card
    from profile_manager.data.scenarios import _list_scenarios_for_compare
    scenario_cards = {s["id"]: scenario_card(s) for s in _list_scenarios_for_compare()}
    return render(
        "operate/bundles.j2",
        page_title="Bundles",
        density="dense",        active="operate",
        active_sub="bundles",
        rows=rows,
        pills=pills,
        scenario_cards=scenario_cards,
        empty=not rows,
    )
