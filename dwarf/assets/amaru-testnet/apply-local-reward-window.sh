#!/usr/bin/env bash
# Opt-in (substrate.amaru_local_reward_window_timing): apply the local reward-window
# timing contract (dwarf/scripts/local_reward_window.py) to one pinned pool
# configuration, after the loader copied it and before the start time is set.
# Only the contract's fields change; every other genesis field stays as pinned.
set -euo pipefail

request=$1
pool_root=$2

shelley="$pool_root/configs/shelley-genesis.json"
byron="$pool_root/configs/byron-genesis.json"
if [[ ! -f "$shelley" ]] || [[ ! -f "$byron" ]]; then
    echo "local reward-window: $pool_root is missing its Shelley or Byron genesis" >&2
    exit 1
fi
jq -e '(.shelley | type == "object") and (.byron_protocol_k | type == "number")' "$request" >/dev/null || {
    echo "local reward-window: request is invalid" >&2
    exit 1
}

tmp="${shelley}.local-reward-window.tmp"
jq --slurpfile request "$request" \
    '($request[0].shelley) as $c
     | .epochLength = $c.epochLength
     | .slotLength = $c.slotLength
     | .securityParam = $c.securityParam
     | .activeSlotsCoeff = $c.activeSlotsCoeff
     | .protocolParams.rho = $c.protocolParams.rho
     | .protocolParams.tau = $c.protocolParams.tau' \
    "$shelley" >"$tmp"
mv -f "$tmp" "$shelley"

tmp="${byron}.local-reward-window.tmp"
jq --slurpfile request "$request" \
    '.protocolConsts.k = $request[0].byron_protocol_k' "$byron" >"$tmp"
mv -f "$tmp" "$byron"
