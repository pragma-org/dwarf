import json
import re
from pathlib import Path

import jsonschema
import yaml

from profile_manager.dashboard import render_route_html
from profile_manager.data.measurement_coverage import measurement_coverage_payload
from profile_manager.data import client_example_evidence


ROOT = Path(__file__).resolve().parents[1]
MAP = ROOT / "dwarf/measurement-coverage/v1.yaml"
SCHEMA = ROOT / "dwarf/spec/v1/measurement-coverage-map.schema.json"
SCRIPT = ROOT / "dwarf/dashboard/static/js/measurement-coverage.js"
CSS = ROOT / "dwarf/dashboard/static/css/base.css"


def test_five_card_evidence_uses_the_authoritative_runtime_runs_root(monkeypatch, tmp_path):
    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(tmp_path))
    run_id = "20260920T235440Z-050046a4"
    assert client_example_evidence._run_manifest_path(run_id) == tmp_path / run_id / "manifest.json"


def test_versioned_mapping_is_schema_valid_and_references_current_measurements():
    mapping = yaml.safe_load(MAP.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    jsonschema.validate(mapping, schema)

    measurement_ids = {path.stem for path in (ROOT / "dwarf/measurements").glob("*.yaml")}
    mapped_ids = {
        measurement_id
        for section in ("surface_rules", "family_rules")
        for rule in mapping[section]
        for measurement_id in rule["measurements"]
    }
    assert mapped_ids
    assert mapped_ids <= measurement_ids
    assert mapping["schema_version"] == "v1"
    assert mapping["provenance"]


def test_render_time_join_reconciles_current_authoritative_sources():
    payload = measurement_coverage_payload()
    views = payload["views"]

    assert len(views["threats"]) == 36
    assert len(views["risks"]) == 35
    assert sum(row["scenario_count"] for row in views["scenario_families"]) == 266
    assert len({row["id"] for row in views["scenario_families"]}) == len(
        views["scenario_families"]
    )
    assert {"chainsync", "cbor/parser", "resource/runtime"} <= {
        row["id"] for row in views["surfaces"]
    }

    for view_rows in views.values():
        for row in view_rows:
            assert row["id"]
            assert row["title"]
            assert row["description"]
            assert row["implementations"]
            assert row["prerequisite"]
            assert row["can_prove"]
            assert row["non_claims"]
            assert row["status"] in payload["statuses"]
            for tap in row["measurements"]:
                assert tap["id"]
                assert tap["source"] in payload["sources"]
                assert tap["status"] in payload["statuses"]
                assert tap["id"] in row["search_text"]
            for scenario in row["scenarios"]:
                assert scenario["id"] in row["search_text"]
            if row["status"] == "Reserved":
                assert {tap["status"] for tap in row["measurements"]} == {"Reserved"}

            assert row["meaning"]
            assert row["coverage_statement"]
            assert row["tap_summary"]["applicable"] == len(row["measurements"])
            assert row["tap_summary"]["verified"] == sum(
                tap["status"] == "Verified" for tap in row["measurements"]
            )
            assert sum(len(group["taps"]) for group in row["tap_groups"]) == len(
                row["measurements"]
            )

    for overview in payload["overview"]:
        rows = views[overview["id"]]
        assert overview["mapped"] == sum(row["scenario_count"] > 0 for row in rows)
        assert overview["verified"] == sum(row["status"] == "Verified" for row in rows)
        assert overview["gaps"] == sum(row["status"] == "Unavailable" for row in rows)


def test_retained_evidence_is_grouped_once_per_run_not_repeated_per_tap():
    payload = measurement_coverage_payload()
    verified = next(row for row in payload["views"]["threats"] if row["id"] == "TM-015")

    evidence_keys = [(item["run_id"], item["scenario_id"]) for item in verified["evidence"]]
    assert len(evidence_keys) == len(set(evidence_keys))
    assert all(item["measurement_count"] == len(item["measurements"]) for item in verified["evidence"])
    assert all(item["measurements"] for item in verified["evidence"])


def test_evidence_states_do_not_convert_gaps_or_zeroes_into_proof():
    payload = measurement_coverage_payload()
    by_id = {
        row["id"]: row
        for name in ("threats", "risks")
        for row in payload["views"][name]
    }

    for gap_id in ("TM-030", "TM-031", "RR-027"):
        row = by_id[gap_id]
        assert row["status"] == "Unavailable"
        assert row["evidence"] == []
        assert row["scenario_count"] == 0

    verified = by_id["TM-015"]
    assert verified["status"] == "Verified"
    assert verified["scenario_count"] > 0
    assert "cardano-patched-protocol-decode" in {
        tap["id"] for tap in verified["measurements"]
    }
    assert any(item["run_id"] for item in verified["evidence"])
    assert all(item["url"].startswith("/") for item in verified["evidence"])

    assert payload["state_definitions"]["Applicable"] != payload["state_definitions"]["Configured"]
    assert payload["state_definitions"]["Configured"] != payload["state_definitions"]["Exercised"]
    assert "zero" in payload["state_definitions"]["Exercised"].lower()


def test_route_has_compact_overview_filters_and_progressive_disclosure():
    html = render_route_html("/learn/measurement-coverage")

    assert html is not None
    assert "Measurement coverage" in html
    for view, label in (
        ("threats", "Threats"),
        ("risks", "Risks"),
        ("scenario_families", "Scenario families"),
        ("surfaces", "Node/protocol surfaces"),
    ):
        assert f'data-coverage-view="{view}"' in html
        assert f">{label}<" in html
        assert f'data-overview-id="{view}"' in html
    for control in (
        'type="search"',
        'id="measurement-coverage-implementation"',
        'id="measurement-coverage-source"',
        'id="measurement-coverage-status"',
    ):
        assert control in html
    assert 'id="measurement-coverage-reset"' in html
    assert 'id="measurement-coverage-show-more"' in html
    assert 'data-initial-page-size="10"' in html
    assert '<summary>How to read this page</summary>' in html
    assert '<summary>Measurement taps</summary>' in html
    assert '<details class="measurement-coverage__tap-group">' in html
    assert '<summary>Scenarios</summary>' in html
    assert '<summary>Evidence</summary>' in html
    assert '<summary>Workload prerequisite</summary>' in html
    assert '<summary>Claims and limits</summary>' in html
    assert "<details open" not in html
    assert 'role="status"' in html
    assert html.count("Child-friendly explanation:") == 4
    assert "/learn/coverage" in html
    assert "/learn/threat-coverage" in html
    assert "/learn/measurements" in html


def test_collapsed_summaries_are_human_first_and_do_not_leak_raw_ids():
    html = render_route_html("/learn/measurement-coverage")
    summaries = re.findall(
        r'<summary class="measurement-coverage__row-summary">(.*?)</summary>',
        html,
        flags=re.DOTALL,
    )

    assert summaries
    for summary in summaries:
        assert "data-measurement-id" not in summary
        assert "/operate/measurements/" not in summary
        assert "/operate/scenarios/" not in summary
        assert not re.search(r"20\d{6}T\d{6}Z-[0-9a-f]+", summary)
        assert "applicable" in summary
        assert "verified" in summary
        assert "scenario" in summary


def test_long_lists_are_bounded_and_remaining_items_are_in_inert_templates():
    html = render_route_html("/learn/measurement-coverage")

    initial_lists = re.findall(
        r'<(?:ul|div) class="measurement-coverage__initial-list"[^>]*>(.*?)</(?:ul|div)>',
        html,
        flags=re.DOTALL,
    )
    assert initial_lists
    assert all(fragment.count('data-list-item="true"') <= 3 for fragment in initial_lists)
    assert 'template class="measurement-coverage__more-items"' in html
    assert 'data-action="show-all"' in html


def test_heading_order_and_controls_have_accessible_names():
    html = render_route_html("/learn/measurement-coverage")

    assert html.index("<h1>") < html.index("<h2") < html.index("<h3")
    for phrase in (
        'aria-label="Show Threats view"',
        'aria-label="Show Risks view"',
        'aria-label="Show Scenario families view"',
        'aria-label="Show Node/protocol surfaces view"',
        'aria-label="Clear search and filters"',
        'aria-controls="measurement-coverage-results"',
    ):
        assert phrase in html


def test_landing_and_related_pages_cross_link_without_replacing_existing_routes():
    landing = render_route_html("/learn")
    coverage = render_route_html("/learn/coverage")
    threats = render_route_html("/learn/threat-coverage")
    measurements = render_route_html("/learn/measurements")

    assert landing.count('href="/learn/measurement-coverage"') == 1
    assert "Measurement coverage" in landing
    for html in (coverage, threats, measurements):
        assert 'href="/learn/measurement-coverage"' in html
    assert render_route_html("/learn/coverage") is not None
    assert render_route_html("/learn/threat-coverage") is not None


def test_links_are_relative_or_public_and_page_has_no_private_references():
    html = render_route_html("/learn/measurement-coverage")
    hrefs = re.findall(r'href="([^"]+)"', html)
    assert hrefs
    assert all(
        href.startswith(("/", "#", "https://github.com/pragma-org/dwarf"))
        for href in hrefs
    )
    forbidden = "gain" + "palfam"
    assert forbidden not in html.lower()


def test_client_filter_is_safe_accessible_and_has_empty_and_print_states():
    script = SCRIPT.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")

    assert "DOMContentLoaded" in script
    assert "textContent" in script
    assert "innerHTML" not in script
    assert "measurement-coverage-empty" in script
    assert "aria-pressed" in script
    assert "hidden" in script
    assert "INITIAL_PAGE_SIZE = 10" in script
    assert "visibleLimit" in script
    assert "show-more" in script
    assert "show-all" in script
    assert "beforeprint" in script
    assert "afterprint" in script
    assert "measurement-coverage-reset" in script
    assert ".measurement-coverage" in css
    assert "overflow-wrap: anywhere" in css
    assert "@media print" in css
    assert ".measurement-coverage__controls" in css
