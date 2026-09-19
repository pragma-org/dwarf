#!/usr/bin/env python3
"""Run one bounded real Cardano-node measurement workload leg."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.runtime_amaru_measurement_calibration import (
    HANDSHAKE_UNSUPPORTED_VERSION_HEX,
    RESPONSE_CAP_BYTES,
    build_attempt_record,
    run_attempts,
    summarize_attempts,
)


SEED = "0xCA4DA001"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _physical_memory_bytes() -> int | None:
    try:
        return int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def build_workload_identity(*, attempt_count: int, seed: str = SEED) -> dict[str, Any]:
    body = {
        "scenario_id": "cardano-measurement-e2e-stock",
        "implementation": "cardano-node",
        "seed": seed,
        "attempt_count": int(attempt_count),
        "mini_protocol": "handshake",
        "case": "unsupported-version-refusal",
        "payload_hex": HANDSHAKE_UNSUPPORTED_VERSION_HEX,
        "network_magic": 42,
        "transport": "tcp",
        "target_port": 3001,
        "mux_direction_bit": 0,
        "response_cap_bytes": RESPONSE_CAP_BYTES,
    }
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return {**body, "workload_digest": _sha256_bytes(encoded)}


def _result_context(output_dir: Path) -> dict[str, Any]:
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
        "runner": {"script": script.name, "script_sha256": _sha256_bytes(script.read_bytes())},
    }


def _run(command: list[str], *, timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, check=False, timeout=timeout)


def _container_ip(container: str) -> str:
    result = _run(["docker", "inspect", container])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"cannot inspect {container}")
    body = json.loads(result.stdout)[0]
    for network in ((body.get("NetworkSettings") or {}).get("Networks") or {}).values():
        if network.get("IPAddress"):
            return str(network["IPAddress"])
    raise RuntimeError(f"container has no routable address: {container}")


def _release(runtime: dict[str, Any]) -> dict[str, Any]:
    snapshot = ((runtime.get("version_provenance") or {}).get("catalog_snapshot") or {})
    for release in snapshot.get("selected_releases") or []:
        if release.get("implementation") == "cardano-node":
            return release
    raise RuntimeError("runtime metadata has no exact Cardano-node release")


def _target(runtime: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    release = _release(runtime)
    node = next(
        (node for node in (runtime.get("nodes") or []) if node.get("impl") == "cardano-node"),
        None,
    )
    if node is None:
        raise RuntimeError("runtime metadata has no Cardano-node target")
    artifact = node.get("artifact_identity") or {}
    version = node.get("version_identity") or {}
    expected_digest = next(
        item["digest"]
        for item in release.get("artifacts") or []
        if item.get("kind") == "oci" and item.get("availability") == "available"
    )
    observed_digest = artifact.get("image_digest") or artifact.get("container_image_id")
    if artifact.get("satisfied") is not True or version.get("satisfied") is not True:
        raise RuntimeError("Cardano-node runtime identity is not proven")
    if observed_digest != expected_digest or node.get("source_revision") != release.get("source_revision"):
        raise RuntimeError("Cardano-node runtime identity does not match the catalog")
    return ({
        "implementation": "cardano-node",
        "version": release["version"],
        "source_revision": release["source_revision"],
        "mode": "stock",
        "image_digest": expected_digest,
        "image_reference": node.get("image_ref"),
    }, node)


def _capture_logs(container: str, *, since: str, destination: Path) -> dict[str, Any]:
    result = _run(["docker", "logs", "--since", since, container], timeout=120)
    destination.parent.mkdir(parents=True, exist_ok=True)
    body = result.stdout + ("\n" + result.stderr if result.stderr else "")
    destination.write_text(body, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"cannot capture logs from {container}")
    record_count = 0
    protocol_record_count = 0
    namespaces: set[str] = set()
    for line in body.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        record_count += 1
        namespace = record.get("ns")
        if isinstance(namespace, str):
            namespaces.add(namespace)
            if namespace.startswith(
                ("Net.Handshake.", "ChainSync.", "BlockFetch.", "TxSubmission.", "Mempool")
            ) or namespace.startswith("ChainDB."):
                protocol_record_count += 1
    return {
        "record_count": record_count,
        "protocol_record_count": protocol_record_count,
        "byte_count": len(body.encode("utf-8")),
        "namespaces": sorted(namespaces),
    }


def run_leg(
    *,
    runtime_root: Path,
    output_dir: Path,
    attempt_count: int,
    timeout_seconds: float,
    observation_seconds: float = 2.0,
    trace_timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    runtime = json.loads((runtime_root / "runtime.json").read_text(encoding="utf-8"))
    target, node = _target(runtime)
    container = str(node.get("container_name") or "")
    if not container:
        raise RuntimeError("Cardano-node target has no container identity")
    output_dir.mkdir(parents=True, exist_ok=True)
    started_at = _utc_now()
    attempts = run_attempts(
        host=_container_ip(container),
        port=3001,
        attempt_count=attempt_count,
        timeout_seconds=timeout_seconds,
    )
    attempts_path = output_dir / "attempts.ndjson"
    attempts_path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in attempts),
        encoding="utf-8",
    )
    trace_path = output_dir / "raw" / "node1.ndjson"
    observation_started = time.monotonic()
    time.sleep(observation_seconds)
    while True:
        trace_evidence = _capture_logs(
            container, since=started_at, destination=trace_path
        )
        if trace_evidence["protocol_record_count"] > 0:
            break
        elapsed = time.monotonic() - observation_started
        if elapsed >= trace_timeout_seconds:
            break
        time.sleep(min(1.0, trace_timeout_seconds - elapsed))
    observed_seconds = time.monotonic() - observation_started
    summarized = summarize_attempts(attempts)
    result = {
        "schema_version": 1,
        "created_at": _utc_now(),
        **_result_context(output_dir),
        "target": target,
        "workload_identity": build_workload_identity(attempt_count=attempt_count),
        "measurements": {"handshake_rejection_roundtrip": summarized["combined"]},
        "measurements_by_outcome": {"handshake_rejection_roundtrip": summarized["by_outcome"]},
        "attempts": {
            "total": len(attempts),
            "outcomes": {
                outcome: sum(row["outcome"] == outcome for row in attempts)
                for outcome in sorted({row["outcome"] for row in attempts})
            },
            "artifact": "attempts.ndjson",
        },
        "raw_trace": "raw/node1.ndjson",
        "node_trace": {
            **trace_evidence,
            "minimum_observation_seconds": observation_seconds,
            "observation_timeout_seconds": trace_timeout_seconds,
            "observed_seconds": observed_seconds,
        },
    }
    (output_dir / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["leg"])
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--attempts", type=int, default=100)
    parser.add_argument("--timeout-seconds", type=float, default=2.0)
    parser.add_argument("--observation-seconds", type=float, default=2.0)
    parser.add_argument("--trace-timeout-seconds", type=float, default=20.0)
    args = parser.parse_args(argv)
    if args.attempts < 30 or args.attempts > 10_000:
        raise SystemExit("--attempts must be within 30..10000")
    if args.observation_seconds < 0.5 or args.observation_seconds > 30:
        raise SystemExit("--observation-seconds must be within 0.5..30")
    if (
        args.trace_timeout_seconds < args.observation_seconds
        or args.trace_timeout_seconds > 120
    ):
        raise SystemExit(
            "--trace-timeout-seconds must be at least observation-seconds and at most 120"
        )
    result = run_leg(
        runtime_root=args.runtime_root,
        output_dir=args.output_dir,
        attempt_count=args.attempts,
        timeout_seconds=args.timeout_seconds,
        observation_seconds=args.observation_seconds,
        trace_timeout_seconds=args.trace_timeout_seconds,
    )
    print(json.dumps(result, sort_keys=True))
    trace = result["node_trace"]
    return 0 if trace["record_count"] > 0 and trace["protocol_record_count"] > 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
