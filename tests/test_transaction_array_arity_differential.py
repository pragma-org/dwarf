import json
import subprocess
import hashlib
from pathlib import Path

import cbor2

from profile_manager.primitives import load_registry
from profile_manager.scenario import semantic_validate_scenario
from profile_manager.submit_primitives import RuntimeTransactionArrayArityDifferential
from scripts.runtime_transaction_array_arity import (
    _submit_reference_with_decoder_log,
    _submit_boundary_payload,
    assess_transaction_array_arity,
    build_three_element_variant,
)

from scripts import runtime_amaru_bootstrap_synth as bootstrap
from scripts import runtime_compose_substrate as compose
from scripts import runtime_substrate_common as common


def _generated_key_tree(env_root: Path) -> None:
    for index in range(1, 4):
        key_root = env_root / "utxo-keys" / f"utxo{index}"
        key_root.mkdir(parents=True)
        (key_root / "utxo.vkey").write_text(f"vkey-{index}\n", encoding="utf-8")
        (key_root / "utxo.skey").write_text(f"skey-{index}\n", encoding="utf-8")


def test_generated_workload_funding_retains_all_three_seeded_credentials(
    monkeypatch, tmp_path
):
    env_root = tmp_path / "env"
    _generated_key_tree(env_root)
    (env_root / "shelley-genesis.json").write_text(
        json.dumps(
            {
                "initialFunds": {"60" + "0" * 56: 120_000_000_000},
                "maxLovelaceSupply": 45_000_000_000_000_000,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    def fake_run(command, **_kwargs):
        if "initial-addr" in command:
            key_index = int(
                Path(
                    command[command.index("--verification-key-file") + 1]
                ).parent.name[-1]
            )
            return common.CommandResult(command, 0, f"addr_test1funded{key_index}\n", "")
        if "address" in command and "info" in command:
            address = command[command.index("--address") + 1]
            index = int(address[-1])
            return common.CommandResult(
                command,
                0,
                json.dumps({"base16": "60" + str(index) * 56, "type": "payment"}),
                "",
            )
        raise AssertionError(command)

    monkeypatch.setattr(compose, "run_command", fake_run)
    request = compose._prepare_generated_workload_funding(
        env_root=env_root,
        cardano_cli="/opt/cardano-cli",
        network_magic=42,
    )

    assert request["address"] == "addr_test1funded1"
    assert request["verification_key"] == "utxo-keys/utxo1/utxo.vkey"
    assert [item["id"] for item in request["credentials"]] == [
        "utxo1",
        "utxo2",
        "utxo3",
    ]
    assert [item["address_base16"] for item in request["credentials"]] == [
        "60" + "1" * 56,
        "60" + "2" * 56,
        "60" + "3" * 56,
    ]
    assert all("signing" not in json.dumps(item) for item in request["credentials"])


def test_staged_loader_materializes_three_independent_genesis_inputs(tmp_path):
    scripts = tmp_path / "scripts"
    configs = tmp_path / "configs"
    generated = tmp_path / "generated"
    request = tmp_path / "workload-funding.json"
    evidence = generated / "workload-funding-applied.json"
    genesis = configs / "1" / "configs" / "shelley-genesis.json"
    genesis.parent.mkdir(parents=True)
    generated.mkdir()
    genesis.write_text(
        json.dumps(
            {
                "initialFunds": {"60" + "0" * 56: 120_000_000_000},
                "maxLovelaceSupply": 45_000_000_000_000_000,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    credentials = [
        {
            "id": f"utxo{index}",
            "address": f"addr_test1funded{index}",
            "address_base16": "60" + str(index) * 56,
            "verification_key": f"utxo-keys/utxo{index}/utxo.vkey",
        }
        for index in range(1, 4)
    ]
    request.write_text(
        json.dumps({**credentials[0], "credentials": credentials}) + "\n",
        encoding="utf-8",
    )

    bootstrap._stage_loader_scripts(scripts)
    subprocess.run(
        [
            "bash",
            str(scripts / "apply-workload-funding.sh"),
            str(request),
            str(evidence),
            str(configs),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    body = json.loads(genesis.read_text(encoding="utf-8"))
    retained = json.loads(evidence.read_text(encoding="utf-8"))
    assert [item["id"] for item in retained["credentials"]] == [
        "utxo1",
        "utxo2",
        "utxo3",
    ]
    for credential in retained["credentials"]:
        assert body["initialFunds"][credential["address_base16"]] == 120_000_000_000
        assert credential["lovelace"] == 120_000_000_000
    assert retained["address"] == credentials[0]["address"]
    assert "skey" not in json.dumps(retained)


def test_three_element_variant_omits_auxiliary_element_without_trailing_bytes():
    body = {0: {(bytes.fromhex("11" * 32), 0)}, 1: [], 2: 177_777}
    witnesses = {0: [[bytes.fromhex("22" * 32), bytes.fromhex("33" * 64)]]}
    canonical = cbor2.dumps([body, witnesses, True, None])

    built = build_three_element_variant(canonical)

    assert cbor2.loads(built["variant_payload"]) == [body, witnesses, True]
    assert built["canonical_element_count"] == 4
    assert built["variant_element_count"] == 3
    assert built["canonical_auxiliary_data_is_null"] is True
    assert built["variant_trailing_bytes"] == 0
    assert built["body_sha256"] == hashlib.sha256(cbor2.dumps(body)).hexdigest()
    expected_transaction_id = hashlib.blake2b(
        cbor2.dumps(body), digest_size=32
    ).hexdigest()
    assert built["transaction_id"] == expected_transaction_id
    assert built["canonical_payload_sha256"] == hashlib.sha256(canonical).hexdigest()
    assert built["variant_payload_sha256"] == hashlib.sha256(
        built["variant_payload"]
    ).hexdigest()


def test_shared_boundary_is_observed_by_reference_before_target():
    calls = []

    def submit(role, payload):
        calls.append((role, payload))
        return {"classification": "accepted"}

    payload = bytes.fromhex("830102f5")
    responses = _submit_boundary_payload(submit, payload)

    assert calls == [("reference", payload), ("target", payload)]
    assert list(responses) == ["reference", "target"]


def test_reference_transport_close_uses_only_same_attempt_localtx_decoder_log(tmp_path):
    log_path = tmp_path / "node2.stdout.log"
    log_path.write_text("earlier unrelated output\n", encoding="utf-8")
    payload = bytes.fromhex("83a0a0f5")
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

    def submit_with_matching_decoder_failure():
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(
                "[2026-10-16 07:22:50.7779Z][node2:Net.Mux.Local.ExceptionExit] "
                "Miniprotocol (MiniProtocolNum 6) ResponderDir terminated with "
                "exception DecoderFailure (LocalTxSubmission ... "
                'DeserialiseFailure 245 "end of input")\n'
            )
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

    observed = _submit_reference_with_decoder_log(
        submit=submit_with_matching_decoder_failure,
        payload=payload,
        reference=reference,
    )

    assert observed["classification"] == "decode_reject"
    assert observed["http"] == 0
    assert observed["node_response_observed"] is False
    evidence = observed["reference_decoder_evidence"]
    assert evidence["classification"] == "decode_reject"
    assert evidence["payload_sha256"] == payload_sha256
    assert evidence["payload_length"] == len(payload)
    assert evidence["identity"] == identity_binding
    assert evidence["log_path"] == str(log_path)
    assert evidence["log_start_offset"] == len("earlier unrelated output\n")
    assert evidence["log_end_offset"] > evidence["log_start_offset"]
    assert len(evidence["log_slice_sha256"]) == 64
    assert "DeserialiseFailure 245" in evidence["matched_line"]

    def submit_with_generic_close():
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write("connection closed without a protocol diagnostic\n")
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

    unavailable = _submit_reference_with_decoder_log(
        submit=submit_with_generic_close,
        payload=payload,
        reference=reference,
    )
    assert unavailable["classification"] == "unavailable"
    assert "reference_decoder_evidence" not in unavailable


def test_assessment_requires_independent_controls_and_identical_variant_bytes():
    shared_hash = "d" * 64
    status = assess_transaction_array_arity(
        {
            "target": {
                "funding_credential": "utxo1",
                "classification": "accepted",
                "payload_sha256": "a" * 64,
            },
            "reference": {
                "funding_credential": "utxo2",
                "classification": "accepted",
                "payload_sha256": "b" * 64,
            },
        },
        {
            "funding_credential": "utxo3",
            "variant_element_count": 3,
            "variant_trailing_bytes": 0,
            "payload_sha256": shared_hash,
            "target": {"classification": "accepted", "payload_sha256": shared_hash},
            "reference": {
                "classification": "decode_reject",
                "payload_sha256": shared_hash,
            },
        },
    )

    assert status["valid_controls_complete"] is True
    assert status["funding_credentials_isolated"] is True
    assert status["variant_payloads_match"] is True
    assert status["three_element_boundary_reached"] is True
    assert status["outcomes_conclusive"] is True
    assert status["divergence_observed"] is True
    assert status["complete"] is True


def test_transaction_array_primitive_and_portable_scenario_validate(monkeypatch, tmp_path):
    root = Path(__file__).resolve().parents[1]
    registry_path = root / "dwarf/primitives/registry.json"
    registry = load_registry(registry_path)
    scenario_path = root / (
        "dwarf/scenarios/ledger-submit-transaction-array-arity-"
        "differential-amaru-cardano-node.yaml"
    )

    assert registry["runtime_transaction_array_arity_differential"].family == "load"
    assert registry["transaction_array_arity_differential_matches"].family == "assertion"
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
        "scripts.runtime_transaction_array_arity.run_transaction_array_arity_differential",
        fake_run,
    )
    primitive = RuntimeTransactionArrayArityDifferential(
        params={
            "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
            "target_node": "node1",
            "reference_node": "node2",
            "output_dir": "outputs/transaction-array-arity",
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
