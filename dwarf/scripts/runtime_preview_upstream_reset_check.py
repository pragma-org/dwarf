#!/usr/bin/env python3

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import runtime_common as rc  # noqa: E402
from runtime_telemetry import emit_runtime_metric, emit_target_event  # noqa: E402
from runtime_preview_trace_metrics import collect_amaru_trace_metrics, collect_cardano_trace_metrics, read_log_window, read_timestamp_window  # noqa: E402


DEFAULT_RUNTIME_ROOTS = {
    "amaru": "/opt/dwarf/cardano-profiles/profile-d-amaru-preview-proof",
    "cardano-node": "/opt/dwarf/cardano-profiles/profile-e-haskell-preview-proof",
}
AMARU_TRACE_PRE_GRACE_MS = 250
AMARU_TRACE_POST_GRACE_MS = 2000


def target_implementation_from_scenario(path: Path) -> str:
    body = json.loads(path.read_text(encoding="utf-8"))
    target = body.get("target") or {}
    implementation = target.get("implementation")
    if implementation not in DEFAULT_RUNTIME_ROOTS:
        raise RuntimeError(f"unsupported target implementation in {path}: {implementation!r}")
    return implementation


def runtime_root_for_implementation(implementation: str) -> Path:
    env_key = f"ADA2_DWARF_PREVIEW_{implementation.upper().replace('-', '_')}_ROOT"
    return rc.resolve_preview_runtime_root(os.environ.get(env_key, DEFAULT_RUNTIME_ROOTS[implementation]))


def _dir_size_bytes(path: Path) -> int:
    return sum(node.stat().st_size for node in path.rglob("*") if node.is_file())


def _listener_ok(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=3):
            return True
    except OSError:
        return False


def _run_iptables(args: list[str]) -> None:
    result = subprocess.run(["sudo", "iptables", *args], text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"iptables failed: {' '.join(args)}")


def _apply_reset_rules(ips: list[str], port: int, view=None) -> None:
    if view is not None and view.layout == "container":
        rc.preview_apply_iptables(view, ips, port, ['REJECT', '--reject-with', 'tcp-reset'])
        return
    for ip in ips:
        _run_iptables(
            [
                "-I",
                "OUTPUT",
                "1",
                "-p",
                "tcp",
                "-d",
                ip,
                "--dport",
                str(port),
                "-j",
                "REJECT",
                "--reject-with",
                "tcp-reset",
            ]
        )


def _remove_reset_rules(ips: list[str], port: int, view=None) -> None:
    if view is not None and view.layout == "container":
        rc.preview_remove_iptables(view, ips, port, ['REJECT', '--reject-with', 'tcp-reset'])
        return
    for ip in ips:
        result = subprocess.run(
            [
                "sudo",
                "iptables",
                "-D",
                "OUTPUT",
                "-p",
                "tcp",
                "-d",
                ip,
                "--dport",
                str(port),
                "-j",
                "REJECT",
                "--reject-with",
                "tcp-reset",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode not in (0, 1):
            raise RuntimeError(result.stderr.strip() or f"iptables cleanup failed for {ip}:{port}")


def _window_delta(before: int, after: int) -> int:
    return max(0, after - before)


def _emit_window_metrics(
    prefix: str,
    *,
    chain_before: int,
    chain_after: int,
    log_before: int,
    log_after: int,
    log_path: Path,
    target_implementation: str,
    window_start_epoch_ms: int | None = None,
    window_end_epoch_ms: int | None = None,
) -> None:
    chain_delta = _window_delta(chain_before, chain_after)
    log_delta = _window_delta(log_before, log_after)
    progress_ok = 1 if chain_delta > 0 or log_delta > 0 else 0
    meta = {"target_implementation": target_implementation, "window": prefix}
    emit_runtime_metric(f"{prefix}_chain_bytes_delta", value=chain_delta, meta=meta)
    emit_runtime_metric(f"{prefix}_log_bytes_delta", value=log_delta, meta=meta)
    emit_runtime_metric(f"{prefix}_progress_ok", value=progress_ok, meta=meta)
    amaru_trace_metrics = None
    trace_window = read_log_window(log_path, log_before, log_after)
    if target_implementation == "amaru" and window_start_epoch_ms is not None and window_end_epoch_ms is not None:
        timestamp_window = read_timestamp_window(
            log_path,
            start_epoch_ms=max(0, window_start_epoch_ms - AMARU_TRACE_PRE_GRACE_MS),
            end_epoch_ms=window_end_epoch_ms + AMARU_TRACE_POST_GRACE_MS,
            target_implementation=target_implementation,
        )
        if timestamp_window:
            trace_window = timestamp_window
    if target_implementation == "amaru":
        amaru_trace_metrics = collect_amaru_trace_metrics(trace_window)
        emit_runtime_metric(f"{prefix}_adopted_tip_count", value=amaru_trace_metrics["adopted_tip_count"], meta=meta)
        emit_runtime_metric(f"{prefix}_tip_slot_delta", value=amaru_trace_metrics["tip_slot_delta"], meta=meta)
        emit_runtime_metric(f"{prefix}_peer_connected_count", value=amaru_trace_metrics["peer_connected_count"], meta=meta)
        emit_runtime_metric(f"{prefix}_peer_connection_died_count", value=amaru_trace_metrics["peer_connection_died_count"], meta=meta)
        emit_runtime_metric(f"{prefix}_amaru_adopted_tip_count", value=amaru_trace_metrics["adopted_tip_count"], meta=meta)
        emit_runtime_metric(f"{prefix}_amaru_tip_slot_delta", value=amaru_trace_metrics["tip_slot_delta"], meta=meta)
        emit_runtime_metric(f"{prefix}_amaru_peer_connected_count", value=amaru_trace_metrics["peer_connected_count"], meta=meta)
        emit_runtime_metric(f"{prefix}_amaru_peer_connection_died_count", value=amaru_trace_metrics["peer_connection_died_count"], meta=meta)
    elif target_implementation == "cardano-node":
        cardano_trace_metrics = collect_cardano_trace_metrics(trace_window)
        emit_runtime_metric(f"{prefix}_adopted_tip_count", value=cardano_trace_metrics["adopted_tip_count"], meta=meta)
        emit_runtime_metric(f"{prefix}_tip_slot_delta", value=cardano_trace_metrics["tip_slot_delta"], meta=meta)
        emit_runtime_metric(f"{prefix}_peer_connected_count", value=cardano_trace_metrics["peer_connected_count"], meta=meta)
        emit_runtime_metric(f"{prefix}_peer_connection_died_count", value=cardano_trace_metrics["peer_connection_died_count"], meta=meta)
    emit_target_event(
        primitive="runtime_preview_upstream_reset_check",
        event=f"{prefix}_observed",
        payload={
            "target_implementation": target_implementation,
            "chain_bytes_before": chain_before,
            "chain_bytes_after": chain_after,
            "chain_bytes_delta": chain_delta,
            "log_bytes_before": log_before,
            "log_bytes_after": log_after,
            "log_bytes_delta": log_delta,
            "progress_ok": bool(progress_ok),
            "amaru_trace_metrics": amaru_trace_metrics,
        },
    )


def run_reset(*, scenario_path: Path, fault_seconds: int, recovery_seconds: int) -> int:
    target_implementation = target_implementation_from_scenario(scenario_path)
    runtime_root = runtime_root_for_implementation(target_implementation)
    view = rc.preview_node_view(runtime_root, target_implementation)
    listen_host, listen_port = view.listen_host, view.listen_port
    chain_dir = view.chain_dir
    log_path = view.log_path
    if not chain_dir.exists():
        raise RuntimeError(f"missing chain dir: {chain_dir}")
    if not log_path.exists():
        raise RuntimeError(f"missing log path: {log_path}")
    if not view.is_running():
        raise RuntimeError(f"{target_implementation} is not running: {view.describe()}")
    if not _listener_ok(listen_host, listen_port):
        raise RuntimeError(f"listener probe failed: {listen_host}:{listen_port}")
    upstream_host, upstream_port, upstream_ips = rc.resolve_upstream_peer(view.upstream_peer_address)
    active_upstream_ips = rc.preview_active_upstream_ips(view, upstream_port)
    if not active_upstream_ips:
        raise RuntimeError(f"reset fault found no active upstream peer IPs for {view.describe()} port={upstream_port}")

    chain_before = _dir_size_bytes(chain_dir)
    log_before = log_path.stat().st_size
    emit_target_event(
        primitive="runtime_preview_upstream_reset_check",
        event="fault_planned",
        payload={
            "target_implementation": target_implementation,
            "runtime_root": str(runtime_root),
            "runtime_layout": view.layout,
            "container_name": view.container_name,
            "listen_address": view.listen_address,
            "upstream_peer_address": view.upstream_peer_address,
            "resolved_upstream_ips": upstream_ips,
            "active_upstream_ips": active_upstream_ips,
            "fault_seconds": fault_seconds,
            "recovery_seconds": recovery_seconds,
        },
    )

    applied_epoch_ms = int(time.time() * 1000)
    _apply_reset_rules(active_upstream_ips, upstream_port, view)
    fault_active = True
    try:
        emit_runtime_metric("preview_fault_applied_epoch_ms", value=applied_epoch_ms, meta={"target_implementation": target_implementation})
        emit_target_event(
            primitive="runtime_preview_upstream_reset_check",
            event="fault_applied",
            payload={
                "target_implementation": target_implementation,
                "upstream_host": upstream_host,
                "upstream_port": upstream_port,
                "upstream_ips": active_upstream_ips,
                "applied_epoch_ms": applied_epoch_ms,
            },
        )
        time.sleep(fault_seconds)
        chain_fault_end = _dir_size_bytes(chain_dir)
        log_fault_end = log_path.stat().st_size
        _emit_window_metrics(
            "preview_fault_window",
            chain_before=chain_before,
            chain_after=chain_fault_end,
            log_before=log_before,
            log_after=log_fault_end,
            log_path=log_path,
            target_implementation=target_implementation,
            window_start_epoch_ms=applied_epoch_ms,
            window_end_epoch_ms=int(time.time() * 1000),
        )

        removed_epoch_ms = int(time.time() * 1000)
        _remove_reset_rules(active_upstream_ips, upstream_port, view)
        fault_active = False
        emit_runtime_metric("preview_fault_removed_epoch_ms", value=removed_epoch_ms, meta={"target_implementation": target_implementation})
        emit_target_event(
            primitive="runtime_preview_upstream_reset_check",
            event="fault_removed",
            payload={
                "target_implementation": target_implementation,
                "upstream_host": upstream_host,
                "upstream_port": upstream_port,
                "upstream_ips": active_upstream_ips,
                "removed_epoch_ms": removed_epoch_ms,
            },
        )

        time.sleep(recovery_seconds)
        if not view.is_running():
            raise RuntimeError(f"{target_implementation} exited during recovery window: {view.describe()}")
        chain_recovery_end = _dir_size_bytes(chain_dir)
        log_recovery_end = log_path.stat().st_size
        _emit_window_metrics(
            "preview_postfault_window",
            chain_before=chain_fault_end,
            chain_after=chain_recovery_end,
            log_before=log_fault_end,
            log_after=log_recovery_end,
            log_path=log_path,
            target_implementation=target_implementation,
            window_start_epoch_ms=removed_epoch_ms,
            window_end_epoch_ms=int(time.time() * 1000),
        )

        total_chain_delta = _window_delta(chain_before, chain_recovery_end)
        total_log_delta = _window_delta(log_before, log_recovery_end)
        overall_progress_ok = 1 if total_chain_delta > 0 or total_log_delta > 0 else 0
        meta = {"target_implementation": target_implementation}
        emit_runtime_metric("preview_chain_bytes_delta", value=total_chain_delta, meta=meta)
        emit_runtime_metric("preview_log_bytes_delta", value=total_log_delta, meta=meta)
        emit_runtime_metric("preview_progress_ok", value=overall_progress_ok, meta=meta)
        emit_runtime_metric("preview_listener_ok", value=1 if _listener_ok(listen_host, listen_port) else 0, meta=meta)
        emit_runtime_metric("preview_listener_port", value=listen_port, meta=meta)
        emit_target_event(
            primitive="runtime_preview_upstream_reset_check",
            event="fault_check_completed",
            payload={
                "target_implementation": target_implementation,
                "upstream_peer_address": view.upstream_peer_address,
                "active_upstream_ips": active_upstream_ips,
                "fault_seconds": fault_seconds,
                "recovery_seconds": recovery_seconds,
                "total_chain_bytes_delta": total_chain_delta,
                "total_log_bytes_delta": total_log_delta,
                "overall_progress_ok": bool(overall_progress_ok),
            },
            level="info",
        )
        print(
            f"target_implementation={target_implementation} "
            f"upstream_peer={view.upstream_peer_address} "
            f"fault_window_chain_delta={_window_delta(chain_before, chain_fault_end)} "
            f"postfault_window_chain_delta={_window_delta(chain_fault_end, chain_recovery_end)} "
            f"overall_progress_ok={bool(overall_progress_ok)}"
        )
        return 0
    finally:
        if fault_active:
            _remove_reset_rules(active_upstream_ips, upstream_port, view)


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] != "reset":
        print(
            "usage: runtime_preview_upstream_reset_check.py reset --scenario-path PATH [--fault-seconds N] [--recovery-seconds N]",
            file=sys.stderr,
        )
        return 2
    scenario_path = None
    fault_seconds = 15
    recovery_seconds = 20
    i = 2
    while i < len(argv):
        if argv[i] == "--scenario-path" and i + 1 < len(argv):
            scenario_path = Path(argv[i + 1])
            i += 2
            continue
        if argv[i] == "--fault-seconds" and i + 1 < len(argv):
            fault_seconds = int(argv[i + 1])
            i += 2
            continue
        if argv[i] == "--recovery-seconds" and i + 1 < len(argv):
            recovery_seconds = int(argv[i + 1])
            i += 2
            continue
        print(f"unknown argument: {argv[i]}", file=sys.stderr)
        return 2
    if scenario_path is None:
        print("reset mode requires --scenario-path", file=sys.stderr)
        return 2
    return run_reset(scenario_path=scenario_path, fault_seconds=fault_seconds, recovery_seconds=recovery_seconds)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
