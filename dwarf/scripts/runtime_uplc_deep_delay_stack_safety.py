#!/usr/bin/env python3
"""Exercise the release-fixed recursive UPLC FLAT term decoder at depth 28,000."""
from __future__ import annotations

import hashlib
import io
import json
import os
from collections.abc import Callable, Mapping
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path
from typing import Any

import cbor2


CONTROL_DEPTH = 1
BOUNDARY_DEPTH = 28_000
MINTING_ARGUMENT_COUNT = 2
MINT_EXECUTION_STEPS = 500_000_000
MINT_EXECUTION_MEMORY = 1_000_000
CONTROL_CASE = "shallow-control"
BOUNDARY_CASE = "deep-delay-28000"
UPSTREAM_FIX_COMMIT = "d9bb18e71e70a299c260653806de6fc557eeeb04"


class _FlatEncoder:
    def __init__(self) -> None:
        self.buffer = bytearray()
        self.used_bits = 0
        self.current_byte = 0

    def _next_word(self) -> None:
        self.buffer.append(self.current_byte)
        self.used_bits = 0
        self.current_byte = 0

    def bits(self, count: int, value: int) -> None:
        if count < 1 or count > 8 or value >= 1 << count:
            raise ValueError("invalid FLAT bit field")
        self.used_bits += count
        unused = 8 - self.used_bits
        if unused == 0:
            self.current_byte |= value
            self._next_word()
        elif unused > 0:
            self.current_byte |= value << unused
        else:
            used = -unused
            self.current_byte |= value >> used
            self._next_word()
            self.current_byte = (value << (8 - used)) & 0xFF
            self.used_bits = used

    def word(self, value: int) -> None:
        if isinstance(value, bool) or value < 0:
            raise ValueError("FLAT word must be nonnegative")
        while True:
            byte = value & 127
            value >>= 7
            if value:
                byte |= 128
            self.bits(8, byte)
            if not value:
                return

    def filler(self) -> None:
        self.current_byte |= 1
        self._next_word()


def _encode_deep_delay_program(depth: int, *, lambda_count: int) -> bytes:
    """Encode UPLC 1.0.0 lambdas around the exact Delay^depth(Error) term."""

    if isinstance(depth, bool) or not isinstance(depth, int) or depth < 1:
        raise ValueError("delay depth must be a positive integer")
    if isinstance(lambda_count, bool) or lambda_count < 0:
        raise ValueError("lambda count must be nonnegative")
    encoder = _FlatEncoder()
    for value in (1, 0, 0):
        encoder.word(value)
    for _ in range(lambda_count):
        encoder.bits(4, 2)  # LAMBDA; DeBruijn parameters encode no additional bits
    for _ in range(depth):
        encoder.bits(4, 1)  # DELAY
    encoder.bits(4, 6)  # ERROR
    encoder.filler()
    return bytes(encoder.buffer)


def encode_upstream_deep_delay_program(depth: int) -> bytes:
    """Return the byte-exact upstream decoder regression program."""

    return _encode_deep_delay_program(depth, lambda_count=0)


def encode_deep_delay_program(depth: int) -> bytes:
    """Return a callable V1 minting policy containing the exact upstream term."""

    return _encode_deep_delay_program(depth, lambda_count=MINTING_ARGUMENT_COUNT)


def _output_with_coin(template: object, address: bytes, coin: int) -> object:
    if isinstance(template, list) and len(template) >= 2:
        result = list(template)
        value = result[1]
        if isinstance(value, list) and value:
            value = list(value)
            value[0] = coin
            result[1] = value
        elif isinstance(value, int):
            result[1] = coin
        else:
            raise ValueError("transaction output value has an unsupported shape")
        result[0] = address
        return result
    if isinstance(template, dict):
        result = dict(template)
        value = result.get(1)
        if isinstance(value, list) and value:
            value = list(value)
            value[0] = coin
            result[1] = value
        elif isinstance(value, int):
            result[1] = coin
        else:
            raise ValueError("transaction output value has an unsupported shape")
        result[0] = address
        return result
    raise ValueError("transaction output has an unsupported shape")


def _positive_parameter(parameters: Mapping[str, Any], name: str) -> int:
    value = parameters.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"protocol parameter {name} must be a positive integer")
    return value


def _positive_decimal_parameter(parameters: Mapping[str, Any], name: str) -> Decimal:
    value = parameters.get(name)
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError(f"protocol parameter {name} must be an exact positive decimal")
    try:
        decimal = value if isinstance(value, Decimal) else Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(
            f"protocol parameter {name} must be an exact positive decimal"
        ) from None
    if not decimal.is_finite() or decimal <= 0:
        raise ValueError(f"protocol parameter {name} must be an exact positive decimal")
    return decimal


def _mint_execution_budget(witnesses: Mapping[Any, Any]) -> tuple[int, int]:
    redeemers = witnesses.get(5)
    if not isinstance(redeemers, dict) or len(redeemers) != 1:
        raise ValueError("deep-delay transaction requires one mint redeemer")
    entry = redeemers.get((1, 0))
    if not isinstance(entry, list) or len(entry) != 2:
        raise ValueError("deep-delay transaction mint redeemer has an unsupported shape")
    budget = entry[1]
    if not isinstance(budget, list) or len(budget) != 2:
        raise ValueError("deep-delay transaction mint budget has an unsupported shape")
    memory, steps = budget
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in budget):
        raise ValueError("deep-delay transaction mint budget must contain positive integers")
    if (steps, memory) != (MINT_EXECUTION_STEPS, MINT_EXECUTION_MEMORY):
        raise ValueError("deep-delay transaction mint budget does not match the runtime budget")
    return steps, memory


def _ledger_fee_transaction_size(payload: bytes) -> int:
    transaction = cbor2.loads(payload)
    if not isinstance(transaction, list) or len(transaction) != 4 or payload[:1] != b"\x84":
        raise ValueError("signed transaction does not use the expected four-element encoding")
    # Cardano's toCBORForSizeComputation retains its own one-byte three-item list
    # header, but omits the wire shape's one-byte IsValid field for Mary compatibility.
    return len(payload) - 1


def build_deep_delay_transaction(
    *,
    template_payload: bytes,
    address: bytes,
    input_lovelace: int,
    expected_flat: bytes,
    protocol_parameters: Mapping[str, Any],
    sign_body: Callable[[bytes], bytes],
) -> dict[str, Any]:
    """Finalize one fee-correct signed transaction with an exact Plutus V1 script."""

    transaction = cbor2.loads(template_payload)
    if not isinstance(transaction, list) or len(transaction) != 4:
        raise ValueError("transaction template must be a four-element transaction")
    body, witnesses = transaction[0], transaction[1]
    if not isinstance(body, dict) or not isinstance(witnesses, dict):
        raise ValueError("transaction template body/witness set has an unsupported shape")
    outputs = body.get(1)
    scripts = witnesses.get(3)
    if not isinstance(outputs, list) or len(outputs) != 1:
        raise ValueError("deep-delay transaction requires exactly one output")
    if not isinstance(scripts, (list, set, tuple)) or len(scripts) != 1:
        raise ValueError("deep-delay transaction requires exactly one Plutus V1 script")
    expected_witness = cbor2.dumps(expected_flat)
    if next(iter(scripts)) != expected_witness:
        raise ValueError("Plutus V1 witness does not contain the expected FLAT program")
    if input_lovelace <= 0:
        raise ValueError("funding input must be positive")

    max_tx_size = _positive_parameter(protocol_parameters, "maxTxSize")
    fee_per_byte = _positive_parameter(protocol_parameters, "txFeePerByte")
    fee_fixed = _positive_parameter(protocol_parameters, "txFeeFixed")
    coins_per_utxo_byte = _positive_parameter(protocol_parameters, "utxoCostPerByte")
    cost_models = protocol_parameters.get("costModels")
    if not isinstance(cost_models, dict) or not isinstance(cost_models.get("PlutusV1"), list):
        raise ValueError("live protocol parameters do not contain a PlutusV1 cost model")
    execution_prices = protocol_parameters.get("executionUnitPrices")
    if not isinstance(execution_prices, Mapping):
        raise ValueError("live protocol parameters do not contain execution unit prices")
    price_steps = _positive_decimal_parameter(execution_prices, "priceSteps")
    price_memory = _positive_decimal_parameter(execution_prices, "priceMemory")
    execution_steps, execution_memory = _mint_execution_budget(witnesses)
    execution_fee = int(
        (
            price_steps * execution_steps
            + price_memory * execution_memory
        ).to_integral_value(rounding=ROUND_CEILING)
    )

    fee = 1
    payload = b""
    body_raw = b""
    fee_transaction_size = 0
    final_output: object = outputs[0]
    for _ in range(12):
        output_coin = input_lovelace - fee
        if output_coin <= 0:
            raise ValueError("funding input cannot satisfy transaction fee")
        final_output = _output_with_coin(outputs[0], address, output_coin)
        final_body = dict(body)
        final_body[1] = [final_output]
        final_body[2] = fee
        body_raw = cbor2.dumps(final_body)
        payload = sign_body(body_raw)
        fee_transaction_size = _ledger_fee_transaction_size(payload)
        minimum_fee = fee_fixed + fee_per_byte * fee_transaction_size + execution_fee
        if minimum_fee == fee:
            break
        fee = minimum_fee
    else:
        raise RuntimeError("minimum-fee calculation did not converge")

    output_size = len(cbor2.dumps(final_output))
    minimum_coin = (160 + output_size) * coins_per_utxo_byte
    decoded_output = cbor2.loads(cbor2.dumps(final_output))
    output_value = decoded_output[1] if isinstance(decoded_output, list) else decoded_output[1]
    output_coin = output_value[0] if isinstance(output_value, list) else output_value
    if output_coin < minimum_coin:
        raise ValueError("deep-delay output is below minimum ADA")
    if len(payload) > max_tx_size:
        raise ValueError("deep-delay transaction exceeds maxTxSize")
    stream = io.BytesIO(payload)
    cbor2.CBORDecoder(stream).decode()
    if stream.tell() != len(payload):
        raise ValueError("deep-delay transaction has trailing outer bytes")
    if expected_flat == encode_deep_delay_program(BOUNDARY_DEPTH):
        depth = BOUNDARY_DEPTH
    elif expected_flat == encode_deep_delay_program(CONTROL_DEPTH):
        depth = CONTROL_DEPTH
    else:
        raise ValueError("deep-delay program does not match the audited control or boundary")
    upstream = encode_upstream_deep_delay_program(depth)
    if expected_flat[:3] != upstream[:3] or expected_flat[4:] != upstream[3:]:
        raise ValueError("minting wrapper does not preserve the upstream recursive term")
    return {
        "depth": depth,
        "flat": expected_flat,
        "flat_size": len(expected_flat),
        "flat_sha256": hashlib.sha256(expected_flat).hexdigest(),
        "embedded_upstream_size": len(upstream),
        "embedded_upstream_sha256": hashlib.sha256(upstream).hexdigest(),
        "executable_program_size": len(expected_flat),
        "executable_program_sha256": hashlib.sha256(expected_flat).hexdigest(),
        "minting_argument_count": MINTING_ARGUMENT_COUNT,
        "script_witness_sha256": hashlib.sha256(expected_witness).hexdigest(),
        "body": body_raw,
        "body_sha256": hashlib.sha256(body_raw).hexdigest(),
        "payload": payload,
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "transaction_size": len(payload),
        "fee_transaction_size": fee_transaction_size,
        "headroom_bytes": max_tx_size - len(payload),
        "fee": fee,
        "execution_fee": execution_fee,
        "execution_units": {
            "steps": execution_steps,
            "memory": execution_memory,
        },
        "output_coin": output_coin,
        "minimum_coin": minimum_coin,
        "minimum_ada_satisfied": output_coin >= minimum_coin,
        "outer_trailing_bytes": 0,
        "protocol_limits": {
            "max_tx_size": max_tx_size,
            "fee_per_byte": fee_per_byte,
            "fee_fixed": fee_fixed,
            "price_steps": str(price_steps),
            "price_memory": str(price_memory),
            "coins_per_utxo_byte": coins_per_utxo_byte,
            "plutus_v1_cost_model_present": True,
        },
    }


def _script_file(path: Path, flat: bytes, *, description: str) -> None:
    path.write_text(
        json.dumps(
            {
                "type": "PlutusScriptV1",
                "description": description,
                "cborHex": cbor2.dumps(flat).hex(),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def assess_deep_delay_stack_safety(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_case = {str(record.get("case_id") or ""): record for record in records}
    control = by_case.get(CONTROL_CASE) or {}
    boundary = by_case.get(BOUNDARY_CASE) or {}

    def complete_control(role: str) -> bool:
        item = control.get(role) or {}
        return bool(
            item.get("classification") == "accepted"
            and item.get("included") is True
            and item.get("target_adopted") is True
            and item.get("transaction_id")
        )

    controls_complete = all(complete_control(role) for role in ("target", "reference"))
    funding_inputs = [
        str((control.get(role) or {}).get("funding_input") or "")
        for role in ("target", "reference")
    ] + [str(boundary.get("funding_input") or "")]
    funding_isolated = bool(
        all(funding_inputs) and len(funding_inputs) == len(set(funding_inputs))
    )
    payload_sha256 = str(boundary.get("payload_sha256") or "")
    roles = [boundary.get(role) or {} for role in ("target", "reference")]
    payloads_match = bool(payload_sha256) and all(
        role.get("payload_sha256") == payload_sha256 for role in roles
    )
    reached = bool(
        boundary.get("depth") == BOUNDARY_DEPTH
        and boundary.get("flat_sha256")
        and int(boundary.get("transaction_size") or 0) <= 16_384
    )
    included = boundary.get("included") is True
    adopted = boundary.get("target_adopted") is True
    role_evidence = all(
        role.get("classification") in {
            "accepted",
            "decode_reject",
            "ledger_reject",
            "phase2_reject",
            "transport_unavailable",
        }
        for role in roles
    )
    complete = bool(
        controls_complete
        and funding_isolated
        and payloads_match
        and reached
        and included
        and adopted
        and role_evidence
    )
    return {
        "valid_controls_complete": controls_complete,
        "funding_inputs_isolated": funding_isolated,
        "boundary_payloads_match": payloads_match,
        "deep_boundary_reached": reached,
        "boundary_included": included,
        "target_adopted_boundary": adopted,
        "role_outcomes_recorded": role_evidence,
        "security_divergence_established": False,
        "complete": complete,
    }


def run_uplc_deep_delay_stack_safety(
    *, runtime: dict[str, Any], output_dir: Path,
    target_node: str, reference_node: str,
    expected_target_version: str, expected_target_source: str,
    expected_reference_version: str, expected_reference_source: str,
) -> dict[str, Any]:
    """Fund, construct and submit shallow controls plus the exact deep vector."""

    from profile_manager.submit_primitives import _validation_stage
    from scripts.runtime_cardano_measurement_calibration import (
        _checked,
        _containerized_cardano_workload,
        _controller_container,
        _inspect_container,
        _query_utxo,
        _transaction_id,
    )
    from scripts.runtime_governance_zero_denominator import (
        _terminal_inclusion_tip,
        resign_transaction_body,
    )
    from scripts.runtime_redeemer_container_live_controls import (
        _envelope,
        _query_reference_tip,
        _reference_tx_submission_propagation_gate,
        _select_funding_input,
        _submit_amaru_container,
        _submit_reference_raw,
        _wait_for_reference_inclusion,
        _wait_for_target_adoption,
        _write_envelope,
        split_transaction,
        validate_runtime_identity,
    )

    identities = validate_runtime_identity(
        runtime,
        target_node=target_node,
        reference_node=reference_node,
        expected_target_version=expected_target_version,
        expected_target_source=expected_target_source,
        expected_reference_version=expected_reference_version,
        expected_reference_source=expected_reference_source,
    )
    controller = _controller_container()
    if controller is None:
        raise RuntimeError("deep-delay stack-safety case requires the installed Docker controller")
    runtime_root = Path(str(runtime.get("runtime_root") or ""))
    if not runtime_root.is_absolute():
        raise RuntimeError("runtime root is not absolute")
    output_dir.mkdir(parents=True, exist_ok=True)
    target, reference = identities["target"], identities["reference"]
    network_magic = int(runtime["network_magic"])
    socket_path = str(reference.get("container_socket_path") or "")
    helper_image = str(os.environ.get("DWARF_LOCALTXSUBMISSION_HELPER_IMAGE") or "")
    target_propagation_gate = _reference_tx_submission_propagation_gate(
        reference, inspect_container=_inspect_container
    )

    with _containerized_cardano_workload(
        runtime_root=runtime_root,
        runtime=runtime,
        node=reference,
        output_dir=output_dir,
        controller=controller,
    ) as (workload_runtime, _workload_node):
        cli = str(workload_runtime["support_binaries"]["cardano-cli"])
        key_root = Path(str(workload_runtime["workload_key_root"]))
        controller_key_root = Path(str(workload_runtime["controller_workload_key_root"]))
        address = _checked([
            cli, "latest", "genesis", "initial-addr", "--verification-key-file",
            str(key_root / "utxo.vkey"), "--testnet-magic", str(network_magic),
        ]).stdout.strip()
        if (runtime.get("generated_workload_funding") or {}).get("address") != address:
            raise RuntimeError("deep-delay funding address does not match generated substrate")

        pparams_path = output_dir / "protocol-parameters.json"
        _checked([
            cli, "query", "protocol-parameters", "--socket-path", socket_path,
            "--testnet-magic", str(network_magic), "--out-file", str(pparams_path),
        ])
        pparams = json.loads(
            pparams_path.read_text(encoding="utf-8"), parse_float=Decimal
        )
        for name in ("maxTxSize", "txFeePerByte", "txFeeFixed", "utxoCostPerByte"):
            _positive_parameter(pparams, name)
        if not isinstance((pparams.get("costModels") or {}).get("PlutusV1"), list):
            raise RuntimeError("live protocol parameters omit the PlutusV1 cost model")

        funding_before = _query_utxo(
            cli=cli, socket_path=socket_path, network_magic=network_magic,
            address=address, destination=output_dir / "funding-before.json",
        )
        fresh_input, _ = _select_funding_input(funding_before, minimum_lovelace=76_000_000)
        setup_body = output_dir / "funding-setup.body"
        command = [
            cli, "conway", "transaction", "build", "--socket-path", socket_path,
            "--testnet-magic", str(network_magic), "--tx-in", fresh_input,
            "--change-address", address,
        ]
        for _ in range(3):
            command.extend(["--tx-out", f"{address}+20000000"])
        for _ in range(3):
            command.extend(["--tx-out", f"{address}+5000000"])
        command.extend(["--out-file", str(setup_body)])
        _checked(command)
        setup_tx = output_dir / "funding-setup.tx"
        _checked([
            cli, "conway", "transaction", "sign", "--tx-body-file", str(setup_body),
            "--signing-key-file", str(key_root / "utxo.skey"), "--testnet-magic",
            str(network_magic), "--out-file", str(setup_tx),
        ])
        setup_id = _transaction_id(cli, setup_tx)
        funding_tip_before = _query_reference_tip(
            cli=cli, socket_path=socket_path, network_magic=network_magic
        )
        _checked([
            cli, "conway", "transaction", "submit", "--tx-file", str(setup_tx),
            "--socket-path", socket_path, "--testnet-magic", str(network_magic),
        ])
        funding_ready = _wait_for_reference_inclusion(
            cli=cli, socket_path=socket_path, network_magic=network_magic,
            address=address, destination=output_dir / "funding-ready.json",
            transaction_id=setup_id, initial_tip=funding_tip_before,
            evidence_path=output_dir / "funding-inclusion.json",
        )
        spends = [f"{setup_id}#{index}" for index in range(3)]
        collaterals = [f"{setup_id}#{index}" for index in range(3, 6)]
        for txin, amount in zip(spends + collaterals, [20_000_000] * 3 + [5_000_000] * 3):
            if ((funding_ready.get(txin) or {}).get("value") or {}).get("lovelace") != amount:
                raise RuntimeError("deep-delay funding output does not match")
        _wait_for_target_adoption(
            log_path=Path(str(target.get("log_path") or "")),
            expected_tip=_terminal_inclusion_tip(
                output_dir / "funding-inclusion.json", label="deep-delay funding"
            ),
            evidence_path=output_dir / "funding-target-adoption.json",
        )

        unit_path = output_dir / "unit.json"
        unit_path.write_text(json.dumps({"constructor": 0, "fields": []}) + "\n", encoding="utf-8")

        def build_case(case_dir: Path, funding_input: str, collateral_input: str, depth: int) -> dict[str, Any]:
            case_dir.mkdir(parents=True, exist_ok=True)
            flat = encode_deep_delay_program(depth)
            script_path = case_dir / "deep-delay-v1.plutus"
            _script_file(
                script_path,
                flat,
                description=f"UPLC lambda^2(Delay^{depth}(Error)) V1 minting policy",
            )
            policy_id = _checked([
                cli, "conway", "transaction", "policyid", "--script-file", str(script_path),
            ]).stdout.strip()
            if len(policy_id) != 56:
                raise RuntimeError("deep-delay policy id is invalid")
            token = f"1 {policy_id}.535441434b"
            raw_path = case_dir / "template.raw"
            _checked([
                cli, "conway", "transaction", "build-raw", "--tx-in", funding_input,
                "--tx-out", f"{address}+19000000+{token}", "--fee", "1000000",
                "--mint", token, "--mint-script-file", str(script_path),
                "--mint-redeemer-file", str(unit_path), "--mint-execution-units",
                f"({MINT_EXECUTION_STEPS},{MINT_EXECUTION_MEMORY})",
                "--protocol-params-file", str(pparams_path),
                "--tx-in-collateral", collateral_input, "--out-file", str(raw_path),
            ])
            signed_template = case_dir / "template.signed"
            _checked([
                cli, "conway", "transaction", "sign", "--tx-body-file", str(raw_path),
                "--signing-key-file", str(key_root / "utxo.skey"), "--testnet-magic",
                str(network_magic), "--out-file", str(signed_template),
            ])
            envelope, template_payload = _envelope(signed_template)
            template_body, _witnesses, _tail = split_transaction(template_payload)
            output_address = cbor2.loads(template_body)[1][0][0]
            built = build_deep_delay_transaction(
                template_payload=template_payload,
                address=output_address,
                input_lovelace=20_000_000,
                expected_flat=flat,
                protocol_parameters=pparams,
                sign_body=lambda body: resign_transaction_body(
                    template_payload, body, [controller_key_root / "utxo.skey"]
                ),
            )
            final_path = case_dir / "transaction.signed"
            _write_envelope(final_path, envelope, built["payload"])
            txid = hashlib.blake2b(built["body"], digest_size=32).hexdigest()
            return {
                **{key: value for key, value in built.items() if key not in {"flat", "body", "payload"}},
                "payload": built["payload"],
                "transaction_id": txid,
                "funding_input": funding_input,
                "collateral_input": collateral_input,
                "policy_id": policy_id,
            }

        def submit_and_observe(role: str, built: dict[str, Any], case_dir: Path) -> dict[str, Any]:
            pre_tip = _query_reference_tip(cli=cli, socket_path=socket_path, network_magic=network_magic)
            response = (
                _submit_amaru_container(str(target["container_id"]), built["payload"])
                if role == "target"
                else _submit_reference_raw(
                    runtime=runtime, reference=reference, payload=built["payload"],
                    helper_image=helper_image,
                )
            )
            classification = _validation_stage(response)
            included = False
            adopted = False
            if classification == "accepted":
                inclusion_path = case_dir / "inclusion.json"
                try:
                    _wait_for_reference_inclusion(
                        cli=cli, socket_path=socket_path, network_magic=network_magic,
                        address=address, destination=case_dir / "included-utxo.json",
                        transaction_id=built["transaction_id"], initial_tip=pre_tip,
                        evidence_path=inclusion_path,
                        target_propagation_gate=(
                            target_propagation_gate if role == "target" else None
                        ),
                    )
                    included = True
                    _wait_for_target_adoption(
                        log_path=Path(str(target.get("log_path") or "")),
                        expected_tip=_terminal_inclusion_tip(
                            inclusion_path, label=f"deep-delay {role} transaction"
                        ),
                        evidence_path=case_dir / "target-adoption.json",
                    )
                    adopted = True
                except RuntimeError:
                    included = False
                    adopted = False
            return {
                **response,
                "classification": classification,
                "included": included,
                "target_adopted": adopted,
                "funding_input": built["funding_input"],
                "collateral_input": built["collateral_input"],
                "transaction_id": built["transaction_id"],
                "payload_sha256": built["payload_sha256"],
                "body_sha256": built["body_sha256"],
            }

        records: list[dict[str, Any]] = []
        control: dict[str, Any] = {"case_id": CONTROL_CASE, "depth": CONTROL_DEPTH}
        for index, role in enumerate(("target", "reference")):
            case_dir = output_dir / CONTROL_CASE / role
            built = build_case(case_dir, spends[index], collaterals[index], CONTROL_DEPTH)
            control[role] = {**submit_and_observe(role, built, case_dir), **{
                key: built[key] for key in (
                    "flat_size", "flat_sha256", "embedded_upstream_size",
                    "embedded_upstream_sha256", "executable_program_size",
                    "executable_program_sha256", "minting_argument_count",
                    "script_witness_sha256",
                    "transaction_size", "fee_transaction_size", "headroom_bytes",
                    "fee", "execution_fee", "execution_units", "policy_id",
                    "protocol_limits",
                )
            }}
            (case_dir / "result.json").write_text(
                json.dumps(control[role], indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        records.append(control)
        if not all(
            control[role]["classification"] == "accepted"
            and control[role]["included"] is True
            and control[role]["target_adopted"] is True
            for role in ("target", "reference")
        ):
            raise RuntimeError("deep-delay controls are incomplete; boundary was not submitted")

        boundary_dir = output_dir / BOUNDARY_CASE
        built = build_case(boundary_dir, spends[2], collaterals[2], BOUNDARY_DEPTH)
        pre_tip = _query_reference_tip(cli=cli, socket_path=socket_path, network_magic=network_magic)
        responses = {
            "reference": _submit_reference_raw(
                runtime=runtime, reference=reference, payload=built["payload"], helper_image=helper_image
            ),
            "target": _submit_amaru_container(str(target["container_id"]), built["payload"]),
        }
        classifications = {role: _validation_stage(response) for role, response in responses.items()}
        accepted_roles = {
            role for role, classification in classifications.items()
            if classification == "accepted"
        }
        included = False
        adopted = False
        if "accepted" in classifications.values():
            inclusion_path = boundary_dir / "inclusion.json"
            try:
                _wait_for_reference_inclusion(
                    cli=cli, socket_path=socket_path, network_magic=network_magic,
                    address=address, destination=boundary_dir / "included-utxo.json",
                    transaction_id=built["transaction_id"], initial_tip=pre_tip,
                    evidence_path=inclusion_path,
                    target_propagation_gate=(
                        target_propagation_gate
                        if accepted_roles == {"target"}
                        else None
                    ),
                )
                included = True
                _wait_for_target_adoption(
                    log_path=Path(str(target.get("log_path") or "")),
                    expected_tip=_terminal_inclusion_tip(inclusion_path, label="deep-delay boundary"),
                    evidence_path=boundary_dir / "target-adoption.json",
                )
                adopted = True
            except RuntimeError:
                included = False
                adopted = False
        boundary: dict[str, Any] = {
            "case_id": BOUNDARY_CASE,
            **{key: built[key] for key in (
                "depth", "flat_size", "flat_sha256", "script_witness_sha256",
                "embedded_upstream_size", "embedded_upstream_sha256",
                "executable_program_size", "executable_program_sha256",
                "minting_argument_count",
                "body_sha256", "payload_sha256", "transaction_size",
                "fee_transaction_size", "headroom_bytes", "fee", "execution_fee",
                "execution_units", "minimum_coin", "output_coin", "minimum_ada_satisfied",
                "outer_trailing_bytes", "funding_input", "collateral_input",
                "transaction_id", "policy_id", "protocol_limits",
            )},
            "included": included,
            "target_adopted": adopted,
            "upstream_fix_commit": UPSTREAM_FIX_COMMIT,
            "source_vector": "UPLC 1.0.0 Delay^28000(Error)",
            "executable_program": "UPLC 1.0.0 lambda^2(Delay^28000(Error))",
        }
        for role in ("target", "reference"):
            boundary[role] = {
                **responses[role],
                "classification": classifications[role],
                "payload_sha256": built["payload_sha256"],
            }
        records.append(boundary)
        (boundary_dir / "result.json").write_text(
            json.dumps(boundary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        status = assess_deep_delay_stack_safety(records)
        report = {
            "schema_version": 1,
            "case_family": "uplc_deep_delay_stack_safety",
            "source_vector": {
                "upstream_fix_commit": UPSTREAM_FIX_COMMIT,
                "depth": BOUNDARY_DEPTH,
                "embedded_upstream_size": len(
                    encode_upstream_deep_delay_program(BOUNDARY_DEPTH)
                ),
                "embedded_upstream_sha256": hashlib.sha256(
                    encode_upstream_deep_delay_program(BOUNDARY_DEPTH)
                ).hexdigest(),
                "executable_program_size": len(
                    encode_deep_delay_program(BOUNDARY_DEPTH)
                ),
                "executable_program_sha256": hashlib.sha256(
                    encode_deep_delay_program(BOUNDARY_DEPTH)
                ).hexdigest(),
                "minting_argument_count": MINTING_ARGUMENT_COUNT,
            },
            "runtime_identity": identities,
            "records": records,
            "stack_safety_status": status,
            "claim_limits": [
                "This case exercises UPLC FLAT term decoding, not nested PlutusData.",
                "A transport close is not a node crash or validation rejection.",
                "Acceptance without retained inclusion is not ledger adoption or consensus evidence.",
            ],
        }
        (output_dir / "result.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return report
