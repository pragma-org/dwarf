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


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--corpus", default=str(Path(__file__).resolve().parents[1] / "fixture" / "governance"))
    p.add_argument("--amaru", default="http://localhost:3012/api/submit/tx")
    p.add_argument("--cardano", default="http://localhost:8090/api/submit/tx")
    a = p.parse_args()
    rows = run(a.corpus, a.amaru, a.cardano)
    viol = [x for x in rows if x["expected"] == "reject"]
    ok = all(x["verdict_parity"] and x["reason_parity"]["cred_match"] for x in viol)
    print(json.dumps(rows, indent=2))
    print(f"VIOLATION VERDICT+REASON PARITY ({len(viol)} cases):", "ALL AGREE" if ok else "DIVERGENCE")
    print("(satisfied controls are single-use: verify one per fresh mempool restart)")
    sys.exit(0 if ok else 1)
