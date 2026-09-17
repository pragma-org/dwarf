"""Operate-side dashboard landing — operator command center.

Slice 20 CP2: replaced the 3-row wiring proof with a tile grid sourced
entirely from existing data extractors. Every value on the page comes
from a real evidence path; nothing is synthesized. Anti-fabrication
rails inherited from prior slices: no findings/severity/risk language,
no synthesized labels, evidence-only counts.
"""
from __future__ import annotations

from profile_manager.data.compare import (
    _has_metric_divergence,
    latest_compare_per_scenario,
)
from profile_manager.data.operate_bundles import operate_bundle_rows
from profile_manager.data.operate_runs import (
    operate_run_rows,
    status_pill_inventory,
)
from profile_manager.data.operate_targets import (
    implementation_pill_inventory,
    operate_target_rows,
)
from profile_manager.data.profiles import _profile_rows
from profile_manager.data.operate_primitives import primitive_catalog_rows
from profile_manager.data.operate_profile_templates import profile_template_catalog_rows
from profile_manager.data.operate_corpora import corpus_catalog_rows
from profile_manager.data.operate_grammars import grammar_catalog_rows
from profile_manager.data.operate_risk_packages import risk_package_catalog_rows
from profile_manager.data.scenarios import _list_scenarios_for_compare
from profile_manager.templating import render

# Recent-runs window for the pass/fail tile. 200 keeps a meaningful
# rolling sample without dragging in years of history.
_RECENT_RUNS_WINDOW = 200


def render_operate_landing() -> str:
    profiles = _profile_rows()
    profile_id = profiles[0]["id"] if profiles else None

    runs = operate_run_rows(limit=_RECENT_RUNS_WINDOW)
    pills = status_pill_inventory(runs)
    pass_count = next((p["count"] for p in pills if p["slug"] == "pass"), 0)
    fail_count = next((p["count"] for p in pills if p["slug"] == "fail"), 0)
    other_count = max(len(runs) - pass_count - fail_count, 0)
    pass_rate_pct = round(100 * pass_count / len(runs)) if runs else None

    targets = operate_target_rows()
    target_pills = implementation_pill_inventory(targets)

    bundles = operate_bundle_rows()

    comparisons = latest_compare_per_scenario(limit=_RECENT_RUNS_WINDOW)
    divergent = [c for c in comparisons if _has_metric_divergence(c)]
    corpora = corpus_catalog_rows()
    grammars = grammar_catalog_rows()
    risk_packages = risk_package_catalog_rows()

    return render(
        "operate/landing.j2",
        page_title="Operate",
        density="dense",        active="operate",
        active_sub="overview",
        profile_id=profile_id,
        profile_count=len(profiles),
        runs_total=len(runs),
        runs_window=_RECENT_RUNS_WINDOW,
        pass_count=pass_count,
        fail_count=fail_count,
        other_count=other_count,
        pass_rate_pct=pass_rate_pct,
        bundles_count=len(bundles),
        target_count=len(targets),
        target_pills=[p for p in target_pills if p["slug"]],
        scenario_count=len(_list_scenarios_for_compare()),
        primitive_count=len(primitive_catalog_rows()),
        profile_template_count=len(profile_template_catalog_rows()),
        corpus_count=len(corpora),
        corpus_input_count=sum(row["input_count"] for row in corpora),
        grammar_count=len(grammars),
        grammar_token_count=sum(row["token_count"] for row in grammars),
        risk_package_count=len(risk_packages),
        risk_candidate_count=len({candidate for row in risk_packages for candidate in row["candidate_ids"]}),
        comparisons_count=len(comparisons),
        divergent_count=len(divergent),
    )
