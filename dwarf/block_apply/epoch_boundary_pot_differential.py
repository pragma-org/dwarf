#!/usr/bin/env python3
"""epoch_boundary_pot_differential — diff the pot TRANSITION across an epoch boundary
between amaru and cardano-node.

Where block_apply_state_differential compares a single post-apply snapshot, this compares
the DELTA each node applies as it crosses an epoch boundary. It reads pots on both nodes
BEFORE the boundary (snapshot A) and AFTER (snapshot B), computes each node's
delta for the comparable pots (treasury, reserves, fees), and diffs amaru's transition
against the cardano-node reference transition.

A divergence (POT_TRANSITION_DIVERGENCE) means amaru's epoch-boundary pot accounting differs
from cardano-node — e.g. the confirmed is_valid=false donation realizing into amaru's treasury
at the boundary while cardano-node's treasury is unchanged.

Reuses the pot readers from block_apply_state_differential (same dir); no new extraction logic.

Usage:
  epoch_boundary_pot_differential.py snapshot-amaru <amaru_log> [out.json]
  epoch_boundary_pot_differential.py snapshot-cardano <container> <socket> <magic> [out.json]
  epoch_boundary_pot_differential.py transition-diff \
      <amaru_before.json> <amaru_after.json> <cardano_before.json> <cardano_after.json> [result.json]
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from block_apply_state_differential import read_amaru_pots, read_cardano_pots  # noqa: E402

COMPARABLE = ["treasury", "reserves", "fees"]


def _load(path):
    with open(path) as fh:
        return json.load(fh)


def _delta(before, after, keys):
    return {k: int(after.get(k) or 0) - int(before.get(k) or 0) for k in keys}


def transition_diff(amaru_before, amaru_after, cardano_before, cardano_after):
    amaru_delta = _delta(amaru_before, amaru_after, COMPARABLE)
    cardano_delta = _delta(cardano_before, cardano_after, COMPARABLE)
    diffs = {
        k: {"amaru": amaru_delta[k], "cardano": cardano_delta[k]}
        for k in COMPARABLE
        if amaru_delta[k] != cardano_delta[k]
    }
    verdict = "CONFORMANT" if not diffs else "POT_TRANSITION_DIVERGENCE"
    return {
        "amaru_before": amaru_before,
        "amaru_after": amaru_after,
        "cardano_before": cardano_before,
        "cardano_after": cardano_after,
        "amaru_delta": amaru_delta,
        "cardano_delta": cardano_delta,
        "transition_diffs": diffs,
        "amaru_only": {
            "donations_delta": int((amaru_after or {}).get("donations") or 0)
            - int((amaru_before or {}).get("donations") or 0)
        },
        "cardano_only": {
            "deposited_delta": int((cardano_after or {}).get("deposited") or 0)
            - int((cardano_before or {}).get("deposited") or 0)
        },
        "verdict": verdict,
    }


def _emit(obj, out):
    text = json.dumps(obj, indent=2)
    if out:
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
        with open(out, "w") as fh:
            fh.write(text)
    print(text)


def main():
    if len(sys.argv) < 2:
        print("usage: see module docstring", file=sys.stderr)
        sys.exit(2)
    cmd = sys.argv[1]
    if cmd == "snapshot-amaru":
        out = sys.argv[3] if len(sys.argv) > 3 else None
        _emit(read_amaru_pots(sys.argv[2]) or {}, out)
    elif cmd == "snapshot-cardano":
        out = sys.argv[6] if len(sys.argv) > 6 else (sys.argv[5] if len(sys.argv) > 5 else None)
        _emit(read_cardano_pots(sys.argv[2], sys.argv[3], sys.argv[4]), out)
    elif cmd == "transition-diff":
        amaru_before, amaru_after, cardano_before, cardano_after = (
            _load(sys.argv[i]) for i in range(2, 6)
        )
        out = sys.argv[6] if len(sys.argv) > 6 else None
        _emit(transition_diff(amaru_before, amaru_after, cardano_before, cardano_after), out)
    else:
        print(f"unknown cmd {cmd!r}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
