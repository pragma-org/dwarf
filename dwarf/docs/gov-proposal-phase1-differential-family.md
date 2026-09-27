# Conway governance-proposal phase-1 differential family

A DWARF phase-1 differential family (extends `workload/mixed_phase1.py`, grades through
`stake_pool_differential.grade`). It covers **proposal submission**: the GOV proposal rules
(deposit, return account and its network, prev-action lineage, hard-fork succession,
committee-update rules, the guardrails policy hash, ParameterChange well-formedness) and the
anchor decode edges. It is distinct from the DRep-certificate family and the vote-authorization
family. It adds coverage; it is not a finding.

> **STATUS (2026-09-27): GRADED, 24/24 AGREE. Amaru v10.11.20260925 (`eaf8ac3f`) is
> CONFORMANT with cardano-node 11.1.2 (`fef83fed`)** on every case:
> - The 16 phase-1 violations are `AGREE`: same verdict, a shared reason class, and the same
>   parity token where one applies (deposit amount, credential, fake prev id, guardrails hash).
> - The 2 anchor decode edges are `AGREE`: both nodes DECODE-reject. There is no Amaru decoder
>   leniency.
> - The 6 controls are `AGREE`: both nodes accept, with the same tx id.
>
> There is no verdict divergence and no reason divergence. The only differences are
> precedence-*reporting* differences: Amaru reports the first failing check, cardano reports the
> whole set (see below). Binaries were verified with `--version`. The run used the dedicated,
> mempool-isolated pair 4, reset before the violation run and before each control. Full responses:
> `fixture/gov_proposal/graded-2026-09-27.json`.

## Reachability (probe-first, 2026-09-27)

- **Gov state at the freeze:** pv 10.0 (post-bootstrap), 0 proposals, every root null
  (Committee, Constitution, HardFork, PParamUpdate), a 7-member script-hash committee (threshold
  2/3), and constitution guardrails script `fa24fb30…`. `govActionDeposit` = 100 000 ADA.
- **No usable pre-registered reward account exists.** Used as a return account, the genesis
  delegator `3e521ccc…` and the stock pool reward account `eb481c0e…` are "does not exist" on
  **both** nodes.
- **The unlock:** register the return account **in the same tx**. Both ledgers run GOV against
  the post-CERTS state, and stake-reg + InfoAction is accepted by both with the same tx id. So
  every case (except `return-unregistered`) registers a fresh key and returns to it, and only the
  targeted rule fails.
- **Unreachable here:** proposals chained to an in-flight proposal. The frozen substrate cannot
  mine one, and a tx cannot reference its own proposal id (the id is the hash of the body that
  would contain it). So Amaru's `pending_hard_fork_version` in-flight branch (which carries an
  `unreachable!()`) cannot be exercised. Also unreachable: ParameterChange / TreasuryWithdrawal
  **with** the correct guardrails policy (the Plutus guardrails script must run; that is the
  phase-2 lane), and votes on these proposals.

## Construction

`fixture/gov_proposal/build.sh` builds 20 cases with cardano-cli. `edits.py` derives 4 more from
`info-valid.tx`, using the shared `fixture/txedit.py` (re-sign + self-checks):
- `anchor-url-129` / `anchor-url-128-valid`: the url length edge and its boundary control.
- `anchor-hash-31`: a 31-byte anchor data hash.
- `return-wrong-network`: the return reward account's header set to mainnet (`0xe1`).
  **cardano-cli trap:** `create-info --testnet --deposit-return-stake-address <stake1…>`
  silently re-encodes the mainnet address as testnet (`0xe0`). The first CLI-built version was
  therefore byte-identical in intent to `info-valid`, and both nodes accepted it. It is now a raw
  edit with an asserted header byte.

Every case's targeted field was verified by decoding the built bytes: deposits, header byte,
prev ids, versions, expiry, add/remove sets, policy hash, maxTxSize, url length, hash length.
The offline `cardano-cli debug transaction view` pre-check rejects exactly the 2 decode edges
("Text exceeds 128 bytes"; "Expected 32 number of bytes, but got 31").

Rebuild: `PYTHON=<python with cbor2+cryptography> fixture/gov_proposal/build.sh`. The rebuild is
byte-reproducible: witness sets are spliced as raw bytes (`txedit.raw_map`), because cbor2 decodes
tag-258 sets into hash-randomised Python sets.

## Result (pair 4, 2026-09-27)

| case | cardano-node 11.1.2 | Amaru 0925 | grade |
|---|---|---|---|
| deposit-low / -high | `ProposalDepositIncorrect` (99999999999 / 100000000001) | `incorrect proposal deposit: provided …` | AGREE + amount |
| return-unregistered | `ProposalReturnAccountDoesNotExist` | `proposal return account does not exist` | AGREE + credential |
| return-wrong-network | `ProposalProcedureNetworkIdMismatch (… Mainnet …)` | `proposal return address has wrong network` | AGREE |
| hardfork / noconfidence / constitution -prev-nonexistent | `InvalidPrevGovActionId` | `invalid previous governance action id` | AGREE + prev id |
| hardfork-skip-major (12.0) | `ProposalCantFollow … pvMajor = Version 12` | `hardfork version (12, 0) cannot follow version (10, 0)` | AGREE |
| hardfork-minor-on-major-bump (11.1) | `ProposalCantFollow` | `cannot follow version` | AGREE |
| hardfork-same-version (10.0) | `ProposalCantFollow` | `cannot follow version` | AGREE |
| committee-expiry-too-small (epoch 1) | `ExpirationEpochTooSmall` | `committee member expiration epoch 1 is not greater than current epoch` | AGREE |
| committee-conflicting | `ConflictingCommitteeUpdate (349e55f8…)` | `conflicting committee update` | AGREE |
| ppupdate-no-policy / -wrong-policy, treasury-no-policy | `InvalidGuardrailsScriptHash … fa24fb30…` | `invalid guardrails script hash: … expected Some(fa24fb30…)` | AGREE + guardrails hash |
| ppupdate-malformed-zero | `InvalidGuardrailsScriptHash` + `MalformedProposal` | `malformed parameter change proposal: max_transaction_size cannot be 0` | AGREE (precedence) |
| anchor-url-129 | `DeserialiseFailure` | `text exceeds 128 bytes: got 129` | AGREE (decode) |
| anchor-hash-31 | `DeserialiseFailure` | `invalid hash size` | AGREE (decode) |
| info-valid, hardfork-major-valid (11.0), hardfork-minor-valid (10.1), committee-add-valid (epoch 50), two-proposals-valid, anchor-url-128-valid | 202 | 202, same tx id | AGREE |

**Precedence-reporting only.** Amaru returns the first failing proposal check. The order is
deposit, then prev action, then return account, then network, then the action-specific rule; for
ParameterChange, well-formedness comes before the guardrails hash. cardano-node reports the whole
failure set. Seen in `ppupdate-malformed-zero`, and in the probe's deposit + unregistered-return
combination. Graded as agreement by class-set intersection, like the stake-pool withdrawal
precedence case.

**Epoch skew:** Amaru's store is at epoch 2 and cardano's at epoch 3. Expiry epoch 1 is too small
on both, and expiry epoch 50 is valid on both. Epochs 2–3 are deliberately not used.

## Oracle (fail-closed)

`workload/gov_proposal_differential.py` uses the shared grade (verdict → reason class →
parity-token parity; MASKED/unavailable = INCONCLUSIVE; a truncated reason = REASON-UNVERIFIED; a
decode edge that one node decodes further is flagged `decode_leniency`). The reason markers
are cardano `ConwayGovFailure` constructors and the Amaru `proposals.rs` errors. Note that
cardano-ledger renamed `InvalidPolicyHash` to `InvalidGuardrailsScriptHash`; 11.1.2 emits the
latter, and the marker accepts both. The first run graded the policy cases as REASON-DIVERGENCE
on the stale name; that was a marker fault, fixed, not a node difference.

Run the violations: `python3 workload/gov_proposal_differential.py --amaru URL --cardano URL`.
Run the controls one at a time with `--control CASE`, each after a mempool reset. **An
unexpected accept consumes the funding UTxO** and MASKs every later case on cardano (Amaru admits
conflicting inputs), so reset and re-run after any accept in a violation run.

Shared-code changes: `fixture/txedit.py` (re-sign helpers, reused by `mint_burn/edits.py`, whose
output is byte-identical); `decode_leniency` moved from the mint/burn driver into the shared
`stake_pool_differential.grade`.
