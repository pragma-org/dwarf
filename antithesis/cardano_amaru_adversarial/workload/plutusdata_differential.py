"""PlutusData (datum / redeemer) CBOR canonicalization + decode-strictness differential.

Non-canonical / malformed PlutusData spliced into a redeemer or a supplied datum (body untouched,
signatures valid). PlutusData in redeemers/datums is always under the script-integrity hash, so a
node either (a) rejects the encoding at DECODE, or (b) accepts decode and rejects at the
integrity-hash. Oracle: verdict parity (accept / reject), then reason-class parity
(decode vs integrity_hash); fail-closed (masked/unavailable = INCONCLUSIVE). A one-accepts-one-
rejects split, or a datum-hash split, is HIGH.

Boundary: the "pure decoder acceptance" question (would a node accept a non-canonical encoding
WITH a matching integrity hash) needs the integrity-hash recomputed the way each node does it
(the languageViews/integrity-hash lane), so it is not isolated here; within reach, both nodes'
verdicts are compared on the same crafted bytes.
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mixed_phase1 import HttpSubmitTransport

ACCEPTED, MASKED, UNAVAILABLE, DECODE, REJECT = "accepted", "masked", "unavailable", "decode_reject", "reject"
_MEMPOOL = re.compile(r"all inputs are spent|probably already been included", re.I)
_DECODE = re.compile(r"invalid cbor|deserialisefailure|decode error|decodeerror|exceeds the 64-byte", re.I)
REASON_CLASSES = {
    "decode": (re.compile(r"deserialisefailure|decodeerror", re.I),
               re.compile(r"invalid cbor|decode error|exceeds the 64-byte", re.I)),
    "integrity_hash": (re.compile(r"ppviewhashesdontmatch", re.I),
                       re.compile(r"script integrity hash mismatch", re.I)),
    # a non-canonical supplied datum hashes to a value no input requires: cardano reports a set
    # {PPViewHashesDontMatch, NotAllowedSupplementalDatums, MissingRequiredDatums}; amaru reports
    # the single "extraneous supplemental datums" -- same rule + same datum hash (class-set intersection).
    "extraneous_datum": (re.compile(r"notallowedsupplementaldatums|missingrequireddatums", re.I),
                         re.compile(r"extraneous supplemental datums", re.I)),
}


def classify(status, body):
    if status is None:
        return UNAVAILABLE
    if status in (200, 202):
        return ACCEPTED
    if _MEMPOOL.search(body or ""):
        return MASKED
    if _DECODE.search(body or ""):
        return DECODE
    return REJECT


def _classes(reason, node):
    idx = 0 if node == "cardano" else 1
    return {c for c, pats in REASON_CLASSES.items() if pats[idx].search(reason or "")}


def grade(case, obs):
    o = {n: {"status": v.get("status"), "reason": (v.get("reason") or v.get("body") or "")} for n, v in obs.items()}
    cls = {n: classify(v["status"], v["reason"]) for n, v in o.items()}
    row = {"case_id": case["case_id"], "expected": case["expected"], "classes": cls}
    if any(c in (MASKED, UNAVAILABLE) for c in cls.values()) or len(cls) < 2:
        row["status"] = "INCONCLUSIVE"; row["why"] = "masked" if MASKED in cls.values() else "unavailable"
        return row
    accepted = {n: cls[n] == ACCEPTED for n in cls}
    verdict_parity = len(set(accepted.values())) == 1
    want_accept = case["expected"] == "accept"
    row["verdict_parity"] = verdict_parity
    row["matches_expected"] = all((c == ACCEPTED) == want_accept for c in cls.values())
    if not (verdict_parity and row["matches_expected"]):
        row["status"] = "VERDICT-DIVERGENCE"
    elif want_accept:
        row["status"] = "AGREE"
    else:
        got = {n: _classes(o[n]["reason"], n) for n in o}
        row["reason_classes"] = {n: sorted(g) for n, g in got.items()}
        row["status"] = "AGREE" if (all(got.values()) and set.intersection(*got.values())) else "REASON-DIVERGENCE"
    for n in o:
        row[f"{n}_reason"] = str(o[n]["reason"])[:200]
    return row


def run(corpus_dir, amaru_url, cardano_url, control=None):
    root = Path(corpus_dir)
    manifest = json.loads((root / "plutusdata_corpus.json").read_text())
    t = {"amaru": HttpSubmitTransport(amaru_url, keep_detail=True), "cardano": HttpSubmitTransport(cardano_url, keep_detail=True)}
    cases = ([c for c in manifest["cases"] if c["case_id"] == control and c["expected"] == "accept"] if control
             else [c for c in manifest["cases"] if c["expected"] != "accept"])
    if control and not cases:
        raise SystemExit(f"unknown control: {control}")
    out = []
    for c in cases:
        payload = bytes.fromhex(json.loads((root / c["tx_file"]).read_text())["cborHex"])
        out.append(grade(c, {n: tr.send(payload) for n, tr in t.items()}))
    return out


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--corpus", default=str(Path(__file__).resolve().parents[1] / "fixture" / "plutusdata"))
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
