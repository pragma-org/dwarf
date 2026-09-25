"""Fake unit tests for opcert_soak_result.summarize_precedence.

Builds the error-precedence finding table programmatically from a differential
result's reason_mismatches (result.json only, no re-run). The fixture mirrors the
confirmed live divergence (resmoke-errorprec): amaru ranks counter-jump first,
cardano ranks it last; cardano/amaru also invert cold-key vs KES.
"""
from scripts import opcert_soak_result as R

N1 = "node1"
N2 = "amaru-relay-1"


def _rm(rules, canon_a, canon_b, reason_a="ra", reason_b="rb"):
    return {
        "node_a": N1, "node_b": N2,
        "reason_a": reason_a, "reason_b": reason_b,
        "canonical_a": canon_a, "canonical_b": canon_b,
        "spec": {"params": {"rules": rules}},
    }


def _result(reason_mismatches):
    return {"reason_mismatches": reason_mismatches, "differential": True}


def test_empty_result_yields_empty_tables():
    out = R.summarize_precedence(_result([]))
    assert out["reason_mismatch_count"] == 0
    assert out["combos"] == []
    assert out["precedence_edges"] == {}
    assert out["target_nodes"] == []


def test_single_combo_table_and_edges():
    # cold-key + counter-jump: cardano reports cold-key, amaru reports counter.
    out = R.summarize_precedence(_result([
        _rm(["cold-key-unauthorized", "counter-jump"], "cold-key-unauthorized", "counter-too-large"),
    ]))
    assert out["target_nodes"] == [N2, N1] or out["target_nodes"] == sorted([N1, N2])
    assert out["reason_mismatch_count"] == 1
    assert len(out["combos"]) == 1
    combo = out["combos"][0]
    assert combo["combo"] == ["cold-key-unauthorized", "counter-jump"]
    assert combo["count"] == 1
    assert combo["reported"][N1] == "cold-key-unauthorized"
    assert combo["reported"][N2] == "counter-too-large"
    # edges: cardano ranked cold-key > counter; amaru ranked counter > cold-key
    assert ["cold-key-unauthorized", "counter-too-large"] in out["precedence_edges"][N1]
    assert ["counter-too-large", "cold-key-unauthorized"] in out["precedence_edges"][N2]


def test_counts_aggregate_across_repeated_combo():
    rms = [_rm(["counter-jump", "kes-before-window"], "kes-before-window", "counter-too-large")
           for _ in range(3)]
    out = R.summarize_precedence(_result(rms))
    assert out["reason_mismatch_count"] == 3
    assert len(out["combos"]) == 1
    assert out["combos"][0]["count"] == 3


def test_full_confirmed_divergence_reconstructs_check_orders():
    # The six live reason_mismatches from resmoke-errorprec (seed 20260925).
    rms = [
        _rm(["cold-key-unauthorized", "counter-jump"], "cold-key-unauthorized", "counter-too-large"),
        _rm(["counter-jump", "kes-before-window"], "kes-before-window", "counter-too-large"),
        _rm(["counter-jump", "kes-before-window"], "kes-before-window", "counter-too-large"),
        _rm(["counter-jump", "hot-key-mismatch"], "hot-key-mismatch", "counter-too-large"),
        _rm(["counter-jump", "kes-before-window"], "kes-before-window", "counter-too-large"),
        _rm(["cold-key-unauthorized", "kes-before-window"], "kes-before-window", "cold-key-unauthorized"),
    ]
    out = R.summarize_precedence(_result(rms))
    assert out["reason_mismatch_count"] == 6
    # four distinct diverging combos
    assert len(out["combos"]) == 4

    n1_edges = {tuple(e) for e in out["precedence_edges"][N1]}
    n2_edges = {tuple(e) for e in out["precedence_edges"][N2]}

    # cardano (node1): counter-too-large is checked LAST -> everything ranks above it
    assert ("cold-key-unauthorized", "counter-too-large") in n1_edges
    assert ("kes-before-window", "counter-too-large") in n1_edges
    assert ("hot-key-mismatch", "counter-too-large") in n1_edges
    # cardano ranks kes-before-window above cold-key
    assert ("kes-before-window", "cold-key-unauthorized") in n1_edges

    # amaru (amaru-relay-1): counter-too-large is checked FIRST -> ranks above all
    assert ("counter-too-large", "cold-key-unauthorized") in n2_edges
    assert ("counter-too-large", "kes-before-window") in n2_edges
    assert ("counter-too-large", "hot-key-mismatch") in n2_edges
    # amaru ranks cold-key above kes-before-window (the inversion vs cardano)
    assert ("cold-key-unauthorized", "kes-before-window") in n2_edges

    # the inversion is real: cardano and amaru disagree on cold-key vs kes order
    assert ("kes-before-window", "cold-key-unauthorized") in n1_edges
    assert ("cold-key-unauthorized", "kes-before-window") in n2_edges
