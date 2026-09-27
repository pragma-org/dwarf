# Plutus VM BLS12-381 builtins — differential coverage (CONFORMANT)

**Surface:** Plutus V3 BLS12-381 builtins (the highest-impact previously-unexercised VM area — only a
single G1 roundtrip was tested before). **Result: 25/25 cases AGREE, no consensus divergence, no VM
crash.** amaru `v10.11.20260925` (`eaf8ac3f`) matches cardano-node `11.1.2` (`fef83fed`) on every case,
including graceful phase-2 rejection (no panic) on all malformed points.
**Method:** aiken v1.1.24 PlutusV3 mint-policy validators asserting each builtin; input via redeemer
(avoids compile-time point-constant folding). Mint tx (funding UTxO 9708b921 as input+collateral, ample
ex-units, is_valid=true), submit to pair1 amaru:3210 vs cardano:8110, per-case reset, grade is_valid +
phase-2 parity, + amaru-liveness (crash) check after each. Harness: fixture bls/validators.ak +
workload/bls_full.py.

## Cases (all AGREE; amaru alive after each)
| builtin surface | cases | verdict |
|---|---|---|
| G1_uncompress | valid→accept; infinity(c0..)→accept; short/long/allzero(00..)/garbage(ff..)/bad-flag(cleared compression bit)→reject | AGREE, no crash |
| G1 compress roundtrip | generator round-trips | AGREE |
| G2_uncompress | valid→accept; infinity→accept; short/allzero/garbage→reject | AGREE, no crash |
| G1 add/neg | P+(-P) = identity (48-byte compress) | AGREE |
| G1/G2 scalarMul | 2·P == P+P (G1 and G2) | AGREE |
| pairing millerLoop+finalVerify | bilinear e(5P,Q)==e(P,5Q) → accept; tampered e(5P,Q) vs e(P,6Q) → reject | AGREE (non-vacuous) |
| G1/G2 hashToGroup | hash(msg,DST) on-curve → accept | AGREE |
| G1/G2 uncompress NON-SUBGROUP | on-curve but outside prime-order subgroup (py_ecc map_to_curve pre-cofactor-clear vectors) → reject; + fed downstream to scalarMul and pairing → reject | AGREE (amaru ENFORCES the CIP-0381 subgroup check; no small-subgroup bypass) |

CRASH-HUNT: every malformed uncompress input (wrong length, all-zero, all-0xFF, bad compression flag)
is gracefully phase-2-rejected by BOTH nodes; amaru's submit-API stays validating (malformed-POST=400)
after each — NO VM panic (contrast the vkey-non-curve-point crash finding on the ed25519 path).

## Non-vacuity
Correct-value cases accept on both; wrong-value cases (tampered pairing, malformed points) reject on
both — the assertions are exercised, not vacuously true.

## Non-subgroup (CLOSED) + notes
- NON-SUBGROUP points: CLOSED. Verified non-subgroup vectors (on-curve, multiply(P,r)!=identity;
  G1 a82e62c0..., G2 a47d40ac...) are PHASE2-REJECTED by BOTH nodes at uncompress, and also rejected
  when fed downstream to scalarMul and pairing — amaru enforces the CIP-0381 subgroup-membership check
  identically to cardano-node. No small-subgroup-bypass; no crash. The BLS surface is fully closed.
- "empty DST" hashToGroup: both ACCEPT (conformant); noted that Plutus hashToGroup does not reject an
  empty DST here (my initial expected-reject label was wrong — both agree).
