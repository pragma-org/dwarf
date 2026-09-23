from scripts import runtime_opcert_header_cases as ohc


def _case(cid, verdict, reason):
    return {"id": cid, "expected_verdict": verdict,
            "expected_reason": {"cardano-node": reason, "amaru": reason}}


def test_join_matches_reject_with_right_reason():
    cases = [_case("counter-behind", "reject", "CounterTooSmallOCERT")]
    served = {"counter-behind": "h1"}
    observed = {"h1": {"verdict": "rejected", "reason": "CounterTooSmallOCERT"}}
    rows = ohc.join_cases(cases, served, observed, implementation="cardano-node")
    assert rows[0]["status"] == "matched"


def test_wrong_reason_is_mismatch_not_pass():
    cases = [_case("counter-behind", "reject", "CounterTooSmallOCERT")]
    served = {"counter-behind": "h1"}
    observed = {"h1": {"verdict": "rejected", "reason": "InvalidKesSignatureOCERT"}}
    rows = ohc.join_cases(cases, served, observed, implementation="cardano-node")
    assert rows[0]["status"] == "mismatch"


def test_accepted_bad_case_is_mismatch():
    cases = [_case("counter-behind", "reject", "CounterTooSmallOCERT")]
    served = {"counter-behind": "h1"}
    observed = {"h1": {"verdict": "accepted", "reason": None}}
    assert ohc.join_cases(cases, served, observed, "cardano-node")[0]["status"] == "mismatch"


def test_unserved_case_is_inconclusive():
    cases = [_case("counter-behind", "reject", "CounterTooSmallOCERT")]
    assert ohc.join_cases(cases, {}, {}, "cardano-node")[0]["status"] == "inconclusive"


def test_served_but_unobserved_is_inconclusive():
    cases = [_case("counter-behind", "reject", "CounterTooSmallOCERT")]
    rows = ohc.join_cases(cases, {"counter-behind": "h1"}, {}, "cardano-node")
    assert rows[0]["status"] == "inconclusive"


def test_valid_control_accepted_is_matched():
    cases = [_case("valid-control", "accept", None)]
    rows = ohc.join_cases(cases, {"valid-control": "h0"},
                          {"h0": {"verdict": "accepted", "reason": None}}, "amaru")
    assert rows[0]["status"] == "matched"


def test_valid_control_rejected_is_mismatch():
    cases = [_case("valid-control", "accept", None)]
    rows = ohc.join_cases(cases, {"valid-control": "h0"},
                          {"h0": {"verdict": "rejected", "reason": "whatever"}}, "amaru")
    assert rows[0]["status"] == "mismatch"


def test_row_carries_expected_and_observed_fields():
    cases = [_case("counter-behind", "reject", "CounterTooSmallOCERT")]
    row = ohc.join_cases(cases, {"counter-behind": "h1"},
                         {"h1": {"verdict": "rejected", "reason": "CounterTooSmallOCERT"}},
                         "cardano-node")[0]
    assert row["case"] == "counter-behind"
    assert row["expected_verdict"] == "reject"
    assert row["expected_reason"] == "CounterTooSmallOCERT"
    assert row["served_hash"] == "h1"
    assert row["observed_verdict"] == "rejected"
    assert row["observed_reason"] == "CounterTooSmallOCERT"
