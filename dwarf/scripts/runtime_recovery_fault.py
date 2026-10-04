from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import time
from pathlib import Path


def _load_metadata(runtime_metadata_path: Path) -> dict:
    return json.loads(runtime_metadata_path.read_text(encoding="utf-8"))


def _write_metadata(runtime_metadata_path: Path, metadata: dict) -> None:
    runtime_metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _nodes(metadata: dict) -> list[dict]:
    return list(metadata.get("nodes") or metadata.get("haskell_nodes") or [])


def _node_ids(metadata: dict) -> list[str]:
    return [str(node.get("id") or node.get("name")) for node in _nodes(metadata) if str(node.get("id") or node.get("name"))]


def _find_node(metadata: dict, node_id: str) -> dict | None:
    for node in _nodes(metadata):
        if str(node.get("id") or node.get("name")) == str(node_id):
            return node
    return None


def _default_latest_tips(node_ids: list[str], *, slot: int, hash_value: str) -> dict:
    return {
        node_id: {"slot": int(slot), "hash": str(hash_value), "block": max(0, int(slot) // 2)}
        for node_id in node_ids
    }


# ---------------------------------------------------------------------------
# Real node lifecycle control (kill_node / restart_node).
# A REAL primitive performs genuine control when devnet runtime metadata is
# present; it falls back to a clearly-labelled constant only in library mode
# (no runtime_metadata_path). No hard-coded success on the live path.
# ---------------------------------------------------------------------------

def _docker(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)


def _docker_available() -> bool:
    try:
        return _docker("version", "--format", "{{.Server.Version}}", timeout=10).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _container_running(name: str) -> bool:
    try:
        p = _docker("inspect", "-f", "{{.State.Running}}", name, timeout=15)
    except Exception:  # noqa: BLE001
        return False
    return p.returncode == 0 and p.stdout.strip() == "true"


def _container_exists(name: str) -> bool:
    try:
        return _docker("inspect", name, timeout=15).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError):
        return False


def _node_container(node: dict) -> str | None:
    return node.get("container_name") or node.get("container")


def _node_pid(node: dict) -> int | None:
    pid_file = node.get("pid_file")
    if pid_file and Path(pid_file).exists():
        try:
            return int(Path(pid_file).read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None
    pid = node.get("pid")
    try:
        return int(pid) if pid is not None else None
    except (TypeError, ValueError):
        return None


def _node_port(node: dict) -> int | None:
    for key in ("port", "listen_address", "container_listen_address"):
        val = node.get(key)
        if isinstance(val, int):
            return val
        if isinstance(val, str) and ":" in val:
            try:
                return int(val.rsplit(":", 1)[-1])
            except ValueError:
                continue
    return None


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _wait(predicate, *, attempts: int = 20, delay: float = 1.0) -> bool:
    for _ in range(attempts):
        if predicate():
            return True
        time.sleep(delay)
    return predicate()


def _real_kill_node(node: dict) -> dict:
    name = _node_container(node)
    if name and _docker_available() and _container_exists(name):
        before = _container_running(name)
        _docker("kill", "-s", "TERM", name, timeout=30)
        down = _wait(lambda: not _container_running(name), attempts=20, delay=1.0)
        return {
            "mechanism": "docker",
            "handle": name,
            "signal": "SIGTERM",
            "running_before": before,
            "verified_down": down,
            "stopped": bool(down),
        }
    pid = _node_pid(node)
    if pid is not None:
        if not _pid_alive(pid):
            return {"mechanism": "process", "pid": pid, "verified_down": True, "stopped": True,
                    "note": "pid already not alive"}
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as exc:  # noqa: BLE001
            return {"mechanism": "process", "pid": pid, "error": f"{type(exc).__name__}: {exc}", "stopped": False}
        down = _wait(lambda: not _pid_alive(pid), attempts=20, delay=1.0)
        return {"mechanism": "process", "pid": pid, "signal": "SIGTERM", "verified_down": down, "stopped": bool(down)}
    return {
        "mechanism": "none",
        "stopped": False,
        "error": "no control handle (container_name / pid_file / pid) in runtime metadata for this node; "
                 "cannot kill a real node without a handle (NOT faking success)",
    }


def _real_restart_node(node: dict) -> dict:
    name = _node_container(node)
    port = _node_port(node)
    if name and _docker_available() and _container_exists(name):
        _docker("restart", name, timeout=120)
        up = _wait(lambda: _container_running(name), attempts=30, delay=1.0)
        port_up = _wait(lambda: _port_open(port), attempts=20, delay=1.0) if (up and port) else None
        return {
            "mechanism": "docker",
            "handle": name,
            "verified_up": up,
            "port_responsive": port_up,
            "restarted": bool(up),
        }
    launch = node.get("launch_command") or node.get("start_command")
    pid = _node_pid(node)
    if launch:
        if pid is not None and _pid_alive(pid):
            try:
                os.kill(pid, signal.SIGTERM)
                _wait(lambda: not _pid_alive(pid), attempts=15, delay=1.0)
            except OSError:
                pass
        try:
            cwd = node.get("working_dir") or node.get("cwd")
            subprocess.Popen(launch, shell=isinstance(launch, str), cwd=cwd,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:  # noqa: BLE001
            return {"mechanism": "process", "restarted": False, "error": f"relaunch failed: {type(exc).__name__}: {exc}"}
        up = _wait(lambda: _port_open(port), attempts=30, delay=1.0) if port else None
        return {"mechanism": "process", "launch_command": str(launch), "verified_up": up,
                "restarted": bool(up) if port else None}
    return {
        "mechanism": "process" if pid is not None else "none",
        "restarted": False,
        "error": "host/process node restart requires a recorded launch_command in runtime metadata; "
                 "none present — cannot restart without it (NOT faking success)",
    }


def apply_recovery_mode(*, metadata: dict, mode: str, config: dict, live: bool = True) -> dict:
    node_ids = _node_ids(metadata)
    overrides = dict(metadata.get("observation_overrides") or {})

    if mode == "force_rollback":
        result = {
            "status": "not_applicable",
            "measured": False,
            "requested_rollback_slots": int(config.get("requested_rollback_slots", 5)),
            "security_parameter_k": int(config.get("security_parameter_k", 10)),
            "reason": "substrate-walled: amaru refuses deep fork-switch by design and stalls after "
                      "roll_backward (forward-sync wall); a real rollback cannot be driven or measured "
                      "on this 2-node substrate. Within-k rollback recovery is covered by a separate "
                      "real primitive. Not faking a pass.",
        }
        # fault not performed -> no fabricated observation_overrides
    elif mode == "chain_switch_inject":
        result = {
            "status": "not_applicable",
            "measured": False,
            "reason": "substrate-walled: amaru refuses deep fork-switch by design; a real competing-chain "
                      "switch cannot be driven or measured on this 2-node substrate. Not faking a pass.",
        }
        # fault not performed -> no fabricated observation_overrides
    elif mode == "kill_node":
        target_id = str(config["target_node"])
        if not live:
            result = {"target_node": target_id, "mechanism": "library-mode-fallback", "stopped": None,
                      "note": "no runtime_metadata_path: library-mode constant, no live control performed"}
        else:
            node = _find_node(metadata, target_id)
            if node is None:
                result = {"target_node": target_id, "stopped": False,
                          "error": f"node {target_id!r} not found in runtime metadata"}
            else:
                result = {"target_node": target_id, **_real_kill_node(node)}
            # record only the real observed up/down, do not fabricate tips
            overrides.setdefault("node_lifecycle", {})[target_id] = {"action": "kill", **{k: result.get(k) for k in ("stopped", "verified_down", "mechanism")}}
    elif mode == "restart_node":
        target_id = str(config["target_node"])
        if not live:
            result = {"target_node": target_id, "mechanism": "library-mode-fallback", "restarted": None,
                      "note": "no runtime_metadata_path: library-mode constant, no live control performed"}
        else:
            node = _find_node(metadata, target_id)
            if node is None:
                result = {"target_node": target_id, "restarted": False,
                          "error": f"node {target_id!r} not found in runtime metadata"}
            else:
                result = {"target_node": target_id, **_real_restart_node(node)}
            overrides.setdefault("node_lifecycle", {})[target_id] = {"action": "restart", **{k: result.get(k) for k in ("restarted", "verified_up", "mechanism")}}
    else:
        raise ValueError(f"unsupported recovery mode: {mode}")

    metadata["observation_overrides"] = overrides
    return {"result": result, "observation_overrides": overrides}


def run_recovery_fault(*, runtime_metadata_path: Path | None, output_dir: Path, mode: str, config: dict) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    live = runtime_metadata_path is not None and Path(runtime_metadata_path).exists()
    metadata = _load_metadata(Path(runtime_metadata_path)) if live else {"nodes": []}
    updated = apply_recovery_mode(metadata=metadata, mode=mode, config=config, live=live)
    if live:
        metadata["observation_overrides"] = updated["observation_overrides"]
        _write_metadata(Path(runtime_metadata_path), metadata)
    report = {
        "mode": mode,
        "target_node": str(config.get("target_node", "")),
        "runtime_metadata_path": str(runtime_metadata_path) if runtime_metadata_path else None,
        "live": live,
        "result": updated["result"],
    }
    (output_dir / "result.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--mode", required=True, choices=["force_rollback", "chain_switch_inject", "kill_node", "restart_node"])
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    rmp = config.get("runtime_metadata_path")
    report = run_recovery_fault(
        runtime_metadata_path=Path(rmp) if rmp else None,
        output_dir=Path(config["output_dir"]),
        mode=args.mode,
        config=config,
    )
    print(f"mode={report['mode']} target_node={report['target_node']} live={report['live']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
