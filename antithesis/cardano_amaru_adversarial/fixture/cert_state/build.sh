#!/usr/bin/env bash
# Probe certificate state-transition edges (intra-tx cert sequences, fresh creds) on pair4.
set -euo pipefail
P=/tmp/nsprobe/cert; mkdir -p $P/c $P/tx; cd $P
A=/tmp/plutus-wt/antithesis/cardano_amaru_adversarial
cp $A/fixture/funding/payment.skey .
cp $A/fixture/stake_pool/keys/stake.vkey $A/fixture/stake_pool/keys/stake.skey .
cp $A/fixture/stake_pool/keys/owner.vkey $A/fixture/stake_pool/keys/owner.skey .
CLI="docker run --rm -u $(id -u):$(id -g) -v $P:/w -w /w --entrypoint cardano-cli cnode-p1:local"
IN=9708b921e619b34a6fafc375ebd46bf65d77eed427f953eabed2b9da9212c4a1#0
TOTAL=200000000000000; FEE=300000; DEP=2000000
ADDR=addr_test1vrj6thds8l3hqktz0lk7w9zcgq0qmktmkdcm80tuecxjxfcjg8ye6; M="--testnet-magic 42"
NODREP=deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef   # 28-byte nonexistent DRep key-hash
$CLI conway stake-address registration-certificate   --stake-verification-key-file stake.vkey --key-reg-deposit-amt $DEP --out-file c/reg.cert
$CLI conway stake-address deregistration-certificate --stake-verification-key-file stake.vkey --key-reg-deposit-amt $DEP --out-file c/dereg.cert
$CLI conway stake-address deregistration-certificate --stake-verification-key-file stake.vkey --key-reg-deposit-amt 1000000 --out-file c/dereg-wrong.cert
$CLI conway stake-address registration-and-vote-delegation-certificate --stake-verification-key-file stake.vkey --drep-key-hash $NODREP --key-reg-deposit-amt $DEP --out-file c/regvote-nodrep.cert
sign() { local n=$1; shift; local ks=(); for k in "$@"; do ks+=(--signing-key-file "$k.skey"); done
  $CLI conway transaction sign --tx-body-file tx/$n.raw "${ks[@]}" $M --out-file tx/$n.tx; rm -f tx/$n.raw; }
# tx <name> <out_delta_from_TOTAL_minus_FEE> <cert-args...>
mk() { local n=$1 outadj=$2; shift 2
  $CLI conway transaction build-raw --tx-in $IN --tx-out "$ADDR+$((TOTAL-FEE+outadj))" --fee $FEE "$@" --out-file tx/$n.raw; }
# dereg unregistered fresh cred (refund would be +DEP if it were registered; it is not)
mk dereg-unregistered 0 --certificate-file c/dereg.cert; sign dereg-unregistered payment stake
# reg + reg same cred (second cert hits already-registered); one deposit charged
mk reg-reg-samecred $((-DEP)) --certificate-file c/reg.cert --certificate-file c/reg.cert; sign reg-reg-samecred payment stake
# reg + dereg same cred, deposit charged then refunded (net 0)
mk reg-dereg-samecred 0 --certificate-file c/reg.cert --certificate-file c/dereg.cert; sign reg-dereg-samecred payment stake
# reg deposit 2, dereg refund 1 (wrong): net charge 1
mk reg-dereg-wrong-refund $((-DEP+1000000)) --certificate-file c/reg.cert --certificate-file c/dereg-wrong.cert; sign reg-dereg-wrong-refund payment stake
# reg + vote-deleg to a nonexistent DRep
mk regvote-nodrep $((-DEP)) --certificate-file c/regvote-nodrep.cert; sign regvote-nodrep payment stake
rm -f payment.skey stake.skey owner.skey
ls tx/*.tx | wc -l
