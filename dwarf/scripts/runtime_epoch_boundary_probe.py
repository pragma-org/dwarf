"""Epoch / stake / ledger producer for the epoch-boundary assertions.

One producer contract. Each mode writes exactly the section its assertion reads, under
``result.<section>``, with ``source``, ``measured`` and ``per_node`` results:

- ``force_epoch_boundary``          -> ``epoch_boundary``
  {observed_boundary_slot, expected_window_start, expected_window_end, all_nodes_within_bounds}
- ``simulate_stake_snapshot_update`` -> ``stake_snapshot``
  {snapshot_hashes, freeze_window_stable, rotation_consistent, cross_node_consistent}
- ``recompute_leadership_schedule``  -> ``leadership_schedule``
  {deterministic_match, expected_schedule_hash, recomputed_schedule_hash}
- ``trigger_rupd_pulse``             -> ``reward_calculation``
  {boundary_invariant_holds, expected_total_rewards, observed_total_rewards}

Nothing is forced or simulated. On the self-composed testnet_42 devnets epochs are short, so
the probe OBSERVES the next natural epoch boundary on every node and measures each node by its
own method:

- cardano-node: the node image's own ``cardano-cli`` local-state queries, run inside the node
  container against the node socket (tip, stake-snapshot, ledger-state + utxo, leadership-schedule),
  with the network parameters read from the devnet's own shelley genesis (``/env``).
- Amaru: its JSON trace lines (``tip.update`` with epoch/slot/slot_in_epoch, ``snapshot`` and
  ``pots.dump`` lines). Where Amaru does not expose a quantity, the per-node value is ``None``
  with a reason, and the section does not pass.

The assertions read these sections from the producer step's own result (not from
``observation_overrides``, which F133 refuses for these keys).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import time
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

# Amaru log lines (stdout of the amaru container / host log file).
_SNAPSHOT_RE = re.compile(
    r"snapshot accounts=(\d+) dreps=(\d+) pools=(\d+) active_stake=(\d+) "
    r"pools_voting_stake=(\d+) dreps_voting_stake=(\d+) epoch=(\d+)"
)
_POTS_RE = re.compile(r"pots\.dump treasury=(\d+) reserves=(\d+) fees=(\d+) donations=(\d+)")

SECTION_BY_MODE = {
    "force_epoch_boundary": "epoch_boundary",
    "simulate_stake_snapshot_update": "stake_snapshot",
    "recompute_leadership_schedule": "leadership_schedule",
    "trigger_rupd_pulse": "reward_calculation",
}

# Test seams: subprocess runner, wall clock, sleep.
_run_cmd = subprocess.run
_now = time.time
_sleep = time.sleep

ENV_ROOT = "/env"


# --------------------------------------------------------------------------------------
# Runtime metadata helpers
# --------------------------------------------------------------------------------------


def _load_metadata(runtime_metadata_path: Path) -> dict:
    return json.loads(runtime_metadata_path.read_text(encoding="utf-8"))


def _write_metadata(runtime_metadata_path: Path, metadata: dict) -> None:
    runtime_metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _node_id(node: dict) -> str:
    return str(node.get("id") or node.get("name") or "")


def _impl(node: dict) -> str:
    return str(node.get("impl") or node.get("implementation") or "cardano-node")


def all_nodes(metadata: dict) -> list[dict]:
    """Every node of the substrate, deduplicated by id, in metadata order."""
    seen: dict[str, dict] = {}
    for key in ("nodes", "haskell_nodes", "amaru_nodes"):
        for node in metadata.get(key) or []:
            if isinstance(node, dict) and _node_id(node) and _node_id(node) not in seen:
                seen[_node_id(node)] = node
    return list(seen.values())


def _cardano_nodes(nodes: list[dict]) -> list[dict]:
    return [node for node in nodes if _impl(node) == "cardano-node" and node.get("container_name")]


def _amaru_nodes(nodes: list[dict]) -> list[dict]:
    return [node for node in nodes if _impl(node) == "amaru"]


def _network_magic(metadata: dict) -> int:
    for key in ("network_magic", "testnet_magic", "networkMagic"):
        if metadata.get(key) is not None:
            return int(metadata[key])
    return 42


def _utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _digest(body) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# --------------------------------------------------------------------------------------
# cardano-node: the node image's own cardano-cli, inside the node container
# --------------------------------------------------------------------------------------


def _docker_exec(node: dict, argv: list[str], timeout: float = 90) -> tuple[int, str, str]:
    try:
        proc = _run_cmd(
            ["docker", "exec", str(node["container_name"]), *argv],
            capture_output=True, text=True, check=False, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout}s: {' '.join(argv[:4])}"
    except OSError as exc:
        return 127, "", f"docker exec unavailable: {exc}"
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _socket(node: dict) -> str:
    return str(node.get("container_socket_path") or f"{ENV_ROOT}/socket/{_node_id(node)}/sock")


def cardano_query(node: dict, magic: int, name: str, extra: list[str] | tuple = (), *,
                  json_flag: bool = True, timeout: float = 90) -> tuple[object | None, str | None]:
    """Run ``cardano-cli <era> query <name>`` in the node container; return (parsed JSON, error).

    Version-agnostic: tries the ``latest`` era group then the top-level ``query`` group, with and
    without ``--output-json`` (flags differ across cardano-cli releases)."""
    tail = [*extra, "--socket-path", _socket(node), "--testnet-magic", str(magic)]
    errors = []
    for prefix in (["latest", "query"], ["query"]):
        for flags in ((["--output-json"], []) if json_flag else ([],)):
            rc, out, err = _docker_exec(node, ["cardano-cli", *prefix, name, *tail, *flags], timeout=timeout)
            if rc == 0:
                try:
                    return json.loads(out), None
                except ValueError:
                    errors.append(f"{' '.join(prefix)} {name}: non-JSON output")
                    continue
            errors.append(f"{' '.join(prefix)} {name}{' ' + ' '.join(flags) if flags else ''}: "
                          f"{(err or out).strip()[:300] or f'exit {rc}'}")
    return None, "; ".join(errors)


def cardano_tip(node: dict, magic: int) -> dict:
    body, error = cardano_query(node, magic, "tip", json_flag=False, timeout=30)
    t = _now()
    if not isinstance(body, dict) or body.get("slot") is None:
        return {"ok": False, "t": t, "observed_at": _utc(t), "error": error or "tip without slot"}
    return {
        "ok": True, "t": t, "observed_at": _utc(t),
        "slot": int(body["slot"]), "epoch": int(body["epoch"]) if body.get("epoch") is not None else None,
        "hash": str(body.get("hash") or ""), "block": body.get("block"),
        "slot_in_epoch": body.get("slotInEpoch"), "slots_to_epoch_end": body.get("slotsToEpochEnd"),
    }


def _container_file(node: dict, path: str) -> tuple[str | None, str | None]:
    rc, out, err = _docker_exec(node, ["cat", path], timeout=30)
    if rc != 0:
        return None, (err or out).strip()[:300] or f"cat {path} exit {rc}"
    return out, None


def read_genesis(nodes: list[dict]) -> tuple[dict | None, str | None]:
    """Network parameters from the devnet's own shelley genesis (first readable cardano node)."""
    errors = []
    for node in _cardano_nodes(nodes):
        text, error = _container_file(node, f"{ENV_ROOT}/shelley-genesis.json")
        if text is None:
            errors.append(f"{_node_id(node)}: {error}")
            continue
        try:
            body = json.loads(text)
        except ValueError as exc:
            errors.append(f"{_node_id(node)}: shelley-genesis.json not JSON ({exc})")
            continue
        system_start = body.get("systemStart")
        start_unix = None
        if system_start:
            start_unix = datetime.fromisoformat(str(system_start).replace("Z", "+00:00")).timestamp()
        config_text, _ = _container_file(node, f"{ENV_ROOT}/configuration.yaml")
        shelley_at = None
        if config_text:
            match = re.search(r'"?TestShelleyHardForkAtEpoch"?\s*:\s*(\d+)', config_text)
            shelley_at = int(match.group(1)) if match else None
        return {
            "source": f"{_node_id(node)}:{ENV_ROOT}/shelley-genesis.json",
            "epoch_length": int(body["epochLength"]),
            "slot_length": float(body["slotLength"]),
            "active_slots_coeff": float(body.get("activeSlotsCoeff") or 0) or None,
            "security_param": body.get("securityParam"),
            "system_start": system_start,
            "system_start_unix": start_unix,
            "max_lovelace_supply": int(body["maxLovelaceSupply"]) if body.get("maxLovelaceSupply") is not None else None,
            "shelley_hard_fork_at_epoch": shelley_at,
        }, None
    if not errors:
        errors.append("no cardano-node container in runtime metadata")
    return None, "; ".join(errors)


def _expected_epoch_start(epoch: int, genesis: dict) -> int | None:
    """First slot of ``epoch`` per genesis. Only defined when Shelley starts at epoch 0
    (no Byron prefix with a different epoch length), which the devnet config states."""
    if genesis.get("shelley_hard_fork_at_epoch") != 0:
        return None
    return epoch * int(genesis["epoch_length"])


# ouroboros-consensus defaultClockSkew = clockSkewInSeconds 2 (MiniProtocol/ChainSync/Client/InFutureCheck.hs):
# a node accepts a header up to 2 s ahead of its own clock, so a first block that far ahead of the
# controller's clock is still on time.
CONSENSUS_CLOCK_SKEW_SECONDS = 2.0


def _clock_skew_slots(genesis: dict) -> int:
    return int(math.ceil(CONSENSUS_CLOCK_SKEW_SECONDS / float(genesis["slot_length"])))


def _window_end(t: float, genesis: dict) -> int | None:
    """Last slot a first post-boundary block may carry: wall-clock slot plus the consensus clock skew."""
    slot = _wallclock_slot(t, genesis)
    return None if slot is None else slot + _clock_skew_slots(genesis)


def _wallclock_slot(t: float, genesis: dict) -> int | None:
    start = genesis.get("system_start_unix")
    if start is None:
        return None
    return int(math.ceil((t - start) / float(genesis["slot_length"])))


# --------------------------------------------------------------------------------------
# Amaru: JSON trace lines
# --------------------------------------------------------------------------------------


def _amaru_log(node: dict, config: dict) -> tuple[str | None, str | None]:
    log_path = node.get("log_path") or config.get("amaru_log")
    if log_path and Path(str(log_path)).exists():
        try:
            return Path(str(log_path)).read_text(errors="replace"), None
        except OSError as exc:
            return None, f"amaru log unreadable: {exc}"
    if node.get("container_name"):
        try:
            proc = _run_cmd(["docker", "logs", "--tail", "200000", str(node["container_name"])],
                            capture_output=True, text=True, check=False, timeout=60)
        except subprocess.TimeoutExpired:
            return None, "docker logs timed out"
        except OSError as exc:
            return None, f"docker logs unavailable: {exc}"
        text = (proc.stdout or "") + (proc.stderr or "")
        if text.strip():
            return text, None
        return None, f"docker logs returned no output (rc={proc.returncode})"
    return None, "amaru node has neither a readable log_path nor a container_name"


def _ts(raw) -> float | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def amaru_tip_updates(log_text: str) -> list[dict]:
    """Adopted tips from Amaru's ``tip.update`` trace (epoch, slot, slot_in_epoch, hash)."""
    updates = []
    for line in log_text.splitlines():
        line = line.strip()
        if "tip.update" not in line or not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        fields = obj.get("fields") or {}
        if fields.get("message") != "tip.update" or fields.get("slot") is None or fields.get("epoch") is None:
            continue
        updates.append({
            "t": _ts(obj.get("timestamp")), "observed_at": obj.get("timestamp"),
            "slot": int(fields["slot"]), "epoch": int(fields["epoch"]),
            "slot_in_epoch": fields.get("slot_in_epoch"), "hash": fields.get("header_hash"),
            "block": fields.get("block_height"),
        })
    return updates


# --------------------------------------------------------------------------------------
# Boundary watch (shared by the modes that need a boundary)
# --------------------------------------------------------------------------------------


def _budget(config: dict, genesis: dict | None) -> float:
    budget = float(config.get("time_budget_seconds") or 150)
    if genesis:
        epoch_seconds = genesis["epoch_length"] * genesis["slot_length"]
        budget = min(budget, epoch_seconds * 1.5 + 30)
    return max(budget, 1.0)


def _watch_boundary(cardano: list[dict], magic: int, config: dict, genesis: dict | None, sample_fn=None,
                    min_window_samples: int = 1, need_both_sides: bool = False) -> dict:
    """Sample every cardano node until each has crossed into the epoch after its first sample and
    holds ``min_window_samples`` samples inside one epoch (before or after the boundary).

    ``sample_fn(node, tip)`` (optional) takes a mode measurement at each tip sample; its
    result is stored with the sample. Returns {node_id: [samples]} plus watch metadata."""
    interval = float(config.get("sample_interval_seconds") or 2.0)
    deadline = _now() + _budget(config, genesis)
    samples: dict[str, list[dict]] = {_node_id(node): [] for node in cardano}
    start_epoch: dict[str, int | None] = {_node_id(node): None for node in cardano}
    done: dict[str, bool] = {_node_id(node): False for node in cardano}
    while True:
        for node in cardano:
            nid = _node_id(node)
            if done[nid]:
                continue
            tip = cardano_tip(node, magic)
            if tip["ok"] and sample_fn is not None:
                tip["measurement"] = sample_fn(node, tip)
            samples[nid].append(tip)
            if tip["ok"] and tip.get("epoch") is not None:
                if start_epoch[nid] is None:
                    start_epoch[nid] = tip["epoch"]
                crossed = tip["epoch"] > start_epoch[nid] or done[nid]
                usable = [s for s in samples[nid] if s.get("ok") and s.get("epoch") is not None
                          and (sample_fn is None or (s.get("measurement") or {}).get("ok"))]
                pre = sum(1 for s in usable if s["epoch"] == start_epoch[nid])
                post = sum(1 for s in usable if s["epoch"] == start_epoch[nid] + 1)
                if crossed and need_both_sides and pre == 0:
                    # No usable sample before this boundary: watch the next one instead.
                    start_epoch[nid] = tip["epoch"]
                    continue
                done[nid] = crossed and post >= 1 and (pre >= min_window_samples or post >= min_window_samples)
        if all(done.values()) or _now() >= deadline:
            break
        _sleep(interval)
    return {"samples": samples, "start_epoch": start_epoch, "crossed": done,
            "budget_seconds": round(_budget(config, genesis), 3), "sample_interval_seconds": interval}


def _pre_post(samples: list[dict], start_epoch: int | None) -> tuple[dict | None, dict | None]:
    ok = [s for s in samples if s.get("ok") and s.get("epoch") is not None]
    pre = [s for s in ok if s["epoch"] == start_epoch]
    post = [s for s in ok if start_epoch is not None and s["epoch"] > start_epoch]
    return (pre[-1] if pre else None), (post[0] if post else None)


def _strip(sample: dict | None) -> dict | None:
    if sample is None:
        return None
    return {k: v for k, v in sample.items() if k not in ("t", "measurement")}


def _choose_target(per_node: dict, config: dict) -> str | None:
    target = str(config.get("target_node") or "")
    if target in per_node:
        return target
    return next(iter(per_node), None)


# --------------------------------------------------------------------------------------
# Mode: epoch boundary timing
# --------------------------------------------------------------------------------------


def _cardano_epoch_boundary(node: dict, samples: list[dict], start_epoch, genesis: dict) -> dict:
    pre, post = _pre_post(samples, start_epoch)
    body = {
        "implementation": "cardano-node",
        "source": "cardano-cli query tip in the node container; window from shelley genesis "
                  "(epochLength, slotLength, systemStart)",
        "sample_count": len(samples),
        "pre_boundary_sample": _strip(pre), "post_boundary_sample": _strip(post),
    }
    if post is None or pre is None:
        failures = [s.get("error") for s in samples if not s.get("ok")]
        body.update({
            "measured": False, "observed_boundary_slot": None,
            "expected_window_start": None, "expected_window_end": None, "within_bounds": None,
            "reason": ("no epoch boundary observed within the time budget" if not failures
                       else f"tip queries failed: {failures[-1]}"),
        })
        return body
    boundary_epoch = int(post["epoch"])
    window_start = _expected_epoch_start(boundary_epoch, genesis)
    window_end = _window_end(post["t"], genesis)
    epoch_length = int(genesis["epoch_length"])
    # The node's own epoch arithmetic must agree with genesis on every sample.
    epoch_math = [
        s["epoch"] == s["slot"] // epoch_length
        and (s.get("slot_in_epoch") is None or int(s["slot_in_epoch"]) == s["slot"] % epoch_length)
        for s in samples if s.get("ok") and s.get("epoch") is not None
    ] if genesis.get("shelley_hard_fork_at_epoch") == 0 else []
    body.update({
        "measured": window_start is not None and window_end is not None,
        "boundary_epoch": boundary_epoch,
        "observed_boundary_slot": int(post["slot"]),
        "expected_window_start": window_start,
        "expected_window_end": window_end,
        "window_end_basis": "wall-clock slot at the post-boundary sample (controller clock vs genesis systemStart) plus the consensus clock skew (2 s)",
        "clock_skew_allowance_slots": _clock_skew_slots(genesis),
        "epoch_math_consistent": all(epoch_math) if epoch_math else None,
    })
    if window_start is None:
        body["reason"] = "genesis epoch start undefined: configuration does not put Shelley at epoch 0"
    elif window_end is None:
        body["reason"] = "shelley genesis has no systemStart"
    body["within_bounds"] = (
        body["measured"] and int(pre["slot"]) < window_start <= int(post["slot"]) <= window_end
        and body["epoch_math_consistent"] is not False
    )
    return body


def _amaru_epoch_boundary(node: dict, config: dict, genesis: dict | None, watch_started: float) -> dict:
    body = {"implementation": "amaru",
            "source": "amaru tip.update trace (epoch, slot, slot_in_epoch); window from the devnet shelley genesis"}
    # Amaru follows the cardano producers; give its trace a short grace period after the watch.
    grace_deadline = _now() + float(config.get("amaru_grace_seconds", 20))
    while True:
        log_text, error = _amaru_log(node, config)
        if log_text is None:
            body.update({"measured": False, "observed_boundary_slot": None, "within_bounds": None, "reason": error})
            return body
        updates = [u for u in amaru_tip_updates(log_text) if u["t"] is None or u["t"] >= watch_started - 1]
        post = pre = None
        for previous, current in zip(updates, updates[1:]):
            if current["epoch"] > previous["epoch"]:
                pre, post = previous, current
                break
        if post is not None or _now() >= grace_deadline:
            break
        _sleep(float(config.get("sample_interval_seconds") or 2.0))
    body.update({"pre_boundary_sample": _strip(pre), "post_boundary_sample": _strip(post),
                 "tip_updates_in_window": len(updates)})
    if post is None:
        body.update({"measured": False, "observed_boundary_slot": None, "within_bounds": None,
                     "reason": "no epoch change in amaru tip.update lines during the watch"})
        return body
    if genesis is None:
        body.update({"measured": False, "observed_boundary_slot": int(post["slot"]), "within_bounds": None,
                     "reason": "no cardano-node genesis on this substrate to give the expected window"})
        return body
    window_start = _expected_epoch_start(int(post["epoch"]), genesis)
    window_end = _window_end(post["t"], genesis) if post.get("t") is not None else None
    reported_start = (int(post["slot"]) - int(post["slot_in_epoch"])) if post.get("slot_in_epoch") is not None else None
    body.update({
        "measured": window_start is not None and window_end is not None,
        "boundary_epoch": int(post["epoch"]),
        "observed_boundary_slot": int(post["slot"]),
        "expected_window_start": window_start, "expected_window_end": window_end,
        "window_end_basis": "wall-clock slot at the amaru trace timestamp of the first post-boundary tip plus the consensus clock skew (2 s)",
        "clock_skew_allowance_slots": _clock_skew_slots(genesis),
        "amaru_reported_epoch_start": reported_start,
        "epoch_math_consistent": (reported_start == window_start) if reported_start is not None else None,
    })
    body["within_bounds"] = (
        body["measured"] and int(pre["slot"]) < window_start <= int(post["slot"]) <= window_end
        and body["epoch_math_consistent"] is not False
    )
    return body


def measure_epoch_boundary(metadata: dict, config: dict) -> dict:
    nodes = all_nodes(metadata)
    magic = _network_magic(metadata)
    genesis, genesis_error = read_genesis(nodes)
    cardano = _cardano_nodes(nodes)
    started = _now()
    per_node: dict[str, dict] = {}
    watch = None
    if cardano and genesis is not None:
        watch = _watch_boundary(cardano, magic, config, genesis)
        for node in cardano:
            nid = _node_id(node)
            per_node[nid] = _cardano_epoch_boundary(node, watch["samples"][nid], watch["start_epoch"][nid], genesis)
    elif cardano:
        for node in cardano:
            per_node[_node_id(node)] = {"implementation": "cardano-node", "measured": False,
                                        "observed_boundary_slot": None, "within_bounds": None,
                                        "reason": f"shelley genesis unreadable: {genesis_error}"}
    amaru = _amaru_nodes(nodes)
    for node in amaru:
        per_node[_node_id(node)] = _amaru_epoch_boundary(node, config, genesis, started)
    return _epoch_boundary_section(per_node, config, genesis, genesis_error, watch)


def _epoch_boundary_section(per_node, config, genesis, genesis_error, watch) -> dict:
    target = _choose_target(per_node, config)
    top = per_node.get(target) or {}
    section = {
        "source": "observed natural epoch boundary (not forced); per-node method in per_node[*].source",
        "measured": bool(per_node) and all(body.get("measured") for body in per_node.values()),
        "target_node": target,
        "requested_target_slot": config.get("target_slot"),
        "observed_boundary_slot": top.get("observed_boundary_slot"),
        "expected_window_start": top.get("expected_window_start"),
        "expected_window_end": top.get("expected_window_end"),
        "all_nodes_within_bounds": bool(per_node) and all(body.get("within_bounds") is True for body in per_node.values()),
        "genesis": genesis,
        "per_node": per_node,
    }
    if genesis is not None and config.get("target_slot") is not None and genesis.get("shelley_hard_fork_at_epoch") == 0:
        section["requested_target_slot_is_epoch_start"] = int(config["target_slot"]) % int(genesis["epoch_length"]) == 0
    if watch is not None:
        section["watch"] = {k: watch[k] for k in ("start_epoch", "crossed", "budget_seconds", "sample_interval_seconds")}
    if not per_node:
        section["reason"] = f"no measurable node in runtime metadata ({genesis_error or 'no nodes'})"
    return section


# --------------------------------------------------------------------------------------
# Mode: stake snapshot freeze
# --------------------------------------------------------------------------------------


def _snapshot_rotation(pre: dict, post: dict) -> bool | None:
    """At a boundary the ledger rotates go <- set <- mark. Check post.set == pre.mark and
    post.go == pre.set for the totals and every pool present before the boundary."""
    def table(body: dict) -> dict:
        rows = {}
        if isinstance(body.get("total"), dict):
            rows["__total__"] = body["total"]
        for pool, row in (body.get("pools") or {}).items():
            if isinstance(row, dict):
                rows[pool] = row
        return rows

    before, after = table(pre), table(post)
    if not before or not after:
        return None
    for key, row in before.items():
        if "stakeMark" not in row or "stakeSet" not in row:
            return None
        nxt = after.get(key) or {}
        if nxt.get("stakeSet") != row["stakeMark"] or nxt.get("stakeGo") != row["stakeSet"]:
            return False
    return True


def _cardano_stake_sample(magic: int):
    def sample(node: dict, tip: dict) -> dict:
        body, error = cardano_query(node, magic, "stake-snapshot", ["--all-stake-pools"])
        after = cardano_tip(node, magic)
        if body is None:
            return {"ok": False, "error": error}
        if not after["ok"] or after.get("epoch") != tip.get("epoch"):
            return {"ok": False, "error": "epoch changed during the query; sample discarded"}
        return {"ok": True, "hash": _digest(body), "body": body}
    return sample


def _cardano_stake_snapshot(samples: list[dict], start_epoch) -> dict:
    by_epoch: dict[int, list[str]] = {}
    bodies: dict[int, list[dict]] = {}
    errors = []
    for s in samples:
        m = s.get("measurement") or {}
        if not m.get("ok"):
            if m.get("error"):
                errors.append(m["error"])
            continue
        by_epoch.setdefault(int(s["epoch"]), []).append(m["hash"])
        bodies.setdefault(int(s["epoch"]), []).append(m["body"])
    body = {"implementation": "cardano-node",
            "source": "cardano-cli query stake-snapshot --all-stake-pools (mark/set/go) in the node container; "
                      "sha256 of the canonical JSON per sample",
            "hashes_by_epoch": {str(k): v for k, v in sorted(by_epoch.items())}}
    # The freeze window is the epoch before the boundary, or the one after it when the watch
    # started too close to the boundary to sample the earlier epoch twice.
    window_epoch = start_epoch if len(by_epoch.get(start_epoch) or []) >= 2 else (
        start_epoch + 1 if start_epoch is not None and len(by_epoch.get(start_epoch + 1) or []) >= 2 else start_epoch)
    window = by_epoch.get(window_epoch) or []
    if len(window) < 2:
        body.update({"measured": False, "snapshot_hashes": window, "freeze_window_stable": None,
                     "reason": (errors[-1] if errors and not window
                                else "fewer than two snapshot samples inside one epoch")})
        return body
    stable = all(len(set(hashes)) == 1 for hashes in by_epoch.values())
    later = [e for e in by_epoch if e > start_epoch]
    rotation = (_snapshot_rotation(bodies[start_epoch][-1], bodies[min(later)][0])
                if later and start_epoch in bodies else None)
    body.update({
        "measured": True,
        "freeze_window_epoch": window_epoch,
        "snapshot_hashes": window,
        "within_epoch_stable": stable,
        "boundary_crossed": bool(later),
        "rotation_consistent": rotation,
        "freeze_window_stable": stable and rotation is not False,
    })
    return body


def _amaru_stake_snapshot(node: dict, config: dict) -> dict:
    body = {"implementation": "amaru", "source": "amaru 'snapshot accounts=.. epoch=N' log lines; sha256 per line"}
    log_text, error = _amaru_log(node, config)
    if log_text is None:
        body.update({"measured": False, "snapshot_hashes": [], "freeze_window_stable": None, "reason": error})
        return body
    by_epoch: dict[int, list[str]] = {}
    for match in _SNAPSHOT_RE.findall(log_text):
        fields = [int(v) for v in match]
        by_epoch.setdefault(fields[6], []).append(_digest(fields))
    if not by_epoch:
        body.update({"measured": False, "snapshot_hashes": [], "freeze_window_stable": None,
                     "reason": "no stake-snapshot lines in the amaru log"})
        return body
    window_epoch = max(by_epoch)
    body.update({
        "measured": True, "freeze_window_epoch": window_epoch,
        "hashes_by_epoch": {str(k): v for k, v in sorted(by_epoch.items())},
        "snapshot_hashes": by_epoch[window_epoch],
        "freeze_window_stable": all(len(set(v)) == 1 for v in by_epoch.values()),
    })
    return body


def measure_stake_snapshot(metadata: dict, config: dict) -> dict:
    nodes = all_nodes(metadata)
    magic = _network_magic(metadata)
    genesis, genesis_error = read_genesis(nodes)
    cardano = _cardano_nodes(nodes)
    per_node: dict[str, dict] = {}
    watch = None
    if cardano:
        watch = _watch_boundary(cardano, magic, config, genesis, sample_fn=_cardano_stake_sample(magic),
                                min_window_samples=2)
        for node in cardano:
            nid = _node_id(node)
            per_node[nid] = _cardano_stake_snapshot(watch["samples"][nid], watch["start_epoch"][nid])
    for node in _amaru_nodes(nodes):
        per_node[_node_id(node)] = _amaru_stake_snapshot(node, config)
    target = _choose_target(per_node, config)
    top = per_node.get(target) or {}
    # Same epoch, same snapshot on every node (each node's own ledger view).
    by_epoch: dict[str, set] = {}
    for body in per_node.values():
        if body.get("implementation") == "cardano-node":
            for epoch, hashes in (body.get("hashes_by_epoch") or {}).items():
                by_epoch.setdefault(epoch, set()).update(hashes)
    cross_node = all(len(v) == 1 for v in by_epoch.values()) if by_epoch else None
    all_stable = bool(per_node) and all(body.get("freeze_window_stable") is True for body in per_node.values())
    section = {
        "source": "per-node stake snapshot sampled across an observed epoch; method in per_node[*].source",
        "measured": bool(per_node) and all(body.get("measured") for body in per_node.values()),
        "target_node": target,
        "snapshot_hashes": top.get("snapshot_hashes") or [],
        "freeze_window_stable": bool(all_stable and cross_node is not False),
        "cross_node_consistent": cross_node,
        "rotation_consistent": top.get("rotation_consistent"),
        "per_node": per_node,
    }
    if watch is not None:
        section["watch"] = {k: watch[k] for k in ("start_epoch", "crossed", "budget_seconds", "sample_interval_seconds")}
    if not per_node:
        section["reason"] = f"no measurable node ({genesis_error or 'no nodes'})"
    return section


# --------------------------------------------------------------------------------------
# Mode: leadership schedule recomputation
# --------------------------------------------------------------------------------------


def _pool_dir(node: dict) -> str:
    index = node.get("host_slot_index") or node.get("slot_index")
    if index is None:
        match = re.search(r"(\d+)$", _node_id(node))
        index = match.group(1) if match else "1"
    return f"{ENV_ROOT}/pools-keys/pool{index}"


def _pool_id(node: dict) -> tuple[str | None, str | None]:
    pool_dir = _pool_dir(node)
    errors = []
    for prefix in (["latest"], []):
        for flags in (["--output-format", "hex"], []):
            rc, out, err = _docker_exec(node, ["cardano-cli", *prefix, "stake-pool", "id",
                                               "--cold-verification-key-file", f"{pool_dir}/cold.vkey", *flags], timeout=30)
            if rc == 0 and out.strip():
                return out.strip().splitlines()[-1].strip(), None
            errors.append((err or out).strip()[:200] or f"exit {rc}")
    return None, f"pool id from {pool_dir}/cold.vkey failed: {errors[-1]}"


def _schedule(node: dict, magic: int, pool_id: str, key_node: dict) -> tuple[dict | None, str | None]:
    pool_dir = _pool_dir(key_node)
    body, error = cardano_query(node, magic, "leadership-schedule", [
        "--genesis", f"{ENV_ROOT}/shelley-genesis.json", "--stake-pool-id", pool_id,
        "--vrf-signing-key-file", f"{pool_dir}/vrf.skey", "--current",
    ], timeout=120)
    if not isinstance(body, list):
        return None, error or "leadership-schedule did not return a JSON list"
    slots = sorted(int(row["slotNumber"]) for row in body if isinstance(row, dict) and row.get("slotNumber") is not None)
    return {"slots": slots, "hash": _digest(slots), "leader_slot_count": len(slots)}, None


def _cardano_leadership(target: dict, cardano: list[dict], magic: int) -> dict:
    body = {"implementation": "cardano-node",
            "source": "cardano-cli query leadership-schedule --current (pool cold/vrf keys from the devnet env), "
                      "computed twice on the target node and once on every other cardano node"}
    pool_id, error = _pool_id(target)
    if pool_id is None:
        body.update({"measured": False, "deterministic_match": None, "reason": error})
        return body
    for _attempt in range(2):
        epoch_before = cardano_tip(target, magic).get("epoch")
        first, error = _schedule(target, magic, pool_id, target)
        second, error2 = _schedule(target, magic, pool_id, target) if first else (None, None)
        others = {}
        for node in cardano:
            if _node_id(node) != _node_id(target):
                result, other_error = _schedule(node, magic, pool_id, target)
                others[_node_id(node)] = result or {"error": other_error}
        epoch_after = cardano_tip(target, magic).get("epoch")
        if epoch_before == epoch_after:
            break
    if first is None or second is None:
        body.update({"measured": False, "deterministic_match": None, "pool_id": pool_id,
                     "reason": error or error2})
        return body
    other_hashes = {nid: r.get("hash") for nid, r in others.items()}
    if not first["slots"]:
        # Two empty lists always "match"; an empty schedule is nothing to compare.
        body.update({"measured": False, "deterministic_match": None, "pool_id": pool_id, "epoch": epoch_after,
                     "leader_slot_count": 0,
                     "reason": f"empty leader schedule for pool {pool_id} in epoch {epoch_after} "
                               "(the 'set' stake snapshot used for the current epoch may still be empty)"})
        return body
    body.update({
        "measured": True, "pool_id": pool_id, "epoch": epoch_after,
        "expected_schedule_hash": first["hash"], "recomputed_schedule_hash": second["hash"],
        "leader_slot_count": first["leader_slot_count"], "leader_slots": first["slots"],
        "cross_node_schedule_hashes": other_hashes,
        "deterministic_match": first["hash"] == second["hash"]
        and all(h == first["hash"] for h in other_hashes.values()),
    })
    if epoch_before != epoch_after:
        body["note"] = "epoch changed during both attempts; schedules may span two epochs"
    return body


def _amaru_leadership(node: dict, config: dict, cardano: list[dict], magic: int) -> dict:
    """Amaru forges nothing and exposes no schedule. Honest equivalent: every block Amaru adopted in
    the current epoch sits in a slot where some devnet pool is an elected leader, per the schedules
    recomputed independently with each pool's own VRF key (Amaru's header validation checks the VRF)."""
    body = {"implementation": "amaru",
            "source": "amaru tip.update slots in the current epoch vs the union of every devnet pool's "
                      "leadership-schedule recomputed via cardano-node LSQ"}
    if not cardano:
        body.update({"measured": False, "deterministic_match": None,
                     "reason": "no cardano-node on this substrate to recompute the leader schedule independently; "
                               "amaru exposes no leadership schedule"})
        return body
    reference = cardano[0]
    union: set[int] = set()
    epoch = cardano_tip(reference, magic).get("epoch")
    for key_node in cardano:
        pool_id, error = _pool_id(key_node)
        if pool_id is None:
            body.update({"measured": False, "deterministic_match": None, "reason": error})
            return body
        result, error = _schedule(reference, magic, pool_id, key_node)
        if result is None:
            body.update({"measured": False, "deterministic_match": None, "reason": error})
            return body
        union.update(result["slots"])
    log_text, error = _amaru_log(node, config)
    if log_text is None:
        body.update({"measured": False, "deterministic_match": None, "reason": error})
        return body
    adopted = sorted({u["slot"] for u in amaru_tip_updates(log_text) if u["epoch"] == epoch})
    if not adopted:
        body.update({"measured": False, "deterministic_match": None,
                     "reason": f"amaru adopted no block in epoch {epoch} (tip.update)"})
        return body
    outside = [slot for slot in adopted if slot not in union]
    body.update({
        "measured": True, "epoch": epoch, "adopted_block_slots": adopted,
        "expected_schedule_hash": _digest(adopted), "recomputed_schedule_hash": _digest(sorted(set(adopted) & union)),
        "adopted_outside_schedule": outside, "deterministic_match": not outside,
    })
    return body


def measure_leadership_schedule(metadata: dict, config: dict) -> dict:
    nodes = all_nodes(metadata)
    magic = _network_magic(metadata)
    cardano = _cardano_nodes(nodes)
    per_node: dict[str, dict] = {}
    target_id = str(config.get("target_node") or "")
    target = next((n for n in cardano if _node_id(n) == target_id), cardano[0] if cardano else None)
    if target is not None:
        per_node[_node_id(target)] = _cardano_leadership(target, cardano, magic)
    for node in _amaru_nodes(nodes):
        per_node[_node_id(node)] = _amaru_leadership(node, config, cardano, magic)
    chosen = _choose_target(per_node, config)
    top = per_node.get(chosen) or {}
    section = {
        "source": "leader schedule recomputed from each pool's VRF key; method in per_node[*].source",
        "measured": bool(per_node) and all(body.get("measured") for body in per_node.values()),
        "target_node": chosen,
        "expected_schedule_hash": top.get("expected_schedule_hash"),
        "recomputed_schedule_hash": top.get("recomputed_schedule_hash"),
        "deterministic_match": bool(per_node) and all(body.get("deterministic_match") is True for body in per_node.values()),
        "per_node": per_node,
    }
    if not per_node:
        section["reason"] = "no node in runtime metadata"
    return section


# --------------------------------------------------------------------------------------
# Mode: reward calculation / ADA conservation across the boundary
# --------------------------------------------------------------------------------------


def _find_dicts(value, predicate, found=None):
    found = [] if found is None else found
    if isinstance(value, dict):
        if predicate(value):
            found.append(value)
        for item in value.values():
            _find_dicts(item, predicate, found)
    elif isinstance(value, list):
        for item in value:
            _find_dicts(item, predicate, found)
    return found


def _first_key(body: dict, *names):
    for name in names:
        if name in body:
            return body[name]
    return None


def _lovelace(output) -> int | None:
    if not isinstance(output, dict):
        return None
    value = _first_key(output, "value", "amount")
    if isinstance(value, int):
        return value
    if isinstance(value, dict) and isinstance(value.get("lovelace"), int):
        return value["lovelace"]
    return None


def utxo_total(utxo) -> int | None:
    if not isinstance(utxo, dict):
        return None
    total = 0
    for output in utxo.values():
        amount = _lovelace(output)
        if amount is None:
            return None
        total += amount
    return total


def extract_pots(ledger_state) -> tuple[dict | None, str | None]:
    """reserves/treasury/deposits/fees/donations/rewards from ``query ledger-state`` JSON.

    Tolerant of field-name drift across ledger releases; every pot must be found exactly once
    (otherwise None and a reason, never a guess)."""
    accounts = _find_dicts(ledger_state, lambda d: (("reserves" in d and "treasury" in d)
                                                   or ("casReserves" in d and "casTreasury" in d)))
    utxo_states = _find_dicts(ledger_state, lambda d: "deposited" in d and "fees" in d)
    dstates = [d["dstate"] for d in _find_dicts(ledger_state, lambda d: isinstance(d.get("dstate"), dict))]
    if len(accounts) != 1 or len(utxo_states) != 1 or len(dstates) != 1:
        return None, (f"ledger-state shape not recognised: account-state={len(accounts)} "
                      f"utxo-state={len(utxo_states)} dstate={len(dstates)}")
    account, utxo_state, dstate = accounts[0], utxo_states[0], dstates[0]
    rewards = 0
    reward_fields = 0

    def walk(value):
        nonlocal rewards, reward_fields
        if isinstance(value, dict):
            # One account record: newer ledgers repeat the same lovelace as balance and reward.
            # Count the record once, from the first field present.
            own = next((k for k in ("balance", "rdReward", "reward") if isinstance(value.get(k), int)), None)
            if own is not None:
                rewards += value[own]
                reward_fields += 1
            for key, item in value.items():
                if key in ("rdReward", "reward", "balance") and isinstance(item, int):
                    continue
                elif key == "rewards" and isinstance(item, dict) and all(isinstance(v, int) for v in item.values()):
                    rewards += sum(item.values())
                    reward_fields += len(item)
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(dstate)
    pots = {
        "reserves": int(_first_key(account, "reserves", "casReserves")),
        "treasury": int(_first_key(account, "treasury", "casTreasury")),
        "deposits": int(utxo_state["deposited"]),
        "fees": int(utxo_state["fees"]),
        "donations": int(utxo_state.get("donation") or 0),
        "rewards": rewards,
        "reward_account_fields": reward_fields,
    }
    utxo_in_state = utxo_total(utxo_state.get("utxo"))
    if utxo_in_state is not None:
        pots["utxo"] = utxo_in_state
        pots["utxo_source"] = "ledger-state utxoState.utxo"
    return pots, None


def _cardano_pots_sample(magic: int):
    def sample(node: dict, tip: dict) -> dict:
        state, error = cardano_query(node, magic, "ledger-state", timeout=120)
        if state is None:
            return {"ok": False, "error": error}
        pots, error = extract_pots(state)
        if pots is None:
            return {"ok": False, "error": error, "top_level_keys": sorted(state)[:20] if isinstance(state, dict) else None}
        if "utxo" not in pots:
            utxo, error = cardano_query(node, magic, "utxo", ["--whole-utxo"], timeout=120)
            total = utxo_total(utxo)
            if total is None:
                return {"ok": False, "error": error or "utxo values not recognised"}
            pots["utxo"] = total
            pots["utxo_source"] = "cardano-cli query utxo --whole-utxo"
        after = cardano_tip(node, magic)
        # The pots and the UTxO must come from one ledger state: no block in between.
        if not after["ok"] or after.get("hash") != tip.get("hash"):
            return {"ok": False, "error": "tip moved during the pot queries; sample discarded"}
        return {"ok": True, "pots": pots}
    return sample


_CONSERVED = ("reserves", "treasury", "utxo", "deposits", "fees", "donations", "rewards")


def _cardano_reward(samples: list[dict], start_epoch, genesis: dict | None) -> dict:
    body = {"implementation": "cardano-node",
            "source": "cardano-cli query ledger-state (+ utxo) in the node container, sampled on both sides of "
                      "the boundary; ADA conservation against shelley-genesis maxLovelaceSupply"}
    good = [s for s in samples if (s.get("measurement") or {}).get("ok")]
    pre = [s for s in good if s["epoch"] == start_epoch]
    post = [s for s in good if start_epoch is not None and s["epoch"] > start_epoch]
    errors = [(s.get("measurement") or {}).get("error") for s in samples if (s.get("measurement") or {}).get("error")]
    if not pre or not post or genesis is None or genesis.get("max_lovelace_supply") is None:
        body.update({"measured": False, "boundary_invariant_holds": None,
                     "expected_total_rewards": None, "observed_total_rewards": None,
                     "reason": ("shelley genesis maxLovelaceSupply unavailable" if genesis is None
                                or genesis.get("max_lovelace_supply") is None
                                else errors[-1] if errors and (not pre or not post)
                                else "no pot sample on both sides of an observed boundary")})
        return body
    before, after = pre[-1]["measurement"]["pots"], post[0]["measurement"]["pots"]
    supply = int(genesis["max_lovelace_supply"])
    total_before = sum(before[k] for k in _CONSERVED)
    total_after = sum(after[k] for k in _CONSERVED)
    # Rewards the pots say left reserves/fees/treasury/utxo/deposits into reward accounts...
    expected_rewards = -sum(after[k] - before[k] for k in _CONSERVED if k != "rewards")
    # ...versus the measured change of the reward-account balances.
    observed_rewards = after["rewards"] - before["rewards"]
    body.update({
        "measured": True,
        "boundary_epoch": post[0]["epoch"],
        "pots_before": before, "pots_after": after,
        "pre_boundary_slot": pre[-1]["slot"], "post_boundary_slot": post[0]["slot"],
        "max_lovelace_supply": supply,
        "total_before": total_before, "total_after": total_after,
        "conserved_before": total_before == supply, "conserved_after": total_after == supply,
        "expected_total_rewards": expected_rewards,
        "observed_total_rewards": observed_rewards,
        "boundary_invariant_holds": total_before == supply and total_after == supply
        and expected_rewards == observed_rewards,
    })
    return body


def _amaru_reward(node: dict, config: dict) -> dict:
    body = {"implementation": "amaru", "source": "amaru pots.dump log lines (treasury, reserves, fees, donations)"}
    log_text, error = _amaru_log(node, config)
    pots = _POTS_RE.findall(log_text or "")
    body["pots_samples"] = [
        {"treasury": int(t), "reserves": int(r), "fees": int(f), "donations": int(d)} for t, r, f, d in pots[-4:]
    ]
    body.update({
        "measured": False, "boundary_invariant_holds": None,
        "expected_total_rewards": None, "observed_total_rewards": None,
        "reason": error if log_text is None else (
            "amaru logs treasury/reserves/fees/donations but exposes neither the UTxO total nor reward-account "
            "balances, so ADA conservation across the boundary is not computable from amaru"),
    })
    return body


def measure_reward_calculation(metadata: dict, config: dict) -> dict:
    nodes = all_nodes(metadata)
    magic = _network_magic(metadata)
    genesis, genesis_error = read_genesis(nodes)
    cardano = _cardano_nodes(nodes)
    per_node: dict[str, dict] = {}
    if cardano:
        watch = _watch_boundary(cardano, magic, config, genesis, sample_fn=_cardano_pots_sample(magic),
                                need_both_sides=True)
        for node in cardano:
            nid = _node_id(node)
            per_node[nid] = _cardano_reward(watch["samples"][nid], watch["start_epoch"][nid], genesis)
            if genesis is None:
                per_node[nid]["reason"] = f"shelley genesis unreadable: {genesis_error}"
    for node in _amaru_nodes(nodes):
        per_node[_node_id(node)] = _amaru_reward(node, config)
    target = _choose_target(per_node, config)
    top = per_node.get(target) or {}
    return {
        "source": "ADA conservation (reserves+treasury+utxo+deposits+fees+donations+rewards = maxLovelaceSupply) "
                  "before and after an observed boundary; method in per_node[*].source",
        "measured": bool(per_node) and all(body.get("measured") for body in per_node.values()),
        "target_node": target,
        "expected_total_rewards": top.get("expected_total_rewards"),
        "observed_total_rewards": top.get("observed_total_rewards"),
        "boundary_invariant_holds": bool(per_node) and all(body.get("boundary_invariant_holds") is True
                                                           for body in per_node.values()),
        "per_node": per_node,
    }


# --------------------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------------------

_MEASURE = {
    "force_epoch_boundary": measure_epoch_boundary,
    "simulate_stake_snapshot_update": measure_stake_snapshot,
    "recompute_leadership_schedule": measure_leadership_schedule,
    "trigger_rupd_pulse": measure_reward_calculation,
}


def _library_fallback(mode: str) -> dict:
    return {
        "measured": False,
        "mode_scope": "library-fallback",
        "note": f"{mode}: no runtime metadata (library mode) — no real observation performed",
    }


def apply_epoch_boundary_mode(*, metadata: dict, mode: str, config: dict) -> dict:
    if mode not in _MEASURE:
        raise ValueError(f"unsupported epoch-boundary mode: {mode}")
    section = _MEASURE[mode](metadata, config)
    # Measured evidence lives in this step's result; observation_overrides are left untouched.
    return {"result": {SECTION_BY_MODE[mode]: section},
            "observation_overrides": dict(metadata.get("observation_overrides") or {})}


def run_epoch_boundary_probe(*, runtime_metadata_path: Path, output_dir: Path, mode: str, config: dict) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = _load_metadata(runtime_metadata_path)
    updated = apply_epoch_boundary_mode(metadata=metadata, mode=mode, config=config)
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
    parser.add_argument("--mode", required=True, choices=sorted(_MEASURE))
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if "runtime_metadata_path" not in config:
        # library mode: labeled fallback, no fabricated success
        out = Path(config.get("output_dir", "."))
        out.mkdir(parents=True, exist_ok=True)
        report = {"mode": args.mode, "result": {SECTION_BY_MODE[args.mode]: _library_fallback(args.mode)}}
        (out / "result.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"mode={args.mode} library-fallback", flush=True)
        return 0
    report = run_epoch_boundary_probe(
        runtime_metadata_path=Path(config["runtime_metadata_path"]),
        output_dir=Path(config["output_dir"]),
        mode=args.mode,
        config=config,
    )
    section = next(iter(report["result"].values()))
    print(f"mode={report['mode']} target_node={report['target_node']} measured={section.get('measured')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
