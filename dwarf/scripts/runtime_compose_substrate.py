from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import shutil
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cbor2

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_substrate_common import (
    allocate_node_plan,
    build_version_provenance,
    normalize_substrate,
    resolve_binary_for_node,
    resolve_docker_image_for_node,
    run_command,
    verify_running_container_artifact,
    verify_running_node_version,
    wait_for_nodes_healthy,
    write_json,
)
from runtime_amaru_bootstrap_synth import DEFAULT_LOADER_BASE_IMAGE, synthesize_amaru_bootstrap
from runtime_multi_node_observation import _query_tip_once, _resolve_socket_path


def _cardano_testnet_env(*, cardano_node_binary: str, cardano_cli_binary: str) -> dict[str, str]:
    env = dict(__import__("os").environ)
    env["CARDANO_NODE"] = cardano_node_binary
    env["CARDANO_CLI"] = cardano_cli_binary
    return env


def _amaru_repo_root(binary_path: str) -> Path:
    path = Path(binary_path).resolve()
    if path.parent.name in {"debug", "release"} and path.parent.parent.name == "target":
        return path.parent.parent.parent
    return path.parent


def _amaru_bootstrap_required(network_name: str) -> bool:
    return not network_name.startswith("testnet_")


PUBLIC_NETWORK_ASSET_FILES = (
    "config.json",
    "topology.json",
    "peer-snapshot.json",
    "checkpoints.json",
    "byron-genesis.json",
    "shelley-genesis.json",
    "alonzo-genesis.json",
    "conway-genesis.json",
)

PUBLIC_NETWORK_MAGIC = {
    "mainnet": 764824073,
    "preprod": 1,
    "preview": 2,
}

GOVERNANCE_GUARDRAILS_FIXTURE = "always-succeeds-v3"
GOVERNANCE_GUARDRAILS_SCRIPT = (
    SCRIPT_DIR.parent
    / "corpora"
    / "redeemer-container"
    / "live-controls"
    / "always-succeeds-v3.plutus"
)


def _is_public_network(network_name: str) -> bool:
    return network_name in {"mainnet", "preprod", "preview"}


def _resolve_support_binary(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    candidate = Path.home() / ".local" / "bin" / name
    if candidate.exists():
        return str(candidate)
    raise FileNotFoundError(f"missing required binary: {name}")


def _packaged_cardano_testnet_create_env_command(
    *,
    output_dir: Path,
    pool_count: int,
    network_magic: int,
    binary: str = "cardano-testnet",
) -> list[str]:
    """Build the create-env command supported by the packaged 11.1.2 tool.

    JSON node logging is the tool's default.  The historical
    ``--node-logging-format`` option is not accepted by the 11.1.2 release.
    """

    return [
        binary,
        "create-env",
        "--output",
        str(output_dir),
        "--num-pool-nodes",
        str(pool_count),
        "--testnet-magic",
        str(network_magic),
    ]


def _port_bases(compose_project: str) -> tuple[int, int]:
    offset = sum(compose_project.encode("utf-8")) % 500
    return 33001 + (offset * 10), 43001 + (offset * 10)


def _ensure_tmux_session_absent(session: str) -> None:
    result = run_command(["tmux", "has-session", "-t", session])
    if result.returncode == 0:
        raise RuntimeError(f"tmux session already exists: {session}")


def _cardano_local_roots(access_points: list[dict[str, int | str]]) -> list[dict]:
    if not access_points:
        return [
            {
                "accessPoints": [],
                "advertise": False,
                "behindFirewall": False,
                "diffusionMode": "InitiatorAndResponder",
                "hotValency": 0,
                "trustable": False,
                "warmValency": 0,
            }
        ]
    return [
        {
            "accessPoints": access_points,
            "advertise": False,
            "behindFirewall": False,
            "diffusionMode": "InitiatorAndResponder",
            "hotValency": len(access_points),
            "trustable": True,
            "warmValency": len(access_points),
        }
    ]


def _requires_legacy_cardano_testnet_genesis_fields(version: object) -> bool:
    try:
        major_text, *_ = str(version).split(".")
        major = int(major_text)
    except (TypeError, ValueError):
        return False
    return major < 11


def _requires_cardano_testnet_log_metrics(version: object) -> bool:
    try:
        major_text, *_ = str(version).split(".")
        major = int(major_text)
    except (TypeError, ValueError):
        return False
    return major < 11


def _ensure_cardano_testnet_config_compatibility(
    *, config_path: Path, nodes: list[dict]
) -> dict:
    required_by_versions = sorted(
        {
            str(node.get("version"))
            for node in nodes
            if node.get("impl") == "cardano-node"
            and _requires_cardano_testnet_log_metrics(node.get("version"))
        }
    )
    body = json.loads(config_path.read_text(encoding="utf-8"))
    changed = bool(required_by_versions and "TurnOnLogMetrics" not in body)
    if changed:
        body["TurnOnLogMetrics"] = True
        config_path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    return {
        "changed": changed,
        "required_by_versions": required_by_versions,
        "turn_on_log_metrics": body.get("TurnOnLogMetrics"),
    }


def _tip_has_real_chain_progress(tip: dict) -> bool:
    hash_value = str((tip or {}).get("hash") or "").strip()
    slot_value = (tip or {}).get("slot")
    block_value = (tip or {}).get("block")
    try:
        slot_int = int(slot_value)
        block_int = int(block_value)
    except (TypeError, ValueError):
        return False
    return slot_int > 0 and block_int >= 1 and bool(hash_value)


def _refresh_cardano_testnet_start_times(
    *,
    env_root: Path,
    current_time: datetime | None = None,
    start_time_offset_seconds: int = 15,
) -> dict:
    byron_path = env_root / "byron-genesis.json"
    shelley_path = env_root / "shelley-genesis.json"
    missing_files = [path.name for path in (byron_path, shelley_path) if not path.exists()]
    if missing_files:
        return {"changed": False, "missing_files": missing_files}

    now = current_time or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    start_time = now.astimezone(timezone.utc) + timedelta(seconds=start_time_offset_seconds)
    start_time_iso = start_time.isoformat().replace("+00:00", "Z")
    start_time_unix = int(start_time.timestamp())

    byron_body = json.loads(byron_path.read_text(encoding="utf-8"))
    byron_body["startTime"] = start_time_unix
    byron_path.write_text(json.dumps(byron_body, indent=4) + "\n", encoding="utf-8")

    shelley_body = json.loads(shelley_path.read_text(encoding="utf-8"))
    shelley_body["systemStart"] = start_time_iso
    shelley_path.write_text(json.dumps(shelley_body, indent=4) + "\n", encoding="utf-8")

    return {
        "changed": True,
        "missing_files": [],
        "start_time_iso": start_time_iso,
        "start_time_unix": start_time_unix,
        "start_time_offset_seconds": start_time_offset_seconds,
    }


CARDANO_MEASUREMENT_TRACE_NAMESPACES = (
    "Net.Handshake.Remote",
    "ChainSync.Client",
    "ChainSync.ServerHeader",
    "BlockFetch.Client",
    "BlockFetch.Server",
    "TxSubmission.TxInbound",
    "TxSubmission.TxOutbound",
    "Mempool",
    "KeepAlive.Remote",
    "ChainDB",
    "Forge.Loop",
    "LedgerMetrics",
    "Resources",
)


def _enable_cardano_measurement_traces(config_path: Path) -> dict:
    """Enable audited machine namespaces only for an opt-in measurement profile."""
    body = json.loads(config_path.read_text(encoding="utf-8"))
    options = body.get("TraceOptions")
    if not isinstance(options, dict):
        options = {}
    for namespace in CARDANO_MEASUREMENT_TRACE_NAMESPACES:
        current = options.get(namespace)
        configured = dict(current) if isinstance(current, dict) else {}
        configured.update({"severity": "Info", "detail": "DDetailed"})
        options[namespace] = configured
    body["TraceOptions"] = options
    config_path.write_text(json.dumps(body, indent=4) + "\n", encoding="utf-8")
    return {
        "enabled": True,
        "namespaces": sorted(CARDANO_MEASUREMENT_TRACE_NAMESPACES),
        "config_path": str(config_path),
    }


def _refresh_cardano_testnet_start_times_remote(*, host: dict, env_root: str, start_time_offset_seconds: int = 15) -> dict:
    command = (
        "python3 - "
        + " ".join([json.dumps(f"{env_root}/byron-genesis.json"), json.dumps(f"{env_root}/shelley-genesis.json"), json.dumps(str(start_time_offset_seconds))])
        + " <<'PY'\n"
        + "import datetime, json, pathlib, sys\n"
        + "byron_path = pathlib.Path(sys.argv[1])\n"
        + "shelley_path = pathlib.Path(sys.argv[2])\n"
        + "offset = int(sys.argv[3])\n"
        + "missing = [p.name for p in (byron_path, shelley_path) if not p.exists()]\n"
        + "if missing:\n"
        + "    print(json.dumps({'changed': False, 'missing_files': missing}))\n"
        + "    raise SystemExit(0)\n"
        + "now = datetime.datetime.now(datetime.timezone.utc)\n"
        + "start = now + datetime.timedelta(seconds=offset)\n"
        + "start_iso = start.isoformat().replace('+00:00', 'Z')\n"
        + "start_unix = int(start.timestamp())\n"
        + "with open(byron_path, encoding='utf-8') as fh:\n"
        + "    byron = json.load(fh)\n"
        + "byron['startTime'] = start_unix\n"
        + "with open(byron_path, 'w', encoding='utf-8') as fh:\n"
        + "    json.dump(byron, fh, indent=4)\n"
        + "    fh.write('\\n')\n"
        + "with open(shelley_path, encoding='utf-8') as fh:\n"
        + "    shelley = json.load(fh)\n"
        + "shelley['systemStart'] = start_iso\n"
        + "with open(shelley_path, 'w', encoding='utf-8') as fh:\n"
        + "    json.dump(shelley, fh, indent=4)\n"
        + "    fh.write('\\n')\n"
        + "print(json.dumps({'changed': True, 'missing_files': [], 'start_time_iso': start_iso, 'start_time_unix': start_unix, 'start_time_offset_seconds': offset}))\n"
        + "PY"
    )
    result = _run_remote_command(host, command)
    if result.returncode != 0:
        raise RuntimeError(f"failed to refresh cardano-testnet start times on {host['id']}: {result.stderr or result.stdout}")
    output = (result.stdout or "").strip().splitlines()
    if not output:
        raise RuntimeError(f"missing refresh-cardano-testnet output on {host['id']}")
    return json.loads(output[-1])


def _wait_for_cardano_node_block(
    *,
    runtime_metadata_path: Path,
    nodes: list[dict],
    network_magic: int,
    cardano_cli: str,
    timeout_seconds: float = 45.0,
    sample_interval_seconds: float = 2.0,
) -> dict:
    deadline = time.monotonic() + timeout_seconds
    attempts = 0
    latest_tips: dict[str, dict] = {}
    while True:
        attempts += 1
        for node in nodes:
            node_id = str(node.get("id") or node.get("name") or "")
            latest_tips[node_id] = _query_tip_once(
                cardano_cli=cardano_cli,
                socket_path=_resolve_socket_path(runtime_metadata_path, node),
                network_magic=network_magic,
                container_name=str(node.get("container_name") or "") or None,
                container_socket_path=str(node.get("container_socket_path") or "") or None,
            )
        ready_nodes = [node_id for node_id, tip in latest_tips.items() if _tip_has_real_chain_progress(tip)]
        if ready_nodes:
            return {
                "ready": True,
                "timed_out": False,
                "timeout_seconds": timeout_seconds,
                "sample_interval_seconds": sample_interval_seconds,
                "attempt_count": attempts,
                "ready_node_count": len(ready_nodes),
                "ready_nodes": ready_nodes,
                "latest_tips": latest_tips,
            }
        if time.monotonic() >= deadline:
            return {
                "ready": False,
                "timed_out": True,
                "timeout_seconds": timeout_seconds,
                "sample_interval_seconds": sample_interval_seconds,
                "attempt_count": attempts,
                "ready_node_count": 0,
                "ready_nodes": [],
                "latest_tips": latest_tips,
            }
        time.sleep(sample_interval_seconds)


def _parse_amaru_runtime_progress(text: str) -> dict:
    bootstrap_slots = [
        int(value)
        for value in re.findall(r"\bbuild_ledger\b[^\n]*?\btip\.slot=([0-9]+)", text or "")
    ]
    highest_slots = [
        int(value)
        for value in re.findall(r"\bintersect found\b[^\n]*?\bhighest=([0-9]+)\.", text or "")
    ]
    current_intersections = [
        (int(current), int(highest))
        for current, highest in re.findall(
            r"\bchainsync\.intersect_found\b[^\n]*?\bcurrent=\"?\[([0-9]+),"
            r"[^\n]*?\bhighest=\"?\[([0-9]+),",
            text or "",
        )
    ]
    highest_slots.extend(highest for _, highest in current_intersections)
    adopted_slots = [
        int(value)
        for value in re.findall(r"\badopted tip\b[^\n]*?\btip\.slot=([0-9]+)", text or "")
    ]
    adopted_slots.extend(
        int(value)
        for value in re.findall(r"\btip\.adopt\b[^\n]*?\bslot=([0-9]+)", text or "")
    )
    blocking_patterns = {
        "invalid-vrf-proof": r"Invalid VRF proof|VRFKeyBadProof",
        "panic": r"\bpanicked at\b",
        "consensus-terminated": r"\bConsensus died\b",
        "future-rollback": r"attempted roll back in the future",
        "handshake-decode-error": r"failed to decode message from network[^\n]*decode error",
    }
    blocking_signals = [
        name
        for name, pattern in blocking_patterns.items()
        if re.search(pattern, text or "", flags=re.IGNORECASE)
    ]
    bootstrap_slot = (
        bootstrap_slots[-1]
        if bootstrap_slots
        else (current_intersections[0][0] if current_intersections else None)
    )
    # Measure convergence against the stable peer tip advertised at the first
    # intersection. A live producer may advance while the follower catches up.
    highest_peer_slot = highest_slots[0] if highest_slots else None
    latest_adopted_slot = max(adopted_slots) if adopted_slots else None
    advanced = bool(
        bootstrap_slot is not None
        and latest_adopted_slot is not None
        and latest_adopted_slot > bootstrap_slot
    )
    converged = bool(
        highest_peer_slot is not None
        and latest_adopted_slot is not None
        and latest_adopted_slot >= highest_peer_slot
    )
    return {
        "bootstrap_slot": bootstrap_slot,
        "highest_peer_slot": highest_peer_slot,
        "convergence_target": "initial-advertised-peer-tip",
        "latest_adopted_slot": latest_adopted_slot,
        "intersection_found": bool(highest_slots),
        "advanced": advanced,
        "converged": converged,
        "blocking_signals": blocking_signals,
        "ready": bool(advanced and converged and not blocking_signals),
    }


def _wait_for_amaru_progress(
    *,
    nodes: list[dict],
    timeout_seconds: float = 120.0,
    sample_interval_seconds: float = 2.0,
) -> dict:
    deadline = time.monotonic() + timeout_seconds
    attempts = 0
    latest: dict[str, dict] = {}
    while True:
        attempts += 1
        for node in nodes:
            node_id = str(node.get("id") or node.get("name") or "")
            log_path = Path(str(node.get("log_path") or ""))
            try:
                text = log_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            latest[node_id] = _parse_amaru_runtime_progress(text)
        ready_nodes = [node_id for node_id, item in latest.items() if item.get("ready")]
        blocking = {
            node_id: item["blocking_signals"]
            for node_id, item in latest.items()
            if item.get("blocking_signals")
        }
        if len(ready_nodes) == len(nodes):
            return {
                "ready": True,
                "timed_out": False,
                "timeout_seconds": timeout_seconds,
                "sample_interval_seconds": sample_interval_seconds,
                "attempt_count": attempts,
                "ready_node_count": len(ready_nodes),
                "ready_nodes": ready_nodes,
                "nodes": latest,
                "blocking_signals": {},
            }
        if blocking:
            return {
                "ready": False,
                "timed_out": False,
                "timeout_seconds": timeout_seconds,
                "sample_interval_seconds": sample_interval_seconds,
                "attempt_count": attempts,
                "ready_node_count": len(ready_nodes),
                "ready_nodes": ready_nodes,
                "nodes": latest,
                "blocking_signals": blocking,
            }
        if time.monotonic() >= deadline:
            return {
                "ready": False,
                "timed_out": True,
                "timeout_seconds": timeout_seconds,
                "sample_interval_seconds": sample_interval_seconds,
                "attempt_count": attempts,
                "ready_node_count": len(ready_nodes),
                "ready_nodes": ready_nodes,
                "nodes": latest,
                "blocking_signals": {},
            }
        time.sleep(sample_interval_seconds)


def _apply_kes_genesis_override_to_shelley(*, genesis_path: Path, override: dict | None) -> dict | None:
    """Inject a short operational-KES window into a generated Shelley genesis so
    the aged/short-KES opcert boundary rules become reachable on a cardano-only
    devnet. The pool opcerts created by cardano-testnet start at KES period 0 and
    stay valid under the shortened window; the change only shortens the
    slot->KES-period mapping and the evolution ceiling. Fails closed on a
    malformed override. Returns the applied values, or None when not requested."""
    if not override:
        return None
    slots = int(override["slots_per_kes_period"])
    evolutions = int(override["max_kes_evolutions"])
    if slots < 1 or evolutions < 1:
        raise RuntimeError("kes_genesis_override values must be >= 1")
    body = json.loads(genesis_path.read_text(encoding="utf-8"))
    body["slotsPerKESPeriod"] = slots
    body["maxKESEvolutions"] = evolutions
    genesis_path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    return {
        "slots_per_kes_period": slots,
        "max_kes_evolutions": evolutions,
        "window_slots": slots * evolutions,
        "effective_shelley_genesis_path": str(genesis_path),
        "effective_shelley_genesis_sha256": hashlib.sha256(
            genesis_path.read_bytes()
        ).hexdigest(),
    }


def _prepare_governance_guardrails_fixture_request(
    *, fixture: dict | None
) -> dict | None:
    """Describe an explicit guardrails edit for the staged pinned loader config."""

    if not fixture:
        return None
    if set(fixture) != {"name", "expected_active_script_hash"}:
        raise RuntimeError(
            "governance_guardrails_fixture requires name and expected_active_script_hash"
        )
    if fixture["name"] != GOVERNANCE_GUARDRAILS_FIXTURE:
        raise RuntimeError("governance guardrails fixture is not supported")
    expected = str(fixture["expected_active_script_hash"] or "").lower()
    if not re.fullmatch(r"[0-9a-f]{56}", expected):
        raise RuntimeError("expected constitution guardrails hash is invalid")

    envelope = json.loads(GOVERNANCE_GUARDRAILS_SCRIPT.read_text(encoding="utf-8"))
    if envelope.get("type") != "PlutusScriptV3":
        raise RuntimeError("governance guardrails fixture is not Plutus V3")
    encoded = bytes.fromhex(str(envelope.get("cborHex") or ""))
    script = cbor2.loads(encoded)
    if not isinstance(script, bytes):
        raise RuntimeError("governance guardrails fixture CBOR is not a script byte string")
    fixture_hash = hashlib.blake2b(b"\x03" + script, digest_size=28).hexdigest()

    return {
        "fixture": GOVERNANCE_GUARDRAILS_FIXTURE,
        "expected_source_script_hash": expected,
        "effective_script_hash": fixture_hash,
        "script_sha256": hashlib.sha256(
            GOVERNANCE_GUARDRAILS_SCRIPT.read_bytes()
        ).hexdigest(),
    }


def _rewrite_cardano_testnet_genesis_for_older_cardano_nodes(*, genesis_path: Path, nodes: list[dict]) -> bool:
    if not any(
        node.get("impl") == "cardano-node"
        and _requires_legacy_cardano_testnet_genesis_fields(node.get("version"))
        for node in nodes
    ):
        return False
    body = json.loads(genesis_path.read_text(encoding="utf-8"))
    changed = False
    extra_config = body.get("extraConfig") or {}
    extra_initial_funds = copy.deepcopy(
        ((extra_config.get("initialFunds") or {}).get("data") or {})
    )
    extra_stake_pools = copy.deepcopy(
        ((extra_config.get("stakePools") or {}).get("data") or {})
    )
    extra_stake_credentials = copy.deepcopy(
        ((extra_config.get("stakeCredentials") or {}).get("data") or {})
    )
    if extra_initial_funds and not body.get("initialFunds"):
        body["initialFunds"] = extra_initial_funds
        changed = True
    staking = body.setdefault("staking", {})
    if extra_stake_pools and not staking.get("pools"):
        staking["pools"] = extra_stake_pools
        changed = True
    if extra_stake_credentials and not staking.get("stake"):
        staking["stake"] = extra_stake_credentials
        changed = True
    pools = staking.get("pools") or {}
    for pool_id, pool_body in pools.items():
        if not isinstance(pool_body, dict):
            continue
        if "publicKey" not in pool_body:
            pool_body["publicKey"] = str(pool_body.get("poolId") or pool_id)
            changed = True
        if "rewardAccount" not in pool_body and "accountAddress" in pool_body:
            pool_body["rewardAccount"] = pool_body["accountAddress"]
            changed = True
    if changed:
        genesis_path.write_text(json.dumps(body, indent=4) + "\n", encoding="utf-8")
    return changed


def _prepare_generated_workload_funding(
    *, env_root: Path, cardano_cli: str, network_magic: int
) -> dict:
    """Stage a public identity request for the post-copy bootstrap loader hook."""

    if not (env_root / "shelley-genesis.json").is_file():
        raise RuntimeError("cardano-testnet omitted the Shelley genesis")

    credentials = []
    for index in range(1, 4):
        credential_id = f"utxo{index}"
        key_root = env_root / "utxo-keys" / credential_id
        verification_key = key_root / "utxo.vkey"
        signing_key = key_root / "utxo.skey"
        if not verification_key.is_file() or not signing_key.is_file():
            raise RuntimeError(
                "cardano-testnet omitted one of three generated workload credentials"
            )
        address_result = run_command(
            [
                cardano_cli,
                "latest",
                "genesis",
                "initial-addr",
                "--verification-key-file",
                str(verification_key),
                "--testnet-magic",
                str(network_magic),
            ]
        )
        if address_result.returncode != 0:
            raise RuntimeError(
                "failed to derive a generated workload address: "
                f"{address_result.stderr or address_result.stdout}"
            )
        address = address_result.stdout.strip()
        info_result = run_command(
            [cardano_cli, "latest", "address", "info", "--address", address]
        )
        if info_result.returncode != 0:
            raise RuntimeError(
                "failed to inspect a generated workload address: "
                f"{info_result.stderr or info_result.stdout}"
            )
        try:
            address_info = json.loads(info_result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("generated workload address metadata is invalid") from exc
        address_base16 = str(address_info.get("base16") or "").lower()
        if (
            not address.startswith("addr_test")
            or address_info.get("type") != "payment"
            or len(address_base16) != 58
            or not address_base16.startswith("60")
            or any(character not in "0123456789abcdef" for character in address_base16)
        ):
            raise RuntimeError(
                "generated workload address is not a testnet payment address"
            )
        credentials.append(
            {
                "id": credential_id,
                "address": address,
                "address_base16": address_base16,
                "verification_key": f"utxo-keys/{credential_id}/utxo.vkey",
            }
        )
    if len({item["address_base16"] for item in credentials}) != len(credentials):
        raise RuntimeError("generated workload credentials do not have unique addresses")

    request = {
        **credentials[0],
        "credentials": credentials,
    }
    (env_root / "generated-workload-funding.json").write_text(
        json.dumps(request, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return request


def _public_topology_with_upstream(
    template: dict, upstream_peer_address: str
) -> tuple[dict, list[dict[str, int | str]]]:
    """Retain an explicit public peer in both legacy and Genesis-mode inputs."""

    body = dict(template)
    upstream = str(upstream_peer_address or "").strip()
    if not upstream:
        return body, []
    peer_host, peer_port_text = upstream.rsplit(":", 1)
    access_point: dict[str, int | str] = {
        "address": peer_host,
        "port": int(peer_port_text),
    }
    # cardano-node 11.x Genesis mode deliberately ignores bootstrapPeers, but
    # earlier supported versions still consume it.  The same catalog-owned peer
    # must therefore also be emitted as a trusted local root.
    body["bootstrapPeers"] = [access_point]
    return body, [access_point]


def _rewrite_haskell_topology(
    node: dict,
    *,
    topo_path: Path,
    template: dict,
    peer_snapshot_path: Path | None = None,
    access_points: list[dict[str, int | str]] | None = None,
) -> None:
    body = dict(template)
    peer_points = access_points or []
    if not peer_points:
        for peer_address in node["peer_addresses"]:
            host, port_text = peer_address.rsplit(":", 1)
            peer_points.append({"address": host, "port": int(port_text)})
    body["localRoots"] = _cardano_local_roots(peer_points)
    if "bootstrapPeers" not in body:
        body["bootstrapPeers"] = None
    if "publicRoots" not in body:
        body["publicRoots"] = [{"accessPoints": [], "advertise": False}]
    # Synthetic devnets need ledger peers immediately, but public-network
    # topology assets carry a deliberate bootstrap threshold.  Overwriting it
    # made a fresh node abandon its trusted bootstrap path while still far
    # behind the public tip.
    body.setdefault("useLedgerAfterSlot", -1)
    if peer_snapshot_path is not None:
        body["peerSnapshotFile"] = str(peer_snapshot_path)
    topo_path.parent.mkdir(parents=True, exist_ok=True)
    topo_path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")


def _launch_haskell_node(node: dict, *, runtime_root: Path, binary_path: str, public_network: bool) -> None:
    _ensure_tmux_session_absent(node["session"])
    node_log_dir = runtime_root / "logs" / node["id"]
    node_log_dir.mkdir(parents=True, exist_ok=True)
    command_parts = [
        binary_path,
        "run",
        "--config",
        node["config_path"],
        "--topology",
        node["topology_path"],
        "--database-path",
        node["db_dir"],
        "--socket-path",
        node["socket_path"],
        "--port",
        str(node["port"]),
        "--host-addr",
        "127.0.0.1",
    ]
    if not public_network:
        slot = int(node["slot_index"])
        command_parts.extend(
            [
                "--shelley-kes-key",
                str(runtime_root / "env" / "pools-keys" / f"pool{slot}" / "kes.skey"),
                "--shelley-vrf-key",
                str(runtime_root / "env" / "pools-keys" / f"pool{slot}" / "vrf.skey"),
                "--shelley-operational-certificate",
                str(runtime_root / "env" / "pools-keys" / f"pool{slot}" / "opcert.cert"),
                "--byron-delegation-certificate",
                str(runtime_root / "env" / "pools-keys" / f"pool{slot}" / "byron-delegation.cert"),
                "--byron-signing-key",
                str(runtime_root / "env" / "pools-keys" / f"pool{slot}" / "byron-delegate.key"),
            ]
        )
    command = f"echo $$ > {node['pid_file']}; exec {' '.join(json.dumps(part) for part in command_parts)} 2>&1 | tee -a {json.dumps(node['log_path'])}"
    result = run_command(["tmux", "new-session", "-d", "-s", node["session"], f"bash -lc {json.dumps(command)}"])
    if result.returncode != 0:
        raise RuntimeError(f"failed to launch {node['id']}: {result.stderr or result.stdout}")


def _launch_amaru_node(node: dict, *, network_name: str, binary_path: str, runtime_root: Path) -> None:
    _ensure_tmux_session_absent(node["session"])
    Path(node["state_root"]).mkdir(parents=True, exist_ok=True)
    Path(node["log_path"]).parent.mkdir(parents=True, exist_ok=True)
    if _amaru_bootstrap_required(network_name):
        bootstrap = run_command(
            [
                binary_path,
                "bootstrap",
                "--network",
                network_name,
                "--ledger-dir",
                node["ledger_dir"],
                "--chain-dir",
                node["chain_dir"],
            ],
            cwd=runtime_root,
        )
        Path(node["bootstrap_stdout"]).write_text(bootstrap.stdout, encoding="utf-8")
        Path(node["bootstrap_stderr"]).write_text(bootstrap.stderr, encoding="utf-8")
        if bootstrap.returncode != 0:
            raise RuntimeError(f"failed to bootstrap {node['id']}: {bootstrap.stderr or bootstrap.stdout}")
    else:
        Path(node["bootstrap_stdout"]).write_text("", encoding="utf-8")
        Path(node["bootstrap_stderr"]).write_text("", encoding="utf-8")
    # In a containerised (docker) substrate the host-mapped peer_addresses
    # (127.0.0.1:<host-port>) resolve to the Amaru container's own loopback, so
    # it never connects. Prefer the container-network addresses (prod1:3001)
    # when the compose layer provided them; fall back to host addresses for
    # host-process substrates.
    peer_addresses = list(node.get("container_peer_addresses") or node["peer_addresses"])
    if node.get("fallback_peer_addresses"):
        for peer_address in node["fallback_peer_addresses"]:
            if peer_address not in peer_addresses:
                peer_addresses.append(peer_address)
    if not peer_addresses and node["effective_peer_address"]:
        peer_addresses = [node["effective_peer_address"]]
    if not peer_addresses:
        raise RuntimeError(f"no peer address available for {node['id']}")
    command_parts = [
        binary_path,
        "run",
        "--network",
        network_name,
    ]
    for peer_address in peer_addresses:
        command_parts.extend(["--peer-address", peer_address])
    command_parts.extend(
        [
            "--listen-address",
            node["listen_address"],
            "--ledger-dir",
            node["ledger_dir"],
            "--chain-dir",
            node["chain_dir"],
            "--pid-file",
            node["pid_file"],
        ]
    )
    command = f"cd {json.dumps(str(runtime_root))} && echo $$ > {json.dumps(node['pid_file'])}; exec {' '.join(json.dumps(part) for part in command_parts)} 2>&1 | tee -a {json.dumps(node['log_path'])}"
    result = run_command(["tmux", "new-session", "-d", "-s", node["session"], f"bash -lc {json.dumps(command)}"])
    if result.returncode != 0:
        raise RuntimeError(f"failed to launch {node['id']}: {result.stderr or result.stdout}")


def _download_public_network_assets(*, network_name: str, destination: Path) -> dict:
    destination.mkdir(parents=True, exist_ok=True)
    base_url = f"https://book.play.dev.cardano.org/environments/{network_name}"
    assets = {}
    for asset_name in PUBLIC_NETWORK_ASSET_FILES:
        target = destination / asset_name
        if not target.exists():
            with urllib.request.urlopen(f"{base_url}/{asset_name}") as response:
                target.write_bytes(response.read())
        assets[asset_name] = str(target)
    return assets


def _stage_public_network_assets(
    *, network_name: str, destination: Path, config_source_dir: str | None
) -> dict:
    """Stage the profile's public-network config without changing its source contract."""

    if not config_source_dir:
        return _download_public_network_assets(
            network_name=network_name, destination=destination
        )
    source = Path(config_source_dir)
    required = {
        "config.json",
        "topology.json",
        "byron-genesis.json",
        "shelley-genesis.json",
        "alonzo-genesis.json",
        "conway-genesis.json",
    }
    missing = sorted(name for name in required if not (source / name).is_file())
    if missing:
        raise RuntimeError(
            f"public-network config source {source} is missing: {', '.join(missing)}"
        )
    destination.mkdir(parents=True, exist_ok=True)
    assets = {}
    for asset_name in PUBLIC_NETWORK_ASSET_FILES:
        source_path = source / asset_name
        if not source_path.is_file():
            continue
        target = destination / asset_name
        shutil.copy2(source_path, target)
        assets[asset_name] = str(target)
    return assets


def _resolve_public_network_magic(*, network_name: str, config_path: Path) -> int | None:
    body = json.loads(config_path.read_text(encoding="utf-8"))
    for key in ("networkMagic", "NetworkMagic", "TestnetMagic", "testnetMagic"):
        value = body.get(key)
        if value is not None:
            return int(value)
    return PUBLIC_NETWORK_MAGIC.get(network_name)


def _emit_bundle_runtime_view(*, output_dir: Path, metadata_path: Path, metadata: dict) -> Path:
    sockets_dir = output_dir / "sockets"
    sockets_dir.mkdir(parents=True, exist_ok=True)
    mirrored_nodes = []
    for node in list(metadata.get("haskell_nodes") or []) + list(metadata.get("amaru_nodes") or []):
        mirrored = dict(node)
        mirrored["name"] = str(node.get("id") or node.get("name") or "")
        mirrored["implementation"] = str(node.get("impl") or node.get("implementation") or "")
        socket_path = node.get("socket_path")
        if socket_path:
            source = Path(str(socket_path))
            mirror = sockets_dir / f"{mirrored['name']}.sock"
            if source.exists():
                if mirror.exists() or mirror.is_symlink():
                    mirror.unlink()
                mirror.symlink_to(source)
        mirrored_nodes.append(mirrored)
    bundle_metadata = dict(metadata)
    bundle_metadata["nodes"] = mirrored_nodes
    bundle_metadata["runtime_metadata_path"] = str(metadata_path)
    bundle_metadata_path = output_dir / "runtime.json"
    bundle_metadata_path.write_text(json.dumps(bundle_metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return bundle_metadata_path


def _docker_image_ref(node: dict) -> str:
    configured = str(node.get("image") or "").strip()
    if configured:
        return configured
    if node["impl"] == "cardano-node":
        return f"dwarf/cardano-node:{node['version']}"
    return f"dwarf/amaru:{node['version']}"


def _docker_container_port(node: dict) -> int:
    return 3001 if node["impl"] == "cardano-node" else 5001


def _docker_container_socket_path(node: dict) -> str | None:
    if node["impl"] == "cardano-node":
        return f"/env/socket/{node['id']}/sock"
    return None


def _synthesize_amaru_bootstrap_for_custom_testnet(*, runtime_root: Path, plan: dict) -> dict:
    amaru_nodes = [node for node in plan["nodes"] if node["impl"] == "amaru"]
    if not amaru_nodes:
        raise RuntimeError("Amaru bootstrap synthesis requires at least one Amaru node")
    selected_images = {str(node.get("image") or "").strip() for node in amaru_nodes}
    if len(selected_images) != 1 or not next(iter(selected_images)):
        raise RuntimeError("Amaru bootstrap synthesis requires one exact selected Amaru image")
    # Bootstrap state is a support artifact, not the target under test.  The
    # immutable loader below retains the legacy conversion/import commands;
    # current target releases consume that state through their migration path.
    result = synthesize_amaru_bootstrap(
        runtime_root=runtime_root,
        plan=plan,
        loader_image=DEFAULT_LOADER_BASE_IMAGE,
    )
    # The producer chain hard-forks at epoch zero and its immutable support
    # bundle does not ship a Byron genesis delegation pair.  Supplying the
    # unrelated fresh cardano-testnet Byron credentials makes cardano-node
    # reject an otherwise coherent Shelley pool identity at startup.
    for node in plan["nodes"]:
        if node["impl"] == "cardano-node":
            node["omit_byron_credentials"] = True
    return result


def _docker_project_name(compose_project: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]", "-", compose_project.lower())
    return normalized or "dwarf-substrate"


def _ssh_base_command(host: dict) -> list[str]:
    command = ["ssh", "-n", "-o", "BatchMode=yes"]
    ssh_key_path = host.get("ssh_key_path")
    if ssh_key_path:
        command.extend(["-i", str(ssh_key_path)])
    command.append(str(host["ssh_target"]))
    return command


def _run_remote_command(host: dict, script: str) -> object:
    return run_command(_ssh_base_command(host) + [f"bash -lc {json.dumps(script)}"])


def _rsync_runtime_to_host(*, local_root: Path, host: dict, remote_root: str) -> object:
    command = ["rsync", "-a", "--delete"]
    ssh_key_path = host.get("ssh_key_path")
    if ssh_key_path:
        command.extend(["-e", f"ssh -i {ssh_key_path}"])
    command.extend([f"{str(local_root)}/", f"{host['ssh_target']}:{remote_root}/"])
    return run_command(command)


def _host_runtime_root(*, host: dict, compose_project: str) -> str:
    return f"{host['remote_runtime_base'].rstrip('/')}/{compose_project}-{host['id']}"


def _host_local_root(*, runtime_root: Path, host_id: str) -> Path:
    return runtime_root / "hosts" / host_id


def _host_compose_project(*, compose_project: str, host_id: str) -> str:
    return _docker_project_name(f"{compose_project}-{host_id}")


def _docker_compose_body(
    *,
    compose_project: str,
    nodes: list[dict],
    network_name: str,
    network_magic: int | None = None,
    controller_ownership: dict | None = None,
    docker_host_runtime_root: str | None = None,
    amaru_txsubmission_trace: bool = False,
    runtime_user: str = "1000:1000",
) -> dict:
    services = {}
    for node in nodes:
        host, port_text = node["listen_address"].rsplit(":", 1)
        if node["impl"] == "cardano-node":
            patched_measurement_trace_setup = ""
            if node.get("target_mode") == "patched":
                patched_measurement_trace_setup = (
                    f"touch /logs/{node['id']}/cardano-measurement.ndjson "
                    f"/logs/{node['id']}/cardano-plutus.ndjson && "
                )
            if node.get("public_network"):
                command = [(
                    "set -euo pipefail; "
                    f"mkdir -p /env/socket/{node['id']} /logs/{node['id']} /env/node-data/node{node['host_slot_index']} && "
                    f"{patched_measurement_trace_setup}"
                    "exec cardano-node run "
                    f"--config /env/configuration.yaml "
                    f"--topology /env/node-data/node{node['host_slot_index']}/topology.json "
                    f"--database-path /env/node-data/node{node['host_slot_index']}/db "
                    f"--socket-path /env/socket/{node['id']}/sock "
                    "--port 3001 "
                    "--host-addr 0.0.0.0 "
                    f"2>&1 | tee -a /logs/{node['id']}/stdout.log"
                )]
            else:
                byron_credentials = ""
                if not node.get("omit_byron_credentials"):
                    byron_credentials = (
                        f"--byron-delegation-certificate /env/pools-keys/pool{node['host_slot_index']}/byron-delegation.cert "
                        f"--byron-signing-key /env/pools-keys/pool{node['host_slot_index']}/byron-delegate.key "
                    )
                command = [(
                    "set -euo pipefail; "
                    f"mkdir -p /env/socket/{node['id']} /logs/{node['id']} && "
                    f"{patched_measurement_trace_setup}"
                    "exec cardano-node run "
                    f"--config /env/configuration.yaml "
                    f"--topology /env/node-data/node{node['host_slot_index']}/topology.json "
                    f"--database-path /env/node-data/node{node['host_slot_index']}/db "
                    f"--socket-path /env/socket/{node['id']}/sock "
                    "--port 3001 "
                    "--host-addr 0.0.0.0 "
                    f"--shelley-kes-key /env/pools-keys/pool{node['host_slot_index']}/kes.skey "
                    f"--shelley-vrf-key /env/pools-keys/pool{node['host_slot_index']}/vrf.skey "
                    f"--shelley-operational-certificate /env/pools-keys/pool{node['host_slot_index']}/opcert.cert "
                    f"{byron_credentials}"
                    f"2>&1 | tee -a /logs/{node['id']}/stdout.log"
                )]
        else:
            peer_addresses = list(node.get("container_peer_addresses") or [])
            if not peer_addresses and node.get("effective_peer_address"):
                peer_addresses = [str(node["effective_peer_address"])]
            if node.get("fallback_peer_addresses"):
                for peer_address in node["fallback_peer_addresses"]:
                    candidate = str(peer_address)
                    if candidate not in peer_addresses:
                        peer_addresses.append(candidate)
            if not peer_addresses:
                raise RuntimeError(f"no peer address available for docker amaru node {node['id']}")
            chain_name = Path(str(node["chain_dir"])).name
            ledger_name = Path(str(node["ledger_dir"])).name
            state_root = f"/amaru/{node['id']}"
            command_parts = [
                "set -euo pipefail",
                f"mkdir -p {state_root} /logs/{node['id']}",
            ]
            if _amaru_bootstrap_required(network_name):
                command_parts.append(
                    "amaru bootstrap "
                    f"--network {json.dumps(network_name)} "
                    f"--ledger-dir {json.dumps(f'{state_root}/{ledger_name}')} "
                    f"--chain-dir {json.dumps(f'{state_root}/{chain_name}')}"
                )
            peer_args = " ".join(f"--peer-address {json.dumps(peer_address)}" for peer_address in peer_addresses)
            command_parts.append(
                "exec amaru run "
                f"--network {json.dumps(network_name)} "
                f"{peer_args} "
                "--listen-address 0.0.0.0:5001 "
                "--submit-api-address 0.0.0.0:3011 "
                f"--ledger-dir {json.dumps(f'{state_root}/{ledger_name}')} "
                f"--chain-dir {json.dumps(f'{state_root}/{chain_name}')} "
                + (
                    f"--era-history {json.dumps(f'{state_root}/era-history.json')} "
                    if not _amaru_bootstrap_required(network_name)
                    else ""
                )
                +
                f"--pid-file {json.dumps(f'{state_root}/amaru.pid')} "
                f"2>&1 | tee -a /logs/{node['id']}/stdout.log"
            )
            command = ["; ".join(command_parts)]
        labels = {
            "ada2.managed": "dwarf",
            "ada2.profile": compose_project,
            "ada2.service": node["id"],
        }
        if controller_ownership:
            labels.update(
                {
                    "io.dwarf.managed": "true",
                    "io.dwarf.installation": str(controller_ownership["installation_id"]),
                    "io.dwarf.topology": str(controller_ownership["topology_id"]),
                    "io.dwarf.component": "node",
                    "io.dwarf.node_id": str(node["id"]),
                    "io.dwarf.implementation": str(node["impl"]),
                    "io.dwarf.version": str(node.get("version") or "unknown"),
                    "io.dwarf.network_magic": str(network_magic if network_magic is not None else ""),
                }
            )
        volume_root = docker_host_runtime_root.rstrip("/") if docker_host_runtime_root else "."
        services[node["id"]] = {
            "image": _docker_image_ref(node),
            "container_name": f"{compose_project}-{node['id']}-1",
            "hostname": node["id"],
            "labels": labels,
            "networks": {"default": {"aliases": [node["id"]]}},
            "ports": [f"{host}:{port_text}:{_docker_container_port(node)}"],
            "volumes": [
                f"{volume_root}/env:/env",
                f"{volume_root}/logs:/logs",
            ],
            "user": runtime_user,
            "read_only": True,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "tmpfs": ["/tmp", "/run"],
            "entrypoint": (["/bin/sh", "-ec"] if node["impl"] == "amaru" else ["bash", "-lc"]),
            "command": command,
        }
        if node["impl"] == "amaru":
            services[node["id"]]["volumes"].append(f"{volume_root}/amaru:/amaru")
            services[node["id"]]["environment"] = {
                "AMARU_MIGRATE_CHAIN_DB": "true",
                # `tip.adopt` is INFO at most once per second and DEBUG for
                # suppressed catch-up blocks. The readiness gate must observe
                # actual adoption, not infer it from a ChainSync intersection.
                "AMARU_LOG": "error,amaru=info,amaru_pure_stage=warn,amaru::consensus=debug",
            }
            if amaru_txsubmission_trace:
                services[node["id"]]["environment"].update(
                    {
                        "AMARU_TRACE_EMIT_PRIVATE": "true",
                        "AMARU_LOG": (
                            "error,amaru=info,amaru_pure_stage=warn,"
                            "amaru::consensus=debug,amaru::protocols=debug"
                        ),
                    }
                )
        elif node.get("target_mode") == "patched":
            services[node["id"]]["environment"] = {
                "DWARF_CARDANO_MEASUREMENT_TRACE": (
                    f"/logs/{node['id']}/cardano-measurement.ndjson"
                ),
                "DWARF_CARDANO_PLUTUS_TRACE": (
                    f"/logs/{node['id']}/cardano-plutus.ndjson"
                ),
            }
    network: dict[str, object] = {"name": f"{compose_project}-net"}
    if controller_ownership:
        network["labels"] = {
            "io.dwarf.managed": "true",
            "io.dwarf.installation": str(controller_ownership["installation_id"]),
            "io.dwarf.topology": str(controller_ownership["topology_id"]),
            "io.dwarf.component": "network",
        }
    return {
        "name": compose_project,
        "services": services,
        "networks": {"default": network},
    }


def _runtime_user(runtime_root: Path) -> str:
    owner = runtime_root.stat()
    return f"{owner.st_uid}:{owner.st_gid}"


def _inspect_docker_node(*, compose_project: str, node: dict) -> dict:
    container_name = f"{compose_project}-{node['id']}-1"
    inspect = run_command(["docker", "inspect", container_name])
    if inspect.returncode != 0:
        raise RuntimeError(f"docker inspect failed for {container_name}: {inspect.stderr or inspect.stdout}")
    body = json.loads(inspect.stdout)[0]
    network_name = f"{compose_project}-net"
    network_entry = body.get("NetworkSettings", {}).get("Networks", {}).get(network_name) or {}
    container_ip = network_entry.get("IPAddress", "")
    result = {
        "container_name": container_name,
        "container_id": body.get("Id", ""),
        "container_image_id": body.get("Image", ""),
        "container_network": network_name,
        "container_ip": container_ip,
        "container_listen_address": f"{node['id']}:{_docker_container_port(node)}",
        "container_socket_path": _docker_container_socket_path(node),
    }
    if node["impl"] == "amaru":
        result["submit_api_url"] = f"http://{node['id']}:3011/api/submit/tx"
    return result


def _inspect_docker_node_remote(*, compose_project: str, node: dict, host: dict) -> dict:
    container_name = f"{compose_project}-{node['id']}-1"
    inspect = _run_remote_command(host, f"docker inspect {json.dumps(container_name)}")
    if inspect.returncode != 0:
        raise RuntimeError(f"docker inspect failed for {container_name} on {host['id']}: {inspect.stderr or inspect.stdout}")
    body = json.loads(inspect.stdout)[0]
    network_name = f"{compose_project}-net"
    network_entry = body.get("NetworkSettings", {}).get("Networks", {}).get(network_name) or {}
    container_ip = network_entry.get("IPAddress", "")
    return {
        "container_name": container_name,
        "container_id": body.get("Id", ""),
        "container_image_id": body.get("Image", ""),
        "container_network": network_name,
        "container_ip": container_ip,
        "container_listen_address": f"{node['id']}:{_docker_container_port(node)}",
        "container_socket_path": _docker_container_socket_path(node),
    }


def _compose_substrate_docker(
    *,
    plan: dict,
    output_dir: Path,
    runtime_root: Path,
    compose_project: str,
    healthy_timeout_seconds: float,
    support: dict[str, str],
) -> dict:
    docker_project = _docker_project_name(compose_project)
    start_time_refresh = None
    haskell_nodes = [node for node in plan["nodes"] if node["impl"] == "cardano-node"]
    public_network = _is_public_network(plan["network"])
    public_assets = None
    resolved_public_network_magic = None
    cardano_testnet_config_compatibility = None
    generated_workload_funding = None
    kes_genesis_override_evidence = None
    governance_guardrails_fixture_evidence = None
    env_root = runtime_root / "env"
    if haskell_nodes and public_network:
        public_assets = _stage_public_network_assets(
            network_name=plan["network"],
            destination=runtime_root / "public-network" / plan["network"],
            config_source_dir=plan.get("config_source_dir"),
        )
        env_root.mkdir(parents=True, exist_ok=True)
        for asset_name, asset_path in public_assets.items():
            destination_name = "configuration.yaml" if asset_name == "config.json" else asset_name
            shutil.copy2(asset_path, env_root / destination_name)
        config_path = env_root / "configuration.yaml"
        topology_template = json.loads(Path(public_assets["topology.json"]).read_text(encoding="utf-8"))
        topology_template, upstream_access_points = _public_topology_with_upstream(
            topology_template,
            str(plan.get("upstream_peer_address") or ""),
        )
        resolved_public_network_magic = _resolve_public_network_magic(
            network_name=plan["network"], config_path=config_path
        )
        for node in haskell_nodes:
            node["public_network"] = True
            node["socket_path"] = str(env_root / "socket" / node["id"] / "sock")
            node["db_dir"] = str(env_root / "node-data" / f"node{node['slot_index']}" / "db")
            topo_path = env_root / "node-data" / f"node{node['slot_index']}" / "topology.json"
            node["topology_path"] = str(topo_path)
            node["config_path"] = str(config_path)
            _rewrite_haskell_topology(
                node,
                topo_path=topo_path,
                template=topology_template,
                access_points=upstream_access_points,
                peer_snapshot_path=(
                    Path("/env/peer-snapshot.json")
                    if public_assets.get("peer-snapshot.json")
                    else None
                ),
            )
    elif haskell_nodes:
        host_cardano_node_binary = None
        for node in haskell_nodes:
            candidate = str(node.get("resolved_binary") or "").strip()
            if candidate and not candidate.startswith("docker-image:"):
                host_cardano_node_binary = candidate
                break
        if host_cardano_node_binary is None:
            host_cardano_node_binary = _resolve_support_binary("cardano-node")
        create_env = run_command(
            _packaged_cardano_testnet_create_env_command(
                output_dir=env_root,
                pool_count=len(haskell_nodes),
                network_magic=int(plan["network_magic"]),
                binary=support["cardano-testnet"],
            ),
            env=_cardano_testnet_env(
                cardano_node_binary=host_cardano_node_binary,
                cardano_cli_binary=support["cardano-cli"],
            ),
        )
        if create_env.returncode != 0:
            raise RuntimeError(f"cardano-testnet create-env failed: {create_env.stderr or create_env.stdout}")
        cardano_testnet_config_compatibility = _ensure_cardano_testnet_config_compatibility(
            config_path=env_root / "configuration.yaml",
            nodes=haskell_nodes,
        )
        _rewrite_cardano_testnet_genesis_for_older_cardano_nodes(
            genesis_path=env_root / "shelley-genesis.json",
            nodes=haskell_nodes,
        )
        measurement_trace_config = (
            _enable_cardano_measurement_traces(env_root / "configuration.yaml")
            if plan.get("cardano_measurement_traces") is True
            else {"enabled": False, "namespaces": []}
        )
        for node in haskell_nodes:
            node["socket_path"] = str(env_root / "socket" / node["id"] / "sock")
            node["db_dir"] = str(env_root / "node-data" / f"node{node['slot_index']}" / "db")
            topo_path = env_root / "node-data" / f"node{node['slot_index']}" / "topology.json"
            node["topology_path"] = str(topo_path)
            node["config_path"] = str(env_root / "configuration.yaml")
            container_access_points = [
                {"address": edge["to"], "port": _docker_container_port(next(peer for peer in plan["nodes"] if peer["id"] == edge["to"]))}
                for edge in plan["topology"]["edges"]
                if edge["from"] == node["id"]
            ]
            template = json.loads(topo_path.read_text(encoding="utf-8"))
            _rewrite_haskell_topology(node, topo_path=topo_path, template=template, access_points=container_access_points)
    elif not public_network:
        raise RuntimeError("docker compose custom devnet requires at least one cardano-node")

    for node in plan["nodes"]:
        if node["impl"] == "amaru":
            Path(node["state_root"]).mkdir(parents=True, exist_ok=True)
            node["container_peer_addresses"] = [
                f"{edge['to']}:{_docker_container_port(next(peer for peer in plan['nodes'] if peer['id'] == edge['to']))}"
                for edge in plan["topology"]["edges"]
                if edge["from"] == node["id"]
            ]
            upstream_peer = str(plan.get("upstream_peer_address") or "").strip()
            if upstream_peer:
                node["fallback_peer_addresses"] = [upstream_peer]

    uses_synthetic_amaru_bootstrap = (
        any(node["impl"] == "amaru" for node in plan["nodes"])
        and plan["network"].startswith("testnet_")
    )
    if uses_synthetic_amaru_bootstrap:
        guardrails_request = _prepare_governance_guardrails_fixture_request(
            fixture=plan.get("governance_guardrails_fixture"),
        )
        if guardrails_request:
            (env_root / "generated-governance-guardrails.json").write_text(
                json.dumps(guardrails_request, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        _prepare_generated_workload_funding(
            env_root=env_root,
            cardano_cli=support["cardano-cli"],
            network_magic=int(plan["network_magic"]),
        )
        bootstrap_result = _synthesize_amaru_bootstrap_for_custom_testnet(
            runtime_root=runtime_root, plan=plan
        )
        generated_workload_funding = bootstrap_result.get(
            "generated_workload_funding"
        )
        governance_guardrails_fixture_evidence = bootstrap_result.get(
            "governance_guardrails_fixture"
        )
        if guardrails_request and not isinstance(
            governance_guardrails_fixture_evidence, dict
        ):
            raise RuntimeError("Amaru bootstrap omitted governance guardrails evidence")
        if not isinstance(generated_workload_funding, dict):
            raise RuntimeError("Amaru bootstrap omitted generated workload funding")
    elif not public_network:
        start_time_refresh = _refresh_cardano_testnet_start_times(env_root=runtime_root / "env")
    if plan.get("kes_genesis_override"):
        if any(node["impl"] == "amaru" for node in plan["nodes"]):
            raise RuntimeError(
                "short-KES is not qualified for mixed Cardano/Amaru compose runtimes"
            )
        if public_network:
            raise RuntimeError("short-KES requires a generated private Cardano devnet")
        kes_genesis_override_evidence = _apply_kes_genesis_override_to_shelley(
            genesis_path=env_root / "shelley-genesis.json",
            override=plan["kes_genesis_override"],
        )

    compose_body = _docker_compose_body(
        compose_project=docker_project,
        nodes=plan["nodes"],
        network_name=plan["network"],
        network_magic=plan.get("network_magic"),
        controller_ownership=plan.get("controller_ownership"),
        docker_host_runtime_root=plan.get("docker_host_runtime_root"),
        amaru_txsubmission_trace=bool(
            plan.get("amaru_txsubmission_trace", False)
        ),
        runtime_user=_runtime_user(runtime_root),
    )
    if plan.get("controller_ownership"):
        for service in compose_body["services"].values():
            for published in service.get("ports") or []:
                fields = str(published).rsplit(":", 2)
                if len(fields) >= 2 and fields[-2] == "8787":
                    raise RuntimeError("controller-owned topology may not publish port 8787")
    compose_path = runtime_root / "docker-compose.yml"
    compose_path.write_text(json.dumps(compose_body, indent=2) + "\n", encoding="utf-8")

    up = run_command(["docker", "compose", "--project-name", docker_project, "up", "-d"], cwd=runtime_root)
    if up.returncode != 0:
        raise RuntimeError(f"docker compose up failed: {up.stderr or up.stdout}")

    report_nodes = []
    metadata_nodes = []
    for node in plan["nodes"]:
        container = _inspect_docker_node(compose_project=docker_project, node=node)
        artifact_identity = verify_running_container_artifact(container["container_image_id"], node)
        version_identity = verify_running_node_version(container["container_name"], node)
        if not artifact_identity["satisfied"]:
            raise RuntimeError(
                f"running image identity mismatch for {node['id']}: "
                f"expected {artifact_identity['expected_image_id']}, got {artifact_identity['container_image_id']}"
            )
        if not version_identity["satisfied"]:
            raise RuntimeError(
                f"running version mismatch for {node['id']}: "
                f"requested {version_identity['requested_version']}, reported {version_identity['reported_version']}"
            )
        container_peer_addresses = [
            f"{edge['to']}:{_docker_container_port(next(peer for peer in plan['nodes'] if peer['id'] == edge['to']))}"
            for edge in plan["topology"]["edges"]
            if edge["from"] == node["id"]
        ]
        metadata_node = {
            **node,
            "compose_mode": "docker",
            **container,
            "container_peer_addresses": container_peer_addresses,
            "image_ref": _docker_image_ref(node),
            "artifact_identity": artifact_identity,
            "version_identity": version_identity,
        }
        metadata_nodes.append(metadata_node)
        report_nodes.append(
            {
                "id": node["id"],
                "impl": node["impl"],
                "role": node["role"],
                "version": node["version"],
                "resolved_binary": node["resolved_binary"],
                "resolved_version": node["resolved_version"],
                "listen_address": node["listen_address"],
                "peer_addresses": node["peer_addresses"],
                "effective_peer_address": node["effective_peer_address"],
                "unsupported_extra_peer_addresses": node["unsupported_extra_peer_addresses"],
                "compose_mode": "docker",
                **container,
                "container_peer_addresses": container_peer_addresses,
                "image_ref": _docker_image_ref(node),
                "artifact_identity": artifact_identity,
                "version_identity": version_identity,
            }
        )

    metadata = {
        "compose_mode": "docker",
        "profile_id": str(plan.get("profile_id") or compose_project),
        "runtime_root": str(runtime_root),
        "compose_project": docker_project,
        "network": plan["network"],
        "network_magic": resolved_public_network_magic if public_network else plan["network_magic"],
        "support_binaries": dict(support),
        "cardano_testnet_start_time_refresh": start_time_refresh,
        "cardano_testnet_config_compatibility": cardano_testnet_config_compatibility,
        "generated_workload_funding": generated_workload_funding,
        "governance_guardrails_fixture": governance_guardrails_fixture_evidence,
        "kes_genesis_override": kes_genesis_override_evidence,
        "nodes": metadata_nodes,
        "haskell_nodes": [node for node in metadata_nodes if node["impl"] == "cardano-node"],
        "amaru_nodes": [node for node in metadata_nodes if node["impl"] == "amaru"],
        "topology": plan["topology"],
        "aux_sessions": [],
        "faults": [],
        "era_transition": {},
        "version_provenance": build_version_provenance(plan, metadata_nodes),
        "cardano_measurement_traces": measurement_trace_config if haskell_nodes and not public_network else {"enabled": False, "namespaces": []},
        "amaru_txsubmission_trace": bool(
            plan.get("amaru_txsubmission_trace", False)
        ),
    }
    metadata_path = runtime_root / "runtime.json"
    write_json(metadata_path, metadata)
    bundle_metadata_path = _emit_bundle_runtime_view(output_dir=output_dir, metadata_path=metadata_path, metadata=metadata)

    health = wait_for_nodes_healthy(plan["nodes"], timeout_seconds=healthy_timeout_seconds)
    health_by_id = {entry["id"]: entry["healthy"] for entry in health}
    for node in report_nodes:
        node["healthy"] = bool(health_by_id.get(node["id"], False))

    chain_progress_gate = None
    if haskell_nodes:
        chain_network_magic = (
            resolved_public_network_magic if public_network else plan["network_magic"]
        )
        if chain_network_magic is None:
            raise RuntimeError(
                f"cardano network magic is unavailable for {plan['network']} chain progress gate"
            )
        chain_progress_gate = _wait_for_cardano_node_block(
            runtime_metadata_path=bundle_metadata_path,
            nodes=[node for node in metadata_nodes if node["impl"] == "cardano-node"],
            network_magic=int(chain_network_magic),
            # Docker-backed nodes are queried with `docker exec ... cardano-cli`.
            # Public-network plans intentionally have no controller-local support
            # binaries, so retain a harmless fallback for the non-container branch.
            cardano_cli=support.get("cardano-cli", "cardano-cli"),
        )
        write_json(output_dir / "cardano-chain-progress-gate.json", chain_progress_gate)
        if not chain_progress_gate.get("ready"):
            raise RuntimeError(
                "cardano compose substrate did not produce a real block before the startup gate timed out "
                f"(ready_node_count={chain_progress_gate.get('ready_node_count', 0)})"
            )

    amaru_nodes = [node for node in metadata_nodes if node["impl"] == "amaru"]
    amaru_progress_gate = None
    if amaru_nodes:
        amaru_progress_gate = _wait_for_amaru_progress(
            nodes=amaru_nodes,
            timeout_seconds=healthy_timeout_seconds,
        )
        write_json(output_dir / "amaru-chain-progress-gate.json", amaru_progress_gate)
        if not amaru_progress_gate.get("ready"):
            raise RuntimeError(
                "amaru compose substrate did not intersect, advance, and converge before the startup gate failed "
                f"(ready_node_count={amaru_progress_gate.get('ready_node_count', 0)}, "
                f"blocking_signals={amaru_progress_gate.get('blocking_signals', {})})"
            )

    report = {
        "compose_mode": "docker",
        "healthy": all(node["healthy"] for node in report_nodes),
        "node_count": len(report_nodes),
        "runtime_root": str(runtime_root),
        "runtime_metadata_path": str(metadata_path),
        "bundle_runtime_metadata_path": str(bundle_metadata_path),
        "compose_project": docker_project,
        "network": plan["network"],
        "network_magic": resolved_public_network_magic if public_network else plan["network_magic"],
        "support_binaries": dict(support),
        "cardano_testnet_start_time_refresh": start_time_refresh,
        "cardano_testnet_config_compatibility": cardano_testnet_config_compatibility,
        "generated_workload_funding": generated_workload_funding,
        "amaru_txsubmission_trace": bool(
            plan.get("amaru_txsubmission_trace", False)
        ),
        "governance_guardrails_fixture": governance_guardrails_fixture_evidence,
        "kes_genesis_override": kes_genesis_override_evidence,
        "cardano_chain_progress_gate": chain_progress_gate,
        "amaru_chain_progress_gate": amaru_progress_gate,
        "public_network_assets": public_assets,
        "nodes": report_nodes,
        "topology": plan["topology"],
        "version_provenance": build_version_provenance(plan, metadata_nodes),
    }
    write_json(output_dir / "compose-report.json", report)
    return report


def _compose_substrate_docker_multihost(
    *,
    plan: dict,
    output_dir: Path,
    runtime_root: Path,
    compose_project: str,
    healthy_timeout_seconds: float,
    support: dict[str, str],
) -> dict:
    hosts = list(plan.get("hosts") or [])
    if not hosts:
        raise RuntimeError("multihost docker compose requires at least one host")
    host_reports = []
    metadata_nodes = []
    report_nodes = []
    public_network = _is_public_network(plan["network"])
    public_assets = None
    resolved_public_network_magic = None
    topology_template = None
    peer_snapshot_path = None
    config_source_path = None
    if public_network:
        public_assets = _download_public_network_assets(
            network_name=plan["network"],
            destination=runtime_root / "public-network" / plan["network"],
        )
        topology_template = json.loads(Path(public_assets["topology.json"]).read_text(encoding="utf-8"))
        peer_snapshot_path = Path(public_assets["peer-snapshot.json"])
        config_source_path = Path(public_assets["config.json"])
        resolved_public_network_magic = _resolve_public_network_magic(
            network_name=plan["network"],
            config_path=config_source_path,
        )

    for host in hosts:
        host_nodes = [node for node in plan["nodes"] if node["host_id"] == host["id"]]
        host_local_root = _host_local_root(runtime_root=runtime_root, host_id=host["id"])
        host_local_root.mkdir(parents=True, exist_ok=True)
        (host_local_root / "logs").mkdir(parents=True, exist_ok=True)
        docker_project = _host_compose_project(compose_project=compose_project, host_id=host["id"])
        remote_runtime_root = _host_runtime_root(host=host, compose_project=compose_project)

        haskell_nodes = [node for node in host_nodes if node["impl"] == "cardano-node"]
        remote_prepare = _run_remote_command(host, f"mkdir -p {json.dumps(remote_runtime_root)}")
        if remote_prepare.returncode != 0:
            raise RuntimeError(f"failed to prepare remote runtime root for {host['id']}: {remote_prepare.stderr or remote_prepare.stdout}")
        start_time_refresh = None

        if haskell_nodes and public_network:
            env_root = host_local_root / "env"
            env_root.mkdir(parents=True, exist_ok=True)
            for asset_name, asset_path in (public_assets or {}).items():
                source = Path(asset_path)
                destination_name = "configuration.yaml" if asset_name == "config.json" else asset_name
                shutil.copy2(source, env_root / destination_name)
            for node in haskell_nodes:
                node["public_network"] = True
                node["socket_path"] = f"{remote_runtime_root}/env/socket/{node['id']}/sock"
                node["db_dir"] = f"{remote_runtime_root}/env/node-data/node{node['host_slot_index']}/db"
                node["topology_path"] = f"{remote_runtime_root}/env/node-data/node{node['host_slot_index']}/topology.json"
                node["config_path"] = f"{remote_runtime_root}/env/configuration.yaml"
                local_topology_dir = env_root / "node-data" / f"node{node['host_slot_index']}"
                local_topology_dir.mkdir(parents=True, exist_ok=True)
                local_topology_path = local_topology_dir / "topology.json"
                container_access_points = []
                for edge in plan["topology"]["edges"]:
                    if edge["from"] != node["id"]:
                        continue
                    peer = next(peer for peer in plan["nodes"] if peer["id"] == edge["to"])
                    if peer["host_id"] == host["id"]:
                        container_access_points.append({"address": peer["id"], "port": _docker_container_port(peer)})
                    else:
                        peer_host = next(item for item in hosts if item["id"] == peer["host_id"])
                        container_access_points.append({"address": peer_host["published_host"], "port": peer["port"]})
                _rewrite_haskell_topology(
                    node,
                    topo_path=local_topology_path,
                    template=topology_template or {},
                    peer_snapshot_path=env_root / "peer-snapshot.json",
                    access_points=container_access_points,
                )
        elif haskell_nodes:
            image_ref = _docker_image_ref(haskell_nodes[0])
            create_env = _run_remote_command(
                host,
                " && ".join(
                    [
                        f"mkdir -p {json.dumps(remote_runtime_root)}",
                        (
                            "docker run --rm "
                            f"-v {json.dumps(remote_runtime_root)}:/work "
                            "--entrypoint /usr/local/bin/cardano-testnet "
                            f"{json.dumps(image_ref)} "
                            f"create-env --output /work/env --num-pool-nodes {len(haskell_nodes)} "
                            f"--testnet-magic {plan['network_magic']} --node-logging-format json"
                        ),
                    ]
                ),
            )
            if create_env.returncode != 0:
                raise RuntimeError(f"cardano-testnet create-env failed for {host['id']}: {create_env.stderr or create_env.stdout}")
            older_nodes = [node for node in haskell_nodes if _requires_legacy_cardano_testnet_genesis_fields(node.get("version"))]
            if older_nodes:
                remote_genesis_compat = _run_remote_command(
                    host,
                    "python3 - "
                    + " ".join(
                        [
                            json.dumps(f"{remote_runtime_root}/env/shelley-genesis.json"),
                        ]
                    )
                    + " <<'PY'\n"
                    + "import json, sys\n"
                    + "path = sys.argv[1]\n"
                    + "body = json.load(open(path, encoding='utf-8'))\n"
                    + "pools = ((body.get('staking') or {}).get('pools') or {})\n"
                    + "changed = False\n"
                    + "for pool_id, pool_body in pools.items():\n"
                    + "    if not isinstance(pool_body, dict):\n"
                    + "        continue\n"
                    + "    if 'publicKey' not in pool_body:\n"
                    + "        pool_body['publicKey'] = str(pool_body.get('poolId') or pool_id)\n"
                    + "        changed = True\n"
                    + "    if 'rewardAccount' not in pool_body and 'accountAddress' in pool_body:\n"
                    + "        pool_body['rewardAccount'] = pool_body['accountAddress']\n"
                    + "        changed = True\n"
                    + "if changed:\n"
                    + "    json.dump(body, open(path, 'w', encoding='utf-8'), indent=4)\n"
                    + "    open(path, 'a', encoding='utf-8').write('\\n')\n"
                    + "PY",
                )
                if remote_genesis_compat.returncode != 0:
                    raise RuntimeError(
                        f"failed to rewrite shelley genesis for older cardano-node on {host['id']}: {remote_genesis_compat.stderr or remote_genesis_compat.stdout}"
                    )
            start_time_refresh = _refresh_cardano_testnet_start_times_remote(host=host, env_root=f"{remote_runtime_root}/env")
            for node in haskell_nodes:
                node["socket_path"] = f"{remote_runtime_root}/env/socket/{node['id']}/sock"
                node["db_dir"] = f"{remote_runtime_root}/env/node-data/node{node['host_slot_index']}/db"
                node["topology_path"] = f"{remote_runtime_root}/env/node-data/node{node['host_slot_index']}/topology.json"
                node["config_path"] = f"{remote_runtime_root}/env/configuration.yaml"
                container_access_points = []
                for edge in plan["topology"]["edges"]:
                    if edge["from"] != node["id"]:
                        continue
                    peer = next(peer for peer in plan["nodes"] if peer["id"] == edge["to"])
                    if peer["host_id"] == host["id"]:
                        container_access_points.append({"address": peer["id"], "port": _docker_container_port(peer)})
                    else:
                        peer_host = next(item for item in hosts if item["id"] == peer["host_id"])
                        container_access_points.append({"address": peer_host["published_host"], "port": peer["port"]})
                remote_topology_update = _run_remote_command(
                    host,
                    "python3 - "
                    + " ".join(
                        [
                            json.dumps(node["topology_path"]),
                            json.dumps(json.dumps(container_access_points)),
                        ]
                    )
                    + " <<'PY'\n"
                    + "import json, sys\n"
                    + "path = sys.argv[1]\n"
                    + "access_points = json.loads(sys.argv[2])\n"
                    + "body = json.load(open(path, encoding='utf-8'))\n"
                    + "body['localRoots'] = [\n"
                    + "        {\n"
                    + "            'accessPoints': access_points,\n"
                    + "            'advertise': False,\n"
                    + "            'behindFirewall': False,\n"
                    + "            'diffusionMode': 'InitiatorAndResponder',\n"
                    + "            'hotValency': len(access_points),\n"
                    + "            'trustable': bool(access_points),\n"
                    + "            'warmValency': len(access_points),\n"
                    + "        }\n"
                    + "    ] if access_points else [{\n"
                    + "        'accessPoints': [],\n"
                    + "        'advertise': False,\n"
                    + "        'behindFirewall': False,\n"
                    + "        'diffusionMode': 'InitiatorAndResponder',\n"
                    + "        'hotValency': 0,\n"
                    + "        'trustable': False,\n"
                    + "        'warmValency': 0,\n"
                    + "    }]\n"
                    + "body.setdefault('publicRoots', [{'accessPoints': [], 'advertise': False}])\n"
                    + "body['useLedgerAfterSlot'] = -1\n"
                    + "json.dump(body, open(path, 'w', encoding='utf-8'), indent=2)\n"
                    + "open(path, 'a', encoding='utf-8').write('\\n')\n"
                    + "PY",
                )
                if remote_topology_update.returncode != 0:
                    raise RuntimeError(
                        f"failed to rewrite topology for {node['id']} on {host['id']}: {remote_topology_update.stderr or remote_topology_update.stdout}"
                    )

        for node in host_nodes:
            if node["impl"] == "amaru":
                host_state_root = host_local_root / "amaru" / node["id"]
                host_state_root.mkdir(parents=True, exist_ok=True)
                node["container_peer_addresses"] = [
                    (
                        f"{edge['to']}:{_docker_container_port(next(peer for peer in plan['nodes'] if peer['id'] == edge['to']))}"
                        if next(peer for peer in plan["nodes"] if peer["id"] == edge["to"])["host_id"] == host["id"]
                        else (
                            f"{next(item for item in hosts if item['id'] == next(peer for peer in plan['nodes'] if peer['id'] == edge['to'])['host_id'])['published_host']}:"
                            f"{next(peer for peer in plan['nodes'] if peer['id'] == edge['to'])['port']}"
                        )
                    )
                    for edge in plan["topology"]["edges"]
                    if edge["from"] == node["id"]
                ]
        compose_body = _docker_compose_body(compose_project=docker_project, nodes=host_nodes, network_name=plan["network"])
        compose_path = host_local_root / "docker-compose.yml"
        compose_path.write_text(json.dumps(compose_body, indent=2) + "\n", encoding="utf-8")

        sync = _rsync_runtime_to_host(local_root=host_local_root, host=host, remote_root=remote_runtime_root)
        if sync.returncode != 0:
            raise RuntimeError(f"failed to sync runtime package to {host['id']}: {sync.stderr or sync.stdout}")
        up = _run_remote_command(
            host,
            f"cd {json.dumps(remote_runtime_root)} && docker compose --project-name {json.dumps(docker_project)} up -d",
        )
        if up.returncode != 0:
            raise RuntimeError(f"docker compose up failed on {host['id']}: {up.stderr or up.stdout}")

        host_report_nodes = []
        for node in host_nodes:
            node["health_probe"] = "port-only"
            container = _inspect_docker_node_remote(compose_project=docker_project, node=node, host=host)
            artifact_identity = verify_running_container_artifact(container["container_image_id"], node)
            version_identity = verify_running_node_version(
                container["container_name"],
                node,
                runner=lambda command, _host=host, **_: _run_remote_command(
                    _host, " ".join(json.dumps(str(part)) for part in command)
                ),
            )
            if not artifact_identity["satisfied"]:
                raise RuntimeError(f"running image identity mismatch for {node['id']} on {host['id']}")
            if not version_identity["satisfied"]:
                raise RuntimeError(f"running version mismatch for {node['id']} on {host['id']}")
            metadata_node = {
                **node,
                "compose_mode": "docker",
                "host_id": host["id"],
                "host_ssh_target": host["ssh_target"],
                "host_published_address": host["published_host"],
                "runtime_root": remote_runtime_root,
                **container,
                "container_peer_addresses": node["container_peer_addresses"],
                "image_ref": _docker_image_ref(node),
                "artifact_identity": artifact_identity,
                "version_identity": version_identity,
            }
            metadata_nodes.append(metadata_node)
            node_report = {
                "id": node["id"],
                "impl": node["impl"],
                "role": node["role"],
                "version": node["version"],
                "resolved_binary": node["resolved_binary"],
                "resolved_version": node["resolved_version"],
                "listen_address": node["listen_address"],
                "peer_addresses": node["peer_addresses"],
                "effective_peer_address": node["effective_peer_address"],
                "unsupported_extra_peer_addresses": node["unsupported_extra_peer_addresses"],
                "compose_mode": "docker",
                "host_id": host["id"],
                "host_ssh_target": host["ssh_target"],
                "host_published_address": host["published_host"],
                "runtime_root": remote_runtime_root,
                **container,
                "container_peer_addresses": node["container_peer_addresses"],
                "image_ref": _docker_image_ref(node),
                "artifact_identity": artifact_identity,
                "version_identity": version_identity,
            }
            host_report_nodes.append(node_report)
            report_nodes.append(node_report)

        host_report = {
            "compose_mode": "docker",
            "multi_host": True,
            "host_id": host["id"],
            "compose_project": docker_project,
            "runtime_root": remote_runtime_root,
            "network": plan["network"],
            "network_magic": resolved_public_network_magic if public_network else plan["network_magic"],
            "cardano_testnet_start_time_refresh": start_time_refresh,
            "node_count": len(host_report_nodes),
            "nodes": host_report_nodes,
            "topology": plan["topology"],
        }
        write_json(output_dir / "hosts" / host["id"] / "compose-report.json", host_report)
        host_reports.append(
            {
                "id": host["id"],
                "ssh_target": host["ssh_target"],
                "published_host": host["published_host"],
                "runtime_root": remote_runtime_root,
                "compose_project": docker_project,
                "node_count": len(host_report_nodes),
                "cardano_testnet_start_time_refresh": start_time_refresh,
                "compose_report_path": str(output_dir / "hosts" / host["id"] / "compose-report.json"),
            }
        )

    metadata = {
        "compose_mode": "docker",
        "multi_host": True,
        "host_strategy": plan["host_strategy"],
        "profile_id": str(plan.get("profile_id") or compose_project),
        "runtime_root": str(runtime_root),
        "compose_project": compose_project,
        "network": plan["network"],
        "network_magic": resolved_public_network_magic if public_network else plan["network_magic"],
        "support_binaries": dict(support),
        "cardano_testnet_start_time_refresh": [
            host.get("cardano_testnet_start_time_refresh")
            for host in host_reports
            if host.get("cardano_testnet_start_time_refresh") is not None
        ],
        "hosts": host_reports,
        "nodes": metadata_nodes,
        "haskell_nodes": [node for node in metadata_nodes if node["impl"] == "cardano-node"],
        "amaru_nodes": [node for node in metadata_nodes if node["impl"] == "amaru"],
        "topology": plan["topology"],
        "aux_sessions": [],
        "faults": [],
        "era_transition": {},
        "version_provenance": build_version_provenance(plan, metadata_nodes),
    }
    metadata_path = runtime_root / "runtime.json"
    write_json(metadata_path, metadata)
    bundle_metadata_path = _emit_bundle_runtime_view(output_dir=output_dir, metadata_path=metadata_path, metadata=metadata)

    health = wait_for_nodes_healthy(plan["nodes"], timeout_seconds=healthy_timeout_seconds)
    health_by_id = {entry["id"]: entry["healthy"] for entry in health}
    for node in report_nodes:
        node["healthy"] = bool(health_by_id.get(node["id"], False))
    for host in host_reports:
        host_report_path = Path(host["compose_report_path"])
        host_report = json.loads(host_report_path.read_text(encoding="utf-8"))
        for node in host_report["nodes"]:
            node["healthy"] = bool(health_by_id.get(node["id"], False))
        host_report["healthy"] = all(node["healthy"] for node in host_report["nodes"])
        write_json(host_report_path, host_report)

    report = {
        "compose_mode": "docker",
        "multi_host": True,
        "host_strategy": plan["host_strategy"],
        "healthy": all(node["healthy"] for node in report_nodes),
        "host_count": len(host_reports),
        "node_count": len(report_nodes),
        "runtime_root": str(runtime_root),
        "runtime_metadata_path": str(metadata_path),
        "bundle_runtime_metadata_path": str(bundle_metadata_path),
        "compose_project": compose_project,
        "network": plan["network"],
        "network_magic": resolved_public_network_magic if public_network else plan["network_magic"],
        "support_binaries": dict(support),
        "public_network_assets": public_assets,
        "cardano_testnet_start_time_refresh": [
            host.get("cardano_testnet_start_time_refresh")
            for host in host_reports
            if host.get("cardano_testnet_start_time_refresh") is not None
        ],
        "hosts": host_reports,
        "nodes": report_nodes,
        "topology": plan["topology"],
        "version_provenance": build_version_provenance(plan, metadata_nodes),
    }
    write_json(output_dir / "compose-report.json", report)
    return report


def compose_substrate(
    *,
    substrate: dict,
    output_dir: Path,
    runtime_root: Path,
    compose_project: str,
    install_report: dict | None = None,
    healthy_timeout_seconds: float = 180.0,
) -> dict:
    normalized = normalize_substrate(substrate)
    compose_mode = str(substrate.get("compose_mode", "host"))
    image_backed = compose_mode == "docker"
    output_dir.mkdir(parents=True, exist_ok=True)
    runtime_root.mkdir(parents=True, exist_ok=True)
    for subdir in ("logs", "socket", "pids", "amaru"):
        (runtime_root / subdir).mkdir(parents=True, exist_ok=True)
    base_haskell_port, base_amaru_port = _port_bases(compose_project)
    plan = allocate_node_plan(
        normalized,
        runtime_root=runtime_root,
        compose_project=compose_project,
        base_haskell_port=base_haskell_port,
        base_amaru_port=base_amaru_port,
    )
    resolved = dict((install_report or {}).get("nodes") or {})
    for node in plan["nodes"]:
        if node["id"] not in resolved:
            if image_backed:
                resolved[node["id"]] = resolve_docker_image_for_node(node)
            else:
                resolved[node["id"]] = resolve_binary_for_node(node)
        node["resolved_binary"] = resolved[node["id"]]["resolved_binary"]
        node["resolved_version"] = resolved[node["id"]]["resolved_version"]
        for provenance_key in (
            "status",
            "image_ref",
            "image_id",
            "image_digest",
            "repo_digests",
            "requested_digest",
            "source_revision",
        ):
            if provenance_key in resolved[node["id"]]:
                node[f"artifact_{provenance_key}" if provenance_key == "status" else provenance_key] = resolved[node["id"]][provenance_key]
        if not resolved[node["id"]].get("satisfied"):
            raise RuntimeError(
                f"version artifact resolution failed for {node['id']}: {resolved[node['id']].get('status')}"
            )
        if not node["resolved_binary"] and not image_backed:
            raise RuntimeError(f"missing resolved binary for {node['id']}")

    plan["kes_genesis_override"] = substrate.get("kes_genesis_override")
    plan["governance_guardrails_fixture"] = substrate.get(
        "governance_guardrails_fixture"
    )
    plan["amaru_txsubmission_trace"] = bool(
        substrate.get("amaru_txsubmission_trace", False)
    )

    if compose_mode == "docker":
        if plan.get("host_strategy") == "explicit":
            return _compose_substrate_docker_multihost(
                plan=plan,
                output_dir=output_dir,
                runtime_root=runtime_root,
                compose_project=compose_project,
                healthy_timeout_seconds=healthy_timeout_seconds,
                support={},
            )
        support = {}
        if not _is_public_network(plan["network"]):
            support = {
                "cardano-testnet": _resolve_support_binary("cardano-testnet"),
                "cardano-cli": _resolve_support_binary("cardano-cli"),
            }
        return _compose_substrate_docker(
            plan=plan,
            output_dir=output_dir,
            runtime_root=runtime_root,
            compose_project=compose_project,
            healthy_timeout_seconds=healthy_timeout_seconds,
            support=support,
        )

    support = {
        "cardano-testnet": _resolve_support_binary("cardano-testnet"),
        "cardano-cli": _resolve_support_binary("cardano-cli"),
    }

    haskell_nodes = [node for node in plan["nodes"] if node["impl"] == "cardano-node"]
    amaru_nodes = [node for node in plan["nodes"] if node["impl"] == "amaru"]
    network_name = plan["network"]
    public_network = _is_public_network(network_name)
    public_assets = None
    resolved_public_network_magic = None
    start_time_refresh = None
    if haskell_nodes:
        if public_network:
            public_assets = _download_public_network_assets(
                network_name=network_name,
                destination=runtime_root / "public-network" / network_name,
            )
            topology_template = json.loads(Path(public_assets["topology.json"]).read_text(encoding="utf-8"))
            peer_snapshot_path = Path(public_assets["peer-snapshot.json"])
            config_path = Path(public_assets["config.json"])
            resolved_public_network_magic = _resolve_public_network_magic(network_name=network_name, config_path=config_path)
            for node in haskell_nodes:
                node["public_network"] = True
                node_topology_path = runtime_root / "cardano-topology" / f"{node['id']}.topology.json"
                node["topology_path"] = str(node_topology_path)
                node["config_path"] = str(config_path)
                Path(node["db_dir"]).mkdir(parents=True, exist_ok=True)
                _rewrite_haskell_topology(
                    node,
                    topo_path=node_topology_path,
                    template=topology_template,
                    peer_snapshot_path=peer_snapshot_path,
                )
                _launch_haskell_node(
                    node,
                    runtime_root=runtime_root,
                    binary_path=node["resolved_binary"],
                    public_network=True,
                )
        else:
            create_env = run_command(
                [
                    support["cardano-testnet"],
                    "create-env",
                    "--output",
                    str(runtime_root / "env"),
                    "--num-pool-nodes",
                    str(len(haskell_nodes)),
                    "--testnet-magic",
                    str(plan["network_magic"]),
                    "--node-logging-format",
                    "json",
                ],
                env=_cardano_testnet_env(
                    cardano_node_binary=haskell_nodes[0]["resolved_binary"],
                    cardano_cli_binary=support["cardano-cli"],
                ),
            )
            if create_env.returncode != 0:
                raise RuntimeError(f"cardano-testnet create-env failed: {create_env.stderr or create_env.stdout}")
            _rewrite_cardano_testnet_genesis_for_older_cardano_nodes(
                genesis_path=runtime_root / "env" / "shelley-genesis.json",
                nodes=haskell_nodes,
            )
            start_time_refresh = _refresh_cardano_testnet_start_times(env_root=runtime_root / "env")
            for node in haskell_nodes:
                topo_path = runtime_root / "env" / "node-data" / f"node{node['slot_index']}" / "topology.json"
                node["topology_path"] = str(topo_path)
                node["config_path"] = str(runtime_root / "env" / "configuration.yaml")
                node["db_dir"] = str(runtime_root / "env" / "node-data" / f"node{node['slot_index']}" / "db")
                _rewrite_haskell_topology(node, topo_path=topo_path, template=json.loads(topo_path.read_text(encoding="utf-8")))
                _launch_haskell_node(
                    node,
                    runtime_root=runtime_root,
                    binary_path=node["resolved_binary"],
                    public_network=False,
                )
    for node in amaru_nodes:
        if public_assets is not None:
            bootstrap_peers = json.loads(Path(public_assets["topology.json"]).read_text(encoding="utf-8")).get("bootstrapPeers") or []
            node["fallback_peer_addresses"] = [
                f"{entry['address']}:{entry['port']}"
                for entry in bootstrap_peers
                if isinstance(entry, dict) and entry.get("address") and entry.get("port")
            ]
        else:
            node["fallback_peer_addresses"] = []
        _launch_amaru_node(
            node,
            network_name=network_name,
            binary_path=node["resolved_binary"],
            runtime_root=runtime_root,
        )

    metadata = {
        "profile_id": str(plan.get("profile_id") or compose_project),
        "runtime_root": str(runtime_root),
        "compose_project": compose_project,
        "network": network_name,
        "network_magic": resolved_public_network_magic if public_network else plan["network_magic"],
        "cardano_testnet_start_time_refresh": start_time_refresh,
        "haskell_nodes": [node for node in plan["nodes"] if node["impl"] == "cardano-node"],
        "amaru_nodes": [node for node in plan["nodes"] if node["impl"] == "amaru"],
        "topology": plan["topology"],
        "aux_sessions": [],
        "faults": [],
        "era_transition": {},
        "version_provenance": build_version_provenance(plan, plan["nodes"]),
    }
    metadata_path = runtime_root / "runtime.json"
    write_json(metadata_path, metadata)
    bundle_metadata_path = _emit_bundle_runtime_view(output_dir=output_dir, metadata_path=metadata_path, metadata=metadata)
    health = wait_for_nodes_healthy(plan["nodes"], timeout_seconds=healthy_timeout_seconds)
    health_by_id = {entry["id"]: entry["healthy"] for entry in health}
    chain_progress_gate = None
    if haskell_nodes and not public_network:
        chain_progress_gate = _wait_for_cardano_node_block(
            runtime_metadata_path=bundle_metadata_path,
            nodes=[node for node in plan["nodes"] if node["impl"] == "cardano-node"],
            network_magic=int(plan["network_magic"]),
            cardano_cli=support["cardano-cli"],
        )
        write_json(output_dir / "cardano-chain-progress-gate.json", chain_progress_gate)
        if not chain_progress_gate.get("ready"):
            raise RuntimeError(
                "cardano host substrate did not produce a real block before the startup gate timed out "
                f"(ready_node_count={chain_progress_gate.get('ready_node_count', 0)})"
            )
    report_nodes = []
    for node in plan["nodes"]:
        report_nodes.append(
            {
                "id": node["id"],
                "impl": node["impl"],
                "role": node["role"],
                "version": node["version"],
                "resolved_binary": node["resolved_binary"],
                "resolved_version": node["resolved_version"],
                "listen_address": node["listen_address"],
                "session": node["session"],
                "peer_addresses": node["peer_addresses"],
                "effective_peer_address": node["effective_peer_address"],
                "unsupported_extra_peer_addresses": node["unsupported_extra_peer_addresses"],
                "healthy": bool(health_by_id.get(node["id"], False)),
            }
        )
    report = {
        "healthy": all(node["healthy"] for node in report_nodes),
        "node_count": len(report_nodes),
        "runtime_root": str(runtime_root),
        "runtime_metadata_path": str(metadata_path),
        "bundle_runtime_metadata_path": str(bundle_metadata_path),
        "compose_project": compose_project,
        "network": network_name,
        "network_magic": metadata["network_magic"],
        "support_binaries": support,
        "cardano_testnet_start_time_refresh": start_time_refresh,
        "cardano_chain_progress_gate": chain_progress_gate,
        "public_network_assets": public_assets,
        "nodes": report_nodes,
        "topology": plan["topology"],
        "version_provenance": build_version_provenance(plan, plan["nodes"]),
    }
    write_json(output_dir / "compose-report.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    install_report = None
    if config.get("install_report_path"):
        install_report = json.loads(Path(config["install_report_path"]).read_text(encoding="utf-8"))
    report = compose_substrate(
        substrate=config["substrate"],
        output_dir=Path(config["output_dir"]),
        runtime_root=Path(config["runtime_root"]),
        compose_project=config["compose_project"],
        install_report=install_report,
        healthy_timeout_seconds=float(config.get("healthy_timeout_seconds", 180)),
    )
    print(f"healthy={'true' if report['healthy'] else 'false'} nodes={report['node_count']}")
    return 0 if report["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
