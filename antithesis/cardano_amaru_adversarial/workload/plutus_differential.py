"""Plutus phase-2 differential driver (cardano-node vs Amaru).

Extends mixed_phase1 (transport + funding-conflict MASKED logic) and reuses
stake_pool_differential's grading shape. Unlike the phase-1 families, a rejection here comes
from the Plutus VM: the transaction is phase-1-valid (valid collateral, correct script-integrity
hash, redeemer present, declared ex-units <= maxTxExUnits), so any reject is unambiguously
phase-2 -- a disagreement between the transaction's claimed `is_valid` tag and the actual script
results (cardano `ValidationTagMismatch` / `ScriptWitnessNotValidating`; Amaru "transaction
failed phase two validation: validation tag mismatch").

Oracle, per case:
  - VERDICT parity (primary): both accept (202: scripts evaluate to the claimed is_valid), or
    both reject (phase-2). One-accepts-one-rejects is a real, consensus-relevant VM divergence.
  - REASON-CLASS parity on a shared reject: the phase-2 error class must intersect.
Fail-closed: either node MASKED (funding input already consumed) or unavailable -> INCONCLUSIVE,
never a pass and never a divergence.
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # workload/ on path
import mixed_phase1 as mp
from mixed_phase1 import HttpSubmitTransport, ACCEPTED, MASKED, UNAVAILABLE

PHASE2_REJECT = "phase2_reject"

# phase-2 reject markers, matched case-insensitively: (cardano-node, Amaru)
_P2 = re.compile
# Both nodes reduce every is_valid-tag contradiction to one semantic class, phrased differently:
#   cardano: "ValidationTagMismatch (IsValid True) FailedUnexpectedly" / "(IsValid False) PassedUnexpectedly"
#   amaru:   "...phase two validation: expected scripts to pass but they failed" / "...to fail but they passed"
# so tag_mismatch must match BOTH phrasings, or agreeing rejects read as a false REASON-DIVERGENCE.
# prep_error is the distinct phase-2 failure where context/inputs cannot even be assembled.
_MARKERS = {
    "tag_mismatch": (
        _P2(r"validationtagmismatch|failedunexpectedly|passedunexpectedly", re.I),
        _P2(r"expected scripts to (pass|fail) but they (fail|pass)|validation tag mismatch", re.I)),
    "prep_error": (
        _P2(r"badtranslation|translationerror|missing.?script|extraredeemers|missingredeemers", re.I),
        _P2(r"script preparation failed|missing input|unable to construct|translation", re.I)),
}


def classify(status, body, payload=None):
    """202 -> accepted; funding-conflict -> masked; no response -> unavailable; any other
    non-2xx here is a phase-2 reject (these txs are phase-1-valid by construction)."""
    base = mp.classify_response(status, body, payload=payload)
    if base in (ACCEPTED, MASKED, UNAVAILABLE):
        return base
    return PHASE2_REJECT  # decode_reject/phase1_reject/unknown all mean "rejected, not phase-2-accepted"


def reason_classes(reason, node):
    idx = 0 if node == "cardano" else 1
    low = (reason or "")
    return {name: bool(pats[idx].search(low)) for name, pats in _MARKERS.items()}


def _classes(reason, node):
    return {k for k, v in reason_classes(reason, node).items() if v}


def grade(case, obs):
    """obs = {node: {'status','body'|'reason'}}. Returns a graded row."""
    o = {n: {"status": v.get("status"),
             "reason": (v.get("reason") or v.get("body") or "")} for n, v in obs.items()}
    cls = {n: classify(v["status"], v["reason"]) for n, v in o.items()}
    row = {"case_id": case["case_id"], "expected": case["expected"], "classes": cls}
    if any(c in (MASKED, UNAVAILABLE) for c in cls.values()) or len(cls) < 2:
        row["status"] = "INCONCLUSIVE"
        row["why"] = "masked" if MASKED in cls.values() else "unavailable"
        return row
    verdict_parity = len(set(cls.values())) == 1
    want = ACCEPTED if case["expected"] == "accept" else PHASE2_REJECT
    row["verdict_parity"] = verdict_parity
    row["matches_expected"] = all(c == want for c in cls.values())
    if not (verdict_parity and row["matches_expected"]):
        row["status"] = "VERDICT-DIVERGENCE"      # <-- the headline: is_valid divergence
    elif want == PHASE2_REJECT:
        got = {n: _classes(o[n]["reason"], n) for n in o}
        row["reason_classes"] = {n: sorted(g) for n, g in got.items()}
        inter = set.intersection(*got.values()) if all(got.values()) else set()
        row["status"] = "AGREE" if inter else "REASON-DIVERGENCE"
    else:
        row["status"] = "AGREE"
    for n in o:
        row[f"{n}_reason"] = str(o[n]["reason"])[:240]
    return row


def run(corpus_dir, amaru_url, cardano_url, single=None):
    root = Path(corpus_dir)
    manifest = json.loads((root / "plutus_corpus.json").read_text())
    t = {"amaru": HttpSubmitTransport(amaru_url), "cardano": HttpSubmitTransport(cardano_url)}
    if single:
        cases = [c for c in manifest["cases"] if c["case_id"] == single and c.get("single_use")]
        if not cases:
            raise SystemExit(f"unknown single-use case: {single}")
    else:
        cases = [c for c in manifest["cases"] if not c.get("single_use")]
    rows = []
    for case in cases:
        payload = bytes.fromhex(json.loads((root / case["tx_file"]).read_text())["cborHex"])
        obs = {n: tr.send(payload) for n, tr in t.items()}
        rows.append(grade(case, obs))
    return rows


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--corpus", default=str(Path(__file__).resolve().parents[1] / "fixture" / "plutus"))
    p.add_argument("--amaru", default="http://localhost:3020/api/submit/tx")
    p.add_argument("--cardano", default="http://localhost:8093/api/submit/tx")
    p.add_argument("--single", help="run ONE single-use case (after a mempool reset)")
    a = p.parse_args()
    rows = run(a.corpus, a.amaru, a.cardano, a.single)
    print(json.dumps(rows, indent=2))
    st = {r["status"] for r in rows}
    label = "SINGLE " + a.single if a.single else f"CASES ({len(rows)})"
    if st & {"VERDICT-DIVERGENCE", "REASON-DIVERGENCE"}:
        print(f"{label}: DIVERGENCE {sorted(st)}"); sys.exit(1)
    if st - {"AGREE"}:
        print(f"{label}: INCONCLUSIVE {sorted(st)}"); sys.exit(2)
    print(f"{label}: ALL AGREE"); sys.exit(0)
