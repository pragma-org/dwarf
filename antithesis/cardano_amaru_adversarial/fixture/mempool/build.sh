#!/usr/bin/env bash
# Rebuild the mempool / submit-path phase-1 differential corpus (testnet-only, no value).
#
# mp-base.tx (cardano-cli) spends the committed funding UTxO 9708b921...#0 with fee 1 ADA (>> the
# 876 277 minimum at 16384 bytes), no validity interval, and a small metadata entry. edits.py pads
# it to the exact max-tx-size boundary in canonical and non-canonical encodings. mp-base.tx itself
# is the duplicate-resubmission case. Violations are idempotent; accepts are single-use.
#
# Usage: fixture/mempool/build.sh   (cardano-cli from $CARDANO_CLI_IMAGE; python3 with cbor2 +
#        cryptography from $PYTHON)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
FUND="$HERE/../funding"
IMAGE="${CARDANO_CLI_IMAGE:-cnode-p1:local}"
PY="${PYTHON:-python3}"
cd "$HERE"; mkdir -p keys
cp "$FUND/payment.skey" keys/payment.skey
trap 'rm -f keys/payment.skey *.raw; rmdir keys 2>/dev/null || true' EXIT
CLI="docker run --rm -u $(id -u):$(id -g) -v $HERE:/w -w /w --entrypoint cardano-cli $IMAGE"

IN=9708b921e619b34a6fafc375ebd46bf65d77eed427f953eabed2b9da9212c4a1#0
TOTAL=200000000000000; FEE=1000000
ADDR=addr_test1vrj6thds8l3hqktz0lk7w9zcgq0qmktmkdcm80tuecxjxfcjg8ye6

printf '{"674":{"msg":["dwarf-mempool"]}}\n' > mp-base.json
$CLI conway transaction build-raw --tx-in $IN --tx-out "$ADDR+$((TOTAL - FEE))" --fee $FEE \
  --metadata-json-file mp-base.json --out-file mp-base.raw
$CLI conway transaction sign --tx-body-file mp-base.raw --signing-key-file keys/payment.skey \
  --testnet-magic 42 --out-file mp-base.tx

"$PY" edits.py
IN=$IN "$PY" manifest.py
