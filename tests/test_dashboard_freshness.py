import json
import re
import threading
from pathlib import Path

from profile_manager.data.coverage import scenario_census
from profile_manager.data.learn_api import html_route_groups
from profile_manager.dashboard import dashboard_serve_text, dispatch_api_request, render_dashboard_html
from profile_manager.data import operate_topology_health
from profile_manager.data import health as health_data
from profile_manager.data.health import active_profile_ids
from profile_manager.profiles import active_profile_command
from profile_manager.inspect import inspect_health_command
from profile_manager.data.operate_status import active_profile
from profile_manager.data.scenarios import (
    _list_packaged_scenarios_for_compare,
    invalidate_scenario_cache,
)
from profile_manager.fuzz import fuzz_evidence_root
from profile_manager.smoke import smoke_evidence_root
from profile_manager.views.learn_overview import render_learn_overview
from profile_manager.views.learn import render_learn_landing
from profile_manager.views.learn_cli import render_learn_cli
from profile_manager.views.learn_docs import render_learn_faq, render_learn_troubleshooting
from profile_manager.views.learn_examples import render_learn_examples
from profile_manager.views.status import render_learn_status
from profile_manager.views.threat_coverage import render_learn_threat_coverage
from profile_manager.views.operate_status import render_operate_status


ROOT = Path(__file__).resolve().parents[1]


def _documented_html_routes():
    return {route for group in html_route_groups() for route in group["routes"]}


def test_api_reference_covers_current_browser_surface():
    required = {
        "/operate/audit",
        "/operate/antithesis",
        "/operate/config/edit",
        "/operate/crashes",
        "/operate/primitives/new",
        "/operate/profiles/new",
        "/operate/schedule",
        "/operate/scenarios/<id>",
        "/operate/scenarios/<id>/edit",
        "/operate/profiles/<id>",
        "/operate/profiles/<id>/edit",
        "/operate/targets/<id>",
        "/operate/targets/<id>/edit",
        "/operate/targets/new",
        "/learn/attack-cost",
        "/learn/consensus",
        "/learn/developer-onboarding",
        "/learn/operator-runbook",
        "/learn/overview",
        "/learn/plugin-authoring",
        "/learn/status",
        "/learn/threat-coverage",
    }

    assert required <= _documented_html_routes()


def test_status_renders_deployed_revision_and_live_inventory(monkeypatch):
    monkeypatch.setenv("DWARF_SOURCE_REVISION", "0123456789abcdef")
    total = sum(
        cell["count"]
        for (row, column), cell in scenario_census()["cells"].items()
        if column == "__total"
    )
    primitive_total = len(
        json.loads((ROOT / "dwarf/primitives/registry.json").read_text(encoding="utf-8"))["primitives"]
    )

    html = render_learn_status()

    assert "0123456789abcdef" in html
    assert f"{total} scenarios" in html
    assert f"{primitive_total} primitives" in html
    assert "Last 0 feature commits" not in html


def test_threat_coverage_reconciles_to_runtime_scenario_catalog():
    html = render_learn_threat_coverage()
    match = re.search(r"const DATA = (\{.*\});\nconst TYPES", html)
    assert match is not None
    data = json.loads(match.group(1))
    expected = sum(
        cell["count"]
        for (row, column), cell in scenario_census()["cells"].items()
        if column == "__total"
    )

    assert data["meta"]["n_scen"] == expected
    assert len(data["scenarios"]) == expected


def test_threat_coverage_maps_new_security_scenarios_without_misclassifying_control():
    html = render_learn_threat_coverage()
    match = re.search(r"const DATA = (\{.*\});\nconst TYPES", html)
    assert match is not None
    data = json.loads(match.group(1))
    inventory = {item["id"] for item in data["scenarios"]}
    threat_scenarios = {
        row["id"]: {item["id"] for item in row.get("scenarios", [])}
        for row in data["threats"]
    }
    risk_scenarios = {
        row["id"]: {item["id"] for item in row.get("scenarios", [])}
        for row in data["risks"]
    }

    mini = "cardano-amaru-miniprotocol-security-local"
    kes = "cardano-amaru-kes-security-local"
    control = "cardano-amaru-relay-bootstrap-control-local"
    assert {mini, kes, control} <= inventory
    assert mini in threat_scenarios["TM-001"]
    assert mini in threat_scenarios["TM-015"]
    assert mini in risk_scenarios["RR-001"]
    assert mini in risk_scenarios["RR-015"]
    assert kes in threat_scenarios["TM-013"]
    assert kes in risk_scenarios["RR-013"]
    assert all(control not in scenario_ids for scenario_ids in threat_scenarios.values())
    assert all(control not in scenario_ids for scenario_ids in risk_scenarios.values())
    assert "Every DWARF test scenario mapped" not in html


def test_runs_api_honors_documented_limit_query(tmp_path):
    runs_dir = tmp_path / "runs"
    for index in range(3):
        run_dir = runs_dir / f"run-{index}"
        run_dir.mkdir(parents=True)
        (run_dir / "manifest.json").write_text(
            json.dumps({"ended_at": f"2026-09-09T00:00:0{index}Z"}),
            encoding="utf-8",
        )

    status, content_type, body = dispatch_api_request(
        "/api/runs?limit=1", runs_dir=runs_dir, bundles_dir=tmp_path / "bundles"
    )

    assert status == 200
    assert content_type.startswith("application/json")
    assert len(json.loads(body)["recent_runs"]) == 1


def test_runs_api_rejects_invalid_limit_query(tmp_path):
    for value in ("bad", "0", "201"):
        status, content_type, body = dispatch_api_request(
            f"/api/runs?limit={value}",
            runs_dir=tmp_path / "runs",
            bundles_dir=tmp_path / "bundles",
        )
        assert status == 400
        assert content_type.startswith("application/json")
        assert "limit" in json.loads(body)["error"]


def test_topology_health_service_shares_one_inflight_probe():
    operate_topology_health.reset_topology_health_state()
    started = threading.Event()
    release = threading.Event()
    calls = []

    def probe():
        calls.append("probe")
        started.set()
        assert release.wait(timeout=2)
        return {
            "state": "healthy",
            "checked_at": "2026-09-15T00:00:00Z",
            "reason_code": "all_mixed_readiness_gates_passed",
        }

    first = operate_topology_health.request_topology_health_check(probe=probe)
    assert started.wait(timeout=2)
    second = operate_topology_health.request_topology_health_check(probe=probe)

    assert first["state"] == "checking"
    assert second["state"] == "checking"
    assert calls == ["probe"]

    release.set()
    completed = operate_topology_health.wait_for_topology_health(timeout=2)
    assert completed["state"] == "healthy"
    assert calls == ["probe"]


def test_topology_health_result_survives_dashboard_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(tmp_path))
    operate_topology_health.reset_topology_health_state()

    operate_topology_health.request_topology_health_check(
        probe=lambda: {
            "state": "healthy",
            "checked_at": "2026-09-19T12:00:00Z",
            "reason_code": "all_mixed_readiness_gates_passed",
        }
    )
    completed = operate_topology_health.wait_for_topology_health(timeout=2)

    assert completed["state"] == "healthy"
    evidence_path = tmp_path / "topology-health" / "dashboard-latest.json"
    assert json.loads(evidence_path.read_text(encoding="utf-8"))["state"] == "healthy"

    # Simulate a process restart by clearing only process-local state.
    operate_topology_health.reset_topology_health_state()
    restored = operate_topology_health.topology_health_snapshot()

    assert restored["state"] == "healthy"
    assert restored["observation_source"] == "persisted"
    assert restored["cached"] is True
    assert restored["age_seconds"] >= 0


def test_topology_health_ignores_invalid_persisted_result(tmp_path, monkeypatch):
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(tmp_path))
    evidence_path = tmp_path / "topology-health" / "dashboard-latest.json"
    evidence_path.parent.mkdir(parents=True)
    evidence_path.write_text("not json", encoding="utf-8")
    operate_topology_health.reset_topology_health_state()

    restored = operate_topology_health.topology_health_snapshot()

    assert restored["state"] == "idle"
    assert restored["previous"] is None


def test_topology_health_timeout_is_unknown_not_cached_success():
    operate_topology_health.reset_topology_health_state()

    result = operate_topology_health.normalize_probe_failure(
        TimeoutError("probe exceeded deadline"),
        previous={"state": "healthy", "checked_at": "2026-09-14T00:00:00Z"},
    )

    assert result["state"] == "unknown"
    assert result["reason_code"] == "topology_probe_failed"
    assert result["previous"]["state"] == "healthy"
    assert result["previous_is_current"] is False


def test_active_profile_discovery_uses_managed_profile_identity():
    stdout = "\n".join([
        "DWARF_NODE docker profile-v-cardano-measurement-nanoseconds-v2 node1 Up 3 hours",
        "DWARF_NODE docker profile-v-cardano-measurement-nanoseconds-v2 node2 Up 3 hours",
        "DWARF_NODE docker profile-v-cardano-measurement-nanoseconds-v2 node3 Up 3 hours",
    ])

    assert active_profile_ids(stdout) == ["profile-v-cardano-measurement-nanoseconds-v2"]
    assert '.Label "ada2.profile"' in active_profile_command()


def test_active_profile_discovery_preserves_no_active_and_ambiguous_states():
    assert active_profile_ids("") == []
    assert active_profile_ids(
        "DWARF_NODE docker profile-a node1 Up\n"
        "DWARF_NODE docker profile-b node1 Up\n"
    ) == ["profile-a", "profile-b"]


def test_docker_profile_health_queries_tip_inside_exact_container_first():
    command = inspect_health_command(
        "/opt/dwarf/cardano-profiles/profile-v-cardano-measurement-nanoseconds-v2"
    )

    assert command.index('docker inspect "$container"') < command.index(
        'if [ -S "$socket" ]'
    )


def test_status_does_not_label_first_catalog_entry_as_active_when_none_is_running():
    payload = {
        "live": {"enabled": False, "state": "no_active"},
        "profiles": [{"id": "profile-a"}, {"id": "profile-b"}],
    }

    assert active_profile(payload) == {}


def test_current_topology_health_requires_exact_readiness_and_progress():
    first = {
        "enabled": True,
        "profile_id": "profile-v-cardano-measurement-nanoseconds-v2",
        "health": {"returncode": 0, "parsed": {
            "cardano_node_processes": "3", "socket_count": "3",
            "listener_count": "3", "loopback_only": "true",
            "tip_block": 2400, "tip_slot": 50000, "sync_progress": "100.00",
        }},
        "expected_nodes": 3,
    }
    second = json.loads(json.dumps(first))
    second["health"]["parsed"].update({"tip_block": 2402, "tip_slot": 50002})

    result = operate_topology_health.classify_current_profile_health(first, second)

    assert result["state"] == "healthy"
    assert result["profile_id"] == "profile-v-cardano-measurement-nanoseconds-v2"
    assert result["reason_code"] == "active_profile_ready_and_progressing"
    assert result["redeploy_supported"] is False


def test_current_topology_health_uses_exact_managed_topology_probe_result():
    sample = {
        "enabled": True,
        "profile_id": "profile-w-amaru-measurement-plutus-v2",
        "topology_result": {
            "state": "healthy",
            "reason_code": "all_mixed_readiness_gates_passed",
            "checked_at": "2026-09-22T14:20:00Z",
            "reference_tip": {"slot": 2000, "block": 400, "syncProgress": "100.00"},
            "consumer_lag_slots": 0,
            "sample_count": 2,
            "evidence_path": "/opt/dwarf/cardano-profiles/profile-w/evidence/dashboard-health.json",
        },
    }

    result = operate_topology_health.classify_current_profile_health(sample, None)

    assert result["state"] == "healthy"
    assert result["reason_code"] == "all_mixed_readiness_gates_passed"
    assert result["profile_id"] == "profile-w-amaru-measurement-plutus-v2"
    assert result["reference_tip"]["slot"] == 2000
    assert result["consumer_lag_slots"] == 0
    assert result["evidence_scope"] == "current_active_profile"
    assert result["redeploy_supported"] is False


def test_profile_health_parses_exact_topology_probe_json():
    parser = getattr(health_data, "_topology_result_from_body", None)
    assert parser is not None
    body = "probe preface\n" + json.dumps({
        "schema_version": 1,
        "topology_id": "cardano_amaru",
        "state": "healthy",
        "reason_code": "all_mixed_readiness_gates_passed",
        "observation": {"compose_project": "dwarf-profile-w"},
    }) + "\n"

    result = parser(body)

    assert result["state"] == "healthy"
    assert result["observation"]["compose_project"] == "dwarf-profile-w"
    assert parser("INSPECT_VIEW=health\ncardano_node_processes=3\n") is None


def test_current_topology_health_does_not_hide_no_active_or_stalled_state():
    no_active = operate_topology_health.classify_current_profile_health(
        {"enabled": False, "state": "no_active", "error": "no active profile"}, None,
    )
    stalled_sample = {
        "enabled": True, "profile_id": "profile-v", "expected_nodes": 3,
        "health": {"returncode": 0, "parsed": {
            "cardano_node_processes": "3", "socket_count": "3",
            "listener_count": "3", "loopback_only": "true",
            "tip_block": 9, "tip_slot": 11, "sync_progress": "100.00",
        }},
    }
    stalled = operate_topology_health.classify_current_profile_health(
        stalled_sample, json.loads(json.dumps(stalled_sample))
    )

    assert no_active["state"] == "no_active"
    assert stalled["state"] == "unhealthy"
    assert stalled["reason_code"] == "active_profile_not_progressing"
    assert stalled["redeploy_supported"] is False


def test_topology_health_api_starts_fresh_check(monkeypatch):
    calls = []
    monkeypatch.setattr(
        operate_topology_health,
        "request_topology_health_check",
        lambda: calls.append("fresh")
        or {"state": "checking", "previous": None, "previous_is_current": False},
    )

    status, content_type, body = dispatch_api_request(
        "/api/topology/health?fresh=1"
    )

    assert status == 200
    assert content_type.startswith("application/json")
    assert json.loads(body)["state"] == "checking"
    assert calls == ["fresh"]


def test_topology_health_evidence_download_uses_completed_public_result(monkeypatch):
    monkeypatch.setattr(
        operate_topology_health,
        "topology_health_evidence",
        lambda: {"state": "unhealthy", "reason_code": "amaru_relay_stalled"},
    )

    status, content_type, body = dispatch_api_request(
        "/api/topology/health/evidence"
    )

    assert status == 200
    assert content_type.startswith("application/json")
    assert json.loads(body)["reason_code"] == "amaru_relay_stalled"


def test_operate_status_loads_active_topology_health_panel(monkeypatch):
    monkeypatch.setattr(
        "profile_manager.dashboard.build_dashboard_status_payload",
        lambda live: {
            "live": {},
            "last_local_health": {},
            "profiles": [],
            "config": {},
        },
    )
    monkeypatch.setattr(
        operate_topology_health,
        "topology_health_snapshot",
        lambda: {
            "state": "idle",
            "previous": None,
            "previous_is_current": False,
        },
    )

    html = render_operate_status(port=8787, bind="127.0.0.1", token="test-token")

    assert 'id="topology-health-panel"' in html
    assert 'src="/static/js/topology-health.js"' in html
    assert 'data-health-url="/api/topology/health"' in html
    assert "A fresh read-only check starts whenever this page is opened" in html
    assert "Current managed topology health" in html
    assert "Mixed topology health" not in html


def test_learn_cli_links_to_live_primitive_catalog_and_reference():
    html = render_learn_cli()

    assert 'href="/operate/primitives"' in html
    assert 'href="/learn/primitives"' in html
    assert "206 primitives" not in html


def test_learn_docs_match_current_container_and_config_workflows():
    faq = render_learn_faq()
    troubleshooting = render_learn_troubleshooting()

    assert "dashboard never mutates config" not in faq
    assert "/operate/config/edit" in faq
    assert "V3 Docker container" not in troubleshooting
    assert "V3 image" not in troubleshooting


def test_legacy_dashboard_and_startup_text_describe_live_mutating_controls():
    html = render_dashboard_html()
    startup = dashboard_serve_text(port=8787, bind="127.0.0.1", token="dwarf")

    for text in (html, startup):
        assert "No browser action deploys, removes, fuzzes, or mutates runtime state." not in text
    assert "Browser mutations are token-gated and serialized" in html
    assert "Token gate active for mutating endpoints" in startup


def test_smoke_and_fuzz_evidence_fall_back_to_retained_runtime_root(monkeypatch, tmp_path):
    evidence_root = tmp_path / "evidence"
    monkeypatch.delenv("ADA2_PROFILE_MANAGER_SMOKE_EVIDENCE_ROOT", raising=False)
    monkeypatch.delenv("ADA2_PROFILE_MANAGER_FUZZ_EVIDENCE_ROOT", raising=False)
    monkeypatch.setenv("ADA2_PROFILE_MANAGER_EVIDENCE_ROOT", str(evidence_root))

    assert smoke_evidence_root() == evidence_root / "smoke-tests"
    assert fuzz_evidence_root() == evidence_root / "fuzz"


def test_overview_describes_proven_mixed_generation_honestly():
    html = render_learn_overview()

    assert "Amaru / differential generation is follow-on work" not in html
    assert "Mixed Cardano/Amaru N2N state-machine" in html
    assert "local runtime proof" in html
    assert "not a valid live Antithesis result" in html
    assert "239 shipped scenarios" not in html
    assert "on-wire adversary mode is follow-on work" not in html


def test_learn_landing_uses_runtime_primitive_count():
    expected = len(
        json.loads((ROOT / "dwarf/primitives/registry.json").read_text(encoding="utf-8"))["primitives"]
    )

    html = render_learn_landing()
    source = (ROOT / "dwarf/dashboard/templates/learn/landing.j2").read_text(encoding="utf-8")

    assert f"the {expected}-primitive catalogue" in html
    assert "the 206-primitive catalogue" not in source


def test_learn_separates_shipped_scenarios_from_runtime_extensions(tmp_path, monkeypatch):
    runtime = tmp_path / "scenarios"
    runtime.mkdir()
    packaged = _list_packaged_scenarios_for_compare()
    assert len(packaged) == 283

    source = ROOT / "dwarf/scenarios" / "client-example-simple-transfer-amaru.yaml"
    (runtime / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    (runtime / "operator-proof.yaml").write_text(
        source.read_text(encoding="utf-8").replace(
            "client-example-simple-transfer-amaru", "operator-proof"
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ADA2_DWARF_SCENARIOS_DIR", str(runtime))
    invalidate_scenario_cache()

    landing = render_learn_landing()
    census = scenario_census()

    assert "283 shipped" in landing
    assert "2 active" in landing
    assert "All 2 scenarios are in the active scenario catalog" in census["caption"]
    assert "The shipped repository catalog has 283 scenarios" in census["caption"]
    assert "All 2 scenarios in <code>dwarf/scenarios/</code>" not in census["caption"]


def test_public_runbook_contains_portable_moog_paths():
    source = (ROOT / "dwarf/dashboard/templates/learn/operator_runbook.j2").read_text(
        encoding="utf-8"
    )

    assert "nigel" not in source.lower()
    assert "/srv/dwarf" in source


def test_learn_examples_cover_every_reusable_asset_without_claiming_execution():
    html = render_learn_examples()

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
        assert f'id="asset-{catalog}"' in html
    assert "Definitions and examples are inputs to execution, not runtime evidence." in html
    assert "Source of truth" in html


def test_profile_and_plugin_guidance_do_not_freeze_inventory_or_overstate_isolation():
    concepts = (ROOT / "dwarf/profile_manager/data/concepts.py").read_text(encoding="utf-8")
    docs = (ROOT / "dwarf/profile_manager/data/learn_docs.py").read_text(encoding="utf-8")

    assert "five current profiles" not in concepts.lower()
    assert "live catalog" in concepts.lower()
    assert "audit-grade isolation" not in docs.lower()
    assert "not sandboxed" in docs.lower()
