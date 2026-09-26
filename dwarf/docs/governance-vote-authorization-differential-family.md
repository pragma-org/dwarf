# Conway governance vote-authorization differential family (CORE)

The **core** Conway governance-signature angles — committee and DRep **vote authorization** —
as a cardano-node-vs-Amaru phase-1 differential with a verdict + reason-class + credential parity
oracle. These are unreachable on the stock reference substrate (script-hash committee, no
registered DReps, no open action — see `governance-signature-phase1-differential-family.md`), so
they run on a dedicated **governance-provisioned re-baked substrate** built for this purpose.
Coverage addition, not a finding: a clean conformance pass on the angles where governance
divergences are most likely to live.

## Result (2026-09-26)

**Amaru 10.11.20260903 (`ea1f34e4`) is CONFORMANT with cardano-node 10.7.1 (045bc187)** on governance
vote authorization — verdict + reason-class + credential parity across all cases.

| case | expected | cardano | amaru | parity |
|---|---|---|---|---|
| cc-unauthorized (vote by cc3, MemberNotAuthorized hot key) | reject | `ConwayGovFailure VotersDoNotExist(CommitteeVoter 8360fd1e…)` | `invalid voting procedures: unauthorized or unknown voters {ConstitutionalCommitteeKey(8360fd1e…)}` | verdict + class + **same cred** |
| cc-missing-witness (cc1 authorized, cc1-hot witness omitted) | reject | `MissingVKeyWitnessesUTXOW(82d5ac73…)` | `missing required signatures … [82d5ac73…]` | verdict + class + same cred |
| cc-wrong-key (cc1 vote signed by cc2-hot) | reject | `MissingVKeyWitnessesUTXOW(82d5ac73…)` | same cred `82d5ac73…` | verdict + class + same cred |
| drep-missing-witness (drep1 vote, drep1 witness omitted) | reject | `MissingVKeyWitnessesUTXOW(411058a7…)` | same cred `411058a7…` | verdict + class + same cred |
| drep-wrong-key (drep1 vote signed by drep2) | reject | `MissingVKeyWitnessesUTXOW(411058a7…)` | same cred `411058a7…` | verdict + class + same cred |
| cc-valid (cc1 authorized hot key) | accept | 202 | 202 | verdict |
| drep-valid (registered drep1) | accept | 202 | 202 | verdict |

Both distinguish **unauthorized voter** (cc3 seated but hot key never authorized → the vote's
voter credential is unknown/unauthorized) from **missing witness** (the authorized credential's
signature is absent) — same rule, same credential, on both nodes. Parity is **per-case**
node-vs-node; the cc-unauthorized class legitimately differs from the missing-witness class (they
are different rules), which is expected, not a divergence.

## Governance-provisioned substrate (govrebake) — how it was built

A **separate** forging cluster + genesis, leaving the phase-1 reference substrate and its
UTxO `9708b921…` fixtures fully intact (isolation constraint). All state is REAL and reachable —
no hand-built ledger, no faked witnesses, stock node validation only (the four guardrails):

1. **Genesis:** configurator `0f9570b` base, then inject a **key-hashed** constitutional committee
   (3 committee cold-key hashes I hold/commit, threshold 2/3, `committeeMinSize` 3) + the funding
   `initialFunds` + a forward `systemStart`. cardano-node accepts the key-hashed committee (Q1,
   confirmed live in `gov-state`).
2. **Forge** a 3-node k=20 cluster (docker-compose producer path: wrapper entrypoint with
   `CARDANO_BLOCK_PRODUCER=true`, tracer, peering — a bare `cardano-node run` won't forge).
3. **Real mined setup txs** from the funded UTxO: register 2 DReps (committed keys); authorize
   cc1 + cc2 hot keys via committee hot-key-authorization certs signed by the cold keys (cc3 left
   **unauthorized**, for the reject case); submit one normally-proposed **InfoAction** (real
   proposal + `govActionDeposit`). All mined; verified in `drep-state` / `committee-state` /
   `gov-state`.
4. **Freeze** at epoch 3 (slot 1205), with the action open (`expiresAfter` epoch 7 — votes are
   well inside the live window): a **non-forging** cardano reference (empty topology, no keys) +
   an Amaru 0918 store via `snapshot create` + `node bootstrap`, both from the same post-setup
   point → both frozen stores share the committee + DReps + open action.

**Amaru captured the governance state** (residual-risk i/ii check, from the bootstrap log):
`proposals.import size=1`, `dreps.import size=2`, `constitutional_committee.import members=3
threshold 2/3`, `import.utxo size=7`.

## Substrate-sanity gate (run before the corpus)

The honest path was proven on BOTH nodes first, so a violation rejection is attributable to the
authorization/witness rule (not a substrate gap): a valid CC vote (cc1 authorized hot key) and a
valid DRep vote (drep1) were each **ACCEPTED by both** cardano (202) and Amaru (202) on the open
action. The two frozen stores sit at the same slot-1205 freeze with the action live to epoch 7
(residual-risk iii managed — the vote is well inside the window, so no expiry-driven false split).

## Reproduce

`prepare-govrebake.sh` documents + drives the substrate build; then bring up the frozen cardano
reference (submit-api on :8091) + the Amaru 0918 relay (:3013) and run:

```
cd workload && python3 governance_votes_differential.py \
  --amaru http://localhost:3013/api/submit/tx --cardano http://localhost:8091/api/submit/tx
# -> "VIOLATION VERDICT+REASON+CRED PARITY (5 cases): ALL AGREE"
```

Committed keys (committee cold/hot, DReps, stake), vote files, the vote fixtures, and the
key-hashed-committee genesis are under `fixture/governance_votes/` (testnet-only, no value).
Violations are idempotent; the two valid controls are single-use (an accept consumes the funding
UTxO — restart the frozen nodes between accept-runs).


## Re-validated against LATEST amaru — v10.11.20260925 (eaf8ac3f), 2026-09-26

Re-ran this family against the current tagged latest amaru (git_commit eaf8ac3f) via `node run` on the 0903-bootstrapped store (store format compatible; latest cannot freshly bootstrap a custom testnet — see amaru-custom-testnet-bootstrap-regression-0903-to-0925.md), vs cardano-node 10.7.1 (045bc187): **still CONFORMANT — all cases agree, no divergence.** Result is now current-version-validated.
