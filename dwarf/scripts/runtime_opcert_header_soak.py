"""Randomized differential opcert-header SOAK driver.

A soak runs one *family* for a wall-clock time budget (default 3h). Each
iteration it generates a fresh seed-deterministic ``SoakCaseSpec``
(``opcert_soak_families.generate_case``), serves it through the existing opcert
forger (``serve-case --case-spec``) against an isolated consumer of each target,
reads the verdict(s) through the existing parser, evaluates the per-family
invariant, appends the iteration to ``attempts.ndjson`` and updates counters.
``result.json`` carries the counters and, for every mismatch/disagreement, the
full seed-derived spec so a finding is replayable.

This is an EXTENSION of ``runtime_opcert_header_cases`` — it imports and reuses
that module's substrate detection, forger invocation and log parser, plus the
fail-closed accept/reject/reason semantics (via ``opcert_soak_result``). It does
not reinvent any of them.

Fail-closed everywhere: no leader slot / undelivered header within the
per-iteration timeout ⇒ ``inconclusive`` (tracked separately, never ``pass``,
never ``agree``/``disagree``); a differential iteration with one side
inconclusive is inconclusive on both sides; zero conclusive iterations ⇒ the
whole run FAILS. The loop is bounded by the injected monotonic ``clock``.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from scripts import opcert_soak_families as families
from scripts import opcert_soak_result as R
from scripts import runtime_opcert_header_cases as det

SCRIPT_DIR = Path(__file__).resolve().parent
DWARF_ROOT = SCRIPT_DIR.parent

_OUTCOME_FOR_DIFF = {"agree": "pass", "disagree": "mismatch", "inconclusive": "inconclusive"}

# Per-node listen/consumer port bases; each node gets a distinct pair so the two
# sides of a differential iteration never collide.
_PORT_BASE = {"listen": 34081, "consumer": 34082}


# --------------------------------------------------------------------------- #
# Substrate seams (patched out in unit tests; real bodies reuse the
# deterministic driver's proven helpers for a live run).
# --------------------------------------------------------------------------- #

def _load_runtime_root(runtime_root):
    """Return ``(runtime_dict, default_implementation)`` for ``runtime_root``."""
    runtime, _path = det._load_runtime(runtime_root)
    nodes = runtime.get("haskell_nodes") or []
    implementation = (nodes[0].get("impl") if nodes else None) or "cardano-node"
    return runtime, implementation


def _node_impl(runtime, node_id):
    for node in (runtime or {}).get("haskell_nodes") or []:
        if node.get("id") == node_id:
            return node.get("impl")
    return None


def _find_node(runtime, node_id):
    for node in (runtime or {}).get("haskell_nodes") or []:
        if node.get("id") == node_id:
            return node
    return None


def _open_consumers(runtime, nodes, *, peer_bin=None, output_dir=None):
    """Start one isolated fresh-DB consumer per target node (live path).

    Each consumer follows only the per-node forger listen port; the forger
    (started per iteration by ``_serve_one``/``_serve_one_node``) serves the
    generated case header into it. Returns ``{node: consumer_context}``.
    """
    consumers = {}
    work = Path(output_dir) / "harness"
    work.mkdir(parents=True, exist_ok=True)
    for index, node_id in enumerate(nodes):
        node = _find_node(runtime, node_id)
        if node is None:
            raise RuntimeError(f"soak target node {node_id!r} not in runtime")
        env_dir = det._host_env_dir(node["container_name"])
        image = det._container_image(node["container_name"])
        listen_port = _PORT_BASE["listen"] + index * 2
        consumer_port = _PORT_BASE["consumer"] + index * 2
        consumer_name = f"opcert-soak-consumer-{node_id}-{runtime.get('compose_project', 'devnet')}"[:100]
        consumer_volume = f"{consumer_name}-db"[:100]
        topo_path = work / f"consumer-topology-{node_id}.json"
        topo_path.write_text(json.dumps(det._consumer_topology("127.0.0.1", listen_port)), encoding="utf-8")
        det._docker("rm", "-f", consumer_name, check=False)
        det._docker("volume", "rm", "-f", consumer_volume, check=False)
        consumer_cmd = (
            "mkdir -p /consumer && exec cardano-node run "
            "--config /env/configuration.yaml "
            f"--topology {topo_path.as_posix()} "
            "--database-path /consumer/db --socket-path /consumer/sock "
            f"--port {consumer_port} --host-addr 0.0.0.0"
        )
        det._docker(
            "run", "-d", "--name", consumer_name, "--network", "host",
            "-v", f"{env_dir}:/env:ro",
            "-v", f"{topo_path.parent}:{topo_path.parent.as_posix()}:ro",
            "-v", f"{consumer_volume}:/consumer",
            "--entrypoint", "bash", image, "-lc", consumer_cmd,
        )
        pool_dir = env_dir / "pools-keys" / "pool1"
        shelley = json.loads((env_dir / "shelley-genesis.json").read_text(encoding="utf-8"))
        listen_addr = node.get("listen_address") or f"127.0.0.1:{node.get('port')}"
        consumers[node_id] = {
            "name": consumer_name, "volume": consumer_volume, "work": work,
            "listen_port": listen_port, "implementation": node.get("impl") or "cardano-node",
            "kes_skey": pool_dir / "kes.skey", "cold_skey": pool_dir / "cold.skey",
            "slots_per_kes": int(shelley["slotsPerKESPeriod"]),
            "max_kes_evo": int(shelley["maxKESEvolutions"]),
            "upstream": listen_addr,
        }
    return consumers


def _close_consumers(consumers):
    for ctx in (consumers or {}).values():
        det._docker("rm", "-f", ctx["name"], check=False)
        det._docker("volume", "rm", "-f", ctx["volume"], check=False)


def _serve_case_spec(spec, ctx, *, peer_bin, per_iteration_timeout, output_dir, iteration):
    """Serve one generated spec through the forger and read the verdict.

    Writes ``case-spec-<n>.json``, runs ``serve-case --case-spec`` against the
    node's consumer, and returns ``(served_hash | None, observed | None)``.
    Fail-closed: no served hash or no observed verdict within the timeout ⇒
    ``(served_hash_or_None, None)``.
    """
    work = Path(output_dir) / "harness"
    work.mkdir(parents=True, exist_ok=True)
    spec_path = work / f"case-spec-{spec['family']}-{iteration:06d}.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    evidence = work / f"evidence-{spec['family']}-{iteration:06d}.ndjson"
    evidence.write_text("", encoding="utf-8")
    peer = Path(peer_bin or det.DEFAULT_PEER_BIN)
    up_host, up_port = ctx["upstream"].split(":")
    since = det._now_docker_ts()
    peer_cmd = [
        str(peer), "serve-case",
        "--case", spec["base_case"],
        "--case-spec", str(spec_path),
        "--upstream", f"{up_host}:{up_port}",
        "--listen-port", str(ctx["listen_port"]),
        "--kes-skey", str(ctx["kes_skey"]),
        "--cold-skey", str(ctx["cold_skey"]),
        "--slots-per-kes", str(ctx["slots_per_kes"]),
        "--max-kes-evo", str(ctx["max_kes_evo"]),
        "--evidence", str(evidence),
    ]
    proc = subprocess.Popen(peer_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    served_hash = None
    observed = None
    deadline = time.time() + per_iteration_timeout
    try:
        while time.time() < deadline:
            time.sleep(3)
            if evidence.is_file():
                served = det.served_hash_by_case(evidence.read_text(encoding="utf-8").splitlines())
                if spec["base_case"] in served:
                    served_hash = served[spec["base_case"]]
            if served_hash is not None:
                events = det._read_consumer_events(ctx["name"], since, ctx["implementation"])
                vmap = det.verdict_by_hash(events)
                if served_hash in vmap:
                    observed = vmap[served_hash]
                    break
    finally:
        peer.terminate()
        try:
            peer.wait(timeout=10)
        except subprocess.TimeoutExpired:
            peer.kill()
    return served_hash, observed


def _serve_one(spec, *, consumer=None, consumers=None, node=None, peer_bin=None,
               per_iteration_timeout=240, output_dir=None, iteration=0, **_kw):
    """Single-target serve. For ``restart-persistence`` the rotation+restart is
    performed by the operator's pre-rotated profile; here we serve the replay
    counter and read the verdict."""
    ctx = consumer if isinstance(consumer, dict) else (consumers or {}).get(node)
    return _serve_case_spec(spec, ctx, peer_bin=peer_bin,
                            per_iteration_timeout=per_iteration_timeout,
                            output_dir=output_dir, iteration=iteration)


def _serve_one_node(spec, *, node, consumers=None, peer_bin=None,
                    per_iteration_timeout=240, output_dir=None, iteration=0, **_kw):
    """Differential serve to one of the two compared nodes."""
    ctx = (consumers or {}).get(node)
    return _serve_case_spec(spec, ctx, peer_bin=peer_bin,
                            per_iteration_timeout=per_iteration_timeout,
                            output_dir=output_dir, iteration=iteration)


def _node_tip(runtime, node_id):
    node = _find_node(runtime, node_id)
    if not node:
        return {}
    try:
        magic = int(runtime.get("network_magic") or 42)
        socket = node.get("container_socket_path") or f"/env/socket/{node_id}/sock"
        return det._cardano_tip(node["container_name"], socket, magic) or {}
    except Exception:
        return {}


# --------------------------------------------------------------------------- #
# The soak loop.
# --------------------------------------------------------------------------- #

def run_opcert_header_soak(runtime_root, family, seed, output_dir, *, target_node=None,
                           target_nodes=None, time_budget_seconds=10800, peer_bin=None,
                           per_iteration_timeout=240, restart_k=4, clock=time.monotonic):
    if family not in families.FAMILIES:
        raise ValueError(f"unknown family: {family!r}")
    differential = family in families.DIFFERENTIAL_FAMILIES
    if differential:
        if not target_nodes or len(target_nodes) != 2:
            raise ValueError(f"family {family} is differential; needs target_nodes of length 2")
        nodes = list(target_nodes)
    else:
        if not target_node:
            raise ValueError(f"family {family} is single-target; needs target_node")
        nodes = [target_node]

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    attempts_path = output_dir / "attempts.ndjson"
    result_path = output_dir / "result.json"

    runtime, implementation = _load_runtime_root(runtime_root)
    compose_project = (runtime or {}).get("compose_project")
    if not differential:
        implementation = _node_impl(runtime, nodes[0]) or implementation

    consumers = _open_consumers(runtime, nodes, peer_bin=peer_bin, output_dir=output_dir)

    tip_before = _node_tip(runtime, nodes[0])
    records = []
    start = clock()
    iteration = 0
    try:
        while clock() - start < time_budget_seconds:
            spec = families.generate_case(family, seed, iteration, restart_k=restart_k)
            if differential:
                verdicts = {}
                for node in nodes:
                    _hash, observed = _serve_one_node(
                        spec, node=node, consumers=consumers, peer_bin=peer_bin,
                        per_iteration_timeout=per_iteration_timeout, output_dir=output_dir,
                        iteration=iteration)
                    verdicts[node] = observed["verdict"] if observed else None
                diff = R.classify_differential(verdicts[nodes[0]], verdicts[nodes[1]])
                record = {
                    "outcome": _OUTCOME_FOR_DIFF[diff], "differential": diff,
                    "spec": spec, "verdicts": verdicts,
                }
            else:
                served_hash, observed = _serve_one(
                    spec, consumer=consumers.get(nodes[0]), node=nodes[0], peer_bin=peer_bin,
                    per_iteration_timeout=per_iteration_timeout, output_dir=output_dir,
                    iteration=iteration)
                outcome = R.classify_iteration(spec, served_hash, observed, implementation)
                record = {
                    "outcome": outcome, "spec": spec, "served_hash": served_hash,
                    "observed_verdict": (observed or {}).get("verdict"),
                    "observed_reason": (observed or {}).get("reason"),
                }
            with attempts_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
            records.append(record)
            iteration += 1
    finally:
        _close_consumers(consumers)

    tip_after = _node_tip(runtime, nodes[0])
    duration = clock() - start
    target_health = {
        "before": {}, "after": {},
        "tip_before": tip_before or {}, "tip_after": tip_after or {},
        "log_signals": {},
    }
    result = R.build_soak_result(
        family=family, seed=seed, differential=differential, target_nodes=nodes,
        records=records, duration_seconds=duration, runtime_root=str(runtime_root),
        compose_project=compose_project, target_health=target_health)
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run a randomized opcert-header soak against a target.")
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--family", required=True, choices=list(families.FAMILIES))
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--target-node", default="")
    parser.add_argument("--target-nodes", default="")
    parser.add_argument("--time-budget-seconds", type=int, default=10800)
    parser.add_argument("--peer-bin", default="")
    parser.add_argument("--per-iteration-timeout", type=int, default=240)
    parser.add_argument("--restart-k", type=int, default=4)
    args = parser.parse_args(argv)
    target_nodes = [n for n in args.target_nodes.split(",") if n] or None
    result = run_opcert_header_soak(
        runtime_root=args.runtime_root, family=args.family, seed=args.seed,
        output_dir=args.output_dir, target_node=args.target_node or None,
        target_nodes=target_nodes, time_budget_seconds=args.time_budget_seconds,
        peer_bin=args.peer_bin or None, per_iteration_timeout=args.per_iteration_timeout,
        restart_k=args.restart_k)
    print(json.dumps({k: result[k] for k in ("family", "seed", "iterations", "conclusive",
                                             "inconclusive", "pass", "counters")}, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
