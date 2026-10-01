#!/usr/bin/env bash
# Runner: run all 5 block-apply scenarios on the block-apply-adversary profile.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SCNS=(
  ledger-block-apply-stake-address-output-crash-differential-amaru-cardano-node
  ledger-block-apply-cert-phantom-deleg-unregistered-differential-amaru-cardano-node
  ledger-block-apply-collateral-foreign-unwitnessed-differential-amaru-cardano-node
  ledger-block-apply-donation-isvalid-false-treasury-credit-differential-amaru-cardano-node
  consensus-epoch-boundary-active-nonce-differential
)
for s in "${SCNS[@]}"; do
  echo "=== $s ==="
  dwarf run --scenario "$ROOT/dwarf/scenarios/$s.yaml" --profile block-apply-adversary "$@" \
    || echo "  (run needs the forge/serve external deps + live pair)"
done
