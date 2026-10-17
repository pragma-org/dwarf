import base64
import copy
import hashlib
import json
import subprocess
from pathlib import Path

import cbor2
import pytest

import scripts.runtime_redeemer_container_live_controls as live_controls
from profile_manager.launch_store import materialized_scenario
from profile_manager.run_plan import RunPlanRequest, resolve_run_plan

from scripts.runtime_redeemer_container_live_controls import (
    LIVE_CASES,
    assess_live_controls,
    build_funding_setup_command,
    derive_no_script_variants,
    derive_script_variants,
    execute_case_matrix,
    funding_collateral_inputs,
    funding_inputs,
    reconcile_control_inclusions,
    script_integrity_hash,
    select_mint_language,
    split_transaction,
    validate_runtime_identity,
    _select_funding_input,
    _submit_amaru_container,
    _submit_reference_cli,
    _submit_reference_raw,
    _submit_reference_with_decoder_log,
)
from scripts.runtime_substrate_common import (
    CommandResult,
    normalize_substrate,
    resolve_docker_image_for_node,
    verify_running_node_version,
)


ROOT = Path(__file__).resolve().parents[1]
COLLATERAL = ROOT / "antithesis/cardano_amaru_adversarial/fixture/collateral"


def _payload(name: str) -> bytes:
    return bytes.fromhex(json.loads((COLLATERAL / name).read_text())["cborHex"])


def test_scenario_nodes_bind_exact_catalog_images_sources_and_reported_versions():
    plan = resolve_run_plan(
        RunPlanRequest(
            scenario_id="ledger-submit-empty-redeemers-container-differential-amaru-cardano-node",
            version_policy="exact",
            cardano_version="11.1.2",
            amaru_version="10.11.20260925",
            acknowledge_unknown_versions=True,
            measurement_profile="none",
        )
    )
    scenario = json.loads(materialized_scenario(plan))
    nodes = normalize_substrate(scenario["substrate"])["nodes"]
    expected = {
        "node1": {
            "version": "10.11.20260925",
            "source": "eaf8ac3fa309f2a174c045454c276194c9312b8d",
            "image": "ghcr.io/pragma-org/amaru:v10.11.20260925@sha256:ad95eabd49d29b04978763b94e3036ad727c5e53c06946eeea98bd706cbaaee7",
        },
        "node2": {
            "version": "11.1.2",
            "source": "fef83fed01d7926f3de83b3b917be5a4a48768b5",
            "image": "ghcr.io/intersectmbo/cardano-node:11.1.2@sha256:6365403f44713d0a046865fb0466503ef207b71beae1b1ffece7f4399356db9f",
        },
    }
    for node in nodes:
        binding = expected[node["id"]]
        assert node["version"] == binding["version"]
        assert node["source_revision"] == binding["source"]
        assert node["image"] == binding["image"]
        digest = binding["image"].rsplit("@", 1)[1]

        def inspect_runner(command, *, _image=binding["image"], _digest=digest):
            repository = _image.split("@", 1)[0].rsplit(":", 1)[0]
            return CommandResult(
                command,
                0,
                json.dumps([{"Id": _digest, "RepoDigests": [repository + "@" + _digest]}]),
                "",
            )

        resolved = resolve_docker_image_for_node(node, runner=inspect_runner)
        assert resolved["satisfied"] is True
        assert resolved["source_revision"] == binding["source"]
        assert resolved["image_ref"] == binding["image"]

        def version_runner(command, *, _version=binding["version"]):
            return CommandResult(command, 0, f"{node['impl']} {_version}\n", "")

        observed = verify_running_node_version("case-node", node, runner=version_runner)
        assert observed["satisfied"] is True
        assert observed["reported_version"] == binding["version"]


def test_select_mint_language_uses_a_cost_model_present_in_live_parameters():
    both = {"costModels": {"PlutusV2": [2], "PlutusV3": [3]}}
    selected = select_mint_language(both)
    assert selected.name == "PlutusV3"
    assert selected.language_tag == 2
    assert selected.cost_model == [3]

    v2_only = select_mint_language({"costModels": {"PlutusV2": [2]}})
    assert v2_only.name == "PlutusV2"
    assert v2_only.language_tag == 1

    with pytest.raises(RuntimeError, match="supported Plutus mint cost model"):
        select_mint_language({"costModels": {"PlutusV1": [1]}})


def test_case_plan_shares_mutation_payload_inputs_but_isolates_cases_and_controls():
    assert [case.case_id for case in LIVE_CASES][:2] == [
        "no-script-valid-control",
        "script-valid-control",
    ]
    assert all(not case.control for case in LIVE_CASES[2:])

    assigned = funding_inputs("a" * 64)
    assert set(assigned) == {
        (case.case_id, role)
        for case in LIVE_CASES
        for role in ("target", "reference")
    }
    mutation_inputs = []
    control_inputs = []
    for case in LIVE_CASES:
        target = assigned[(case.case_id, "target")]
        reference = assigned[(case.case_id, "reference")]
        if case.control:
            assert target != reference
            control_inputs.extend((target, reference))
        else:
            assert target == reference
            mutation_inputs.append(target)
    assert len(set(mutation_inputs + control_inputs)) == len(mutation_inputs + control_inputs)
    assert all(value.startswith("a" * 64 + "#") for value in assigned.values())
    collateral = funding_collateral_inputs("a" * 64)
    assert set(collateral.values()).isdisjoint(set(assigned.values()))
    assert collateral[("script-redeemers-empty-map", "target")] == collateral[
        ("script-redeemers-empty-map", "reference")
    ]
    assert collateral[("script-valid-control", "target")] != collateral[
        ("script-valid-control", "reference")
    ]


def test_funding_setup_uses_fresh_input_and_only_the_required_isolated_outputs(tmp_path):
    command = build_funding_setup_command(
        cli="/owned/cardano-cli",
        socket_path="/env/socket/node2/sock",
        network_magic=42,
        funding_input="b" * 64 + "#3",
        address="addr_test1fresh",
        output_file=tmp_path / "funding.body",
    )
    assert command[:4] == [
        "/owned/cardano-cli",
        "conway",
        "transaction",
        "build",
    ]
    assert command[command.index("--tx-in") + 1] == "b" * 64 + "#3"
    outputs = [command[index + 1] for index, value in enumerate(command) if value == "--tx-out"]
    assert len(outputs) == 14
    assert set(outputs) == {"addr_test1fresh+20000000"}
    assert "9708b921e619b34a6fafc375ebd46bf65d77eed427f953eabed2b9da9212c4a1#0" not in command


def test_funding_input_requires_enough_lovelace_for_every_isolated_output():
    required = 14 * 20_000_000
    utxos = {
        "a" * 64 + "#0": {"value": {"lovelace": required - 1}},
        "b" * 64 + "#1": {"value": {"lovelace": required + 50_000_000}},
    }

    assert _select_funding_input(utxos, minimum_lovelace=required) == (
        "b" * 64 + "#1",
        required + 50_000_000,
    )
    with pytest.raises(RuntimeError, match="enough lovelace"):
        _select_funding_input(
            {"a" * 64 + "#0": {"value": {"lovelace": required - 1}}},
            minimum_lovelace=required,
        )


def test_funding_inclusion_waits_for_post_submit_block_progress(monkeypatch, tmp_path):
    wait = getattr(live_controls, "_wait_for_reference_inclusion", None)
    assert wait is not None

    transaction_id = "c" * 64
    utxo_responses = iter(
        [
            {},
            {transaction_id + "#0": {"value": {"lovelace": 20_000_000}}},
        ]
    )
    tip_responses = iter(
        [
            {"block": 41, "slot": 345_600, "hash": "before"},
            {"block": 42, "slot": 345_737, "hash": "after"},
        ]
    )
    mempool_responses = iter([True, False])
    clock = iter([0.0, 61.0, 62.0])

    monkeypatch.setattr(live_controls, "_query_utxo", lambda **_kwargs: next(utxo_responses))
    monkeypatch.setattr(
        live_controls, "_query_reference_tip", lambda **_kwargs: next(tip_responses)
    )
    monkeypatch.setattr(
        live_controls,
        "_query_reference_mempool",
        lambda **_kwargs: next(mempool_responses),
    )
    monkeypatch.setattr(live_controls.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(live_controls.time, "sleep", lambda _seconds: None)

    evidence_path = tmp_path / "funding-inclusion.json"
    observed = wait(
        cli="/owned/cardano-cli",
        socket_path="/env/socket/node2/sock",
        network_magic=42,
        address="addr_test1fresh",
        destination=tmp_path / "funding-ready.json",
        transaction_id=transaction_id,
        initial_tip={"block": 41, "slot": 345_558, "hash": "initial"},
        evidence_path=evidence_path,
        timeout_seconds=120,
    )

    assert transaction_id + "#0" in observed
    evidence = json.loads(evidence_path.read_text())
    assert evidence["status"] == "included"
    assert evidence["initial_tip"]["block"] == 41
    assert evidence["observations"][-1]["tip"]["block"] == 42
    assert evidence["observations"][-1]["post_submit_blocks"] == 1


def test_funding_inclusion_rechecks_utxo_after_tip_advances(monkeypatch, tmp_path):
    transaction_id = "d" * 64
    utxo_responses = iter(
        [
            {},
            {transaction_id + "#0": {"value": {"lovelace": 20_000_000}}},
        ]
    )
    monkeypatch.setattr(live_controls, "_query_utxo", lambda **_kwargs: next(utxo_responses))
    monkeypatch.setattr(
        live_controls,
        "_query_reference_tip",
        lambda **_kwargs: {"block": 42, "slot": 345_737, "hash": "after"},
    )
    monkeypatch.setattr(
        live_controls, "_query_reference_mempool", lambda **_kwargs: False
    )
    clock = iter([0.0, 1.0])
    monkeypatch.setattr(live_controls.time, "monotonic", lambda: next(clock))

    evidence_path = tmp_path / "funding-inclusion.json"
    observed = live_controls._wait_for_reference_inclusion(
        cli="/owned/cardano-cli",
        socket_path="/env/socket/node2/sock",
        network_magic=42,
        address="addr_test1fresh",
        destination=tmp_path / "funding-ready.json",
        transaction_id=transaction_id,
        initial_tip={"block": 41, "slot": 345_558, "hash": "initial"},
        evidence_path=evidence_path,
        timeout_seconds=120,
    )

    assert transaction_id + "#0" in observed
    evidence = json.loads(evidence_path.read_text())
    assert evidence["status"] == "included"
    assert evidence["observations"][-1]["terminal_utxo_recheck"] is True
    assert evidence["observations"][-1]["included"] is True


def test_runtime_identity_is_bound_to_exact_target_and_reference():
    runtime = {
        "network": "testnet_42",
        "network_magic": 42,
        "nodes": [
            {
                "id": "node1",
                "impl": "amaru",
                "resolved_version": "v10.11.20260925",
                "source_revision": "eaf8ac3fa309f2a174c045454c276194c9312b8d",
                "container_id": "1" * 64,
                "container_image_id": "sha256:" + "2" * 64,
                "artifact_identity": {"satisfied": True},
                "version_identity": {"satisfied": True},
            },
            {
                "id": "node2",
                "impl": "cardano-node",
                "resolved_version": "11.1.2",
                "source_revision": "fef83fed01d7926f3de83b3b917be5a4a48768b5",
                "container_id": "3" * 64,
                "container_image_id": "sha256:" + "4" * 64,
                "artifact_identity": {"satisfied": True},
                "version_identity": {"satisfied": True},
            },
        ],
    }
    identity = validate_runtime_identity(
        runtime,
        target_node="node1",
        reference_node="node2",
        expected_target_version="v10.11.20260925",
        expected_target_source="eaf8ac3fa309f2a174c045454c276194c9312b8d",
        expected_reference_version="11.1.2",
        expected_reference_source="fef83fed01d7926f3de83b3b917be5a4a48768b5",
    )
    assert identity["target"]["container_id"] == "1" * 64
    assert identity["reference"]["container_id"] == "3" * 64

    runtime["nodes"][0]["source_revision"] = "0" * 40
    with pytest.raises(RuntimeError, match="target source revision"):
        validate_runtime_identity(
            runtime,
            target_node="node1",
            reference_node="node2",
            expected_target_version="v10.11.20260925",
            expected_target_source="eaf8ac3fa309f2a174c045454c276194c9312b8d",
            expected_reference_version="11.1.2",
            expected_reference_source="fef83fed01d7926f3de83b3b917be5a4a48768b5",
        )


def test_script_mutations_recompute_integrity_hash_from_the_exact_container():
    payload = _payload("valid-minimal.tx")
    pparams = json.loads((COLLATERAL / "pparams.json").read_text())
    selected = select_mint_language(pparams)
    variants = derive_script_variants(payload, selected)

    original_body_raw, original_witness_raw, _tail = split_transaction(payload)
    original_body = cbor2.loads(original_body_raw)
    original_witnesses = cbor2.loads(original_witness_raw)
    assert variants["valid"] == payload
    assert original_witnesses[5]

    expected_containers = {
        "omitted": None,
        "empty-list": [],
        "empty-map": {},
    }
    for name, expected in expected_containers.items():
        body_raw, witness_raw, _tail = split_transaction(variants[name])
        body = cbor2.loads(body_raw)
        witnesses = cbor2.loads(witness_raw)
        assert {key: value for key, value in body.items() if key != 11} == {
            key: value for key, value in original_body.items() if key != 11
        }
        if expected is None:
            assert 5 not in witnesses
            hash_container = {}
        else:
            assert witnesses[5] == expected
            hash_container = expected
        assert body[11] == script_integrity_hash(
            hash_container, selected.cost_model, selected.language_tag
        )
        assert hashlib.sha256(variants[name]).digest() != hashlib.sha256(payload).digest()


def test_final_body_is_signed_before_only_the_redeemer_container_is_mutated():
    control = _payload("valid-minimal.tx")
    selected = select_mint_language(
        json.loads((COLLATERAL / "pparams.json").read_text())
    )
    intended = derive_script_variants(control, selected)["empty-list"]

    signable = live_controls.prepare_variant_for_signing(control, intended)
    signable_body, signable_witness_raw, signable_tail = split_transaction(signable)
    intended_body, intended_witness_raw, intended_tail = split_transaction(intended)
    signable_witnesses = cbor2.loads(signable_witness_raw)
    assert signable_body == intended_body
    assert signable_tail == intended_tail
    assert isinstance(signable_witnesses[5], dict) and signable_witnesses[5]

    # Model cardano-cli adding the key witness to the structurally decodable
    # signing envelope.  Finalization must retain that exact witness while
    # changing only the redeemer-container representation.
    key_witness = [b"verification-key", b"signature-over-final-body"]
    signed_witnesses = dict(signable_witnesses)
    signed_witnesses[0] = [key_witness]
    signed = b"\x84" + signable_body + cbor2.dumps(signed_witnesses) + signable_tail
    final = live_controls.apply_intended_witness_container(signed, intended)

    final_body, final_witness_raw, final_tail = split_transaction(final)
    final_witnesses = cbor2.loads(final_witness_raw)
    intended_witnesses = cbor2.loads(intended_witness_raw)
    assert final_body == intended_body == signable_body
    assert hashlib.blake2b(final_body, digest_size=32).digest() == hashlib.blake2b(
        signable_body, digest_size=32
    ).digest()
    assert final_witnesses[0] == [key_witness]
    assert final_witnesses[5] == intended_witnesses[5] == []
    assert {
        key: value for key, value in final_witnesses.items() if key != 5
    } == {
        key: value for key, value in signed_witnesses.items() if key != 5
    }
    assert final_tail == intended_tail


def test_no_script_mutations_change_only_the_redeemer_container():
    payload = _payload("p1-control-no-collateral.tx")
    variants = derive_no_script_variants(payload)
    original_body_raw, _original_witness_raw, original_tail = split_transaction(payload)

    expected_containers = {
        "omitted": None,
        "empty-list": [],
        "empty-map": {},
    }
    for name, expected in expected_containers.items():
        body_raw, witness_raw, tail = split_transaction(variants[name])
        witnesses = cbor2.loads(witness_raw)
        assert body_raw == original_body_raw
        assert tail == original_tail
        if expected is None:
            assert 5 not in witnesses
        else:
            assert witnesses[5] == expected


def test_live_control_assessment_requires_real_controls_and_missing_redeemer_evidence():
    records = []
    assigned = funding_inputs("f" * 64)
    for case in LIVE_CASES:
        for role in ("target", "reference"):
            if case.control:
                observation = {
                    "classification": "accepted",
                    "transaction_id": f"{case.case_id}-{role}",
                    "reference_chain_included": True,
                }
            elif case.case_id == "script-redeemers-omitted":
                observation = {
                    "classification": "ledger_reject",
                    "body": "MissingRedeemers [(Mint, 0)]",
                    "reference_chain_included": False,
                }
            else:
                observation = {
                    "classification": "decode_reject",
                    "body": "DecoderError: unexpected redeemer container",
                    "reference_chain_included": False,
                }
            records.append(
                {
                    "case_id": case.case_id,
                    "role": role,
                    "funding_input": assigned[(case.case_id, role)],
                    "payload_sha256": None if case.control else "a" * 64,
                    "observation": observation,
                }
            )

    status = assess_live_controls(records)
    assert status["valid_control_present"] is True
    assert status["missing_redeemer_evidence"] is True
    assert status["funding_inputs_isolated"] is True
    assert status["complete"] is True

    records[0]["observation"] = {
        "classification": "inconclusive_reject",
        "body": "BadInputsUTxO",
        "reference_chain_included": False,
    }
    failed = assess_live_controls(records)
    assert failed["complete"] is False
    assert "inconclusive" in failed["reason"]


def test_later_retained_utxo_reconciles_pending_control_without_rewriting_timeout(tmp_path):
    records = []
    assigned = funding_inputs("f" * 64)
    pending_key = ("no-script-valid-control", "reference")
    pending_tx = "31" * 32
    for case in LIVE_CASES:
        for role in ("target", "reference"):
            if case.control:
                transaction_id = (
                    pending_tx
                    if (case.case_id, role) == pending_key
                    else f"{case.case_id}-{role}"
                )
                observation = {
                    "classification": "accepted",
                    "transaction_id": transaction_id,
                    "reference_chain_included": (case.case_id, role) != pending_key,
                }
            elif case.case_id == "script-redeemers-omitted":
                observation = {
                    "classification": "ledger_reject",
                    "body": "MissingRedeemers [(Mint, 0)]",
                    "reference_chain_included": False,
                }
            else:
                observation = {
                    "classification": "decode_reject",
                    "body": "DecoderError",
                    "reference_chain_included": False,
                }
            records.append(
                {
                    "case_id": case.case_id,
                    "role": role,
                    "funding_input": assigned[(case.case_id, role)],
                    "payload_sha256": None if case.control else "a" * 64,
                    "observation": observation,
                }
            )

    late_path = tmp_path / "script-valid-control-reference-included-utxo.json"
    late_path.write_text(
        json.dumps({f"{pending_tx}#0": {"value": {"lovelace": 19_000_000}}}) + "\n",
        encoding="utf-8",
    )

    reconciliation = reconcile_control_inclusions(records, tmp_path)
    status = assess_live_controls(records, inclusion_reconciliation=reconciliation)

    pending = next(
        record
        for record in records
        if (record["case_id"], record["role"]) == pending_key
    )
    assert pending["observation"]["reference_chain_included"] is False
    assert reconciliation == [
        {
            "case_id": pending_key[0],
            "role": pending_key[1],
            "transaction_id": pending_tx,
            "initial_reference_chain_included": False,
            "reconciled_reference_chain_included": True,
            "evidence": [
                {
                    "path": late_path.name,
                    "sha256": hashlib.sha256(late_path.read_bytes()).hexdigest(),
                }
            ],
        }
    ]
    assert status["valid_control_present"] is True
    assert status["reconciled_control_inclusion_count"] == 1
    assert status["complete"] is True


def test_live_control_completion_does_not_require_the_hypothesized_target_verdict():
    records = []
    assigned = funding_inputs("f" * 64)
    for case in LIVE_CASES:
        for role in ("target", "reference"):
            if case.control:
                observation = {
                    "classification": "accepted",
                    "transaction_id": f"{case.case_id}-{role}",
                    "reference_chain_included": True,
                }
            elif case.case_id == "script-redeemers-omitted":
                observation = (
                    {
                        "classification": "accepted",
                        "transaction_id": "finding-target",
                        "reference_chain_included": True,
                    }
                    if role == "target"
                    else {
                        "classification": "ledger_reject",
                        "body": "NoRedeemer (Mint, 0)",
                        "reference_chain_included": False,
                    }
                )
            else:
                observation = {
                    "classification": "decode_reject",
                    "body": "decode error",
                    "reference_chain_included": False,
                }
            records.append(
                {
                    "case_id": case.case_id,
                    "role": role,
                    "funding_input": assigned[(case.case_id, role)],
                    "payload_sha256": None if case.control else "a" * 64,
                    "observation": observation,
                }
            )

    status = assess_live_controls(records)
    assert status["complete"] is True
    assert status["reference_missing_redeemer_evidence"] is True
    assert status["target_missing_redeemer_classification"] == "accepted"


def test_case_execution_retains_order_and_checks_every_accepted_transaction():
    assigned = funding_inputs("c" * 64)
    build_calls = []
    submit_calls = []
    inclusion_calls = []

    def build(case, role, funding_input):
        build_calls.append((case.case_id, role, funding_input))
        return {
            "payload": f"{case.case_id}:{role}".encode(),
            "transaction_id": f"tx-{case.case_id}-{role}",
            "body_sha256": "d" * 64,
            "cbor_sha256": "e" * 64,
        }

    def submit(role, payload):
        submit_calls.append((role, payload))
        case_id = payload.decode().rsplit(":", 1)[0]
        if case_id.endswith("valid-control"):
            return {
                "http": 202,
                "body": "accepted",
                "pre_submit_reference_tip": {"block": len(submit_calls), "slot": 1},
            }
        if case_id == "script-redeemers-omitted":
            return (
                {
                    "http": 202,
                    "body": "accepted",
                    "pre_submit_reference_tip": {"block": len(submit_calls), "slot": 1},
                }
                if role == "target"
                else {"http": 400, "body": "MissingRedeemers [(Mint, 0)]"}
            )
        return {"http": 400, "body": "decode error"}

    def wait_reference_chain(transaction_id, case_id, role, response):
        inclusion_calls.append(
            (transaction_id, case_id, role, response["pre_submit_reference_tip"])
        )
        return True

    records = execute_case_matrix(
        assigned=assigned,
        build_case=build,
        submit_case=submit,
        wait_for_reference_chain_inclusion=wait_reference_chain,
    )

    assert [(case_id, role) for case_id, role, _value in build_calls] == [
        (case.case_id, role)
        for case in LIVE_CASES
        for role in (("target", "reference") if case.control else ("shared",))
    ]
    assert len({value for _case, _role, value in build_calls}) == len(build_calls)
    for case in (item for item in LIVE_CASES if not item.control):
        case_submits = [item for item in submit_calls if item[1].startswith(case.case_id.encode())]
        assert [role for role, _payload in case_submits] == ["target", "reference"]
        assert case_submits[0][1] == case_submits[1][1]
        target_record = next(
            item for item in records
            if item["case_id"] == case.case_id and item["role"] == "target"
        )
        reference_record = next(
            item for item in records
            if item["case_id"] == case.case_id and item["role"] == "reference"
        )
        assert target_record["payload_sha256"] == reference_record["payload_sha256"]
        assert target_record["funding_input"] == reference_record["funding_input"]
    assert len(inclusion_calls) == 4  # four independently funded normal controls
    assert all(call[3]["block"] > 0 for call in inclusion_calls)
    assert assess_live_controls(records)["complete"] is True


def test_mutation_submissions_both_happen_before_reference_chain_wait():
    assigned = funding_inputs("c" * 64)
    events = []

    def build(case, role, funding_input):
        return {
            "payload": f"{case.case_id}:{role}".encode(),
            "transaction_id": f"tx-{case.case_id}-{role}",
        }

    def submit(role, payload):
        events.append(("submit", role, payload))
        return {
            "http": 202,
            "body": "accepted",
            "pre_submit_reference_tip": {"block": len(events), "slot": 1},
        }

    def wait_reference_chain(transaction_id, case_id, role, response):
        events.append(
            (
                "reference-chain-wait",
                transaction_id,
                case_id,
                role,
                response["pre_submit_reference_tip"],
            )
        )
        return True

    execute_case_matrix(
        assigned=assigned,
        build_case=build,
        submit_case=submit,
        wait_for_reference_chain_inclusion=wait_reference_chain,
    )

    mutation_start = events.index(
        ("submit", "target", b"script-redeemers-omitted:shared")
    )
    assert events[mutation_start : mutation_start + 3] == [
        ("submit", "target", b"script-redeemers-omitted:shared"),
        ("submit", "reference", b"script-redeemers-omitted:shared"),
        (
            "reference-chain-wait",
            "tx-script-redeemers-omitted-shared",
            "script-redeemers-omitted",
            "reference",
            {"block": mutation_start + 2, "slot": 1},
        ),
    ]


def test_mutations_do_not_run_until_every_normal_control_is_accepted():
    assigned = funding_inputs("d" * 64)
    submitted = []

    def build(case, role, _funding_input):
        return {
            "payload": f"{case.case_id}:{role}".encode(),
            "transaction_id": f"tx-{case.case_id}-{role}",
        }

    def submit(role, payload):
        submitted.append((role, payload))
        if payload == b"script-valid-control:reference":
            return {"http": 400, "body": "typed control rejection"}
        return {"http": 202, "body": "accepted"}

    with pytest.raises(RuntimeError, match="normal controls.*AcceptTx"):
        execute_case_matrix(
            assigned=assigned,
            build_case=build,
            submit_case=submit,
            wait_for_reference_chain_inclusion=lambda *_args: True,
        )

    assert all(b"redeemers" not in payload for _role, payload in submitted)


@pytest.mark.parametrize("submitter", ["amaru", "reference"])
def test_submit_timeout_is_retained_as_unavailable(monkeypatch, tmp_path, submitter):
    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="submit", timeout=30)

    monkeypatch.setattr(subprocess, "run", timeout)
    if submitter == "amaru":
        result = _submit_amaru_container("a" * 64, b"tx")
    else:
        result = _submit_reference_cli(
            "/owned/cardano-cli",
            tmp_path / "case.tx",
            socket_path="/env/socket/node2/sock",
            network_magic=42,
        )
    assert result["http"] == 0


def test_reference_cli_decode_failure_is_not_reported_as_a_node_response(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=["cardano-cli"],
            returncode=1,
            stdout="",
            stderr=(
                "TextEnvelope decode error: DeserialiseFailure 257 "
                '"Empty list found, expected non-empty"'
            ),
        ),
    )
    result = _submit_reference_cli(
        "/owned/cardano-cli",
        tmp_path / "case.tx",
        socket_path="/env/socket/node2/sock",
        network_magic=42,
    )
    assert result["http"] == 0
    assert result["observation_boundary"] == "client-decode"
    assert result["node_response_observed"] is False


def _raw_reference_runtime():
    return {
        "profile_id": "dwarf-substrate-run-1",
        "compose_project": "dwarf-substrate-run-1",
        "network_magic": 42,
    }


def _raw_reference_node():
    return {
        "id": "node2",
        "impl": "cardano-node",
        "container_id": "a" * 64,
        "container_image_id": "sha256:" + "b" * 64,
        "container_socket_path": "/env/socket/node2/sock",
        "source_revision": "c" * 40,
    }


def _raw_reference_inspect():
    return {
        "Id": "a" * 64,
        "Image": "sha256:" + "b" * 64,
        "Config": {
            "Labels": {
                "com.docker.compose.project": "dwarf-substrate-run-1",
                "io.dwarf.managed": "true",
                "io.dwarf.installation": "install-test",
                "io.dwarf.topology": "dwarf-substrate-run-1",
                "io.dwarf.node_id": "node2",
                "io.dwarf.implementation": "cardano-node",
                "io.dwarf.network_magic": "42",
            }
        },
    }


def _raw_helper_inspect(config_id):
    return {
        "Id": config_id,
        "RepoDigests": [],
        "Os": "linux",
        "Architecture": "amd64",
        "Config": {
            "Labels": {
                "org.opencontainers.image.revision": "7" * 40,
                "io.dwarf.component": "dwarf-localtx-submit-raw",
            }
        },
    }


def test_raw_reference_submit_preserves_exact_bytes_and_one_shot_confinement(
    monkeypatch,
):
    payload = bytes.fromhex("8401a0a0f5")
    calls = []
    monkeypatch.setenv("DWARF_INSTALLATION_ID", "install-test")
    monkeypatch.setattr(
        live_controls, "_inspect_container", lambda _identifier: _raw_reference_inspect()
    )
    config_id = "sha256:" + "d" * 64

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if command[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(
                command, 0, json.dumps([_raw_helper_inspect(config_id)]), ""
            )
        request = json.loads(kwargs["input"])
        assert request["payload_hex"] == payload.hex()
        assert request["transaction_era"] == "conway"
        assert request["expected_identity"] == request["observed_identity"]
        receipt = {
            "schema_version": 1,
            "outcome": "accepted",
            "payload_sha256": hashlib.sha256(payload).hexdigest(),
            "payload_length": len(payload),
            "transaction_era": "conway",
            "network_magic": 42,
            "negotiated_version": "NodeToClientV_22",
            "rejection": None,
            "detail": None,
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(receipt), "")

    monkeypatch.setattr(subprocess, "run", run)
    helper = config_id
    result = _submit_reference_raw(
        runtime=_raw_reference_runtime(),
        reference=_raw_reference_node(),
        payload=payload,
        helper_image=helper,
    )

    assert result["http"] == 202
    assert result["observation_boundary"] == "node-submit-accepted"
    assert result["identity_binding"]["socket_path"] == "/env/socket/node2/sock"
    assert result["identity_binding"]["container_id"] == "a" * 64
    assert result["node_response_observed"] is True
    assert result["protocol_receipt"]["payload_sha256"] == hashlib.sha256(payload).hexdigest()
    command, kwargs = calls[1]
    assert command[:4] == ["docker", "run", "--rm", "--pull"]
    assert "-i" in command[: command.index(helper)]
    assert ["--network", "none"] == command[command.index("--network") : command.index("--network") + 2]
    assert ["--volumes-from", "a" * 64 + ":ro"] == command[
        command.index("--volumes-from") : command.index("--volumes-from") + 2
    ]
    assert helper in command
    assert payload not in command
    assert kwargs["text"] is True
    assert result["helper_identity"] == {
        "kind": "local_config_id",
        "reference": config_id,
        "config_id": config_id,
        "platform": "linux/amd64",
        "source_revision": "7" * 40,
        "component": "dwarf-localtx-submit-raw",
    }


def test_raw_reference_submit_rejects_mutable_or_mismatched_helper_image(monkeypatch):
    payload = b"tx"
    monkeypatch.setenv("DWARF_INSTALLATION_ID", "install-test")
    monkeypatch.setattr(
        live_controls, "_inspect_container", lambda _identifier: _raw_reference_inspect()
    )
    calls = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **_kwargs: calls.append(command)
        or subprocess.CompletedProcess(
            command,
            0,
            json.dumps([_raw_helper_inspect("sha256:" + "e" * 64)]),
            "",
        ),
    )

    with pytest.raises(RuntimeError, match="immutable"):
        _submit_reference_raw(
            runtime=_raw_reference_runtime(),
            reference=_raw_reference_node(),
            payload=payload,
            helper_image="dwarf/adversary:latest",
        )
    assert calls == []

    with pytest.raises(RuntimeError, match="identity"):
        _submit_reference_raw(
            runtime=_raw_reference_runtime(),
            reference=_raw_reference_node(),
            payload=payload,
            helper_image="sha256:" + "d" * 64,
        )
    assert len(calls) == 1


def test_raw_helper_image_build_records_exact_source_provenance():
    component = ROOT / "antithesis/components/dwarf-adversary"
    dockerfile = (component / "Dockerfile").read_text(encoding="utf-8")
    build = (component / "build-image.sh").read_text(encoding="utf-8")

    assert "ARG DWARF_SOURCE_REVISION" in dockerfile
    assert "org.opencontainers.image.revision=${DWARF_SOURCE_REVISION}" in dockerfile
    assert 'io.dwarf.component="dwarf-localtx-submit-raw"' in dockerfile
    assert 'DWARF_SOURCE_REVISION' in build
    assert '--build-arg "DWARF_SOURCE_REVISION=${DWARF_SOURCE_REVISION}"' in build
    assert 'DWARF_FFI_ROOT="${DWARF_FFI_ROOT:-/home/dwarf/dwarf-ffi-fuzz}"' in build


def test_raw_reference_submit_refuses_owner_mismatch_before_runner(monkeypatch):
    calls = []
    inspect = _raw_reference_inspect()
    inspect["Config"]["Labels"]["io.dwarf.topology"] = "another-topology"
    monkeypatch.setenv("DWARF_INSTALLATION_ID", "install-test")
    monkeypatch.setattr(live_controls, "_inspect_container", lambda _identifier: inspect)
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: calls.append(True))

    with pytest.raises(RuntimeError, match="ownership"):
        _submit_reference_raw(
            runtime=_raw_reference_runtime(),
            reference=_raw_reference_node(),
            payload=b"tx",
            helper_image="ghcr.io/j-gainsec/dwarf-adversary@sha256:" + "d" * 64,
        )
    assert calls == []


@pytest.mark.parametrize(
    ("outcome", "expected_boundary", "node_response"),
    [
        ("node_rejected", "node-submit-rejected", True),
        ("transport_unavailable", "node-submit-transport", False),
        ("decode_close", "node-submit-decode-close", False),
    ],
)
def test_raw_reference_submit_preserves_protocol_boundary(
    monkeypatch, outcome, expected_boundary, node_response
):
    payload = b"tx"
    helper = "ghcr.io/j-gainsec/dwarf-adversary@sha256:" + "d" * 64
    helper_inspect = _raw_helper_inspect("sha256:" + "e" * 64)
    helper_inspect["RepoDigests"] = [helper]
    monkeypatch.setenv("DWARF_INSTALLATION_ID", "install-test")
    monkeypatch.setattr(
        live_controls, "_inspect_container", lambda _identifier: _raw_reference_inspect()
    )
    receipt = {
        "schema_version": 1,
        "outcome": outcome,
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "payload_length": len(payload),
        "transaction_era": "conway",
        "network_magic": 42,
        "negotiated_version": "NodeToClientV_22",
        "rejection": "MissingRedeemers" if node_response else None,
        "detail": None,
    }
    def run(command, **_kwargs):
        output = [helper_inspect] if command[:3] == ["docker", "image", "inspect"] else receipt
        return subprocess.CompletedProcess(command, 0, json.dumps(output), "")

    monkeypatch.setattr(subprocess, "run", run)

    result = _submit_reference_raw(
        runtime=_raw_reference_runtime(),
        reference=_raw_reference_node(),
        payload=payload,
        helper_image=helper,
    )
    assert result["observation_boundary"] == expected_boundary
    assert result["node_response_observed"] is node_response
    assert result["http"] == (400 if node_response else 0)


def _complete_reject_evidence():
    wire = bytes.fromhex("820282704d697373696e6752656465656d65727301")
    canonical = bytes.fromhex("82704d697373696e6752656465656d65727301")
    return {
        "schema_version": 1,
        "status": "complete",
        "wire_scope": "localtxsubmission-mini-protocol-response-after-demux",
        "wire_encoding": "base64",
        "wire_bytes_base64": base64.b64encode(wire).decode("ascii"),
        "wire_length": len(wire),
        "wire_sha256": hashlib.sha256(wire).hexdigest(),
        "wire_capture_limit": 65536,
        "wire_truncated": False,
        "term_tree_schema_version": 1,
        "term_tree": {
            "type": "list",
            "items": [
                {"type": "text", "value": "MissingRedeemers"},
                {"type": "int", "value": 1},
            ],
        },
        "term_node_count": 3,
        "term_max_depth": 2,
        "term_depth_limit": 64,
        "term_node_limit": 4096,
        "term_json_size_limit": 262144,
        "canonical_cbor_scope": "decoded-reject-term-canonical-cbor",
        "canonical_cbor_encoding": "base64",
        "canonical_cbor_base64": base64.b64encode(canonical).decode("ascii"),
        "canonical_cbor_length": len(canonical),
        "canonical_cbor_sha256": hashlib.sha256(canonical).hexdigest(),
        "canonical_matches_captured_reject": None,
        "failure_reason": None,
    }


def _oversized_reject_evidence():
    evidence = _complete_reject_evidence()
    wire = b"Z" * 65537
    evidence.update(
        status="oversized",
        wire_bytes_base64=None,
        wire_length=len(wire),
        wire_sha256=hashlib.sha256(wire).hexdigest(),
        wire_truncated=True,
        failure_reason="wire-response-exceeds-65536-bytes",
    )
    return evidence


def _submit_raw_receipt(monkeypatch, receipt):
    payload = b"tx"
    helper = "ghcr.io/j-gainsec/dwarf-adversary@sha256:" + "d" * 64
    helper_inspect = _raw_helper_inspect("sha256:" + "e" * 64)
    helper_inspect["RepoDigests"] = [helper]
    monkeypatch.setenv("DWARF_INSTALLATION_ID", "install-test")
    monkeypatch.setattr(
        live_controls, "_inspect_container", lambda _identifier: _raw_reference_inspect()
    )

    def run(command, **_kwargs):
        output = [helper_inspect] if command[:3] == ["docker", "image", "inspect"] else receipt
        return subprocess.CompletedProcess(command, 0, json.dumps(output), "")

    monkeypatch.setattr(subprocess, "run", run)
    return _submit_reference_raw(
        runtime=_raw_reference_runtime(),
        reference=_raw_reference_node(),
        payload=payload,
        helper_image=helper,
    )


def test_raw_reject_evidence_survives_validated_receipt_and_identity(monkeypatch):
    payload = b"tx"
    evidence = _complete_reject_evidence()
    receipt = {
        "schema_version": 1,
        "outcome": "node_rejected",
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "payload_length": len(payload),
        "transaction_era": "conway",
        "network_magic": 42,
        "negotiated_version": "NodeToClientV_22",
        "rejection": "TList [TString MissingRedeemers]",
        "detail": None,
        "reject_evidence": evidence,
    }

    result = _submit_raw_receipt(monkeypatch, receipt)

    assert result["http"] == 400
    assert result["observation_boundary"] == "node-submit-rejected"
    assert result["identity_binding"]["container_id"] == "a" * 64
    assert result["helper_identity"]["source_revision"] == "7" * 40
    retained = result["protocol_receipt"]["reject_evidence"]
    for key, value in evidence.items():
        assert retained[key] == value
    assert retained["wire_reject_term"] == {
        "schema_version": 1,
        "scope": "localtxsubmission-submitfail-reject-term",
        "encoding": "base64",
        "bytes_base64": evidence["canonical_cbor_base64"],
        "length": evidence["canonical_cbor_length"],
        "sha256": evidence["canonical_cbor_sha256"],
        "message_tag": 2,
        "matches_canonical_cbor": True,
    }
    assert "reject_evidence_validation" not in result["protocol_receipt"]


def test_raw_reject_evidence_oversized_requires_full_response_hash(monkeypatch):
    payload = b"tx"
    evidence = _oversized_reject_evidence()
    receipt = {
        "schema_version": 1,
        "outcome": "node_rejected",
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "payload_length": len(payload),
        "transaction_era": "conway",
        "network_magic": 42,
        "negotiated_version": "NodeToClientV_22",
        "rejection": "opaque typed rejection",
        "detail": None,
        "reject_evidence": evidence,
    }

    result = _submit_raw_receipt(monkeypatch, receipt)

    assert result["protocol_receipt"]["reject_evidence"] == evidence
    assert "reject_evidence_validation" not in result["protocol_receipt"]

    receipt["reject_evidence"] = {**evidence, "wire_sha256": None}
    result = _submit_raw_receipt(monkeypatch, receipt)
    assert "reject_evidence" not in result["protocol_receipt"]
    assert result["protocol_receipt"]["reject_evidence_validation"]["status"] == "invalid"

    receipt["reject_evidence"] = {**evidence, "wire_sha256": "not-a-sha256"}
    result = _submit_raw_receipt(monkeypatch, receipt)
    assert "reject_evidence" not in result["protocol_receipt"]
    assert result["protocol_receipt"]["reject_evidence_validation"]["status"] == "invalid"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda evidence: evidence.update(wire_encoding="hex"),
        lambda evidence: evidence.update(wire_length=evidence["wire_length"] + 1),
        lambda evidence: evidence.update(wire_sha256="0" * 64),
        lambda evidence: evidence.update(wire_bytes_base64="%%%"),
        lambda evidence: evidence.update(
            status="term-unavailable", failure_reason="false unavailability"
        ),
        lambda evidence: evidence.update(term_node_count=2),
        lambda evidence: evidence.update(canonical_cbor_length=999),
        lambda evidence: evidence.update(canonical_cbor_sha256="f" * 64),
    ],
)
def test_raw_reject_evidence_is_sanitized_without_changing_typed_outcome(
    monkeypatch, mutate
):
    payload = b"tx"
    evidence = copy.deepcopy(_complete_reject_evidence())
    mutate(evidence)
    receipt = {
        "schema_version": 1,
        "outcome": "node_rejected",
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "payload_length": len(payload),
        "transaction_era": "conway",
        "network_magic": 42,
        "negotiated_version": "NodeToClientV_22",
        "rejection": "opaque typed rejection",
        "detail": None,
        "reject_evidence": evidence,
    }

    result = _submit_raw_receipt(monkeypatch, receipt)

    assert result["http"] == 400
    assert result["node_response_observed"] is True
    assert "reject_evidence" not in result["protocol_receipt"]
    assert result["protocol_receipt"]["reject_evidence_validation"]["status"] == "invalid"


@pytest.mark.parametrize("outcome", ["accepted", "decode_close", "transport_unavailable"])
def test_raw_reject_evidence_never_survives_non_rejection_outcome(
    monkeypatch, outcome
):
    payload = b"tx"
    receipt = {
        "schema_version": 1,
        "outcome": outcome,
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "payload_length": len(payload),
        "transaction_era": "conway",
        "network_magic": 42,
        "negotiated_version": "NodeToClientV_22",
        "rejection": None,
        "detail": None,
        "reject_evidence": _complete_reject_evidence(),
    }

    result = _submit_raw_receipt(monkeypatch, receipt)

    assert "reject_evidence" not in result["protocol_receipt"]
    assert result["protocol_receipt"]["reject_evidence_validation"]["status"] == "invalid"


def test_reference_transport_close_uses_only_same_attempt_localtx_decoder_log(tmp_path):
    log_path = tmp_path / "node2.stdout.log"
    prior_line = (
        "[prior][node2:Net.Mux.Local.ExceptionExit] Miniprotocol "
        "(MiniProtocolNum 6) ResponderDir terminated with exception DecoderFailure "
        '(LocalTxSubmission ... DeserialiseFailure 111 "prior input")\n'
    )
    log_path.write_text(prior_line, encoding="utf-8")
    payload = bytes.fromhex("84a0a0f5")
    payload_sha256 = hashlib.sha256(payload).hexdigest()
    reference = {
        "id": "node2",
        "container_id": "1" * 64,
        "container_image_id": "sha256:" + "2" * 64,
        "source_revision": "3" * 40,
        "log_path": str(log_path),
    }
    identity_binding = {
        "node_id": "node2",
        "container_id": reference["container_id"],
        "container_image_id": reference["container_image_id"],
        "source_revision": reference["source_revision"],
    }

    def unavailable_receipt() -> dict:
        return {
            "http": 0,
            "body": "",
            "transport": "connection-error",
            "observation_boundary": "node-submit-transport",
            "node_response_observed": False,
            "classification": "unavailable",
            "identity_binding": identity_binding,
            "protocol_receipt": {
                "outcome": "transport_unavailable",
                "payload_sha256": payload_sha256,
                "payload_length": len(payload),
            },
        }

    def submit_with_matching_decoder_failure():
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(
                "[2026-10-16 07:22:50.7779Z][node2:Net.Mux.Local.ExceptionExit] "
                "Miniprotocol (MiniProtocolNum 6) ResponderDir terminated with "
                "exception DecoderFailure (LocalTxSubmission ... "
                'DeserialiseFailure 245 "end of input")\n'
            )
        return unavailable_receipt()

    observed = _submit_reference_with_decoder_log(
        submit=submit_with_matching_decoder_failure,
        payload=payload,
        reference=reference,
    )

    assert observed["classification"] == "decode_reject"
    assert observed["http"] == 0
    assert observed["protocol_receipt"] == unavailable_receipt()["protocol_receipt"]
    evidence = observed["reference_decoder_evidence"]
    assert evidence["payload_sha256"] == payload_sha256
    assert evidence["payload_length"] == len(payload)
    assert evidence["identity"] == identity_binding
    assert evidence["log_start_offset"] == len(prior_line.encode())
    assert evidence["log_end_offset"] > evidence["log_start_offset"]
    assert len(evidence["log_slice_sha256"]) == 64
    assert "DeserialiseFailure 245" in evidence["matched_line"]
    assert "prior input" not in evidence["matched_line"]
    assert live_controls._classify_live_response(observed) == "decode_reject"

    def submit_with_generic_close():
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write("connection closed without a protocol diagnostic\n")
        return unavailable_receipt()

    unavailable = _submit_reference_with_decoder_log(
        submit=submit_with_generic_close,
        payload=payload,
        reference=reference,
    )
    assert unavailable["classification"] == "unavailable"
    assert unavailable["http"] == 0
    assert "reference_decoder_evidence" not in unavailable


def test_target_funding_adoption_is_hash_bound_and_fails_fast_on_invalid_header(
    tmp_path,
):
    header_hash = "f" * 64
    log_path = tmp_path / "amaru.log"
    evidence_path = tmp_path / "target-adoption.json"
    log_path.write_text(
        " ".join(
            (
                "ERROR perf.header.lifecycle",
                f'header_hash="{header_hash}"',
                'error="Invalid VRF proof: VerificationFailed"',
                'outcome="invalid_header"',
            )
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="rejected the exact funding block"):
        live_controls._wait_for_target_adoption(
            log_path=log_path,
            expected_tip={"block": 3497, "slot": 345808, "hash": header_hash},
            evidence_path=evidence_path,
            timeout_seconds=1,
        )
    retained = json.loads(evidence_path.read_text())
    assert retained["status"] == "invalid-header"
    assert retained["expected_tip"]["hash"] == header_hash
    assert "Invalid VRF proof" in retained["blocking_diagnostic"]

    log_path.write_text(
        f'DEBUG tip.adopt slot=345808 header_hash="{header_hash}" '
        "block_height=3497 max_block_height=3497\n",
        encoding="utf-8",
    )
    adopted = live_controls._wait_for_target_adoption(
        log_path=log_path,
        expected_tip={"block": 3497, "slot": 345808, "hash": header_hash},
        evidence_path=evidence_path,
        timeout_seconds=1,
    )
    assert adopted["status"] == "adopted"
    assert adopted["expected_tip"]["block"] == 3497
