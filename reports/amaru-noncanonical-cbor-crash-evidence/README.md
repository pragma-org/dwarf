# Evidence — Amaru non-canonical-CBOR node crash / DoS

Supporting evidence for `dwarf/docs/finding-amaru-noncanonical-cbor-crash.md` — the opcert
campaign's headline finding and first real availability risk. Serving a REAL live-tip Praos header
re-encoded with non-canonical CBOR (same logical header, different bytes) over ChainSync to a
single-target Amaru 10.11.20260918 consumer on profile-zb: **Amaru panics and aborts the node
process** (chain-store integrity failure, `types.rs:88:9`) while cardano-node 11.1.2 accepts the
same header. Deterministic; reproduced 4x.

## Files
- `crash-panics.txt` — the panic excerpt from each of the 4 crash runs (original reverify-A +
  repro1/repro2 noncanonical-int + repro3 indefinite-array), each with the raw-wire key -> canonical
  hash pair. Full logs live under
  `~/.local/share/dwarf/runs/reverify-A/...` and `.../runs/confirm-Acrash/.../amaru-consumer-crash.log`.
- `trigger-class.md` — which encoding forms crash (noncanonical-int, indefinite-array) vs not
  (trailing-bytes / outer-envelope), pinning the trigger to inner-header non-canonical CBOR.
- `cardano-accepts.txt` — cardano-node 11.1.2 `decode-praos-header` accepts both the canonical and
  the non-canonical re-encoding; live verdict=accept, node healthy, no tip adoption (no observed
  consensus split).

## Root cause (one line)
Amaru keys stored headers by the raw received-wire hash but integrity-checks the canonically
re-encoded hash -> a non-canonical CBOR encoding makes the two differ -> panic. Header identity is
not invariant to CBOR canonicity. Remotely triggerable via ChainSync => node crash / DoS.
