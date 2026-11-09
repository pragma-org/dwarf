"""F144: the assertions read the measured fields of the real producers.

- blockfetch_range_pressure_bounded: the peak blocks per batch measured on the wire (no constant).
- k_bound_rollback_recovered: the observed rollback depth must be 1..k (k from the node's genesis).
- chain_switch_consistent: the target tip is the majority tip at heal; on a growing chain the honest
  nodes are later *past* it, so membership (ChainSync FindIntersect, measured by the producer) plus
  a latest tip at or after the target slot is what "reached the target" means.
"""
from __future__ import annotations

import pytest

import profile_manager.primitives as prim


def _observation(tips: dict) -> dict:
    per_node = {}
    for node_id, tip in tips.items():
        per_node[node_id] = {"tip_state": {"latest_tip": tip, "samples": [{"slot": tip["slot"] - 5, "hash": "p"}, tip]}}
    return {"outcome": "ok", "result": {"per_node": per_node,
                                        "summary": {"chain_select_consistent": True, "observed_peer_edge_count": 6}}}


@pytest.fixture
def events(monkeypatch):
    store: dict[str, dict] = {}

    def fake(handle, *, phase, event, primitive):
        payload = store.get(primitive)
        return [{"payload": payload}] if payload is not None else []

    monkeypatch.setattr(prim, "_events_from_handle", fake)
    return store


def test_range_pressure_reads_the_measured_batch_peak(events):
    events["runtime_blockfetch_range_pressure"] = {"outcome": "ok", "result": {
        "resource_bound_ok": True, "observed_peak_blocks_per_batch": 40, "configured_limit": 64,
        "blocks_fetched": 300, "block_range_requests_observed": 9}}
    out = prim.BlockfetchRangePressureBounded(params={}).evaluate(object())
    assert out["result"] == "pass" and out["evaluated_value"]["observed_peak_blocks_per_batch"] == 40


def test_range_pressure_without_a_measured_peak_fails(events):
    events["runtime_blockfetch_range_pressure"] = {"outcome": "ok", "result": {
        "resource_bound_ok": True, "configured_limit": 64, "blocks_fetched": 3, "block_range_requests_observed": 3,
        "observed_peak_blocks_in_memory": 29}}
    out = prim.BlockfetchRangePressureBounded(params={}).evaluate(object())
    assert out["result"] == "fail"


def _rollback(**changes):
    body = {"requested_rollback_slots": 5, "security_parameter_k": 10, "rollback_status": "recovered",
            "ledger_state_consistent_post_rollback": True, "observed_rollback_depth_blocks": 2}
    body.update(changes)
    return {"outcome": "ok", "result": body}


def test_k_bound_rollback_passes_on_a_measured_within_k_rollback(events):
    events["runtime_force_rollback"] = _rollback()
    events["runtime_multi_node_observation"] = _observation({"node1": {"slot": 900, "hash": "t"}})
    out = prim.KBoundRollbackRecovered(params={}).evaluate(object())
    assert out["result"] == "pass", out
    assert out["evaluated_value"]["observed_rollback_depth_blocks"] == 2


@pytest.mark.parametrize("changes", [{"observed_rollback_depth_blocks": 0}, {"observed_rollback_depth_blocks": 11},
                                     {"observed_rollback_depth_blocks": None}, {"security_parameter_k": None},
                                     {"rollback_status": "no_fork_formed"},
                                     {"ledger_state_consistent_post_rollback": None}])
def test_k_bound_rollback_fails_without_a_real_within_k_rollback(events, changes):
    events["runtime_force_rollback"] = _rollback(**changes)
    events["runtime_multi_node_observation"] = _observation({"node1": {"slot": 900, "hash": "t"}})
    assert prim.KBoundRollbackRecovered(params={}).evaluate(object())["result"] == "fail"


def _switch(**changes):
    body = {"target_tip_hash": "aa", "target_tip_slot": 500, "nodes_on_target_chain": ["node1", "node2", "node3"],
            "chain_switch_status": "switched"}
    body.update(changes)
    return {"outcome": "ok", "result": body}


LATER = {"node1": {"slot": 900, "hash": "t"}, "node2": {"slot": 900, "hash": "t"}, "node3": {"slot": 900, "hash": "t"}}


def test_chain_switch_passes_when_every_honest_node_holds_the_target_and_moved_past_it(events):
    events["runtime_chain_switch_inject"] = _switch()
    events["runtime_multi_node_observation"] = _observation(LATER)
    out = prim.ChainSwitchConsistent(params={"honest_node_ids": ["node1", "node2", "node3"]}).evaluate(object())
    assert out["result"] == "pass", out
    assert out["evaluated_value"]["matching_honest_node_count"] == 3


def test_chain_switch_fails_when_a_node_does_not_hold_the_target(events):
    events["runtime_chain_switch_inject"] = _switch(nodes_on_target_chain=["node1", "node2"])
    events["runtime_multi_node_observation"] = _observation(LATER)
    out = prim.ChainSwitchConsistent(params={"honest_node_ids": ["node1", "node2", "node3"]}).evaluate(object())
    assert out["result"] == "fail"


def test_chain_switch_fails_without_a_target(events):
    events["runtime_chain_switch_inject"] = _switch(target_tip_hash="", target_tip_slot=None, nodes_on_target_chain=[])
    events["runtime_multi_node_observation"] = _observation(LATER)
    out = prim.ChainSwitchConsistent(params={"honest_node_ids": ["node1", "node2", "node3"]}).evaluate(object())
    assert out["result"] == "fail"


def test_chain_switch_still_matches_an_exact_latest_tip(events):
    events["runtime_chain_switch_inject"] = _switch(nodes_on_target_chain=[])
    events["runtime_multi_node_observation"] = _observation(
        {n: {"slot": 500, "hash": "aa"} for n in ("node1", "node2", "node3")})
    out = prim.ChainSwitchConsistent(params={"honest_node_ids": ["node1", "node2", "node3"]}).evaluate(object())
    # samples still show a slot transition (495 -> 500)
    assert out["result"] == "pass", out
