"""The epoch / stake / ledger-peer assertions read the producer step's own measured result."""
from types import SimpleNamespace

from profile_manager import primitives
from scripts import runtime_multi_node_observation as observation


def _completed(primitive, result, outcome="ok"):
    return {"phase": "load", "event": "completed", "primitive": primitive,
            "payload": {"outcome": outcome, "result": result}}


def _observation(summary):
    return {"phase": "load", "event": "completed", "primitive": "runtime_multi_node_observation",
            "payload": {"outcome": "ok", "artifact_summary": {"has_observation_summary": True},
                        "result": {"summary": summary}}}


def _eval(cls, events, **params):
    return cls(params=params).evaluate(SimpleNamespace(events=events))


EPOCH = {"measured": True, "observed_boundary_slot": 502, "expected_window_start": 500,
         "expected_window_end": 504, "all_nodes_within_bounds": True, "source": "x", "per_node": {}}


def test_epoch_boundary_passes_on_producer_measurement():
    out = _eval(primitives.EpochBoundaryTimingWithinBounds,
                [_completed("runtime_force_epoch_boundary", {"epoch_boundary": EPOCH})])
    assert out["result"] == "pass", out
    assert out["evaluated_value"]["observed_boundary_slot"] == 502


def test_epoch_boundary_ignores_observation_summary_values():
    out = _eval(primitives.EpochBoundaryTimingWithinBounds, [_observation({"epoch_boundary": EPOCH})])
    assert out["result"] == "fail"


def test_epoch_boundary_fails_when_any_node_is_out_of_bounds():
    out = _eval(primitives.EpochBoundaryTimingWithinBounds,
                [_completed("runtime_force_epoch_boundary", {"epoch_boundary": {**EPOCH, "all_nodes_within_bounds": False}})])
    assert out["result"] == "fail"


def test_epoch_boundary_fails_when_not_measured():
    out = _eval(primitives.EpochBoundaryTimingWithinBounds,
                [_completed("runtime_force_epoch_boundary", {"epoch_boundary": {**EPOCH, "measured": False}})])
    assert out["result"] == "fail"


def test_stake_snapshot_reads_producer_and_requires_stable_hashes():
    good = {"measured": True, "snapshot_hashes": ["h", "h"], "freeze_window_stable": True}
    events = [_completed("runtime_simulate_stake_snapshot_update", {"stake_snapshot": good})]
    assert _eval(primitives.StakeSnapshotFreezeConsistent, events)["result"] == "pass"
    bad = [_completed("runtime_simulate_stake_snapshot_update",
                      {"stake_snapshot": {**good, "snapshot_hashes": ["h", "g"]}})]
    assert _eval(primitives.StakeSnapshotFreezeConsistent, bad)["result"] == "fail"


def test_leadership_and_reward_read_their_producer_sections():
    leadership = {"measured": True, "deterministic_match": True,
                  "expected_schedule_hash": "a", "recomputed_schedule_hash": "a"}
    reward = {"measured": True, "boundary_invariant_holds": True,
              "expected_total_rewards": 7, "observed_total_rewards": 7}
    events = [_completed("runtime_recompute_leadership_schedule", {"leadership_schedule": leadership}),
              _completed("runtime_trigger_rupd_pulse", {"reward_calculation": reward})]
    lead = _eval(primitives.LeadershipScheduleRecomputesClean, events)
    rew = _eval(primitives.RewardCalculationBoundaryInvariant, events)
    assert lead["result"] == "pass" and lead["evaluated_value"]["expected_schedule_hash"] == "a"
    assert rew["result"] == "pass" and rew["evaluated_value"]["observed_total_rewards"] == 7
    failed = [_completed("runtime_trigger_rupd_pulse", {"reward_calculation": {**reward, "measured": False}})]
    assert _eval(primitives.RewardCalculationBoundaryInvariant, failed)["result"] == "fail"


def test_ledger_peer_weight_unmeasured_delta_fails():
    unmeasured = {"measured": False, "max_absolute_delta": None,
                  "expected_stake_distribution": None, "observed_stake_distribution": None}
    events = [_completed("runtime_perturb_ledger_peer_weights", {"ledger_peers": unmeasured})]
    out = _eval(primitives.LedgerPeerStakeWeightPreserved, events, maximum_absolute_delta=0.05)
    assert out["result"] == "fail"
    assert out["evaluated_value"]["max_absolute_delta"] is None


def test_ledger_peer_weight_passes_within_delta():
    measured = {"measured": True, "max_absolute_delta": 0.01,
                "expected_stake_distribution": {"a": 0.5}, "observed_stake_distribution": {"a": 0.49}}
    events = [_completed("runtime_perturb_ledger_peer_weights", {"ledger_peers": measured})]
    assert _eval(primitives.LedgerPeerStakeWeightPreserved, events, maximum_absolute_delta=0.05)["result"] == "pass"
    events = [_completed("runtime_perturb_ledger_peer_weights", {"ledger_peers": {**measured, "max_absolute_delta": 0.2}})]
    assert _eval(primitives.LedgerPeerStakeWeightPreserved, events, maximum_absolute_delta=0.05)["result"] == "fail"


def test_big_ledger_peer_quorum_reads_producer():
    section = {"measured": True, "expected_top_peer_ids": ["a", "b", "c"], "observed_top_peer_ids": ["a", "b", "c"]}
    events = [_completed("runtime_substitute_big_ledger_peers", {"big_ledger_peers": section})]
    out = _eval(primitives.BigLedgerPeerQuorumIntact, events, minimum_expected_matches=3)
    assert out["result"] == "pass" and out["evaluated_value"]["matched_peer_ids"] == ["a", "b", "c"]
    assert _eval(primitives.BigLedgerPeerQuorumIntact, [_observation({"big_ledger_peers": section})],
                 minimum_expected_matches=3)["result"] == "fail"


def test_observation_overrides_cannot_supply_producer_keys():
    for key in ("epoch_boundary", "stake_snapshot", "leadership_schedule", "reward_calculation",
                "ledger_peers", "big_ledger_peers"):
        assert key in observation.OBSERVATION_MEASUREMENT_KEYS
