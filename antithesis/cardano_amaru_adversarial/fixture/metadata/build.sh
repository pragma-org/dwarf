#!/usr/bin/env bash
# Rebuild the metadata-validation phase-1 differential corpus (testnet-only, no value).
#
# md-base.tx (cardano-cli) spends the committed funding UTxO 9708b921...#0 with fee 300000 >> min,
# no validity interval, and metadata {674: {"msg": ["hello"]}} in the Alonzo aux form. Every other
# case is derived from it by edits.py (raw aux bytes + re-signed body), so a rejection is
# attributable to the metadata / auxiliary-data-hash rule under test. Violations are idempotent;
# controls are single-use (an accept consumes the funding UTxO).
#
# Usage: fixture/metadata/build.sh   (cardano-cli from $CARDANO_CLI_IMAGE; python3 with cbor2 +
#        cryptography from $PYTHON)
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
TOTAL=200000000000000; FEE=300000
ADDR=addr_test1vrj6thds8l3hqktz0lk7w9zcgq0qmktmkdcm80tuecxjxfcjg8ye6

printf '{"674":{"msg":["hello"]}}\n' > md-base.json
$CLI conway transaction build-raw --tx-in $IN --tx-out "$ADDR+$((TOTAL - FEE))" --fee $FEE \
  --metadata-json-file md-base.json --out-file md-base.raw
$CLI conway transaction sign --tx-body-file md-base.raw --signing-key-file keys/payment.skey \
  --testnet-magic 42 --out-file md-base.tx

"$PY" edits.py
IN=$IN "$PY" manifest.py
