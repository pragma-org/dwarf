from pathlib import Path
from http.server import ThreadingHTTPServer
import threading
from urllib.error import HTTPError
from urllib.request import urlopen

from profile_manager.data import schedule_store
from profile_manager import dashboard
from profile_manager.views import operate_schedule


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "tools" / "dashboard_visual_audit.js"
CSS = ROOT / "dwarf" / "dashboard" / "static" / "css" / "base.css"
TOKENS = ROOT / "dwarf" / "dashboard" / "static" / "css" / "tokens.css"
SCHEDULE_TEMPLATE = ROOT / "dwarf" / "dashboard" / "templates" / "operate" / "schedule.j2"
RUNS_TEMPLATE = ROOT / "dwarf" / "dashboard" / "templates" / "operate" / "runs.j2"
RUN_ROW_TEMPLATE = (
    ROOT
    / "dwarf"
    / "dashboard"
    / "templates"
    / "operate"
    / "_partials"
    / "_run_row.j2"
)
OVERVIEW = ROOT / "dwarf" / "dashboard" / "static" / "overview.html"
CONSENSUS = ROOT / "dwarf" / "dashboard" / "static" / "consensus-differential.html"
THREAT_COVERAGE = ROOT / "dwarf" / "profile_manager" / "data" / "threat_risk_coverage.html"
PROFILE_ENTRY = (
    ROOT
    / "dwarf"
    / "dashboard"
    / "templates"
    / "operate"
    / "_partials"
    / "_profile_entry.j2"
)
DEFINITION_DETAIL_VIEW = (
    ROOT / "dwarf" / "profile_manager" / "views" / "operate_definition.py"
)
OPERATIONS = ROOT / "OPERATIONS.md"
LEARN_DOCS = ROOT / "dwarf" / "profile_manager" / "data" / "learn_docs.py"


def test_author_styles_preserve_hidden_attribute_semantics():
    source = CSS.read_text(encoding="utf-8")

    assert "[hidden] { display: none !important; }" in source


def test_all_dashboard_tables_receive_accessible_responsive_scroll_regions():
    base = (ROOT / "dwarf" / "dashboard" / "templates" / "_base.j2").read_text()
    script_path = ROOT / "dwarf" / "dashboard" / "static" / "js" / "responsive-tables.js"

    assert 'src="/static/js/responsive-tables.js"' in base
    assert script_path.exists()
    script = script_path.read_text()
    assert ".shell-main table:not(.no-responsive-table)" in script
    assert "responsive-table" in script
    assert "Scrollable data table" in script

    source = CSS.read_text(encoding="utf-8")
    assert ".responsive-table > table:not(.no-responsive-table)" in source
    assert "width: max-content" in source


def test_visual_audit_is_read_only_and_checks_both_viewports():
    source = AUDIT.read_text(encoding="utf-8")

    assert "1440" in source
    assert "390" in source
    assert "scrollWidth" in source
    assert "rgb(255, 255, 255)" in source
    assert "naturalWidth" in source
    assert "pageerror" in source
    assert "waitForLoadState('load'" in source
    assert "reducedMotion: 'reduce'" in source
    assert "form.submit" not in source
    assert "page.locator('button[type=\"submit\"]')" not in source
    assert "token=" not in source
    assert "page.request.post" in source
    assert "expectedStatus: 403" in source


def test_visual_audit_discovers_definition_detail_and_edit_routes():
    source = AUDIT.read_text(encoding="utf-8")

    for catalog in ("scenarios", "targets", "profiles"):
        assert f"'/operate/{catalog}'" in source
    assert "definitionDetail" in source
    assert "definitionEdit" in source
    assert "scenarios|targets|profiles" in source
    assert "item !== `${source}/new`" in source
    assert "'/operate/versions'" in source
    assert "'/learn/versions'" in source


def test_visual_audit_checks_component_geometry_and_mobile_touch_targets():
    source = AUDIT.read_text(encoding="utf-8")

    for signal in (
        "escapedEditorControls",
        "undersizedMobileControls",
        "stretchedSingleLineControls",
        "profileRowOverflow",
    ):
        assert signal in source


def test_shared_css_themes_native_controls_and_contains_wide_content():
    source = CSS.read_text(encoding="utf-8")

    for selector in (
        '.shell-main input:not([type="checkbox"]):not([type="radio"])',
        ".shell-main select",
        ".shell-main textarea",
        ".shell-main button",
        ".responsive-table",
    ):
        assert selector in source
    assert "overflow-wrap: anywhere" in source
    assert "overflow-x: auto" in source
    assert "overflow-x: clip" in source
    assert "@media (max-width: 640px)" in source


def test_topology_health_panel_has_responsive_product_chrome():
    template = (
        ROOT / "dwarf/dashboard/templates/operate/status.j2"
    ).read_text(encoding="utf-8")
    script = (
        ROOT / "dwarf/dashboard/static/js/topology-health.js"
    ).read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")

    assert 'id="topology-health-panel"' in template
    assert 'aria-live="polite"' in template
    assert 'data-topology-action="recheck"' in template
    assert 'data-topology-action="redeploy"' in template
    assert 'id="topology-redeploy-dialog"' in template
    assert 'data-required-confirmation="REDEPLOY cardano_amaru"' in template
    assert "cardano_amaru_relay_bootstrap_control" in template
    assert "a1-state" in template
    assert "amaru-consumer-state" in template
    assert "Download diagnostic evidence" in template
    assert "textContent" in script
    assert "innerHTML" not in script
    assert "fresh=1" in script
    assert "REDEPLOY cardano_amaru" in script
    assert "response.body.getReader" in script
    assert ".topology-health__grid" in css
    assert ".topology-health__nodes" in css
    assert ".topology-redeploy" in css
    assert "overflow-y: auto" in css
    assert "focus({preventScroll: true})" in script
    assert "dialog.scrollTop = 0" in script


def test_mixed_topology_is_the_authoritative_substrate_health_surface():
    template = (
        ROOT / "dwarf/dashboard/templates/operate/status.j2"
    ).read_text(encoding="utf-8")
    script = (
        ROOT / "dwarf/dashboard/static/js/topology-health.js"
    ).read_text(encoding="utf-8")

    flask = template.index('<section class="flask-stage"')
    mixed_health = template.index('id="topology-health-panel"')
    summary = template.index('<section class="status-summary"')
    recovery_action = template.index('data-topology-action="redeploy"')
    diagnostics = template.index('class="topology-health__grid"')
    legacy_substrate = template.index('<span class="eyebrow">Substrate</span>')

    assert flask < mixed_health < summary
    assert recovery_action < diagnostics
    assert "Active substrate · Cardano + Amaru" in template
    assert "Mixed topology health" in template
    assert ">Fresh redeploy&hellip;</button>" in template
    assert "flask-logo-img{% if _no_substrate and not shim_enabled %}" in template
    assert "flask.dataset.state = effectiveState;" in script
    assert "{% if not shim_enabled %}" in template[summary:legacy_substrate]


def test_definition_builders_and_profile_catalog_use_wide_reading_layout():
    for path in (
        "/operate/scenarios/new",
        "/operate/targets/new",
        "/operate/profiles/new",
        "/operate/profiles",
    ):
        html = dashboard.render_route_html(path)

        assert html is not None
        assert '<body data-density="reading" data-layout="wide">' in html


def test_definition_field_grid_preserves_natural_control_height():
    source = CSS.read_text(encoding="utf-8")

    fields_rule = source.split(".definition-editor__fields {", 1)[1].split("}", 1)[0]
    field_rule = source.split(".definition-field,", 1)[1].split("}", 1)[0]

    assert "align-items: start" in fields_rule
    assert "align-content: start" in field_rule


def test_definition_editor_chrome_contains_long_values_and_uses_product_controls():
    source = CSS.read_text(encoding="utf-8")

    assert ".definition-editor select {" in source
    assert '.definition-editor input[type="checkbox"] {' in source
    assert ".definition-editor__tabs button + button" in source

    tabs_rule = source.split(".definition-editor__tabs {", 1)[1].split("}", 1)[0]
    select_rule = source.split(".definition-editor select {", 1)[1].split("}", 1)[0]
    checkbox_rule = source.split(
        '.definition-editor input[type="checkbox"] {', 1
    )[1].split("}", 1)[0]

    assert "gap: 0" in tabs_rule
    assert "padding-inline-end" in select_rule
    assert "text-overflow: ellipsis" in select_rule
    assert "appearance: none" in checkbox_rule
    assert "inline-size: 20px" in checkbox_rule
    assert ".definition-editor__report" in source
    assert "overflow-wrap: anywhere" in source.split(
        ".definition-editor__report {", 1
    )[1].split("}", 1)[0]


def test_definition_catalog_rows_are_compact_labeled_and_details_are_wide():
    source = CSS.read_text(encoding="utf-8")
    entry = PROFILE_ENTRY.read_text(encoding="utf-8")
    detail = DEFINITION_DETAIL_VIEW.read_text(encoding="utf-8")

    row_rule = source.split(".profile-catalog__row {")[2].split("}", 1)[0]
    identity_rule = source.split(".profile-catalog__identity {", 1)[1].split("}", 1)[0]

    assert "overflow: hidden" in row_rule
    assert "align-items: start" in row_rule
    assert "text-overflow: ellipsis" in identity_rule
    for label in ("Type ", "Nodes ", "Network ", "Peer sharing "):
        assert f">{label}</span>" in entry
    assert 'layout="wide"' in detail


def test_definition_workflows_use_the_full_mobile_well_and_touch_sized_controls():
    source = CSS.read_text(encoding="utf-8")
    tokens = TOKENS.read_text(encoding="utf-8")

    assert "--space-5:  20px" in tokens
    assert "@media (max-width: 720px)" in source
    mobile = source.rsplit("@media (max-width: 720px)", 1)[1]

    assert ".shell-header" in mobile
    assert ".shell-main" in mobile
    assert "padding-inline: var(--space-5)" in mobile
    assert ".definition-editor__fields" in mobile
    assert ".definition-editor__tabs button" in mobile
    assert "min-height: 44px" in mobile


def test_visual_audit_exercises_builder_help_and_selected_option_descriptions():
    source = AUDIT.read_text(encoding="utf-8")

    assert "data-field-help" in source
    assert "data-selected-option-help" in source
    assert "builderHelpFailures" in source


def test_visual_audit_exercises_safe_catalog_interactions_and_failure_states():
    source = AUDIT.read_text(encoding="utf-8")

    for signal in (
        "EXPECTED_STATUS_ROUTES",
        "exerciseReadOnlyInteractions",
        "verifyDownloads",
        "verifyTokenGates",
        "interactionChecks",
        "downloadChecks",
        "gateChecks",
        "contextualLinkFailures",
        "data-editor-tab=\"raw\"",
        "__dwarf_visual_audit_no_match__",
        "visual-audit-missing",
        "content-disposition",
    ):
        assert signal in source
    for catalog in (
        "scenarios",
        "targets",
        "profiles",
        "primitives",
        "profile-templates",
        "testcases",
        "testcase-buckets",
        "corpora",
        "grammars",
        "risk-packages",
        "plugins",
    ):
        assert f"/operate/{catalog}/visual-audit-missing" in source


def test_live_unknown_asset_ids_render_themed_controlled_404_pages():
    handler = dashboard.serve_dashboard_handler_factory("dwarf")
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        for catalog in (
            "primitives",
            "profile-templates",
            "testcases",
            "testcase-buckets",
            "corpora",
            "grammars",
            "risk-packages",
            "plugins",
        ):
            try:
                urlopen(
                    f"http://127.0.0.1:{port}/operate/{catalog}/missing-record"
                )
                raise AssertionError(f"{catalog} missing record unexpectedly returned 200")
            except HTTPError as exc:
                body = exc.read().decode("utf-8")
                assert exc.code == 404
                assert exc.headers.get_content_type() == "text/html"
                assert "<h1>" in body and "not found</h1>" in body
                assert f'href="/operate/{catalog}"' in body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_operator_docs_distinguish_automatic_and_optional_evidence_layers():
    operations = OPERATIONS.read_text(encoding="utf-8")
    glossary = LEARN_DOCS.read_text(encoding="utf-8")

    assert "SARIF is generated automatically" in operations
    assert "Signed attestation remains optional" in operations
    assert "missing retained predecessor" in operations
    assert "automatically for every completed run" in glossary


def test_schedule_ui_uses_product_facing_resolved_store_path():
    source = SCHEDULE_TEMPLATE.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")

    assert "$ADA2_DWARF_STATE_DIR" not in source
    assert "DWARF state store" in source
    assert "{{ store_path }}" in source
    assert ".schedule-form" in css
    assert ".schedule-btn" in css


def test_schedule_view_passes_the_resolved_store_path(monkeypatch, tmp_path):
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(tmp_path))
    captured = {}

    monkeypatch.setattr(schedule_store, "list_entries", lambda: [])
    monkeypatch.setattr(operate_schedule, "_scenario_options", lambda: [])
    monkeypatch.setattr(
        operate_schedule,
        "render",
        lambda template, **context: captured.update(template=template, **context) or "rendered",
    )

    assert schedule_store.store_path() == tmp_path / "schedule.json"
    assert operate_schedule.render_operate_schedule() == "rendered"
    assert captured["store_path"] == str(tmp_path / "schedule.json")


def test_legacy_static_pages_obey_the_visual_contract():
    overview = OVERVIEW.read_text(encoding="utf-8")
    consensus = CONSENSUS.read_text(encoding="utf-8")
    threat_coverage = THREAT_COVERAGE.read_text(encoding="utf-8")

    assert "api.koios.rest" not in overview
    assert "snapshot · live figures: /learn/attack-cost" in overview
    assert "--obsidian-0" in consensus
    assert "background:var(--obsidian-0)" in consensus
    assert "table{display:block;overflow-x:auto" in consensus
    assert "table{display:block;overflow-x:auto" in threat_coverage


def test_dense_mobile_tables_scroll_instead_of_collapsing_columns():
    css = CSS.read_text(encoding="utf-8")
    runs = RUNS_TEMPLATE.read_text(encoding="utf-8")
    consensus = CONSENSUS.read_text(encoding="utf-8")
    threat_coverage = THREAT_COVERAGE.read_text(encoding="utf-8")

    assert '<div class="responsive-table runs-table-wrap">' in runs
    assert ".runs-table-wrap .runs-table" in css
    assert consensus.count('<div class="table-scroll">') == 2
    assert threat_coverage.count('<div class="table-scroll">') == 2


def test_runs_table_names_execution_and_workload_columns():
    runs = RUNS_TEMPLATE.read_text(encoding="utf-8")
    row = RUN_ROW_TEMPLATE.read_text(encoding="utf-8")

    assert "<th>Execution</th>" in runs
    assert "<th>Workload</th>" in runs
    assert "<th>Evidence</th>" in runs
    assert "<th>Profile</th>" not in runs
    assert "row.execution" in row
    assert "row.workload_label" in row
    assert "row.evidence_tags" in row


def test_browser_form_patterns_escape_hyphens_for_unicode_set_validation():
    templates = [
        Path("dwarf/dashboard/templates/operate/corpus_detail.j2"),
        Path("dwarf/dashboard/templates/operate/grammar_detail.j2"),
        Path("dwarf/dashboard/templates/operate/run_compare_help.j2"),
        Path("dwarf/dashboard/templates/operate/scenarios_new.j2"),
    ]

    for template in templates:
        source = template.read_text(encoding="utf-8")
        assert "._-" not in source, f"unescaped pattern hyphen in {template}"
        assert "._\\-" in source, f"escaped pattern hyphen missing in {template}"


def test_run_banner_actions_stack_without_mobile_overflow():
    css = CSS.read_text(encoding="utf-8")

    assert ".run-status-banner__cta {" in css
    assert ".run-status-banner__cta .cta {" in css
    assert "overflow-wrap: anywhere" in css
