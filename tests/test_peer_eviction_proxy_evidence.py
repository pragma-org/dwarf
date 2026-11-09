"""F130: peer eviction from proxy evidence: each honest node dropped the byzantine peer's connection.

With the corrupting proxy in place the honest node dials the controller, so the connection map
would show node1->node3 gone whatever node1 did. The proxy records which side closed the
connection that carried the corrupted block; the target closing it is the eviction.
"""
from __future__ import annotations

from types import SimpleNamespace

from profile_manager import primitives


def _fault(target, upstream, *, dropped, drop_seconds):
    return {"phase": "load", "event": "completed", "primitive": "runtime_blockfetch_invalid_block_cbor",
            "payload": {"outcome": "ok", "report": {"target_node": target, "upstream_node": upstream},
                        "result": {"dropped_by_target": dropped, "drop_seconds": drop_seconds,
                                   "invalid_block_rejected": dropped}}}


def _evaluate(events, timeout=10):
    prim = primitives.PeerEvictionWithinSeconds(params={"honest_node_ids": ["node1", "node2"],
                                                        "byzantine_node_ids": ["node3"], "timeout_seconds": timeout})
    return prim.evaluate(SimpleNamespace(events=events, run_dir="/nonexistent"))


def test_both_honest_nodes_dropped_the_byzantine_peer_in_time():
    out = _evaluate([_fault("node1", "node3", dropped=True, drop_seconds=0.05),
                     _fault("node2", "node3", dropped=True, drop_seconds=0.3)])
    assert out["result"] == "pass"
    assert out["evaluated_value"]["evidence_source"] == "proxy-drop"


def test_a_node_that_kept_the_byzantine_peer_fails():
    out = _evaluate([_fault("node1", "node3", dropped=True, drop_seconds=0.05),
                     _fault("node2", "node3", dropped=False, drop_seconds=None)])
    assert out["result"] == "fail"
    assert out["evaluated_value"]["violating_honest_nodes"] == {"node2": ["node3"]}


def test_a_slow_drop_fails_the_timeout():
    out = _evaluate([_fault("node1", "node3", dropped=True, drop_seconds=12.0),
                     _fault("node2", "node3", dropped=True, drop_seconds=0.1)], timeout=10)
    assert out["result"] == "fail"


def test_missing_evidence_for_an_honest_node_fails():
    out = _evaluate([_fault("node1", "node3", dropped=True, drop_seconds=0.05)])
    assert out["result"] == "fail"
    assert "node2" in out["evaluated_value"]["violating_honest_nodes"]
