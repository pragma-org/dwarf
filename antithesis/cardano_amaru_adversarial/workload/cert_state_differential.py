"""Certificate & deposit state-transition edge differential (cardano-node vs Amaru).

Extends the stake/pool-withdrawal family (does NOT duplicate its reg/dereg/pool/withdrawal cases).
Targets state-machine + deposit-accounting edges reachable on the frozen substrate with fresh
credentials (intra-tx cert sequences): deregister an unregistered cred, register-then-deregister
in one tx, a dereg with the WRONG refund amount, and vote-delegation to a nonexistent DRep.

Thin wrapper over stake_pool_differential.grade (verdict -> reason-class -> credential parity,
fail-closed); only the reason-class table is added. Full response body kept (cardano returns a
failure set for the deposit case).
"""
from __future__ import annotations
import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mixed_phase1 import HttpSubmitTransport, observe_differential
import stake_pool_differential as base

REASON_CLASSES = {
    "stake_not_registered": (r"stakekeynotregistereddeleg", r"stake credential not registered"),
    "incorrect_deposit": (r"incorrectdepositdeleg", r"incorrect stake deposit"),
    "drep_not_registered": (r"delegateedrepnotregistereddeleg", r"vote delegation: unknown target entity"),
}
base.REASON_CLASSES = REASON_CLASSES


def run(corpus_dir, amaru_url, cardano_url, control=None):
    root = Path(corpus_dir)
    manifest = json.loads((root / "cert_state_corpus.json").read_text())
    t = {"amaru": HttpSubmitTransport(amaru_url, keep_detail=True),
         "cardano": HttpSubmitTransport(cardano_url, keep_detail=True)}
    if control:
        cases = [c for c in manifest["cases"] if c["case_id"] == control and c["expected"] == "accept"]
        if not cases:
            raise SystemExit(f"unknown control case: {control}")
    else:
        cases = [c for c in manifest["cases"] if c["expected"] != "accept"]
    return [base.grade(c, observe_differential(bytes.fromhex(json.loads((root / c["tx_file"]).read_text())["cborHex"]), t))
            for c in cases]


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--corpus", default=str(Path(__file__).resolve().parents[1] / "fixture" / "cert_state"))
    p.add_argument("--amaru", default="http://localhost:3213/api/submit/tx")
    p.add_argument("--cardano", default="http://localhost:8113/api/submit/tx")
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
