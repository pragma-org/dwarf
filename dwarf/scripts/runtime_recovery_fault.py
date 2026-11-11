from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


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


_KILL_SIGNALS = ("TERM", "KILL")


def _real_kill_node(node: dict, signal_name: str = "TERM") -> dict:
    """Stop a node with SIGTERM (clean) or SIGKILL (unclean: the node cannot flush its ChainDB)."""
    if signal_name not in _KILL_SIGNALS:
        raise ValueError(f"unsupported kill signal {signal_name!r}; use one of {_KILL_SIGNALS}")
    sig = getattr(signal, "SIG" + signal_name)
    name = _node_container(node)
    if name and _docker_available() and _container_exists(name):
        before = _container_running(name)
        _docker("kill", "-s", signal_name, name, timeout=30)
        down = _wait(lambda: not _container_running(name), attempts=20, delay=1.0)
        return {
            "mechanism": "docker",
            "handle": name,
            "signal": "SIG" + signal_name,
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
            os.kill(pid, sig)
        except OSError as exc:  # noqa: BLE001
            return {"mechanism": "process", "pid": pid, "error": f"{type(exc).__name__}: {exc}", "stopped": False}
        down = _wait(lambda: not _pid_alive(pid), attempts=20, delay=1.0)
        return {"mechanism": "process", "pid": pid, "signal": "SIG" + signal_name, "verified_down": down, "stopped": bool(down)}
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


# ---------------------------------------------------------------------------
# F144: a real fork by partition (force_rollback / chain_switch_inject).
# The target node is cut off its docker network; it keeps forging its own branch while the
# majority forges a longer one. On heal the target must roll back its branch (within k) and
# switch to the majority chain. Everything reported is read from the nodes: tips (LocalStateQuery
# / Amaru log), the target's own ChainDB trace, chain membership over ChainSync FindIntersect, and
# the UTxO the nodes serve after the rollback.
# ---------------------------------------------------------------------------


def _all_nodes(metadata: dict) -> list[dict]:
    seen, out = set(), []
    for node in list(metadata.get("haskell_nodes") or []) + list(metadata.get("amaru_nodes") or []) + list(
            metadata.get("nodes") or []):
        node_id = str(node.get("id") or node.get("name") or "")
        if node_id and node_id not in seen:
            seen.add(node_id)
            out.append(node)
    return out


def _impl(node: dict) -> str:
    return str(node.get("impl") or node.get("implementation") or "cardano-node")


def genesis_security_parameter(node: dict) -> int | None:
    """securityParam from the Shelley genesis the node was started with (never a release default)."""
    config_path = str(node.get("config_path") or "")
    if not config_path or not Path(config_path).is_file():
        return None
    text = Path(config_path).read_text(encoding="utf-8")
    match = re.search(r'"?ShelleyGenesisFile"?\s*:\s*"?([^"\n,}]+)"?', text)
    if not match:
        return None
    genesis = Path(match.group(1).strip())
    if not genesis.is_absolute():
        genesis = Path(config_path).parent / genesis
    try:
        return int(json.loads(genesis.read_text(encoding="utf-8"))["securityParam"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def count_fork_switches(lines) -> int:
    return sum(1 for line in (lines or []) if "SwitchedToAFork" in str(line))


def find_intersect(host: str, port: int, *, network_magic: int, slot: int, block_hash: str,
                   timeout: float = 10.0) -> bool | None:
    """Is [slot, hash] on the node's current chain? ChainSync MsgFindIntersect over node-to-node.

    True for MsgIntersectFound, False for MsgIntersectNotFound, None when the node could not be asked."""
    import cbor2  # noqa: PLC0415 - controller image dependency
    from runtime_txsubmission_probe import (  # noqa: PLC0415
        _classify_handshake_reply, _encode_mux_sdu, _handshake_propose_payload, _recv_exact)

    try:
        with socket.create_connection((host, int(port)), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(_encode_mux_sdu(_handshake_propose_payload(int(network_magic)), mini_protocol_num=0))
            deadline = time.time() + timeout
            accepted = False
            sent = False
            buffers: dict[int, bytearray] = {}
            while time.time() < deadline:
                header = _recv_exact(sock, 8)
                word = int.from_bytes(header[4:6], "big")
                body = _recv_exact(sock, int.from_bytes(header[6:8], "big"))
                protocol, from_responder = word & 0x7FFF, bool(word & 0x8000)
                if protocol == 0 and not accepted:
                    if _classify_handshake_reply(body) != "accepted":
                        return None
                    accepted = True
                if accepted and not sent:
                    request = cbor2.dumps([4, [[int(slot), bytes.fromhex(block_hash)]]])
                    sock.sendall(_encode_mux_sdu(request, mini_protocol_num=2))
                    sent = True
                    continue
                if protocol == 2 and from_responder:
                    buffer = buffers.setdefault(protocol, bytearray())
                    buffer.extend(body)
                    try:
                        reply = cbor2.loads(bytes(buffer))
                    except Exception:  # noqa: BLE001 - wait for the rest of the message
                        continue
                    return {5: True, 6: False}.get(int(reply[0]))
    except (OSError, EOFError, ValueError):
        return None
    return None


class DockerSubstrateOps:
    """The real substrate operations the partition fork uses (controller-run docker devnet)."""

    def __init__(self, *, network_magic: int | None) -> None:
        self.network_magic = network_magic

    @staticmethod
    def now() -> float:
        return time.time()

    @staticmethod
    def sleep(seconds: float) -> None:
        time.sleep(seconds)

    def tip(self, node: dict) -> dict:
        if _impl(node) == "amaru":
            from runtime_multi_node_observation import _extract_latest_amaru_tip  # noqa: PLC0415

            lines = self.log_lines_since(node, time.time() - 3600)
            tip = _extract_latest_amaru_tip("\n".join(lines or [])) if lines is not None else None
            return {"ok": tip is not None, **(tip or {"hash": None, "slot": None, "block": None})}
        from runtime_multi_node_observation import _query_tip_once  # noqa: PLC0415

        return _query_tip_once(cardano_cli="cardano-cli", socket_path=Path(str(node.get("socket_path") or "")),
                               network_magic=int(self.network_magic), container_name=_node_container(node),
                               container_socket_path=str(node.get("container_socket_path") or "") or None)

    def disconnect(self, node: dict) -> dict:
        name = _node_container(node)
        if not name:
            return {"ok": False, "error": "no container_name for the target node"}
        inspect = _docker("inspect", "-f", "{{json .NetworkSettings.Networks}}", name, timeout=15)
        try:
            networks = json.loads(inspect.stdout or "{}") or {}
        except ValueError:
            networks = {}
        network = str(node.get("container_network") or next(iter(networks), "") or "")
        record = networks.get(network) or {}
        aliases = [a for a in (record.get("Aliases") or []) if a and not str(name).startswith(str(a))]
        ip = str(record.get("IPAddress") or "") or None
        if not network:
            return {"ok": False, "error": "target container is on no docker network"}
        proc = _docker("network", "disconnect", network, name, timeout=30)
        return {"ok": proc.returncode == 0, "network": network, "aliases": aliases, "ip": ip,
                "error": (proc.stderr or "").strip()[-300:] or None}

    def connect(self, node: dict, partition: dict) -> dict:
        name = _node_container(node)
        args = ["network", "connect"]
        for alias in partition.get("aliases") or []:
            args += ["--alias", alias]
        attempts = []
        for with_ip in ([True, False] if partition.get("ip") else [False]):
            command = args + (["--ip", partition["ip"]] if with_ip else []) + [partition["network"], name]
            proc = _docker(*command, timeout=30)
            attempts.append({"ip_pinned": with_ip, "rc": proc.returncode, "stderr": (proc.stderr or "").strip()[-300:]})
            if proc.returncode == 0:
                return {"ok": True, "attempts": attempts}
        return {"ok": False, "attempts": attempts}

    @staticmethod
    def log_lines_since(node: dict, since: float) -> list[str] | None:
        name = _node_container(node)
        if not name:
            return None
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(max(0.0, since - 2)))
        proc = _docker("logs", "--since", stamp, name, timeout=60)
        if proc.returncode != 0:
            return None
        return (str(proc.stdout or "") + "\n" + str(proc.stderr or "")).splitlines()

    def on_chain(self, node: dict, tip: dict) -> bool | None:
        from runtime_common import node_wire_endpoint  # noqa: PLC0415

        if not tip or not tip.get("hash") or tip.get("slot") is None or self.network_magic is None:
            return None
        try:
            host, port = node_wire_endpoint(node)
        except (KeyError, ValueError, TypeError):
            return None
        return find_intersect(host, port, network_magic=int(self.network_magic), slot=int(tip["slot"]),
                              block_hash=str(tip["hash"]))

    def ledger_digest(self, node: dict) -> dict:
        """sha256 of the whole UTxO the node serves over LocalStateQuery (cardano-node only)."""
        if _impl(node) != "cardano-node":
            return {"ok": False, "error": f"{_impl(node)}: no LocalStateQuery UTxO query"}
        name = _node_container(node)
        socket_path = str(node.get("container_socket_path") or "")
        base = ["--whole-utxo", "--testnet-magic", str(self.network_magic), "--socket-path", socket_path]
        for command in (["exec", name, "cardano-cli", "conway", "query", "utxo", *base, "--output-json"],
                        ["exec", name, "cardano-cli", "query", "utxo", *base, "--out-file", "/dev/stdout"]):
            proc = _docker(*command, timeout=60)
            if proc.returncode == 0:
                try:
                    body = json.loads(proc.stdout)
                except ValueError:
                    continue
                canonical = json.dumps(body, sort_keys=True).encode("utf-8")
                return {"ok": True, "digest": hashlib.sha256(canonical).hexdigest(), "utxo_count": len(body)}
        return {"ok": False, "error": (proc.stderr or proc.stdout or "").strip()[-300:]}


def _tips(nodes: list[dict], ops) -> dict[str, dict]:
    return {str(n.get("id") or n.get("name")): ops.tip(n) for n in nodes}


def _converged(tips: dict[str, dict]) -> bool:
    hashes = {t.get("hash") for t in tips.values() if t.get("ok", True)}
    return len(tips) > 0 and all(t.get("hash") for t in tips.values()) and len(hashes) == 1


def _wait_converged(nodes: list[dict], ops, *, timeout: float, poll: float) -> tuple[bool, dict, float]:
    started = ops.now()
    tips = _tips(nodes, ops)
    while not _converged(tips) and ops.now() - started < timeout:
        ops.sleep(poll)
        tips = _tips(nodes, ops)
    return _converged(tips), tips, ops.now() - started


def _short(tip: dict | None) -> dict | None:
    if not tip:
        return None
    return {"hash": tip.get("hash"), "slot": tip.get("slot"), "block": tip.get("block")}


def _ledger_consistency(target: dict, reference: dict, ops, *, attempts: int = 5, poll: float = 1.0) -> dict:
    """Same tip on both nodes before and after reading their UTxO -> the UTxO must be identical."""
    for _ in range(attempts):
        before = (ops.tip(target), ops.tip(reference))
        if before[0].get("hash") and before[0].get("hash") == before[1].get("hash"):
            mine, theirs = ops.ledger_digest(target), ops.ledger_digest(reference)
            after = (ops.tip(target), ops.tip(reference))
            if not (mine.get("ok") and theirs.get("ok")):
                return {"consistent": None, "error": mine.get("error") or theirs.get("error")}
            if after[0].get("hash") == after[1].get("hash") == before[0].get("hash"):
                return {"consistent": mine["digest"] == theirs["digest"], "at_tip": _short(before[0]),
                        "target_digest": mine["digest"], "reference_digest": theirs["digest"],
                        "reference_node": str(reference.get("id")), "utxo_count": mine.get("utxo_count")}
        ops.sleep(poll)
    return {"consistent": None, "error": "the two nodes never held the same tip across the UTxO reads"}


def partition_fork_and_heal(*, nodes: list[dict], target_id: str, config: dict, ops,
                            security_parameter_k: int | None, mode: str) -> dict:
    target = next(n for n in nodes if str(n.get("id") or n.get("name")) == str(target_id))
    others = [n for n in nodes if n is not target]
    poll = float(config.get("poll_seconds", 1.0))
    converge_timeout = float(config.get("converge_timeout_seconds", 45))
    partition_max = float(config.get("partition_max_seconds", 75))
    # Stop growing the target's branch before a rollback deeper than k would be needed.
    depth_cap = max(1, int(security_parameter_k) - 1) if security_parameter_k else int(config.get("max_fork_blocks", 5))
    # The majority keeps forging while the peers reconnect after the heal (5.8 s and 4 blocks in run
    # 20261110T101845Z). If its branch passes k before the reconnect, the fork can never resolve, so
    # heal while the majority still leaves reconnect_margin_blocks of room below k.
    margin = max(1, int(config.get("reconnect_margin_blocks", 2)))
    majority_cap = max(2, int(security_parameter_k) - margin) if security_parameter_k else depth_cap + 1
    out = {"target_node": str(target_id), "security_parameter_k": security_parameter_k,
           "security_parameter_k_source": "shelley-genesis securityParam" if security_parameter_k else None,
           "fork_formed": False, "converged": False, "rollback_status": "not_started"}

    pre_ok, pre_tips, _ = _wait_converged(nodes, ops, timeout=converge_timeout, poll=poll)
    out["pre_partition_converged"] = pre_ok
    out["pre_partition_tips"] = {k: _short(v) for k, v in pre_tips.items()}
    # The leader schedule is random: in a short window the partitioned node can outrun the majority,
    # hit the depth cap and win on heal. That is no fork in the measured direction, so heal, let the
    # nodes reconverge and partition again, up to a bounded number of attempts (each one recorded).
    attempts_max = max(1, int(config.get("partition_attempts", 3)))
    out["partition_attempts"] = []
    own = majority = 0
    for attempt in range(1, attempts_max + 1):
        partition = ops.disconnect(target)
        out["partition"] = {k: v for k, v in partition.items() if k != "ok"}
        if not partition.get("ok"):
            out["rollback_status"] = "partition_failed"
            out["partition_attempts"].append({"attempt": attempt, "outcome": "partition_failed"})
            return out
        fork_point = ops.tip(target)
        out["fork_point"] = _short(fork_point)
        started = ops.now()
        own = majority = 0
        target_tip, majority_tip = fork_point, fork_point
        outcome = "no_fork_in_window"
        try:
            while ops.now() - started < partition_max:
                ops.sleep(poll)
                target_tip = ops.tip(target)
                majority_tips = [t for t in (ops.tip(n) for n in others) if t.get("block") is not None]
                majority_tip = max(majority_tips, key=lambda t: int(t["block"]), default=majority_tip)
                if target_tip.get("block") is None or fork_point.get("block") is None:
                    continue
                on_majority = any(t.get("hash") == target_tip.get("hash") for t in majority_tips)
                own = 0 if on_majority else int(target_tip["block"]) - int(fork_point["block"])
                majority = int(majority_tip["block"]) - int(fork_point["block"])
                if own >= 1 and majority > own:
                    out["fork_formed"] = True
                    outcome = "fork_formed"
                    break
                if own >= depth_cap:
                    outcome = "isolated_branch_reached_depth_cap"
                    break
                if majority >= majority_cap:
                    outcome = "majority_reached_depth_cap"
                    break
        finally:
            out["partition_seconds"] = round(ops.now() - started, 3)
            heal_at = ops.now()
            out["heal"] = ops.connect(target, partition)
        out["partition_attempts"].append({
            "attempt": attempt, "outcome": outcome, "partition_seconds": out["partition_seconds"],
            "isolated_fork_blocks": own, "majority_blocks_since_fork": majority})
        if out["fork_formed"] or attempt == attempts_max:
            break
        settled, _tips, _ = _wait_converged(nodes, ops, timeout=converge_timeout, poll=poll)
        if not settled:
            break
    out["isolated_fork_tip"] = _short(target_tip)
    out["isolated_fork_blocks"] = own
    out["majority_tip_at_heal"] = _short(majority_tip)
    out["majority_blocks_since_fork"] = majority

    ok, tips, seconds = _wait_converged(nodes, ops, timeout=converge_timeout, poll=poll)
    out["converged"], out["heal_to_convergence_seconds"] = ok, round(seconds, 3)
    out["post_heal_tips"] = {k: _short(v) for k, v in tips.items()}
    fork_block = (out.get("fork_point") or {}).get("block")
    other_ids = {str(n.get("id") or n.get("name")) for n in others}
    majority_after = [int(t["block"]) for node_id, t in tips.items()
                      if node_id in other_ids and isinstance(t, dict) and t.get("block") is not None]
    out["post_heal_majority_blocks_since_fork"] = (
        max(majority_after) - int(fork_block) if (majority_after and fork_block is not None) else None)

    target_lines = ops.log_lines_since(target, heal_at)
    out["switch_logged_by_target"] = (count_fork_switches(target_lines) >= 1) if target_lines is not None else None
    abandoned_on_chain = ops.on_chain(target, target_tip) if out["fork_formed"] else None
    out["abandoned_fork_tip_on_target_chain"] = abandoned_on_chain
    out["observed_rollback_depth_blocks"] = own if out["fork_formed"] else 0
    rolled_back = abandoned_on_chain is False or (abandoned_on_chain is None and out["switch_logged_by_target"])
    if not out["fork_formed"]:
        out["rollback_status"] = "no_fork_formed"
    elif not ok and security_parameter_k and (out["post_heal_majority_blocks_since_fork"] or 0) > int(security_parameter_k):
        # The majority passed k before the nodes reconnected: by design no node may switch that far.
        out["rollback_status"] = "fork_deeper_than_k_after_heal"
    elif not ok:
        out["rollback_status"] = "not_converged"
    elif security_parameter_k and own > int(security_parameter_k):
        out["rollback_status"] = "exceeded_k"
    elif rolled_back:
        out["rollback_status"] = "recovered"
    else:
        out["rollback_status"] = "unconfirmed"

    ledger = _ledger_consistency(target, others[0], ops, poll=poll) if (ok and others) else {
        "consistent": None, "error": "not converged"}
    out["ledger_consistency"] = ledger
    out["ledger_state_consistent_post_rollback"] = ledger.get("consistent")

    if mode == "chain_switch_inject":
        target_point = majority_tip if out["fork_formed"] else None
        out["target_tip_hash"] = (target_point or {}).get("hash") or ""
        out["target_tip_slot"] = (target_point or {}).get("slot")
        on_target = []
        for node in nodes:
            node_id = str(node.get("id") or node.get("name"))
            if not (ok and target_point):
                break
            member = ops.on_chain(node, target_point)
            if member is None:
                # No wire answer: a majority node that never switched forks extended the target;
                # the partitioned node reached it only by switching.
                lines = target_lines if node is target else ops.log_lines_since(node, heal_at)
                switched = count_fork_switches(lines) >= 1 if lines is not None else None
                member = bool(switched) if node is target else (switched is False)
            if member:
                on_target.append(node_id)
        out["nodes_on_target_chain"] = on_target
        if out["rollback_status"] == "recovered" and len(on_target) == len(nodes):
            out["chain_switch_status"] = "switched"
        else:
            out["chain_switch_status"] = out["rollback_status"] if out["rollback_status"] != "recovered" else "partial"
    return out


def _partition_fork_mode(*, metadata: dict, mode: str, config: dict, live: bool, ops=None) -> dict:
    """F144: drive and measure a real fork (see partition_fork_and_heal); never a constant."""
    base = {"requested_rollback_slots": int(config.get("requested_rollback_slots", 5)),
            "scenario_security_parameter_k": config.get("security_parameter_k")}
    if not live:
        return {**base, "status": "not_measured", "measured": False, "rollback_status": "",
                "reason": "no runtime metadata: library mode performs no fork"}
    nodes = _all_nodes(metadata)
    target_id = str(config.get("target_node") or "")
    target = next((n for n in nodes if str(n.get("id") or n.get("name")) == target_id), None)
    if target is None:
        return {**base, "status": "error", "measured": False, "rollback_status": "",
                "error": f"node {target_id!r} not found in runtime metadata"}
    k = genesis_security_parameter(target)
    ops = ops or DockerSubstrateOps(network_magic=metadata.get("network_magic"))
    measured = partition_fork_and_heal(nodes=nodes, target_id=target_id, config=config, ops=ops,
                                       security_parameter_k=k, mode=mode)
    return {**base, "status": "measured", "measured": True, **measured}


def apply_recovery_mode(*, metadata: dict, mode: str, config: dict, live: bool = True) -> dict:
    node_ids = _node_ids(metadata)
    overrides = dict(metadata.get("observation_overrides") or {})

    if mode in {"force_rollback", "chain_switch_inject"}:
        result = _partition_fork_mode(metadata=metadata, mode=mode, config=config, live=live)
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
                result = {"target_node": target_id, **_real_kill_node(node, str(config.get("signal") or "TERM").upper())}
            # record only the real observed up/down, do not fabricate tips
            overrides.setdefault("node_lifecycle", {})[target_id] = {"action": "kill", **{k: result.get(k) for k in ("stopped", "verified_down", "mechanism", "signal")}}
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
