"""Operate /runs view."""
from __future__ import annotations

from profile_manager.data.lifecycle import _local_testcase_lifecycle_summary
from profile_manager.data.operate_run_families import issue_families
from profile_manager.data.operate_runs import (
    apply_run_filters,
    operate_run_page,
)
from profile_manager.templating import render


def render_operate_runs(
    *,
    outcome: str = "",
    q: str = "",
    scope: str = "scenario",
    cursor: str | None = None,
) -> str:
    """Render /operate/runs: dense-table recent-runs index with filter pills.

    Slice 26 added an "Issue families" aggregate panel that surfaces the
    runtime-anomaly buckets across the testcase lifecycle state — derived
    from data/state/testcases not the runs index itself, so the panel is
    informative even when filtered by a particular result pill.

    Slice 46 wired server-side filters: ``outcome`` (pass / fail / error /
    inconclusive / "" for all) and ``q`` (substring match on run_id /
    scenario_id / profile_id / target_implementation). Filters are
    bookmarkable since they live in the URL.
    """
    page = operate_run_page(limit=100, cursor=cursor, scope=scope, outcome=outcome)
    all_rows = page["runs"]
    filtered = apply_run_filters(all_rows, outcome=outcome, q=q)
    verdict_counts = page["counts"]["verdicts"]
    pills = [
        {"slug": "", "label": "all", "count": page["scope_total"], "active": not outcome}
    ] + [
        {
            "slug": value,
            "label": value,
            "count": verdict_counts.get(value, 0),
            "active": outcome == value,
        }
        for value in ("pass", "fail", "error", "inconclusive")
    ]
    lifecycle = _local_testcase_lifecycle_summary()
    families = issue_families(lifecycle)
    from profile_manager.data.scenario_cards import run_card, scenario_card
    from profile_manager.data.scenarios import _list_scenarios_for_compare
    scenario_cards = {s["id"]: scenario_card(s) for s in _list_scenarios_for_compare()}
    run_cards = {r["run_id"]: run_card(r.get("scenario_id"), r["run_id"], scenario_cards) for r in all_rows}
    from profile_manager.data.runs import _forensic_runs_dir
    runs_base = _forensic_runs_dir()
    measured = {r["run_id"]: (runs_base / r["run_id"] / "measurements" / "report.json").is_file() for r in all_rows}
    return render(
        "operate/runs.j2",
        page_title="Runs",
        density="dense",        active="operate",
        active_sub="runs",
        rows=filtered,
        all_rows=all_rows,
        run_cards=run_cards,
        measured=measured,
        all_count=page["total"],
        scope_count=page["scope_total"],
        page_count=len(all_rows),
        filtered_count=len(filtered),
        pills=pills,
        empty=page["scope_total"] == 0,
        filter_outcome=outcome,
        filter_q=q,
        history_scope=scope,
        next_cursor=page["next_cursor"],
        history_counts=page["counts"],
        history_scope_totals=page["scope_totals"],
        families=families,
        runtime_anomaly_count=lifecycle.get("runtime_anomaly_count", 0),
    )
