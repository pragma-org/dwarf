import hashlib
import json
from decimal import Decimal
from pathlib import Path

import cbor2
import pytest

from profile_manager.primitives import load_registry
from profile_manager.scenario import semantic_validate_scenario
from profile_manager.submit_primitives import RuntimeUplcDeepDelayStackSafety
from scripts import runtime_redeemer_container_live_controls as live_controls
from scripts import runtime_uplc_deep_delay_stack_safety as deep_delay
from scripts.runtime_uplc_deep_delay_stack_safety import (
    BOUNDARY_DEPTH,
    CONTROL_DEPTH,
    MINTING_ARGUMENT_COUNT,
    _script_file,
    assess_deep_delay_stack_safety,
    build_deep_delay_transaction,
    encode_deep_delay_program,
    encode_upstream_deep_delay_program,
)


def _template(depth: int) -> tuple[bytes, bytes]:
    address = bytes.fromhex("60" + "11" * 28)
    policy_id = bytes.fromhex("22" * 28)
    flat = encode_deep_delay_program(depth)
    body = {
        0: {(bytes.fromhex("33" * 32), 0)},
        1: [[address, [19_000_000, {policy_id: {b"STACK": 1}}]]],
        2: 1_000_000,
        9: {policy_id: {b"STACK": 1}},
        11: bytes.fromhex("44" * 32),
        13: {(bytes.fromhex("55" * 32), 0)},
    }
    witnesses = {
        0: [(bytes.fromhex("66" * 32), bytes.fromhex("77" * 64))],
        3: {cbor2.dumps(flat)},
        5: {(1, 0): [cbor2.CBORTag(121, []), [1_000_000, 500_000_000]]},
    }
    return cbor2.dumps([body, witnesses, True, None]), address


def _replace_body(template: bytes, body_raw: bytes) -> bytes:
    transaction = cbor2.loads(template)
    transaction[0] = cbor2.loads(body_raw)
    return cbor2.dumps(transaction)


def test_exact_upstream_deep_delay_vector_is_reachable_under_live_limits():
    flat = encode_deep_delay_program(BOUNDARY_DEPTH)
    upstream = encode_upstream_deep_delay_program(BOUNDARY_DEPTH)
    template, address = _template(BOUNDARY_DEPTH)
    params = {
        "maxTxSize": 16_384,
        "txFeePerByte": 44,
        "txFeeFixed": 155_381,
        "utxoCostPerByte": 4_310,
        "executionUnitPrices": {
            "priceSteps": Decimal("7.21e-05"),
            "priceMemory": Decimal("0.0577"),
        },
        "costModels": {"PlutusV1": [1], "PlutusV3": [1]},
    }

    built = build_deep_delay_transaction(
        template_payload=template,
        address=address,
        input_lovelace=20_000_000,
        expected_flat=flat,
        protocol_parameters=params,
        sign_body=lambda body: _replace_body(template, body),
    )

    assert MINTING_ARGUMENT_COUNT == 2
    assert len(upstream) == 14_004
    assert hashlib.sha256(upstream).hexdigest() == (
        "9adb02de4a8db8952aa6ad9570db6cb4f2c056837810400728a846d1b0c58fc0"
    )
    assert len(flat) == 14_005
    assert hashlib.sha256(flat).hexdigest() == (
        "5186acb9e1f8fec9c336d2e147fc9ca98bc898bc127233d2694107d8897ddea3"
    )
    assert flat[:3] == upstream[:3] == bytes.fromhex("010000")
    assert flat[3] == 0x22  # two DeBruijn lambda tags; parameters encode no bytes
    assert flat[4:] == upstream[3:]  # the exact recursive term and filler are unchanged
    assert built["depth"] == 28_000
    assert built["transaction_size"] < params["maxTxSize"]
    assert built["transaction_size"] == 14_386
    assert built["fee_transaction_size"] == 14_385
    assert built["headroom_bytes"] == 1_998
    assert built["embedded_upstream_sha256"] == hashlib.sha256(upstream).hexdigest()
    assert built["executable_program_sha256"] == hashlib.sha256(flat).hexdigest()
    assert built["minting_argument_count"] == 2
    assert built["execution_fee"] == 93_750
    assert built["fee"] == 882_071
    assert built["minimum_ada_satisfied"] is True
    assert built["outer_trailing_bytes"] == 0


def test_live_execution_prices_converge_shallow_control_to_node_minimum():
    flat = encode_deep_delay_program(CONTROL_DEPTH)
    template, address = _template(CONTROL_DEPTH)
    built = build_deep_delay_transaction(
        template_payload=template,
        address=address,
        input_lovelace=20_000_000,
        expected_flat=flat,
        protocol_parameters={
            "maxTxSize": 16_384,
            "txFeePerByte": 44,
            "txFeeFixed": 155_381,
            "utxoCostPerByte": 4_310,
            "executionUnitPrices": {
                "priceSteps": Decimal("7.21e-05"),
                "priceMemory": Decimal("0.0577"),
            },
            "costModels": {"PlutusV1": [1]},
        },
        sign_body=lambda body: _replace_body(template, body),
    )

    assert built["transaction_size"] == 383
    assert built["fee_transaction_size"] == 382
    assert built["execution_fee"] == 93_750
    assert built["execution_units"] == {"steps": 500_000_000, "memory": 1_000_000}
    assert built["protocol_limits"]["price_steps"] == "0.0000721"
    assert built["protocol_limits"]["price_memory"] == "0.0577"
    assert built["fee"] == 265_939


def test_shallow_control_and_boundary_have_exact_flat_identity():
    control = encode_deep_delay_program(CONTROL_DEPTH)
    boundary = encode_deep_delay_program(BOUNDARY_DEPTH)
    upstream_control = encode_upstream_deep_delay_program(CONTROL_DEPTH)
    upstream_boundary = encode_upstream_deep_delay_program(BOUNDARY_DEPTH)

    assert control.hex() == "010000221601"
    assert upstream_control.hex() == "0100001601"
    assert boundary[:5].hex() == "0100002211"
    assert boundary[-2:].hex() == "1161"
    assert boundary[4:] == upstream_boundary[3:]
    assert boundary != control
    assert encode_deep_delay_program(BOUNDARY_DEPTH) == boundary


def test_generated_v1_envelope_and_witness_share_one_cbor_wrapper(tmp_path):
    flat = encode_deep_delay_program(CONTROL_DEPTH)
    script_path = tmp_path / "deep-delay-v1.plutus"
    _script_file(script_path, flat, description="deep-delay control")

    generated = json.loads(script_path.read_text(encoding="utf-8"))
    generated_wrapper = bytes.fromhex(generated["cborHex"])
    assert cbor2.loads(generated_wrapper) == flat
    assert generated_wrapper == cbor2.dumps(flat)

    audited_path = Path(
        "antithesis/cardano_amaru_adversarial/fixture/languageviews/v1.plutus"
    )
    audited = json.loads(audited_path.read_text(encoding="utf-8"))
    audited_wrapper = bytes.fromhex(audited["cborHex"])
    assert isinstance(cbor2.loads(audited_wrapper), bytes)
    assert audited_wrapper == cbor2.dumps(cbor2.loads(audited_wrapper))

    template, address = _template(CONTROL_DEPTH)
    built = build_deep_delay_transaction(
        template_payload=template,
        address=address,
        input_lovelace=20_000_000,
        expected_flat=flat,
        protocol_parameters={
            "maxTxSize": 16_384,
            "txFeePerByte": 44,
            "txFeeFixed": 155_381,
            "utxoCostPerByte": 4_310,
            "executionUnitPrices": {
                "priceSteps": Decimal("7.21e-05"),
                "priceMemory": Decimal("0.0577"),
            },
            "costModels": {"PlutusV1": [1]},
        },
        sign_body=lambda body: _replace_body(template, body),
    )
    assert built["script_witness_sha256"] == hashlib.sha256(
        generated_wrapper
    ).hexdigest()


def test_assessment_requires_controls_and_included_adopted_shared_boundary():
    records = [
        {
            "case_id": "shallow-control",
            "target": {
                "funding_input": "a" * 64 + "#0",
                "classification": "accepted",
                "included": True,
                "target_adopted": True,
                "transaction_id": "1" * 64,
            },
            "reference": {
                "funding_input": "a" * 64 + "#1",
                "classification": "accepted",
                "included": True,
                "target_adopted": True,
                "transaction_id": "2" * 64,
            },
        },
        {
            "case_id": "deep-delay-28000",
            "funding_input": "a" * 64 + "#2",
            "depth": 28_000,
            "flat_sha256": "b" * 64,
            "payload_sha256": "c" * 64,
            "included": True,
            "target_adopted": True,
            "target": {
                "classification": "accepted",
                "payload_sha256": "c" * 64,
            },
            "reference": {
                "classification": "accepted",
                "payload_sha256": "c" * 64,
            },
        },
    ]

    status = assess_deep_delay_stack_safety(records)

    assert status["valid_controls_complete"] is True
    assert status["funding_inputs_isolated"] is True
    assert status["boundary_payloads_match"] is True
    assert status["deep_boundary_reached"] is True
    assert status["boundary_included"] is True
    assert status["target_adopted_boundary"] is True
    assert status["complete"] is True
    assert status["security_divergence_established"] is False


def test_primitive_and_portable_scenario_bind_selected_versions(monkeypatch, tmp_path):
    root = Path(__file__).resolve().parents[1]
    registry_path = root / "dwarf/primitives/registry.json"
    scenario_path = root / (
        "dwarf/scenarios/ledger-submit-uplc-deep-delay-stack-safety-"
        "differential-amaru-cardano-node.yaml"
    )
    registry = load_registry(registry_path)

    assert registry["runtime_uplc_deep_delay_stack_safety"].family == "load"
    assert registry["uplc_deep_delay_stack_safety_matches"].family == "assertion"
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
        return {"stack_safety_status": {"complete": True}}

    monkeypatch.setattr(
        "scripts.runtime_uplc_deep_delay_stack_safety.run_uplc_deep_delay_stack_safety",
        fake_run,
    )
    primitive = RuntimeUplcDeepDelayStackSafety(
        params={
            "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
            "target_node": "node1",
            "reference_node": "node2",
            "output_dir": "outputs/uplc-deep-delay-stack-safety",
        }
    )
    primitive.bound_substrate = {
        "nodes": [
            {
                "id": "node1",
                "impl": "amaru",
                "version": "selected-amaru",
                "source_revision": "a" * 40,
            },
            {
                "id": "node2",
                "impl": "cardano-node",
                "version": "selected-cardano",
                "source_revision": "b" * 40,
            },
        ]
    }

    primitive.run(type("Handle", (), {"run_dir": tmp_path / "run"})(), None)

    assert captured["expected_target_version"] == "selected-amaru"
    assert captured["expected_target_source"] == "a" * 40
    assert captured["expected_reference_version"] == "selected-cardano"
    assert captured["expected_reference_source"] == "b" * 40


def _stub_reference_observations(monkeypatch, *, tips, utxos, mempool, wall_times):
    tip_responses = iter(tips)
    utxo_responses = iter(utxos)
    mempool_responses = iter(mempool)
    wall_responses = iter(wall_times)
    monotonic = iter([0.0, *range(1, 20)])
    monkeypatch.setattr(
        live_controls, "_query_reference_tip", lambda **_kwargs: next(tip_responses)
    )
    monkeypatch.setattr(
        live_controls, "_query_utxo", lambda **_kwargs: next(utxo_responses)
    )
    monkeypatch.setattr(
        live_controls,
        "_query_reference_mempool",
        lambda **_kwargs: next(mempool_responses),
    )
    monkeypatch.setattr(live_controls.time, "time", lambda: next(wall_responses))
    monkeypatch.setattr(live_controls.time, "monotonic", lambda: next(monotonic))
    monkeypatch.setattr(live_controls.time, "sleep", lambda _seconds: None)


def test_target_propagation_waits_for_post_delay_reference_progress(monkeypatch, tmp_path):
    transaction_id = "9" * 64
    _stub_reference_observations(
        monkeypatch,
        tips=[
            {"block": 101, "slot": 1, "hash": "early"},
            {"block": 102, "slot": 2, "hash": "delay-baseline"},
            {"block": 103, "slot": 3, "hash": "eligible"},
        ],
        utxos=[{}, {}, {}, {}],
        mempool=[False, False, False],
        wall_times=[1_030.0, 1_060.0, 1_061.0],
    )
    evidence_path = tmp_path / "target-inclusion.json"

    with pytest.raises(RuntimeError, match="absent after"):
        live_controls._wait_for_reference_inclusion(
            cli="/owned/cardano-cli",
            socket_path="/env/socket/node2/sock",
            network_magic=42,
            address="addr_test1target",
            destination=tmp_path / "included.json",
            transaction_id=transaction_id,
            initial_tip={"block": 100, "slot": 0, "hash": "initial"},
            evidence_path=evidence_path,
            timeout_seconds=120,
            target_propagation_gate={
                "reference_started_at": "1970-01-01T00:16:40Z",
                "reference_started_epoch_seconds": 1_000.0,
                "tx_submission_init_delay_seconds": 60.0,
                "source": "reference-startup-log+docker-inspect",
                "log_path": "/owned/node2.log",
                "reference_container_id": "a" * 64,
                "reference_container_image_id": "sha256:" + "b" * 64,
            },
        )

    evidence = json.loads(evidence_path.read_text())
    assert evidence["status"] == "not-included-after-reference-progress"
    assert evidence["target_propagation_gate"]["eligible_at_epoch_seconds"] == 1_060.0
    assert evidence["target_propagation_gate"]["log_path"] == "/owned/node2.log"
    assert evidence["target_propagation_gate"]["reference_container_id"] == "a" * 64
    assert evidence["target_propagation_gate"]["reference_container_image_id"] == (
        "sha256:" + "b" * 64
    )
    assert evidence["target_propagation_gate"]["post_delay_progress_baseline"]["block"] == 102
    assert evidence["observations"][0]["terminal_non_inclusion_eligible"] is False
    assert evidence["observations"][1]["terminal_non_inclusion_eligible"] is False
    assert evidence["observations"][2]["post_delay_progress_blocks"] == 1
    assert evidence["observations"][2]["terminal_non_inclusion_eligible"] is True


def test_target_propagation_inclusion_wins_before_delay(monkeypatch, tmp_path):
    transaction_id = "8" * 64
    included = {transaction_id + "#0": {"value": {"lovelace": 1}}}
    _stub_reference_observations(
        monkeypatch,
        tips=[{"block": 101, "slot": 1, "hash": "early"}],
        utxos=[included],
        mempool=[False],
        wall_times=[1_030.0],
    )
    evidence_path = tmp_path / "included.json"

    observed = live_controls._wait_for_reference_inclusion(
        cli="/owned/cardano-cli",
        socket_path="/env/socket/node2/sock",
        network_magic=42,
        address="addr_test1target",
        destination=tmp_path / "utxo.json",
        transaction_id=transaction_id,
        initial_tip={"block": 100, "slot": 0, "hash": "initial"},
        evidence_path=evidence_path,
        timeout_seconds=120,
        target_propagation_gate={
            "reference_started_at": "1970-01-01T00:16:40Z",
            "reference_started_epoch_seconds": 1_000.0,
            "tx_submission_init_delay_seconds": 60.0,
            "source": "reference-startup-log",
        },
    )

    assert observed == included
    assert json.loads(evidence_path.read_text())["status"] == "included"


def test_direct_reference_wait_keeps_existing_one_block_terminal(monkeypatch, tmp_path):
    transaction_id = "7" * 64
    _stub_reference_observations(
        monkeypatch,
        tips=[{"block": 101, "slot": 1, "hash": "next"}],
        utxos=[{}, {}],
        mempool=[False],
        wall_times=[],
    )
    evidence_path = tmp_path / "direct.json"

    with pytest.raises(RuntimeError, match="absent after 1"):
        live_controls._wait_for_reference_inclusion(
            cli="/owned/cardano-cli",
            socket_path="/env/socket/node2/sock",
            network_magic=42,
            address="addr_test1reference",
            destination=tmp_path / "utxo.json",
            transaction_id=transaction_id,
            initial_tip={"block": 100, "slot": 0, "hash": "initial"},
            evidence_path=evidence_path,
            timeout_seconds=120,
        )

    evidence = json.loads(evidence_path.read_text())
    assert evidence["status"] == "not-included-after-reference-progress"
    assert evidence["terminal_progress_block_threshold"] == 1
    assert "target_propagation_gate" not in evidence


def test_target_propagation_waits_for_configured_third_block(monkeypatch, tmp_path):
    transaction_id = "6" * 64
    _stub_reference_observations(
        monkeypatch,
        tips=[
            {"block": 101, "slot": 1, "hash": "baseline"},
            {"block": 102, "slot": 2, "hash": "first"},
            {"block": 103, "slot": 3, "hash": "second"},
            {"block": 104, "slot": 4, "hash": "third"},
        ],
        utxos=[{}, {}, {}, {}, {}],
        mempool=[False, False, False, False],
        wall_times=[1_060.0, 1_061.0, 1_062.0, 1_063.0],
    )
    evidence_path = tmp_path / "third-block.json"

    with pytest.raises(RuntimeError, match="absent after 4"):
        live_controls._wait_for_reference_inclusion(
            cli="/owned/cardano-cli",
            socket_path="/env/socket/node2/sock",
            network_magic=42,
            address="addr_test1target",
            destination=tmp_path / "utxo.json",
            transaction_id=transaction_id,
            initial_tip={"block": 100, "slot": 0, "hash": "initial"},
            evidence_path=evidence_path,
            timeout_seconds=120,
            terminal_progress_blocks=3,
            target_propagation_gate={
                "reference_started_at": "1970-01-01T00:16:40Z",
                "reference_started_epoch_seconds": 1_000.0,
                "tx_submission_init_delay_seconds": 60.0,
                "source": "reference-startup-log+docker-inspect",
            },
        )

    evidence = json.loads(evidence_path.read_text())
    assert evidence["status"] == "not-included-after-reference-progress"
    assert evidence["terminal_progress_block_threshold"] == 3
    assert [
        item["terminal_non_inclusion_eligible"]
        for item in evidence["observations"][:4]
    ] == [False, False, False, True]


def test_target_propagation_inclusion_wins_inside_three_block_window(
    monkeypatch, tmp_path
):
    transaction_id = "5" * 64
    included = {transaction_id + "#0": {"value": {"lovelace": 1}}}
    _stub_reference_observations(
        monkeypatch,
        tips=[
            {"block": 101, "slot": 1, "hash": "baseline"},
            {"block": 102, "slot": 2, "hash": "included"},
        ],
        utxos=[{}, included],
        mempool=[False, False],
        wall_times=[1_060.0, 1_061.0],
    )
    evidence_path = tmp_path / "included-inside-window.json"

    observed = live_controls._wait_for_reference_inclusion(
        cli="/owned/cardano-cli",
        socket_path="/env/socket/node2/sock",
        network_magic=42,
        address="addr_test1target",
        destination=tmp_path / "utxo.json",
        transaction_id=transaction_id,
        initial_tip={"block": 100, "slot": 0, "hash": "initial"},
        evidence_path=evidence_path,
        timeout_seconds=120,
        terminal_progress_blocks=3,
        target_propagation_gate={
            "reference_started_at": "1970-01-01T00:16:40Z",
            "reference_started_epoch_seconds": 1_000.0,
            "tx_submission_init_delay_seconds": 60.0,
            "source": "reference-startup-log+docker-inspect",
        },
    )

    assert observed == included
    evidence = json.loads(evidence_path.read_text())
    assert evidence["status"] == "included"
    assert evidence["terminal_progress_block_threshold"] == 3
    assert evidence["observations"][-1]["post_delay_progress_blocks"] == 1


def _reference_and_inspect(log_path):
    container_id = "a" * 64
    image_id = "sha256:" + "b" * 64
    image_ref = "ghcr.io/intersectmbo/cardano-node:11.1.2@sha256:" + "c" * 64
    reference = {
        "log_path": str(log_path),
        "container_id": container_id,
        "container_image_id": image_id,
        "image": image_ref,
    }
    inspected = {
        "Id": container_id,
        "Image": image_id,
        "Config": {"Image": image_ref},
        "State": {
            "Running": True,
            "StartedAt": "2026-10-16T06:31:23.108827222Z",
        },
    }
    return reference, inspected


@pytest.mark.parametrize(
    "configuration",
    [
        "ncTxSubmissionInitDelay = TxSubmissionInitDelay 60s",
        "ncTxSubmissionInitDelay = 60s",
    ],
)
def test_reference_startup_log_and_inspect_supply_target_gate(
    tmp_path, configuration
):
    log_path = tmp_path / "node2.log"
    log_path.write_text(f"Node configuration: {configuration}\n", encoding="utf-8")
    reference, inspected = _reference_and_inspect(log_path)
    inspected_ids = []

    gate = live_controls._reference_tx_submission_propagation_gate(
        reference,
        inspect_container=lambda identifier: (
            inspected_ids.append(identifier) or inspected
        ),
    )

    assert inspected_ids == ["a" * 64]
    assert gate["reference_started_at"] == "2026-10-16T06:31:23.108827222Z"
    assert gate["tx_submission_init_delay_seconds"] == 60.0
    assert gate["source"] == "reference-startup-log+docker-inspect"
    assert gate["log_path"] == str(log_path)
    assert gate["delay_source"] == "reference-startup-log"
    assert gate["startup_source"] == "docker-inspect:State.StartedAt"
    assert gate["eligible_at"] == "2026-10-16T06:32:23.108827Z"
    assert gate["eligible_at_epoch_seconds"] == pytest.approx(
        gate["reference_started_epoch_seconds"] + 60.0
    )
    assert gate["reference_container_id"] == "a" * 64
    assert gate["reference_container_image_id"] == "sha256:" + "b" * 64
    assert gate["reference_container_image_reference"] == (
        "ghcr.io/intersectmbo/cardano-node:11.1.2@sha256:" + "c" * 64
    )


@pytest.mark.parametrize(
    "state, expected_error",
    [
        ({"Running": True}, "StartedAt is missing"),
        ({"Running": True, "StartedAt": "not-a-timestamp"}, "StartedAt is invalid"),
    ],
)
def test_reference_target_gate_rejects_missing_or_malformed_inspect_start(
    tmp_path, state, expected_error
):
    log_path = tmp_path / "node2.log"
    log_path.write_text(
        "Node configuration: ncTxSubmissionInitDelay = TxSubmissionInitDelay 60s\n",
        encoding="utf-8",
    )
    reference, inspected = _reference_and_inspect(log_path)
    inspected["State"] = state

    with pytest.raises(RuntimeError, match=expected_error):
        live_controls._reference_tx_submission_propagation_gate(
            reference, inspect_container=lambda _identifier: inspected
        )


def test_reference_target_gate_rejects_inspect_identity_mismatch(tmp_path):
    log_path = tmp_path / "node2.log"
    log_path.write_text(
        "Node configuration: ncTxSubmissionInitDelay = TxSubmissionInitDelay 60s\n",
        encoding="utf-8",
    )
    reference, inspected = _reference_and_inspect(log_path)
    inspected["Image"] = "sha256:" + "d" * 64

    with pytest.raises(RuntimeError, match="image identity does not match"):
        live_controls._reference_tx_submission_propagation_gate(
            reference, inspect_container=lambda _identifier: inspected
        )


def test_reference_target_gate_requires_retained_delay(tmp_path):
    log_path = tmp_path / "node2.log"

    log_path.write_text(
        "Node configuration: remote submission delay unavailable\n",
        encoding="utf-8",
    )
    reference, inspected = _reference_and_inspect(log_path)
    with pytest.raises(RuntimeError, match="does not retain ncTxSubmissionInitDelay"):
        live_controls._reference_tx_submission_propagation_gate(
            reference, inspect_container=lambda _identifier: inspected
        )
