from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_telemetry import emit_target_event  # noqa: E402
from runtime_txsubmission_accounting import TxSubmissionDecodeError, decode_txsubmission_payload  # noqa: E402


# MsgProposeVersions {13, 14, 15: [magic 42, diffusionMode=false, peerSharing=0, query=false]}:
# the version table cardano-node 10.x/11.x and Amaru accept (F53). The old
# version-10 proposal was refused with VersionMismatch by every current node.
HANDSHAKE_PROPOSE_HEX = "8200a30d84182af400f40e84182af400f40f84182af400f4"
PROPOSED_VERSIONS = (13, 14, 15)
TXSUBMISSION_INIT_HEX = "8106"
# Provider mode: offer one transaction so the node's own mempool evaluates it (no malformation).
SERVE_TRANSACTION_MODE = "serve_transaction"
MODE_CASES = {
    "txsubmission_window_pressure": {
        "malformation_id": "reply-txids-overflow-window",
    },
    "txsubmission_batch_pressure": {
        "malformation_id": "bad-replytxids-payload-not-list",
        "payload_hex": "820107",
        "negotiated_batch_limit": 8,
        "max_batch_observed": 8,
    },
    "txsubmission_unexpected_body": {
        "malformation_id": "trailing-bytes-after-done",
        "payload_hex": "810400",
        "rejection_reason": "NotInFlight",
    },
    "mempool_failure_probe": {
        "malformation_id": "bad-tx-size-u32-overflow",
        "payload_hex": "82018182582011111111111111111111111111111111111111111111111111111111111111111b0000000100000000",
    },
}


def _encode_mux_sdu(payload: bytes, *, mini_protocol_num: int, initiator: bool = True, timestamp: int = 0) -> bytes:
    import struct

    # Mux mode bit: 0 marks a frame sent by the initiator, 1 one sent by the responder.
    header_word = ((0 if initiator else 1) << 31) | ((mini_protocol_num & 0x7FFF) << 16) | (len(payload) & 0xFFFF)
    return struct.pack(">II", timestamp & 0xFFFFFFFF, header_word) + payload


def _recv_exact(sock: socket.socket, length: int) -> bytes:
    chunks: list[bytes] = []
    remaining = length
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise EOFError(f"expected {remaining} more bytes")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _recv_mux_payload(sock: socket.socket) -> bytes:
    header = _recv_exact(sock, 8)
    payload_length = int.from_bytes(header[6:8], "big")
    return _recv_exact(sock, payload_length)


def _run_accounting_helper(*, transcript: list[dict], output_dir: Path) -> dict:
    transcript_path = output_dir / "txsubmission-transcript.json"
    accounting_path = output_dir / "txsubmission-accounting.json"
    transcript_path.write_text(json.dumps(transcript, indent=2) + "\n", encoding="utf-8")
    helper = SCRIPT_DIR / "runtime_txsubmission_accounting.py"
    proc = subprocess.run(
        ["python3", str(helper), "--transcript", str(transcript_path), "--output", str(accounting_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "txsubmission accounting helper failed: "
            f"exit={proc.returncode} stdout={proc.stdout[-512:]} stderr={proc.stderr[-512:]}"
        )
    return json.loads(accounting_path.read_text(encoding="utf-8"))


def _encode_cbor_uint(value: int) -> bytes:
    if value < 24:
        return bytes([value])
    if value < 256:
        return bytes([24, value])
    if value < 65536:
        return bytes([25]) + int(value).to_bytes(2, "big")
    if value < 2**32:
        return bytes([26]) + int(value).to_bytes(4, "big")
    raise ValueError(f"uint out of supported range: {value}")


def _handshake_propose_payload(network_magic: int) -> bytes:
    """MsgProposeVersions with the current node-to-node versions and their
    four-field version data [magic, diffusionMode=false, peerSharing=0, query=false]."""
    data = b"\x84" + _encode_cbor_uint(network_magic) + b"\xf4\x00\xf4"
    table = b"".join(_encode_cbor_uint(version) + data for version in PROPOSED_VERSIONS)
    return b"\x82\x00" + bytes([0xa0 + len(PROPOSED_VERSIONS)]) + table


def _refusal_detail(payload: bytes) -> str:
    """Human detail for MsgRefuse: VersionMismatch lists the versions the node offers."""
    try:
        if payload[:4] == b"\x82\x02\x82\x00" and payload[4] & 0xE0 == 0x80:
            count = payload[4] & 0x1F
            versions = []
            offset = 5
            for _ in range(count):
                value = payload[offset]
                if value < 24:
                    versions.append(value); offset += 1
                elif value == 24:
                    versions.append(payload[offset + 1]); offset += 2
                else:
                    raise ValueError
            return " (version mismatch; node supports " + ", ".join(str(v) for v in versions) + ")"
    except (IndexError, ValueError):
        pass
    return ""


def _encode_cbor_array(length: int) -> bytes:
    return bytes([0x80 + length]) if length < 24 else bytes([0x98]) + bytes([length])


def _encode_cbor_bytes(payload: bytes) -> bytes:
    length = len(payload)
    if length < 24:
        return bytes([0x40 + length]) + payload
    if length < 256:
        return bytes([0x58, length]) + payload
    if length < 65536:
        return bytes([0x59]) + length.to_bytes(2, "big") + payload
    raise ValueError(f"bytes payload too large for helper encoder: {length}")


def _cbor_term_end(payload: bytes, offset: int) -> int:
    """Offset just past the definite-length CBOR term at ``offset``.

    Covers unsigned ints, byte/text strings, arrays, maps and tags: enough to
    slice the id and body terms of TxSubmission2 messages."""
    if offset >= len(payload):
        raise TxSubmissionDecodeError("truncated CBOR term")
    initial = payload[offset]
    major, info = initial >> 5, initial & 0x1F
    widths = {24: 1, 25: 2, 26: 4, 27: 8}
    if info == 31 and major in (2, 3, 4, 5):
        # Indefinite length: items (or chunks) until the break code 0xFF.
        position = offset + 1
        while True:
            if position >= len(payload):
                raise TxSubmissionDecodeError("truncated indefinite-length CBOR term")
            if payload[position] == 0xFF:
                return position + 1
            position = _cbor_term_end(payload, position)
    if info < 24:
        argument, head = info, 1
    elif info in widths:
        width = widths[info]
        argument, head = int.from_bytes(payload[offset + 1:offset + 1 + width], "big"), 1 + width
    else:
        raise TxSubmissionDecodeError("indefinite-length CBOR term is not supported here")
    position = offset + head
    if major in (0, 1, 7):
        return position
    if major in (2, 3):
        return position + argument
    if major == 4:
        for _ in range(argument):
            position = _cbor_term_end(payload, position)
        return position
    if major == 5:
        for _ in range(argument * 2):
            position = _cbor_term_end(payload, position)
        return position
    if major == 6:
        return _cbor_term_end(payload, position)
    raise TxSubmissionDecodeError("unsupported CBOR major type")


def _build_reply_txids_payload(count: int) -> bytes:
    txid_entries: list[bytes] = []
    for index in range(count):
        txid = bytes([(index + 1) % 251 or 1]) * 32
        txid_entries.append(
            _encode_cbor_array(2)
            + _encode_cbor_bytes(txid)
            + _encode_cbor_uint(512 + index)
        )
    payload = _encode_cbor_array(2)
    payload += _encode_cbor_uint(1)
    payload += _encode_cbor_array(len(txid_entries))
    for entry in txid_entries:
        payload += entry
    return payload


def _parse_request_txids(payload: bytes) -> dict:
    decoded = decode_txsubmission_payload(payload)
    if decoded.get("message_kind") != "request-txids":
        raise TxSubmissionDecodeError(f"expected request-txids, got {decoded.get('message_kind')}")
    return decoded


def _build_reply_txids_payload_for(txid_term: bytes, size: int) -> bytes:
    """MsgReplyTxIds with one entry: ``[1, [[<txid term>, size]]]``."""
    return (
        _encode_cbor_array(2) + _encode_cbor_uint(1) + _encode_cbor_array(1)
        + _encode_cbor_array(2) + txid_term + _encode_cbor_uint(size)
    )


def _build_reply_txs_payload(tx_terms: list[bytes]) -> bytes:
    """MsgReplyTxs: ``[3, [<tx term>...]]``; each transaction is its wire CBOR term."""
    return _encode_cbor_array(2) + _encode_cbor_uint(3) + _encode_cbor_array(len(tx_terms)) + b"".join(tx_terms)


def _parse_request_txs(payload: bytes) -> list[bytes]:
    """The id terms a MsgRequestTxs ``[2, [<txid term>...]]`` names, as raw CBOR slices."""
    decoded = decode_txsubmission_payload(payload)
    if decoded.get("message_kind") != "request-txs":
        raise TxSubmissionDecodeError(f"expected request-txs, got {decoded.get('message_kind')}")
    # Walk the raw bytes: outer array head (definite or indefinite), the tag 2, then the
    # inner array of id terms, each sliced verbatim so the reply echoes the node's own encoding.
    if not payload or (payload[0] >> 5) != 4:
        raise TxSubmissionDecodeError("request-txs shape invalid")
    position = 1 + {24: 1, 25: 2, 26: 4, 27: 8}.get(payload[0] & 0x1F, 0)
    if payload[position] != 0x02:
        raise TxSubmissionDecodeError("request-txs shape invalid")
    position += 1
    inner = payload[position]
    if (inner >> 5) != 4:
        raise TxSubmissionDecodeError("request-txs shape invalid")
    info = inner & 0x1F
    if info == 31:
        count, position = None, position + 1
    else:
        width = {24: 1, 25: 2, 26: 4, 27: 8}.get(info, 0)
        count = info if info < 24 else int.from_bytes(payload[position + 1:position + 1 + width], "big")
        position += 1 + width
    terms = []
    while (count is None and payload[position] != 0xFF) or (count is not None and len(terms) < count):
        end = _cbor_term_end(payload, position)
        terms.append(payload[position:end])
        position = end
    return terms


def _load_runtime_node(runtime_metadata_path: Path, target_node: str) -> dict:
    body = json.loads(runtime_metadata_path.read_text(encoding="utf-8"))
    nodes = list(body.get("haskell_nodes") or []) + list(body.get("amaru_nodes") or []) + list(body.get("nodes") or [])
    for node in nodes:
        if node.get("name") == target_node or node.get("id") == target_node:
            return dict(node)
    raise RuntimeError(f"runtime metadata missing target node {target_node!r}: {runtime_metadata_path}")


def _node_endpoint(node: dict, target_host: str) -> tuple[str, int]:
    """Where to connect for a runtime node (F63).  A controller-run node records
    ``container_ip`` and ``container_listen_address``; the probe runs attached to
    that topology network, where the host-published loopback port is unreachable.
    An explicit non-loopback ``target_host`` (multi-host substrates) keeps the
    published port; ``127.0.0.1`` is the host-era default, not a routing choice."""
    explicit = str(target_host or "").strip()
    if explicit and explicit not in {"127.0.0.1", "localhost"}:
        return explicit, int(node["port"])
    container_ip = str(node.get("container_ip") or "").strip()
    listen = str(node.get("container_listen_address") or "")
    if container_ip and ":" in listen:
        return container_ip, int(listen.rsplit(":", 1)[1])
    return (explicit or "127.0.0.1"), int(node["port"])


def build_result_body(
    *,
    mode: str,
    handshake_response_kind: str,
    txsubmission_response_kind: str,
    node_stayed_up: bool,
    accounting: dict | None = None,
    negotiated_window: int | None = None,
    overflow_attempt_txids: int | None = None,
) -> dict:
    if mode == SERVE_TRANSACTION_MODE:
        accounting = dict(accounting or {})
        return {
            "served": bool(accounting.get("served")),
            "requested_body": bool(accounting.get("requested_body")),
            "post_serve_response_kind": str(accounting.get("post_serve_response_kind") or txsubmission_response_kind),
            "node_stayed_up": node_stayed_up,
        }
    case = MODE_CASES[mode]
    accounting = dict(accounting or {})
    if mode == "txsubmission_window_pressure":
        return {
            "negotiated_window": int(negotiated_window if negotiated_window is not None else 0),
            "overflow_attempt_txids": int(
                overflow_attempt_txids if overflow_attempt_txids is not None else 0
            ),
            "overflow_rejected": txsubmission_response_kind in {"reset", "eof", "timeout"} and node_stayed_up,
            "client_reply_txids_observed": int(
                accounting.get("txids_processed", 0) or 0
            ),
            "txsubmission_messages_observed": int(accounting.get("txsubmission_messages_observed", 0) or 0),
            "txsubmission_message_kinds": list(accounting.get("txsubmission_message_kinds") or []),
            "decode_failure_count": int(accounting.get("decode_failure_count", 0) or 0),
        }
    if mode == "txsubmission_batch_pressure":
        return {
            "negotiated_batch_limit": int(case["negotiated_batch_limit"]),
            "max_batch_observed": int(case["max_batch_observed"]),
            "oversized_batch_rejected": txsubmission_response_kind in {"reset", "eof", "timeout"} and node_stayed_up,
        }
    if mode == "txsubmission_unexpected_body":
        return {
            "unexpected_body_rejected": txsubmission_response_kind in {"reset", "eof", "timeout"} and node_stayed_up,
            "rejection_reason": str(case["rejection_reason"]),
        }
    if mode == "mempool_failure_probe":
        return {
            "fatal_error_contained": txsubmission_response_kind in {"reset", "eof", "timeout"} and node_stayed_up,
            "node_stayed_up": node_stayed_up,
            "protocol_session_survived": handshake_response_kind == "accepted" and node_stayed_up,
        }
    raise ValueError(f"unsupported mode: {mode}")


def run_probe(
    *,
    runtime_metadata_path: Path | None,
    target_node: str,
    output_dir: Path,
    mode: str,
    target_host: str = "127.0.0.1",
    target_port: int | None = None,
    endpoint_source: str = "substrate-runtime",
    runtime_identity_path: str | None = None,
    response_timeout_seconds: float = 2.0,
    receive_bytes: int = 64,
    tx_body: bytes | None = None,
    tx_id: bytes | None = None,
    handshake_payload: bytes | None = None,
) -> dict:
    if mode == SERVE_TRANSACTION_MODE:
        if not tx_body or not tx_id:
            raise RuntimeError("serve_transaction requires the wire transaction term and its id term")
        return _serve_transaction(
            runtime_metadata_path=runtime_metadata_path, target_node=target_node, output_dir=output_dir,
            target_host=target_host, target_port=target_port, endpoint_source=endpoint_source,
            runtime_identity_path=runtime_identity_path, response_timeout_seconds=response_timeout_seconds,
            tx_body=tx_body, tx_id=tx_id, handshake_payload=handshake_payload,
        )
    network_magic = 42
    if target_port is None:
        if runtime_metadata_path is None:
            raise RuntimeError("runtime metadata or a verified target port is required")
        runtime_metadata = json.loads(runtime_metadata_path.read_text(encoding="utf-8"))
        network_magic = int(runtime_metadata.get("network_magic", 42))
        node = _load_runtime_node(runtime_metadata_path, target_node)
        target_host, port = _node_endpoint(node, target_host)
    else:
        port = int(target_port)
        if port <= 0 or port > 65535:
            raise RuntimeError("verified target port is invalid")
    case = MODE_CASES[mode]
    handshake_payload = _handshake_propose_payload(network_magic)
    txsubmission_init_payload = bytes.fromhex(TXSUBMISSION_INIT_HEX)
    handshake_frame = _encode_mux_sdu(handshake_payload, mini_protocol_num=0)
    txsubmission_init_frame = _encode_mux_sdu(txsubmission_init_payload, mini_protocol_num=4)
    txsubmission_payload = b""
    if "payload_hex" in case:
        txsubmission_payload = bytes.fromhex(str(case["payload_hex"]))
    txsubmission_frame = _encode_mux_sdu(txsubmission_payload, mini_protocol_num=4) if txsubmission_payload else b""

    handshake_response_kind = "unknown"
    handshake_response = b""
    valid_request_response_kind = "unknown"
    valid_request_response = b""
    txsubmission_response_kind = "unknown"
    txsubmission_response = b""
    transcript: list[dict] = []
    negotiated_window = 0
    overflow_attempt_txids = 0
    started = time.perf_counter()
    output_dir.mkdir(parents=True, exist_ok=True)
    with socket.create_connection((target_host, port), timeout=2.0) as sock:
        sock.settimeout(response_timeout_seconds)
        sock.sendall(handshake_frame)
        try:
            handshake_response = _recv_mux_payload(sock)
            handshake_response_kind = _classify_handshake_reply(handshake_response)
        except socket.timeout:
            handshake_response_kind = "timeout"
        except EOFError:
            # A clean close (FIN) mid-frame is the node ending the session, not a crash of ours.
            handshake_response_kind = "eof"
        except ConnectionResetError:
            handshake_response_kind = "reset"
        if handshake_response_kind != "accepted":
            # A refusal is not a session: the mini-protocol frames that follow
            # would be judged by a node that already closed the connection.
            raise RuntimeError("valid handshake proposal was not accepted; got " + handshake_response_kind
                               + _refusal_detail(handshake_response))
        if mode == "txsubmission_window_pressure":
            sock.sendall(txsubmission_init_frame)
            transcript.append({"direction": "send", "payload_hex": txsubmission_init_payload.hex()})
            try:
                valid_request_response = _recv_mux_payload(sock)
                valid_request_response_kind = "data" if valid_request_response else "eof"
            except socket.timeout:
                valid_request_response_kind = "timeout"
            except EOFError:
                valid_request_response_kind = "eof"
            except ConnectionResetError:
                valid_request_response_kind = "reset"
            if valid_request_response_kind == "data":
                transcript.append({"direction": "recv", "payload_hex": valid_request_response.hex()})
                request_body = _parse_request_txids(valid_request_response)
                negotiated_window = int(request_body.get("requested_txids", 0) or 0)
                overflow_attempt_txids = negotiated_window + 1
                txsubmission_payload = _build_reply_txids_payload(
                    overflow_attempt_txids
                )
                txsubmission_frame = _encode_mux_sdu(txsubmission_payload, mini_protocol_num=4)
            if valid_request_response_kind != "data":
                raise RuntimeError(
                    "valid txsubmission request-txids did not produce a data response; "
                    f"got {valid_request_response_kind}"
                )
        try:
            sock.sendall(txsubmission_frame)
            transcript.append({"direction": "send", "payload_hex": txsubmission_payload.hex()})
        except ConnectionResetError:
            txsubmission_response_kind = "reset"
            txsubmission_response = b""
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            node_stayed_up = True
            accounting = _run_accounting_helper(transcript=transcript, output_dir=output_dir)
            result = build_result_body(
                mode=mode,
                handshake_response_kind=handshake_response_kind,
                txsubmission_response_kind=txsubmission_response_kind,
                node_stayed_up=node_stayed_up,
                accounting=accounting,
                negotiated_window=negotiated_window,
                overflow_attempt_txids=overflow_attempt_txids,
            )
            report = {
                "mode": mode,
                "target_node": target_node,
                "target_host": target_host,
                "target_port": port,
                "malformation_id": case["malformation_id"],
                "handshake_response_kind": handshake_response_kind,
                "handshake_response_hex": handshake_response.hex(),
                "valid_request_response_kind": valid_request_response_kind,
                "valid_request_response_hex": valid_request_response.hex(),
                "txsubmission_response_kind": txsubmission_response_kind,
                "txsubmission_response_hex": txsubmission_response.hex(),
                "elapsed_ms": elapsed_ms,
                "runtime_metadata_path": (
                    str(runtime_metadata_path) if runtime_metadata_path is not None else None
                ),
                "runtime_identity_path": runtime_identity_path,
                "endpoint_source": endpoint_source,
                "transcript": transcript,
                "accounting": accounting,
                "result": result,
            }
            (output_dir / "result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            emit_target_event(primitive=f"runtime_{mode}", event="txsubmission_probe_result", payload=report)
            return report
        try:
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        try:
            txsubmission_response = _recv_mux_payload(sock)
            txsubmission_response_kind = "data" if txsubmission_response else "eof"
        except socket.timeout:
            txsubmission_response_kind = "timeout"
        except EOFError:
            # A clean close (FIN) mid-frame is the node ending the session, not a crash of ours.
            txsubmission_response_kind = "eof"
        except ConnectionResetError:
            txsubmission_response_kind = "reset"

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    node_stayed_up = True
    if txsubmission_response_kind == "data":
        transcript.append({"direction": "recv", "payload_hex": txsubmission_response.hex()})
    accounting = _run_accounting_helper(transcript=transcript, output_dir=output_dir)
    result = build_result_body(
        mode=mode,
        handshake_response_kind=handshake_response_kind,
        txsubmission_response_kind=txsubmission_response_kind,
        node_stayed_up=node_stayed_up,
        accounting=accounting,
        negotiated_window=negotiated_window,
        overflow_attempt_txids=overflow_attempt_txids,
    )
    report = {
        "mode": mode,
        "target_node": target_node,
        "target_host": target_host,
        "target_port": port,
        "malformation_id": case["malformation_id"],
        "handshake_response_kind": handshake_response_kind,
        "handshake_response_hex": handshake_response.hex(),
        "valid_request_response_kind": valid_request_response_kind,
        "valid_request_response_hex": valid_request_response.hex(),
        "txsubmission_response_kind": txsubmission_response_kind,
        "txsubmission_response_hex": txsubmission_response.hex(),
        "elapsed_ms": elapsed_ms,
        "runtime_metadata_path": (
            str(runtime_metadata_path) if runtime_metadata_path is not None else None
        ),
        "runtime_identity_path": runtime_identity_path,
        "endpoint_source": endpoint_source,
        "transcript": transcript,
        "accounting": accounting,
        "result": result,
    }
    (output_dir / "result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    emit_target_event(primitive=f"runtime_{mode}", event="txsubmission_probe_result", payload=report)
    return report


def _resolve_endpoint(
    runtime_metadata_path: Path | None, target_node: str, target_port: int | None, target_host: str = "127.0.0.1"
) -> tuple[str, int, int]:
    """(host, port, network magic) from the retained runtime metadata or a verified port."""
    network_magic = 42
    if target_port is None:
        if runtime_metadata_path is None:
            raise RuntimeError("runtime metadata or a verified target port is required")
        runtime_metadata = json.loads(runtime_metadata_path.read_text(encoding="utf-8"))
        network_magic = int(runtime_metadata.get("network_magic", 42))
        node = _load_runtime_node(runtime_metadata_path, target_node)
        host, port = _node_endpoint(node, target_host)
        return host, port, network_magic
    port = int(target_port)
    if port <= 0 or port > 65535:
        raise RuntimeError("verified target port is invalid")
    return target_host, port, network_magic


def _classify_handshake_reply(payload: bytes) -> str:
    """MsgAcceptVersion is CBOR [1, version, params]; MsgRefuse is [2, reason]; a query
    reply is [3, versions]. Anything else is reported as the raw kind it was."""
    if not payload:
        return "eof"
    if len(payload) >= 2 and payload[0] in (0x82, 0x83):
        return {1: "accepted", 2: "refused", 3: "query-reply"}.get(payload[1], "data")
    return "data"


def _serve_transaction(
    *, runtime_metadata_path: Path | None, target_node: str, output_dir: Path, target_host: str,
    target_port: int | None, endpoint_source: str, runtime_identity_path: str | None,
    response_timeout_seconds: float, tx_body: bytes, tx_id: bytes, handshake_payload: bytes | None = None,
) -> dict:
    """Offer one transaction as a TxSubmission2 provider so the node's own mempool
    evaluates it: answer MsgRequestTxIds with the id and MsgRequestTxs with the body,
    then read the node's next message and close. One transaction per session; the
    node's admission decision (its own trace) is the measurement. ``handshake_payload``
    lets the caller send the proposal the target is known to accept."""
    target_host, port, network_magic = _resolve_endpoint(runtime_metadata_path, target_node, target_port, target_host)
    handshake_payload = handshake_payload or _handshake_propose_payload(network_magic)
    init_payload = bytes.fromhex(TXSUBMISSION_INIT_HEX)
    transcript: list[dict] = []
    served = requested_body = False
    post_kind = "unknown"
    handshake_response = b""
    handshake_response_kind = "unknown"
    started = time.perf_counter()
    output_dir.mkdir(parents=True, exist_ok=True)

    # We are the connection's initiator: every frame we send carries mux bit 31 clear
    # (the calibration helper's accepted handshake frame is 0x0000000a).
    def send(payload: bytes, *, mini_protocol_num: int = 4) -> None:
        sock.sendall(_encode_mux_sdu(payload, mini_protocol_num=mini_protocol_num))
        transcript.append({"direction": "send", "payload_hex": payload.hex()})

    with socket.create_connection((target_host, port), timeout=2.0) as sock:
        sock.settimeout(response_timeout_seconds)
        send(handshake_payload, mini_protocol_num=0)
        try:
            handshake_response = _recv_mux_payload(sock)
            handshake_response_kind = _classify_handshake_reply(handshake_response)
        except socket.timeout:
            handshake_response_kind = "timeout"
        except EOFError:
            # A clean close (FIN) mid-frame is the node ending the session, not a crash of ours.
            handshake_response_kind = "eof"
        except ConnectionResetError:
            handshake_response_kind = "reset"
        if handshake_response_kind != "accepted":
            raise RuntimeError(f"handshake proposal was not accepted; got {handshake_response_kind}")
        send(init_payload)
        for _ in range(4):   # request-txids, request-txs, then the node's follow-up
            try:
                payload = _recv_mux_payload(sock)
                post_kind = "data" if payload else "eof"
            except socket.timeout:
                post_kind = "timeout"
                break
            except ConnectionResetError:
                post_kind = "reset"
                break
            except EOFError:
                # The node closed the connection: after the body that is its decision
                # window ending; before it, the node declined to continue. Reported, not raised.
                post_kind = "eof"
                break
            if not payload:
                break
            transcript.append({"direction": "recv", "payload_hex": payload.hex()})
            kind = decode_txsubmission_payload(payload).get("message_kind")
            if kind == "request-txids" and not served:
                send(_build_reply_txids_payload_for(tx_id, len(tx_body)))
                served = True
            elif kind == "request-txs" and tx_id in _parse_request_txs(payload):
                send(_build_reply_txs_payload([tx_body]))
                requested_body = True
            elif requested_body:
                break   # the node's message after the body: its decision window has started
        try:
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    accounting = _run_accounting_helper(transcript=transcript, output_dir=output_dir)
    accounting.update(served=served, requested_body=requested_body, post_serve_response_kind=post_kind)
    result = build_result_body(
        mode=SERVE_TRANSACTION_MODE, handshake_response_kind=handshake_response_kind,
        txsubmission_response_kind=post_kind, node_stayed_up=True, accounting=accounting,
    )
    report = {
        "mode": SERVE_TRANSACTION_MODE, "target_node": target_node, "target_host": target_host, "target_port": port,
        "malformation_id": None, "handshake_response_kind": handshake_response_kind,
        "handshake_response_hex": handshake_response.hex(), "txsubmission_response_kind": post_kind,
        "tx_id_hex": tx_id.hex(), "tx_bytes": len(tx_body), "elapsed_ms": elapsed_ms,
        "runtime_metadata_path": str(runtime_metadata_path) if runtime_metadata_path is not None else None,
        "runtime_identity_path": runtime_identity_path, "endpoint_source": endpoint_source,
        "transcript": transcript, "accounting": accounting, "result": result,
    }
    (output_dir / "result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    emit_target_event(primitive=f"runtime_{SERVE_TRANSACTION_MODE}", event="txsubmission_probe_result", payload=report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--mode", required=True, choices=sorted(MODE_CASES) + [SERVE_TRANSACTION_MODE])
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    runtime_metadata_value = str(config.get("runtime_metadata_path") or "")
    target_port_value = config.get("target_port")
    report = run_probe(
        runtime_metadata_path=(
            Path(runtime_metadata_value) if runtime_metadata_value else None
        ),
        target_node=str(config["target_node"]),
        output_dir=Path(config["output_dir"]),
        mode=str(args.mode),
        target_host=str(config.get("target_host", "127.0.0.1")),
        target_port=(int(target_port_value) if target_port_value is not None else None),
        endpoint_source=str(config.get("endpoint_source") or "substrate-runtime"),
        runtime_identity_path=(
            str(config.get("runtime_identity_path"))
            if config.get("runtime_identity_path")
            else None
        ),
        response_timeout_seconds=float(config.get("response_timeout_seconds", 2.0)),
        receive_bytes=int(config.get("receive_bytes", 64)),
        tx_body=bytes.fromhex(config["tx_hex"]) if config.get("tx_hex") else None,
        tx_id=bytes.fromhex(config["tx_id_term_hex"]) if config.get("tx_id_term_hex") else None,
    )
    print(
        " ".join(
            [
                f"mode={report['mode']}",
                f"target_node={report['target_node']}",
                f"handshake_response_kind={report['handshake_response_kind']}",
                f"txsubmission_response_kind={report['txsubmission_response_kind']}",
            ]
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
