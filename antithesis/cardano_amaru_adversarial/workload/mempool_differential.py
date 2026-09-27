"""Mempool / submit-path phase-1 differential driver (cardano-node vs Amaru).

Extends mixed_phase1 and reuses stake_pool_differential's grading. Three parts:

  size (default, --control): the max-tx-size boundary 16384 / 16385 in canonical and non-canonical
      encodings of the body, witnesses and aux data. Both ledgers must count the ORIGINAL bytes; the
      parity token of a reject is the original size (16385). A node that measured a re-encoding
      would accept the non-canonical -16385 case or print a smaller size.
  --duplicate: the same valid tx twice. First submission: both accept. Second: neither may accept
      (amaru answers 409 duplicate, cardano "All inputs are spent"; both classify MASKED). AGREE
      requires exactly that; any second ACCEPT is a divergence.
  --http: malformed requests (empty body, 1 MiB junk, truncated tx, wrong Content-Type, GET). AGREE
      requires the same HTTP status from both AND a liveness check after each request: a known
      reject (size-canonical-16385) must still get phase1_reject from both nodes.

Size violations are idempotent. Accepts are single-use: run --control CASE and --duplicate after a
mempool reset.
"""
from __future__ import annotations
import json, sys, urllib.error, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # workload/ on path
from mixed_phase1 import HttpSubmitTransport, observe_differential, ACCEPTED, PHASE1_REJECT
import stake_pool_differential as base

REASON_CLASSES = {"tx_too_large": (r"maxtxsizeutxo", r"transaction too large")}
LIVENESS_CASE = "size-canonical-16385"


def grade(case: dict, result: dict) -> dict:
    return base.grade(case, result, REASON_CLASSES)


def _payload(root: Path, case: dict) -> bytes:
    return bytes.fromhex(json.loads((root / case["tx_file"]).read_text())["cborHex"])


def _cases(root: Path) -> dict:
    return {c["case_id"]: c for c in json.loads((root / "mempool_corpus.json").read_text())["cases"]}


def run(root: Path, transports: dict, control: str | None = None) -> list[dict]:
    cases = _cases(root)
    if control:
        if cases.get(control, {}).get("expected") != "accept":
            raise SystemExit(f"unknown control case: {control}")
        selected = [cases[control]]
    else:
        selected = [c for c in cases.values() if c["expected"] == "reject"]
    return [grade(c, observe_differential(_payload(root, c), transports)) for c in selected]


def run_duplicate(root: Path, transports: dict) -> list[dict]:
    payload = _payload(root, _cases(root)["mp-base"])
    first = observe_differential(payload, transports)
    second = observe_differential(payload, transports)
    c1 = {n: o["classification"] for n, o in first["observations"].items()}
    c2 = {n: o["classification"] for n, o in second["observations"].items()}
    row = {"case_id": "duplicate-resubmission", "first": c1, "second": c2,
           "second_reasons": {n: o["reason"][:160] for n, o in second["observations"].items()}}
    if set(c1.values()) != {ACCEPTED}:
        row["status"] = "INCONCLUSIVE"; row["why"] = "first submission not accepted by both"
    elif ACCEPTED in c2.values():
        row["status"] = "VERDICT-DIVERGENCE"  # a node admitted the same tx twice
    elif all(c in ("masked", PHASE1_REJECT) for c in c2.values()):
        row["status"] = "AGREE"
    else:
        row["status"] = "INCONCLUSIVE"; row["why"] = "second submission unclassifiable/unavailable"
    return [row]


HTTP_CASES = (  # (case_id, body, content-type, method)
    ("http-empty-body", b"", "application/cbor", "POST"),
    ("http-junk-1mib", b"\xff" * (1 << 20), "application/cbor", "POST"),
    ("http-truncated-tx", None, "application/cbor", "POST"),   # mp-base minus its last byte
    ("http-wrong-content-type", b"\x84", "text/plain", "POST"),
    ("http-get", None, None, "GET"),
)


def _raw_request(url: str, body, ctype, method) -> int | str:
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"Content-Type": ctype} if ctype else {})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:  # noqa: BLE001 - any transport failure is a liveness signal
        return type(e).__name__


def run_http(root: Path, urls: dict, transports: dict) -> list[dict]:
    cases = _cases(root)
    live_payload = _payload(root, cases[LIVENESS_CASE])
    truncated = _payload(root, cases["mp-base"])[:-1]
    rows = []
    for cid, body, ctype, method in HTTP_CASES:
        body = truncated if cid == "http-truncated-tx" else body
        status = {n: _raw_request(u, body, ctype, method) for n, u in urls.items()}
        live = observe_differential(live_payload, transports)
        alive = {n: o["classification"] == PHASE1_REJECT for n, o in live["observations"].items()}
        row = {"case_id": cid, "http_status": status, "alive_after": alive}
        if not all(alive.values()):
            row["status"] = "LIVENESS-FAILURE"
        elif len(set(status.values())) == 1 and all(isinstance(s, int) and 400 <= s < 500
                                                      for s in status.values()):
            row["status"] = "AGREE"
        else:
            row["status"] = "STATUS-DIVERGENCE"
        rows.append(row)
    return rows


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--corpus", default=str(Path(__file__).resolve().parents[1] / "fixture" / "mempool"))
    p.add_argument("--amaru", default="http://localhost:3012/api/submit/tx")
    p.add_argument("--cardano", default="http://localhost:8090/api/submit/tx")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--control", help="run ONE single-use accept case (after a mempool reset)")
    g.add_argument("--duplicate", action="store_true", help="duplicate resubmission (after a reset)")
    g.add_argument("--http", action="store_true", help="HTTP robustness + liveness")
    a = p.parse_args()
    root = Path(a.corpus)
    urls = {"amaru": a.amaru, "cardano": a.cardano}
    transports = {n: HttpSubmitTransport(u, keep_detail=True) for n, u in urls.items()}
    if a.duplicate:
        rows, label = run_duplicate(root, transports), "DUPLICATE"
    elif a.http:
        rows, label = run_http(root, urls, transports), "HTTP"
    else:
        rows = run(root, transports, a.control)
        label = "CONTROL " + a.control if a.control else f"SIZE VIOLATIONS ({len(rows)} cases)"
    print(json.dumps(rows, indent=2))
    statuses = {r["status"] for r in rows}
    if statuses & {"VERDICT-DIVERGENCE", "REASON-DIVERGENCE", "STATUS-DIVERGENCE", "LIVENESS-FAILURE"}:
        print(f"{label}: DIVERGENCE {sorted(statuses)}"); sys.exit(1)
    if statuses - {"AGREE"}:
        print(f"{label}: INCONCLUSIVE {sorted(statuses)}"); sys.exit(2)
    print(f"{label}: ALL AGREE"); sys.exit(0)
