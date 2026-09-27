# Finding: Amaru Plutus VM execution-cost SIZE measures vs cardano-node (systematic; one confirmed divergence)

**Component:** `amaru` Plutus VM ex-memory size measures — `crates/amaru-uplc/src/machine/cost_model/value.rs`
(the `*_ex_mem` functions fed into the shared, pparams-loaded cost model at `machine/runtime.rs`).
**Type:** Execution-cost (ex-units) size-measure conformance vs cardano-node. A wrong size → wrong charge →
the two nodes disagree on whether a phase-2 tx (and a block containing it) is valid.
**Severity:** MEDIUM-HIGH for the confirmed String divergence; the rest of the surface is conformant.
**Provenance:** amaru `v10.11.20260925` (`eaf8ac3f`) vs cardano-node `11.1.2` (`fef83fed`), PlutusV3,
shared cost model (pparams, 251 V3 entries, protocol v10). pair1. 2026-09-27. Method: aiken v1.1.24
redeemer-driven validators, `calculate-plutus-script-cost online` for cardano's exact ex-units, then submit
at that budget ±1 to amaru; per-case mempool reset; 2×-confirm; control against a known-conformant type.

## Result — the whole cost-SIZE surface, one pass (source) + empirical confirmation

| size measure | amaru formula | status |
|---|---|---|
| Integer | zero→1; `(bits(\|i\|)−1)/64+1` | CONFORMANT (source = cardano; empirical) |
| ByteString | empty→1; `((n−1)/8)+1` | CONFORMANT (empirical: appendByteString×20 exact @ 4109737/17902, reject @−1) |
| **String (Text)** | `utf8_byte_len / 4` (no +1) | **DIVERGENT — amaru UNDER-sizes → under-charges CPU ~54% (10 append) to >69% (20 append); is_valid split.** Filed: `finding-amaru-plutus-string-builtin-cost` (869cc38). |
| Unit / Bool | `1` / `1` | CONFORMANT |
| BLS G1/G2/MlResult | `size_of(blst)/8` = 18/36/72 | CONFORMANT (empirical: BLS 25/25) |
| Data node (I/B/List/Map/Constr) | `+4`/node; fields summed | CONFORMANT (empirical: equals_data×20 on a nested Data exact @ 27562453/30258, reject @−1) |
| Data **Constr constructor-index** | ignored (`{fields, ..}`, value.rs:96) | CONFORMANT — **verified cardano ALSO ignores it**: cost identical for constructor index 0 vs 2^60 (steps=27562453/mem=30258 both). |
| ProtoList / ProtoPair | `Σ` / `l+r` (via Data, no overhead) | CONFORMANT (covered by the Data equals_data test) |
| **Value (MultiAsset)** | `Constant::Value(v) => v.size` (custom `count_stats`, ledger_value.rs) | **UNVERIFIED (substrate-limited)** — the builtin-Value ops (un_value_data/value_data/union_value) are not exposed by aiken v1.1.24 and the v10 cost model (251 entries) predates them, so a Value cannot be constructed/exercised in a script here. Source-flagged; needs a protocol-v11 substrate or raw-UPLC harness. |

## The single systemic story

Amaru's Plutus `ExMemoryUsage` size measures are **conformant with cardano-node across the entire reachable
surface — integer, bytestring, unit, bool, BLS elements, and all of Data (nodes, fields, and the
constructor index) — EXCEPT the String type**, where `string_ex_mem = utf8_len/4` (no +1) under-sizes the
operand fed to `appendString`/`equalsString`, causing amaru to charge ~54–69% fewer CPU steps than
cardano. Because the cost-model coefficients are the shared protocol-params cost model (byte-identical on
both nodes), every possible charge divergence reduces to a size-measure bug — and only String has one.

- **Consequence (String):** a string-heavy phase-2 tx declaring ex-units between amaru's cost and
  cardano's cost is `is_valid`/in-budget on amaru but `ExUnitsTooBig`-rejected by cardano → the nodes
  disagree on the tx's (and a block's) validity = consensus-validity split; plus a DoS-amplification angle
  (unbounded extra string computation per budget, growing with size). Reachability-bounded: amaru doesn't
  forge Praos and honest producers reject the over-budget tx, but a validating amaru would accept a block
  cardano rejects.
- **Recommendation:** align `string_ex_mem` with cardano-ledger's `ExMemoryUsage Text`. Separately verify
  the **Value** size measure (`v.size` vs cardano's builtin-Value `ExMemoryUsage`) once a substrate with
  the Value builtins enabled is available — it is the one remaining unverified size measure and, being a
  new custom count over attacker-controllable multi-asset data, the highest-risk untested one.

## Cross-references
`finding-amaru-plutus-string-builtin-cost` (869cc38, the confirmed String divergence),
`amaru-plutus-cost-size-diff` (195e155, the full source diff). Harnesses: `fixture/bls/validators/`
(strcost/apponly/cost/dataeq), `workload/bls_full.py`, `workload/plutus_cost_knife_edge.sh`.
