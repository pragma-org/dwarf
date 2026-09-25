"""Soak result model + fail-closed decision helpers.

A soak runs one *family* for a wall-clock budget, generating a fresh
seed-deterministic case each iteration, serving it through the existing opcert
forger, and reading the target(s)' verdict(s) through the existing parser. This
module is the pure decision layer:

- ``classify_iteration`` reuses the exact accept/reject/reason semantics of
  ``runtime_opcert_header_cases.join_cases``: an accept-spec passes iff the
  observed verdict is ``accepted``; a reject-spec passes iff the verdict is
  ``rejected`` AND the observed reason equals the per-implementation expected
  reason. Never-served or never-observed is ``inconclusive`` (never a pass).
- ``classify_differential`` compares two nodes' verdicts; either side ``None``
  (inconclusive on that side) makes the whole iteration inconclusive, so a
  one-sided iteration is never scored as agreement.
- ``build_soak_result`` assembles the ``result.json`` body from the driver's
  per-iteration records, with fail-closed counters: zero conclusive iterations
  makes the whole run ``pass: False``.
"""
from __future__ import annotations

from scripts.header_validation_parse import canonical_reason


def conclusive_verdict(observed):
    """Reduce a raw per-hash verdict event to a *conclusive* verdict token, or
    ``None`` (inconclusive).

    Fail-closed attribution (the iter-243 false-divergence fix): a ``rejected``
    verdict counts as a genuine header-validation rejection ONLY when it carries
    an attributable reason token. A reason-less / ``null``-reason reject is not a
    hard rejection -- it is how a transport wedge, a dropped connection, or a
    consumer going dark mid-stream surfaces on the wire -- so it downgrades to
    ``None`` and the iteration is excluded from agree/disagree rather than scored
    a false disagreement. ``accepted`` is always conclusive; ``None`` observed
    (never observed) stays ``None``.
    """
    if observed is None:
        return None
    verdict = observed.get("verdict")
    if verdict == "rejected":
        reason = observed.get("reason")
        if reason is None or reason == "":
            return None
        return "rejected"
    if verdict == "accepted":
        return "accepted"
    return None


def classify_iteration(spec, served_hash, observed, implementation):
    """Classify one iteration as ``pass`` | ``mismatch`` | ``inconclusive``.

    Mirrors ``runtime_opcert_header_cases.join_cases``. ``spec`` carries the
    expected verdict (``accept``/``reject``) and, for reject specs, a per-node
    ``expected_reason`` dict; ``implementation`` selects the expected token.
    ``served_hash is None`` (never served) or ``observed is None`` (served but
    never observed) is fail-closed ``inconclusive``.
    """
    if served_hash is None or observed is None:
        return "inconclusive"
    verdict = conclusive_verdict(observed)
    # A reason-less reject (transport wedge / dark consumer) is not a genuine
    # header-validation outcome: fail-closed inconclusive, never a mismatch.
    if verdict is None:
        return "inconclusive"
    expected_verdict = spec.get("expected_verdict")
    if expected_verdict == "accept":
        return "pass" if verdict == "accepted" else "mismatch"
    # reject: verdict AND reason must match the per-implementation expectation.
    expected_reason = (spec.get("expected_reason") or {}).get(implementation)
    if verdict == "rejected" and observed.get("reason") == expected_reason:
        return "pass"
    return "mismatch"


def classify_differential(verdict_a, verdict_b):
    """Compare two nodes' verdicts: ``agree`` | ``disagree`` | ``inconclusive``.

    Either side ``None`` (that side inconclusive) ⇒ ``inconclusive`` — a
    one-sided iteration is never counted as agreement (guards against false
    agreement when a node simply never received the header).
    """
    if verdict_a is None or verdict_b is None:
        return "inconclusive"
    return "agree" if verdict_a == verdict_b else "disagree"


def classify_reason_parity(reason_a, reason_b, *, node_a=None, node_b=None):
    """Reason-parity for a BOTH-REJECT differential iteration.

    The caller invokes this only when the two nodes already AGREE on the verdict
    ``rejected`` (a verdict-level agreement). It maps each node's observed reason
    token to its canonical opcert rule (``header_validation_parse.canonical_reason``,
    which is implementation-agnostic because the cardano ``*OCERT`` and amaru
    token namespaces are disjoint). Returns ``None`` when both reasons map to the
    SAME non-None canonical rule (reasons agree); otherwise a REASON-DIVERGENCE
    finding dict -- both rejected, but for a different (or unrecognised) rule.
    This is DISTINCT from a verdict disagreement: the verdict-level score stays
    ``agree`` while this surfaces the subtler reason divergence.
    """
    canon_a = canonical_reason(reason_a)
    canon_b = canonical_reason(reason_b)
    if canon_a is not None and canon_b is not None and canon_a == canon_b:
        return None
    return {
        "node_a": node_a, "node_b": node_b,
        "reason_a": reason_a, "reason_b": reason_b,
        "canonical_a": canon_a, "canonical_b": canon_b,
    }


def build_soak_result(*, family, seed, differential, target_nodes, records,
                      duration_seconds, runtime_root, compose_project, target_health):
    """Assemble the soak ``result.json`` body from per-iteration ``records``.

    Each record has at least ``outcome`` (``pass``/``mismatch``/``inconclusive``)
    and ``spec``; differential records also carry ``differential``
    (``agree``/``disagree``/``inconclusive``) and, on disagreement, ``verdicts``.
    Fail-closed: ``pass`` is True only when there are no mismatches, no
    disagreements, and at least one conclusive iteration.
    """
    counters = {"pass": 0, "mismatch": 0, "inconclusive": 0}
    if differential:
        counters["agree"] = 0
        counters["disagree"] = 0

    mismatches = []
    disagreements = []
    reason_mismatches = []
    conclusive = 0

    for record in records:
        spec = record.get("spec") or {}
        iteration = spec.get("iteration")
        outcome = record.get("outcome")
        if outcome in counters:
            counters[outcome] += 1
        if outcome != "inconclusive":
            conclusive += 1
        if outcome == "mismatch":
            mismatches.append({
                "iteration": iteration,
                "case_id": spec.get("case_id"),
                "spec": spec,
                "served_hash": record.get("served_hash"),
                "observed_verdict": record.get("observed_verdict"),
                "observed_reason": record.get("observed_reason"),
            })
        if differential:
            diff = record.get("differential")
            if diff in ("agree", "disagree"):
                counters[diff] += 1
            if diff == "disagree":
                disagreements.append({
                    "iteration": iteration,
                    "case_id": spec.get("case_id"),
                    "spec": spec,
                    "verdicts": record.get("verdicts") or {},
                })
            # A both-reject-but-different-canonical-reason iteration is a REASON
            # divergence: recorded here, DISTINCT from a verdict disagreement (the
            # iteration is still scored verdict-level ``agree`` above).
            reason_mismatch = record.get("reason_mismatch")
            if reason_mismatch:
                reason_mismatches.append({
                    "iteration": iteration,
                    "case_id": spec.get("case_id"),
                    "spec": spec,
                    "verdicts": record.get("verdicts") or {},
                    "reasons": record.get("reasons") or {},
                    **reason_mismatch,
                })

    passed = conclusive > 0 and not mismatches and not disagreements

    result = {
        "schema_version": "v1",
        "family": family,
        "seed": seed,
        "differential": differential,
        "target_nodes": list(target_nodes),
        "iterations": len(records),
        "conclusive": conclusive,
        "inconclusive": counters["inconclusive"],
        "pass": passed,
        "counters": counters,
        "mismatches": mismatches,
        "disagreements": disagreements,
        "reason_mismatches": reason_mismatches,
        "duration_seconds": duration_seconds,
        "runtime_root": runtime_root,
        "compose_project": compose_project,
        "target_health": target_health,
    }
    return result


def evaluate_soak_invariant(result):
    """PASS iff ``mismatches == []`` and ``conclusive > 0`` (fail-closed vacuous)."""
    mismatches = result.get("mismatches") or []
    conclusive = int(result.get("conclusive") or 0)
    passed = not mismatches and conclusive > 0
    return {"result": "pass" if passed else "fail",
            "mismatches": mismatches, "conclusive": conclusive}


def evaluate_soak_agree(result):
    """Differential families: PASS iff ``disagreements == []`` and ``conclusive > 0``."""
    disagreements = result.get("disagreements") or []
    conclusive = int(result.get("conclusive") or 0)
    passed = not disagreements and conclusive > 0
    return {"result": "pass" if passed else "fail",
            "disagreements": disagreements, "conclusive": conclusive}


def evaluate_soak_reasons_agree(result):
    """Differential families: PASS iff ``reason_mismatches == []`` and
    ``conclusive > 0`` (fail-closed vacuous). Verdict-level agreement is checked
    separately by ``evaluate_soak_agree``; this asserts that every both-reject
    iteration also agreed on the CANONICAL rejection rule."""
    reason_mismatches = result.get("reason_mismatches") or []
    conclusive = int(result.get("conclusive") or 0)
    passed = not reason_mismatches and conclusive > 0
    return {"result": "pass" if passed else "fail",
            "reason_mismatches": reason_mismatches, "conclusive": conclusive}
