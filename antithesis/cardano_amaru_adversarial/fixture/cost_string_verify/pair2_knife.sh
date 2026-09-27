#!/usr/bin/env bash
# Independent 2x of fd's string-builtin under-charge, on pair2 (amaru :3211 / cardano :8111).
# append_string x10 (redeemer-driven decode_utf8, no const-fold). Measure cardano's EXACT ex-units,
# then declare (steps-1, mem) -> cardano REJECTS (1 step over its metered cost); if amaru ACCEPTS
# it charges fewer steps than cardano => is_valid consensus divergence. Run the split twice.
set -uo pipefail
P=/tmp/nsprobe/strcost; mkdir -p $P; cd $P
AK=/tmp/nsprobe/aiken/p2b
source ~/.aiken/bin/env
(cd $AK && aiken blueprint convert -m strcost -v string_amp) > $P/string_amp.plutus 2>/dev/null
cp /tmp/plutus-wt/antithesis/cardano_amaru_adversarial/fixture/funding/payment.skey $P/
docker exec pair2-cardano-ref cardano-cli conway query protocol-parameters --testnet-magic 42 --socket-path /state/db/node.socket > $P/pparams.json 2>/dev/null
CLI="docker run --rm -u $(id -u):$(id -g) -v $P:/w -w /w --entrypoint cardano-cli cnode-p1:local"
IN=9708b921e619b34a6fafc375ebd46bf65d77eed427f953eabed2b9da9212c4a1#0
ADDR=addr_test1vrj6thds8l3hqktz0lk7w9zcgq0qmktmkdcm80tuecxjxfcjg8ye6
TOTAL=200000000000000; FEE=3000000; M="--testnet-magic 42"
# redeemer: 32-byte ASCII bytestring (valid UTF-8) as Data ByteArray
echo '{"bytes":"6162636465666768696a6b6c6d6e6f707172737475767778797a303132333435"}' > $P/r.json
POL=$($CLI conway transaction policyid --script-file /w/string_amp.plutus); TOK="1 $POL.434f4c4c"
build() {
  $CLI conway transaction build-raw --script-valid --tx-in "$IN" --tx-in-collateral "$IN" \
    --tx-out "$ADDR+$((TOTAL-FEE))+$TOK" --fee $FEE \
    --mint "$TOK" --mint-script-file /w/string_amp.plutus --mint-redeemer-file /w/r.json \
    --mint-execution-units "$1" --protocol-params-file /w/pparams.json --out-file /w/t.raw 2>/tmp/be
  $CLI conway transaction sign --tx-body-file /w/t.raw --signing-key-file /w/payment.skey $M --out-file /w/t.tx 2>>/tmp/be
}
sub() { local hex; hex=$(python3 -c "import json;print(json.load(open('$P/t.tx'))['cborHex'])")
  curl -s -m 30 -w "\n@@%{http_code}" -X POST "$1" -H "Content-Type: application/cbor" \
    --data-binary @<(python3 -c "import sys;sys.stdout.buffer.write(bytes.fromhex('$hex'))"); }

echo "=== measure cardano EXACT cost ==="
build "(12000000000,12000000)"
docker cp $P/t.tx pair2-cardano-ref:/tmp/t.tx >/dev/null 2>&1
COST=$(docker exec pair2-cardano-ref cardano-cli conway transaction calculate-plutus-script-cost online --tx-file /tmp/t.tx $M --socket-path /state/db/node.socket 2>&1)
echo "$COST"
CS=$(echo "$COST" | python3 -c "import json,sys;print(json.load(sys.stdin)[0]['executionUnits']['steps'])" 2>/dev/null)
CM=$(echo "$COST" | python3 -c "import json,sys;print(json.load(sys.stdin)[0]['executionUnits']['memory'])" 2>/dev/null)
echo "cardano EXACT: steps=$CS mem=$CM"
[ -z "$CS" ] && { echo "PARSE FAIL"; exit 1; }

echo; echo "=== CONTROL: declare cardano-exact, submit both (expect both accept) ==="
/home/nigel/reset-pair.sh pair2 >/dev/null 2>&1
build "($CS,$CM)"
echo "amaru : $(sub http://localhost:3211/api/submit/tx | tr -d '\n' | head -c 200)"
echo "cardano: $(sub http://localhost:8111/api/submit/tx | tr -d '\n' | head -c 200)"

for trial in 1 2; do
  echo; echo "=== SPLIT trial $trial: declare (steps-1=$((CS-1)), mem=$CM) ==="
  /home/nigel/reset-pair.sh pair2 >/dev/null 2>&1
  build "($((CS-1)),$CM)"
  echo "amaru : $(sub http://localhost:3211/api/submit/tx | tr -d '\n' | head -c 260)"
  echo "cardano: $(sub http://localhost:8111/api/submit/tx | tr -d '\n' | head -c 260)"
done
rm -f $P/payment.skey
