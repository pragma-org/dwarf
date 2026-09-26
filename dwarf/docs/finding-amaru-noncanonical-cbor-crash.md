# Finding note — Amaru node crash / DoS on a non-canonical-CBOR block header (Amaru vs cardano-node)

**From:** DWARF opcert soak campaign, family A (encoding-form) after the CBOR-in-CBOR fix (Pragma)
· **Date:** 2026-09-26
**Scope:** serve, over ChainSync, a real live-tip Praos block header that has been **re-encoded with
non-canonical CBOR** (same logical header, different byte encoding) to a mixed devnet running
cardano-node 11.1.2 (`fef83fed`) and Amaru 10.11.20260918 (`aedfe797`), and observe each node's
outcome. This is the opcert campaign's **headline finding** and the first that is a genuine
availability risk (unlike the reported-reason-only #4 precedence and #6 decode-leniency findings).

## Headline — a valid-but-non-canonical header CRASHES Amaru; cardano-node accepts it

cardano-node 11.1.2 **accepts** the re-encoded header (decode + header validation pass, node stays
healthy). Amaru 10.11.20260918 **panics and aborts the whole node process**:

```
thread 'amaru-node' panicked at crates/amaru-ouroboros-traits/src/stores/chain_store/types.rs:88:9:
chain-store integrity failure: header stored at key <raw-wire-hash> hashes to <canonical-hash>;
the database is corrupt and Amaru cannot continue
```

This is a **remotely-triggerable node crash / DoS-class divergence**: any ChainSync peer can crash
an Amaru node by serving it a valid header in a non-canonical CBOR encoding.

## Deterministic, reproduced 4×

| run | form | slot | raw-wire key (stored) → canonical (hashes to) |
|---|---|---|---|
| reverify-A (original) | noncanonical-int | 3186 | `1a1af19b…` → `a81f5f80…` |
| repro1 | noncanonical-int | 1908 | `e680b039…` → `3cf95e3f…` |
| repro2 | noncanonical-int | 2681 | `84ca08f5…` → `b0c56d77…` |
| repro3 | indefinite-array | 2730 | `67094c34…` → `7cfd784c…` |

Identical panic (same file:line `types.rs:88:9`, same message) every time; the hashes differ only
because each run served a different live tip. Fully deterministic from `(family, seed, iteration)`.

## Root cause

Amaru stores a received header in its chain-store keyed by the **raw received-wire blake2b hash**,
but its integrity check re-computes the hash of the **canonically re-encoded** header. For a
canonically-encoded header these coincide; for a **non-canonical** encoding of the same logical
header they differ, so the stored key ≠ the recomputed hash and the integrity assertion fires and
**panics** (`chain-store integrity failure … the database is corrupt and Amaru cannot continue`).
In short: **Amaru treats header identity as invariant to CBOR canonicity, but keys by raw bytes and
checks by canonical bytes.** cardano-node canonicalises/normalises on decode and is unaffected.

## Trigger class — inner-header non-canonical CBOR (not int-specific)

Both `noncanonical-int` (a non-minimal integer/length encoding) and `indefinite-array` (an
indefinite-length array in place of a definite one) crash Amaru — they change the raw header bytes
while decoding to the same logical header. `trailing-bytes` (which appends at the OUTER NodeToNode
envelope, leaving the inner header bytes canonical) does **not** crash. So the trigger is **any
non-canonical CBOR re-encoding of the inner header**, not one specific deviation.

## Rigor checks (all passed)

- **Same logical header (Check A).** The served bytes decode to the SAME logical Praos header as the
  canonical one, differing only in CBOR encoding: `reEncodeOpcert` preserves the decoded CBOR term
  (forger `reEncodesToSameOpcert` / `decodeTop`-equality unit tests), the harness canonically
  re-encodes the served deviant back to the canonical hash, and cardano-node validates it as a valid
  Praos header. Amaru's own panic message is corroborating: it reports the header "hashes to"
  the **canonical** hash — i.e. Amaru itself decoded the deviant to the valid header before the
  integrity check fired. So the claim is "Amaru crashes on a VALID re-encoded header," not "on
  garbage." (See `reports/amaru-noncanonical-cbor-crash-evidence/cardano-accepts.txt` for the
  cardano-node `decode-praos-header` OK on both the canonical and the non-canonical encoding.)
- **cardano-node / consensus (Check B).** cardano-node 11.1.2 verdict = **accept** (decode + header
  validation); the node stays healthy and, in this isolated-consumer test, does **not** adopt the
  lone served header as its tip — so **no chain-carried consensus split was observed**. The finding
  is therefore an **availability / DoS-class robustness divergence**, remotely triggerable via
  ChainSync — explicitly NOT (as observed) a consensus/safety split.

## Severity

**HIGH for availability.** A single malicious or buggy ChainSync peer can crash an Amaru node by
sending one valid-but-non-canonically-encoded header — the node aborts its process ("cannot
continue"), a denial-of-service. No special privilege or stake is required; the header is otherwise
valid. Not (observed) a consensus/safety violation, but a node-liveness one.

## Recommendation (for the Amaru maintainers)

Make header identity invariant to CBOR canonicity in the chain-store: either **key stored headers
by the canonical-form hash** (re-encode before hashing/keying), or **reject non-canonical CBOR at
the header decoder** (fail the header cleanly as invalid rather than storing it and then panicking
on the integrity check). Either removes the raw-vs-canonical hash mismatch that drives the panic.
De-escalating the assertion from a process-abort to a per-header rejection would also contain the
blast radius.

## Reproduce / evidence

- Trigger: family A (encoding-form) `noncanonical-int` or `indefinite-array` served via the forger's
  live serve path to a single-target Amaru consumer on profile-zb (requires the CBOR-in-CBOR fix,
  commit `d865648`, so the deviation reaches the inner header).
- Evidence bundle `reports/amaru-noncanonical-cbor-crash-evidence/`: the four crash-panic excerpts
  (`crash-panics.txt`), the trigger-class table (`trigger-class.md`), and the cardano-node
  accept corroboration (`cardano-accepts.txt`). Full crash logs under
  `~/.local/share/dwarf/runs/{reverify-A,confirm-Acrash}/…/amaru-consumer-crash.log`.
