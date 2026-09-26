"""Fake (no-devnet) unit tests for opcert soak family #5: counter-edge-cases.

Differential (mixed) family that pushes the opcert counter to edge values. The
fresh-devnet-reachable cases are the too-large edges (extreme forward jumps +
overflow max_uint64), which both nodes reject as counter-too-large; the too-small
edge (counter=0) is rotation-gated (needs recorded>=1) and only emitted with
counter_rotated=True. Scoring reuses the reason-parity path.
"""
from scripts import opcert_soak_families as F
from scripts import opcert_soak_result as R

FAMILY = "counter-edge-cases"


# --------------------------------------------------------------------------- #
# Generator.
# --------------------------------------------------------------------------- #

def test_family_registered_as_differential():
    assert FAMILY in F.FAMILIES
    assert FAMILY in F.DIFFERENTIAL_FAMILIES


def test_fresh_default_emits_only_too_large_edges():
    edges = {F.generate_case(FAMILY, s, i)["params"]["edge"]
             for s in range(30) for i in range(30)}
    assert edges == {"jump-16", "jump-32", "jump-48", "jump-63", "overflow-max"}
    assert "too-small-zero" not in edges, "fresh must not emit the rotation-gated too-small edge"


def test_rotated_adds_too_small_zero_edge():
    edges = {F.generate_case(FAMILY, s, i, counter_rotated=True)["params"]["edge"]
             for s in range(30) for i in range(30)}
    assert "too-small-zero" in edges
    assert {"jump-16", "jump-32", "jump-48", "jump-63", "overflow-max"} <= edges


def test_jump_magnitudes_are_powers_of_two_to_the_far_edge():
    seen = {}
    for i in range(3000):
        p = F.generate_case(FAMILY, 5, i)["params"]
        if p["edge"].startswith("jump-"):
            seen[p["edge"]] = p["counter_jump"]
    assert seen["jump-16"] == 1 << 16
    assert seen["jump-32"] == 1 << 32
    assert seen["jump-48"] == 1 << 48
    assert seen["jump-63"] == 1 << 63  # 2^63, the far edge


def test_overflow_edge_is_uint64_max():
    ov = next(F.generate_case(FAMILY, 5, i)["params"] for i in range(200)
              if F.generate_case(FAMILY, 5, i)["params"]["edge"] == "overflow-max")
    assert ov["counter_value"] == (1 << 64) - 1


def test_too_small_edge_is_zero_and_only_when_rotated():
    z = next(F.generate_case(FAMILY, 5, i, counter_rotated=True)["params"] for i in range(200)
             if F.generate_case(FAMILY, 5, i, counter_rotated=True)["params"]["edge"] == "too-small-zero")
    assert z["counter_value"] == 0


def test_reason_per_edge_kind():
    for i in range(400):
        c = F.generate_case(FAMILY, 5, i, counter_rotated=True)
        edge = c["params"]["edge"]
        assert c["expected_verdict"] == "reject"
        if edge == "too-small-zero":
            assert c["expected_reason"] == {"cardano-node": "CounterTooSmallOCERT",
                                            "amaru": "SequenceNumberTooSmall"}
        else:
            assert c["expected_reason"] == {"cardano-node": "CounterOverIncrementedOCERT",
                                            "amaru": "SequenceNumberTooFarAhead"}


def test_seed_iteration_deterministic_and_replayable():
    a = F.generate_case(FAMILY, 4242, 11)
    assert a == F.generate_case(FAMILY, 4242, 11)
    assert a == F.generate_case(a["family"], a["seed"], a["iteration"])
    b = F.generate_case(FAMILY, 4242, 11, counter_rotated=True)
    assert b == F.generate_case(FAMILY, 4242, 11, counter_rotated=True)


# --------------------------------------------------------------------------- #
# Scoring (reused differential reason-parity): too-large edges should AGREE
# across both nodes; an overflow-specific reason divergence would surface.
# --------------------------------------------------------------------------- #

def test_too_large_edges_agree_across_nodes():
    # both nodes map their too-large token to the same canonical rule -> agree.
    assert R.classify_reason_parity("CounterOverIncrementedOCERT", "SequenceNumberTooFarAhead",
                                    node_a="node1", node_b="amaru-relay-1") is None


def test_too_small_edges_agree_across_nodes():
    assert R.classify_reason_parity("CounterTooSmallOCERT", "SequenceNumberTooSmall",
                                    node_a="node1", node_b="amaru-relay-1") is None


def test_overflow_specific_reason_divergence_would_surface():
    # If a node reported an overflow-specific / generic reject for max_uint64 while
    # the other reported counter-too-large, the parity oracle surfaces it.
    mm = R.classify_reason_parity("CounterOverIncrementedOCERT", "SequenceNumberTooSmall",
                                  node_a="node1", node_b="amaru-relay-1")
    assert mm is not None
    assert mm["canonical_a"] == "counter-too-large"
    assert mm["canonical_b"] == "counter-too-small"


def test_all_agree_run_passes_all_oracles():
    def rec(i):
        spec = F.generate_case(FAMILY, 5, i)
        va = vb = "rejected"
        diff = R.classify_differential(va, vb)
        rm = R.classify_reason_parity("CounterOverIncrementedOCERT", "SequenceNumberTooFarAhead",
                                      node_a="node1", node_b="amaru-relay-1")
        return {"outcome": "pass", "differential": diff, "spec": spec,
                "verdicts": {"node1": va, "amaru-relay-1": vb},
                "reasons": {"node1": "CounterOverIncrementedOCERT", "amaru-relay-1": "SequenceNumberTooFarAhead"},
                "reason_mismatch": rm}
    result = R.build_soak_result(
        family=FAMILY, seed=5, differential=True, target_nodes=["node1", "amaru-relay-1"],
        records=[rec(i) for i in range(8)], duration_seconds=1.0, runtime_root="rt",
        compose_project="proj", target_health={})
    assert R.evaluate_soak_invariant(result)["result"] == "pass"
    assert R.evaluate_soak_agree(result)["result"] == "pass"
    assert R.evaluate_soak_reasons_agree(result)["result"] == "pass"
