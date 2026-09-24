"""Join opcert header cases (ground truth) with the target's observed verdicts.

The peer serves one header per case (each preceded by a valid control) and
records the header hash it served for each case. The target's own logs, parsed
into verdicts by hash, are the observed side. join_cases classifies each case:

  matched      — the target reached its declared verdict (accept for valid
                 cases; reject with the declared reason for broken cases).
  mismatch     — the target reached a different verdict, or rejected for a
                 different reason, or accepted a case it should reject.
  inconclusive — the case was never served, or its header was never observed
                 in the target's logs (fail-closed: never a pass).

The driver (run_opcert_header_cases) that starts the peer, captures logs and
writes result.json is wired after the peer's evidence format is fixed (Task 3).
"""
from __future__ import annotations


def join_cases(cases, served_hash_by_case, observed_by_hash, implementation):
    rows = []
    for case in cases:
        cid = case["id"]
        served = served_hash_by_case.get(cid)
        observed = observed_by_hash.get(served) if served else None
        expected_verdict = case["expected_verdict"]
        expected_reason = case["expected_reason"][implementation]
        if served is None or observed is None:
            status = "inconclusive"
        elif expected_verdict == "accept":
            status = "matched" if observed["verdict"] == "accepted" else "mismatch"
        else:  # reject: verdict AND reason must match
            status = (
                "matched"
                if observed["verdict"] == "rejected" and observed.get("reason") == expected_reason
                else "mismatch"
            )
        rows.append({
            "case": cid,
            "expected_verdict": expected_verdict,
            "expected_reason": expected_reason,
            "served_hash": served,
            "observed_verdict": (observed or {}).get("verdict"),
            "observed_reason": (observed or {}).get("reason"),
            "status": status,
        })
    return rows


def evaluate_match(cases_rows):
    """Assertion decision: every case must be 'matched' (fail-closed).

    A 'mismatch' (wrong verdict, wrong reason, or an accepted bad case) fails;
    an 'inconclusive' (case never served or verdict never observed) also fails.
    """
    mismatched = [r["case"] for r in cases_rows if r.get("status") == "mismatch"]
    inconclusive = [r["case"] for r in cases_rows if r.get("status") == "inconclusive"]
    passed = bool(cases_rows) and not mismatched and not inconclusive
    return {
        "result": "pass" if passed else "fail",
        "mismatched": mismatched,
        "inconclusive": inconclusive,
        "case_count": len(cases_rows),
    }


def evaluate_agree(rows_a, rows_b):
    """Cross-node agreement: both targets must reach the same verdict per case.

    Fails closed if a case is missing on either side (no verdict to compare).
    """
    by_a = {r["case"]: r.get("observed_verdict") for r in rows_a}
    by_b = {r["case"]: r.get("observed_verdict") for r in rows_b}
    cases = sorted(set(by_a) | set(by_b))
    disagreements = []
    for case in cases:
        if case not in by_a or case not in by_b or by_a[case] != by_b[case]:
            disagreements.append(case)
    passed = bool(cases) and not disagreements
    return {
        "result": "pass" if passed else "fail",
        "disagreements": disagreements,
        "case_count": len(cases),
    }


# =====================================================================
# Ground-truth / observed join helpers + live driver (Task 4)
# =====================================================================

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from scripts.header_validation_parse import (
    parse_amaru_header_events,
    parse_cardano_header_events,
    verdict_by_hash,
)

SCRIPT_DIR = Path(__file__).resolve().parent
DWARF_ROOT = SCRIPT_DIR.parent

# The always-reachable default case set (one leader slot reliably reaches each).
DEFAULT_CASE_IDS = (
    "valid-control",
    "cold-key-unauthorized",
    "counter-jump",
    "kes-before-window",
    "hot-key-mismatch",
)

DEFAULT_PEER_BIN = (
    DWARF_ROOT.parent
    / "antithesis/components/dwarf-opcert-adversary-latest/dist-newstyle/build/"
    "x86_64-linux/ghc-9.6.7/dwarf-opcert-adversary-latest-0.1.0.0/x/"
    "dwarf-opcert-adversary-latest/build/dwarf-opcert-adversary-latest/"
    "dwarf-opcert-adversary-latest"
)


def served_hash_by_case(evidence_lines):
    """Map each case id to the header hash the peer served for it.

    Reads the peer's ``--evidence`` ndjson. Only ``opcert_case_served`` lines
    carry a delivered header; a case that only reached ``opcert_case_started``
    or ``opcert_case_unreachable`` has no served hash (stays inconclusive).
    """
    served = {}
    for line in evidence_lines:
        line = (line or "").strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(record, dict) or record.get("kind") != "opcert_case_served":
            continue
        case = record.get("case")
        header_hash = record.get("header_hash")
        if case and header_hash:
            served[str(case)] = str(header_hash)
    return served


def build_result(cases, served_map, observed_by_hash, target, *, target_node=None, case_set=None):
    """Assemble the primitive's ``result.json`` body from joined evidence.

    ``target`` is the implementation key ("cardano-node" or "amaru") used to
    pick each case's expected reason. Fail-closed via ``join_cases``: a case
    never served, or served but never observed, is ``inconclusive``.
    """
    rows = join_cases(cases, served_map, observed_by_hash, target)
    decision = evaluate_match(rows)
    matched = [r["case"] for r in rows if r.get("status") == "matched"]
    return {
        "schema_version": "v1",
        "target": target,
        "target_node": target_node,
        "case_set": list(case_set) if case_set is not None else [c["id"] for c in cases],
        "cases": rows,
        "summary": {
            "result": decision["result"],
            "case_count": decision["case_count"],
            "matched": matched,
            "mismatched": decision["mismatched"],
            "inconclusive": decision["inconclusive"],
            "all_matched": decision["result"] == "pass",
        },
    }


# --------------------------------------------------------------------- runtime

def _load_runtime(runtime_root):
    root = Path(runtime_root)
    candidates = [
        root / "outputs" / "substrate-compose" / "runtime.json",
        root / "runtime.json",
        root / "substrate-compose" / "runtime.json",
    ]
    for path in candidates:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8")), path
    raise FileNotFoundError(f"no runtime.json under {runtime_root} (tried {candidates})")


def _docker(*args, check=True):
    proc = subprocess.run(["docker", *args], capture_output=True, text=True, check=False)
    if check and proc.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc


def _host_env_dir(container_name):
    proc = _docker("inspect", container_name, "--format", "{{json .Mounts}}")
    for mount in json.loads(proc.stdout):
        if mount.get("Destination") == "/env":
            return Path(mount["Source"])
    raise RuntimeError(f"container {container_name} has no /env mount")


def _container_image(container_name):
    proc = _docker("inspect", container_name, "--format", "{{.Config.Image}}")
    return proc.stdout.strip()


def _now_docker_ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _consumer_topology(peer_host, peer_port):
    return {
        "bootstrapPeers": None,
        "localRoots": [
            {
                "accessPoints": [{"address": peer_host, "port": peer_port}],
                "advertise": False,
                "trustable": True,
                "diffusionMode": "InitiatorAndResponder",
                "hotValency": 1,
                "warmValency": 1,
            }
        ],
        "publicRoots": [{"accessPoints": [], "advertise": False}],
        "useLedgerAfterSlot": -1,
    }


def _container_state(container):
    proc = _docker("inspect", container, check=False)
    if proc.returncode != 0:
        return {"running": None, "status": None, "exit_code": None,
                "oom_killed": None, "restart_count": None}
    body = json.loads(proc.stdout)[0]
    state = body.get("State") or {}
    return {
        "running": state.get("Running") is True,
        "status": state.get("Status"),
        "exit_code": state.get("ExitCode"),
        "oom_killed": state.get("OOMKilled") is True,
        "restart_count": int(body.get("RestartCount") or 0),
    }


def _cardano_tip(container, socket_path, magic):
    proc = _docker(
        "exec", container, "cardano-cli", "query", "tip",
        "--socket-path", socket_path, "--testnet-magic", str(magic), check=False,
    )
    if proc.returncode != 0:
        return None
    try:
        body = json.loads(proc.stdout)
    except (ValueError, TypeError):
        return None
    height = body.get("block")
    if isinstance(height, bool) or not isinstance(height, int):
        return None
    return {"block_height": height, "hash": body.get("hash"), "slot": body.get("slot")}


def _target_log_signals(container, since):
    try:
        from scripts.runtime_cardano_measurement_calibration import classify_log_signals
    except Exception:  # pragma: no cover - defensive
        return {}
    proc = _docker("logs", "--since", since, container, check=False)
    return classify_log_signals(proc.stdout + "\n" + proc.stderr)


def _read_consumer_events(consumer_name, since, implementation):
    proc = _docker("logs", "--since", since, consumer_name, check=False)
    lines = (proc.stdout + "\n" + proc.stderr).splitlines()
    if implementation == "amaru":
        return parse_amaru_header_events(lines)
    return parse_cardano_header_events(lines)


def run_opcert_header_cases(
    runtime_root,
    target_node,
    output_dir,
    case_ids=None,
    peer_bin=None,
    listen_port=34071,
    consumer_port=34072,
    per_case_timeout=240,
    settle_seconds=8,
):
    """Serve one mutated header per case to an isolated copy of the target and
    join the peer's ground truth with the copy's observed verdicts.

    Returns the ``result.json`` body and writes it (plus attempts.ndjson) under
    ``output_dir``. Fail-closed: any case whose header is not served or whose
    verdict is not observed stays ``inconclusive``.
    """
    from scripts.opcert_cases import load_cases

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    attempts_path = output_dir / "attempts.ndjson"
    result_path = output_dir / "result.json"

    peer_bin = Path(peer_bin or DEFAULT_PEER_BIN)
    if not peer_bin.is_file():
        raise FileNotFoundError(f"opcert peer binary not found: {peer_bin}")

    wanted = list(case_ids) if case_ids else list(DEFAULT_CASE_IDS)
    all_cases = {c["id"]: c for c in load_cases()}
    cases = [all_cases[cid] for cid in wanted if cid in all_cases]

    runtime, runtime_path = _load_runtime(runtime_root)
    nodes = runtime.get("haskell_nodes") or []
    target = next((n for n in nodes if n.get("id") == target_node), None)
    if target is None:
        raise RuntimeError(f"target node {target_node!r} not in {runtime_path}")
    implementation = target.get("impl") or "cardano-node"

    listen_addr = target.get("listen_address") or f"127.0.0.1:{target.get('port')}"
    up_host, up_port = listen_addr.split(":")
    env_dir = _host_env_dir(target["container_name"])
    image = _container_image(target["container_name"])
    pool_dir = env_dir / "pools-keys" / "pool1"
    kes_skey = pool_dir / "kes.skey"
    cold_skey = pool_dir / "cold.skey"
    shelley = json.loads((env_dir / "shelley-genesis.json").read_text(encoding="utf-8"))
    slots_per_kes = int(shelley["slotsPerKESPeriod"])
    max_kes_evo = int(shelley["maxKESEvolutions"])
    magic = int(runtime.get("network_magic") or shelley.get("networkMagic") or 42)
    target_container = target["container_name"]
    target_socket = target.get("container_socket_path") or f"/env/socket/{target_node}/sock"

    health_started_at = _now_docker_ts()
    state_before = _container_state(target_container)
    tip_before = _cardano_tip(target_container, target_socket, magic)

    consumer_name = f"opcert-consumer-{runtime.get('compose_project', 'devnet')}"[:100]
    consumer_volume = f"{consumer_name}-db"[:100]
    work = output_dir / "harness"
    work.mkdir(parents=True, exist_ok=True)
    topo_path = work / "consumer-topology.json"
    topo_path.write_text(json.dumps(_consumer_topology("127.0.0.1", listen_port)), encoding="utf-8")

    attempts = []

    def record(event):
        with attempts_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event) + "\n")
        attempts.append(event)

    # Start a single isolated consumer (fresh db) that only follows the peer.
    # A docker-managed named volume avoids root-owned bind-mount cleanup issues.
    _docker("rm", "-f", consumer_name, check=False)
    _docker("volume", "rm", "-f", consumer_volume, check=False)
    consumer_cmd = (
        "mkdir -p /consumer && exec cardano-node run "
        "--config /env/configuration.yaml "
        f"--topology {topo_path.as_posix()} "
        "--database-path /consumer/db --socket-path /consumer/sock "
        f"--port {consumer_port} --host-addr 0.0.0.0"
    )
    _docker(
        "run", "-d", "--name", consumer_name, "--network", "host",
        "-v", f"{env_dir}:/env:ro",
        "-v", f"{topo_path.parent}:{topo_path.parent.as_posix()}:ro",
        "-v", f"{consumer_volume}:/consumer",
        "--entrypoint", "bash", image, "-lc", consumer_cmd,
    )
    record({"kind": "consumer_started", "container": consumer_name, "image": image})

    served_map = {}
    observed_by_hash = {}
    try:
        # let the consumer sync the honest chain from the peer first
        for cid in wanted:
            if cid not in all_cases:
                record({"kind": "case_skipped", "case": cid, "reason": "not in corpus"})
                continue
            evidence = work / f"evidence-{cid}.ndjson"
            evidence.write_text("", encoding="utf-8")
            since = _now_docker_ts()
            peer_cmd = [
                str(peer_bin), "serve-case",
                "--case", cid,
                "--upstream", f"{up_host}:{up_port}",
                "--listen-port", str(listen_port),
                "--kes-skey", str(kes_skey),
                "--cold-skey", str(cold_skey),
                "--slots-per-kes", str(slots_per_kes),
                "--max-kes-evo", str(max_kes_evo),
                "--evidence", str(evidence),
            ]
            record({"kind": "case_started", "case": cid, "command": peer_cmd})
            peer = subprocess.Popen(peer_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            served_hash = None
            observed = None
            deadline = time.time() + per_case_timeout
            try:
                while time.time() < deadline:
                    time.sleep(3)
                    if evidence.is_file():
                        served = served_hash_by_case(evidence.read_text(encoding="utf-8").splitlines())
                        if cid in served:
                            served_hash = served[cid]
                    if served_hash is not None:
                        events = _read_consumer_events(consumer_name, since, implementation)
                        vmap = verdict_by_hash(events)
                        if served_hash in vmap:
                            observed = vmap[served_hash]
                            break
            finally:
                peer.terminate()
                try:
                    peer.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    peer.kill()
            if served_hash is not None:
                served_map[cid] = served_hash
                if observed is not None:
                    observed_by_hash[served_hash] = observed
            record({
                "kind": "case_observed", "case": cid,
                "served_hash": served_hash,
                "observed_verdict": (observed or {}).get("verdict"),
                "observed_reason": (observed or {}).get("reason"),
            })
            # brief settle so the consumer re-establishes to the next peer process
            time.sleep(settle_seconds)
    finally:
        logs = _docker("logs", consumer_name, check=False)
        (work / "consumer.log").write_text(logs.stdout + "\n" + logs.stderr, encoding="utf-8")
        _docker("rm", "-f", consumer_name, check=False)
        _docker("volume", "rm", "-f", consumer_volume, check=False)

    state_after = _container_state(target_container)
    tip_after = _cardano_tip(target_container, target_socket, magic)

    result = build_result(
        cases, served_map, observed_by_hash, implementation,
        target_node=target_node, case_set=wanted,
    )
    result["runtime_root"] = str(runtime_root)
    result["compose_project"] = runtime.get("compose_project")
    # target_health lets runtime_target_health_and_progress / target_progress_continues
    # confirm the honest producer kept advancing while the isolated copy was probed.
    result["target_health"] = {
        "before": state_before,
        "after": state_after,
        "tip_before": tip_before or {},
        "tip_after": tip_after or {},
        "log_signals": _target_log_signals(target_container, health_started_at),
    }
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run opcert header validation cases against a target.")
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--target-node", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--case-ids", default="")
    parser.add_argument("--peer-bin", default="")
    parser.add_argument("--listen-port", type=int, default=34071)
    parser.add_argument("--consumer-port", type=int, default=34072)
    parser.add_argument("--per-case-timeout", type=int, default=240)
    args = parser.parse_args(argv)
    case_ids = [c for c in args.case_ids.split(",") if c] or None
    result = run_opcert_header_cases(
        runtime_root=args.runtime_root,
        target_node=args.target_node,
        output_dir=args.output_dir,
        case_ids=case_ids,
        peer_bin=args.peer_bin or None,
        listen_port=args.listen_port,
        consumer_port=args.consumer_port,
        per_case_timeout=args.per_case_timeout,
    )
    print(json.dumps(result["summary"], indent=2))
    return 0 if result["summary"]["all_matched"] else 1


if __name__ == "__main__":
    sys.exit(main())
