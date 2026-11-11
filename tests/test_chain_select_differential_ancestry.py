"""F192. Antithesis chainhold run 435d5b17: the Amaru consumer's final tip was 37 slots behind
the converged relays under injected delay (tolerance_slots=5) and chain_select_differential
reported a divergence. Lag is not a fork: when the target's tip is on every reference node's
chain (ChainSync FindIntersect answers IntersectFound), the implementations agree."""
from __future__ import annotations

from types import SimpleNamespace

from profile_manager import primitives
from scripts import runtime_multi_node_observation as obs

REF = {"slot": 1355, "hash": "h1355", "block": 271}
LAG = {"slot": 1318, "hash": "h1318", "block": 263}
FORK = {"slot": 1350, "hash": "f1350", "block": 270}


def _evaluate(tips, ancestry, **params):
    summary = {"latest_tips": tips, "tip_ancestry": ancestry}
    event = {"phase": "load", "event": "completed", "primitive": "runtime_multi_node_observation",
             "payload": {"outcome": "ok", "artifact_summary": {"has_observation_summary": True}, "result": {}}}
    handle = SimpleNamespace(events=[event], run_dir="/nonexistent")
    prim = primitives.ChainSelectDifferential(params=dict(reference_node_ids=["relay1", "relay2"],
                                                          target_node_ids=["amaru-consumer"], tolerance_slots=5, **params))
    original = primitives._latest_multi_node_observation
    primitives._latest_multi_node_observation = lambda h: (event, event["payload"], {}, summary)
    try:
        return prim.evaluate(handle)
    finally:
        primitives._latest_multi_node_observation = original


def test_a_lagging_target_whose_tip_is_on_every_reference_chain_agrees():
    out = _evaluate({"relay1": REF, "relay2": REF, "amaru-consumer": LAG},
                    {"amaru-consumer": {"relay1": True, "relay2": True}})
    assert out["result"] == "pass"
    assert out["evaluated_value"]["agreement_kind"] == "lagging_ancestor" and out["evaluated_value"]["slot_gap"] == 37


def test_a_target_on_a_different_branch_is_still_a_divergence():
    out = _evaluate({"relay1": REF, "relay2": REF, "amaru-consumer": FORK},
                    {"amaru-consumer": {"relay1": False, "relay2": False}})
    assert out["result"] == "fail" and out["evaluated_value"]["agreement_kind"] == "none"
    assert "divergence" in out["note"]


def test_an_unanswered_ancestry_probe_does_not_turn_lag_into_agreement():
    out = _evaluate({"relay1": REF, "relay2": REF, "amaru-consumer": LAG},
                    {"amaru-consumer": {"relay1": True, "relay2": None}})
    assert out["result"] == "fail" and out["evaluated_value"]["agreement_kind"] == "none"


def test_tip_ancestry_asks_each_other_node_whether_a_differing_tip_is_on_its_chain():
    asked = []

    def probe(host, port, *, network_magic, slot, block_hash):
        asked.append((host, port, network_magic, slot, block_hash))
        return None if host == "10.0.0.12" else True      # relay2 cannot be asked -> None, never a verdict

    # controller-run nodes carry container_ip + listen address; a package node its compose hostname (F166)
    nodes = {"relay1": {"name": "relay1", "port": 13001, "container_ip": "10.0.0.11", "container_listen_address": "0.0.0.0:3001"},
             "relay2": {"name": "relay2", "port": 13002, "container_ip": "10.0.0.12", "container_listen_address": "0.0.0.0:3001"},
             "amaru-consumer": {"name": "amaru-consumer", "port": 3000, "observation_transport": "antithesis-package",
                                "container_listen_address": "amaru-consumer.example:3000"}}
    out = obs._tip_ancestry({"relay1": REF, "relay2": REF, "amaru-consumer": LAG}, nodes, network_magic=42, probe=probe)
    # the consumer's tip is asked of both relays; the relays' shared tip is asked of the consumer once
    assert sorted(asked) == sorted([("10.0.0.11", 3001, 42, 1318, "h1318"), ("10.0.0.12", 3001, 42, 1318, "h1318"),
                                    ("amaru-consumer.example", 3000, 42, 1355, "h1355")])
    assert out["amaru-consumer"] == {"relay1": True, "relay2": None}
    assert out["relay1"] == {"amaru-consumer": True} and out["relay2"] == {"amaru-consumer": True}
