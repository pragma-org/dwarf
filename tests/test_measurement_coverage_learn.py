import json
import re
from pathlib import Path

import jsonschema
import yaml

from profile_manager.dashboard import render_route_html
from profile_manager.data.measurement_coverage import measurement_coverage_payload


ROOT = Path(__file__).resolve().parents[1]
MAP = ROOT / "dwarf/measurement-coverage/v1.yaml"
SCHEMA = ROOT / "dwarf/spec/v1/measurement-coverage-map.schema.json"
SCRIPT = ROOT / "dwarf/dashboard/static/js/measurement-coverage.js"
CSS = ROOT / "dwarf/dashboard/static/css/base.css"


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


def test_route_has_four_views_filters_details_and_child_explanations():
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
    for control in (
        'type="search"',
        'id="measurement-coverage-implementation"',
        'id="measurement-coverage-source"',
        'id="measurement-coverage-status"',
    ):
        assert control in html
    assert '<fieldset class="measurement-coverage__views"' in html
    assert "<legend>View coverage by</legend>" in html
    assert "<details" in html and "<summary>Technical detail" in html
    assert 'role="status"' in html
    assert html.count("Child-friendly explanation:") >= 4
    assert "/learn/coverage" in html
    assert "/learn/threat-coverage" in html
    assert "/learn/measurements" in html


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
    assert ".measurement-coverage" in css
    assert "overflow-wrap: anywhere" in css
    assert "@media print" in css
    assert ".measurement-coverage__controls" in css
