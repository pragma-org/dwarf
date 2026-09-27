# Amaru's submit-API accepts trailing bytes after a transaction (no end-of-input check)

**Component:** `amaru` submit-API transaction ingress (`crates/amaru-node/src/submit_api.rs`)
**Type:** CBOR decode conformance divergence vs. the Haskell reference node (missing end-of-input check / transaction malleability at ingress)
**Status:** Confirmed (root cause in source; empirically verified on the shipping release). Amaru admits a transaction that carries **arbitrary trailing bytes**; cardano-node rejects the same bytes at deserialization. **Present in the latest release `v10.11.20260730`.** Distinct from and *not* covered by the 3-element-array fix (`finding-amaru-tx-3element-array.md`), which is now closed.
**Found by:** DWARF differential fuzzing — same transaction bytes submitted to Amaru and cardano-node, comparing decode/accept behaviour, over a deep certificate/governance corpus.
**Date:** 2026-08-01
**Severity:** Low (mempool-ingress conformance; not consensus-affecting; not a denial-of-service vector — see *Severity*).

---

## Summary

Amaru's submit-API decodes the request body with `minicbor::decode`, which returns the
decoded transaction **without verifying that the entire input was consumed**. Any bytes
appended after a valid transaction are therefore silently ignored: Amaru decodes the prefix,
computes the **same transaction id**, and admits it to the mempool.

The Haskell node (`cardano-node`, via `cardano-submit-api`) enforces end-of-input and rejects
the same bytes at CBOR deserialization with an explicit *leftover* error.

The same logical transaction thus has infinitely many accepted wire encodings in Amaru
(`tx ‖ <any bytes>`) but exactly one in cardano-node — a decode-conformance / ingress
malleability divergence.

## Environment

| | |
|---|---|
| Amaru | `ghcr.io/pragma-org/amaru:v10.11.20260730` (baked as `ghcr.io/j-gainsec/amaru-baked:0.2.0`) |
| Reference | `cardano-node` 10.16 via `ghcr.io/intersectmbo/cardano-submit-api:10.7.1` (against a live testnet_42 node) |
| Network | testnet_42 (k=5) |
| Endpoint | `POST /api/submit/tx`, `Content-Type: application/cbor` |

## Observed behaviour

Starting from a real, serialized Conway transaction and appending bytes to it:

| Input | cardano-node | Amaru |
|---|---|---|
| `<tx>` (canonical) | decodes, rejects at validation | decodes, rejects at validation — tx id `9ec42389…58fba6` |
| `<tx> ‖ 0xff` | **HTTP 400 — decode error**: `DecoderErrorLeftover "Shelley Tx" "\255"` → `TxCmdTxReadError` | **decodes** — **same** tx id `9ec42389…58fba6`, advances to validation |
| `<tx> ‖ 0x000102` | **HTTP 400 — decode error** (leftover) | **decodes** — same tx id, advances to validation |

cardano-node's error literally names the leftover byte (`"\255"`); Amaru returns the identical
`failed to prepare transaction 9ec42389…58fba6 for validation` it returns for the clean tx,
i.e. it did not observe the trailing byte at all.

## Root cause

`crates/amaru-node/src/submit_api.rs` (v10.11.20260730):

```rust
async fn submit_tx(State(mempool_sender): State<SubmitApiState>, headers: HeaderMap, body: Bytes) -> Response {
    ...
    let tx: Transaction = match minicbor::decode(&body) {   // <-- no end-of-input check
        Ok(tx) => tx,
        Err(e) => return text_response(StatusCode::BAD_REQUEST, format!("Invalid CBOR transaction: {e}")),
    };
    ...
}
```

`minicbor::decode(&body)` constructs a decoder, decodes one `Transaction`, and returns it. It
does **not** assert that the decoder reached the end of `body`. Any bytes after the transaction
are left unconsumed and ignored. The Conway wire format (and cardano-node's decoder) treat a
transaction submission as exactly the transaction bytes and nothing more — cardano-node reports
`DecoderErrorLeftover` when input remains.

This is a separate check from the outer-array arity fix in
`finding-amaru-tx-3element-array.md`: that finding added `assert_len(4)` inside the
`Transaction` decoder (now present at this tag), which constrains the *shape of the transaction*;
it does not constrain what follows the transaction in the request body.

## Severity (graded from source + measurement)

**Low — a mempool-ingress decode-conformance divergence, not consensus-affecting and not a DoS.**

- **No propagation / no chain split.** The 10.11 `Transaction` type carries **no original-bytes
  field** and is re-serialized through its (canonical) derived/manual encoder before relay
  (verified in `source/transaction.rs` — fields are `body`, `witnesses`, `is_valid`,
  `auxiliary_data` only). So a transaction Amaru admits with trailing junk is **re-emitted
  canonically**; the junk never propagates to peers. This is strictly an *ingress* leniency.
- **Not a resource-exhaustion vector (measured, refuted).** Amaru caps the request body at
  ~2 MB (`HTTP 413 "Failed to buffer the request body: length limit exceeded"` at 2048 KiB;
  accepted at 1024 KiB) — axum's `DefaultBodyLimit`. Memory stayed flat at ~45 MiB across
  1/10/50/100 MB payloads, and under a burst of **2000 requests at 300 concurrency** of ~1.9 MB
  padded transactions Amaru peaked at **285.8 MiB** (sub-linear → bounded concurrent buffering)
  and survived with zero panics. The trailing bytes an attacker can sneak past decode are bounded
  to `< ~2 MB`, and padded copies of one transaction **dedupe by tx id** in the mempool. See
  `resource/`.
- **What it is:** a strict-vs-lenient ingress conformance gap. Amaru accepts (and does mempool
  admission / validation-prep work on) a family of non-canonical `tx ‖ junk` byte-strings that a
  strict node rejects cheaply at the decoder. Same *family* as the 3-element finding (lenient
  CBOR ingress) but a distinct, still-open root cause.

## Scope

Confined to the **submit-API / mempool ingress** decode path (`minicbor::decode(&body)`).
Block decoding uses a manual decoder with explicit length assertions and is unaffected. This is
not a block/consensus split.

## Reproduction

```bash
# 1. Obtain any real serialized Conway transaction as tx.cbor (e.g. cardano-cli conway
#    transaction build-raw + sign, then the envelope's cborHex).

# 2. Append a byte:
cp tx.cbor tx_pad.cbor && printf '\xff' >> tx_pad.cbor

# 3. Submit to each node:
curl -s -X POST http://<cardano-submit-api>:8090/api/submit/tx \
     -H 'Content-Type: application/cbor' --data-binary @tx_pad.cbor
# -> 400  DecoderErrorLeftover "Shelley Tx" "\255"  (decode rejected)

curl -s -X POST http://<amaru>:3011/api/submit/tx \
     -H 'Content-Type: application/cbor' --data-binary @tx_pad.cbor
# -> "failed to prepare transaction <same id as clean tx> for validation"  (decoded; junk ignored)
```

## Suggested remediation

Enforce end-of-input after decoding: decode with a `minicbor::Decoder` and reject the request
unless the decoder position equals `body.len()` (i.e. all bytes were consumed) — mirroring
cardano-node's `DecoderErrorLeftover` behaviour. This rejects `tx ‖ junk` at ingress rather than
silently ignoring the trailing bytes.

## How this was found

DWARF ran the submit-API differential (the same harness that surfaced the 3-element finding) over
a deeper corpus of Conway transactions carrying certificates and governance actions
(stake registration, vote delegation, DRep registration, an info governance action, a DRep vote,
a datum-hash output, multiple certificates, metadata, and a validity interval — built with
`cardano-cli conway transaction build-raw`). A single-byte mutation sweep of array/map length
headers surfaced 39 decode-divergences; a mechanism probe showed they reduce to one root cause —
appending arbitrary bytes to a valid transaction is accepted by Amaru (same tx id) and rejected
by cardano-node (`DecoderErrorLeftover`). See `differential/` and `workload/`.

---

## Confirmed LIVE on the latest supported pair — 2026-09-26

Independently re-surfaced by the phase-1 mutation soak (`workload/phase1_soak.py`) and confirmed
**STILL OPEN** on the current pair: **amaru `v10.11.20260925` (git `eaf8ac3f`)** accepts, while
**cardano-node `11.1.2` (git `fef83fed`)** decode-rejects. The divergence therefore persists across
every release since `v10.11.20260730` (730 → 807 → 903 → 925) — it is not fixed on latest.

**Provenance (ground-truthed via `--version`):** amaru `eaf8ac3f` (v10.11.20260925, built from
source); cardano-node `11.1.2` git rev `fef83fed01d7926f3de83b3b917be5a4a48768b5`. Frozen rebake
substrate (submit-api differential, no N2N).

**Evidence (phase-1 soak):**
- In a 30 s smoke run (24,303 mutants), **every** verdict divergence where amaru ACCEPTED and
  cardano REJECTED was a **pure trailing append** — a valid base tx + 1–4 trailing bytes, byte-diff
  confirmed the mutant's prefix is byte-identical to the base. Reproduced cleanly on a fresh mempool.
- Across **9 different base tx types** — stake-registration, DRep-registration, DRep-vote,
  native-script (timelock / nested / threshold / minting-policy), min-fee, pool-registration — so it
  is the framing, not any specific transaction.
- **A single trailing byte triggers it** (mutants with +1 byte, e.g. `…f5f6` → `…f5f6 8f`).
- cardano: `DecoderErrorDeserialiseFailure "Shelley Tx" (DeserialiseFailure <n> "Size mismatch …")`
  — DECODE stage. amaru: `202` with the same tx id as the un-appended tx (admits the prefix,
  ignores the trailing bytes) — matching the root cause above (no end-of-input check).
- Longer chunked soak (7×120 s, amaru UTxO-refreshed between chunks): 2x120 s chunks (~200k mutants) reproduced the trailing-append divergence across seeds; the run is inconclusive-dominated once amaru mempool UTxOs are consumed by accepted trailing-mutants (fail-closed, so uninformative past that point) — the 30 s smoke is the cleaner dataset. Only the
  trailing-append class produced amaru-accept-vs-cardano-reject; no non-trailing divergence class surfaced: the other 40 smoke divergences were amaru decoding further than cardano then validation-rejecting (both reject — same decoder-leniency root, no acceptance, no safety gap).

**Severity:** unchanged (Low) — amaru would mempool/relay a tx that cardano-node rejects as
malformed → a mixed-network mempool divergence on the submit path; bounded because amaru does not
produce Praos blocks. Conformance / ingress-malleability, not consensus.

**Status:** this is a CURRENCY confirmation of the known open finding (no new upstream filing beyond
the existing recommendation — enforce end-of-input after decode). Artifacts: `workload/phase1_soak.py`
(the soak harness) + the soak result JSON in this finding's evidence dir.
