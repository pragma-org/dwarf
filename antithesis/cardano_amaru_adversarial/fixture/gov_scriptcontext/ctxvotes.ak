use cardano/governance.{StakePool, Yes}
use cardano/transaction.{Transaction}

// Votes TxInfo fidelity: mint iff tx.votes contains exactly the 97b0 StakePool Yes-vote.
// tx.votes : Pairs<Voter, Pairs<GovernanceActionId, Vote>>  (List<Pair<..>>)
validator ctx_votes {
  mint(_r: Int, _p: Data, tx: Transaction) {
    expect [vpair] = tx.votes
    expect StakePool(pool) = vpair.1st
    expect [gpair] = vpair.2nd
    pool == #"97b0f582dcf255b2c776f3540454b99fbf26e48833f275bfcd63bfcd" && gpair.2nd == Yes
  }
}
