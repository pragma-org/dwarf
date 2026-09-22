from pathlib import Path
import json

from profile_manager import dashboard
from profile_manager.data.operate_run_wizard import dispatch_run_resolve_request
from profile_manager.data.sub_nav import OPERATE_SUB_NAV


ROOT = Path(__file__).resolve().parents[1]
LANDING_TEMPLATE = ROOT / "dwarf/dashboard/templates/landing.j2"
STATUS_TEMPLATE = ROOT / "dwarf/dashboard/templates/operate/status.j2"
LANDING_SCRIPT = ROOT / "dwarf/dashboard/static/js/landing.js"
RUN_WIZARD_TEMPLATE = ROOT / "dwarf/dashboard/templates/operate/run_wizard.j2"
RUN_WIZARD_SCRIPT = ROOT / "dwarf/dashboard/static/js/run-wizard.js"
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


def test_run_route_exposes_server_catalog_and_default_selection():
    html = dashboard.render_route_html("/run", token="test-token")

    assert html is not None
    assert 'id="run-wizard"' in html
    assert 'id="run-wizard-bootstrap"' in html
    assert "runtime-substrate-honest-baseline-docker-mode-example-smoke" in html
    assert "Scenario" in html
    assert "Profile" in html
    assert "Node versions" in html
    assert "Measurements" in html
    assert "Primitives" in html


def test_run_catalog_exposes_structured_scenario_presentation_metadata():
    from profile_manager.data.operate_run_wizard import run_wizard_catalog

    catalog = run_wizard_catalog()
    assert len(catalog["scenarios"]) == 268
    required = {
        "short_title", "implementation", "implementation_key",
        "measurement_state", "proof_state", "recommended_demo",
        "measurement_profile", "deployment_profile", "target_version",
        "purpose", "limitation", "retained_run_id", "evidence_url",
        "filter_group",
    }
    assert all(required <= set(row) for row in catalog["scenarios"])
    assert {row["filter_group"] for row in catalog["scenarios"]} >= {
        "amaru", "cardano-node", "mixed", "other"
    }
    assert {row["proof_state"] for row in catalog["scenarios"]} == {
        "retained-proof", "supported-without-retained-proof", "unconfirmed-unsupported"
    }


def test_run_catalog_classifies_all_accepted_measurement_cards_and_recommendations():
    from profile_manager.data.operate_run_wizard import run_wizard_catalog

    rows = {row["id"]: row for row in run_wizard_catalog()["scenarios"]}
    accepted = {
        "client-example-cbor-decoding-amaru-d3a6dafc-regression",
        "client-example-cbor-decoding-cardano-patched",
        "client-example-plutus-vm-amaru-onchain-v2",
        "client-example-plutus-vm-cardano",
        "client-example-invalid-mini-protocol-amaru",
        "client-example-invalid-mini-protocol-cardano",
        "client-example-block-application-amaru-canonical-v3",
        "client-example-block-application-cardano-canonical-v2",
        "client-example-restart-recovery-sync-amaru",
        "client-example-restart-recovery-sync-cardano",
        "client-example-simple-transfer-amaru",
        "client-example-simple-transfer-cardano",
    }
    assert accepted <= set(rows)
    for scenario_id in accepted:
        row = rows[scenario_id]
        assert row["measurement_state"] == "measurement-enabled"
        assert row["proof_state"] == "retained-proof"
        assert row["retained_run_id"]
        assert row["evidence_url"] == f'/operate/runs/{row["retained_run_id"]}'
        assert row["measurement_profile"] in {
            "amaru-security-patched", "cardano-security-patched"
        }
    assert rows["client-example-block-application-amaru-canonical-v3"]["recommended_demo"] is True
    assert rows["client-example-cbor-decoding-cardano-patched"]["recommended_demo"] is True
    assert sum(row["recommended_demo"] for row in rows.values()) == 2


def test_run_picker_uses_accessible_visual_options_and_keeps_canonical_value():
    html = dashboard.render_route_html("/run", token="test-token")
    template = RUN_WIZARD_TEMPLATE.read_text(encoding="utf-8")
    script = RUN_WIZARD_SCRIPT.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")

    assert 'id="run-scenario" name="scenario_id"' in html
    assert 'data-scenario-picker' in html
    assert 'role="listbox"' in html
    assert 'role="option"' in html
    for group in ("recommended", "amaru", "cardano-node", "mixed", "other"):
        assert f'data-scenario-filter="{group}"' in html
    for label in (
        "Amaru", "Cardano-node", "Mixed", "Measurement-enabled",
        "Measurement proof", "Supported · no retained proof", "Unconfirmed / unsupported",
    ):
        assert label in html
    for class_name in (
        "scenario-picker__option--amaru", "scenario-picker__option--cardano",
        "scenario-picker__option--mixed", "scenario-picker__option--neutral",
        "scenario-picker__badge--proof",
    ):
        assert class_name in html or class_name in css
    assert 'data-scenario-summary' in template
    assert 'data-scenario-raw-id' in template
    assert "selectScenario" in script
    assert "scenarioSelect.value = scenarioId" in script
    assert "ArrowDown" in script and "ArrowUp" in script and "Enter" in script
    assert "full metrics" not in html.casefold()


def test_run_catalog_profile_options_expose_evidence_qualification(monkeypatch):
    from profile_manager.data import operate_run_wizard

    monkeypatch.setattr(
        operate_run_wizard,
        "profile_deployment_version_preview",
        lambda profile_id: {
            "status": "confirmed" if profile_id.endswith("latest-confirmed") else "unknown",
            "reason": "retained proof" if profile_id.endswith("latest-confirmed") else "not run",
        },
    )

    catalog = operate_run_wizard.run_wizard_catalog()
    confirmed = next(
        item for item in catalog["profiles"]
        if item["id"] == "profile-n-cardano-latest-confirmed"
    )
    unmarked = next(
        item for item in catalog["profiles"]
        if item["id"] != "profile-n-cardano-latest-confirmed"
    )

    assert confirmed["qualification"] == "confirmed"
    assert confirmed["status_label"] == "CONFIRMED"
    assert unmarked["qualification"] == "unmarked"
    assert unmarked["status_label"] == ""


def test_start_run_is_a_primary_and_operate_navigation_destination():
    operate_html = dashboard.render_route_html("/operate")
    run_html = dashboard.render_route_html("/run")

    assert any(
        item == {"slug": "run", "label": "Start run", "url": "/run"}
        for item in OPERATE_SUB_NAV
    )
    assert 'href="/run"' in operate_html
    assert ">Start run<" in operate_html
    assert 'href="/run" class="shell-nav__start active"' in run_html
    assert 'href="/operate" class="active"' not in run_html


def test_operator_runbook_explains_wizard_defaults_and_claim_boundary():
    html = dashboard.render_route_html("/learn/operator-runbook")

    for text in (
        "Start a local run",
        "latest-confirmed",
        "does not prove that a process is live",
        "Measurements observe the run",
        "Customize as a new scenario",
        "Exact command-line equivalent",
        "Plain meaning:",
    ):
        assert text in html


def test_run_resolve_api_returns_normalized_plan_without_a_token():
    status, content_type, body = dispatch_run_resolve_request(
        method="POST",
        path="/api/run/resolve",
        body=json.dumps(
            {"scenario_id": "edge-cases-cbor-tx-body-amaru"}
        ).encode("utf-8"),
    )

    payload = json.loads(body)
    assert status == 200
    assert content_type.startswith("application/json")
    assert payload["ok"] is True
    assert payload["plan"]["scenario"]["runtime"] == "library"
    assert payload["plan"]["readiness"]["mixed_topology_required"] is False
    assert payload["plan_digest"].startswith("sha256:")


def test_run_resolve_accepts_patched_amaru_nanosecond_v3_capabilities():
    status, content_type, body = dispatch_run_resolve_request(
        method="POST",
        path="/api/run/resolve",
        body=json.dumps(
            {"scenario_id": "client-example-block-application-amaru-canonical-v3"}
        ).encode("utf-8"),
    )

    payload = json.loads(body)
    assert status == 200
    assert content_type.startswith("application/json")
    assert payload["ok"] is True
    assert payload["plan"]["profile"]["id"] == "profile-y-amaru-block-application-nanoseconds-v3"
    assert payload["plan"]["measurements"]["profile"]["id"] == "amaru-security-patched"
    assert len(payload["plan"]["measurements"]["resolved"]) == 14


def test_run_resolve_api_returns_structured_field_errors():
    status, _content_type, body = dispatch_run_resolve_request(
        method="POST",
        path="/api/run/resolve",
        body=b'{"scenario_id":"../escape"}',
    )

    payload = json.loads(body)
    assert status == 400
    assert payload["ok"] is False
    assert payload["error"]["field"] == "scenario_id"


def test_run_wizard_has_ten_ordered_accessible_stages():
    html = dashboard.render_route_html("/run", token="test-token")

    expected = (
        "scenario",
        "target",
        "profile",
        "versions",
        "measurements",
        "primitives",
        "settings",
        "readiness",
        "review",
        "launch",
    )
    positions = [html.index(f'data-run-step="{step}"') for step in expected]
    assert positions == sorted(positions)
    assert len(positions) == 10
    assert 'aria-label="Run stages"' in html
    assert 'aria-live="polite"' in html
    assert 'aria-live="assertive"' in html
    assert "Required" in html
    assert "Optional" in html
    assert "Scenario default" in html
    assert "Customize as a new scenario" in html
    assert 'target="_blank" rel="noreferrer"' in html


def test_run_wizard_resolves_changes_without_embedding_catalog_logic_in_javascript():
    template = RUN_WIZARD_TEMPLATE.read_text(encoding="utf-8")
    script = RUN_WIZARD_SCRIPT.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")

    assert 'src="/static/js/run-wizard.js"' in template
    assert "run-wizard-bootstrap" in template
    assert "fetch(resolveUrl" in script
    assert "textContent" in script
    assert "replaceChildren" in script
    assert "innerHTML" not in script
    assert ".focus(" in script
    assert "aria-current" in script
    assert "runtime-substrate-honest-baseline" not in script
    assert "cardano-security-default" not in script
    assert ".run-wizard__layout" in css
    assert "grid-template-columns" in css
    assert "minmax(0" in css
    assert "overflow-x: clip" in css
    assert "@media (max-width: 900px) {\n  .run-wizard__layout" in css


def test_run_start_rejects_missing_token_before_creating_a_launch(tmp_path, monkeypatch):
    monkeypatch.setenv("ADA2_DWARF_LAUNCH_ROOT", str(tmp_path))

    status, content_type, body = dashboard.dispatch_run_start_request(
        method="POST",
        path="/api/run/start",
        body=b"{}",
        expected_token="secret",
    )

    assert status == 401
    assert content_type.startswith("application/json")
    assert not list(tmp_path.iterdir())


def test_run_start_rejects_a_stale_resolved_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("ADA2_DWARF_LAUNCH_ROOT", str(tmp_path))
    body = json.dumps(
        {
            "request": {"scenario_id": "edge-cases-cbor-tx-body-amaru"},
            "plan_digest": "sha256:" + "0" * 64,
        }
    ).encode("utf-8")

    status, _content_type, response = dashboard.dispatch_run_start_request(
        method="POST",
        path="/api/run/start?token=secret",
        body=body,
        expected_token="secret",
    )

    assert status == 409
    assert json.loads(response)["error"]["field"] == "plan_digest"


def test_library_run_start_streams_existing_engine_without_mixed_health(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ADA2_DWARF_LAUNCH_ROOT", str(tmp_path))
    request = {"scenario_id": "edge-cases-cbor-tx-body-amaru"}
    plan = __import__("profile_manager.run_plan", fromlist=["resolve_run_plan"]).resolve_run_plan(request)
    digest = __import__("profile_manager.run_plan", fromlist=["digest_run_plan"]).digest_run_plan(plan)
    calls = []

    status, content_type, stream = dashboard.dispatch_run_start_request(
        method="POST",
        path="/api/run/start?token=secret",
        body=json.dumps({"request": request, "plan_digest": digest}).encode("utf-8"),
        expected_token="secret",
        preflight_runner=lambda launch_id, resolved: calls.append((launch_id, resolved["readiness"])) or {"state": "ready", "checks": []},
        command_builder=lambda launch_id: ["dwarf-launch", launch_id],
        streamer=lambda command: iter([b"data: run_id: test-run\n\n", b'event: done\ndata: {"exit_code": 0}\n\n']),
    )

    output = b"".join(stream)
    assert status == 200
    assert content_type.startswith("text/event-stream")
    assert calls[0][1]["mixed_topology_required"] is False
    assert b"run_id: test-run" in output


def test_failed_mixed_readiness_stops_before_execution(tmp_path, monkeypatch):
    monkeypatch.setenv("ADA2_DWARF_LAUNCH_ROOT", str(tmp_path))
    request = {"scenario_id": "consensus-chainhold-upstream-differential"}
    run_plan = __import__("profile_manager.run_plan", fromlist=["resolve_run_plan", "digest_run_plan"])
    plan = run_plan.resolve_run_plan(request)
    digest = run_plan.digest_run_plan(plan)
    commands = []

    status, _content_type, stream = dashboard.dispatch_run_start_request(
        method="POST",
        path="/api/run/start?token=secret",
        body=json.dumps({"request": request, "plan_digest": digest}).encode("utf-8"),
        expected_token="secret",
        preflight_runner=lambda _launch_id, _plan: {
            "state": "blocked",
            "checks": [{"id": "topology:cardano_amaru", "passed": False}],
        },
        command_builder=lambda launch_id: commands.append(launch_id) or ["never"],
    )

    output = b"".join(stream)
    assert status == 200
    assert b"preflight_failed" in output
    assert b'"exit_code": 78' in output
    assert commands == []
    assert list(tmp_path.glob("launch-*/preflight.json"))


def test_run_start_rejects_concurrent_mutation(tmp_path, monkeypatch):
    monkeypatch.setenv("ADA2_DWARF_LAUNCH_ROOT", str(tmp_path))
    request = {"scenario_id": "edge-cases-cbor-tx-body-amaru"}
    run_plan = __import__("profile_manager.run_plan", fromlist=["resolve_run_plan", "digest_run_plan"])
    plan = run_plan.resolve_run_plan(request)
    digest = run_plan.digest_run_plan(plan)
    assert dashboard.try_acquire_mutating_lock()
    try:
        status, _content_type, body = dashboard.dispatch_run_start_request(
            method="POST",
            path="/api/run/start?token=secret",
            body=json.dumps({"request": request, "plan_digest": digest}).encode("utf-8"),
            expected_token="secret",
        )
    finally:
        dashboard.release_mutating_lock()

    assert status == 409
    assert "another mutating action" in json.loads(body)["error"]["message"]
