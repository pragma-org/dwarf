"""Conway governance-signature phase-1 differential driver (extends mixed_phase1).

Reachable slice of surface 3: gov-cert REQUIRED-WITNESS validation via DRep-registration
certs (registering a fresh DRep needs that DRep credential's own vkey witness, and
"not registered" is the correct pre-state, so ONLY the UTXOW witness rule applies — no
pre-existing DRep/proposal is needed). Submits each case to both cardano-node and Amaru
and applies a verdict + reason-class (missing-vkey-witness for the SAME credential) parity
oracle:

  - verdict parity: both accept, or both reject (primary divergence oracle);
  - reason-class parity (reject): cardano MissingVKeyWitnessesUTXOW(KeyHash <cred>) and
    Amaru "verification key witness: missing required signatures for keys or roots [<cred>]"
    must name the SAME governance credential.

Committee hot-key auth (script-hash committee) and DRep VOTING (needs a registered DRep +
open gov action in both frozen stores) are NOT reachable on the frozen substrate — see
dwarf/docs/governance-signature-phase1-differential-family.md.

Violation cases are idempotent (replay-safe); satisfied controls are single-use (an accept
consumes the funding UTxO — run at most one per fresh mempool). Reuses
mixed_phase1.HttpSubmitTransport / observe_differential (one transport + classification
path across the phase-1 families).
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # workload/ on path
from mixed_phase1 import HttpSubmitTransport, observe_differential

_CARDANO_CRED_RE = re.compile(r"MissingVKeyWitnessesUTXOW.*?unKeyHash = \\?\"([0-9a-f]{56})", re.S)
_AMARU_CRED_RE = re.compile(r"missing required signatures for keys or roots: \[([0-9a-f]{56})")


def _cred(reason: str, is_cardano: bool) -> str | None:
    m = (_CARDANO_CRED_RE if is_cardano else _AMARU_CRED_RE).search(reason or "")
    return m.group(1) if m else None


def reason_parity(observations: dict) -> dict:
    c = observations.get("cardano", {}); a = observations.get("amaru", {})
    cc = _cred(c.get("reason", ""), True); ac = _cred(a.get("reason", ""), False)
    return {"cardano_cred": cc, "amaru_cred": ac, "cred_match": bool(cc and ac and cc == ac)}


def run(corpus_dir: str, amaru_url: str, cardano_url: str) -> list[dict]:
    root = Path(corpus_dir)
    manifest = json.loads((root / "governance_corpus.json").read_text())
    transports = {"amaru": HttpSubmitTransport(amaru_url), "cardano": HttpSubmitTransport(cardano_url)}
    results = []
    for case in manifest["cases"]:
        payload = bytes.fromhex(json.loads((root / case["tx_file"]).read_text())["cborHex"])
        r = observe_differential(payload, transports)
        cls = {k: v["classification"] for k, v in r["observations"].items()}
        row = {"case_id": case["case_id"], "expected": case["expected"], "classes": cls,
               "verdict_parity": r["both_classifiable"] and len(set(cls.values())) == 1}
        if case["expected"] == "reject":
            row["reason_parity"] = reason_parity(r["observations"])
        results.append(row)
    return results


# ---- shared-grade path (verdict -> reason class -> parity token; fail-closed) ----
import stake_pool_differential as base  # noqa: E402

REASON_CLASSES = {"missing_witness": (r"missingvkeywitnessesutxow", r"missing required signatures")}


def grade_case(case: dict, result: dict) -> dict:
    """Grade with the shared oracle; the corpus predates it, so normalise its schema."""
    reject = case["expected"] == "reject"
    norm = {"case_id": case["case_id"], "expected": "reject" if reject else "accept",
             "reason_classes": ["missing_witness"] if reject else []}
    token = (case.get("observed") or {}).get("credential")
    if reject and token:
        norm["credential"] = token
    return base.grade(norm, result, REASON_CLASSES)


def run_graded(corpus_dir: str, amaru_url: str, cardano_url: str, control: str | None = None) -> list[dict]:
    root = Path(corpus_dir)
    cases = json.loads((root / "governance_corpus.json").read_text())["cases"]
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
    p.add_argument("--corpus", default=str(Path(__file__).resolve().parents[1] / "fixture" / "governance"))
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
