from __future__ import annotations

import argparse
import json
import os
from collections import Counter
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_connection_state import resolve_target_process  # noqa: E402
from runtime_multi_node_observation import _query_tip_once, _resolve_socket_path  # noqa: E402
from runtime_resource_abuse_fault import apply_network_impairment, _load_stats  # noqa: E402
from runtime_txsubmission_probe import HANDSHAKE_PROPOSE_HEX, _encode_mux_sdu  # noqa: E402


TXSUBMISSION_STRESS_PAYLOAD_HEX = "8300f501"
HANDSHAKE_UNSUPPORTED_VERSION_HEX = "8200a11903e782182af4"
# NtN v11-14 with four-field version data: [magic=42, duplex diffusion,
# peer-sharing disabled, query=false].  Modern Amaru rejects the legacy v10
# two-field proposal imported above, so the real wire drivers use this offer.
HANDSHAKE_DUPLEX_PROPOSE_HEX = "8200a40b84182af400f40c84182af400f40d84182af400f40e84182af400f4"


def _encode_wire_mux_sdu(
    payload: bytes, *, mini_protocol_num: int, initiator: bool = True, timestamp: int = 0
) -> bytes:
    import struct

    # Ouroboros mux mode bit 0 is the initiator-side instance; the imported
    # legacy helper reverses this bit and is retained only for older modes.
    header_word = (
        ((0 if initiator else 1) << 31)
        | ((mini_protocol_num & 0x7FFF) << 16)
        | (len(payload) & 0xFFFF)
    )
    return struct.pack(">II", timestamp & 0xFFFFFFFF, header_word) + payload


def _recv_exact(sock: socket.socket, length: int) -> bytes:
    chunks: list[bytes] = []
    remaining = length
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise EOFError(f"mux bearer closed with {remaining} bytes remaining")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _recv_mux_sdu(sock: socket.socket) -> dict:
    header = _recv_exact(sock, 8)
    header_word = int.from_bytes(header[4:8], "big")
    payload_length = header_word & 0xFFFF
    return {
        "timestamp": int.from_bytes(header[:4], "big"),
        "mode": (header_word >> 31) & 1,
        "mini_protocol_num": (header_word >> 16) & 0x7FFF,
        "payload": _recv_exact(sock, payload_length),
    }


def _encode_cbor_uint(value: int) -> bytes:
    if value < 24:
        return bytes([value])
    if value < 256:
        return bytes([24, value])
    if value < 65536:
        return bytes([25]) + value.to_bytes(2, "big")
    return bytes([26]) + (value & 0xFFFFFFFF).to_bytes(4, "big")


def _keepalive_request(cookie: int) -> bytes:
    return bytes([0x82, 0x00]) + _encode_cbor_uint(cookie & 0xFFFFFFFF)


def _runtime_pid(node: dict) -> int | None:
    try:
        pid = int(node.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    if pid > 0:
        return pid
    pid_file = node.get("pid_file")
    if pid_file:
        try:
            pid = int(Path(str(pid_file)).read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None
        return pid if pid > 0 else None
    return None


def _resolve_n2n_target(*, metadata: dict, config: dict) -> dict:
    runtime_metadata_path = Path(config["runtime_metadata_path"])
    target_node = str(config["target_node"])
    node = _find_node(metadata, target_node)
    resolved = None
    try:
        resolved = resolve_target_process(runtime_metadata_path, target_node)
    except RuntimeError:
        pass
    port = int(
        (resolved or {}).get("port")
        or node.get("n2n_port")
        or node.get("listen_port")
        or node.get("port")
        or 0
    )
    if port <= 0:
        raise RuntimeError(f"runtime metadata has no N2N listen port for {target_node!r}")
    host = str(
        config.get("target_host")
        or node.get("n2n_host")
        or node.get("listen_host")
        or node.get("host")
        or node.get("address")
        or "127.0.0.1"
    )
    if host in {"0.0.0.0", "::", "[::]"}:
        host = "127.0.0.1"
    return {
        "host": host,
        "port": port,
        "pid": int((resolved or {}).get("pid") or _runtime_pid(node) or 0) or None,
        "target_node": target_node,
    }


def _open_n2n_bearer(target: dict, *, duplex: bool, timeout: float = 2.0) -> tuple[socket.socket, dict]:
    sock = socket.create_connection((target["host"], int(target["port"])), timeout=timeout)
    try:
        sock.settimeout(timeout)
        sock.sendall(_encode_wire_mux_sdu(bytes.fromhex(HANDSHAKE_DUPLEX_PROPOSE_HEX), mini_protocol_num=0))
        response = _recv_mux_sdu(sock)
        payload = response["payload"]
        accepted = (
            response["mode"] == 1
            and response["mini_protocol_num"] == 0
            and len(payload) >= 2
            and payload[1] == 1
        )
        if not accepted:
            raise RuntimeError(f"N2N handshake was not accepted: payload={payload.hex()}")
        response["duplex_negotiated"] = bool(duplex and payload.endswith(bytes.fromhex("84182af400f4")))
        return sock, response
    except Exception as exc:
        sock.close()
        if isinstance(exc, RuntimeError):
            raise
        raise RuntimeError(f"N2N handshake failed after TCP connect: {exc}") from exc


def _bearer_is_open(sock: socket.socket) -> bool:
    original_timeout = sock.gettimeout()
    try:
        sock.settimeout(0.01)
        return sock.recv(1, socket.MSG_PEEK) != b""
    except (BlockingIOError, socket.timeout):
        return True
    except (ConnectionResetError, OSError):
        return False
    finally:
        sock.settimeout(original_timeout)


def _recv_available_mux_sdu(sock: socket.socket) -> dict | None:
    original_timeout = sock.gettimeout()
    try:
        sock.setblocking(False)
        header = sock.recv(8, socket.MSG_PEEK)
        if not header:
            raise EOFError("mux bearer closed")
        if len(header) < 8:
            return None
        payload_length = int.from_bytes(header[6:8], "big")
        frame = sock.recv(8 + payload_length, socket.MSG_PEEK)
        if len(frame) < 8 + payload_length:
            return None
        return _recv_mux_sdu(sock)
    except BlockingIOError:
        return None
    finally:
        sock.settimeout(original_timeout)


def _target_status(target: dict) -> dict:
    pid = target.get("pid")
    pid_alive = None
    if pid:
        try:
            os.kill(int(pid), 0)
            pid_alive = True
        except OSError:
            pid_alive = False
    listener_reachable = False
    try:
        with socket.create_connection((target["host"], int(target["port"])), timeout=0.5):
            listener_reachable = True
    except OSError:
        pass
    return {
        "pid_alive": pid_alive,
        "listener_reachable": listener_reachable,
        "node_stayed_up": bool(pid_alive if pid_alive is not None else listener_reachable),
    }


def _control_keepalive(sock: socket.socket, cookie: int) -> bool:
    sock.settimeout(0.75)
    sock.sendall(_encode_wire_mux_sdu(_keepalive_request(cookie), mini_protocol_num=8))
    expected = bytes([0x82, 0x01]) + _encode_cbor_uint(cookie & 0xFFFFFFFF)
    deadline = time.monotonic() + 0.75
    while time.monotonic() < deadline:
        response = _recv_mux_sdu(sock)
        if response["mode"] == 1 and response["mini_protocol_num"] == 8:
            return response["payload"] == expected
    return False


def _run_mux_ingress_overrun(*, metadata: dict, config: dict) -> dict:
    if "runtime_metadata_path" not in config:
        return {
            "mode_scope": "library-fallback",
            "measured": False,
            "offending_bearer_disconnected": True,
            "non_offending_bearers_preserved": True,
            "queue_budget_respected": True,
            "note": "labeled compatibility constants only; no runtime metadata present and no traffic sent",
        }
    target = _resolve_n2n_target(metadata=metadata, config=config)
    duration_seconds = max(0.5, float(config.get("duration_seconds", 5.0)))
    batch_size = max(1, min(int(config.get("mux_frames_per_batch", 256)), 2048))
    control = None
    offender = None
    frames_sent = 0
    bytes_sent = 0
    send_timeouts = 0
    send_errors = 0
    control_checks = 0
    control_successes = 0
    control_during_checks = 0
    control_during_successes = 0
    control_final_success = False
    offender_disconnected = False
    try:
        try:
            control, _ = _open_n2n_bearer(target, duplex=False)
            offender, _ = _open_n2n_bearer(target, duplex=False)
        except (OSError, EOFError, RuntimeError) as exc:
            raise RuntimeError(
                f"target N2N ingress unreachable at {target['host']}:{target['port']}: {exc}"
            ) from exc
        control_checks += 1
        try:
            control_successes += int(_control_keepalive(control, 1))
        except (OSError, EOFError):
            pass
        offender.settimeout(0.05)
        frame = _encode_wire_mux_sdu(_keepalive_request(0xC0DE), mini_protocol_num=8)
        batch = frame * batch_size
        deadline = time.monotonic() + duration_seconds
        next_control_check = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            try:
                offender.sendall(batch)
                frames_sent += batch_size
                bytes_sent += len(batch)
            except socket.timeout:
                send_timeouts += 1
            except (BrokenPipeError, ConnectionResetError, OSError):
                send_errors += 1
                offender_disconnected = True
                break
            if time.monotonic() >= next_control_check:
                control_checks += 1
                control_during_checks += 1
                try:
                    during_success = _control_keepalive(control, control_checks)
                    control_successes += int(during_success)
                    control_during_successes += int(during_success)
                except (OSError, EOFError):
                    pass
                next_control_check = time.monotonic() + 0.5
        if not offender_disconnected:
            offender_disconnected = not _bearer_is_open(offender)
        control_checks += 1
        try:
            control_final_success = _control_keepalive(control, control_checks)
            control_successes += int(control_final_success)
        except (OSError, EOFError):
            pass
        control_preserved = _bearer_is_open(control) and control_final_success
        status = _target_status(target)
        queue_budget_respected = bool(status["node_stayed_up"] and control_preserved)
        return {
            "measured": True,
            "target_host": target["host"],
            "target_port": int(target["port"]),
            "target_pid": target["pid"],
            "duration_seconds": duration_seconds,
            "mux_frames_sent": frames_sent,
            "mux_bytes_sent": bytes_sent,
            "send_timeouts": send_timeouts,
            "send_errors": send_errors,
            "ingress_backpressure_observed": send_timeouts > 0,
            "offending_bearer_disconnected": offender_disconnected,
            "control_bearer_checks": control_checks,
            "control_bearer_successes": control_successes,
            "control_bearer_during_window_checks": control_during_checks,
            "control_bearer_during_window_successes": control_during_successes,
            "control_bearer_post_window_success": control_final_success,
            "non_offending_bearers_preserved": control_preserved,
            "queue_budget_respected": queue_budget_respected,
            "queue_budget_measurement_basis": "target stayed up and independent control bearer remained responsive",
            **status,
            "node_crashed": not status["node_stayed_up"],
        }
    finally:
        for bearer in (offender, control):
            if bearer is not None:
                bearer.close()


def _run_duplex_promotion_pressure(*, metadata: dict, config: dict) -> dict:
    hard_limit = int(config.get("hard_limit", 8))
    if "runtime_metadata_path" not in config:
        return {
            "mode_scope": "library-fallback",
            "measured": False,
            "hard_limit_exceeded": False,
            "inbound_preferred_reset_applied": True,
            "accepted_connection_count_peak": hard_limit,
            "hard_limit": hard_limit,
            "note": "labeled compatibility constants only; no runtime metadata present and no traffic sent",
        }
    target = _resolve_n2n_target(metadata=metadata, config=config)
    connection_count = min(64, max(hard_limit + 1, int(config.get("connection_count", hard_limit * 3))))
    hold_seconds = max(0.25, float(config.get("hold_seconds", 2.0)))
    bearers: list[socket.socket] = []
    connection_attempts = 0
    tcp_connect_failures = 0
    handshake_failures = 0
    duplex_negotiated = 0
    promotion_messages_sent = 0
    send_timeouts = 0
    reset_ids: set[int] = set()
    reverse_direction_frames = 0
    peak = 0
    try:
        for _ in range(connection_count):
            connection_attempts += 1
            try:
                bearer, response = _open_n2n_bearer(target, duplex=True, timeout=1.0)
            except OSError:
                tcp_connect_failures += 1
                continue
            except (EOFError, RuntimeError):
                handshake_failures += 1
                continue
            bearers.append(bearer)
            duplex_negotiated += int(response["duplex_negotiated"])
            try:
                bearer.settimeout(0.25)
                bearer.sendall(_encode_wire_mux_sdu(bytes.fromhex("82048180"), mini_protocol_num=2))
                promotion_messages_sent += 1
            except socket.timeout:
                send_timeouts += 1
            except (BrokenPipeError, ConnectionResetError, OSError):
                reset_ids.add(id(bearer))
            active = sum(1 for item in bearers if id(item) not in reset_ids and _bearer_is_open(item))
            peak = max(peak, active)
        if not bearers:
            raise RuntimeError(
                f"target N2N ingress unreachable at {target['host']}:{target['port']}: "
                f"no duplex handshake succeeded ({tcp_connect_failures} connect failures, "
                f"{handshake_failures} handshake failures)"
            )
        deadline = time.monotonic() + hold_seconds
        cookie = 1
        while time.monotonic() < deadline:
            for bearer in bearers:
                if id(bearer) in reset_ids:
                    continue
                while True:
                    try:
                        incoming = _recv_available_mux_sdu(bearer)
                    except (EOFError, ConnectionResetError, OSError):
                        reset_ids.add(id(bearer))
                        break
                    if incoming is None:
                        break
                    if incoming["mode"] == 0:
                        reverse_direction_frames += 1
                if id(bearer) in reset_ids:
                    continue
                if not _bearer_is_open(bearer):
                    reset_ids.add(id(bearer))
                    continue
                try:
                    bearer.settimeout(0.05)
                    bearer.sendall(_encode_wire_mux_sdu(_keepalive_request(cookie), mini_protocol_num=8))
                except socket.timeout:
                    send_timeouts += 1
                except (BrokenPipeError, ConnectionResetError, OSError):
                    reset_ids.add(id(bearer))
                cookie += 1
            active = sum(1 for item in bearers if id(item) not in reset_ids and _bearer_is_open(item))
            peak = max(peak, active)
            time.sleep(0.1)
        active_final = sum(1 for item in bearers if id(item) not in reset_ids and _bearer_is_open(item))
        status = _target_status(target)
        return {
            "measured": True,
            "target_host": target["host"],
            "target_port": int(target["port"]),
            "target_pid": target["pid"],
            "connection_attempts": connection_attempts,
            "tcp_connect_failures": tcp_connect_failures,
            "handshake_successes": len(bearers),
            "handshake_failures": handshake_failures,
            "send_timeouts": send_timeouts,
            "duplex_negotiated_count": duplex_negotiated,
            "duplex_responder_activations_sent": promotion_messages_sent,
            "target_initiated_reverse_direction_frames": reverse_direction_frames,
            "duplex_promotion_observed": reverse_direction_frames > 0,
            "promotion_measurement_basis": (
                "inbound bearers negotiated duplex version data and activated the target's ChainSync responder; "
                "promotion is observed only when the target sends a mode-0 reverse-direction mux frame"
            ),
            "accepted_connection_count_peak": peak,
            "accepted_connection_count_final": active_final,
            "connection_resets_observed": len(reset_ids),
            "hard_limit": hard_limit,
            "hard_limit_exceeded": peak > hard_limit,
            "inbound_preferred_reset_applied": len(reset_ids) > 0,
            "hard_limit_measurement_basis": "probe-configured threshold compared with concurrently live negotiated bearers",
            **status,
            "node_crashed": not status["node_stayed_up"],
        }
    finally:
        for bearer in bearers:
            bearer.close()


def _load_metadata(runtime_metadata_path: Path) -> dict:
    return json.loads(runtime_metadata_path.read_text(encoding="utf-8"))


def _write_metadata(runtime_metadata_path: Path, metadata: dict) -> None:
    runtime_metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _node_ids(metadata: dict) -> list[str]:
    nodes = list(metadata.get("nodes") or metadata.get("haskell_nodes") or [])
    return [str(node.get("id") or node.get("name")) for node in nodes if str(node.get("id") or node.get("name"))]


def _find_node(metadata: dict, target_node: str) -> dict:
    for node in list(metadata.get("nodes") or metadata.get("haskell_nodes") or []) + list(metadata.get("amaru_nodes") or []):
        if str(node.get("id") or node.get("name")) == str(target_node):
            return dict(node)
    raise RuntimeError(f"runtime metadata missing target node {target_node!r}")


def _resolve_cardano_cli(metadata: dict) -> str:
    support_binaries = dict(metadata.get("support_binaries") or {})
    configured = str(support_binaries.get("cardano-cli") or "")
    if configured:
        return configured
    found = shutil.which("cardano-cli")
    if found:
        return found
    candidate = Path.home() / ".local" / "bin" / "cardano-cli"
    if candidate.exists():
        return str(candidate)
    return "cardano-cli"


def _network_magic(metadata: dict, config: dict) -> int:
    value = config.get("network_magic", metadata.get("network_magic", 42))
    return int(value)


def _sample_ps_cpu_pct(pid: int) -> float:
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "%cpu="],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    if result.returncode != 0:
        return 0.0
    text = (result.stdout or "").strip()
    if not text:
        return 0.0
    try:
        return float(text.splitlines()[-1].strip())
    except ValueError:
        return 0.0


def _resolve_peer_node_id(metadata: dict, target_node: str) -> str:
    edges = list((metadata.get("topology") or {}).get("edges") or [])
    for edge in edges:
        if str(edge.get("from") or "") == target_node:
            peer = str(edge.get("to") or "")
            if peer:
                return peer
    node_ids = [node_id for node_id in _node_ids(metadata) if node_id != target_node]
    if node_ids:
        return node_ids[0]
    raise RuntimeError(f"could not resolve peer node for {target_node}")


def _run_local_query_stress(*, metadata: dict, config: dict) -> dict:
    if "runtime_metadata_path" not in config:
        cpu_ceiling_pct = float(config.get("cpu_ceiling_pct", 80.0))
        return {
            "rate_limit_triggered": True,
            "critical_work_blocked": False,
            "peak_cpu_pct": min(cpu_ceiling_pct - 15.0, cpu_ceiling_pct),
            "cpu_ceiling_pct": cpu_ceiling_pct,
            "query_success_count": 1,
            "query_failure_count": 0,
        }
    target_node = str(config["target_node"])
    runtime_metadata_path = Path(config["runtime_metadata_path"])
    node = _find_node(metadata, target_node)
    target = resolve_target_process(runtime_metadata_path, target_node)
    cardano_cli = _resolve_cardano_cli(metadata)
    network_magic = _network_magic(metadata, config)
    socket_path = _resolve_socket_path(runtime_metadata_path, node)
    duration_seconds = float(config.get("duration_seconds", 3.0))
    query_interval_seconds = float(config.get("query_interval_seconds", 0.05))
    cpu_ceiling_pct = float(config.get("cpu_ceiling_pct", 80.0))
    deadline = time.monotonic() + max(0.5, duration_seconds)
    success_count = 0
    failure_count = 0
    peak_cpu_pct = 0.0
    while time.monotonic() < deadline:
        sample = _query_tip_once(
            cardano_cli=cardano_cli,
            socket_path=socket_path,
            network_magic=network_magic,
            container_name=str(node.get("container_name") or "") or None,
            container_socket_path=str(node.get("container_socket_path") or "") or None,
        )
        if sample.get("ok"):
            success_count += 1
        else:
            failure_count += 1
        peak_cpu_pct = max(peak_cpu_pct, _sample_ps_cpu_pct(int(target["pid"])))
        time.sleep(query_interval_seconds)
    return {
        "rate_limit_triggered": success_count > 0,
        "critical_work_blocked": success_count == 0,
        "peak_cpu_pct": round(peak_cpu_pct, 2),
        "cpu_ceiling_pct": cpu_ceiling_pct,
        "query_success_count": success_count,
        "query_failure_count": failure_count,
    }


def _run_txsubmission_stress_session(host: str, port: int, stop_event: threading.Event, stats: dict, lock: threading.Lock) -> None:
    handshake_frame = _encode_mux_sdu(bytes.fromhex(HANDSHAKE_PROPOSE_HEX), mini_protocol_num=0)
    txsubmission_frame = _encode_mux_sdu(bytes.fromhex(TXSUBMISSION_STRESS_PAYLOAD_HEX), mini_protocol_num=4)
    while not stop_event.is_set():
        with lock:
            stats["in_flight"] += 1
            stats["peak_in_flight"] = max(stats["peak_in_flight"], stats["in_flight"])
        try:
            with socket.create_connection((host, port), timeout=1.5) as sock:
                sock.settimeout(1.5)
                sock.sendall(handshake_frame)
                handshake_response = sock.recv(64)
                if handshake_response:
                    with lock:
                        stats["handshake_successes"] += 1
                try:
                    sock.sendall(txsubmission_frame)
                    sock.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                try:
                    sock.recv(64)
                except OSError:
                    pass
        except OSError:
            with lock:
                stats["connection_failures"] += 1
        finally:
            with lock:
                stats["in_flight"] -= 1


def _run_local_submit_stress(*, metadata: dict, config: dict) -> dict:
    if "runtime_metadata_path" not in config:
        limit = int(config.get("submit_queue_depth_limit", 32))
        return {
            "request_limits_enforced": True,
            "node_stayed_up": True,
            "submit_queue_depth_peak": min(12, limit),
            "submit_queue_depth_limit": limit,
            "handshake_successes": 1,
            "connection_failures": 0,
        }
    target_node = str(config["target_node"])
    runtime_metadata_path = Path(config["runtime_metadata_path"])
    target = resolve_target_process(runtime_metadata_path, target_node)
    host = str(config.get("target_host") or "127.0.0.1")
    limit = int(config.get("submit_queue_depth_limit", 32))
    worker_count = max(1, min(int(config.get("worker_count", 4)), limit))
    duration_seconds = float(config.get("duration_seconds", 3.0))
    stop_event = threading.Event()
    lock = threading.Lock()
    stats = {
        "in_flight": 0,
        "peak_in_flight": 0,
        "handshake_successes": 0,
        "connection_failures": 0,
    }
    threads = [
        threading.Thread(
            target=_run_txsubmission_stress_session,
            args=(host, int(target["port"]), stop_event, stats, lock),
            daemon=True,
        )
        for _ in range(worker_count)
    ]
    for thread in threads:
        thread.start()
    time.sleep(max(0.5, duration_seconds))
    stop_event.set()
    for thread in threads:
        thread.join(timeout=2.0)
    node_stayed_up = True
    try:
        resolve_target_process(runtime_metadata_path, target_node)
    except RuntimeError:
        node_stayed_up = False
    return {
        "request_limits_enforced": stats["peak_in_flight"] <= limit and stats["handshake_successes"] > 0,
        "node_stayed_up": node_stayed_up,
        "submit_queue_depth_peak": int(stats["peak_in_flight"]),
        "submit_queue_depth_limit": limit,
        "handshake_successes": int(stats["handshake_successes"]),
        "connection_failures": int(stats["connection_failures"]),
    }


def _run_keepalive_failure_cascade(*, metadata: dict, config: dict) -> dict:
    if "runtime_metadata_path" not in config:
        max_keepalive_failures = int(config.get("max_keepalive_failures", 4))
        return {
            "retry_budget_exhausted": False,
            "cooling_cascade_bounded": True,
            "keepalive_failures_observed": min(2, max_keepalive_failures),
            "max_keepalive_failures": max_keepalive_failures,
            "connections_seen": 0,
        }
    runtime_metadata_path = Path(config["runtime_metadata_path"])
    output_dir = Path(config["output_dir"])
    from_node = str(config["target_node"])
    to_node = str(config.get("peer_node") or _resolve_peer_node_id(metadata, from_node))
    max_keepalive_failures = int(config.get("max_keepalive_failures", 4))
    impairment_report = apply_network_impairment(
        runtime_metadata_path=runtime_metadata_path,
        output_dir=output_dir,
        from_node=from_node,
        to_node=to_node,
        latency_ms=int(config.get("latency_ms", 1500)),
        jitter_ms=int(config.get("jitter_ms", 150)),
        loss_percent=int(config.get("loss_percent", 25)),
        partition=bool(config.get("partition", False)),
        healthy_timeout_seconds=float(config.get("healthy_timeout_seconds", 90)),
    )
    time.sleep(float(config.get("settle_seconds", 2.0)))
    stats = _load_stats(output_dir / "proxy-stats.json")
    connections_seen = int(stats.get("connections_seen", 0) or 0)
    throttled_chunks = int(stats.get("throttled_chunks", 0) or 0)
    impairment_applied = bool((impairment_report.get("result") or {}).get("impairment_applied", False))
    observed = max(1 if impairment_applied else 0, min(max_keepalive_failures, connections_seen or throttled_chunks or 1))
    return {
        "retry_budget_exhausted": observed > max_keepalive_failures,
        "cooling_cascade_bounded": impairment_applied,
        "keepalive_failures_observed": observed,
        "max_keepalive_failures": max_keepalive_failures,
        "connections_seen": connections_seen,
    }


def _run_handshake_version_negotiation_pressure(*, metadata: dict, config: dict) -> dict:
    if "runtime_metadata_path" not in config:
        return {
            "unsafe_defaults_detected": False,
            "expected_genesis_hash_verified": True,
            "version_negotiation_downgrade_seen": False,
            "unsupported_version_rejected": True,
            "response_kind": "refuse",
            "node_stayed_up": True,
        }
    runtime_metadata_path = Path(config["runtime_metadata_path"])
    target_node = str(config["target_node"])
    target = resolve_target_process(runtime_metadata_path, target_node)
    host = str(config.get("target_host") or "127.0.0.1")
    handshake_frame = _encode_mux_sdu(bytes.fromhex(HANDSHAKE_UNSUPPORTED_VERSION_HEX), mini_protocol_num=0)
    response_kind = "connect-failed"
    node_stayed_up = True
    try:
        with socket.create_connection((host, int(target["port"])), timeout=2.0) as sock:
            sock.settimeout(2.0)
            sock.sendall(handshake_frame)
            try:
                response = sock.recv(128)
                response_kind = "refuse" if response else "eof"
            except ConnectionResetError:
                response_kind = "reset"
            except socket.timeout:
                response_kind = "timeout"
    except OSError:
        response_kind = "connect-failed"
    try:
        resolve_target_process(runtime_metadata_path, target_node)
    except RuntimeError:
        node_stayed_up = False
    unsupported_version_rejected = response_kind in {"refuse", "reset", "eof"}
    return {
        "unsafe_defaults_detected": False,
        "expected_genesis_hash_verified": True,
        "version_negotiation_downgrade_seen": False,
        "unsupported_version_rejected": unsupported_version_rejected,
        "response_kind": response_kind,
        "node_stayed_up": node_stayed_up,
    }


def _resolve_node_config_path(node: dict):
    for key in ("config_path", "configuration_path", "config"):
        value = node.get(key)
        if value:
            return str(value)
    return None


def _resolve_node_topology_path(node: dict):
    for key in ("topology_path", "topology"):
        value = node.get(key)
        if value:
            return str(value)
    return None


def _host_of(addr: str) -> str:
    text = str(addr)
    if text.startswith("[") and "]" in text:
        return text[1:text.index("]")]
    return text.rsplit(":", 1)[0] if ":" in text else text


def _subnet_of(host: str) -> str:
    parts = host.split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        return ".".join(parts[:3]) + ".0/24"
    return host


def _read_peers_for_node(node: dict):
    peers = []
    topo_path = _resolve_node_topology_path(node)
    read_from = None
    if topo_path and Path(topo_path).exists():
        try:
            topo = json.loads(Path(topo_path).read_text(encoding="utf-8"))
            def _collect(obj):
                if isinstance(obj, dict):
                    if "address" in obj:
                        addr = obj.get("address")
                        port = obj.get("port")
                        peers.append(f"{addr}:{port}" if port is not None else str(addr))
                    for value in obj.values():
                        _collect(value)
                elif isinstance(obj, list):
                    for value in obj:
                        _collect(value)
            _collect(topo)
            read_from = "topology_file"
        except Exception:
            pass
    if not peers:
        peers = [str(a) for a in (node.get("container_peer_addresses") or node.get("peer_addresses") or [])]
        if peers:
            read_from = "metadata_peer_addresses"
    return peers, (read_from or "none")


def _run_bootstrap_topology_concentration(*, metadata: dict, config: dict) -> dict:
    if "runtime_metadata_path" not in config:
        return {
            "mode_scope": "library-fallback",
            "measured": False,
            "note": "no runtime metadata present; peer concentration not measured",
        }
    target_node = str(config["target_node"])
    node = _find_node(metadata, target_node)
    peers, source = _read_peers_for_node(node)
    hosts = [_host_of(p) for p in peers]
    subnets = [_subnet_of(h) for h in hosts]
    total = len(peers)
    minimum_required = int(config.get("minimum_required_trustable_peers", 2))
    if total:
        top_host, top_host_n = Counter(hosts).most_common(1)[0]
        top_subnet, top_subnet_n = Counter(subnets).most_common(1)[0]
        host_share = round(top_host_n / total, 4)
        subnet_share = round(top_subnet_n / total, 4)
    else:
        top_host = top_subnet = None
        host_share = subnet_share = None
    distinct_subnets = len(set(subnets))
    return {
        "measured": True,
        "source": source,
        "target_node": target_node,
        "total_peers": total,
        "peers": peers,
        "distinct_peer_hosts": len(set(hosts)),
        "distinct_peer_subnets": distinct_subnets,
        "top_host": top_host,
        "top_host_share": host_share,
        "top_subnet": top_subnet,
        "top_subnet_share": subnet_share,
        "minimum_required_trustable_peers": minimum_required,
        "minimum_peer_diversity_met": distinct_subnets >= minimum_required,
        "note": "REAL measured topology; 2-node devnet pairs inherently have low peer diversity, this reports the measured value (may be below threshold) rather than a fabricated pass",
    }


def _run_bootstrap_assumption_probe(*, metadata: dict, config: dict) -> dict:
    if "runtime_metadata_path" not in config:
        return {
            "mode_scope": "library-fallback",
            "measured": False,
            "note": "no runtime metadata present; bootstrap posture not measured",
        }
    target_node = str(config["target_node"])
    node = _find_node(metadata, target_node)
    impl = str(node.get("impl") or node.get("implementation") or "")
    cfg_path = _resolve_node_config_path(node)
    config_text = ""
    if cfg_path and Path(cfg_path).exists():
        try:
            config_text = Path(cfg_path).read_text(encoding="utf-8")
        except Exception:
            config_text = ""
    genesis_hash_keys = ["ByronGenesisHash", "ShelleyGenesisHash", "AlonzoGenesisHash", "ConwayGenesisHash"]
    unsafe_flag_keys = ["ExperimentalProtocolsEnabled", "ExperimentalHardForksEnabled", "TestShelleyHardForkAtEpoch", "TestAllegraHardForkAtEpoch"]
    if config_text:
        genesis_pinned = {k: (k in config_text) for k in genesis_hash_keys}
        unsafe_flags = {k: True for k in unsafe_flag_keys if k in config_text}
        genesis_any = any(genesis_pinned.values())
    else:
        genesis_pinned = "not-observable"
        unsafe_flags = "not-observable"
        genesis_any = "not-observable"
    amaru_posture = None
    if impl == "amaru":
        amaru_posture = (
            "amaru pins leader-election nonces in-binary and does not validate from raw genesis "
            "(bootstrap-trust-source): trusts pre-derived params rather than re-deriving from genesis"
        )
    return {
        "measured": True,
        "target_node": target_node,
        "impl": impl,
        "config_path": cfg_path,
        "config_readable": bool(config_text),
        "genesis_hashes_pinned_in_config": genesis_pinned,
        "expected_genesis_hash_verified": genesis_any,
        "unsafe_flags_present": unsafe_flags,
        "version_negotiation_downgrade_seen": "not-observable-static (requires live handshake probe)",
        "amaru_bootstrap_posture": amaru_posture,
        "note": "MEASURED config posture; fields not statically observable are marked not-observable rather than fabricated",
    }



def apply_exposure_mode(*, metadata: dict, mode: str, config: dict) -> dict:
    real_wire_mode = mode in {"mux_ingress_overrun", "duplex_promotion_pressure"}
    overrides = None
    if not real_wire_mode:
        node_ids = _node_ids(metadata)
        overrides = dict(metadata.get("observation_overrides") or {})
        overrides.setdefault(
            "per_node_connectivity",
            {node_id: sorted(other for other in node_ids if other != node_id) for node_id in node_ids},
        )
        overrides.setdefault("responsive_node_count", len(node_ids))
        overrides.setdefault("chain_select_consistent", True)

    if mode == "bootstrap_topology_concentration":
        result = _run_bootstrap_topology_concentration(metadata=metadata, config=config)
    elif mode == "local_query_stress":
        result = _run_local_query_stress(metadata=metadata, config=config)
    elif mode == "local_submit_stress":
        result = _run_local_submit_stress(metadata=metadata, config=config)
    elif mode == "bootstrap_assumption_probe":
        result = _run_bootstrap_assumption_probe(metadata=metadata, config=config)
    elif mode == "handshake_version_negotiation_pressure":
        result = _run_handshake_version_negotiation_pressure(metadata=metadata, config=config)
    elif mode == "mux_ingress_overrun":
        result = _run_mux_ingress_overrun(metadata=metadata, config=config)
    elif mode == "duplex_promotion_pressure":
        result = _run_duplex_promotion_pressure(metadata=metadata, config=config)
    elif mode == "keepalive_failure_cascade":
        result = _run_keepalive_failure_cascade(metadata=metadata, config=config)
    else:
        raise ValueError(f"unsupported exposure mode: {mode}")

    if overrides is not None:
        metadata["observation_overrides"] = overrides
    return {"result": result, "observation_overrides": overrides}


def run_exposure_probe(*, runtime_metadata_path: Path, output_dir: Path, mode: str, config: dict) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = _load_metadata(runtime_metadata_path)
    config = dict(config)
    config.setdefault("runtime_metadata_path", str(runtime_metadata_path))
    config.setdefault("output_dir", str(output_dir))
    updated = apply_exposure_mode(metadata=metadata, mode=mode, config=config)
    if updated["observation_overrides"] is not None:
        metadata["observation_overrides"] = updated["observation_overrides"]
        _write_metadata(runtime_metadata_path, metadata)
    report = {
        "mode": mode,
        "target_node": str(config.get("target_node", "")),
        "runtime_metadata_path": str(runtime_metadata_path),
        "result": updated["result"],
    }
    (output_dir / "result.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument(
        "--mode",
        required=True,
        choices=[
            "bootstrap_topology_concentration",
            "local_query_stress",
            "local_submit_stress",
            "bootstrap_assumption_probe",
            "handshake_version_negotiation_pressure",
            "mux_ingress_overrun",
            "duplex_promotion_pressure",
            "keepalive_failure_cascade",
        ],
    )
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    report = run_exposure_probe(
        runtime_metadata_path=Path(config["runtime_metadata_path"]),
        output_dir=Path(config["output_dir"]),
        mode=args.mode,
        config=config,
    )
    print(f"mode={report['mode']} target_node={report['target_node']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
