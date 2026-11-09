"""F129: the bootstrap-topology probe measures trustable peers from the node's own topology file.

topology_bootstrap_diversity_preserved reads trustable_peer_count, which the producer never wrote,
so the assertion read 0 on every run (live 6a8c5682: minimum 2, measured 0).
"""
from __future__ import annotations

import json

from scripts import runtime_exposure_probe as probe


def _topology(tmp_path, body):
    path = tmp_path / "topology.json"
    path.write_text(json.dumps(body))
    return {"id": "node1", "topology_path": str(path)}


def test_counts_trustable_local_roots_and_bootstrap_peers(tmp_path):
    node = _topology(tmp_path, {
        "localRoots": [
            {"accessPoints": [{"address": "node2", "port": 3001}, {"address": "node3", "port": 3001}], "trustable": True},
            {"accessPoints": [{"address": "relay9", "port": 3001}], "trustable": False},
        ],
        "bootstrapPeers": [{"address": "boot1", "port": 3001}],
        "publicRoots": [],
    })
    result = probe._run_bootstrap_topology_concentration(
        metadata={"nodes": [node]}, config={"runtime_metadata_path": "x", "target_node": "node1"})
    assert result["trustable_peer_count"] == 3
    assert result["trustable_source"] == "topology_file"


def test_no_topology_file_is_not_observable(tmp_path):
    result = probe._run_bootstrap_topology_concentration(
        metadata={"nodes": [{"id": "node1", "peer_addresses": ["10.0.0.2:3001"]}]},
        config={"runtime_metadata_path": "x", "target_node": "node1"})
    assert result["trustable_peer_count"] is None
    assert result["trustable_source"] == "not-observable"
