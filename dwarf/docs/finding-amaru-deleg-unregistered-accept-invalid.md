# Finding: Amaru accepts a delegation certificate for an unregistered stake credential (+ block-apply)

**Severity:** HIGH — accept-invalid consensus divergence, **systemic** (a missing
registration-precondition shared across several UPDATE-style certificate types). Live-confirmed at
mempool admission **and** at block-apply.
**Class:** ledger divergence — `amaru accepts (and applies, in a block) a transaction cardano-node rejects`.
**Amaru:** `eaf8ac3f` (v10.11.20260925). **cardano-node:** `11.1.2`. **Date:** 2026-09-30.
**Substrate:** pair1 (amaru consumer `:3210` vs cardano-node `:8110`), full reset per submit; plus the
adversary-producer block-apply bridge.

## Divergence

A `StakeDelegation` certificate must reference a stake credential that is **already registered**.
cardano-node rejects a delegation for an unregistered credential with `StakeKeyNotRegisteredDELEG`.
Amaru **accepts** it.

- amaru `:3210` → **202 accept** (txid `ea76e324…`).
- cardano-node `:8110` → **400** `StakeKeyNotRegisteredDELEG (KeyHash bf444e76…)`.

The transaction is otherwise fully valid and properly witnessed (funding key + a fresh stake key),
which isolates the deviation to the missing registration precondition.

## Source

`rules/transaction/phase_one/certificates.rs` (~lines 317–321): the `StakeDelegation` arm calls
`context.delegate_pool(...)` with **no registration lookup** — it binds the delegation
unconditionally (the `diff_bind` / "bind-left" phantom-insert pattern). The
`StakeCredentialNotRegistered`-style precondition check exists **only** on the *deregistration* path
(~lines 289–301). cardano-node enforces the precondition on delegation; amaru does not.

**Systemic:** the same missing-registration precondition applies to the other UPDATE-style
certificates that `bind_left` without a registration lookup — `StakeDelegation`, `VoteDelegation`,
`StakeVoteDelegation`, and `UpdateDRep` — so this is a class, not a single cert.

## Live block-apply confirmation (2026-09-30)

Using the adversary-producer bridge (a forged block served to an amaru follower over one socket,
forged against amaru's own epoch-boundary nonce so the leader check passes, carrying the crafted body;
no patch to amaru's validation):

- **Forged block** `21329c9b…` at slot 1211 / height 234, body = 1 tx carrying the `StakeDelegation`
  cert for the unregistered credential → pool `97b0`.
- amaru **adopted** the header (`tip.adopt slot=1211`), **block-fetched** the body
  (`block_served=True`), and **applied** it — **no cert error, no crash, node alive**. amaru accepts,
  as a valid chain block, a block whose cert cardano-node rejects (`StakeKeyNotRegisteredDELEG`).
  This is a **would-accept-invalid-block** at block-apply.

Evidence: `artifacts/certphantom-apply-2026-09-30/` (`block.json`, `serve.log`,
`amaru-cert-phantom-apply.txt`) + `certphantom-preflight-evidence.txt`.

## Realization (scoped)

The mempool-admission divergence is live on an amaru consumer/follower (amaru does not forge Praos
blocks). It is consensus-relevant because block-apply runs the same certificate rule — confirmed
above: a served block carrying the cert is adopted + applied. A peer serving such a block drives
amaru's chain state out of agreement with cardano-node.

## Reproduce

```
# build a tx with a StakeDelegation cert for a never-registered stake credential,
# delegating to a known pool; witness it (payment + stake key); submit:
#   amaru :3210  -> 202 accept
#   cardano :8110 -> 400 StakeKeyNotRegisteredDELEG
# block-apply: place the cert in a forged block body and serve it to an amaru follower.
```
Keys testnet-only.

## Fix direction

Enforce the registration precondition on the `StakeDelegation` path (and the sibling UPDATE-style
`VoteDelegation` / `StakeVoteDelegation` / `UpdateDRep` paths), mirroring cardano-node's
`StakeKeyNotRegisteredDELEG`, instead of an unconditional `bind_left`.
