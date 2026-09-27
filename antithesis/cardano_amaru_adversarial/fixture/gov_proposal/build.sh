#!/usr/bin/env bash
# Rebuild the governance-PROPOSAL phase-1 differential corpus (testnet-only keys, no value).
#
# Every case spends the committed funding UTxO 9708b921...#0 with fee 300000 >> min and no
# validity interval. The substrate has NO registered reward account usable as a proposal return
# account (genesis delegator / pool reward accounts: "does not exist" on both nodes), so every case
# REGISTERS its return account in the same tx: both ledgers run GOV after CERTS (probed: stake-reg
# + InfoAction accepted by both, same tx id). A rejection is then attributable to the proposal
# rule under test. Gov state at the freeze: pv 10.0, 0 proposals, every root null, a 7-member
# script-hash committee, constitution guardrails script fa24fb30.
# Violations are idempotent; controls are single-use (an accept consumes the funding UTxO).
#
# Usage: fixture/gov_proposal/build.sh   (cardano-cli from $CARDANO_CLI_IMAGE; python3 with cbor2 +
#        cryptography from $PYTHON for the anchor decode edges, see edits.py)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
FUND="$HERE/../funding"
IMAGE="${CARDANO_CLI_IMAGE:-cnode-p1:local}"
PY="${PYTHON:-python3}"
cd "$HERE"; mkdir -p keys actions
cp "$FUND/payment.skey" keys/payment.skey
trap 'rm -f keys/payment.skey *.raw' EXIT
CLI="docker run --rm -u $(id -u):$(id -g) -v $HERE:/w -w /w --entrypoint cardano-cli $IMAGE"

IN=9708b921e619b34a6fafc375ebd46bf65d77eed427f953eabed2b9da9212c4a1#0
TOTAL=200000000000000; FEE=300000
DEP=100000000000; SDEP=2000000   # govActionDeposit, stakeAddressDeposit (baked pparams)
ADDR=addr_test1vrj6thds8l3hqktz0lk7w9zcgq0qmktmkdcm80tuecxjxfcjg8ye6
M="--testnet-magic 42"
GUARD=fa24fb305126805cf2164c161d852a0e7330cf988f1fe558cf7d4a64    # constitution guardrails script
CC_MEMBER=349e55f83e9af24813e6cb368df6a80d38951b2a334dfcdf26815558 # sitting committee member (script)
FAKE=$(printf 'dwarf-nonexistent-gov-action' | b2sum -l 256 | cut -d' ' -f1)  # never a tx id
AH=$(printf 'dwarf-anchor' | b2sum -l 256 | cut -d' ' -f1)
ANCHOR=(--anchor-url https://dwarf.example/p --anchor-data-hash "$AH")

for n in ret unreg; do [ -f keys/$n.skey ] || $CLI conway stake-address key-gen \
  --verification-key-file keys/$n.vkey --signing-key-file keys/$n.skey; done
[ -f keys/cc.skey ] || $CLI conway governance committee key-gen-cold \
  --cold-verification-key-file keys/cc.vkey --cold-signing-key-file keys/cc.skey
RET=$($CLI conway stake-address key-hash --stake-verification-key-file keys/ret.vkey)
UNREG=$($CLI conway stake-address key-hash --stake-verification-key-file keys/unreg.vkey)
CC=$($CLI conway governance committee key-hash --verification-key-file keys/cc.vkey)
$CLI conway stake-address registration-certificate --stake-verification-key-file keys/ret.vkey \
  --key-reg-deposit-amt $SDEP --out-file actions/reg-ret.cert

GA="$CLI conway governance action"
ret=(--deposit-return-stake-verification-key-file keys/ret.vkey)
act() { local n=$1 kind=$2; shift 2; $GA $kind --testnet "$@" "${ANCHOR[@]}" --out-file actions/$n.action; }
# --- actions ---
act info             create-info --governance-action-deposit $DEP "${ret[@]}"
act info-dep-low     create-info --governance-action-deposit $((DEP - 1)) "${ret[@]}"
act info-dep-high    create-info --governance-action-deposit $((DEP + 1)) "${ret[@]}"
act info-unreg       create-info --governance-action-deposit $DEP --deposit-return-stake-verification-key-file keys/unreg.vkey
hf() { act "$1" create-hardfork --governance-action-deposit $DEP "${ret[@]}" --protocol-major-version "$2" \
  --protocol-minor-version "$3" "${@:4}"; }
hf hf-11-0 11 0
hf hf-10-1 10 1
hf hf-12-0 12 0
hf hf-11-1 11 1
hf hf-10-0 10 0
hf hf-prev-fake 11 0 --prev-governance-action-tx-id "$FAKE" --prev-governance-action-index 0
act noconf-prev-fake create-no-confidence --governance-action-deposit $DEP "${ret[@]}" \
  --prev-governance-action-tx-id "$FAKE" --prev-governance-action-index 0
act const-prev-fake create-constitution --governance-action-deposit $DEP "${ret[@]}" \
  --constitution-url https://dwarf.example/c --constitution-hash "$AH" --constitution-script-hash $GUARD \
  --prev-governance-action-tx-id "$FAKE" --prev-governance-action-index 0
cc() { act "$1" update-committee --governance-action-deposit $DEP "${ret[@]}" --threshold 2/3 "${@:2}"; }
cc cc-add-e50       --add-cc-cold-verification-key-hash $CC --epoch 50
cc cc-add-e1        --add-cc-cold-verification-key-hash $CC --epoch 1
cc cc-conflicting   --add-cc-cold-script-hash $CC_MEMBER --epoch 50 --remove-cc-cold-script-hash $CC_MEMBER
act pp-no-policy    create-protocol-parameters-update --governance-action-deposit $DEP "${ret[@]}" \
  --max-tx-size 16385
act pp-wrong-policy create-protocol-parameters-update --governance-action-deposit $DEP "${ret[@]}" \
  --max-tx-size 16385 --constitution-script-hash "$(printf '%056d' 0)"
act pp-zero-no-policy create-protocol-parameters-update --governance-action-deposit $DEP "${ret[@]}" \
  --max-tx-size 0
act tw-no-policy    create-treasury-withdrawal --governance-action-deposit $DEP "${ret[@]}" \
  --funds-receiving-stake-verification-key-file keys/ret.vkey --transfer 1000000

# --- transactions: tx <case> <total-deposit> [--certificate-file ...] --proposal-file ... ---
tx() { local n=$1 dep=$2; shift 2
  $CLI conway transaction build-raw --tx-in $IN --tx-out "$ADDR+$((TOTAL - FEE - dep))" --fee $FEE \
    "$@" --out-file "$n.raw"
  $CLI conway transaction sign --tx-body-file "$n.raw" --signing-key-file keys/payment.skey \
    --signing-key-file keys/ret.skey $M --out-file "$n.tx"; }
reg=(--certificate-file actions/reg-ret.cert)
p() { tx "$1" $((${3:-$DEP} + SDEP)) "${reg[@]}" --proposal-file "actions/$2.action"; }
# violations (expected reject)
p deposit-low          info-dep-low $((DEP - 1))     # balanced to the DECLARED deposit
p deposit-high         info-dep-high $((DEP + 1))
tx return-unregistered $DEP --proposal-file actions/info-unreg.action
p hardfork-prev-nonexistent hf-prev-fake
p noconfidence-prev-nonexistent noconf-prev-fake
p constitution-prev-nonexistent const-prev-fake
p hardfork-skip-major  hf-12-0
p hardfork-minor-on-major-bump hf-11-1
p hardfork-same-version hf-10-0
p committee-expiry-too-small cc-add-e1
p committee-conflicting cc-conflicting
p ppupdate-no-policy   pp-no-policy
p ppupdate-wrong-policy pp-wrong-policy
p ppupdate-malformed-zero pp-zero-no-policy
p treasury-no-policy   tw-no-policy
# satisfied controls (expected accept; single-use)
p info-valid           info
p hardfork-major-valid hf-11-0
p hardfork-minor-valid hf-10-1
p committee-add-valid  cc-add-e50
tx two-proposals-valid $((2 * DEP + SDEP)) "${reg[@]}" --proposal-file actions/info.action \
  --proposal-file actions/hf-11-0.action

# --- anchor decode edges + mainnet return account: cardano-cli cannot express (see edits.py) ---
"$PY" edits.py

RET=$RET UNREG=$UNREG FAKE=$FAKE IN=$IN DEP=$DEP GUARD=$GUARD "$PY" manifest.py
