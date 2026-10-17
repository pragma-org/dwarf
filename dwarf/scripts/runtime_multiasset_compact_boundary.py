#!/usr/bin/env python3
"""Construct the smallest live transaction family around Cardano's compact-value bound.

Cardano's ``CompactValue`` decoder conservatively requires
``44 * asset_count + 28 * policy_count <= 65535``.  A one-policy control with
1,488 assets is the last accepted cardinality; 1,489 assets crosses the bound.
The asset names below minimize the actual CBOR footprint so the experiment
tests that compact-storage boundary rather than ``maxValueSize`` or
``maxTxSize``.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import cbor2


CONTROL_ASSET_COUNT = 1_488
MUTANT_ASSET_COUNT = 1_489
COMPACT_OFFSET_LIMIT = 65_535
CONTROL_CASE = "compact-control"
BOUNDARY_CASE = "compact-boundary"


def minimal_asset_names(count: int) -> list[bytes]:
    """Return ``count`` distinct Cardano asset names in shortest-length order."""

    if isinstance(count, bool) or count < 1:
        raise ValueError("asset count must be positive")
    names = [b""]
    for width in range(1, 33):
        for value in range(1 << (8 * width)):
            names.append(value.to_bytes(width, "big"))
            if len(names) == count:
                return names
    raise ValueError("asset count exceeds the 32-byte name space")


def compact_representation_upper_bound(asset_count: int, policy_count: int) -> int:
    """Mirror cardano-ledger's conservative compact-offset guard."""

    if min(asset_count, policy_count) < 0:
        raise ValueError("compact representation counts cannot be negative")
    return 44 * asset_count + 28 * policy_count


def _ledger_map_envelope_size(entry_count: int) -> int:
    # cardano-ledger writes maps through 23 entries with a definite header and
    # larger maps with an indefinite header plus break byte.
    if entry_count <= 23:
        return len(cbor2.dumps({index: 0 for index in range(entry_count)})) - 2 * entry_count
    return 2


def _ledger_multiasset_size(policy_id: bytes, assets: Mapping[bytes, int]) -> int:
    inner = _ledger_map_envelope_size(len(assets)) + sum(
        len(cbor2.dumps(name)) + len(cbor2.dumps(quantity))
        for name, quantity in assets.items()
    )
    return 1 + len(cbor2.dumps(policy_id)) + inner


def _ledger_value_size(coin: int, policy_id: bytes, assets: Mapping[bytes, int]) -> int:
    return 1 + len(cbor2.dumps(coin)) + _ledger_multiasset_size(policy_id, assets)


def _output_like(template: object, address: bytes, value: object) -> object:
    if isinstance(template, list):
        return [address, value]
    if isinstance(template, dict):
        output = dict(template)
        output[0] = address
        output[1] = value
        return output
    raise ValueError("transaction template has an unsupported output shape")


def _minimum_coin_output(
    *, template: object, address: bytes, policy_id: bytes,
    assets: Mapping[bytes, int], coins_per_utxo_byte: int,
) -> tuple[object, int]:
    coin = 1
    for _ in range(12):
        output = _output_like(template, address, [coin, {policy_id: dict(assets)}])
        minimum = (160 + len(cbor2.dumps(output))) * coins_per_utxo_byte
        if minimum == coin:
            return output, coin
        coin = minimum
    raise RuntimeError("minimum-ADA calculation did not converge")


def _require_parameter(parameters: Mapping[str, Any], name: str) -> int:
    value = parameters.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"protocol parameter {name} must be a positive integer")
    return value


def build_boundary_transaction(
    *, template_payload: bytes, address: bytes, policy_id: bytes,
    input_lovelace: int, asset_count: int,
    protocol_parameters: Mapping[str, Any],
    sign_body: Callable[[bytes], bytes],
) -> dict[str, Any]:
    """Materialize one fee-correct, value-conserving compact-boundary transaction."""

    if len(policy_id) != 28:
        raise ValueError("policy id must be 28 bytes")
    if isinstance(input_lovelace, bool) or input_lovelace <= 0:
        raise ValueError("input lovelace must be positive")
    transaction = cbor2.loads(template_payload)
    if not isinstance(transaction, list) or len(transaction) != 4:
        raise ValueError("transaction template must be a four-element transaction")
    template_body = transaction[0]
    if not isinstance(template_body, dict):
        raise ValueError("transaction template body must be a map")
    outputs = template_body.get(1)
    if not isinstance(outputs, list) or not outputs:
        raise ValueError("transaction template must contain an output")

    max_value_size = _require_parameter(protocol_parameters, "maxValueSize")
    max_tx_size = _require_parameter(protocol_parameters, "maxTxSize")
    fee_per_byte = _require_parameter(protocol_parameters, "txFeePerByte")
    fee_fixed = _require_parameter(protocol_parameters, "txFeeFixed")
    coins_per_utxo_byte = _require_parameter(protocol_parameters, "utxoCostPerByte")

    names = minimal_asset_names(asset_count)
    split = asset_count // 2
    groups = [names[:split], names[split:]]
    asset_groups = [{name: 1 for name in group} for group in groups]
    first_output, first_coin = _minimum_coin_output(
        template=outputs[0], address=address, policy_id=policy_id,
        assets=asset_groups[0], coins_per_utxo_byte=coins_per_utxo_byte,
    )
    second_output = _output_like(
        outputs[0], address, [1, {policy_id: asset_groups[1]}]
    )

    fee = 1
    payload = b""
    body_raw = b""
    for _ in range(12):
        second_coin = input_lovelace - first_coin - fee
        if second_coin <= 0:
            raise ValueError("funding input cannot satisfy output and fee requirements")
        if isinstance(second_output, list):
            second_output[1][0] = second_coin
        else:
            second_output[1][0] = second_coin
        body = dict(template_body)
        body[1] = [first_output, second_output]
        body[2] = fee
        body[9] = {policy_id: {name: 1 for name in names}}
        body_raw = cbor2.dumps(body)
        payload = sign_body(body_raw)
        minimum_fee = fee_fixed + fee_per_byte * len(payload)
        if minimum_fee == fee:
            break
        fee = minimum_fee
    else:
        raise RuntimeError("minimum-fee calculation did not converge")

    decoded_body = cbor2.loads(body_raw)
    final_outputs = decoded_body[1]
    raw_output_sizes = [len(cbor2.dumps(output)) for output in final_outputs]
    output_coins = [int(output[1][0]) for output in final_outputs]
    minimum_coins = [
        (160 + size) * coins_per_utxo_byte for size in raw_output_sizes
    ]
    ledger_value_sizes = [
        _ledger_value_size(output[1][0], policy_id, output[1][1][policy_id])
        for output in final_outputs
    ]
    compact_size = compact_representation_upper_bound(asset_count, 1)
    if len(payload) > max_tx_size:
        raise ValueError("compact-boundary transaction exceeds maxTxSize")
    if max(ledger_value_sizes) > max_value_size:
        raise ValueError("compact-boundary output exceeds maxValueSize")
    if any(given < required for given, required in zip(output_coins, minimum_coins)):
        raise ValueError("compact-boundary output is below minimum ADA")

    return {
        "asset_count": asset_count,
        "output_asset_counts": [len(group) for group in groups],
        "compact_representation_upper_bound": compact_size,
        "compact_boundary_crossed": compact_size > COMPACT_OFFSET_LIMIT,
        "body": body_raw,
        "body_sha256": hashlib.sha256(body_raw).hexdigest(),
        "payload": payload,
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "transaction_size": len(payload),
        "fee": fee,
        "ledger_value_sizes": ledger_value_sizes,
        "raw_output_sizes": raw_output_sizes,
        "output_coins": output_coins,
        "minimum_coins": minimum_coins,
        "minimum_ada_satisfied": all(
            given >= required for given, required in zip(output_coins, minimum_coins)
        ),
        "protocol_limits": {
            "max_value_size": max_value_size,
            "max_tx_size": max_tx_size,
            "fee_per_byte": fee_per_byte,
            "fee_fixed": fee_fixed,
            "coins_per_utxo_byte": coins_per_utxo_byte,
        },
    }


def assess_compact_boundary(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize evidence without making success depend on a divergence."""

    by_case = {str(record.get("case_id") or ""): record for record in records}
    control = by_case.get(CONTROL_CASE) or {}
    boundary = by_case.get(BOUNDARY_CASE) or {}

    def accepted_and_included(role: str) -> bool:
        item = control.get(role) or {}
        return bool(
            item.get("classification") == "accepted"
            and item.get("included") is True
            and item.get("target_adopted") is True
            and item.get("transaction_id")
        )

    valid_controls = all(
        accepted_and_included(role) for role in ("target", "reference")
    )
    funding_inputs = [
        str((control.get(role) or {}).get("funding_input") or "")
        for role in ("target", "reference")
    ] + [str(boundary.get("funding_input") or "")]
    funding_isolated = bool(
        all(funding_inputs) and len(funding_inputs) == len(set(funding_inputs))
    )
    payload_sha256 = str(boundary.get("payload_sha256") or "")
    mutant_roles = [boundary.get(role) or {} for role in ("target", "reference")]
    payloads_match = bool(payload_sha256) and all(
        item.get("payload_sha256") == payload_sha256 for item in mutant_roles
    )
    decisive = {"accepted", "decode_reject", "ledger_reject", "phase2_reject"}
    outcomes_conclusive = all(
        item.get("classification") in decisive
        and (
            item.get("classification") != "accepted"
            or item.get("included") is True
        )
        for item in mutant_roles
    )
    target_class = str(mutant_roles[0].get("classification") or "")
    reference_class = str(mutant_roles[1].get("classification") or "")
    divergence = bool(
        outcomes_conclusive
        and target_class != reference_class
        and {target_class, reference_class} != {"ledger_reject", "phase2_reject"}
    )
    complete = bool(
        valid_controls and funding_isolated and payloads_match and outcomes_conclusive
    )
    return {
        "valid_controls_complete": valid_controls,
        "funding_inputs_isolated": funding_isolated,
        "mutation_payloads_match": payloads_match,
        "outcomes_conclusive": outcomes_conclusive,
        "compact_boundary_reached": bool(
            boundary.get("compact_boundary_crossed") is True
        ),
        "divergence_established": divergence,
        "complete": complete,
    }


def run_multiasset_compact_boundary(
    *, runtime: dict[str, Any], output_dir: Path,
    target_node: str, reference_node: str,
    expected_target_version: str, expected_target_source: str,
    expected_reference_version: str, expected_reference_source: str,
) -> dict[str, Any]:
    """Fund, construct, and submit the exact compact-value boundary family."""

    from profile_manager.submit_primitives import _validation_stage
    from scripts.runtime_cardano_measurement_calibration import (
        _checked,
        _containerized_cardano_workload,
        _controller_container,
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
        raise RuntimeError("multiasset boundary requires the installed Docker controller")
    runtime_root = Path(str(runtime.get("runtime_root") or ""))
    if not runtime_root.is_absolute():
        raise RuntimeError("runtime root is not absolute")
    output_dir.mkdir(parents=True, exist_ok=True)
    target = identities["target"]
    reference = identities["reference"]
    network_magic = int(runtime["network_magic"])
    socket_path = str(reference.get("container_socket_path") or "")
    helper_image = str(os.environ.get("DWARF_LOCALTXSUBMISSION_HELPER_IMAGE") or "")

    with _containerized_cardano_workload(
        runtime_root=runtime_root,
        runtime=runtime,
        node=reference,
        output_dir=output_dir,
        controller=controller,
    ) as (workload_runtime, _workload_node):
        cli = str(workload_runtime["support_binaries"]["cardano-cli"])
        key_root = Path(str(workload_runtime["workload_key_root"]))
        controller_key_root = Path(
            str(workload_runtime["controller_workload_key_root"])
        )
        address = _checked(
            [
                cli,
                "latest",
                "genesis",
                "initial-addr",
                "--verification-key-file",
                str(key_root / "utxo.vkey"),
                "--testnet-magic",
                str(network_magic),
            ]
        ).stdout.strip()
        if (runtime.get("generated_workload_funding") or {}).get("address") != address:
            raise RuntimeError("multiasset funding address does not match generated substrate")

        pparams_path = output_dir / "protocol-parameters.json"
        _checked(
            [
                cli,
                "query",
                "protocol-parameters",
                "--socket-path",
                socket_path,
                "--testnet-magic",
                str(network_magic),
                "--out-file",
                str(pparams_path),
            ]
        )
        pparams = json.loads(pparams_path.read_text(encoding="utf-8"))
        required = (
            "maxValueSize",
            "maxTxSize",
            "txFeePerByte",
            "txFeeFixed",
            "utxoCostPerByte",
        )
        if any(
            isinstance(pparams.get(name), bool)
            or not isinstance(pparams.get(name), int)
            or int(pparams[name]) <= 0
            for name in required
        ):
            raise RuntimeError("live protocol parameters omit compact-boundary limits")

        funding_before = _query_utxo(
            cli=cli,
            socket_path=socket_path,
            network_magic=network_magic,
            address=address,
            destination=output_dir / "funding-before.json",
        )
        fresh_input, _selected_lovelace = _select_funding_input(
            funding_before, minimum_lovelace=92_000_000
        )
        setup_body = output_dir / "funding-setup.body"
        setup_command = [
            cli,
            "conway",
            "transaction",
            "build",
            "--socket-path",
            socket_path,
            "--testnet-magic",
            str(network_magic),
            "--tx-in",
            fresh_input,
            "--change-address",
            address,
        ]
        for _ in range(3):
            setup_command.extend(["--tx-out", f"{address}+30000000"])
        setup_command.extend(["--out-file", str(setup_body)])
        _checked(setup_command)
        setup_tx = output_dir / "funding-setup.tx"
        _checked(
            [
                cli,
                "conway",
                "transaction",
                "sign",
                "--tx-body-file",
                str(setup_body),
                "--signing-key-file",
                str(key_root / "utxo.skey"),
                "--testnet-magic",
                str(network_magic),
                "--out-file",
                str(setup_tx),
            ]
        )
        _setup_envelope, setup_payload = _envelope(setup_tx)
        setup_id = _transaction_id(cli, setup_tx)
        funding_tip_before = _query_reference_tip(
            cli=cli, socket_path=socket_path, network_magic=network_magic
        )
        _checked(
            [
                cli,
                "conway",
                "transaction",
                "submit",
                "--tx-file",
                str(setup_tx),
                "--socket-path",
                socket_path,
                "--testnet-magic",
                str(network_magic),
            ]
        )
        funding_ready = _wait_for_reference_inclusion(
            cli=cli,
            socket_path=socket_path,
            network_magic=network_magic,
            address=address,
            destination=output_dir / "funding-ready.json",
            transaction_id=setup_id,
            initial_tip=funding_tip_before,
            evidence_path=output_dir / "funding-inclusion.json",
        )
        funding_inputs = [f"{setup_id}#{index}" for index in range(3)]
        for reference_id in funding_inputs:
            value = (funding_ready.get(reference_id) or {}).get("value") or {}
            if value.get("lovelace") != 30_000_000:
                raise RuntimeError("compact-boundary funding output does not match")
        funding_tip = _terminal_inclusion_tip(
            output_dir / "funding-inclusion.json", label="compact-boundary funding"
        )
        _wait_for_target_adoption(
            log_path=Path(str(target.get("log_path") or "")),
            expected_tip=funding_tip,
            evidence_path=output_dir / "funding-target-adoption.json",
        )

        payment_key_hash = _checked(
            [
                cli,
                "address",
                "key-hash",
                "--payment-verification-key-file",
                str(key_root / "utxo.vkey"),
            ]
        ).stdout.strip()
        if len(payment_key_hash) != 56:
            raise RuntimeError("payment key hash is invalid")
        policy_path = output_dir / "native-policy.json"
        policy_path.write_text(
            json.dumps({"type": "sig", "keyHash": payment_key_hash}, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        policy_hex = _checked(
            [
                cli,
                "conway",
                "transaction",
                "policyid",
                "--script-file",
                str(policy_path),
            ]
        ).stdout.strip()
        try:
            policy_id = bytes.fromhex(policy_hex)
        except ValueError as exc:
            raise RuntimeError("native policy id is invalid") from exc
        if len(policy_id) != 28:
            raise RuntimeError("native policy id is invalid")

        def build_case(case_dir: Path, funding_input: str, asset_count: int) -> dict[str, Any]:
            case_dir.mkdir(parents=True, exist_ok=True)
            raw_path = case_dir / "template.raw"
            _checked(
                [
                    cli,
                    "conway",
                    "transaction",
                    "build-raw",
                    "--tx-in",
                    funding_input,
                    "--tx-out",
                    f"{address}+29000000+1 {policy_hex}.00",
                    "--fee",
                    "1000000",
                    "--mint",
                    f"1 {policy_hex}.00",
                    "--mint-script-file",
                    str(policy_path),
                    "--out-file",
                    str(raw_path),
                ]
            )
            signed_template_path = case_dir / "template.signed"
            _checked(
                [
                    cli,
                    "conway",
                    "transaction",
                    "sign",
                    "--tx-body-file",
                    str(raw_path),
                    "--signing-key-file",
                    str(key_root / "utxo.skey"),
                    "--testnet-magic",
                    str(network_magic),
                    "--out-file",
                    str(signed_template_path),
                ]
            )
            envelope, template_payload = _envelope(signed_template_path)
            template_body, _witnesses, _tail = split_transaction(template_payload)
            decoded = cbor2.loads(template_body)
            output_address = decoded[1][0][0]
            if not isinstance(output_address, bytes):
                raise RuntimeError("transaction template output address is not bytes")
            built = build_boundary_transaction(
                template_payload=template_payload,
                address=output_address,
                policy_id=policy_id,
                input_lovelace=30_000_000,
                asset_count=asset_count,
                protocol_parameters=pparams,
                sign_body=lambda body: resign_transaction_body(
                    template_payload,
                    body,
                    [controller_key_root / "utxo.skey"],
                ),
            )
            final_path = case_dir / "transaction.signed"
            _write_envelope(final_path, envelope, built["payload"])
            # Do not ask cardano-cli to decode the final transaction: the
            # 1,489-entry case deliberately crosses the reference decoder's
            # CompactValue guard.  A Cardano transaction id is the blake2b-256
            # digest of the exact transaction-body bytes we just signed.
            transaction_id = hashlib.blake2b(
                built["body"], digest_size=32
            ).hexdigest()
            return {
                **{key: value for key, value in built.items() if key not in {"body", "payload"}},
                "payload": built["payload"],
                "transaction_id": transaction_id,
                "funding_input": funding_input,
            }

        records: list[dict[str, Any]] = []
        control_record: dict[str, Any] = {"case_id": CONTROL_CASE}
        for role, funding_input in zip(("target", "reference"), funding_inputs[:2]):
            role_dir = output_dir / CONTROL_CASE / role
            built = build_case(role_dir, funding_input, CONTROL_ASSET_COUNT)
            pre_submit_tip = _query_reference_tip(
                cli=cli, socket_path=socket_path, network_magic=network_magic
            )
            response = (
                _submit_amaru_container(str(target["container_id"]), built["payload"])
                if role == "target"
                else _submit_reference_raw(
                    runtime=runtime,
                    reference=reference,
                    payload=built["payload"],
                    helper_image=helper_image,
                )
            )
            classification = _validation_stage(response)
            included = False
            target_adopted = False
            if classification == "accepted":
                inclusion_path = role_dir / "inclusion.json"
                try:
                    _wait_for_reference_inclusion(
                        cli=cli,
                        socket_path=socket_path,
                        network_magic=network_magic,
                        address=address,
                        destination=role_dir / "included-utxo.json",
                        transaction_id=built["transaction_id"],
                        initial_tip=pre_submit_tip,
                        evidence_path=inclusion_path,
                    )
                    included = True
                except RuntimeError:
                    included = False
                if included:
                    try:
                        _wait_for_target_adoption(
                            log_path=Path(str(target.get("log_path") or "")),
                            expected_tip=_terminal_inclusion_tip(
                                inclusion_path, label=f"compact boundary {role} control"
                            ),
                            evidence_path=role_dir / "target-adoption.json",
                        )
                        target_adopted = True
                    except RuntimeError:
                        target_adopted = False
            role_result = {
                **response,
                "classification": classification,
                "included": included,
                "target_adopted": target_adopted,
                "funding_input": funding_input,
                "payload_sha256": built["payload_sha256"],
                "body_sha256": built["body_sha256"],
                "transaction_id": built["transaction_id"],
                "asset_count": built["asset_count"],
                "output_asset_counts": built["output_asset_counts"],
                "compact_representation_upper_bound": built[
                    "compact_representation_upper_bound"
                ],
                "compact_boundary_crossed": built["compact_boundary_crossed"],
                "transaction_size": built["transaction_size"],
                "fee": built["fee"],
                "ledger_value_sizes": built["ledger_value_sizes"],
                "raw_output_sizes": built["raw_output_sizes"],
                "minimum_coins": built["minimum_coins"],
                "output_coins": built["output_coins"],
            }
            control_record[role] = role_result
            (role_dir / "result.json").write_text(
                json.dumps(role_result, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        records.append(control_record)
        (output_dir / CONTROL_CASE / "result.json").write_text(
            json.dumps(control_record, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if not all(
            control_record[role]["classification"] == "accepted"
            and control_record[role]["included"] is True
            and control_record[role]["target_adopted"] is True
            for role in ("target", "reference")
        ):
            raise RuntimeError(
                "compact-boundary controls are incomplete; boundary case was not submitted"
            )

        boundary_dir = output_dir / BOUNDARY_CASE
        built = build_case(boundary_dir, funding_inputs[2], MUTANT_ASSET_COUNT)
        pre_submit_tip = _query_reference_tip(
            cli=cli, socket_path=socket_path, network_magic=network_magic
        )
        responses = {
            "reference": _submit_reference_raw(
                runtime=runtime,
                reference=reference,
                payload=built["payload"],
                helper_image=helper_image,
            ),
            "target": _submit_amaru_container(
                str(target["container_id"]), built["payload"]
            ),
        }
        classifications = {
            role: _validation_stage(response) for role, response in responses.items()
        }
        included = False
        target_adopted = False
        if "accepted" in classifications.values():
            try:
                inclusion_path = boundary_dir / "inclusion.json"
                _wait_for_reference_inclusion(
                    cli=cli,
                    socket_path=socket_path,
                    network_magic=network_magic,
                    address=address,
                    destination=boundary_dir / "included-utxo.json",
                    transaction_id=built["transaction_id"],
                    initial_tip=pre_submit_tip,
                    evidence_path=inclusion_path,
                )
                included = True
            except RuntimeError:
                included = False
            if included:
                try:
                    _wait_for_target_adoption(
                        log_path=Path(str(target.get("log_path") or "")),
                        expected_tip=_terminal_inclusion_tip(
                            inclusion_path, label="compact boundary mutant"
                        ),
                        evidence_path=boundary_dir / "target-adoption.json",
                    )
                    target_adopted = True
                except RuntimeError:
                    target_adopted = False
        boundary_record: dict[str, Any] = {
            "case_id": BOUNDARY_CASE,
            "funding_input": funding_inputs[2],
            "asset_count": built["asset_count"],
            "output_asset_counts": built["output_asset_counts"],
            "compact_representation_upper_bound": built[
                "compact_representation_upper_bound"
            ],
            "compact_boundary_crossed": built["compact_boundary_crossed"],
            "transaction_size": built["transaction_size"],
            "fee": built["fee"],
            "ledger_value_sizes": built["ledger_value_sizes"],
            "raw_output_sizes": built["raw_output_sizes"],
            "minimum_coins": built["minimum_coins"],
            "output_coins": built["output_coins"],
            "payload_sha256": built["payload_sha256"],
            "body_sha256": built["body_sha256"],
            "transaction_id": built["transaction_id"],
        }
        for role in ("target", "reference"):
            boundary_record[role] = {
                **responses[role],
                "classification": classifications[role],
                "included": included,
                "target_adopted": target_adopted,
                "payload_sha256": built["payload_sha256"],
            }
        records.append(boundary_record)
        (boundary_dir / "result.json").write_text(
            json.dumps(boundary_record, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    status = assess_compact_boundary(records)
    return {
        "schema_version": 1,
        "evidence_scope": "multiasset_compact_storage_boundary",
        "target_identity": target,
        "reference_identity": reference,
        "network_magic": network_magic,
        "policy": {
            "type": "native_signature",
            "policy_id": policy_hex,
            "script_sha256": hashlib.sha256(policy_path.read_bytes()).hexdigest(),
        },
        "protocol_parameters_sha256": hashlib.sha256(
            pparams_path.read_bytes()
        ).hexdigest(),
        "boundary_formula": "44 * asset_count + 28 * policy_count <= 65535",
        "records": records,
        "boundary_status": status,
        "limitations": [
            "A submit acceptance is not inclusion unless retained inclusion evidence is true.",
            "A transport close is unavailable evidence, not a rejection or crash.",
            "The case tests compact MultiAsset decoding, not maxValueSize or maxTxSize.",
        ],
    }
