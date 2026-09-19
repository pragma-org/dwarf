import json
from pathlib import Path

import jsonschema
import yaml


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "dwarf/spec/v1/client-example-acceptance-card.schema.json"
CARDS_DIR = ROOT / "dwarf/docs/client-examples/contracts"

EXPECTED_IDS = {
    "client-example-01-cbor-decoding",
    "client-example-02-plutus-vm",
    "client-example-03-invalid-mini-protocol",
    "client-example-04-block-application",
    "client-example-05-restart-recovery-sync",
}

EXPECTED_TARGETS = {
    "amaru": {
        "version": "10.11.20260912",
        "source_revision": "b159172f25a9c389f82f20bca4f15e3032791638",
        "stock_image_digest": "sha256:45d46a6ba7147bfa95d96c103820542a9e3ac3602c4c316cc0d04bbd6d71489e",
        "patched_image_digest": "sha256:dacb2351e69ab1d0bbfbc569b222d1bde40d9a158e79c556b30df554375addcc",
    },
    "cardano-node": {
        "version": "11.1.2",
        "source_revision": "fef83fed01d7926f3de83b3b917be5a4a48768b5",
        "stock_image_digest": "sha256:6365403f44713d0a046865fb0466503ef207b71beae1b1ffece7f4399356db9f",
        "patched_image_digest": "sha256:c74c3deafac54ed4d30da21952fce579c3293919993d31081e18787605757b8c",
    },
}


def _cards():
    return [
        yaml.safe_load(path.read_text(encoding="utf-8"))
        for path in sorted(CARDS_DIR.glob("*.yaml"))
    ]


def test_exactly_five_cards_validate_against_the_contract_schema():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    cards = _cards()
    assert {card["id"] for card in cards} == EXPECTED_IDS
    validator = jsonschema.Draft202012Validator(schema)
    for card in cards:
        validator.validate(card)


def test_every_card_pins_both_real_node_targets_and_modes():
    for card in _cards():
        assert card["scope"]["real_node_required"] is True
        assert card["scope"]["simulation_allowed"] is False
        assert set(card["targets"]) == set(EXPECTED_TARGETS)
        for implementation, expected in EXPECTED_TARGETS.items():
            target = card["targets"][implementation]
            for key, value in expected.items():
                assert target[key] == value
            assert target["stock_mode"] == "stock"
            assert target["patched_mode"] == "patched"


def test_every_card_defines_non_vacuous_security_and_measurement_gates():
    for card in _cards():
        assert card["functional_requirement"]["expected_result"]
        assert card["security_assertions"]
        assert card["measurements"]
        assert all(metric["boundary"] for metric in card["measurements"])
        assert all(metric["minimum_sample_floor"] >= 1 for metric in card["measurements"])
        assert card["evidence_quality_rules"]["very_small_sample"] == "n < 5"
        assert card["evidence_quality_rules"]["small_sample"] == "5 <= n < 30"
        assert card["evidence_quality_rules"]["useful_sample"] == "n >= 30"
        assert card["required_collectors"]
        assert card["retained_artifacts"]
        assert card["report_routes"]
        assert card["legitimate_claims"]
        assert card["explicit_non_claims"]


def test_cards_keep_measurements_non_gating_and_walkthrough_deferred():
    for card in _cards():
        assert card["verdict_policy"]["security_assertions_authoritative"] is True
        assert card["verdict_policy"]["measurements_gate_by_default"] is False
        assert card["verdict_policy"]["threshold_gate"] == "explicit-opt-in-only"
        assert card["deferred_scope"]["presentation_walkthrough"] is True
        assert card["deferred_scope"]["mixed_node_comparison"] is True
        assert card["deferred_scope"]["weekly_automation"] is True
        assert card["deferred_scope"]["stable_release_thresholds"] is True


def test_cards_pin_workload_identity_and_stop_conditions():
    for card in _cards():
        workload = card["workload"]
        assert workload["seed"].startswith("0x")
        assert workload["digest"].startswith("sha256:")
        assert workload["warm_up_seconds"] >= 0
        assert workload["duration_seconds"] > 0
        assert workload["stop_conditions"]
        assert card["environment"]["hardware_fingerprint"] == "cardano-box-2026-09-19"


def test_invalid_protocol_card_names_the_three_exact_live_amaru_boundaries():
    card = next(
        item
        for item in _cards()
        if item["id"] == "client-example-03-invalid-mini-protocol"
    )

    assert {
        metric["boundary"]
        for metric in card["measurements"]
        if metric["name"].startswith("handshake_")
    } == {
        "mux-cbor-item",
        "mini-protocol-decode",
        "handshake-negotiation",
    }
