"""Join opcert header cases (ground truth) with the target's observed verdicts.

The peer serves one header per case (each preceded by a valid control) and
records the header hash it served for each case. The target's own logs, parsed
into verdicts by hash, are the observed side. join_cases classifies each case:

  matched      — the target reached its declared verdict (accept for valid
                 cases; reject with the declared reason for broken cases).
  mismatch     — the target reached a different verdict, or rejected for a
                 different reason, or accepted a case it should reject.
  inconclusive — the case was never served, or its header was never observed
                 in the target's logs (fail-closed: never a pass).

The driver (run_opcert_header_cases) that starts the peer, captures logs and
writes result.json is wired after the peer's evidence format is fixed (Task 3).
"""
from __future__ import annotations


def join_cases(cases, served_hash_by_case, observed_by_hash, implementation):
    rows = []
    for case in cases:
        cid = case["id"]
        served = served_hash_by_case.get(cid)
        observed = observed_by_hash.get(served) if served else None
        expected_verdict = case["expected_verdict"]
        expected_reason = case["expected_reason"][implementation]
        if served is None or observed is None:
            status = "inconclusive"
        elif expected_verdict == "accept":
            status = "matched" if observed["verdict"] == "accepted" else "mismatch"
        else:  # reject: verdict AND reason must match
            status = (
                "matched"
                if observed["verdict"] == "rejected" and observed.get("reason") == expected_reason
                else "mismatch"
            )
        rows.append({
            "case": cid,
            "expected_verdict": expected_verdict,
            "expected_reason": expected_reason,
            "served_hash": served,
            "observed_verdict": (observed or {}).get("verdict"),
            "observed_reason": (observed or {}).get("reason"),
            "status": status,
        })
    return rows
