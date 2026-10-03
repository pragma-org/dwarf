#!/usr/bin/env bash
# Run the is_valid=false body-leak differential scenarios (value-creation/theft detector).
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
echo "profile invalid-tx-body-leak: run the ledger-isvalidfalse-body-leak-*-differential scenarios"
echo "see $HERE/README.md ; scenarios under dwarf/scenarios/"
