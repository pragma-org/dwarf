"""F125: a node restarted from copied stale state must catch up; staying behind is not the pass.

On the private devnet (0.1 s slots) the copied node rejoins within seconds. The four copied-state
modes raised when it did ("unexpectedly recovered", "expected bounded divergence"). Decision
2026-11-09 (option a): recovery to the honest tip within the window is the pass; the divergence
gap is recorded, not required.
"""
from __future__ import annotations

import pytest

from scripts import runtime_profile_recovery_check as check


def test_recovery_within_the_window_is_the_pass():
    outcome = check.copied_state_recovery_outcome(
        {"node2": {"slot": 2049}}, {"timed_out": False, "duration_seconds": 3.2}, baseline_slot=2000)
    assert outcome == {"outcome": "recovered_within_bound", "recovered_slot": 2049, "baseline_slot": 2000,
                       "recovery_seconds": 3.2}


@pytest.mark.parametrize("recovered,wait", [
    (None, {"timed_out": True, "last_error": "tip below target"}),
    ({"node2": {"slot": 1500}}, {"timed_out": False}),
])
def test_staying_behind_after_the_window_fails(recovered, wait):
    with pytest.raises(RuntimeError, match="did not recover"):
        check.copied_state_recovery_outcome(recovered, wait, baseline_slot=2000)


@pytest.mark.parametrize("copied,honest,observed", [(1500, 2000, True), (2049, 2049, False), (2050, 2049, False)])
def test_divergence_is_recorded_not_required(copied, honest, observed):
    tips = {"node2": {"slot": copied}, "node3": {"slot": honest}}
    assert check.divergence_observed(tips) == {"divergence_observed": observed, "copied_slot": copied,
                                               "honest_slot": honest, "slot_gap": honest - copied}
