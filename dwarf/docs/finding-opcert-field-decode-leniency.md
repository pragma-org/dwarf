# Finding note — opcert-field CBOR decode-leniency divergence (Amaru vs cardano-node)

**From:** DWARF opcert soak campaign, family #6 (opcert-field CBOR mutations), decoder-level
differential (Pragma) · **Date:** 2026-09-25
**Scope:** feed the SAME bare Praos block-header CBOR, with its operational-certificate
sub-structure mutated at the FIELD level, to both implementations' header decoders — Amaru
10.11.20260918 (`amaru-cbor-decode-block-header`, `from_cbor_no_leftovers::<BlockHeader>`) and
cardano-node 11.1.2 (the forger's `decode-praos-header`, `decodeFullAnnotator` of
`Praos.Header StandardCrypto`) — and compare decode accept/reject. Library/binary level, no
devnet.

## Why decoder-level (not the live soak)

The differential soak harness correlates every verdict by `header_hash`; a malformed or
value-changing opcert CBOR either fails to decode (no `header_hash` logged) or changes the header
hash, so the live soak scores these mutations **inconclusive**, never "agreement" (confirmed by
the encoding-form family's 25 inconclusive out of 98). Field-level opcert CBOR mutations are only
observable by feeding the same bytes to both decoders directly and diffing — which is what this
harness does. It complements families #1–#5, which cover the semantic/validation stage.

## Headline — Amaru's header decoder is more LENIENT than cardano-node's at the opcert level

Of 11 opcert-field CBOR mutations, 8 agree (both decoders reject) and **3 diverge: Amaru accepts
a header cardano-node rejects at decode.** Deterministic; reproduced across multiple base headers
(preprod + mainnet). Both decoders accept the unmutated base header (the mutation rewrites only
the opcert sub-structure, every other byte preserved).

| opcert-field mutation | Amaru | cardano-node | result |
|---|---|---|---|
| counter-negative (uint → −1) | reject | reject | agree |
| counter-bignum-oversized (> 2⁶⁴) | reject | reject | agree |
| counter-type-text (uint → text) | reject | reject | agree |
| counter-type-bytes (uint → bytes) | reject | reject | agree |
| kesperiod-type-text (uint → text) | reject | reject | agree |
| kesperiod-negative (uint → −1) | reject | reject | agree |
| **hot-vkey-truncated** (32 B → 16 B) | **ACCEPT** | reject | **DIVERGENCE** |
| **cold-sig-truncated** (64 B → 32 B) | **ACCEPT** | reject | **DIVERGENCE** |
| opcert-missing-field (drop sigma) | reject | reject | agree |
| opcert-duplicate-field (dup counter) | reject | reject | agree |
| **opcert-extra-field** (append a 5th element) | **ACCEPT** | reject | **DIVERGENCE** |

cardano-node's exact decode errors on the three divergent inputs (it enforces the fixed field
widths and the opcert arity at decode):

- hot-vkey-truncated — `DecoderErrorDeserialiseFailure "Header" (... "Number of bytes mismatch.
  Expected 32 number of bytes, but got 16")`
- cold-sig-truncated — `DecoderErrorDeserialiseFailure "Header" (... "PinnedSizedBytes 64: wrong
  length, expected 64 bytes but got 32")`
- opcert-extra-field — `DecoderErrorDeserialiseFailure "Header" (... "Size mismatch when decoding
  Record CBORGroup. Expected 5, but found 4.")`

Amaru's `from_cbor_no_leftovers::<BlockHeader>` returns `Ok(_)` for all three: it does NOT reject,
at decode, a KES hot verification key that is the wrong length, a cold-key signature that is the
wrong length, or an operational certificate carrying an extra trailing element.

## Interpretation and scope

This is a **decode-stage leniency divergence**: Amaru's header decoder admits opcert structures
that cardano-node's decoder rejects outright. The three consistent (counter/period value +
type-confusion + missing/duplicate-field) agreements show both decoders are strict about the
counter and KES-period field *values/types* and about a *too-short* opcert; the divergence is
specifically about **fixed-width byte fields (KES hot vkey, cold signature) and extra opcert
elements**, which Amaru does not size-check at decode.

Whether Amaru rejects these later, at header/consensus VALIDATION (e.g. when it uses the hot vkey
or verifies the signature), is NOT covered by this decoder-level test — a lenient decoder that is
backed by a strict validator is not a consensus risk, but a lenient decoder is still a larger
attack surface and a divergence from cardano-node's fail-fast behaviour. Recommended follow-up:
confirm whether a truncated-hot-vkey / truncated-cold-sig / extra-field header is rejected by
Amaru at validation (it would be, on the shared devnet, via the existing opcert families — but
those serve well-formed CBOR, so a dedicated check is warranted). This mirrors #4's result that
the two implementations differ in opcert handling; here the difference is at the decoder boundary.

Both nodes REJECT the value/type-confusion and too-short mutations, so those are not a divergence.

## Reproduce

Deterministic, no devnet:

```
python3 dwarf/scripts/opcert_field_decoder_diff.py \
  --base dwarf/corpora/opcert/field-mutations/base-header-mainnet-10817298.cbor \
  --forger-bin <dwarf-opcert-adversary-latest> \
  --amaru-bin  <amaru-cbor-decode-block-header> \
  --report reports/opcert-field-decode-leniency-evidence/differential-report.json \
  --corpus-dir reports/opcert-field-decode-leniency-evidence/corpus
```

- Mutator: forger `mutate-opcert <mutation>` (rewrites only the opcert sub-structure of a base
  header; `oPCERT_FIELD_MUTATIONS` in `DwarfOpcertAdversary.hs`).
- Cardano decoder: forger `decode-praos-header` (bare `Praos.Header StandardCrypto`).
- Amaru decoder: `amaru-cbor-decode-block-header` (manifest
  `dwarf/targets/manifests/amaru-cbor-decode-block-header.yaml`).
- Pure decision layer (`opcert_field_decoder_diff.classify_pair` / `build_report`) is covered by
  `tests/test_opcert_field_decoder_diff.py`.

Evidence (report + the 11 mutated vectors + base header) in
`reports/opcert-field-decode-leniency-evidence/`.
