#!/usr/bin/env python3
"""Submit canonical four-element controls and one exact three-element transaction.

The variant omits the auxiliary-data element from an otherwise valid, signed
Conway transaction.  It is submitted byte-for-byte to both implementations;
acceptance here is only a submission/admission observation, not inclusion.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import cbor2


CONTROL_CASE = "four-element-control"
BOUNDARY_CASE = "three-element-transaction"
_CONCLUSIVE = {"accepted", "decode_reject", "ledger_reject", "phase2_reject"}
_MAX_REFERENCE_LOG_SLICE_BYTES = 256 * 1024


def _split_four_element_payload(payload: bytes) -> tuple[list[bytes], list[Any]]:
    if not payload or payload[0] != 0x84:
        raise ValueError("canonical transaction must use a definite four-element array")
    stream = io.BytesIO(payload[1:])
    decoder = cbor2.CBORDecoder(stream)
    raw_elements: list[bytes] = []
    values: list[Any] = []
    for _ in range(4):
        start = stream.tell()
        values.append(decoder.decode())
        end = stream.tell()
        raw_elements.append(payload[1 + start : 1 + end])
    if stream.read(1):
        raise ValueError("canonical transaction has trailing bytes")
    if values[3] is not None:
        raise ValueError("canonical transaction auxiliary-data element must be null")
    return raw_elements, values


def build_three_element_variant(canonical_payload: bytes) -> dict[str, Any]:
    """Omit the exact fourth null element without re-encoding the first three."""

    elements, values = _split_four_element_payload(canonical_payload)
    variant = b"\x83" + b"".join(elements[:3])
    decoded = cbor2.loads(variant)
    if not isinstance(decoded, list) or len(decoded) != 3:
        raise RuntimeError("three-element transaction construction failed")
    body = elements[0]
    return {
        "variant_payload": variant,
        "canonical_element_count": 4,
        "variant_element_count": 3,
        "canonical_auxiliary_data_is_null": values[3] is None,
        "variant_trailing_bytes": 0,
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "transaction_id": hashlib.blake2b(body, digest_size=32).hexdigest(),
        "canonical_payload_sha256": hashlib.sha256(canonical_payload).hexdigest(),
        "variant_payload_sha256": hashlib.sha256(variant).hexdigest(),
        "canonical_payload_length": len(canonical_payload),
        "variant_payload_length": len(variant),
        "omitted_element_hex": elements[3].hex(),
    }


def _submit_boundary_payload(
    submit: Callable[[str, bytes], dict[str, Any]], payload: bytes
) -> dict[str, dict[str, Any]]:
    """Observe the reference directly before target admission can relay the payload."""

    return {role: submit(role, payload) for role in ("reference", "target")}


def _submit_reference_with_decoder_log(
    *,
    submit: Callable[[], dict[str, Any]],
    payload: bytes,
    reference: dict[str, Any],
) -> dict[str, Any]:
    """Correlate one unavailable raw submission with its bounded node log slice."""

    log_path = Path(str(reference.get("log_path") or ""))
    try:
        log_start_offset = log_path.stat().st_size if log_path.is_absolute() else -1
    except OSError:
        log_start_offset = -1
    attempt_started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    response = submit()
    attempt_ended_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    if response.get("classification") != "unavailable" or log_start_offset < 0:
        return response

    payload_sha256 = hashlib.sha256(payload).hexdigest()
    receipt = response.get("protocol_receipt") or {}
    identity_binding = response.get("identity_binding") or {}
    identity = {
        "node_id": str(reference.get("id") or ""),
        "container_id": str(reference.get("container_id") or ""),
        "container_image_id": str(reference.get("container_image_id") or ""),
        "source_revision": str(reference.get("source_revision") or ""),
    }
    if (
        receipt.get("outcome") not in {"transport_unavailable", "decode_close"}
        or receipt.get("payload_sha256") != payload_sha256
        or receipt.get("payload_length") != len(payload)
        or any(identity_binding.get(key) != value for key, value in identity.items())
        or not re.fullmatch(r"[0-9a-f]{64}", identity["container_id"])
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", identity["container_image_id"])
        or not re.fullmatch(r"[0-9a-f]{40}", identity["source_revision"])
    ):
        return response

    try:
        log_end_offset = log_path.stat().st_size
        slice_length = log_end_offset - log_start_offset
        if not 0 < slice_length <= _MAX_REFERENCE_LOG_SLICE_BYTES:
            return response
        with log_path.open("rb") as stream:
            stream.seek(log_start_offset)
            log_slice = stream.read(slice_length)
    except OSError:
        return response
    if len(log_slice) != slice_length:
        return response

    matched_line = ""
    for line in log_slice.decode("utf-8", errors="replace").splitlines():
        lowered = line.lower()
        if (
            "miniprotocolnum 6" in lowered
            and "responderdir terminated with exception decoderfailure" in lowered
            and "localtxsubmission" in lowered
            and "deserialisefailure" in lowered
        ):
            matched_line = line[:4096]
            break
    if not matched_line:
        return response

    return {
        **response,
        "classification": "decode_reject",
        "observation_boundary": "node-log-correlated-decode-reject",
        "node_log_observed": True,
        "reference_decoder_evidence": {
            "schema_version": 1,
            "classification": "decode_reject",
            "correlation_method": "same-attempt-appended-log-slice",
            "payload_sha256": payload_sha256,
            "payload_length": len(payload),
            "attempt_started_at": attempt_started_at,
            "attempt_ended_at": attempt_ended_at,
            "identity": identity,
            "log_path": str(log_path),
            "log_start_offset": log_start_offset,
            "log_end_offset": log_end_offset,
            "log_slice_sha256": hashlib.sha256(log_slice).hexdigest(),
            "matched_line": matched_line,
        },
    }


def assess_transaction_array_arity(
    controls: dict[str, dict[str, Any]], boundary: dict[str, Any]
) -> dict[str, Any]:
    roles = ("target", "reference")
    controls_complete = all(
        isinstance(controls.get(role), dict)
        and controls[role].get("classification") == "accepted"
        for role in roles
    )
    credentials = [str((controls.get(role) or {}).get("funding_credential") or "") for role in roles]
    boundary_credential = str(boundary.get("funding_credential") or "")
    isolated = len(set(credentials + [boundary_credential])) == 3 and all(
        credentials + [boundary_credential]
    )
    shared_hash = str(boundary.get("payload_sha256") or "")
    payloads_match = bool(
        len(shared_hash) == 64
        and all(
            str((boundary.get(role) or {}).get("payload_sha256") or "") == shared_hash
            for role in roles
        )
    )
    reached = (
        boundary.get("variant_element_count") == 3
        and boundary.get("variant_trailing_bytes") == 0
        and payloads_match
    )
    classifications = [
        str((boundary.get(role) or {}).get("classification") or "") for role in roles
    ]
    conclusive = all(value in _CONCLUSIVE for value in classifications)
    divergence = conclusive and classifications[0] != classifications[1]
    return {
        "valid_controls_complete": controls_complete,
        "funding_credentials_isolated": isolated,
        "variant_payloads_match": payloads_match,
        "three_element_boundary_reached": reached,
        "outcomes_conclusive": conclusive,
        "divergence_observed": divergence,
        "complete": controls_complete and isolated and reached and conclusive,
    }


def _funding_credentials(runtime: dict[str, Any], runtime_root: Path) -> list[dict[str, Any]]:
    funding = runtime.get("generated_workload_funding") or {}
    raw = funding.get("credentials")
    if not isinstance(raw, list) or len(raw) != 3:
        raise RuntimeError("transaction-array case requires three generated genesis credentials")
    credentials: list[dict[str, Any]] = []
    for index, item in enumerate(raw, start=1):
        expected_id = f"utxo{index}"
        if not isinstance(item, dict) or item.get("id") != expected_id:
            raise RuntimeError("generated genesis credential ordering is invalid")
        address = str(item.get("address") or "")
        vkey = runtime_root / "env" / "utxo-keys" / expected_id / "utxo.vkey"
        skey = runtime_root / "env" / "utxo-keys" / expected_id / "utxo.skey"
        if not address.startswith("addr_test") or not vkey.is_file() or not skey.is_file():
            raise RuntimeError(f"generated genesis credential {expected_id} is unavailable")
        credentials.append(
            {
                "id": expected_id,
                "address": address,
                "lovelace": item.get("lovelace"),
                "controller_vkey": str(vkey),
                "controller_skey": str(skey),
                "container_skey": f"/env/utxo-keys/{expected_id}/utxo.skey",
            }
        )
    if len({item["address"] for item in credentials}) != 3:
        raise RuntimeError("generated genesis credential addresses are not independent")
    return credentials


def run_transaction_array_arity_differential(
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
    from profile_manager.submit_primitives import _validation_stage
    from scripts.runtime_cardano_measurement_calibration import (
        _checked,
        _containerized_cardano_workload,
        _controller_container,
        _query_utxo,
        _transaction_id,
    )
    from scripts.runtime_redeemer_container_live_controls import (
        _envelope,
        _select_funding_input,
        _submit_amaru_container,
        _submit_reference_raw,
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
        raise RuntimeError("transaction-array case requires the installed Docker controller")
    runtime_root = Path(str(runtime.get("runtime_root") or ""))
    if not runtime_root.is_absolute():
        raise RuntimeError("runtime root is not absolute")
    output_dir.mkdir(parents=True, exist_ok=True)
    credentials = _funding_credentials(runtime, runtime_root)
    target = identities["target"]
    reference = identities["reference"]
    network_magic = int(runtime["network_magic"])
    socket_path = str(reference.get("container_socket_path") or "")
    helper_image = str(os.environ.get("DWARF_LOCALTXSUBMISSION_HELPER_IMAGE") or "")

    def submit(role: str, payload: bytes) -> dict[str, Any]:
        response = (
            _submit_amaru_container(str(target["container_id"]), payload)
            if role == "target"
            else _submit_reference_raw(
                runtime=runtime,
                reference=reference,
                payload=payload,
                helper_image=helper_image,
            )
        )
        return {**response, "classification": _validation_stage(response)}

    with _containerized_cardano_workload(
        runtime_root=runtime_root,
        runtime=runtime,
        node=reference,
        output_dir=output_dir,
        controller=controller,
    ) as (workload_runtime, _workload_node):
        cli = str(workload_runtime["support_binaries"]["cardano-cli"])

        def build_canonical(credential: dict[str, Any], case_dir: Path) -> dict[str, Any]:
            case_dir.mkdir(parents=True, exist_ok=True)
            utxos = _query_utxo(
                cli=cli,
                socket_path=socket_path,
                network_magic=network_magic,
                address=credential["address"],
                destination=case_dir / "genesis-utxo.json",
            )
            expected_lovelace = credential.get("lovelace")
            minimum = int(expected_lovelace) if isinstance(expected_lovelace, int) else 10_000_000
            funding_input, funding_lovelace = _select_funding_input(
                utxos, minimum_lovelace=minimum
            )
            body_path = case_dir / "transaction.body"
            _checked(
                [
                    cli,
                    "conway",
                    "transaction",
                    "build",
                    "--socket-path",
                    socket_path,
                    "--testnet-magic",
                    str(network_magic),
                    "--tx-in",
                    funding_input,
                    "--tx-out",
                    f"{credential['address']}+5000000",
                    "--change-address",
                    credential["address"],
                    "--out-file",
                    str(body_path),
                ]
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
            elements, _values = _split_four_element_payload(payload)
            transaction_id = _transaction_id(cli, signed_path)
            return {
                "payload": payload,
                "payload_sha256": hashlib.sha256(payload).hexdigest(),
                "body_sha256": hashlib.sha256(elements[0]).hexdigest(),
                "transaction_id": transaction_id,
                "funding_input": funding_input,
                "funding_lovelace": funding_lovelace,
                "funding_credential": credential["id"],
                "element_count": 4,
                "auxiliary_data_is_null": True,
                "trailing_bytes": 0,
            }

        controls: dict[str, dict[str, Any]] = {}
        for role, credential in zip(("target", "reference"), credentials[:2]):
            built = build_canonical(credential, output_dir / CONTROL_CASE / role)
            response = submit(role, built["payload"])
            controls[role] = {
                **{key: value for key, value in built.items() if key != "payload"},
                **response,
            }
        if any(controls[role]["classification"] != "accepted" for role in controls):
            raise RuntimeError("canonical four-element controls were not both accepted")

        canonical = build_canonical(credentials[2], output_dir / BOUNDARY_CASE)
        variant = build_three_element_variant(canonical["payload"])
        payload = variant.pop("variant_payload")
        if variant["transaction_id"] != canonical["transaction_id"]:
            raise RuntimeError("canonical and three-element transaction IDs do not match")
        def submit_boundary(role: str, boundary_payload: bytes) -> dict[str, Any]:
            if role == "reference":
                return _submit_reference_with_decoder_log(
                    submit=lambda: submit(role, boundary_payload),
                    payload=boundary_payload,
                    reference=reference,
                )
            return submit(role, boundary_payload)

        responses = _submit_boundary_payload(submit_boundary, payload)
        boundary = {
            **variant,
            "funding_credential": canonical["funding_credential"],
            "funding_input": canonical["funding_input"],
            "funding_lovelace": canonical["funding_lovelace"],
            "transaction_id": variant["transaction_id"],
            "canonical_transaction_id": canonical["transaction_id"],
            "variant_transaction_id": variant["transaction_id"],
            "transaction_id_match": True,
            "canonical_body_sha256": canonical["body_sha256"],
            "payload_sha256": variant["variant_payload_sha256"],
            "target": {
                **responses["target"],
                "payload_sha256": variant["variant_payload_sha256"],
            },
            "reference": {
                **responses["reference"],
                "payload_sha256": variant["variant_payload_sha256"],
            },
        }
        status = assess_transaction_array_arity(controls, boundary)

    return {
        "schema_version": 1,
        "evidence_scope": "transaction_array_three_element_decode_and_admission",
        "target_identity": target,
        "reference_identity": reference,
        "network_magic": network_magic,
        "controls": controls,
        "boundary": boundary,
        "boundary_status": status,
        "limitations": [
            "This direct-genesis case observes submission decoding/admission and does not require post-start block production.",
            "Acceptance is not inclusion, adoption or consensus agreement.",
            "A transport close remains unavailable unless the same bounded attempt "
            "is correlated to a reference LocalTxSubmission DecoderFailure log.",
            "The legacy header-only corpus did not provide independently funded valid controls.",
        ],
    }
