# Conway stake/pool certificate + reward-withdrawal phase-1 differential family

A DWARF phase-1 differential family (extends `workload/mixed_phase1.py`) covering **stake
certificates, pool registration, and reward withdrawals**: required-witness checks and the
DELEG / POOL / withdrawal rules. It is separate from the DRep-certificate family
(`governance-signature-phase1-differential-family.md`). It adds coverage; it is not a finding.

> **STATUS (2026-09-26): GRADED. Amaru v10.11.20260925 (`eaf8ac3f`) is CONFORMANT with
> cardano-node 11.1.2 (`fef83fed`)** on all 17 cases. The 13 violations are graded
> `AGREE`: same verdict, a shared reason class, and the same credential. The 4 controls are
> `AGREE`: both nodes accept, with the same tx id. There is **no verdict divergence and no
> reason-class divergence**. The withdrawal precedence case is a reason-*reporting*
> difference, not a divergence (see below). Graded on a dedicated, mempool-isolated
> ref+Amaru pair; the cardano mempool was 0 before every run. Full responses are in
> `fixture/stake_pool/graded-2026-09-26.json`.

## Reachability (probe-first, 2026-09-26)

The corpus uses **fresh** credentials: a new stake key, a new pool cold key, a new owner key.
For these, "not registered" is the correct pre-state, so each violation reaches the rule it
targets. No on-chain setup is needed that would have to be mined identically into both frozen
stores. Two pieces of genesis state are used read-only:

- genesis pool `5801a763…` (registered), as a valid delegation target;
- genesis delegator `3e521ccc…` (a registered stake credential; we have **no** key for it).

**Not reachable on the stock substrate** (they need keys or state that we do not have):

- a withdrawal ACCEPT control, or a wrong-amount withdrawal on a funded account (needs a
  registered, DRep-delegated account whose key we hold);
- pool re-registration or retirement, and VRF-key reuse (needs a genesis pool's cold/VRF keys).

`/home/nigel/govrebake/p*/keys` holds those keys for the governance substrate, so this would be
a phase 2 there.

## Cases & result

All cases spend `9708b921…#0`, with **fee 300000 ≫ min** and no validity interval.
Violations are idempotent. Controls are single-use: each was run once, after a mempool reset.

| case | expected | cardano-node 11.1.2 | Amaru 0925 | grade |
|---|---|---|---|---|
| stakereg-missing-witness | reject | `MissingVKeyWitnessesUTXOW(3e762d7f…)` | `missing required signatures … [3e762d7f…]` | AGREE + cred |
| stakereg-wrong-key | reject | `MissingVKeyWitnessesUTXOW(3e762d7f…)` | same cred | AGREE + cred |
| stakereg-bad-deposit | reject | `{IncorrectDepositDELEG (Coin 1000000), ValueNotConservedUTxO}` | `incorrect stake deposit: provided 1000000, expected 2000000` | AGREE (see note) |
| stakereg-bad-deposit-balanced | reject | `IncorrectDepositDELEG (Coin 1000000)` only | `incorrect stake deposit …` | AGREE |
| regvote-missing-witness | reject | `MissingVKeyWitnessesUTXOW(3e762d7f…)` | same cred | AGREE + cred |
| regdeleg-missing-witness | reject | `MissingVKeyWitnessesUTXOW(3e762d7f…)` | same cred | AGREE + cred |
| regdeleg-unknown-pool | reject | `DelegateeStakePoolNotRegisteredDELEG(b2e43441…)` | `unknown target entity … b2e43441…` | AGREE |
| poolreg-missing-cold | reject | `MissingVKeyWitnessesUTXOW(b2e43441…)` (cold) | same cred | AGREE + cred |
| poolreg-missing-owner | reject | `MissingVKeyWitnessesUTXOW(b0f6c2fb…)` (owner) | same cred | AGREE + cred |
| poolreg-cost-too-low | reject | `StakePoolCostTooLowPOOL {supplied 339999999, expected 340000000}` | `pool cost too low: provided 339999999, minimum 340000000` | AGREE |
| poolreg-wrong-network | reject | `WrongNetworkPOOL {supplied Mainnet, expected Testnet}` | `pool reward account has wrong network: expected Testnet, actual Mainnet` | AGREE |
| wdrl-unregistered | reject | `{ConwayWdrlNotDelegatedToDRep, WithdrawalsNotInRewardsCERTS}` | `… that is not registered` | AGREE (see note) |
| wdrl-genesis-no-witness | reject | `{ConwayWdrlNotDelegatedToDRep, MissingVKeyWitnessesUTXOW}` (both `3e521ccc…`) | `… (3e521ccc…) that has no drep delegation` | AGREE + cred (precedence) |
| stakereg-legacy-no-witness | accept | 202 | 202 | AGREE (**no over-strictness**) |
| stakereg-present | accept | 202 | 202 | AGREE |
| regvote-present | accept | 202 | 202 | AGREE |
| poolreg-present | accept | 202 | 202 | AGREE |

Each accept returned the same tx id from both nodes, and the cardano mempool then held 1 tx,
so each accept was real.

## Reason-reporting notes (NOT divergences)

cardano-node reports the **set** of failed rules. Amaru reports a **single** reason. This is the
same pattern the governance families documented. In every co-occurring case, Amaru's single
reason is a member of cardano's set, so the class-set-intersection oracle grades AGREE:

- **wdrl-genesis-no-witness (the precedence case):** withdrawing 0 from a registered genesis
  account that has no DRep delegation and no witness. cardano-node 11.1.2 enforces the
  pv10 **ConwayWdrlNotDelegatedToDRep** rule and lists it **first**, together with the missing
  witness. Amaru reports only the DRep-delegation rule. Both nodes enforce the pv10 rule, and
  both reject.
- **wdrl-unregistered:** cardano-node co-reports ConwayWdrlNotDelegatedToDRep (an unregistered
  account is also undelegated). Amaru reports only "not registered".
- **stakereg-bad-deposit:** the tx balances to the **declared** 1 ADA. cardano-node balances
  against the **pparam** 2 ADA, so it also reports ValueNotConservedUTxO. Amaru reports only
  the deposit rule. `stakereg-bad-deposit-balanced` isolates the rule: it balances to the pparam
  deposit, and cardano-node then reports *only* IncorrectDepositDELEG. Both nodes still reject.

## Oracle

`workload/stake_pool_differential.py` grades each case as follows:

- **INCONCLUSIVE**: either node is MASKED (the funding input is already consumed in its mempool)
  or unavailable. This is never a pass.
- **VERDICT-DIVERGENCE**: the verdicts differ, or they do not match the expected verdict.
- **REASON-DIVERGENCE**: the verdicts are the same, but a node does not cite a target rule, the
  two nodes share no rule class, or a node does not name the expected credential.
- **REASON-UNVERIFIED**: the verdicts agree, but a reason was cut at the 400 characters that
  `mixed_phase1` keeps.

The reason markers were matched against **live cardano-node 11.1.2 output** before grading.
They are the cardano-ledger constructor names `MissingVKeyWitnessesUTXOW`,
`IncorrectDepositDELEG`, `DelegateeStakePoolNotRegisteredDELEG`, `StakePoolCostTooLowPOOL`,
`WrongNetworkPOOL`, `WithdrawalsNotInRewardsCERTS` and `ConwayWdrlNotDelegatedToDRep`, plus
Amaru's phrases.

## History

- First probe (2026-09-26) on the **shared** reference (cardano-node 10.7.1 `045bc187`, Amaru
  v10.11.20260903 `ea1f34e4`). Amaru rejected all 12 original violations with the targeted
  reasons. Every cardano response was `ConwayMempoolFailure "All inputs are spent"`, because
  another family's pending control held the funding input. `mixed_phase1` classified that as
  `phase1_reject`, which was **false parity**. This led to the MASKED class in `mixed_phase1`.

## Reproduce

```
fixture/stake_pool/build.sh                 # rebuild certs/txs/manifest (cardano-cli via docker);
                                            # regenerates keys if the .skey files are absent
# reset mempools; confirm `cardano-cli conway query tx-mempool info` shows numberOfTxs = 0
cd workload && python3 stake_pool_differential.py --amaru <amaru>/api/submit/tx --cardano <ref>/api/submit/tx
# -> "VIOLATIONS (13 cases): ALL AGREE"
python3 stake_pool_differential.py --control stakereg-legacy-no-witness …   # ONE control per reset
```

Exit codes: 0 means all agree, 1 means divergence, 2 means inconclusive. The keys in
`fixture/stake_pool/keys` are testnet-only and hold no value. The public bundle strips the
`.skey` files, and `build.sh` regenerates them.
