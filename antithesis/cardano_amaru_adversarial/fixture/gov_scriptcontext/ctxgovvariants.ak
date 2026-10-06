use cardano/governance.{ProtocolParameters, HardFork, TreasuryWithdrawal,
  NoConfidence, ConstitutionalCommittee, NewConstitution}
use cardano/transaction.{Transaction}

// Structured gov-action TxInfo-fidelity observers (phase-2). Each mints iff the
// single proposal_procedure in the ScriptContext decodes to the expected
// GovernanceAction variant (Aiken reference types == cardano ledger spec). A mismatch
// on amaru = is_valid consensus split vs the cardano Plutus reference.

validator ctx_proposal_is_paramchange {
  mint(_r: Int, _p: Data, tx: Transaction) {
    expect [pp] = tx.proposal_procedures
    expect ProtocolParameters { .. } = pp.governance_action
    True
  }
}

validator ctx_proposal_is_hardfork {
  mint(_r: Int, _p: Data, tx: Transaction) {
    expect [pp] = tx.proposal_procedures
    expect HardFork { .. } = pp.governance_action
    True
  }
}

validator ctx_proposal_is_treasury {
  mint(_r: Int, _p: Data, tx: Transaction) {
    expect [pp] = tx.proposal_procedures
    expect TreasuryWithdrawal { .. } = pp.governance_action
    True
  }
}

validator ctx_proposal_is_noconfidence {
  mint(_r: Int, _p: Data, tx: Transaction) {
    expect [pp] = tx.proposal_procedures
    expect NoConfidence { .. } = pp.governance_action
    True
  }
}

validator ctx_proposal_is_committee {
  mint(_r: Int, _p: Data, tx: Transaction) {
    expect [pp] = tx.proposal_procedures
    expect ConstitutionalCommittee { .. } = pp.governance_action
    True
  }
}

validator ctx_proposal_is_newconstitution {
  mint(_r: Int, _p: Data, tx: Transaction) {
    expect [pp] = tx.proposal_procedures
    expect NewConstitution { .. } = pp.governance_action
    True
  }
}
