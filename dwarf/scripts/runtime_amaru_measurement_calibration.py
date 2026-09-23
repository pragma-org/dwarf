#!/usr/bin/env python3
"""Run and compare a bounded real-node stock/patched Amaru calibration leg."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from profile_manager.measurement_collectors.amaru_patched import (
    PATCHED_MEASUREMENT_IDS,
    AmaruPatchedCollector,
    paired_overhead_calibration,
)
from profile_manager.measurement_report import distribution_summary
from profile_manager.measurement_runtime import CollectorContext
from scripts.qualify_node_versions import classify_log_signals
from scripts.runtime_amaru_preview_proof import extract_latest_adopted_tip
from scripts.runtime_txsubmission_probe import _encode_mux_sdu


SEED = "0xA11CE501"
HANDSHAKE_SUPPORTED_VERSION_HEX = "8200a10f84182af400f4"
HANDSHAKE_UNSUPPORTED_VERSION_HEX = "8200a11903e784182af400f4"
HANDSHAKE_MALFORMED_HEX = "ff"
DEFAULT_CASE_SET = "unsupported-version-only-v1"
ACCEPTANCE_CASE_SET = "accepted-and-rejected-v1"
CLIENT_INVALID_CASE_SET = "fixed-handshake-invalid-cases-v1"
VERSION_TABLE_FORWARD_COMPAT_CASE_SET = "version-table-forward-compat-v1"
# MsgProposeVersions offers built with corpus_synthesizer.cbor_encode.
# Record for V11-V15: [networkMagic=42, initiatorOnly=false, peerSharing=1, query=false].
HANDSHAKE_V14_V15_OFFER_HEX = "8200a20e84182af401f40f84182af401f4"
# Exact Cardano-node 11.1.x ExperimentalProtocolsEnabled offer: NodeToNodeV_16
# appends perasSupport, so its record has five fields.
HANDSHAKE_NODE_11_1_V16_OFFER_HEX = (
    "8200a30e84182af401f40f84182af401f41085182af401f4f4"
)
# V14/V15 plus an unknown future version whose data no current node can parse.
HANDSHAKE_UNKNOWN_FUTURE_VERSION_OFFER_HEX = (
    "8200a30e84182af401f40f84182af401f4186382182a66667574757265"
)
RESPONSE_CAP_BYTES = 256

_CASE_SETS = {
    # A responder must accept the greatest common version and ignore version
    # numbers it does not know; every case here therefore expects acceptance.
    VERSION_TABLE_FORWARD_COMPAT_CASE_SET: (
        {
            "name": "v14-v15-offer",
            "payload_hex": HANDSHAKE_V14_V15_OFFER_HEX,
            "expected_external_outcome": "accepted",
            "expected_decode_outcome": "decoded",
        },
        {
            "name": "node-11-1-experimental-v16-offer",
            "payload_hex": HANDSHAKE_NODE_11_1_V16_OFFER_HEX,
            "expected_external_outcome": "accepted",
            "expected_decode_outcome": "decoded",
        },
        {
            "name": "unknown-future-version-offer",
            "payload_hex": HANDSHAKE_UNKNOWN_FUTURE_VERSION_OFFER_HEX,
            "expected_external_outcome": "accepted",
            "expected_decode_outcome": "decoded",
        },
    ),
    DEFAULT_CASE_SET: (
        {
            "name": "unsupported-version-refusal",
            "payload_hex": HANDSHAKE_UNSUPPORTED_VERSION_HEX,
            "expected_external_outcome": "rejected",
            "expected_decode_outcome": "decoded",
        },
    ),
    ACCEPTANCE_CASE_SET: (
        {
            "name": "supported-version-acceptance",
            "payload_hex": HANDSHAKE_SUPPORTED_VERSION_HEX,
            "expected_external_outcome": "accepted",
            "expected_decode_outcome": "decoded",
        },
        {
            "name": "unsupported-version-refusal",
            "payload_hex": HANDSHAKE_UNSUPPORTED_VERSION_HEX,
            "expected_external_outcome": "rejected",
            "expected_decode_outcome": "decoded",
        },
        {
            "name": "malformed-cbor-rejection",
            "payload_hex": HANDSHAKE_MALFORMED_HEX,
            "expected_external_outcome": "rejected",
            "expected_decode_outcome": "malformed",
        },
    ),
    CLIENT_INVALID_CASE_SET: (
        {
            "name": "unsupported-version-refusal",
            "payload_hex": HANDSHAKE_UNSUPPORTED_VERSION_HEX,
            "expected_external_outcome": "rejected",
            "expected_decode_outcome": "decoded",
        },
        {
            "name": "malformed-cbor",
            "payload_hex": HANDSHAKE_MALFORMED_HEX,
            "expected_external_outcome": "rejected",
            "expected_decode_outcome": "malformed",
        },
    ),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_case_plan(*, attempt_count: int, case_set: str = DEFAULT_CASE_SET) -> list[dict[str, Any]]:
    cases = _CASE_SETS.get(case_set)
    if cases is None:
        raise ValueError(f"unsupported calibration case set: {case_set}")
    if attempt_count < len(cases):
        raise ValueError("attempt count must cover every calibration case")
    quotient, remainder = divmod(attempt_count, len(cases))
    plan = []
    for index, case in enumerate(cases):
        plan.extend(dict(case) for _ in range(quotient + (1 if index < remainder else 0)))
    return plan


def build_workload_identity(
    *, attempt_count: int, seed: str = SEED, case_set: str = DEFAULT_CASE_SET
) -> dict[str, Any]:
    plan = build_case_plan(attempt_count=attempt_count, case_set=case_set)
    case_rows = []
    for case in _CASE_SETS[case_set]:
        case_rows.append(
            {
                **case,
                "attempt_count": sum(1 for row in plan if row["name"] == case["name"]),
            }
        )
    body = {
        "scenario_id": "amaru-measurement-overhead-calibration",
        "seed": seed,
        "attempt_count": int(attempt_count),
        "mini_protocol": "handshake",
        "case_set": case_set,
        "cases": case_rows,
        "network_magic": 42,
        "transport": "tcp",
        "target_port": 3000,
        "mux_direction_bit": 0,
        "response_cap_bytes": RESPONSE_CAP_BYTES,
    }
    digest = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {**body, "workload_digest": f"sha256:{digest}"}


def _sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def build_attempt_record(
    *,
    attempt_id: str,
    outcome: str,
    detail: str | None,
    elapsed_micros: int,
    request: bytes,
    response: bytes,
) -> dict[str, Any]:
    """Retain one bounded, outcome-independent timing and wire transcript."""
    return {
        "attempt_id": attempt_id,
        "outcome": outcome,
        "detail": detail,
        "elapsed_micros": elapsed_micros,
        "request_length": len(request),
        "request_sha256": _sha256_bytes(request),
        "request_hex": request.hex(),
        "response_length": len(response),
        "response_sha256": _sha256_bytes(response),
        "response_hex": response.hex(),
    }


def _physical_memory_bytes() -> int | None:
    try:
        return int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def build_result_context(*, output_dir: Path) -> dict[str, Any]:
    system = platform.uname()
    script = Path(__file__).resolve()
    return {
        "run_id": output_dir.name,
        "timing_policy": {
            "clock": "monotonic-perf-counter-ns",
            "start": "before-tcp-connect",
            "stop": "terminal-response-eof-timeout-or-disconnect",
            "warmup": "none",
            "outcome_inclusion": "all",
        },
        "hardware": {
            "system": system.system,
            "kernel_release": system.release,
            "architecture": system.machine,
            "logical_cpu_count": os.cpu_count(),
            "physical_memory_bytes": _physical_memory_bytes(),
        },
        "runner": {
            "script": script.name,
            "script_sha256": _sha256_bytes(script.read_bytes()),
        },
    }


def _distribution(records: list[dict[str, Any]]) -> dict[str, Any]:
    return distribution_summary(
        [{"value": row["elapsed_micros"], "unit": "us"} for row in records],
        unit="us",
    )


def summarize_attempts(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    outcomes = sorted({str(row["outcome"]) for row in attempts})
    return {
        "combined": _distribution(attempts),
        "by_outcome": {
            outcome: _distribution(
                [row for row in attempts if str(row["outcome"]) == outcome]
            )
            for outcome in outcomes
        },
    }


def build_handshake_frame(payload_hex: str = HANDSHAKE_UNSUPPORTED_VERSION_HEX) -> bytes:
    """Build an inbound handshake SDU for the node's responder-side bearer."""
    return _encode_mux_sdu(
        bytes.fromhex(payload_hex),
        mini_protocol_num=0,
        initiator=False,
    )


def classify_unsupported_handshake_response(response: bytes) -> tuple[str, str]:
    """Classify the terminal response to the fixed unsupported-version proposal."""
    return ("rejected", "refuse-response" if response else "eof")


def _handshake_response_tag(response: bytes) -> int | None:
    if len(response) < 10:
        return None
    header_word = int.from_bytes(response[4:8], "big")
    mini_protocol_num = (header_word >> 16) & 0x7FFF
    payload_length = header_word & 0xFFFF
    if mini_protocol_num != 0 or payload_length < 2:
        return None
    if len(response) < 8 + payload_length:
        return None
    payload = response[8 : 8 + payload_length]
    if not 0x80 <= payload[0] <= 0x97 or payload[1] > 0x17:
        return None
    return payload[1]


def classify_case_response(case_name: str, response: bytes) -> tuple[str, str]:
    if not response:
        return (
            ("disconnected", "eof")
            if case_name == "supported-version-acceptance"
            else ("rejected", "eof")
        )
    response_tag = _handshake_response_tag(response)
    if response_tag == 1:
        return ("accepted", "accept-response")
    if response_tag == 2:
        return ("rejected", "refuse-response")
    return ("unclassified", "invalid-handshake-response")


def _recv_mux_frame(sock, *, max_bytes: int = RESPONSE_CAP_BYTES) -> bytes:
    def read_exact(length: int) -> bytes:
        chunks = []
        remaining = length
        while remaining > 0:
            chunk = sock.recv(remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    header = read_exact(8)
    if len(header) < 8:
        return header
    payload_length = int.from_bytes(header[6:8], "big")
    retained_length = min(payload_length, max(0, max_bytes - len(header)))
    return header + read_exact(retained_length)


def _run(command: list[str], *, timeout: float = 30.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _compose_container(project: str, service: str) -> str:
    result = _run(
        [
            "docker",
            "ps",
            "-q",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--filter",
            f"label=com.docker.compose.service={service}",
        ]
    )
    containers = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if result.returncode != 0 or len(containers) != 1:
        raise RuntimeError(
            result.stderr.strip()
            or f"expected one live container for {project}/{service}, found {len(containers)}"
        )
    return containers[0]


def _container_ip(container: str) -> str:
    result = _run(["docker", "inspect", container])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"cannot inspect {container}")
    body = json.loads(result.stdout)[0]
    for network in ((body.get("NetworkSettings") or {}).get("Networks") or {}).values():
        address = network.get("IPAddress")
        if address:
            return str(address)
    raise RuntimeError(f"container has no routable address: {container}")


def run_attempts(
    *,
    host: str,
    port: int,
    attempt_count: int,
    timeout_seconds: float,
    case_set: str = DEFAULT_CASE_SET,
    midpoint_callback: Callable[[], None] | None = None,
    attempt_interval_seconds: float = 0.0,
) -> list[dict[str, Any]]:
    if attempt_interval_seconds < 0:
        raise ValueError("attempt_interval_seconds must be non-negative")
    records = []
    plan = build_case_plan(attempt_count=attempt_count, case_set=case_set)
    schedule_started = time.monotonic()
    for index, case in enumerate(plan):
        if index and attempt_interval_seconds:
            scheduled = schedule_started + (index * attempt_interval_seconds)
            remaining = scheduled - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)
        if midpoint_callback is not None and index == len(plan) // 2:
            midpoint_callback()
        frame = build_handshake_frame(case["payload_hex"])
        started_at = _utc_now()
        started = time.perf_counter_ns()
        outcome = "unclassified"
        detail = None
        response = b""
        listener_reached = False
        try:
            with socket.create_connection((host, port), timeout=timeout_seconds) as sock:
                listener_reached = True
                sock.settimeout(timeout_seconds)
                sock.sendall(frame)
                try:
                    response = _recv_mux_frame(sock)
                    outcome, detail = classify_case_response(case["name"], response)
                except socket.timeout:
                    outcome = "timeout"
                    detail = "response-timeout"
                except ConnectionResetError:
                    outcome = "rejected"
                    detail = "connection-reset"
        except TimeoutError:
            outcome = "timeout"
            detail = "connect-timeout"
        except OSError as exc:
            outcome = "disconnected"
            detail = type(exc).__name__
        elapsed_micros = max(0, (time.perf_counter_ns() - started) // 1000)
        completed_at = _utc_now()
        records.append(
            {
                **build_attempt_record(
                attempt_id=f"handshake-{index:04d}",
                outcome=outcome,
                detail=detail,
                elapsed_micros=elapsed_micros,
                request=frame,
                response=response,
                ),
                "case": case["name"],
                "started_at": started_at,
                "completed_at": completed_at,
                "payload_hex": case["payload_hex"],
                "target_endpoint": f"{host}:{port}",
                "listener_reached": listener_reached,
                "expected_external_outcome": case["expected_external_outcome"],
                "expected_decode_outcome": case["expected_decode_outcome"],
            }
        )
    return records


def _target_identity(runtime: dict[str, Any]) -> dict[str, Any]:
    measurement_target = runtime.get("measurement_target")
    if isinstance(measurement_target, dict) and measurement_target.get("target_mode") == "patched":
        return {
            "implementation": "amaru",
            "version": measurement_target["version"],
            "source_revision": measurement_target["source_revision"],
            "mode": "patched",
            "image_digest": measurement_target["image_digest"],
            "executable_digest": measurement_target["executable_digest"],
            "patch_set_sha256": measurement_target["patch_set_sha256"],
        }
    snapshot = ((runtime.get("versions") or {}).get("catalog_snapshot") or {})
    release = next(
        (
            item
            for item in snapshot.get("selected_releases") or []
            if item.get("implementation") == "amaru"
        ),
        None,
    )
    if not release:
        raise RuntimeError("runtime metadata has no exact Amaru release identity")
    service = ((runtime.get("identity") or {}).get("services") or {}).get("amaru-relay-1") or {}
    return {
        "implementation": "amaru",
        "version": release["version"],
        "source_revision": release["source_revision"],
        "mode": "stock",
        "image_digest": service.get("artifact_image_id") or service.get("expected_image_id"),
    }


def _capture_logs(container: str, *, since: str, destination: Path) -> None:
    result = _run(["docker", "logs", "--since", since, container], timeout=120)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        result.stdout + ("\n" + result.stderr if result.stderr else ""),
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"cannot capture logs from {container}")


def _container_state(container: str) -> dict[str, Any]:
    result = _run(["docker", "inspect", container])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"cannot inspect {container}")
    body = json.loads(result.stdout)[0]
    state = body.get("State") or {}
    return {
        "running": state.get("Running") is True,
        "status": state.get("Status"),
        "exit_code": state.get("ExitCode"),
        "oom_killed": state.get("OOMKilled") is True,
        "restart_count": int(body.get("RestartCount") or 0),
    }


def _independent_peer(runtime: dict[str, Any]) -> tuple[str, str]:
    peer_id = str((runtime.get("actual_topology") or {}).get("isolated_consumer") or "")
    service = ((runtime.get("identity") or {}).get("services") or {}).get(peer_id) or {}
    container = str(service.get("container") or "")
    if not peer_id or not container:
        raise RuntimeError("Amaru runtime has no controlled independent consumer peer")
    return peer_id, container


def _query_cardano_tip(
    container: str, *, socket_path: str = "/state/node.socket", network_magic: int = 42
) -> dict[str, Any] | None:
    result = _run(
        [
            "docker", "exec", container, "cardano-cli", "query", "tip",
            "--socket-path", socket_path, "--testnet-magic", str(network_magic),
        ],
        timeout=30,
    )
    if result.returncode != 0:
        return None
    try:
        body = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    height = body.get("block")
    if isinstance(height, bool) or not isinstance(height, int) or not body.get("hash"):
        return None
    return {
        "block_height": height,
        "hash": body.get("hash"),
        "slot": body.get("slot"),
    }


def _peer_observation(peer_id: str, container: str, network_magic: int) -> dict[str, Any]:
    tip = _query_cardano_tip(container, network_magic=network_magic)
    return {
        "observed_at_epoch_seconds": time.time(),
        "peer_id": peer_id,
        "container": container,
        "usable": tip is not None,
        "tip": tip,
    }


def _latest_tip(container: str) -> dict[str, Any] | None:
    result = _run(["docker", "logs", "--tail", "8000", container], timeout=60)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"cannot read logs from {container}")
    return extract_latest_adopted_tip(result.stdout + "\n" + result.stderr)


def _wait_for_progress(
    container: str, *, start_height: int, timeout_seconds: float
) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        tip = _latest_tip(container)
        if tip is not None and int(tip.get("block_height", -1)) > start_height:
            return tip
        time.sleep(min(2.0, max(0.1, deadline - time.monotonic())))
    return _latest_tip(container)


def _patched_measurements(
    *, output_dir: Path, trace_path: Path, target: dict[str, Any]
) -> dict[str, Any]:
    results = {}
    for measurement_id in PATCHED_MEASUREMENT_IDS:
        entry = {"id": measurement_id, "definition": {"id": measurement_id}}
        context = CollectorContext(
            measurement_id=measurement_id,
            definition=entry["definition"],
            parameters={},
            run_dir=output_dir,
            collector_dir=output_dir / "measurements" / "collectors" / measurement_id,
        )
        collector = AmaruPatchedCollector(
            entry,
            json_trace_paths=[trace_path],
            target_identity=target,
            include_existing=True,
        )
        collector.prepare(context)
        collector.start(context)
        collector.stop(context)
        results[measurement_id] = collector.finalize(context)
    return results


def run_leg(
    *,
    runtime_root: Path,
    output_dir: Path,
    attempt_count: int,
    timeout_seconds: float,
    case_set: str = DEFAULT_CASE_SET,
    progress_timeout_seconds: float = 120.0,
    attempt_interval_seconds: float = 0.0,
) -> dict[str, Any]:
    runtime_path = runtime_root / "runtime.json"
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    target = _target_identity(runtime)
    project = str(runtime["compose_project"])
    service = str((runtime.get("actual_topology") or {}).get("amaru_services", ["amaru-relay-1"])[0])
    container = _compose_container(project, service)
    host = _container_ip(container)
    output_dir.mkdir(parents=True, exist_ok=True)
    state_before = _container_state(container)
    tip_before = _latest_tip(container)
    if tip_before is None:
        raise RuntimeError("Amaru target logs contain no adopted pre-workload tip")
    started_at = _utc_now()
    peer_id = None
    peer_container = None
    peer_before = None
    peer_during = None
    network_magic = int(runtime.get("network_magic") or 42)
    if case_set == CLIENT_INVALID_CASE_SET:
        peer_id, peer_container = _independent_peer(runtime)
        peer_before = _peer_observation(peer_id, peer_container, network_magic)

    def observe_midpoint() -> None:
        nonlocal peer_during
        if peer_id is not None and peer_container is not None:
            peer_during = _peer_observation(peer_id, peer_container, network_magic)

    attempts = run_attempts(
        host=host,
        port=3000,
        attempt_count=attempt_count,
        timeout_seconds=timeout_seconds,
        case_set=case_set,
        midpoint_callback=(observe_midpoint if case_set == CLIENT_INVALID_CASE_SET else None),
        attempt_interval_seconds=attempt_interval_seconds,
    )
    peer_after = (
        _peer_observation(peer_id, peer_container, network_magic)
        if peer_id is not None and peer_container is not None
        else None
    )
    tip_after = _wait_for_progress(
        container,
        start_height=int(tip_before["block_height"]),
        timeout_seconds=progress_timeout_seconds,
    )
    state_after = _container_state(container)
    trace_path = output_dir / "raw" / "amaru-relay-1.ndjson"
    _capture_logs(container, since=started_at, destination=trace_path)
    trace_text = trace_path.read_text(encoding="utf-8")
    log_signals = classify_log_signals(trace_text)
    attempts_path = output_dir / "attempts.ndjson"
    attempts_path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in attempts),
        encoding="utf-8",
    )
    summarized = summarize_attempts(attempts)
    unexpected_attempts = [
        row for row in attempts if row["outcome"] != row["expected_external_outcome"]
    ]
    progressed = (
        tip_after is not None
        and int(tip_after.get("block_height", -1)) > int(tip_before["block_height"])
    )
    security_checks = {
        "all_attempts_classified_as_expected": not unexpected_attempts,
        "target_running_before": state_before["running"],
        "target_running_after": state_after["running"],
        "target_not_oom_killed": not state_after["oom_killed"],
        "target_restart_count_unchanged": (
            state_after["restart_count"] == state_before["restart_count"]
        ),
        "honest_chain_progressed": progressed,
        "no_fatal_signals": not log_signals["fatal"],
    }
    result = {
        "schema_version": 1,
        "status": "available" if all(security_checks.values()) else "unavailable",
        "created_at": _utc_now(),
        **build_result_context(output_dir=output_dir),
        "target": target,
        "workload_identity": build_workload_identity(
            attempt_count=attempt_count, case_set=case_set
        ),
        "measurements": {"handshake_rejection_roundtrip": summarized["combined"]},
        "measurements_by_outcome": {
            "handshake_rejection_roundtrip": summarized["by_outcome"]
        },
        "attempts": {
            "total": len(attempts),
            "outcomes": {
                outcome: sum(1 for row in attempts if row["outcome"] == outcome)
                for outcome in sorted({row["outcome"] for row in attempts})
            },
            "artifact": "attempts.ndjson",
            "unexpected_count": len(unexpected_attempts),
            "by_case": {
                case["name"]: {
                    "total": sum(1 for row in attempts if row["case"] == case["name"]),
                    "outcomes": {
                        outcome: sum(
                            1
                            for row in attempts
                            if row["case"] == case["name"] and row["outcome"] == outcome
                        )
                        for outcome in sorted(
                            {row["outcome"] for row in attempts if row["case"] == case["name"]}
                        )
                    },
                }
                for case in _CASE_SETS[case_set]
            },
        },
        "target_health": {
            "before": state_before,
            "after": state_after,
            "tip_before": tip_before,
            "tip_after": tip_after,
            "log_signals": log_signals,
            "checks": security_checks,
            "observer": {
                "implementation": "amaru",
                "container": container,
                "started_at": started_at,
            },
        },
        **(
            {
                "peer_session": {
                    "peer_id": peer_id,
                    "before": peer_before,
                    "during": peer_during,
                    "after": peer_after,
                    "recovered_within_seconds": (
                        0.0
                        if (peer_during or {}).get("usable") is True
                        else (
                            max(
                                0.0,
                                float((peer_after or {}).get("observed_at_epoch_seconds") or 0)
                                - float((peer_during or {}).get("observed_at_epoch_seconds") or 0),
                            )
                            if (peer_after or {}).get("usable") is True and peer_during
                            else None
                        )
                    ),
                    "observer": {
                        "peer_id": peer_id,
                        "container": peer_container,
                        "network_magic": network_magic,
                        "socket_path": "/state/node.socket",
                    },
                }
            }
            if case_set == CLIENT_INVALID_CASE_SET
            else {}
        ),
        "raw_trace": "raw/amaru-relay-1.ndjson",
        "node_measurements": (
            _patched_measurements(output_dir=output_dir, trace_path=trace_path, target=target)
            if target["mode"] == "patched"
            else {}
        ),
    }
    (output_dir / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    leg = sub.add_parser("leg")
    leg.add_argument("--runtime-root", type=Path, required=True)
    leg.add_argument("--output-dir", type=Path, required=True)
    leg.add_argument("--attempts", type=int, default=40)
    leg.add_argument("--timeout-seconds", type=float, default=2.0)
    leg.add_argument("--case-set", choices=sorted(_CASE_SETS), default=DEFAULT_CASE_SET)
    leg.add_argument("--progress-timeout-seconds", type=float, default=120.0)
    leg.add_argument("--attempt-interval-seconds", type=float, default=0.0)
    pair = sub.add_parser("pair")
    pair.add_argument("--stock", type=Path, required=True)
    pair.add_argument("--patched", type=Path, required=True)
    pair.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "leg":
        if args.attempts < 30 or args.attempts > 10_000:
            raise SystemExit("--attempts must be within 30..10000")
        result = run_leg(
            runtime_root=args.runtime_root,
            output_dir=args.output_dir,
            attempt_count=args.attempts,
            timeout_seconds=args.timeout_seconds,
            case_set=args.case_set,
            progress_timeout_seconds=args.progress_timeout_seconds,
            attempt_interval_seconds=args.attempt_interval_seconds,
        )
    else:
        stock = json.loads(args.stock.read_text(encoding="utf-8"))
        patched = json.loads(args.patched.read_text(encoding="utf-8"))
        result = paired_overhead_calibration(stock, patched, minimum_samples=30)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("status", "available") == "available" else 2


if __name__ == "__main__":
    raise SystemExit(main())
