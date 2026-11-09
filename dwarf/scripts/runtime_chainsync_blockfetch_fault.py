from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_byzantine_peer import (  # noqa: E402
    _ensure_tmux_session_absent,
    _kill_tmux_session,
    _launch_proxy_session,
    _pick_free_port,
    replace_peer_address,
)
from runtime_compose_substrate import (  # noqa: E402
    _is_public_network,
    _launch_haskell_node,
    _rewrite_haskell_topology,
)
from runtime_substrate_common import run_command, wait_for_nodes_healthy, write_json  # noqa: E402


MODE_DEFAULTS: dict[str, dict[str, object]] = {
    "chainsync_parent_discontinuity": {
        # F142: replay an earlier genuinely signed header: slot back, height not up, parent mismatch.
        "mutation_mode": "replay_earlier",
        "mutation_direction": "inbound",
        "mutation_protocol": "2",
        "mutation_message": "roll_forward",
        "mutate_after_segments": 3,
    },
    "chainsync_nonincrementing_height": {
        # F142: replay an earlier genuinely signed header: slot back, height not up, parent mismatch.
        "mutation_mode": "replay_earlier",
        "mutation_direction": "inbound",
        "mutation_protocol": "2",
        "mutation_message": "roll_forward",
        "mutate_after_segments": 3,
    },
    "chainsync_nonmonotonic_slot": {
        # F142: replay an earlier genuinely signed header: slot back, height not up, parent mismatch.
        "mutation_mode": "replay_earlier",
        "mutation_direction": "inbound",
        "mutation_protocol": "2",
        "mutation_message": "roll_forward",
        "mutate_after_segments": 3,
    },
    "chainsync_responder_fork_switch": {
        # F144: after 2 followed headers, roll the follower back 2 headers and replay them for real.
        "mutation_mode": "chainsync_fork_switch_once",
        "mutation_direction": "inbound",
        "mutation_protocol": "2",
        "mutate_after_segments": 2,
    },
    "praos_header_invalid_signature": {
        # F144: flip the last byte (KES signature) of the 2nd genuine header served to the target.
        "mutation_mode": "corrupt_header_signature",
        "mutation_direction": "inbound",
        "mutation_protocol": "2",
        "mutation_message": "roll_forward",
        "mutate_after_segments": 2,
    },
    "blockfetch_invalid_range": {
        # F142: corrupt the point inside the outgoing MsgRequestRange (once); measure what the upstream serves.
        "mutation_mode": "flip_once",
        "mutation_direction": "outbound",
        "mutation_protocol": "3",
        "mutation_message": "request_range",
        "mutate_after_segments": 1,
    },
    "blockfetch_range_pressure": {
        "mutation_mode": "pass_through",
        "mutation_direction": "both",
        "mutation_protocol": "any",
        "mutate_after_segments": 999999,
    },
    "blockfetch_invalid_block_cbor": {
        # F130: corrupt the blocks the upstream serves (MsgBlock only), not the first segment of any protocol.
        "mutation_mode": "flip_payload_byte",
        "mutation_direction": "inbound",
        "mutation_protocol": "3",
        "mutation_message": "block",
        "mutate_after_segments": 1,
    },
    "blockfetch_range_mismatch": {
        # F142: serve an earlier genuine block in place of the requested one.
        "mutation_mode": "replay_earlier",
        "mutation_direction": "inbound",
        "mutation_protocol": "3",
        "mutation_message": "block",
        "mutate_after_segments": 2,
    },
    "blockfetch_continuity_failure": {
        # F142: serve an earlier genuine block in place of the requested one.
        "mutation_mode": "replay_earlier",
        "mutation_direction": "inbound",
        "mutation_protocol": "3",
        "mutation_message": "block",
        "mutate_after_segments": 2,
    },
}


def load_runtime_metadata(path: Path) -> tuple[Path, dict]:
    body = json.loads(path.read_text(encoding="utf-8"))
    runtime_metadata_path = body.get("runtime_metadata_path")
    if runtime_metadata_path:
        resolved = Path(str(runtime_metadata_path))
        return resolved, json.loads(resolved.read_text(encoding="utf-8"))
    return path, body


def _write_metadata(path: Path, metadata: dict) -> None:
    write_json(path, metadata)


def _find_node(metadata: dict, node_id: str) -> dict:
    for node in list(metadata.get("haskell_nodes") or []) + list(metadata.get("amaru_nodes") or []) + list(
        metadata.get("nodes") or []
    ):
        if node.get("id") == node_id or node.get("name") == node_id:
            return node
    raise ValueError(f"unknown substrate node: {node_id}")


def _other_cardano_peers(metadata: dict, *, target_node_id: str) -> list[dict]:
    peers = []
    for node in list(metadata.get("haskell_nodes") or []) + list(metadata.get("nodes") or []):
        if (node.get("impl") or node.get("implementation")) != "cardano-node":
            continue
        node_name = str(node.get("id") or node.get("name") or "")
        if node_name == target_node_id:
            continue
        peers.append(node)
    return peers


def _docker_network_gateway(network_name: str) -> str:
    inspect = run_command(["docker", "network", "inspect", network_name])
    if inspect.returncode != 0:
        raise RuntimeError(f"docker network inspect failed for {network_name}: {inspect.stderr or inspect.stdout}")
    body = json.loads(inspect.stdout)
    configs = (((body[0] if body else {}) or {}).get("IPAM") or {}).get("Config") or []
    for config in configs:
        gateway = str((config or {}).get("Gateway") or "").strip()
        if gateway:
            return gateway
    raise RuntimeError(f"docker network {network_name} did not report a gateway address")


def _proxy_bind_address(network_name: str) -> str:
    """Address the byzantine proxy binds and advertises on the substrate network.

    On the host the proxy listens on the Docker bridge gateway, which the containers reach.
    Inside the controller (attached to the network, F66) the gateway is not ours to bind: the
    controller's own address on that network is, and the nodes reach it directly.
    """
    inspect = run_command(["docker", "network", "inspect", network_name])
    if inspect.returncode != 0:
        raise RuntimeError(f"docker network inspect failed for {network_name}: {inspect.stderr or inspect.stdout}")
    body = json.loads(inspect.stdout)
    network = (body[0] if body else {}) or {}
    own_id = str(os.environ.get("HOSTNAME") or "").strip()
    if own_id:
        for container_id, record in (network.get("Containers") or {}).items():
            if str(container_id).startswith(own_id) or own_id.startswith(str(container_id)[:12]):
                address = str((record or {}).get("IPv4Address") or "").split("/", 1)[0].strip()
                if address:
                    return address
    for config in (network.get("IPAM") or {}).get("Config") or []:
        gateway = str((config or {}).get("Gateway") or "").strip()
        if gateway:
            return gateway
    raise RuntimeError(f"docker network {network_name} did not report a gateway address")


def resolve_fault_routing(
    *,
    metadata: dict,
    target_node: dict,
    target_node_id: str,
    upstream_node_id: str | None,
    proxy_port: int,
) -> dict:
    compose_mode = str(metadata.get("compose_mode", "host"))
    original_peer_addresses = list(target_node.get("peer_addresses") or [])
    if compose_mode == "docker":
        network_name = str(target_node.get("container_network") or f"{metadata['compose_project']}-net")
        gateway = _proxy_bind_address(network_name)
        container_peer_addresses = list(target_node.get("container_peer_addresses") or [])
        if upstream_node_id:
            upstream_node = _find_node(metadata, upstream_node_id)
            selected_upstream = str(upstream_node.get("container_listen_address") or upstream_node["listen_address"])
        elif container_peer_addresses:
            selected_upstream = str(container_peer_addresses[0])
        else:
            fallback_peers = _other_cardano_peers(metadata, target_node_id=target_node_id)
            if not fallback_peers:
                raise ValueError(f"no upstream peer address available for {target_node_id}")
            selected_upstream = str(fallback_peers[0].get("container_listen_address") or fallback_peers[0]["listen_address"])
        replacement_peer_address = f"{gateway}:{proxy_port}"
        return {
            "selected_upstream": selected_upstream,
            "proxy_listen_address": replacement_peer_address,
            "replacement_peer_address": replacement_peer_address,
            "access_points": [{"address": gateway, "port": proxy_port}],
            "restart_mode": "docker",
            "original_peer_addresses": original_peer_addresses,
        }
    if upstream_node_id:
        selected_upstream = str(_find_node(metadata, upstream_node_id)["listen_address"])
    elif original_peer_addresses:
        selected_upstream = str(original_peer_addresses[0])
    else:
        fallback_peers = _other_cardano_peers(metadata, target_node_id=target_node_id)
        if not fallback_peers:
            raise ValueError(f"no upstream peer address available for {target_node_id}")
        selected_upstream = str(fallback_peers[0]["listen_address"])
        original_peer_addresses = [selected_upstream]
    proxy_listen_address = f"127.0.0.1:{proxy_port}"
    return {
        "selected_upstream": selected_upstream,
        "proxy_listen_address": proxy_listen_address,
        "replacement_peer_address": proxy_listen_address,
        "access_points": None,
        "restart_mode": "host",
        "original_peer_addresses": original_peer_addresses,
    }


def restart_target_node(*, metadata: dict, target_node: dict, runtime_root: Path) -> None:
    compose_mode = str(metadata.get("compose_mode", "host"))
    if compose_mode == "docker":
        container_name = str(target_node.get("container_name") or "").strip()
        if not container_name:
            raise RuntimeError(f"docker compose target {target_node.get('id')} is missing container_name")
        result = run_command(["docker", "restart", container_name])
        if result.returncode != 0:
            raise RuntimeError(f"docker restart failed for {container_name}: {result.stderr or result.stdout}")
        return
    _kill_tmux_session(str(target_node["session"]))
    _ensure_tmux_session_absent(str(target_node["session"]))
    _launch_haskell_node(
        target_node,
        runtime_root=runtime_root,
        binary_path=str(target_node["resolved_binary"]),
        public_network=_is_public_network(str(metadata["network"])),
    )


def move_db_aside(db_dir: Path, *, label: str) -> Path:
    """Move a stopped node's chain DB aside (kept for evidence) so it re-syncs from genesis (F144)."""
    db_dir = Path(db_dir)
    moved = db_dir.with_name(f"{db_dir.name}.before-{label}-{int(time.time())}")
    db_dir.rename(moved)
    db_dir.mkdir(parents=True, exist_ok=True)
    return moved


def query_target_tip(target_node: dict, *, network_magic) -> dict | None:
    """The target's own tip via cardano-cli in its container (the observation helper); None if unknown."""
    from runtime_multi_node_observation import _query_tip_once  # noqa: PLC0415 - controller-only import

    container = str(target_node.get("container_name") or "")
    socket_path = str(target_node.get("container_socket_path") or "")
    if not container or not socket_path or network_magic is None:
        return None
    try:
        return _query_tip_once(cardano_cli="cardano-cli", socket_path=Path(str(target_node.get("socket_path") or "")),
                               network_magic=int(network_magic), container_name=container,
                               container_socket_path=socket_path)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def resync_target_from_empty_db(*, metadata: dict, target_node: dict, runtime_root: Path) -> dict:
    """Restart the target with an empty chain DB so it must fetch the whole chain through the proxy.

    That is the real BlockFetch range pressure: long ranges served to a syncing node (F144). Docker
    substrates only; on failure the plain restart happens and the reason is recorded."""
    tip_before = query_target_tip(target_node, network_magic=metadata.get("network_magic"))
    container = str(target_node.get("container_name") or "").strip()
    db_dir = str(target_node.get("db_dir") or "").strip()
    report = {"requested": True, "tip_before": tip_before, "db_dir": db_dir or None, "moved_to": None,
              "error": None}
    if str(metadata.get("compose_mode", "host")) != "docker" or not container or not db_dir:
        report["error"] = "resync needs a docker substrate node with container_name and db_dir"
        restart_target_node(metadata=metadata, target_node=target_node, runtime_root=runtime_root)
        return report
    stop = run_command(["docker", "stop", container])
    if stop.returncode != 0:
        report["error"] = f"docker stop failed: {(stop.stderr or stop.stdout or '').strip()[-300:]}"
        restart_target_node(metadata=metadata, target_node=target_node, runtime_root=runtime_root)
        return report
    try:
        report["moved_to"] = str(move_db_aside(Path(db_dir), label="range-pressure"))
    except OSError as exc:
        report["error"] = f"could not move the chain DB aside: {type(exc).__name__}: {exc}"
    start = run_command(["docker", "start", container])
    if start.returncode != 0:
        raise RuntimeError(f"docker start failed for {container}: {start.stderr or start.stdout}")
    return report


def collect_container_log_lines(target_node: dict, *, since_epoch: float) -> list[str] | None:
    """The target container's log lines since ``since_epoch`` (None when it cannot be read)."""
    container = str(target_node.get("container_name") or "").strip()
    if not container:
        return None
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(max(0.0, since_epoch - 5)))
    proc = run_command(["docker", "logs", "--since", since, container])
    if proc.returncode != 0:
        return None
    return (str(proc.stdout or "") + "\n" + str(proc.stderr or "")).splitlines()


def _load_proxy_stats(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def access_points_with_proxy(template: dict, *, selected_upstream: str, proxy_point: dict) -> list[dict]:
    """The target's local-root access points with only the chosen upstream swapped for the proxy (F131)."""
    host, _, port = str(selected_upstream).rpartition(":")
    kept = []
    for group in template.get("localRoots") or []:
        for point in (group or {}).get("accessPoints") or []:
            if str(point.get("address")) == host and str(point.get("port")) == port:
                continue
            if point not in kept:
                kept.append(dict(point))
    return [*kept, dict(proxy_point)]


def peer_addresses_with_proxy(peer_addresses: list[str], *, selected_upstream: str, replacement: str) -> list[str]:
    """The target's peer list with only the chosen upstream swapped for the proxy (F131)."""
    kept = [address for address in peer_addresses if address != selected_upstream]
    return [*kept, replacement]


REPLAY_MODES = ("chainsync_parent_discontinuity", "chainsync_nonincrementing_height", "chainsync_nonmonotonic_slot",
                "blockfetch_range_mismatch", "blockfetch_continuity_failure")


def _first_replay(stats: dict, kind: str) -> dict | None:
    return next((r for r in (stats.get("replays") or []) if r.get("message_kind") == kind), None)


def peer_drop_evidence(stats: dict) -> dict:
    """Did the target node drop a connection that carried an injected message? (F130)

    The proxy records each connection: its injected segments and which side closed it.
    "downstream" is the target node; "upstream" or "proxy" closures are not the target rejecting.
    """
    mutated = [c for c in (stats.get("connections") or []) if int(c.get("mutated_segments", 0) or 0) >= 1]
    closed = [c for c in mutated if c.get("closed_by")]
    dropped = [c for c in closed if c.get("closed_by") == "downstream"]
    first = (dropped or closed or mutated or [None])[0]
    return {
        "mutated_connection_seen": bool(mutated),
        "dropped_by_target": bool(dropped),
        "closed_by": (first or {}).get("closed_by"),
        "drop_seconds": (dropped[0].get("drop_seconds") if dropped else None),
        "mutated_segments": int(stats.get("mutated_segments", 0) or 0)
        or sum(int(c.get("mutated_segments", 0) or 0) for c in mutated),
    }


# F144: modes whose measurement needs time after the first evidence (seconds the proxy must be quiet
# / the follower must keep going) and a floor on how long the step waits for the injection.
MODE_SETTLE_SECONDS = {"blockfetch_range_pressure": 6.0}
FORK_SWITCH_FOLLOW_SECONDS = 3.0
MODE_MIN_ACTIVITY_SECONDS = {
    "chainsync_responder_fork_switch": 120.0,
    "blockfetch_range_pressure": 120.0,
    "praos_header_invalid_signature": 120.0,
}


def _connection(stats: dict, connection_id) -> dict:
    return next((c for c in (stats.get("connections") or []) if c.get("id") == connection_id), {})


def proxy_activity_ready(*, mode: str, stats: dict) -> bool:
    if mode == "chainsync_responder_fork_switch":
        # F144: the injected rollback was followed (the follower asked past the displaced header and
        # kept going for a moment) or its connection closed.
        switch = next(iter(stats.get("fork_switches") or []), None)
        if switch is None:
            return False
        if _connection(stats, switch.get("connection_id")).get("closed_by"):
            return True
        delivered = switch.get("displaced_delivered_at")
        return bool(delivered and int(switch.get("requests_after_displaced", 0) or 0) >= 1
                    and time.time() - float(delivered) >= FORK_SWITCH_FOLLOW_SECONDS)
    if mode == "praos_header_invalid_signature":
        corruption = next(iter(stats.get("header_corruptions") or []), None)
        return bool(corruption and _connection(stats, corruption.get("connection_id")).get("closed_by"))
    if mode in REPLAY_MODES:
        # F142: ready once the replay happened and its connection was closed.
        return bool(stats.get("replays")) and any(
            int(c.get("mutated_segments", 0) or 0) >= 1 and c.get("closed_by") for c in (stats.get("connections") or []))
    if mode == "blockfetch_invalid_range":
        return any(c.get("range_request_corrupted") and (c.get("range_response") or c.get("closed_by"))
                   for c in (stats.get("connections") or []))
    if mode == "blockfetch_invalid_block_cbor":
        # F130: the measurement is a corrupting connection being closed, not the first segment.
        return any(int(c.get("mutated_segments", 0) or 0) >= 1 and c.get("closed_by")
                   for c in (stats.get("connections") or []))
    if mode == "blockfetch_range_pressure":
        # F127: the range request alone is not the measurement; wait for a block reply.
        # F144: and for one whole batch (StartBatch .. BatchDone) measured on the wire.
        return int(stats.get("blocks_fetched", 0) or 0) >= 1 and bool(stats.get("blockfetch_batches"))
    return int(stats.get("intercepted_segments", 0) or 0) >= 1


def _wait_for_proxy_stats(path: Path, *, timeout_seconds: float) -> dict:
    return _wait_for_mode_proxy_stats(path, mode="generic", timeout_seconds=timeout_seconds)


def _wait_for_mode_proxy_stats(path: Path, *, mode: str, timeout_seconds: float) -> dict:
    deadline = time.time() + timeout_seconds
    latest = {}
    settle = MODE_SETTLE_SECONDS.get(mode, 0.0)
    quiet_since = None
    last_blocks = None
    while time.time() < deadline:
        latest = _load_proxy_stats(path)
        if proxy_activity_ready(mode=mode, stats=latest):
            if not settle:
                return latest
            # F144: keep reading until no block has passed for ``settle`` seconds (the sync is done).
            blocks = int(latest.get("blocks_fetched", 0) or 0)
            if blocks != last_blocks:
                last_blocks, quiet_since = blocks, time.time()
            elif time.time() - quiet_since >= settle:
                return latest
        time.sleep(0.5)
    return latest


def summarize_proxy_events(path: Path) -> dict:
    summary = {
        "chainsync_messages_observed": 0,
        "chainsync_roll_backward_count": 0,
        "chainsync_roll_forward_count": 0,
        "rollback_then_forward_count": 0,
    }
    if not path.is_file():
        return summary
    pending_rollbacks = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        body = json.loads(raw)
        message_kind = body.get("message_kind")
        if not message_kind:
            continue
        summary["chainsync_messages_observed"] += 1
        if message_kind == "roll_backward":
            summary["chainsync_roll_backward_count"] += 1
            if body.get("direction") == "inbound":
                pending_rollbacks += 1
        elif message_kind == "roll_forward":
            summary["chainsync_roll_forward_count"] += 1
            if body.get("direction") == "inbound" and pending_rollbacks > 0:
                summary["rollback_then_forward_count"] += 1
                pending_rollbacks -= 1
    return summary


def _chainsync_replay_result(stats: dict, target_healthy: bool) -> dict:
    """F142: one replayed earlier header realises all three ChainSync violations; values are measured."""
    replay, evidence = _first_replay(stats, "roll_forward"), peer_drop_evidence(stats)
    dropped = bool(replay) and evidence["dropped_by_target"]
    return {
        "rejected": bool(target_healthy and dropped),
        "observed_slot_delta": (replay or {}).get("slot_delta"),
        "observed_height_delta": (replay or {}).get("height_delta"),
        "parent_mismatch": (replay or {}).get("parent_mismatch"),
        "candidate_chain_advanced": (False if dropped else None),
        **evidence,
        "replay": replay,
        "violation_crafted": replay is not None,
        "mutation": "replay_earlier_signed_header",
    }


def _fork_switch_result(stats: dict, target_healthy: bool) -> dict:
    """F144: the injected rollback-then-forward, judged by what the follower did with it.

    The follower followed when it asked for more after the displaced header (it took the rollback,
    the replayed genuine headers and the displaced one). Session-start rollbacks are not counted."""
    switch = next(iter(stats.get("fork_switches") or []), None)
    conn = _connection(stats, (switch or {}).get("connection_id")) if switch else {}
    dropped = conn.get("closed_by") == "downstream"
    followed = bool(switch and switch.get("rollback_delivered_at") and switch.get("displaced_delivered_at")
                    and int(switch.get("requests_after_displaced", 0) or 0) >= 1)
    depth = int((switch or {}).get("rollback_depth", 0) or 0)
    rewritten = bool(followed and int(switch.get("requests_after_rollback", 0) or 0) >= depth + 1
                     and int(switch.get("replays_delivered", 0) or 0) == depth)
    count = 1 if followed else 0
    return {
        "rollback_then_forward_sequence_observed": bool(target_healthy and count >= 1),
        "follower_state_rewritten": bool(target_healthy and rewritten),
        "rollback_then_forward_count": count,
        "chainsync_messages_observed": int(stats.get("chainsync_messages_observed", 0) or 0),
        "chainsync_roll_backward_count": int(stats.get("chainsync_roll_backward_count", 0) or 0),
        "chainsync_roll_forward_count": int(stats.get("chainsync_roll_forward_count", 0) or 0),
        "rollback_depth": depth or None,
        "rollback_point": (switch or {}).get("rollback_point"),
        "follower_dropped_connection": dropped if switch else None,
        "drop_seconds": conn.get("drop_seconds") if dropped else None,
        "fork_switch": switch,
        "violation_crafted": switch is not None,
        "mutation": "rollback_to_earlier_point_then_replay_genuine_headers",
    }


def _range_pressure_result(stats: dict, target_healthy: bool, configured_limit: int, resync: dict | None) -> dict:
    """F144: the peak is the most blocks served in one StartBatch..BatchDone on the proxied connection."""
    batches = list(stats.get("blockfetch_batches") or [])
    peak = stats.get("peak_blocks_per_batch")
    peak = int(peak) if peak is not None and batches else None
    return {
        "resource_bound_ok": bool(target_healthy and peak is not None and 1 <= peak <= int(configured_limit)),
        "observed_peak_blocks_per_batch": peak,
        "configured_limit": configured_limit,
        "configured_limit_source": "scenario parameter (cardano-node does not expose its in-flight block limit)",
        "batches_completed": len(batches),
        "batch_block_counts": [int(b.get("blocks", 0) or 0) for b in batches][-64:],
        "peak_outstanding_range_requests": stats.get("peak_outstanding_range_requests"),
        "blocks_fetched": int(stats.get("blocks_fetched", 0) or 0),
        "block_range_requests_observed": int(stats.get("block_range_requests_observed", 0) or 0),
        "resync": resync,
        "measure": "blocks between StartBatch and BatchDone per range request, read on the proxied connection",
    }


HEADER_ERROR_REASONS = (
    "InvalidKesSignatureOCERT", "InvalidSignatureOCERT", "VRFKeyBadProof", "VRFLeaderValueTooBig",
    "VRFKeyUnknown", "VRFKeyWrongVRFKey", "KESBeforeStartOCERT", "KESAfterEndOCERT", "CounterTooSmallOCERT",
    "CounterOverIncrementedOCERT", "NoCounterForKeyHashOCERT", "HeaderProtVerTooHigh", "InvalidKesSignature",
)


def scan_header_rejection(lines, *, corrupted_hash: str) -> dict:
    """What the target's own log says about the corrupted header (F144).

    ``matched``: a line names the corrupted header hash together with a header-validation error;
    ``adopted``: a chain-selection line made it the tip (the boundary was crossed)."""
    matched_lines, reason, adopted = [], None, False
    for line in lines:
        text = str(line)
        if corrupted_hash not in text:
            continue
        if "AddedToCurrentChain" in text or "SwitchedToAFork" in text:
            adopted = True
            continue
        if "HeaderError" in text or any(r in text for r in HEADER_ERROR_REASONS) or "InvalidBlock" in text:
            matched_lines.append(text[-2000:])
            reason = reason or next((r for r in HEADER_ERROR_REASONS if r in text), None)
    return {"matched": bool(matched_lines), "lines": matched_lines[:5], "reason": reason, "adopted": adopted}


def _praos_header_result(stats: dict, target_healthy: bool, log_evidence: dict | None) -> dict:
    corruption = next(iter(stats.get("header_corruptions") or []), None)
    conn = _connection(stats, (corruption or {}).get("connection_id")) if corruption else {}
    dropped = conn.get("closed_by") == "downstream"
    log = log_evidence or {}
    rejected = bool(target_healthy and corruption and dropped and not log.get("adopted"))
    return {
        "header_rejected": rejected,
        # Rejected at header validation: the target never asked for the block behind the header
        # and never selected it.
        "assertion_boundary_preserved": bool(rejected and not corruption.get("block_requested")
                                             and not log.get("adopted")),
        "rejection_reason": log.get("reason"),
        "rejection_logged": bool(log.get("matched")) if log_evidence is not None else None,
        "rejection_log_lines": log.get("lines") or [],
        "dropped_by_target": dropped if corruption else None,
        "drop_seconds": conn.get("drop_seconds") if dropped else None,
        "block_requested_for_corrupted_header": (corruption or {}).get("block_requested"),
        "corrupted_header_hash": (corruption or {}).get("corrupted_header_hash"),
        "original_header_hash": (corruption or {}).get("original_header_hash"),
        "corrupted_slot": (corruption or {}).get("slot"),
        "corrupted_field": (corruption or {}).get("corrupted_field"),
        "violation_crafted": corruption is not None,
        "mutation": "flip_last_byte_of_genuine_header_kes_signature",
    }


def build_result_body(
    *,
    mode: str,
    stats: dict,
    target_healthy: bool,
    configured_limit: int = 64,
    resync: dict | None = None,
    log_evidence: dict | None = None,
) -> dict:
    if mode == "chainsync_parent_discontinuity":
        return _chainsync_replay_result(stats, target_healthy)
    if mode == "chainsync_nonincrementing_height":
        return _chainsync_replay_result(stats, target_healthy)
    if mode == "chainsync_nonmonotonic_slot":
        return _chainsync_replay_result(stats, target_healthy)
    if mode == "chainsync_responder_fork_switch":
        return _fork_switch_result(stats, target_healthy)
    if mode == "praos_header_invalid_signature":
        return _praos_header_result(stats, target_healthy, log_evidence)
    if mode == "blockfetch_invalid_range":
        conn = next((c for c in (stats.get("connections") or []) if c.get("range_request_corrupted")), None)
        answer = (conn or {}).get("range_response") or ((conn or {}).get("closed_by") and "closed")
        served = int((conn or {}).get("blocks_after_bad_range", 0) or 0) if conn else None
        return {
            "invalid_range_rejected": bool(target_healthy and conn and answer in ("no_blocks", "closed") and not served),
            "served_blocks": served,
            "upstream_answer": answer,
            "violation_crafted": conn is not None,
            "mutation": "flip_last_byte_of_range_request",
        }
    if mode == "blockfetch_range_pressure":
        return _range_pressure_result(stats, target_healthy, configured_limit, resync)
    if mode == "blockfetch_invalid_block_cbor":
        evidence = peer_drop_evidence(stats)
        return {
            "invalid_block_rejected": bool(target_healthy and evidence["dropped_by_target"]),
            **evidence,
            "mutation": "flip_last_payload_byte",
            "mutated_message": "block",
            "decode_path": "corrupted_msgblock_from_upstream",
        }
    if mode == "blockfetch_range_mismatch":
        replay, evidence = _first_replay(stats, "block"), peer_drop_evidence(stats)
        return {
            "range_mismatch_rejected": bool(target_healthy and replay and evidence["dropped_by_target"]),
            "accepted_mismatched_range": (not evidence["dropped_by_target"]) if replay else None,
            **evidence, "replay": replay, "violation_crafted": replay is not None, "mutation": "replay_earlier_block",
        }
    if mode == "blockfetch_continuity_failure":
        replay, evidence = _first_replay(stats, "block"), peer_drop_evidence(stats)
        return {
            "continuity_failure_rejected": bool(target_healthy and replay and evidence["dropped_by_target"]),
            "downstream_state_advanced": (not evidence["dropped_by_target"]) if replay else None,
            **evidence, "replay": replay, "violation_crafted": replay is not None, "mutation": "replay_earlier_block",
        }
    raise ValueError(f"unsupported mode: {mode}")


def apply_mode(
    *,
    runtime_metadata_path: Path,
    output_dir: Path,
    mode: str,
    target_node_id: str,
    upstream_node_id: str | None,
    healthy_timeout_seconds: float,
    activity_timeout_seconds: float,
    configured_limit: int,
    resync_from_empty_db: bool = True,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    runtime_metadata_path, metadata = load_runtime_metadata(runtime_metadata_path)
    runtime_root = Path(metadata["runtime_root"])
    target_node = _find_node(metadata, target_node_id)
    if (target_node.get("impl") or target_node.get("implementation")) != "cardano-node":
        raise ValueError(f"{mode} currently supports cardano-node targets only")

    proxy_port = _pick_free_port()
    routing = resolve_fault_routing(
        metadata=metadata,
        target_node=target_node,
        target_node_id=target_node_id,
        upstream_node_id=upstream_node_id,
        proxy_port=proxy_port,
    )
    selected_upstream = str(routing["selected_upstream"])
    proxy_listen_address = str(routing["proxy_listen_address"])
    replacement_peer_address = str(routing["replacement_peer_address"])
    original_peer_addresses = list(routing["original_peer_addresses"])
    proxy_session = f"{metadata['compose_project']}-{mode}-{target_node_id}"
    proxy_output_dir = output_dir / "proxy"
    defaults = MODE_DEFAULTS[mode]
    proxy_info = _launch_proxy_session(
        session=proxy_session,
        runtime_root=runtime_root,
        listen_address=proxy_listen_address,
        upstream_address=selected_upstream,
        output_dir=proxy_output_dir,
        mutation_mode=str(defaults["mutation_mode"]),
        mutation_direction=str(defaults["mutation_direction"]),
        mutation_protocol=str(defaults["mutation_protocol"]),
        mutate_after_segments=int(defaults["mutate_after_segments"]),
        mutation_message=defaults.get("mutation_message"),
    )

    if selected_upstream in original_peer_addresses:
        updated_peer_addresses = replace_peer_address(
            original_peer_addresses,
            original=selected_upstream,
            replacement=replacement_peer_address,
        )
    else:
        updated_peer_addresses = [replacement_peer_address, *original_peer_addresses]
    target_node["peer_addresses"] = updated_peer_addresses
    topology_path = Path(str(target_node["topology_path"]))
    topology_template = json.loads(topology_path.read_text(encoding="utf-8"))
    access_points = routing["access_points"]
    if str(routing["restart_mode"]) == "docker":
        # F131: swap only the chosen upstream for the proxy; the target keeps its other peers.
        target_node["container_peer_addresses"] = peer_addresses_with_proxy(
            list(target_node.get("container_peer_addresses") or []),
            selected_upstream=selected_upstream, replacement=replacement_peer_address)
        access_points = access_points_with_proxy(
            topology_template, selected_upstream=selected_upstream, proxy_point=routing["access_points"][0])
    _rewrite_haskell_topology(
        node=target_node,
        topo_path=topology_path,
        template=topology_template,
        access_points=access_points,
    )
    resync = None
    restarted_at = time.time()
    if mode == "blockfetch_range_pressure" and resync_from_empty_db:
        # F144: a node at the tip fetches one block at a time; an empty DB makes it fetch the chain.
        resync = resync_target_from_empty_db(metadata=metadata, target_node=target_node, runtime_root=runtime_root)
    else:
        restart_target_node(metadata=metadata, target_node=target_node, runtime_root=runtime_root)

    health = wait_for_nodes_healthy([target_node], timeout_seconds=healthy_timeout_seconds)
    target_healthy = bool(health and health[0].get("healthy"))
    stats = _wait_for_mode_proxy_stats(
        Path(proxy_info["stats_path"]),
        mode=mode,
        timeout_seconds=max(float(activity_timeout_seconds), MODE_MIN_ACTIVITY_SECONDS.get(mode, 0.0)),
    )
    event_summary = summarize_proxy_events(Path(proxy_info["events_path"]))
    for key, value in event_summary.items():
        if int(stats.get(key, 0) or 0) < int(value):
            stats[key] = value
    if resync is not None:
        resync["tip_after"] = query_target_tip(target_node, network_magic=metadata.get("network_magic"))
    log_evidence = None
    if mode == "praos_header_invalid_signature":
        corruption = next(iter(stats.get("header_corruptions") or []), None)
        if corruption:
            lines = collect_container_log_lines(target_node, since_epoch=restarted_at)
            if lines is not None:
                log_evidence = scan_header_rejection(lines, corrupted_hash=str(corruption["corrupted_header_hash"]))
    result_body = build_result_body(
        mode=mode,
        stats=stats,
        target_healthy=target_healthy,
        configured_limit=configured_limit,
        resync=resync,
        log_evidence=log_evidence,
    )

    metadata.setdefault("aux_sessions", []).append(
        {
            "id": f"{mode}-proxy-{target_node_id}",
            "kind": "chainsync_blockfetch_proxy",
            "session": proxy_session,
        }
    )
    topology_edges = list((metadata.get("topology") or {}).get("edges") or [])
    upstream_node_name = upstream_node_id
    if upstream_node_name is None:
        for candidate in _other_cardano_peers(metadata, target_node_id=target_node_id):
            if str(candidate["listen_address"]) == selected_upstream or str(candidate.get("container_listen_address") or "") == selected_upstream:
                upstream_node_name = str(candidate.get("id") or candidate.get("name") or "")
                break
    if upstream_node_name:
        edge = {"from": target_node_id, "to": upstream_node_name}
        if edge not in topology_edges:
            topology_edges.append(edge)
        metadata.setdefault("topology", {})["edges"] = topology_edges
    metadata.setdefault("faults", []).append(
        {
            "kind": mode,
            "target_node_id": target_node_id,
            "proxy_session": proxy_session,
            "proxy_listen_address": proxy_listen_address,
            "upstream_address": selected_upstream,
            "proxy_output_dir": str(proxy_output_dir),
        }
    )
    _write_metadata(runtime_metadata_path, metadata)

    report = {
        "mode": mode,
        "target_node": target_node_id,
        "upstream_node": upstream_node_name,
        "upstream_address": selected_upstream,
        "proxy_session": proxy_session,
        "proxy_listen_address": proxy_listen_address,
        "healthy": target_healthy,
        "proxy_stats": {
            "intercepted_segments": int(stats.get("intercepted_segments", 0) or 0),
            "mutated_segments": int(stats.get("mutated_segments", 0) or 0),
            "connections_seen": int(stats.get("connections_seen", 0) or 0),
            "chainsync_messages_observed": int(stats.get("chainsync_messages_observed", 0) or 0),
            "chainsync_roll_backward_count": int(stats.get("chainsync_roll_backward_count", 0) or 0),
            "chainsync_roll_forward_count": int(stats.get("chainsync_roll_forward_count", 0) or 0),
            "rollback_then_forward_count": int(stats.get("rollback_then_forward_count", 0) or 0),
            "blockfetch_messages_observed": int(stats.get("blockfetch_messages_observed", 0) or 0),
            "block_range_requests_observed": int(stats.get("block_range_requests_observed", 0) or 0),
            "blocks_fetched": int(stats.get("blocks_fetched", 0) or 0),
        },
        "result": result_body,
        "runtime_metadata_path": str(runtime_metadata_path),
        "simulation_mode": "mux_proxy_mutation",
    }
    write_json(output_dir / "result.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--mode", required=True, choices=sorted(MODE_DEFAULTS))
    args = parser.parse_args(argv)

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    report = apply_mode(
        runtime_metadata_path=Path(config["runtime_metadata_path"]),
        output_dir=Path(config["output_dir"]),
        mode=str(args.mode),
        target_node_id=str(config["target_node"]),
        upstream_node_id=config.get("upstream_node_id"),
        healthy_timeout_seconds=float(config.get("healthy_timeout_seconds", 90)),
        activity_timeout_seconds=float(config.get("activity_timeout_seconds", 15)),
        configured_limit=int(config.get("configured_limit", 64)),
        resync_from_empty_db=bool(config.get("resync_from_empty_db", True)),
    )
    print(
        " ".join(
            [
                f"mode={report['mode']}",
                f"target_node={report['target_node']}",
                f"healthy={'true' if report['healthy'] else 'false'}",
                f"intercepted_segments={report['proxy_stats']['intercepted_segments']}",
                f"mutated_segments={report['proxy_stats']['mutated_segments']}",
            ]
        ),
        flush=True,
    )
    return 0 if report["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
