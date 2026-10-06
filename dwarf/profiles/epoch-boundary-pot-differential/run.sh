#!/usr/bin/env bash
# Run the epoch-boundary pot-transition differential scenarios (treasury/reserves/fees Δ across an epoch roll).
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
echo "profile epoch-boundary-pot-differential: run the ledger-epoch-boundary-*-differential scenarios"
echo "see $HERE/README.md ; scenarios under dwarf/scenarios/"
