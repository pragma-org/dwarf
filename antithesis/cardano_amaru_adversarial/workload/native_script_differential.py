"""Native-script phase-1 differential driver (extends mixed_phase1).

Submits each native-script corpus case (minting-policy signature rules, RequireMOf
thresholds, nested scripts, and timelock before/after boundaries) to both cardano-node
and Amaru, and applies a verdict + reason-class parity oracle:

  - verdict parity: both accept, or both reject (the primary divergence oracle);
  - reason-class parity (reject cases): cardano ScriptWitnessNotValidatingUTXOW(<hash>)
    and Amaru "native script(s) failed to validate [<hash>]" must name the SAME script hash.

Violation cases are idempotent (rejected, replay-safe). Satisfied/boundary cases are
single-use (an accept consumes the funding UTxO): submit them at most once per fresh
mempool (restart the frozen reference + Amaru between accept-runs).

Reuses mixed_phase1.HttpSubmitTransport / observe_differential so there is one transport
+ classification path across the phase-1 families (extend, do not reinvent).
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # workload/ on path
from mixed_phase1 import HttpSubmitTransport, observe_differential, ACCEPTED, PHASE1_REJECT

_CARDANO_SCRIPT_RE = re.compile(r"ScriptWitnessNotValidatingUTXOW.*?ScriptHash[^0-9a-f]*([0-9a-f]{56})", re.S)
_AMARU_SCRIPT_RE = re.compile(r"native script\(s\) failed to validate:?\s*\[?([0-9a-f]{56})")


def _script_hash(reason: str, is_cardano: bool) -> str | None:
    m = (_CARDANO_SCRIPT_RE if is_cardano else _AMARU_SCRIPT_RE).search(reason or "")
    return m.group(1) if m else None


def reason_parity(observations: dict) -> dict:
    """For a reject, confirm both name the SAME native script hash."""
    c = observations.get("cardano", {}); a = observations.get("amaru", {})
    ch = _script_hash(c.get("reason", ""), True); ah = _script_hash(a.get("reason", ""), False)
    return {"cardano_hash": ch, "amaru_hash": ah, "hash_match": bool(ch and ah and ch == ah)}


def run(corpus_dir: str, amaru_url: str, cardano_url: str) -> list[dict]:
    root = Path(corpus_dir)
    manifest = json.loads((root / "native_script_corpus.json").read_text())
    transports = {"amaru": HttpSubmitTransport(amaru_url), "cardano": HttpSubmitTransport(cardano_url)}
    results = []
    for case in manifest["cases"]:
        payload = bytes.fromhex(json.loads((root / case["tx_file"]).read_text())["cborHex"])
        r = observe_differential(payload, transports)
        cls = {k: v["classification"] for k, v in r["observations"].items()}
        verdict_parity = r["both_classifiable"] and len(set(cls.values())) == 1
        row = {"case_id": case["case_id"], "expected": case["expected"],
               "classes": cls, "verdict_parity": verdict_parity}
        if case["expected"] == "reject":
            row["reason_parity"] = reason_parity(r["observations"])
        results.append(row)
    return results


# ---- shared-grade path (verdict -> reason class -> parity token; fail-closed) ----
import stake_pool_differential as base  # noqa: E402

REASON_CLASSES = {"native_script_fail": (r"scriptwitnessnotvalidatingutxow", r"native script\(s\) failed to validate")}


def grade_case(case: dict, result: dict) -> dict:
    """Grade with the shared oracle; the corpus predates it, so normalise its schema."""
    reject = case["expected"] == "reject"
    norm = {"case_id": case["case_id"], "expected": "reject" if reject else "accept",
             "reason_classes": ["native_script_fail"] if reject else []}
    token = (case.get("observed") or {}).get("script_hash")
    if reject and token:
        norm["credential"] = token
    return base.grade(norm, result, REASON_CLASSES)


def run_graded(corpus_dir: str, amaru_url: str, cardano_url: str, control: str | None = None) -> list[dict]:
    root = Path(corpus_dir)
    cases = json.loads((root / "native_script_corpus.json").read_text())["cases"]
    transports = {"amaru": HttpSubmitTransport(amaru_url, keep_detail=True),
                   "cardano": HttpSubmitTransport(cardano_url, keep_detail=True)}
    if control:
        cases = [c for c in cases if c["case_id"] == control and c["expected"] != "reject"]
        if not cases:
            raise SystemExit(f"unknown control case: {control}")
    else:
        cases = [c for c in cases if c["expected"] == "reject"]
    return [grade_case(c, observe_differential(
        bytes.fromhex(json.loads((root / c["tx_file"]).read_text())["cborHex"]), transports)) for c in cases]


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--corpus", default=str(Path(__file__).resolve().parents[1] / "fixture" / "native_script"))
    p.add_argument("--amaru", default="http://localhost:3012/api/submit/tx")
    p.add_argument("--cardano", default="http://localhost:8090/api/submit/tx")
    p.add_argument("--control", help="run ONE single-use accept case (after a mempool reset)")
    a = p.parse_args()
    rows = run_graded(a.corpus, a.amaru, a.cardano, a.control)
    print(json.dumps(rows, indent=2))
    statuses = {r["status"] for r in rows}
    label = "CONTROL " + a.control if a.control else f"VIOLATIONS ({len(rows)} cases)"
    if statuses & {"VERDICT-DIVERGENCE", "REASON-DIVERGENCE"}:
        print(f"{label}: DIVERGENCE {sorted(statuses)}"); sys.exit(1)
    if statuses - {"AGREE"}:
        print(f"{label}: INCONCLUSIVE {sorted(statuses)}"); sys.exit(2)
    print(f"{label}: ALL AGREE"); sys.exit(0)
