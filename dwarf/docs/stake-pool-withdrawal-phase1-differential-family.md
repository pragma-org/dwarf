# Conway stake/pool certificate + reward-withdrawal phase-1 differential family

A DWARF phase-1 differential family (extends `workload/mixed_phase1.py`) covering **stake
certificates, pool registration, and reward withdrawals**: required-witness checks and the
DELEG / POOL / withdrawal rules. It is separate from the DRep-certificate family
(`governance-signature-phase1-differential-family.md`). It adds coverage; it is not a finding.

> **STATUS (2026-09-26): corpus built; Amaru side observed; cardano side NOT YET GRADED.**
> The probe ran on the shared reference. There, cardano-node's mempool already held another
> family's control spending the funding UTxO, so every cardano response was
> `ConwayMempoolFailure "All inputs are spent"`. That response is now classified **MASKED**
> (inconclusive) by `mixed_phase1`, not `phase1_reject`. Cardano grading waits for an isolated
> ref+Amaru pair and for confirmation of the reference cardano-node version.
> `rebake-ref` reports `cardano-node 10.7.1`; the other families' docs say 11.1.2.

## Reachability (probe-first, 2026-09-26)

The corpus uses **fresh** credentials: a new stake key, a new pool cold key, a new owner key.
For these, "not registered" is the correct pre-state, so each violation reaches exactly the
rule it targets. No on-chain setup is needed that would have to be mined identically into both
frozen stores. Two pieces of genesis state are used read-only:

- genesis pool `5801a763…` (registered), as a valid delegation target;
- genesis delegator `3e521ccc…` (a registered stake credential; we have **no** key for it).

**Not reachable on the stock substrate** (they need keys or state that we do not have):

- a withdrawal ACCEPT control, or a wrong-amount withdrawal on a funded account (needs a
  registered, DRep-delegated account whose key we hold);
- pool re-registration or retirement, and VRF-key reuse (needs a genesis pool's cold/VRF keys).

`/home/nigel/govrebake/p*/keys` holds those keys for the governance substrate, so this would be
a phase 2 there.

## Cases

All cases spend `9708b921…#0`, with **fee 300000 ≫ min** and no validity interval.
Violations are idempotent. Controls are single-use.

| case | expected | target rule | credential |
|---|---|---|---|
| stakereg-missing-witness | reject | UTXOW MissingVKeyWitness (Conway reg cert, tag 7) | stake `3e762d7f…` |
| stakereg-wrong-key | reject | same, signed by an unrelated stake key | stake |
| stakereg-bad-deposit | reject | DELEG IncorrectDeposit (1 ADA vs 2 ADA) | — |
| regvote-missing-witness | reject | UTXOW MissingVKeyWitness (reg + vote-deleg AlwaysAbstain, tag 12) | stake |
| regdeleg-missing-witness | reject | UTXOW MissingVKeyWitness (reg + stake-deleg genesis pool, tag 11) | stake |
| regdeleg-unknown-pool | reject | DELEG DelegateeStakePoolNotRegistered | — |
| poolreg-missing-cold | reject | UTXOW MissingVKeyWitness (pool cold key) | pool `b2e43441…` |
| poolreg-missing-owner | reject | UTXOW MissingVKeyWitness (pool owner) | owner `b0f6c2fb…` |
| poolreg-cost-too-low | reject | POOL StakePoolCostTooLow (minPoolCost − 1) | — |
| poolreg-wrong-network | reject | POOL WrongNetwork (mainnet reward account) | — |
| wdrl-unregistered | reject | WithdrawalsNotInRewards (never-registered account) | — |
| wdrl-genesis-no-witness | reject | MissingVKeyWitness **and** pv10 ConwayWdrlNotDelegatedToDRep, which co-occur (**precedence** case) | genesis `3e521ccc…` |
| stakereg-present | accept | control | |
| stakereg-legacy-no-witness | accept | **over-strictness** control: legacy Shelley reg cert (tag 0), which needs no stake witness | |
| regvote-present | accept | control | |
| poolreg-present | accept | control (cold + owner witnesses, 500 ADA deposit) | |

## Oracle

`workload/stake_pool_differential.py` grades each case as follows:

- **INCONCLUSIVE**: either node is MASKED or unavailable. This is never a pass.
- **VERDICT-DIVERGENCE**: the verdicts differ, or they do not match the expected verdict.
- **REASON-DIVERGENCE**: the verdicts are the same, but a node does not cite a target rule, the
  two nodes share no rule class, or a node does not name the expected credential. For the
  precedence case, the grade is **class-set intersection**. A cardano failure set
  `{MissingVKey, WdrlNotDelegatedToDRep}` agrees with Amaru's single `no drep delegation`.
  A cardano report of *only* MissingVKey against Amaru's *only* DRep-delegation is a
  reason-precedence divergence.
- **REASON-UNVERIFIED**: the verdicts agree, but a reason was cut at the 400 characters that
  `mixed_phase1` keeps.

Reason markers (case-insensitive): the cardano-ledger constructor names
(`MissingVKeyWitnessesUTXOW`, `IncorrectDepositDELEG`, `DelegateeStakePoolNotRegisteredDELEG`,
`StakePoolCostTooLowPOOL`, `WrongNetworkPOOL`, `WithdrawalsNotInRewards…`,
`…WdrlNotDelegatedToDRep`) and Amaru's phrases. The **cardano markers are not yet verified
against live responses**; see the status note above.

## Observed so far: Amaru 10.11.20260903 (`ea1f34e4`)

All 12 violations are `phase1_reject`. Each one names its target rule, and every case with an
expected credential names that credential. Examples: `missing required signatures … [3e762d7f…]`,
`incorrect stake deposit: provided 1000000, expected 2000000`,
`unknown target entity … b2e43441…`, `pool cost too low: provided 339999999, minimum 340000000`,
`pool reward account has wrong network: expected Testnet, actual Mainnet`,
`… that is not registered`. For **wdrl-genesis-no-witness**, Amaru reports
`… (3e521ccc…) that has no drep delegation`. It reports the pv10 DRep-delegation rule, **not**
the missing witness. The cardano-node comparison of this case is the main open question.

The controls have not been submitted yet: an accept would consume the shared funding UTxO.

## Reproduce

```
fixture/stake_pool/build.sh                 # rebuild certs/txs/manifest (cardano-cli via docker)
docker restart <cardano-ref> <amaru>        # fresh mempools; check `query tx-mempool info` = 0 txs
cd workload && python3 stake_pool_differential.py --amaru … --cardano …   # 12 violations
python3 stake_pool_differential.py --control stakereg-legacy-no-witness  # ONE control per restart
```

Exit codes: 0 means all agree, 1 means divergence, 2 means inconclusive. The keys in
`fixture/stake_pool/keys` are testnet-only and hold no value. `build.sh` copies the funding key
in temporarily and deletes it on exit.
