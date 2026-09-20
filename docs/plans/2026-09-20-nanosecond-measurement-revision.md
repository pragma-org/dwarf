# Nanosecond Measurement Revision Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an exact, additive nanosecond measurement revision for the patched Amaru and Cardano-node targets, pin the four future client cards to it, and then continue the frozen G3-B through Gate 5 sequence without changing accepted Card 03 evidence.

**Architecture:** Keep the existing whole-microsecond v1 patch sets and profiles unchanged. Add separate `nanoseconds-v2` patch manifests and profiles, teach the existing collectors to accept both locked identities, normalize v2 nanoseconds into exact fractional microseconds, and use the existing report and presentation pipeline. Build and record immutable v2 target artifacts before the four future card runs.

**Tech Stack:** Python 3.12, pytest, JSON Schema, Rust tracing patches, Haskell/Aeson patches, Docker, Git, DWARF scenario YAML.

---

The user explicitly forbids a worktree and subagents. Execute this plan in the current dirty worktree on `cardano-box:/home/nigel/dwarf-pragma`. Preserve `dwarf/state/chain-head.json`. Do not run Card 03, Antithesis, or Moog. Push only reviewed, verified batches to internal V7-PRAGMA.

### Task 1: Lock the precision contract with red tests

**Files:**
- Modify: `tests/test_amaru_patched_collector.py`
- Modify: `tests/test_cardano_patched_collector.py`
- Modify: `tests/test_measurement_report.py`
- Modify: `tests/test_measurement_presentation.py`
- Create: `tests/test_patched_timing_schema.py`
- Create: `dwarf/spec/v1/patched-timing-event.schema.json`

**Step 1: Add the exact Amaru conversion test**

Create a trace event with both `elapsed_nanos: 2184` and a compatibility `elapsed_micros` value. Assert that normalized evidence retains `2184`, the collector distribution uses `2.184`, and the raw NDJSON bytes retain the original integer.

**Step 2: Add the Amaru legacy fallback test**

Use an existing microsecond-only event. Assert that its distribution value is unchanged and that the collector does not invent a nanosecond value.

**Step 3: Add the Cardano exact conversion and fallback tests**

Use a v2 event with `elapsed_nanos: 2184` and `duration_us: 2`. Assert that `elapsed_nanos` wins and yields `2.184`. Use a v1 event with only `duration_us` and assert unchanged fallback.

**Step 4: Add distribution and presentation tests**

Assert that `distribution_summary` preserves fractional inputs and that the presentation card displays `2.184`, not `2` or `2.18`.

**Step 5: Add schema compatibility tests**

Validate one v1 Amaru event, one v2 Amaru event, one v1 Cardano event, and one v2 Cardano event. Reject a v2 event whose nanosecond value is negative, Boolean, or fractional.

**Step 6: Run the focused tests and retain the expected failures**

Run:

```bash
PYTHONPATH=dwarf /home/nigel/.venvs/dwarf-moog-fix/bin/python -m pytest -q \
  tests/test_amaru_patched_collector.py \
  tests/test_cardano_patched_collector.py \
  tests/test_measurement_report.py \
  tests/test_measurement_presentation.py \
  tests/test_patched_timing_schema.py
```

Expected: the new precision tests fail because nanos-first normalization, the compatibility schema, and exact display coverage do not exist.

### Task 2: Implement nanos-first normalization in the existing collectors

**Files:**
- Create: `dwarf/profile_manager/measurement_precision.py`
- Modify: `dwarf/profile_manager/measurement_collectors/amaru_patched.py`
- Modify: `dwarf/profile_manager/measurement_collectors/cardano_patched.py`
- Modify: `dwarf/profile_manager/measurement_presentation.py`
- Modify: `dwarf/spec/v1/patched-timing-event.schema.json`

**Step 1: Add one strict conversion helper**

Implement a helper with this contract:

```python
def precise_microseconds(
    fields: Mapping[str, Any], *, nanos_field: str, micros_field: str
) -> tuple[int | None, float | int | None]:
    nanos = fields.get(nanos_field)
    if nanos is not None:
        if isinstance(nanos, bool) or not isinstance(nanos, int) or nanos < 0:
            return None, None
        return nanos, nanos / 1000
    micros = fields.get(micros_field)
    if isinstance(micros, bool) or not isinstance(micros, (int, float)):
        return None, None
    if not math.isfinite(float(micros)) or micros < 0:
        return None, None
    return None, micros
```

Do not synthesize nanoseconds for a v1 record.

**Step 2: Normalize Amaru timing pairs**

For each timed field used by `_build_measurements`, prefer its `*_nanos` sibling, retain the integer sibling in `event["fields"]`, and store the exact derived value in the existing `*_micros` field consumed by distributions. Leave count and depth fields unchanged.

**Step 3: Normalize Cardano timing**

Retain `elapsed_nanos` and `duration_us`. When valid nanoseconds exist, set normalized `duration_us` to `elapsed_nanos / 1000`; otherwise retain the legacy `duration_us`.

**Step 4: Make identity validation additive**

Replace each single accepted patch-set comparison with a locked mapping from patch-set digest to measurement revision. Keep the existing v1 constants and behavior. Add v2 constants only after Task 4 computes the real patch-set digests. Emit the selected `measurement_revision` in collector results.

**Step 5: Preserve exact display values**

Keep the current report pipeline. Change only numeric display behavior needed to show values such as `2.184` without whole-microsecond coercion.

**Step 6: Run focused tests**

Run the Task 1 command. Expected: all tests pass, except identity tests that are intentionally waiting for the real v2 digest if staged separately.

**Step 7: Commit the reviewed collector batch**

Stage only the precision helper, collectors, schema, presentation code, and their tests. Do not stage G3-B files or `chain-head.json`.

### Task 3: Create revision-locked Amaru nanoseconds-v2 patches

**Files:**
- Create: `dwarf/targets/amaru/measurement-patches-nanoseconds-v2/b159172f25a9c389f82f20bca4f15e3032791638/0001-dwarf-measurement-instrumentation.patch`
- Create: `dwarf/targets/amaru/measurement-patches-nanoseconds-v2/b159172f25a9c389f82f20bca4f15e3032791638/manifest.json`
- Modify: `tests/test_amaru_measurement_patch.py`
- Modify: `tests/test_amaru_measurement_source_contract.py`
- Modify: `dwarf/scripts/build_amaru_measurement_target.py` only if manifest-result metadata needs an additive field

**Step 1: Add red patch-contract tests**

Assert that the v1 manifest and files are unchanged, the v2 manifest declares `measurement_revision: nanoseconds-v2`, every timing boundary emits integer nanos and the existing micros, and the patch-set digest is self-consistent.

**Step 2: Derive a separate full patch from the exact source revision**

At each monotonic boundary, retain the duration once in nanoseconds. Emit the matching `*_nanos` field and derive the existing integer `*_micros` field from that value. Do not call the clock again for compatibility output.

**Step 3: Compute and insert exact patch and patch-set digests**

Use the same ordered digest algorithm as `verify_patch_set`. Run `git apply --check --whitespace=error-all` through the build verifier.

**Step 4: Run Amaru patch and source-contract tests**

Expected: all v1 and v2 tests pass.

### Task 4: Create revision-locked Cardano-node nanoseconds-v2 patches

**Files:**
- Create: `dwarf/targets/cardano-node/measurement-patches-nanoseconds-v2/fef83fed01d7926f3de83b3b917be5a4a48768b5/0001-network-protocol-and-ledger-measurements.patch`
- Create: `dwarf/targets/cardano-node/measurement-patches-nanoseconds-v2/fef83fed01d7926f3de83b3b917be5a4a48768b5/0002-consensus-block-epoch-measurements.patch`
- Create: `dwarf/targets/cardano-node/measurement-patches-nanoseconds-v2/fef83fed01d7926f3de83b3b917be5a4a48768b5/0003-plutus-vm-measurement.patch`
- Create: `dwarf/targets/cardano-node/measurement-patches-nanoseconds-v2/fef83fed01d7926f3de83b3b917be5a4a48768b5/manifest.json`
- Modify: `tests/test_cardano_measurement_patch.py`
- Modify: `tests/test_cardano_measurement_source_contract.py`

**Step 1: Add red v2 contract tests**

Require `elapsed_nanos` at protocol, block-application, epoch-transition, and Plutus boundaries while retaining `duration_us`.

**Step 2: Add the precise fields**

Pass the already computed `endedNs - startedNs` value to each emitter as `elapsed_nanos`. Compute `duration_us` from that same value. Do not alter outcome classification.

**Step 3: Compute manifest digests and run patch checks**

Verify all dependency preimages, ordered patch digests, and the combined patch-set digest.

**Step 4: Run Cardano patch and source-contract tests**

Expected: all v1 and v2 tests pass.

### Task 5: Build, smoke, and register the v2 targets

**Files:**
- Read: `dwarf/scripts/build_amaru_measurement_target.py`
- Read: `dwarf/scripts/build_cardano_measurement_target.py`
- Create through the existing builders: target build result and registry records outside the repository state
- Create: `dwarf/profiles/profile-u-amaru-measurement-nanoseconds-v2/profile.yaml`
- Create: `dwarf/profiles/profile-v-cardano-measurement-nanoseconds-v2/profile.yaml`
- Modify: collector identity maps and profile tests

**Step 1: Build Amaru with the explicit v2 manifest**

Use `--manifest` with a fresh bounded output directory. Require successful patch verification, executable build, static-binary verification, image build, image smoke, and runtime probe.

**Step 2: Record Amaru identities**

Record the source revision, measurement revision, patch-set digest, executable digest, image digest, build-result digest, manifest digest, and registry record path.

**Step 3: Build Cardano with the explicit v2 manifest**

Use a fresh bounded output directory and the existing dependency cache. Require dependency preimage verification, executable build, runtime-library staging, image smoke, and runtime probe.

**Step 4: Record Cardano identities**

Record the same complete identity set as Amaru.

**Step 5: Add new profiles**

Pin the two new profiles to the exact v2 patch-set digests. Do not edit `profile-q-amaru-measurement-patched` or `profile-t-cardano-measurement-patched`.

**Step 6: Complete identity tests and run profile resolution tests**

Prove both v1 profiles still resolve their old artifacts and both v2 profiles resolve only the new records.

### Task 6: Pin only future cards to nanoseconds-v2

**Files:**
- Modify: `dwarf/docs/client-examples/contracts/01-cbor-decoding.yaml`
- Modify: `dwarf/docs/client-examples/contracts/02-plutus-vm.yaml`
- Modify: `dwarf/docs/client-examples/contracts/04-block-application.yaml`
- Modify: `dwarf/docs/client-examples/contracts/05-restart-recovery-sync.yaml`
- Do not modify: `dwarf/docs/client-examples/contracts/03-invalid-mini-protocol.yaml`
- Modify: the Card 01, 02, 04, and 05 scenario files
- Modify: `dwarf/docs/client-examples/GATE-3-FIVE-CARD-GAP-MATRIX.md`
- Modify: acceptance-card tests

**Step 1: Add exact revision metadata**

For each future card leg, state `measurement_revision: nanoseconds-v2` and record the exact profile, patch-set, executable, image, and manifest digests.

**Step 2: Prove Card 03 immutability**

Tests must assert that Card 03 still names the v1 profiles and accepted whole-microsecond revision. Do not rerun it.

**Step 3: Use ASD-STE100 text**

Add a child explanation: the old card keeps its old ruler; the next cards use the finer ruler.

### Task 7: Finish the current G3-B controlled progress and restart slice

**Files:**
- Preserve and finish the current dirty changes in `dwarf/profile_manager/primitives.py`, `dwarf/primitives/registry.json`, the seven new primitive schemas, four Card 04/Card 05 scenarios, and `tests/test_client_example_progress_restart.py`
- Modify: `dwarf/profile_manager/measurement_collectors/amaru_resources.py`
- Modify: `dwarf/profile_manager/measurement_collectors/amaru_external.py`
- Modify: `tests/test_amaru_resource_collector.py`
- Modify: `tests/test_amaru_external_collectors.py`

**Step 1: Reproduce the three currently pending red tests**

Run the single controlled-window test, PID re-resolution test, and exact sync-range-event test. Confirm failures match missing behavior.

**Step 2: Implement real health, restart, peer, and exact-window evidence**

Use observed container state and logs. Do not construct default healthy evidence. Re-resolve the resource PID after restart. Attribute resources to the controlled window. Derive sync speed from the exact start/end events.

**Step 3: Run the complete G3-B focused suite**

Review proof outputs, fail-closed behavior, and both implementation scenario definitions.

**Step 4: Commit and push only after review and full verification**

Push to internal V7-PRAGMA only if authentication is available. Never push publicly.

### Task 8: Continue G3-C and Gates 4 and 5 exactly as frozen

**Files:**
- Follow the exact active goal and the five frozen acceptance cards.
- Update workbench object `obj_eb40eb62fdd24c539d85d818` only with accepted evidence.

**Step 1: Implement and rehearse Cards 01 and 02 on v2**

Use the exact frozen inputs, assertions, target identities, raw evidence, and acceptance rules.

**Step 2: Rehearse Cards 04 and 05 on v2**

Use the completed G3-B primitives and exact resource/sync windows.

**Step 3: Complete G3-C**

Finish the remaining frozen scenario and assertion integration without changing security assertion semantics.

**Step 4: Complete Gate 4**

Run the full regression suite and all required scenario-validation, schema, digest, bundle, GUI, and reproducibility checks.

**Step 5: Complete Gate 5**

Run the accepted real-node examples in frozen order, retain evidence, verify all claims against raw artifacts, update the workbench, and push reviewed commits only to the internal remote.

**Step 6: Leave Gate 6 deferred**

Do not launch Antithesis or Moog.
