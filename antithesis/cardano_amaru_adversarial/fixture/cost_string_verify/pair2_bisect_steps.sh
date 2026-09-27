#!/usr/bin/env bash
# Binary-search amaru's exact metered STEPS for string_amp (mem fixed at cardano-exact 16284).
# The lowest declared-steps at which amaru still ACCEPTS == amaru's actual step cost.
set -uo pipefail
P=/tmp/nsprobe/strcost; cd $P
source ~/.aiken/bin/env
cp /tmp/plutus-wt/antithesis/cardano_amaru_adversarial/fixture/funding/payment.skey $P/
CLI="docker run --rm -u $(id -u):$(id -g) -v $P:/w -w /w --entrypoint cardano-cli cnode-p1:local"
IN=9708b921e619b34a6fafc375ebd46bf65d77eed427f953eabed2b9da9212c4a1#0
ADDR=addr_test1vrj6thds8l3hqktz0lk7w9zcgq0qmktmkdcm80tuecxjxfcjg8ye6
TOTAL=200000000000000; FEE=3000000; M="--testnet-magic 42"
POL=$($CLI conway transaction policyid --script-file /w/string_amp.plutus); TOK="1 $POL.434f4c4c"
CM=16284
probe() { # $1 = declared steps ; echoes http code from amaru
  $CLI conway transaction build-raw --script-valid --tx-in "$IN" --tx-in-collateral "$IN" \
    --tx-out "$ADDR+$((TOTAL-FEE))+$TOK" --fee $FEE \
    --mint "$TOK" --mint-script-file /w/string_amp.plutus --mint-redeemer-file /w/r.json \
    --mint-execution-units "($1,$CM)" --protocol-params-file /w/pparams.json --out-file /w/t.raw 2>/dev/null
  $CLI conway transaction sign --tx-body-file /w/t.raw --signing-key-file /w/payment.skey $M --out-file /w/t.tx 2>/dev/null
  local hex; hex=$(python3 -c "import json;print(json.load(open('$P/t.tx'))['cborHex'])")
  /home/nigel/reset-pair.sh pair2 >/dev/null 2>&1
  curl -s -m 30 -w "%{http_code}" -o /dev/null -X POST http://localhost:3211/api/submit/tx \
    -H "Content-Type: application/cbor" --data-binary @<(python3 -c "import sys;sys.stdout.buffer.write(bytes.fromhex('$hex'))"); }
lo=1; hi=149561297   # amaru accepts at hi (=cardano exact), search lowest accepting
while [ $((hi-lo)) -gt 200 ]; do
  mid=$(((lo+hi)/2))
  code=$(probe $mid)
  if [ "$code" = "202" ]; then hi=$mid; else lo=$mid; fi
  echo "steps=$mid -> $code   [lo=$lo hi=$hi]"
done
echo "AMARU exact steps ~= $hi (cardano=149561297); undercharge = $(python3 -c "print(f'{100*(149561297-$hi)/149561297:.1f}%')")"
rm -f $P/payment.skey
