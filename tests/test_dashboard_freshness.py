import json
import hashlib
import re
import threading
from pathlib import Path

import pytest

from profile_manager import dashboard as dashboard_module
from profile_manager.data.coverage import scenario_census
from profile_manager.config import StorageConfigurationError
from profile_manager.data.learn_api import html_route_groups
from profile_manager.dashboard import (
    build_dashboard_status_payload,
    dashboard_serve_text,
    dispatch_api_request,
    render_dashboard_html,
)
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
from profile_manager.templating import set_current_view
from profile_manager.views.learn_overview import render_learn_overview
from profile_manager.views.learn import render_learn_landing
from profile_manager.views.learn_cli import render_learn_cli
from profile_manager.views.learn_docs import render_learn_faq, render_learn_troubleshooting
from profile_manager.views.learn_examples import render_learn_examples
from profile_manager.views.status import render_learn_status
from profile_manager.views.threat_coverage import render_learn_threat_coverage
from profile_manager.views.operate_status import render_operate_status


ROOT = Path(__file__).resolve().parents[1]
REVISION_A = "a" * 40
REVISION_B = "b" * 40


def _test_catalog_digest(scenarios_dir, primitive_registry):
    digest = hashlib.sha256(b"DWARF-CATALOG-V1\0")
    paths = [
        (f"scenarios/{candidate.name}", candidate)
        for candidate in sorted(scenarios_dir.glob("*.yaml"))
    ]
    paths.append(("primitives/registry.json", primitive_registry))
    for relative, candidate in paths:
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(candidate.read_bytes()).digest())
    return f"sha256:{digest.hexdigest()}"


def _write_test_catalog(root, scenarios):
    scenarios_dir = root / "scenarios"
    scenarios_dir.mkdir(parents=True)
    for name, body in scenarios.items():
        (scenarios_dir / name).write_text(body, encoding="utf-8")
    primitive_registry = root / "primitives" / "registry.json"
    primitive_registry.parent.mkdir(parents=True)
    primitive_registry.write_text(
        json.dumps({"primitives": {"test-primitive": {"family": "probe"}}}) + "\n",
        encoding="utf-8",
    )
    return scenarios_dir, primitive_registry


def _write_release_identity(
    path,
    *,
    release_revision,
    catalog_revision,
    scenarios_dir,
    primitive_registry,
    component_role="dashboard",
    overrides=None,
):
    payload = {
        "schema_version": 1,
        "component_role": component_role,
        "release_revision": release_revision,
        "catalog_revision": catalog_revision,
        "catalog_digest": _test_catalog_digest(scenarios_dir, primitive_registry),
        "packaged_scenario_count": len(list(scenarios_dir.glob("*.yaml"))),
        "packaged_primitive_count": len(
            json.loads(primitive_registry.read_text(encoding="utf-8"))["primitives"]
        ),
    }
    payload.update(overrides or {})
    path.write_text(
        json.dumps(payload, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _use_test_catalog(monkeypatch, scenarios_dir, primitive_registry, active_dir=None):
    monkeypatch.setattr(health_data, "PACKAGED_SCENARIOS_DIR", scenarios_dir, raising=False)
    monkeypatch.setattr(health_data, "PRIMITIVE_REGISTRY_PATH", primitive_registry, raising=False)
    monkeypatch.setenv("ADA2_DWARF_SCENARIOS_DIR", str(active_dir or scenarios_dir))


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


def test_status_payload_reports_runtime_identity_and_effective_storage_without_secrets(
    monkeypatch, tmp_path
):
    scenarios_dir, primitive_registry = _write_test_catalog(
        tmp_path / "packaged", {"base.yaml": "id: base\n"}
    )
    _use_test_catalog(monkeypatch, scenarios_dir, primitive_registry)
    identity_path = tmp_path / "release-identity.json"
    _write_release_identity(
        identity_path,
        release_revision=REVISION_A,
        catalog_revision=REVISION_A,
        scenarios_dir=scenarios_dir,
        primitive_registry=primitive_registry,
    )
    monkeypatch.setattr(health_data, "RELEASE_IDENTITY_PATH", identity_path)
    roots = {
        "runs": tmp_path / "retained-runs",
        "state": tmp_path / "control-state",
        "bundles": tmp_path / "exported-bundles",
    }
    monkeypatch.setenv("DWARF_SOURCE_REVISION", "runtime-release-override")
    monkeypatch.setenv("DWARF_CATALOG_REVISION", "runtime-catalog-override")
    monkeypatch.setenv("DWARF_COMPONENT_ROLE", "runtime-role-override")
    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(roots["runs"]))
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(roots["state"]))
    monkeypatch.setenv("ADA2_DWARF_BUNDLES_DIR", str(roots["bundles"]))
    monkeypatch.setenv("DWARF_DASHBOARD_TOKEN", "never-render-this-token")
    monkeypatch.setenv("MOOG_GITHUB_PAT", "never-render-this-pat")
    payload = build_dashboard_status_payload(live=False)
    runtime = payload["runtime_identity"]

    assert runtime["component_role"] == "dashboard"
    assert runtime["release_revision"] == REVISION_A
    assert runtime["release_revision_known"] is True
    assert runtime["identity_source"] == "packaged_artifact"
    assert runtime["attested"] is True
    assert runtime["readiness"] == {
        "ready": True,
        "state": "ready",
        "reason": "attested identity and packaged catalog are aligned",
    }
    catalog = runtime["catalog"]
    assert catalog["revision"] == REVISION_A
    assert catalog["scenario_count"] == 1
    assert catalog["packaged_scenario_count"] == 1
    assert catalog["primitive_count"] == 1
    assert catalog["packaged_primitive_count"] == 1
    assert catalog["matches_release"] is True
    assert catalog["status"] == "aligned"
    assert catalog["packaged_digest"] == _test_catalog_digest(
        scenarios_dir, primitive_registry
    )
    assert catalog["active_digest"] == catalog["packaged_digest"]
    assert catalog["runtime_extension_count"] == 0
    assert runtime["storage_roots"] == {
        name: {
            "path": str(path),
            "configured": True,
            "resolution": "environment",
            "status": "configured",
        }
        for name, path in roots.items()
    }
    serialized = json.dumps(payload)
    assert "never-render-this-token" not in serialized
    assert "never-render-this-pat" not in serialized


def test_runtime_identity_fails_closed_on_attested_catalog_drift(monkeypatch, tmp_path):
    scenarios_dir, primitive_registry = _write_test_catalog(
        tmp_path / "packaged", {"base.yaml": "id: base\n"}
    )
    _use_test_catalog(monkeypatch, scenarios_dir, primitive_registry)
    identity_path = tmp_path / "release-identity.json"
    _write_release_identity(
        identity_path,
        release_revision=REVISION_A,
        catalog_revision=REVISION_B,
        scenarios_dir=scenarios_dir,
        primitive_registry=primitive_registry,
    )
    monkeypatch.setattr(health_data, "RELEASE_IDENTITY_PATH", identity_path)

    runtime = health_data.runtime_identity()

    assert runtime["attested"] is True
    assert runtime["catalog"]["matches_release"] is False
    assert runtime["readiness"] == {
        "ready": False,
        "state": "drift",
        "reason": "catalog revision does not match release revision",
    }


def test_runtime_identity_fails_closed_on_unknown_packaged_identity(monkeypatch, tmp_path):
    scenarios_dir, primitive_registry = _write_test_catalog(
        tmp_path / "packaged", {"base.yaml": "id: base\n"}
    )
    _use_test_catalog(monkeypatch, scenarios_dir, primitive_registry)
    identity_path = tmp_path / "release-identity.json"
    _write_release_identity(
        identity_path,
        release_revision="unknown",
        catalog_revision="unknown",
        scenarios_dir=scenarios_dir,
        primitive_registry=primitive_registry,
    )
    monkeypatch.setattr(health_data, "RELEASE_IDENTITY_PATH", identity_path)

    runtime = health_data.runtime_identity()

    assert runtime["identity_source"] == "packaged_artifact"
    assert runtime["attested"] is False
    assert runtime["catalog"]["matches_release"] is None
    assert runtime["readiness"]["ready"] is False
    assert runtime["readiness"]["state"] == "unknown"


def test_runtime_identity_fails_closed_on_malformed_packaged_identity(monkeypatch, tmp_path):
    scenarios_dir, primitive_registry = _write_test_catalog(
        tmp_path / "packaged", {"base.yaml": "id: base\n"}
    )
    _use_test_catalog(monkeypatch, scenarios_dir, primitive_registry)
    identity_path = tmp_path / "release-identity.json"
    identity_path.write_text("[]\n", encoding="utf-8")
    monkeypatch.setattr(health_data, "RELEASE_IDENTITY_PATH", identity_path)

    runtime = health_data.runtime_identity()

    assert runtime["identity_source"] == "packaged_artifact"
    assert runtime["attested"] is False
    assert runtime["readiness"] == {
        "ready": False,
        "state": "unknown",
        "reason": "packaged release identity artifact is invalid",
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2),
        ("component_role", "controller"),
        ("release_revision", 7),
        ("release_revision", "A" * 40),
        ("catalog_revision", "b" * 39),
        ("catalog_digest", "sha256:not-a-digest"),
        ("packaged_scenario_count", True),
        ("packaged_primitive_count", "1"),
    ],
)
def test_runtime_identity_rejects_invalid_packaged_schema(
    monkeypatch, tmp_path, field, value
):
    scenarios_dir, primitive_registry = _write_test_catalog(
        tmp_path / "packaged", {"base.yaml": "id: base\n"}
    )
    _use_test_catalog(monkeypatch, scenarios_dir, primitive_registry)
    identity_path = tmp_path / "release-identity.json"
    _write_release_identity(
        identity_path,
        release_revision=REVISION_A,
        catalog_revision=REVISION_A,
        scenarios_dir=scenarios_dir,
        primitive_registry=primitive_registry,
        overrides={field: value},
    )
    monkeypatch.setattr(health_data, "RELEASE_IDENTITY_PATH", identity_path)

    runtime = health_data.runtime_identity()

    assert runtime["attested"] is False
    assert runtime["readiness"]["ready"] is False
    assert runtime["readiness"]["state"] == "unknown"


def test_runtime_catalog_allows_named_extensions_when_packaged_base_is_unchanged(
    monkeypatch, tmp_path
):
    scenarios_dir, primitive_registry = _write_test_catalog(
        tmp_path / "packaged", {"base.yaml": "id: base\n"}
    )
    active_dir = tmp_path / "active"
    active_dir.mkdir()
    (active_dir / "base.yaml").write_text("id: base\n", encoding="utf-8")
    (active_dir / "operator-extra.yaml").write_text("id: operator-extra\n", encoding="utf-8")
    _use_test_catalog(monkeypatch, scenarios_dir, primitive_registry, active_dir)
    identity_path = tmp_path / "release-identity.json"
    _write_release_identity(
        identity_path,
        release_revision=REVISION_A,
        catalog_revision=REVISION_A,
        scenarios_dir=scenarios_dir,
        primitive_registry=primitive_registry,
    )
    monkeypatch.setattr(health_data, "RELEASE_IDENTITY_PATH", identity_path)

    runtime = health_data.runtime_identity()

    assert runtime["catalog"]["status"] == "extended"
    assert runtime["catalog"]["scenario_count"] == 2
    assert runtime["catalog"]["packaged_scenario_count"] == 1
    assert runtime["catalog"]["runtime_extension_count"] == 1
    assert runtime["catalog"]["runtime_extension_names"] == ["operator-extra.yaml"]
    assert runtime["catalog"]["missing_packaged_names"] == []
    assert runtime["catalog"]["modified_packaged_names"] == []
    assert runtime["readiness"]["ready"] is True
    assert runtime["readiness"]["state"] == "ready"


def test_runtime_catalog_fails_closed_on_missing_or_modified_packaged_scenarios(
    monkeypatch, tmp_path
):
    scenarios_dir, primitive_registry = _write_test_catalog(
        tmp_path / "packaged",
        {"kept.yaml": "id: kept\n", "missing.yaml": "id: missing\n"},
    )
    active_dir = tmp_path / "active"
    active_dir.mkdir()
    (active_dir / "kept.yaml").write_text("id: changed\n", encoding="utf-8")
    _use_test_catalog(monkeypatch, scenarios_dir, primitive_registry, active_dir)
    identity_path = tmp_path / "release-identity.json"
    _write_release_identity(
        identity_path,
        release_revision=REVISION_A,
        catalog_revision=REVISION_A,
        scenarios_dir=scenarios_dir,
        primitive_registry=primitive_registry,
    )
    monkeypatch.setattr(health_data, "RELEASE_IDENTITY_PATH", identity_path)

    runtime = health_data.runtime_identity()

    assert runtime["catalog"]["status"] == "drift"
    assert runtime["catalog"]["missing_packaged_names"] == ["missing.yaml"]
    assert runtime["catalog"]["modified_packaged_names"] == ["kept.yaml"]
    assert runtime["readiness"]["ready"] is False
    assert runtime["readiness"]["state"] == "drift"


@pytest.mark.parametrize(
    ("overrides", "mismatch_field"),
    [
        ({"catalog_digest": "sha256:" + "f" * 64}, "artifact_digest_matches"),
        ({"packaged_scenario_count": 99}, "artifact_counts_match"),
    ],
)
def test_runtime_catalog_fails_closed_on_artifact_mismatch(
    monkeypatch, tmp_path, overrides, mismatch_field
):
    scenarios_dir, primitive_registry = _write_test_catalog(
        tmp_path / "packaged", {"base.yaml": "id: base\n"}
    )
    _use_test_catalog(monkeypatch, scenarios_dir, primitive_registry)
    identity_path = tmp_path / "release-identity.json"
    _write_release_identity(
        identity_path,
        release_revision=REVISION_A,
        catalog_revision=REVISION_A,
        scenarios_dir=scenarios_dir,
        primitive_registry=primitive_registry,
        overrides=overrides,
    )
    monkeypatch.setattr(health_data, "RELEASE_IDENTITY_PATH", identity_path)

    runtime = health_data.runtime_identity()

    assert runtime["attested"] is True
    assert runtime["catalog"]["status"] == "drift"
    assert runtime["catalog"][mismatch_field] is False
    assert runtime["readiness"]["ready"] is False
    assert runtime["readiness"]["state"] == "drift"


def test_runtime_identity_labels_missing_artifact_as_unattested(
    monkeypatch, tmp_path
):
    scenarios_dir, primitive_registry = _write_test_catalog(
        tmp_path / "packaged", {"base.yaml": "id: base\n"}
    )
    _use_test_catalog(monkeypatch, scenarios_dir, primitive_registry)
    monkeypatch.setattr(health_data, "RELEASE_IDENTITY_PATH", tmp_path / "missing.json")
    monkeypatch.setenv("DWARF_SOURCE_REVISION", "development-release")
    monkeypatch.delenv("DWARF_CATALOG_REVISION", raising=False)
    monkeypatch.setenv("DWARF_COMPONENT_ROLE", "dashboard-development")
    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ADA2_DWARF_BUNDLES_DIR", str(tmp_path / "bundles"))

    runtime = health_data.runtime_identity()

    assert runtime["identity_source"] == "development_environment_fallback"
    assert runtime["attested"] is False
    assert runtime["release_revision"] == "development-release"
    assert runtime["release_revision_known"] is False
    assert runtime["catalog"]["revision"] == "unknown"
    assert runtime["catalog"]["matches_release"] is None
    assert runtime["readiness"]["ready"] is False
    assert runtime["readiness"]["state"] == "unknown"
    assert {
        name: item["path"] for name, item in runtime["storage_roots"].items()
    } == {
        "runs": str(tmp_path / "runs"),
        "state": str(tmp_path / "state"),
        "bundles": str(tmp_path / "bundles"),
    }


def test_runtime_identity_rejects_partial_storage_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.delenv("ADA2_DWARF_STATE_DIR", raising=False)
    monkeypatch.delenv("ADA2_DWARF_BUNDLES_DIR", raising=False)

    with pytest.raises(StorageConfigurationError, match="must be configured as all three"):
        health_data.runtime_identity()


def test_operate_status_explains_runtime_alignment_in_basic_and_advanced(monkeypatch):
    runtime_identity = {
        "component_role": "dashboard",
        "release_revision": "0123456789abcdef",
        "release_revision_known": True,
        "identity_source": "packaged_artifact",
        "attested": False,
        "readiness": {
            "ready": False,
            "state": "unknown",
            "reason": "release identity artifact is missing; environment fallback is unattested",
        },
        "catalog": {
            "revision": "0123456789abcdef",
            "repository": "https://github.com/pragma-org/dwarf",
            "scenario_count": 344,
            "primitive_count": 66,
            "matches_release": True,
        },
        "storage_roots": {
            "runs": {"path": "/var/dwarf/runs", "configured": True},
            "state": {"path": "/var/dwarf/state", "configured": True},
            "bundles": {"path": "/var/dwarf/bundles", "configured": True},
        },
    }
    monkeypatch.setattr(
        "profile_manager.dashboard.build_dashboard_status_payload",
        lambda live: {
            "live": {},
            "last_local_health": {},
            "profiles": [],
            "config": {},
            "runtime_identity": runtime_identity,
        },
    )
    monkeypatch.setattr(
        operate_topology_health,
        "topology_health_snapshot",
        lambda: {"state": "idle", "previous": None, "previous_is_current": False},
    )

    try:
        set_current_view("basic")
        basic_html = render_operate_status(port=8787, bind="127.0.0.1", token="test-token")
        set_current_view("bento")
        advanced_html = render_operate_status(port=8787, bind="127.0.0.1", token="test-token")
    finally:
        set_current_view(None)

    assert 'data-runtime-health="unknown"' in basic_html
    assert "Runtime identity is not ready" in basic_html
    assert "environment fallback is unattested" in basic_html
    assert "Runtime identity &amp; storage" in advanced_html
    assert "0123456789abcdef" in advanced_html
    assert "/var/dwarf/runs" in advanced_html
    assert "/var/dwarf/state" in advanced_html
    assert "/var/dwarf/bundles" in advanced_html


def test_operate_status_basic_summarizes_runtime_extensions(monkeypatch):
    monkeypatch.setattr(
        "profile_manager.dashboard.build_dashboard_status_payload",
        lambda live: {
            "live": {},
            "last_local_health": {},
            "profiles": [],
            "config": {},
            "runtime_identity": {
                "component_role": "dashboard",
                "release_revision": REVISION_A,
                "release_revision_known": True,
                "identity_source": "packaged_artifact",
                "attested": True,
                "readiness": {"ready": True, "state": "ready", "reason": "aligned"},
                "catalog": {
                    "status": "extended",
                    "runtime_extension_count": 2,
                    "runtime_extension_names": ["local-a.yaml", "local-b.yaml"],
                    "revision": REVISION_A,
                    "matches_release": True,
                },
                "storage_roots": {},
            },
        },
    )
    monkeypatch.setattr(
        operate_topology_health,
        "topology_health_snapshot",
        lambda: {"state": "idle", "previous": None, "previous_is_current": False},
    )

    try:
        set_current_view("basic")
        html = render_operate_status(port=8787, bind="127.0.0.1", token="test-token")
    finally:
        set_current_view(None)

    assert "Packaged base aligned; 2 runtime extensions" in html


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
        "/api/runs?limit=1&scope=external",
        runs_dir=runs_dir,
        bundles_dir=tmp_path / "bundles",
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
    assert first["schema_version"] == "v1"
    assert second["state"] == "checking"
    assert second["schema_version"] == "v1"
    assert calls == ["probe"]

    release.set()
    completed = operate_topology_health.wait_for_topology_health(timeout=2)
    assert completed["state"] == "healthy"
    assert calls == ["probe"]


def test_topology_health_service_refuses_cross_profile_singleflight_join():
    operate_topology_health.reset_topology_health_state()
    started = threading.Event()
    release = threading.Event()

    def probe():
        started.set()
        assert release.wait(timeout=2)
        return {"state": "healthy", "checked_at": "2026-10-08T07:00:00Z"}

    first = operate_topology_health.request_topology_health_check(
        probe=probe, profile_id="profile-a"
    )
    assert started.wait(timeout=2)
    conflict = operate_topology_health.request_topology_health_check(
        profile_id="profile-b"
    )

    assert first["requested_profile_id"] == "profile-a"
    assert conflict["state"] == "conflict"
    assert conflict["reason_code"] == "different_profile_check_in_progress"
    assert conflict["requested_profile_id"] == "profile-b"
    assert conflict["inflight_profile_id"] == "profile-a"

    release.set()
    operate_topology_health.wait_for_topology_health(timeout=2)


def test_topology_health_service_refuses_same_profile_different_desired_plan_join():
    operate_topology_health.reset_topology_health_state()
    started = threading.Event()
    release = threading.Event()
    profile_id = "profile-registered-amaru"
    old_plan = "sha256:" + "a" * 64
    current_plan = "sha256:" + "b" * 64

    def old_probe():
        started.set()
        assert release.wait(timeout=2)
        return {
            "state": "healthy",
            "profile_id": profile_id,
            "desired": {"plan_sha256": old_plan},
            "checked_at": "2026-10-12T02:37:21Z",
        }

    first = operate_topology_health.request_topology_health_check(
        probe=old_probe,
        profile_id=profile_id,
        desired_plan_sha256=old_plan,
    )
    assert started.wait(timeout=2)
    legacy_join = operate_topology_health.request_topology_health_check(
        probe=lambda: pytest.fail("an unbound same-profile caller must join"),
        profile_id=profile_id,
    )
    conflict = operate_topology_health.request_topology_health_check(
        probe=lambda: pytest.fail("a mismatched check must not start or join"),
        profile_id=profile_id,
        desired_plan_sha256=current_plan,
    )

    assert first["state"] == "checking"
    assert legacy_join["state"] == "checking"
    assert conflict["state"] == "conflict"
    assert conflict["reason_code"] == "different_topology_check_in_progress"
    assert conflict["requested_profile_id"] == profile_id
    assert conflict["requested_desired_plan_sha256"] == current_plan
    assert conflict["inflight_desired_plan_sha256"] == old_plan

    release.set()
    completed = operate_topology_health.wait_for_topology_health(
        timeout=2,
        profile_id=profile_id,
        desired_plan_sha256=old_plan,
    )
    assert completed["desired"]["plan_sha256"] == old_plan
    mismatched = operate_topology_health.wait_for_topology_health(
        timeout=0,
        profile_id=profile_id,
        desired_plan_sha256=current_plan,
    )
    assert mismatched["state"] == "conflict"
    assert mismatched["reason_code"] == "completed_topology_identity_mismatch"

    current = operate_topology_health.request_topology_health_check(
        probe=lambda: {
            "state": "healthy",
            "profile_id": profile_id,
            # The authoritative probe reports the retained physical identity.
            "desired": {"plan_sha256": old_plan},
            "request_binding": {
                "profile_id": profile_id,
                "desired_plan_sha256": old_plan,
            },
            "checked_at": "2026-10-12T02:38:21Z",
        },
        profile_id=profile_id,
        desired_plan_sha256=current_plan,
    )
    assert current["state"] == "checking"
    current_completed = operate_topology_health.wait_for_topology_health(
        timeout=2,
        profile_id=profile_id,
        desired_plan_sha256=current_plan,
    )
    assert current_completed["state"] == "healthy"
    assert current_completed["desired"]["plan_sha256"] == old_plan
    assert current_completed["request_binding"] == {
        "profile_id": profile_id,
        "desired_plan_sha256": current_plan,
    }


def test_failed_profile_probe_remains_visible_to_that_profile():
    operate_topology_health.reset_topology_health_state()

    def probe():
        raise RuntimeError("profile probe exploded")

    operate_topology_health.request_topology_health_check(
        probe=probe, profile_id="profile-a"
    )
    operate_topology_health.wait_for_topology_health(timeout=2)

    completed = operate_topology_health.topology_health_snapshot(
        profile_id="profile-a"
    )

    assert completed["state"] == "unknown"
    assert completed["reason_code"] == "topology_probe_failed"
    assert completed["profile_id"] == "profile-a"


def test_completed_profile_snapshots_are_cached_without_cross_profile_previous(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("ADA2_DWARF_BUNDLES_DIR", str(tmp_path / "bundles"))
    operate_topology_health.reset_topology_health_state()
    operate_topology_health.request_topology_health_check(
        probe=lambda: {"state": "healthy", "checked_at": "2026-10-08T07:00:00Z"},
        profile_id="profile-a",
    )
    operate_topology_health.wait_for_topology_health(timeout=2)

    started = threading.Event()
    release = threading.Event()

    def profile_b_probe():
        started.set()
        assert release.wait(timeout=2)
        return {"state": "unhealthy", "checked_at": "2026-10-08T07:01:00Z"}

    checking_b = operate_topology_health.request_topology_health_check(
        probe=profile_b_probe, profile_id="profile-b"
    )
    assert started.wait(timeout=2)

    try:
        assert checking_b["previous"] is None
    finally:
        release.set()
        operate_topology_health.wait_for_topology_health(timeout=2)

    cached_a = operate_topology_health.topology_health_snapshot(profile_id="profile-a")
    cached_b = operate_topology_health.topology_health_snapshot(profile_id="profile-b")
    assert cached_a["state"] == "healthy"
    assert cached_a["profile_id"] == "profile-a"
    assert cached_b["state"] == "unhealthy"
    assert cached_b["profile_id"] == "profile-b"


def test_targeted_profile_snapshot_does_not_replace_unscoped_active_snapshot(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("ADA2_DWARF_BUNDLES_DIR", str(tmp_path / "bundles"))
    operate_topology_health.reset_topology_health_state()

    operate_topology_health.request_topology_health_check(
        probe=lambda: {"state": "healthy", "checked_at": "2026-10-08T07:00:00Z"},
        profile_id="profile-b",
    )
    operate_topology_health.wait_for_topology_health(timeout=2)

    assert operate_topology_health.topology_health_snapshot()["state"] == "idle"
    targeted = operate_topology_health.topology_health_snapshot(
        profile_id="profile-b"
    )
    assert targeted["state"] == "healthy"
    assert targeted["profile_id"] == "profile-b"
    assert not (tmp_path / "topology-health" / "dashboard-latest.json").exists()

    operate_topology_health.reset_topology_health_state()
    assert operate_topology_health.topology_health_snapshot()["state"] == "idle"
    restored = operate_topology_health.topology_health_snapshot(
        profile_id="profile-b"
    )
    assert restored["state"] == "healthy"
    assert restored["profile_id"] == "profile-b"
    assert restored["observation_source"] == "persisted"


def test_wait_for_profile_health_returns_the_requested_scope(tmp_path, monkeypatch):
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("ADA2_DWARF_BUNDLES_DIR", str(tmp_path / "bundles"))
    operate_topology_health.reset_topology_health_state()

    operate_topology_health.request_topology_health_check(
        probe=lambda: {
            "state": "healthy",
            "profile_id": "active-profile",
            "checked_at": "2026-10-08T07:00:00Z",
        }
    )
    operate_topology_health.wait_for_topology_health(timeout=2)
    operate_topology_health.request_topology_health_check(
        probe=lambda: {
            "state": "unhealthy",
            "reason_code": "targeted-node-stalled",
            "checked_at": "2026-10-08T07:01:00Z",
        },
        profile_id="target-profile",
    )

    targeted = operate_topology_health.wait_for_topology_health(
        timeout=2, profile_id="target-profile"
    )

    assert targeted["state"] == "unhealthy"
    assert targeted["profile_id"] == "target-profile"
    assert targeted["reason_code"] == "targeted-node-stalled"
    assert operate_topology_health.topology_health_snapshot()["state"] == "healthy"


def test_topology_health_result_survives_dashboard_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("ADA2_DWARF_BUNDLES_DIR", str(tmp_path / "bundles"))
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
    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("ADA2_DWARF_BUNDLES_DIR", str(tmp_path / "bundles"))
    evidence_path = tmp_path / "topology-health" / "dashboard-latest.json"
    evidence_path.parent.mkdir(parents=True)
    evidence_path.write_text("not json", encoding="utf-8")
    operate_topology_health.reset_topology_health_state()

    restored = operate_topology_health.topology_health_snapshot()

    assert restored["state"] == "idle"
    assert restored["schema_version"] == "v1"
    assert restored["previous"] is None


def test_topology_health_timeout_is_unknown_not_cached_success():
    operate_topology_health.reset_topology_health_state()

    result = operate_topology_health.normalize_probe_failure(
        TimeoutError("probe exceeded deadline"),
        previous={"state": "healthy", "checked_at": "2026-09-14T00:00:00Z"},
    )

    assert result["state"] == "unknown"
    assert result["schema_version"] == "v1"
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


def test_current_topology_health_preserves_latest_controller_failure():
    first = {
        "enabled": True,
        "profile_id": "local-lab",
        "expected_nodes": 2,
        "health": {"returncode": 0, "parsed": {
            "cardano_node_processes": 2, "socket_count": 2,
            "listener_count": 2, "loopback_only": "true",
            "tip_block": 10, "tip_slot": 100, "sync_progress": "100.00",
        }},
    }
    second = json.loads(json.dumps(first))
    second["health"]["parsed"].update({"tip_block": 11, "tip_slot": 101})
    second["topology_result"] = {
        "state": "unhealthy",
        "reason_code": "node_chain_not_converged",
        "detail": "Managed nodes do not share one chain tip.",
    }

    result = operate_topology_health.classify_current_profile_health(first, second)

    assert result["state"] == "unhealthy"
    assert result["reason_code"] == "node_chain_not_converged"
    assert result["observations"] == [first, second]


def test_current_topology_health_does_not_preserve_superseded_first_failure():
    first = {
        "enabled": True,
        "profile_id": "local-lab",
        "expected_nodes": 2,
        "topology_result": {
            "state": "unhealthy",
            "reason_code": "node_chain_not_converged",
            "detail": "Managed nodes do not share one chain tip.",
        },
        "health": {"returncode": 0, "parsed": {
            "cardano_node_processes": 2, "socket_count": 2,
            "listener_count": 2, "loopback_only": "true",
            "tip_block": 10, "tip_slot": 100, "sync_progress": "100.00",
        }},
    }
    second = json.loads(json.dumps(first))
    second.pop("topology_result")
    second["health"]["parsed"].update({"tip_block": 11, "tip_slot": 101})

    result = operate_topology_health.classify_current_profile_health(first, second)

    assert result["state"] == "healthy"
    assert result["reason_code"] == "active_profile_ready_and_progressing"
    assert result["observations"] == [first, second]


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


def test_topology_health_api_forwards_requested_profile(monkeypatch):
    calls = []
    monkeypatch.setattr(
        operate_topology_health,
        "request_topology_health_check",
        lambda *, profile_id=None: calls.append(profile_id)
        or {
            "schema_version": "v1",
            "state": "checking",
            "requested_profile_id": profile_id,
        },
    )

    status, content_type, body = dispatch_api_request(
        "/api/topology/health?fresh=1&profile_id=profile-a-haskell-peersharing-disabled"
    )

    assert status == 200
    assert content_type.startswith("application/json")
    assert json.loads(body)["requested_profile_id"] == "profile-a-haskell-peersharing-disabled"
    assert calls == ["profile-a-haskell-peersharing-disabled"]


def test_topology_health_api_rejects_unsafe_profile_id():
    status, content_type, body = dispatch_api_request(
        "/api/topology/health?fresh=1&profile_id=../../escape"
    )

    assert status == 400
    assert content_type.startswith("application/json")
    assert json.loads(body)["error"] == "invalid profile_id"


def test_topology_health_api_rejects_unknown_profile_before_starting_probe():
    status, content_type, body = dispatch_api_request(
        "/api/topology/health?fresh=1&profile_id=does-not-exist"
    )

    assert status == 404
    assert content_type.startswith("application/json")
    assert json.loads(body)["error"] == "unknown profile_id"


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
    assert 'data-topology-action-url="/api/topology/actions"' in html
    assert "A fresh read-only check starts whenever this page is opened" in html
    assert "Current managed topology health" in html
    assert "Mixed topology health" not in html


def test_operate_status_route_forwards_requested_profile(monkeypatch):
    calls = []
    monkeypatch.setattr(
        dashboard_module,
        "render_operate_status",
        lambda *, profile_id=None, **_kwargs: calls.append(profile_id) or "status",
    )

    html = dashboard_module.render_route_html(
        "/operate/status?profile_id=task43-current-requal"
    )

    assert html == "status"
    assert calls == ["task43-current-requal"]


def test_operate_status_keeps_requested_profile_identity_visible(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "profile_manager.dashboard.build_dashboard_status_payload",
        lambda live, profile_id=None: calls.append((live, profile_id))
        or {
            "live": {"profile_id": profile_id},
            "last_local_health": {},
            "profiles": [
                {
                    "id": profile_id,
                    "label": "Generated local topology",
                    "node_type": "cardano-node",
                    "node_count": 2,
                    "peer_sharing": False,
                    "remote_runtime_root": "/opt/dwarf/cardano-profiles/" + profile_id,
                }
            ],
            "config": {},
        },
    )
    monkeypatch.setattr(
        operate_topology_health,
        "topology_health_snapshot",
        lambda *, profile_id=None: {
            "state": "no_active",
            "reason_code": "no_managed_resources",
            "profile_id": profile_id,
            "actions": {},
        },
    )

    html = render_operate_status(
        port=8787,
        bind="127.0.0.1",
        token="test-token",
        profile_id="task43-current-requal",
    )

    assert calls == [(True, "task43-current-requal")]
    assert "Selected topology" in html
    assert (
        'data-topology-field="profile-id">task43-current-requal</code>'
        in html
    )


def test_operate_status_retains_server_validated_profile_during_cold_snapshot(monkeypatch):
    monkeypatch.setattr(
        "profile_manager.dashboard.build_dashboard_status_payload",
        lambda live, profile_id=None: {
            "live": {"enabled": False},
            "last_local_health": {},
            "profiles": [],
            "config": {},
        },
    )
    monkeypatch.setattr(
        operate_topology_health,
        "topology_health_snapshot",
        lambda *, profile_id=None: {
            "state": "idle",
            "reason_code": "not_checked",
            "actions": {},
        },
    )

    html = render_operate_status(
        port=9999,
        bind="127.0.0.1",
        token="test-token",
        profile_id="task43-current-requal",
    )

    assert 'data-profile-id="task43-current-requal"' in html
    assert 'data-active-profile="task43-current-requal"' in html
    assert (
        'data-topology-field="profile-id">task43-current-requal</code>'
        in html
    )


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
    shipped_count = len(list((ROOT / "dwarf/scenarios").glob("*.yaml")))
    assert len(packaged) == shipped_count

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

    assert f"{shipped_count} shipped" in landing
    assert "2 active" in landing
    assert "All 2 scenarios are in the active scenario catalog" in census["caption"]
    assert f"The shipped repository catalog has {shipped_count} scenarios" in census["caption"]
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
