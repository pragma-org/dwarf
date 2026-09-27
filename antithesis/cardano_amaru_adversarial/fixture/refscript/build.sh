#!/usr/bin/env bash
# Build the reference-script + inline-datum phase-2 corpus (9 cases). Testnet-only, no value.
# Needs the pre-mined UTxO ids from the re-bake, passed as env vars:
#   A_INLINE  B_HASH  C_OK  D_FAIL  C_FAIL   (each TxId#Ix)
# plus the funding UTxO for collateral. All PlutusV3, unit redeemer/datum.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
FUND="$HERE/../funding"
IMAGE="${CARDANO_CLI_IMAGE:-cnode-p1:local}"
: "${A_INLINE:?}"; : "${B_HASH:?}"; : "${C_OK:?}"; : "${D_FAIL:?}"; : "${C_FAIL:?}"
cd "$HERE"; mkdir -p keys
cp "$FUND/payment.skey" keys/
trap 'rm -f keys/payment.skey *.raw' EXIT
CLI="docker run --rm -u $(id -u):$(id -g) -v $HERE:/w -w /w --entrypoint cardano-cli $IMAGE"

COLL=9708b921e619b34a6fafc375ebd46bf65d77eed427f953eabed2b9da9212c4a1#0
ADDR=addr_test1vrj6thds8l3hqktz0lk7w9zcgq0qmktmkdcm80tuecxjxfcjg8ye6
M="--testnet-magic 42"; FEE=3000000; EXU="(6000000000,6000000)"
# every spend nets the script UTxO's value back to ADDR minus fee; exact out value is set by the
# node's balance check — we self-send the whole spent input minus fee (script UTxOs are ~10 ADA;
# use --tx-out with the input's value). Query is done at build time for the real amount:
val() { docker exec refpair-cardano-ref cardano-cli conway query utxo --tx-in "$1" $M --socket-path /state/db/node.socket --output-json | python3 -c "import json,sys;d=json.load(sys.stdin);print(list(d.values())[0]['value']['lovelace'])"; }
sign() { $CLI conway transaction sign --tx-body-file "$1.raw" --signing-key-file keys/payment.skey $M --out-file "$1.tx"; }

# witness-script spend of an inline-datum UTxO
wit_inline() { local n=$1 utxo=$2 script=$3 valid=$4; local v=$(val "$utxo")
  $CLI conway transaction build-raw $valid --tx-in "$utxo" --tx-in-script-file "$script" \
    --tx-in-inline-datum-present --tx-in-redeemer-file unit.json --tx-in-execution-units "$EXU" \
    --tx-in-collateral $COLL --tx-out "$ADDR+$((v-FEE))" --fee $FEE --protocol-params-file pparams.json --out-file "$n.raw"; sign "$n"; }
# reference-script spend of an inline-datum UTxO (script from C)
ref_inline() { local n=$1 utxo=$2 refc=$3 valid=$4 extra=${5:-}; local v=$(val "$utxo")
  $CLI conway transaction build-raw $valid --tx-in "$utxo" --spending-tx-in-reference "$refc" --spending-plutus-script-v3 \
    --spending-reference-tx-in-inline-datum-present --spending-reference-tx-in-redeemer-file unit.json \
    --spending-reference-tx-in-execution-units "$EXU" $extra \
    --tx-in-collateral $COLL --tx-out "$ADDR+$((v-FEE))" --fee $FEE --protocol-params-file pparams.json --out-file "$n.raw"; sign "$n"; }
# witness-script spend of a datum-HASH UTxO; datum supplied unless $5=omit
wit_hash() { local n=$1 utxo=$2 script=$3 mode=$4; local v=$(val "$utxo"); local d=(--tx-in-datum-file unit.json); [ "$mode" = omit ] && d=()
  $CLI conway transaction build-raw --script-valid --tx-in "$utxo" --tx-in-script-file "$script" \
    "${d[@]}" --tx-in-redeemer-file unit.json --tx-in-execution-units "$EXU" \
    --tx-in-collateral $COLL --tx-out "$ADDR+$((v-FEE))" --fee $FEE --protocol-params-file pparams.json --out-file "$n.raw"; sign "$n"; }
ref_hash() { local n=$1 utxo=$2 refc=$3; local v=$(val "$utxo")
  $CLI conway transaction build-raw --script-valid --tx-in "$utxo" --spending-tx-in-reference "$refc" --spending-plutus-script-v3 \
    --spending-reference-tx-in-datum-file unit.json --spending-reference-tx-in-redeemer-file unit.json --spending-reference-tx-in-execution-units "$EXU" \
    --tx-in-collateral $COLL --tx-out "$ADDR+$((v-FEE))" --fee $FEE --protocol-params-file pparams.json --out-file "$n.raw"; sign "$n"; }

wit_inline spendA-witness            "$A_INLINE" ok.plutus   --script-valid
ref_inline spendA-refscript          "$A_INLINE" "$C_OK"     --script-valid
wit_hash   spendB-hash-witness       "$B_HASH"   ok.plutus   supply
ref_hash   spendB-refscript          "$B_HASH"   "$C_OK"
wit_hash   spendB-missing-datum      "$B_HASH"   ok.plutus   omit
wit_inline spendD-fail-valid         "$D_FAIL"   fail.plutus --script-valid
wit_inline spendD-fail-invalid       "$D_FAIL"   fail.plutus --script-invalid
ref_inline spendA-wrong-refscript    "$A_INLINE" "$C_FAIL"   --script-valid            # C_FAIL script != A's addr hash
ref_inline spendA-refscript-also-refinput "$A_INLINE" "$C_OK" --script-valid "--read-only-tx-in-reference $C_OK"

EXU="$EXU" python3 "$HERE/manifest.py"
echo "built 9 cases"
