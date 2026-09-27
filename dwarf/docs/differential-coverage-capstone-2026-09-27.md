# Amaru vs cardano-node: transaction-validation differential coverage (capstone, 2026-09-27)

This document is the client-facing summary of DWARF's submit-path differential campaign. The same
transaction bytes are sent to Amaru and to the Haskell reference node. The campaign then compares
the verdict, the rule each node cites, and the entity it names. It supersedes
`phase1-differential-coverage-2026-09-26.md`, whose semantic edge matrix is carried forward below.

**Every verdict here comes from a real run.** Evidence is under
`antithesis/cardano_amaru_adversarial/fixture/<family>/graded-*.json`, or in the family doc where
a family predates the graded-file convention. Nothing is inferred from source unless it is labelled
**source-level**.

## Versions under test

Ground truth is the binary itself (`--version`), enforced by the version-provenance gate.

| component | version | git commit |
|---|---|---|
| Amaru | v10.11.20260925 | `eaf8ac3f` |
| cardano-node | 11.1.2 | `fef83fed` |
| cardano-submit-api (the cardano side's front door) | 11.1.2 | matches the node (gated since `9536647`) |

Substrate: a frozen, non-forging reference-ledger pair built from the same genesis. Each family
ran on a dedicated, mempool-isolated pair, with the mempool reset before every single-use accept.

## Oracle (common to every family)

Every case is graded on three things:
- **Verdict parity:** accepted, phase-1 reject, phase-2 reject, or decode reject.
- **Reason-class parity:** both nodes cite the targeted ledger rule. Where one node reports a
  failure *set* and the other the first failure, class-set intersection decides.
- **Parity token:** both name the same credential, script, policy, hash, amount or size.

Grading is **fail-closed**. A node that is masked (its funding input consumed), unavailable, or
whose reason was truncated gives `INCONCLUSIVE`, never a pass. Violations are replay-safe; every
accept control is single-use and run after a mempool reset.

## Results: current pair (Amaru 0925 vs cardano-node 11.1.2)

| family | cases | result | evidence |
|---|---|---|---|
| Stake / pool / withdrawal (certs, pool reg/re-reg/retire, withdrawals) | 27 | **27 AGREE** | `stake-pool-withdrawal-phase1-differential-family.md`; `fixture/stake_pool/graded*.json` |
| Collateral / redeemer | 18 | **17 AGREE, 1 VERDICT-DIVERGENCE (P1)** | `collateral-phase1-differential-family.md`; `fixture/collateral/graded-2026-09-26.json` |
| Mint / burn + multi-asset value | 19 | **19 AGREE** | `mint-burn-value-phase1-differential-family.md` |
| Governance **proposals** (deposit, return account, prev-action lineage, hard-fork succession, committee, guardrails) | 24 | **24 AGREE** | `gov-proposal-phase1-differential-family.md` |
| Metadata / auxiliary-data hash | 17 | **17 AGREE** | `metadata-phase1-differential-family.md` |
| Reference-input resolution | 5 | **5 AGREE** | `reference-input-resolution-differential-family.md` |
| Mempool / submit path (size cap, duplicates, HTTP robustness + liveness) | 15 | **15 AGREE** | `mempool-submit-path-differential-family.md` |
| Plutus phase 2: 2a ex-units / `is_valid` (9), 2b builtins + ScriptContext (6), 2c error-path builtins (11) | 26 | **26 AGREE**, no VM panic | `plutus-phase2-differential-coverage-2026-09-27.md` |
| **Total** | **151** | **150 AGREE, 1 divergence** | |

Selected conformance datapoints:
- **Metadata:** Amaru hashes auxiliary data **as sent**. With a non-canonical aux encoding, both
  nodes accept when the hash is over the sent bytes and both reject (`ConflictingMetadataHash`,
  with an identical expected hash) when it is over the canonical re-encoding.
- **Size:** both count the **original** bytes at the 16384 max-tx-size cap, IsValid excluded.
  This was confirmed with a separate non-canonical encoding of the body, the witnesses and the aux
  data.
- **Plutus:** the ex-unit knife-edge agrees exactly.
- **Decoder strictness:** every decode edge tested (mint/multi-asset CDDL, anchor bounds,
  metadatum size and type) is refused by **both** decoders. No Amaru decoder leniency was found in
  these families.

## Results: families last graded against cardano-node 10.7.1

These families were graded on Amaru 0903 / cardano-node 10.7.1, then **re-validated on Amaru
0925 (`eaf8ac3f`) against cardano-node 10.7.1 (`045bc187`)**. They have **not** yet been re-run
against 11.1.2. Their cases are witness and script rules, not decode edges, so the 10.7.1-vs-11.1.2
decoder difference noted below does not bear on them. A re-run on the current pair is still the
honest next step.

| family | cases | result | doc |
|---|---|---|---|
| Native scripts (multisig, RequireMOf, nested, timelocks incl. inclusive boundaries) | 10 | all AGREE (script-hash parity) | `native-script-phase1-differential-family.md` |
| Governance certificate witnesses (DRep registration) | 5 | all AGREE (credential parity) | `governance-signature-phase1-differential-family.md` |
| Governance vote authorization (committee / DRep) | 7 | all AGREE (credential parity) | `governance-vote-authorization-differential-family.md` |

## Semantic field-edge matrix (Amaru 0925 vs cardano-node 11.1.2; carried forward)

Min-fee / tx-size (164180 reject, 164181 accept; gap 0), value conservation (both directions),
output min-UTxO (ADA-only and multi-asset), certificate deposit ±1, int64-max mint, validity
interval (expired, not yet valid, controls), max-tx-size (now pinned at exactly 16384 / 16385):
**all AGREE.** Detail is in `phase1-differential-coverage-2026-09-26.md`.

## Findings (Amaru)

| severity | finding | status | doc |
|---|---|---|---|
| **HIGH** | A **non-curve-point verification key** in a witness panics the ledger thread and the node exits: a remote, unauthenticated, single-transaction crash. cardano-node rejects the same tx and stays up. | CONFIRMED 2/2, independently verified, NOVEL. The panic-hunt closure found it to be the **sole reachable** panic among the sibling `.expect` / `unreachable!` sites. | `finding-amaru-vkey-noncurve-point-crash.md` |
| **MEDIUM** | **Collateral witness bypass (P1).** Amaru accepts a no-script tx naming someone else's UTxO as collateral without that owner's witness; cardano rejects it. A phase-1 validation-rule bypass. | LIVE, reproduced 2/2 | `collateral-phase1-differential-family.md` |
| LOW | The submit API accepts **trailing bytes** after a tx; cardano decode-rejects. | known, confirmed live on 0925 | `finding-amaru-submit-trailing-bytes.md` |
| LOW | The mempool admits **conflicting-input** txs; cardano rejects the second. | likely by-design; not filed | `finding-amaru-mempool-input-conflict-admission.md` |

The two LOW items are ingress permissiveness. They are bounded, have no consensus impact, and
Amaru does not forge Praos blocks.

## Source-level candidates (not empirically confirmed)

- **Mempool capacity accounting (LOW; D3 LOW-MEDIUM).** Amaru's mempool is a hard-coded
  180 224 B, charged by raw tx bytes, bounded by bytes only, and rejects immediately when full.
  cardano-node's is 2 × (maxBlockBodySize − 1024) = 178 176 B, taken from protocol parameters,
  charged `sizeTxF + 4`, also bounded by execution units and reference-script bytes, and it
  blocks the submitter when full. Verified against the exact shipped versions (amaru `eaf8ac3f`;
  ouroboros-consensus 4.2.1.0 as shipped in cardano-node 11.1.2). The live confirmation (a 64-UTxO
  re-bake) was deliberately skipped as low value. `finding-candidate-amaru-mempool-capacity-accounting.md`.
- **Epoch-boundary VRF: PLACEHOLDER.** A block-level result from the block-level lane is pending
  its (a)/(b) proof. *To be filled with the verdict, evidence path and severity once proven. No
  claim is made here until then.*

## Harness hardening delivered in this campaign

- **Submit-API provenance gate** (`9536647`): `check_submit_api_refs` requires every
  `cardano-submit-api` image to match the node release under test. This was prompted by a live
  decoder skew. A 10.7.1 submit-api in front of an 11.1.2 node accepted 65-byte metadata that the
  node's decoder rejects, and the node dropped the connection (`BearerClosed`). All pairs are
  redeployed on 11.1.2, and the gate is negative-tested.
- **Reproducible fixtures** (`fixture/txedit.py`): shared re-sign helpers with self-checks. Unchanged
  witness entries are spliced as raw bytes, because cbor2 decodes tag-258 sets into hash-randomised
  Python sets, and before this fix the corpus rebuilds were not byte-reproducible.
- **Shared grading fixes:** each family passes its own reason table (no cross-family global
  mutation); an opt-in full-response `detail` field for parity tokens past the 400-char reason cut;
  a classifier marker for large cardano rejections (`conwayutxowfailure`); and `decode_leniency`
  flagging.
- **Pair reset system-start guard:** `reset-pair.sh` waits out Amaru's "process start must be after
  Ouroboros system start" window after a fresh deploy. *This is an operational script on the test
  host (`/home/nigel/reset-pair.sh`), not in the repository.*

## Known limits of this substrate (what is NOT covered)

- **Mempool capacity, ordering and eviction:** there is one spendable UTxO, and Amaru does not chain
  on unconfirmed outputs. These are covered source-level only (above).
- **Guardrails-script execution** for ParameterChange and TreasuryWithdrawal with the correct
  policy, and **votes on new proposals** (a frozen chain cannot mine them).
- **Proposals chained to an in-flight proposal** (a frozen chain cannot mine them; a tx cannot
  reference its own proposal id).
- **A valid burn** of a pre-existing token (no token UTxO can confirm on a frozen chain).

## Pending

- **Reference-script / inline-datum family:** substrate being baked.
- **Block-level differential:** blocked on forward-sync across the epoch boundary. The VRF
  placeholder above depends on it.
- **Re-run the three 10.7.1-era families** (native-script, governance certificate, governance vote)
  on the 11.1.2 pair to bring the whole table onto one reference version.
