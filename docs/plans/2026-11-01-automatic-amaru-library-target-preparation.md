# Automatic Amaru Library Target Preparation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** When a normal DWARF Prepare or Run selects an Amaru library scenario, build or reuse the exact source-qualified helper on the controller host, freeze it into the launch, and retain its identity and build evidence in the run.

**Architecture:** Plan resolution will remain read-only. It will add trusted library-target requirements derived from the selected scenario, exact catalog release, and packaged DWARF recipe. Prepare will send those requirements to a new bounded controller operation. The controller will use an execution-host Docker builder, a content-addressed cache, a per-key process lock, and atomic publication. Run will repeat the same validation as a cache-hit fallback before it creates the private controller launch. The controller will freeze the verified executable, generated exact-source manifest, receipt, and log into that private launch; the original dashboard launch and historical target manifests remain unchanged.

**Tech Stack:** Python 3, pytest, DWARF controller contracts/service/runtime, Docker CLI, Rust/Cargo in a pinned Linux builder container.

---

### Task 1: Freeze read-only library requirements in the run plan

**Files:**
- Create: `dwarf/profile_manager/library_targets.py`
- Modify: `dwarf/profile_manager/run_plan.py`
- Test: `tests/test_run_plan.py`

1. Add a failing test that resolves `amaru-cbor-tx-body-fuzz` at an exact Amaru release and expects one requirement with target ID, scenario location, exact version and source revision, platform, trusted recipe digest, and source repository.
2. Add a failing test that a read-only resolve does not invoke any build or Docker action.
3. Add a failing test that `upstream_commit: local` is treated only as a packaged recipe template and never as proof that an executable matches the selected release.
4. Run the focused tests and confirm the expected failures.
5. Implement strict recipe discovery and the smallest run-plan integration needed to pass them.
6. Run the focused tests and commit.

### Task 2: Add a controller-owned, content-addressed preparation cache

**Files:**
- Modify: `dwarf/profile_manager/library_targets.py`
- Create: `dwarf/controller/library_target_builds.py`
- Test: `tests/test_library_target_builds.py`

1. Add failing tests for cache miss/build, verified cache hit/no rebuild, exact version or source change/new key, build failure with retained log, tampered binary rejection, wrong-source receipt rejection, and two concurrent requests producing one build.
2. Run the focused tests and confirm the expected failures.
3. Implement strict requirement validation against the controller's trusted recipe and effective release catalog.
4. Reuse the existing controller Docker execution and release-builder isolation patterns. Build on the controller host for the selected Linux architecture. Clone only the exact selected Amaru commit, verify `HEAD`, retain the Git tree identity, stage the packaged shim sources without changing the Amaru node source, and compile only the selected binary.
5. Publish the executable, exact-source target manifest, build log, and receipt atomically under a key that includes source, recipe, builder, toolchain, target, and platform identities.
6. Validate all receipt fields and file hashes on every hit. Reject symlinks, path escapes, stale source, recipe mismatch, platform mismatch, and changed bytes. Never fall back to an older version.
7. Run the focused tests and commit.

### Task 3: Connect Prepare to the controller operation

**Files:**
- Modify: `dwarf/controller/contracts.py`
- Modify: `dwarf/controller/service.py`
- Modify: `dwarf/controller/docker_runtime.py`
- Modify: `dwarf/profile_manager/dashboard.py`
- Modify: `dwarf/profile_manager/topology_actions.py`
- Test: `tests/test_controller_contracts.py`
- Test: `tests/test_controller_service.py`
- Test: `tests/test_landing_run_wizard.py`

1. Add failing contract tests for a bounded `library.prepare` request and rejection of unknown fields, malformed digests, unsafe target IDs, or unsupported implementation.
2. Add failing service tests that the operation runs through the existing idempotent mutation boundary and returns only validated receipt data.
3. Add failing dashboard integration tests that Prepare calls the controller for library requirements, reports a clear block on incompatible source/build failure, and skips the operation for non-library scenarios.
4. Run the focused tests and confirm the expected failures.
5. Implement the contract, service dispatch, runtime adapter, and Prepare generator integration. Keep `/api/run/resolve` free of builds.
6. Run the focused tests and commit.

### Task 4: Freeze the verified target into the real Run path

**Files:**
- Modify: `dwarf/controller/docker_runtime.py`
- Modify: `dwarf/profile_manager/launch_store.py`
- Modify: `dwarf/profile_manager/primitives.py`
- Test: `tests/test_controller_docker_runtime.py`
- Test: `tests/test_launch_store.py`
- Test: `tests/test_scenario_runner.py`

1. Add a failing controller integration test that enters `DockerRuntime.run_launch`, performs its existing catalog and launch digest checks, validates or prepares the required target, freezes it, then passes the derived private launch to `cardano-profile scenario run`.
2. Add failing tests that the dashboard launch remains unchanged, the frozen generated manifest names the selected exact source revision, and the primitive verifies the declared binary digest before execution.
3. Add a failing test that the run evidence retains the executable, generated manifest, receipt, build log, source identity, recipe identity, builder identity, and binary identity.
4. Run the focused tests and confirm the expected failures.
5. Implement the derived controller-private launch binding without changing the checked dashboard launch. Validate the cache again immediately before freezing and use atomic copies.
6. Extend retained launch inputs so the prepared-library evidence is copied into the canonical run bundle.
7. Add a focused direct-CLI test. The CLI must either use this same verified controller-host preparation contract or stop before execution with a precise message that names the required prepared target, exact version, exact source revision, and preparation command/path. Do not claim CLI automation unless this path is integrated.
8. Run the focused tests and commit.

### Task 5: Verify locally and prove on the execution host after idle

**Files:**
- Modify only if a test exposes a scoped defect.

1. Run all focused test files from Tasks 1-4.
2. Run only focused affected tests. Broaden a test set only when a focused failure or a demonstrated shared-contract dependency gives new evidence that the broader set is needed.
3. Inspect the diff for secrets, historical-manifest changes, accidental source patches, and unrelated files.
4. Before any live replacement, use an isolated controller/runtime setup to prove that the packaged shim recipe compiles unchanged against Amaru `10.11.20261029` at source revision `23031e0ed4423b9cb899da030d515cb69d203749`, and that the resulting executable passes its input/output smoke contract. Treat an incompatibility as a clear blocked preparation; do not patch the Amaru node source or silently use an older recipe.
5. Wait until the already-running security workload and its containers are fully closed. Do not start another security scenario.
6. Build and deploy the feature only after the active deployment is idle and an explicit deployment step is in scope.
7. Through the normal dashboard Prepare/Run path on `cardano-box`, prove one cache miss/build and one cache hit/no rebuild for Amaru `10.11.20261029` at `23031e0ed4423b9cb899da030d515cb69d203749`. Verify the run bundle and cache receipt hashes.
8. Report source changes, focused test evidence, isolated exact-source build proof, and any later live-host proof as separate sections. Do not claim host proof before it exists.
