from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_resource_abuse_fault import (
    _ensure_tmux_session_absent,
    _find_node,
    _kill_tmux_session,
    _read_metadata,
    _write_metadata,
    _launch_haskell_node,
)
from runtime_substrate_common import run_command, wait_for_nodes_healthy, write_json


DEFAULT_LIBFAKETIME = "/usr/lib/x86_64-linux-gnu/faketime/libfaketime.so.1"
# Docker mode: the controller copies its libfaketime into the substrate's env dir, which every
# node service already bind-mounts at /env, so the node container needs no new mount.
CONTAINER_LIBFAKETIME = "/env/faketime/libfaketime.so.1"
DEFAULT_SKEW_TOLERANCE_SECONDS = 5.0
CLOCK_MEASUREMENT_SOURCE = "node-log-timestamp-minus-docker-log-timestamp"
# cardano-node JSON traces carry the node's clock in "at"; Amaru JSON spans in "timestamp".
_NODE_TIMESTAMP_KEYS = ("at", "timestamp")
_TIMESTAMP_RE = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:?\d\d)?$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def faketime_offset_spec(skew_seconds: int) -> str:
    """libfaketime relative offset: "+N"/"-N" seconds (m/h/d/y are its only multipliers)."""
    return f"{'+' if int(skew_seconds) >= 0 else '-'}{abs(int(skew_seconds))}"


def _parse_timestamp(text: str) -> datetime | None:
    match = _TIMESTAMP_RE.match(str(text).strip())
    if not match:
        return None
    base, fraction, zone = match.groups()
    micro = int((fraction or "0")[:6].ljust(6, "0"))
    parsed = datetime.strptime(base, "%Y-%m-%dT%H:%M:%S").replace(microsecond=micro)
    if zone and zone != "Z":
        sign = 1 if zone[0] == "+" else -1
        digits = zone[1:].replace(":", "")
        offset = timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))
        return parsed.replace(tzinfo=timezone(sign * offset))
    return parsed.replace(tzinfo=timezone.utc)


def _line_clock_offset(line: str) -> float | None:
    daemon_text, _, payload = line.partition(" ")
    daemon = _parse_timestamp(daemon_text)
    if daemon is None or not payload.lstrip().startswith("{"):
        return None
    try:
        document = json.loads(payload)
    except ValueError:
        return None
    if not isinstance(document, dict):
        return None
    for key in _NODE_TIMESTAMP_KEYS:
        node_time = _parse_timestamp(document.get(key) or "")
        if node_time is not None:
            return (node_time - daemon).total_seconds()
    return None


def measure_container_clock_offset(container: str, *, since: str | None = None, tail: int = 200,
                                   minimum_samples: int = 3) -> dict:
    """The node process's clock relative to the Docker daemon's, from the node's own log lines.

    `docker logs --timestamps` prefixes each line with the daemon's receive time (real clock); the
    node's JSON log line carries the time the node process read from its own clock. Their difference
    is the process clock offset (plus a few ms of pipe latency).
    """
    command = ["docker", "logs", "--timestamps", "--tail", str(int(tail))]
    if since:
        command += ["--since", since]
    command.append(container)
    proc = run_command(command, timeout=30)
    body = {"container": container, "source": CLOCK_MEASUREMENT_SOURCE, "since": since, "measured": False,
            "sample_count": 0, "median_offset_seconds": None, "min_offset_seconds": None,
            "max_offset_seconds": None, "reason": None}
    if proc.returncode != 0:
        body["reason"] = f"docker logs failed: {(proc.stderr or proc.stdout or '').strip()[-300:]}"
        return body
    offsets = [offset for line in f"{proc.stdout or ''}\n{proc.stderr or ''}".splitlines()
               if (offset := _line_clock_offset(line)) is not None]
    body["sample_count"] = len(offsets)
    if len(offsets) < minimum_samples:
        body["reason"] = (f"{len(offsets)} timestamped node log line(s) (need {minimum_samples}); "
                          "the node clock was not observable")
        return body
    body.update(measured=True, median_offset_seconds=round(statistics.median(offsets), 3),
                min_offset_seconds=round(min(offsets), 3), max_offset_seconds=round(max(offsets), 3))
    return body


def _wait_for_clock_samples(container: str, *, since: str, timeout_seconds: float) -> dict:
    deadline = time.monotonic() + float(timeout_seconds)
    while True:
        measured = measure_container_clock_offset(container, since=since)
        if measured["measured"] or time.monotonic() >= deadline:
            return measured
        time.sleep(1.0)


def build_result_body(*, skew_seconds: int, host_time_at_apply: str, host_time_at_release: str) -> dict:
    """Host mode: the node is relaunched under libfaketime but its clock is not measured."""
    return {
        "compose_mode": "host",
        "skew_requested_seconds": int(skew_seconds),
        "skew_applied_seconds": None,
        "skew_confirmed": None,
        "measured": False,
        "reason": "host-mode skew relaunches the node under libfaketime; its clock is not measured",
        "host_time_at_apply": str(host_time_at_apply),
        "host_time_at_release": str(host_time_at_release),
    }


def build_docker_result_body(*, skew_seconds: int, before: dict, during: dict, tolerance_seconds: float,
                             host_time_at_apply: str, host_time_at_release: str) -> dict:
    measured = bool(before.get("measured") and during.get("measured"))
    applied = round(during["median_offset_seconds"] - before["median_offset_seconds"], 3) if measured else None
    confirmed = bool(measured and abs(applied - int(skew_seconds)) <= float(tolerance_seconds))
    if not measured:
        missing = "after recreate" if before.get("measured") else "before apply"
        reason = f"node clock not measured {missing}: {during.get('reason') or before.get('reason')}"
    elif not confirmed:
        reason = (f"node clock moved {applied}s, requested {int(skew_seconds)}s "
                  f"(tolerance {float(tolerance_seconds)}s); libfaketime did not take effect as requested")
    else:
        reason = None
    return {
        "compose_mode": "docker",
        "skew_requested_seconds": int(skew_seconds),
        "skew_applied_seconds": applied,
        "skew_confirmed": confirmed,
        "skew_tolerance_seconds": float(tolerance_seconds),
        "measured": measured,
        "reason": reason,
        "measurement_source": CLOCK_MEASUREMENT_SOURCE,
        "clock_offset_before": before,
        "clock_offset_during": during,
        "host_time_at_apply": str(host_time_at_apply),
        "host_time_at_release": str(host_time_at_release),
    }


def exit_code_for(report: dict) -> int:
    """Non-zero when the skew was measured as not applied (or not measurable) in the node."""
    return 3 if (report.get("result") or {}).get("skew_confirmed") is False else 0


def _launch_skewed_haskell_node(node: dict, *, skew_seconds: int, libfaketime_path: str) -> None:
    _ensure_tmux_session_absent(str(node["session"]))
    command_parts = [
        str(node["resolved_binary"]),
        "run",
        "--config",
        str(node["config_path"]),
        "--topology",
        str(node["topology_path"]),
        "--database-path",
        str(node["db_dir"]),
        "--socket-path",
        str(node["socket_path"]),
        "--port",
        str(node["port"]),
        "--host-addr",
        "127.0.0.1",
    ]
    command = (
        f"echo $$ > {json.dumps(str(node['pid_file']))}; "
        f"export LD_PRELOAD={json.dumps(libfaketime_path)}; "
        f"export FAKETIME={json.dumps(faketime_offset_spec(skew_seconds))}; "
        f"exec {' '.join(json.dumps(part) for part in command_parts)} 2>&1 | tee -a {json.dumps(str(node['log_path']))}"
    )
    result = run_command(["tmux", "new-session", "-d", "-s", str(node["session"]), f"bash -lc {json.dumps(command)}"])
    if result.returncode != 0:
        raise RuntimeError(f"failed to launch skewed node {node['id']}: {result.stderr or result.stdout}")


def _spawn_release_session(*, session: str, config_path: Path, duration_seconds: float) -> None:
    _ensure_tmux_session_absent(session)
    command = (
        f"sleep {duration_seconds}; "
        f"exec python3 {json.dumps(str(SCRIPT_DIR / 'runtime_time_skew.py'))} --config {json.dumps(str(config_path))} --mode release"
    )
    result = run_command(["tmux", "new-session", "-d", "-s", session, f"bash -lc {json.dumps(command)}"])
    if result.returncode != 0:
        raise RuntimeError(f"failed to launch time-skew release session {session}: {result.stderr or result.stdout}")


def _is_docker(metadata: dict, node: dict) -> bool:
    return str(node.get("compose_mode") or metadata.get("compose_mode") or "") == "docker"


def _compose_path(metadata: dict) -> Path:
    return Path(str(metadata["runtime_root"])) / "docker-compose.yml"


def _service_environment(metadata: dict, service_id: str) -> dict | None:
    compose = json.loads(_compose_path(metadata).read_text(encoding="utf-8"))
    environment = compose["services"][service_id].get("environment")
    if environment is not None and not isinstance(environment, dict):
        raise ValueError(f"service {service_id} environment is not a mapping: {environment!r}")
    return None if environment is None else dict(environment)


def _set_service_environment(metadata: dict, service_id: str, environment: dict | None) -> None:
    compose_path = _compose_path(metadata)
    compose = json.loads(compose_path.read_text(encoding="utf-8"))
    service = compose["services"][service_id]
    if environment is None:
        service.pop("environment", None)
    else:
        service["environment"] = dict(environment)
    compose_path.write_text(json.dumps(compose, indent=2) + "\n", encoding="utf-8")


def _recreate_service(metadata: dict, service_id: str) -> None:
    """Recreate one node through the substrate's own compose project (as repoint_amaru does)."""
    result = run_command(
        ["docker", "compose", "--project-name", str(metadata["compose_project"]), "up", "-d", "--no-deps", service_id],
        cwd=Path(str(metadata["runtime_root"])), timeout=180,
    )
    if result.returncode != 0:
        raise RuntimeError(f"docker compose up {service_id} failed: {result.stderr or result.stdout}")


def _inspect_recreated_container(metadata: dict, node: dict) -> dict:
    from runtime_compose_substrate import _inspect_docker_node  # noqa: PLC0415 - heavy import, docker path only

    inspected = _inspect_docker_node(compose_project=str(metadata["compose_project"]), node=node)
    return {"container_id": inspected["container_id"], "container_ip": inspected["container_ip"]}


def _update_node_records(metadata: dict, node_id: str, updates: dict) -> None:
    for listing in ("nodes", "haskell_nodes", "amaru_nodes"):
        for record in metadata.get(listing) or []:
            if record.get("id") == node_id:
                record.update(updates)


def _container_since() -> str:
    # Whole seconds, one earlier, so the recreated container's first lines are never cut off.
    return (datetime.now(timezone.utc) - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _recreate_with_environment(metadata: dict, node: dict, environment: dict | None, *,
                               timeout_seconds: float) -> dict:
    _set_service_environment(metadata, str(node["id"]), environment)
    since = _container_since()
    _recreate_service(metadata, str(node["id"]))
    _update_node_records(metadata, str(node["id"]), _inspect_recreated_container(metadata, node))
    return _wait_for_clock_samples(str(node["container_name"]), since=since, timeout_seconds=timeout_seconds)


def _apply_docker_time_skew(*, metadata: dict, node: dict, skew_seconds: int, healthy_timeout_seconds: float,
                            libfaketime_path: str, tolerance_seconds: float) -> tuple[dict, dict | None]:
    if node.get("host") or str(node.get("host_id") or "local") != "local":
        raise ValueError("runtime_time_skew docker mode supports nodes on the local Docker host only")
    original_environment = _service_environment(metadata, str(node["id"]))
    before = measure_container_clock_offset(str(node["container_name"]))
    staged = Path(str(metadata["runtime_root"])) / "env" / "faketime" / "libfaketime.so.1"
    staged.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(libfaketime_path, staged)
    os.chmod(staged, 0o644)
    skewed_environment = {**(original_environment or {}), "LD_PRELOAD": CONTAINER_LIBFAKETIME,
                          "FAKETIME": faketime_offset_spec(skew_seconds)}
    apply_time = _utc_now()
    try:
        during = _recreate_with_environment(metadata, node, skewed_environment,
                                            timeout_seconds=healthy_timeout_seconds)
    except Exception:
        _set_service_environment(metadata, str(node["id"]), original_environment)
        _recreate_service(metadata, str(node["id"]))
        raise
    body = build_docker_result_body(skew_seconds=skew_seconds, before=before, during=during,
                                    tolerance_seconds=tolerance_seconds, host_time_at_apply=apply_time,
                                    host_time_at_release=apply_time)
    return body, original_environment


def apply_time_skew(
    *,
    runtime_metadata_path: Path,
    output_dir: Path,
    target_node: str,
    skew_seconds: int,
    duration_seconds: float,
    healthy_timeout_seconds: float,
    libfaketime_path: str,
    skew_tolerance_seconds: float = DEFAULT_SKEW_TOLERANCE_SECONDS,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    if not Path(libfaketime_path).exists():
        raise FileNotFoundError(f"missing libfaketime shared object: {libfaketime_path}")
    metadata = _read_metadata(runtime_metadata_path)
    node = _find_node(metadata, target_node)
    release_config = {
        "runtime_metadata_path": str(runtime_metadata_path),
        "output_dir": str(output_dir),
        "target_node": target_node,
        "skew_seconds": int(skew_seconds),
        "healthy_timeout_seconds": float(healthy_timeout_seconds),
        "skew_tolerance_seconds": float(skew_tolerance_seconds),
    }
    if _is_docker(metadata, node):
        body, original_environment = _apply_docker_time_skew(
            metadata=metadata, node=node, skew_seconds=skew_seconds, healthy_timeout_seconds=healthy_timeout_seconds,
            libfaketime_path=libfaketime_path, tolerance_seconds=skew_tolerance_seconds,
        )
        release_config["docker_original_environment"] = original_environment
    else:
        _kill_tmux_session(str(node["session"]))
        _launch_skewed_haskell_node(node, skew_seconds=skew_seconds, libfaketime_path=libfaketime_path)
        wait_for_nodes_healthy([node], timeout_seconds=healthy_timeout_seconds)
        # F143: no fabricated observation values (the helper was removed in F128).
        apply_time = _utc_now()
        body = build_result_body(skew_seconds=skew_seconds, host_time_at_apply=apply_time,
                                 host_time_at_release=apply_time)
    report = {"mode": "time_skew", "runtime_metadata_path": str(runtime_metadata_path), "result": body}
    write_json(output_dir / "result.json", report)
    release_config_path = output_dir / "runtime_time_skew-release.json"
    write_json(release_config_path, release_config)
    release_session = f"{metadata['compose_project']}-timeskew-{target_node}"
    _spawn_release_session(session=release_session, config_path=release_config_path, duration_seconds=duration_seconds)
    metadata.setdefault("faults", []).append(
        {"kind": "time_skew", "target_node_id": target_node, "release_session": release_session}
    )
    metadata.setdefault("aux_sessions", []).append(
        {"id": f"time-skew-{target_node}", "kind": "time_skew_release", "session": release_session}
    )
    _write_metadata(runtime_metadata_path, metadata)
    return report


def release_time_skew(*, runtime_metadata_path: Path, output_dir: Path, target_node: str, skew_seconds: int,
                      healthy_timeout_seconds: float, docker_original_environment: dict | None = None,
                      skew_tolerance_seconds: float = DEFAULT_SKEW_TOLERANCE_SECONDS) -> dict:
    metadata = _read_metadata(runtime_metadata_path)
    node = _find_node(metadata, target_node)
    result_path = output_dir / "result.json"
    body = json.loads(result_path.read_text(encoding="utf-8"))
    result = dict(body.get("result") or {})
    if _is_docker(metadata, node):
        after = _recreate_with_environment(metadata, node, docker_original_environment,
                                           timeout_seconds=healthy_timeout_seconds)
        before = result.get("clock_offset_before") or {}
        result["clock_offset_after_release"] = after
        result["clock_restored"] = bool(
            after.get("measured") and before.get("measured")
            and abs(after["median_offset_seconds"] - before["median_offset_seconds"]) <= float(skew_tolerance_seconds)
        )
        result["host_time_at_release"] = _utc_now()
    else:
        _kill_tmux_session(str(node["session"]))
        _launch_haskell_node(node)
        wait_for_nodes_healthy([node], timeout_seconds=healthy_timeout_seconds)
        result = build_result_body(
            skew_seconds=skew_seconds,
            host_time_at_apply=result.get("host_time_at_apply", _utc_now()),
            host_time_at_release=_utc_now(),
        )
    body["result"] = result
    write_json(result_path, body)
    metadata["aux_sessions"] = [
        item for item in list(metadata.get("aux_sessions") or []) if item.get("kind") != "time_skew_release"
    ]
    metadata["faults"] = [item for item in list(metadata.get("faults") or []) if item.get("kind") != "time_skew"]
    _write_metadata(runtime_metadata_path, metadata)
    return body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--mode", choices=("apply", "release"), required=True)
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    tolerance = float(config.get("skew_tolerance_seconds", DEFAULT_SKEW_TOLERANCE_SECONDS))
    if args.mode == "apply":
        report = apply_time_skew(
            runtime_metadata_path=Path(config["runtime_metadata_path"]),
            output_dir=Path(config["output_dir"]),
            target_node=str(config["target_node"]),
            skew_seconds=int(config["skew_seconds"]),
            duration_seconds=float(config.get("duration_seconds", 20)),
            healthy_timeout_seconds=float(config.get("healthy_timeout_seconds", 90)),
            libfaketime_path=str(config.get("libfaketime_path", DEFAULT_LIBFAKETIME)),
            skew_tolerance_seconds=tolerance,
        )
        exit_code = exit_code_for(report)
    else:
        release_time_skew(
            runtime_metadata_path=Path(config["runtime_metadata_path"]),
            output_dir=Path(config["output_dir"]),
            target_node=str(config["target_node"]),
            skew_seconds=int(config["skew_seconds"]),
            healthy_timeout_seconds=float(config.get("healthy_timeout_seconds", 90)),
            docker_original_environment=config.get("docker_original_environment"),
            skew_tolerance_seconds=tolerance,
        )
        exit_code = 0
    print(f"mode=time_skew target_node={config.get('target_node', '')}", flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
