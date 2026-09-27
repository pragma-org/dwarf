#!/usr/bin/env bash
# Cost-model corroboration sweep on pair2: per family, measure cardano's EXACT ex-units, then
# knife-edge probe. CONFORMANT family => amaru meters == cardano => at (steps-1) and (mem-1) BOTH
# REJECT; at exact BOTH ACCEPT. A SPLIT (amaru 202 where cardano rejects) => amaru mis-prices.
set -uo pipefail
P=/tmp/nsprobe/cost; cd $P
AK=/tmp/nsprobe/aiken/p2b
source ~/.aiken/bin/env
cp /tmp/plutus-wt/antithesis/cardano_amaru_adversarial/fixture/funding/payment.skey $P/
docker exec pair2-cardano-ref cardano-cli conway query protocol-parameters --testnet-magic 42 --socket-path /state/db/node.socket > $P/pparams.json 2>/dev/null
CLI="docker run --rm -u $(id -u):$(id -g) -v $P:/w -w /w --entrypoint cardano-cli cnode-p1:local"
IN=9708b921e619b34a6fafc375ebd46bf65d77eed427f953eabed2b9da9212c4a1#0
ADDR=addr_test1vrj6thds8l3hqktz0lk7w9zcgq0qmktmkdcm80tuecxjxfcjg8ye6
TOTAL=200000000000000; FEE=3000000; M="--testnet-magic 42"
declare -A VAL
while read -r cid val; do VAL[$cid]=$val; done < <(python3 -c "import json;[print(c,v) for c,v in json.load(open('/tmp/nsprobe/cost_cases.json'))]")

build() { # $1=case $2=exu
  local pol; pol=$($CLI conway transaction policyid --script-file /w/${VAL[$1]}.plutus)
  $CLI conway transaction build-raw --script-valid --tx-in "$IN" --tx-in-collateral "$IN" \
    --tx-out "$ADDR+$((TOTAL-FEE))+1 $pol.434f4c4c" --fee $FEE \
    --mint "1 $pol.434f4c4c" --mint-script-file /w/${VAL[$1]}.plutus --mint-redeemer-file /w/r_$1.json \
    --mint-execution-units "$2" --protocol-params-file /w/pparams.json --out-file /w/t.raw 2>/tmp/be
  $CLI conway transaction sign --tx-body-file /w/t.raw --signing-key-file /w/payment.skey $M --out-file /w/t.tx 2>>/tmp/be
}
code() { local url=$1 hex; hex=$(python3 -c "import json;print(json.load(open('$P/t.tx'))['cborHex'])")
  curl -s -m 40 -w "%{http_code}" -o /dev/null -X POST "$url" -H "Content-Type: application/cbor" \
    --data-binary @<(python3 -c "import sys;sys.stdout.buffer.write(bytes.fromhex('$hex'))"); }

# build all plutus files once
for v in cost_baseline cost_add cost_mul cost_index cost_cons cost_slice cost_serialise cost_equalsdata cost_blake2b cost_sha2 cost_keccak; do
  (cd $AK && aiken blueprint convert -m cost -v $v) > $P/$v.plutus 2>/dev/null
done
printf "%-11s %13s %9s | %-14s %-14s %-14s\n" family steps mem "exact(a/c)" "steps-1(a/c)" "mem-1(a/c)"
RESULT=/tmp/nsprobe/cost_sweep_result.txt; : > $RESULT
for cid in baseline add mul index cons slice serialise equalsdata blake2b sha2 keccak; do
  build $cid "(13000000000,13000000)"
  docker cp $P/t.tx pair2-cardano-ref:/tmp/t.tx >/dev/null 2>&1
  COST=$(docker exec pair2-cardano-ref cardano-cli conway transaction calculate-plutus-script-cost online --tx-file /tmp/t.tx $M --socket-path /state/db/node.socket 2>/dev/null)
  CS=$(echo "$COST" | python3 -c "import json,sys;print(json.load(sys.stdin)[0]['executionUnits']['steps'])" 2>/dev/null)
  CM=$(echo "$COST" | python3 -c "import json,sys;print(json.load(sys.stdin)[0]['executionUnits']['memory'])" 2>/dev/null)
  [ -z "$CS" ] && { printf "%-11s COST-PARSE-FAIL\n" $cid; continue; }
  /home/nigel/reset-pair.sh pair2 >/dev/null 2>&1; build $cid "($CS,$CM)"
  ea=$(code http://localhost:3211/api/submit/tx); ec=$(code http://localhost:8111/api/submit/tx)
  /home/nigel/reset-pair.sh pair2 >/dev/null 2>&1; build $cid "($((CS-1)),$CM)"
  sa=$(code http://localhost:3211/api/submit/tx); sc=$(code http://localhost:8111/api/submit/tx)
  /home/nigel/reset-pair.sh pair2 >/dev/null 2>&1; build $cid "($CS,$((CM-1)))"
  ma=$(code http://localhost:3211/api/submit/tx); mc=$(code http://localhost:8111/api/submit/tx)
  printf "%-11s %13s %9s | a%s/c%s        a%s/c%s        a%s/c%s\n" $cid "$CS" "$CM" "$ea" "$ec" "$sa" "$sc" "$ma" "$mc"
  echo "$cid steps=$CS mem=$CM exact=a$ea/c$ec steps-1=a$sa/c$sc mem-1=a$ma/c$mc" >> $RESULT
done
rm -f $P/payment.skey
echo "=== VERDICT ==="
awk '{split=0; if($4=="steps-1=a202/c400"||$5=="mem-1=a202/c400"){split=1}; print $1, (split? "*** SPLIT (amaru undercharges) ***":"AGREE (conformant)")}' $RESULT
