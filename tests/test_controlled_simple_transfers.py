import json
from pathlib import Path

from scripts import runtime_controlled_simple_transfers as subject


ROOT = Path(__file__).resolve().parents[1]


def test_frozen_simple_transfer_dataset_has_non_vacuous_real_attempt_volume():
    manifest = json.loads(
        (ROOT / "dwarf/corpora/cardano-measurement/simple-transfer-v1.json").read_text()
    )

    assert manifest["schema_version"] == "v1"
    assert manifest["attempt_count"] >= 30
    assert manifest["expected_outcomes"] == {
        "accepted_minimum": 30,
        "rejected_minimum": 5,
        "timed_out_maximum": 0,
    }
    assert manifest["transaction_kind"] == "signed-simple-payment"
    assert manifest["simulation_allowed"] is False
    assert manifest["seed"] == "0x51A9E001"
    assert manifest["dataset_sha256"].startswith("sha256:")


def test_accounting_payload_preserves_every_terminal_outcome_and_nanoseconds():
    records = [
        {
            "attempt_id": "simple-transfer-0000",
            "transaction_id": "aa",
            "transaction_bytes": 300,
            "outcome": "accepted",
            "elapsed_nanos": 2_184,
        },
        {
            "attempt_id": "simple-transfer-0001",
            "transaction_id": "bb",
            "transaction_bytes": 320,
            "outcome": "rejected",
            "elapsed_nanos": 5_500,
        },
        {
            "attempt_id": "simple-transfer-0002",
            "transaction_id": None,
            "transaction_bytes": 280,
            "outcome": "timeout",
            "elapsed_nanos": 10_000,
        },
    ]

    payload = subject.accounting_payload(records)

    assert payload == {
        "attempted": 3,
        "successful": 1,
        "rejected": 1,
        "timed_out": 1,
        "bytes": 900,
        "batches": 1,
        "backlog": 0,
        "attempts": [
            {
                "input_id": "simple-transfer-0000",
                "tx_id": "aa",
                "outcome": "accepted",
                "elapsed_nanos": 2_184,
                "elapsed_micros": 2.184,
                "bytes": 300,
            },
            {
                "input_id": "simple-transfer-0001",
                "tx_id": "bb",
                "outcome": "rejected",
                "elapsed_nanos": 5_500,
                "elapsed_micros": 5.5,
                "bytes": 320,
            },
            {
                "input_id": "simple-transfer-0002",
                "outcome": "timeout",
                "elapsed_nanos": 10_000,
                "elapsed_micros": 10.0,
                "bytes": 280,
            },
        ],
    }


def test_scenarios_are_separate_exact_real_node_legs_with_shared_card_contract():
    cases = {
        "amaru": (
            "client-example-simple-transfer-amaru",
            "profile-x-amaru-cbor-fix-regression-nanoseconds-v2",
            "d3a6dafcced78f5809a96619e883cf04911d2bdc",
        ),
        "cardano-node": (
            "client-example-simple-transfer-cardano",
            "profile-v-cardano-measurement-nanoseconds-v2",
            "fef83fed01d7926f3de83b3b917be5a4a48768b5",
        ),
    }
    for implementation, (scenario_id, profile, revision) in cases.items():
        scenario = json.loads(
            (ROOT / "dwarf/scenarios" / f"{scenario_id}.yaml").read_text()
        )
        assert scenario["target"]["implementation"] == implementation
        assert scenario["target"]["source_revision"] == revision
        assert scenario["profile"] == profile
        assert scenario["measurement_profile"] == (
            "amaru-security-patched"
            if implementation == "amaru"
            else "cardano-security-patched"
        )
        assert scenario["seed"] == "0x51A9E001"
        transfer = next(
            row
            for row in scenario["load"]
            if row["primitive"] == "runtime_controlled_simple_transfers"
        )
        assert transfer["attempt_count"] >= 30
        assert transfer["measurement_implementation"] == implementation
        assert "mixed" not in scenario_id


def test_card_contract_forbids_comparison_and_keeps_measurements_non_gating():
    import yaml

    contract = yaml.safe_load(
        (ROOT / "dwarf/docs/client-examples/contracts/06-simple-transfer.yaml").read_text()
    )
    assert contract["id"] == "client-example-06-simple-transfer"
    assert contract["scope"]["real_node_required"] is True
    assert contract["scope"]["simulation_allowed"] is False
    assert contract["verdict_policy"]["measurements_gate_by_default"] is False
    assert any("benchmark" in claim.lower() for claim in contract["explicit_non_claims"])
    assert {leg["implementation"] for leg in contract["scenario_legs"]} == {
        "amaru",
        "cardano-node",
    }
