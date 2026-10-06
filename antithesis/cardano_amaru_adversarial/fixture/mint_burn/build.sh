#!/usr/bin/env bash
# Rebuild the mint/burn + multi-asset value phase-1 differential corpus (testnet-only keys, no value).
#
# Every case spends the committed funding UTxO 9708b921...#0 (signed by the committed payment key)
# with a fee well above the minimum and no validity interval, and every minting case is signed by
# the policy key, so a rejection is attributable to the mint / multi-asset value rule under test.
# ADA is always balanced exactly; only the multi-asset side (or the output rule) is wrong.
# Violations are idempotent; controls are single-use (an accept consumes the funding UTxO in that
# node's mempool).
#
# A VALID BURN control is infeasible on this substrate: the frozen references never forge, so no
# token UTxO can confirm, and the mint map carries one net quantity per asset (no same-tx mint and
# burn). Burn semantics are covered by the rejects burn-nonexistent / burn-int64-min.
#
# Usage: fixture/mint_burn/build.sh   (cardano-cli from $CARDANO_CLI_IMAGE; python3 with cbor2 +
#        cryptography from $PYTHON for the edits cardano-cli cannot express, see edits.py)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
FUND="$HERE/../funding"
IMAGE="${CARDANO_CLI_IMAGE:-cnode-p1:local}"
PY="${PYTHON:-python3}"
cd "$HERE"; mkdir -p keys
cp "$FUND/payment.skey" keys/payment.skey
trap 'rm -f keys/payment.skey *.raw' EXIT
CLI="docker run --rm -u $(id -u):$(id -g) -v $HERE:/w -w /w --entrypoint cardano-cli $IMAGE"

IN=9708b921e619b34a6fafc375ebd46bf65d77eed427f953eabed2b9da9212c4a1#0
TOTAL=200000000000000; FEE=300000; FEE_BIG=1000000
ADDR=addr_test1vrj6thds8l3hqktz0lk7w9zcgq0qmktmkdcm80tuecxjxfcjg8ye6
M="--testnet-magic 42"

# Policy keys: reuse committed ones if present, otherwise generate (manifest.py re-records ids).
for n in policy other; do [ -f keys/$n.skey ] || $CLI address key-gen \
  --verification-key-file keys/$n.vkey --signing-key-file keys/$n.skey; done
for n in policy other; do
  printf '{"type":"all","scripts":[{"type":"sig","keyHash":"%s"}]}\n' \
    "$($CLI address key-hash --payment-verification-key-file keys/$n.vkey)" > $n.script
done
POL=$($CLI conway transaction policyid --script-file policy.script)
OTH=$($CLI conway transaction policyid --script-file other.script)
A=$POL.4d494e54; B=$POL.4d494e5442; X=$POL.4d494e5458; Q=$OTH.4f54484552  # MINT MINTB MINTX OTHER

raw() { local n=$1; shift; $CLI conway transaction build-raw --tx-in $IN "$@" --out-file "$n.raw"; }
sign() { local n=$1; shift; local ks=(--signing-key-file keys/payment.skey)
  for k in "$@"; do ks+=(--signing-key-file "keys/$k.skey"); done
  $CLI conway transaction sign --tx-body-file "$n.raw" "${ks[@]}" $M --out-file "$n.tx"; }
out() { echo "$ADDR+$((TOTAL - ${2:-$FEE}))${1:+ + $1}"; }   # single output, ADA balanced
MS=(--mint-script-file policy.script)

# --- satisfied controls (expected accept; single-use) ---
raw mint-valid --tx-out "$(out "10 $A")" --fee $FEE --mint "10 $A" "${MS[@]}" ; sign mint-valid policy
raw multiasset-mint-valid --tx-out "$(out "10 $A + 3 $B")" --fee $FEE --mint "10 $A + 3 $B" "${MS[@]}"
sign multiasset-mint-valid policy

# --- multi-asset min-ADA boundary: token output at exactly the min (accept) and min-1 (reject) ---
MIN=$($CLI conway transaction calculate-min-required-utxo --protocol-params-file pparams.json \
  --tx-out "$ADDR+1000000+1 $A" | awk '{print $2}')
for c in "minada-asset-at-min $MIN" "minada-asset-below $((MIN - 1))"; do set -- $c
  raw $1 --tx-out "$ADDR+$2+1 $A" --tx-out "$ADDR+$((TOTAL - FEE - $2))" --fee $FEE --mint "1 $A" "${MS[@]}"
  sign $1 policy; done

# --- asset value not preserved (ADA exact) ---
raw asset-surplus  --tx-out "$(out "11 $A")" --fee $FEE --mint "10 $A" "${MS[@]}"; sign asset-surplus policy
raw asset-deficit  --tx-out "$(out "9 $A")"  --fee $FEE --mint "10 $A" "${MS[@]}"; sign asset-deficit policy
raw asset-relabel  --tx-out "$(out "10 $X")" --fee $FEE --mint "10 $A" "${MS[@]}"; sign asset-relabel policy
raw unminted-policy-output --tx-out "$(out "10 $Q")" --fee $FEE; sign unminted-policy-output

# --- burn edges ---
raw burn-nonexistent --tx-out "$(out)" --fee $FEE --mint "-5 $A" "${MS[@]}"; sign burn-nonexistent policy

# --- output value > maxValueSize 5000: 150 x 32-byte asset names in one output ---
BIG=""; for i in $(seq 1 150); do BIG="$BIG${BIG:+ + }1 $POL.$(printf '%064x' $i)"; done
raw value-too-big --tx-out "$ADDR+100000000+$BIG" --tx-out "$ADDR+$((TOTAL - FEE_BIG - 100000000))" \
  --fee $FEE_BIG --mint "$BIG" "${MS[@]}"; sign value-too-big policy

# --- edits cardano-cli cannot express (script-witness and decode edges) ---
"$PY" edits.py

POL=$POL OTH=$OTH MIN=$MIN IN=$IN "$PY" manifest.py
