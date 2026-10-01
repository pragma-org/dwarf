#!/usr/bin/env bash
# Direct runner for the runtime_tx_submit_differential primitive logic: submit ONE fixture tx to a
# target(amaru) + reference(cardano) submit-API and classify each outcome (accept/reject/crash).
# Accepts a raw .cbor OR a cardano-cli .tx envelope ({"cborHex":...}). Does a FULL pair reset first
# (restarts cardano-ref to clear its mempool + fresh funded amaru) so same-txid single-use fixtures
# do not mask as "inputs already spent" on the frozen reference.
#   usage: submit_differential_run.sh <fixture> [pair=pair1] [amaru_port=3210] [cardano_port=8110] [--no-reset]
set -uo pipefail
FX="${1:?usage: submit_differential_run.sh <fixture> [pair] [amaru_port] [cardano_port] [--no-reset]}"
PAIR="${2:-pair1}"; AP="${3:-3210}"; CP="${4:-8110}"; NORESET="${5:-}"
PAY=$(mktemp /tmp/sdr-XXXX.bin)
if head -c1 "$FX" | grep -q "{"; then python3 -c "import json,sys;open(\"$PAY\",\"wb\").write(bytes.fromhex(json.load(open(\"$FX\"))[\"cborHex\"]))"; else cp "$FX" "$PAY"; fi
[ "$NORESET" != "--no-reset" ] && bash /home/nigel/reset-pair.sh "$PAIR" >/dev/null 2>&1
AC=$(curl -s -o /dev/null -w "%{http_code}" --max-time 25 -X POST -H "Content-Type: application/cbor" --data-binary @"$PAY" "http://127.0.0.1:${AP}/api/submit/tx" 2>/dev/null)
tmux has-session -t "amaru-${PAIR}" 2>/dev/null && ALIVE=ALIVE || ALIVE=DOWN
CBODY=$(curl -s --max-time 25 -X POST -H "Content-Type: application/cbor" --data-binary @"$PAY" "http://127.0.0.1:${CP}/api/submit/tx" 2>/dev/null)
CC=$(echo "$CBODY" | grep -q "^\"" && echo 202 || echo 400)
cls(){ if [ "$1" -ge 200 ] && [ "$1" -lt 300 ]; then echo accept; elif [ "$1" -ge 400 ] && [ "$1" -lt 500 ]; then echo reject; else echo crash; fi; }
AO=$(cls "$AC"); [ "$AC" = "000" ] && [ "$ALIVE" = DOWN ] && AO=crash
CO=$(cls "$CC")
V="AGREE"; [ "$AO" != "$CO" ] && V="DIVERGENCE"
echo "fixture=$(basename "$FX") amaru=$AO($AC,$ALIVE) cardano=$CO => $V"
echo "cardano_detail: $(echo "$CBODY" | head -c 200)"
rm -f "$PAY"
