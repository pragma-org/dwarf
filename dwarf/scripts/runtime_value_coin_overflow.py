#!/usr/bin/env python3
"""Fresh-funded Amaru regression for output coin values around ``i64::MAX``."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable

import cbor2


I64_MAX = (1 << 63) - 1
I64_OVERFLOW = 1 << 63
VALID_CONTROL = "valid-control"
BOUNDARY_CONTROL = "i64-max-control"
OVERFLOW_MUTANT = "i64-max-plus-one"
TEMPLATE_FEE = 1_000_000

_VALUE_REJECTION_MARKERS = (
    "valuenotconservedutxo",
    "value not conserved",
    "value not preserved",
)
_DECODE_REJECTION_MARKERS = (
    "decodererror",
    "deserialisefailure",
    "invalid cbor",
    "txcmdtxreaderror",
    "decode error",
    "expected type",
    "expected/found mismatch",
)
_MASKED_REJECTION_MARKERS = (
    "badinputsutxo",
    "bad inputs",
    "already spent",
    "unknown input",
    "feetoosmallutxo",
    "minimum fee",
    "fee too small",
)


def build_raw_template_command(
    *,
    cli: str,
    funding_input: str,
    address: str,
    funding_lovelace: int,
    output_path: str | Path,
) -> list[str]:
    """Build a valid one-output template with fee headroom independent of size."""

    output_lovelace = funding_lovelace - TEMPLATE_FEE
    if output_lovelace <= 0:
        raise ValueError("funding input does not cover the fixed template fee")
    return [
        cli,
        "conway",
        "transaction",
        "build-raw",
        "--tx-in",
        funding_input,
        "--tx-out",
        f"{address}+{output_lovelace}",
        "--fee",
        str(TEMPLATE_FEE),
        "--out-file",
        str(output_path),
    ]


def _value_rejection_marker(response: dict[str, Any]) -> str | None:
    body = str(response.get("body") or "").lower()
    return next((marker for marker in _VALUE_REJECTION_MARKERS if marker in body), None)


def classify_value_coin_response(response: dict[str, Any]) -> str:
    """Classify only the expected value-conservation path as a clean reject."""

    status = int(response.get("http") or 0)
    body = str(response.get("body") or "").lower()
    if 200 <= status < 300:
        return "accepted"
    if not 400 <= status < 500:
        return "unavailable"
    if any(marker in body for marker in _DECODE_REJECTION_MARKERS):
        return "decode_reject"
    if status == 409 or any(marker in body for marker in _MASKED_REJECTION_MARKERS):
        return "inconclusive_reject"
    if _value_rejection_marker(response) is not None:
        return "value_reject"
    return "inconclusive_reject"


def build_value_coin_case(
    *,
    template_payload: bytes,
    output_coin: int,
    sign_body: Callable[[bytes], bytes],
) -> dict[str, Any]:
    """Set one pure-ADA output coin exactly and sign the resulting body."""

    if output_coin not in {I64_MAX, I64_OVERFLOW}:
        raise ValueError("output coin must be exactly i64::MAX or i64::MAX + 1")
    transaction = cbor2.loads(template_payload)
    if not isinstance(transaction, list) or len(transaction) != 4:
        raise ValueError("template must be a four-element transaction")
    body = transaction[0]
    if not isinstance(body, dict):
        raise ValueError("transaction body must be a map")
    outputs = body.get(1)
    if not isinstance(outputs, list) or not outputs:
        raise ValueError("transaction body must contain an output")
    output = outputs[0]
    if isinstance(output, list) and len(output) >= 2:
        output[1] = output_coin
    elif isinstance(output, dict) and 1 in output:
        output[1] = output_coin
    else:
        raise ValueError("transaction output shape is unsupported")
    mutated_body = cbor2.dumps(body)
    payload = sign_body(mutated_body)
    signed = cbor2.loads(payload)
    if not isinstance(signed, list) or signed[0] != body:
        raise RuntimeError("signed transaction does not retain the mutated body")
    return {
        "body": mutated_body,
        "payload": payload,
        "body_sha256": hashlib.sha256(mutated_body).hexdigest(),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "transaction_id": hashlib.blake2b(mutated_body, digest_size=32).hexdigest(),
        "output_coin": output_coin,
        "output_coin_boundary": (
            "i64-max" if output_coin == I64_MAX else "i64-max-plus-one"
        ),
    }


def boundary_control_allows_mutant(record: dict[str, Any]) -> bool:
    """Fail closed unless the exact boundary rejected and the target survived."""

    return bool(
        record.get("case_id") == BOUNDARY_CONTROL
        and record.get("output_coin") == I64_MAX
        and record.get("funding_input_resolvable") is True
        and record.get("classification") == "value_reject"
        and record.get("value_rejection_marker") in _VALUE_REJECTION_MARKERS
        and record.get("target_running_after") is True
    )


def assess_value_coin_overflow(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Distinguish a clean regression pass from a retained security finding."""

    by_case = {str(item.get("case_id") or ""): item for item in records}
    valid = by_case.get(VALID_CONTROL) or {}
    boundary = by_case.get(BOUNDARY_CONTROL) or {}
    mutant = by_case.get(OVERFLOW_MUTANT) or {}
    post_probe = mutant.get("post_mutant_boundary_probe") or {}
    expected_cases = {VALID_CONTROL, BOUNDARY_CONTROL, OVERFLOW_MUTANT}
    inputs = [str((by_case.get(case) or {}).get("funding_input") or "") for case in expected_cases]
    isolated = bool(all(inputs) and len(inputs) == len(set(inputs)))
    inputs_resolvable = all(
        (by_case.get(case) or {}).get("funding_input_resolvable") is True
        for case in expected_cases
    )
    post_probe_identity = bool(
        post_probe.get("transaction_id")
        and post_probe.get("transaction_id") == boundary.get("transaction_id")
        and post_probe.get("payload_sha256")
        and post_probe.get("payload_sha256") == boundary.get("payload_sha256")
    )
    post_probe_observed = bool(
        post_probe_identity
        and post_probe.get("classification")
        in {
            "accepted",
            "value_reject",
            "decode_reject",
            "inconclusive_reject",
            "unavailable",
        }
        and isinstance(post_probe.get("target_running_after"), bool)
    )
    post_probe_healthy = bool(
        post_probe_observed
        and post_probe.get("classification") == "value_reject"
        and post_probe.get("value_rejection_marker") in _VALUE_REJECTION_MARKERS
        and post_probe.get("target_running_after") is True
    )
    valid_control = bool(
        valid.get("classification") == "accepted"
        and valid.get("included") is True
        and valid.get("target_adopted") is True
        and valid.get("target_running_after") is True
    )
    boundary_control = boundary_control_allows_mutant(boundary)
    mutant_observed = bool(
        mutant.get("output_coin") == I64_OVERFLOW
        and mutant.get("classification") in {"accepted", "value_reject", "unavailable"}
        and isinstance(mutant.get("target_running_after"), bool)
    )
    regression_passed = bool(
        valid_control
        and boundary_control
        and isolated
        and inputs_resolvable
        and mutant.get("classification") == "value_reject"
        and mutant.get("value_rejection_marker") in _VALUE_REJECTION_MARKERS
        and mutant.get("target_running_after") is True
        and post_probe_healthy
    )
    security_finding = bool(
        valid_control
        and boundary_control
        and isolated
        and inputs_resolvable
        and (
            mutant.get("classification") == "accepted"
            or (
                mutant.get("classification") in {"value_reject", "unavailable"}
                and post_probe_observed
                and (
                    mutant.get("target_running_after") is False
                    or post_probe.get("classification") == "unavailable"
                    or post_probe.get("target_running_after") is not True
                )
            )
        )
    )
    return {
        "valid_control_complete": valid_control,
        "boundary_control_complete": boundary_control,
        "funding_inputs_isolated": isolated,
        "funding_inputs_resolvable": inputs_resolvable,
        "mutant_observed": mutant_observed,
        "post_mutant_boundary_probe_observed": post_probe_observed,
        "post_mutant_boundary_probe_complete": post_probe_healthy,
        "regression_passed": regression_passed,
        "security_finding_observed": security_finding,
        "complete": bool(
            valid_control
            and boundary_control
            and isolated
            and inputs_resolvable
            and mutant_observed
            and post_probe_observed
        ),
    }


def _container_health(container_id: str) -> dict[str, Any]:
    from scripts.runtime_cardano_measurement_calibration import _inspect_container

    inspected = _inspect_container(container_id)
    state = inspected.get("State") or {}
    return {
        "container_id": str(inspected.get("Id") or ""),
        "running": state.get("Running") is True,
        "status": str(state.get("Status") or ""),
        "exit_code": state.get("ExitCode"),
        "oom_killed": state.get("OOMKilled") is True,
    }


def _settled_container_health(
    container_id: str, *, observation_seconds: float = 3.0
) -> dict[str, Any]:
    """Give an asynchronous ledger panic a bounded window to stop the container."""

    deadline = time.monotonic() + observation_seconds
    health = _container_health(container_id)
    while health["running"] and time.monotonic() < deadline:
        time.sleep(0.25)
        health = _container_health(container_id)
    return health


def run_value_coin_overflow_regression(
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
    """Build three isolated transactions and stop before the mutant if a control fails."""

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
    wait_for_target_propagation_eligibility,
        _select_funding_input,
        _submit_amaru_container,
        _wait_for_reference_inclusion,
        _wait_for_target_adoption,
        split_transaction,
        validate_runtime_identity,
    )
    from scripts.runtime_transaction_array_arity import _funding_credentials

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
        raise RuntimeError("value-overflow regression requires the installed Docker controller")
    runtime_root = Path(str(runtime.get("runtime_root") or ""))
    if not runtime_root.is_absolute():
        raise RuntimeError("runtime root is not absolute")
    output_dir.mkdir(parents=True, exist_ok=True)
    target = identities["target"]
    reference = identities["reference"]
    target_container = str(target.get("container_id") or "")
    network_magic = int(runtime["network_magic"])
    socket_path = str(reference.get("container_socket_path") or "")
    credentials = _funding_credentials(runtime, runtime_root)
    propagation_gate = _reference_tx_submission_propagation_gate(
        reference, inspect_container=_inspect_container
    )
    wait_for_target_propagation_eligibility(propagation_gate)
    records: list[dict[str, Any]] = []

    with _containerized_cardano_workload(
        runtime_root=runtime_root,
        runtime=runtime,
        node=reference,
        output_dir=output_dir,
        controller=controller,
    ) as (workload_runtime, _workload_node):
        cli = str(workload_runtime["support_binaries"]["cardano-cli"])

        def build_template(credential: dict[str, Any], case_dir: Path) -> dict[str, Any]:
            case_dir.mkdir(parents=True, exist_ok=True)
            utxos = _query_utxo(
                cli=cli,
                socket_path=socket_path,
                network_magic=network_magic,
                address=credential["address"],
                destination=case_dir / "funding-before.json",
            )
            expected_lovelace = credential.get("lovelace")
            minimum = int(expected_lovelace) if isinstance(expected_lovelace, int) else 10_000_000
            funding_input, funding_lovelace = _select_funding_input(
                utxos, minimum_lovelace=minimum
            )
            body_path = case_dir / "transaction.body"
            _checked(
                build_raw_template_command(
                    cli=cli,
                    funding_input=funding_input,
                    address=credential["address"],
                    funding_lovelace=funding_lovelace,
                    output_path=body_path,
                )
            )
            signed_path = case_dir / "transaction.signed"
            _checked(
                [
                    cli,
                    "conway",
                    "transaction",
                    "sign",
                    "--tx-body-file",
                    str(body_path),
                    "--signing-key-file",
                    credential["container_skey"],
                    "--testnet-magic",
                    str(network_magic),
                    "--out-file",
                    str(signed_path),
                ]
            )
            _envelope_body, payload = _envelope(signed_path)
            raw_body, _witnesses, _tail = split_transaction(payload)
            return {
                "payload": payload,
                "body": raw_body,
                "transaction_id": _transaction_id(cli, signed_path),
                "funding_input": funding_input,
                "funding_lovelace": funding_lovelace,
                "funding_credential": credential["id"],
                "funding_input_resolvable": funding_input in utxos,
                "controller_skey": credential["controller_skey"],
            }

        def submit_record(case_id: str, built: dict[str, Any]) -> dict[str, Any]:
            response = _submit_amaru_container(target_container, built["payload"])
            health = _settled_container_health(target_container)
            if health["container_id"] != target_container:
                raise RuntimeError("target container identity changed during regression")
            return {
                "case_id": case_id,
                "funding_input": built["funding_input"],
                "funding_lovelace": built["funding_lovelace"],
                "funding_credential": built["funding_credential"],
                "funding_input_resolvable": built["funding_input_resolvable"],
                "transaction_id": built["transaction_id"],
                "body_sha256": hashlib.sha256(built["body"]).hexdigest(),
                "payload_sha256": hashlib.sha256(built["payload"]).hexdigest(),
                "classification": classify_value_coin_response(response),
                "value_rejection_marker": _value_rejection_marker(response),
                "response": response,
                "target_running_after": health["running"],
                "target_health_after": health,
                "included": False,
                "target_adopted": False,
            }

        valid_built = build_template(credentials[0], output_dir / VALID_CONTROL)
        initial_tip = _query_reference_tip(
            cli=cli, socket_path=socket_path, network_magic=network_magic
        )
        valid = submit_record(VALID_CONTROL, valid_built)
        if valid["classification"] == "accepted" and valid["target_running_after"]:
            inclusion_path = output_dir / VALID_CONTROL / "inclusion.json"
            _wait_for_reference_inclusion(
                cli=cli,
                socket_path=socket_path,
                network_magic=network_magic,
                address=credentials[0]["address"],
                destination=output_dir / VALID_CONTROL / "included-utxo.json",
                transaction_id=valid["transaction_id"],
                initial_tip=initial_tip,
                evidence_path=inclusion_path,
                target_propagation_gate=propagation_gate,
            )
            valid["included"] = True
            _wait_for_target_adoption(
                log_path=Path(str(target.get("log_path") or "")),
                expected_tip=_terminal_inclusion_tip(
                    inclusion_path, label="value-overflow valid control"
                ),
                evidence_path=output_dir / VALID_CONTROL / "target-adoption.json",
            )
            valid["target_adopted"] = True
        records.append(valid)
        (output_dir / VALID_CONTROL / "result.json").write_text(
            json.dumps(valid, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (output_dir / "checkpoint.json").write_text(
            json.dumps({"records": records}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if not (
            valid["classification"] == "accepted"
            and valid["funding_input_resolvable"] is True
            and valid["included"] is True
            and valid["target_adopted"] is True
            and valid["target_running_after"] is True
        ):
            raise RuntimeError("valid target control failed before overflow cases")

        boundary_probe_payload: bytes | None = None
        boundary_probe_identity: dict[str, str] | None = None
        for case_id, coin, credential in (
            (BOUNDARY_CONTROL, I64_MAX, credentials[1]),
            (OVERFLOW_MUTANT, I64_OVERFLOW, credentials[2]),
        ):
            template = build_template(credential, output_dir / case_id)
            mutated = build_value_coin_case(
                template_payload=template["payload"],
                output_coin=coin,
                sign_body=lambda body, template=template: resign_transaction_body(
                    template["payload"], body, [Path(template["controller_skey"])]
                ),
            )
            built = {
                **template,
                **mutated,
                "transaction_id": mutated["transaction_id"],
            }
            if case_id == BOUNDARY_CONTROL:
                boundary_probe_payload = built["payload"]
                boundary_probe_identity = {
                    "transaction_id": str(built["transaction_id"]),
                    "payload_sha256": str(built["payload_sha256"]),
                }
            record = {**submit_record(case_id, built), "output_coin": coin}
            if case_id == OVERFLOW_MUTANT:
                if boundary_probe_payload is None or boundary_probe_identity is None:
                    raise RuntimeError("boundary probe payload was not retained")
                probe_response = _submit_amaru_container(
                    target_container, boundary_probe_payload
                )
                probe_health = _settled_container_health(target_container)
                if probe_health["container_id"] != target_container:
                    raise RuntimeError(
                        "target container identity changed during post-mutant probe"
                    )
                record["post_mutant_boundary_probe"] = {
                    **boundary_probe_identity,
                    "classification": classify_value_coin_response(probe_response),
                    "value_rejection_marker": _value_rejection_marker(probe_response),
                    "response": probe_response,
                    "target_running_after": probe_health["running"],
                    "target_health_after": probe_health,
                }
            records.append(record)
            (output_dir / case_id / "result.json").write_text(
                json.dumps(record, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            (output_dir / "checkpoint.json").write_text(
                json.dumps({"records": records}, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            if case_id == BOUNDARY_CONTROL and not boundary_control_allows_mutant(record):
                raise RuntimeError(
                    "i64::MAX boundary control failed; overflow mutant was not submitted"
                )

    status = assess_value_coin_overflow(records)
    report = {
        "schema_version": 1,
        "evidence_scope": "fresh_funded_amaru_value_coin_i64_boundary",
        "target_identity": target,
        "reference_identity": reference,
        "network_magic": network_magic,
        "records": records,
        "regression_status": status,
        "limitations": [
            "Cardano-node is supporting production infrastructure, not a differential oracle.",
            "A pass applies only to the exact selected Amaru artifact and private devnet contract.",
        ],
    }
    (output_dir / "result.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report
