from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path


def _load_metadata(runtime_metadata_path: Path) -> dict:
    return json.loads(runtime_metadata_path.read_text(encoding="utf-8"))


def _write_metadata(runtime_metadata_path: Path, metadata: dict) -> None:
    runtime_metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _role_map(metadata: dict) -> dict[str, str]:
    nodes = list(metadata.get("nodes") or metadata.get("haskell_nodes") or [])
    return {
        str(node.get("id") or node.get("name")): str(node.get("role", "honest"))
        for node in nodes
        if str(node.get("id") or node.get("name"))
    }



_ADVERSARY_RELAYS_DEFAULT = ["192.0.2.66", "192.0.2.67"]  # TEST-NET-1, unroutable adversary markers


def _free_port(preferred: int = 3399) -> int:
    for p in [preferred, 3410, 3411, 3412, 3413]:
        with socket.socket() as s:
            try:
                s.bind(("0.0.0.0", p))
                return p
            except OSError:
                continue
    return preferred


def _run_snapshot_substitution(config: dict) -> dict:
    """REAL driver (Phase-2 Mode 4): craft a SUBSTITUTED --peer-snapshot whose bigLedgerPools point at
    adversary relays, launch a disposable amaru with it, and observe whether amaru ADDS + DIALS those
    adversary peers from the snapshot source WITHOUT re-validating them against on-chain stake.
    Reports MEASURED outcome; never a fabricated pass. Needs config: amaru_binary, amaru_store (a
    pristine ledger+chain+era-history store to copy), network, network_magic, optional globals_env,
    point_hash/point_slot, adversary_relays, observe_seconds. Honest not_applicable when those are
    absent (library/non-live mode)."""
    amaru_bin = config.get("amaru_binary")
    store_src = config.get("amaru_store")
    network = str(config.get("network", "testnet_42"))
    magic = int(config.get("network_magic", 42))
    globals_env = config.get("globals_env")
    point_hash = str(config.get("point_hash", "181e9b48af913dd4823f9a44a7460c90b61f55e6f51f4a8050fe2c4f0c642d37"))
    point_slot = int(config.get("point_slot", 1000))
    adversary = list(config.get("adversary_relays") or _ADVERSARY_RELAYS_DEFAULT)
    observe_secs = int(config.get("observe_seconds", 45))
    if not (amaru_bin and store_src and os.path.exists(str(amaru_bin)) and os.path.exists(str(store_src))):
        return {
            "mode": "substitute_big_ledger_peers", "status": "not_applicable", "measured": False,
            "reason": "library/non-live mode: real driver needs config.amaru_binary + config.amaru_store "
                      "(a pristine ledger+chain+era-history store to copy). Not faking a pass.",
        }
    work = tempfile.mkdtemp(prefix="blp-subst-")
    snap = os.path.join(work, "substituted-peer-snapshot.json")
    with open(snap, "w") as f:
        json.dump({
            "NetworkMagic": magic, "NodeToClientVersion": 23,
            "Point": {"blockPointHash": point_hash, "blockPointSlot": point_slot},
            "bigLedgerPools": [{"accumulatedStake": 0.9, "relativeStake": 0.9,
                                "relays": [{"address": a, "port": 3001} for a in adversary]}],
        }, f)
    store = os.path.join(work, "store")
    shutil.copytree(str(store_src), store)
    log = os.path.join(work, "amaru.log")
    pidf = os.path.join(work, "amaru.pid")
    port = _free_port()
    env = dict(os.environ)
    if globals_env and os.path.exists(str(globals_env)):
        for line in open(str(globals_env)):
            line = line.strip()
            if line.startswith("export ") and "=" in line:
                k, v = line[len("export "):].split("=", 1)
                env[k.strip()] = v.strip()
    era = config.get("era_history") or os.path.join(store, "era-history.json")
    cmd = [str(amaru_bin), "node", "run", "--network", network, "--era-history", str(era),
           "--ledger-dir", os.path.join(store, f"ledger.{network}.db"),
           "--chain-dir", os.path.join(store, f"chain.{network}.db"),
           "--listen-address", f"0.0.0.0:{port}", "--peer-snapshot", snap,
           "--no-tui", "--with-json-traces", "--pid-file", pidf]
    proc = subprocess.Popen(cmd, stdout=open(log, "w"), stderr=subprocess.STDOUT, env=env)
    try:
        time.sleep(observe_secs)
    finally:
        for fn in (proc.terminate, proc.kill):
            try:
                fn(); proc.wait(timeout=8); break
            except Exception:
                continue
    txt = open(log, errors="replace").read() if os.path.exists(log) else ""
    loaded = ("peer_snapshot.loaded" in txt) and (snap in txt)
    added = [a for a in adversary if ("peer_selection.peer.added" in txt and f"{a}:3001" in txt)]
    dialed = [a for a in adversary if ("manager.peer.connect" in txt and f"{a}:3001" in txt)]
    revalidated = bool(re.search(r"revalidat|stake.*reject|reject.*snapshot|verify.*snapshot.*stake", txt, re.I))
    violation = bool(loaded and added and dialed and not revalidated)
    return {
        "mode": "substitute_big_ledger_peers", "status": "measured", "measured": True,
        "snapshot_loaded": loaded, "adversary_relays": adversary,
        "peers_added_from_snapshot": added, "dialed_substituted_peers": dialed,
        "revalidated_against_chain": revalidated, "listen_port": port, "log_path": log,
        "verdict": "TRUST_BOUNDARY_VIOLATION" if violation else "clean_or_inconclusive",
        "note": ("amaru added + dialed adversary big-ledger relays from the SUBSTITUTED --peer-snapshot "
                 "with no on-chain stake re-validation" if violation
                 else "no trust-boundary violation observed (amaru did not add/dial the substituted relays, "
                      "or re-validated)"),
    }


# --------------------------------------------------------------------------------------
# Shared live-mesh primitives (Phase-2 modes 1-2): build a REAL disposable amaru mesh of
# host processes on loopback ports, with ONE target amaru whose peer sources we flood with
# adversary relays, then MEASURE the target's peer_selection telemetry. No fabrication.
# --------------------------------------------------------------------------------------


def _apply_globals_env(env: dict, globals_env) -> None:
    """Fold an `export K=V` globals file (amaru GlobalParameters) into env."""
    if globals_env and os.path.exists(str(globals_env)):
        for line in open(str(globals_env)):
            line = line.strip()
            if line.startswith("export ") and "=" in line:
                k, v = line[len("export "):].split("=", 1)
                env[k.strip()] = v.strip()


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _alloc_ports(base: int, count: int) -> list[int]:
    ports: list[int] = []
    p = base
    while len(ports) < count and p < base + 500:
        if _port_free(p):
            ports.append(p)
        p += 1
    if len(ports) < count:
        raise RuntimeError(f"could not allocate {count} free loopback ports from {base}")
    return ports


def _wait_listening(port: int, timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.5)
    return False


def _prepare_store(store_src: str, era_src: str | None, dest: str) -> str:
    """Copy a pristine amaru store (+era-history) into a fresh disposable workdir."""
    shutil.copytree(store_src, dest)
    for lock in Path(dest).rglob("LOCK"):
        try:
            lock.unlink()
        except OSError:
            pass
    era_dest = os.path.join(dest, "era-history.json")
    if era_src and os.path.exists(str(era_src)) and not os.path.exists(era_dest):
        shutil.copy(str(era_src), era_dest)
    return dest


def _launch_amaru(
    *,
    amaru_bin: str,
    store_src: str,
    era_src: str | None,
    globals_env,
    workroot: str,
    node_id: str,
    role: str,
    port: int,
    network: str,
    peer_addresses: list[str] | None = None,
    peer_snapshot: str | None = None,
    peer_mix: str | None = None,
    upstream_peers: int | None = None,
    cooldown_secs: int | None = None,
    migrate: bool = True,
) -> dict:
    work = os.path.join(workroot, node_id)
    store = os.path.join(work, "store")
    os.makedirs(work, exist_ok=True)
    _prepare_store(store_src, era_src, store)
    era = os.path.join(store, "era-history.json")
    net_suffix = network
    log = os.path.join(work, "amaru.log")
    env = dict(os.environ)
    _apply_globals_env(env, globals_env)
    env["AMARU_ERA_HISTORY"] = era
    env.setdefault("AMARU_LOG", "error,amaru=info")
    cmd = [
        amaru_bin, "node", "run", "--network", network,
        "--era-history", era,
        "--ledger-dir", os.path.join(store, f"ledger.{net_suffix}.db"),
        "--chain-dir", os.path.join(store, f"chain.{net_suffix}.db"),
        "--listen-address", f"127.0.0.1:{port}",
        "--no-tui", "--with-json-traces",
    ]
    if migrate:
        cmd.append("--migrate-chain-db")
    for pa in (peer_addresses or []):
        cmd += ["--peer-address", pa]
    if peer_snapshot:
        cmd += ["--peer-snapshot", peer_snapshot]
    if peer_mix:
        cmd += ["--peer-mix", peer_mix]
    if upstream_peers is not None:
        cmd += ["--upstream-peers", str(upstream_peers)]
    if cooldown_secs is not None:
        cmd += ["--peer-removal-cooldown-secs", str(cooldown_secs)]
    proc = subprocess.Popen(cmd, stdout=open(log, "w"), stderr=subprocess.STDOUT, env=env)
    return {
        "node_id": node_id, "role": role, "port": port, "addr": f"127.0.0.1:{port}",
        "proc": proc, "log": log, "work": work, "cmd": cmd,
    }


def _build_adversary_snapshot(path: str, adversary_addrs: list[str], magic: int,
                              point_hash: str, point_slot: int) -> None:
    """Craft a SUBSTITUTED --peer-snapshot whose bigLedgerPools point at the adversary relays
    (one high-stake pool per adversary relay), so the target's SNAPSHOT source is flooded."""
    pools = []
    n = max(1, len(adversary_addrs))
    for a in adversary_addrs:
        host, _, port = a.partition(":")
        pools.append({
            "accumulatedStake": 0.9 / n, "relativeStake": 0.9 / n,
            "relays": [{"address": host, "port": int(port or 3001)}],
        })
    with open(path, "w") as f:
        json.dump({
            "NetworkMagic": magic, "NodeToClientVersion": 23,
            "Point": {"blockPointHash": point_hash, "blockPointSlot": point_slot},
            "bigLedgerPools": pools,
        }, f)


def _ts(line_obj: dict) -> float:
    raw = str(line_obj.get("timestamp", ""))
    if not raw:
        return 0.0
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _iter_events(log_text: str):
    for ln in log_text.splitlines():
        ln = ln.strip()
        if not ln or not ln.startswith("{"):
            continue
        try:
            obj = json.loads(ln)
        except json.JSONDecodeError:
            continue
        fields = obj.get("fields") or {}
        yield obj, fields, str(fields.get("message", ""))


def _parse_target_telemetry(log_text: str, honest_addrs: set[str], adversary_addrs: set[str]) -> dict:
    """Walk the target's JSON traces in order; reconstruct the Using set (set_local_use==Diffusion,
    not later removed/demoted) and per-peer transition history. Attribute each peer to honest/adversary
    by its socket address (exact match). Pure measurement from emitted telemetry."""
    state: dict[str, str] = {}          # peer -> added|using|maintenance|removed
    transitions: list[dict] = []        # ordered {t, peer, event}
    connect_initial = None
    stake_ready = False
    ledger_candidate_events = 0
    source_counts_seen = []
    added_total = removed_total = demoted_total = 0

    for obj, fields, msg in _iter_events(log_text):
        t = _ts(obj)
        peer = str(fields.get("peer", "")) if fields.get("peer") is not None else ""
        if msg == "peer_selection.connect_initial":
            connect_initial = {
                "static_peers": fields.get("static_peers"),
                "snapshot_peers": fields.get("snapshot_peers"),
            }
        elif msg == "peer_selection.peer.added" and peer:
            state[peer] = "added"
            added_total += 1
            transitions.append({"t": t, "peer": peer, "event": "added"})
        elif msg == "manager.peer.set_local_use" and peer:
            lu = str(fields.get("local_use", ""))
            if lu == "Diffusion":
                state[peer] = "using"
                transitions.append({"t": t, "peer": peer, "event": "using"})
            elif lu == "Maintenance":
                state[peer] = "maintenance"
                transitions.append({"t": t, "peer": peer, "event": "maintenance"})
        elif msg in ("peer_selection.peer.demoted",) and peer:
            state[peer] = "maintenance"
            demoted_total += 1
            transitions.append({"t": t, "peer": peer, "event": "demoted"})
        elif msg in ("peer_selection.peer.removed",) and peer:
            state[peer] = "removed"
            removed_total += 1
            transitions.append({"t": t, "peer": peer, "event": "removed"})
        elif msg.startswith("stake_distribution.") and "ready" in msg:
            stake_ready = True
        elif msg.startswith("stake_distribution.initial"):
            stake_ready = stake_ready or ("ready" in msg)
        elif "ledger" in msg and "candidate" in msg:
            ledger_candidate_events += 1
        elif msg == "SourceCounts" or "SourceCounts" in msg:
            source_counts_seen.append(fields)

    using = {p for p, st in state.items() if st == "using"}
    honest_using = sorted(using & honest_addrs)
    adversary_using = sorted(using & adversary_addrs)
    other_using = sorted(using - honest_addrs - adversary_addrs)
    return {
        "connect_initial": connect_initial,
        "using_set": sorted(using),
        "honest_in_using": honest_using,
        "adversary_in_using": adversary_using,
        "other_in_using": other_using,
        "honest_using_count": len(honest_using),
        "adversary_using_count": len(adversary_using),
        "using_total": len(using),
        "added_total": added_total,
        "removed_total": removed_total,
        "demoted_total": demoted_total,
        "transitions": transitions,
        "stake_distribution_ready": stake_ready,
        "ledger_candidate_events": ledger_candidate_events,
        "source_counts_samples": source_counts_seen[:5],
    }


def _teardown(nodes: list[dict], workroot: str | None) -> None:
    for n in nodes:
        proc = n.get("proc")
        if not proc:
            continue
        for fn in (proc.terminate, proc.kill):
            try:
                fn(); proc.wait(timeout=8); break
            except Exception:
                continue
    if workroot and os.path.isdir(workroot):
        shutil.rmtree(workroot, ignore_errors=True)


def _live_prereq(config: dict) -> str | None:
    """Return a reason string if live mode is unavailable (library/non-live fallback), else None."""
    amaru_bin = config.get("amaru_binary")
    store_src = config.get("amaru_store")
    if not (amaru_bin and store_src and os.path.exists(str(amaru_bin)) and os.path.exists(str(store_src))):
        return ("library/non-live mode: real driver needs config.amaru_binary + config.amaru_store "
                "(a pristine ledger+chain+era-history store to copy). Not faking a pass.")
    return None


def _run_peer_set_capture(config: dict) -> dict:
    """ECLIPSE (Phase-2 Mode 1): stand up a disposable mesh of reachable amaru relays — HONEST static
    anchors + ADVERSARY relays — plus ONE target amaru. Flood the target's SNAPSHOT source with the
    adversary relays (substituted --peer-snapshot, Mode-4 mechanism) while the honest relays feed the
    STATIC source via --peer-address. Observe the target's peer_selection telemetry and MEASURE whether
    the `static!2` floor retains >=2 honest peers in the Using set (ECLIPSE_RESISTED) or the Using set
    is fully adversary-captured (ECLIPSE_ACHIEVED). No hard-coded verdict."""
    reason = _live_prereq(config)
    if reason:
        return {"mode": "simulate_peer_set_capture", "status": "not_applicable", "measured": False, "reason": reason}

    amaru_bin = str(config["amaru_binary"])
    store_src = str(config["amaru_store"])
    era_src = config.get("era_history") or os.path.join(store_src, "era-history.json")
    globals_env = config.get("globals_env")
    network = str(config.get("network", "testnet_42"))
    magic = int(config.get("network_magic", 42))
    honest_count = int(config.get("honest_count", 3))
    adversary_count = int(config.get("adversary_count", 8))
    base_port = int(config.get("base_port", 3520))
    observe_secs = int(config.get("observe_seconds", 120))
    upstream_peers = config.get("upstream_peers")  # None => amaru default (3)
    peer_mix = config.get("peer_mix")
    migrate = bool(config.get("migrate_chain_db", True))
    point_hash = str(config.get("point_hash", "181e9b48af913dd4823f9a44a7460c90b61f55e6f51f4a8050fe2c4f0c642d37"))
    point_slot = int(config.get("point_slot", 1000))

    workroot = tempfile.mkdtemp(prefix="eclipse-mesh-")
    nodes: list[dict] = []
    try:
        ports = _alloc_ports(base_port, honest_count + adversary_count + 1)
        honest_ports = ports[:honest_count]
        adversary_ports = ports[honest_count:honest_count + adversary_count]
        target_port = ports[-1]
        honest_addrs = {f"127.0.0.1:{p}" for p in honest_ports}
        adversary_addrs_list = [f"127.0.0.1:{p}" for p in adversary_ports]
        adversary_addrs = set(adversary_addrs_list)

        # 1) honest static anchor relays
        for i, p in enumerate(honest_ports):
            nodes.append(_launch_amaru(amaru_bin=amaru_bin, store_src=store_src, era_src=era_src,
                                       globals_env=globals_env, workroot=workroot, node_id=f"honest-{i}",
                                       role="honest", port=p, network=network, migrate=migrate))
        # 2) adversary relays (reachable, so they CAN compete for Using slots)
        for i, p in enumerate(adversary_ports):
            nodes.append(_launch_amaru(amaru_bin=amaru_bin, store_src=store_src, era_src=era_src,
                                       globals_env=globals_env, workroot=workroot, node_id=f"adv-{i}",
                                       role="adversary", port=p, network=network, migrate=migrate))

        # wait for relays to listen
        relay_up = {n["addr"]: _wait_listening(n["port"], 25.0) for n in nodes}

        # 3) adversary-flooded snapshot
        snap = os.path.join(workroot, "adversary-peer-snapshot.json")
        _build_adversary_snapshot(snap, adversary_addrs_list, magic, point_hash, point_slot)

        # 4) target amaru: honest statics via --peer-address, adversary flood via --peer-snapshot
        target = _launch_amaru(amaru_bin=amaru_bin, store_src=store_src, era_src=era_src,
                               globals_env=globals_env, workroot=workroot, node_id="target",
                               role="target", port=target_port, network=network,
                               peer_addresses=sorted(honest_addrs), peer_snapshot=snap,
                               peer_mix=peer_mix, upstream_peers=(int(upstream_peers) if upstream_peers else None),
                               migrate=migrate)
        nodes.append(target)

        time.sleep(observe_secs)

        log_text = open(target["log"], errors="replace").read() if os.path.exists(target["log"]) else ""
        tele = _parse_target_telemetry(log_text, honest_addrs, adversary_addrs)

        honest_in_using = tele["honest_using_count"]
        adversary_in_using = tele["adversary_using_count"]
        using_total = tele["using_total"]

        if using_total == 0:
            verdict = "INCONCLUSIVE"
            note = ("no peers reached the Using set during the observation window — eclipse cannot be "
                    "meaningfully induced on this stock substrate; flag: needs patched-amaru + matched-genesis "
                    "substrate for a chain-following eclipse test.")
        elif honest_in_using == 0 and adversary_in_using > 0:
            verdict = "ECLIPSE_ACHIEVED"
            note = ("the target's Using set was fully captured by adversary relays with NO honest static "
                    "peer retained — the static!2 floor did not hold. FINDING.")
        else:
            verdict = "ECLIPSE_RESISTED"
            note = (f"static!2 floor held: {honest_in_using} honest static peer(s) retained in the Using set "
                    f"despite an adversary-flooded snapshot source ({adversary_in_using} adversary in Using). "
                    "Clean-negative (expected): amaru's static floor defends peer selection.")

        return {
            "mode": "simulate_peer_set_capture", "status": "measured", "measured": True,
            "verdict": verdict, "note": note,
            "mesh": {
                "target_addr": target["addr"],
                "honest_static_addrs": sorted(honest_addrs),
                "adversary_relay_addrs": adversary_addrs_list,
                "node_count": len(nodes),
                "relays_listening": relay_up,
            },
            "scope": ("peer-selection resistance only (static-floor / Using-set capture). Stock substrate is "
                      "VRF-walled at the bootstrap tip, so this does NOT test honest-majority chain-following "
                      "under eclipse (that needs patched-amaru + matched-genesis — stronger follow-up)."),
            "peer_mix": peer_mix or "default (static!2@15m, inbound~6, shared~6, snapshot~3@1h, ledger~3@24h)",
            "upstream_peers": upstream_peers or "default(3)",
            "static_floor_held": honest_in_using >= 2,
            "honest_in_using": tele["honest_in_using"],
            "adversary_in_using": tele["adversary_in_using"],
            "other_in_using": tele["other_in_using"],
            "connect_initial": tele["connect_initial"],
            "ledger_source_state": {
                "stake_distribution_ready": tele["stake_distribution_ready"],
                "note": ("ledger-peer source derives from on-chain stake; on stock the ledger is frozen at the "
                         "bootstrap tip (cannot advance past the VRF wall), so the ledger source is the static "
                         "bootstrap stake snapshot, not a live-advancing distribution."),
            },
            "telemetry": {
                "added_total": tele["added_total"], "removed_total": tele["removed_total"],
                "demoted_total": tele["demoted_total"], "using_set": tele["using_set"],
            },
            "observe_seconds": observe_secs, "log_path": target["log"],
        }
    finally:
        _teardown(nodes, workroot)


def _run_hot_warm_churn(config: dict) -> dict:
    """CHURN (Phase-2 Mode 2): on the same mesh shape, induce peer churn by FLAPPING the adversary
    relays (stop/start cycles -> ConnectFailed -> removal -> cooldown -> re-regulation) and OBSERVE the
    target's Using-set turnover. MEASURE whether turnover stays within amaru's bounds (non-static re-adds
    respect --peer-removal-cooldown-secs; static peers stable) => CHURN_WITHIN_BOUNDS, vs unbounded
    re-add thrash => CHURN_UNBOUNDED. No hard-coded verdict."""
    reason = _live_prereq(config)
    if reason:
        return {"mode": "inject_hot_warm_churn", "status": "not_applicable", "measured": False, "reason": reason}

    amaru_bin = str(config["amaru_binary"])
    store_src = str(config["amaru_store"])
    era_src = config.get("era_history") or os.path.join(store_src, "era-history.json")
    globals_env = config.get("globals_env")
    network = str(config.get("network", "testnet_42"))
    magic = int(config.get("network_magic", 42))
    honest_count = int(config.get("honest_count", 3))
    adversary_count = int(config.get("adversary_count", 8))
    base_port = int(config.get("base_port", 3560))
    observe_secs = int(config.get("observe_seconds", 150))
    cooldown_secs = int(config.get("peer_removal_cooldown_secs", 20))
    flap_interval = int(config.get("flap_interval_secs", 25))
    upstream_peers = config.get("upstream_peers")
    peer_mix = config.get("peer_mix")
    migrate = bool(config.get("migrate_chain_db", True))
    point_hash = str(config.get("point_hash", "181e9b48af913dd4823f9a44a7460c90b61f55e6f51f4a8050fe2c4f0c642d37"))
    point_slot = int(config.get("point_slot", 1000))

    workroot = tempfile.mkdtemp(prefix="churn-mesh-")
    nodes: list[dict] = []
    adversary_nodes: list[dict] = []
    try:
        ports = _alloc_ports(base_port, honest_count + adversary_count + 1)
        honest_ports = ports[:honest_count]
        adversary_ports = ports[honest_count:honest_count + adversary_count]
        target_port = ports[-1]
        honest_addrs = {f"127.0.0.1:{p}" for p in honest_ports}
        adversary_addrs_list = [f"127.0.0.1:{p}" for p in adversary_ports]
        adversary_addrs = set(adversary_addrs_list)

        def _spawn_adv(i: int, p: int) -> dict:
            return _launch_amaru(amaru_bin=amaru_bin, store_src=store_src, era_src=era_src,
                                 globals_env=globals_env, workroot=workroot, node_id=f"adv-{i}-{int(time.time())}",
                                 role="adversary", port=p, network=network, migrate=migrate)

        for i, p in enumerate(honest_ports):
            nodes.append(_launch_amaru(amaru_bin=amaru_bin, store_src=store_src, era_src=era_src,
                                       globals_env=globals_env, workroot=workroot, node_id=f"honest-{i}",
                                       role="honest", port=p, network=network, migrate=migrate))
        for i, p in enumerate(adversary_ports):
            n = _spawn_adv(i, p)
            nodes.append(n); adversary_nodes.append(n)

        for n in nodes:
            _wait_listening(n["port"], 25.0)

        snap = os.path.join(workroot, "adversary-peer-snapshot.json")
        _build_adversary_snapshot(snap, adversary_addrs_list, magic, point_hash, point_slot)

        target = _launch_amaru(amaru_bin=amaru_bin, store_src=store_src, era_src=era_src,
                               globals_env=globals_env, workroot=workroot, node_id="target",
                               role="target", port=target_port, network=network,
                               peer_addresses=sorted(honest_addrs), peer_snapshot=snap,
                               peer_mix=peer_mix, upstream_peers=(int(upstream_peers) if upstream_peers else None),
                               cooldown_secs=cooldown_secs, migrate=migrate)
        nodes.append(target)

        # Flap loop: repeatedly down+up the adversary relays to drive churn across >=1 window.
        flaps = []
        deadline = time.time() + observe_secs
        port_by_index = {i: adversary_ports[i] for i in range(len(adversary_ports))}
        while time.time() < deadline:
            time.sleep(min(flap_interval, max(1, int(deadline - time.time()))))
            if time.time() >= deadline:
                break
            # down half the adversary relays
            half = adversary_nodes[: max(1, len(adversary_nodes) // 2)]
            for n in half:
                proc = n.get("proc")
                if proc:
                    try:
                        proc.terminate(); proc.wait(timeout=5)
                    except Exception:
                        pass
            flaps.append({"t": time.time(), "action": "down", "count": len(half)})
            time.sleep(min(8, max(1, int(deadline - time.time()))))
            # bring them back up on the same ports
            new_half = []
            for n in half:
                idx = int(str(n["node_id"]).split("-")[1])
                p = port_by_index.get(idx, n["port"])
                fresh = _spawn_adv(idx, p)
                new_half.append(fresh)
            # replace in tracking lists
            for old, fresh in zip(half, new_half):
                nodes.append(fresh)
                adversary_nodes[adversary_nodes.index(old)] = fresh
            flaps.append({"t": time.time(), "action": "up", "count": len(new_half)})

        log_text = open(target["log"], errors="replace").read() if os.path.exists(target["log"]) else ""
        tele = _parse_target_telemetry(log_text, honest_addrs, adversary_addrs)

        # Cooldown-compliance: for each peer, find (removed -> added) gaps; non-static (adversary) re-adds
        # must respect cooldown_secs. Honest peers are static (special ban period) -> note flapping only.
        last_removed: dict[str, float] = {}
        violations = []
        static_flaps = 0
        readd_gaps = []
        for tr in tele["transitions"]:
            peer = tr["peer"]; ev = tr["event"]; t = tr["t"]
            if ev == "removed":
                last_removed[peer] = t
            elif ev == "added" and peer in last_removed:
                gap = t - last_removed[peer]
                readd_gaps.append({"peer": peer, "gap_secs": round(gap, 2),
                                   "class": "honest-static" if peer in honest_addrs else "adversary"})
                if peer in honest_addrs:
                    static_flaps += 1
                elif gap + 0.5 < cooldown_secs:
                    violations.append({"peer": peer, "gap_secs": round(gap, 2), "cooldown_secs": cooldown_secs})
                del last_removed[peer]

        turnover = tele["added_total"] + tele["removed_total"] + tele["demoted_total"]
        if turnover == 0:
            verdict = "INCONCLUSIVE"
            note = ("no Using-set turnover observed during the churn window — churn could not be meaningfully "
                    "induced on this stock substrate; flag: needs patched substrate for a chain-following churn test.")
        elif violations:
            verdict = "CHURN_UNBOUNDED"
            note = (f"{len(violations)} non-static peer re-add(s) occurred FASTER than the "
                    f"{cooldown_secs}s removal cooldown — peer-removal cooldown not enforced (unbounded thrash). FINDING.")
        else:
            verdict = "CHURN_WITHIN_BOUNDS"
            note = (f"Using-set turnover stayed within bounds: all {len(readd_gaps)} re-add(s) respected the "
                    f"{cooldown_secs}s cooldown; no unbounded thrash. Clean-negative (expected).")

        return {
            "mode": "inject_hot_warm_churn", "status": "measured", "measured": True,
            "verdict": verdict, "note": note,
            "mesh": {"target_addr": target["addr"], "node_count": len(set(id(n) for n in nodes)),
                     "honest_static_addrs": sorted(honest_addrs), "adversary_relay_addrs": adversary_addrs_list},
            "scope": ("peer-selection churn resistance only; stock VRF-walled substrate. Does NOT test "
                      "chain-following under churn (needs patched-amaru + matched-genesis follow-up)."),
            "churn_driver": {
                "method": "adversary-relay flap (stop/start) cycles",
                "flap_interval_secs": flap_interval, "flap_events": len(flaps),
                "peer_removal_cooldown_secs": cooldown_secs,
                "natural_churn_interval_base_secs": 3300,
                "note": ("amaru's periodic Using-churn timer is CHURN_INTERVAL_BASE=3300s (+<=600s fuzz), far "
                         "beyond a bounded run; this driver measures event-driven churn from connection failures."),
            },
            "measured_turnover": {
                "added_total": tele["added_total"], "removed_total": tele["removed_total"],
                "demoted_total": tele["demoted_total"], "total_transitions": turnover,
                "readd_gaps": readd_gaps, "cooldown_violations": violations,
                "static_peer_flaps": static_flaps,
            },
            "final_using_set": tele["using_set"],
            "observe_seconds": observe_secs, "log_path": target["log"],
        }
    finally:
        _teardown(nodes, workroot)


def apply_topology_mode(*, metadata: dict, mode: str, config: dict) -> dict:
    valid = {
        "simulate_peer_set_capture",
        "inject_hot_warm_churn",
        "perturb_ledger_peer_weights",
        "substitute_big_ledger_peers",
    }
    if mode not in valid:
        raise ValueError(f"unsupported topology mode: {mode}")

    overrides = dict(metadata.get("observation_overrides") or {})

    if mode == "substitute_big_ledger_peers":
        result = _run_snapshot_substitution(config)
        result.setdefault("target_node", str(config.get("target_node", "")))
        metadata["observation_overrides"] = overrides
        return {"result": result, "observation_overrides": overrides}

    if mode == "simulate_peer_set_capture":
        result = _run_peer_set_capture(config)
        result.setdefault("target_node", str(config.get("target_node", "")))
        metadata["observation_overrides"] = overrides  # no fabricated overrides
        return {"result": result, "observation_overrides": overrides}

    if mode == "inject_hot_warm_churn":
        result = _run_hot_warm_churn(config)
        result.setdefault("target_node", str(config.get("target_node", "")))
        metadata["observation_overrides"] = overrides  # no fabricated overrides
        return {"result": result, "observation_overrides": overrides}

    # perturb_ledger_peer_weights: still substrate-walled in the single-process model. We do NOT
    # fabricate observation_overrides (no fake peer edges / quorum / tips / stake distributions) and
    # we do NOT return a pass. Honest not_applicable.
    result = {
        "target_node": str(config.get("target_node", "")),
        "mode": mode,
        "status": "not_applicable",
        "measured": False,
        "reason": "ledger-peer weight manipulation requires a live-advancing on-chain stake distribution; "
                  "the stock substrate is VRF-walled at the bootstrap tip (ledger frozen). Not faking a pass. "
                  "Real implementation pending a patched-amaru + matched-genesis substrate.",
    }
    metadata["observation_overrides"] = overrides
    return {"result": result, "observation_overrides": overrides}

def run_topology_fault(*, runtime_metadata_path: Path, output_dir: Path, mode: str, config: dict) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = _load_metadata(runtime_metadata_path)
    updated = apply_topology_mode(metadata=metadata, mode=mode, config=config)
    metadata["observation_overrides"] = updated["observation_overrides"]
    _write_metadata(runtime_metadata_path, metadata)
    report = {
        "mode": mode,
        "target_node": str(config.get("target_node", "")),
        "runtime_metadata_path": str(runtime_metadata_path),
        "result": updated["result"],
    }
    (output_dir / "result.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument(
        "--mode",
        required=True,
        choices=[
            "simulate_peer_set_capture",
            "inject_hot_warm_churn",
            "perturb_ledger_peer_weights",
            "substitute_big_ledger_peers",
        ],
    )
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    report = run_topology_fault(
        runtime_metadata_path=Path(config["runtime_metadata_path"]),
        output_dir=Path(config["output_dir"]),
        mode=args.mode,
        config=config,
    )
    print(f"mode={report['mode']} target_node={report['target_node']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
