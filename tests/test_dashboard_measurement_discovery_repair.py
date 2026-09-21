import json
import re
from pathlib import Path

from profile_manager.dashboard import render_route_html
from profile_manager.data.learn_api import html_route_groups


ROOT = Path(__file__).resolve().parents[1]
RUN_WIZARD_SCRIPT = ROOT / "dwarf/dashboard/static/js/run-wizard.js"


def test_learn_landing_has_one_authoritative_measurements_card():
    html = render_route_html("/learn")
    cards = re.findall(
        r'<a class="learn-tile" href="/learn/measurements">(.*?)</a>',
        html,
        re.DOTALL,
    )

    assert len(cards) == 1
    assert "Measurements" in cards[0]
    assert "30 measurement definitions" in cards[0]
    assert "4 profiles" in cards[0]


def test_measurement_guide_defines_sources_types_and_claim_boundaries():
    html = render_route_html("/learn/measurements")

    for source in ("Stock", "External", "Patched", "Reserved"):
        assert f">{source}<" in html
    for metric_type in ("counter", "gauge", "histogram", "distribution"):
        assert metric_type in html.lower()
    assert "Catalogued is not exercised" in html
    assert "The five frozen cards are complete" in html
    assert "/learn/coverage" in html


def test_operate_has_one_clear_measurements_hub_with_existing_destinations():
    html = render_route_html("/operate")
    hubs = re.findall(
        r'<article class="tile[^\"]*" data-measurements-hub>(.*?)</article>',
        html,
        re.DOTALL,
    )

    assert len(hubs) == 1
    hub = hubs[0]
    assert "Measurements" in hub
    assert "/operate/measurements" in hub
    assert "/operate/measurement-profiles" in hub
    assert "/operate/runs" in hub
    assert "Profiles select taps" in hub
    assert "run reports contain results" in hub


def test_run_wizard_keeps_all_ten_semantic_stages_visible():
    html = render_route_html("/run")
    stages = re.findall(
        r'<section id="run-stage-[^"]+"[^>]*>\s*<header><span>(\d\d)</span>',
        html,
    )
    script = RUN_WIZARD_SCRIPT.read_text(encoding="utf-8")

    assert stages == [f"{value:02d}" for value in range(1, 11)]
    assert "[data-run-step=\"versions\"].hidden" not in script
    assert "[data-run-step=\"measurements\"].hidden" not in script
    assert "setConditionalState" not in script


def test_coverage_separates_inventory_from_accepted_runtime_evidence():
    html = render_route_html("/learn/coverage")

    assert "Catalog inventory is not runtime proof" in html
    assert "Five-card accepted runtime evidence" in html
    assert "all collectors configured" in html
    assert "all metrics exercised" in html
    for card_id in ("01", "02", "03", "04", "05"):
        assert f'data-evidence-card="{card_id}"' in html
    for run_id in (
        "20260920T235440Z-050046a4",
        "20260920T132629Z-ea000d37",
        "20260921T013953Z-565b77c3",
        "20260920T135958Z-362eedc7",
        "20260920T072858Z-2cc3bb0c",
        "20260920T073447Z-ab81bfb7",
        "20260921T035546Z-9747122c",
        "20260921T021935Z-3b58eafc",
        "20260921T045619Z-15e864a0",
        "20260921T045807Z-8e2bbb0e",
    ):
        assert run_id in html


def test_threat_coverage_labels_mapping_and_runtime_evidence_separately():
    html = render_route_html("/learn/threat-coverage")
    data = json.loads(re.search(r"const DATA = (\{.*\});\nconst TYPES", html).group(1))

    assert "Mapped catalog coverage is not runtime proof" in html
    assert "Five-card accepted runtime evidence" in html
    assert "Threats mapped" in html
    assert "Risks mapped" in html
    assert len(data["five_card_evidence"]) == 5
    assert {card["state"] for card in data["five_card_evidence"]} == {"accepted"}
    card_three = next(card for card in data["five_card_evidence"] if card["id"] == "03")
    assert all(leg["run_url"] is None for leg in card_three["legs"])
    assert ".legend .pill{white-space:normal;max-width:100%}" in html
    assert data["meta"]["tm_covered"] == len(
        [row for row in data["threats"] if row.get("scenarios")]
    )
    assert data["meta"]["rr_covered"] == len(
        [row for row in data["risks"] if row.get("scenarios")]
    )

def test_measurement_destinations_are_in_the_existing_route_inventory():
    routes = {route for group in html_route_groups() for route in group["routes"]}

    assert "/learn/measurements" in routes
    assert "/learn/measurement-coverage" in routes
    assert "/operate/measurements" in routes
    assert "/operate/measurement-profiles" in routes
    assert "/operate/runs" in routes
