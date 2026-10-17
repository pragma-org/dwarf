import cbor2
import json
from pathlib import Path


from profile_manager.primitives import load_registry
from profile_manager.scenario import semantic_validate_scenario
from profile_manager.submit_primitives import RuntimeMultiassetCompactBoundary
from scripts.runtime_multiasset_compact_boundary import (
    CONTROL_ASSET_COUNT,
    MUTANT_ASSET_COUNT,
    assess_compact_boundary,
    build_boundary_transaction,
    compact_representation_upper_bound,
    minimal_asset_names,
)


def _template_payload() -> tuple[bytes, bytes, bytes]:
    address = bytes.fromhex("60" + "11" * 28)
    policy_id = bytes.fromhex("22" * 28)
    body = {
        0: {(bytes.fromhex("33" * 32), 0)},
        1: [[address, [29_000_000, {policy_id: {b"x": 1}}]]],
        2: 1_000_000,
        9: {policy_id: {b"x": 1}},
    }
    witness = {
        0: [[bytes.fromhex("44" * 32), bytes.fromhex("55" * 64)]],
        1: [[0, bytes.fromhex("66" * 28)]],
    }
    payload = cbor2.dumps([body, witness, True, None])
    return payload, address, policy_id


def _replace_body(template_payload: bytes, body_raw: bytes) -> bytes:
    transaction = cbor2.loads(template_payload)
    transaction[0] = cbor2.loads(body_raw)
    return cbor2.dumps(transaction)


def test_compact_boundary_is_reachable_with_fee_correct_ledger_valid_shape():
    template, address, policy_id = _template_payload()
    params = {
        "maxValueSize": 5000,
        "maxTxSize": 16384,
        "txFeePerByte": 44,
        "txFeeFixed": 155381,
        "utxoCostPerByte": 4310,
    }

    control = build_boundary_transaction(
        template_payload=template,
        address=address,
        policy_id=policy_id,
        input_lovelace=30_000_000,
        asset_count=CONTROL_ASSET_COUNT,
        protocol_parameters=params,
        sign_body=lambda body: _replace_body(template, body),
    )
    mutant = build_boundary_transaction(
        template_payload=template,
        address=address,
        policy_id=policy_id,
        input_lovelace=30_000_000,
        asset_count=MUTANT_ASSET_COUNT,
        protocol_parameters=params,
        sign_body=lambda body: _replace_body(template, body),
    )

    assert compact_representation_upper_bound(CONTROL_ASSET_COUNT, 1) == 65_500
    assert compact_representation_upper_bound(MUTANT_ASSET_COUNT, 1) == 65_544
    assert control["compact_boundary_crossed"] is False
    assert mutant["compact_boundary_crossed"] is True
    assert control["fee"] == params["txFeeFixed"] + params["txFeePerByte"] * len(control["payload"])
    assert mutant["fee"] == params["txFeeFixed"] + params["txFeePerByte"] * len(mutant["payload"])
    assert len(control["payload"]) < params["maxTxSize"]
    assert len(mutant["payload"]) < params["maxTxSize"]
    assert max(control["ledger_value_sizes"]) < params["maxValueSize"]
    assert max(mutant["ledger_value_sizes"]) < params["maxValueSize"]
    assert control["minimum_ada_satisfied"] is True
    assert mutant["minimum_ada_satisfied"] is True
    assert control["transaction_size"] == 11_758
    assert mutant["transaction_size"] == 11_766
    assert control["ledger_value_sizes"] == [2757, 3015]
    assert mutant["ledger_value_sizes"] == [2757, 3019]
    assert len(minimal_asset_names(MUTANT_ASSET_COUNT)) == MUTANT_ASSET_COUNT
    assert len(set(minimal_asset_names(MUTANT_ASSET_COUNT))) == MUTANT_ASSET_COUNT


def test_compact_mutant_payload_is_identical_for_both_submission_roles():
    template, address, policy_id = _template_payload()
    params = {
        "maxValueSize": 5000,
        "maxTxSize": 16384,
        "txFeePerByte": 44,
        "txFeeFixed": 155381,
        "utxoCostPerByte": 4310,
    }
    mutant = build_boundary_transaction(
        template_payload=template,
        address=address,
        policy_id=policy_id,
        input_lovelace=30_000_000,
        asset_count=MUTANT_ASSET_COUNT,
        protocol_parameters=params,
        sign_body=lambda body: _replace_body(template, body),
    )

    submissions = {role: mutant["payload"] for role in ("reference", "target")}
    assert submissions["reference"] == submissions["target"]
    assert mutant["asset_count"] == 1489
    assert mutant["output_asset_counts"] == [744, 745]


def test_assessment_requires_both_controls_and_classifies_without_assuming_divergence():
    records = [
        {
            "case_id": "compact-control",
            "target": {
                "funding_input": "d" * 64 + "#0",
                "classification": "accepted",
                "included": True,
                "target_adopted": True,
                "transaction_id": "a" * 64,
            },
            "reference": {
                "funding_input": "d" * 64 + "#1",
                "classification": "accepted",
                "included": True,
                "target_adopted": True,
                "transaction_id": "b" * 64,
            },
        },
        {
            "case_id": "compact-boundary",
            "funding_input": "d" * 64 + "#2",
            "payload_sha256": "c" * 64,
            "target": {
                "classification": "accepted",
                "included": False,
                "payload_sha256": "c" * 64,
            },
            "reference": {
                "classification": "decode_reject",
                "included": False,
                "payload_sha256": "c" * 64,
            },
        },
    ]

    status = assess_compact_boundary(records)

    assert status["valid_controls_complete"] is True
    assert status["funding_inputs_isolated"] is True
    assert status["mutation_payloads_match"] is True
    assert status["outcomes_conclusive"] is False
    assert status["complete"] is False
    assert status["divergence_established"] is False


def test_compact_boundary_primitive_and_portable_scenario_validate(monkeypatch, tmp_path):
    root = Path(__file__).resolve().parents[1]
    registry_path = root / "dwarf/primitives/registry.json"
    registry = load_registry(registry_path)
    scenario_path = (
        root
        / "dwarf/scenarios/ledger-submit-multiasset-compact-boundary-differential-amaru-cardano-node.yaml"
    )

    assert registry["runtime_multiasset_compact_boundary"].family == "load"
    assert registry["multiasset_compact_boundary_matches"].family == "assertion"
    assert semantic_validate_scenario(scenario_path, registry_path=registry_path)["errors"] == []
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    assert scenario["target"]["version"] == "resolved-plan"
    assert all(
        node["version"] == "resolved-plan"
        and node["identity_binding"] == "resolved-plan"
        for node in scenario["substrate"]["nodes"]
    )

    runtime_path = tmp_path / "run/outputs/substrate-compose/runtime.json"
    runtime_path.parent.mkdir(parents=True)
    runtime_path.write_text(json.dumps({"nodes": []}), encoding="utf-8")
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return {"boundary_status": {"complete": False}}

    monkeypatch.setattr(
        "scripts.runtime_multiasset_compact_boundary.run_multiasset_compact_boundary",
        fake_run,
    )
    primitive = RuntimeMultiassetCompactBoundary(
        params={
            "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
            "target_node": "node1",
            "reference_node": "node2",
            "output_dir": "outputs/multiasset-compact-boundary",
        }
    )
    primitive.bound_substrate = {
        "nodes": [
            {
                "id": "node1",
                "impl": "amaru",
                "version": "10.11.selected",
                "source_revision": "a" * 40,
            },
            {
                "id": "node2",
                "impl": "cardano-node",
                "version": "11.1.selected",
                "source_revision": "b" * 40,
            },
        ]
    }

    class Handle:
        run_dir = tmp_path / "run"

    primitive.run(Handle(), None)
    assert captured["expected_target_version"] == "10.11.selected"
    assert captured["expected_target_source"] == "a" * 40
    assert captured["expected_reference_version"] == "11.1.selected"
    assert captured["expected_reference_source"] == "b" * 40
