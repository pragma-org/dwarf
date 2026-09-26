# Collateral / redeemer phase-1 differential family

A DWARF phase-1 differential family (extends `workload/mixed_phase1.py`) covering the
**collateral** rules (Alonzo+ collateral inputs, collateral return, total collateral), and
the **redeemer, execution-unit and script-integrity** rules that apply to a Plutus
transaction. It is separate from the min-fee, native-script, governance and stake/pool
families.

> **STATUS (2026-09-26): GRADED, 1 LIVE DIVERGENCE.** Amaru v10.11.20260925 (`eaf8ac3f`)
> is compared with cardano-node 11.1.2 (`fef83fed`).
>
> - 12 violations are `AGREE`: same verdict, a shared reason class, and credential parity
>   where it applies.
> - 5 single-use controls are `AGREE`: both nodes accept, with the same tx id.
> - **P1 is a `VERDICT-DIVERGENCE`**, reproduced 2 of 2 times on clean mempools. Amaru
>   accepts a transaction with no scripts that names someone else's UTxO as collateral
>   without that owner's signature. cardano-node rejects it with `MissingVKeyWitnessesUTXOW`.
>   See [P1](#p1-unwitnessed-foreign-collateral-no-redeemers).
>
> The escalation questions (relay, block inclusion) are **OPEN** pending the orchestrator's
> assessment. Not filed upstream. Full responses are in `fixture/collateral/graded-2026-09-26.json`.

## Substrate and identities

The run used a dedicated, mempool-isolated pair built from the re-bake genesis
(`fixture/funding/genesis`). Both node identities were read from the binaries themselves:

- **cardano-node 11.1.2 (`fef83fed`)**: `cardano-node --version` reports git rev
  `fef83fed01d7926f3de83b3b917be5a4a48768b5`.
- **Amaru 10.11.20260925 (`eaf8ac3f`)**: the startup `build.version` line reports
  `git_commit=eaf8ac3fa309f2a174c045454c276194c9312b8d git_dirty=false`.

Each mempool was reset before every single-use case.

## Reachability (probe-first, 2026-09-26)

These facts come from read-only queries of the live 11.1.2 reference:

- **Cost models are PlutusV1 and PlutusV3 only.** The repo's PlutusV2 scripts cannot be used
  on this chain.
- Every case mints 1 token under a minimal **PlutusV3 always-succeeds** policy: `(lam _ ())`,
  CBOR `46450101002499`, policy `186e32fa…`. The mint gives the transaction a redeemer, so the
  collateral rules apply without any script-locked UTxO. cardano-node evaluates the script:
  the `valid-minimal` control is accepted after phase 2.
- `collateralPercentage` 150, `maxCollateralInputs` 3, `maxTxExUnits` 14 000 000 mem /
  14 000 000 000 steps.
- The ledger holds the 7 genesis `initialFunds` UTxOs. `9708b921…#0` is ours. The other 6 are
  locked by keys DWARF does **not** hold. Those make the foreign-collateral cases reachable
  with no setup transaction.

Every case spends `9708b921…#0`, with **fee 400001 ≫ min** (the minimum collateral is then
`ceil(1.5 × 400001) = 600002`) and no validity interval. The script-integrity hash is computed
from `pparams.json`, the live protocol parameters.

`redeemers.py` builds the two cases that cardano-cli cannot express (missing redeemer, extra
redeemer). It edits only the witness set and the 32-byte integrity hash. Before editing, it
self-checks that its hash computation reproduces the hash the CLI wrote.

**Not measurable here:**

- **Unknown collateral input.** cardano-node reports it as `BadInputsUTxO`. The shared
  `mixed_phase1` classifier treats that response as MASKED (funding input already consumed),
  by design.
- **Collateral locked at a script address.** This needs a script-locked UTxO, which only a
  single-use setup transaction could create.

## Cases & result

Violations are idempotent. Single-use cases were each run once, after a mempool reset.

| case | expected | cardano-node 11.1.2 | Amaru 0925 | grade |
|---|---|---|---|---|
| no-collateral | reject | `{NoCollateralInputs, InsufficientCollateral}` | `No collateral was provided` | AGREE |
| insufficient-at-boundary | reject | `InsufficientCollateral` | `effective collateral value (=600001) is insufficient; at least 600002` | AGREE |
| total-collateral-mismatch | reject | `IncorrectTotalCollateralField` | `declared collateral (=699999) does not equal effective collateral (=700000)` | AGREE |
| negative-asset-return | reject | `CollateralContainsNonADA` | `collateral has non-zero delta` | AGREE |
| return-too-small | reject | `BabbageOutputTooSmallUTxO` | `output doesn't contain enough Lovelace` | AGREE |
| return-wrong-network | reject | `WrongNetwork` | `address has the wrong network` | AGREE |
| too-many-collateral | reject | `{TooManyCollateralInputs, MissingVKeyWitnessesUTXOW}` | `too many collateral inputs: provided: 4 allowed: 3` | AGREE (precedence) |
| foreign-collateral-redeemer | reject | `MissingVKeyWitnessesUTXOW(19b9a38f…)` | `missing required signatures … [19b9a38f…]` | AGREE + cred |
| exunits-too-big | reject | `ExUnitsTooBigUTxO` | `transaction execution units exceeded` | AGREE |
| integrity-hash-mismatch | reject | `PPViewHashesDontMatch` | `script integrity hash mismatch` | AGREE |
| missing-redeemer | reject | `CollectErrors (NoRedeemer (ConwayMinting …))` | `missing redeemers: [[Mint, 0]]` | AGREE |
| extra-redeemer | reject | `ExtraRedeemers` | `extraneous redeemers` | AGREE |
| valid-minimal | accept | 202 `eef410b2…` | 202 `eef410b2…` | AGREE |
| valid-with-return | accept | 202 `6055f955…` | 202 `6055f955…` | AGREE |
| valid-at-boundary | accept | 202 `92caf30a…` | 202 `92caf30a…` | AGREE (**no rounding over-strictness**) |
| p2-total-mismatch-no-redeemers | accept | 202 `30608b18…` | 202 `30608b18…` | AGREE |
| p1-control-no-collateral | accept | 202 `c4214d2b…` | 202 `c4214d2b…` | AGREE |
| **p1-foreign-collateral-no-redeemers** | reject | **`MissingVKeyWitnessesUTXOW(19b9a38f…)`** | **202 accepted `1e26b8ca…`** | **VERDICT-DIVERGENCE** |

Reason-reporting notes (NOT divergences):

- cardano-node reports the **set** of failed rules; Amaru reports one reason. In the
  co-occurring cases (no-collateral, too-many-collateral), Amaru's single reason is a member
  of cardano's set.
- Amaru checks the collateral input count **before** it looks up the inputs or checks their
  witnesses.
- In the `missing-redeemer` case, the body carries the integrity hash the ledger expects for
  an **empty** redeemer map. While a Plutus script is needed, Conway still requires a hash;
  omitting it adds `PPViewHashesDontMatch` on cardano-node.

## P1: unwitnessed foreign collateral, no redeemers

**The transaction** (`fixture/collateral/p1-foreign-collateral-no-redeemers.tx`):

- 242 bytes, sha256 `6f35e75b…`, txid `1e26b8ca3fdb0f66723f540d51f7f9ca9b752bdc3e2858321c37f6de5c09a6a2`.
- Input: `9708b921…#0`.
- Collateral: `0b1e73fe…#0`. This is a genesis UTxO whose payment key
  `19b9a38fc0a2a5ac858598a998cf293f20cfd123e542c0e1f2fd67d1` DWARF does not hold.
- One output, fee 400001, **no scripts, no redeemers**.
- Exactly one vkey witness: the funding key `e5a5ddb0…`.

**Observed** (2 of 2 runs, each on a freshly reset pair, not MASKED):

- **cardano-node 11.1.2 (`fef83fed`):** rejects it with
  `ConwayUtxowFailure (MissingVKeyWitnessesUTXOW [KeyHash "19b9a38f…"])`. That is the
  collateral owner's key, and nothing else.
- **Amaru 0925 (`eaf8ac3f`):** the submit API returns the txid, and its log shows
  `amaru::mempool: transaction.accepted id="1e26b8ca…" origin="local"`.

**Artifacts ruled out:**

- **The collateral UTxO exists on both sides.** cardano-node's `query utxo` shows it. On the
  Amaru side, `collateral.rs` looks the input up *before* the branch below, so a missing input
  would have been rejected as `Unknown input`.
- **The collateral field is the only difference.** `p1-control-no-collateral` is the same
  transaction without the collateral field. Both nodes accept it, with the same txid.
- **The gap is limited to the no-redeemers branch.** `foreign-collateral-redeemer` uses the
  same foreign collateral, but the transaction **has** a redeemer. Both nodes reject it, and
  both name `19b9a38f…`.

**Root cause (source, Amaru `eaf8ac3f`):** in
`crates/amaru-ledger/src/rules/transaction/phase_one/collateral.rs` (`execute`), the loop over
collateral inputs runs `if !has_redeemers { continue; }` **before**
`context.require_verification_key_witness(hash)`. So when a transaction has no redeemers,
Amaru never adds the collateral owners to the required-witness set.

The ledger requires those witnesses regardless of redeemers:

- In Babbage and Conway, `spendableInputsTxBodyF` is `inputs ∪ collateralInputs`
  (`babbageSpendableInputsTxBodyF`).
- `getShelleyWitsVKeyNeededNoGov` requires a vkey witness for every spendable input.

**Impact (first assessment):**

- **Validity divergence:** Amaru admits into its mempool a transaction that the reference
  ledger rejects.
- **No direct theft path:** collateral is consumed only when `is_valid = false`, which needs
  a transaction with scripts. Such a transaction has redeemers, and that branch enforces the
  witness (see `foreign-collateral-redeemer`).

**OPEN (escalation, pending the orchestrator's assessment):**

- (a) Does Amaru **relay** such a transaction to its peers?
- (b) Would an Amaru-produced block **include** it? cardano-node would reject that block: a
  block-level / consensus divergence, the same question as the min-fee escalation.
- (c) Is there any path where the difference reaches an applied ledger state? cardano-node
  never produces such a transaction, so the risk runs in the Amaru → cardano direction.

The novelty check against Amaru's issues and wiki is also open. **Do not file upstream yet.**

## Reproduce

```bash
PYTHON=/path/to/python-with-cbor2 fixture/collateral/build.sh    # deterministic rebuild
python3 workload/collateral_differential.py --amaru <amaru submit> --cardano <cardano submit>
python3 workload/collateral_differential.py ... --single p1-foreign-collateral-no-redeemers  # clean mempool
```

`cardano-cli --mint-execution-units` takes `(steps, memory)`, not `(memory, steps)`.
