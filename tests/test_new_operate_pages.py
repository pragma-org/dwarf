from pathlib import Path

from profile_manager.config import DeploymentConfig, load_config
from profile_manager.data.operate_config_edit import config_edit_payload, handle_config_save
from profile_manager.views.operate_config_edit import render_operate_config_edit
from profile_manager.views.scenarios import render_operate_scenarios


ROOT = Path(__file__).resolve().parents[1]


def test_config_edit_renders_typed_defaults_when_config_is_absent(monkeypatch, tmp_path):
    config_path = tmp_path / "missing" / "config.json"
    monkeypatch.setenv("ADA2_PROFILE_MANAGER_CONFIG", str(config_path))

    payload = config_edit_payload()
    html = render_operate_config_edit(token="dwarf")

    assert payload["config_path"] == str(config_path)
    assert payload["fields"]
    assert all(field["key"] != "moog" for field in payload["fields"])
    assert "Edit deployment settings" in html
    assert str(config_path) in html
    auto = next(
        field
        for field in payload["fields"]
        if field["key"] == "auto_redeploy_unhealthy_topology"
    )
    assert auto["value"] == "false"
    assert auto["dangerous"] is True
    assert "Automatic topology repair is disabled by default" in html


def test_config_save_creates_first_config_when_config_is_absent(monkeypatch, tmp_path):
    config_path = tmp_path / "state" / "config.yaml"
    monkeypatch.setenv("ADA2_PROFILE_MANAGER_CONFIG", str(config_path))

    status, html = handle_config_save(b"host=127.0.0.1")

    assert status == 200
    assert "Settings saved" in html
    assert config_path.is_file()


def test_auto_redeploy_setting_round_trips_and_shows_enabled_warning(
    monkeypatch, tmp_path
):
    config_path = tmp_path / "state" / "config.yaml"
    monkeypatch.setenv("ADA2_PROFILE_MANAGER_CONFIG", str(config_path))
    assert DeploymentConfig.from_dict({}).auto_redeploy_unhealthy_topology is False

    status, _html = handle_config_save(b"auto_redeploy_unhealthy_topology=true")

    assert status == 200
    assert load_config().auto_redeploy_unhealthy_topology is True
    rendered = render_operate_config_edit(token="dwarf")
    assert "AUTO-REPAIR ENABLED" in rendered
    assert "at most one evidence-preserving redeploy" in rendered
    scenarios = render_operate_scenarios(token="dwarf")
    assert "AUTO-REPAIR ENABLED FOR ATTACHED SCENARIOS" in scenarios


def test_visual_audit_includes_new_routes_and_accessibility_checks():
    audit = (ROOT / "tools/dashboard_visual_audit.js").read_text(encoding="utf-8")

    for route in (
        "/operate/antithesis",
        "/operate/config/edit",
        "/operate/primitives/new",
        "/operate/profiles/new",
        "/operate/targets/new",
    ):
        assert repr(route) in audit
    assert "unlabeledControls" in audit
    assert "missingHeadings" in audit
    assert "brokenFragments" in audit


def test_antithesis_warning_wraps_as_one_readable_block():
    template = (ROOT / "dwarf/dashboard/templates/operate/antithesis.j2").read_text(
        encoding="utf-8"
    )

    assert ".at-warn{color:var(--crimson-glow);font-size:.78rem;display:block" in template
