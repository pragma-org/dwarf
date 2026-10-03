# Clean-negative: amaru proposal_procedures ScriptContext (TxInfo) matches cardano (phase-2 AGREE)

**Gap (user pick):** the Conway governance TxInfo fields (`tx.proposal_procedures`, `tx.votes`) are the
one area the 16-observer ScriptContext-fidelity corpus does not touch. If amaru builds a different
`proposal_procedures` representation than cardano, a governance-aware Plutus script sees a different
world => is_valid consensus split.

**Verdict (b1 proposals): LIVE CONFORMANCE AGREE. No divergence.** A PlutusV3 phase-2 observer that
asserts the proposal's governance-action variant (`expect NicePoll = pp.governance_action`, i.e. an
Info action) PASSES on BOTH amaru and cardano-node for the same tx. amaru's proposal_procedures
ScriptContext construction (gov-action variant encoding) is faithful to the cardano reference.

## Method (self-observing phase-2 script, verify-don't-predict)

- Observer (Aiken v1.1.24, extends the scriptctx/validators.ak field-observer pattern):
  `ctx_proposal_is_info` (policy 241b6413) mints iff `tx.proposal_procedures == [pp]` and
  `pp.governance_action == NicePoll`. Aiken's governance types are modeled on the cardano ledger's
  Plutus ScriptContext, so a pass on amaru means amaru's TxInfo decodes identically. (Also built
  ctx_num_proposals / ctx_proposal_deposit; one observer per tx used — 3-in-one exceeds
  max_tx_ex_units 14e9/14e6.)
- Substrate: pair1 / GOLDEN (self-contained mint policy + funding 9708b921#0). Governance precondition:
  a proposal's deposit-return must be a REGISTERED stake account; the frozen substrate has none, so a
  stake-registration (register cfdf1e64, deposit 2e6; tx df534d5b) was forge-applied to amaru (amaru
  does not mempool-chain) and mempool-submitted to cardano (which does).
- Tx: one Info `proposal_procedure` (deposit 1e11, return cfdf1e64) + mint the observer token, spending
  the registration change df534d5b#0.

## Observations

| step | amaru :3210 | cardano :8110 |
|------|-------------|---------------|
| unregistered return account (earlier) | 400 ProposalReturnAccountDoesNotExist | 400 ProposalReturnAccountDoesNotExist (AGREE, phase-1) |
| stake-reg (register cfdf1e64) | forge-applied (block 214, tip.adopt) | 202 (mempool) |
| proposal + ctx_proposal_is_info observer | **202** (phase-2 observer PASS) | **202** (phase-2 observer PASS) |

amaru stayed alive throughout (submit :3210=400). The 202 on amaru is a phase-2 pass (the Plutus mint
policy ran and returned True), so amaru's `tx.proposal_procedures` + the Info/NicePoll gov-action
decode exactly as the cardano-reference Aiken types expect.

## Conclusion

Conformance clean-negative for the proposals TxInfo surface: amaru's proposal_procedures ScriptContext
(gov-action variant) matches cardano-node; no is_valid split. Plus a phase-1 governance-precondition
AGREE (both reject an unregistered deposit-return account identically). 

Scope tested = the gov-action variant encoding (the sharpest representation dimension) + proposal
presence. Not exercised: multi-proposal ordering, the scalar count/deposit observers in one tx
(ExUnits-limited), and the votes path (b2 — needs a live gov-action to vote on; follow-on).

## (b2) VOTES — LIVE CONFORMANCE AGREE (2026-10-03)

Extended to the votes TxInfo field (`tx.votes : Pairs<Voter, Pairs<GovernanceActionId, Vote>>` — a
nested ordered map, higher map-ordering/encoding divergence risk than proposals). Observer `ctx_votes`
(policy 819ee9e1) mints iff `tx.votes == [ StakePool(97b0f582...) -> [ _gaid -> Yes ] ]`.

Substrate: ONE forge of block = [reg.tx (df534d5b, registers cfdf1e64), proposal_plain.tx (736a51ab,
Info proposal creating live gov-action 736a51ab#0)] (intra-block chained), applied to pair1 amaru. Vote
cast by StakePool 97b0 (the registered forging pool; cold key (pool 97b0's forging key)) via
`cardano-cli conway governance vote create --yes --governance-action-tx-id 736a51ab --governance-action-index 0`.

Observations (both nodes ALIVE):
- cardano :8110 (mempool-chain reg -> proposal -> vote): reg 202, proposal 202, vote 202 (ctx_votes observer PASS = reference tx.votes encoding).
- amaru :3210 (reg+proposal forge-applied, block 8366de2f -> cfdf1e64 registered + gov-action 736a51ab#0): vote 202 (ctx_votes observer PASS).

So amaru's tx.votes nested-Pairs(Voter, Pairs(GovActionId, Vote)) ScriptContext encoding (StakePool voter + Yes vote + map structure) matches cardano-node. No is_valid divergence on the votes TxInfo surface.

## Combined verdict

The governance ScriptContext (TxInfo) surface — the one area the 16-observer corpus did not touch —
is CONFORMANT on amaru for both proposal_procedures (b1) and votes (b2): the phase-2 observers pass on
both amaru and cardano-node. No is_valid consensus split. (Plus the phase-1
ProposalReturnAccountDoesNotExist AGREE.)
