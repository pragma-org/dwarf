"""Conway reference-script fee differential (cardano-node vs Amaru).

Spend a script UTxO via a reference script so the Conway minFeeRefScriptCostPerByte surcharge
applies, at fee = cardano's computed minimum vs minimum-1. The knife-edge is whether Amaru's
computed minimum (base min-fee + ref-script surcharge) equals cardano's to the lovelace. A
one-accepts-one-rejects split is a fee-CONSENSUS divergence.

Thin wrapper over stake_pool_differential.grade (verdict -> reason-class parity, fail-closed);
adds the fee_too_small reason class. Full body kept.
"""
from __future__ import annotations
import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mixed_phase1 import HttpSubmitTransport, observe_differential
import stake_pool_differential as base

REASON_CLASSES = {
    "fee_too_small": (r"feetoosmallutxo", r"declared fee \d+ below minimum|fee too small"),
}
base.REASON_CLASSES = REASON_CLASSES


def run(corpus_dir, amaru_url, cardano_url, control=None):
    root = Path(corpus_dir)
    manifest = json.loads((root / "reffee_corpus.json").read_text())
    t = {"amaru": HttpSubmitTransport(amaru_url, keep_detail=True),
         "cardano": HttpSubmitTransport(cardano_url, keep_detail=True)}
    if control:
        cases = [c for c in manifest["cases"] if c["case_id"] == control and c["expected"] == "accept"]
        if not cases:
            raise SystemExit(f"unknown control: {control}")
    else:
        cases = [c for c in manifest["cases"] if c["expected"] != "accept"]
    return [base.grade(c, observe_differential(bytes.fromhex(json.loads((root / c["tx_file"]).read_text())["cborHex"]), t))
            for c in cases]


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--corpus", default=str(Path(__file__).resolve().parents[1] / "fixture" / "refscript_fee"))
    p.add_argument("--amaru", default="http://localhost:3214/api/submit/tx")
    p.add_argument("--cardano", default="http://localhost:8114/api/submit/tx")
    p.add_argument("--control")
    a = p.parse_args()
    rows = run(a.corpus, a.amaru, a.cardano, a.control)
    print(json.dumps(rows, indent=2))
    st = {r["status"] for r in rows}
    label = "CONTROL " + a.control if a.control else f"VIOLATIONS ({len(rows)})"
    if st & {"VERDICT-DIVERGENCE", "REASON-DIVERGENCE"}:
        print(f"{label}: DIVERGENCE {sorted(st)}"); sys.exit(1)
    if st - {"AGREE"}:
        print(f"{label}: INCONCLUSIVE {sorted(st)}"); sys.exit(2)
    print(f"{label}: ALL AGREE"); sys.exit(0)
