# Version-agnostic Plutus differentials and the profile runtime tools

Date: 2026-11-08. Decisions by the user in this session: remove every Plutus scenario that tests
nothing, then set the Plutus scenarios up properly and version-agnostically; build the Rust
runtime tools the profile scenarios need; mount the CBOR dataset.

## 1. Plutus phase-2 differentials

### What is removed

`runtime_plutus_phase2_submit_probe` (4 scenarios, removed in 51ad1263) and
`runtime_plutus_phase2_differential_observation` + `plutus_phase2_differential_equivalent`
(9 scenarios) never sent a transaction. They copied the fixture's `expected` block into their
result and the assertion graded that copy. The 9 are removed in the same change that adds the
real replacement, so the security and qualification suites never point at a missing scenario.

### Why the existing real Plutus tooling is not the base

`runtime_version_pinned_plutus_conformance` runs library-level evaluators built for one Amaru and
one cardano-node revision (adapter records under `measurement-targets/.../plutus-conformance/<rev>`)
and a frozen cost-model file. It is exact and useful, but pinned by construction.

### The base: the on-chain differential

`runtime_uplc_deep_delay_stack_safety` already does the version-agnostic thing:

- nodes bind through the resolved plan (`identity_binding: resolved-plan`), so any catalog release runs;
- the transaction is built against the **live** protocol parameters and cost models queried from
  the running reference node;
- the same signed transaction is submitted to Amaru (container LocalTxSubmission) and to
  cardano-node (the pinned raw LocalTxSubmission helper), and the outcome classes are compared;
- a shallow control must be accepted by both, so a rejection is never mistaken for coverage.

### What is new

1. **A small UPLC term builder** over the existing FLAT encoder: variables (de Bruijn), lambda,
   apply, force, delay, error, builtins by tag, and constants (integer, bytestring, bool, unit,
   data). Encoding follows the Plutus Core specification; every case's program bytes and hash are
   recorded so a result is reproducible.
2. **`runtime_plutus_case_differential`** — one primitive, a list of cases. Each case is a minting
   policy (V2 two-argument, V3 one-argument for the V3-only builtins) that evaluates a check and
   returns unit, or raises `error`. Per case the record keeps: program hash, language, Amaru
   outcome, cardano-node outcome, the specification's expected outcome, and inclusion/adoption.
3. **`plutus_case_outcomes_agree`** — passes when the control is accepted by both nodes, every
   case's outcome is the same on both nodes, and each matches the specification's expectation.
   A disagreement fails with the case named (a finding candidate); missing evidence is inconclusive.

### Case families (replace the 9 removed scenarios)

| Family | Language | Cases (accept and reject) |
|---|---|---|
| CEK integer division semantics | V2 | `divideInteger`/`modInteger` floor vs `quotientInteger`/`remainderInteger` truncation on negative operands; division by zero errors |
| CEK value structure | V2 | big integers beyond 64 bits; `indexByteString` out of range errors; nested `Data` equality |
| Crypto builtins | V2 | `sha2_256`, `blake2b_256` known vectors; `verifyEd25519Signature` RFC 8032 vector true, tampered false |
| BLS12-381 builtins | V3 | G1 generator uncompress/compress round trip; G1 add with negation equals identity |
| Script context (later) | V3 | TxInfo field checks against a redeemer-supplied expectation |

Version agnosticism: the case semantics are fixed by the Plutus language version, not by node
release; cost models and limits come from the live chain; nothing names a node version.

### Suite repointing

- `github-security-regression-v1`: the `cek-value` entry points at the new CEK value scenario.
- `node-measurements-v1`: the three plutus-execution rows point at the new scenarios; their
  `runtime.status` changes from `missing` to `implemented` once a live run produces accepted and
  rejected outcomes.
- Threat coverage TM-016 / TM-036 and measurement coverage list the new scenario ids.

## 2. Profile runtime tools (Rust)

| Tool | Status | Plan |
|---|---|---|
| `dwarf-amaru-runtime-blockfetch` | pallas-network blockfetch client, source in the older tree | port `src/bin` into `dwarf/targets/amaru`, build on the box |
| `dwarf-amaru-runtime-blockfetch-stale-server` | pallas-network server | same |
| chainsync burst | calls Amaru `fetch-chain-headers`, which current Amaru no longer has | new small pallas-network chainsync client in the same crate |
| LSQ debug epoch-state extractor | `cargo run` at every execution | prebuild once with the others |

Build: a host-side script on the box (the pattern of `build_amaru_measurement_target.py`),
against the box's pinned Amaru and pallas checkouts, writing binaries plus a record (source
revisions, sha256) under the controller state, mounted read-only into the controller. The helpers
resolve tool paths from that record, and connect to the node's container endpoint instead of the
host loopback.

### As built (2026-11-08)

- `scripts/build_runtime_tools.py` copies `targets/amaru` and the extractor next to symlinks of
  the host's `codebases/{amaru,pallas}` checkouts, builds with the pinned toolchain, and writes
  `<key>/bin/<tool>` plus `record.json` (revisions, toolchain, sha256 per tool) under
  `~/.local/share/dwarf/state/measurement-targets/runtime-tools`. The installed controller
  already mounts that host folder read-only, so no compose change was needed.
- `runtime_common.runtime_tool(name)` resolves a tool from the record and refuses a digest
  mismatch; `run_blockfetch` and `run_chainsync_fetch` map a published host port to the node's
  container address and container port (`peer_endpoint`), because container nodes publish on the
  host loopback only. The LSQ extractor command uses the recorded binary and keeps `cargo run`
  as the source-checkout fallback.
- The blockfetch client no longer uses an Amaru kernel API for its point check. It reads the slot
  and hashes the header straight from the block CBOR, so it builds against any Amaru revision.
- Box build: amaru 9eb5971f, pallas 3951639, nightly-2026-09-04, key
  `9eb5971fc6e0-3951639df88b-ba952e399454`. Live check against profile-n (cardano-node): chain-sync
  returned 3 headers; blockfetch verified the tip block's point; a wrong hash was refused.
- The committed `Cargo.lock` is left as is. It belongs to an older Amaru revision, and the build
  resolves against whichever checkouts it is given.

## 3. CBOR dataset

The installer already supports `DWARF_CBOR_DATASET_DIR` (read-only bind mount, validated real
git checkout). The box deploy passes `/home/nigel/cardano-cbor-dataset`.
