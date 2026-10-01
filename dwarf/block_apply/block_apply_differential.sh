#!/usr/bin/env bash
# M2 serialized: verify ACTIVE-BODY.txt == block.json, reset+repoint amaru, watch fetch/apply/crash.
set -uo pipefail
PB=/home/nigel/forge-work/pathb
D=$PB/outputs/forged-block
ADDR=$(tr -d ' \t\r\n' < "$D/serve-addr.txt" | sed 's/:3001$//')
BJ_HASH=$(python3 -c "import json;print(json.load(open('$D/block.json'))['point_hash_hex'])")
SLOT=$(python3 -c "import json;print(json.load(open('$D/block.json'))['point_slot'])")
# race guard: block.json must match cod-forge's ACTIVE-BODY.txt (<label> <point_hash>)
if [ -f "$D/ACTIVE-BODY.txt" ]; then
  AB=$(cat "$D/ACTIVE-BODY.txt"); AB_LABEL=$(echo "$AB" | awk '{print $1}'); AB_HASH=$(echo "$AB" | awk '{print $2}')
  echo "ACTIVE-BODY: label=$AB_LABEL hash=${AB_HASH:0:16} | block.json hash=${BJ_HASH:0:16}"
  if [ "$AB_HASH" != "$BJ_HASH" ]; then
    echo "ABORT: block.json point_hash != ACTIVE-BODY.txt hash (race/mismatch). Not running."; exit 4; fi
else
  echo "WARN: no ACTIVE-BODY.txt; proceeding on block.json hash ${BJ_HASH:0:16}"
  AB_LABEL="(unlabeled)"
fi
echo "serve_host=$ADDR forged_slot=$SLOT body=$AB_LABEL forged_hash=${BJ_HASH:0:16}"
bash "$PB/repoint-amaru-pair1.sh" "$ADDR"
echo "== watch ~90s (early-exit on panic) =="
for i in $(seq 1 45); do
  sleep 2
  if grep -qiE "panic|unreachable code|thread .*panicked" /tmp/amaru-pair1.log; then
    echo "=== EARLY: PANIC ==="; grep -iE "panic|unreachable" /tmp/amaru-pair1.log | tail -4; break; fi
done
echo "===================== OUTCOME DUMP ($AB_LABEL) ====================="
echo "-- peer/connect --"; grep -iE "peer.added|handshake|address_rejected|static_peers|connect_failed" /tmp/amaru-pair1.log | tail -3
echo "-- chainsync/adopt --"; grep -iE "intersect_found|tip.adopt" /tmp/amaru-pair1.log | tail -3
echo "-- fetch/apply/reject --"; grep -iE "block.?fetch|fetched|block.*apply|apply.*block|store.*block|invalid_header|Invalid VRF|nothing_to_fetch|epoch_transition.apply|perf.header" /tmp/amaru-pair1.log | tail -8
echo "-- amaru alive --"; curl -s -o /dev/null -w "submit:3210=%{http_code}\n" --max-time 5 -X POST -H "Content-Type: application/cbor" --data-binary "\x84" http://localhost:3210/api/submit/tx
echo "-- serve.log tail --"; tail -4 "$D/serve.log" 2>/dev/null
