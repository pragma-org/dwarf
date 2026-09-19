from pathlib import Path

from profile_manager import dashboard


ROOT = Path(__file__).resolve().parents[1]
LANDING_TEMPLATE = ROOT / "dwarf/dashboard/templates/landing.j2"
STATUS_TEMPLATE = ROOT / "dwarf/dashboard/templates/operate/status.j2"
LANDING_SCRIPT = ROOT / "dwarf/dashboard/static/js/landing.js"
CSS = ROOT / "dwarf/dashboard/static/css/base.css"


def test_root_is_the_product_landing_page(monkeypatch):
    monkeypatch.setattr(
        "profile_manager.views.landing.topology_health_snapshot",
        lambda: {
            "state": "healthy",
            "checked_at": "2026-09-19T12:00:00Z",
            "cached": False,
        },
    )
    monkeypatch.setattr(
        "profile_manager.views.landing.version_default_summary",
        lambda: {
            "cardano": "10.7.1",
            "amaru": "10.11.0",
            "mixed_cardano": "10.7.1",
            "mixed_amaru": "10.11.0",
        },
    )
    monkeypatch.setattr(
        "profile_manager.views.landing.control_shim_enabled", lambda: True
    )

    html = dashboard.render_route_html("/")

    assert html is not None
    assert 'data-page="landing"' in html
    assert 'aria-label="DWARF"' in html
    assert 'src="/static/dwarf-logo.png"' in html
    assert 'href="/run"' in html
    assert ">Enter<" in html
    assert 'href="https://pragma.io/"' in html
    assert "Owned by PRAGMA" in html
    assert 'href="https://gainsec.com"' in html
    assert 'Created by Jon &quot;GainSec&quot; Gaines' in html
    assert 'href="https://github.com/pragma-org/dwarf"' in html
    assert "Open Source" in html
    for label in ("Framework", "Control channel", "Active topology", "Node versions"):
        assert label in html
    assert "Browser mutations are token-gated" not in html


def test_landing_motion_is_accessible_and_status_is_freshened():
    template = LANDING_TEMPLATE.read_text(encoding="utf-8")
    script = LANDING_SCRIPT.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")

    assert 'src="/static/js/landing.js"' in template
    assert "/healthz" in template
    assert "/api/topology/health?fresh=1" in template
    assert "prefers-reduced-motion" in script
    assert "aria-label" in template
    assert "textContent" in script
    assert "innerHTML" not in script
    assert ".product-landing" in css
    assert "overflow-x: clip" in css
    assert "@media (max-width: 640px)" in css


def test_status_keeps_diagnostics_but_no_longer_duplicates_landing_artwork():
    template = STATUS_TEMPLATE.read_text(encoding="utf-8")

    assert '<section class="flask-stage"' not in template
    assert 'src="/static/dwarf-logo.png"' not in template
    assert 'id="topology-health-panel"' in template
    assert 'data-topology-action="redeploy"' in template

