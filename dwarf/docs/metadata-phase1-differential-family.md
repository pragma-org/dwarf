# Transaction-metadata / auxiliary-data-hash phase-1 differential family

A DWARF phase-1 differential family (extends `workload/mixed_phase1.py`, grades through
`stake_pool_differential.grade`). It covers the **auxiliary-data-hash rules**, above all whether a
node hashes the aux bytes **as sent** or its own re-encoding, plus the metadatum encoding and size
rules. This is the validation-path cousin of #1277 (aux-data relay re-encoding); it does not
re-test relay. It adds coverage; it is not a finding.

> **STATUS (2026-09-27): GRADED, 17/17 AGREE. Amaru v10.11.20260925 (`eaf8ac3f`) is
> CONFORMANT with cardano-node 11.1.2 (`fef83fed`)**, run through cardano-submit-api 11.1.2 on
> pair 4. There is no divergence.
>
> **Headline: Amaru hashes the auxiliary data AS SENT, exactly like cardano. There is no
> #1277-style re-encoding on the validation path.** The same metadata was sent in two
> non-canonical encodings: label 674 as a 5-byte uint, and an indefinite-length map and list.
> - Body hash over the **sent** bytes: **both accept, with the same tx id** (`8ffc22a8…`,
>   `71962b0d…`).
> - Body hash over the **canonical re-encoding**: **both reject** with `ConflictingMetadataHash`,
>   and both report the SAME expected hash, blake2b of the sent bytes (`7126b40b…`, `8a9c7079…`).
>   That hash is the oracle's parity token, so a node that hashed its own re-encoding would fail
>   parity even if its verdict agreed.
>
> Also AGREE:
> - hash-mismatch, hash-missing and aux-missing, each with hash parity;
> - DECODE-rejects on both: 65-byte text, 65-byte bytes, 65-byte nested text (the 64-byte limit is
>   a decoder rule in 11.1.2), tag-2 bignum ints, and duplicate labels;
> - accepted by both: the 64-byte boundaries and chunked indefinite text.
>
> Evidence: `fixture/metadata/graded-2026-09-27.json`.

## Substrate note (resolved)

- The first grading run went through pair 4's **cardano-submit-api 10.7.1**, in front of
  cardano-node 11.1.2. Its older decoder accepted the 65-byte metadata cases (a UTXOW-rule-era
  decoder) and forwarded them. The 11.1.2 node's decoder rejects them (`.size (0..64)`), so the
  node closed the local connection. The submit-api answered `BearerClosed`, and the 3 cases were
  INCONCLUSIVE (fail-closed; the node did not crash).
- All pair submit-apis were then redeployed on cardano-submit-api 11.1.2, and the provenance gate
  now also pins the submit-api image to the node release. The whole family was re-graded, and
  those 3 cases are clean decode-reject AGREEs.
- The mint/burn and gov-proposal decode edges were re-checked with the 11.1.2-era decoder: they
  are unchanged.

## Construction

- `fixture/metadata/build.sh` builds `md-base.tx` with cardano-cli: metadata
  `{674: {"msg": ["hello"]}}` in the Alonzo aux form, tag 259 `{0: md}`.
- `edits.py` writes every other case from **raw aux bytes** (a hand encoder, not cbor2, so the
  non-canonical forms are exact) and re-signs the body, which carries `auxiliary_data_hash`.
  Anchors, or abort:
  - the hand-encoded canonical aux must equal cardano-cli's bytes;
  - each non-canonical form must decode to the same value as the canonical form;
  - the `txedit` self-checks must pass.
- The rebuild is byte-reproducible.
- Offline pre-check: use the 11.1.2-era cli (11.2.3, in the node container). The older cli
  decoder (10.16, `cnode-p1:local`) still accepts the 65-byte cases.

## Result (pair 4, 2026-09-27)

| case | cardano-node 11.1.2 | Amaru 0925 | grade |
|---|---|---|---|
| noncanon-uint-hash-canonical | `ConflictingMetadataHash` expected `7126b40b…` | `metadata hash mismatch … expected 7126b40b…` | AGREE + sent-bytes hash |
| noncanon-indef-hash-canonical | `ConflictingMetadataHash` expected `8a9c7079…` | same hash | AGREE + sent-bytes hash |
| hash-mismatch | `ConflictingMetadataHash` expected `c80ec55e…` | same | AGREE + hash |
| hash-missing | `MissingTxBodyMetadataHash c80ec55e…` | `missing auxiliary data hash: metadata hash c80ec55e…` | AGREE + hash |
| aux-missing | `MissingTxMetadata c80ec55e…` | `missing metadata: auxiliary data hash c80ec55e…` | AGREE + hash |
| bignum-small, int-overflow | `DeserialiseFailure … Unsupported token type TypeInteger` | `unexpected CBOR datatype Tag when decoding metadatum` | AGREE (decode) |
| dup-labels | `DeserialiseFailure` | `found duplicate keys` | AGREE (decode) |
| text-65, bytes-65, nested-text-65 | `DeserialiseFailure "text/bytes .size (0..64)"` | `text/bytes exceeds 64 bytes: got 65` | AGREE (decode) |
| noncanon-uint-hash-sent, noncanon-indef-hash-sent | 202 | 202, same tx id | AGREE |
| canonical-valid, text-64-valid, bytes-64-valid, indef-text | 202 | 202, same tx id | AGREE |

## Oracle (fail-closed)

`workload/metadata_differential.py` uses the shared grade. For the hash rules, the parity token is
**blake2b of the aux bytes as they sit in the tx**, which is the hash the ledger must compute. A
node that hashed its own re-encoding would therefore fail parity even when its verdict agreed. A
submit-api `BearerClosed` (a decoder version skew) classifies as `unknown`, so the case is INCONCLUSIVE.

Run the violations: `python3 workload/metadata_differential.py --amaru URL --cardano URL`.
Run the controls one at a time with `--control CASE`, each after a mempool reset.
