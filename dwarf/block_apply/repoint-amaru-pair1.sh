#!/usr/bin/env bash
# Reset pair1 amaru to a FRESH store (tip 1199) and relaunch dialing wire/cod-forge serve <addr>:3001,
# so a forged block at tip+1 is validated against a clean 1199 chain each run.
# Usage: repoint-amaru-pair1.sh <serve_addr> [--keep-store]
set -uo pipefail
ADDR="${1:?usage: repoint-amaru-pair1.sh <serve_addr> [--keep-store]}"
KEEP="${2:-}"
AMARU=/home/nigel/amaru-0925-clean-eaf8ac3f
GLOBALS=/home/nigel/rebake-amaru/globals.env
STORE_SRC=/home/nigel/rebake-amaru/store
AS=/home/nigel/pair1-store
tmux kill-session -t amaru-pair1 2>/dev/null || true
sleep 1
if [ "$KEEP" != "--keep-store" ]; then
  rm -rf "$AS"; cp -a "$STORE_SRC" "$AS"; find "$AS" -name LOCK -delete 2>/dev/null || true
  echo "fresh store copied (tip 1199)"
fi
tmux new-session -d -s amaru-pair1 \
  "cd /home/nigel/rebake-amaru && . $GLOBALS && $AMARU node run --network testnet_42 --migrate-chain-db \
     --era-history $AS/era-history.json --ledger-dir $AS/ledger.testnet_42.db --chain-dir $AS/chain.testnet_42.db \
     --listen-address 0.0.0.0:4111 --submit-api-address 0.0.0.0:3210 \
     --peer-address ${ADDR}:3001 --peer-removal-cooldown-secs 86400 > /tmp/amaru-pair1.log 2>&1"
AH=000
for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25; do
  sleep 2
  AH=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:3210/api/submit/tx -H "Content-Type: application/cbor" --data-binary "00" 2>/dev/null || echo 000)
  [ "$AH" = "400" ] && break
done
echo "amaru-pair1 relaunched dialing ${ADDR}:3001, submit=${AH}"
grep "build.ledger_opened" /tmp/amaru-pair1.log | tail -1
