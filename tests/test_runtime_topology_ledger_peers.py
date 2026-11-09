"""Ledger-peer producer: on-chain stake vs the node's own ledger-peer snapshot, per node, no constants."""
import json
from types import SimpleNamespace

import pytest

from scripts import runtime_topology_fault as topology
import runtime_epoch_boundary_probe as lsq  # the module object runtime_topology_fault imports (scripts dir on sys.path)

# Five pools with equal stake; pool ids as cardano-cli prints them (bech32 in stake-distribution,
# hex in pool-state). pool1 bech32 below decodes to 28 bytes of 0x01 etc.
POOL_HEX = {f"node{i}": bytes([i]) * 28 for i in range(1, 6)}


def _bech32(raw: bytes) -> str:
    charset = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
    acc, bits, out = 0, 0, []
    for byte in raw:
        acc = (acc << 8) | byte
        bits += 8
        while bits >= 5:
            bits -= 5
            out.append((acc >> bits) & 31)
    if bits:
        out.append((acc << (5 - bits)) & 31)
    return "pool1" + "".join(charset[v] for v in out) + "qqqqqq"  # checksum not verified by the decoder


class FakeLedger:
    def __init__(self, relays=True, observed_override=None):
        self.relays = relays
        self.observed_override = observed_override

    def __call__(self, argv, **_kwargs):
        cmd = argv[3:]
        rest = cmd[1:]
        if rest[0] == "latest":
            rest = rest[1:]
        name = rest[1]
        if name == "stake-distribution":
            return self._ok({_bech32(raw): 0.2 for raw in POOL_HEX.values()})
        if name == "pool-state":
            return self._ok({raw.hex(): {"poolParams": {"relays": (
                [{"single host address": {"IPv4": f"10.0.0.{i}", "IPv6": None, "port": 3001}}] if self.relays else [])}}
                for i, raw in enumerate(POOL_HEX.values(), start=1)})
        if name == "ledger-peer-snapshot":
            pools = self.observed_override or [
                {"accumulatedStake": 0.2 * i, "relativeStake": 0.2, "relays": [{"address": f"10.0.0.{i}", "port": 3001}]}
                for i in range(1, 6)] if self.relays else []
            return self._ok({"NodeToClientVersion": 20, "Point": {"slot": 10}, "bigLedgerPools": pools})
        raise AssertionError(argv)

    @staticmethod
    def _ok(body):
        return SimpleNamespace(returncode=0, stdout=json.dumps(body), stderr="")


def _metadata():
    nodes = [{"id": f"node{i}", "impl": "cardano-node", "container_name": f"c-node{i}"} for i in range(1, 6)]
    return {"network_magic": 42, "nodes": nodes}


def test_bech32_pool_id_decodes_to_the_pool_state_hex_key():
    assert topology._bech32_to_hex(_bech32(POOL_HEX["node3"])) == POOL_HEX["node3"].hex()


def test_ledger_peer_weights_match_on_chain_stake(monkeypatch):
    monkeypatch.setattr(lsq, "_run_cmd", FakeLedger())
    out = topology.apply_topology_mode(metadata=_metadata(), mode="perturb_ledger_peer_weights",
                                       config={"target_node": "node3"})
    section = out["result"]["ledger_peers"]
    assert out["result"]["fault_applied"] is False
    assert section["measured"] is True
    assert section["max_absolute_delta"] == pytest.approx(0.0)
    assert section["expected_stake_distribution"]["10.0.0.1:3001"] == pytest.approx(0.2)
    assert set(section["per_node"]) == {f"node{i}" for i in range(1, 6)}
    assert out["observation_overrides"] == {}


def test_ledger_peer_weight_drift_is_reported(monkeypatch):
    skewed = [{"relativeStake": 0.6, "relays": [{"address": "10.0.0.1", "port": 3001}]},
              {"relativeStake": 0.4, "relays": [{"address": "10.0.0.2", "port": 3001}]}]
    monkeypatch.setattr(lsq, "_run_cmd", FakeLedger(observed_override=skewed))
    section = topology.apply_topology_mode(metadata=_metadata(), mode="perturb_ledger_peer_weights",
                                           config={"target_node": "node1"})["result"]["ledger_peers"]
    assert section["max_absolute_delta"] == pytest.approx(0.4)


def test_big_ledger_peer_quorum_compares_expected_and_observed_relays(monkeypatch):
    monkeypatch.setattr(lsq, "_run_cmd", FakeLedger())
    section = topology.apply_topology_mode(metadata=_metadata(), mode="substitute_big_ledger_peers",
                                           config={"target_node": "node1"})["result"]["big_ledger_peers"]
    assert section["measured"] is True
    assert section["expected_top_peer_ids"] == [f"10.0.0.{i}:3001" for i in range(1, 6)]
    assert section["matched_peer_ids"] == section["expected_top_peer_ids"]


def test_devnet_without_registered_relays_is_not_measured(monkeypatch):
    monkeypatch.setattr(lsq, "_run_cmd", FakeLedger(relays=False))
    out = topology.apply_topology_mode(metadata=_metadata(), mode="substitute_big_ledger_peers",
                                       config={"target_node": "node1"})
    section = out["result"]["big_ledger_peers"]
    assert section["measured"] is False
    assert section["matched_peer_ids"] == []
    assert "registers a relay" in section["reason"]


def test_expected_set_stops_at_the_big_ledger_quota():
    distribution = {"aa": 0.5, "bb": 0.3, "cc": 0.15, "dd": 0.05}
    pool_state = {k: {"poolParams": {"relays": [{"single host name": {"dnsName": f"{k}.relay", "port": 1}}]}}
                  for k in distribution}
    expected = topology.expected_big_ledger_pools(distribution, pool_state, 0.9)
    assert sorted(expected["distribution"]) == ["aa.relay:1", "bb.relay:1", "cc.relay:1"]
