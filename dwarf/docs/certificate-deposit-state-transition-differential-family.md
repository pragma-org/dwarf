# Certificate & deposit state-transition edge differential family

Ledger state-machine + deposit-accounting integrity: a certificate sequence Amaru accepts that
cardano-node rejects can corrupt registration/delegation state or mis-account deposits (value).
Extends the stake/pool-withdrawal family (does NOT duplicate its reg/dereg/pool P1+P2/withdrawal
cases); reuses the stake/pool cert tooling and `stake_pool_differential`'s grade (verdict →
reason-class → credential parity, fail-closed) via `workload/cert_state_differential.py`.

> **STATUS (2026-09-27): 4/4 AGREE, no divergence, no deposit mismatch.** Amaru v10.11.20260925
> (`eaf8ac3f`) handles these state transitions and deposit accounting identically to cardano-node
> 11.1.2. Graded on pair4; evidence `fixture/cert_state/graded-2026-09-27.json`.

## Cases & result

Reachable intra-tx cert-sequence edges with fresh credentials (the frozen substrate has no
registered stake cred / DRep we control):

| case | edit | expected | result |
|---|---|---|---|
| dereg-unregistered | deregister a fresh, never-registered stake cred | reject | AGREE — both `StakeKeyNotRegistered` (same cred `3e762d7f…`) |
| reg-dereg-wrong-refund | reg (deposit 2 ADA) + dereg declaring **1 ADA** refund, one tx | reject | AGREE — both `IncorrectDeposit (Coin 1000000)` (deposit exactness on the refund) |
| regvote-nodrep | register + vote-delegate to a **nonexistent** DRep key-hash | reject | AGREE — both `DelegateeDRepNotRegistered` (same DRep id `deadbeef…`) |
| reg-dereg-samecred | register then deregister the same fresh cred in one tx | accept | AGREE — both accept (deposit charged then refunded) |

Deposit accounting is exact and identical: the wrong-refund case is rejected by both with
`IncorrectDeposit` naming the declared 1 ADA, and the register-then-deregister round-trip
(charge 2 ADA, refund 2 ADA, net 0) is accepted by both.

## Not expressible / substrate-limited (documented, not forced)

- **Intra-tx double-registration** (register an already-registered cred within one tx): Conway
  certificates are a **set** — two identical registration certs **dedup** (cardano-cli collapsed
  them into one, confirmed by decoding the body), so this is not expressible in a single tx.
  Cert **ordering** is likewise not attacker-controlled (a set, canonically ordered).
- **Delegate to a retired pool**, **pool re-registration after announced retirement**,
  **register an already-registered DRep**, **delegate to an expired DRep**: all need pre-existing
  on-chain registered/retired/expired state that the frozen (non-forging) substrate does not
  provide and cannot be created without a forging window (or a governance-provisioned re-bake).
  The genesis pools are registered but their keys are not held; `drep-state` is empty.

## Reproduce

```
fixture/cert_state/build.sh            # certs + txs via cardano-cli (fresh stake keys)
/home/nigel/reset-pair.sh pair4        # FULL reset (spends the shared 9708b921)
cd workload && python3 cert_state_differential.py --corpus ../fixture/cert_state --amaru :3213 --cardano :8113
python3 cert_state_differential.py --corpus ../fixture/cert_state --control reg-dereg-samecred …   # accept, after a reset
```
Exit 0 all agree / 1 divergence / 2 inconclusive. Keys testnet-only.
