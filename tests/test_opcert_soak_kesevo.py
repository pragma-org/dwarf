"""Fake (no-devnet) unit tests for opcert soak family #3: KES-evolution.

The forger serves a real pool1 header whose KES signature is produced with the
key evolved to the WRONG number of steps for the header's KES period (sign at
``correctEvol + delta``, delta != 0) while the opcert period/counter/cold-sig
stay valid. The node verifies the KES signature at the expected evolution
``t = kp - c0`` and it fails (a KES signature is period-bound):
``InvalidKesSignatureOCERT`` (cardano) / ``InvalidKesSignature`` (amaru).
Invariant = REJECTED; an ACCEPT is a finding.

These cover the Python-side ownership without a live node:
1. generator determinism/replay and structured (nonzero, both-sign) variation;
2. fail-closed scoring: under-/over-evolved that the node REJECTS with the KES
   reason scores ``pass``; a node that ACCEPTS scores a ``mismatch`` (finding);
   the correct evolution (delta 0) would be an accepted header, which is exactly
   why the generator never emits delta 0.
No forger, node, or docker is touched: verdicts are synthesised as the parser
would emit them and fed through the pure decision layer.
"""
from scripts import opcert_soak_families as F
from scripts import opcert_soak_result as R

FAMILY = "kes-evolution"


# --------------------------------------------------------------------------- #
# Generator.
# --------------------------------------------------------------------------- #

def test_family_registered_single_target():
    assert FAMILY in F.FAMILIES
    assert FAMILY not in F.DIFFERENTIAL_FAMILIES


def test_generator_is_seed_iteration_deterministic():
    assert F.generate_case(FAMILY, 4242, 11) == F.generate_case(FAMILY, 4242, 11)


def test_generator_replay_reproduces_spec_from_its_own_fields():
    a = F.generate_case(FAMILY, 7, 3)
    assert a == F.generate_case(a["family"], a["seed"], a["iteration"])


def test_case_shape_is_reject_with_per_node_kes_reasons():
    c = F.generate_case(FAMILY, 1, 0)
    assert c["base_case"] == "kes-evolution"
    assert c["expected_verdict"] == "reject"
    assert c["expected_reason"] == {
        "cardano-node": "InvalidKesSignatureOCERT",
        "amaru": "InvalidKesSignature",
    }
    assert c["params"]["kes_evolution_delta"] in F.KESEVO_DELTAS


def test_delta_is_never_zero_and_covers_both_signs():
    deltas = [F.generate_case(FAMILY, 5, i)["params"]["kes_evolution_delta"] for i in range(80)]
    assert 0 not in deltas, "delta 0 is the correct evolution (accepted) -- must never be emitted"
    assert any(d < 0 for d in deltas), "no under-evolution generated"
    assert any(d > 0 for d in deltas), "no over-evolution generated"
    assert set(deltas) <= set(F.KESEVO_DELTAS)


def test_delta_is_attributable_in_spec_for_finding_traceability():
    # A mis-evolved KES sig rejects with the SAME token as hot-key-mismatch, so
    # the per-iteration spec must carry the delta to attribute a finding to
    # *evolution* rather than *wrong-key*.
    for i in range(10):
        spec = F.generate_case(FAMILY, 9, i)
        assert "kes_evolution_delta" in spec["params"]


# --------------------------------------------------------------------------- #
# Fake scoring.
# --------------------------------------------------------------------------- #

def _served(reason=None, verdict="rejected"):
    return {"header_hash": "hh", "verdict": verdict, "reason": reason, "at": "t"}


def test_under_evolved_reject_scores_pass():
    # under-evolved (delta < 0) rejected with the KES reason -> invariant holds.
    spec = F.generate_case(FAMILY, 5, 0)  # delta -1
    assert spec["params"]["kes_evolution_delta"] < 0
    assert R.classify_iteration(spec, "abc", _served("InvalidKesSignatureOCERT"),
                                "cardano-node") == "pass"


def test_over_evolved_reject_scores_pass_amaru():
    spec = F.generate_case(FAMILY, 5, 1)  # delta 1
    assert spec["params"]["kes_evolution_delta"] > 0
    assert R.classify_iteration(spec, "abc", _served("InvalidKesSignature"),
                                "amaru") == "pass"


def test_accept_after_serve_scores_a_finding():
    # The node ACCEPTED a header whose KES evolution doesn't match its period.
    spec = F.generate_case(FAMILY, 5, 0)
    assert R.classify_iteration(spec, "abc", _served(verdict="accepted", reason=None),
                                "cardano-node") == "mismatch"


def test_reject_with_wrong_reason_scores_mismatch():
    spec = F.generate_case(FAMILY, 5, 0)
    assert R.classify_iteration(spec, "abc", _served("CounterTooSmallOCERT"),
                                "cardano-node") == "mismatch"


def test_reasonless_reject_is_inconclusive():
    spec = F.generate_case(FAMILY, 5, 0)
    assert R.classify_iteration(spec, "abc", _served(reason=None, verdict="rejected"),
                                "cardano-node") == "inconclusive"


def test_unreachable_never_served_is_inconclusive():
    # The forger fail-closes an out-of-range target evolution to unreachable:
    # the driver records no served hash, which is scored inconclusive (never a
    # false finding), never accept.
    spec = F.generate_case(FAMILY, 5, 0)
    assert R.classify_iteration(spec, None, None, "cardano-node") == "inconclusive"


# --------------------------------------------------------------------------- #
# End-to-end (fake): an accepting run FAILS and captures the replayable spec
# (with the delta) so a finding is attributable to evolution.
# --------------------------------------------------------------------------- #

def test_accepting_run_fails_and_captures_replayable_delta():
    records = []
    accept_iter = 3
    for i in range(8):
        spec = F.generate_case(FAMILY, 5, i)
        observed = (_served(verdict="accepted", reason=None) if i == accept_iter
                    else _served("InvalidKesSignatureOCERT"))
        outcome = R.classify_iteration(spec, "h", observed, "cardano-node")
        records.append({"outcome": outcome, "spec": spec, "served_hash": "h",
                        "observed_verdict": observed["verdict"],
                        "observed_reason": observed["reason"]})
    result = R.build_soak_result(
        family=FAMILY, seed=5, differential=False, target_nodes=["node1"],
        records=records, duration_seconds=1.0, runtime_root="rt",
        compose_project="proj", target_health={})
    assert result["pass"] is False
    assert len(result["mismatches"]) == 1
    finding = result["mismatches"][0]
    assert finding["iteration"] == accept_iter
    assert finding["spec"] == F.generate_case(FAMILY, 5, accept_iter)
    # the delta is present in the captured spec -> evolution-attributable.
    assert "kes_evolution_delta" in finding["spec"]["params"]


def test_all_reject_run_passes():
    records = []
    for i in range(8):
        spec = F.generate_case(FAMILY, 5, i)
        outcome = R.classify_iteration(spec, "h", _served("InvalidKesSignatureOCERT"),
                                       "cardano-node")
        records.append({"outcome": outcome, "spec": spec, "served_hash": "h",
                        "observed_verdict": "rejected",
                        "observed_reason": "InvalidKesSignatureOCERT"})
    result = R.build_soak_result(
        family=FAMILY, seed=5, differential=False, target_nodes=["node1"],
        records=records, duration_seconds=1.0, runtime_root="rt",
        compose_project="proj", target_health={})
    assert result["pass"] is True
    assert result["conclusive"] == 8
