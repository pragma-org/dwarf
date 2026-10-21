#!/usr/bin/env python3
"""Exercise trailing CBOR inside an output inline-datum's tag-24 bytes.

The mutation is nested inside ``[1, tag(24, bytes)]`` in a modern transaction
output.  It is neither trailing data after the transaction nor a witness datum
mutation covered by the script-integrity hash.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import cbor2


CONTROL_CASE = "inline-datum-control"
BOUNDARY_CASE = "inline-datum-tag24-trailing"
AUDITED_CANONICAL_DATUM = bytes.fromhex("d87980")


def _inline_datum_build_raw_command(
    *, cli: str, funding_input: str, address: str, destination: Path
) -> list[str]:
    """Return the exact cardano-cli command used by the live case builder."""

    return [
        cli,
        "conway",
        "transaction",
        "build-raw",
        "--tx-in",
        funding_input,
        "--tx-out",
        f"{address}+29000000",
        "--tx-out-inline-datum-value",
        '{"constructor":0,"fields":[]}',
        "--fee",
        "1000000",
        "--out-file",
        str(destination),
    ]


def _required_positive_int(parameters: Mapping[str, Any], name: str) -> int:
    value = parameters.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"protocol parameter {name} must be a positive integer")
    return value


def _inline_datum_bytes(output: object) -> bytes:
    if not isinstance(output, dict):
        raise ValueError("inline-datum transaction must use a modern map output")
    datum = output.get(2)
    if not isinstance(datum, list) or len(datum) != 2 or datum[0] != 1:
        raise ValueError("transaction output does not contain an inline datum")
    tagged = datum[1]
    if not isinstance(tagged, cbor2.CBORTag) or tagged.tag != 24:
        raise ValueError("inline datum is not wrapped in CBOR tag 24")
    if not isinstance(tagged.value, bytes) or not tagged.value:
        raise ValueError("inline datum tag 24 does not contain CBOR bytes")
    # Validate that the CLI template contains one complete item, but do not use
    # its representation as the canonical control datum.
    stream = cbor2.CBORDecoder(io.BytesIO(tagged.value))
    stream.decode()
    if stream.fp.read(1):
        raise ValueError("inline datum template already has trailing inner bytes")
    return tagged.value


def build_inline_datum_transaction(
    *,
    template_payload: bytes,
    input_lovelace: int,
    protocol_parameters: Mapping[str, Any],
    trailing_inner_byte: bool,
    sign_body: Callable[[bytes], bytes],
) -> dict[str, Any]:
    """Build and sign a fee-correct control or nested trailing-byte mutant."""

    if isinstance(input_lovelace, bool) or input_lovelace <= 0:
        raise ValueError("input lovelace must be positive")
    transaction = cbor2.loads(template_payload)
    if not isinstance(transaction, list) or len(transaction) != 4:
        raise ValueError("transaction template must be a four-element transaction")
    template_body = transaction[0]
    if not isinstance(template_body, dict):
        raise ValueError("transaction template body must be a map")
    outputs = template_body.get(1)
    if not isinstance(outputs, list) or len(outputs) != 1:
        raise ValueError("inline-datum template must contain one output")

    _inline_datum_bytes(outputs[0])
    canonical_inner = AUDITED_CANONICAL_DATUM
    inner = canonical_inner + (b"\x00" if trailing_inner_byte else b"")
    max_tx_size = _required_positive_int(protocol_parameters, "maxTxSize")
    fee_per_byte = _required_positive_int(protocol_parameters, "txFeePerByte")
    fee_fixed = _required_positive_int(protocol_parameters, "txFeeFixed")
    coins_per_utxo_byte = _required_positive_int(
        protocol_parameters, "utxoCostPerByte"
    )

    fee = 1
    body_raw = b""
    payload = b""
    output_coin = 0
    output_size = 0
    minimum_coin = 0
    for _ in range(12):
        output_coin = input_lovelace - fee
        if output_coin <= 0:
            raise ValueError("funding input cannot satisfy transaction fee")
        output = dict(outputs[0])
        output[1] = output_coin
        output[2] = [1, cbor2.CBORTag(24, inner)]
        body = dict(template_body)
        body[1] = [output]
        body[2] = fee
        body_raw = cbor2.dumps(body)
        payload = sign_body(body_raw)
        minimum_fee = fee_fixed + fee_per_byte * len(payload)
        if minimum_fee == fee:
            output_size = len(cbor2.dumps(output))
            minimum_coin = (160 + output_size) * coins_per_utxo_byte
            break
        fee = minimum_fee
    else:
        raise RuntimeError("minimum-fee calculation did not converge")

    if len(payload) > max_tx_size:
        raise ValueError("inline-datum transaction exceeds maxTxSize")
    if output_coin < minimum_coin:
        raise ValueError("inline-datum output is below minimum ADA")
    # cbor2.loads accepts a first item with leftovers, so record the outer
    # byte accounting explicitly instead of using it as an end-of-input check.
    outer = cbor2.CBORDecoder(io.BytesIO(payload))
    outer.decode()
    transaction_trailing = len(outer.fp.read())

    return {
        "body": body_raw,
        "body_sha256": hashlib.sha256(body_raw).hexdigest(),
        "payload": payload,
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "transaction_size": len(payload),
        "fee": fee,
        "output_coin": output_coin,
        "output_size": output_size,
        "minimum_coin": minimum_coin,
        "minimum_ada_satisfied": output_coin >= minimum_coin,
        "canonical_inner_datum_hex": canonical_inner.hex(),
        "inner_datum_hex": inner.hex(),
        "inner_datum_sha256": hashlib.sha256(inner).hexdigest(),
        "nested_trailing_bytes": len(inner) - len(canonical_inner),
        "transaction_trailing_bytes": transaction_trailing,
        "protocol_limits": {
            "max_tx_size": max_tx_size,
            "fee_per_byte": fee_per_byte,
            "fee_fixed": fee_fixed,
            "coins_per_utxo_byte": coins_per_utxo_byte,
        },
    }


def assess_inline_datum_boundary(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Assess controls and evidence without requiring or assuming divergence."""

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
    roles = [boundary.get(role) or {} for role in ("target", "reference")]
    payloads_match = bool(payload_sha256) and all(
        item.get("payload_sha256") == payload_sha256 for item in roles
    )
    decisive = {"accepted", "decode_reject", "ledger_reject", "phase2_reject"}
    outcomes_conclusive = all(
        item.get("classification") in decisive
        and (
            item.get("classification") != "accepted"
            or item.get("included") is True
        )
        for item in roles
    )
    target_class = str(roles[0].get("classification") or "")
    reference_class = str(roles[1].get("classification") or "")
    divergence = bool(outcomes_conclusive and target_class != reference_class)
    nested_boundary = bool(
        boundary.get("nested_trailing_bytes") == 1
        and boundary.get("transaction_trailing_bytes") == 0
    )
    complete = bool(
        valid_controls
        and funding_isolated
        and payloads_match
        and nested_boundary
        and outcomes_conclusive
    )
    return {
        "valid_controls_complete": valid_controls,
        "funding_inputs_isolated": funding_isolated,
        "mutation_payloads_match": payloads_match,
        "nested_boundary_reached": nested_boundary,
        "outcomes_conclusive": outcomes_conclusive,
        "divergence_established": divergence,
        "complete": complete,
    }


def run_inline_datum_tag24_boundary(
    *,
    runtime: dict[str, Any],
    output_dir: Path,
    target_node: str,
    reference_node: str,
    expected_target_version: str,
    expected_target_source: str,
    expected_reference_version: str,
    expected_reference_source: str,
) -> dict[str, Any]:
    """Fund, construct and submit the exact nested tag-24 transaction family."""

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
        raise RuntimeError("inline-datum boundary requires the installed Docker controller")
    runtime_root = Path(str(runtime.get("runtime_root") or ""))
    if not runtime_root.is_absolute():
        raise RuntimeError("runtime root is not absolute")
    output_dir.mkdir(parents=True, exist_ok=True)
    target = identities["target"]
    reference = identities["reference"]
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
            raise RuntimeError("inline-datum funding address does not match generated substrate")

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
        for name in ("maxTxSize", "txFeePerByte", "txFeeFixed", "utxoCostPerByte"):
            _required_positive_int(pparams, name)

        funding_before = _query_utxo(
            cli=cli,
            socket_path=socket_path,
            network_magic=network_magic,
            address=address,
            destination=output_dir / "funding-before.json",
        )
        fresh_input, _ = _select_funding_input(
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
        _setup_envelope, _setup_payload = _envelope(setup_tx)
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
                raise RuntimeError("inline-datum funding output does not match")
        funding_tip = _terminal_inclusion_tip(
            output_dir / "funding-inclusion.json", label="inline-datum funding"
        )
        _wait_for_target_adoption(
            log_path=Path(str(target.get("log_path") or "")),
            expected_tip=funding_tip,
            evidence_path=output_dir / "funding-target-adoption.json",
        )

        def build_case(
            case_dir: Path, funding_input: str, *, trailing_inner_byte: bool
        ) -> dict[str, Any]:
            case_dir.mkdir(parents=True, exist_ok=True)
            raw_path = case_dir / "template.raw"
            _checked(
                _inline_datum_build_raw_command(
                    cli=cli,
                    funding_input=funding_input,
                    address=address,
                    destination=raw_path,
                )
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
            split_transaction(template_payload)
            built = build_inline_datum_transaction(
                template_payload=template_payload,
                input_lovelace=30_000_000,
                protocol_parameters=pparams,
                trailing_inner_byte=trailing_inner_byte,
                sign_body=lambda body: resign_transaction_body(
                    template_payload,
                    body,
                    [controller_key_root / "utxo.skey"],
                ),
            )
            final_path = case_dir / "transaction.signed"
            _write_envelope(final_path, envelope, built["payload"])
            transaction_id = hashlib.blake2b(
                built["body"], digest_size=32
            ).hexdigest()
            return {
                **{
                    key: value
                    for key, value in built.items()
                    if key not in {"body", "payload"}
                },
                "payload": built["payload"],
                "transaction_id": transaction_id,
                "funding_input": funding_input,
            }

        records: list[dict[str, Any]] = []
        control_record: dict[str, Any] = {"case_id": CONTROL_CASE}
        for role, funding_input in zip(("target", "reference"), funding_inputs[:2]):
            role_dir = output_dir / CONTROL_CASE / role
            built = build_case(role_dir, funding_input, trailing_inner_byte=False)
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
                        target_propagation_gate=(
                            target_propagation_gate if role == "target" else None
                        ),
                    )
                    included = True
                except RuntimeError:
                    included = False
                if included:
                    try:
                        _wait_for_target_adoption(
                            log_path=Path(str(target.get("log_path") or "")),
                            expected_tip=_terminal_inclusion_tip(
                                inclusion_path,
                                label=f"inline-datum {role} control",
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
                "inner_datum_sha256": built["inner_datum_sha256"],
            }
            control_record[role] = role_result
            (role_dir / "result.json").write_text(
                json.dumps(role_result, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        records.append(control_record)
        if not all(
            control_record[role]["classification"] == "accepted"
            and control_record[role]["included"] is True
            and control_record[role]["target_adopted"] is True
            for role in ("target", "reference")
        ):
            raise RuntimeError(
                "inline-datum controls are incomplete; boundary case was not submitted"
            )

        boundary_dir = output_dir / BOUNDARY_CASE
        built = build_case(
            boundary_dir, funding_inputs[2], trailing_inner_byte=True
        )
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
            inclusion_path = boundary_dir / "inclusion.json"
            try:
                _wait_for_reference_inclusion(
                    cli=cli,
                    socket_path=socket_path,
                    network_magic=network_magic,
                    address=address,
                    destination=boundary_dir / "included-utxo.json",
                    transaction_id=built["transaction_id"],
                    initial_tip=pre_submit_tip,
                    evidence_path=inclusion_path,
                    target_propagation_gate=(
                        target_propagation_gate
                        if classifications.get("target") == "accepted"
                        else None
                    ),
                )
                included = True
            except RuntimeError:
                included = False
            if included:
                try:
                    _wait_for_target_adoption(
                        log_path=Path(str(target.get("log_path") or "")),
                        expected_tip=_terminal_inclusion_tip(
                            inclusion_path, label="inline-datum boundary"
                        ),
                        evidence_path=boundary_dir / "target-adoption.json",
                    )
                    target_adopted = True
                except RuntimeError:
                    target_adopted = False
        boundary_record: dict[str, Any] = {
            "case_id": BOUNDARY_CASE,
            "funding_input": funding_inputs[2],
            "payload_sha256": built["payload_sha256"],
            "body_sha256": built["body_sha256"],
            "transaction_id": built["transaction_id"],
            "transaction_size": built["transaction_size"],
            "fee": built["fee"],
            "output_coin": built["output_coin"],
            "output_size": built["output_size"],
            "minimum_coin": built["minimum_coin"],
            "minimum_ada_satisfied": built["minimum_ada_satisfied"],
            "canonical_inner_datum_hex": built["canonical_inner_datum_hex"],
            "inner_datum_hex": built["inner_datum_hex"],
            "inner_datum_sha256": built["inner_datum_sha256"],
            "nested_trailing_bytes": built["nested_trailing_bytes"],
            "transaction_trailing_bytes": built["transaction_trailing_bytes"],
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

    status = assess_inline_datum_boundary(records)
    return {
        "schema_version": 1,
        "evidence_scope": "inline_datum_nested_tag24_end_of_input",
        "target_identity": target,
        "reference_identity": reference,
        "network_magic": network_magic,
        "protocol_parameters_sha256": hashlib.sha256(
            pparams_path.read_bytes()
        ).hexdigest(),
        "records": records,
        "boundary_status": status,
        "limitations": [
            "The mutation is one trailing byte inside the tag-24 inline-datum bytes, not after the transaction.",
            "A submit acceptance is not inclusion unless retained inclusion evidence is true.",
            "A transport close is unavailable evidence, not a rejection or crash.",
            "This output-only case does not exercise script execution or script-integrity hashing.",
        ],
    }
