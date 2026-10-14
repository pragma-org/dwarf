from __future__ import annotations

import hashlib
import json

from profile_manager.data.scenarios import _list_scenarios_for_compare
from profile_manager.data.threat_risk_coverage import threat_risk_discovery_index
from profile_manager.views.threat_coverage import current_threat_coverage_data


_PRE_EXTRACTION_CONTRACT_SHA256 = (
    "61f2b338fab9ff8f2bd54d232fb29b07291cd42030837e9e068d668c031f6fad"
)


def _canonical_contract(data: dict) -> dict:
    contract = {}
    for section in ("threats", "risks"):
        contract[section] = []
        for row in data[section]:
            fields = {
                key: row.get(key)
                for key in (
                    "id", "vector", "why", "title", "desc", "surface", "surfaces"
                )
                if row.get(key) is not None
            }
            fields["scenarios"] = sorted(
                item["id"] for item in row.get("scenarios") or []
            )
            contract[section].append(fields)
    return contract


def test_shared_index_preserves_pre_extraction_threat_risk_contract():
    coverage = current_threat_coverage_data(include_measurements=False)
    raw = json.dumps(
        _canonical_contract(coverage), sort_keys=True, separators=(",", ":")
    ).encode()
    assert hashlib.sha256(raw).hexdigest() == _PRE_EXTRACTION_CONTRACT_SHA256

    scenario_ids = {row["id"] for row in _list_scenarios_for_compare()}
    index = threat_risk_discovery_index(coverage, scenario_ids=scenario_ids)

    assert len(index["facets"]["threat_model"]) == 37  # 36 TM rows + Unmapped
    assert len(index["facets"]["risk_register"]) == 36  # 35 RR rows + Unmapped
    assert index["facets"]["threat_model"][0]["label"].startswith("TM-")
    assert index["facets"]["risk_register"][0]["label"].startswith("RR-")
    assert index["facets"]["threat_model"][-1]["value"] == "unmapped"
    assert index["facets"]["risk_register"][-1]["value"] == "unmapped"
    assert set(index["by_scenario"]) == scenario_ids


def test_shared_index_keeps_vetted_links_and_unmapped_state_exact():
    coverage = current_threat_coverage_data(include_measurements=False)
    scenario_ids = {row["id"] for row in _list_scenarios_for_compare()}
    index = threat_risk_discovery_index(coverage, scenario_ids=scenario_ids)

    mini = index["by_scenario"]["cardano-amaru-miniprotocol-security-local"]
    assert {"TM-001", "TM-015"} <= set(mini["threat_model_ids"])
    assert {"RR-001", "RR-015"} <= set(mini["risk_register_ids"])

    control = index["by_scenario"]["cardano-amaru-relay-bootstrap-control-local"]
    assert control["threat_model_ids"] == ["unmapped"]
    assert control["risk_register_ids"] == ["unmapped"]

    linked = index["by_scenario"]["client-example-simple-transfer-amaru"]
    assert linked["threat_model_ids"] == ["TM-012", "TM-024"]
    assert linked["risk_register_ids"] == ["RR-012", "RR-019"]


def test_shared_index_does_not_infer_links_for_unknown_catalog_identity():
    coverage = current_threat_coverage_data(include_measurements=False)
    index = threat_risk_discovery_index(
        coverage, scenario_ids={"future-unmapped-definition"}
    )

    assert index["by_scenario"]["future-unmapped-definition"] == {
        "threat_model_ids": ["unmapped"],
        "risk_register_ids": ["unmapped"],
        "threat_model_search": [],
        "risk_register_search": [],
        "coverage_surfaces": [],
    }


def test_subject_projection_is_lossless_and_uses_structural_precedence():
    from profile_manager.data.scenario_discovery import (
        catalog_discovery,
        group_scenario_subjects,
    )
    from profile_manager.profiles import load_profiles

    raw = _list_scenarios_for_compare()
    ids = {row["id"] for row in raw}
    coverage = current_threat_coverage_data(include_measurements=False)
    index = threat_risk_discovery_index(coverage, scenario_ids=ids)
    profile_shapes = {
        profile.id: (profile.node_count, profile.amaru_node_count)
        for profile in load_profiles()
    }
    rows = catalog_discovery(
        raw, profile_shapes=profile_shapes, threat_risk_by_scenario=index["by_scenario"]
    )
    by_id = {row["id"]: row for row in rows}

    assert len(rows) == len(ids) == 351
    subjects = group_scenario_subjects(rows)
    assert [row["key"] for row in subjects] == [
        "cbor", "n2n", "opcert", "plutus", "consensus", "recovery",
        "measurements", "utilities",
    ]
    variants = [
        variant["id"]
        for subject in subjects
        for child in subject["children"]
        for family in child["families"]
        for variant in family["variants"]
    ]
    assert len(variants) == 351
    assert set(variants) == ids

    assert by_id["amaru-cbor-block-fuzz"]["subject_path"] == ["cbor"]
    assert by_id[
        "client-example-cbor-decoding-amaru-d3a6dafc-regression"
    ]["subject_path"] == ["cbor"]
    assert by_id["amaru-cbor-tx-body-fuzz"]["subject_path"] == ["cbor", "tx-body"]
    assert by_id[
        "cardano-node-cbor-tx-body-fuzz-structured"
    ]["subject_path"] == ["cbor", "tx-body"]
    assert by_id[
        "ledger-block-apply-isvalid-false-donation-value-creation-amaru-cardano-node"
    ]["subject_path"] == ["cbor", "tx-body"]
    # A protocol case containing the word body remains N2N, not structural TX Body.
    assert by_id[
        "runtime-substrate-txsubmission-unexpected-body-mixed-amaru"
    ]["subject_path"] == ["n2n"]
    assert by_id["opcert-header-validation-cases-amaru-20260918"]["subject_path"] == ["opcert"]
    assert by_id["client-example-plutus-vm-cardano"]["subject_path"] == ["plutus"]
    assert by_id[
        "consensus-epoch-boundary-active-nonce-differential"
    ]["subject_path"] == ["consensus"]
    assert by_id["client-example-restart-recovery-sync-amaru"]["subject_path"] == ["recovery"]
    assert by_id["amaru-measurement-overhead-calibration-stock"]["subject_path"] == ["measurements"]
    assert by_id["runtime-bundle-timeline-example-smoke"]["subject_path"] == ["utilities"]
    assert "opcert" in by_id[
        "opcert-header-validation-cases-amaru-20260918"
    ]["search_text"]
    assert "tx body" in by_id["amaru-cbor-tx-body-fuzz"]["search_text"]


def test_discovery_rows_receive_only_canonical_threat_risk_links():
    from profile_manager.data.scenario_discovery import catalog_discovery
    from profile_manager.profiles import load_profiles

    raw = _list_scenarios_for_compare()
    ids = {row["id"] for row in raw}
    coverage = current_threat_coverage_data(include_measurements=False)
    index = threat_risk_discovery_index(coverage, scenario_ids=ids)
    profile_shapes = {
        profile.id: (profile.node_count, profile.amaru_node_count)
        for profile in load_profiles()
    }
    rows = catalog_discovery(
        raw, profile_shapes=profile_shapes, threat_risk_by_scenario=index["by_scenario"]
    )
    by_id = {row["id"]: row for row in rows}

    linked = by_id["client-example-simple-transfer-amaru"]
    assert linked["threat_model_ids"] == ["TM-012", "TM-024"]
    assert linked["risk_register_ids"] == ["RR-012", "RR-019"]
    assert all(label.startswith("TM-") for label in linked["threat_model_search"])
    assert all(label.startswith("RR-") for label in linked["risk_register_search"])
    unmapped = by_id["cardano-amaru-relay-bootstrap-control-local"]
    assert unmapped["threat_model_ids"] == ["unmapped"]
    assert unmapped["risk_register_ids"] == ["unmapped"]


def test_both_basic_routes_receive_the_same_canonical_threat_risk_join():
    from profile_manager import dashboard
    from profile_manager.views.scenarios import render_operate_scenarios

    for html in (dashboard.render_route_html("/run"), render_operate_scenarios()):
        assert 'data-facet-threat-model="TM-012 TM-024"' in html
        assert 'data-facet-risk-register="RR-012 RR-019"' in html
        assert 'data-scenario-id="client-example-simple-transfer-amaru"' in html
        assert 'data-facet-threat-model="unmapped"' in html
        assert 'data-facet-risk-register="unmapped"' in html


def test_discovery_enrichment_does_not_change_run_resolution_or_digest():
    from profile_manager.data.operate_run_wizard import run_wizard_catalog

    scenario_id = "client-example-invalid-mini-protocol-amaru"
    raw = _list_scenarios_for_compare()
    ids = {row["id"] for row in raw}
    index = threat_risk_discovery_index(
        current_threat_coverage_data(include_measurements=False),
        scenario_ids=ids,
    )
    plain = run_wizard_catalog(requested_scenario_id=scenario_id)
    enriched = run_wizard_catalog(
        requested_scenario_id=scenario_id,
        threat_risk_index=index,
    )

    assert enriched["default_scenario_id"] == plain["default_scenario_id"] == scenario_id
    assert enriched["default_plan"] == plain["default_plan"]
    assert enriched["default_plan_digest"] == plain["default_plan_digest"]
    assert [row["id"] for row in enriched["scenarios"]] == [
        row["id"] for row in plain["scenarios"]
    ]
