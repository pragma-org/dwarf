# Latest supported pair — light verify: cardano-node 11.1.2 + amaru v10.11.20260925

> **VERIFIED-SUPPORTED (submit-api / verbose-reject taps), 2026-09-26.** Light verify per operator
> (3 representative scenarios + their measurement taps), not the full scenario set.

## Version provenance (with git sha, per convention)

- **cardano-node 11.1.2** — git rev `fef83fed01d7926f3de83b3b917be5a4a48768b5` (catalog `fef83fed`),
  image `ghcr.io/intersectmbo/cardano-node:11.1.2` (`6365403f4471`). submit-api `10.7.1`; cli `10.16`.
- **amaru v10.11.20260925** — git_commit `eaf8ac3f` (built from source, nightly-2026-08-03).

## Store compatibility

cardano-node 11.1.2 **opens the 10.7.1-baked store cleanly** (frozen non-forging ref from the
rebake chain): replays to epoch 3 / slot 1297, era Conway, funded UTxO `9708b921…#0` present — no
migration error or panic. amaru 0925 opens the 0903-baked store cleanly (see the re-validation
note). So no fresh 11.1.2 bake was needed for the submit-api verify.

## 3-scenario + tap-parse result (cardano 11.1.2 fef83fed vs amaru 0925 eaf8ac3f)

The bar is that the **measurement taps parse both nodes' formats** (the patched verbose-reject
classifier + reason-class/credential parity), not just that the nodes run.

| scenario | tap exercised | result |
|---|---|---|
| min-fee band (164180 / 164181) | fee-reject classifier | 164180 → both reject; 164181 → both accept (gap 0). Tap parses both. |
| native-script (5 violations) | `ScriptWitnessNotValidatingUTXOW` / native-script class + script-hash parity | ALL AGREE. Tap parses both. |
| gov-cert (3 violations) | `MissingVKeyWitnessesUTXOW` class + credential parity | ALL AGREE. Tap parses both. |

`mixed_phase1`'s classifier (incl. the `"phase one validation"` marker added for amaru's verbose
0903+ rejects) and the per-family reason-class/credential regexes parse both cardano-node 11.1.2
and amaru 0925 outputs. Verified-supported for the submit-api differential families.

## KNOWN-NOT-YET-VERIFIED on latest (explicit gap, de-scoped from this light verify)

- **N2N sync / header-validation tap on latest = PENDING.** The header-validation (opcert)
  forward-sync tap has NOT been run against 11.1.2 + 0925. It needs the forger + a live mesh + the
  V16 workaround `ExperimentalProtocolsEnabled=false` (when amaru 0925 peers with cardano 11.1.2 the
  N2N handshake otherwise drops). The submit-api differential families verified above do NOT peer
  N2N, so they are unaffected. This gap can be closed later; it was de-scoped from the operator's
  "3-or-so scenarios + taps" light verify.
- amaru 0925 custom-testnet **bootstrap** is regressed (S3-only) — see
  amaru-custom-testnet-bootstrap-regression-0903-to-0925.md; the store here was bootstrapped with
  0903 and run with 0925.

## Adoption (default pair for NEW scenarios)

cardano-node 11.1.2 (`fef83fed`) + amaru 0925 (`eaf8ac3f`) is the **default / current-supported
pair for NEW scenarios and measurements** going forward (with the 0903-bootstrap workaround for
amaru and the EPE=false workaround for any N2N sync scenario). This is a pointer/label: the already
delivered families remain validated on their as-run pair (amaru 0903 `ea1f34e4` + cardano-node
10.7.1 `045bc187`) and are re-validated on latest per each family doc's re-validation note — they
are NOT re-pointed. The catalog already records fef83fed + eaf8ac3f.
