"""Opt-in devnet pool relays (decision 2026-11-09).

Live run 20261109T193555Z-1ddc3ff8 (runtime-substrate-eclipse-topology-example-smoke):
ledger_peer_stake_weight_preserved and big_ledger_peer_quorum_intact were measured:false
because no stake pool on the self-composed devnet registered a relay, so the ledger had no
ledger peers. substrate.cardano_pool_relays: true registers one relay per pool (the pool
node's own container DNS name and container port) in the generated Shelley genesis. Off by
default: the genesis is left byte-for-byte as cardano-testnet wrote it.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import runtime_compose_substrate as compose
from scripts import runtime_substrate_common as common
from scripts import runtime_topology_fault as topo

ECLIPSE = Path("dwarf/scenarios/runtime-substrate-eclipse-topology-example-smoke.yaml")


def _cold_vkey(seed: int) -> tuple[str, str]:
    raw = bytes([seed]) * 32
    envelope = {"type": "StakePoolVerificationKey", "description": "Stake Pool Operator Verification Key",
                "cborHex": "5820" + raw.hex()}
    return json.dumps(envelope), hashlib.blake2b(raw, digest_size=28).hexdigest()


def _env(tmp_path, *, pool_count=2, legacy=True, extra=True):
    env_root = tmp_path / "env"
    pool_ids = []
    for index in range(1, pool_count + 1):
        text, pool_id = _cold_vkey(index)
        (env_root / "pools-keys" / f"pool{index}").mkdir(parents=True)
        (env_root / "pools-keys" / f"pool{index}" / "cold.vkey").write_text(text)
        pool_ids.append(pool_id)
    body = {"systemStart": "x", "staking": {"pools": {}, "stake": {}}}
    if legacy:
        body["staking"]["pools"] = {pid: {"publicKey": pid, "vrf": f"vrf{i}", "relays": []}
                                    for i, pid in enumerate(pool_ids)}
    if extra:
        body["extraConfig"] = {"stakePools": {"data": {pid: {"poolId": pid, "vrf": f"vrf{i}", "relays": []}
                                                       for i, pid in enumerate(pool_ids)}}}
    (env_root / "shelley-genesis.json").write_text(json.dumps(body, indent=4) + "\n")
    return env_root, pool_ids


def _nodes(count=2):
    nodes = [{"id": f"node{i}", "impl": "cardano-node", "host_slot_index": i} for i in range(1, count + 1)]
    return nodes + [{"id": "amaru1", "impl": "amaru", "host_slot_index": 1}]


# --- substrate key ------------------------------------------------------------------------------

def test_normalize_reads_the_opt_in_and_defaults_off():
    assert common._cardano_pool_relays({}) is False
    assert common._cardano_pool_relays({"cardano_pool_relays": True}) is True
    with pytest.raises(ValueError, match="cardano_pool_relays"):
        common._cardano_pool_relays({"cardano_pool_relays": "yes"})


def test_the_plan_carries_the_opt_in(tmp_path):
    substrate = {"network": "testnet_42", "compose_mode": "docker", "cardano_pool_relays": True,
                 "nodes": [{"id": "node1", "impl": "cardano-node", "version": "11.1.2", "role": "honest"}]}
    plan = compose.allocate_node_plan(substrate, runtime_root=tmp_path, compose_project="p")
    assert plan["cardano_pool_relays"] is True
    del substrate["cardano_pool_relays"]
    assert compose.allocate_node_plan(substrate, runtime_root=tmp_path, compose_project="p")["cardano_pool_relays"] is False


# --- genesis edit -------------------------------------------------------------------------------

def test_each_pool_registers_its_own_node_as_relay(tmp_path):
    env_root, pool_ids = _env(tmp_path)
    evidence = compose._register_cardano_pool_relays(env_root=env_root, nodes=_nodes(), enabled=True)
    body = json.loads((env_root / "shelley-genesis.json").read_text())
    for index, pool_id in enumerate(pool_ids, start=1):
        relay = [{"single host name": {"dnsName": f"node{index}", "port": 3001}}]
        assert body["staking"]["pools"][pool_id]["relays"] == relay
        assert body["extraConfig"]["stakePools"]["data"][pool_id]["relays"] == relay
    assert evidence == {"applied": True, "relays": {pool_ids[0]: "node1:3001", pool_ids[1]: "node2:3001"}}


def test_off_by_default_leaves_the_genesis_untouched(tmp_path):
    env_root, _ = _env(tmp_path)
    before = (env_root / "shelley-genesis.json").read_bytes()
    assert compose._register_cardano_pool_relays(env_root=env_root, nodes=_nodes(), enabled=False) == {"applied": False}
    assert (env_root / "shelley-genesis.json").read_bytes() == before


def test_new_genesis_layout_only_extra_config(tmp_path):
    env_root, pool_ids = _env(tmp_path, legacy=False)
    compose._register_cardano_pool_relays(env_root=env_root, nodes=_nodes(), enabled=True)
    body = json.loads((env_root / "shelley-genesis.json").read_text())
    assert body["staking"]["pools"] == {}
    assert body["extraConfig"]["stakePools"]["data"][pool_ids[1]]["relays"] == [
        {"single host name": {"dnsName": "node2", "port": 3001}}]


def test_a_pool_without_a_node_fails_visibly(tmp_path):
    env_root, _ = _env(tmp_path, pool_count=3)
    with pytest.raises(RuntimeError, match="no cardano-node owns"):
        compose._register_cardano_pool_relays(env_root=env_root, nodes=_nodes(2), enabled=True)


def test_a_genesis_without_pools_fails_visibly(tmp_path):
    env_root, _ = _env(tmp_path, legacy=False, extra=False)
    with pytest.raises(RuntimeError, match="no stake pools"):
        compose._register_cardano_pool_relays(env_root=env_root, nodes=_nodes(), enabled=True)


def test_compose_registers_relays_before_amaru_bootstrap_and_hash_pins():
    source = Path(compose.__file__).read_text(encoding="utf-8")
    docker_fn = source[source.index("def _compose_substrate_docker("):source.index("def _compose_substrate_docker_multihost(")]
    relays = docker_fn.index("_register_cardano_pool_relays(")
    assert docker_fn.index("_rewrite_cardano_testnet_genesis_for_older_cardano_nodes(") < relays
    assert relays < docker_fn.index("_synthesize_amaru_bootstrap_for_custom_testnet(")
    assert relays < docker_fn.index("genesis_hash_pins = _pin_cardano_genesis_hashes(")
    assert '"pool_relays": pool_relays' in docker_fn


def test_unsupported_compose_paths_refuse_the_opt_in():
    with pytest.raises(RuntimeError, match="cardano_pool_relays"):
        compose._refuse_unsupported_pool_relays({"cardano_pool_relays": True, "network": "preview"}, mode="docker")
    with pytest.raises(RuntimeError, match="cardano_pool_relays"):
        compose._refuse_unsupported_pool_relays({"cardano_pool_relays": True, "network": "testnet_42"}, mode="multi-host")
    compose._refuse_unsupported_pool_relays({"cardano_pool_relays": True, "network": "testnet_42"}, mode="docker")
    compose._refuse_unsupported_pool_relays({"cardano_pool_relays": False, "network": "preview"}, mode="host")


# --- scenario loader + scenarios ----------------------------------------------------------------

def test_scenario_loader_keeps_and_validates_the_opt_in():
    from profile_manager import scenario as scenario_module
    body = json.loads(ECLIPSE.read_text())
    assert scenario_module.scenario_from_body(json.dumps(body).encode()).substrate["cardano_pool_relays"] is True
    body["substrate"]["cardano_pool_relays"] = "yes"
    with pytest.raises(scenario_module.ScenarioValidationError, match="cardano_pool_relays"):
        scenario_module.scenario_from_body(json.dumps(body).encode())


def test_only_the_eclipse_scenario_opts_in():
    opted = sorted(p.name for p in Path("dwarf/scenarios").glob("*.yaml")
                   if "cardano_pool_relays" in p.read_text(encoding="utf-8"))
    assert opted == [ECLIPSE.name]


# --- producers measure once relays exist ---------------------------------------------------------

def test_producer_measures_dns_relays_from_pool_state_and_snapshot():
    a, b = "aa" * 28, "bb" * 28
    stake = {a: 0.6, b: 0.4}
    pool_state = {a: {"poolParams": {"relays": [{"single host name": {"dnsName": "node1", "port": 3001}}]}},
                  b: {"poolParams": {"relays": [{"single host name": {"dnsName": "node2", "port": 3001}}]}}}
    expected = topo.expected_big_ledger_pools(stake, pool_state, 0.9)
    assert expected["pools_with_relays"] == 2
    assert expected["distribution"] == {"node1:3001": 0.6, "node2:3001": 0.4}
    snapshot = {"bigLedgerPools": [
        {"relativeStake": 0.6, "relays": [{"domain": "node1", "port": 3001}]},
        {"relativeStake": 0.4, "relays": [{"domain": "node2", "port": 3001}]}]}
    assert topo.observed_big_ledger_pools(snapshot)["distribution"] == expected["distribution"]
