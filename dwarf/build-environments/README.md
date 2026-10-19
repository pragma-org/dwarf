# Trusted candidate build environments

No candidate build lane is executable. These versioned contracts resolve
declared inputs only. The executor still freezes source and writes a blocked
receipt without launching a compiler, container, dependency download or node.

## Retained asset inventory

Inspected at source `122bcd8a391be04e11e9c6550a2f8c54e9b5e73a` on 2026-10-19.
Identities below describe existing recipes; they do not select candidate versions.

| Implementation / lane | Existing toolchain and runtime identity | Dependency inputs and packaging | Missing execution prerequisites |
| --- | --- | --- | --- |
| Amaru stock | Candidate must declare an exact Rust channel. Recipe requires cargo-zigbuild `0.23.4`, Zig `0.15.2`, `x86_64-unknown-linux-musl`. | Candidate `Cargo.toml` / `Cargo.lock`; no stock runtime packaging contract. The shim toolchain is `nightly-2026-09-04`, independently of a submitted node. | Immutable builder, retained Cargo registry/git/native/target sysroot closure, offline evidence, pinned runtime and packaging. |
| Amaru patched | Reviewed manifests use `nightly-2026-09-04` and the same Zig tools. Runtime bases: `ghcr.io/pragma-org/amaru@sha256:45d46a6ba7147bfa95d96c103820542a9e3ac3602c4c316cc0d04bbd6d71489e` (b159 and d3a assets), `ghcr.io/pragma-org/amaru@sha256:2176f12085219a4052be17dca89742954897eb9a5321554c799d0458913c9c60` (aedfe). | Hashed patch sets/preimages; helper verifies a static executable and copies it onto the pinned release base. | Immutable builder, complete retained Cargo/native/tool closure, observed offline verification and candidate patch compatibility. A pinned runtime is not a builder. |
| Amaru compiler coverage | Reviewed manifests use `nightly-2026-09-04`, cargo-fuzz `0.13.1`, `-Cinstrument-coverage`. | Hashed production harness; coverage envelope required separately from build outputs. | Builder, Cargo/native/LLVM profile-tool closure, offline evidence, runtime base/coverage packaging and production API verification. |
| cardano-node stock | Candidate must pin exact GHC; candidate recipe requires Cabal `3.16.0.0`. | Candidate `cabal.project` / freeze. Legacy devnet Dockerfile uses `haskell:9.6.7`, `debian:bookworm-slim`, mutable apt repositories and `cabal update`. | Immutable builder/runtime, full Cabal solver/store/index/native closure, offline evidence and packaging. The legacy Dockerfile is unsuitable. |
| cardano-node patched | GHC `9.6.7`, Cabal `3.16.0.0`; runtime `ghcr.io/intersectmbo/cardano-node:11.1.2@sha256:6365403f44713d0a046865fb0466503ef207b71beae1b1ffece7f4399356db9f`. | Hashed patch/preimage manifests name ouroboros-network `1.2.0.0` (`f94167122d6d7956206626afff2d90ce2637d4c82af8dff8784fae227ddbad70`), ouroboros-consensus `4.2.1.0` (`160af19b249dbb65c8d84cf74a6c475b2080a5b16b1c30c3c709f3e6065f0972`), cardano-ledger-alonzo `1.16.0.0` (`195f1b9dac59bcdd8d4f499275f450bc37ee57169efee0fadf0d1676bc37cae5`). Helper copies binary, verified runtime libraries and wrapper. | Those retained archives, complete solver/store/index/native closure, immutable builder, offline evidence and candidate compatibility. Three archive hashes are not the full closure. |
| cardano-node compiler coverage | GHC `9.6.7`, Cabal `3.16.0.0`, `-fhpc`. | Hashed production harness plus cardano-diffusion `1.1.1.0` archive identity `50c0fceec6bcc8b37f9eefaf4fcb0544eae5108c2413d5d87a9833b623fdc549`; no retained archive. | Builder, full Cabal/native/HPC tool closure, offline evidence, runtime base/coverage packaging and production API verification. |

Relevant checked-in target/build directories contain no complete vendored or
cached dependency archive set. `antithesis/components/dwarf-adversary/flake.lock`
pins Nix input revisions/NAR hashes, but does not retain a Nix store closure for
either candidate node. Its project selects `ghc984` for a separate adversary;
its runtime Dockerfile copies host-built binaries/libraries onto mutable
`ubuntu:24.04` and installs apt packages. These assets cannot enable these lanes.
The baked Amaru Dockerfile similarly wraps existing release images and a store;
it uses mutable Amaru/Alpine defaults and is not a source compiler environment.

The fuzz workflow caches Cargo and lets rustup acquire its toolchain. The security
campaign workflow validates on a hosted runner, then retains blocked build
receipts. Neither workflow provides an immutable candidate builder or a retained
offline closure. Framework Python lockfiles/images are a separate environment.

Inventory sources: `dwarf/targets/{amaru,cardano-node}/`, the four
`dwarf/scripts/build_{amaru,cardano}_{measurement,coverage}_target.py` helpers,
`dwarf/devnet-build/cardano-node.Dockerfile`, the relevant Antithesis Docker/Nix
files above, and `.github/workflows/dwarf-{security-campaign,fuzz}.yml`.

## Contract and trust boundary

Only the trusted checkout's fixed path is selected:

```
dwarf/build-environments/v1/<implementation>/<source-tree-sha256>/<variant>.json
```

The environment and dependency closure use the schemas in
`dwarf/spec/v1/candidate-{build-environment,dependency-closure}.schema.json`.
Candidate metadata cannot select a manifest, command, image, cache or host path.
No real environment manifests are checked in yet. Missing catalog paths appear
in `CandidateBuildPlan.build_environment_blockers` and executor receipts.

The environment binds source digest, implementation, lane, effective trusted
recipe digest, exact toolchain, immutable builder and runtime references, platform,
toolchain probe argv/output hashes, packaging method, and closure file hash.
`environment_recipe_digest(plan)` binds trusted file identities, commands,
environments, generated inputs, patch/harness and artifact/coverage requirements.
The plan digest additionally binds the environment, closure and every retained
file identity, without a circular manifest/plan hash dependency.

The v1 isolation contract requires no network, host socket, secrets, privilege or
capabilities; an unprivileged UID/GID; a read-only root and frozen source; owned
writable outputs; and explicit CPU, memory, PID, timeout and output-byte limits.
These are validation requirements, not a claim that isolation was enforced.
The future backend must run in a disposable environment, clear inherited host
environment/configuration, stage working copies under owned outputs, keep the
frozen source read-only, enforce these limits, and verify probes inside the exact
builder. A host Docker socket must never enter that environment.

The closure binds the exact source locks, builder and effective recipe. It must
retain and hash package-manager configuration, solver plan, native inputs,
Cargo vendor archive or Cabal store and repository indexes, dynamic runtime root
for Cardano, coverage tools for coverage lanes, and every selected dependency
archive. Relative paths cannot traverse or contain symlinks; missing files and
digest changes fail closed. An offline verification record must bind the complete
closure-input digest, exact observed toolchain, successful exit, network `none`,
and a retained hashed log. A lockfile alone cannot satisfy this contract.
Offline evidence remains a trusted declaration until the future backend checks
its provenance and independently observes execution. Test fixtures are synthetic
and cannot authorize or establish a real build.

Even a fully validated contract keeps `observed-toolchain-unverified`,
`disposable-build-backend-unimplemented` and `artifact-verification-unimplemented`
in the receipt. A future backend must retain exact plan/source/toolchain,
executable/image/build-result artifacts and coverage evidence where applicable;
only observed, hash-verified outputs may establish success. Filling these trusted
contracts will resolve inputs without changing the source intake or plan format;
it does not implement that backend.

## Focused implementation and verification

Task 6/9's approved design requires trusted recipes and isolated experimental
workers. The smallest supported step is this data-only contract; reusing host
helpers or mutable Dockerfiles would not satisfy it. Implementation order:
write failing schema/binding tests; add versioned validators; bind plan inputs
and receipt blockers; check negatives and retained-file tampering; retain this
inventory. No builds, downloads, nodes, scenarios, deployment or publication.

```
PYTHONPATH=dwarf python3 -m pytest -q tests/test_candidate_build_environments.py tests/test_candidate_builds.py tests/test_candidate_build_executor.py
git diff --check
```
