# Finding: Amaru under-charges Plutus string builtins (execution-cost non-conformance → is_valid consensus split)

**Component:** `amaru` Plutus VM cost accounting for the string builtins (`appendString`, `equalsString`)
— `crates/amaru-uplc/src/machine/cost_model/value.rs` `string_ex_mem` (fed at `machine/runtime.rs`).
**Type:** Execution-cost (ex-units) non-conformance vs cardano-node → the two nodes disagree on whether a
phase-2 transaction (and a block containing it) is valid.
**Severity:** MEDIUM-HIGH. Measured + 2×-confirmed consensus-validity divergence; reachability bounded
(amaru does not forge Praos; honest cardano producers won't include an over-budget tx) — but a validating
amaru following the network would judge valid a block cardano judges invalid, and the under-charge lets an
attacker run more script computation per tx than the protocol budget permits on an amaru validator/relay
(resource/DoS angle).
**Provenance (ground-truthed via `--version`):** amaru `v10.11.20260925` (git `eaf8ac3f`) vs cardano-node
`11.1.2` (git `fef83fed`). pair1 (amaru `:3210` vs cardano `:8110`), shared PlutusV3 cost model
(`pparams.json`), is_valid=true mint. 2026-09-27.

## Summary

Amaru computes a **much smaller execution cost** than cardano-node for scripts using the string builtins
`appendString`/`equalsString`, dominated by **CPU/steps** (~54% lower for a 10-append script; ~69% lower
for 20 appends — the gap **grows** with string size/count). Consequently a string-heavy phase-2 tx whose
declared ex-units lie **between** amaru's cost and cardano's cost is accepted by amaru (is_valid, in
budget) and **rejected by cardano-node** (`ExUnitsTooBig`/phase-2). The nodes disagree on the tx's
validity → a validating amaru would accept a block cardano rejects (consensus-validity split).

## Evidence (measured; `calculate-plutus-script-cost online` for cardano's exact cost, then submit at
that exact budget ± ε to both nodes; per-case mempool reset; is_valid=true)

Script A = `decode_utf8(#"61626364")` → 10× `append_string` → `equals_string` (mint policy):
| budget (steps, mem) | amaru | cardano |
|---|---|---|
| (21774298, 14458) = cardano exact | ACCEPT 202 | ACCEPT 202 |
| (21774298, **14457**) mem−1 | **ACCEPT 202** | REJECT 400 | ← **2×-confirmed** |
| (**21774297**, 14458) steps−1 | **ACCEPT 202** | REJECT 400 |
| (10000000, ample) | ACCEPT 202 | REJECT 400 |
| (9950000, ample) | REJECT 400 | REJECT 400 |
- Amaru's exact charge for Script A ≈ **10,000,000 steps** (accepts 10.0M, rejects 9.95M) and mem ≈
  **14,300** (accepts 14300, rejects 14200). cardano charges **21,774,298 steps / 14,458 mem**.
  → amaru **~54% lower on steps**, ~1% lower on mem.

Script B = `decode_utf8` → **20×** `append_string` → `equals_string`:
- cardano exact = **64,419,678 steps / 19,158 mem**; amaru ACCEPTS at **20,000,000 steps** (and at
  cardano−1) → amaru **>69% lower on steps**. The divergence **scales** with the number/size of string
  ops (10→20 appends: cardano 21.8M→64.4M vs amaru ~10M→≤20M).

## Control (isolates the cause)

The identical harness with the **bytestring** builtin `appendByteString` (20× on empty operands) is
**exactly conformant** — both nodes accept at (4109737 steps, 17902 mem) and both reject at any −1. So
the divergence is **specific to the string builtins**, not general cost accounting.

## Source anchor

`machine/runtime.rs`: `appendString` and `equalsString` feed `value::string_ex_mem(arg)` to the cost
model; `decodeUtf8` feeds `value::byte_string_ex_mem(arg)` (the input bytes). In
`machine/cost_model/value.rs:51`:
```rust
pub fn string_ex_mem(s: &str) -> i64 { s.len() as i64 / 4 }   // UTF-8 byte length / 4, NO +1
```
This under-estimates the string operand size relative to cardano-ledger's `ExMemoryUsage Text`. The cost
model **coefficients** are loaded from the shared protocol-params cost model (identical on both), so the
divergence is the **size measure** amaru feeds into the (size-dominated) CPU cost of the string builtins —
CPU is size-dominated (huge divergence) while memory is intercept-dominated (barely diverges), matching
the measured ~54% steps / ~1% mem split. (Bytestring size `byte_string_ex_mem` = `((n-1)/8)+1` was
verified conformant by the control, so only the string size formula is wrong.)

## Recommendation

Align `string_ex_mem` with cardano-ledger's `ExMemoryUsage Text` (the size measure fed to `appendString`/
`equalsString`), so amaru's charged ex-units for string builtins equal cardano's. Add a differential
regression: a string-builtin script graded by `calculate-plutus-script-cost` parity between the nodes.

## Reachability / severity bound (honest)

- amaru does not forge Praos blocks, and honest cardano producers reject the over-budget tx, so this is
  not realized on the honest chain by amaru alone.
- It IS a real ledger-rule (ex-units) non-conformance: a validating amaru following the chain would judge
  a block valid that cardano judges invalid (`ExUnitsTooBig`) = consensus-validity disagreement; and an
  amaru validator/relay lets a tx consume more string-builtin computation than the protocol budget allows
  (resource/DoS angle that grows with string size).

## Protocol-version scope (pv10 current; pv11 open)

- This divergence is **MEASURED at protocol version 10** (amaru `Semantics::C` for PlutusV3), which **IS the
  current live protocol** — Conway mainnet is pv10; pv11 is only *proposed* (announced 2025-12-04, not yet
  enacted). So this is a **real current-protocol consensus-validity finding**, not stale/hypothetical.
  Severity stays **MEDIUM-HIGH** for the current protocol.
- amaru's `machine/semantics.rs` **changes the string cost measure at pv11**: `costs_strings_by_utf8_bytes()`
  is `true` for `Semantics::D|E` (pv11) and `false` for `C` (pv10). So amaru's pv11 string costing runs a
  **different code path** than the one measured here. It is **UNTESTED** — it may match cardano's pv11 model
  (amaru may already address this at the future hard fork) or may still differ. Not asserted either way
  (verify-don't-predict); tracked as an open pv11 candidate — see the combined cost-size doc's pv11
  forward-looking surface.

## Artifacts

aiken v1.1.24 validators `fixture/bls/validators/{strcost.ak, apponly.ak}` (redeemer-driven to avoid
const-folding), harness `workload/bls_full.py` machinery + the measure/knife-edge scripts. Control:
`cost.ak` empty-bytestring amplifier (conformant).
