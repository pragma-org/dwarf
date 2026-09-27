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
>
> **PHASE 2 (2026-09-26): GRADED, 27/27 AGREE at protocol version 10.** Phase 2 adds 10
> cases: re-registration and retirement of an existing genesis pool, retirement epoch bounds,
> retiring an unknown pool, VRF-key reuse, and a same-tx register-and-withdraw. Full responses
> are in `fixture/stake_pool/graded-phase2-2026-09-26.json`. There are two open items. (1) A
> **divergence candidate at protocol version 11**, from source: Amaru has no duplicate-VRF
> check. It is not provable on this pv10 substrate. (2) A **low-severity diagnostics defect**
> in Amaru's unknown-pool error message. Both are described in the Phase 2 section.

## Reachability (probe-first, 2026-09-26)

The corpus uses **fresh** credentials: a new stake key, a new pool cold key, a new owner key.
For these, "not registered" is the correct pre-state, so each violation reaches the rule it
targets. No on-chain setup is needed that would have to be mined identically into both frozen
stores. Two pieces of genesis state are used read-only:

- genesis pool `5801a763…` (registered), as a valid delegation target;
- genesis delegator `3e521ccc…` (a registered stake credential; we have **no** key for it).

**Phase 2** also uses the stock genesis pool keys. These are the cold key of `5801a763…`
(internal-only `.skey`), the VRF keys of `5801a763…` and `720c084d…`, and the reward-account
vkey of `5801a763…`. The files under `/home/nigel/govrebake/p*/keys` are, despite the directory
name, the **stock rebake** pool keys: the cold keys hash to the stock pool ids, and the VRF keys
hash to the registered `spsVrf`. The governance substrate's own pools are different.

**Still not reachable** (it needs a re-bake with setup mined before the freeze): a withdrawal
ACCEPT control, and a wrong-amount withdrawal on a funded account. Both need a registered,
DRep-delegated account whose key we hold, with rewards. No such account exists in the stock
state, and `wdrl-sametx-regvote` shows it cannot be set up inside the same tx.

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

## Phase 2: existing pools, retirement, VRF reuse (2026-09-26)

These cases run on the same dedicated pair: cardano-node 11.1.2 (`fef83fed`) and Amaru
v10.11.20260925 (`eaf8ac3f`), at protocol version 10. The pool is genesis pool `5801a763…`.

| case | expected | cardano-node 11.1.2 | Amaru 0925 | grade |
|---|---|---|---|---|
| rereg-missing-cold | reject | `MissingVKeyWitnessesUTXOW(5801a763…)` | `missing required signatures … [5801a763…]` | AGREE + cred |
| retire-missing-cold | reject | `MissingVKeyWitnessesUTXOW(5801a763…)` | same cred | AGREE + cred |
| retire-unregistered | reject | `StakePoolNotRegisteredOnKeyPOOL(b2e43441…)` | `unknown pool: unknown entity: PhantomData<…Hash<28>>` | AGREE (cred waived, see below) |
| retire-too-early (epoch 2) | reject | `StakePoolRetirementWrongEpochPOOL {supplied 2, expected > 3}` | `pool retirement epoch out of range: epoch 2, must satisfy 2 < epoch <= 20` | AGREE |
| retire-too-late (epoch 30) | reject | `StakePoolRetirementWrongEpochPOOL {supplied 30 …}` | `… epoch 30, must satisfy 2 < epoch <= 20` | AGREE |
| wdrl-sametx-regvote | reject | `{WdrlNotDelegatedToDRep, WithdrawalsNotInRewardsCERTS}` | `… that is not registered` | AGREE |
| newpool-dupvrf | accept@pv10 | 202 | 202 (same tx id) | AGREE |
| rereg-dupvrf | accept@pv10 | 202 | 202 (same tx id) | AGREE |
| rereg-present | accept | 202 | 202 (same tx id) | AGREE |
| retire-present (epoch 5) | accept | 202 | 202 (same tx id) | AGREE |

After each accept, the cardano mempool held 1 tx, so each accept was real. With phase 1, the
corpus has 27 cases: 19 violations and 8 controls, all AGREE.

**Same-tx ordering (wdrl-sametx-regvote).** The tx registers a credential, vote-delegates it to
AlwaysAbstain, **and** withdraws 0 from it. Both nodes judge withdrawals against the **pre-tx**
account state, so the same-tx certificates do not satisfy the withdrawal rules. This was a
plausible divergence point; the nodes agree.

**VRF-key reuse: divergence candidate at protocol version 11 (source evidence, not yet
empirical).**
- **cardano-ledger:** `hardforkConwayDisallowDuplicatedVRFKeys pv = pvMajor pv > 10`
  (`Shelley/Era.hs`). When that flag is on, `Shelley/Rules/Pool.hs` rejects with
  `VRFKeyHashAlreadyRegistered` in two cases: a new pool that reuses a registered VRF, and a
  re-registration that switches to another pool's VRF. The ledger's own `PoolSpec` test says
  accept at pv < 11 and reject at pv ≥ 11.
- **Amaru `eaf8ac3f`:** the `PoolRegistration` rule
  (`crates/amaru-ledger/src/rules/transaction/phase_one/certificates.rs`) checks only the
  cold and owner witnesses, the reward-account network, and the minimum pool cost. It has **no
  VRF-uniqueness check and no protocol-version gate**.
- **Consequence:** at pv10 both nodes accept, which is correct and graded above. From pv11,
  cardano-node rejects and Amaru, per its source, accepts. That would be a consensus-relevant
  split in ledger validity. Proving it needs a substrate with protocolVersion major 11 (a
  re-bake). `newpool-dupvrf` and `rereg-dupvrf` are already staged for it, with expected
  accept at pv10 and reject at pv11.
- **Upstream status:** this is a **known, in-progress** gap, not a new one. The open upstream
  pull request [pragma-org/amaru#1247](https://github.com/pragma-org/amaru/pull/1247), "VRF Key
  Uniqueness Validation - Take 2" (opened 2026-08-20, still open on 2026-09-26), adds this
  check. It is not in the build tested here (`eaf8ac3f`). When #1247 lands, the two staged
  cases become the regression test for its protocol-version gating: accept at pv10, reject at
  pv11.
- **Standing regression note (for when #1247 merges):** re-run `newpool-dupvrf` and
  `rereg-dupvrf` on this pv10 substrate against the Amaru build that contains #1247. Both
  **must still be accepted**. cardano-ledger gates the check on `pvMajor > 10`, so an ungated
  Amaru check would reject at pv10 what cardano-node accepts: the **reverse** divergence, with
  Amaru over-strict. The pv10 controls detect that with no re-bake. Only the pv11 half (reject
  on both) needs a pv11 substrate. The operator deferred that re-bake on 2026-09-26.

**Diagnostics defect (low severity, not a validation divergence).** On `retire-unregistered`,
Amaru's error text is `unknown pool: unknown entity:
PhantomData<amaru_kernel::cardano::hash::Hash<28>>`. It prints a Rust type name **instead of
the pool id**. cardano-node names `b2e43441…`. The verdict and the rule class agree, so
credential parity is waived for this case only.

**Substrate epoch-skew trap.** The two frozen stores sit at different tips. The cardano ledger
tip is slot 1297 (epoch 3); the Amaru tip is slot 1199 (epoch 2); `epochLength` is 400. Both
retirement error messages show this: cardano says `expected > 3`, and Amaru says
`2 < epoch <= 20`. A retirement at **epoch 3** would be rejected by cardano and accepted by
Amaru. That would be a **false** divergence caused by the substrate, not the nodes. So the
corpus uses only epochs that give the same answer on both stores (5 valid, 2 too early, 30 too
late), and epoch 3 is excluded. Any epoch-dependent family on this substrate must do the same,
or re-bake both stores at the same tip.

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
# -> "VIOLATIONS (19 cases): ALL AGREE"
python3 stake_pool_differential.py --control stakereg-legacy-no-witness …   # ONE control per reset
```

Exit codes: 0 means all agree, 1 means divergence, 2 means inconclusive. The keys in
`fixture/stake_pool/keys` are testnet-only and hold no value. The public bundle strips the
`.skey` files, and `build.sh` regenerates them.
