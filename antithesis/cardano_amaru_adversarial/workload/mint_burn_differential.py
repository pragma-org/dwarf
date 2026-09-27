"""Mint/burn + multi-asset value phase-1 differential driver (cardano-node vs Amaru).

Extends mixed_phase1 and reuses stake_pool_differential's grading (verdict parity, then
reason-class parity via class-set intersection, then parity-token parity; MASKED/unavailable =
INCONCLUSIVE). Only the reason-class table differs. Each case in
fixture/mint_burn/mint_burn_corpus.json is designed to violate exactly one mint / multi-asset rule,
or to satisfy all of them (controls):

  - minting policy not satisfied: policy script missing / wrong / extraneous (the policy SIG case
    is in the native-script family);
  - burn and quantity edges: burn of an asset no input holds, -2^63 burn;
  - decode edges, expected DECODE_REJECT on both: zero mint quantity, empty mint / inner asset
    map, 33-byte asset name, zero-quantity output token. Each tx is otherwise VALID, so a lenient
    decoder surfaces as an ACCEPT (verdict divergence); a node that decodes further and then
    phase-1-rejects is flagged "decode_leniency";
  - multi-asset value not preserved with ADA exactly balanced: surplus, deficit, relabel,
    unminted policy in an output;
  - multi-asset min-UTxO boundary and maxValueSize.

The parity token is the policy id (script/value rules) or the amount the rule is about. The
full response is kept (keep_detail) because cardano's ValueNotConserved Mismatch puts the policy
id past the 400-char reason cut. A valid BURN control is infeasible on the frozen substrate.

Violations are idempotent. Controls are single-use: run one at a time with --control CASE, after a
mempool reset.
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # workload/ on path
from mixed_phase1 import HttpSubmitTransport, observe_differential, DECODE_REJECT, PHASE1_REJECT
import stake_pool_differential as base

# reason class -> (cardano-node marker, Amaru marker), matched case-insensitively
REASON_CLASSES = {
    "missing_script": (r"missingscriptwitnessesutxow", r"missing required scripts"),
    "extraneous_script": (r"extraneousscriptwitnessesutxow", r"extraneous script witnesses"),
    "value_not_conserved": (r"valuenotconservedutxo", r"value not preserved"),
    "output_too_small": (r"outputtoosmallutxo", r"doesn't contain enough lovelace"),
    "output_too_big": (r"outputtoobigutxo", r"output value is too large"),
}

# value size each node reports for OutputTooBig (cardano: first int of the (actual, max, out)
# triple; Amaru: "actual: N"); a mismatch is a value-serialisation divergence
_SIZE_RE = {"cardano": re.compile(r"outputtoobigutxo[^0-9]*(\d+)", re.I),
            "amaru": re.compile(r"actual:\s*(\d+)", re.I)}


def grade(case: dict, result: dict) -> dict:
    row = base.grade(case, result, REASON_CLASSES)
    obs = result["observations"]
    cls = {n: o["classification"] for n, o in obs.items()}
    if case["expected"] == "decode_reject" and set(cls.values()) == {DECODE_REJECT, PHASE1_REJECT}:
        row["decode_leniency"] = sorted(n for n, c in cls.items() if c == PHASE1_REJECT)
    if "output_too_big" in case["reason_classes"]:
        sizes = {n: (m.group(1) if (m := _SIZE_RE[n].search(base._text(o))) else None)
                 for n, o in obs.items()}
        row["value_size"] = sizes
        if all(sizes.values()) and len(set(sizes.values())) > 1 and row["status"] == "AGREE":
            row["status"] = "REASON-DIVERGENCE"
    return row


def run(corpus_dir: str, amaru_url: str, cardano_url: str, control: str | None = None) -> list[dict]:
    root = Path(corpus_dir)
    manifest = json.loads((root / "mint_burn_corpus.json").read_text())
    transports = {"amaru": HttpSubmitTransport(amaru_url, keep_detail=True),
                  "cardano": HttpSubmitTransport(cardano_url, keep_detail=True)}
    if control:
        cases = [c for c in manifest["cases"] if c["case_id"] == control and c["expected"] == "accept"]
        if not cases:
            raise SystemExit(f"unknown control case: {control}")
    else:
        cases = [c for c in manifest["cases"] if c["expected"] != "accept"]
    rows = []
    for case in cases:
        payload = bytes.fromhex(json.loads((root / case["tx_file"]).read_text())["cborHex"])
        rows.append(grade(case, observe_differential(payload, transports)))
    return rows


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--corpus", default=str(Path(__file__).resolve().parents[1] / "fixture" / "mint_burn"))
    p.add_argument("--amaru", default="http://localhost:3012/api/submit/tx")
    p.add_argument("--cardano", default="http://localhost:8090/api/submit/tx")
    p.add_argument("--control", help="run ONE single-use control case (after a mempool reset)")
    a = p.parse_args()
    rows = run(a.corpus, a.amaru, a.cardano, a.control)
    print(json.dumps(rows, indent=2))
    statuses = {r["status"] for r in rows}
    label = "CONTROL " + a.control if a.control else f"VIOLATIONS ({len(rows)} cases)"
    if statuses & {"VERDICT-DIVERGENCE", "REASON-DIVERGENCE"}:
        print(f"{label}: DIVERGENCE {sorted(statuses)}"); sys.exit(1)
    if statuses - {"AGREE"}:
        print(f"{label}: INCONCLUSIVE {sorted(statuses)} (masked/unavailable/reason-truncated)"); sys.exit(2)
    print(f"{label}: ALL AGREE"); sys.exit(0)
