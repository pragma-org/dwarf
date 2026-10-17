#!/usr/bin/env python3
"""Fresh-funded Conway governance admission control and zero-denominator case.

This is deliberately admission-only.  It retains exact selected node identities,
live governance prerequisites, signed payload identities, submit responses and
reference-chain inclusion without claiming ratification or enactment.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import urllib.parse
from collections.abc import Callable, Mapping
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any

import cbor2
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scripts.runtime_cardano_measurement_calibration import (
    _checked,
    _containerized_cardano_workload,
    _controller_container,
    _inspect_container,
    _query_utxo,
)
from scripts.runtime_redeemer_container_live_controls import (
    _envelope,
    _query_reference_tip,
    _reference_tx_submission_propagation_gate,
    _select_funding_input,
    _submit_amaru_container,
    _submit_reference_raw,
    _submit_reference_with_decoder_log,
    _wait_for_reference_inclusion,
    _wait_for_target_adoption,
    _write_envelope,
    split_transaction,
    validate_runtime_identity,
)

CONTROL_CASE = "nonzero-control"
ZERO_CASE = "zero-denominator"
MATCHED_MIDDLE_CASE = "nonzero-middle-control"
POST_CONTROL_CASE = "post-mutant-valid-traffic"
ZERO_DENOMINATOR_MODE = "zero-denominator"
MATCHED_CONTROL_MODE = "nonzero-middle-control"
REGISTRATION_PREP = "registration-prep"
PARAMETER_KEY = 33  # Conway minFeeRefScriptCostPerByte
FEE_RESERVE = 5_000_000
COLLATERAL_LOVELACE = 5_000_000
POST_CONTROL_LOVELACE = 5_000_000
POST_CONTROL_PAYMENT_LOVELACE = 2_000_000
FIXTURE_ROOT = (
    Path(__file__).resolve().parents[1]
    / "corpora"
    / "redeemer-container"
    / "live-controls"
)
GUARDRAILS_SCRIPT = FIXTURE_ROOT / "always-succeeds-v3.plutus"
ANCHOR_BYTES = (
    b'{"description":"DWARF zero-denominator governance admission control",'
    b'"schema_version":1}\n'
)
_MAX_TARGET_LOG_SLICE_BYTES = 256 * 1024
_TARGET_ADMISSION_WAIT_SECONDS = 10.0
_POST_MUTANT_TERMINAL_PROGRESS_BLOCKS = 3
_MAX_P2P_TRACE_BYTES = 4 * 1024 * 1024
_MAX_P2P_TRACE_PRELUDE_BYTES = 4 * 1024 * 1024
_MAX_P2P_TRACE_EVENTS = 256
_P2P_EVENT_MARKERS = (
    "tx_submission.initiator.reply_tx_ids",
    "tx_submission.initiator.received_request",
    "tx_submission.initiator.reply_txs",
    "tx_submission.initiator.acknowledged",
    "tx_submission.initiator.wait_for_at_least",
    "connection.child_died",
    "manager.peer.connection_died",
    "manager.peer.connected",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _target_log_identity(target: Mapping[str, Any]) -> dict[str, str]:
    identity = {
        "node_id": str(target.get("id") or ""),
        "container_id": str(target.get("container_id") or ""),
        "container_image_id": str(target.get("container_image_id") or ""),
        "source_revision": str(target.get("source_revision") or ""),
    }
    if (
        not identity["node_id"]
        or re.fullmatch(r"[0-9a-f]{64}", identity["container_id"]) is None
        or re.fullmatch(
            r"sha256:[0-9a-f]{64}", identity["container_image_id"]
        )
        is None
        or re.fullmatch(r"[0-9a-f]{40}", identity["source_revision"]) is None
    ):
        raise RuntimeError("Amaru admission log identity is incomplete")
    return identity


def _trace_field(line: str, name: str) -> str | None:
    match = re.search(
        rf"(?:^|\s){re.escape(name)}=(?:\"([^\"]*)\"|(\[[^\]]*\])|(\S+))",
        line,
    )
    if match is None:
        return None
    return next((value for value in match.groups() if value is not None), None)


def _trace_connection(line: str) -> int | None:
    value = _trace_field(line, "conn_id")
    return int(value) if value is not None and value.isdigit() else None


def _selected_trace_event(line: str) -> str | None:
    return next((marker for marker in _P2P_EVENT_MARKERS if marker in line), None)


def assess_txsubmission_trace(
    *,
    target: Mapping[str, Any],
    log_start_offset: int,
    malformed_transaction_id: str,
    later_valid_transaction_id: str,
    expected_peer: str | None = None,
) -> dict[str, Any]:
    """Assess one bounded, single-peer TxSubmission sequence from retained logs."""

    identity = _target_log_identity(target)
    for transaction_id in (malformed_transaction_id, later_valid_transaction_id):
        if re.fullmatch(r"[0-9a-f]{64}", transaction_id) is None:
            raise RuntimeError("P2P trace transaction identity is invalid")
    peer_addresses = [
        str(value)
        for value in (target.get("container_peer_addresses") or [])
        if str(value)
    ]
    if expected_peer is None:
        if len(peer_addresses) != 1:
            return {
                "schema_version": 1,
                "status": "inconclusive",
                "restart_eligible": False,
                "blocking_reason": "single-controlled-peer-is-not-established",
                "identity": identity,
                "configured_peers": peer_addresses,
            }
        expected_peer = peer_addresses[0]
        peer_binding = "single-configured-peer"
    else:
        peer_binding = "reference-container-network-address"
    log_path = Path(str(target.get("log_path") or ""))
    if not log_path.is_absolute() or not log_path.is_file():
        raise RuntimeError("P2P trace requires the retained target log")
    try:
        log_size = log_path.stat().st_size
    except OSError as exc:
        raise RuntimeError("P2P trace log is unavailable") from exc
    if (
        isinstance(log_start_offset, bool)
        or not isinstance(log_start_offset, int)
        or log_start_offset < 0
        or log_start_offset > log_size
    ):
        raise RuntimeError("P2P trace start offset is invalid")
    trace_length = log_size - log_start_offset
    base = {
        "schema_version": 1,
        "status": "inconclusive",
        "restart_eligible": False,
        "identity": identity,
        "configured_peers": peer_addresses,
        "observed_peer": expected_peer,
        "peer_binding": peer_binding,
        "log_path": str(log_path),
        "log_start_offset": log_start_offset,
        "log_end_offset": log_size,
        "log_slice_length": trace_length,
        "limits": {
            "maximum_trace_bytes": _MAX_P2P_TRACE_BYTES,
            "maximum_prelude_bytes": _MAX_P2P_TRACE_PRELUDE_BYTES,
            "maximum_retained_events": _MAX_P2P_TRACE_EVENTS,
            "requested_id_prefix_hex_length": 12,
        },
    }
    if trace_length > _MAX_P2P_TRACE_BYTES:
        return {**base, "blocking_reason": "trace-slice-oversized"}
    prelude_start = max(0, log_start_offset - _MAX_P2P_TRACE_PRELUDE_BYTES)
    try:
        with log_path.open("rb") as stream:
            stream.seek(prelude_start)
            prelude = stream.read(log_start_offset - prelude_start)
            trace = stream.read(trace_length)
    except OSError as exc:
        raise RuntimeError("P2P trace log cannot be read") from exc
    base.update(
        {
            "prelude_start_offset": prelude_start,
            "prelude_sha256": hashlib.sha256(prelude).hexdigest(),
            "log_slice_sha256": hashlib.sha256(trace).hexdigest(),
        }
    )

    active_connection: int | None = None
    for line in prelude.decode("utf-8", errors="replace").splitlines():
        if "manager.peer.connected" not in line:
            continue
        if _trace_field(line, "peer") != expected_peer:
            continue
        connection = _trace_connection(line)
        if connection is not None:
            active_connection = connection
    initial_binding = "prelude-connected-event"
    if active_connection is None:
        # The connection can still be bound retroactively by the exact
        # same-peer child-death event after the transaction exchange.
        active_connection = 0
        initial_binding = "subsequent-child-death-event"

    windows: dict[int, list[str]] = {active_connection: []}
    malformed_advertised = False
    malformed_requested = False
    malformed_reply_txs = False
    malformed_acked = False
    later_advertised = False
    later_requested = False
    close_connection: int | None = None
    reconnect_connection: int | None = None
    malformed_readvertised = False
    retained_events: list[dict[str, Any]] = []
    pending_requests: dict[int, list[str]] = {}
    blocking_reason = "required-p2p-cycle-was-not-observed"
    relative_offset = 0
    for raw_line in trace.splitlines(keepends=True):
        line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
        marker = _selected_trace_event(line)
        line_offset = log_start_offset + relative_offset
        relative_offset += len(raw_line)
        if marker is None:
            continue
        peer = _trace_field(line, "peer")
        if peer is not None and peer != expected_peer:
            continue
        if len(retained_events) >= _MAX_P2P_TRACE_EVENTS:
            return {
                **base,
                "blocking_reason": "retained-event-limit-exceeded",
                "events": retained_events,
            }
        event = {
            "name": marker,
            "offset": line_offset,
            "line_sha256": hashlib.sha256(raw_line).hexdigest(),
            "line": line[:4096],
        }
        retained_events.append(event)

        if marker == "manager.peer.connected":
            connection = _trace_connection(line)
            if connection is None:
                blocking_reason = "connected-event-lacks-connection-id"
                continue
            if close_connection is None and connection != active_connection:
                blocking_reason = "overlapping-peer-connection-observed"
                continue
            if close_connection is not None and connection != close_connection:
                reconnect_connection = connection
                active_connection = connection
                windows.setdefault(connection, [])
            continue

        if marker == "tx_submission.initiator.reply_tx_ids":
            ids = re.findall(r"(?<![0-9A-Fa-f])[0-9a-f]{64}(?![0-9A-Fa-f])", line)
            window = windows.setdefault(active_connection, [])
            window.extend(ids)
            if malformed_transaction_id in ids:
                if reconnect_connection == active_connection:
                    malformed_readvertised = True
                else:
                    malformed_advertised = True
            if later_valid_transaction_id in ids:
                later_advertised = True
            continue

        if marker == "tx_submission.initiator.received_request" and _trace_field(
            line, "request"
        ) == "txs":
            ids_value = _trace_field(line, "ids") or ""
            prefixes = re.findall(
                r"(?<![0-9A-Fa-f])[0-9a-f]{12}(?![0-9A-Fa-f])", ids_value
            )
            window = windows.setdefault(active_connection, [])
            for prefix in prefixes:
                candidates = {value for value in window if value.startswith(prefix)}
                if len(candidates) != 1:
                    return {
                        **base,
                        "blocking_reason": "requested-id-prefix-is-ambiguous",
                        "events": retained_events,
                    }
                requested = next(iter(candidates))
                malformed_requested |= requested == malformed_transaction_id
                later_requested |= requested == later_valid_transaction_id
                pending_requests.setdefault(active_connection, []).append(requested)
            continue

        if marker == "tx_submission.initiator.reply_txs":
            count_value = _trace_field(line, "count")
            requested = pending_requests.pop(active_connection, [])
            if count_value is not None and count_value.isdigit():
                delivered_count = int(count_value)
                malformed_reply_txs |= (
                    malformed_transaction_id in requested
                    and delivered_count == len(requested)
                    and delivered_count > 0
                )
            continue

        if marker == "tx_submission.initiator.acknowledged":
            ack_value = _trace_field(line, "ack")
            if ack_value is None or not ack_value.isdigit():
                blocking_reason = "acknowledgement-count-is-invalid"
                continue
            ack = int(ack_value)
            window = windows.setdefault(active_connection, [])
            removed = window[:ack]
            del window[:ack]
            malformed_acked |= malformed_transaction_id in removed
            continue

        if marker in {
            "connection.child_died",
            "manager.peer.connection_died",
        }:
            connection = _trace_connection(line)
            if active_connection == 0 and connection is not None:
                windows[connection] = windows.pop(0, [])
                active_connection = connection
            if connection != active_connection:
                blocking_reason = "connection-close-does-not-bind-active-peer"
                continue
            if marker == "connection.child_died":
                child = (_trace_field(line, "child") or "").lower()
                if "txsubmission" not in child.replace("_", ""):
                    continue
            if malformed_requested and not malformed_acked:
                close_connection = connection

    exact_cycle = all(
        (
            malformed_advertised,
            malformed_requested,
            malformed_reply_txs,
            later_advertised,
            close_connection is not None,
            reconnect_connection is not None,
            malformed_readvertised,
            not malformed_acked,
        )
    )
    return {
        **base,
        "status": "exact-cycle-observed" if exact_cycle else "inconclusive",
        "restart_eligible": exact_cycle,
        "blocking_reason": None if exact_cycle else blocking_reason,
        "malformed": {
            "transaction_id": malformed_transaction_id,
            "advertised": malformed_advertised,
            "requested": malformed_requested,
            "reply_txs_observed": malformed_reply_txs,
            "acknowledged_before_close": malformed_acked,
            "readvertised_after_reconnect": malformed_readvertised,
        },
        "later_valid": {
            "transaction_id": later_valid_transaction_id,
            "advertised": later_advertised,
            "requested": later_requested,
        },
        "connection": {
            "initial_connection_id": next(iter(windows)),
            "initial_connection_binding": initial_binding,
            "closed_connection_id": close_connection,
            "reconnected_connection_id": reconnect_connection,
        },
        "events": retained_events,
        "evidence_limit": (
            "source event sequence binds the controlled peer connection but does not "
            "establish public-network reachability or causation outside this fixture"
        ),
    }


def _submit_amaru_with_admission_log(
    *,
    target: Mapping[str, Any],
    payload: bytes,
    transaction_id: str,
    submit: Callable[[str, bytes], dict[str, Any]] = _submit_amaru_container,
    wait_seconds: float = _TARGET_ADMISSION_WAIT_SECONDS,
) -> dict[str, Any]:
    """Submit to Amaru and retain only a newly appended exact-ID admission event."""

    if re.fullmatch(r"[0-9a-f]{64}", transaction_id) is None:
        raise RuntimeError("Amaru admission transaction identity is invalid")
    identity = _target_log_identity(target)
    log_path = Path(str(target.get("log_path") or ""))
    if not log_path.is_absolute() or not log_path.is_file():
        raise RuntimeError("Amaru admission requires the retained target log")
    try:
        log_start_offset = log_path.stat().st_size
    except OSError as exc:
        raise RuntimeError("Amaru admission log is unavailable") from exc

    attempt_started_at = _utc_now()
    response = submit(identity["container_id"], payload)
    attempt_ended_at = _utc_now()
    payload_sha256 = hashlib.sha256(payload).hexdigest()
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "status": "exact-transaction-admission-not-observed",
        "correlation_method": "same-attempt-appended-log-slice",
        "transaction_id": transaction_id,
        "payload_sha256": payload_sha256,
        "payload_length": len(payload),
        "attempt_started_at": attempt_started_at,
        "attempt_ended_at": attempt_ended_at,
        "identity": identity,
        "log_path": str(log_path),
        "log_start_offset": log_start_offset,
    }
    matched_line = ""
    transaction_id_token = re.compile(
        rf"(?<![0-9A-Fa-f]){re.escape(transaction_id)}(?![0-9A-Fa-f])"
    )
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while True:
        try:
            log_end_offset = log_path.stat().st_size
            slice_length = log_end_offset - log_start_offset
            if slice_length > _MAX_TARGET_LOG_SLICE_BYTES:
                evidence.update(
                    {
                        "status": "log-slice-oversized",
                        "log_end_offset": log_end_offset,
                        "log_slice_length": slice_length,
                    }
                )
                break
            with log_path.open("rb") as stream:
                stream.seek(log_start_offset)
                log_slice = stream.read(max(0, slice_length))
        except OSError:
            evidence["status"] = "log-slice-unavailable"
            break
        for line in log_slice.decode("utf-8", errors="replace").splitlines():
            if (
                "mempool: transaction.accepted" in line
                and transaction_id_token.search(line) is not None
            ):
                matched_line = line[:4096]
                break
        if matched_line or time.monotonic() >= deadline:
            evidence.update(
                {
                    "log_end_offset": log_end_offset,
                    "log_slice_length": len(log_slice),
                    "log_slice_sha256": hashlib.sha256(log_slice).hexdigest(),
                }
            )
            break
        time.sleep(0.05)

    observed = bool(matched_line)
    if observed:
        evidence.update(
            {
                "status": "exact-transaction-admission-observed",
                "matched_line": matched_line,
            }
        )
    from profile_manager.submit_primitives import _validation_stage

    classification = _validation_stage(response)
    if classification == "accepted" and not observed:
        classification = "unavailable"
    return {
        **response,
        "classification": classification,
        "local_mempool_admission_observed": observed,
        "amaru_admission_evidence": evidence,
    }


def _submit_zero_denominator_attempts(
    *,
    runtime: dict[str, Any],
    reference: dict[str, Any],
    target: dict[str, Any],
    payload: bytes,
    transaction_id: str,
    helper_image: str,
    before_target: Callable[[], Any] | None = None,
    admission_wait_seconds: float = _TARGET_ADMISSION_WAIT_SECONDS,
) -> dict[str, Any]:
    """Submit one shared mutant reference-first, then to the Amaru target."""

    from profile_manager.submit_primitives import _validation_stage

    def submit_reference() -> dict[str, Any]:
        response = _submit_reference_raw(
            runtime=runtime,
            reference=reference,
            payload=payload,
            helper_image=helper_image,
        )
        return {
            **response,
            "classification": response.get("classification")
            or _validation_stage(response),
        }

    reference_response = _submit_reference_with_decoder_log(
        submit=submit_reference,
        payload=payload,
        reference=reference,
    )
    if (
        reference_response.get("classification") != "decode_reject"
        or not isinstance(
            reference_response.get("reference_decoder_evidence"), dict
        )
    ):
        return {
            "submission_order": ["reference"],
            "target_pre_submit_tip": None,
            "reference": reference_response,
            "target": {
                "classification": "skipped",
                "reason": "direct reference decoder evidence is unavailable",
            },
        }
    target_pre_submit = before_target() if before_target is not None else None
    target_response = _submit_amaru_with_admission_log(
        target=target,
        payload=payload,
        transaction_id=transaction_id,
        submit=_submit_amaru_container,
        wait_seconds=admission_wait_seconds,
    )
    return {
        "submission_order": ["reference", "target"],
        "target_pre_submit_tip": target_pre_submit,
        "reference": reference_response,
        "target": target_response,
    }


def _submit_matched_nonzero_attempts(
    *,
    runtime: dict[str, Any],
    reference: dict[str, Any],
    target: dict[str, Any],
    payload: bytes,
    transaction_id: str,
    helper_image: str,
    before_reference: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Submit one shared valid middle control reference-first, then to Amaru."""

    from profile_manager.submit_primitives import _validation_stage

    reference_pre_submit = (
        before_reference() if before_reference is not None else None
    )
    reference_response = _submit_reference_raw(
        runtime=runtime,
        reference=reference,
        payload=payload,
        helper_image=helper_image,
    )
    reference_response = {
        **reference_response,
        "classification": reference_response.get("classification")
        or _validation_stage(reference_response),
    }
    if reference_response["classification"] != "accepted":
        return {
            "submission_order": ["reference"],
            "reference_pre_submit_tip": reference_pre_submit,
            "reference": reference_response,
            "target": {
                "classification": "skipped",
                "reason": "matched nonzero reference control was not accepted",
            },
        }
    target_response = _submit_amaru_with_admission_log(
        target=target,
        payload=payload,
        transaction_id=transaction_id,
        submit=_submit_amaru_container,
    )
    return {
        "submission_order": ["reference", "target"],
        "reference_pre_submit_tip": reference_pre_submit,
        "reference": reference_response,
        "target": target_response,
    }


def _wait_for_post_mutant_reference_inclusion(**kwargs: Any) -> dict[str, Any]:
    return _wait_for_reference_inclusion(
        terminal_progress_blocks=_POST_MUTANT_TERMINAL_PROGRESS_BLOCKS,
        **kwargs,
    )


def _confirm_post_mutant_control_on_reference(
    *,
    runtime: dict[str, Any],
    reference: dict[str, Any],
    payload: bytes,
    transaction_id: str,
    helper_image: str,
    cli: str,
    socket_path: str,
    network_magic: int,
    address: str,
    output_dir: Path,
    target_observation: dict[str, Any],
) -> dict[str, Any]:
    """Submit the retained post-control bytes directly after target observation."""

    if (
        target_observation.get("status")
        != "not-included-after-reference-progress"
        or target_observation.get("terminal_progress_block_threshold")
        != _POST_MUTANT_TERMINAL_PROGRESS_BLOCKS
    ):
        raise RuntimeError(
            "direct post-control confirmation requires the completed target "
            "propagation observation"
        )
    from profile_manager.submit_primitives import _validation_stage

    payload_sha256 = hashlib.sha256(payload).hexdigest()
    payload_path = output_dir / "direct-reference-payload.cbor"
    payload_path.write_bytes(payload)
    initial_tip = _query_reference_tip(
        cli=cli, socket_path=socket_path, network_magic=network_magic
    )
    response = _submit_reference_raw(
        runtime=runtime,
        reference=reference,
        payload=payload,
        helper_image=helper_image,
    )
    classification = response.get("classification") or _validation_stage(response)
    identity_binding = response.get("identity_binding") or {}
    expected_identity = {
        "node_id": str(reference.get("id") or ""),
        "container_id": str(reference.get("container_id") or ""),
        "container_image_id": str(reference.get("container_image_id") or ""),
        "source_revision": str(reference.get("source_revision") or ""),
    }
    identity_match = all(
        identity_binding.get(key) == value
        for key, value in expected_identity.items()
    )
    receipt = response.get("protocol_receipt") or {}
    payload_identity_match = (
        receipt.get("payload_sha256") == payload_sha256
        and receipt.get("payload_length") == len(payload)
    )
    if classification == "accepted" and not (
        identity_match and payload_identity_match
    ):
        classification = "unavailable"

    inclusion_path = output_dir / "direct-reference-inclusion.json"
    included = False
    if classification == "accepted":
        try:
            _wait_for_reference_inclusion(
                cli=cli,
                socket_path=socket_path,
                network_magic=network_magic,
                address=address,
                destination=output_dir / "direct-reference-included-utxo.json",
                transaction_id=transaction_id,
                initial_tip=initial_tip,
                evidence_path=inclusion_path,
            )
            included = True
        except RuntimeError:
            included = False
    inclusion_evidence: dict[str, Any] = {}
    if inclusion_path.is_file():
        inclusion_evidence = json.loads(inclusion_path.read_text(encoding="utf-8"))
    return {
        **response,
        "classification": classification,
        "included": included,
        "payload_sha256": payload_sha256,
        "payload_length": len(payload),
        "payload_path": str(payload_path.name),
        "transaction_id": transaction_id,
        "reference_identity": expected_identity,
        "identity_match": identity_match,
        "payload_identity_match": payload_identity_match,
        "target_observation_status": target_observation.get("status"),
        "target_observation_terminal_progress_blocks": target_observation.get(
            "terminal_progress_block_threshold"
        ),
        "inclusion_evidence_path": str(inclusion_path.name),
        "inclusion_evidence": inclusion_evidence,
        "evidence_limit": (
            "direct reference acceptance/inclusion confirms the control bytes; "
            "it does not prove peer relay or relay rejection"
        ),
    }


def _reference_peer_identity(reference: Mapping[str, Any]) -> dict[str, Any]:
    container_id = str(reference.get("container_id") or "")
    if re.fullmatch(r"[0-9a-f]{64}", container_id) is None:
        raise RuntimeError("reference peer container identity is invalid")
    inspected = _inspect_container(container_id)
    if str(inspected.get("Id") or "") != container_id:
        raise RuntimeError("reference peer Docker identity changed")
    image_id = str(reference.get("container_image_id") or "")
    if str(inspected.get("Image") or "") != image_id:
        raise RuntimeError("reference peer image identity changed")
    networks = (inspected.get("NetworkSettings") or {}).get("Networks") or {}
    addresses = sorted(
        {
            str(value.get("IPAddress") or "")
            for value in networks.values()
            if isinstance(value, dict) and str(value.get("IPAddress") or "")
        }
    )
    if len(addresses) != 1:
        raise RuntimeError("reference peer network address is not unique")
    return {
        "peer": f"{addresses[0]}:3001",
        "container_id": container_id,
        "container_image_id": image_id,
        "source_revision": str(reference.get("source_revision") or ""),
        "docker_networks": sorted(str(name) for name in networks),
    }


def _restart_owned_amaru(
    *, runtime: Mapping[str, Any], target: Mapping[str, Any]
) -> dict[str, Any]:
    identity = _target_log_identity(target)
    inspected_before = _inspect_container(identity["container_id"])
    labels = (inspected_before.get("Config") or {}).get("Labels") or {}
    expected_labels = {
        "io.dwarf.managed": "true",
        "io.dwarf.installation": str(os.environ.get("DWARF_INSTALLATION_ID") or ""),
        "com.docker.compose.project": str(runtime.get("compose_project") or ""),
        "io.dwarf.topology": str(runtime.get("profile_id") or ""),
        "io.dwarf.node_id": identity["node_id"],
        "io.dwarf.implementation": "amaru",
    }
    if any(str(labels.get(key) or "") != value for key, value in expected_labels.items()):
        raise RuntimeError("Amaru recovery restart ownership does not match")
    if (
        str(inspected_before.get("Id") or "") != identity["container_id"]
        or str(inspected_before.get("Image") or "")
        != identity["container_image_id"]
    ):
        raise RuntimeError("Amaru recovery restart identity changed")
    log_path = Path(str(target.get("log_path") or ""))
    if not log_path.is_absolute() or not log_path.is_file():
        raise RuntimeError("Amaru recovery restart log is unavailable")
    log_start_offset = log_path.stat().st_size
    before_state = inspected_before.get("State") or {}
    before_started_at = str(before_state.get("StartedAt") or "")
    _checked(
        ["docker", "restart", "--time", "30", identity["container_id"]],
        timeout=90,
    )
    inspected_after = _inspect_container(identity["container_id"])
    after_state = inspected_after.get("State") or {}
    after_started_at = str(after_state.get("StartedAt") or "")
    if (
        str(inspected_after.get("Id") or "") != identity["container_id"]
        or str(inspected_after.get("Image") or "")
        != identity["container_image_id"]
        or after_state.get("Running") is not True
        or not after_started_at
        or after_started_at == before_started_at
    ):
        raise RuntimeError("Amaru recovery restart did not preserve a running identity")
    return {
        "schema_version": 1,
        "status": "restarted",
        "identity": identity,
        "log_start_offset": log_start_offset,
        "started_at_before": before_started_at,
        "started_at_after": after_started_at,
        "restart_count_before": int(inspected_before.get("RestartCount") or 0),
        "restart_count_after": int(inspected_after.get("RestartCount") or 0),
    }


def _wait_for_fresh_peer_connection(
    *,
    target: Mapping[str, Any],
    expected_peer: str,
    prior_connection_id: int,
    log_start_offset: int,
    timeout_seconds: float = 60.0,
) -> dict[str, Any]:
    log_path = Path(str(target.get("log_path") or ""))
    started = time.monotonic()
    while True:
        try:
            end_offset = log_path.stat().st_size
            length = end_offset - log_start_offset
            if length > _MAX_P2P_TRACE_BYTES:
                raise RuntimeError("fresh peer connection log slice is oversized")
            with log_path.open("rb") as stream:
                stream.seek(log_start_offset)
                log_slice = stream.read(max(0, length))
        except OSError as exc:
            raise RuntimeError("fresh peer connection log is unavailable") from exc
        offset = log_start_offset
        for raw_line in log_slice.splitlines(keepends=True):
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            if (
                "manager.peer.connected" in line
                and _trace_field(line, "peer") == expected_peer
            ):
                connection = _trace_connection(line)
                if connection is not None and connection != prior_connection_id:
                    return {
                        "schema_version": 1,
                        "status": "connected",
                        "peer": expected_peer,
                        "conn_id": connection,
                        "prior_conn_id": prior_connection_id,
                        "log_start_offset": log_start_offset,
                        "event_offset": offset,
                        "event_line": line[:4096],
                        "event_line_sha256": hashlib.sha256(raw_line).hexdigest(),
                    }
            offset += len(raw_line)
        if time.monotonic() - started >= timeout_seconds:
            raise RuntimeError("Amaru did not establish a fresh peer connection")
        time.sleep(0.25)


def _wait_for_reference_tip_progress(
    *,
    cli: str,
    socket_path: str,
    network_magic: int,
    initial_tip: Mapping[str, Any],
    evidence_path: Path,
    timeout_seconds: float = 360.0,
) -> dict[str, Any]:
    initial_block = initial_tip.get("block")
    initial_hash = str(initial_tip.get("hash") or "")
    if (
        isinstance(initial_block, bool)
        or not isinstance(initial_block, int)
        or re.fullmatch(r"[0-9a-f]{64}", initial_hash) is None
    ):
        raise RuntimeError("reference progress baseline is invalid")
    started = time.monotonic()
    latest = dict(initial_tip)
    while time.monotonic() - started < timeout_seconds:
        latest = _query_reference_tip(
            cli=cli, socket_path=socket_path, network_magic=network_magic
        )
        block = latest.get("block")
        header_hash = str(latest.get("hash") or "")
        if (
            isinstance(block, int)
            and not isinstance(block, bool)
            and block > initial_block
            and re.fullmatch(r"[0-9a-f]{64}", header_hash) is not None
            and header_hash != initial_hash
        ):
            evidence = {
                "schema_version": 1,
                "status": "progressed",
                "initial_tip": dict(initial_tip),
                "progressed_tip": latest,
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }
            evidence_path.write_text(
                json.dumps(evidence, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            return evidence
        time.sleep(1)
    evidence = {
        "schema_version": 1,
        "status": "not-progressed",
        "initial_tip": dict(initial_tip),
        "last_tip": latest,
        "timeout_seconds": timeout_seconds,
    }
    evidence_path.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    raise RuntimeError("reference did not progress after the Amaru restart")


def _recover_post_control_after_cycle(
    *,
    trace_evidence: Mapping[str, Any],
    runtime: dict[str, Any],
    target: dict[str, Any],
    reference: dict[str, Any],
    payload: bytes,
    transaction_id: str,
    target_propagation_gate: dict[str, Any],
    cli: str,
    socket_path: str,
    network_magic: int,
    address: str,
    output_dir: Path,
) -> dict[str, Any]:
    if (
        trace_evidence.get("status") != "exact-cycle-observed"
        or trace_evidence.get("restart_eligible") is not True
    ):
        return {
            "schema_version": 1,
            "status": "not-attempted",
            "reason": "exact transaction-bound P2P cycle was not established",
        }
    connection = trace_evidence.get("connection") or {}
    prior_connection_id = connection.get("reconnected_connection_id")
    if isinstance(prior_connection_id, bool) or not isinstance(
        prior_connection_id, int
    ):
        raise RuntimeError("P2P recovery connection identity is unavailable")
    peer_identity = _reference_peer_identity(reference)
    output_dir.mkdir(parents=True, exist_ok=True)
    restart = _restart_owned_amaru(runtime=runtime, target=target)
    (output_dir / "restart.json").write_text(
        json.dumps(restart, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    reconnect = _wait_for_fresh_peer_connection(
        target=target,
        expected_peer=str(peer_identity["peer"]),
        prior_connection_id=prior_connection_id,
        log_start_offset=int(restart["log_start_offset"]),
    )
    (output_dir / "fresh-peer-connection.json").write_text(
        json.dumps(reconnect, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    reference_tip = _query_reference_tip(
        cli=cli, socket_path=socket_path, network_magic=network_magic
    )
    reference_progress = _wait_for_reference_tip_progress(
        cli=cli,
        socket_path=socket_path,
        network_magic=network_magic,
        initial_tip=reference_tip,
        evidence_path=output_dir / "post-restart-reference-progress.json",
    )
    progressed_tip = dict(reference_progress["progressed_tip"])
    sync = _wait_for_target_adoption(
        log_path=Path(str(target.get("log_path") or "")),
        expected_tip=progressed_tip,
        evidence_path=output_dir / "post-restart-sync.json",
        minimum_log_offset=int(restart["log_start_offset"]),
    )
    response = _submit_amaru_with_admission_log(
        target=target,
        payload=payload,
        transaction_id=transaction_id,
    )
    if response.get("classification") != "accepted":
        raise RuntimeError("post-restart exact valid control was not admitted by Amaru")
    initial_tip = _query_reference_tip(
        cli=cli, socket_path=socket_path, network_magic=network_magic
    )
    inclusion_path = output_dir / "reference-inclusion.json"
    _wait_for_reference_inclusion(
        cli=cli,
        socket_path=socket_path,
        network_magic=network_magic,
        address=address,
        destination=output_dir / "included-utxo.json",
        transaction_id=transaction_id,
        initial_tip=initial_tip,
        evidence_path=inclusion_path,
        target_propagation_gate=target_propagation_gate,
        terminal_progress_blocks=_POST_MUTANT_TERMINAL_PROGRESS_BLOCKS,
    )
    included_tip = _terminal_inclusion_tip(
        inclusion_path, label="post-restart valid traffic control"
    )
    adoption = _wait_for_target_adoption(
        log_path=Path(str(target.get("log_path") or "")),
        expected_tip=included_tip,
        evidence_path=output_dir / "target-adoption.json",
        minimum_log_offset=int(restart["log_start_offset"]),
    )
    return {
        "schema_version": 1,
        "status": "recovered",
        "transaction_id": transaction_id,
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "payload_length": len(payload),
        "reference_peer_identity": peer_identity,
        "restart": restart,
        "fresh_peer_connection": reconnect,
        "post_restart_reference_progress": reference_progress,
        "post_restart_sync": sync,
        "target_resubmission": response,
        "reference_inclusion_path": str(inclusion_path.relative_to(output_dir)),
        "target_adoption": adoption,
    }


def prepare_governance_anchor(
    *, output_dir: Path, artifact_base_url: str
) -> dict[str, Any]:
    """Write and address deterministic proposal metadata through the run artifact route."""

    parsed = urllib.parse.urlsplit(artifact_base_url.strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("governance artifact base URL is invalid")
    if output_dir.parent.name != "outputs":
        raise RuntimeError("governance output directory is outside the run outputs")
    run_dir = output_dir.parent.parent
    run_id = run_dir.name
    if not run_id or run_id in {".", ".."} or "/" in run_id:
        raise RuntimeError("governance run identity is invalid")
    anchor_path = output_dir.parent / "gov-a.json"
    anchor_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = anchor_path.with_suffix(".json.tmp")
    temporary.write_bytes(ANCHOR_BYTES)
    temporary.replace(anchor_path)
    relative_path = anchor_path.relative_to(run_dir).as_posix()
    query = urllib.parse.urlencode({"path": relative_path})
    url = (
        artifact_base_url.rstrip("/")
        + f"/runs/{urllib.parse.quote(run_id, safe='')}/output?{query}"
    )
    if len(url.encode("utf-8")) > 128:
        raise RuntimeError("governance anchor URL exceeds the ledger limit")
    return {
        "bytes": ANCHOR_BYTES,
        "hash": hashlib.blake2b(ANCHOR_BYTES, digest_size=32).hexdigest(),
        "path": str(anchor_path),
        "relative_path": relative_path,
        "url": url,
    }


def admission_funding_inputs(setup_transaction_id: str) -> dict[str, dict[str, str]]:
    if len(setup_transaction_id) != 64 or any(
        value not in "0123456789abcdef" for value in setup_transaction_id
    ):
        raise ValueError("setup transaction id must be lowercase hex")
    return {
        CONTROL_CASE: {
            "target": f"{setup_transaction_id}#0",
            "reference": f"{setup_transaction_id}#2",
        },
        ZERO_CASE: {"shared": f"{setup_transaction_id}#4"},
        POST_CONTROL_CASE: {"target": f"{setup_transaction_id}#7"},
    }


def admission_collateral_inputs(setup_transaction_id: str) -> dict[str, dict[str, str]]:
    if len(setup_transaction_id) != 64 or any(
        value not in "0123456789abcdef" for value in setup_transaction_id
    ):
        raise ValueError("setup transaction id must be lowercase hex")
    return {
        CONTROL_CASE: {
            "target": f"{setup_transaction_id}#1",
            "reference": f"{setup_transaction_id}#3",
        },
        ZERO_CASE: {"shared": f"{setup_transaction_id}#5"},
    }


def admission_registration_input(setup_transaction_id: str) -> str:
    if len(setup_transaction_id) != 64 or any(
        value not in "0123456789abcdef" for value in setup_transaction_id
    ):
        raise ValueError("setup transaction id must be lowercase hex")
    return f"{setup_transaction_id}#6"


def admission_funding_amounts(
    *,
    proposal_lovelace: int,
    collateral_lovelace: int,
    registration_lovelace: int,
    post_control_lovelace: int,
) -> tuple[int, ...]:
    return (
        proposal_lovelace,
        collateral_lovelace,
        proposal_lovelace,
        collateral_lovelace,
        proposal_lovelace,
        collateral_lovelace,
        registration_lovelace,
        post_control_lovelace,
    )


def _control_submission_specs(
    assigned: dict[str, dict[str, str]],
    collateral: dict[str, dict[str, str]],
) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (
            role,
            assigned[CONTROL_CASE][role],
            collateral[CONTROL_CASE][role],
        )
        for role in ("target", "reference")
    )


def _allocated_references(values: dict[str, Any]) -> list[str]:
    references: list[str] = []
    for value in values.values():
        if isinstance(value, dict):
            references.extend(str(reference) for reference in value.values())
        else:
            references.append(str(value))
    return references


def validate_admission_funding_outputs(
    utxos: dict[str, Any],
    *,
    assigned: dict[str, Any],
    expected_lovelace: int,
    collateral: dict[str, Any] | None = None,
    expected_collateral_lovelace: int | None = None,
    registration_input: str | None = None,
    expected_registration_lovelace: int | None = None,
    post_control_input: str | None = None,
    expected_post_control_lovelace: int | None = None,
) -> None:
    funding_references = _allocated_references(assigned)
    collateral_references = _allocated_references(collateral or {})
    allocated_references = funding_references + collateral_references
    if len(allocated_references) != len(set(allocated_references)):
        raise RuntimeError("governance funding and collateral inputs are not isolated")
    for reference in funding_references:
        if reference == post_control_input:
            continue
        entry = utxos.get(reference)
        value = entry.get("value") if isinstance(entry, dict) else None
        lovelace = value.get("lovelace") if isinstance(value, dict) else None
        if lovelace != expected_lovelace:
            raise RuntimeError(
                "governance case lacks its exact independent funding output"
            )
    if collateral is not None:
        if expected_collateral_lovelace is None:
            raise RuntimeError("governance collateral amount is not configured")
        for reference in collateral_references:
            entry = utxos.get(reference)
            value = entry.get("value") if isinstance(entry, dict) else None
            lovelace = value.get("lovelace") if isinstance(value, dict) else None
            if lovelace != expected_collateral_lovelace:
                raise RuntimeError(
                    "governance case lacks its exact independent collateral output"
                )
    if registration_input is not None:
        if expected_registration_lovelace is None:
            raise RuntimeError("governance registration amount is not configured")
        entry = utxos.get(registration_input)
        value = entry.get("value") if isinstance(entry, dict) else None
        lovelace = value.get("lovelace") if isinstance(value, dict) else None
        if lovelace != expected_registration_lovelace:
            raise RuntimeError(
                "governance registration lacks its exact independent funding output"
            )
        if registration_input in {
            *funding_references,
            *collateral_references,
        }:
            raise RuntimeError("governance registration funding is not isolated")
    if post_control_input is not None:
        if expected_post_control_lovelace is None:
            raise RuntimeError("post-mutant control amount is not configured")
        entry = utxos.get(post_control_input)
        value = entry.get("value") if isinstance(entry, dict) else None
        lovelace = value.get("lovelace") if isinstance(value, dict) else None
        if lovelace != expected_post_control_lovelace:
            raise RuntimeError(
                "post-mutant control lacks its exact independent funding output"
            )
        if post_control_input not in funding_references:
            raise RuntimeError("post-mutant control funding is not assigned")


def _control_ratio(protocol_parameters: dict[str, Any]) -> Fraction:
    value = protocol_parameters.get("minFeeRefScriptCostPerByte")
    if isinstance(value, bool):
        raise RuntimeError("live minFeeRefScriptCostPerByte is invalid")
    try:
        ratio = Fraction(str(value))
    except (ValueError, ZeroDivisionError) as exc:
        raise RuntimeError("live minFeeRefScriptCostPerByte is invalid") from exc
    if ratio < 0:
        raise RuntimeError("live minFeeRefScriptCostPerByte is negative")
    return ratio


def _require_fresh_pparam_root(gov_state: dict[str, Any]) -> None:
    ratify = gov_state.get("nextRatifyState")
    if not isinstance(ratify, dict):
        raise RuntimeError("live governance state omits previous action identities")
    roots = []
    for field in ("nextEnactState", "enactState"):
        if field not in ratify:
            continue
        enact = ratify[field]
        if not isinstance(enact, dict):
            raise RuntimeError("live governance state has malformed enactment state")
        previous = enact.get("prevGovActionIds")
        if not isinstance(previous, dict) or "PParamUpdate" not in previous:
            raise RuntimeError("live governance state omits the parameter-update root")
        roots.append(previous)
    if not roots:
        raise RuntimeError("live governance state omits previous action identities")
    if any(previous != roots[0] for previous in roots[1:]):
        raise RuntimeError("live governance enactment states are inconsistent")
    if roots[0]["PParamUpdate"] is not None:
        raise RuntimeError(
            "governance admission fixture requires a fresh parameter-update root"
        )


def _guardrails_witness_arguments(
    *, active_script_hash: str | None, script_path: Path, collateral_input: str
) -> list[str]:
    if active_script_hash is None:
        raise RuntimeError("governance fixture requires active constitution guardrails")
    if not script_path.is_file():
        raise RuntimeError("staged governance guardrails fixture is incomplete")
    return [
        "--proposal-script-file",
        str(script_path),
        "--proposal-redeemer-value",
        "{}",
        "--tx-in-collateral",
        collateral_input,
    ]


def _proposal_build_command(
    *,
    cli: str,
    socket_path: str,
    network_magic: int,
    funding_input: str,
    change_address: str,
    proposal_path: Path,
    active_script_hash: str,
    guardrails_script_path: Path,
    collateral_input: str,
    output_path: Path,
) -> list[str]:
    """Build an already-registered proposal without registration certificates."""

    return [
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
        "--change-address",
        change_address,
        "--proposal-file",
        str(proposal_path),
        *_guardrails_witness_arguments(
            active_script_hash=active_script_hash,
            script_path=guardrails_script_path,
            collateral_input=collateral_input,
        ),
        "--out-file",
        str(output_path),
    ]


def _governance_action_command(
    *,
    cli: str,
    governance_action_deposit: int,
    stake_vkey: Path,
    anchor_url: str,
    anchor_hash: str,
    guardrails_hash: str,
    control_ratio: Fraction,
    action_path: Path,
) -> list[str]:
    return [
        cli,
        "conway",
        "governance",
        "action",
        "create-protocol-parameters-update",
        "--testnet",
        "--governance-action-deposit",
        str(governance_action_deposit),
        "--deposit-return-stake-verification-key-file",
        str(stake_vkey),
        "--anchor-url",
        anchor_url,
        "--anchor-data-hash",
        anchor_hash,
        "--check-anchor-data",
        "--constitution-script-hash",
        guardrails_hash,
        "--ref-script-cost-per-byte",
        f"{control_ratio.numerator}/{control_ratio.denominator}",
        "--out-file",
        str(action_path),
    ]


def _tagged_items(value: object) -> list[Any]:
    if isinstance(value, cbor2.CBORTag):
        return list(value.value)
    return list(value) if isinstance(value, (list, tuple, set)) else []


def mutate_min_fee_ref_script_cost(body_raw: bytes) -> bytes:
    """Replace only the positive denominator of parameter-update key 33 with zero."""

    body = cbor2.loads(body_raw)
    if not isinstance(body, dict) or cbor2.dumps(body) != body_raw:
        raise ValueError("governance transaction body is not byte-stable canonical CBOR")
    proposals = body.get(20)
    proposal_items = _tagged_items(proposals)
    if len(proposal_items) != 1 or not isinstance(proposal_items[0], (list, tuple)):
        raise ValueError("governance body must contain one proposal procedure")
    proposal = list(proposal_items[0])
    if len(proposal) != 4 or not isinstance(proposal[2], (list, tuple)):
        raise ValueError("governance proposal procedure is malformed")
    action = list(proposal[2])
    if len(action) != 4 or action[0] != 0 or not isinstance(action[2], Mapping):
        raise ValueError("proposal is not a protocol-parameter update")
    update = dict(action[2])
    ratio = update.get(PARAMETER_KEY)
    if not isinstance(ratio, Fraction):
        raise ValueError("parameter update lacks tagged minFeeRefScriptCostPerByte")
    if ratio.numerator < 0 or ratio.denominator <= 0:
        raise ValueError("parameter update ratio is not a valid nonnegative interval")
    encoded_key = cbor2.dumps(PARAMETER_KEY)
    encoded_ratio = cbor2.dumps(ratio)
    marker = encoded_key + encoded_ratio
    if body_raw.count(marker) != 1:
        raise ValueError("parameter-update ratio is not uniquely encoded in the body")
    invalid_ratio = cbor2.dumps(
        cbor2.CBORTag(30, [ratio.numerator, 0])
    )
    return body_raw.replace(marker, encoded_key + invalid_ratio)


def _private_key(path: Path) -> Ed25519PrivateKey:
    envelope = json.loads(path.read_text(encoding="utf-8"))
    encoded = bytes.fromhex(str(envelope.get("cborHex") or ""))
    if len(encoded) != 34 or encoded[:2] != b"\x58\x20":
        raise RuntimeError(f"unsupported signing-key envelope: {path.name}")
    return Ed25519PrivateKey.from_private_bytes(encoded[2:])


def resign_transaction_body(
    signed_template: bytes, mutated_body: bytes, signing_key_paths: list[Path]
) -> bytes:
    """Sign the final body and preserve every non-vkey witness and transaction tail."""

    _old_body, witness_raw, tail = split_transaction(signed_template)
    witnesses = cbor2.loads(witness_raw)
    if not isinstance(witnesses, dict) or 0 not in witnesses:
        raise ValueError("signed template has no vkey witness set")
    original_vkeys = witnesses[0]
    tagged = isinstance(original_vkeys, cbor2.CBORTag)
    keys = [_private_key(path) for path in signing_key_paths]
    body_hash = hashlib.blake2b(mutated_body, digest_size=32).digest()
    generated = [
        [
            key.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            ),
            key.sign(body_hash),
        ]
        for key in keys
    ]
    witnesses = dict(witnesses)
    witnesses[0] = (
        cbor2.CBORTag(original_vkeys.tag, generated) if tagged else generated
    )
    return b"\x84" + mutated_body + cbor2.dumps(witnesses) + tail


def _assess_matched_governance_control(
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    by_case = {str(record.get("case_id") or ""): record for record in records}
    control = by_case.get(CONTROL_CASE) or {}
    middle = by_case.get(MATCHED_MIDDLE_CASE) or {}
    post_control = by_case.get(POST_CONTROL_CASE) or {}
    inputs = [
        str((control.get(role) or {}).get("funding_input") or "")
        for role in ("target", "reference")
    ] + [
        str(middle.get("funding_input") or ""),
        str((post_control.get("target") or {}).get("funding_input") or ""),
    ]
    funding_isolated = (
        set(by_case) == {CONTROL_CASE, MATCHED_MIDDLE_CASE, POST_CONTROL_CASE}
        and all(inputs)
        and len(inputs) == len(set(inputs))
    )
    valid_control = _valid_control_complete(control)
    middle_payload = str(middle.get("payload_sha256") or "")
    middle_funding = str(middle.get("funding_input") or "")
    middle_collateral = str(middle.get("collateral_input") or "")
    middle_roles = [middle.get(role) or {} for role in ("reference", "target")]
    middle_payloads_match = bool(_lower_hex(middle_payload, 64)) and all(
        (middle.get(role) or {}).get("payload_sha256") == middle_payload
        for role in ("reference", "target")
    )

    def matched_identity(field: str) -> bool:
        nested = [str(role.get(field) or "") for role in middle_roles]
        if any(nested):
            return (
                all(_lower_hex(value, 64) for value in nested)
                and len(set(nested)) == 1
            )
        return bool(_lower_hex(middle.get(field), 64))

    middle_complete = (
        middle_payloads_match
        and all(role.get("classification") == "accepted" for role in middle_roles)
        and all(role.get("included") is True for role in middle_roles)
        and all(role.get("target_adopted") is True for role in middle_roles)
        and middle_roles[1].get("local_mempool_admission_observed") is True
        and matched_identity("transaction_id")
        and matched_identity("body_sha256")
        and middle_funding != middle_collateral
        and _lower_hex(middle_funding.partition("#")[0], 64)
        and middle_funding.partition("#")[1] == "#"
        and middle_funding.partition("#")[2].isdigit()
        and _lower_hex(middle_collateral.partition("#")[0], 64)
        and middle_collateral.partition("#")[1] == "#"
        and middle_collateral.partition("#")[2].isdigit()
    )
    post_control_complete = _post_mutant_control_complete(post_control)
    complete = (
        funding_isolated
        and valid_control
        and middle_complete
        and post_control_complete
    )
    post_target = post_control.get("target") or {}
    post_propagation = post_target.get("propagation_observation") or {}
    direct_confirmation = post_control.get("direct_reference_confirmation") or {}
    if not funding_isolated:
        reason = "matched governance inputs are not independently funded"
    elif not valid_control:
        reason = _control_failure_reason(control)
    elif not middle_complete:
        reason = "matched nonzero middle control is incomplete"
    elif not post_control_complete:
        reason = "matched post-middle valid traffic control is incomplete"
    else:
        reason = "matched nonzero governance negative-control evidence is complete"
    return {
        "complete": complete,
        "experiment_mode": MATCHED_CONTROL_MODE,
        "valid_control_present": valid_control,
        "funding_inputs_isolated": funding_isolated,
        "mutation_payloads_match": middle_payloads_match,
        "middle_payloads_match": middle_payloads_match,
        "matched_middle_control_complete": middle_complete,
        "post_mutant_valid_control_complete": post_control_complete,
        "post_valid_control_complete": post_control_complete,
        "post_target_path_included": post_target.get("included") is True,
        "post_target_path_absent_after_progress": (
            post_propagation.get("status")
            == "not-included-after-reference-progress"
        ),
        "post_direct_reference_confirmation_used": bool(direct_confirmation),
        "p2p_relay_observed": None,
        "relay_rejection_observed": None,
        "outcomes_conclusive": complete,
        "enactment_observed": False,
        "reason": reason,
    }


def assess_governance_admission(
    records: list[dict[str, Any]],
    *,
    experiment_mode: str = ZERO_DENOMINATOR_MODE,
) -> dict[str, Any]:
    if experiment_mode == MATCHED_CONTROL_MODE:
        return _assess_matched_governance_control(records)
    if experiment_mode != ZERO_DENOMINATOR_MODE:
        raise RuntimeError("unsupported governance admission experiment mode")
    by_case = {str(record.get("case_id") or ""): record for record in records}
    control = by_case.get(CONTROL_CASE) or {}
    mutant = by_case.get(ZERO_CASE) or {}
    post_control = by_case.get(POST_CONTROL_CASE) or {}
    inputs = [
        str((control.get(role) or {}).get("funding_input") or "")
        for role in ("target", "reference")
    ] + [
        str(mutant.get("funding_input") or ""),
        str((post_control.get("target") or {}).get("funding_input") or ""),
    ]
    funding_isolated = (
        set(by_case) == {CONTROL_CASE, ZERO_CASE, POST_CONTROL_CASE}
        and all(inputs)
        and len(inputs) == len(set(inputs))
    )
    valid_control = _valid_control_complete(control)
    mutant_roles = [mutant.get(role) or {} for role in ("target", "reference")]
    mutant_payload = str(mutant.get("payload_sha256") or "")
    mutation_payloads_match = bool(mutant_payload) and all(
        item.get("payload_sha256") == mutant_payload for item in mutant_roles
    )
    reference_evidence = mutant.get("reference") or {}
    target_evidence = mutant.get("target") or {}
    propagation = mutant.get("propagation_observation") or {}
    direct_reference_decoder_reject = (
        reference_evidence.get("classification") == "decode_reject"
        and isinstance(reference_evidence.get("reference_decoder_evidence"), dict)
    )
    target_local_admission = (
        target_evidence.get("classification") == "accepted"
        and target_evidence.get("local_mempool_admission_observed") is True
    )
    propagation_absence = (
        propagation.get("status") == "not-included-after-reference-progress"
        and propagation.get("p2p_relay_observed") is None
        and propagation.get("relay_rejection_observed") is None
    )
    post_control_complete = _post_mutant_control_complete(post_control)
    p2p_trace = post_control.get("p2p_trace") or {}
    p2p_relay_observed = (
        True if p2p_trace.get("status") == "exact-cycle-observed" else None
    )
    conclusive = (
        direct_reference_decoder_reject
        and target_local_admission
        and propagation_absence
        and post_control_complete
    )
    complete = (
        funding_isolated
        and valid_control
        and mutation_payloads_match
        and conclusive
    )
    if not funding_isolated:
        reason = "governance admission inputs are not independently funded"
    elif not valid_control:
        reason = _control_failure_reason(control)
    elif not mutation_payloads_match:
        reason = "zero-denominator submissions do not share exact payload bytes"
    elif not direct_reference_decoder_reject:
        reason = "zero-denominator direct reference decoder evidence is unavailable"
    elif not target_local_admission:
        reason = "zero-denominator exact Amaru local admission is unavailable"
    elif not propagation_absence:
        reason = "zero-denominator propagation absence is unavailable or inconclusive"
    elif not post_control_complete:
        reason = "post-mutant valid traffic control is incomplete"
    elif not conclusive:
        reason = "zero-denominator outcome is unavailable or inconclusive"
    else:
        reason = "bounded zero-denominator impact follow-up evidence is complete"
    return {
        "complete": complete,
        "experiment_mode": ZERO_DENOMINATOR_MODE,
        "valid_control_present": valid_control,
        "funding_inputs_isolated": funding_isolated,
        "mutation_payloads_match": mutation_payloads_match,
        "direct_reference_decoder_reject": direct_reference_decoder_reject,
        "target_local_admission_observed": target_local_admission,
        "propagation_absence_observed": propagation_absence,
        "post_mutant_valid_control_complete": post_control_complete,
        "p2p_relay_observed": p2p_relay_observed,
        "relay_rejection_observed": None,
        "outcomes_conclusive": conclusive,
        "enactment_observed": False,
        "reason": reason,
    }


def _valid_control_complete(record: dict[str, Any]) -> bool:
    return all(
        _control_role_identity_complete(record.get(role) or {})
        and (record.get(role) or {}).get("classification") == "accepted"
        and (record.get(role) or {}).get("included") is True
        and (record.get(role) or {}).get("target_adopted") is True
        for role in ("target", "reference")
    )


def _post_mutant_control_complete(record: dict[str, Any]) -> bool:
    evidence = record.get("target") or {}
    funding_input = str(evidence.get("funding_input") or "")
    identity_complete = (
        evidence.get("classification") == "accepted"
        and evidence.get("local_mempool_admission_observed") is True
        and _lower_hex(evidence.get("transaction_id"), 64)
        and _lower_hex(evidence.get("body_sha256"), 64)
        and _lower_hex(evidence.get("payload_sha256"), 64)
        and _lower_hex(funding_input.partition("#")[0], 64)
        and funding_input.partition("#")[1] == "#"
        and funding_input.partition("#")[2].isdigit()
    )
    if not identity_complete:
        return False
    if evidence.get("included") is True and evidence.get("target_adopted") is True:
        return True
    target_observation = evidence.get("propagation_observation") or {}
    direct = record.get("direct_reference_confirmation") or {}
    recovery = record.get("conditional_recovery") or {}
    recovered = (
        recovery.get("status") == "recovered"
        and recovery.get("payload_sha256") == evidence.get("payload_sha256")
        and recovery.get("transaction_id") == evidence.get("transaction_id")
        and (recovery.get("target_resubmission") or {}).get("classification")
        == "accepted"
        and (recovery.get("target_adoption") or {}).get("status") == "adopted"
    )
    return (
        target_observation.get("status")
        == "not-included-after-reference-progress"
        and target_observation.get("terminal_progress_block_threshold")
        == _POST_MUTANT_TERMINAL_PROGRESS_BLOCKS
        and (
            recovered
            or (
                direct.get("target_observation_status")
                == "not-included-after-reference-progress"
                and direct.get("target_observation_terminal_progress_blocks")
                == _POST_MUTANT_TERMINAL_PROGRESS_BLOCKS
                and direct.get("classification") == "accepted"
                and direct.get("included") is True
                and direct.get("identity_match") is True
                and direct.get("payload_identity_match") is True
                and direct.get("payload_sha256") == evidence.get("payload_sha256")
                and direct.get("transaction_id") == evidence.get("transaction_id")
            )
        )
    )


def _lower_hex(value: object, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def _control_role_identity_complete(evidence: dict[str, Any]) -> bool:
    funding_input = str(evidence.get("funding_input") or "")
    collateral_input = str(evidence.get("collateral_input") or "")
    return (
        _lower_hex(evidence.get("transaction_id"), 64)
        and _lower_hex(evidence.get("body_sha256"), 64)
        and _lower_hex(evidence.get("payload_sha256"), 64)
        and funding_input != collateral_input
        and _lower_hex(funding_input.partition("#")[0], 64)
        and funding_input.partition("#")[1] == "#"
        and funding_input.partition("#")[2].isdigit()
        and _lower_hex(collateral_input.partition("#")[0], 64)
        and collateral_input.partition("#")[1] == "#"
        and collateral_input.partition("#")[2].isdigit()
    )


def _control_failure_reason(record: dict[str, Any]) -> str:
    for role in ("target", "reference"):
        evidence = record.get(role) or {}
        if not _control_role_identity_complete(evidence):
            return (
                f"nonzero governance {role} control lacks its exact "
                "transaction identity"
            )
        classification = str(evidence.get("classification") or "unavailable")
        if classification != "accepted":
            return (
                f"nonzero governance {role} control was not accepted "
                f"({classification})"
            )
        if evidence.get("included") is not True:
            return (
                f"nonzero governance {role} control was accepted but lacks exact "
                "transaction inclusion evidence"
            )
        if evidence.get("target_adopted") is not True:
            return (
                f"nonzero governance {role} control lacks exact target "
                "adoption evidence"
            )
    return "nonzero governance controls are complete"


def _terminal_inclusion_tip(evidence_path: Path, *, label: str) -> dict[str, Any]:
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    terminal = [
        item
        for item in evidence.get("observations", [])
        if item.get("included") is True and isinstance(item.get("tip"), dict)
    ]
    if not terminal:
        raise RuntimeError(f"{label} inclusion omitted its terminal tip")
    return dict(terminal[-1]["tip"])


def _guardrails_script(gov_state: dict[str, Any]) -> str | None:
    constitution = gov_state.get("constitution")
    if not isinstance(constitution, dict) or "script" not in constitution:
        raise RuntimeError("live governance state omits the constitution guardrails field")
    value = constitution.get("script")
    if value is None:
        return None
    if isinstance(value, str) and len(value) == 56:
        return value
    raise RuntimeError("live constitution guardrails identity is unsupported")


def _verified_guardrails_identity(
    *, runtime: dict[str, Any], gov_state: dict[str, Any]
) -> str:
    active = _guardrails_script(gov_state)
    fixture = runtime.get("governance_guardrails_fixture")
    if not isinstance(fixture, dict):
        raise RuntimeError("runtime omits the governance guardrails fixture receipt")
    if (
        fixture.get("fixture") != "always-succeeds-v3"
        or fixture.get("effective_script_hash") != active
        or fixture.get("script_sha256")
        != hashlib.sha256(GUARDRAILS_SCRIPT.read_bytes()).hexdigest()
    ):
        raise RuntimeError(
            "active constitution guardrails do not match the audited runtime fixture"
        )
    if active is None:
        raise RuntimeError("governance fixture requires active constitution guardrails")
    return active


def _transaction_id(payload: bytes) -> str:
    body, _witnesses, _tail = split_transaction(payload)
    return hashlib.blake2b(body, digest_size=32).hexdigest()


def _transaction_id_from_body(body: bytes) -> str:
    return hashlib.blake2b(body, digest_size=32).hexdigest()


@contextmanager
def _controller_generated_stake_keys(
    *, controller_cli: str, controller_key_root: Path, case_id: str
):
    """Generate ephemeral governance keys outside the read-only CLI container."""

    with tempfile.TemporaryDirectory(
        prefix=f".governance-{case_id}-", dir=controller_key_root
    ) as private_key_text:
        private_key_dir = Path(private_key_text)
        stake_vkey = private_key_dir / "return.vkey"
        stake_skey = private_key_dir / "return.skey"
        _checked(
            [
                controller_cli,
                "conway",
                "stake-address",
                "key-gen",
                "--verification-key-file",
                str(stake_vkey),
                "--signing-key-file",
                str(stake_skey),
            ]
        )
        if not stake_vkey.is_file() or not stake_skey.is_file():
            raise RuntimeError("controller-native stake key generation omitted its output")
        yield stake_vkey, stake_skey


def _registered_stake_state(value: object, *, expected_address: str) -> dict[str, Any]:
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise RuntimeError("governance return credential is not registered on-chain")
    state = dict(value[0])
    if state.get("address") != expected_address:
        raise RuntimeError("governance return credential state has the wrong address")
    return state


@contextmanager
def _registered_return_credentials(
    *,
    cli: str,
    controller_cli: str,
    controller_key_root: Path,
    key_root: Path,
    socket_path: str,
    network_magic: int,
    change_address: str,
    stake_deposit: int,
    registration_input: str,
    output_dir: Path,
    target_log_path: Path,
):
    """Register both ephemeral return credentials before proposal construction."""

    controller_key_root.mkdir(parents=True, exist_ok=True)
    registration_dir = output_dir / REGISTRATION_PREP
    registration_dir.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        credentials: dict[str, dict[str, Any]] = {}
        for case_id in (CONTROL_CASE, ZERO_CASE):
            private_vkey, private_skey = stack.enter_context(
                _controller_generated_stake_keys(
                    controller_cli=controller_cli,
                    controller_key_root=controller_key_root,
                    case_id=case_id,
                )
            )
            case_dir = output_dir / case_id
            case_dir.mkdir(parents=True, exist_ok=True)
            public_vkey = case_dir / "return.vkey"
            certificate = case_dir / "return.cert"
            shutil.copyfile(private_vkey, public_vkey)
            stake_address = _checked(
                [
                    cli,
                    "conway",
                    "stake-address",
                    "build",
                    "--stake-verification-key-file",
                    str(public_vkey),
                    "--testnet-magic",
                    str(network_magic),
                ]
            ).stdout.strip()
            if not stake_address:
                raise RuntimeError("governance return credential address is unavailable")
            _checked(
                [
                    cli,
                    "conway",
                    "stake-address",
                    "registration-certificate",
                    "--stake-verification-key-file",
                    str(public_vkey),
                    "--key-reg-deposit-amt",
                    str(stake_deposit),
                    "--out-file",
                    str(certificate),
                ]
            )
            credentials[case_id] = {
                "stake_vkey": public_vkey,
                "stake_skey": private_skey,
                "certificate": certificate,
                "stake_address": stake_address,
            }

        body_path = registration_dir / "transaction.body"
        signed_path = registration_dir / "transaction.signed"
        build_command = [
            cli,
            "conway",
            "transaction",
            "build",
            "--socket-path",
            socket_path,
            "--testnet-magic",
            str(network_magic),
            "--tx-in",
            registration_input,
            "--change-address",
            change_address,
        ]
        for case_id in (CONTROL_CASE, ZERO_CASE):
            build_command.extend(
                ["--certificate-file", str(credentials[case_id]["certificate"])]
            )
        build_command.extend(["--witness-override", "3", "--out-file", str(body_path)])
        _checked(build_command)
        sign_command = [
            cli,
            "conway",
            "transaction",
            "sign",
            "--tx-body-file",
            str(body_path),
            "--signing-key-file",
            str(key_root / "utxo.skey"),
        ]
        for case_id in (CONTROL_CASE, ZERO_CASE):
            sign_command.extend(
                ["--signing-key-file", str(credentials[case_id]["stake_skey"])]
            )
        sign_command.extend(
            [
                "--testnet-magic",
                str(network_magic),
                "--out-file",
                str(signed_path),
            ]
        )
        _checked(sign_command)
        _template, payload = _envelope(signed_path)
        transaction_id = _transaction_id(payload)
        initial_tip = _query_reference_tip(
            cli=cli, socket_path=socket_path, network_magic=network_magic
        )
        _checked(
            [
                cli,
                "conway",
                "transaction",
                "submit",
                "--tx-file",
                str(signed_path),
                "--socket-path",
                socket_path,
                "--testnet-magic",
                str(network_magic),
            ]
        )
        _wait_for_reference_inclusion(
            cli=cli,
            socket_path=socket_path,
            network_magic=network_magic,
            address=change_address,
            destination=registration_dir / "included-utxo.json",
            transaction_id=transaction_id,
            initial_tip=initial_tip,
            evidence_path=registration_dir / "inclusion.json",
        )
        inclusion = json.loads(
            (registration_dir / "inclusion.json").read_text(encoding="utf-8")
        )
        terminal = [
            item
            for item in inclusion.get("observations", [])
            if isinstance(item, dict) and item.get("included") is True
        ]
        if not terminal or not isinstance(terminal[-1].get("tip"), dict):
            raise RuntimeError("governance credential registration lacks inclusion evidence")
        expected_tip = dict(terminal[-1]["tip"])
        _wait_for_target_adoption(
            log_path=target_log_path,
            expected_tip=expected_tip,
            evidence_path=registration_dir / "target-adoption.json",
        )

        receipt_credentials: dict[str, dict[str, Any]] = {}
        for case_id in (CONTROL_CASE, ZERO_CASE):
            state_path = registration_dir / f"{case_id}-stake-address-info.json"
            _checked(
                [
                    cli,
                    "conway",
                    "query",
                    "stake-address-info",
                    "--testnet-magic",
                    str(network_magic),
                    "--socket-path",
                    socket_path,
                    "--address",
                    str(credentials[case_id]["stake_address"]),
                    "--out-file",
                    str(state_path),
                ]
            )
            state_raw = json.loads(state_path.read_text(encoding="utf-8"))
            _registered_stake_state(
                state_raw, expected_address=str(credentials[case_id]["stake_address"])
            )
            receipt_credentials[case_id] = {
                "stake_address": credentials[case_id]["stake_address"],
                "verification_key_sha256": hashlib.sha256(
                    Path(credentials[case_id]["stake_vkey"]).read_bytes()
                ).hexdigest(),
                "certificate_sha256": hashlib.sha256(
                    Path(credentials[case_id]["certificate"]).read_bytes()
                ).hexdigest(),
                "state_sha256": hashlib.sha256(state_path.read_bytes()).hexdigest(),
                "state_path": str(state_path.relative_to(output_dir)),
            }
        receipt = {
            "schema_version": 1,
            "status": "registered",
            "transaction_id": transaction_id,
            "funding_input": registration_input,
            "reference_inclusion_path": str(
                (registration_dir / "inclusion.json").relative_to(output_dir)
            ),
            "target_adoption_path": str(
                (registration_dir / "target-adoption.json").relative_to(output_dir)
            ),
            "expected_tip": expected_tip,
            "credentials": receipt_credentials,
        }
        (registration_dir / "result.json").write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        yield {
            case_id: {
                "stake_vkey": values["stake_vkey"],
                "certificate": values["certificate"],
                "stake_address": values["stake_address"],
            }
            for case_id, values in credentials.items()
        }


def run_governance_zero_denominator_admission(
    *,
    runtime: dict[str, Any],
    output_dir: Path,
    target_node: str,
    reference_node: str,
    expected_target_version: str,
    expected_target_source: str,
    expected_reference_version: str,
    expected_reference_source: str,
    experiment_mode: str = ZERO_DENOMINATOR_MODE,
    p2p_trace_recovery: bool = False,
) -> dict[str, Any]:
    if experiment_mode not in {ZERO_DENOMINATOR_MODE, MATCHED_CONTROL_MODE}:
        raise RuntimeError("unsupported governance admission experiment mode")
    if p2p_trace_recovery and experiment_mode != ZERO_DENOMINATOR_MODE:
        raise RuntimeError("P2P trace recovery requires the zero-denominator mode")
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
        raise RuntimeError("governance admission requires the installed Docker controller")
    runtime_root = Path(str(runtime.get("runtime_root") or ""))
    if not runtime_root.is_absolute():
        raise RuntimeError("runtime root is not absolute")
    output_dir.mkdir(parents=True, exist_ok=True)
    anchor = prepare_governance_anchor(
        output_dir=output_dir,
        artifact_base_url=str(os.environ.get("DWARF_RUN_ARTIFACT_BASE_URL") or ""),
    )
    target = identities["target"]
    reference = identities["reference"]
    if p2p_trace_recovery and runtime.get("amaru_txsubmission_trace") is not True:
        raise RuntimeError("P2P trace recovery requires the scenario trace opt-in")
    network_magic = int(runtime["network_magic"])
    socket_path = str(reference.get("container_socket_path") or "")
    helper_image = str(os.environ.get("DWARF_LOCALTXSUBMISSION_HELPER_IMAGE") or "")
    target_propagation_gate = _reference_tx_submission_propagation_gate(
        reference,
        inspect_container=_inspect_container,
    )

    with _containerized_cardano_workload(
        runtime_root=runtime_root,
        runtime=runtime,
        node=reference,
        output_dir=output_dir,
        controller=controller,
    ) as (workload_runtime, _workload_node):
        cli = str(workload_runtime["support_binaries"]["cardano-cli"])
        controller_cli = str(
            (workload_runtime.get("controller_support_binaries") or {}).get(
                "cardano-cli"
            )
            or ""
        )
        if not controller_cli or not Path(controller_cli).is_file():
            raise RuntimeError(
                "governance admission requires controller-native cardano-cli for offline key generation"
            )
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
            raise RuntimeError("governance funding address does not match generated substrate")

        fetched_anchor_hash = _checked(
            [cli, "hash", "anchor-data", "--url", str(anchor["url"])]
        ).stdout.strip()
        if fetched_anchor_hash != anchor["hash"]:
            raise RuntimeError("governance anchor retrieval hash does not match")
        (output_dir / "anchor-retrieval.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "verified",
                    "url": anchor["url"],
                    "relative_path": anchor["relative_path"],
                    "byte_length": len(anchor["bytes"]),
                    "blake2b_256": anchor["hash"],
                    "fetched_blake2b_256": fetched_anchor_hash,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        pparams_path = output_dir / "protocol-parameters.json"
        gov_state_path = output_dir / "governance-state.json"
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
        _checked(
            [
                cli,
                "conway",
                "query",
                "gov-state",
                "--socket-path",
                socket_path,
                "--testnet-magic",
                str(network_magic),
                "--out-file",
                str(gov_state_path),
            ]
        )
        pparams = json.loads(pparams_path.read_text(encoding="utf-8"))
        gov_state = json.loads(gov_state_path.read_text(encoding="utf-8"))
        gov_deposit = pparams.get("govActionDeposit")
        stake_deposit = pparams.get("stakeAddressDeposit")
        if not all(
            isinstance(value, int) and not isinstance(value, bool) and value > 0
            for value in (gov_deposit, stake_deposit)
        ):
            raise RuntimeError("live governance or stake deposit is unavailable")
        guardrails = _verified_guardrails_identity(
            runtime=runtime, gov_state=gov_state
        )
        _require_fresh_pparam_root(gov_state)
        control_ratio = _control_ratio(pparams)

        funding_before = _query_utxo(
            cli=cli,
            socket_path=socket_path,
            network_magic=network_magic,
            address=address,
            destination=output_dir / "funding-before.json",
        )
        per_case = int(gov_deposit) + FEE_RESERVE
        registration_budget = (2 * int(stake_deposit)) + FEE_RESERVE
        fresh_input, selected_lovelace = _select_funding_input(
            funding_before,
            minimum_lovelace=(3 * per_case)
            + (3 * COLLATERAL_LOVELACE)
            + registration_budget
            + POST_CONTROL_LOVELACE
            + FEE_RESERVE,
        )
        setup_body = output_dir / "funding-setup.body"
        command = [
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
        for amount in admission_funding_amounts(
            proposal_lovelace=per_case,
            collateral_lovelace=COLLATERAL_LOVELACE,
            registration_lovelace=registration_budget,
            post_control_lovelace=POST_CONTROL_LOVELACE,
        ):
            command.extend(["--tx-out", f"{address}+{amount}"])
        command.extend(["--out-file", str(setup_body)])
        _checked(command)
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
        _setup_template, setup_payload = _envelope(setup_tx)
        setup_id = _transaction_id(setup_payload)
        initial_tip = _query_reference_tip(
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
            initial_tip=initial_tip,
            evidence_path=output_dir / "funding-inclusion.json",
        )
        assigned = admission_funding_inputs(setup_id)
        collateral = admission_collateral_inputs(setup_id)
        registration_input = admission_registration_input(setup_id)
        validate_admission_funding_outputs(
            funding_ready,
            assigned=assigned,
            expected_lovelace=per_case,
            collateral=collateral,
            expected_collateral_lovelace=COLLATERAL_LOVELACE,
            registration_input=registration_input,
            expected_registration_lovelace=registration_budget,
            post_control_input=assigned[POST_CONTROL_CASE]["target"],
            expected_post_control_lovelace=POST_CONTROL_LOVELACE,
        )
        funding_tip = _terminal_inclusion_tip(
            output_dir / "funding-inclusion.json", label="governance funding"
        )
        _wait_for_target_adoption(
            log_path=Path(str(target.get("log_path") or "")),
            expected_tip=funding_tip,
            evidence_path=output_dir / "funding-target-adoption.json",
        )
        anchor_url = str(anchor["url"])
        anchor_hash = str(anchor["hash"])
        with _registered_return_credentials(
            cli=cli,
            controller_cli=controller_cli,
            controller_key_root=controller_key_root,
            key_root=key_root,
            socket_path=socket_path,
            network_magic=network_magic,
            change_address=address,
            stake_deposit=int(stake_deposit),
            registration_input=registration_input,
            output_dir=output_dir,
            target_log_path=Path(str(target.get("log_path") or "")),
        ) as registered:
            registered_credentials = dict(registered)

        def build_valid_proposal(
            *,
            case_dir: Path,
            credential_case: str,
            funding_input: str,
            collateral_input: str,
        ) -> tuple[dict[str, Any], bytes, bytes]:
            case_dir.mkdir(parents=True, exist_ok=True)
            action = case_dir / "parameter-update.action"
            body_path = case_dir / "transaction.body"
            signed_path = case_dir / "transaction.signed"
            guardrails_script_path = case_dir / "guardrails.plutus"
            shutil.copyfile(GUARDRAILS_SCRIPT, guardrails_script_path)
            _checked(
                _governance_action_command(
                    cli=cli,
                    governance_action_deposit=int(gov_deposit),
                    stake_vkey=Path(
                        registered_credentials[credential_case]["stake_vkey"]
                    ),
                    anchor_url=anchor_url,
                    anchor_hash=anchor_hash,
                    guardrails_hash=str(guardrails),
                    control_ratio=control_ratio,
                    action_path=action,
                )
            )
            _checked(
                _proposal_build_command(
                    cli=cli,
                    socket_path=socket_path,
                    network_magic=network_magic,
                    funding_input=funding_input,
                    change_address=address,
                    proposal_path=action,
                    active_script_hash=str(guardrails),
                    guardrails_script_path=guardrails_script_path,
                    collateral_input=collateral_input,
                    output_path=body_path,
                )
            )
            _checked(
                [
                    cli,
                    "conway",
                    "transaction",
                    "sign",
                    "--tx-body-file",
                    str(body_path),
                    "--signing-key-file",
                    str(key_root / "utxo.skey"),
                    "--testnet-magic",
                    str(network_magic),
                    "--out-file",
                    str(signed_path),
                ]
            )
            envelope, payload = _envelope(signed_path)
            body, _witnesses, _tail = split_transaction(payload)
            return envelope, body, payload

        records: list[dict[str, Any]] = []

        control_record: dict[str, Any] = {"case_id": CONTROL_CASE}
        for role, funding_input, collateral_input in _control_submission_specs(
            assigned, collateral
        ):
            role_dir = output_dir / CONTROL_CASE / role
            _envelope_header, body, payload = build_valid_proposal(
                case_dir=role_dir,
                credential_case=CONTROL_CASE,
                funding_input=funding_input,
                collateral_input=collateral_input,
            )
            tx_id = _transaction_id_from_body(body)
            pre_submit_tip = _query_reference_tip(
                cli=cli, socket_path=socket_path, network_magic=network_magic
            )
            if role == "target":
                response = _submit_amaru_with_admission_log(
                    target=target,
                    payload=payload,
                    transaction_id=tx_id,
                    submit=_submit_amaru_container,
                )
            else:
                response = _submit_reference_raw(
                    runtime=runtime,
                    reference=reference,
                    payload=payload,
                    helper_image=helper_image,
                )
            from profile_manager.submit_primitives import _validation_stage

            classification = str(
                response.get("classification") or _validation_stage(response)
            )
            included = False
            target_adopted = False
            if classification == "accepted":
                try:
                    inclusion_path = role_dir / "inclusion.json"
                    _wait_for_reference_inclusion(
                        cli=cli,
                        socket_path=socket_path,
                        network_magic=network_magic,
                        address=address,
                        destination=role_dir / "included-utxo.json",
                        transaction_id=tx_id,
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
                    expected_tip = _terminal_inclusion_tip(
                        inclusion_path,
                        label=f"nonzero governance {role} control",
                    )
                    try:
                        _wait_for_target_adoption(
                            log_path=Path(str(target.get("log_path") or "")),
                            expected_tip=expected_tip,
                            evidence_path=role_dir / "target-adoption.json",
                        )
                        target_adopted = True
                    except RuntimeError:
                        target_adopted = False
            role_record = {
                **response,
                "classification": classification,
                "included": included,
                "target_adopted": target_adopted,
                "funding_input": funding_input,
                "collateral_input": collateral_input,
                "payload_sha256": hashlib.sha256(payload).hexdigest(),
                "body_sha256": hashlib.sha256(body).hexdigest(),
                "transaction_id": tx_id,
            }
            control_record[role] = role_record
            (role_dir / "result.json").write_text(
                json.dumps(role_record, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        records.append(control_record)
        control_dir = output_dir / CONTROL_CASE
        (control_dir / "result.json").write_text(
            json.dumps(control_record, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if not _valid_control_complete(control_record):
            raise RuntimeError(
                _control_failure_reason(control_record)
                + "; zero-denominator case was not submitted"
            )

        middle_case_id = (
            MATCHED_MIDDLE_CASE
            if experiment_mode == MATCHED_CONTROL_MODE
            else ZERO_CASE
        )
        zero_dir = output_dir / middle_case_id
        envelope, valid_body, valid_payload = build_valid_proposal(
            case_dir=zero_dir,
            credential_case=ZERO_CASE,
            funding_input=assigned[ZERO_CASE]["shared"],
            collateral_input=collateral[ZERO_CASE]["shared"],
        )
        if experiment_mode == MATCHED_CONTROL_MODE:
            final_body = valid_body
            payload = valid_payload
        else:
            final_body = mutate_min_fee_ref_script_cost(valid_body)
            payload = resign_transaction_body(
                valid_payload,
                final_body,
                [controller_key_root / "utxo.skey"],
            )
            _write_envelope(zero_dir / "transaction.signed", envelope, payload)
        tx_id = _transaction_id_from_body(final_body)
        if experiment_mode == MATCHED_CONTROL_MODE:
            attempts = _submit_matched_nonzero_attempts(
                runtime=runtime,
                reference=reference,
                target=target,
                payload=payload,
                transaction_id=tx_id,
                helper_image=helper_image,
                before_reference=lambda: _query_reference_tip(
                    cli=cli,
                    socket_path=socket_path,
                    network_magic=network_magic,
                ),
            )
        else:
            attempts = _submit_zero_denominator_attempts(
                runtime=runtime,
                reference=reference,
                target=target,
                payload=payload,
                transaction_id=tx_id,
                helper_image=helper_image,
                before_target=lambda: _query_reference_tip(
                    cli=cli,
                    socket_path=socket_path,
                    network_magic=network_magic,
                ),
            )
        (zero_dir / "submission-attempts.json").write_text(
            json.dumps(attempts, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        responses = {
            "reference": attempts["reference"],
            "target": attempts["target"],
        }
        classifications = {
            role: str(response.get("classification") or "unavailable")
            for role, response in responses.items()
        }
        if experiment_mode == MATCHED_CONTROL_MODE:
            if classifications != {"reference": "accepted", "target": "accepted"}:
                raise RuntimeError(
                    "matched nonzero middle control was not accepted by both nodes"
                )
        else:
            if classifications["reference"] != "decode_reject":
                raise RuntimeError(
                    "zero-denominator direct reference decoder evidence is unavailable; "
                    "target submission was not attempted"
                )
            if classifications["target"] != "accepted":
                raise RuntimeError(
                    "zero-denominator exact Amaru local admission is unavailable; "
                    "propagation and post-mutant control were not attempted"
                )
        included = False
        target_adopted = False
        inclusion_path = zero_dir / "inclusion.json"
        try:
            _wait_for_reference_inclusion(
                cli=cli,
                socket_path=socket_path,
                network_magic=network_magic,
                address=address,
                destination=zero_dir / "included-utxo.json",
                transaction_id=tx_id,
                initial_tip=dict(
                    attempts[
                        "reference_pre_submit_tip"
                        if experiment_mode == MATCHED_CONTROL_MODE
                        else "target_pre_submit_tip"
                    ]
                ),
                evidence_path=inclusion_path,
                target_propagation_gate=(
                    None
                    if experiment_mode == MATCHED_CONTROL_MODE
                    else target_propagation_gate
                ),
            )
            included = True
        except RuntimeError as exc:
            try:
                propagation_evidence = json.loads(
                    inclusion_path.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError) as evidence_error:
                raise RuntimeError(
                    "zero-denominator propagation evidence is unavailable"
                ) from evidence_error
            if experiment_mode == MATCHED_CONTROL_MODE:
                raise RuntimeError(
                    "matched nonzero middle control was not included"
                ) from exc
            if (
                propagation_evidence.get("status")
                != "not-included-after-reference-progress"
            ):
                raise RuntimeError(
                    "zero-denominator propagation observation is inconclusive"
                ) from exc
        else:
            propagation_evidence = json.loads(
                inclusion_path.read_text(encoding="utf-8")
            )
            expected_tip = _terminal_inclusion_tip(
                inclusion_path, label="zero-denominator governance case"
            )
            try:
                _wait_for_target_adoption(
                    log_path=Path(str(target.get("log_path") or "")),
                    expected_tip=expected_tip,
                    evidence_path=zero_dir / "target-adoption.json",
                )
                target_adopted = True
            except RuntimeError:
                target_adopted = False
        if experiment_mode == MATCHED_CONTROL_MODE and not target_adopted:
            raise RuntimeError(
                "matched nonzero middle control lacks exact target adoption"
            )
        payload_sha256 = hashlib.sha256(payload).hexdigest()
        propagation_observation = {
            "status": str(propagation_evidence.get("status") or "unavailable"),
            "evidence_path": str(inclusion_path.relative_to(output_dir)),
            "target_propagation_gate": propagation_evidence.get(
                "target_propagation_gate"
            ),
            "p2p_relay_observed": None,
            "relay_rejection_observed": None,
            "evidence_limit": (
                "the matched middle transaction was submitted directly to both "
                "nodes and does not measure peer relay"
                if experiment_mode == MATCHED_CONTROL_MODE
                else (
                    "reference mempool/UTxO absence after eligibility and progress "
                    "does not prove peer-to-peer relay or relay rejection"
                )
            ),
        }
        zero_record = {
            "case_id": middle_case_id,
            "experiment_mode": experiment_mode,
            "funding_input": assigned[ZERO_CASE]["shared"],
            "collateral_input": collateral[ZERO_CASE]["shared"],
            "payload_sha256": payload_sha256,
            "body_sha256": hashlib.sha256(final_body).hexdigest(),
            "transaction_id": tx_id,
            "submission_order": attempts["submission_order"],
            "propagation_observation": propagation_observation,
            "target": {
                **responses["target"],
                "classification": classifications["target"],
                "included": included,
                "target_adopted": target_adopted,
                "payload_sha256": payload_sha256,
            },
            "reference": {
                **responses["reference"],
                "classification": classifications["reference"],
                "included": included,
                "target_adopted": target_adopted,
                "payload_sha256": payload_sha256,
            },
        }
        records.append(zero_record)
        (zero_dir / "result.json").write_text(
            json.dumps(zero_record, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        post_dir = output_dir / POST_CONTROL_CASE
        post_record: dict[str, Any] = {"case_id": POST_CONTROL_CASE}
        if included and experiment_mode == ZERO_DENOMINATOR_MODE:
            post_record["target"] = {
                "classification": "skipped",
                "reason": (
                    "zero-denominator transaction was unexpectedly included; "
                    "post-mutant control was not submitted"
                ),
                "funding_input": assigned[POST_CONTROL_CASE]["target"],
            }
        else:
            post_dir.mkdir(parents=True, exist_ok=True)
            post_body = post_dir / "transaction.body"
            post_signed = post_dir / "transaction.signed"
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
                    assigned[POST_CONTROL_CASE]["target"],
                    "--tx-out",
                    f"{address}+{POST_CONTROL_PAYMENT_LOVELACE}",
                    "--change-address",
                    address,
                    "--out-file",
                    str(post_body),
                ]
            )
            _checked(
                [
                    cli,
                    "conway",
                    "transaction",
                    "sign",
                    "--tx-body-file",
                    str(post_body),
                    "--signing-key-file",
                    str(key_root / "utxo.skey"),
                    "--testnet-magic",
                    str(network_magic),
                    "--out-file",
                    str(post_signed),
                ]
            )
            _post_envelope, post_payload = _envelope(post_signed)
            post_body_raw, _post_witnesses, _post_tail = split_transaction(
                post_payload
            )
            post_tx_id = _transaction_id_from_body(post_body_raw)
            post_pre_submit_tip = _query_reference_tip(
                cli=cli,
                socket_path=socket_path,
                network_magic=network_magic,
            )
            post_response = _submit_amaru_with_admission_log(
                target=target,
                payload=post_payload,
                transaction_id=post_tx_id,
                submit=_submit_amaru_container,
            )
            post_included = False
            post_adopted = False
            post_propagation_observation: dict[str, Any] = {}
            post_inclusion_path = post_dir / "inclusion.json"
            if post_response.get("classification") == "accepted":
                try:
                    _wait_for_post_mutant_reference_inclusion(
                        cli=cli,
                        socket_path=socket_path,
                        network_magic=network_magic,
                        address=address,
                        destination=post_dir / "included-utxo.json",
                        transaction_id=post_tx_id,
                        initial_tip=post_pre_submit_tip,
                        evidence_path=post_inclusion_path,
                        target_propagation_gate=target_propagation_gate,
                    )
                    post_included = True
                except RuntimeError:
                    post_included = False
            if post_inclusion_path.is_file():
                post_propagation_observation = json.loads(
                    post_inclusion_path.read_text(encoding="utf-8")
                )
            if post_included:
                post_tip = _terminal_inclusion_tip(
                    post_inclusion_path,
                    label="post-mutant valid traffic control",
                )
                try:
                    _wait_for_target_adoption(
                        log_path=Path(str(target.get("log_path") or "")),
                        expected_tip=post_tip,
                        evidence_path=post_dir / "target-adoption.json",
                    )
                    post_adopted = True
                except RuntimeError:
                    post_adopted = False
            post_record["target"] = {
                **post_response,
                "included": post_included,
                "target_adopted": post_adopted,
                "funding_input": assigned[POST_CONTROL_CASE]["target"],
                "payload_sha256": hashlib.sha256(post_payload).hexdigest(),
                "body_sha256": hashlib.sha256(post_body_raw).hexdigest(),
                "transaction_id": post_tx_id,
                "propagation_observation": post_propagation_observation,
                "observation_boundary": "post-mutant-valid-target-traffic",
            }
            if (
                p2p_trace_recovery
                and post_response.get("classification") == "accepted"
            ):
                admission_evidence = attempts.get("target", {}).get(
                    "amaru_admission_evidence", {}
                )
                peer_identity = _reference_peer_identity(reference)
                trace_evidence = assess_txsubmission_trace(
                    target=target,
                    log_start_offset=admission_evidence.get("log_start_offset"),
                    malformed_transaction_id=tx_id,
                    later_valid_transaction_id=post_tx_id,
                    expected_peer=str(peer_identity["peer"]),
                )
                trace_evidence["reference_peer_identity"] = peer_identity
                (post_dir / "p2p-trace.json").write_text(
                    json.dumps(trace_evidence, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                post_record["p2p_trace"] = trace_evidence
                if (
                    not post_included
                    and post_propagation_observation.get("status")
                    == "not-included-after-reference-progress"
                ):
                    post_record["conditional_recovery"] = (
                        _recover_post_control_after_cycle(
                            trace_evidence=trace_evidence,
                            runtime=runtime,
                            target=target,
                            reference=reference,
                            payload=post_payload,
                            transaction_id=post_tx_id,
                            target_propagation_gate=target_propagation_gate,
                            cli=cli,
                            socket_path=socket_path,
                            network_magic=network_magic,
                            address=address,
                            output_dir=post_dir / "conditional-recovery",
                        )
                    )
                else:
                    post_record["conditional_recovery"] = {
                        "schema_version": 1,
                        "status": "not-attempted",
                        "reason": "post-control target path did not meet the recovery precondition",
                    }
            elif (
                post_response.get("classification") == "accepted"
                and not post_included
                and post_propagation_observation.get("status")
                == "not-included-after-reference-progress"
            ):
                if not p2p_trace_recovery:
                    post_record["direct_reference_confirmation"] = (
                        _confirm_post_mutant_control_on_reference(
                            runtime=runtime,
                            reference=reference,
                            payload=post_payload,
                            transaction_id=post_tx_id,
                            helper_image=helper_image,
                            cli=cli,
                            socket_path=socket_path,
                            network_magic=network_magic,
                            address=address,
                            output_dir=post_dir,
                            target_observation=post_propagation_observation,
                        )
                    )
        records.append(post_record)
        (post_dir / "result.json").parent.mkdir(parents=True, exist_ok=True)
        (post_dir / "result.json").write_text(
            json.dumps(post_record, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    status = assess_governance_admission(records, experiment_mode=experiment_mode)
    registration_receipt = json.loads(
        (output_dir / REGISTRATION_PREP / "result.json").read_text(encoding="utf-8")
    )
    return {
        "schema_version": 1,
        "evidence_scope": (
            "governance_parameter_update_matched_negative_control"
            if experiment_mode == MATCHED_CONTROL_MODE
            else "governance_parameter_update_admission_only"
        ),
        "experiment_mode": experiment_mode,
        "p2p_trace_recovery": p2p_trace_recovery,
        "target_identity": target,
        "reference_identity": reference,
        "network_magic": network_magic,
        "target_propagation_gate": target_propagation_gate,
        "parameter": {
            "cddl_key": PARAMETER_KEY,
            "name": "minFeeRefScriptCostPerByte",
            "control_ratio": [control_ratio.numerator, control_ratio.denominator],
            **(
                {
                    "middle_ratio": [
                        control_ratio.numerator,
                        control_ratio.denominator,
                    ]
                }
                if experiment_mode == MATCHED_CONTROL_MODE
                else {"mutant_ratio": [control_ratio.numerator, 0]}
            ),
        },
        "prerequisites": {
            "governance_action_deposit": gov_deposit,
            "stake_address_deposit": stake_deposit,
            "constitution_guardrails_script": guardrails,
            "protocol_parameters_sha256": hashlib.sha256(
                pparams_path.read_bytes()
            ).hexdigest(),
            "governance_state_sha256": hashlib.sha256(
                gov_state_path.read_bytes()
            ).hexdigest(),
            "guardrails_script_sha256": hashlib.sha256(
                GUARDRAILS_SCRIPT.read_bytes()
            ).hexdigest(),
            "previous_parameter_update": None,
            "return_credentials": registration_receipt,
        },
        "funding": {
            "setup_transaction_id": setup_id,
            "selected_input": fresh_input,
            "selected_input_lovelace": selected_lovelace,
            "assigned_inputs": assigned,
            "collateral_inputs": collateral,
            "registration_input": registration_input,
            "registration_budget_lovelace": registration_budget,
        },
        "cases": records,
        "admission_status": status,
        "limitations": [
            "This experiment covers admission and optional proposal insertion only.",
            "It does not cover voting, ratification, enactment, refunds or epoch transitions.",
            "Transport closure or timeout is unavailable evidence, not node rejection or crash.",
            "Reference mempool/UTxO absence does not prove peer-to-peer relay or relay rejection.",
            "Resource observations are retained separately and do not establish resource impact.",
        ],
    }
