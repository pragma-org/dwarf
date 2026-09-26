#!/usr/bin/env bash
# Rebuild the stake/pool/withdrawal phase-1 differential corpus (testnet-only keys, no value).
#
# Every case spends the committed funding UTxO 9708b921...#0 with fee 300000 >> min and no
# validity interval, so a rejection is attributable to the certificate/withdrawal rule under
# test, not to fee/size/validity. Violations are idempotent; controls are single-use (an
# accept consumes the funding UTxO in that node's mempool).
#
# Usage: fixture/stake_pool/build.sh            (uses cardano-cli from $CARDANO_CLI_IMAGE)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
FUND="$HERE/../funding"
IMAGE="${CARDANO_CLI_IMAGE:-cnode-p1:local}"
cd "$HERE"; mkdir -p certs keys
cp "$FUND/payment.skey" keys/payment.skey
trap 'rm -f keys/payment.skey' EXIT
CLI="docker run --rm -u $(id -u):$(id -g) -v $HERE:/w -w /w --entrypoint cardano-cli $IMAGE"

IN=9708b921e619b34a6fafc375ebd46bf65d77eed427f953eabed2b9da9212c4a1#0
TOTAL=200000000000000; FEE=300000
ADDR=addr_test1vrj6thds8l3hqktz0lk7w9zcgq0qmktmkdcm80tuecxjxfcjg8ye6
M="--testnet-magic 42"
STAKE_DEP=2000000; POOL_DEP=500000000; MIN_POOL_COST=340000000   # baked pparams
GEN_POOL=5801a7637e0273804ebcc720c8a11a80c05cb5d594836160ca51cbf8   # genesis pool (registered)
GEN_DELEG=3e521ccc7cb396438c191029b02acbe25f6d48f09bcdd990d24171b0  # genesis delegator (registered, no key)

# Keys: reuse committed ones if present; otherwise (e.g. a public bundle that ships no .skey)
# generate fresh ones. Fresh keys change the credentials and tx bytes; manifest.py re-records both.
for n in stake owner wrong; do [ -f keys/$n.skey ] || $CLI conway stake-address key-gen \
  --verification-key-file keys/$n.vkey --signing-key-file keys/$n.skey; done
[ -f keys/cold.skey ] || { $CLI conway node key-gen --cold-verification-key-file keys/cold.vkey \
  --cold-signing-key-file keys/cold.skey --operational-certificate-issue-counter-file keys/cold.counter
  rm -f keys/cold.counter; }
[ -f keys/vrf.skey ] || $CLI conway node key-gen-VRF --verification-key-file keys/vrf.vkey \
  --signing-key-file keys/vrf.skey

POOLID=$($CLI conway stake-pool id --cold-verification-key-file keys/cold.vkey --output-format hex)
STAKE_HASH=$($CLI conway stake-address key-hash --stake-verification-key-file keys/stake.vkey)
OWNER_HASH=$($CLI conway stake-address key-hash --stake-verification-key-file keys/owner.vkey)
$CLI conway stake-address build --stake-verification-key-file keys/stake.vkey $M --out-file certs/stake.addr
$CLI conway stake-address build --stake-key-hash $GEN_DELEG $M --out-file certs/gendeleg.addr

# --- certificates ---
$CLI conway stake-address registration-certificate --stake-verification-key-file keys/stake.vkey \
  --key-reg-deposit-amt $STAKE_DEP --out-file certs/reg.cert
$CLI conway stake-address registration-certificate --stake-verification-key-file keys/stake.vkey \
  --key-reg-deposit-amt $((STAKE_DEP / 2)) --out-file certs/reg-baddep.cert
$CLI conway stake-address registration-and-vote-delegation-certificate --stake-verification-key-file keys/stake.vkey \
  --always-abstain --key-reg-deposit-amt $STAKE_DEP --out-file certs/regvote.cert
$CLI conway stake-address registration-and-delegation-certificate --stake-verification-key-file keys/stake.vkey \
  --stake-pool-id $GEN_POOL --key-reg-deposit-amt $STAKE_DEP --out-file certs/regdeleg-genpool.cert
$CLI conway stake-address registration-and-delegation-certificate --stake-verification-key-file keys/stake.vkey \
  --stake-pool-id $POOLID --key-reg-deposit-amt $STAKE_DEP --out-file certs/regdeleg-nopool.cert
# Legacy Shelley stake_registration (cert tag 0, no deposit field): still valid in Conway and,
# per the ledger, requires NO stake-credential witness. cardano-cli conway only emits tag 7, so
# the 33-byte cert is written directly: [0, [0, keyhash]].
printf '{"type":"CertificateConway","description":"legacy stake_registration (tag 0)","cborHex":"82008200581c%s"}\n' \
  "$STAKE_HASH" > certs/reg-legacy.cert
pool() { # out cost network-flag
  $CLI conway stake-pool registration-certificate --cold-verification-key-file keys/cold.vkey \
    --vrf-verification-key-file keys/vrf.vkey --pool-pledge 0 --pool-cost "$2" --pool-margin 0 \
    --pool-reward-account-verification-key-file keys/stake.vkey \
    --pool-owner-stake-verification-key-file keys/owner.vkey \
    --single-host-pool-relay ns.example --pool-relay-port 3001 $3 --out-file "$1"; }
pool certs/poolreg.cert $MIN_POOL_COST "$M"
pool certs/poolreg-lowcost.cert $((MIN_POOL_COST - 1)) "$M"
pool certs/poolreg-mainnet.cert $MIN_POOL_COST "--mainnet"

# --- transactions: tx <case> <deposit> <withdrawal-args> <cert> <signers...> ---
tx() {
  local n=$1 dep=$2 wd=$3 cert=$4; shift 4
  local c=(); [ -n "$cert" ] && c=(--certificate-file "certs/$cert")
  $CLI conway transaction build-raw --tx-in $IN --tx-out $ADDR+$((TOTAL - FEE - dep)) --fee $FEE \
    $wd "${c[@]}" --out-file "$n.raw"
  local ks=(); for k in "$@"; do ks+=(--signing-key-file "keys/$k.skey"); done
  $CLI conway transaction sign --tx-body-file "$n.raw" "${ks[@]}" $M --out-file "$n.tx"
  rm -f "$n.raw"; }
# violations (expected reject)
tx stakereg-missing-witness   $STAKE_DEP "" reg.cert payment
tx stakereg-wrong-key         $STAKE_DEP "" reg.cert payment wrong
tx stakereg-bad-deposit       $((STAKE_DEP / 2)) "" reg-baddep.cert payment stake
tx regvote-missing-witness    $STAKE_DEP "" regvote.cert payment
tx regdeleg-missing-witness   $STAKE_DEP "" regdeleg-genpool.cert payment
tx regdeleg-unknown-pool      $STAKE_DEP "" regdeleg-nopool.cert payment stake
tx poolreg-missing-cold       $POOL_DEP "" poolreg.cert payment owner
tx poolreg-missing-owner      $POOL_DEP "" poolreg.cert payment cold
tx poolreg-cost-too-low       $POOL_DEP "" poolreg-lowcost.cert payment cold owner
tx poolreg-wrong-network      $POOL_DEP "" poolreg-mainnet.cert payment cold owner
tx wdrl-unregistered          0 "--withdrawal $(cat certs/stake.addr)+0" "" payment stake
tx wdrl-genesis-no-witness    0 "--withdrawal $(cat certs/gendeleg.addr)+0" "" payment
# satisfied controls (expected accept; single-use)
tx stakereg-present           $STAKE_DEP "" reg.cert payment stake
tx stakereg-legacy-no-witness $STAKE_DEP "" reg-legacy.cert payment
tx regvote-present            $STAKE_DEP "" regvote.cert payment stake
tx poolreg-present            $POOL_DEP "" poolreg.cert payment cold owner
STAKE_HASH=$STAKE_HASH OWNER_HASH=$OWNER_HASH POOLID=$POOLID GEN_DELEG=$GEN_DELEG IN=$IN \
  python3 "$HERE/manifest.py"
