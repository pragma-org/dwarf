"""F122: the network partition uses the target node's own Docker network from runtime metadata.

It defaulted to the upstream Antithesis network name 'cardano-amaru-testnet', so on a
self-composed substrate the disconnect named a network that does not exist.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from profile_manager import primitives


class _Handle:
    def __init__(self, run_dir):
        self.run_dir = run_dir
        self.logs = []

    def log(self, **entry):
        self.logs.append(entry)


def test_partition_uses_the_node_network_when_none_is_named(tmp_path, monkeypatch):
    (tmp_path / "outputs" / "c").mkdir(parents=True)
    (tmp_path / "outputs/c/runtime.json").write_text(json.dumps({"nodes": [
        {"id": "relay2", "container_name": "dwarf-s-relay2-1", "container_network": "dwarf-substrate-x-net"}]}))
    calls = []
    prim = primitives.RuntimeNetworkPartition(params={"runtime_metadata_path": "outputs/c/runtime.json", "target_node": "relay2",
                                                      "partition_seconds": 0, "settle_seconds": 0})
    monkeypatch.setattr(prim, "_docker", lambda args: calls.append(args) or SimpleNamespace(returncode=0, stderr=""))
    monkeypatch.setattr("time.sleep", lambda s: None)
    prim.apply(_Handle(tmp_path))
    assert calls[0] == ["network", "disconnect", "dwarf-substrate-x-net", "dwarf-s-relay2-1"]
    assert calls[1] == ["network", "connect", "dwarf-substrate-x-net", "dwarf-s-relay2-1"]


def test_an_explicit_network_name_still_wins(tmp_path, monkeypatch):
    (tmp_path / "outputs" / "c").mkdir(parents=True)
    (tmp_path / "outputs/c/runtime.json").write_text(json.dumps({"nodes": [
        {"id": "p2", "container_name": "p2", "container_network": "ignored"}]}))
    calls = []
    prim = primitives.RuntimeNetworkPartition(params={"runtime_metadata_path": "outputs/c/runtime.json", "target_node": "p2",
                                                      "network_name": "cardano-amaru-testnet", "partition_seconds": 0, "settle_seconds": 0})
    monkeypatch.setattr(prim, "_docker", lambda args: calls.append(args) or SimpleNamespace(returncode=0, stderr=""))
    monkeypatch.setattr("time.sleep", lambda s: None)
    prim.apply(_Handle(tmp_path))
    assert calls[0][2] == "cardano-amaru-testnet"


def test_remove_reconnects_to_the_same_node_network(tmp_path, monkeypatch):
    (tmp_path / "outputs" / "c").mkdir(parents=True)
    (tmp_path / "outputs/c/runtime.json").write_text(json.dumps({"nodes": [
        {"id": "relay2", "container_name": "dwarf-s-relay2-1", "container_network": "dwarf-substrate-x-net"}]}))
    calls = []
    prim = primitives.RuntimeNetworkPartition(params={"runtime_metadata_path": "outputs/c/runtime.json", "target_node": "relay2"})
    monkeypatch.setattr(prim, "_docker", lambda args: calls.append(args) or SimpleNamespace(returncode=0, stderr=""))
    prim.remove(_Handle(tmp_path))
    assert calls == [["network", "connect", "dwarf-substrate-x-net", "dwarf-s-relay2-1"]]
