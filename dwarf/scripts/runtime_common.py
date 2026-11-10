#!/usr/bin/env python3

import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimeConfig:
    runtime_root: Path
    env_root: Path
    session: str
    cardano_cli: Path
    cardano_node: Path
    network_magic: str
    blockfetch_bin: str
    chainsync_bin: str
    log_paths: tuple[Path, ...]
    fallback_range: tuple[str, str]


_LEGACY_PROFILES_ROOT = Path("/opt/dwarf/cardano-profiles")
_LEGACY_BIN = Path("/home/dwarf/.local/bin")


def _resolve_binary(name: str) -> Path:
    legacy = _LEGACY_BIN / name
    if legacy.exists():
        return legacy
    found = shutil.which(name)
    return Path(found) if found else legacy


def resolve_profile_config(profile_id: str, *, legacy_root: Path | None = None) -> RuntimeConfig:
    """Build the helper config for a profile against whichever layout exists.

    The host-devnet era kept profiles under ``/opt/dwarf/cardano-profiles/<id>`` with binaries in
    ``~/.local/bin``; a dashboard install keeps the profile runtime under the controller state
    (``profile_manager.profiles.runtime_root``) with ``cardano-cli`` on PATH. Legacy wins when present.
    """
    legacy = Path(legacy_root) if legacy_root is not None else _LEGACY_PROFILES_ROOT / profile_id
    runtime_root = legacy
    if not legacy.exists():
        try:
            from profile_manager import profiles as _profiles

            runtime_root = Path(_profiles.runtime_root(profile_id))
        except Exception:  # noqa: BLE001 - stay on the legacy path when the package is unavailable
            runtime_root = legacy
    return RuntimeConfig(
        runtime_root=runtime_root,
        env_root=runtime_root / "env",
        session="cardano-profile-a",
        cardano_cli=_resolve_binary("cardano-cli"),
        cardano_node=_resolve_binary("cardano-node"),
        network_magic="42",
        blockfetch_bin="/home/dwarf/dwarf-fw/targets/amaru/target/release/dwarf-amaru-runtime-blockfetch",
        chainsync_bin="/home/dwarf/amaru-verification/target/debug/amaru",
        log_paths=tuple(runtime_root / "logs" / node / "stdout.log" for node in ("node3", "node2", "node1")),
        fallback_range=(
            "4318100.9166aa0afdf7fc09ff1504b987371f62b24022b81726a7cff09165d78069e50b",
            "4318130.f71200f374e97287835e573fadfa9a051bbb7dd40f85f746973f28bed5d43fb0",
        ),
    )


PROFILE_A_CONFIG = resolve_profile_config("profile-a-haskell-peersharing-disabled")

# Rust runtime tools are host-built by scripts/build_runtime_tools.py; the installed controller
# mounts the host measurement-targets state read-only here. The legacy paths are the host-devnet
# era's in-place cargo builds and are used only when present.
_RUNTIME_TOOLS_RECORD = Path("/var/dwarf/controller-state/measurement-targets/runtime-tools/record.json")
_LEGACY_RUNTIME_TOOLS = {
    "dwarf-amaru-runtime-blockfetch": "/home/dwarf/dwarf-fw/targets/amaru/target/release/dwarf-amaru-runtime-blockfetch",
    "dwarf-amaru-runtime-blockfetch-stale-server": "/home/dwarf/dwarf-fw/targets/amaru/target/release/dwarf-amaru-runtime-blockfetch-stale-server",
}


def runtime_tool(name: str) -> str:
    """Absolute path of a host-built runtime tool, verified against its recorded sha256."""
    record_path = Path(os.environ.get("DWARF_RUNTIME_TOOLS_RECORD") or _RUNTIME_TOOLS_RECORD)
    if record_path.is_file():
        record = json.loads(record_path.read_text(encoding="utf-8"))
        entry = (record.get("tools") or {}).get(name)
        if not isinstance(entry, dict):
            raise RuntimeError(f"runtime tool {name} is not in {record_path}; rebuild with scripts/build_runtime_tools.py")
        path = (record_path.parent / str(entry["path"])).resolve()
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        if digest != entry.get("sha256"):
            raise RuntimeError(f"runtime tool {name} digest does not match {record_path}")
        return str(path)
    legacy = _LEGACY_RUNTIME_TOOLS.get(name)
    if legacy and Path(legacy).exists():
        return legacy
    raise RuntimeError(f"runtime tool {name} is not built; run scripts/build_runtime_tools.py on the host")


def run(args, *, timeout=60, check=True, text=True, capture_output=True, env=None):
    return subprocess.run(
        args,
        timeout=timeout,
        check=check,
        text=text,
        capture_output=capture_output,
        env=env,
    )


def directory_size_bytes(path: Path) -> int:
    total = 0
    for item in Path(path).rglob("*"):
        if item.is_file():
            total += item.stat().st_size
    return total


def session_exists(config: RuntimeConfig) -> bool:
    result = subprocess.run(["tmux", "has-session", "-t", config.session], text=True, capture_output=True)
    return result.returncode == 0


def matching_pids(pattern: str):
    result = subprocess.run(["pgrep", "-f", pattern], text=True, capture_output=True)
    if result.returncode != 0:
        return []
    return [int(line.strip()) for line in result.stdout.splitlines() if line.strip()]


def startup_command(config: RuntimeConfig) -> str:
    start_log = config.runtime_root / "logs" / "start-devnet.log"
    return (
        f"cd {config.runtime_root}; "
        f"export PATH={Path.home() / '.local' / 'bin'}:$PATH; "
        f"export CARDANO_CLI={config.cardano_cli}; "
        f"export CARDANO_NODE={config.cardano_node}; "
        f"cardano-testnet cardano --node-env {config.env_root} --num-pool-nodes 3 --update-time 2>&1 | tee {start_log}"
    )


def stop_session(config: RuntimeConfig) -> None:
    details = stop_session_with_details(config)
    if details["remaining_pids"]:
        raise RuntimeError("profile processes did not stop within 20 seconds")


def _profile_node_containers(config: RuntimeConfig) -> list[str]:
    """Node container names when the profile runs as containers (F121), else empty."""
    try:
        body = json.loads((config.runtime_root / "runtime.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = [*(body.get("nodes") or []), *(body.get("haskell_nodes") or []), *(body.get("amaru_nodes") or [])]
    names = []
    for entry in entries:
        name = isinstance(entry, dict) and entry.get("container_name")
        if name and name not in names:
            names.append(str(name))
    return names


def _require_containers_on_controller(config: RuntimeConfig, containers: list[str]) -> None:
    """A dashboard/controller profile runs its nodes as containers; the host-era tmux path only
    exists under the legacy profiles root. Finding no container there is an error, never a
    silent tmux 'restart' that kills nothing (run 20261109T040452Z passed that way)."""
    legacy = Path(config.runtime_root).is_relative_to(_LEGACY_PROFILES_ROOT)
    if not containers and not legacy:
        raise RuntimeError(
            f"controller profile at {config.runtime_root} lists no node containers in runtime.json; "
            "refusing the host-era tmux stop/start (it would restart nothing)")


def stop_session_with_details(config: RuntimeConfig) -> dict:
    started_at = time.monotonic()
    containers = _profile_node_containers(config)
    _require_containers_on_controller(config, containers)
    if containers:
        # F121: a container profile is stopped through its node containers, not a tmux session
        result = subprocess.run(["docker", "stop", "-t", "30", *containers], check=False, text=True, capture_output=True)
        if result.returncode != 0:
            raise RuntimeError(f"docker stop failed: {(result.stderr or result.stdout).strip()[-300:]}")
        return {"mechanism": "container", "session_existed": True, "stopped_containers": containers,
                "killed_pids": [], "forced_kills": [], "remaining_pids": [],
                "duration_seconds": round(time.monotonic() - started_at, 6)}
    details = {
        "session_existed": session_exists(config),
        "killed_pids": [],
        "forced_kills": [],
        "remaining_pids": [],
    }
    if session_exists(config):
        result = subprocess.run(
            ["tmux", "kill-session", "-t", config.session],
            check=False,
            text=True,
            capture_output=True,
        )
        if result.returncode != 0 and session_exists(config):
            raise RuntimeError(result.stderr.strip() or f"failed to kill tmux session {config.session}")
    patterns = [
        f"cardano-testnet cardano --node-env {config.env_root}",
        f"cardano-node run --config {config.env_root / 'configuration.yaml'}",
    ]
    pids = []
    for pattern in patterns:
        pids.extend(matching_pids(pattern))
    for pid in sorted(set(pids)):
        subprocess.run(["kill", str(pid)], check=False, text=True, capture_output=True)
        details["killed_pids"].append(pid)
    deadline = time.time() + 20
    remaining = []
    while time.time() < deadline:
        remaining = []
        for pattern in patterns:
            remaining.extend(matching_pids(pattern))
        if not remaining:
            details["duration_seconds"] = round(time.monotonic() - started_at, 6)
            return details
        time.sleep(1)
    for pid in sorted(set(remaining)):
        subprocess.run(["kill", "-9", str(pid)], check=False, text=True, capture_output=True)
        details["forced_kills"].append(pid)
    time.sleep(1)
    remaining = []
    for pattern in patterns:
        remaining.extend(matching_pids(pattern))
    details["remaining_pids"] = sorted(set(remaining))
    details["duration_seconds"] = round(time.monotonic() - started_at, 6)
    return details


def start_session(config: RuntimeConfig) -> None:
    details = start_session_with_details(config)
    if details.get("start_failed"):
        raise RuntimeError(f"failed to start tmux session {config.session}")


def start_session_with_details(config: RuntimeConfig) -> dict:
    started_at = time.monotonic()
    containers = _profile_node_containers(config)
    _require_containers_on_controller(config, containers)
    if containers:
        result = subprocess.run(["docker", "start", *containers], check=False, text=True, capture_output=True)
        if result.returncode != 0:
            raise RuntimeError(f"docker start failed: {(result.stderr or result.stdout).strip()[-300:]}")
        return {"mechanism": "container", "already_running": False, "started": True,
                "started_containers": containers, "duration_seconds": round(time.monotonic() - started_at, 6)}
    details = {"already_running": session_exists(config)}
    if session_exists(config):
        details["duration_seconds"] = round(time.monotonic() - started_at, 6)
        return details
    result = subprocess.run(
        ["tmux", "new-session", "-d", "-s", config.session, "bash", "-lc", startup_command(config)],
        check=True,
        text=True,
        capture_output=True,
    )
    details["started"] = result.returncode == 0
    details["duration_seconds"] = round(time.monotonic() - started_at, 6)
    return details


_SHORT_SOCKET_DIR = Path("/tmp/dwarf-sock")
_UNIX_SOCKET_PATH_LIMIT = 100  # sun_path holds 108 bytes including the terminator; keep a margin


def short_socket_path(path: Path) -> Path:
    """Return a path short enough for a unix socket connect, linking to ``path`` when needed (F94).

    The kernel limits the path string given to ``connect``; it resolves a symlink after that check,
    so a short link to a deeply nested socket works for any client.
    """
    path = Path(path)
    if len(str(path)) <= _UNIX_SOCKET_PATH_LIMIT:
        return path
    import hashlib
    _SHORT_SOCKET_DIR.mkdir(parents=True, exist_ok=True)
    link = _SHORT_SOCKET_DIR / (hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16] + ".sock")
    try:
        if link.is_symlink() and Path(os.readlink(link)) == path:
            return link
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(path)
    except OSError:
        return path
    return link


def query_tip(config: RuntimeConfig, node: str):
    socket_candidates = (
        config.runtime_root / "socket" / f"{node}.sock",
        config.runtime_root / "socket" / node / "sock",
        config.env_root / "socket" / node / "sock",
    )
    existing_candidates = []
    seen = set()
    for path in socket_candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if path.exists():
            existing_candidates.append(path)
    if not existing_candidates:
        existing_candidates = [socket_candidates[0]]

    last_error = None
    for socket_path in existing_candidates:
        env = {"CARDANO_NODE_SOCKET_PATH": str(short_socket_path(socket_path))}
        result = run(
            [str(config.cardano_cli), "query", "tip", "--testnet-magic", config.network_magic],
            timeout=15,
            check=False,
            env=env,
        )
        if result.returncode != 0:
            last_error = result.stderr.strip() or f"tip query failed for {node}"
            continue
        data = json.loads(result.stdout)
        return {
            "slot": int(data.get("slot", 0)),
            "block": int(data.get("block", 0)),
            "syncProgress": str(data.get("syncProgress", "")),
            "hash": data.get("hash", ""),
        }
    raise RuntimeError(last_error or f"tip query failed for {node}")


def _runtime_metadata_entry(config: RuntimeConfig, node: str):
    metadata_path = config.runtime_root / "runtime.json"
    if not metadata_path.exists():
        return None
    try:
        body = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    for entry in body.get("haskell_nodes", []):
        if entry.get("name") == node:
            return entry
    return None


def _node_entry(config: RuntimeConfig, node: str) -> dict | None:
    try:
        body = json.loads((config.runtime_root / "runtime.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for entry in [*(body.get("haskell_nodes") or []), *(body.get("amaru_nodes") or [])]:
        if isinstance(entry, dict) and node in (entry.get("name"), entry.get("id")):
            return entry
    return None


def node_wire_endpoint(node: dict, target_host: str | None = None) -> tuple[str, int]:
    """Where a wire probe in the controller connects to a runtime node (F63, shared by F126).

    A controller-run node records ``container_ip`` and ``container_listen_address``; the probe runs
    attached to that network, where the host-published loopback port is not the node. An explicit
    non-loopback ``target_host`` (multi-host substrates) keeps the published port.
    """
    explicit = str(target_host or "").strip()
    if explicit and explicit not in {"127.0.0.1", "localhost"}:
        return explicit, int(node["port"])
    container_ip = str(node.get("container_ip") or "").strip()
    listen = str(node.get("container_listen_address") or "")
    if container_ip and ":" in listen:
        return container_ip, int(listen.rsplit(":", 1)[1])
    if node.get("observation_transport") == "antithesis-package" and ":" in listen:
        # Inside an Antithesis package the workload reaches the node by its compose hostname.
        host, port = listen.rsplit(":", 1)
        return host, int(port)
    return (explicit or "127.0.0.1"), int(node["port"])


def runtime_node_wire_endpoint(runtime_metadata_path, target_node: str, target_host: str | None = None,
                               *, fallback_port: int | None = None) -> tuple[str, int]:
    """node_wire_endpoint for ``target_node`` in a runtime metadata file."""
    body = json.loads(Path(runtime_metadata_path).read_text(encoding="utf-8"))
    for entry in [*(body.get("nodes") or []), *(body.get("haskell_nodes") or []), *(body.get("amaru_nodes") or [])]:
        if isinstance(entry, dict) and target_node in (entry.get("id"), entry.get("name")):
            node = dict(entry)
            if not node.get("port") and fallback_port:
                node["port"] = fallback_port
            return node_wire_endpoint(node, target_host)
    if fallback_port:
        return (str(target_host or "127.0.0.1"), int(fallback_port))
    raise RuntimeError(f"runtime metadata has no node {target_node!r}")


def run_in_node_netns(container: str, script: str, *, runner=subprocess.run):
    """Run ``script`` in a node container's network namespace (F99 helper settings).

    The helper is the controller's own image; it runs as root with every capability dropped
    except NET_ADMIN, and rules it installs stay in the node's namespace after it exits.
    """
    own = os.environ.get("HOSTNAME") or ""
    probe = runner(["docker", "inspect", "-f", "{{.Image}}", own], capture_output=True, text=True, timeout=15, check=False)
    image = (probe.stdout or "").strip() if probe.returncode == 0 else ""
    if not image:
        raise RuntimeError("could not resolve the controller image for the netns helper")
    result = runner(
        ["docker", "run", "--rm", "--network", f"container:{container}", "--user", "0:0",
         "--cap-drop", "ALL", "--cap-add", "NET_ADMIN", "--security-opt", "no-new-privileges",
         "--entrypoint", "sh", image, "-c", script],
        capture_output=True, text=True, timeout=60, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"netns helper failed in {container} (exit {result.returncode}): {(result.stderr or '')[-200:]}")
    return result


def capture_tools_command(tool_argv, *, network_container: str | None = None, pid_container: str | None = None,
                          capabilities=(), runner=subprocess.run) -> list[str]:
    """``docker run`` argv for the capture-tools image, joined to one namespace (F113).

    The image is resolved to its immutable id and must carry this release's revision label, so a
    stale or foreign image is never used. Only the named capabilities are added.
    """
    reference = os.environ.get("DWARF_CAPTURE_TOOLS_IMAGE") or "dwarf/capture-tools:current"
    probe = runner(["docker", "image", "inspect", "-f",
                    "{{.Id}} {{index .Config.Labels \"org.opencontainers.image.revision\"}}", reference],
                   capture_output=True, text=True, timeout=15, check=False)
    fields = (probe.stdout or "").split()
    if probe.returncode != 0 or len(fields) != 2:
        raise RuntimeError(f"capture-tools image {reference} is unavailable; reinstall to build it")
    image_id, revision = fields
    expected = os.environ.get("DWARF_SOURCE_REVISION") or ""
    if expected and revision != expected:
        raise RuntimeError(f"capture-tools image revision {revision} differs from the controller revision {expected}")
    argv = ["docker", "run", "--rm", "--user", "0:0", "--cap-drop", "ALL"]
    for capability in capabilities:
        argv += ["--cap-add", capability]
    argv += ["--security-opt", "no-new-privileges"]
    if network_container:
        argv += ["--network", f"container:{network_container}"]
    if pid_container:
        argv += ["--pid", f"container:{pid_container}", "--network", "none"]
    return [*argv, image_id, *tool_argv]


def _controller_ip_toward(container: str, *, runner=subprocess.run) -> str:
    """The controller's address on a network it shares with ``container``."""
    own = os.environ.get("HOSTNAME") or ""
    template = "{{range $name, $_ := .NetworkSettings.Networks}}{{$name}} {{end}}"
    node_networks = runner(["docker", "inspect", "-f", template, container], capture_output=True, text=True, timeout=15, check=False)
    for network in (node_networks.stdout or "").split():
        address = runner(
            ["docker", "inspect", "-f", "{{(index .NetworkSettings.Networks \"" + network + "\").IPAddress}}", own],
            capture_output=True, text=True, timeout=15, check=False,
        )
        ip = (address.stdout or "").strip()
        if address.returncode == 0 and ip and ip != "<no value>":
            return ip
    raise RuntimeError(f"the controller shares no network with {container}")


def observer_partition(config: RuntimeConfig, node: str, *, apply: bool, runner=subprocess.run) -> dict:
    """Cut (or restore) the traffic between this controller and one node's N2N port (F110).

    Container nodes: rules in the node's own namespace, matching only the controller's address,
    so peer traffic is untouched. Host nodes keep the legacy loopback rules.
    """
    flag = "-I" if apply else "-D"
    entry = _node_entry(config, node) or {}
    address = str(entry.get("container_listen_address") or "")
    if entry.get("container_name") and ":" in address:
        port = int(address.rsplit(":", 1)[1])
        controller_ip = _controller_ip_toward(str(entry["container_name"]), runner=runner)
        rules = [
            f"iptables -w {flag} INPUT -p tcp -s {controller_ip} --dport {port} -j DROP",
            f"iptables -w {flag} OUTPUT -p tcp -d {controller_ip} --sport {port} -j DROP",
        ]
        run_in_node_netns(str(entry["container_name"]), " && ".join(rules), runner=runner)
        return {"mechanism": "container-netns", "node": node, "port": port, "observer": controller_ip}
    port = target_port(config, node)
    for chain, spec in (("OUTPUT", f"-p tcp -d 127.0.0.1 --dport {port} -j DROP"),
                        ("INPUT", f"-p tcp -s 127.0.0.1 --sport {port} -j DROP")):
        result = runner(["sudo", "iptables", "-w", flag, chain, *spec.split()], capture_output=True, text=True, timeout=15, check=False)
        if apply and result.returncode != 0:
            raise RuntimeError(f"failed to apply {chain} {spec}")
    return {"mechanism": "host-loopback", "node": node, "port": port}


def _running_process_port(entry: dict) -> int | None:
    pid_file = entry.get("pid_file")
    if not pid_file:
        return None
    try:
        pid = Path(pid_file).read_text(encoding="utf-8").strip()
        if not pid:
            return None
        result = subprocess.run(
            ["ps", "-p", pid, "-o", "args="],
            check=False,
            text=True,
            capture_output=True,
        )
    except Exception:  # noqa: BLE001
        return None
    if result.returncode != 0:
        return None
    match = re.search(r"(?:^|\s)--port\s+(\d+)(?:\s|$)", result.stdout)
    if not match:
        return None
    return int(match.group(1))


def _runtime_metadata_port(config: RuntimeConfig, node: str):
    entry = _runtime_metadata_entry(config, node)
    if entry is None:
        return None
    live_port = _running_process_port(entry)
    if live_port is not None:
        return live_port
    if "port" in entry:
        return int(entry["port"])
    return None


def target_port(config: RuntimeConfig, node: str) -> int:
    runtime_port = _runtime_metadata_port(config, node)
    if runtime_port is not None:
        return runtime_port
    return int((config.env_root / "node-data" / node / "port").read_text(encoding="utf-8").strip())


def wait_for_all_tips(config: RuntimeConfig, nodes, *, min_slot=None, timeout_seconds=120):
    tips, details = wait_for_all_tips_with_details(
        config,
        nodes,
        min_slot=min_slot,
        timeout_seconds=timeout_seconds,
    )
    if details.get("timed_out"):
        raise RuntimeError(f"timed out waiting for all tips: {details.get('last_error')}")
    return tips


def wait_for_all_tips_with_details(config: RuntimeConfig, nodes, *, min_slot=None, timeout_seconds=120):
    started_at = time.monotonic()
    deadline = time.time() + timeout_seconds
    last_error = None
    attempts = 0
    while time.time() < deadline:
        attempts += 1
        try:
            tips = {node: query_tip(config, node) for node in nodes}
            if min_slot is not None and any(info["slot"] < min_slot for info in tips.values()):
                time.sleep(2)
                continue
            return tips, {
                "attempts": attempts,
                "duration_seconds": round(time.monotonic() - started_at, 6),
                "timed_out": False,
                "min_slot": min_slot,
            }
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(2)
    return None, {
        "attempts": attempts,
        "duration_seconds": round(time.monotonic() - started_at, 6),
        "timed_out": True,
        "last_error": str(last_error) if last_error else None,
        "min_slot": min_slot,
    }


def wait_for_node_slots(config: RuntimeConfig, required_slots: dict[str, int], *, timeout_seconds=120):
    tips, details = wait_for_node_slots_with_details(
        config,
        required_slots,
        timeout_seconds=timeout_seconds,
    )
    if details.get("timed_out"):
        raise RuntimeError(f"timed out waiting for node slots {required_slots}: {details.get('last_error')}")
    return tips


def wait_for_node_slots_with_details(config: RuntimeConfig, required_slots: dict[str, int], *, timeout_seconds=120):
    started_at = time.monotonic()
    deadline = time.time() + timeout_seconds
    last_error = None
    attempts = 0
    while time.time() < deadline:
        attempts += 1
        try:
            tips = {node: query_tip(config, node) for node in required_slots}
            if any(tips[node]["slot"] < min_slot for node, min_slot in required_slots.items()):
                time.sleep(2)
                continue
            return tips, {
                "attempts": attempts,
                "duration_seconds": round(time.monotonic() - started_at, 6),
                "timed_out": False,
                "required_slots": dict(required_slots),
            }
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(2)
    return None, {
        "attempts": attempts,
        "duration_seconds": round(time.monotonic() - started_at, 6),
        "timed_out": True,
        "last_error": str(last_error) if last_error else None,
        "required_slots": dict(required_slots),
    }


def extract_range_from_tail(tail_text: str) -> tuple[str, str] | None:
    tips = deque(maxlen=8)
    for line in tail_text.splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("ns") != "ChainDB.AddBlockEvent.AddedToCurrentChain":
            continue
        newtip = obj.get("data", {}).get("newtip")
        if not isinstance(newtip, str) or "@" not in newtip:
            continue
        hash_value, slot_value = newtip.split("@", 1)
        tips.append((int(slot_value), hash_value))
    if len(tips) < 8:
        return None
    from_slot, from_hash = tips[-8]
    to_slot, to_hash = tips[-7]
    return f"{from_slot}.{from_hash}", f"{to_slot}.{to_hash}"


def derive_range(config: RuntimeConfig) -> tuple[str, str]:
    for log_path in config.log_paths:
        try:
            tail = run(["tail", "-n", "20000", str(log_path)], timeout=15, check=True)
        except subprocess.CalledProcessError:
            continue
        result = extract_range_from_tail(tail.stdout)
        if result is not None:
            return result
    return config.fallback_range


def derive_chainsync_point(config: RuntimeConfig) -> str:
    for log_path in config.log_paths:
        try:
            tail = run(["tail", "-n", "20000", str(log_path)], timeout=15, check=True)
        except subprocess.CalledProcessError:
            continue
        tips = deque(maxlen=6)
        for line in tail.stdout.splitlines():
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("ns") != "ChainDB.AddBlockEvent.AddedToCurrentChain":
                continue
            newtip = obj.get("data", {}).get("newtip")
            if not isinstance(newtip, str) or "@" not in newtip:
                continue
            hash_value, slot_value = newtip.split("@", 1)
            tips.append((int(slot_value), hash_value))
        if len(tips) >= 6:
            slot_value, hash_value = tips[0]
            return f"{slot_value}.{hash_value}"
    from_point, _ = config.fallback_range
    return from_point


def point_slot(point: str) -> int | None:
    if not isinstance(point, str) or "." not in point:
        return None
    slot_text, _hash = point.split(".", 1)
    try:
        return int(slot_text)
    except ValueError:
        return None


def point_span(from_point: str, to_point: str) -> int | None:
    from_slot = point_slot(from_point)
    to_slot = point_slot(to_point)
    if from_slot is None or to_slot is None:
        return None
    return max(0, to_slot - from_slot)


def peer_endpoint(config: RuntimeConfig, peer_port: int) -> tuple[str, int]:
    """The address a client in the controller uses for a node published on ``peer_port``.

    Container nodes publish their N2N port on the host loopback only, which the controller's own
    network namespace cannot reach; the controller joins the run network, so it uses the node's
    container address and container port. Host-devnet nodes keep loopback.
    """
    metadata_path = config.runtime_root / "runtime.json"
    try:
        body = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        body = {}
    for entry in [*(body.get("haskell_nodes") or []), *(body.get("amaru_nodes") or [])]:
        if not isinstance(entry, dict):
            continue
        address = str(entry.get("container_listen_address") or "")
        try:
            published = int(entry.get("port"))
        except (TypeError, ValueError):
            continue
        if published == int(peer_port) and entry.get("container_ip") and ":" in address:
            return str(entry["container_ip"]), int(address.rsplit(":", 1)[1])
    return "127.0.0.1", int(peer_port)


def run_blockfetch(config: RuntimeConfig, peer_port: int, from_point: str, to_point: str, *, timeout_seconds=6,
                   host: str | None = None):
    endpoint_host, endpoint_port = (host, peer_port) if host else peer_endpoint(config, peer_port)
    return run(
        [
            "timeout",
            f"{timeout_seconds}s",
            runtime_tool("dwarf-amaru-runtime-blockfetch"),
            f"{endpoint_host}:{endpoint_port}",
            config.network_magic,
            from_point,
            to_point,
            "2",
        ],
        timeout=timeout_seconds + 5,
        check=False,
    )


def run_chainsync_fetch(config: RuntimeConfig, peer_port: int, point: str, output_dir: Path, *, timeout_seconds=6,
                        host: str | None = None, max_headers: int = 2):
    """Fetch headers after ``point`` with the pallas chain-sync client; writes ``header.*.cbor``.

    ``max_headers`` defaults to two per point, the count Amaru's fetch-chain-headers wrote.
    """
    endpoint_host, endpoint_port = (host, peer_port) if host else peer_endpoint(config, peer_port)
    return run(
        [
            "timeout",
            f"{timeout_seconds}s",
            runtime_tool("dwarf-amaru-runtime-chainsync"),
            f"{endpoint_host}:{endpoint_port}",
            config.network_magic,
            point,
            str(output_dir),
            str(int(max_headers)),
        ],
        timeout=timeout_seconds + 5,
    )


def replace_node_db(config: RuntimeConfig, target_node: str, source_db: Path) -> None:
    target_db = config.env_root / "node-data" / target_node / "db"
    shutil.rmtree(target_db)
    shutil.copytree(source_db, target_db)


# --- public-preview profiles: host-era and dashboard-installed container layouts -------------------

_LEGACY_PROFILES_DIRNAME = "cardano-profiles"
_UPSTREAM_ADDR_RE = re.compile(r"(\d+\.\d+\.\d+\.\d+)\]?:(\d+)")  # IPv4 or [::ffff:IPv4]
_NETEM_BAND = 4  # a fourth prio band the default priomap never selects: only filtered traffic is shaped


def _profiles_module():
    try:
        from profile_manager import profiles
    except ImportError:
        dwarf_root = str(Path(__file__).resolve().parent.parent)
        if dwarf_root not in sys.path:
            sys.path.append(dwarf_root)
        try:
            from profile_manager import profiles
        except ImportError:
            return None
    return profiles


def resolve_preview_runtime_root(raw) -> Path:
    """The runtime root a preview helper reads, for a host-era or a dashboard-installed profile.

    Scenarios name ``~/cardano-profiles/<id>`` or ``/opt/dwarf/cardano-profiles/<id>`` (host era).
    That path wins when it holds a ``runtime.json``; otherwise ``<id>`` resolves through
    ``profiles.runtime_root`` and then the controller's own retained runtime
    (``$ADA2_DWARF_STATE_DIR/controller/topology-runtimes/<id>/current``, the layout
    ``controller.docker_runtime`` deploys to). When none holds metadata the declared path is
    returned so the caller's error names it.
    """
    candidate = Path(os.path.expanduser(os.path.expandvars(str(raw))))
    if (candidate / "runtime.json").is_file():
        return candidate
    parts = candidate.parts
    if _LEGACY_PROFILES_DIRNAME in parts:
        index = parts.index(_LEGACY_PROFILES_DIRNAME)
        if index + 1 < len(parts):
            profile_id, rest = parts[index + 1], parts[index + 2:]
            installed = []
            profiles = _profiles_module()
            if profiles is not None:
                installed.append(Path(profiles.runtime_root(profile_id)).joinpath(*rest))
            state_dir = Path(os.environ.get("ADA2_DWARF_STATE_DIR") or "/var/dwarf/state")
            installed.append(state_dir.joinpath("controller", "topology-runtimes", profile_id, "current", *rest))
            for root in installed:
                if (root / "runtime.json").is_file():
                    return root
    return candidate


@dataclass(frozen=True)
class PreviewNodeView:
    """One preview node as the preview helpers measure it, whatever the runtime layout."""

    runtime_root: Path
    implementation: str
    layout: str  # "host" (host-era process) or "container" (dashboard-installed)
    listen_host: str
    listen_port: int
    chain_dir: Path
    log_path: Path
    pid_file: Path | None
    container_name: str | None
    upstream_peer_address: str | None
    local_peer_ips: tuple[str, ...] = ()

    @property
    def listen_address(self) -> str:
        return f"{self.listen_host}:{self.listen_port}"

    @property
    def process_name(self) -> str:
        return "amaru" if self.implementation == "amaru" else "cardano-node"

    def host_pid(self) -> int:
        if self.pid_file is None:
            raise RuntimeError(f"{self.implementation} preview node has no pid file")
        return int(self.pid_file.read_text(encoding="utf-8").strip())

    def is_running(self, runner=subprocess.run) -> bool:
        """Container profiles: the node container's ``State.Running``; host era: the pid is alive."""
        if self.layout == "container":
            result = runner(["docker", "inspect", "-f", "{{.State.Running}}", str(self.container_name)],
                            capture_output=True, text=True, timeout=15, check=False)
            return result.returncode == 0 and (result.stdout or "").strip() == "true"
        if self.pid_file is None or not self.pid_file.exists():
            return False
        try:
            os.kill(self.host_pid(), 0)
        except (OSError, ValueError):
            return False
        return True

    def describe(self) -> str:
        return f"container {self.container_name}" if self.layout == "container" else f"pid file {self.pid_file}"


def _topology_upstream(topology_path) -> str | None:
    try:
        body = json.loads(Path(str(topology_path)).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for peer in body.get("bootstrapPeers") or []:
        if isinstance(peer, dict) and peer.get("address") and peer.get("port"):
            return f"{peer['address']}:{int(peer['port'])}"
    return None


def _profile_upstream(profile_id: str) -> str | None:
    profiles = _profiles_module()
    if profiles is None or not profile_id:
        return None
    try:
        profile = profiles.find_profile(profile_id)
    except Exception:  # noqa: BLE001 - an unknown profile has no declared upstream
        return None
    return getattr(profile, "upstream_peer_address", None) or None


def preview_node_view(runtime_root, implementation: str) -> PreviewNodeView:
    """Read ``runtime.json`` under ``runtime_root`` into a :class:`PreviewNodeView`.

    Host era: top-level ``listen_address``/``chain_dir``/``log_path``/``pid_file``. Container
    profile: the first ``haskell_nodes``/``amaru_nodes`` entry of ``implementation``; the listen
    endpoint is the container address (``node_wire_endpoint``), the chain is ``chain_dir`` (Amaru)
    or ``db_dir`` (cardano-node), and the upstream peer comes from the metadata, the node's
    fallback peers, its topology ``bootstrapPeers`` or the profile definition, in that order.
    """
    runtime_root = Path(runtime_root)
    metadata_path = runtime_root / "runtime.json"
    if not metadata_path.is_file():
        raise RuntimeError(f"missing runtime metadata: {metadata_path}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("listen_address") and metadata.get("chain_dir"):
        host, port_text = str(metadata["listen_address"]).rsplit(":", 1)
        return PreviewNodeView(
            runtime_root=runtime_root, implementation=implementation, layout="host",
            listen_host=host, listen_port=int(port_text),
            chain_dir=Path(str(metadata["chain_dir"])), log_path=Path(str(metadata["log_path"])),
            pid_file=Path(str(metadata["pid_file"])), container_name=None,
            upstream_peer_address=metadata.get("upstream_peer_address"),
        )
    group = "amaru_nodes" if implementation == "amaru" else "haskell_nodes"
    impl_key = "amaru" if implementation == "amaru" else "cardano-node"
    candidates = [entry for entry in (metadata.get(group) or []) if isinstance(entry, dict)]
    if not candidates:
        candidates = [entry for entry in (metadata.get("nodes") or [])
                      if isinstance(entry, dict) and entry.get("impl") == impl_key]
    if not candidates:
        raise RuntimeError(f"runtime metadata {metadata_path} has no {implementation} node")
    node = candidates[0]
    if not node.get("container_name"):
        raise RuntimeError(f"runtime metadata {metadata_path} node {node.get('id')!r} has no container_name")
    host, port = node_wire_endpoint(node)
    chain_dir = node.get("chain_dir") if implementation == "amaru" else (node.get("db_dir") or node.get("chain_dir"))
    if not chain_dir or not node.get("log_path"):
        raise RuntimeError(f"runtime metadata {metadata_path} node {node.get('id')!r} lacks a chain dir or log path")
    upstream = (
        metadata.get("upstream_peer_address")
        or node.get("upstream_peer_address")
        or next(iter(node.get("fallback_peer_addresses") or []), None)
        or (_topology_upstream(node["topology_path"]) if node.get("topology_path") else None)
        or _profile_upstream(str(metadata.get("profile_id") or ""))
    )
    siblings: list[str] = []
    for entry in [*(metadata.get("nodes") or []), *(metadata.get("haskell_nodes") or []),
                  *(metadata.get("amaru_nodes") or [])]:
        if not isinstance(entry, dict) or entry.get("container_name") == node.get("container_name"):
            continue
        ip = str(entry.get("container_ip") or "")
        if ip and ip not in siblings:
            siblings.append(ip)
    pid_file = node.get("pid_file")
    return PreviewNodeView(
        runtime_root=runtime_root, implementation=implementation, layout="container",
        listen_host=host, listen_port=port, chain_dir=Path(str(chain_dir)), log_path=Path(str(node["log_path"])),
        pid_file=Path(str(pid_file)) if pid_file else None, container_name=str(node["container_name"]),
        upstream_peer_address=str(upstream) if upstream else None, local_peer_ips=tuple(siblings),
    )


def resolve_upstream_peer(address: str | None) -> tuple[str, int, list[str]]:
    """Host, port and resolved IPv4 addresses of an upstream ``host:port``."""
    if not address:
        raise RuntimeError("preview node has no upstream peer address")
    host, port_text = str(address).rsplit(":", 1)
    port = int(port_text)
    infos = socket.getaddrinfo(host, port, family=socket.AF_INET, type=socket.SOCK_STREAM)
    ips = sorted({info[4][0] for info in infos})
    if not ips:
        raise RuntimeError(f"no IPv4 upstream addresses resolved for {address}")
    return host, port, ips


def _established_peer_ips(ss_output: str, port: int, *, line_filter=None) -> list[str]:
    hits = []
    for line in ss_output.splitlines():
        if line_filter is not None and not line_filter(line):
            continue
        addrs = _UPSTREAM_ADDR_RE.findall(line)
        if len(addrs) < 2:
            continue
        for ip, port_text in addrs[1:]:
            if int(port_text) == port:
                hits.append(ip)
    return sorted(set(hits))


def preview_active_upstream_ips(view: PreviewNodeView, port: int, *, runner=subprocess.run) -> list[str]:
    """Remote IPs the node holds established connections to on ``port``.

    Container profiles read the sockets of the node's own network namespace (sibling topology
    nodes excluded); the host era keeps the ``ss -tnp`` process match.
    """
    if view.layout == "container":
        result = run_in_node_netns(str(view.container_name), "ss -Htn state established", runner=runner)
        excluded = set(view.local_peer_ips)
        return [ip for ip in _established_peer_ips(result.stdout or "", port) if ip not in excluded]
    try:
        result = runner(["ss", "-tnp", "state", "established"], text=True, capture_output=True, check=False)
    except FileNotFoundError:
        return []
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "").strip() or "ss failed")
    pid = view.host_pid()
    return _established_peer_ips(
        result.stdout or "", port,
        line_filter=lambda line: f":{port}" in line and (f"pid={pid}," in line or f'("{view.process_name}"' in line),
    )


def _iptables_rule(ip: str, port: int, target: list[str]) -> str:
    return " ".join(["-p", "tcp", "-d", ip, "--dport", str(int(port)), "-j", *target])


def preview_apply_iptables(view: PreviewNodeView, ips: list[str], port: int, target: list[str], *,
                           runner=subprocess.run) -> dict:
    """Install ``OUTPUT`` rules toward the upstream ``ips`` in the node container's namespace."""
    rules = [_iptables_rule(ip, port, target) for ip in ips]
    script = "set -e; " + "; ".join(f"iptables -w -I OUTPUT 1 {rule}" for rule in rules)
    try:
        run_in_node_netns(str(view.container_name), script, runner=runner)
    except RuntimeError:
        try:  # never leave a partial apply behind; the apply error is the one reported
            preview_remove_iptables(view, ips, port, target, runner=runner)
        except RuntimeError:
            pass
        raise
    return {"mechanism": "container-netns", "container": view.container_name, "rules": rules}


def preview_remove_iptables(view: PreviewNodeView, ips: list[str], port: int, target: list[str], *,
                            runner=subprocess.run) -> dict:
    """Remove the rules installed by :func:`preview_apply_iptables`; fails if any remains."""
    rules = [_iptables_rule(ip, port, target) for ip in ips]
    script = "; ".join(f"while iptables -w -D OUTPUT {rule} 2>/dev/null; do :; done" for rule in rules)
    result = run_in_node_netns(str(view.container_name), script + "; iptables -w -S OUTPUT", runner=runner)
    for line in (result.stdout or "").splitlines():
        for ip in ips:
            if f"-d {ip}/32" in line and f"--dport {int(port)}" in line and f"-j {target[0]}" in line:
                raise RuntimeError(f"upstream fault rule still present in {view.container_name}: {line.strip()}")
    return {"mechanism": "container-netns", "container": view.container_name, "rules": rules}


def preview_route_interface(view: PreviewNodeView, ips: list[str], *, runner=subprocess.run) -> str:
    script = "; ".join(f"ip -o route get {shlex.quote(ip)}" for ip in ips)
    result = run_in_node_netns(str(view.container_name), script, runner=runner)
    devices = set()
    for line in (result.stdout or "").splitlines():
        parts = line.split()
        if "dev" in parts:
            devices.add(parts[parts.index("dev") + 1])
    if len(devices) != 1:
        raise RuntimeError(f"upstream fault needs one route interface in {view.container_name}, got {sorted(devices)!r}")
    return devices.pop()


def preview_apply_netem(view: PreviewNodeView, ips: list[str], port: int, netem_args: list[str], *,
                        interface: str | None = None, runner=subprocess.run) -> dict:
    """Shape egress toward the upstream ``ips``:``port`` with netem in the node container's namespace.

    A root ``prio`` qdisc with a fourth band (never chosen by the default priomap) carries netem;
    u32 filters send only the upstream flows there. ``tc qdisc add`` refuses to replace an
    existing non-default root qdisc, so another active fault is never overwritten.
    """
    interface = interface or preview_route_interface(view, ips, runner=runner)
    dev = shlex.quote(interface)
    band = f"1:{_NETEM_BAND}"
    commands = [
        f"tc qdisc add dev {dev} root handle 1: prio bands {_NETEM_BAND} priomap 1 2 2 2 1 2 0 0 1 1 1 1 1 1 1 1",
        f"tc qdisc add dev {dev} parent {band} handle {_NETEM_BAND}0: netem {' '.join(netem_args)}",
    ]
    for ip in ips:
        commands.append(
            f"tc filter add dev {dev} protocol ip parent 1:0 prio {_NETEM_BAND} u32 "
            f"match ip dst {ip}/32 match ip protocol 6 0xff match ip dport {int(port)} 0xffff flowid {band}"
        )
    try:
        run_in_node_netns(str(view.container_name), "set -e; " + "; ".join(commands), runner=runner)
    except RuntimeError:
        # never leave a partial apply (qdisc added, a filter refused) behind
        run_in_node_netns(str(view.container_name), f"tc qdisc del dev {dev} root 2>/dev/null || true", runner=runner)
        raise
    return {"mechanism": "container-netns", "container": view.container_name, "interface": interface,
            "upstream_ips": list(ips), "upstream_port": int(port), "netem": list(netem_args)}


def preview_remove_netem(view: PreviewNodeView, state: dict, *, runner=subprocess.run) -> None:
    """Remove the root qdisc installed by :func:`preview_apply_netem`; fails if it remains."""
    dev = shlex.quote(str(state["interface"]))
    result = run_in_node_netns(
        str(view.container_name), f"tc qdisc del dev {dev} root 2>/dev/null || true; tc qdisc show dev {dev}",
        runner=runner,
    )
    remaining = result.stdout or ""
    if "netem" in remaining or "prio 1:" in remaining:
        raise RuntimeError(f"upstream fault qdisc still present in {view.container_name}: {remaining.strip()[:200]}")


# --------------------------------------------------------------------------- #
# Amaru trace forms
# --------------------------------------------------------------------------- #
# Amaru logs either tracing-subscriber text ("<ts> LEVEL target: message k=v ...")
# or, with AMARU_WITH_JSON_TRACES=true, one JSON object per record. Primitives that
# match the text form read JSON records through this adapter, so one parser serves
# both. Field order follows the observed text records for every field a primitive
# matches positionally (JSON objects arrive key-sorted); other fields follow.
_AMARU_TEXT_FIELD_ORDER = {
    "tip.adopt": ("slot", "header_hash", "block_height", "max_block_height", "suppressed"),
    "pots.dump": ("treasury", "reserves", "fees", "donations"),
    "stake_distribution.snapshot": (
        "accounts", "dreps", "pools", "active_stake",
        "pools_voting_stake", "dreps_voting_stake", "epoch",
    ),
    "chainsync.intersect_found": ("peer", "conn_id", "current", "highest"),
    "manager.peer.handshake_completed": ("peer", "conn_id"),
    "chain.select_from_block_validation": ("time.busy", "time.idle", "point", "valid", "header_hash"),
}
_AMARU_SPAN_EVENTS = frozenset({"new", "enter", "exit", "close"})
_AMARU_UNQUOTED_FIELDS = frozenset({"time.busy", "time.idle"})
_HEX64 = re.compile(r"[0-9a-fA-F]{64}")


def _amaru_text_value(name: str, value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value) if isinstance(value, float) else str(value)
    if isinstance(value, list):
        if (
            len(value) == 3
            and isinstance(value[0], int) and not isinstance(value[0], bool)
            and isinstance(value[1], str) and _HEX64.fullmatch(value[1])
            and isinstance(value[2], int) and not isinstance(value[2], bool)
        ):
            # A chain point, as Amaru's Debug renders it in the text form.
            return f"\"[{value[0]}, h'{value[1]}', {value[2]}]\""
        return "[" + ", ".join(str(item) for item in value) + "]"
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True)
    text = str(value)
    if name in _AMARU_UNQUOTED_FIELDS:
        return text
    return '"' + text.replace('"', '\\"') + '"'


def amaru_trace_line_as_text(line: str) -> str:
    """One Amaru JSON trace record in its text form; any other line unchanged."""
    stripped = line.strip()
    if not stripped.startswith("{"):
        return line
    try:
        record = json.loads(stripped)
    except ValueError:
        return line
    if not isinstance(record, dict):
        return line
    fields = record.get("fields")
    target = record.get("target")
    if not isinstance(fields, dict) or not isinstance(target, str) or "message" not in fields:
        return line
    message = str(fields.get("message"))
    span = record.get("span") if isinstance(record.get("span"), dict) else {}
    head = message
    order_key = message
    if message in _AMARU_SPAN_EVENTS and span.get("name"):
        head = f"{span['name']} {message}"
        order_key = str(span["name"])
    remaining = {key: value for key, value in fields.items() if key != "message"}
    ordered = [key for key in _AMARU_TEXT_FIELD_ORDER.get(order_key, ()) if key in remaining]
    ordered += [key for key in remaining if key not in ordered]
    rendered = " ".join(f"{key}={_amaru_text_value(key, remaining[key])}" for key in ordered)
    level = str(record.get("level") or "INFO").upper()
    timestamp = str(record.get("timestamp") or "")
    prefix = f"{timestamp} {level:>5} {target}: "
    return (prefix + head + (" " + rendered if rendered else "")).lstrip()


def amaru_trace_as_text(text: str) -> str:
    """A whole Amaru log in the text form (text lines and foreign lines unchanged)."""
    if "{" not in (text or ""):
        return text
    return "\n".join(amaru_trace_line_as_text(line) for line in text.split("\n"))
