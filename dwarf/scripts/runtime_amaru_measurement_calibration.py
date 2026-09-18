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
from typing import Any

from profile_manager.measurement_collectors.amaru_patched import (
    PATCHED_MEASUREMENT_IDS,
    AmaruPatchedCollector,
    paired_overhead_calibration,
)
from profile_manager.measurement_report import distribution_summary
from profile_manager.measurement_runtime import CollectorContext
from scripts.runtime_txsubmission_probe import _encode_mux_sdu


SEED = "0xA11CE501"
HANDSHAKE_UNSUPPORTED_VERSION_HEX = "8200a11903e782182af4"
RESPONSE_CAP_BYTES = 256


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_workload_identity(*, attempt_count: int, seed: str = SEED) -> dict[str, Any]:
    body = {
        "scenario_id": "amaru-measurement-overhead-calibration",
        "seed": seed,
        "attempt_count": int(attempt_count),
        "mini_protocol": "handshake",
        "case": "unsupported-version-refusal",
        "payload_hex": HANDSHAKE_UNSUPPORTED_VERSION_HEX,
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


def build_handshake_frame() -> bytes:
    """Build an inbound handshake SDU for the node's responder-side bearer."""
    return _encode_mux_sdu(
        bytes.fromhex(HANDSHAKE_UNSUPPORTED_VERSION_HEX),
        mini_protocol_num=0,
        initiator=False,
    )


def classify_unsupported_handshake_response(response: bytes) -> tuple[str, str]:
    """Classify the terminal response to the fixed unsupported-version proposal."""
    return ("rejected", "refuse-response" if response else "eof")


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
    *, host: str, port: int, attempt_count: int, timeout_seconds: float
) -> list[dict[str, Any]]:
    frame = build_handshake_frame()
    records = []
    for index in range(attempt_count):
        started = time.perf_counter_ns()
        outcome = "unclassified"
        detail = None
        response = b""
        try:
            with socket.create_connection((host, port), timeout=timeout_seconds) as sock:
                sock.settimeout(timeout_seconds)
                sock.sendall(frame)
                try:
                    response = sock.recv(RESPONSE_CAP_BYTES)
                    outcome, detail = classify_unsupported_handshake_response(response)
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
        records.append(
            build_attempt_record(
                attempt_id=f"handshake-{index:04d}",
                outcome=outcome,
                detail=detail,
                elapsed_micros=elapsed_micros,
                request=frame,
                response=response,
            )
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
    *, runtime_root: Path, output_dir: Path, attempt_count: int, timeout_seconds: float
) -> dict[str, Any]:
    runtime_path = runtime_root / "runtime.json"
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    target = _target_identity(runtime)
    project = str(runtime["compose_project"])
    service = str((runtime.get("actual_topology") or {}).get("amaru_services", ["amaru-relay-1"])[0])
    container = _compose_container(project, service)
    host = _container_ip(container)
    output_dir.mkdir(parents=True, exist_ok=True)
    started_at = _utc_now()
    attempts = run_attempts(
        host=host,
        port=3000,
        attempt_count=attempt_count,
        timeout_seconds=timeout_seconds,
    )
    trace_path = output_dir / "raw" / "amaru-relay-1.ndjson"
    _capture_logs(container, since=started_at, destination=trace_path)
    attempts_path = output_dir / "attempts.ndjson"
    attempts_path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in attempts),
        encoding="utf-8",
    )
    summarized = summarize_attempts(attempts)
    result = {
        "schema_version": 1,
        "created_at": _utc_now(),
        **build_result_context(output_dir=output_dir),
        "target": target,
        "workload_identity": build_workload_identity(attempt_count=attempt_count),
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
        },
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
