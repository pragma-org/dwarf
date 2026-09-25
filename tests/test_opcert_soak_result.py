from scripts import opcert_soak_result as R


def _accept_spec(it=0):
    return {"family": "encoding-form", "seed": 7, "iteration": it,
            "base_case": "valid-control", "expected_verdict": "accept",
            "case_id": f"encoding-form-{it:06d}", "params": {"encoding_form": "trailing-bytes"}}


def _reject_spec(it=0):
    return {"family": "restart-persistence", "seed": 7, "iteration": it,
            "base_case": "counter-behind", "expected_verdict": "reject",
            "expected_reason": {"cardano-node": "CounterTooSmallOCERT", "amaru": "SequenceNumberTooSmall"},
            "case_id": f"restart-persistence-{it:06d}", "params": {"replay_counter": 0}}


def test_accept_spec_accepted_is_pass():
    assert R.classify_iteration(_accept_spec(), "h1", {"verdict": "accepted", "reason": None}, "cardano-node") == "pass"


def test_accept_spec_rejected_is_mismatch():
    assert R.classify_iteration(_accept_spec(), "h1", {"verdict": "rejected", "reason": "x"}, "cardano-node") == "mismatch"


def test_reject_spec_wrong_reason_is_mismatch():
    assert R.classify_iteration(_reject_spec(), "h1", {"verdict": "rejected", "reason": "InvalidKesSignatureOCERT"}, "cardano-node") == "mismatch"


def test_reject_spec_right_reason_per_impl_is_pass():
    assert R.classify_iteration(_reject_spec(), "h1", {"verdict": "rejected", "reason": "CounterTooSmallOCERT"}, "cardano-node") == "pass"
    assert R.classify_iteration(_reject_spec(), "h1", {"verdict": "rejected", "reason": "SequenceNumberTooSmall"}, "amaru") == "pass"


def test_unserved_is_inconclusive():
    assert R.classify_iteration(_accept_spec(), None, None, "cardano-node") == "inconclusive"


def test_served_but_unobserved_is_inconclusive():
    assert R.classify_iteration(_accept_spec(), "h1", None, "cardano-node") == "inconclusive"


def test_differential_one_side_missing_is_inconclusive():
    assert R.classify_differential("accepted", None) == "inconclusive"
    assert R.classify_differential(None, "rejected") == "inconclusive"


def test_differential_agree_and_disagree():
    assert R.classify_differential("accepted", "accepted") == "agree"
    assert R.classify_differential("accepted", "rejected") == "disagree"


def test_zero_conclusive_fails():
    records = [{"outcome": "inconclusive", "spec": _accept_spec(i)} for i in range(5)]
    result = R.build_soak_result(family="encoding-form", seed=7, differential=True,
                                 target_nodes=["node1", "amaru-relay-1"], records=records,
                                 duration_seconds=10.0, runtime_root="", compose_project="",
                                 target_health={})
    assert result["conclusive"] == 0
    assert result["pass"] is False
    assert R.evaluate_soak_invariant(result)["result"] == "fail"


def test_mismatch_row_carries_full_spec():
    spec = _accept_spec(3)
    records = [{"outcome": "mismatch", "spec": spec, "served_hash": "h1",
                "observed_verdict": "rejected", "observed_reason": None}]
    result = R.build_soak_result(family="encoding-form", seed=7, differential=False,
                                 target_nodes=["node1"], records=records, duration_seconds=1.0,
                                 runtime_root="", compose_project="", target_health={})
    row = result["mismatches"][0]
    assert row["spec"] == spec and row["iteration"] == 3 and row["served_hash"] == "h1"


def test_agree_disagree_counters_and_rows():
    specs = [_accept_spec(i) for i in range(3)]
    records = [
        {"outcome": "pass", "differential": "agree", "spec": specs[0]},
        {"outcome": "mismatch", "differential": "disagree", "spec": specs[1],
         "verdicts": {"node1": "accepted", "amaru-relay-1": "rejected"}},
        {"outcome": "inconclusive", "differential": "inconclusive", "spec": specs[2]},
    ]
    result = R.build_soak_result(family="encoding-form", seed=7, differential=True,
                                 target_nodes=["node1", "amaru-relay-1"], records=records,
                                 duration_seconds=5.0, runtime_root="", compose_project="",
                                 target_health={})
    assert result["counters"]["agree"] == 1 and result["counters"]["disagree"] == 1
    assert result["conclusive"] == 2
    assert result["disagreements"][0]["iteration"] == 1
    assert R.evaluate_soak_agree(result)["result"] == "fail"
