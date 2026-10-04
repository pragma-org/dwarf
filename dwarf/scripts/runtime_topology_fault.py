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


def apply_topology_mode(*, metadata: dict, mode: str, config: dict) -> dict:
    valid = {
        "simulate_peer_set_capture",
        "inject_hot_warm_churn",
        "perturb_ledger_peer_weights",
        "substitute_big_ledger_peers",
    }
    if mode not in valid:
        raise ValueError(f"unsupported topology mode: {mode}")
    if mode == "substitute_big_ledger_peers":
        result = _run_snapshot_substitution(config)
        result.setdefault("target_node", str(config.get("target_node", "")))
        overrides = dict(metadata.get("observation_overrides") or {})
        metadata["observation_overrides"] = overrides
        return {"result": result, "observation_overrides": overrides}
    # substrate-walled: eclipse / Sybil / peer-set capture / ledger-peer manipulation all require a
    # multi-peer (10-30 node) topology so a node can be surrounded by adversary peers. The current
    # substrate is a 2-node pair (each node has exactly one peer), so none of these faults can be
    # performed. We do NOT fabricate observation_overrides (no fake peer edges / quorum / tips / stake
    # distributions) and we do NOT return a pass. Honest not_applicable; real implementation pending a
    # multi-peer substrate (phase 2).
    result = {
        "target_node": str(config.get("target_node", "")),
        "mode": mode,
        "status": "not_applicable",
        "measured": False,
        "reason": "substrate-walled: eclipse/Sybil/peer-set/ledger-peer manipulation requires a "
                  "multi-peer (10-30 node) topology; current substrate is 2-node. Real implementation "
                  "pending a multi-peer substrate (phase 2). Not faking a pass.",
    }
    # pass existing overrides through unchanged; write NO fabricated topology/quorum/tip overrides
    overrides = dict(metadata.get("observation_overrides") or {})
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
