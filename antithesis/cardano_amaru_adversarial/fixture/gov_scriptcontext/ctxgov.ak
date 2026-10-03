use aiken/collection/list
use cardano/governance.{NicePoll}
use cardano/transaction.{Transaction}

validator ctx_num_proposals {
  mint(r: Int, _p: Data, tx: Transaction) {
    list.length(tx.proposal_procedures) == r
  }
}
validator ctx_proposal_deposit {
  mint(r: Int, _p: Data, tx: Transaction) {
    expect [pp] = tx.proposal_procedures
    pp.deposit == r
  }
}
validator ctx_proposal_is_info {
  mint(_r: Int, _p: Data, tx: Transaction) {
    expect [pp] = tx.proposal_procedures
    expect NicePoll = pp.governance_action
    True
  }
}
