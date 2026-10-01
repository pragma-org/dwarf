# block-apply-adversary profile — forged-block bridge suite

Bundles the 5 block-apply differential scenarios proven live (2026-09-30/10-01) against amaru
eaf8ac3f vs cardano-node 11.1.2 via the PATH-B forged-block bridge: forge a block at amaru tip+1
under amaru's own buggy epoch nonce 3a5e3601, serve it over one socket, repoint amaru, and observe
adopt / block-fetch / apply / crash.

## Scenarios (dwarf/scenarios/)
1. ledger-block-apply-stake-address-output-crash-differential-amaru-cardano-node (HIGH DoS crash, inputs.rs:122)
2. ledger-block-apply-cert-phantom-deleg-unregistered-differential-amaru-cardano-node (accept-invalid-block)
3. ledger-block-apply-collateral-foreign-unwitnessed-differential-amaru-cardano-node (accept-invalid-block)
4. ledger-block-apply-donation-isvalid-false-treasury-credit-differential-amaru-cardano-node (positive control; is_valid=false apply)
5. consensus-epoch-boundary-active-nonce-differential (finding #3 live — amaru adopts a buggy-nonce block)

## Bridge primitives
registry: dwarf/primitives/registry.json ; impl: dwarf/profile_manager/block_apply_primitives.py ;
scripts: dwarf/block_apply/. Pipeline:
build_block_segments -> forge_block (ext: cod-forge forced-nonce VRF+KES) -> assemble_blockjson
-> serve_crafted_block (ext: wire tx-submission/block-fetch responder) -> block_apply_differential
-> assert block_apply_outcome_matches.

## Run
Per scenario: dwarf run --scenario dwarf/scenarios/<id>.yaml --profile block-apply-adversary
Whole suite:  bash dwarf/profiles/block-apply-adversary/run.sh
forge_block + serve_crafted_block need the external cardano-crypto forge (cod-forge) and the serve
responder (wire); see dwarf/block_apply/README.md. Live-run evidence:
reports/amaru-block-apply-upgrades-evidence/README.md.
