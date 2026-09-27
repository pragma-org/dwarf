# amaru Plutus VM ex-memory SIZE-formula diff vs cardano `ExMemoryUsage` (source pass)

Full diff of every size measure amaru feeds into the (shared, pparams-loaded) cost model, from
`crates/amaru-uplc/src/machine/cost_model/value.rs` + `runtime.rs`, vs cardano-ledger's `ExMemoryUsage`
instances / the Plutus spec. The cost-model COEFFICIENTS are the shared protocol-params cost model
(identical on both nodes), so any charge divergence comes from a wrong SIZE measure below.

| type | amaru formula (value.rs) | cardano ExMemoryUsage | verdict |
|---|---|---|---|
| Integer | zero→1; else `(bits(\|i\|)−1)/64 + 1` | zero→1; `(integerLog2 div 64)+1` | **CONFORMANT** (confirmed: source + measured) |
| ByteString | empty→1; else `((n−1)/8)+1` | `((n−1)/8)+1`, empty→1 | **CONFORMANT** (measured exact: appendByteString×20 knife-edge both accept @exact, reject @−1) |
| **String (Text)** | `utf8_byte_len / 4` (no +1) | substantially larger (measured ~4× for ASCII) | **DIVERGENT — under-sizes** → finding-amaru-plutus-string-builtin-cost (cb79184): ~54–69% CPU under-charge, is_valid split |
| Unit | `1` | `1` | CONFORMANT |
| Bool | `1` | `1` | CONFORMANT |
| BLS G1 | `size_of(blst_p1)/8 = 18` | `18` | CONFORMANT (measured: BLS 25/25) |
| BLS G2 | `size_of(blst_p2)/8 = 36` | `36` | CONFORMANT |
| BLS MlResult | `size_of(blst_fp12)/8 = 72` | `72` | CONFORMANT |
| Data node (I/B/List/Map) | `+4` per node | `+4` per node (`nodeMem`) | likely CONFORMANT — VERIFY the constant is 4 |
| **Data Constr** | `4 + Σ memoryUsage(fields)` — the constructor tag/index is IGNORED (`{fields, ..}`, value.rs:96) | `4 + Σ fields` (spec also folds only fields) — but CONFIRM cardano does not charge the constructor index | **FLAG-VERIFY** (high-reach: Data is in every redeemer/datum/scriptContext; if cardano charges the tag, amaru under-sizes every Constr node) |
| ProtoList | `Σ items` (no per-list/elem overhead) | `Σ` | FLAG-VERIFY (confirm no overhead) |
| ProtoPair | `memUsage(l)+memUsage(r)` (no overhead) | `l+r` | FLAG-VERIFY (confirm no pair overhead) |
| **ProtoArray** | `Σ items` (same as list) | array `ExMemoryUsage` (newer type) | **FLAG-VERIFY** (new array type; confirm amaru's per-array sizing matches) |
| **Value (MultiAsset)** | `v.size` — a precomputed count from `count_stats(entries)` (ledger_value.rs) | cardano's `Value` builtin `ExMemoryUsage` (asset/policy-count formula) | **FLAG-VERIFY, HIGH-PRIORITY** (new Conway builtin-Value type; amaru uses a custom count — most likely to diverge after string; attacker can craft many-asset Values) |
| Value-as-term (Lambda/Builtin/Delay/Constr) | `1` | `1` | CONFORMANT (fallback; only Con constants carry real size) |

## Summary
- CONFIRMED CONFORMANT: Integer, ByteString, Unit, Bool, BLS G1/G2/MlResult (source + empirical).
- CONFIRMED DIVERGENT: **String** (utf8_len/4, no +1) — filed (string-builtin cost finding).
- CANDIDATES to knife-edge (hand to 4e; source shows amaru's formula, cardano side needs the empirical
  measure→submit-at-that-budget confirm):
  1. **Value (MultiAsset) `v.size`** — HIGH priority: new type, custom count, attacker-amplifiable via
     many assets/policies. Test: a script consuming a multi-asset Value at a knife-edge.
  2. **Data Constr constructor-tag** — high-reach: amaru ignores the constructor index in the Constr
     node size. Test: a redeemer/datum with a large constructor index / deep Constr nesting at a knife-edge.
  3. **ProtoArray** sizing (new array type); ProtoList/Pair overhead; Data node constant = 4.
  4. Exact cardano `Text` formula (for the string fix).

## Method for 4e (per confirmed finding)
For each candidate: build an aiken PlutusV3 validator dominated by that type/builtin (redeemer-driven to
avoid const-folding), `calculate-plutus-script-cost online` on cardano for the EXACT ex-units, then submit
the tx at that budget (and ±1) to amaru — amaru ACCEPT while cardano REJECT (or vice-versa) = confirmed
cost divergence. Control against a known-conformant type (bytestring) to isolate.
