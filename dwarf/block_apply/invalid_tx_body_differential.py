#!/usr/bin/env python3
"""runtime_invalid_tx_body_differential — value-creation / theft detector for is_valid=false txs.

Conway rule: a phase-2-FAILED tx (is_valid=false) consumes ONLY collateral; the entire tx body
(outputs, mint, withdrawals, treasury donation, certs) is DROPPED. If amaru applies any body effect
on a failed tx, value is created from nothing (donation->treasury inflation; output->double-spend;
mint->free tokens; withdrawal->drained rewards). This reads each node's POST-APPLY state and
classifies whether the body effect leaked on amaru vs cardano.

State sources (both validated this campaign):
  amaru   : newest `pots.dump treasury=.. reserves=.. fees=.. donations=..` from the pair log (restart
            after apply to refresh), + a behavioral UTxO re-spend result for output/mint leaks.
  cardano : `cardano-cli conway query ledger-state` pots + `query utxo` for the body-effect address.

usage: invalid_tx_body_differential.py <body_effect: donation|output|mint|withdrawal> \
         <amaru_log> <amaru_behavior_json> <cardano_pots_json> [baseline_pots_json]
Writes a verdict JSON to stdout: {verdict: no_body_leak|VALUE_CREATION_DIVERGENCE, body_effect,
amaru_leaked: bool, cardano_leaked: bool, evidence}.
"""
import json, re, sys

def amaru_pots(log):
    t = open(log, errors="replace").read()
    h = re.findall(r"pots\.dump treasury=(\d+) reserves=(\d+) fees=(\d+) donations=(\d+)", t)
    if not h: return None
    a = h[-1]; return {"treasury": int(a[0]), "reserves": int(a[1]), "fees": int(a[2]), "donations": int(a[3])}

def main():
    eff = sys.argv[1]
    amaru = amaru_pots(sys.argv[2]) if len(sys.argv) > 2 else None
    behavior = json.load(open(sys.argv[3])) if len(sys.argv) > 3 else {}
    cardano = json.load(open(sys.argv[4])) if len(sys.argv) > 4 else {}
    baseline = json.load(open(sys.argv[5])) if len(sys.argv) > 5 else {"treasury":0,"donations":0}
    # amaru leak classification per body-effect
    amaru_leaked = None; evidence = {}
    if eff == "donation":
        if amaru is not None:
            d = amaru["donations"] - baseline.get("donations",0)
            t = amaru["treasury"] - baseline.get("treasury",0)
            amaru_leaked = (d > 0 or t > 0)
            evidence = {"amaru_donations_delta": d, "amaru_treasury_delta": t}
    elif eff in ("output","mint"):
        # behavioral: a re-spend/transfer of the failed-tx body-effect succeeded on amaru => leaked (created)
        amaru_leaked = bool(behavior.get("amaru_respend_accepted"))
        evidence = {"amaru_respend": behavior.get("amaru_respend_outcome")}
    elif eff == "withdrawal":
        amaru_leaked = bool(behavior.get("amaru_reward_leaked"))
        evidence = {"amaru_reward": behavior.get("amaru_reward_state")}
    cardano_leaked = bool(cardano.get("leaked"))  # expected False (cardano drops body, only collateral)
    verdict = "VALUE_CREATION_DIVERGENCE" if (amaru_leaked and not cardano_leaked) else (
              "inconclusive" if amaru_leaked is None else "no_body_leak")
    print(json.dumps({"primitive":"invalid_tx_body_differential","body_effect":eff,
                      "amaru_leaked":amaru_leaked,"cardano_leaked":cardano_leaked,
                      "verdict":verdict,"evidence":evidence}, indent=2))

if __name__ == "__main__":
    main()
