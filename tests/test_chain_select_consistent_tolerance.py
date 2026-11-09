"""F132 (decision 2026-11-09, 1a): chain_select_consistent accepts final tips within tolerance_slots.

On 0.1 s-slot devnets under injected delay, healthy nodes rarely share the exact tip at one
instant. The tolerance applies to the FINAL tips only: an early match must not hide a node that
stalls afterwards (live threshold-delay: node1 stuck at 1005 while node2/node3 reached 1179).
"""
from __future__ import annotations

from types import SimpleNamespace

from profile_manager import primitives


def _evaluate(tips, *, consistent=False, **params):
    summary = {"chain_select_consistent": consistent, "tip_group_count": 1 if consistent else 2, "latest_tips": tips}
    event = {"phase": "load", "event": "completed", "primitive": "runtime_multi_node_observation",
             "payload": {"outcome": "ok", "artifact_summary": {"has_observation_summary": True}, "result": {}}}
    handle = SimpleNamespace(events=[event], run_dir="/nonexistent")
    prim = primitives.ChainSelectConsistent(params=params)
    original = primitives._latest_multi_node_observation
    primitives._latest_multi_node_observation = lambda h: (event, event["payload"], {}, summary)
    try:
        return prim.evaluate(handle)
    finally:
        primitives._latest_multi_node_observation = original


TIPS = lambda *slots: {f"node{i + 1}": {"slot": s, "hash": f"h{s}", "block": s // 10} for i, s in enumerate(slots)}


def test_identical_tips_still_pass_without_tolerance():
    assert _evaluate(TIPS(100, 100, 100), consistent=True)["result"] == "pass"


def test_without_tolerance_the_check_stays_exact():
    assert _evaluate(TIPS(1170, 1179, 1179))["result"] == "fail"


def test_final_tips_within_tolerance_pass():
    out = _evaluate(TIPS(1170, 1179, 1179), tolerance_slots=20)
    assert out["result"] == "pass"
    assert out["evaluated_value"]["final_slot_spread"] == 9 and out["evaluated_value"]["basis"] == "within-tolerance"


def test_a_stalled_node_fails_even_with_tolerance():
    out = _evaluate(TIPS(1005, 1179, 1179), tolerance_slots=20)
    assert out["result"] == "fail" and out["evaluated_value"]["final_slot_spread"] == 174


def test_missing_tips_fail():
    assert _evaluate({}, tolerance_slots=20)["result"] == "fail"
