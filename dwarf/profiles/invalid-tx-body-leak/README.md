# Profile: invalid-tx-body-leak (value-creation / theft detector)

CRITICAL-class differential. A phase-2-FAILED transaction (`is_valid=false`) must, per the Conway
rules, consume ONLY its collateral; the entire tx body — outputs, mint, withdrawals, treasury
donation, certificates — is DROPPED. If amaru applies any body effect on a failed tx while
cardano-node drops it, value is created from nothing:

- donation leaked  -> treasury inflation (monetary-invariant break)
- output leaked     -> spendable UTxO created without consuming the tx inputs = double-spend / theft
- mint leaked       -> free native tokens
- withdrawal leaked -> reward account drained without debit

Substrate: 1 Amaru + 1 Haskell (reuses the block-apply forged-block bridge — forge the is_valid=false
block at amaru tip+1 under the forced epoch nonce, serve, apply). Reference baseline = cardano-node
(applies only collateral). Observers: `invalid_tx_body_differential` (post-apply state + behavioral
re-spend) + `invalid_tx_body_dropped` assertion (pass = no leak; fail = value-creation divergence).

Scenarios: ledger-isvalidfalse-body-leak-{donation,output,mint,withdrawal}-differential (+ combined).
