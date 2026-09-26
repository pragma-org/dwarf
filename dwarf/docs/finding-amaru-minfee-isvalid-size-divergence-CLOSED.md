# CLOSED — Amaru min-fee tx-size IsValid off-by-one (differential vs cardano-node)

> **STATUS: CLOSED / HISTORICAL. Not a live bug; do not file upstream; not a client-facing
> deliverable.** Detected by the DWARF phase-1 differential harness on **amaru 10.11.20260807**
> (commit `493bffba`); the currency retest establishes it is **already fixed upstream** in
> **amaru 10.11.20260918** (commit `ea1f34e4`), which now matches cardano-node exactly (gap 0).
> This record documents the divergence, its mechanism, its (low) severity, and the fix — as a
> rigorous negative result and a validation that the harness catches real min-fee conformance bugs.

## Summary

For a transaction that is **byte-identical on the wire** and validated under **byte-identical
protocol parameters**, amaru 10.11.20260807 and cardano-node 11.1.2 disagreed on the **minimum
fee** by exactly **44 lovelace (one byte)**, because amaru's mempool min-fee **size** included the
1-byte Alonzo `IsValid` flag that cardano-ledger deliberately excludes. Amaru therefore rejected,
as underfee, a fee band that cardano-node accepts. Fixed in 10.11.20260918.

## Evidence (frozen reference + amaru, new committed-key UTxO 9708b921…#0)

Params on both sides (identical): `min_fee_a = 44`, `min_fee_b = 155381`, protocol 10.0.
Test tx: 1-in/1-out self-send spending `9708b921…#0`, **null auxiliary_data**, RFC-8949-minimal,
**201 bytes on the wire** (raw == canonical, verified with cbor2 — not a canonicalization issue).

| fee (lovelace) | cardano-node 11.1.2 | amaru 807 (493bffba) | amaru 0918 (ea1f34e4) |
|---:|---|---|---|
| 164180 | reject `FeeTooSmallUTxO {supplied 164180, expected 164181}` | reject | reject ("declared fee 164180 below minimum 164181") |
| **164181** | **accept** (= 44×**200**+155381) | **reject** | **accept** |
| 164224 | accept | reject | accept |
| **164225** | accept | **accept** (= 44×**201**+155381) | accept |

- cardano min = **164181** → fee size **200** = serialized_len − 1.
- amaru **807** min = **164225** → fee size **201** = the full serialized length (IsValid included).
- amaru **0918** min = **164181** → fee size **200** = cardano exactly → **gap 0**.

Divergent band on 807: **[164181, 164224]** — accepted by cardano, rejected by amaru 807.

### Metadata cross-check (mechanism isolation)

Same tx shape but with real `auxiliary_data` (**268 wire bytes** — the 4-element
`toCBORForMempoolSubmission` form, which already includes the IsValid byte): cardano min 167129
(= size **267 = wire 268 − 1**, IsValid excluded; the −1 persists **with** metadata → not a
null-placeholder effect). amaru 807 min 167393 (= size **273 = wire 268 + 5**, the +5 being
**entirely** the auxiliary_data re-encode (bare → tagged metadata) — the already-closed relay
issue #1277; IsValid is not "added," it is already in the 268). So the gap vs cardano is
**273 − 267 = 6 bytes (264 lovelace) = 5 (aux re-encode, #1277) + 1 (IsValid, this finding)**. amaru
0918 accepts 167129 → **both** the IsValid off-by-one **and** the aux re-encode over-count are gone.

## Mechanism (cardano-ledger reference)

cardano-ledger computes the fee-relevant tx size with `toCBORForSizeComputation`
(`eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Tx.hs`):

```
toCBORForSizeComputation = encodeListLen 3 <> encCBOR body <> encCBOR wits
                                           <> encodeNullStrictMaybe encCBOR auxData
```

It emits a **3-element** list that **omits `atIsPhase2Valid`** (the `IsValid` boolean), explicitly
"for compatibility with Mary" — `IsValid` was added in Alonzo, and fee/size accounting deliberately
drops it so sizes/fees are unchanged from Mary. The wire (`toCBORForMempoolSubmission`) is the
4-element form that **includes** `IsValid` (1 byte, `f5`/`f4`). cardano is therefore spec-correct at
`len − 1`. This is a known alt-implementation pitfall — gouroboros (Go node) hit the same class
("measure a tx the same way for maxTxSize and fees", PR #2212) and fixed it.

**Why it was mempool-only (root cause, corroborated by the fix diff).** amaru's *block* path
already computed the IsValid-excluded 3-element size — `crates/amaru-kernel/src/cardano/{block.rs,
transaction_ref.rs}` carry the comment *"top-level array of size 3 (0x83) … Importantly, the
validity of the transaction is not taken into account for the size calculation"* with
`size = 1 + body.len() + witnesses.len() + auxiliary_data_len`. That is exactly why amaru's
block-application accepted the band-fee tx (see Severity). The **bug** was that amaru's
**mempool / submit_api** path **re-encoded** the locally-submitted tx to measure its size, which
re-serialized it to the 4-element form → IsValid **included** (+1 byte vs cardano's IsValid-excluded
size). For a tx carrying real metadata the same re-encode additionally re-tagged `auxiliary_data`
(bare → tagged), adding a further **+5 bytes vs the wire** (the #1277 aux effect), for a total +6
vs cardano. Hence the mempool-vs-block internal disagreement this finding evidences.

## Severity — LOW (mempool-only; escalation refuted with block-level evidence)

**Lead point: amaru's own block-application already did the right thing — only the mempool was
wrong.** The consensus-split hypothesis (would amaru reject a cardano-produced block carrying a
band-fee tx?) was tested directly and **refuted**:

- The band-fee tx (fee 164181, `297547590264…`) was mined into rebake-p1's chain; amaru's baked
  store is a prefix of that same chain. Attaching amaru 807 to peers p1/p2/p3 (multiple honest
  peers overcome the single-peer forward-sync limit #736), amaru **forward-synced past the band-fee
  block** (ledger `pots_fees=164181`, no `FeeTooSmallUTxO` / reject / panic) to ~p1's tip.
- Discriminator: the overpay tx spending `9708b921…#0` then returned "failed to prepare … for
  validation" (input absent) where before sync it was accepted — i.e. `9708b921…#0` is **spent in
  amaru's ledger**, so amaru **applied the band-fee tx at block level**.

So amaru's **block-application accepts** the band-fee tx (matching cardano) while only its
**mempool rejects** it. Consequences when live: a band-fee tx is **not relayed/propagated** by
amaru mempools, but is **fully valid at block level** and reaches the chain via any cardano
producer → **no liveness break** (still mined), **no safety break** (no split). Worst case: amaru
nodes don't help propagate a thin band of minimally-fee'd txs. This mempool-vs-block internal
inconsistency (cardano applies one rule in both) pins the fix location: amaru's mempool min-fee
size should use the IsValid-excluded size (`toCBORForSizeComputation`), same as its block path.

## Fix

**Fixed in: amaru PR #1260** *"fix(amaru-ledger): do not re-encode locally submitted transactions
to compute their size"* — merge commit `6eeffa35f8005f5b98bfb00f1c04664d9f7d58d6`, merged
2026-08-21. #1260 makes the mempool/submit path derive size from the **decoded** transaction
(`WithSize`) instead of re-encoding it, so the mempool size now matches the block path and cardano
(IsValid excluded). The buggy 807 build `493bffba` is Aug-19 (before the merge); 0918 `ea1f34e4` is
after → fixed. Confirmed live: 0918 gap 0. **No upstream issue to file.**

Related but **distinct**: PR #1264 *"preserve bytes of transactions flowing through the mempool"*
(`991a1c85`, 2026-08-21) is the byte-preservation / relay auxiliary_data hash fix (issue #1277,
`WithOriginalBytes`) — the aux-hash/relay side, not our size finding. It only compounded the
*metadata* over-count here; the size finding's fix is #1260.

## Backlog (separate, NOT part of this closed finding)

Does amaru's block-application **enforce min-fee at all**, or did the band-fee block apply only
because amaru's block path uses the correct IsValid-excluded size? To disambiguate, feed amaru a
block containing a tx that is underfee **even by cardano's rule** (fee < 164181) and observe the
block verdict. If amaru's block path **skips** min-fee, that is a separate, potentially
higher-severity finding (amaru would accept blocks cardano rejects — a split in the other
direction). Untested here.

## Reproduction

`antithesis/cardano_amaru_adversarial/prepare-rebake.sh` (frozen non-forging reference + amaru
store, shared genesis, funded UTxO `9708b921…#0`); build 1-in/1-out self-sends spending it at the
fees above (`cardano-cli conway transaction build-raw/sign`, committed `fixture/funding/payment.skey`);
POST raw CBOR to cardano-submit-api (`:8090/api/submit/tx`) and the amaru submit API
(`:3011/api/submit/tx`). 0918 store bootstraps with the stock `ea1f34e4` binary via
`--network testnet_42 --era-history <file>` (custom-network support is now upstream).
