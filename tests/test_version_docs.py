from pathlib import Path

from profile_manager import dashboard
from profile_manager.data.learn_api import ENDPOINTS, html_route_groups
from profile_manager.data.sub_nav import LEARN_SUB_NAV


ROOT = Path(__file__).resolve().parents[1]


def test_learn_versions_defines_every_status_policy_and_runtime_gate():
    html = dashboard.render_route_html("/learn/versions")

    assert html is not None
    for term in (
        "latest-confirmed",
        "latest-stable",
        "exact",
        "confirmed",
        "unknown",
        "incompatible",
        "blocked",
        "default",
        "node-reported version",
        "Amaru-only",
        "mixed",
        "fresh volumes",
    ):
        assert term.lower() in html.lower()
    assert "stable does not mean confirmed" in html.lower()
    assert "/operate/versions" in html


def test_version_docs_include_refresh_examples_and_claim_boundaries():
    source = (ROOT / "dwarf/docs/version-qualified-devnets.md").read_text(encoding="utf-8")

    assert "refresh_version_catalog.py" in source
    assert '"version_policy": "latest-confirmed"' in source
    assert '"version_policy": "exact"' in source
    assert "compatibility_pair" in source
    assert "relay/consumer" in source
    assert "does not prove" in source
    assert "Antithesis" in source
    assert "Check for new versions" in source
    assert "runtime-state overlay" in source
    assert "10.11.20260912" in source
    assert "10.7.1" in source


def test_qualification_status_records_bounded_search_and_resume_point():
    source = (ROOT / "dwarf/docs/version-qualification-status-2026-09-18.md").read_text(encoding="utf-8")

    assert "bounded newest-to-oldest search is complete" in source.lower()
    assert "20260918T032343Z-dwarf-qual-cardano-11-1-2-b1f28ed0" in source
    assert "20260918T091139Z-dwarf-qual-amaru-10-7-1-10-11-20260912-ae435ef5" in source
    assert "20260918T085655Z-dwarf-qual-mixed-10-7-1-10-11-0-cffd4b7c" in source
    assert "20260918T092619Z-dwarf-qual-mixed-11-1-2-10-11-20260912-89b35bbd" in source
    assert "resumption point" in source.lower()


def test_versions_route_is_in_navigation_and_api_reference():
    routes = {route for group in html_route_groups() for route in group["routes"]}

    assert any(item["url"] == "/learn/versions" for item in LEARN_SUB_NAV)
    assert "/learn/versions" in routes
    assert "/operate/versions" in routes
    assert any(endpoint["path"] == "/api/deploy/preview" for endpoint in ENDPOINTS)


def test_operator_docs_point_to_version_preflight_and_unknown_acknowledgement():
    operations = (ROOT / "OPERATIONS.md").read_text(encoding="utf-8")
    install = (ROOT / "INSTALL.md").read_text(encoding="utf-8")

    assert "/operate/versions" in operations
    assert "--acknowledge-unknown-version" in operations
    assert "latest-confirmed" in install
