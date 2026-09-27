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

**Serving-binary provenance.** The graded runs above were served by an Amaru `eaf8ac3f` build
carrying three local DWARF patches:
- a definite-map fix in the bootstrap UTxO decoder (`tvar.rs`; store building only);
- custom-testnet era-history loading from an environment variable (`network_name.rs`; the serving
  commands pass `--era-history` explicitly);
- a diagnostic print on the epoch-boundary nonce path (`nonce.rs`; never reached on a frozen submit
  pair).

None of them touches transaction validation. On 2026-09-27 all five pairs were re-served on a
separately built binary reporting `eaf8ac3f` with no local patches. The reference-script and Plutus
phase-2c families have been re-confirmed on that binary with identical verdicts (a
representative check; the serving-path patches are bootstrap-only, so serving behaviour is
identical for all families).

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
| Reference-input resolution | 5 | **5 AGREE** (verdict, reason and named-input parity) | `reference-input-resolution-differential-family.md`; `fixture/reference_inputs/graded-2026-09-27.json` |
| Reference-script + inline-datum spends | 9 | **9 AGREE** | `reference-script-inline-datum-differential-family.md`; `fixture/refscript/graded-2026-09-27.json` |
| Mempool / submit path (size cap, duplicates, HTTP robustness + liveness) | 15 | **15 AGREE** | `mempool-submit-path-differential-family.md` |
| Plutus phase 2: 2a ex-units / `is_valid` (9), 2b builtins + ScriptContext (6), 2c error-path builtins (11) | 26 | **26 AGREE**, no VM panic | `plutus-phase2-differential-coverage-2026-09-27.md` |
| Native scripts (multisig, RequireMOf incl. N-1, nested, timelocks incl. inclusive boundaries) | 10 | **10 AGREE** (script-hash parity; 5 controls same tx id) | `native-script-phase1-differential-family.md`; `fixture/native_script/graded-2026-09-27.json` |
| Governance certificate witnesses (DRep registration: missing / wrong key / multi-cert partial) | 5 | **5 AGREE** (credential parity) | `governance-signature-phase1-differential-family.md`; `fixture/governance/graded-2026-09-27.json` |
| **Total (11 families)** | **175** | **174 AGREE, 1 divergence** | |

Selected conformance datapoints:
- **Metadata:** Amaru hashes auxiliary data **as sent**. With a non-canonical aux encoding, both
  nodes accept when the hash is over the sent bytes and both reject (`ConflictingMetadataHash`,
  with an identical expected hash) when it is over the canonical re-encoding.
- **Size:** both count the **original** bytes at the 16384 max-tx-size cap, IsValid excluded.
  This was confirmed with a separate non-canonical encoding of the body, the witnesses and the aux
  data.
- **Plutus:** the ex-unit knife-edge agrees exactly.
- **Reference scripts:** a script UTxO spent with its validator in the witness set and the same
  spend with the validator supplied as a **reference script** are each accepted by both nodes,
  with the same tx id on both sides. So Amaru resolves reference scripts conformantly. Supplying the
  **wrong** reference script is rejected by both, so the safety property holds. Inline datums vs
  datum hashes (supplied or missing) and the `is_valid=false` collateral path also agree.
  A second agent independently re-ran the exact corpus (CBOR sha256-matched) with identical
  verdicts, including on the clean serving binary (family doc, "Independent re-verification").
- **Decoder strictness:** every decode edge tested (mint/multi-asset CDDL, anchor bounds,
  metadatum size and type) is refused by **both** decoders. No Amaru decoder leniency was found in
  these families.

## Results: family last graded against cardano-node 10.7.1

Native scripts and governance-certificate witnesses were re-run on the current pair on 2026-09-27
and are in the table above. One family remains on the older reference:

| family | cases | result | doc |
|---|---|---|---|
| Governance vote authorization (committee / DRep) | 7 | all AGREE (credential parity), on Amaru 0925 (`eaf8ac3f`) vs **cardano-node 10.7.1** (`045bc187`) | `governance-vote-authorization-differential-family.md` |

It cannot be re-run on 11.1.2 by swapping the reference binary. It needs the governance-provisioned
substrate: a key-hashed committee, registered DReps and an open action, frozen in both stores. That
substrate is not retained (by design it is rebuilt locally, not committed or published), so a re-run
requires a fresh governance re-bake (`prepare-govrebake.sh`). Its cases are witness and
authorization rules, not decode edges, so the 10.7.1 vs 11.1.2 decoder difference does not bear on
them.

## Semantic field-edge matrix (Amaru 0925 vs cardano-node 11.1.2; carried forward)

Min-fee / tx-size (164180 reject, 164181 accept; gap 0), value conservation (both directions),
output min-UTxO (ADA-only and multi-asset), certificate deposit ±1, int64-max mint, validity
interval (expired, not yet valid, controls), max-tx-size (now pinned at exactly 16384 / 16385):
**all AGREE.** Detail is in `phase1-differential-coverage-2026-09-26.md`.

## Findings (Amaru)

| severity | finding | status | doc |
|---|---|---|---|
| **HIGH** | A **non-curve-point verification key** in a witness panics the ledger thread and the node exits: a remote, unauthenticated, single-transaction crash. cardano-node rejects the same tx and stays up. | CONFIRMED 2/2, independently verified, NOVEL. The panic-hunt closure found it to be the **sole reachable** panic among the sibling `.expect` / `unreachable!` sites. | `finding-amaru-vkey-noncurve-point-crash.md` |
| **MEDIUM-HIGH** | **Epoch-boundary active-nonce mismatch (#3).** Amaru rejects the valid first block of a new epoch (`Invalid VRF proof`) that cardano-node accepts. Root cause, traced in source: `store.rs::evolve_nonce` derives the epoch-3 active nonce from an **epoch-1** block reference (the parent of a tail that lags a full epoch) instead of the immediately previous epoch's block. Amaru's imported nonces and its accumulated candidate nonce match cardano. It is reached when an Amaru node forward-syncs across an epoch boundary from a single peer; it is latent in normal deployments, which re-snapshot through the bootstrap producer. | Reproduced live on native 0925 stores; byte-proof of the combine; root cause traced to `store.rs::evolve_nonce`. The exact Praos combine (`⭒` / `hashHeaderToNonce`) is to be confirmed when fixing. An earlier proxy-path stall was traced to a header re-sign artifact and ruled out, not filed (finding doc, "Confound discipline"). | `finding-amaru-epoch-boundary-active-nonce.md` |
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

## Harness hardening delivered in this campaign

- **Submit-API provenance gate** (`9536647`): `check_submit_api_refs` requires every
  `cardano-submit-api` image to match the node release under test. This was prompted by a live
  decoder skew. A 10.7.1 submit-api in front of an 11.1.2 node accepted 65-byte metadata that the
  node's decoder rejects, and the node dropped the connection (`BearerClosed`). All pairs are
  redeployed on 11.1.2, and the gate is negative-tested.
- **Reproducible fixtures** (`fixture/txedit.py`): shared re-sign helpers with self-checks. Unchanged
  witness entries are spliced as raw bytes, because cbor2 decodes tag-258 sets into hash-randomised
  Python sets, and before this fix the corpus rebuilds were not byte-reproducible.
- **Reason and read caps raised** (`e49af08`): the response read and the graded reason went from
  4096 / 400 to 16384 characters. cardano reports multi-rule failure *sets*, and the member matching
  the other node's reason could sit past the old 400-char cut. That produced a false
  REASON-DIVERGENCE on the reference-script family, where the verdicts agree. The shared grader's
  truncation threshold was aligned with the new cap in this revision, so a genuine long-reason
  divergence is not hidden as "unverified".
- **Shared grading fixes:** each family passes its own reason table (no cross-family global
  mutation); an opt-in full-response `detail` field; a classifier marker for large cardano
  rejections (`conwayutxowfailure`); and `decode_leniency` flagging.
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

- **Block-level differential:** a single-peer forward-sync of Amaru across an epoch boundary is
  exactly what finding #3 breaks, so this substrate cannot progress past that boundary until #3 is
  fixed. The rejection itself is the deliverable.
- **Served-binary re-confirmation:** reference-script + Plutus phase-2c re-run with
  identical verdicts on the clean `eaf8ac3f` serving binary (representative check; the
  serving-path patches are bootstrap-only).
- **Governance vote authorization on 11.1.2:** needs a fresh governance re-bake (see above).
  Native scripts and governance certificates were re-run on 11.1.2 on 2026-09-27.
