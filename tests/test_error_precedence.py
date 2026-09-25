"""Fake (no-devnet) unit tests for opcert soak family #4: error-precedence.

The forger breaks TWO reachable opcert rules in one header; each node reports
whichever rule its validator checks FIRST. The oracle is precedence PARITY,
reusing the rules-differential reason-parity machinery: both nodes must REJECT
(verdict parity) AND map their reported reason to the SAME canonical rule (reason
parity). A both-reject-but-different-canonical-rule iteration is a
``precedence_divergence`` -- recorded as a ``reason_mismatch`` and failing
opcert_soak_reasons_agree, DISTINCT from a verdict disagreement.

These cover the Python side without a live node: the generator sweeps the 2-rule
combos deterministically, and the scoring flags precedence divergence vs
agreement vs verdict disagreement.
"""
from scripts import opcert_soak_families as F
from scripts import opcert_soak_result as R

FAMILY = "error-precedence"


# --------------------------------------------------------------------------- #
# Generator.
# --------------------------------------------------------------------------- #

def test_family_registered_as_differential():
    assert FAMILY in F.FAMILIES
    assert FAMILY in F.DIFFERENTIAL_FAMILIES


def test_combos_are_all_distinct_pairs_of_reachable_rules():
    assert len(F.ERROR_PRECEDENCE_COMBOS) == 6  # C(4,2)
    for a, b in F.ERROR_PRECEDENCE_COMBOS:
        assert a != b
        assert a in F.ERROR_PRECEDENCE_RULES and b in F.ERROR_PRECEDENCE_RULES
    # the unreachable-on-fresh rules are excluded
    assert "counter-behind" not in F.ERROR_PRECEDENCE_RULES
    assert "kes-after-window" not in F.ERROR_PRECEDENCE_RULES


def test_generator_is_seed_iteration_deterministic():
    assert F.generate_case(FAMILY, 4242, 11) == F.generate_case(FAMILY, 4242, 11)


def test_generator_replay_reproduces_spec():
    a = F.generate_case(FAMILY, 7, 3)
    assert a == F.generate_case(a["family"], a["seed"], a["iteration"])


def test_case_shape_two_rules_reject_no_predicted_reason():
    for i in range(60):
        c = F.generate_case(FAMILY, 5, i)
        assert c["base_case"] == "error-precedence"
        assert c["expected_verdict"] == "reject"
        assert c["expected_reason"] is None  # precedence is intentionally ambiguous
        rules = c["params"]["rules"]
        assert len(rules) == 2 and len(set(rules)) == 2
        assert set(rules) <= set(F.ERROR_PRECEDENCE_RULES)
        assert "byte_seed" in c["params"]


def test_all_six_combos_are_swept():
    combos = {tuple(F.generate_case(FAMILY, 5, i)["params"]["rules"]) for i in range(3000)}
    assert combos == set(F.ERROR_PRECEDENCE_COMBOS)


def test_magnitudes_attached_only_for_relevant_rules():
    for i in range(200):
        p = F.generate_case(FAMILY, 5, i)["params"]
        rules = p["rules"]
        assert ("counter_jump" in p) == ("counter-jump" in rules)
        assert ("kes_periods_ahead" in p) == ("kes-before-window" in rules)
        if "counter_jump" in p:
            assert p["counter_jump"] in F.COUNTER_JUMP_MAGNITUDES
        if "kes_periods_ahead" in p:
            assert p["kes_periods_ahead"] in F.KES_PERIODS_AHEAD_MAGNITUDES


# --------------------------------------------------------------------------- #
# Precedence scoring (reused reason-parity machinery).
# --------------------------------------------------------------------------- #

def test_same_precedence_agrees_no_divergence():
    # Both nodes reject a cold-key+counter-jump combo reporting the SAME canonical
    # rule (cardano CounterOverIncrementedOCERT / amaru SequenceNumberTooFarAhead
    # both map to counter-too-large) -> precedence agrees, no finding.
    assert R.classify_reason_parity("CounterOverIncrementedOCERT", "SequenceNumberTooFarAhead",
                                    node_a="node1", node_b="amaru-relay-1") is None


def test_different_precedence_is_a_divergence_finding():
    # cardano reports the counter rule first, amaru reports the cold-key rule
    # first for the SAME 2-rule combo -> precedence divergence.
    mm = R.classify_reason_parity("CounterOverIncrementedOCERT", "InvalidSignature",
                                  node_a="node1", node_b="amaru-relay-1")
    assert mm is not None
    assert mm["canonical_a"] == "counter-too-large"
    assert mm["canonical_b"] == "cold-key-unauthorized"


def test_unrecognised_reason_fails_closed_as_divergence():
    mm = R.classify_reason_parity("CounterOverIncrementedOCERT", "???",
                                  node_a="node1", node_b="amaru-relay-1")
    assert mm is not None  # cannot prove agreement on an unknown rule


def test_verdict_disagreement_is_scored_disagree():
    # one node rejects, the other accepts (one rule masked?) -> verdict disagree
    # (a harder finding than a precedence divergence).
    assert R.classify_differential("rejected", "accepted") == "disagree"
    assert R.classify_differential("rejected", "rejected") == "agree"


# --------------------------------------------------------------------------- #
# End-to-end (fake): a precedence-divergent run FAILS reasons_agree while the
# verdict-level invariant/agree can still pass; captured spec is replayable.
# --------------------------------------------------------------------------- #

def _diff_record(spec, va, vb, ra, rb):
    diff = R.classify_differential(va, vb)
    reason_mismatch = None
    if diff == "agree" and va == "rejected":
        reason_mismatch = R.classify_reason_parity(ra, rb, node_a="node1", node_b="amaru-relay-1")
    return {
        "outcome": {"agree": "pass", "disagree": "mismatch", "inconclusive": "inconclusive"}[diff],
        "differential": diff, "spec": spec,
        "verdicts": {"node1": va, "amaru-relay-1": vb},
        "reasons": {"node1": ra, "amaru-relay-1": rb},
        "reason_mismatch": reason_mismatch,
    }


def test_precedence_divergent_run_fails_reasons_agree_but_passes_verdict_agree():
    records = []
    divergent_iter = 3
    for i in range(8):
        spec = F.generate_case(FAMILY, 5, i)
        if i == divergent_iter:
            # both reject, different canonical rule
            rec = _diff_record(spec, "rejected", "rejected",
                               "CounterOverIncrementedOCERT", "InvalidSignature")
        else:
            rec = _diff_record(spec, "rejected", "rejected",
                               "CounterOverIncrementedOCERT", "SequenceNumberTooFarAhead")
        records.append(rec)

    result = R.build_soak_result(
        family=FAMILY, seed=5, differential=True,
        target_nodes=["node1", "amaru-relay-1"], records=records,
        duration_seconds=1.0, runtime_root="rt", compose_project="proj", target_health={})

    # verdict-level agree + invariant hold (both reject every iteration)
    assert R.evaluate_soak_agree(result)["result"] == "pass"
    assert R.evaluate_soak_invariant(result)["result"] == "pass"
    # but reasons_agree FAILS on the precedence divergence
    ra = R.evaluate_soak_reasons_agree(result)
    assert ra["result"] == "fail"
    assert len(result["reason_mismatches"]) == 1
    rm = result["reason_mismatches"][0]
    assert rm["iteration"] == divergent_iter
    # replayable: captured spec regenerates from (seed, iteration)
    assert rm["spec"] == F.generate_case(FAMILY, 5, divergent_iter)
    assert set(rm["spec"]["params"]["rules"]) <= set(F.ERROR_PRECEDENCE_RULES)


def test_all_agree_run_passes_all_three_oracles():
    records = [_diff_record(F.generate_case(FAMILY, 5, i), "rejected", "rejected",
                            "KESBeforeStartOCERT", "OpCertKesPeriodTooLarge")
               for i in range(8)]
    result = R.build_soak_result(
        family=FAMILY, seed=5, differential=True,
        target_nodes=["node1", "amaru-relay-1"], records=records,
        duration_seconds=1.0, runtime_root="rt", compose_project="proj", target_health={})
    assert R.evaluate_soak_invariant(result)["result"] == "pass"
    assert R.evaluate_soak_agree(result)["result"] == "pass"
    assert R.evaluate_soak_reasons_agree(result)["result"] == "pass"
    assert result["conclusive"] == 8
