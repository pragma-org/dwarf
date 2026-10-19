"""Contracts only: catalog coverage never establishes runtime/security success."""
from copy import deepcopy
from pathlib import Path
import json

import pytest
import yaml
from jsonschema import Draft202012Validator

from profile_manager.data.catalog_definitions import InvalidDefinitionError
from profile_manager import security_campaign_suite as api


ROOT = Path(__file__).resolve().parents[1] / "dwarf"
SECURITY = ROOT / "security-suites/github-security-regression-v1.yaml"
MEASUREMENTS = ROOT / "qualification-suites/node-measurements-v1.yaml"


def security():
    return api.load_security_campaign_suite(SECURITY)


def measurement():
    return api.load_measurement_suite(MEASUREMENTS)


def first_entry(document):
    return document["groups"][0]["entries"][0]


def test_initial_catalogs_resolve_and_preserve_security_measurement_separation():
    suite = security()
    assert len(suite["groups"]) == 6
    assert suite["result_policy"] == "security-independent-of-measurement-completeness"
    assert measurement()["result_policy"] == "security-independent-of-measurement-completeness"


@pytest.mark.parametrize("field", [
    "security_objective", "target_nodes", "reference_control", "workload_fault",
    "oracle", "evidence_rule", "normal_control",
])
def test_every_security_entry_requires_decisive_contract(field):
    suite = security()
    del first_entry(suite)[field]
    with pytest.raises(InvalidDefinitionError, match=field):
        api.validate_security_campaign_suite(suite)


def test_mixed_campaign_is_one_topology_with_exact_per_node_attachments():
    entries = [e for g in security()["groups"] for e in g["entries"]]
    mixed = next(e for e in entries if {n["implementation"] for n in e["topology"]["nodes"]} == {"amaru", "cardano-node"})
    assert mixed["topology"]["mode"] == "shared-runtime"
    assert {a["node_id"] for a in mixed["measurement_attachments"]} == {"node1", "node2"}
    for attachment in mixed["measurement_attachments"]:
        api.validate_measurement_attachment(attachment, nodes=mixed["topology"]["nodes"])
        assert attachment["result_namespace"].startswith(f"nodes/{attachment['node_id']}/measurements/")


@pytest.mark.parametrize("mutation", ["implementation", "mode"])
def test_library_topology_preserves_source_implementation_and_runtime(mutation):
    suite = security()
    entry = first_entry(suite)
    assert entry["scenario_id"] == "amaru-cbor-tx-body-fuzz-structured"
    if mutation == "implementation":
        entry["topology"]["nodes"][0]["implementation"] = "cardano-node"
    else:
        entry["topology"]["mode"] = "shared-runtime"
    with pytest.raises(InvalidDefinitionError, match="topology"):
        api.validate_security_campaign_suite(suite)


@pytest.mark.parametrize("mutation,match", [
    ({"node_id": "absent"}, "node"),
    ({"implementation": "cardano-node"}, "implementation"),
    ({"measurement_profile": "absent-profile"}, "profile"),
    ({"measurement_profile": "cardano-security-default"}, "implementation"),
    ({"result_namespace": "nodes/other/measurements/stock"}, "namespace"),
    ({"result_namespace": "nodes/node1/measurements/../secret"}, "namespace"),
])
def test_attachment_rejects_wrong_binding_or_unsafe_namespace(mutation, match):
    nodes = [{"node_id": "node1", "implementation": "amaru"}]
    attachment = {"node_id": "node1", "implementation": "amaru", "measurement_profile": "amaru-security-default", "result_namespace": "nodes/node1/measurements/stock"}
    attachment.update(mutation)
    with pytest.raises(InvalidDefinitionError, match=match):
        api.validate_measurement_attachment(attachment, nodes=nodes)


def test_all_catalog_taps_have_non_vacuous_rules_and_stock_headers_are_selected():
    suite = measurement()
    catalog = {yaml.safe_load(p.read_text())["id"] for p in (ROOT / "measurements").glob("*.yaml")}
    assert {e["measurement_id"] for e in suite["exercises"]} == catalog
    for exercise in suite["exercises"]:
        assert exercise["rule"]
        assert exercise["rule"].get("min_samples", 1) >= 1
    attachments = [a for g in security()["groups"] for e in g["entries"] for a in e["measurement_attachments"]]
    assert {"amaru-stock-header-validation", "cardano-stock-header-validation"} <= {m for a in attachments for m in a.get("additional_measurements", [])}


@pytest.mark.parametrize("rule", [
    {"min_samples": 1}, {"required_event": "restart_started"},
    {"outcomes": ["accepted", "rejected"]}, {"correlation": ["run_id", "input_id"]},
    {"restart": True}, {"progress": {"min_blocks": 1}},
    {"coverage_envelope": {"min_covered_functions": 1, "required_paths": ["decode"]}},
])
def test_rule_forms_are_supported(rule):
    suite = measurement()
    suite["exercises"][0]["rule"] = rule
    assert api.validate_measurement_suite(suite) is suite


@pytest.mark.parametrize("rule", [{}, {"min_samples": 0}, {"restart": False}, {"outcomes": []}, {"progress": {"min_blocks": 0}}])
def test_vacuous_rules_are_rejected(rule):
    suite = measurement()
    suite["exercises"][0]["rule"] = rule
    with pytest.raises(InvalidDefinitionError):
        api.validate_measurement_suite(suite)


def test_missing_runtime_cannot_be_measurement_complete():
    suite = measurement()
    readiness = api.measurement_suite_readiness(suite)
    assert readiness["measurement_complete"] is False
    assert readiness["status"] == "blocked"
    assert "amaru-coverage-production-paths" in readiness["missing_runtime"]
    assert "cardano-coverage-production-paths" in readiness["missing_runtime"]
    # Even an implemented collector needs actual evidence, never an available badge.
    for exercise in suite["exercises"]:
        exercise["runtime"] = {"status": "implemented"}
        exercise.pop("fallback", None)
    pending = api.measurement_suite_readiness(suite)
    assert pending["measurement_complete"] is False
    assert pending["status"] == "pending-evidence"
    assert "security_result" not in readiness


@pytest.mark.parametrize("where,field,value,match", [
    ("entry", "scenario_id", "absent-scenario", "scenario"),
    ("topology", "deployment_profile", "absent-profile", "profile"),
    ("entry", "version", "11.1.2", "version"),
    ("entry", "version_policy", "pinned", "operator-selected"),
])
def test_bad_security_references_and_required_pins_are_rejected(where, field, value, match):
    suite = security()
    entry = first_entry(suite)
    (entry["topology"] if where == "topology" else entry)[field] = value
    with pytest.raises(InvalidDefinitionError, match=match):
        api.validate_security_campaign_suite(suite)


def test_pinned_source_scenario_is_candidate_only():
    suite = security()
    first_entry(suite)["scenario_id"] = "consensus-rollback-recovery-within-k-differential"
    with pytest.raises(InvalidDefinitionError, match="pinned"):
        api.validate_security_campaign_suite(suite)
    candidates = [c for g in suite["groups"] for c in g.get("blocked_candidates", [])]
    assert any("rollback" in c["scenario_id"] for c in candidates)


def test_profile_cannot_reintroduce_required_node_version_pins():
    suite = security()
    first_entry(suite)["topology"]["deployment_profile"] = "profile-r-amaru-measurement-stock-control"
    with pytest.raises(InvalidDefinitionError, match="pinned"):
        api.validate_security_campaign_suite(suite)


def test_declared_profile_must_preserve_source_scenario_binding():
    suite = security()
    entry = next(e for g in suite["groups"] for e in g["entries"] if e["scenario_id"] == "cardano-measurement-e2e-stock-portable")
    entry["topology"]["deployment_profile"] = "profile-o-amaru-target-latest-confirmed"
    with pytest.raises(InvalidDefinitionError, match="profile"):
        api.validate_security_campaign_suite(suite)


def test_attachment_additional_tap_must_exist_and_match_implementation():
    suite = security()
    entry = next(e for g in suite["groups"] for e in g["entries"] if e["measurement_attachments"])
    attachment = entry["measurement_attachments"][0]
    attachment["additional_measurements"] = ["absent-measurement"]
    with pytest.raises(InvalidDefinitionError, match="measurements"):
        api.validate_measurement_attachment(attachment, nodes=entry["topology"]["nodes"])
    attachment["additional_measurements"] = ["cardano-stock-header-validation"] if attachment["implementation"] == "amaru" else ["amaru-stock-header-validation"]
    with pytest.raises(InvalidDefinitionError, match="implementation"):
        api.validate_measurement_attachment(attachment, nodes=entry["topology"]["nodes"])


def test_fallback_cannot_execute_a_pinned_candidate():
    suite = measurement()
    fallback = next(e["fallback"] for e in suite["exercises"] if e["fallback"]["status"] == "blocked")
    fallback["status"] = "ready"
    with pytest.raises(InvalidDefinitionError, match="pinned"):
        api.validate_measurement_suite(suite)


def test_loaders_reject_malformed_documents_and_wrong_suite_kind(tmp_path):
    path = tmp_path / "suite.yaml"
    path.write_text("not: [valid")
    with pytest.raises(InvalidDefinitionError, match="YAML"):
        api.load_security_campaign_suite(path)
    path.write_text("[]")
    with pytest.raises(InvalidDefinitionError, match="object"):
        api.load_measurement_suite(path)
    with pytest.raises(InvalidDefinitionError, match="security suite"):
        api.validate_security_campaign_suite(measurement())
    with pytest.raises(InvalidDefinitionError, match="measurement suite"):
        api.validate_measurement_suite(security())


@pytest.mark.parametrize("change", ["missing-measurement", "missing-scenario", "missing-activation", "missing-runtime-reason", "missing-tap"])
def test_measurement_fallbacks_and_coverage_are_validated(change):
    suite = measurement()
    if change == "missing-measurement":
        suite["exercises"][0]["measurement_id"] = "absent-tap"
    elif change == "missing-scenario":
        suite["exercises"][0]["scenario_id"] = "absent-scenario"
    elif change == "missing-activation":
        fallback = next(e["fallback"] for e in suite["exercises"] if "fallback" in e)
        del fallback["activation_condition"]
    elif change == "missing-runtime-reason":
        missing = next(e["runtime"] for e in suite["exercises"] if e["runtime"]["status"] == "missing")
        del missing["reason"]
    else:
        suite["exercises"].pop()
    with pytest.raises(InvalidDefinitionError):
        api.validate_measurement_suite(suite)


def test_mixed_topology_cannot_lie_about_scenario_node_implementation():
    suite = security()
    entry = next(e for g in suite["groups"] for e in g["entries"] if e["topology"]["mode"] == "shared-runtime" and len({n["implementation"] for n in e["topology"]["nodes"]}) == 2)
    entry["topology"]["nodes"][0]["implementation"] = "cardano-node" if entry["topology"]["nodes"][0]["implementation"] == "amaru" else "amaru"
    with pytest.raises(InvalidDefinitionError, match="topology"):
        api.validate_security_campaign_suite(suite)


@pytest.mark.parametrize("mutation", [
    {"node_id": "wrong-producer"},
    {"implementation": "amaru"},
])
def test_profile_topology_uses_actual_node_ids_including_supporting_producer(mutation):
    suite = security()
    entry = next(e for g in suite["groups"] for e in g["entries"] if e["scenario_id"] == "amaru-measurement-e2e-stock-portable")
    assert {n["node_id"]: n["implementation"] for n in entry["topology"]["nodes"]} == {"amaru1": "amaru", "bootstrap-cardano": "cardano-node"}
    assert entry["target_nodes"] == ["amaru1"]
    producer = next(node for node in entry["topology"]["nodes"] if node["node_id"] == "bootstrap-cardano")
    producer.update(mutation)
    with pytest.raises(InvalidDefinitionError, match="topology"):
        api.validate_security_campaign_suite(suite)


@pytest.mark.parametrize("mutation", [
    {"node_id": "wrong-node"},
    {"implementation": "amaru"},
])
def test_profile_topology_checks_nodes_without_measurement_attachments(mutation):
    suite = security()
    entry = next(e for g in suite["groups"] for e in g["entries"] if e["scenario_id"] == "cardano-measurement-e2e-stock-portable")
    entry["target_nodes"] = ["node1"]
    entry["measurement_attachments"] = [a for a in entry["measurement_attachments"] if a["node_id"] == "node1"]
    node = next(n for n in entry["topology"]["nodes"] if n["node_id"] == "node3")
    node.update(mutation)
    with pytest.raises(InvalidDefinitionError, match="topology"):
        api.validate_security_campaign_suite(suite)


def test_duplicate_entries_and_namespaces_are_rejected():
    suite = security()
    suite["groups"][0]["entries"].append(deepcopy(first_entry(suite)))
    with pytest.raises(InvalidDefinitionError, match="duplicate|non-unique"):
        api.validate_security_campaign_suite(suite)
    suite = security()
    entry = next(e for g in suite["groups"] for e in g["entries"] if e["measurement_attachments"])
    entry["measurement_attachments"].append(deepcopy(entry["measurement_attachments"][0]))
    with pytest.raises(InvalidDefinitionError, match="duplicate|non-unique"):
        api.validate_security_campaign_suite(suite)


def test_schemas_are_valid_draft_2020_12():
    for name in ("security-campaign-suite", "measurement-attachment"):
        path = ROOT / "spec/v1" / f"{name}.schema.json"
        assert path.exists(), f"missing schema {name}"
        Draft202012Validator.check_schema(json.loads(path.read_text()))
