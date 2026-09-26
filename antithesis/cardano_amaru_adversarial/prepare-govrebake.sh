#!/usr/bin/env bash
# prepare-govrebake.sh — build the governance-PROVISIONED re-baked substrate that makes the CORE
# Conway governance-signature angles (committee + DRep vote authorization) reachable as a
# cardano-node-vs-Amaru phase-1 differential.
#
# SEPARATE from the phase-1 reference substrate (prepare-rebake.sh): its own genesis/profile +
# committed funding + committed governance keys, so the existing genesis + UTxO 9708b921 fixtures
# stay intact. Runs LOCALLY; rebuilds state/images that are intentionally NOT committed / NOT GHCR.
#
# Guardrails (the contract): real keys/real signatures (never a hand-faked witness); stock node
# validation only (nothing mocked); real submitted txs judged by the node (outcomes never
# pre-asserted); only genesis/on-chain state a real chain could reach (a normal key-hashed
# committee + registered DReps + a normally-proposed action). Verified end-to-end 2026-09-26.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
FX="$HERE/fixture/governance_votes"
MAGIC=42

echo "== 1. Governance keys (committed under fixture/governance_votes/keys/) =="
# 3 committee members (cold+hot) + 2 DReps + a stake key (proposal deposit return):
#   cardano-cli conway governance committee key-gen-cold / key-gen-hot   (x3)
#   cardano-cli conway governance drep key-gen                           (x2)
#   cardano-cli conway stake-address key-gen

echo "== 2. Genesis: key-hashed committee + funding =="
# configurator 0f9570b base, then inject into conway-genesis:
#   committee.members = { "keyHash-<ccN cold hash>": <expiryEpoch> } x3, threshold 2/3,
#   committeeMinSize 3;  and into shelley-genesis: the funding initialFund + forward systemStart.
# cardano-node accepts a key-hashed committee (Q1). Distribute identical genesis to p1/p2/p3.

echo "== 3. Forge (docker-compose producer path — NOT bare cardano-node) =="
# docker compose -p govrebake up -d configurator ; inject genesis (step 2) ;
# docker compose -p govrebake up -d --no-deps tracer p1 p2 p3
# The wrapper entrypoint (CARDANO_BLOCK_PRODUCER=true) + tracer + peering are required to forge;
# a bare `cardano-node run` starts as a non-producer / never leaves GSM syncing.

echo "== 4. Real mined governance setup (from the funded UTxO) =="
# Tx A: stake-address registration + 2 DRep registrations + 2 committee hot-key AUTHORIZATION
#   certs (cardano-cli conway governance committee create-hot-key-authorization-certificate,
#   signed by the COLD keys) for cc1 + cc2; leave cc3 UNAUTHORIZED (the reject case).
# Tx B: one InfoAction proposal (cardano-cli conway governance action create-info, deposit
#   govActionDeposit, deposit-return to the registered stake). Use the anchor hash cardano-cli
#   reports for the URL (transaction build verifies the anchor). Mine A before B (B needs the
#   stake registered). Verify: drep-state --all-dreps (2), committee-state (cc1/cc2 authorized,
#   cc3 MemberNotAuthorized), gov-state (1 open action, expiresAfter within window).

echo "== 5. Freeze both stores at the same post-setup point (action still open) =="
# Stop producers at epoch 3 (action expiresAfter epoch 7 → live window). Non-forging cardano
# reference from the node db (empty topology, no keys) + submit-api. Amaru 0918 store:
#   db-analyser boundary points (epochs 0,1,2) -> amaru snapshot create --network testnet_42
#   --cardano-node-db --cardano-node-config-dir --epoch 3 --snapshot <p0> <p1> <p2>  (NO
#   --era-history on create) -> amaru node bootstrap --network testnet_42 --epoch 3
#   --era-history era-history.json. Confirm the bootstrap log imports proposals=1, dreps=2,
#   constitutional_committee members=3 (else the vote angles would be masked = a substrate bug).

echo "== 6. Substrate-sanity gate, THEN the corpus =="
# Gate: a valid CC vote (cc1 authorized hot key) AND a valid DRep vote (drep1) must each be
# ACCEPTED by BOTH the frozen cardano ref and Amaru on the open action. Only then run
# workload/governance_votes_differential.py (violations idempotent; controls single-use):
#   -> "VIOLATION VERDICT+REASON+CRED PARITY (5 cases): ALL AGREE"
echo "prepare-govrebake: recipe documented; see dwarf/docs/governance-vote-authorization-differential-family.md"
