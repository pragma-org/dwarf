"""Fake (no-devnet) unit tests for opcert soak family #2: cross-pool confusion.

These cover the two things a family owns on the Python side without a live node:
1. the generator is seed+iteration deterministic and replayable, and its only
   structured variation is which real foreign pool authorizes the opcert; and
2. the fail-closed scoring: a served cross-pool header that the node REJECTS
   with the issuer-signature reason scores ``pass`` (invariant held), while a
   node that ACCEPTS the wrong-pool authorization scores a ``mismatch`` -- a
   real cross-pool-confusion finding, captured replayably in the soak result.

No forger, node, or docker is touched: verdicts are synthesised, exactly as the
parser would emit them, and fed through the pure decision layer.
"""
from scripts import opcert_soak_families as F
from scripts import opcert_soak_result as R

FAMILY = "cross-pool-confusion"


# --------------------------------------------------------------------------- #
# Generator: determinism, replay, structured (non-constant) variation.
# --------------------------------------------------------------------------- #

def test_family_registered():
    assert FAMILY in F.FAMILIES
    # cross-pool is single-target (not a differential family).
    assert FAMILY not in F.DIFFERENTIAL_FAMILIES


def test_generator_is_seed_iteration_deterministic():
    a = F.generate_case(FAMILY, seed=4242, iteration=11)
    b = F.generate_case(FAMILY, seed=4242, iteration=11)
    assert a == b


def test_generator_replay_reproduces_spec_from_its_own_fields():
    a = F.generate_case(FAMILY, seed=7, iteration=3)
    b = F.generate_case(a["family"], a["seed"], a["iteration"])
    assert a == b


def test_case_shape_is_reject_with_per_node_reasons():
    c = F.generate_case(FAMILY, seed=1, iteration=0)
    assert c["base_case"] == "cross-pool"
    assert c["expected_verdict"] == "reject"
    assert c["expected_reason"] == {
        "cardano-node": "InvalidSignatureOCERT",
        "amaru": "InvalidSignature",
    }
    assert c["params"]["variant"] == "foreign-cold-authorization"
    assert c["params"]["foreign_pool"] in F.CROSSPOOL_FOREIGN_POOLS


def test_foreign_pool_varies_but_stays_in_the_allowed_set():
    pools = [F.generate_case(FAMILY, seed=5, iteration=i)["params"]["foreign_pool"]
             for i in range(60)]
    assert set(pools) <= set(F.CROSSPOOL_FOREIGN_POOLS)
    # structured randomization: not a constant (would defeat the soak).
    assert len(set(pools)) > 1


def test_foreign_pool_is_never_the_victim_pool():
    # The victim header is pool1; a "foreign" pool must be a DIFFERENT pool,
    # otherwise it is not cross-pool at all.
    for i in range(60):
        assert F.generate_case(FAMILY, seed=9, iteration=i)["params"]["foreign_pool"] != "pool1"


# --------------------------------------------------------------------------- #
# Fake scoring: wrong-pool authorization must be REJECTED; an ACCEPT is a
# finding. Verdicts are synthesised exactly as the parser emits them.
# --------------------------------------------------------------------------- #

def _served(reason=None, verdict="rejected"):
    """A synthetic per-hash verdict event, shaped like the parser's output."""
    return {"header_hash": "hh", "verdict": verdict, "reason": reason, "at": "t"}


def test_reject_with_correct_reason_scores_pass_cardano():
    spec = F.generate_case(FAMILY, seed=1, iteration=0)
    observed = _served(reason="InvalidSignatureOCERT")
    outcome = R.classify_iteration(spec, served_hash="abc", observed=observed,
                                   implementation="cardano-node")
    assert outcome == "pass"


def test_reject_with_correct_reason_scores_pass_amaru():
    spec = F.generate_case(FAMILY, seed=1, iteration=0)
    observed = _served(reason="InvalidSignature")
    outcome = R.classify_iteration(spec, served_hash="abc", observed=observed,
                                   implementation="amaru")
    assert outcome == "pass"


def test_accept_after_serve_scores_a_finding():
    # The node ACCEPTED a header authorized by the wrong pool: cross-pool
    # confusion. Fail-closed scoring flags it as a mismatch (a finding).
    spec = F.generate_case(FAMILY, seed=1, iteration=0)
    observed = _served(verdict="accepted", reason=None)
    outcome = R.classify_iteration(spec, served_hash="abc", observed=observed,
                                   implementation="cardano-node")
    assert outcome == "mismatch"


def test_reject_with_wrong_reason_scores_mismatch():
    # Rejected, but for a DIFFERENT rule than wrong-pool authorization: the
    # invariant is reason-sensitive, so a wrong reason is still a mismatch.
    spec = F.generate_case(FAMILY, seed=1, iteration=0)
    observed = _served(reason="CounterTooSmallOCERT")
    outcome = R.classify_iteration(spec, served_hash="abc", observed=observed,
                                   implementation="cardano-node")
    assert outcome == "mismatch"


def test_reasonless_reject_is_inconclusive_not_a_finding():
    # A reason-less reject is a transport wedge / dark consumer, never a genuine
    # header-validation outcome: fail-closed inconclusive (never pass, never a
    # false finding).
    spec = F.generate_case(FAMILY, seed=1, iteration=0)
    observed = _served(reason=None, verdict="rejected")
    outcome = R.classify_iteration(spec, served_hash="abc", observed=observed,
                                   implementation="cardano-node")
    assert outcome == "inconclusive"


def test_never_served_or_never_observed_is_inconclusive():
    spec = F.generate_case(FAMILY, seed=1, iteration=0)
    assert R.classify_iteration(spec, None, _served("InvalidSignatureOCERT"),
                                "cardano-node") == "inconclusive"
    assert R.classify_iteration(spec, "abc", None, "cardano-node") == "inconclusive"


# --------------------------------------------------------------------------- #
# End-to-end (still fake): a run where the node accepts wrong-pool auth FAILS
# and captures the replayable spec in the result.
# --------------------------------------------------------------------------- #

def test_accepting_run_fails_and_captures_replayable_spec():
    records = []
    accept_iter = 4
    for i in range(8):
        spec = F.generate_case(FAMILY, seed=5, iteration=i)
        if i == accept_iter:
            observed = _served(verdict="accepted", reason=None)
        else:
            observed = _served(reason="InvalidSignatureOCERT")
        outcome = R.classify_iteration(spec, "h", observed, "cardano-node")
        records.append({
            "outcome": outcome, "spec": spec, "served_hash": "h",
            "observed_verdict": observed["verdict"], "observed_reason": observed["reason"],
        })

    result = R.build_soak_result(
        family=FAMILY, seed=5, differential=False, target_nodes=["node1"],
        records=records, duration_seconds=1.0, runtime_root="rt",
        compose_project="proj", target_health={})

    assert result["pass"] is False
    assert result["counters"]["mismatch"] == 1
    assert len(result["mismatches"]) == 1
    finding = result["mismatches"][0]
    assert finding["iteration"] == accept_iter
    # Replayable: the captured spec regenerates byte-for-byte from (seed, iter).
    assert finding["spec"] == F.generate_case(FAMILY, seed=5, iteration=accept_iter)


def test_all_reject_run_passes():
    records = []
    for i in range(8):
        spec = F.generate_case(FAMILY, seed=5, iteration=i)
        observed = _served(reason="InvalidSignatureOCERT")
        outcome = R.classify_iteration(spec, "h", observed, "cardano-node")
        records.append({"outcome": outcome, "spec": spec, "served_hash": "h",
                        "observed_verdict": "rejected", "observed_reason": "InvalidSignatureOCERT"})
    result = R.build_soak_result(
        family=FAMILY, seed=5, differential=False, target_nodes=["node1"],
        records=records, duration_seconds=1.0, runtime_root="rt",
        compose_project="proj", target_health={})
    assert result["pass"] is True
    assert result["conclusive"] == 8
    assert result["counters"]["pass"] == 8
