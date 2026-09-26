"""Conway stake/pool certificate + reward-withdrawal phase-1 differential driver.

Extends mixed_phase1: one transport + classification path across the phase-1 families.
Each case is designed to violate exactly one rule (or satisfy all of them, for controls):

  - stake registration / reg+vote-deleg / reg+stake-deleg: stake-credential witness, deposit,
    target-pool existence;
  - pool registration: cold-key and owner witnesses, minPoolCost, reward-account network;
  - reward withdrawals: unregistered account, and a registered genesis account with no key and
    no DRep delegation (missing-witness and the pv10 DRep-delegation rule co-occur there, so the
    reported reason is a PRECEDENCE observation, graded by class-set intersection);
  - controls, including the legacy tag-0 stake registration with NO stake witness (the ledger
    requires none: an over-strictness probe).

Oracle per case: verdict parity (primary) and, for rejections, reason-class parity (both nodes
cite the targeted rule) plus credential parity (both name the expected credential). A case where
either node is MASKED (funding input already consumed in its mempool) or unavailable is
INCONCLUSIVE, never a pass and never a divergence.

Violations are idempotent (replay-safe). Controls are single-use (an accept consumes the funding
UTxO in that node's mempool): run them one at a time with --control, after a mempool restart.
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # workload/ on path
from mixed_phase1 import HttpSubmitTransport, observe_differential

# reason class -> (cardano-node marker, Amaru marker), matched case-insensitively
REASON_CLASSES = {
    "missing_witness": (r"missingvkeywitnessesutxow", r"missing required signatures"),
    "incorrect_deposit": (r"incorrectdepositdeleg", r"incorrect stake deposit"),
    "unknown_pool": (r"delegateestakepoolnotregistereddeleg", r"unknown target entity"),
    "pool_cost_too_low": (r"stakepoolcosttoolowpool", r"pool cost too low"),
    "wrong_network": (r"wrongnetworkpool", r"reward account has wrong network"),
    "wdrl_not_registered": (r"withdrawalsnotinrewards", r"that is not registered"),
    "wdrl_not_drep_delegated": (r"wdrlnotdelegatedtodrep", r"has no drep delegation"),
}
_TRUNCATED_AT = 400  # mixed_phase1._observation keeps the first 400 chars of a response


def reason_classes(reason: str, node: str) -> set[str]:
    idx = 0 if node == "cardano" else 1
    low = (reason or "").lower()
    return {c for c, pats in REASON_CLASSES.items() if re.search(pats[idx], low)}


def grade(case: dict, result: dict) -> dict:
    obs = result["observations"]
    cls = {k: v["classification"] for k, v in obs.items()}
    row = {"case_id": case["case_id"], "expected": case["expected"], "classes": cls}
    if not result["both_classifiable"]:
        row["status"] = "INCONCLUSIVE"
        row["why"] = "masked" if result.get("masked") else "unclassifiable/unavailable"
        return row
    verdict_parity = len(set(cls.values())) == 1
    row["verdict_parity"] = verdict_parity
    want = "accepted" if case["expected"] == "accept" else "phase1_reject"
    row["matches_expected"] = all(v == want for v in cls.values())
    if case["expected"] == "reject" and verdict_parity:
        target = set(case["reason_classes"])
        got = {n: reason_classes(o.get("reason", ""), n) for n, o in obs.items()}
        row["reason_classes"] = {n: sorted(g) for n, g in got.items()}
        row["reason_parity"] = all(g & target for g in got.values()) and bool(
            set.intersection(*got.values()))
        cred = case.get("credential")
        if cred:
            row["cred_seen"] = {n: cred in (o.get("reason") or "") for n, o in obs.items()}
            row["cred_parity"] = all(row["cred_seen"].values())
            row["reason_truncated"] = [n for n, o in obs.items()
                                       if len(o.get("reason") or "") >= _TRUNCATED_AT]
    if not (verdict_parity and row["matches_expected"]):
        row["status"] = "VERDICT-DIVERGENCE"
    elif row.get("reason_parity", True) and row.get("cred_parity", True):
        row["status"] = "AGREE"
    elif row.get("reason_truncated"):
        row["status"] = "REASON-UNVERIFIED"  # verdicts agree; a node's reason was cut at 400 chars
    else:
        row["status"] = "REASON-DIVERGENCE"  # same verdict, different rule/credential reported
    for n, o in obs.items():
        row[f"{n}_reason"] = str(o.get("reason", ""))[:240]
    return row


def run(corpus_dir: str, amaru_url: str, cardano_url: str, control: str | None = None) -> list[dict]:
    root = Path(corpus_dir)
    manifest = json.loads((root / "stake_pool_corpus.json").read_text())
    transports = {"amaru": HttpSubmitTransport(amaru_url), "cardano": HttpSubmitTransport(cardano_url)}
    if control:
        cases = [c for c in manifest["cases"] if c["case_id"] == control and c["expected"] == "accept"]
        if not cases:
            raise SystemExit(f"unknown control case: {control}")
    else:
        cases = [c for c in manifest["cases"] if c["expected"] == "reject"]
    rows = []
    for case in cases:
        payload = bytes.fromhex(json.loads((root / case["tx_file"]).read_text())["cborHex"])
        rows.append(grade(case, observe_differential(payload, transports)))
    return rows


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--corpus", default=str(Path(__file__).resolve().parents[1] / "fixture" / "stake_pool"))
    p.add_argument("--amaru", default="http://localhost:3012/api/submit/tx")
    p.add_argument("--cardano", default="http://localhost:8090/api/submit/tx")
    p.add_argument("--control", help="run ONE single-use control case (after a mempool restart)")
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
