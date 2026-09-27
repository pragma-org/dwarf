"""Reference-script + inline-datum phase-2 differential driver.

Reuses plutus_differential's verdict classifier (202=accept, funding-conflict=masked, else
phase2_reject) and grade (verdict parity primary; reason-class intersection on a shared reject;
fail-closed). Only the reason-class table is widened: besides the phase-2 `tag_mismatch`, spends
can reject at phase-1 for a missing datum (datum-hash output spent without supplying the datum) or
a script mismatch (a reference script whose hash ≠ the spent address's). Amaru phrasings marked
TBD are confirmed against live output on first grade (as done for the phase-2a family).
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import plutus_differential as pd
import urllib.request, urllib.error, socket

# cardano returns a failure SET here (PPViewHashesDontMatch, ExtraRedeemers, MissingScriptWitnesses...);
# the shared transport caps reason at 400 chars, which can hide the intersecting class (e.g.
# MissingScriptWitnesses is 3rd in the set). Use a local submit that keeps the full body so the
# reason-class intersection is accurate.
def _submit(url, payload, timeout=12.0):
    req = urllib.request.Request(url, data=payload, method="POST", headers={"Content-Type": "application/cbor"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"status": r.status, "reason": r.read(8192).decode("utf-8", "replace")}
    except urllib.error.HTTPError as e:
        try: body = e.read(8192).decode("utf-8", "replace")
        except Exception: body = str(e.reason or "http error")
        return {"status": e.code, "reason": body}
    except (urllib.error.URLError, ConnectionError, socket.timeout, TimeoutError, OSError) as e:
        return {"status": None, "reason": type(getattr(e, "reason", e)).__name__}

_P2 = lambda p: re.compile(p, re.I)
pd._MARKERS = {
    "tag_mismatch": (
        _P2(r"validationtagmismatch|failedunexpectedly|passedunexpectedly"),
        _P2(r"expected scripts to (pass|fail) but they (fail|pass)|validation tag mismatch")),
    "missing_datum": (
        _P2(r"missingrequireddatums|notallowedsupplementaldatums|unspendableutxonodatumhash"),
        _P2(r"missing.{0,20}datum|no datum|datum.{0,20}missing|unspendable")),
    "script_mismatch": (
        _P2(r"missingscriptwitnessesutxow|extraneousscriptwitnessesutxow|missingrequiredsignature"),
        _P2(r"missing.{0,20}script|extraneous.{0,20}script|script.{0,20}(mismatch|not found)")),
}


def run(corpus_dir, amaru_url, cardano_url, single=None):
    root = Path(corpus_dir)
    manifest = json.loads((root / "refscript_corpus.json").read_text())
    urls = {"amaru": amaru_url, "cardano": cardano_url}
    cases = ([c for c in manifest["cases"] if c["case_id"] == single and c.get("single_use")] if single
             else [c for c in manifest["cases"] if not c.get("single_use")])
    if single and not cases:
        raise SystemExit(f"unknown single-use case: {single}")
    rows = []
    for c in cases:
        payload = bytes.fromhex(json.loads((root / c["tx_file"]).read_text())["cborHex"])
        rows.append(pd.grade(c, {n: _submit(u, payload) for n, u in urls.items()}))
    return rows


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--corpus", default=str(Path(__file__).resolve().parents[1] / "fixture" / "refscript"))
    p.add_argument("--amaru", default="http://localhost:3211/api/submit/tx")
    p.add_argument("--cardano", default="http://localhost:8111/api/submit/tx")
    p.add_argument("--single")
    a = p.parse_args()
    rows = run(a.corpus, a.amaru, a.cardano, a.single)
    print(json.dumps(rows, indent=2))
    st = {r["status"] for r in rows}
    label = "SINGLE " + a.single if a.single else f"CASES ({len(rows)})"
    if st & {"VERDICT-DIVERGENCE", "REASON-DIVERGENCE"}:
        print(f"{label}: DIVERGENCE {sorted(st)}"); sys.exit(1)
    if st - {"AGREE"}:
        print(f"{label}: INCONCLUSIVE {sorted(st)}"); sys.exit(2)
    print(f"{label}: ALL AGREE"); sys.exit(0)
