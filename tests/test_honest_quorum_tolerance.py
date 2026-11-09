"""F132b: honest_quorum_preserved accepts final honest tips within tolerance_slots (decision 1a)."""
from __future__ import annotations

from types import SimpleNamespace

from profile_manager import primitives


def _evaluate(tips, **params):
    summary = {"latest_tips": tips}
    event = {"payload": {"outcome": "ok", "artifact_summary": {"has_observation_summary": True}, "result": {}}}
    original = primitives._latest_multi_node_observation
    primitives._latest_multi_node_observation = lambda h: (event, event["payload"], {}, summary)
    try:
        return primitives.HonestQuorumPreserved(params={"honest_node_ids": ["node1", "node2"], "minimum_fraction": 1.0,
                                                        **params}).evaluate(SimpleNamespace(events=[], run_dir="/x"))
    finally:
        primitives._latest_multi_node_observation = original


TIPS = lambda a, b, c=0: {"node1": {"slot": a, "hash": f"h{a}"}, "node2": {"slot": b, "hash": f"h{b}"},
                          "node3": {"slot": c, "hash": f"h{c}"}}


def test_exact_quorum_is_unchanged():
    assert _evaluate(TIPS(710, 710))["result"] == "pass"
    assert _evaluate(TIPS(700, 710))["result"] == "fail"


def test_honest_tips_within_tolerance_pass_and_the_byzantine_node_is_ignored():
    out = _evaluate(TIPS(700, 710, 5), tolerance_slots=20)
    assert out["result"] == "pass" and out["evaluated_value"]["basis"] == "within-tolerance"
    assert out["evaluated_value"]["honest_final_slot_spread"] == 10


def test_a_real_split_fails_with_tolerance():
    out = _evaluate(TIPS(587, 710), tolerance_slots=20)
    assert out["result"] == "fail" and out["evaluated_value"]["honest_final_slot_spread"] == 123
