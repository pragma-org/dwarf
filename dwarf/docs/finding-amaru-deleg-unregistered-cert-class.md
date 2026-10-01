# Finding: Amaru accepts update/delegation certificates for NEVER-REGISTERED credentials (missing-registration precondition class — 4 cert types)

**Status:** class completion (submit-level confirmed on all 4 cert types; block-apply reach proven).
**Target:** amaru v10.11.20260925 (eaf8ac3f) vs cardano-node 11.1.2 (fef83fed).
**Context:** authorized conformance testing on the local devnet (testnet_42), testnet-only keys; reported to PRAGMA for coordinated disclosure.
**Severity:** HIGH (systemic accept-invalid — amaru would accept blocks cardano-node rejects; ledger-state divergence / consensus-safety class).

## Summary

Conway "update-style" certificates reference a credential that MUST already be registered. amaru's
certificate application binds/updates the credential's state **without a registration lookup**
(`bind_left` phantom-insert), so it ACCEPTS a certificate for a credential that was never registered.
cardano-node rejects each with a `*NotRegistered*` ledger failure. This is a single systemic class
spanning **four** Conway cert types that share the permissive bind path:

| cert | Conway type | amaru (submit) | cardano-node (submit) |
|------|-------------|----------------|------------------------|
| StakeDelegation       | 2  | accept (202) | reject `StakeKeyNotRegisteredDELEG` |
| VoteDelegation        | 9  | accept (202) | reject `StakeKeyNotRegisteredDELEG` |
| StakeVoteDelegation   | 10 | accept (202) | reject `StakeKeyNotRegisteredDELEG` |
| UpdateDRep            | 18 | accept (202) | reject `ConwayDRepNotRegistered` (GovCertFailure) |

StakeDelegation (type 2) was previously demonstrated at **block-apply** (cert-phantom, M2 2026-09-30:
amaru accepts-invalid-BLOCK, cardano rejects the block). The other three are confirmed here at submit.

## Evidence (live, 2026-10-01, funded store-f tip 1000/181e9b48; POST /api/submit/tx, application/cbor)

Each tx spends the committed funding UTxO `9708b921…#0` and carries a fresh, NEVER-REGISTERED
credential key so the tx is otherwise well-witnessed — isolating the missing registration as the
only divergence. Vote-delegation targets the predefined `abstain` DRep so the only invalid aspect is
the unregistered delegator.

- **VoteDelegation** (cert `[9, [0, 8361a5c1…], [2]]`, txid `8f2cfaa0…`):
  cardano → 400 `ConwayCertsFailure (DelegFailure (StakeKeyNotRegisteredDELEG (KeyHash 8361a5c1…)))`;
  amaru → **202 accepted**.
- **StakeVoteDelegation** (cert `[10, [0, fc5272c5…], 97b0…, [2]]`, txid `77ff58c1…`):
  cardano → 400 `StakeKeyNotRegisteredDELEG (KeyHash fc5272c5…)`; amaru → **202 accepted**.
- **UpdateDRep** (cert `[18, [0, 3512b0b6…], null]`, txid `6224af61…`):
  cardano → 400 `ConwayCertsFailure (GovCertFailure (ConwayDRepNotRegistered (KeyHash 3512b0b6…)))`;
  amaru → **202 accepted**.

cardano resolved the input in every case (the rejection is a cert/reg failure, not a UTxO failure).

## Root cause (source-anchored)

amaru applies these certs through a shared permissive `bind_left`/`diff_bind` path that inserts or
updates the target credential's state without first asserting it is registered (a phantom-insert).
The four types above all route through this path; guarded sibling certs (which DO perform the
registration lookup) were ruled out. Block application runs the same certificate-application code as
mempool admission, so an invalid cert in a **block body** is accepted on apply — confirmed for
StakeDelegation at M2 (accept-invalid-BLOCK) and reachable identically for the other three.

## Reproduce via DWARF

1. Funded substrate: pair1 amaru (`:3210`) + cardano-node (`:8110`) on the funded store
   (`/home/nigel/funded-store-GOLDEN`, tip 1000, UTxO `9708b921`). Reset: `reset-pair.sh pair1`.
2. Build the three cert bodies + submit pre-flight fixtures:
   `python3 dwarf/block_apply/build_cert_class.py` → `votedeleg/stakevotedeleg/updatedrep-preflight.cbor`.
3. For each, submit to both and observe the divergence (reset amaru between, since each spends the
   shared funding UTxO): `curl -X POST -H 'Content-Type: application/cbor' --data-binary @<label>-preflight.cbor http://127.0.0.1:8110/api/submit/tx` (cardano → 400 NotRegistered) and `…:3210…` (amaru → 202).
4. Block-apply (StakeDelegation, already demonstrated): the cert-phantom bridge (`build_certphantom.py`
   → forge → serve → `block_apply_differential`) shows amaru accepts-invalid-BLOCK.

Builder: `dwarf/block_apply/build_cert_class.py`. Cert encodings per Conway CDDL (types 2/9/10/18).

## Impact

A single transaction (or a block containing it) drives amaru's ledger state to accept certificates
that cardano-node rejects. On a mixed network this is a ledger-state / consensus-safety divergence:
amaru would follow a chain containing blocks the Haskell reference rejects. The fix is to assert the
target credential is registered before the bind/update, matching the reference's `*NotRegistered*`
ledger rules, across all four cert types.

<!-- GHSA submission block: to be added from the finalized template (orchestrator) -->
