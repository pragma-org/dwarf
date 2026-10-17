import hashlib
import json
from pathlib import Path

import cbor2

from profile_manager.primitives import load_registry
from profile_manager.scenario import semantic_validate_scenario
from profile_manager.submit_primitives import RuntimeInlineDatumTag24Boundary
from scripts.runtime_inline_datum_tag24_boundary import (
    _inline_datum_build_raw_command,
    assess_inline_datum_boundary,
    build_inline_datum_transaction,
)


CANONICAL_DATUM = bytes.fromhex("d87980")
TRAILING_DATUM = CANONICAL_DATUM + b"\x00"
OBSERVED_CLI_MAP_DATUM = bytes.fromhex(
    "a24b636f6e7374727563746f7200466669656c647380"
)


def test_live_builder_uses_one_transaction_subcommand(tmp_path):
    command = _inline_datum_build_raw_command(
        cli="/usr/local/bin/cardano-cli",
        funding_input="a" * 64 + "#0",
        address="addr_test1vruntime",
        destination=tmp_path / "template.raw",
    )

    assert command[:4] == [
        "/usr/local/bin/cardano-cli",
        "conway",
        "transaction",
        "build-raw",
    ]
    assert command.count("transaction") == 1
    assert command[command.index("--tx-in") + 1] == "a" * 64 + "#0"
    assert command[command.index("--out-file") + 1] == str(tmp_path / "template.raw")


def _template_payload(inner_datum: bytes = CANONICAL_DATUM) -> tuple[bytes, bytes]:
    address = bytes.fromhex("60" + "11" * 28)
    body = {
        0: {(bytes.fromhex("33" * 32), 0)},
        1: [
            {
                0: address,
                1: 29_000_000,
                2: [1, cbor2.CBORTag(24, inner_datum)],
            }
        ],
        2: 1_000_000,
    }
    witness = {0: [[bytes.fromhex("44" * 32), bytes.fromhex("55" * 64)]]}
    return cbor2.dumps([body, witness, True, None]), address


def _replace_body(template_payload: bytes, body_raw: bytes) -> bytes:
    transaction = cbor2.loads(template_payload)
    transaction[0] = cbor2.loads(body_raw)
    return cbor2.dumps(transaction)


def test_builder_places_trailing_byte_inside_inline_datum_tag24_and_resigns_final_body():
    template, _address = _template_payload()
    params = {
        "maxTxSize": 16_384,
        "txFeePerByte": 44,
        "txFeeFixed": 155_381,
        "utxoCostPerByte": 4_310,
    }

    control = build_inline_datum_transaction(
        template_payload=template,
        input_lovelace=30_000_000,
        protocol_parameters=params,
        trailing_inner_byte=False,
        sign_body=lambda body: _replace_body(template, body),
    )
    mutant = build_inline_datum_transaction(
        template_payload=template,
        input_lovelace=30_000_000,
        protocol_parameters=params,
        trailing_inner_byte=True,
        sign_body=lambda body: _replace_body(template, body),
    )

    control_output = cbor2.loads(control["body"])[1][0]
    mutant_output = cbor2.loads(mutant["body"])[1][0]
    assert control_output[2][1] == cbor2.CBORTag(24, CANONICAL_DATUM)
    assert mutant_output[2][1] == cbor2.CBORTag(24, TRAILING_DATUM)
    assert cbor2.loads(mutant["payload"])[0] == cbor2.loads(mutant["body"])
    assert mutant["inner_datum_sha256"] == hashlib.sha256(TRAILING_DATUM).hexdigest()
    assert mutant["nested_trailing_bytes"] == 1
    assert mutant["transaction_trailing_bytes"] == 0
    assert mutant["fee"] == params["txFeeFixed"] + params["txFeePerByte"] * len(mutant["payload"])
    assert mutant["minimum_ada_satisfied"] is True
    assert len(mutant["payload"]) < params["maxTxSize"]


def test_builder_normalizes_observed_cli_map_datum_to_audited_constructor_bytes():
    template, _address = _template_payload(OBSERVED_CLI_MAP_DATUM)
    params = {
        "maxTxSize": 16_384,
        "txFeePerByte": 44,
        "txFeeFixed": 155_381,
        "utxoCostPerByte": 4_310,
    }

    control = build_inline_datum_transaction(
        template_payload=template,
        input_lovelace=30_000_000,
        protocol_parameters=params,
        trailing_inner_byte=False,
        sign_body=lambda body: _replace_body(template, body),
    )
    mutant = build_inline_datum_transaction(
        template_payload=template,
        input_lovelace=30_000_000,
        protocol_parameters=params,
        trailing_inner_byte=True,
        sign_body=lambda body: _replace_body(template, body),
    )

    template_body = cbor2.loads(template)[0]
    control_body = cbor2.loads(control["body"])
    mutant_body = cbor2.loads(mutant["body"])
    assert template_body[1][0][2][1] == cbor2.CBORTag(24, OBSERVED_CLI_MAP_DATUM)
    assert control_body[1][0][2][1] == cbor2.CBORTag(24, CANONICAL_DATUM)
    assert mutant_body[1][0][2][1] == cbor2.CBORTag(24, TRAILING_DATUM)
    assert control["canonical_inner_datum_hex"] == CANONICAL_DATUM.hex()
    assert control["inner_datum_hex"] == CANONICAL_DATUM.hex()
    assert mutant["inner_datum_hex"] == TRAILING_DATUM.hex()
    assert control["body_sha256"] == hashlib.sha256(control["body"]).hexdigest()
    assert control["payload_sha256"] == hashlib.sha256(control["payload"]).hexdigest()
    assert mutant["body_sha256"] == hashlib.sha256(mutant["body"]).hexdigest()
    assert mutant["payload_sha256"] == hashlib.sha256(mutant["payload"]).hexdigest()
    assert cbor2.loads(control["payload"])[0] == control_body
    assert cbor2.loads(mutant["payload"])[0] == mutant_body
    assert control["fee"] == params["txFeeFixed"] + params["txFeePerByte"] * len(
        control["payload"]
    )
    assert mutant["fee"] == params["txFeeFixed"] + params["txFeePerByte"] * len(
        mutant["payload"]
    )
    assert control["minimum_ada_satisfied"] is True
    assert mutant["minimum_ada_satisfied"] is True
    assert control["transaction_trailing_bytes"] == 0
    assert mutant["transaction_trailing_bytes"] == 0


def test_assessment_requires_independent_controls_and_same_mutant_payload():
    records = [
        {
            "case_id": "inline-datum-control",
            "target": {
                "funding_input": "a" * 64 + "#0",
                "classification": "accepted",
                "included": True,
                "target_adopted": True,
                "transaction_id": "b" * 64,
            },
            "reference": {
                "funding_input": "a" * 64 + "#1",
                "classification": "accepted",
                "included": True,
                "target_adopted": True,
                "transaction_id": "c" * 64,
            },
        },
        {
            "case_id": "inline-datum-tag24-trailing",
            "funding_input": "a" * 64 + "#2",
            "payload_sha256": "d" * 64,
            "nested_trailing_bytes": 1,
            "transaction_trailing_bytes": 0,
            "target": {
                "classification": "accepted",
                "included": False,
                "payload_sha256": "d" * 64,
            },
            "reference": {
                "classification": "decode_reject",
                "included": False,
                "payload_sha256": "d" * 64,
            },
        },
    ]

    status = assess_inline_datum_boundary(records)

    assert status["valid_controls_complete"] is True
    assert status["funding_inputs_isolated"] is True
    assert status["mutation_payloads_match"] is True
    assert status["nested_boundary_reached"] is True
    assert status["outcomes_conclusive"] is False
    assert status["complete"] is False
    assert status["divergence_established"] is False


def test_existing_inline_and_plutusdata_corpora_do_not_cover_nested_trailing_tag24():
    root = Path(__file__).resolve().parents[1]
    fixture_root = root / "antithesis/cardano_amaru_adversarial/fixture"
    transactions = sorted((fixture_root / "refscript").glob("*.tx")) + sorted(
        (fixture_root / "plutusdata").glob("*.tx")
    )
    assert transactions
    for path in transactions:
        payload = bytes.fromhex(json.loads(path.read_text(encoding="utf-8"))["cborHex"])
        assert bytes.fromhex("d81844d8798000") not in payload, path.name

    graded = json.loads(
        (fixture_root / "plutusdata/graded-2026-09-27.json").read_text(encoding="utf-8")
    )
    assert "integrity hash" in graded["boundary"]


def test_inline_datum_boundary_primitive_and_portable_scenario_validate(monkeypatch, tmp_path):
    root = Path(__file__).resolve().parents[1]
    registry_path = root / "dwarf/primitives/registry.json"
    registry = load_registry(registry_path)
    scenario_path = root / (
        "dwarf/scenarios/ledger-submit-inline-datum-tag24-trailing-"
        "differential-amaru-cardano-node.yaml"
    )

    assert registry["runtime_inline_datum_tag24_boundary"].family == "load"
    assert registry["inline_datum_tag24_boundary_matches"].family == "assertion"
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
        "scripts.runtime_inline_datum_tag24_boundary.run_inline_datum_tag24_boundary",
        fake_run,
    )
    primitive = RuntimeInlineDatumTag24Boundary(
        params={
            "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
            "target_node": "node1",
            "reference_node": "node2",
            "output_dir": "outputs/inline-datum-tag24-boundary",
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
