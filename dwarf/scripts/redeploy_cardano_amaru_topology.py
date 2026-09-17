#!/usr/bin/env python3
"""Evidence-preserving redeploy of DWARF's fixed Cardano/Amaru topology."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


DWARF_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = DWARF_ROOT.parent
SCRIPTS_ROOT = Path(__file__).resolve().parent
for import_root in (DWARF_ROOT, SCRIPTS_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from check_cardano_amaru_topology import (  # noqa: E402
    AMARU_CONSUMER,
    AMARU_RELAYS,
    REQUIRED_SERVICES,
    collect_and_classify,
)
from profile_manager.topology_health import (  # noqa: E402
    TopologyLockBusy,
    acquire_topology_lock,
)


TOPOLOGY_ID = "cardano_amaru"
PROJECT = "cardano_amaru_relay_bootstrap_control"
PACKAGE = REPOSITORY_ROOT / "antithesis" / PROJECT
EPOCH_LENGTH = 400
DEFAULT_TIMEOUT_SECONDS = 1800
DEFAULT_SAMPLE_SECONDS = 5.0

TopologyBusyError = TopologyLockBusy


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def run_command(
    args: list[str], *, timeout: int = 30
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def acquire_redeploy_lock(state_dir: Path):
    """Refuse redeploy while an attached scenario or another redeploy is active."""
    return acquire_topology_lock(Path(state_dir), exclusive=True)


def _write_json(path: Path, body: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _compose_command(compose_file: Path, *arguments: str) -> list[str]:
    return [
        "docker",
        "compose",
        "-p",
        PROJECT,
        "-f",
        str(compose_file),
        *arguments,
    ]


def _require_success(
    result: subprocess.CompletedProcess[str], description: str
) -> subprocess.CompletedProcess[str]:
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown error").strip()
        raise RuntimeError(f"{description} failed: {detail}")
    return result


def _validate_rendered_compose(rendered: dict[str, Any]) -> None:
    services = rendered.get("services")
    if not isinstance(services, dict):
        raise RuntimeError("rendered Compose model has no services map")
    missing = sorted(set(REQUIRED_SERVICES) - set(services))
    if missing:
        raise RuntimeError(
            "rendered Compose model is missing required services: " + ", ".join(missing)
        )

    volumes = rendered.get("volumes") or {}
    if not isinstance(volumes, dict):
        raise RuntimeError("rendered Compose model has an invalid volumes map")
    unsafe = [
        name
        for name, value in volumes.items()
        if isinstance(value, dict) and bool(value.get("external"))
    ]
    if unsafe:
        raise RuntimeError(
            "refusing to redeploy a Compose model with an external volume: "
            + ", ".join(sorted(unsafe))
        )


def remember_bootstrap_evidence(
    result: dict[str, Any], existing: dict[str, Any]
) -> dict[str, Any]:
    """Retain short-lived startup markers while later health samples advance."""
    retained = {name: dict(value) for name, value in existing.items()}
    samples = ((result.get("observation") or {}).get("samples") or [])
    for sample in samples:
        for name, relay in (sample.get("amaru_relays") or {}).items():
            if name not in AMARU_RELAYS or not isinstance(relay, dict):
                continue
            prior = retained.setdefault(name, {})
            targets = relay.get("target_slots") or []
            if len(targets) == 3 and all(isinstance(value, int) for value in targets):
                prior["target_slots"] = list(targets)
            if relay.get("committed") is True:
                prior["committed"] = True
            if relay.get("exec_started") is True:
                prior["exec_started"] = True
    return retained


def fresh_readiness_proven(
    result: dict[str, Any], bootstrap_evidence: dict[str, Any] | None = None
) -> bool:
    """Require health plus proof that both relays crossed a later epoch."""
    if result.get("state") != "healthy":
        return False
    observation = result.get("observation") or {}
    samples = observation.get("samples") or []
    if len(samples) < 2:
        return False
    final = samples[-1]
    tips = final.get("tips") or {}
    consumer = tips.get(AMARU_CONSUMER) or {}
    if not isinstance(consumer.get("slot"), int) or consumer["slot"] <= 0:
        return False
    if not isinstance(result.get("consumer_lag_slots"), int):
        return False
    if result["consumer_lag_slots"] > 6:
        return False

    relays = final.get("amaru_relays") or {}
    for name in AMARU_RELAYS:
        relay = relays.get(name) or {}
        startup = (bootstrap_evidence or {}).get(name) or {}
        targets = relay.get("target_slots") or startup.get("target_slots") or []
        current = relay.get("current_slot")
        if len(targets) != 3 or not all(isinstance(value, int) for value in targets):
            return False
        committed = relay.get("committed") is True or startup.get("committed") is True
        exec_started = (
            relay.get("exec_started") is True or startup.get("exec_started") is True
        )
        if not committed or not exec_started:
            return False
        if not isinstance(current, int):
            return False
        anchor_epoch = max(targets) // EPOCH_LENGTH
        if current // EPOCH_LENGTH < anchor_epoch + 2:
            return False
    return True


def _capture_command(
    *,
    command_runner: Callable[..., subprocess.CompletedProcess[str]],
    args: list[str],
    output: Path,
    timeout: int,
) -> None:
    result = command_runner(args, timeout=timeout)
    output.write_text(
        (result.stdout or "")
        + ("\n[stderr]\n" + result.stderr if result.stderr else ""),
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(f"evidence capture failed for {' '.join(args)}")


def redeploy_topology(
    *,
    package: Path,
    state_dir: Path,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    sample_seconds: float = DEFAULT_SAMPLE_SECONDS,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] = run_command,
    health_probe: Callable[..., dict[str, Any]] = collect_and_classify,
    sleeper: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    timestamp: Callable[[], str] = _utc_stamp,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Capture evidence, replace the fixed project, and prove fresh readiness."""
    package = Path(package).resolve()
    state_dir = Path(state_dir).resolve()
    compose_file = package / "docker-compose.yaml"
    if not compose_file.is_file():
        raise RuntimeError(f"trusted topology Compose file is missing: {compose_file}")

    events: list[dict[str, Any]] = []

    def emit(stage: str, **details: Any) -> None:
        event = {"stage": stage, **details}
        events.append(event)
        if progress is not None:
            progress(event)

    with acquire_redeploy_lock(state_dir):
        evidence_root = (
            state_dir / "topology-health" / "redeployments" / timestamp()
        )
        evidence_root.mkdir(parents=True, exist_ok=False)
        emit("capture_started", evidence_root=str(evidence_root))

        config_result = _require_success(
            command_runner(
                _compose_command(compose_file, "config", "--format", "json"),
                timeout=60,
            ),
            "Compose render",
        )
        try:
            rendered = json.loads(config_result.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError("rendered Compose model is not valid JSON") from error
        _validate_rendered_compose(rendered)
        _write_json(evidence_root / "compose-config.json", rendered)

        pre_health = health_probe(
            project=PROJECT,
            output=evidence_root / "topology-health-before.json",
            sample_seconds=sample_seconds,
        )
        _capture_command(
            command_runner=command_runner,
            args=_compose_command(compose_file, "ps", "--all"),
            output=evidence_root / "compose-ps.txt",
            timeout=60,
        )
        _capture_command(
            command_runner=command_runner,
            args=_compose_command(compose_file, "logs", "--no-color", "--timestamps"),
            output=evidence_root / "compose-logs.txt",
            timeout=300,
        )
        _write_json(
            evidence_root / "capture-complete.json",
            {
                "schema_version": 1,
                "topology_id": TOPOLOGY_ID,
                "compose_project": PROJECT,
                "pre_redeploy_state": pre_health.get("state"),
                "evidence_files": [
                    "compose-config.json",
                    "compose-logs.txt",
                    "compose-ps.txt",
                    "topology-health-before.json",
                ],
            },
        )
        emit("capture_complete")

        down = _compose_command(compose_file, "down", "-v")
        _require_success(command_runner(down, timeout=300), "topology teardown")
        emit("topology_removed")

        up = _compose_command(compose_file, "up", "-d")
        _require_success(
            command_runner(up, timeout=max(600, timeout_seconds)),
            "topology startup",
        )
        emit("topology_started")

        deadline = monotonic() + max(1, timeout_seconds)
        attempt = 0
        final_health: dict[str, Any] = {}
        bootstrap_evidence: dict[str, Any] = {}
        while monotonic() <= deadline:
            attempt += 1
            final_health = health_probe(
                project=PROJECT,
                output=evidence_root / f"readiness-{attempt:03d}.json",
                sample_seconds=sample_seconds,
            )
            bootstrap_evidence = remember_bootstrap_evidence(
                final_health, bootstrap_evidence
            )
            _write_json(
                evidence_root / "bootstrap-startup-evidence.json",
                bootstrap_evidence,
            )
            ready = fresh_readiness_proven(final_health, bootstrap_evidence)
            emit(
                "readiness_sample",
                attempt=attempt,
                state=final_health.get("state"),
                reason_code=final_health.get("reason_code"),
                fresh_readiness=ready,
            )
            if ready:
                break
            sleeper(max(1.0, sample_seconds))

        passed = fresh_readiness_proven(final_health, bootstrap_evidence)
        result = {
            "schema_version": 1,
            "topology_id": TOPOLOGY_ID,
            "compose_project": PROJECT,
            "passed": passed,
            "topology_left_running": True,
            "evidence_root": str(evidence_root),
            "attempts": attempt,
            "bootstrap_evidence": bootstrap_evidence,
            "final_health": final_health,
            "events": events,
        }
        emit("complete" if passed else "readiness_timeout", passed=passed)
        result["events"] = events
        _write_json(evidence_root / "redeploy-result.json", result)
        return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Preserve evidence and redeploy the fixed Cardano/Amaru topology"
    )
    parser.add_argument("--topology", required=True, choices=[TOPOLOGY_ID])
    parser.add_argument("--confirm", action="store_true", required=True)
    parser.parse_args()
    state_dir = Path(
        os.environ.get("ADA2_DWARF_STATE_DIR")
        or os.environ.get("DWARF_STATE_DIR")
        or str(Path.home() / ".local" / "share" / "dwarf" / "state")
    )
    package = Path(os.environ.get("ADA2_DWARF_TOPOLOGY_PACKAGE_DIR") or PACKAGE)

    def print_progress(event: dict[str, Any]) -> None:
        print(json.dumps(event, sort_keys=True), flush=True)

    try:
        result = redeploy_topology(
            package=package,
            state_dir=state_dir,
            progress=print_progress,
        )
    except TopologyBusyError as error:
        print(json.dumps({"passed": False, "error": str(error)}), flush=True)
        return 3
    except Exception as error:
        print(json.dumps({"passed": False, "error": str(error)}), flush=True)
        return 2
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
