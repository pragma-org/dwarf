# Finding: Amaru accepts a transaction output to a bare stake address (+ latent inputs panic)

**Severity:** HIGH — accept-invalid consensus divergence (live-confirmed) which manufactures the
precondition for a **LIVE-reproduced block-apply `unreachable!()` panic / DoS** (a single crafted
block, served to an amaru follower, halts its apply thread; live-reproduced 2026-09-30 — see
"Live block-apply confirmation" below).
**Class:** ledger/decode divergence — `amaru accepts a transaction cardano-node rejects`.
**Amaru:** `eaf8ac3f` (v10.11.20260925). **cardano-node:** `11.1.2`. **Date:** 2026-09-27.
**Substrate:** pair4 (amaru consumer `:3213` vs cardano-node `:8113`), full mempool reset per submit.

## (A) Accept-invalid — stake-address output (live, 2×)

A Cardano transaction output must be to a **payment** address (enterprise / base / pointer /
bootstrap). A bare **stake (reward) address** (`Address::Stake`, testnet header `0xe0`) is not a
valid output address. cardano-node rejects such a transaction at the **decoder**; amaru **accepts**
it.

- amaru `:3213` → **202 accept** (txid `48ea2e4a…`), reconfirmed 2×.
- cardano-node `:8113` → **400** `DecoderErrorDeserialiseFailure "Shelley Tx"` (the stake-address
  header is invalid in an output position), 2×.

**Construction (structure-preserving):** build a normal valid tx paying my own enterprise address
(`0x60` + 28-byte hash), flip the output address header `0x60 → 0xe0` (bare stake key, **same
28-byte hash, same length** — no length fixup), re-encode the body and attach a hand-crafted, valid
ed25519 witness over the new body (cardano-cli's decoder refuses to sign a non-payment output; the
crafted witness is genuinely valid, which amaru's `202` confirms). The tx is otherwise fully valid
and balanced — the only deviation is the output address type.

**Source:** `rules/transaction/phase_one/outputs.rs::execute` runs `inherent_value` (min-ada /
value-size), `validate_network` (which only compares `output.address.network()`), and
`validate_bootstrap_attributes`. There is **no arm rejecting `Address::Stake`** as an output
address, so a stake-address output passes and `context.produce(input, output)` records the
stake-address UTxO. cardano rejects it earlier, at CBOR/address decode.

## (B) Latent panic / DoS on spend — `inputs.rs:122`

Having produced a stake-address UTxO (A), spending it hits:

```rust
// rules/transaction/phase_one/inputs.rs:122
Address::Stake(_) => unreachable!("found a stake address in a TransactionOutput"),
```

`inputs::execute` resolves each spend input's address to register its required witness; a resolved
stake-address UTxO drives the match into the `unreachable!()` → **thread panic**. The same class of
self-flagged panic exists in `collateral.rs:93` (Byron `RedemptionVoucher`) and `:98` (`Stake`) —
both carry the source comment *"FIXME: Not unreachable at all?"*.

**Block-apply reachability: SOURCE-PROVEN** (dwarf-v4-fd) — a crafted block
`{tx1: creates the bare-stake output, tx2: spends it}` reaches `inputs.rs:122` on amaru's apply
path and panics:
1. tx1: `outputs.rs::execute` has no reject arm for `Address::Stake` → accepted (live, A) and
   `outputs.rs:101 context.produce(...)` lands the stake-address output in `state.utxo.produced`
   (`diff_set.rs:32`).
2. the block loop (`rules/block.rs:257-261`) threads the **same** `&mut context` through each tx, so
   tx1's produce is visible to tx2.
3. tx2: `inputs.rs:91 context.lookup(input)` → `validation.rs:105-106`
   `self.utxo.get(input).or_else(|| self.state.utxo.produced.get(input))` resolves the intra-block
   produced stake-address output → `Some`.
4. `inputs.rs:104-122 match &output.address { … Address::Stake(_) => unreachable!(…) }` → **thread
   panic**.

Resolution is via the intra-block **produced set** (`validation.rs:106`), so it is reachable on
block-apply **independent of** the `UnresolvedInputPolicy` Defer/Reject distinction. cardano rejects
tx1 at the decoder, so it never produces such a UTxO and never reaches the panic — amaru **crashes**
where cardano cleanly rejects. Same crash class as the vkey-non-curve-point panic finding. The
collateral sibling (`collateral.rs:93/:98`) is likewise reachable, in the **with-redeemer** variant
(the match is after the `:76 !has_redeemers` short-circuit).

**Live status: LIVE-REPRODUCED (2026-09-30).** (B) is no longer source-proven only — the crash was
reproduced live at block-apply via the adversary-producer sidestep (no patch to amaru's validation).

### Live block-apply confirmation (2026-09-30, amaru `eaf8ac3f` / cardano-node `11.1.2`)

The forward-sync wall was sidestepped with an adversary producer: a forged block is served to amaru
over a single socket (chain-sync + block-fetch responder), forged against amaru's own buggy
epoch-boundary nonce (finding #3) so amaru's leader check passes, with the crafted body carrying the
2-tx stake-address payload. Procedure: reset amaru to a fresh tip-1199 store, point it at the serve,
let it adopt + block-fetch + apply.

- **Forged block** `cd610044…` at slot 1211 / height 234, body = 2 txns: tx0 creates a bare
  stake-address output (header `0xe0`), tx1 spends that stake-address UTxO.
- amaru **adopted** the header (`tip.adopt slot=1211`), **block-fetched** the body
  (`block_served=True`), and on **apply** panicked:
  ```
  thread 'ledger' panicked at crates/amaru-ledger/src/rules/transaction/phase_one/inputs.rs:122:34:
  internal error: entered unreachable code: found a stake address in a TransactionOutput
  ```
  amaru's node went **down** (submit API → connection refused). cardano-node **decode-rejects** the
  same block (the stake-address output is invalid in an output position), so it never applies it and
  never crashes.
- **Independent submit-level re-confirmation:** the same tx1 (stake-address output), pre-flighted at
  mempool admission → amaru `202` accepts / cardano-node `400`
  `Decoding Shelley Address: Invalid header. Unused bits are not suppose to be set: 0b11100000`.

**Result:** a single crafted block, served to an amaru follower, **halts its ledger thread** — a
remotely-triggerable DoS at block-apply, on a block cardano-node rejects. Evidence:
`artifacts/stakeaddr-crash-2026-09-30/` (`block.json`, `serve.log`, `amaru-crash-log.txt`,
`amaru-pair1-full.log`) + `tx1-stakeaddr.cbor` + `tx1-preflight-evidence.txt`. The reusable bridge
(forge-block + crafted-body serve + apply-watch) also drove three sibling block-apply demos
(cert-phantom and collateral accept-invalid-block, and an is_valid=false benign-apply control).

## Realization (scoped)

(A) is a live consensus divergence at mempool admission on an amaru consumer/follower (amaru does not
forge Praos blocks); it is consensus-relevant because block-apply runs the same outputs rule. (B) is
a source-evident latent DoS that (A) directly enables; its live block-apply trigger is behind the
forward-sync wall and pending fd's intra-block-resolution verdict.

## Reproduce

```
# build a valid tx paying an enterprise addr; flip output header 0x60->0xe0 in the body;
# re-encode the body (cbor2) and hand-craft a valid ed25519 witness over it (cardano-cli won't sign
# a non-payment output). reset, submit crafted tx to amaru :3213 (202) and cardano :8113 (decode 400).
```
Keys testnet-only.

## Fix direction

`outputs.rs` must reject an output whose address is not a payment address (mirror cardano's decode /
`outputs` validation), and `inputs.rs:122` / `collateral.rs:93,98` must return a typed error instead
of `unreachable!()` for any address shape that can reach them.

## Reproduce via DWARF

**Scenario:** `ledger-block-apply-stake-address-output-crash-differential-amaru-cardano-node`

```
dwarf run --scenario dwarf/scenarios/ledger-block-apply-stake-address-output-crash-differential-amaru-cardano-node.yaml --profile block-apply-adversary
```

Whole block-apply suite: `bash dwarf/profiles/block-apply-adversary/run.sh`. The scenario encodes
the block-apply differential via the `block_apply_differential` load + `block_apply_outcome_matches`
assertion (expected outcome: amaru crash / accept-invalid vs cardano-node reject); the submit-level
differential and evidence are in the sections above. Bridge primitives live in `dwarf/block_apply/`
(see its README); `forge_block` + `serve_crafted_block` require the external cardano-crypto forge
and serve responder plus a live amaru/cardano pair.

## Latest-version submit reconfirm (2026-10-01)

Reconfirmed on amaru `eaf8ac3f` with a clean **4-element** bare-stake-address transaction (output = header `0xe0` + key hash), isolated from the separate tx-3-element arity finding: amaru `:3210` → **202 accepted**; cardano-node → decode-reject `Decoding Shelley Address: Invalid header. Unused bits are not suppose to be set: 0b11100000`. Reproduce via DWARF: scenario `ledger-submit-stakeaddr-output-differential-amaru-cardano-node`.
