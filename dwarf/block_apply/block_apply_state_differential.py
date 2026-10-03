#!/usr/bin/env python3
"""runtime_block_apply_differential (pots dimension).

Compares post-apply LEDGER STATE (pots) between amaru and cardano-node rather than
just accept/reject/crash. Extracts:
  - amaru  : the latest `pots.dump treasury=.. reserves=.. fees=.. donations=..` line
             from the amaru pair log (amaru dumps pots at boot; restart after an apply
             to refresh it against the updated store).
  - cardano: `cardano-cli conway query ledger-state` -> treasury/reserves/deposited/fees.
Comparable fields: treasury, reserves, fees. Side-specific: amaru.donations, cardano.deposited
(reported, not diffed, since the two models track them differently).

Usage:
  block_apply_state_differential.py amaru-pots <amaru_log>
  block_apply_state_differential.py cardano-pots <container> <socket> <magic>
  block_apply_state_differential.py diff <amaru_log> <container> <socket> <magic>
"""
import json, re, subprocess, sys

def read_amaru_pots(log):
    txt = open(log, errors="replace").read()
    hits = re.findall(r"pots\.dump treasury=(\d+) reserves=(\d+) fees=(\d+) donations=(\d+)", txt)
    if not hits:
        return None
    t, r, f, d = hits[-1]  # newest
    return {"treasury": int(t), "reserves": int(r), "fees": int(f), "donations": int(d)}

def read_cardano_pots(container, socket, magic):
    out = subprocess.run(
        ["docker","exec","-e",f"CARDANO_NODE_SOCKET_PATH={socket}",container,
         "cardano-cli","conway","query","ledger-state","--testnet-magic",str(magic)],
        capture_output=True, text=True, timeout=90).stdout
    d = json.loads(out)
    found = {}
    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k.lower() in ("treasury","reserves","deposited","fees") and isinstance(v,(int,float)):
                    found.setdefault(k.lower(), int(v))
                walk(v)
        elif isinstance(o, list):
            for x in o: walk(x)
    walk(d)
    return found

def main():
    cmd = sys.argv[1]
    if cmd == "amaru-pots":
        print(json.dumps(read_amaru_pots(sys.argv[2])))
    elif cmd == "cardano-pots":
        print(json.dumps(read_cardano_pots(sys.argv[2], sys.argv[3], sys.argv[4])))
    elif cmd == "diff":
        a = read_amaru_pots(sys.argv[2])
        c = read_cardano_pots(sys.argv[3], sys.argv[4], sys.argv[5])
        comparable = ["treasury","reserves","fees"]
        diffs = {k: (a.get(k), c.get(k)) for k in comparable if a.get(k) != c.get(k)}
        verdict = "AGREE" if not diffs else "STATE-DIVERGENCE"
        print(json.dumps({"amaru": a, "cardano": c, "comparable_diffs": diffs,
                          "amaru_only": {"donations": a.get("donations")},
                          "cardano_only": {"deposited": c.get("deposited")},
                          "verdict": verdict}, indent=2))
    else:
        print("unknown cmd", file=sys.stderr); sys.exit(2)

if __name__ == "__main__":
    main()
