"""F133: an observation override can fill an unmeasured key, never replace a measured one.

Fault/probe scripts wrote fabricated values into observation_overrides (exposure probe:
full-mesh per_node_connectivity, responsive_node_count = all nodes, chain_select_consistent True;
resource-abuse faults: fake tips, F128), and the observation copied every override over the
measured summary. Measured keys now win; applied overrides are listed.
"""
from scripts import runtime_multi_node_observation as obs


def _per_node():
    return {
        "node1": {"tip_state": {"latest_tip": {"slot": 100, "hash": "aa", "block": 10}, "sample_count": 3,
                                "successful_sample_count": 3}},
        "node2": {"tip_state": {"latest_tip": {"slot": 90, "hash": "bb", "block": 9}, "sample_count": 3,
                                "successful_sample_count": 3}},
    }


def test_measured_keys_are_not_replaced():
    measured = obs._summarize_observation(_per_node(), ["node1", "node2"], {})
    faked = obs._summarize_observation(_per_node(), ["node1", "node2"], {"observation_overrides": {
        "chain_select_consistent": True, "tip_group_count": 1, "latest_tips": {"node1": {"slot": 0}}}})
    for key in ("chain_select_consistent", "tip_group_count", "latest_tips"):
        assert faked[key] == measured[key]
    assert sorted(faked["observation_overrides_rejected"]) == ["chain_select_consistent", "latest_tips", "tip_group_count"]


def test_unmeasured_keys_are_filled_and_listed():
    summary = obs._summarize_observation(_per_node(), ["node1", "node2"], {"observation_overrides": {
        "node_lifecycle": {"node2": {"action": "restart", "restarted": True}}}})
    assert summary["node_lifecycle"] == {"node2": {"action": "restart", "restarted": True}}
    assert summary["observation_overrides_applied"] == ["node_lifecycle"]


def test_measurement_keys_are_refused_even_when_not_measured_this_run():
    summary = obs._summarize_observation(_per_node(), ["node1", "node2"], {"observation_overrides": {
        "per_node_connectivity": {"node1": ["node2"], "node2": ["node1"]}, "observed_peer_edge_count": 9}})
    assert "per_node_connectivity" not in summary or summary.get("per_node_connectivity") != {"node1": ["node2"], "node2": ["node1"]}
    assert "per_node_connectivity" in summary["observation_overrides_rejected"]
