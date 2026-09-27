"""Collateral / redeemer phase-1 differential driver (cardano-node vs Amaru).

Extends mixed_phase1 and reuses stake_pool_differential's grading (verdict parity, then
reason-class parity via class-set intersection, then credential parity; MASKED/unavailable =
INCONCLUSIVE). Only the reason-class table differs. Each case in
fixture/collateral/collateral_corpus.json is designed to violate exactly one collateral,
redeemer, execution-unit or script-integrity rule, or to satisfy all of them (controls).

Replay-safe violations run together. Single-use cases (controls, and the P1 predicted
divergence, which a node that wrongly accepts will admit to its mempool) run one at a time with
--single CASE, each after a mempool reset.
"""
from __future__ import annotations
import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # workload/ on path
from mixed_phase1 import HttpSubmitTransport, observe_differential
import stake_pool_differential as base

# reason class -> (cardano-node marker, Amaru marker), matched case-insensitively
REASON_CLASSES = {
    "no_collateral": (r"nocollateralinputs", r"no collateral was provided"),
    "insufficient_collateral": (r"insufficientcollateral", r"collateral value .* is insufficient"),
    "total_collateral_mismatch": (r"incorrecttotalcollateralfield", r"does not equal effective collateral"),
    "collateral_non_ada": (r"collateralcontainsnonada", r"collateral has non-zero delta"),
    "output_too_small": (r"outputtoosmallutxo", r"doesn't contain enough lovelace"),
    "wrong_network": (r"wrongnetwork", r"has the wrong network"),
    "too_many_collateral": (r"toomanycollateralinputs", r"too many collateral inputs"),
    "missing_witness": (r"missingvkeywitnessesutxow", r"missing required signatures"),
    "exunits_too_big": (r"exunitstoobigutxo", r"execution units exceeded"),
    "integrity_hash": (r"ppviewhashesdontmatch", r"script integrity hash mismatch"),
    "missing_redeemer": (r"missingredeemers|noredeemer", r"missing redeemers"),
    "extra_redeemer": (r"extraredeemers", r"extraneous redeemers"),
}


def run(corpus_dir: str, amaru_url: str, cardano_url: str, single: str | None = None) -> list[dict]:
    root = Path(corpus_dir)
    manifest = json.loads((root / "collateral_corpus.json").read_text())
    transports = {"amaru": HttpSubmitTransport(amaru_url), "cardano": HttpSubmitTransport(cardano_url)}
    if single:
        cases = [c for c in manifest["cases"] if c["case_id"] == single and c["single_use"]]
        if not cases:
            raise SystemExit(f"unknown single-use case: {single}")
    else:
        cases = [c for c in manifest["cases"] if not c["single_use"]]
    rows = []
    for case in cases:
        payload = bytes.fromhex(json.loads((root / case["tx_file"]).read_text())["cborHex"])
        rows.append(base.grade(case, observe_differential(payload, transports), REASON_CLASSES))
    return rows


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--corpus", default=str(Path(__file__).resolve().parents[1] / "fixture" / "collateral"))
    p.add_argument("--amaru", default="http://localhost:3012/api/submit/tx")
    p.add_argument("--cardano", default="http://localhost:8090/api/submit/tx")
    p.add_argument("--single", help="run ONE single-use case (after a mempool reset)")
    a = p.parse_args()
    rows = run(a.corpus, a.amaru, a.cardano, a.single)
    print(json.dumps(rows, indent=2))
    statuses = {r["status"] for r in rows}
    label = "SINGLE " + a.single if a.single else f"VIOLATIONS ({len(rows)} cases)"
    if statuses & {"VERDICT-DIVERGENCE", "REASON-DIVERGENCE"}:
        print(f"{label}: DIVERGENCE {sorted(statuses)}"); sys.exit(1)
    if statuses - {"AGREE"}:
        print(f"{label}: INCONCLUSIVE {sorted(statuses)} (masked/unavailable/reason-truncated)"); sys.exit(2)
    print(f"{label}: ALL AGREE"); sys.exit(0)
