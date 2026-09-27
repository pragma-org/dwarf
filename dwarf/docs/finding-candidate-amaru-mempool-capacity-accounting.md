# Finding candidate (source-level): Amaru mempool capacity accounting differs from cardano-node

**Status: SOURCE-LEVEL CANDIDATE, source-verified on both sides against the exact shipped versions;
not empirically confirmed.** The multi-hour pair-4 re-bake needed to observe it live was
deliberately skipped (low severity; see "Empirical plan"). **Severity: LOW** (mempool-ingress
resource accounting), with **one LOW-MEDIUM item** (D3, no execution-unit bound). There is no
consensus impact: block validation is unaffected, and Amaru does not forge Praos blocks.

Versions (verified):
- amaru `eaf8ac3f` (v10.11.20260925); source `/home/nigel/codebases/amaru`.
- cardano-node 11.1.2 (`fef83fed`). **Tag `11.1.2` pins `ouroboros-consensus ^>= 4.2.0.1`**
  (`cardano-node/cardano-node.cabal:179`), and the shipped `ghcr.io/intersectmbo/cardano-node:11.1.2`
  image carries `ouroboros-consensus-*-4.2.1.0` in its Nix store. So **4.2.1.0 is the consensus
  version actually running**, and every cardano-side citation below is from that exact source.
  An earlier caveat said the pin was `^>= 4.1`; that was read from the 11.1.0 checkout and is
  withdrawn.

## Correction to the earlier hypothesis

An earlier note said Amaru's mempool counts a *re-encoded* size (`to_cbor(&tx).len()`). **That is
wrong.** The submit API decodes into `WithOriginalBytes<Transaction>` (`amaru-node/src/submit_api.rs:77`),
and its `Encode` impl writes the original bytes verbatim
(`amaru-minicbor-extra/src/decode/with_original_bytes.rs:133-141`). So `to_cbor(&tx).len()` is
the **raw submitted tx size**. There is no re-encoding. Separately, the per-tx 16384 cap counts
original bytes on both nodes; that was confirmed live (`mempool-submit-path-differential-family.md`).

## The differences

| # | aspect | cardano-node 11.1.2 (ouroboros-consensus) | Amaru 0925 | 
|---|---|---|---|
| D1 | capacity value | `2 × (maxBlockBodySize − 1024)` = **178 176 B** here; derived from pparams (`Mempool/Capacity.hs:62-79`, `txsMaxBytes`, `fixedBlockBodyOverhead = 1024`) | **hard-coded** `DEFAULT_MAX_BYTES = 180_224` (`in_memory_mempool.rs:165`); does **not** follow pparams |
| D2 | per-tx byte measure | `sizeTxF + perTxOverhead` = ledger size (IsValid excluded) **+ 4** (`Shelley/Ledger/Mempool.hs:181, 402-412`) | raw submitted bytes = ledger size **+ 1** (the IsValid byte) (`in_memory_mempool.rs:76`) |
| D3 | dimensions | Conway `TxLimits` (`Shelley/Ledger/Mempool.hs:765-772`): a tx measures `AlonzoMeasure` (bytes **and** execution units, `txMeasureAlonzo`) plus `RefScriptSize` (`txMeasureRefScripts`); block capacity = (maxBlockBodySize − 1024, `maxBlockExUnits`, `maxRefScriptSizePerBlock`) (`blockCapacityConwayMeasure`, `:694-700`), ×2 for the mempool | **bytes only**; no execution-unit or reference-script bound on the mempool as a whole |
| D4 | when full | `addTx` **blocks** until space frees ("will block if the mempool is full", `Mempool/Update.hs:157`; local clients wait on the queue) | returns `TxRejectReason::MempoolFull` **immediately** (`in_memory_mempool.rs:83-85`) |

### Worked consequences (pair-4 pparams: maxBlockBodySize 90 112, maxTxSize 16 384)

- **Max-size txs (ledger size 16 384).** cardano charges 16 388 each: 10 fit (163 880), and the
  11th would need 180 268 > 178 176. Amaru charges 16 385 each: 10 fit, and the 11th would need
  180 235 > 180 224. **The same count by coincidence.**
- **Small txs (ledger size 200).** cardano charges 204, so ⌊178 176 / 204⌋ = **873** fit. Amaru
  charges 201, so ⌊180 224 / 201⌋ = **896** fit. Amaru holds about 2.6% more txs, and more again
  as txs get smaller.
- **D1 under governance.** If a ParameterChange raises or lowers `maxBlockBodySize`, cardano's
  capacity follows at the next tick, and Amaru's stays at 180 224. This is a permanent drift
  until the constant is changed.
- **D3.** A mempool of Plutus-heavy txs is capped at 2 blocks' worth of execution units on
  cardano. On Amaru the only limit is 180 224 bytes, so the total execution units held (and
  evaluated at admission) are unbounded by the block budget. Rated LOW-MEDIUM as a resource
  amplification lever for an Amaru relay; not a ledger rule.
- **D4.** A submitter to a full cardano node waits. With cardano-submit-api, the HTTP request
  hangs until space frees or it times out. The same submitter to Amaru gets an immediate rejection.
  Client-visible behaviour differs under load.

None of these change which transactions are *valid*. They change which valid transactions are
*admitted* and *held*, and how the node behaves under load.

## Empirical plan (not run: skipped by orchestrator decision 2026-09-27, low severity vs a multi-hour re-bake)

The plan needs a pair-4 genesis variant with 64 extra `initialFunds` entries paying to the
committed payment key. Use base addresses with `stake_i = blake2b-224("dwarf-cap-%d")`, at
1 000 000 ADA each. UTxO ids are blake2b256(address), so `9708b921#0` and every existing corpus
stay unchanged. It costs a full forge + snapshot + bootstrap re-bake of pair 4.

The cases (all ADA-only and phase-1 valid):
1. Fill with 10 max-size txs, then submit an 11th. Prediction: both refuse it. Cardano may block
   rather than reject (D4): record the submit-api timeout vs Amaru's immediate `MempoolFull`.
2. Fill with small txs up to the 873rd and 896th. Prediction: cardano stops admitting at 873,
   Amaru at 896 (D1 + D2).
3. Hold the bytes fixed and vary the IsValid byte count: none needed; the +1 vs +4 per-tx
   difference shows in case 2.
4. D3 needs Plutus txs (the phase-2 lane's always-succeeds script) with high declared execution
   units. Prediction: cardano stops admitting once 2 × `maxBlockExUnits` is reached, and Amaru
   keeps admitting until the byte limit.

## Suggested upstream note (if confirmed)

Derive the mempool capacity from the current protocol parameters (`2 × (maxBlockBodySize −
overhead)`, plus execution-unit and reference-script dimensions), measure txs as the ledger size
plus a fixed per-tx overhead, and document the full-mempool behaviour (reject vs wait) at the
submit API.
