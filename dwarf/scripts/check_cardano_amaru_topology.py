#!/usr/bin/env python3
"""Read-only health probe for DWARF's pre-staged Cardano/Amaru topology."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


DWARF_ROOT = Path(__file__).resolve().parents[1]
if str(DWARF_ROOT) not in sys.path:
    sys.path.insert(0, str(DWARF_ROOT))

from profile_manager.topology_health import (  # noqa: E402
    AMARU_CONSUMER,
    AMARU_RELAYS,
    CARDANO_REFERENCE_NODES,
    classify_topology_health,
)


TOPOLOGY_ID = "cardano_amaru"
DEFAULT_PROJECT = "cardano_amaru_relay_bootstrap_control"
REQUIRED_SERVICES = (
    "p1",
    "p2",
    "p3",
    "relay1",
    "relay2",
    *AMARU_RELAYS,
    AMARU_CONSUMER,
)
EXPECTED_CONSUMER_PEERS = {
    "amaru-relay-1.example",
    "amaru-relay-2.example",
}
FATAL_PATTERNS = {
    "panic": re.compile(r"\bpanicked at\b", re.IGNORECASE),
    "listener-address-in-use": re.compile(r"EADDRINUSE|Address already in use", re.IGNORECASE),
    "reward-discrepancy": re.compile(
        r"discrepancy between expected total rewards", re.IGNORECASE
    ),
    "consensus-died": re.compile(r"\bConsensus died\b", re.IGNORECASE),
    "future-rollback": re.compile(r"attempted roll back in the future", re.IGNORECASE),
    "vrf-bad-proof": re.compile(r"VRFKeyBadProof", re.IGNORECASE),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def run_command(args: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def parse_amaru_progress(text: str) -> dict[str, Any]:
    """Return the last explicit current/highest point from existing Amaru logs."""
    target_matches = re.findall(
        r"snapshot_slots=([0-9]+) ([0-9]+) ([0-9]+)", text
    )
    target_slots = (
        [int(value) for value in target_matches[-1]] if target_matches else []
    )
    current_pattern = re.compile(
        r'current="\[([0-9]+),\s*h\'([^\']+)\',\s*([0-9]+)\]"'
    )
    adopted_pattern = re.compile(
        r'\btip\.adopt\s+slot=([0-9]+)\s+header_hash="([^"]+)"'
        r'\s+block_height=([0-9]+)\b'
    )
    adopted_tip_pattern = re.compile(
        r'\badopted tip\s+tip\.slot=([0-9]+)\s+tip\.hash=([^\s]+)'
        r'\s+tip\.block_height=([0-9]+)\b'
    )
    current_point = None
    for line in text.splitlines():
        if match := current_pattern.search(line):
            current_point = match.groups()
        if match := adopted_pattern.search(line):
            current_point = match.groups()
        if match := adopted_tip_pattern.search(line):
            current_point = match.groups()
    highest = re.findall(
        r'highest="\[([0-9]+),\s*h\'([^\']+)\',\s*([0-9]+)\]"', text
    )
    # Retain compatibility with the older structured Debug rendering.
    if current_point is None:
        legacy_current = [
            (slot, "", "0")
            for slot in re.findall(r"current=Point[^\n]*?slot:\s*Slot\(([0-9]+)\)", text)
        ]
        current_point = legacy_current[-1] if legacy_current else None
    if not highest:
        highest = [
            (slot, hash_value, "0")
            for slot, hash_value in re.findall(
                r"\bhighest=([0-9]+)\.([0-9a-fA-F]+)\b", text
            )
        ]
    if not highest:
        highest = [
            (slot, "", "0")
            for slot in re.findall(r"highest=Point[^\n]*?slot:\s*Slot\(([0-9]+)\)", text)
        ]
    highest_point = highest[-1] if highest else None
    return {
        "target_slots": target_slots,
        "committed": "committed bundle to" in text,
        "exec_started": "exec'ing amaru run" in text,
        "current_slot": int(current_point[0]) if current_point else None,
        "current_hash": current_point[1] if current_point else None,
        "current_block": int(current_point[2]) if current_point else None,
        "highest_slot": int(highest_point[0]) if highest_point else None,
        "highest_hash": highest_point[1] if highest_point else None,
        "highest_block": int(highest_point[2]) if highest_point else None,
        "fatal_signatures": [
            name for name, pattern in FATAL_PATTERNS.items() if pattern.search(text)
        ],
    }


def consumer_uses_only_amaru(text: str) -> bool | None:
    try:
        topology = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return None
    local_roots = topology.get("localRoots")
    public_roots = topology.get("publicRoots")
    if not isinstance(local_roots, list) or not isinstance(public_roots, list):
        return None
    if public_roots:
        return False
    addresses = {
        point.get("address")
        for root in local_roots
        if isinstance(root, dict)
        for point in root.get("accessPoints", [])
        if isinstance(point, dict) and isinstance(point.get("address"), str)
    }
    return addresses == EXPECTED_CONSUMER_PEERS


def _container_names(project: str) -> dict[str, list[str]]:
    result = run_command(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--format",
            '{{.Names}}\t{{.Label "com.docker.compose.service"}}',
        ]
    )
    if result.returncode != 0:
        return {}
    found: dict[str, list[str]] = {}
    for raw in result.stdout.splitlines():
        container, separator, service = raw.partition("\t")
        if separator and container and service:
            found.setdefault(service, []).append(container)
    return found


def read_project_containers(project: str) -> dict[str, dict[str, Any]]:
    names = _container_names(project)
    containers: dict[str, dict[str, Any]] = {}
    for service in REQUIRED_SERVICES:
        candidates = names.get(service) or []
        if len(candidates) != 1:
            containers[service] = {
                "present": False,
                "running": False,
                "restart_count": 0,
                "oom_killed": False,
                "image": None,
                "image_digest": None,
                "fatal_signatures": [],
                "container_name": None,
                "candidate_count": len(candidates),
            }
            continue
        container_name = candidates[0]
        inspected = run_command(["docker", "inspect", container_name])
        try:
            body = json.loads(inspected.stdout)[0] if inspected.returncode == 0 else {}
        except (json.JSONDecodeError, IndexError, TypeError):
            body = {}
        state = body.get("State") or {}
        config = body.get("Config") or {}
        containers[service] = {
            "present": bool(body),
            "running": bool(state.get("Running")),
            "restart_count": int(body.get("RestartCount") or 0),
            "oom_killed": bool(state.get("OOMKilled")),
            "image": config.get("Image"),
            "image_digest": body.get("Image"),
            "fatal_signatures": [],
            "container_name": container_name,
            "started_at": state.get("StartedAt"),
            "finished_at": state.get("FinishedAt"),
            "status": state.get("Status"),
        }
    return containers


def query_cardano_tip(container: str) -> dict[str, Any] | None:
    result = run_command(
        [
            "docker",
            "exec",
            container,
            "cardano-cli",
            "query",
            "tip",
            "--socket-path",
            "/state/node.socket",
            "--testnet-magic",
            "42",
        ],
        timeout=20,
    )
    if result.returncode != 0:
        return None
    try:
        tip = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(tip.get("slot"), int) or not tip.get("hash"):
        return None
    return {
        "slot": tip["slot"],
        "block": tip.get("block"),
        "hash": tip["hash"],
        "syncProgress": tip.get("syncProgress"),
    }


def read_sample(containers: dict[str, dict[str, Any]]) -> dict[str, Any]:
    tips: dict[str, Any] = {}
    for service in (*CARDANO_REFERENCE_NODES, AMARU_CONSUMER):
        container = (containers.get(service) or {}).get("container_name")
        if container and (tip := query_cardano_tip(container)) is not None:
            tips[service] = tip

    amaru_relays: dict[str, Any] = {}
    for service in AMARU_RELAYS:
        container = (containers.get(service) or {}).get("container_name")
        if not container:
            continue
        logs = run_command(["docker", "logs", "--tail", "8000", container], timeout=45)
        parsed = parse_amaru_progress(logs.stdout + logs.stderr)
        amaru_relays[service] = parsed
        if parsed["fatal_signatures"]:
            containers[service]["fatal_signatures"] = list(parsed["fatal_signatures"])
    return {"at": _utc_now(), "tips": tips, "amaru_relays": amaru_relays}


def read_peer_contract(containers: dict[str, dict[str, Any]]) -> bool | None:
    container = (containers.get(AMARU_CONSUMER) or {}).get("container_name")
    if not container:
        return None
    result = run_command(
        [
            "docker",
            "exec",
            container,
            "cat",
            "/configs/configs/topology.json",
        ],
        timeout=20,
    )
    if result.returncode != 0:
        return None
    return consumer_uses_only_amaru(result.stdout)


def collect_and_classify(
    *,
    project: str,
    output: Path,
    sample_seconds: float,
    container_reader: Callable[[str], dict[str, dict[str, Any]]] = read_project_containers,
    sample_reader: Callable[[dict[str, dict[str, Any]]], dict[str, Any]] = read_sample,
    peer_contract_reader: Callable[[dict[str, dict[str, Any]]], bool | None] = read_peer_contract,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    started_at = _utc_now()
    containers = container_reader(project)
    samples = [sample_reader(containers)]
    sleeper(sample_seconds)
    samples.append(sample_reader(containers))
    observation = {
        "schema_version": 1,
        "topology_id": TOPOLOGY_ID,
        "compose_project": project,
        "started_at": started_at,
        "completed_at": _utc_now(),
        "required_services": list(REQUIRED_SERVICES),
        "containers": containers,
        "peer_contract": {
            "amaru_consumer_only_amaru_upstreams": peer_contract_reader(containers)
        },
        "samples": samples,
        "tolerances": {"slot_lag": 6, "producer_slot_spread": 6},
    }
    classified = classify_topology_health(observation)
    result = {**classified, "observation": observation, "evidence_path": str(output)}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def result_exit_code(result: dict[str, Any], *, require_healthy: bool) -> int:
    if require_healthy and result.get("state") != "healthy":
        return 2
    return 0


def _default_output() -> Path:
    state_dir = Path(os.environ.get("ADA2_DWARF_STATE_DIR") or "/var/dwarf/state")
    return state_dir / "topology-health" / "latest.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topology", choices=[TOPOLOGY_ID], default=TOPOLOGY_ID)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--sample-seconds", type=float, default=10.0)
    parser.add_argument("--require-healthy", action="store_true")
    args = parser.parse_args(argv)

    output = args.output or _default_output()
    result = collect_and_classify(
        project=args.project,
        output=output,
        sample_seconds=max(0.0, args.sample_seconds),
    )
    print(json.dumps(result, sort_keys=True))
    return result_exit_code(result, require_healthy=args.require_healthy)


if __name__ == "__main__":
    raise SystemExit(main())
