# Finding: Amaru accepts a vkey-witness encoded as a 3-element array (cardano-node decode-rejects)

**Component:** `amaru` transaction CBOR decoder — vkey-witness (`[vkey, sig]`) in the witness set.
**Type:** Decode-strictness / transaction-malleability divergence at mempool ingress —
amaru-accepts-what-cardano-decode-rejects.
**Severity:** LOW (local mempool-ingress leniency; cross-network relay unverified/not realized in a live mesh; bounded — amaru does not forge Praos blocks). Same class
as `finding-amaru-submit-trailing-bytes`, distinct mechanism.
**Status:** Reproduced + verified 2× on the latest supported pair.
**Provenance (ground-truthed via `--version`):** amaru `v10.11.20260925` (git `eaf8ac3f`) vs cardano-node
`11.1.2` (git `fef83fed`). Frozen rebake substrate, submit-API differential (pair1: amaru `:3210`,
cardano `:8110`). 2026-09-27.

## Summary

A Conway vkey-witness is a 2-element array `[vkey, sig]`. When it is encoded as a **3-element** array
`[vkey, sig, <extra>]`, **amaru silently ignores the surplus element and ACCEPTS the transaction** (HTTP
202), admitting it to the mempool under the **canonical** transaction id (identical to the well-formed
tx). **cardano-node 11.1.2 DECODE-REJECTS** the same bytes:
`DecoderErrorDeserialiseFailure "Shelley Tx" (DeserialiseFailure "Size mismatch when decoding Record")`.

The witness bytes (vkey, sig) are unchanged and the transaction body is untouched, so the signature
remains valid — hence amaru admits it rather than failing later at validation. amaru returns the same tx
id as the canonical form, confirming its decoder canonicalizes by dropping the extra element.

**Not transaction-id malleability.** The tx id is UNCHANGED (amaru returns the canonical id, having dropped the surplus element), so this is decode-leniency / relay-and-mempool divergence, not tx-id malleability. Severity stays bounded on that basis.

## Evidence

Base: a valid 1-in/1-out self-spend of `9708b921…#0` (the committed `minimum-exact` tx), witness set
= `d90102 81 [82 <vkey:5820…> <sig:5840…>]`. Mutation: the inner witness `82` → `83` with a trailing
`00` element appended (still within the witness set, not the signed body).

| node | verdict | detail |
|---|---|---|
| amaru 0925 | **ACCEPT 202** | tx id `f88afec6…` = the canonical base tx id (extra element ignored) |
| cardano-node 11.1.2 | **DECODE_REJECT 400** | `DeserialiseFailure … Size mismatch when decoding Record` |

Verified 2× (deterministic), each on a freshly-reset pair (clean mempool — no double-spend masking),
fail-closed classification, submit-path only (no forward-sync / tip-skew).

## Scope / not-this (25-class decode-strictness sweep)

This was found in a systematic TX-STRUCTURE canonical-CBOR sweep (body / witness-set / aux). amaru and
cardano AGREE on the large majority of classes:
- both **lenient**: indefinite-length containers (outer array, body/witness maps, inputs array where the
  signature permits), non-minimal integer encodings, non-minimal length prefixes, set encoded as a plain
  array (tag-258 omitted).
- both **strict** (both decode-reject): duplicate map keys, unexpected/extra tags, wrong set tag (259),
  indefinite/chunked byte-strings, duplicate set elements, and **outer-tx / output arity** (extra or
  missing top-level or output elements) — the historical outer-tx-arity leniency is FIXED and holds.

Only two divergences surfaced:
1. **trailing bytes after the tx** — amaru accepts, cardano decode-rejects (`finding-amaru-submit-trailing-bytes`, re-confirmed current on 0925/11.1.2).
2. **this witness-element arity leniency** — distinct mechanism (surplus element *inside* the fixed
   witness array, not trailing end-of-input bytes; and not the fixed outer-tx-arity).
amaru also decodes an extra **input** element leniently, but there the body changes so the signature
fails → both reject (weaker; noted for completeness).

## Severity — LOW-MEDIUM (bounded), a real malleability gap

- Mempool ingress only; amaru does not produce Praos blocks, so no direct consensus/safety impact; the malformed-encoding tx cannot reach the honest chain via amaru.
- Local mempool admission is CONFIRMED (amaru returns 202 and holds the tx under the canonical tx-id).
- Cross-network RELAY form is UNVERIFIED. In a live amaru->cardano n2n mesh (cardano-node 11.1.2,
  ExperimentalProtocolsEnabled=false, pulling amaru's mempool via tx-submission) the V_14 handshake
  completes, but amaru DEMOTES the cardano peer over chain-sync (intersect_not_found -> "uninteresting"
  -> Maintenance) and the TxSubmission child stops before any tx is exchanged (the peer's 60s tx-submission
  init delay races the demotion). So the malformed tx did NOT propagate to the cardano peer's mempool in
  practice, and whether amaru would relay the ORIGINAL malformed bytes or RE-SERIALIZE to canonical on
  egress could not be observed. This BOUNDS the practical impact: the malformed encoding tends to stay in
  amaru's LOCAL mempool (it does not reach a cardano peer here), so the mixed-network relay divergence is
  not demonstrated — the confirmed effect is local mempool-ingress leniency only. A clean relay observation
  would need a chain-sync-healthy mesh or a custom tx-submission egress capture; see
  finding-amaru-forward-sync-reachability-wall for the underlying chain-sync peer-demotion.

## Recommendation

Enforce the exact 2-element arity of the vkey-witness array at decode (reject `[vkey, sig, …]` with a
size-mismatch error, matching cardano-ledger's strict record decoder), rather than ignoring surplus
elements. Extend to any other fixed-arity structures decoded leniently (the extra-input-element case
above indicates the input array is also arity-lenient at decode).

## Artifacts

Sweep harnesses: `cbor_strict.py` / `cbor_strict2.py` (25 non-canonical mutation classes, verdict +
reason-class parity, fail-closed, per-case mempool reset). Cross-ref `finding-amaru-submit-trailing-bytes`.
