"""Fake unit tests for the opcert-field cross-decoder differential (family #6).

Cover the pure decision layer (no subprocess): outcome normalization, per-mutation
classification (agree / divergence / crash), and report assembly — including the
confirmed live result where amaru accepts a truncated hot-vkey / cold-sig / extra
opcert field that cardano-node rejects.
"""
from scripts import opcert_field_decoder_diff as D


def test_normalize_outcome_contract():
    assert D.normalize_outcome(0, "OK") == "ok"
    assert D.normalize_outcome(0, "OK\n") == "ok"
    assert D.normalize_outcome(1, "ERR end of input bytes") == "error"
    assert D.normalize_outcome(139, "") == "crash"          # signal
    assert D.normalize_outcome(0, "weird") == "crash"       # exit 0 but not OK
    assert D.normalize_outcome(2, "ERR ...") == "crash"     # wrong error exit


def test_classify_pair():
    assert D.classify_pair("ok", "ok") == "agree"
    assert D.classify_pair("error", "error") == "agree"
    assert D.classify_pair("ok", "error") == "divergence"
    assert D.classify_pair("error", "ok") == "divergence"
    assert D.classify_pair("crash", "ok") == "crash"
    assert D.classify_pair("ok", "crash") == "crash"


def _row(mutation, a, c):
    return {"mutation": mutation, "amaru": {"outcome": a}, "cardano": {"outcome": c}}


def test_build_report_counts_and_divergences():
    rows = [
        _row("counter-negative", "error", "error"),
        _row("counter-type-text", "error", "error"),
        _row("hot-vkey-truncated", "ok", "error"),      # divergence
        _row("cold-sig-truncated", "ok", "error"),      # divergence
        _row("opcert-extra-field", "ok", "error"),      # divergence
    ]
    rep = D.build_report(rows)
    assert rep["mutations"] == 5
    assert rep["agree"] == 2
    assert rep["divergences"] == 3
    assert rep["crashes"] == 0
    muts = {d["mutation"] for d in rep["divergence_detail"]}
    assert muts == {"hot-vkey-truncated", "cold-sig-truncated", "opcert-extra-field"}
    for d in rep["divergence_detail"]:
        assert d["amaru"] == "ok" and d["cardano"] == "error"


def test_all_agree_report_has_no_divergences():
    rows = [_row(m, "error", "error") for m in D.MUTATIONS]
    rep = D.build_report(rows)
    assert rep["divergences"] == 0
    assert rep["agree"] == len(D.MUTATIONS)


def test_crash_is_not_scored_as_agreement():
    rows = [_row("counter-negative", "crash", "crash")]
    rep = D.build_report(rows)
    assert rep["agree"] == 0
    assert rep["crashes"] == 1


def test_mutation_set_matches_forger_list():
    # The 11 field mutations the forger implements.
    assert set(D.MUTATIONS) == {
        "counter-negative", "counter-bignum-oversized", "counter-type-text",
        "counter-type-bytes", "kesperiod-type-text", "kesperiod-negative",
        "hot-vkey-truncated", "cold-sig-truncated", "opcert-missing-field",
        "opcert-duplicate-field", "opcert-extra-field",
    }
