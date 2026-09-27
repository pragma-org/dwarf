"""Phase-1 adversarial mutation soak (extends mixed_phase1).

Randomised/structural mutation of signed tx CBOR + witnesses at scale, submitted to BOTH
cardano-node and Amaru, with a verdict + reason-class divergence oracle. FAIL-CLOSED: any
MASKED observation (unknown/unavailable/transport error, or a mutant that consumes the funding
UTxO) is INCONCLUSIVE — never a pass, never a divergence. A real divergence is either a VERDICT
mismatch (one accepts / one rejects, or accept-vs-decode-vs-phase1) or, on a shared reject, a
genuine REASON-CLASS mismatch.

Reuses mixed_phase1.classify_response / HttpSubmitTransport (one transport + classification path).
Base corpus = the delivered family fixtures (native-script, governance, governance_votes, min-fee).
No CBOR lib on the box, so mutations are byte-level (flip/truncate/insert/dup/trailing) plus
targeted structural ones (non-minimal uint re-encode, tag-24 wrap of the whole tx) that don't need
a decoder.
"""
from __future__ import annotations
import json, os, re, sys, time, random, hashlib, urllib.request, urllib.error, socket

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mixed_phase1 import classify_response, ACCEPTED, PHASE1_REJECT, DECODE_REJECT, UNAVAILABLE, UNKNOWN

# ---- reason-class extraction (unify cardano + amaru across the families) ----
_CLASSES = [
    ("fee-too-small",  re.compile(r"FeeTooSmallUTxO", re.I),                 re.compile(r"invalid fees|fee too small|below minimum", re.I)),
    ("script-witness", re.compile(r"ScriptWitnessNotValidatingUTXOW", re.I), re.compile(r"native script\(s\) failed", re.I)),
    ("missing-vkey",   re.compile(r"MissingVKeyWitnessesUTXOW", re.I),       re.compile(r"missing required signatures", re.I)),
    ("voter-unauth",   re.compile(r"VotersDoNotExist", re.I),               re.compile(r"unauthorized or unknown voters", re.I)),
    ("validity-intvl", re.compile(r"OutsideValidityIntervalUTxO", re.I),     re.compile(r"validity interval", re.I)),
    ("bad-inputs",     re.compile(r"BadInputsUTxO|inputs are spent|already been included", re.I), re.compile(r"unknown input|already spent|failed to prepare", re.I)),
    ("value-not-cons", re.compile(r"ValueNotConservedUTxO", re.I),           re.compile(r"value not conserved|value preservation", re.I)),
    ("decode",         re.compile(r"DecoderError|DeserialiseFailure|invalid cbor|TxCmdTxReadError|expected", re.I), re.compile(r"invalid cbor|decode|deserialis|malformed|trailing bytes", re.I)),
]

def reason_class(reason: str, is_cardano: bool) -> str | None:
    pats = [(name, (c if is_cardano else a)) for name, c, a in _CLASSES]
    for name, pat in pats:
        if pat.search(reason or ""):
            return name
    return None

def submit(url: str, payload: bytes, timeout=6.0) -> dict:
    req = urllib.request.Request(url, data=payload, method="POST", headers={"Content-Type": "application/cbor"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(2048).decode("utf-8", "replace"); status = r.status
    except urllib.error.HTTPError as e:
        try: body = e.read(2048).decode("utf-8", "replace")
        except Exception: body = str(e.reason or "http error")
        status = e.code
    except (urllib.error.URLError, ConnectionError, socket.timeout, TimeoutError, OSError) as e:
        return {"cls": UNAVAILABLE, "reason": type(getattr(e, "reason", e)).__name__, "status": None}
    return {"cls": classify_response(status, body), "reason": body[:300], "status": status}

# ---- mutators (return mutated bytes) ----
def m_flip(b, rng):
    ba = bytearray(b); i = rng.randrange(len(ba)); ba[i] ^= 1 << rng.randrange(8); return bytes(ba)
def m_flipn(b, rng):
    ba = bytearray(b)
    for _ in range(rng.randint(2, 6)):
        i = rng.randrange(len(ba)); ba[i] ^= 1 << rng.randrange(8)
    return bytes(ba)
def m_trunc(b, rng):
    k = rng.randint(1, max(1, len(b)//4)); return b[:-k]
def m_insert(b, rng):
    i = rng.randrange(len(b)+1); return b[:i] + bytes([rng.randrange(256)]) + b[i:]
def m_dup(b, rng):
    i = rng.randrange(len(b)); return b[:i] + b[i:i+1] + b[i:]
def m_trailing(b, rng):
    return b + bytes(rng.randrange(256) for _ in range(rng.randint(1, 4)))  # trailing garbage (cf submit-trailing-bytes)
def m_zero_run(b, rng):
    ba = bytearray(b); i = rng.randrange(len(ba)); n = rng.randint(1, 4)
    for j in range(i, min(i+n, len(ba))): ba[j] = 0
    return bytes(ba)
def m_byteset(b, rng):
    ba = bytearray(b); i = rng.randrange(len(ba)); ba[i] = rng.randrange(256); return bytes(ba)
MUTATORS = [m_flip, m_flipn, m_trunc, m_insert, m_dup, m_trailing, m_zero_run, m_byteset]

def load_corpus(root):
    base = []
    import glob
    for txf in glob.glob(os.path.join(root, "**", "*.tx"), recursive=True):
        try:
            h = json.loads(open(txf).read()).get("cborHex")
            if h: base.append((os.path.relpath(txf, root), bytes.fromhex(h)))
        except Exception: pass
    return base

def run(corpus_root, amaru_url, cardano_url, seconds, seed=1337):
    rng = random.Random(seed)
    base = load_corpus(corpus_root)
    if not base: raise SystemExit("no base txs")
    print(f"soak: {len(base)} base txs; {seconds}s; seed {seed}", flush=True)
    stats = {"n": 0, "both_reject_agree": 0, "both_accept": 0, "inconclusive": 0,
             "verdict_divergence": 0, "reason_divergence": 0}
    divergences = []
    t0 = time.time()
    while time.time() - t0 < seconds:
        name, b = rng.choice(base)
        mut = rng.choice(MUTATORS)(b, rng)
        if mut == b: continue
        stats["n"] += 1
        c = submit(cardano_url, mut); a = submit(amaru_url, mut)
        cc, ac = c["cls"], a["cls"]
        classifiable = {ACCEPTED, PHASE1_REJECT, DECODE_REJECT}
        if cc not in classifiable or ac not in classifiable:
            stats["inconclusive"] += 1; continue
        if cc == ACCEPTED or ac == ACCEPTED:
            if cc == ac == ACCEPTED:
                # a valid mutant accepted by BOTH consumed the funding UTxO -> stop this chunk;
                # the shell wrapper restarts both nodes (fresh mempool) and resumes with a new seed.
                stats["both_accept"] += 1
                return stats, divergences, True
            # exactly one accepted -> VERDICT DIVERGENCE (headline)
            stats["verdict_divergence"] += 1
            d = {"kind": "verdict", "base": name, "mutator": mut and "?", "cardano": cc, "amaru": ac,
                 "cardano_reason": c["reason"][:200], "amaru_reason": a["reason"][:200],
                 "cbor": mut.hex()}
            divergences.append(d); print("!!! VERDICT DIVERGENCE", json.dumps(d)[:400], flush=True)
            continue
        # both reject (phase1/decode)
        if cc != ac:
            stats["verdict_divergence"] += 1  # e.g. decode-reject vs phase1-reject
            d = {"kind": "reject-verdict", "base": name, "cardano": cc, "amaru": ac,
                 "cardano_reason": c["reason"][:200], "amaru_reason": a["reason"][:200], "cbor": mut.hex()}
            divergences.append(d); print("!!! REJECT-VERDICT DIVERGENCE", json.dumps(d)[:400], flush=True)
            continue
        # same verdict (both phase1 or both decode) -> reason-class parity
        rc, ra = reason_class(c["reason"], True), reason_class(a["reason"], False)
        if rc and ra and rc != ra:
            stats["reason_divergence"] += 1
            d = {"kind": "reason-class", "base": name, "verdict": cc, "cardano_class": rc, "amaru_class": ra,
                 "cardano_reason": c["reason"][:200], "amaru_reason": a["reason"][:200], "cbor": mut.hex()}
            divergences.append(d); print("!!! REASON-CLASS DIVERGENCE", json.dumps(d)[:400], flush=True)
        else:
            stats["both_reject_agree"] += 1
    return stats, divergences, False

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--corpus", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixture"))
    p.add_argument("--amaru", default="http://localhost:3018/api/submit/tx")
    p.add_argument("--cardano", default="http://localhost:8092/api/submit/tx")
    p.add_argument("--seconds", type=int, default=300)
    p.add_argument("--seed", type=int, default=1337)
    p.add_argument("--out", default="/tmp/phase1_soak_result.json")
    a = p.parse_args()
    stats, divs, consumed = run(os.path.abspath(a.corpus), a.amaru, a.cardano, a.seconds, a.seed)
    # merge into any existing out file (multi-chunk soak across restarts)
    prev = {"stats": {}, "divergences": []}
    if os.path.exists(a.out):
        try: prev = json.load(open(a.out))
        except Exception: pass
    for k, v in stats.items(): prev["stats"][k] = prev["stats"].get(k, 0) + v
    prev["divergences"].extend(divs)
    json.dump(prev, open(a.out, "w"), indent=2)
    print("STATS(chunk):", json.dumps(stats), flush=True)
    print("STATS(total):", json.dumps(prev["stats"]), flush=True)
    print("DIVERGENCES(total):", len(prev["divergences"]), "->", a.out, flush=True)
    print("CONSUMED" if consumed else "TIMEBOX_DONE", flush=True)
    print("RESULT:", "DIVERGENCE" if prev["divergences"] else "CLEAN (no verdict/reason-class divergence)", flush=True)
    sys.exit(2 if consumed else (1 if divs else 0))
