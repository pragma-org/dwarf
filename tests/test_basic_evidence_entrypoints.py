"""Basic Timeline / Static-analysis pages link to existing evidence surfaces."""
from __future__ import annotations

import re

from profile_manager.dashboard import render_route_html
from profile_manager.templating import render
from profile_manager.views.operate_primitives import dispatch_primitive_catalog_request

TIMELINE_SCENARIO = "/operate/scenarios/runtime-bundle-timeline-example-smoke"


def _basic(html: str) -> str:
    return html.split('<div class="adv-only">', 1)[0]


def _adv(html: str) -> str:
    return html.split('<div class="adv-only">', 1)[1]


def _hrefs(html: str) -> list[str]:
    return re.findall(r'href="([^"]*)"', html)


def _latest_timeline() -> dict:
    return {
        "run_id": "20261011T000000Z-aaaa",
        "run_url": "/operate/runs/20261011T000000Z-aaaa",
        "generated_at_utc": "2026-10-11T00:00:00Z",
        "json_url": None,
        "md_url": None,
        "summary": {"event_count": 0, "signature_count": 0},
        "events": [],
        "signatures": [],
    }


def _sa_run(tool: str) -> dict:
    rid = f"20261011T000000Z-{tool}"
    return {
        "run_id": rid,
        "run_url": f"/operate/runs/{rid}",
        "pill_state": "ok",
        "tool_status": "clean",
        "tool_exit_code": 0,
        "findings_count": 0,
        "executed_at_utc": "2026-10-11T00:00:00Z",
        "crate_dir": "x",
        "command": ["cargo", tool],
        "parsed_line_count": None,
        "parse_error": None,
        "findings_url": f"/runs/{rid}/output?path=outputs/static-analysis-{tool}/findings.json",
        "stdout_url": f"/runs/{rid}/output?path=outputs/static-analysis-{tool}/stdout.log",
        "stderr_url": None,
        "stderr_tail": "",
    }


def test_linked_routes_exist():
    scenario = render_route_html(TIMELINE_SCENARIO)
    assert scenario and "runtime-bundle-timeline-example-smoke" in scenario
    assert render_route_html("/operate/runs")


def test_timeline_basic_empty_links_runs_and_scenario():
    html = render("operate/timeline.j2", ui="basic", latest=None, history=[])
    basic = _basic(html)
    assert "/operate/runs" in _hrefs(basic)
    assert TIMELINE_SCENARIO in _hrefs(basic)
    assert "Export bundle" in basic
    assert "No run on disk has an outputs/bundle-timeline/ folder yet." in basic
    assert not any(re.search(r"[?&](token|key|secret)=", h) for h in _hrefs(basic))


def test_timeline_basic_with_latest_still_links():
    html = render("operate/timeline.j2", ui="basic", latest=_latest_timeline(), history=[])
    basic = _basic(html)
    hrefs = _hrefs(basic)
    assert "/operate/runs" in hrefs and TIMELINE_SCENARIO in hrefs
    assert "latest completed" in basic
    assert "fixed list of bundle IDs" not in basic


def test_timeline_advanced_unchanged():
    adv = _adv(render("operate/timeline.j2", ui="advanced", latest=None, history=[]))
    assert TIMELINE_SCENARIO not in adv


def test_static_analysis_basic_exposes_retained_artifacts():
    tools = ["clippy", "audit", "deny"]
    latest = {"clippy": _sa_run("clippy"), "audit": None, "deny": None}
    html = render("operate/static_analysis.j2", ui="basic", tools=tools, latest=latest,
                  history={t: [] for t in tools})
    basic = _basic(html)
    hrefs = _hrefs(basic)
    assert latest["clippy"]["findings_url"] in hrefs
    assert latest["clippy"]["stdout_url"] in hrefs
    assert "/operate/runs" in hrefs
    # Never link to a scenario that is absent from the current catalog.
    assert not any(h.startswith("/operate/scenarios/runtime-static-analysis") for h in hrefs)
    assert "Not run yet" in basic
    assert "No per-tool execution scenario or catalog primitive is shipped" in basic
    assert not any(re.search(r"[?&](token|key|secret)=", h) for h in hrefs)


def test_static_analysis_capability_drift_requires_real_scenario_link():
    """Fail visibly if a future catalog adds these scenarios.

    Absence is not the product requirement: the page must track the actual
    catalog. A newly shipped scenario therefore requires replacing the honest
    unavailable message with its real Basic link.
    """
    for tool in ("clippy", "audit", "deny"):
        route = f"/operate/scenarios/runtime-static-analysis-{tool}-example-smoke"
        assert render_route_html(route) is None, (
            f"{route} now exists; expose it from Basic static analysis"
        )

    html = render(
        "operate/static_analysis.j2",
        ui="advanced",
        tools=["clippy", "audit", "deny"],
        latest={tool: None for tool in ("clippy", "audit", "deny")},
        history={tool: [] for tool in ("clippy", "audit", "deny")},
    )
    assert "dwarf/scenarios/runtime-static-analysis-" not in html


def test_static_analysis_empty_state_matches_current_catalog_capabilities():
    tools = ["clippy", "audit", "deny"]
    html = render(
        "operate/static_analysis.j2",
        ui="basic",
        tools=tools,
        latest={tool: None for tool in tools},
        history={tool: [] for tool in tools},
    )
    hrefs = _hrefs(_basic(html))
    for tool in tools:
        scenario = f"/operate/scenarios/runtime-static-analysis-{tool}-example-smoke"
        primitive = f"/operate/primitives/runtime_static_analysis_{tool}"
        available = bool(render_route_html(scenario) or dispatch_primitive_catalog_request(primitive))
        if available:
            assert scenario in hrefs or primitive in hrefs
        else:
            assert scenario not in hrefs and primitive not in hrefs
            assert "No per-tool execution scenario or catalog primitive is shipped" in _basic(html)
