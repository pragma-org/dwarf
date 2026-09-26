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


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--corpus", default=str(Path(__file__).resolve().parents[1] / "fixture" / "native_script"))
    p.add_argument("--amaru", default="http://localhost:3012/api/submit/tx")
    p.add_argument("--cardano", default="http://localhost:8090/api/submit/tx")
    p.add_argument("--controls", action="store_true",
                   help="also submit single-use satisfied controls (needs a fresh mempool; "
                        "an accept consumes the funding UTxO, so run at most one per restart)")
    a = p.parse_args()
    rows = run(a.corpus, a.amaru, a.cardano)
    # The repeatable oracle is the idempotent violation set: verdict parity (both reject)
    # AND reason-class parity (both name the same native script hash). Satisfied controls
    # are single-use (an accept consumes the UTxO) and are proven separately on a fresh mempool.
    viol = [x for x in rows if x["expected"] == "reject"]
    ok = all(x["verdict_parity"] and x["reason_parity"]["hash_match"] for x in viol)
    print(json.dumps(rows, indent=2))
    print(f"VIOLATION VERDICT+REASON PARITY ({len(viol)} cases):", "ALL AGREE" if ok else "DIVERGENCE")
    print("(satisfied controls are single-use: verify one per fresh mempool restart)")
    sys.exit(0 if ok else 1)
