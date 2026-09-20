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

V1_CARD_ID = "client-example-03-invalid-mini-protocol"
V1_PROFILES = {
    "amaru": "profile-q-amaru-measurement-patched",
    "cardano-node": "profile-t-cardano-measurement-patched",
}
V2_PROFILES = {
    "amaru": "profile-u-amaru-measurement-nanoseconds-v2",
    "cardano-node": "profile-v-cardano-measurement-nanoseconds-v2",
}
V2_TARGETS = {
    "amaru": {
        "measurement_revision": "nanoseconds-v2",
        "patch_set_sha256": "4c22d7b0c29a705d1471dcfb6ee09a306c936ce83fd47f808fb2bbb8c2c75de0",
        "patched_executable_digest": "sha256:05233bac96c1914a232a2d9c5a704f08401aff0b20356c015e848f295b919b78",
        "patched_image_digest": "sha256:c3f139e87b4ada079a4dc5c656a2ca06c6dc30ea55719d54bedb772c836de862",
        "patch_manifest_sha256": "sha256:1012e64ad3a3f5a9abd1aea061d5036fcc8e936392f82d5829e3df7733ab6b99",
    },
    "cardano-node": {
        "measurement_revision": "nanoseconds-v2",
        "patch_set_sha256": "1c52fa42b7fd9ee3403165a5269ae851665536d2b920ed8be6fdbe490d1ed93c",
        "patched_executable_digest": "sha256:3fb83f12ac1152e96c884c756a505484a39d9200da6c59b24678ac61218220eb",
        "patched_image_digest": "sha256:956ae21cf9141a7149692392453ea31ece00e33960c2cca0f79548beeacf7370",
        "patch_manifest_sha256": "sha256:06405007512eb8040898fbe53984b43bb5419100e5c7c64954588ee618ad7071",
    },
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
                if key != "patched_image_digest" or card["id"] == V1_CARD_ID:
                    assert target[key] == value
            assert target["stock_mode"] == "stock"
            assert target["patched_mode"] == "patched"


def test_card03_keeps_v1_while_future_cards_pin_nanoseconds_v2():
    for card in _cards():
        profiles = {
            leg["implementation"]: leg["profile"]
            for leg in card["scenario_legs"]
        }
        if card["id"] == V1_CARD_ID:
            assert profiles == V1_PROFILES
            assert all(
                "measurement_revision" not in target
                for target in card["targets"].values()
            )
            continue
        assert profiles == V2_PROFILES
        for implementation, expected in V2_TARGETS.items():
            for key, value in expected.items():
                assert card["targets"][implementation][key] == value


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


def _card(card_id):
    return next(item for item in _cards() if item["id"] == card_id)


def test_card01_records_completed_finding_and_exact_fixed_revision_regression():
    resolution = _card("client-example-01-cbor-decoding")["approved_resolution"]

    assert resolution["kind"] == "completed-security-finding-and-regression"
    assert resolution["historical_finding"] == {
        "run_id": "20260920T135054Z-28289dcd",
        "source_revision": "b159172f25a9c389f82f20bca4f15e3032791638",
        "failed_assertion": "cbor_conformance_clean",
        "finding_id": "amaru-plutus-data-byte-string-bound",
        "execution_classification": "completed_with_security_finding",
        "security_verdict": "fail",
    }
    assert resolution["regression"] == {
        "scenario_id": "client-example-cbor-decoding-amaru-d3a6dafc-regression",
        "profile": "profile-x-amaru-cbor-fix-regression-nanoseconds-v2",
        "source_revision": "d3a6dafcced78f5809a96619e883cf04911d2bdc",
        "upstream_fix_revision": "d3a6dafcced78f5809a96619e883cf04911d2bdc",
        "measurement_revision": "nanoseconds-v2",
        "corpus_digest": "sha256:8f5b409f5c2b25b31e392365bab0e9a703a526e776a4f34dfbc3204f9b022dd9",
        "required_result": "pass",
    }


def test_card02_requires_additive_topology_with_live_on_chain_plutus_v2():
    resolution = _card("client-example-02-plutus-vm")["approved_resolution"]

    assert resolution["kind"] == "on-chain-plutus-v2-topology"
    assert resolution["scenario_id"] == "client-example-plutus-vm-amaru-onchain-v2"
    assert resolution["profile"] == "profile-w-amaru-measurement-plutus-v2"
    assert resolution["preserve_existing_topologies"] is True
    assert resolution["requirements"] == [
        "generated-genesis-digests-retained",
        "live-protocol-parameters-digest-retained",
        "live-plutus-v2-cost-model-matches-pinned-model",
        "30-valid-transactions-included",
        "30-expected-invalid-transactions-included",
        "transaction-hashes-and-identifiers-retained",
        "continued-amaru-chain-progress",
    ]


def test_card04_replaces_raw_monotonicity_with_canonical_progress_v2():
    resolution = _card("client-example-04-block-application")["approved_resolution"]

    assert resolution["kind"] == "canonical-progress-v2"
    assert resolution["preserve_v1_runs"] is True
    assert resolution["scenario_ids"] == {
        "amaru": "client-example-block-application-amaru-canonical-v2",
        "cardano-node": "client-example-block-application-cardano-canonical-v2",
    }
    assert resolution["raw_evidence"] == [
        "adopted-block-events",
        "same-height-hash-switches",
        "rollback-and-fork-events",
        "application-timings",
    ]
    assert resolution["pass_conditions"] == [
        "bounded-canonical-progress",
        "final-convergence",
        "complete-required-correlations",
        "no-fatal-health-signal",
    ]
    assert resolution["fail_conditions"] == [
        "no-progress",
        "non-convergence",
        "excessive-or-continuing-oscillation",
        "missing-correlations",
        "fatal-health-signal",
    ]
    assert resolution["same_height_switch_alone_fails"] is False
