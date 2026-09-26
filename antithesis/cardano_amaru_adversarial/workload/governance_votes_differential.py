"""Conway governance VOTE-authorization phase-1 differential driver (extends mixed_phase1).

The CORE governance-signature angles, reachable only on a governance-PROVISIONED substrate
(govrebake: key-hashed committee, registered DReps, one open InfoAction — all real mined setup
before the freeze; see the scenario doc). Submits committee and DRep vote transactions to both
cardano-node and Amaru and applies a per-case verdict + reason-class + credential parity oracle:

  - verdict parity: both accept, or both reject (primary divergence oracle);
  - reason-class parity (reject): BOTH classify the same rule —
      * missing-vkey-witness  (cardano MissingVKeyWitnessesUTXOW(KeyHash <cred>) ==
                               amaru "verification key witness: missing required signatures [<cred>]"),
      * gov-voter-not-authorized (cardano ConwayGovFailure VotersDoNotExist(<voter cred>) ==
                               amaru "invalid voting procedures: unauthorized or unknown voters {<cred>}").
    Parity is PER-CASE node-vs-node (same case, same class, same credential). Different cases
    legitimately have different classes (an unauthorized-voter reject is NOT the same rule as a
    missing-witness reject) — that is expected, not a divergence.

Violation cases are idempotent (replay-safe); valid controls are single-use (an accept consumes
the funding UTxO — one per fresh mempool). Reuses mixed_phase1 transport + observe_differential.
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mixed_phase1 import HttpSubmitTransport, observe_differential

_C_WIT = re.compile(r"MissingVKeyWitnessesUTXOW.*?unKeyHash = \\?\"([0-9a-f]{56})", re.S)
_C_VOTER = re.compile(r"VotersDoNotExist.*?unKeyHash = \\?\"([0-9a-f]{56})", re.S)
_A_WIT = re.compile(r"missing required signatures for keys or roots: \[([0-9a-f]{56})")
_A_VOTER = re.compile(r"unauthorized or unknown voters:.*?([0-9a-f]{56})", re.S)


def _classify(reason: str, cardano: bool):
    """Return (class, credential) for a reject reason, per node."""
    if cardano:
        m = _C_WIT.search(reason or "")
        if m:
            return "missing-vkey-witness", m.group(1)
        m = _C_VOTER.search(reason or "")
        if m:
            return "gov-voter-not-authorized", m.group(1)
    else:
        m = _A_WIT.search(reason or "")
        if m:
            return "missing-vkey-witness", m.group(1)
        m = _A_VOTER.search(reason or "")
        if m:
            return "gov-voter-not-authorized", m.group(1)
    return None, None


def reason_parity(observations: dict) -> dict:
    cc, ccred = _classify(observations.get("cardano", {}).get("reason", ""), True)
    ac, acred = _classify(observations.get("amaru", {}).get("reason", ""), False)
    return {"cardano_class": cc, "amaru_class": ac, "cardano_cred": ccred, "amaru_cred": acred,
            "class_match": bool(cc and cc == ac), "cred_match": bool(ccred and ccred == acred)}


def run(corpus_dir: str, amaru_url: str, cardano_url: str) -> list[dict]:
    root = Path(corpus_dir)
    manifest = json.loads((root / "governance_votes_corpus.json").read_text())
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
    p.add_argument("--corpus", default=str(Path(__file__).resolve().parents[1] / "fixture" / "governance_votes"))
    p.add_argument("--amaru", default="http://localhost:3013/api/submit/tx")
    p.add_argument("--cardano", default="http://localhost:8091/api/submit/tx")
    a = p.parse_args()
    rows = run(a.corpus, a.amaru, a.cardano)
    viol = [x for x in rows if x["expected"] == "reject"]
    ok = all(x["verdict_parity"] and x["reason_parity"]["class_match"] and x["reason_parity"]["cred_match"] for x in viol)
    print(json.dumps(rows, indent=2))
    print(f"VIOLATION VERDICT+REASON+CRED PARITY ({len(viol)} cases):", "ALL AGREE" if ok else "DIVERGENCE")
    print("(valid controls are single-use: verify one per fresh mempool restart)")
    sys.exit(0 if ok else 1)
