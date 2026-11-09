"""F118: a private-devnet substrate that declares no topology gets a full mesh.

On public preview the nodes found peers through bootstrap; after the move to testnet_42 (F96) a
substrate with no topology left every node without peers (live: eclipse-topology, 0 established
connections on all five nodes, three tip groups). The observation already expects a full mesh when
no topology is declared; compose now builds the same. An explicit empty edge list is kept.
"""
from __future__ import annotations

from scripts.runtime_substrate_common import normalize_substrate


def _nodes(count):
    return [{"id": f"node{i}", "impl": "cardano-node", "version": "resolved-plan", "role": "honest"}
            for i in range(1, count + 1)]


def test_missing_topology_on_a_private_devnet_becomes_a_full_mesh():
    edges = normalize_substrate({"network": "testnet_42", "compose_mode": "docker", "nodes": _nodes(3)})["topology"]["edges"]
    assert sorted((e["from"], e["to"]) for e in edges) == [
        ("node1", "node2"), ("node1", "node3"), ("node2", "node1"), ("node2", "node3"), ("node3", "node1"), ("node3", "node2")]


def test_explicit_empty_edges_and_public_networks_are_untouched():
    explicit = {"network": "testnet_42", "compose_mode": "docker", "nodes": _nodes(3), "topology": {"edges": []}}
    assert normalize_substrate(explicit)["topology"]["edges"] == []
    public = {"network": "preview", "compose_mode": "docker", "nodes": _nodes(3)}
    assert normalize_substrate(public)["topology"]["edges"] == []


def test_a_single_node_needs_no_edges():
    assert normalize_substrate({"network": "testnet_42", "nodes": _nodes(1)})["topology"]["edges"] == []
