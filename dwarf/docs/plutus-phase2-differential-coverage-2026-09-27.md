# Plutus phase-2 differential — coverage capstone (2026-09-27)

**Result: Amaru v10.11.20260925 (`eaf8ac3f`) is CONFORMANT with cardano-node 11.1.2 (`fef83fed`)
across every Plutus phase-2 behaviour tested — 26/26 AGREE, no `is_valid` split, no eval-error
verdict divergence, and no VM panic.** Graded on a dedicated, mempool-isolated pair; verdict +
phase-2 reason-class parity, fail-closed. Details in
`dwarf/docs/plutus-phase2-differential-family.md`; driver `workload/plutus_differential.py`.

## Scope covered

| tier | cases | what | result |
|---|---|---|---|
| 2a — ex-units & is_valid | 9 | exact-cost knife-edge (steps 64100 / mem 500, measured on the node), under/zero/ample budget, `is_valid`=false collateral paths, always-fails | 9/9 AGREE |
| 2b — builtins & ScriptContext | 6 | `integerToByteString` determinism, BLS12-381 G1 uncompress/compress round-trip, validity-range construction (with the raw-mapping-vs-VM attribution split) | 6/6 AGREE |
| 2c — error paths | 11 | divide/mod-by-zero, `consByteString` byte>255, `integerToByteString` width-too-small & oversized, BLS G1 non-canonical uncompress — each a passing control + failing edge | 11/11 AGREE, no VM panic |

Key datapoints:
- **Ex-unit accounting is exact.** Amaru accepts at the precise cost cardano-node computes
  (same tx id); one unit under, in either dimension, both reject. No off-by-one at the boundary.
- **Slot→POSIXTime is not conflated with VM construction.** cardano `systemStart` and Amaru
  `AMARU_GLOBAL_SYSTEM_START` are identical, so the raw mapping agrees by construction; the
  off-by-1-ms validity-range case rejecting on both then shows the VM builds `txInfoValidRange`
  identically. Had the mapping differed (the bootstrap-trust trait), it would have been
  attributed there, not to the VM.
- **UPLC error paths fail cleanly.** After the submit-path panic finding (a `.expect()` on a
  non-curve-point witness key — see `finding-amaru-vkey-noncurve-point-crash.md`), the VM
  interpreter was fresh ground. All 11 error edges returned `UplcMachineError` / tag mismatch and
  left the node alive (submit API 400 after each). No crash.

## Left on the table (diminishing returns; documented, not run)

The 26 cases cover the reachable-with-`cardano-cli`-plus-Aiken surface at breadth. Deeper edges,
if a future campaign wants them:

- **More builtins:** `byteStringToInteger` bounds, `sliceByteString` / `indexByteString`
  out-of-range, `serialiseData`, `verifyEd25519Signature` / ECDSA / Schnorr well-formed
  edge inputs, `keccak_256` / `blake2b_224` known-answer vectors.
- **More BLS12-381:** subgroup-membership checks, G2 operations, `millerLoop` / `finalVerify`
  pairing, `hashToGroup` with a DST, and further non-canonical / infinity encodings.
- **More ScriptContext fields:** `txInfoInputs` / `txInfoReferenceInputs` ordering,
  `txInfoRedeemers` map, `txInfoData`, V3 `ScriptInfo` variants, `txInfoWdrl`, multi-purpose txs.
- **Cost-model coverage:** these cases are PlutusV3; the chain also carries a PlutusV1 cost model
  (no V2). A V1-script pass would exercise the V1 model.

Each is a bounded extension of this family (same carrier, same oracle). The conformance result
above is the basis for stopping here.
