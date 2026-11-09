"""Amaru submissions wait until cardano-node accepts relayed transactions (live F97c class)."""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts import runtime_redeemer_container_live_controls as lc

RUNNERS = ["runtime_governance_zero_denominator", "runtime_inline_datum_tag24_boundary", "runtime_multiasset_compact_boundary",
           "runtime_uplc_deep_delay_stack_safety", "runtime_value_coin_overflow", "runtime_redeemer_container_live_controls"]


def test_wait_sleeps_until_eligible_plus_margin_and_is_capped():
    slept = []
    gate = {"eligible_at_epoch_seconds": 1000.0, "tx_submission_init_delay_seconds": 60.0}
    assert lc.wait_for_target_propagation_eligibility(gate, now=lambda: 970.0, sleep=slept.append) == pytest.approx(35.0)
    assert lc.wait_for_target_propagation_eligibility(gate, now=lambda: 2000.0, sleep=slept.append) == 0.0
    assert lc.wait_for_target_propagation_eligibility(gate, now=lambda: 0.0, sleep=slept.append) == pytest.approx(65.0)
    assert lc.wait_for_target_propagation_eligibility(None, now=lambda: 0.0, sleep=slept.append) == 0.0
    assert slept == [pytest.approx(35.0), pytest.approx(65.0)]


@pytest.mark.parametrize("module", RUNNERS)
def test_every_gated_runner_waits_right_after_computing_its_gate(module):
    source = (Path(lc.__file__).parent / f"{module}.py").read_text(encoding="utf-8")
    calls = source.count("_reference_tx_submission_propagation_gate(\n") - source.count("def _reference_tx_submission_propagation_gate(")
    assert calls >= 1 and source.count("wait_for_target_propagation_eligibility(") - source.count("def wait_for_target_propagation_eligibility(") == calls
