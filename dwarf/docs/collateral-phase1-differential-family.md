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
> Escalation **ASSESSED** (orchestrator, 2026-09-26): Amaru **relays** P1 to peers (evidence below);
> a realized consensus split is **bounded** — Amaru does not forge Praos blocks and cardano-node
> producers reject P1, so it cannot enter the honest chain. Novelty: **novel for Amaru**, upstream-fileable.
> **Not filed upstream** (operator decision 2026-09-26). Responses: `fixture/collateral/graded-2026-09-26.json`;
> escalation logs: `fixture/collateral/escalation-2026-09-26/`.

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

**Escalation (assessed 2026-09-26, orchestrator):**

- (a) **Relay: CONFIRMED.** Amaru propagates P1 over the node-to-node tx-submission protocol.
  Amaru->Amaru: a peer Amaru logs `transaction.accepted ... origin="remote"` and
  `tx_submission.responder ... outcome="inserted"` — the unwitnessed-collateral tx enters a
  second Amaru mempool. Amaru->cardano-node: Amaru offers the tx to a cardano-node 11.1.2 peer
  over tx-submission (`TxIdsSince`->`GetTxsForIds`->`SendEffect` of the tx bytes); cardano-node
  rejects it on validation (`MissingVKeyWitnessesUTXOW`, as on direct submit). Logs:
  `fixture/collateral/escalation-2026-09-26/{relay-amaru-to-amaru,relay-to-cardano}.log`.
- (b)/(c) **Block-level / consensus split: BOUNDED, not realized.** Amaru does not produce Praos
  blocks, and cardano-node producers reject P1, so P1 cannot enter the honest chain — no split is
  demonstrated. Latent risk: Amaru phase-1 block-application uses the same has-redeemers-gated
  check, so an Amaru-derived producer (or Amaru applying such a block) would admit what
  cardano-node rejects. Not exercised here.

**Severity: MEDIUM (bounded).** A genuine phase-1 **validation-rule** bypass (a required vkey
witness is not enforced) that also **propagates** — not merely a mempool-ingress policy
difference. Bounded today: it cannot reach the honest chain, and there is no direct-theft path
(collateral is seized only on `is_valid = false`, which requires scripts -> redeemers -> the
enforced branch). More significant than the two LOW mempool-ingress observations
(submit-trailing-bytes, mempool-conflict).

**Novelty (orchestrator, 2026-09-26): NOVEL for Amaru; upstream-fileable.**

- No Amaru issue tracks the no-redeemers collateral-witness gap.
- Amaru PR **#744** ("Fix Required Vkey Witnesses", merged 2026-04-10) added the collateral-input
  vkey-witness requirement, but `collateral.rs` gates it behind `if !has_redeemers { continue; }`
  before `require_verification_key_witness` — leaving the **no-redeemers** case (P1) unenforced.
  P1 is the residual hole of a known-incomplete fix.
- **dingo #4350** ("enforce collateral vkey witnesses independently of phase-2 redeemers",
  closed/completed) is the **identical bug in another node**, already fixed there — prior art for
  the class and the correct fix.
- Amaru **#1004** ("Correct behavior around invalid phase-two transactions") is possibly related
  but appears distinct (phase-2-invalid collateral consumption, not the no-redeemers witness gap).

**Not filed upstream** (operator decision 2026-09-26). Fileable when chosen, with the #744 + dingo #4350 references.

## Reproduce

```bash
PYTHON=/path/to/python-with-cbor2 fixture/collateral/build.sh    # deterministic rebuild
python3 workload/collateral_differential.py --amaru <amaru submit> --cardano <cardano submit>
python3 workload/collateral_differential.py ... --single p1-foreign-collateral-no-redeemers  # clean mempool
```

`cardano-cli --mint-execution-units` takes `(steps, memory)`, not `(memory, steps)`.

## Live block-apply confirmation (2026-09-30) — accept-INVALID-BLOCK

The submit-level finding (amaru admits a no-script tx with unwitnessed foreign collateral; cardano
rejects) is upgraded to a **live block-apply** demonstration: amaru adopts, block-fetches, and
**applies** a served forged block carrying such a tx, and stays up — a block cardano-node rejects.

**Substrate:** pair1, amaru eaf8ac3f (host binary, submit :3210), cardano-node 11.1.2. Forged block
served over one socket (amaru dials its upstream :3001 as chain-sync initiator) via the PATH-B
forger (pool 97b0 KES/VRF, forced epoch-3 nonce 3a5e3601; see reports/amaru-block-apply-upgrades-evidence/).

**Body (forged block 280e37cbe927ced400b67a246e93090b3c7417d20c54895989f1379b88714362, slot 1211, height 234):**
a no-script tx spending funding `9708b921…#0`, with **foreign, unwitnessed** collateral
`323b8a57466ce10c0bd42b3f6a6f9b941ff0348d73cf677bd75f11746daed3cd#0` (an address whose key we do not
control), no redeemers. amaru skips the foreign-collateral witness requirement at
`crates/amaru-ledger/src/rules/transaction/phase_one/collateral.rs:76` (`if !has_redeemers { continue }`).

**Submit pre-flight (both nodes, 2026-09-30):** amaru `:3210` → **202 ACCEPTED** (txid
`56dfad37a46da88adec8403214aead654832cd9d5cd81b3999be755ef38bf295`); cardano `:8110` → **400**,
missing collateral witness for keyhash `d52f5226` (the foreign address).

**Block-apply result:** amaru `chainsync.intersect_found` highest `[1211, 280e37cb…, 234]` →
`tip.adopt slot=1211` → serve block-fetch `block_served=True` (237-byte body delivered) →
`epoch_transition.apply epoch=3` → amaru **stays alive** (submit 400), tip at the forged block. No
witness error, no crash. cardano-node would reject the block (missing collateral witness). ⇒
would-accept-INVALID-BLOCK.

**Evidence:** `antithesis/cardano_amaru_adversarial/fixture/collateral/` (source) +
`/home/nigel/forge-work/pathb/outputs/forged-block/artifacts/collateral-apply-2026-09-30/`
(block.json, serve.log, amaru-collateral-apply.txt) + `artifacts/collateral-preflight-evidence.txt`.
Severity: MEDIUM (accept-invalid at block level; foreign collateral is not attacker-owned so bounded).
