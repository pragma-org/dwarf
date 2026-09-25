"""Fake (no-devnet) unit tests for the rules-differential MAGNITUDE SWEEP.

Family #1 (rules-differential) previously varied only WHICH reject rule was
served; the forger applied fixed magnitudes (counter jump +2, kes period +1
ahead, fixed wrong keys). The forger now consumes the per-iteration boundary the
generator records, so the served header's violation magnitude / wrong key
varies. These tests cover the Python-side ownership without a live node:

1. the generator sweeps counter_jump / kes_periods_ahead over their full range,
   deterministically and replayably, while preserving rule-selection;
2. every iteration carries byte_seed (the forger's wrong-key seed) and it varies;
3. the sweep does not change the expected per-node reject reason (all magnitudes
   still trigger the same rule).

The Haskell suite (OpcertSoakSpec) covers the forger side: distinct byte_seed ->
distinct wrong cold/KES key bytes (hence distinct served header bytes), and the
counter_jump / kes_periods_ahead parse.
"""
from scripts import opcert_soak_families as F

FAMILY = "rules-differential"


def _cases(seed, n):
    return [F.generate_case(FAMILY, seed, i) for i in range(n)]


def test_rule_selection_preserved():
    rules = {c["params"]["rule"] for c in _cases(5, 2000)}
    assert rules == set(F.RULES_DIFFERENTIAL_RULES)


def test_counter_jump_sweeps_full_range_incl_large():
    seen = {c["params"]["counter_jump"] for c in _cases(5, 3000)
            if c["params"]["rule"] == "counter-jump"}
    assert seen == set(F.COUNTER_JUMP_MAGNITUDES)
    assert max(seen) >= 50, "sweep must include large jumps to hunt threshold divergences"
    # never the accepted +1
    assert all(j >= 2 for j in seen)


def test_kes_periods_ahead_sweeps_full_range_incl_large():
    seen = {c["params"]["kes_periods_ahead"] for c in _cases(5, 3000)
            if c["params"]["rule"] == "kes-before-window"}
    assert seen == set(F.KES_PERIODS_AHEAD_MAGNITUDES)
    assert max(seen) >= 25
    assert all(a >= 1 for a in seen)


def test_magnitudes_are_not_constant():
    cj = [c["params"]["counter_jump"] for c in _cases(5, 3000)
          if c["params"]["rule"] == "counter-jump"]
    ka = [c["params"]["kes_periods_ahead"] for c in _cases(5, 3000)
          if c["params"]["rule"] == "kes-before-window"]
    assert len(set(cj)) > 1, "counter_jump did not sweep"
    assert len(set(ka)) > 1, "kes_periods_ahead did not sweep"


def test_every_iteration_carries_a_wrong_key_seed_that_varies():
    seeds = [c["params"]["byte_seed"] for c in _cases(5, 200)]
    assert all(isinstance(b, int) for b in seeds)
    assert len(set(seeds)) > 1, "byte_seed (forger wrong-key seed) must vary per iteration"


def test_sweep_is_seed_iteration_deterministic():
    for i in (0, 1, 7, 42, 199):
        a = F.generate_case(FAMILY, 5, i)
        b = F.generate_case(FAMILY, 5, i)
        assert a == b


def test_replay_reproduces_full_spec_including_magnitude():
    a = F.generate_case(FAMILY, 12345, 88)
    b = F.generate_case(a["family"], a["seed"], a["iteration"])
    assert a == b
    assert a["params"] == b["params"]


def test_reject_reason_is_magnitude_independent():
    # All magnitudes of a rule keep the same per-node reject reason: the sweep
    # hunts a DIVERGENCE (a node disagreeing), not a change in the oracle.
    for c in _cases(5, 500):
        rule = c["params"]["rule"]
        assert c["expected_verdict"] == "reject"
        assert c["expected_reason"] == F._RULES_DIFFERENTIAL_REASON[rule]
