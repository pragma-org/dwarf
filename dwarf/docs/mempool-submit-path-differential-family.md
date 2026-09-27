# Mempool / submit-path phase-1 differential family

A DWARF phase-1 differential family (extends `workload/mixed_phase1.py`, grades through
`stake_pool_differential.grade`). It covers the **max-tx-size exact boundary** in canonical and
non-canonical encodings, **duplicate resubmission**, and **HTTP-level robustness** of both submit
endpoints, with a liveness oracle. It adds coverage; it is not a finding. The mempool-capacity
accounting differences, which this substrate cannot reach, are written up separately as a
source-level candidate: `finding-candidate-amaru-mempool-capacity-accounting.md`.

> **STATUS (2026-09-27): GRADED, 15/15 AGREE. Amaru v10.11.20260925 (`eaf8ac3f`) is
> CONFORMANT with cardano-node 11.1.2 (`fef83fed`)**, via cardano-submit-api 11.1.2 on pair 4.
>
> - **Both nodes count the ORIGINAL bytes at the 16384 cap.** Each of the body, the witnesses
>   and the aux data was encoded non-canonically so that the original ledger size is exactly 16385
>   while the canonical re-encoding is 16381 / 16384 / 16376. Both nodes reject every one, and
>   both print **16385** (cardano `MaxTxSizeUTxO {supplied: 16385}`, amaru `provided 16385 bytes`).
>   At exactly 16384, both accept every variant, with the same tx id. So there is no re-encoding
>   in either node's size rule, and IsValid is excluded on both.
> - Duplicate resubmission: both accept the first copy and refuse the second. Amaru answers 409
>   "Transaction is a duplicate"; cardano answers "All inputs are spent. Transaction has probably
>   already been included". The wording differs; the verdict agrees.
> - HTTP robustness (empty body, 1 MiB junk, truncated tx, wrong Content-Type, GET): both nodes
>   return identical status codes (400/400/400/415/405), and both stay live after every request.
>
> Evidence: `fixture/mempool/graded-2026-09-27.json`.

## Construction

- `fixture/mempool/build.sh` builds `mp-base.tx` with cardano-cli: it spends `9708b921…#0`, the
  fee is 1 ADA (the minimum at 16384 bytes is 876 277), and it carries a small metadata entry.
  `mp-base.tx` is also the duplicate-resubmission case.
- `edits.py` pads the tx with metadata to an exact ledger size (`1 + body + witnesses + aux`,
  IsValid excluded; the size both ledgers use). Up to three 1-byte metadatum ints close the gaps
  between CBOR text-head sizes.
- It writes 4 variants × {16385, 16384}:
  - `canonical`;
  - `body`: the fee as a 9-byte uint (+4 bytes);
  - `wits`: the vkey array as indefinite length (+1 byte);
  - `aux`: an indefinite metadata map plus 8 non-minimal text heads (+9 bytes).
- Assertions, or abort:
  - each original size is exact;
  - each non-canonical variant's canonical size is smaller than its original and ≤ 16384, so a
    node measuring a re-encoding would accept the -16385 case;
  - the txedit self-checks pass.
- The build is byte-reproducible. All 9 txs decode with the 11.1.2-era decoder (cardano-cli 11.2.3).

## Oracle (fail-closed)

`workload/mempool_differential.py`:
- **Size cases** use the shared grade. The parity token is the original size, `16385`. A node
  measuring a re-encoding would print a smaller number (REASON-DIVERGENCE) or accept
  (VERDICT-DIVERGENCE).
- **`--duplicate`**: both nodes must accept the first copy. Neither may accept the second
  (MASKED or phase1_reject on both); any second ACCEPT is a divergence.
- **`--http`**: both nodes must return the same 4xx status. After every request, a known reject
  (`size-canonical-16385`) must still get `phase1_reject` from both, otherwise the case is graded
  `LIVENESS-FAILURE`.

Run the size violations: `python3 workload/mempool_differential.py --amaru URL --cardano URL`.
Run each accept case after a mempool reset: `--control CASE`, `--duplicate`, `--http`.

## Not reachable here

Mempool capacity, admission ordering, and near-full behaviour are out of reach. This substrate has
one spendable UTxO, Amaru does not chain on unconfirmed outputs, and cardano admits one tx per
reset. See the source-level candidate doc. A pair-4 genesis variant with 64 extra initialFunds
would unlock them: UTxO ids are blake2b256(address), so existing corpora would stay unchanged.
That variant needs a full forge + snapshot + bootstrap re-bake, and whether to do it is an
operator decision.
