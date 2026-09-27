#!/usr/bin/env bash
# Measure cardano's exact ex-units for the empty-bytestring amplifier, then submit at that exact
# budget to amaru. amaru reject(budget) => amaru charges MORE (empty-bs +1 over-charge) = divergence.
set -uo pipefail
D=/tmp/bls-aiken; cd $D
IMG=ghcr.io/intersectmbo/cardano-node:11.1.2
CLI="docker run --rm -u $(id -u):$(id -g) -v $D:/w -w /w --entrypoint cardano-cli $IMG"
IN=9708b921e619b34a6fafc375ebd46bf65d77eed427f953eabed2b9da9212c4a1#0
ADDR=addr_test1vrj6thds8l3hqktz0lk7w9zcgq0qmktmkdcm80tuecxjxfcjg8ye6
TOTAL=200000000000000; FEE=3000000; M="--testnet-magic 42"
echo "{\"bytes\":\"\"}" > $D/empty.redeemer.json
POL=$($CLI conway transaction policyid --script-file /w/empty_bs_amp.plutus); TOK="1 $POL.434f4c4c"

build() { # $1=exu
  $CLI conway transaction build-raw --script-valid --tx-in "$IN" --tx-in-collateral "$IN" \
    --tx-out "$ADDR+$((TOTAL-FEE))+$TOK" --fee $FEE \
    --mint "$TOK" --mint-script-file /w/empty_bs_amp.plutus --mint-redeemer-file /w/empty.redeemer.json \
    --mint-execution-units "$1" --protocol-params-file /w/pparams.json --out-file /w/ebs.raw 2>/tmp/e1
  $CLI conway transaction sign --tx-body-file /w/ebs.raw --signing-key-file /w/payment.skey $M --out-file /w/ebs.tx 2>>/tmp/e1
}
echo "=== build with ample ex-units, then calculate-plutus-script-cost (cardano exact) ==="
build "(10000000000,10000000)"
docker cp $D/ebs.tx pair1-cardano-ref:/tmp/ebs.tx
COST=$(docker exec pair1-cardano-ref cardano-cli conway transaction calculate-plutus-script-cost online --tx-file /tmp/ebs.tx $M --socket-path /state/db/node.socket 2>&1)
echo "$COST"
MEM=$(echo "$COST" | python3 -c "import json,sys;d=json.load(sys.stdin);print(d[0]['executionUnits']['memory'])" 2>/dev/null)
STEPS=$(echo "$COST" | python3 -c "import json,sys;d=json.load(sys.stdin);print(d[0]['executionUnits']['steps'])" 2>/dev/null)
echo "cardano EXACT: mem=$MEM steps=$STEPS"
[ -z "$MEM" ] && { echo "cost parse failed"; exit 1; }
echo "=== rebuild at cardano's EXACT budget, submit to both ==="
build "($STEPS,$MEM)"
HEX=$(python3 -c "import json;print(json.load(open('$D/ebs.tx'))['cborHex'])")
/home/nigel/reset-pair.sh pair1 >/dev/null 2>&1; sleep 1
sub() { curl -s -m 25 -w "\n@@%{http_code}" -X POST "$1" -H "Content-Type: application/cbor" --data-binary @<(python3 -c "import sys;sys.stdout.buffer.write(bytes.fromhex('$HEX'))"); }
ar=$(sub http://localhost:3210/api/submit/tx); cr=$(sub http://localhost:8110/api/submit/tx)
echo "--- amaru (declared=cardano exact) ---"; echo "$ar" | head -c 400; echo
echo "--- cardano (declared=own exact) ---"; echo "$cr" | head -c 300; echo
