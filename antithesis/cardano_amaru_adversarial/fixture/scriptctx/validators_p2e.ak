use aiken/collection/list
use cardano/transaction.{Transaction}

// inputs-list CONTENT fidelity: first input's output_reference txid == redeemer expectation
validator ctx_input_first_txid {
  mint(r: ByteArray, _p: Data, tx: Transaction) {
    expect [first, ..] = tx.inputs
    first.output_reference.transaction_id == r
  }
}
// Conway redeemers map: assert redeemer count
validator ctx_redeemers_len {
  mint(r: Int, _p: Data, tx: Transaction) {
    list.length(tx.redeemers) == r
  }
}
// Conway treasury_donation (Option<Lovelace>): assert == Some(r)
validator ctx_treasury_donation {
  mint(r: Int, _p: Data, tx: Transaction) {
    tx.treasury_donation == Some(r)
  }
}
// Conway current_treasury_amount presence: assert it is None (no --current-treasury-value)
validator ctx_treasury_amount_none {
  mint(_r: Data, _p: Data, tx: Transaction) {
    tx.current_treasury_amount == None
  }
}
