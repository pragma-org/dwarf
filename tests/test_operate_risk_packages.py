from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import tarfile

import jsonschema

from profile_manager import dashboard
from profile_manager.evidence_packages import (
    EvidencePackage,
    evidence_package_list_text,
    load_evidence_packages,
    validate_risk_work_package,
)
from profile_manager.data.operate_risk_packages import (
    deterministic_risk_package_archive,
    dispatch_risk_package_api_request,
    risk_package_catalog_rows,
    risk_package_detail,
    risk_work_package_schema,
)


ROOT = Path(__file__).resolve().parents[1]
SHIPPED = ROOT / "dwarf" / "evidence-packages"


def _package(package_id: str = "package-demo") -> dict:
    return {
        "id": package_id,
        "label": "Parser and protocol boundary",
        "runnable": False,
        "status": "candidate planning remains open",
        "candidate_ids": ["CR-DEMO-001"],
        "evidence_paths": ["evidence/existing.md", "evidence/missing.md"],
        "blockers": ["Differential evidence remains incomplete."],
        "read_only_actions": ["Inspect retained evidence without changing runtime state."],
        "runtime_profile": "profile-demo",
        "runtime_root": "/opt/dwarf/cardano-profiles/profile-demo",
    }


def _write_package(root: Path, body: dict | str, name: str = "package-demo.yaml") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    text = body if isinstance(body, str) else json.dumps(body, indent=2) + "\n"
    path.write_text(text, encoding="utf-8")
    return path


def _write_relationships(root: Path) -> tuple[Path, Path, Path]:
    evidence = root / "evidence-root"
    existing = evidence / "evidence" / "existing.md"
    existing.parent.mkdir(parents=True)
    existing.write_text("# Exact retained evidence\n", encoding="utf-8")
    scenarios = root / "scenarios"
    scenarios.mkdir()
    scenario = {
        "spec_version": "v1",
        "id": "scenario-demo",
        "title": "Scenario demo",
        "target": {"implementation": "amaru", "version": "any"},
        "runtime": "library",
        "m1_trace": {"risk_candidate_ids": ["CR-DEMO-001"]},
        "setup": [], "load": [], "faults": [], "probes": [], "assertions": [], "teardown": [],
    }
    (scenarios / "scenario-demo.yaml").write_text(json.dumps(scenario), encoding="utf-8")
    profiles = root / "profiles"
    profile = profiles / "profile-demo"
    profile.mkdir(parents=True)
    (profile / "profile.yaml").write_text(json.dumps({"id": "profile-demo"}), encoding="utf-8")
    return evidence, scenarios, profiles


def test_existing_files_validate_and_legacy_python_cli_api_remains_compatible():
    expected = sorted(path.stem for path in SHIPPED.glob("package-*.yaml"))
    packages = load_evidence_packages()

    assert [package.id for package in packages] == expected
    assert len(packages) == 4
    assert all(isinstance(package, EvidencePackage) for package in packages)
    assert all(validate_risk_work_package(json.loads((SHIPPED / f"{package.id}.yaml").read_text())) for package in packages)
    jsonschema.Draft202012Validator.check_schema(risk_work_package_schema())
    legacy_text = evidence_package_list_text()
    assert all(package.id in legacy_text for package in packages)


def test_catalog_reports_schema_parse_duplicate_and_symlink_diagnostics(tmp_path):
    packages = tmp_path / "packages"
    _write_package(packages, _package())
    _write_package(packages, _package(), "package-duplicate.yaml")
    _write_package(packages, {"id": "package-invalid"}, "package-invalid.yaml")
    _write_package(packages, "{", "package-malformed.yaml")
    outside = tmp_path / "outside.yaml"
    outside.write_text(json.dumps(_package("package-linked")), encoding="utf-8")
    (packages / "package-linked.yaml").symlink_to(outside)
    (packages / "._package-noise.yaml").write_text("finder", encoding="utf-8")

    rows = risk_package_catalog_rows(package_root=packages, evidence_root=tmp_path)
    by_source = {Path(row["source_path"]).name: row for row in rows}

    assert set(by_source) == {
        "package-demo.yaml", "package-duplicate.yaml", "package-invalid.yaml", "package-malformed.yaml"
    }
    assert len({row["id"] for row in rows}) == len(rows)
    assert by_source["package-demo.yaml"]["status"] == "ready"
    assert by_source["package-duplicate.yaml"]["status"] == "diagnostic"
    assert any("duplicate" in item.lower() for item in by_source["package-duplicate.yaml"]["diagnostics"])
    assert by_source["package-invalid.yaml"]["status"] == "diagnostic"
    assert by_source["package-malformed.yaml"]["status"] == "diagnostic"


def test_invalid_list_shapes_do_not_create_bogus_relationships(tmp_path):
    packages = tmp_path / "packages"
    malformed_lists = _package()
    malformed_lists["candidate_ids"] = "CR-DEMO-001"
    malformed_lists["evidence_paths"] = "evidence/existing.md"
    malformed_lists["blockers"] = "not-a-list"
    malformed_lists["read_only_actions"] = "not-a-list"
    _write_package(packages, malformed_lists)

    row = risk_package_catalog_rows(package_root=packages, evidence_root=tmp_path)[0]

    assert row["status"] == "diagnostic"
    assert row["candidate_ids"] == []
    assert row["evidence"] == []
    assert row["blockers"] == []
    assert row["read_only_actions"] == []


def test_relationships_and_evidence_links_are_exact_and_never_guessed(tmp_path):
    packages = tmp_path / "packages"
    _write_package(packages, _package())
    evidence, scenarios, profiles = _write_relationships(tmp_path)
    (scenarios / "similar-name.yaml").write_text(
        json.dumps({"id": "similar-name", "notes": "CR-DEMO-001-ish"}), encoding="utf-8"
    )

    row = risk_package_catalog_rows(
        package_root=packages,
        evidence_root=evidence,
        scenarios_dir=scenarios,
        profiles_dir=profiles,
    )[0]

    assert row["scenario_ids"] == ["scenario-demo"]
    assert row["profile_id"] == "profile-demo"
    assert row["profile_available"] is True
    assert {(item["catalog"], item["id"], item["resolved"]) for item in row["relationships"]} == {
        ("scenarios", "scenario-demo", True),
        ("profiles", "profile-demo", True),
    }
    assert [item["status"] for item in row["evidence"]] == ["available-file", "missing"]
    assert row["evidence"][0]["download_url"].endswith("/evidence/0/download")
    assert row["evidence"][1]["download_url"] is None


def test_source_and_resolved_evidence_downloads_are_exact_and_exports_deterministic(tmp_path):
    packages = tmp_path / "packages"
    source = _write_package(packages, _package())
    evidence, scenarios, profiles = _write_relationships(tmp_path)
    kwargs = {
        "package_root": packages,
        "evidence_root": evidence,
        "scenarios_dir": scenarios,
        "profiles_dir": profiles,
    }
    source_response = dispatch_risk_package_api_request(
        "/api/risk-packages/package-demo/download", **kwargs
    )
    evidence_response = dispatch_risk_package_api_request(
        "/api/risk-packages/package-demo/evidence/0/download", **kwargs
    )

    assert source_response[0] == 200 and source_response[2] == source.read_bytes()
    assert evidence_response[0] == 200 and evidence_response[2] == b"# Exact retained evidence\n"
    assert dispatch_risk_package_api_request(
        "/api/risk-packages/package-demo/evidence/1/download", **kwargs
    )[0] == 404
    assert dispatch_risk_package_api_request(
        "/api/risk-packages/..%2Foutside/download", **kwargs
    )[0] == 400

    first = deterministic_risk_package_archive(**kwargs)
    second = deterministic_risk_package_archive(**kwargs)
    assert first == second
    with tarfile.open(fileobj=io.BytesIO(first), mode="r:gz") as archive:
        assert archive.getnames() == [
            "DWARF-EXPORT-MANIFEST.json",
            "dwarf/evidence-packages/package-demo.yaml",
        ]
        assert archive.extractfile("dwarf/evidence-packages/package-demo.yaml").read() == source.read_bytes()
        manifest = json.loads(archive.extractfile("DWARF-EXPORT-MANIFEST.json").read())
        assert manifest["object_ids"] == ["package-demo"]


def test_catalog_gets_are_read_only(tmp_path):
    packages = tmp_path / "packages"
    _write_package(packages, _package())
    before = hashlib.sha256((packages / "package-demo.yaml").read_bytes()).hexdigest()

    risk_package_catalog_rows(package_root=packages, evidence_root=tmp_path)
    risk_package_detail("package-demo", package_root=packages, evidence_root=tmp_path)
    deterministic_risk_package_archive(package_root=packages, evidence_root=tmp_path)

    after = hashlib.sha256((packages / "package-demo.yaml").read_bytes()).hexdigest()
    assert after == before


def test_ui_uses_risk_work_package_term_without_relabelling_forensic_bundles(tmp_path, monkeypatch):
    packages = tmp_path / "packages"
    _write_package(packages, _package())
    evidence, scenarios, profiles = _write_relationships(tmp_path)
    monkeypatch.setenv("ADA2_DWARF_RISK_PACKAGES_DIR", str(packages))
    monkeypatch.setenv("ADA2_DWARF_RISK_EVIDENCE_ROOT", str(evidence))
    monkeypatch.setenv("ADA2_DWARF_SCENARIOS_DIR", str(scenarios))
    monkeypatch.setenv("ADA2_DWARF_PROFILES_DIR", str(profiles))

    index = dashboard.render_route_html("/operate/risk-packages")
    detail = dashboard.render_route_html("/operate/risk-packages/package-demo")
    learn = dashboard.render_route_html("/learn/risk-packages")
    tests_page = dashboard.render_tests_html()
    bundles = dashboard.render_route_html("/operate/bundles")

    assert "Risk work packages" in index and "package-demo" in index
    assert "CR-DEMO-001" in detail and "scenario-demo" in detail
    assert "planning and grouping" in learn.lower()
    assert "not a forensic bundle" in learn.lower()
    assert "legacy" in learn.lower() and "evidence-packages" in learn
    assert "Create" not in index and "data-definition-editor" not in detail
    assert "Risk Work Packages" in tests_page
    assert "evidence bundle" in tests_page.lower()
    assert "Risk work package" not in bundles
    assert 'class="risk-evidence-grid"' in detail
    assert 'class="asset-pill-row risk-candidate-pills"' in detail
    css = (ROOT / "dwarf" / "dashboard" / "static" / "css" / "base.css").read_text(encoding="utf-8")
    assert ".risk-evidence-grid" in css
    assert ".risk-candidate-pills .pill" in css
    assert dashboard.render_route_html("/operate/risk-packages/missing") is None


def test_routes_navigation_api_docs_concepts_and_visual_audit_are_reconciled(monkeypatch):
    from profile_manager.data import concepts, learn_api, sub_nav

    routes = {route for group in learn_api.html_route_groups() for route in group["routes"]}
    assert "/operate/risk-packages" in routes
    assert "/operate/risk-packages/<id>" in routes
    assert "/learn/risk-packages" in routes
    assert any(item["url"] == "/operate/risk-packages" for item in sub_nav.OPERATE_SUB_NAV)
    assert any(item["url"] == "/learn/risk-packages" for item in sub_nav.LEARN_SUB_NAV)
    assert any(item["slug"] == "risk-work-package" for item in concepts.CONCEPTS)
    assert any("/api/risk-packages" in endpoint["path"] for endpoint in learn_api.ENDPOINTS)
    assert 'href="/operate/risk-packages"' in dashboard.render_route_html("/operate")
    assert 'href="/learn/risk-packages"' in dashboard.render_route_html("/learn")
    source = (ROOT / "tools" / "dashboard_visual_audit.js").read_text(encoding="utf-8")
    assert repr("/operate/risk-packages") in source
    assert repr("/learn/risk-packages") in source
