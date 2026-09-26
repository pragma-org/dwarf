#!/usr/bin/env bash
# Rebuild the collateral / redeemer phase-1 differential corpus (testnet-only keys, no value).
#
# Every case spends the committed funding UTxO 9708b921...#0 (signed by the committed payment
# key), mints 1 token under a PlutusV3 always-succeeds policy (so the tx carries a redeemer and
# the collateral rules apply) and pays fee 400001 >> min, with no validity interval. The
# script-integrity hash is computed from pparams.json, the live protocol parameters of the
# re-bake chain (cost models: PlutusV1 + PlutusV3 only; collateralPercentage 150;
# maxCollateralInputs 3; maxTxExUnits 14M/14G). Violations are idempotent; controls and P1/P2
# are single-use (an accept consumes the funding UTxO in that node's mempool).
#
# Foreign collateral uses genesis initialFunds UTxOs locked by keys DWARF does not hold; they are
# a function of the committed genesis (fixture/funding/genesis), like 9708b921#0.
#
# Usage: fixture/collateral/build.sh   (cardano-cli from $CARDANO_CLI_IMAGE; python3 with cbor2
#        from $PYTHON for the two redeemer cases the CLI cannot express)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
FUND="$HERE/../funding"
IMAGE="${CARDANO_CLI_IMAGE:-cnode-p1:local}"
PY="${PYTHON:-python3}"
cd "$HERE"; mkdir -p keys
cp "$FUND/payment.skey" "$FUND/payment.vkey" keys/
trap 'rm -f keys/payment.skey keys/payment.vkey *.raw' EXIT
CLI="docker run --rm -u $(id -u):$(id -g) -v $HERE:/w -w /w --entrypoint cardano-cli $IMAGE"

IN=9708b921e619b34a6fafc375ebd46bf65d77eed427f953eabed2b9da9212c4a1#0
TOTAL=200000000000000; FEE=400001            # ceil(1.5 * 400001) = 600002 minimum collateral
MINCOLL=600002
ADDR=addr_test1vrj6thds8l3hqktz0lk7w9zcgq0qmktmkdcm80tuecxjxfcjg8ye6
M="--testnet-magic 42"
# genesis initialFunds UTxOs locked by keys we do NOT hold
F1=0b1e73fecc5f488e3008b1738a6e35a9d7a0b1adf789f602a3247a164c23ec0a#0
F2=2cdeb5a33bb0eb50d8457a89896d4d7ec8c01a73c5194bf899b407ecf86db7d1#0
F3=5a629b8f7368715b5c90dadfd6cba5fc4ee2893395a84682a6d4afb125ba66bc#0
F4=5e6d88d8291c5d3f775aa557b3577c536e6a1b2518285c01be297c297dedf468#0
F1_KEYHASH=19b9a38fc0a2a5ac858598a998cf293f20cfd123e542c0e1f2fd67d1

POL=$($CLI conway transaction policyid --script-file ok.plutus)
TOKEN="1 $POL.434f4c4c"                                  # "COLL"
OUT="$ADDR+$((TOTAL - FEE))+$TOKEN"
MAINNET_ADDR=$($CLI address build --payment-verification-key-file keys/payment.vkey --mainnet)
EXU="(500000000,1000000)"   # cardano-cli order is (steps, memory): mem 1M, steps 500M

# tx <case> [extra build-raw args...]   (mint + redeemer + collateral IN unless overridden)
mint=(--mint "$TOKEN" --mint-script-file ok.plutus --mint-redeemer-file unit.json
      --mint-execution-units "$EXU")
raw() { local n=$1; shift; $CLI conway transaction build-raw --tx-in $IN "$@" --out-file "$n.raw"; }
sign() { $CLI conway transaction sign --tx-body-file "$1.raw" --signing-key-file keys/payment.skey \
  $M --out-file "$1.tx"; }
tx() { local n=$1; shift; raw "$n" "$@"; sign "$n"; }
std=(--tx-out "$OUT" --fee $FEE "${mint[@]}" --protocol-params-file pparams.json)

# --- violations (expected reject) ---
tx no-collateral              "${std[@]}"
tx insufficient-at-boundary   "${std[@]}" --tx-in-collateral $IN \
  --tx-out-return-collateral "$ADDR+$((TOTAL - MINCOLL + 1))" --tx-total-collateral $((MINCOLL - 1))
tx total-collateral-mismatch  "${std[@]}" --tx-in-collateral $IN \
  --tx-out-return-collateral "$ADDR+$((TOTAL - 700000))" --tx-total-collateral 699999
tx negative-asset-return      "${std[@]}" --tx-in-collateral $IN \
  --tx-out-return-collateral "$ADDR+$((TOTAL - 700000))+1 $POL.4e4547"
tx return-too-small           "${std[@]}" --tx-in-collateral $IN --tx-out-return-collateral "$ADDR+100"
tx return-wrong-network       "${std[@]}" --tx-in-collateral $IN \
  --tx-out-return-collateral "$MAINNET_ADDR+$((TOTAL - 700000))"
# (no unknown-collateral case: cardano-node reports it as BadInputsUTxO, which the shared
#  mixed_phase1 classifier treats as MASKED - funding input already consumed - by design.)
tx too-many-collateral        "${std[@]}" --tx-in-collateral $IN --tx-in-collateral $F2 \
  --tx-in-collateral $F3 --tx-in-collateral $F4
tx foreign-collateral-redeemer "${std[@]}" --tx-in-collateral $F1
tx exunits-too-big            --tx-out "$ADDR+$((TOTAL - 2000001))+$TOKEN" --fee 2000001 \
  --mint "$TOKEN" --mint-script-file ok.plutus --mint-redeemer-file unit.json \
  --mint-execution-units "(500000000,14000001)" --protocol-params-file pparams.json --tx-in-collateral $IN
# integrity hash computed from a cost model the chain does not have (V3 entry 0 + 1)
tx integrity-hash-mismatch    --tx-out "$OUT" --fee $FEE "${mint[@]}" \
  --protocol-params-file pparams-altered.json --tx-in-collateral $IN
# the CLI cannot drop or add a redeemer: edit the valid body, recompute the hash, re-sign
raw missing-redeemer          "${std[@]}" --tx-in-collateral $IN
raw extra-redeemer            "${std[@]}" --tx-in-collateral $IN
"$PY" "$HERE/redeemers.py" missing-redeemer.raw extra-redeemer.raw pparams.json
sign missing-redeemer; sign extra-redeemer

# --- satisfied controls (expected accept; single-use) ---
tx valid-minimal              "${std[@]}" --tx-in-collateral $IN
tx valid-with-return          "${std[@]}" --tx-in-collateral $IN \
  --tx-out-return-collateral "$ADDR+$((TOTAL - 700000))" --tx-total-collateral 700000
tx valid-at-boundary          "${std[@]}" --tx-in-collateral $IN \
  --tx-out-return-collateral "$ADDR+$((TOTAL - MINCOLL))" --tx-total-collateral $MINCOLL

# --- predicted-divergence probes (no scripts; single-use on a node that accepts) ---
# P1: a foreign-key UTxO named as collateral, its key never signs. The ledger's witness set is
# inputs ∪ collateral (babbageSpendableInputsTxBodyF), so a witness is required even with no
# redeemers.
tx p1-foreign-collateral-no-redeemers --tx-out "$ADDR+$((TOTAL - FEE))" --fee $FEE --tx-in-collateral $F1
# P1 counter-control: the same no-script tx WITHOUT the foreign collateral (both must accept), so
# the collateral witness is the only difference between P1 and an accepted tx.
tx p1-control-no-collateral --tx-out "$ADDR+$((TOTAL - FEE))" --fee $FEE
# P2: no redeemers, declared total_collateral disagrees with the return: the collateral balance
# checks apply only with redeemers, so both nodes should accept.
tx p2-total-mismatch-no-redeemers --tx-out "$ADDR+$((TOTAL - FEE))" --fee $FEE --tx-in-collateral $IN \
  --tx-out-return-collateral "$ADDR+$((TOTAL - 700000))" --tx-total-collateral 999

IN=$IN POL=$POL F1_KEYHASH=$F1_KEYHASH "$PY" "$HERE/manifest.py"
