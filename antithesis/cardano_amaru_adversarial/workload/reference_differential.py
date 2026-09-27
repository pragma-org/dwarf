"""Reference-input resolution phase-1 differential (cardano-node vs Amaru).

Reachable slice of the reference-inputs / datum / reference-script surface on a FROZEN chain:
reference-INPUT resolution (the referenced UTxOs already exist in the ledger set). The
reference-SCRIPT-spend and inline-datum-spend cases need script-locked UTxOs pre-mined before the
freeze (a substrate ask), so they are not here.

Own verdict classifier (does NOT reuse mixed_phase1's MASKED-on-BadInputsUTxO rule): a bad
*reference* input legitimately produces cardano `BadInputsUTxO`, which is the real verdict here,
not funding-mempool contention. So this driver masks ONLY on the mempool-conflict phrasing ("all
inputs are spent" / "probably already been included"); every other non-2xx is a real reject. The
pair is reset before each run, so the funding UTxO is present and a reject is attributable to the
reference input.

Oracle: verdict parity (accept/accept or reject/reject), then, for a shared reject, reason-class
parity (same rule) AND input parity (both name the same offending input). Fail-closed:
masked/unavailable = INCONCLUSIVE.
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mixed_phase1 import HttpSubmitTransport

ACCEPTED, MASKED, UNAVAILABLE, REJECT = "accepted", "masked", "unavailable", "reject"
_MEMPOOL_CONFLICT = re.compile(r"all inputs are spent|probably already been included", re.I)
_INPUT_RE = re.compile(r"([0-9a-f]{64})#?(\d+)?|unTxId = SafeHash \"([0-9a-f]{64})\"", re.I)

# reason class -> (cardano marker, amaru marker)
REASON_CLASSES = {
    "ref_unknown": (re.compile(r"badinputsutxo", re.I),
                    re.compile(r"unknown \(but required\) transaction input or reference input", re.I)),
    "ref_nondisjoint": (re.compile(r"nondisjointrefinputs", re.I),
                        re.compile(r"included in both reference inputs and spent inputs", re.I)),
}


def classify(status, body):
    if status is None:
        return UNAVAILABLE
    if status in (200, 202):
        return ACCEPTED
    if _MEMPOOL_CONFLICT.search(body or ""):
        return MASKED
    return REJECT


def _inputs(reason):
    return {m.group(1) or m.group(3) for m in _INPUT_RE.finditer(reason or "") if (m.group(1) or m.group(3))}


def _classes(reason, node):
    idx = 0 if node == "cardano" else 1
    return {c for c, pats in REASON_CLASSES.items() if pats[idx].search(reason or "")}


def grade(case, obs):
    o = {n: {"status": v.get("status"), "reason": (v.get("reason") or v.get("body") or "")} for n, v in obs.items()}
    cls = {n: classify(v["status"], v["reason"]) for n, v in o.items()}
    row = {"case_id": case["case_id"], "expected": case["expected"], "classes": cls}
    if any(c in (MASKED, UNAVAILABLE) for c in cls.values()) or len(cls) < 2:
        row["status"] = "INCONCLUSIVE"
        row["why"] = "masked" if MASKED in cls.values() else "unavailable"
        return row
    verdict_parity = len(set(cls.values())) == 1
    want = ACCEPTED if case["expected"] == "accept" else REJECT
    row["verdict_parity"] = verdict_parity
    row["matches_expected"] = all(c == want for c in cls.values())
    if not (verdict_parity and row["matches_expected"]):
        row["status"] = "VERDICT-DIVERGENCE"
    elif want == REJECT:
        got = {n: _classes(o[n]["reason"], n) for n in o}
        row["reason_classes"] = {n: sorted(g) for n, g in got.items()}
        shared = set.intersection(*got.values()) if all(got.values()) else set()
        ins = {n: _inputs(o[n]["reason"]) for n in o}
        row["input_parity"] = bool(set.intersection(*ins.values())) if all(ins.values()) else False
        row["status"] = "AGREE" if (shared and row["input_parity"]) else "REASON-DIVERGENCE"
    else:
        row["status"] = "AGREE"
    for n in o:
        row[f"{n}_reason"] = str(o[n]["reason"])[:200]
    return row


def run(corpus_dir, amaru_url, cardano_url, single=None):
    root = Path(corpus_dir)
    manifest = json.loads((root / "reference_corpus.json").read_text())
    t = {"amaru": HttpSubmitTransport(amaru_url), "cardano": HttpSubmitTransport(cardano_url)}
    cases = ([c for c in manifest["cases"] if c["case_id"] == single and c.get("single_use")] if single
             else [c for c in manifest["cases"] if not c.get("single_use")])
    if single and not cases:
        raise SystemExit(f"unknown single-use case: {single}")
    return [grade(c, {n: tr.send(bytes.fromhex(json.loads((root / c["tx_file"]).read_text())["cborHex"]))
                      for n, tr in t.items()}) for c in cases]


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--corpus", default=str(Path(__file__).resolve().parents[1] / "fixture" / "reference_inputs"))
    p.add_argument("--amaru", default="http://localhost:3211/api/submit/tx")
    p.add_argument("--cardano", default="http://localhost:8111/api/submit/tx")
    p.add_argument("--single")
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
