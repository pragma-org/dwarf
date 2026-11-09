#!/usr/bin/env python3

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_telemetry import emit_target_event  # noqa: E402


DEFAULT_WORKLOAD_SCRIPT = SCRIPT_DIR / "runtime_fetch_check.py"


def count_packets(pcap_path: Path, *, tcpdump_bin: str | None = None) -> int:
    """Count complete packet records in a pcap file without tcpdump (F113).

    Reads the classic pcap global header (micro- or nanosecond magic, either byte order) and walks
    the 16-byte record headers; a truncated final record is not counted.
    """
    import struct

    try:
        data = Path(pcap_path).read_bytes()
    except OSError:
        return 0
    if len(data) < 24:
        return 0
    magic = data[:4]
    if magic in (bytes.fromhex("d4c3b2a1"), bytes.fromhex("4d3cb2a1")):
        endian = "<"
    elif magic in (bytes.fromhex("a1b2c3d4"), bytes.fromhex("a1b23c4d")):
        endian = ">"
    else:
        return 0
    offset, count = 24, 0
    while offset + 16 <= len(data):
        _sec, _frac, included, _original = struct.unpack_from(endian + "IIII", data, offset)
        offset += 16 + included
        if offset > len(data):
            break
        count += 1
    return count


def run_capture(
    *,
    interface: str,
    output_dir: Path,
    workload_mode: str,
    target_host: str,
    target_ports: list[int],
    connect_attempts: int,
    workload_script: str,
    python_bin: str,
    tcpdump_bin: str,
    sudo_bin: str,
    startup_seconds: float,
    settle_seconds: float,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    pcap_path = output_dir / "capture.pcap"
    tcpdump_cmd = [
        sudo_bin,
        "-n",
        tcpdump_bin,
        "-i",
        interface,
        "-n",
        "-s",
        "0",
        "-U",
        "-w",
        str(pcap_path),
        "tcp",
    ]
    workload_cmd = [python_bin, workload_script, workload_mode]

    tcpdump_proc = subprocess.Popen(
        tcpdump_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        time.sleep(startup_seconds)
        if workload_mode == "tcp-connect-burst":
            connect_successes = 0
            for _ in range(connect_attempts):
                for port in target_ports:
                    with socket.create_connection((target_host, port), timeout=1.0):
                        connect_successes += 1
            workload = subprocess.CompletedProcess(
                ["tcp-connect-burst", target_host, *[str(port) for port in target_ports]],
                0,
                stdout=f"connect_successes={connect_successes}\n",
                stderr="",
            )
        else:
            connect_successes = 0
            workload = subprocess.run(
                workload_cmd,
                capture_output=True,
                text=True,
                check=False,
            )
        time.sleep(settle_seconds)
    finally:
        tcpdump_proc.send_signal(signal.SIGINT)
        try:
            tcpdump_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            tcpdump_proc.kill()
            tcpdump_proc.wait(timeout=5)

    packet_count = count_packets(pcap_path) if pcap_path.exists() else 0
    return {
        "interface": interface,
        "workload_mode": workload_mode,
        "pcap_path": str(pcap_path),
        "packet_count": packet_count,
        "pcap_size_bytes": pcap_path.stat().st_size if pcap_path.exists() else 0,
        "workload_exit_code": int(workload.returncode),
        "tcpdump_exit_code": int(tcpdump_proc.returncode or 0),
        "connect_successes": int(connect_successes),
        "workload_stdout": (workload.stdout or "")[:4096],
        "workload_stderr": (workload.stderr or "")[:1024],
    }


def run_container_capture(*, runtime_metadata_path: Path, target_nodes: list[str], output_dir: Path,
                          connect_attempts: int, startup_seconds: float, settle_seconds: float) -> dict:
    """Capture the controller's own traffic to container nodes (F113).

    The workload starts in the controller, as it started on the host before, so tcpdump runs in
    the controller's network namespace from the capture-tools image (NET_RAW/NET_ADMIN only) and
    writes the pcap to stdout. The connect burst uses each node's container endpoint.
    """
    try:
        from scripts import runtime_common as common
    except ImportError:  # pragma: no cover - helper subprocess path
        import runtime_common as common

    body = json.loads(Path(runtime_metadata_path).read_text(encoding="utf-8"))
    entries = {}
    for entry in [*(body.get("haskell_nodes") or []), *(body.get("amaru_nodes") or [])]:
        for key in (entry.get("name"), entry.get("id")):
            if key:
                entries[key] = entry
    endpoints = []
    for name in target_nodes:
        entry = entries.get(name) or {}
        address = str(entry.get("container_listen_address") or "")
        if not entry.get("container_ip") or ":" not in address:
            raise RuntimeError(f"runtime metadata has no container endpoint for {name}")
        endpoints.append((str(entry["container_ip"]), int(address.rsplit(":", 1)[1])))
    output_dir.mkdir(parents=True, exist_ok=True)
    pcap_path = output_dir / "capture.pcap"
    tool = ["tcpdump", "-i", "any", "-n", "-s", "0", "-U", "-w", "-", "tcp"]
    argv = common.capture_tools_command(tool, network_container=os.environ.get("HOSTNAME") or "",
                                        capabilities=("NET_RAW", "NET_ADMIN"))
    connect_successes = 0
    stderr_path = output_dir / "tcpdump.stderr.log"
    with pcap_path.open("wb") as sink, stderr_path.open("wb") as errors:
        proc = subprocess.Popen(argv, stdout=sink, stderr=errors)
        try:
            # docker run needs longer than a fixed wait to start the helper; the burst starts only
            # once tcpdump reports it is listening (live 3e975402: the burst ran before it).
            deadline = time.monotonic() + max(30.0, startup_seconds)
            while b"listening on" not in stderr_path.read_bytes():
                if time.monotonic() > deadline or (hasattr(proc, "poll") and proc.poll() is not None):
                    raise RuntimeError("tcpdump did not start listening in the controller namespace: "
                                       + stderr_path.read_bytes().decode("utf-8", "replace")[-300:])
                time.sleep(0.2)
            time.sleep(startup_seconds)
            for _ in range(connect_attempts):
                for host, port in endpoints:
                    with socket.create_connection((host, port), timeout=1.0):
                        connect_successes += 1
            time.sleep(settle_seconds)
        finally:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
    packet_count = count_packets(pcap_path)
    return {
        "interface": "any",
        "workload_mode": "tcp-connect-burst",
        "mechanism": "controller-netns",
        "pcap_path": str(pcap_path),
        "packet_count": packet_count,
        "pcap_size_bytes": pcap_path.stat().st_size,
        "workload_exit_code": 0 if connect_successes == connect_attempts * len(endpoints) else 1,
        "tcpdump_exit_code": int(proc.returncode or 0),
        "connect_successes": int(connect_successes),
        "workload_stdout": f"connect_successes={connect_successes}\n",
        "workload_stderr": "",
    }


def _default_output_dir() -> Path:
    run_dir = os.environ.get("ADA2_DWARF_RUN_DIR")
    if run_dir:
        return Path(run_dir) / "outputs" / "runtime-pcap-capture"
    return Path.cwd() / "outputs" / "runtime-pcap-capture"


def _relative_pcap_path(pcap_path: Path) -> str:
    run_dir = os.environ.get("ADA2_DWARF_RUN_DIR")
    if run_dir:
        try:
            return str(pcap_path.relative_to(Path(run_dir)))
        except ValueError:
            pass
    return str(pcap_path)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Capture bounded runtime pcap while running a stable fetch workload")
    parser.add_argument("--interface", default="lo")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--workload-mode", default="tcp-connect-burst")
    parser.add_argument("--target-host", default="127.0.0.1")
    parser.add_argument("--target-ports", nargs="+", type=int, default=[33001, 33002, 33003])
    parser.add_argument("--connect-attempts", type=int, default=3)
    parser.add_argument("--workload-script", default=str(DEFAULT_WORKLOAD_SCRIPT))
    parser.add_argument("--python-bin", default="python3")
    parser.add_argument("--tcpdump-bin", default="tcpdump")
    parser.add_argument("--sudo-bin", default="sudo")
    parser.add_argument("--startup-seconds", type=float, default=1.0)
    parser.add_argument("--settle-seconds", type=float, default=0.5)
    parser.add_argument("--runtime-metadata-path", default=None)
    parser.add_argument("--target-nodes", nargs="+", default=None)
    args = parser.parse_args(argv[1:])

    output_dir = Path(args.output_dir) if args.output_dir else _default_output_dir()
    emit_target_event(
        primitive="runtime_pcap_capture",
        event="pcap_capture_started",
        payload={
            "interface": args.interface,
            "workload_mode": args.workload_mode,
            "target_host": args.target_host,
            "target_ports": args.target_ports,
            "output_dir": str(output_dir),
        },
    )

    if args.runtime_metadata_path and args.target_nodes:
        result = run_container_capture(
            runtime_metadata_path=Path(args.runtime_metadata_path), target_nodes=args.target_nodes,
            output_dir=output_dir, connect_attempts=args.connect_attempts,
            startup_seconds=args.startup_seconds, settle_seconds=args.settle_seconds,
        )
    else:
        result = run_capture(
            interface=args.interface,
            output_dir=output_dir,
            workload_mode=args.workload_mode,
            target_host=args.target_host,
            target_ports=args.target_ports,
            connect_attempts=args.connect_attempts,
            workload_script=args.workload_script,
            python_bin=args.python_bin,
            tcpdump_bin=args.tcpdump_bin,
            sudo_bin=args.sudo_bin,
            startup_seconds=args.startup_seconds,
            settle_seconds=args.settle_seconds,
        )
    result["pcap_relpath"] = _relative_pcap_path(Path(result["pcap_path"]))
    emit_target_event(
        primitive="runtime_pcap_capture",
        event="pcap_capture_completed",
        payload=result,
        level="info" if result["workload_exit_code"] == 0 and result["packet_count"] > 0 else "error",
    )
    print(
        "interface={interface} workload_mode={workload_mode} packet_count={packet_count} "
        "connect_successes={connect_successes} "
        "pcap_relpath={pcap_relpath} pcap_size_bytes={pcap_size_bytes} "
        "workload_exit_code={workload_exit_code} tcpdump_exit_code={tcpdump_exit_code}".format(**result)
    )
    return 0 if result["workload_exit_code"] == 0 and result["packet_count"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
