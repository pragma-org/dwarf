#!/usr/bin/env bash
# Rebuild the Plutus phase-2 differential corpus (testnet-only, no value).
#
# Every case mints 1 token under a PlutusV3 policy and is PHASE-1-VALID (valid collateral,
# script-integrity hash computed from pparams.json, redeemer present, declared ex-units <=
# maxTxExUnits, fee 400001 >> min, no validity interval), so any rejection is unambiguously
# PHASE-2 -- a mismatch between the tx's claimed is_valid tag and the Plutus VM's actual result.
#
# The ex-unit knife-edge (exu-exact) needs the EXACT true cost of the always-succeeds mint, which
# only the node can tell us. Pass it in (cardano-cli order is steps,mem):
#   EXACT_STEPS=<n> EXACT_MEM=<n> fixture/plutus/build.sh
# Measure it live on the pair first (see measure-exact-cost below the script).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
FUND="$HERE/../funding"
IMAGE="${CARDANO_CLI_IMAGE:-cnode-p1:local}"
: "${EXACT_STEPS:?set EXACT_STEPS from calculate-plutus-script-cost}"
: "${EXACT_MEM:?set EXACT_MEM from calculate-plutus-script-cost}"
cd "$HERE"; mkdir -p keys
cp "$FUND/payment.skey" "$FUND/payment.vkey" keys/
trap 'rm -f keys/payment.skey keys/payment.vkey *.raw' EXIT
CLI="docker run --rm -u $(id -u):$(id -g) -v $HERE:/w -w /w --entrypoint cardano-cli $IMAGE"

IN=9708b921e619b34a6fafc375ebd46bf65d77eed427f953eabed2b9da9212c4a1#0
TOTAL=200000000000000; FEE=400001; MINCOLL=600002   # ceil(1.5*fee)
ADDR=addr_test1vrj6thds8l3hqktz0lk7w9zcgq0qmktmkdcm80tuecxjxfcjg8ye6
M="--testnet-magic 42"
OKPOL=$($CLI conway transaction policyid --script-file ok.plutus)
FAILPOL=$($CLI conway transaction policyid --script-file fail.plutus)
AMPLE="(500000000,2000000)"                          # >> exact, << maxTxExUnits (14e9,14e6)

# tx <case> <validflag> <script> <policy> <exu> <expected>   (mint 1 token + redeemer + collateral)
tx() {
  local n=$1 vf=$2 scr=$3 pol=$4 exu=$5
  local tok="1 $pol.434f4c4c"
  $CLI conway transaction build-raw $vf --tx-in $IN --tx-in-collateral $IN \
    --tx-out "$ADDR+$((TOTAL - FEE))+$tok" --fee $FEE \
    --mint "$tok" --mint-script-file "$scr" --mint-redeemer-file unit.json --mint-execution-units "$exu" \
    --protocol-params-file pparams.json --out-file "$n.raw"
  $CLI conway transaction sign --tx-body-file "$n.raw" --signing-key-file keys/payment.skey $M --out-file "$n.tx"
}

# is_valid=true, always-succeeds: budget edges
tx exu-exact                --script-valid   ok.plutus   "$OKPOL"   "($EXACT_STEPS,$EXACT_MEM)"
tx exu-under-steps          --script-valid   ok.plutus   "$OKPOL"   "($((EXACT_STEPS-1)),$EXACT_MEM)"
tx exu-under-mem            --script-valid   ok.plutus   "$OKPOL"   "($EXACT_STEPS,$((EXACT_MEM-1)))"
tx exu-zero                 --script-valid   ok.plutus   "$OKPOL"   "(0,0)"
tx exu-ample                --script-valid   ok.plutus   "$OKPOL"   "$AMPLE"
# is_valid flag paths (always-succeeds)
tx isvalid-false-succeeds   --script-invalid ok.plutus   "$OKPOL"   "$AMPLE"
tx isvalid-false-underbudget --script-invalid ok.plutus  "$OKPOL"   "(0,0)"
# always-fails script, both flags
tx alwaysfails-valid        --script-valid   fail.plutus "$FAILPOL" "$AMPLE"
tx alwaysfails-invalid      --script-invalid fail.plutus "$FAILPOL" "$AMPLE"

EXACT_STEPS=$EXACT_STEPS EXACT_MEM=$EXACT_MEM python3 "$HERE/manifest.py"
echo "built 9 cases with exact=($EXACT_STEPS,$EXACT_MEM) ample=$AMPLE"
