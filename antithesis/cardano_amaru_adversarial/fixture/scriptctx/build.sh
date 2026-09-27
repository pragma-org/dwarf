#!/usr/bin/env bash
# Build the ScriptContext-fidelity corpus (mint txs; the mint policy inspects a TxInfo field
# against a redeemer-supplied expectation). Testnet-only. Run in fixture/scriptctx.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; cd "$HERE"
IMAGE="${CARDANO_CLI_IMAGE:-cnode-p1:local}"
REFC="${REFC:-9aecf690dd8d3250f36e2183786a472cad22ee5f1ebee281900acc8b33d7e7b8#2}"  # a read-only ref input
cp ../funding/payment.skey .
trap 'rm -f payment.skey r.json *.raw' EXIT
CLI="docker run --rm -u $(id -u):$(id -g) -v $HERE:/w -w /w --entrypoint cardano-cli $IMAGE"
IN=9708b921e619b34a6fafc375ebd46bf65d77eed427f953eabed2b9da9212c4a1#0
ADDR=addr_test1vrj6thds8l3hqktz0lk7w9zcgq0qmktmkdcm80tuecxjxfcjg8ye6
M="--testnet-magic 42"; FEE=2000000; EXU="(6000000000,6000000)"

# mk <case> <script> <redeemer-int> [extra build-raw args...]
mk() { local n=$1 script=$2 rv=$3; shift 3
  local pol=$($CLI conway transaction policyid --script-file "$script.plutus"); local tok="1 $pol.434f4c4c"
  printf '{"int":%s}' "$rv" > r.json
  $CLI conway transaction build-raw --tx-in $IN --tx-in-collateral $IN \
    --tx-out "$ADDR+$((200000000000000-FEE))+$tok" --fee $FEE \
    --mint "$tok" --mint-script-file "$script.plutus" --mint-redeemer-file r.json --mint-execution-units "$EXU" \
    --protocol-params-file pparams.json "$@" --out-file "$n.raw"
  $CLI conway transaction sign --tx-body-file "$n.raw" --signing-key-file payment.skey $M --out-file "$n.tx"; }

mk ctxfee-ok            ctx_fee           2000000
mk ctxfee-wrong         ctx_fee           2000001
mk ctxmint-ok           ctx_mint_qty      1
mk ctxmint-wrong        ctx_mint_qty      2
mk ctxnuminputs-ok      ctx_num_inputs    1
mk ctxnuminputs-wrong   ctx_num_inputs    2
mk ctxnumref-ok         ctx_num_ref_inputs 1 --read-only-tx-in-reference "$REFC"
mk ctxnumref-wrong      ctx_num_ref_inputs 0 --read-only-tx-in-reference "$REFC"
mk ctxnumref-none-ok    ctx_num_ref_inputs 0
python3 "$HERE/manifest.py"
echo "built"
