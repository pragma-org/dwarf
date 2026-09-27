"""Transaction-metadata / auxiliary-data-hash phase-1 differential driver (cardano-node vs Amaru).

Extends mixed_phase1 and reuses stake_pool_differential's grading (verdict parity, then
reason-class parity, then parity-token parity; MASKED/unavailable = INCONCLUSIVE; a decode edge
one node decodes further is flagged decode_leniency). Only the reason-class table differs.

The headline pair: aux data sent in a NON-canonical encoding, with the body hash over the sent
bytes (must be accepted) or over the canonical re-encoding (must be rejected with
ConflictingMetadataHash). For the hash rules the parity token is blake2b of the SENT aux bytes, so
a node that hashed its own re-encoding fails parity even when its verdict agrees.

Violations are idempotent. Controls are single-use: run one at a time with --control CASE, after a
mempool reset.
"""
from __future__ import annotations
import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # workload/ on path
from mixed_phase1 import HttpSubmitTransport, observe_differential
import stake_pool_differential as base

# reason class -> (cardano-node UTXOW marker, Amaru marker)
REASON_CLASSES = {
    "conflicting_hash": (r"conflictingmetadatahash", r"metadata hash mismatch"),
    "missing_hash": (r"missingtxbodymetadatahash", r"missing auxiliary data hash"),
    "missing_aux": (r"missingtxmetadata\b", r"missing metadata"),
}


def grade(case: dict, result: dict) -> dict:
    return base.grade(case, result, REASON_CLASSES)


def run(corpus_dir: str, amaru_url: str, cardano_url: str, control: str | None = None) -> list[dict]:
    root = Path(corpus_dir)
    manifest = json.loads((root / "metadata_corpus.json").read_text())
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
    p.add_argument("--corpus", default=str(Path(__file__).resolve().parents[1] / "fixture" / "metadata"))
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
