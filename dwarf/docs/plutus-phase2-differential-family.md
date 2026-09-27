# Plutus phase-2 differential family

A DWARF differential family (extends `workload/mixed_phase1.py`) covering **Plutus phase-2
evaluation**: does Amaru's Plutus VM reach the same accept/reject verdict as cardano-node's for
the same transaction? Each case mints under a PlutusV3 policy and is **phase-1-valid** (valid
collateral, script-integrity hash from the live protocol parameters, redeemer present, declared
ex-units ≤ `maxTxExUnits`), so any rejection is unambiguously **phase-2** — a mismatch between
the transaction's claimed `is_valid` tag and the VM's actual script result.

> **STATUS — phase-2a GRADED (2026-09-27): 9/9 AGREE.** Amaru v10.11.20260925 (`eaf8ac3f`) is
> CONFORMANT with cardano-node 11.1.2 (`fef83fed`) on ex-unit accounting and `is_valid`-tag
> handling for the always-succeeds / always-fails V3 policies. **No `is_valid` divergence.**
> Notably the ex-unit knife-edge agrees exactly (below). Graded on a dedicated, mempool-isolated
> pair; mempools reset before every run. Evidence: `fixture/plutus/graded-phase2a-2026-09-27.json`.
> Phase-2b (Aiken builtin-semantics + script-context edges) is planned next.

## Oracle (`workload/plutus_differential.py`)

Per case: **verdict parity** is primary — both accept (202: the scripts evaluate to the claimed
`is_valid`) or both reject (phase-2). **One node accepts while the other rejects the same
transaction is a real, consensus-relevant VM divergence** (an `is_valid` split). On a shared
reject, **reason-class parity** requires both to cite the same phase-2 class. Fail-closed: either
node MASKED (funding input already consumed) or unavailable → INCONCLUSIVE, never a pass.

Both nodes reduce every `is_valid`-tag contradiction to one semantic class, phrased differently,
so the marker table aligns them (else agreeing rejects would read as a false REASON-DIVERGENCE):

| direction | cardano-node 11.1.2 | Amaru 0925 |
|---|---|---|
| `is_valid`=true, script fails | `ValidationTagMismatch (IsValid True) (FailedUnexpectedly (PlutusFailure …))` | `phase two validation: expected scripts to pass but they failed: [UplcMachineError …]` |
| `is_valid`=false, script passes | `ValidationTagMismatch (IsValid False) PassedUnexpectedly` | `phase two validation: expected scripts to fail but they passed` |

## The ex-unit knife-edge

The exact cost of the always-succeeds V3 mint, from `cardano-cli conway transaction
calculate-plutus-script-cost online` against the pair's node: **steps 64100, memory 500**
(lovelace 34). `exu-exact` declares exactly that. **Both nodes accept it, with the same tx id** —
so Amaru's step/memory accounting matches cardano-node's exactly for this script; there is no
off-by-one at the budget boundary. `exu-under-steps` (64099) and `exu-under-mem` (499) both push
one dimension below the true cost; both nodes reject (budget exceeded → script fails → tag
mismatch).

## Cases & result

All spend/collateralise the funded UTxO `9708b921…#0`; fee 400001 ≫ min. Violations (reject) are
idempotent and run together; controls (accept) consume the UTxO and run one per mempool reset.

| case | is_valid | script | ex-units | expected | result |
|---|---|---|---|---|---|
| exu-exact | true | succeeds | (64100, 500) exact | accept | **AGREE** (same tx id) |
| exu-ample | true | succeeds | (5e8, 2e6) | accept | AGREE |
| exu-under-steps | true | succeeds | (64099, 500) | reject | AGREE (tag mismatch) |
| exu-under-mem | true | succeeds | (64100, 499) | reject | AGREE (tag mismatch) |
| exu-zero | true | succeeds | (0, 0) | reject | AGREE (tag mismatch) |
| isvalid-false-succeeds | **false** | succeeds | ample | reject | AGREE (tag mismatch: passed unexpectedly) |
| isvalid-false-underbudget | **false** | succeeds | (0, 0) | accept | AGREE (script fails → claim matches → collateral consumed) |
| alwaysfails-valid | true | fails | ample | reject | AGREE (tag mismatch) |
| alwaysfails-invalid | **false** | fails | ample | accept | AGREE (claim matches → collateral consumed) |

Each accept returned the same tx id from both nodes. The `is_valid`=false accepts confirm both
nodes consume collateral and admit a correctly-tagged failing-script transaction identically.

## Scope / caveats

- **Scripts:** always-succeeds (`46450101002499`) and always-fails (`46450101002601`) V3 policies
  — no compiler was needed (phase-2a is fully `cardano-cli`-expressible via
  `--mint-execution-units` and `--script-valid`/`--script-invalid`). Richer builtin-semantics and
  script-context edges are **phase-2b** (Aiken `v1.1.24`).
- **Cost models:** the chain carries PlutusV1 + PlutusV3 only (no V2); these cases are V3.
- **Protocol parameters** for the script-integrity hash are pulled live from the pair
  (`fixture/plutus/pparams.json`), not the older re-bake chain — a stale pparams would
  false-reject on `PPViewHashesDontMatch`.
- The reference cardano-node is 11.1.2; the ref container's `cardano-cli` is 11.2.3 (used only for
  cost measurement and building).

## Reproduce

```
# measure the exact cost against the pair's node socket, then build with it:
docker exec pair2-cardano-ref cardano-cli conway transaction calculate-plutus-script-cost online \
  --socket-path /state/db/node.socket --testnet-magic 42 --tx-file /tmp/measure.tx
EXACT_STEPS=64100 EXACT_MEM=500 fixture/plutus/build.sh
# reset mempools, then:
cd workload && python3 plutus_differential.py --amaru <amaru>/api/submit/tx --cardano <ref>/api/submit/tx
python3 plutus_differential.py --single exu-exact ...   # one accept case per mempool reset
```

Exit codes: 0 all agree, 1 divergence, 2 inconclusive. Keys are testnet-only, no value.

## Phase-2b: Aiken builtin-semantics & script-context edges (2026-09-27)

Higher-novelty tier: real Aiken-compiled PlutusV3 minting policies (aiken `v1.1.24+bacbeb3`),
same carrier and oracle as 2a. Corpus + compiled scripts + Aiken source in `fixture/plutus2/`
(`validators.ak`, `blueprint.json`). **6/6 AGREE** — Amaru 0925 conformant with cardano-node
11.1.2.

| dimension | correct-value (accept) | wrong-value (reject) | result |
|---|---|---|---|
| `integerToByteString` (V3) | `intToBS(bigendian,4,258)==0x00000102` | `==0x00000103` | AGREE (accept / reject) |
| BLS12-381 G1 codec | `g1_compress(g1_uncompress(gen))==gen` | `==0xaa·48` | AGREE (accept / reject) |
| ScriptContext validity-range | lower-bound POSIXTime `==1790492288000` | `==…001` (off by 1 ms) | AGREE (accept / reject) |

Each correct-value tx is accepted by both with the **same tx id**; each wrong-value tx is
rejected by both with a phase-2 tag mismatch. The wrong-value cases prove non-vacuity (the scripts
are not trivially true).

### Validity-range attribution (kept un-conflated, per the two-layer split)

The validity-range case is the consensus-relevant one. Before trusting the script I confirmed the
two layers independently:

- **Raw slot→POSIXTime mapping layer:** cardano-node's `systemStart` = `1790491788000` ms and
  Amaru's `AMARU_GLOBAL_SYSTEM_START` = `1790491788000` ms are **identical**, slot length 0.5 s on
  both. So both map `invalidBefore` slot 1000 → POSIXTime `1790492288000` ms. The mapping layer
  agrees by construction.
- **VM ScriptContext construction layer:** with the mapping equal, the script asserts the lower
  bound equals `1790492288000` exactly. Both accept it; the off-by-1-ms variant
  (`1790492288001`) is rejected by both. So Amaru's Plutus VM builds `txInfoValidRange` with the
  exact same POSIXTime as cardano-node's — the construction layer agrees too.

This is a conformance result, not a finding. Had the raw mapping differed (e.g. a different
`systemStart`, the bootstrap-trust trait), the divergence would have been attributed to the
mapping layer, not the VM — the two are reported separately.

### Reproduce (phase-2b)

```
cd <aiken project>; aiken build
aiken blueprint convert -m p2b -v <validator> > fixture/plutus2/<validator>.plutus
# build each as a mint tx (fee 3000000, ex-units (6e9,6e6) >> true cost; ctx cases add
#   --invalid-before 1000), then:
cd workload && python3 plutus_differential.py --corpus ../fixture/plutus2 --amaru … --cardano …
python3 plutus_differential.py --corpus ../fixture/plutus2 --single int_to_bytes …  # accepts, one per reset
```

Scope: first-cut 2b (one edge per dimension). Natural extensions — more BLS edges (hashToGroup /
pairing / subgroup & non-canonical encodings), more V3 builtins (`consByteString`>255,
`byteStringToInteger`, `divideInteger`-by-zero), and more ScriptContext fields (`txInfoInputs`
ordering, `txInfoRedeemers`, V3 `ScriptInfo`) — are follow-ups if the operator wants deeper
coverage.

## Phase-2c: error-path builtins (2026-09-27)

The VM interpreter's error paths — where a phase-2 divergence or an Amaru VM panic is most
likely. Real Aiken `v1.1.24` PlutusV3 policies; the edge value is supplied via the **redeemer**
(runtime), so Aiken cannot constant-fold the error at compile time (same validator, control
redeemer vs error redeemer). Corpus + source in `fixture/plutus2c/`. **11/11 AGREE, and both
nodes stayed alive after every error-path edge — no VM panic.**

| builtin | control (accept) | error edge (reject) | result |
|---|---|---|---|
| `divideInteger` | `divideInteger(10,2)==5` | `divideInteger(10,0)` (÷0) | AGREE |
| `modInteger` | `modInteger(10,5)==0` | `modInteger(10,0)` (mod 0) | AGREE |
| `consByteString` | `consByteString(65,#"")==0x41` | `consByteString(256,#"")` (byte>255) | AGREE |
| `integerToByteString` | `intToBS(be,0,258)` minimal | `intToBS(be,1,258)` (width too small) **and** `intToBS(be,9000,1)` (oversized width) | AGREE |
| `bls12_381_g1_uncompress` | uncompress+compress the generator round-trips | `uncompress(0xff·48)` (invalid encoding) | AGREE |

Every control is accepted by both (same tx id); every error edge is rejected by both with a
phase-2 tag mismatch. **Amaru's UPLC interpreter fails these builtin error paths the same way
cardano-node's does, and — importantly, given the VM interpreter is fresh ground after the
submit-path panic finding — none of the error edges crashed the node** (both submit APIs answered
after each: liveness confirmed).

Scope: two edges on `integerToByteString`, one on each other builtin. Further error edges
(`byteStringToInteger` bounds, `sliceByteString`/`indexByteString` out-of-range, BLS subgroup /
G2 / pairing-mismatch, `unConstrData` type errors) are natural follow-ups.
