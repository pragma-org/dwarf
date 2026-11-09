#!/usr/bin/env python3

import hashlib
import json
import os
import re
import shutil
import subprocess
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


def stop_session_with_details(config: RuntimeConfig) -> dict:
    started_at = time.monotonic()
    containers = _profile_node_containers(config)
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
