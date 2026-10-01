# Coverage backfill — submit-level differential scenarios (2026-10-01)

Scenario-ized already-reported submit-level findings with the new reusable
`runtime_tx_submit_differential` + `submit_outcome_matches` primitives. Each was RUN LIVE on the
funded store-f substrate (amaru :3210 / cardano-node :8110) and the recorded outcome matches the
scenario expectation:

| scenario | amaru | cardano | live verdict |
|----------|-------|---------|--------------|
| ledger-submit-votedeleg-deleg-unregistered | accept (202) | reject (StakeKeyNotRegisteredDELEG) | PASS |
| ledger-submit-stakevotedeleg-deleg-unregistered | accept (202) | reject (StakeKeyNotRegisteredDELEG) | PASS |
| ledger-submit-updatedrep-deleg-unregistered | accept (202) | reject (ConwayDRepNotRegistered) | PASS |
| ledger-submit-vkey-noncurve-point-crash | crash (node down) | reject (InvalidWitnessesUTXOW) | PASS |
| ledger-submit-value-coin-i64-overflow-crash | crash (node down) | reject (ValueNotConservedUTxO) | PASS |
| ledger-submit-stakeaddr-output | accept (202) | reject (decode: Invalid header 0b11100000) | PASS |

## Scope notes
- **exUnits** is intentionally NOT in the submit backfill: the L1 finding is a BLOCK-level ExUnits-SUM
  overflow (`ex_units.rs` unchecked `+` across multiple txs in a block), not a single-submit divergence.
  The per-tx `exunits-too-big` case is AGREE (both reject). ExUnits is covered at the block / Plutus
  phase-2 level, not here.
- **collateral** (foreign unwitnessed collateral accept-invalid) is NOT reproducible on the store-f
  backfill substrate: it needs a FOREIGN collateral UTxO, which store-f (durable funding 9708b921 only)
  lacks — amaru fails "unknown input" on the collateral input. The finding is real and is covered by
  `ledger-block-apply-collateral-foreign-unwitnessed-differential` on the collateral-family substrate;
  a submit-level version would need that substrate.
