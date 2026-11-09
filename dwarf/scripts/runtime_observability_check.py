#!/usr/bin/env python3

import sys
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_common import PROFILE_A_CONFIG  # noqa: E402
from runtime_telemetry import emit_runtime_metric, emit_target_event  # noqa: E402


RUNTIME_ROOT = PROFILE_A_CONFIG.runtime_root
ENV_ROOT = PROFILE_A_CONFIG.env_root


def _configuration() -> dict:
    config_path = ENV_ROOT / "configuration.yaml"
    if not config_path.exists():
        raise RuntimeError(f"missing configuration file: {config_path}")
    with config_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise RuntimeError(f"unexpected configuration structure in {config_path}")
    return data


def _emit_log_file_metrics(log_files: list[Path]) -> None:
    total_bytes = 0
    non_empty = 0
    for log_path in log_files:
        size_bytes = log_path.stat().st_size
        total_bytes += size_bytes
        if size_bytes > 0:
            non_empty += 1
        emit_target_event(
            primitive="runtime_observability_check",
            event="log_file_observed",
            payload={"path": str(log_path), "size_bytes": size_bytes},
        )
        emit_runtime_metric("observability_log_file_bytes", value=size_bytes, meta={"path": str(log_path)})
    emit_runtime_metric("observability_log_file_count", value=len(log_files), meta={"kind": "count"})
    emit_runtime_metric("observability_log_nonempty_count", value=non_empty, meta={"kind": "count"})
    emit_runtime_metric("observability_log_total_bytes", value=total_bytes, meta={"kind": "bytes"})


def _logs_root() -> Path:
    candidates = (RUNTIME_ROOT / "logs", ENV_ROOT / "logs")
    for path in candidates:
        if path.is_dir():
            return path
    raise RuntimeError(f"missing logs directory: {candidates[0]}")


def run_log_baseline() -> int:
    logs_root = _logs_root()
    log_files = sorted(logs_root.glob("*/stdout.log"))
    if len(log_files) < 3:
        raise RuntimeError(f"expected at least 3 stdout.log files under {logs_root}, found {len(log_files)}")
    _emit_log_file_metrics(log_files)
    emit_target_event(
        primitive="runtime_observability_check",
        event="log_baseline_completed",
        payload={"logs_root": str(logs_root), "log_count": len(log_files)},
    )
    print(f"logs_root={logs_root} log_count={len(log_files)}")
    return 0


# Legacy tracing flag -> trace-dispatcher namespace that carries the same events.
TRACE_NAMESPACES = {
    "TraceConnectionManager": "Net.ConnectionManager",
    "TracePeerSelection": "Net.PeerSelection",
    "TraceLocalRootPeers": "Net.Peers.LocalRoot",
    "TracePublicRootPeers": "Net.Peers.PublicRoot",
}
_SEVERITY_ORDER = ("Debug", "Info", "Notice", "Warning", "Error", "Critical", "Alert", "Emergency", "Silence")


def _dispatcher_severity(options: dict, namespace: str) -> str:
    """The severity the dispatcher applies to ``namespace``: the longest configured prefix wins."""
    parts = namespace.split(".")
    for length in range(len(parts), -1, -1):
        key = ".".join(parts[:length])
        entry = options.get(key)
        if isinstance(entry, dict) and isinstance(entry.get("severity"), str):
            return entry["severity"]
    return "Notice"  # cardano-node's dispatcher default


def trace_settings(config: dict) -> dict:
    """Read the peer and connection trace settings from either tracing system (F107)."""
    options = config.get("TraceOptions")
    if isinstance(options, dict) and not ("EnableLogging" in config or "setupBackends" in config):
        severities = {ns: _dispatcher_severity(options, ns) for ns in TRACE_NAMESPACES.values()}
        missing = [ns for ns, severity in severities.items() if severity != "Debug"]
        return {"system": "trace-dispatcher", "severities": severities, "missing": missing,
                "enable_logging": True, "min_severity": "Debug" if not missing else "per-namespace",
                "ok": not missing}
    setup = config.get("setupBackends")
    if not isinstance(setup, dict):
        setup = config
    missing = [key for key in TRACE_NAMESPACES if not setup.get(key)]
    enable_logging = bool(setup.get("EnableLogging"))
    min_severity = str(setup.get("minSeverity", ""))
    return {"system": "legacy", "missing": missing, "enable_logging": enable_logging,
            "min_severity": min_severity,
            "ok": enable_logging and min_severity == "Debug" and not missing}


def run_trace_settings_baseline() -> int:
    result = trace_settings(_configuration())
    flags = list(TRACE_NAMESPACES.values() if result["system"] == "trace-dispatcher" else TRACE_NAMESPACES)
    emit_runtime_metric("observability_trace_flag_count", value=len(flags) - len(result["missing"]), meta={"kind": "enabled"})
    emit_runtime_metric("observability_enable_logging", value=1 if result["enable_logging"] else 0, meta={"kind": "bool"})
    emit_runtime_metric("observability_min_severity_debug", value=1 if result["ok"] else 0, meta={"kind": "bool"})
    for key in flags:
        emit_runtime_metric("observability_trace_flag_enabled", value=0 if key in result["missing"] else 1, meta={"flag": key})
    emit_target_event(
        primitive="runtime_observability_check",
        event="trace_settings_observed",
        payload={**result, "trace_flags": flags},
        level="info" if result["ok"] else "error",
    )
    if not result["ok"]:
        raise RuntimeError(f"{result['system']} trace settings do not enable Debug for: {', '.join(result['missing']) or 'logging'}")
    print(f"system={result['system']} trace_flag_count={len(flags)}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: runtime_observability_check.py <log-baseline|trace-settings-baseline>", file=sys.stderr)
        return 2
    if argv[1] == "log-baseline":
        return run_log_baseline()
    if argv[1] == "trace-settings-baseline":
        return run_trace_settings_baseline()
    print(f"unknown mode: {argv[1]}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
