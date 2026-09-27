use aiken/collection/list
use cardano/assets
use cardano/transaction.{Transaction}

// ScriptContext (TxInfo) construction fidelity. Each asserts a field == a REDEEMER-supplied
// expectation, so the control (correct value) and the wrong-expectation share one validator.

validator ctx_fee {
  mint(r: Int, _p: Data, tx: Transaction) {
    tx.fee == r
  }
}
validator ctx_mint_qty {
  mint(r: Int, policy: ByteArray, tx: Transaction) {
    assets.quantity_of(tx.mint, policy, #"434f4c4c") == r   // "COLL"
  }
}
validator ctx_num_ref_inputs {
  mint(r: Int, _p: Data, tx: Transaction) {
    list.length(tx.reference_inputs) == r
  }
}
validator ctx_num_inputs {
  mint(r: Int, _p: Data, tx: Transaction) {
    list.length(tx.inputs) == r
  }
}
