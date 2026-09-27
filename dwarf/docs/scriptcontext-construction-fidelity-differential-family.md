# ScriptContext (Conway TxInfo) construction-fidelity differential family

The highest-impact Plutus question: does Amaru build each `ScriptContext` (TxInfo) field with the
same value/encoding cardano-node does? If any field differs, a script that inspects it passes on
one node and fails on the other — an `is_valid` **consensus** divergence (a phase-2 chain split).
Method: PlutusV3 minting policies (aiken `v1.1.24`) that assert a TxInfo field against a
redeemer/tx-supplied expectation; a correct-value control (both accept) and a wrong-expectation
variant (both reject). Driver `workload/plutus_differential.py`.

> **STATUS (2026-09-27): 18/18 AGREE.** Amaru v10.11.20260925 (`eaf8ac3f`, clean labelled binary)
> is CONFORMANT with cardano-node 11.1.2 (`fef83fed`) on every reachable ScriptContext field
> tested. **No `is_valid` divergence.** Graded on refpair; evidence
> `fixture/scriptctx/graded-2026-09-27.json`.

## Fields & result

| field | control (accept) | wrong-expectation (reject) | result |
|---|---|---|---|
| `txInfoFee` | fee == 2000000 | == 2000001 | AGREE |
| `txInfoMint` | own-policy COLL qty == 1 | == 2 | AGREE |
| `txInfoInputs` (count) | length == 1 | == 2 | AGREE |
| `txInfoInputs` (content) | first input's `output_reference.transaction_id` == funding txid | == a different txid | AGREE |
| `txInfoReferenceInputs` | with 1 ref input, length == 1; and with none, length == 0 | length == 0 while 1 present | AGREE |
| `txInfoRedeemers` (Conway) | length == 1 (the mint redeemer) | == 2 | AGREE |
| `treasury_donation` (Conway) | `--treasury-donation 5` → `Some(5)` | `Some(6)` | AGREE |
| `current_treasury_amount` (Conway) | no value → `None` | (control only) | AGREE |
| `txInfoInputs` **ordering** (byte-crafted non-canonical set) | assert inputs[0].txid == canonical-first | == submit-first | AGREE (both canonicalize) |

Correct controls accept on both with the same tx id; wrong-expectations reject on both with a
phase-2 tag mismatch. The Conway-specific `treasury_donation` / `current_treasury_amount` — the
newest, least-tested ctx fields — construct identically on both nodes.

## Inputs ordering (byte-crafted — the highest-divergence-likelihood aspect)

Conway tx inputs are a set the ledger canonically sorts by `OutputReference`. `cardano-cli` emits
canonically-sorted bodies, so to probe an ordering divergence I **byte-crafted** a tx (via
`fixture/txedit.py` / cbor2 in `/tmp/collat-venv`) with a 2-input set in **reversed
(non-canonical) order** — `[9aecf690…#2, 9708b921…#0]` — and re-signed the payment witness over
the edited body (redeemers/scripts spliced back byte-identical). Result, on both nodes:

- **Neither node decode-rejects** the non-canonically-ordered input set.
- **Both canonicalize `ctx.inputs` identically:** the variant asserting `inputs[0].txid ==`
  the canonical-first (`9708b921`) **accepts on both** (same tx id `61e96574…`); the variant
  asserting `inputs[0].txid ==` the submit-first (`9aecf690`) **rejects on both**.

So Amaru sorts the ScriptContext inputs by `OutputReference` exactly as cardano-node does — no
`is_valid` split and no decode-vs-accept split on the aspect most likely to diverge.

## Not reachable (documented, not run)

- **`txInfoVotes` / `proposal_procedures`.** A vote or proposal in the same tx as a script witness
  needs a registered DRep + an open governance action (and the script as a vote/propose witness);
  refpair carries neither. Substrate-limited — would need a governance-provisioned re-bake (as the
  governance families used).

## Reproduce

```
# aiken validators in fixture/scriptctx/{validators.ak, validators_p2e.ak}; compiled *.plutus committed.
fixture/scriptctx/build.sh            # mint txs; REFC env = a read-only ref input for the ref-input cases
/home/nigel/rebake-refscript/reset-refpair.sh   # FULL reset (clears cardano's mempool too)
cd workload && python3 plutus_differential.py --corpus ../fixture/scriptctx --amaru :3214 --cardano :8114
python3 plutus_differential.py --corpus ../fixture/scriptctx --single ctxfee-ok …   # one accept per reset
```
Exit 0 all agree / 1 divergence / 2 inconclusive. Keys testnet-only.
