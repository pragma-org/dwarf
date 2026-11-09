"""F117: the multi-node observation derives honest peer counts from the edges it observed.

honest_peer_set_uncompromised read topology.honest_peer_counts, which nothing produced (only an
observation override could set it), so it failed on every real run. The counts now come from the
observed peer edges and the substrate roles.
"""
from __future__ import annotations

from scripts import runtime_multi_node_observation as obs


def _per_node(peers):
    return {node: {"connection_state": {"responsive": True, "peer_nodes_connected": list(connected)},
                   "tip_state": {"latest_tip": {"ok": True, "hash": "aa", "slot": 10}}}
            for node, connected in peers.items()}


META = {"nodes": [{"id": "node1", "role": "honest"}, {"id": "node2", "role": "honest"},
                  {"id": "node3", "role": "honest"}, {"id": "node4", "role": "sybil"}]}


def test_honest_peer_counts_come_from_observed_edges_and_roles():
    per_node = _per_node({"node1": ["node2", "node3", "node4"], "node2": ["node1"], "node3": ["node4"], "node4": ["node3"]})
    summary = obs._summarize_observation(per_node, ["node1", "node2", "node3", "node4"], META)
    topology = summary["topology"]
    assert topology["honest_peer_counts"] == {"node1": 2, "node2": 1, "node3": 0}
    assert topology["peer_set_capture_detected"] is True       # node3 sees only the sybil
    assert topology["captured_honest_nodes"] == ["node3"]
    assert topology["source"] == "observed-peer-edges"


def test_no_capture_when_honest_nodes_keep_honest_peers():
    per_node = _per_node({"node1": ["node2"], "node2": ["node1", "node4"], "node3": ["node1"], "node4": []})
    topology = obs._summarize_observation(per_node, ["node1", "node2", "node3", "node4"], META)["topology"]
    assert topology["peer_set_capture_detected"] is False and topology["honest_peer_counts"]["node3"] == 1
