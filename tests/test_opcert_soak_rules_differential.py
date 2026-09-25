"""Decisive fake-unit tests for the opcert rules-differential family + reason parity.

Covers (per the family-#1 deliverable):
  (a) both nodes reject with the SAME canonical reason  -> agree, no reason_mismatch
  (b) both nodes reject with DIFFERENT canonical reasons -> reason_mismatch (finding),
      verdict-level still agree, reasons_agree FAILS (distinct from a verdict disagree)
  (c) verdict disagreement (one accept / one reject)     -> scored disagree
  (d) generator determinism / replay / non-constant + reachable-rules-only
"""
import json

import pytest

from scripts import opcert_soak_families as F
from scripts import opcert_soak_result as R
from scripts import runtime_opcert_header_soak as soak
from scripts.header_validation_parse import canonical_reason


# --------------------------------------------------------------------------- #
# Driver-loop harness (mirrors tests/test_runtime_opcert_header_soak.py seams).
# --------------------------------------------------------------------------- #
class FakeClock:
    def __init__(self, budget, step=1.0):
        self.t = 0.0
        self.budget = budget
        self.step = step

    def __call__(self):
        v = self.t
        self.t += self.step
        return v


def _patch_substrate(monkeypatch):
    monkeypatch.setattr(soak, "_open_consumers",
                        lambda *a, **k: {"node1": object(), "amaru-relay-1": object()})
    monkeypatch.setattr(soak, "_close_consumers", lambda *a, **k: None)
    monkeypatch.setattr(soak, "_load_runtime_root",
                        lambda root: ({"compose_project": "p"}, "cardano-node"))


def _patch_persistent(monkeypatch):
    monkeypatch.setattr(soak, "_ensure_specs", lambda *a, **k: None)
    monkeypatch.setattr(soak, "_launch_persistent_forger",
                        lambda spec_dir, ctx, **k: {"node": k.get("node"), "ctx": ctx, "proc": None})
    monkeypatch.setattr(soak, "_stop_forger", lambda handle: None)


def _run(monkeypatch, tmp_path, fake_await, budget=6):
    _patch_substrate(monkeypatch)
    _patch_persistent(monkeypatch)
    monkeypatch.setattr(soak, "_await_indexed_verdict", fake_await)
    return soak.run_opcert_header_soak(
        str(tmp_path), "rules-differential", 5, str(tmp_path / "o"),
        target_nodes=["node1", "amaru-relay-1"], time_budget_seconds=budget,
        clock=FakeClock(budget=budget))


# --------------------------------------------------------------------------- #
# (a) both reject SAME canonical rule -> agree, no reason divergence.
# --------------------------------------------------------------------------- #
def test_both_reject_same_canonical_reason_is_agree_no_reason_mismatch(monkeypatch, tmp_path):
    def fake_await(handle, ix, *, timeout):
        if handle["node"] == "node1":
            return ("hc%d" % ix, {"verdict": "rejected", "reason": "CounterOverIncrementedOCERT"})
        return ("ha%d" % ix, {"verdict": "rejected", "reason": "SequenceNumberTooFarAhead"})

    r = _run(monkeypatch, tmp_path, fake_await)
    assert r["counters"]["agree"] == r["iterations"] and r["conclusive"] == r["iterations"]
    assert r["reason_mismatches"] == []
    assert R.evaluate_soak_agree(r)["result"] == "pass"
    assert R.evaluate_soak_reasons_agree(r)["result"] == "pass"


# --------------------------------------------------------------------------- #
# (b) both reject DIFFERENT canonical rule -> reason divergence finding.
#     Verdict-level is still agree; reasons_agree FAILS. Distinct signals.
# --------------------------------------------------------------------------- #
def test_both_reject_different_canonical_reason_is_reason_mismatch(monkeypatch, tmp_path):
    def fake_await(handle, ix, *, timeout):
        if handle["node"] == "node1":
            # cardano says counter-too-large ...
            return ("hc%d" % ix, {"verdict": "rejected", "reason": "CounterOverIncrementedOCERT"})
        # ... amaru says kes-before-window: same verdict, DIFFERENT rule.
        return ("ha%d" % ix, {"verdict": "rejected", "reason": "OpCertKesPeriodTooLarge"})

    r = _run(monkeypatch, tmp_path, fake_await)
    # verdict-level agreement is unaffected (both rejected)
    assert r["counters"]["agree"] == r["iterations"]
    assert R.evaluate_soak_agree(r)["result"] == "pass"
    # but every iteration is a reason divergence, recorded distinctly
    assert len(r["reason_mismatches"]) == r["iterations"] and r["iterations"] >= 1
    row = r["reason_mismatches"][0]
    assert row["canonical_a"] == "counter-too-large" and row["canonical_b"] == "kes-before-window"
    assert row["node_a"] == "node1" and row["node_b"] == "amaru-relay-1"
    assert "spec" in row  # replayable
    assert R.evaluate_soak_reasons_agree(r)["result"] == "fail"


# --------------------------------------------------------------------------- #
# (c) verdict disagreement (one accept / one reject) -> scored disagree.
# --------------------------------------------------------------------------- #
def test_verdict_disagreement_is_disagree(monkeypatch, tmp_path):
    def fake_await(handle, ix, *, timeout):
        if handle["node"] == "node1":
            return ("hc%d" % ix, {"verdict": "accepted", "reason": None})
        return ("ha%d" % ix, {"verdict": "rejected", "reason": "InvalidSignature"})

    r = _run(monkeypatch, tmp_path, fake_await)
    assert r["counters"]["disagree"] == r["iterations"] and r["iterations"] >= 1
    assert R.evaluate_soak_agree(r)["result"] == "fail"
    # a verdict disagreement is NOT a reason divergence (reason parity is only
    # evaluated when both nodes reject).
    assert r["reason_mismatches"] == []


def test_reason_parity_classifier_direct():
    assert R.classify_reason_parity("CounterTooSmallOCERT", "SequenceNumberTooSmall") is None
    mm = R.classify_reason_parity("KESBeforeStartOCERT", "InvalidSignature",
                                  node_a="node1", node_b="amaru-relay-1")
    assert mm["canonical_a"] == "kes-before-window" and mm["canonical_b"] == "cold-key-unauthorized"
    # unrecognised token fails closed (cannot show two rejects agree on a rule)
    assert R.classify_reason_parity("CounterTooSmallOCERT", "totally-unknown") is not None


def test_canonical_reason_map_disjoint_namespaces():
    assert canonical_reason("CounterOverIncrementedOCERT") == "counter-too-large"
    assert canonical_reason("SequenceNumberTooFarAhead") == "counter-too-large"
    assert canonical_reason(None) is None and canonical_reason("nope") is None


def test_reasons_agree_vacuous_zero_conclusive_fails():
    result = R.build_soak_result(
        family="rules-differential", seed=5, differential=True,
        target_nodes=["node1", "amaru-relay-1"],
        records=[{"outcome": "inconclusive", "differential": "inconclusive",
                  "spec": {"iteration": i}} for i in range(3)],
        duration_seconds=1.0, runtime_root="", compose_project="", target_health={})
    assert result["conclusive"] == 0
    assert R.evaluate_soak_reasons_agree(result)["result"] == "fail"


# --------------------------------------------------------------------------- #
# (d) generator: determinism / replay / non-constant / reachable rules only.
# --------------------------------------------------------------------------- #
def test_rules_differential_is_differential_family():
    assert "rules-differential" in F.FAMILIES
    assert "rules-differential" in F.DIFFERENTIAL_FAMILIES


def test_rules_differential_deterministic_and_replayable():
    a = F.generate_case("rules-differential", seed=1234, iteration=17)
    assert a == F.generate_case("rules-differential", seed=1234, iteration=17)
    assert a == F.generate_case(a["family"], a["seed"], a["iteration"])


def test_rules_differential_not_constant_and_all_reject():
    specs = [F.generate_case("rules-differential", 5, i) for i in range(60)]
    assert len({repr(s["params"]) for s in specs}) > 1
    assert all(s["expected_verdict"] == "reject" for s in specs)


def test_rules_differential_covers_only_reachable_rules():
    seen = {F.generate_case("rules-differential", 5, i)["base_case"] for i in range(400)}
    assert seen == {"cold-key-unauthorized", "counter-jump",
                    "kes-before-window", "hot-key-mismatch"}
    # the rotation/aging-only rules are excluded (owned by restart/aging families)
    assert "counter-behind" not in seen and "kes-after-window" not in seen


def test_rules_differential_expected_reason_per_node_matches_corpus():
    import pathlib
    corpus = json.loads(pathlib.Path(
        "dwarf/corpora/opcert/opcert-header-cases-v1.json").read_text(encoding="utf-8"))
    by_id = {c["id"]: c for c in corpus}
    for i in range(200):
        spec = F.generate_case("rules-differential", 5, i)
        assert spec["expected_reason"] == by_id[spec["base_case"]]["expected_reason"]


def test_rules_differential_boundary_recorded_for_magnitude_rules():
    for i in range(200):
        p = F.generate_case("rules-differential", 5, i)["params"]
        assert "byte_seed" in p and "rule" in p
        if p["rule"] == "counter-jump":
            assert p["counter_jump"] >= 2
        if p["rule"] == "kes-before-window":
            assert p["kes_periods_ahead"] >= 1
