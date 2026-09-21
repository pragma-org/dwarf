# Cardano CBOR Dataset Integration

## Objective

Add an isolated library-tier DWARF scenario that replays a pinned subset of
`r2rationality/cardano-cbor-dataset` through the real typed Haskell verifier,
DWARF's Cardano-ledger shim, and DWARF's Amaru kernel shim. The scenario must
retain enough provenance and per-input evidence to distinguish:

- an invalid or unverified source corpus;
- Cardano/Amaru acceptance disagreement;
- a clean rejection;
- a target crash or timeout; and
- an implementation result that contradicts the corpus category contract.

This slice does not require a live topology and does not claim node, N2N, N2C,
consensus, mempool, or deployment coverage.

## Pinned source

- Repository: `https://github.com/r2rationality/cardano-cbor-dataset.git`
- Revision: `a7561cd063550c2218898571520f14c3674efe91`
- Initial era/rule: `conway/plutus_data`
- Required categories: `valid`, `zap-1`, `zap-2`, `zap-3`

The corpus bytes are not vendored into DWARF. A source descriptor records the
pin and the runner refuses a different Git revision.

## Execution contract

1. Validate the source revision and dataset tree.
2. Select the first N lexicographically sorted files from every required
   category and hash every input.
3. Stage the exact selected set in the upstream verifier's expected layout.
4. Run the upstream typed Haskell verifier over that staged set and retain its
   stdout, stderr, exit status, and HPC artifacts when emitted.
5. Feed every selected byte string to both DWARF target shims with a bounded
   per-input timeout.
6. Write a per-input NDJSON transcript plus an aggregate JSON and readable
   summary.
7. Fail the assertion unless every category was exercised, the upstream
   verifier passed, both targets were reached for every input, neither target
   crashed/timed out, and the two targets agreed with each other and with the
   category contract.

## Deliberate claim boundary

This produces typed ledger-decoder differential evidence. It is not full-node
evidence and it does not prove that the live Cardano or Amaru transaction
submission path behaves identically. Promotion to live node/protocol testing
is a later, separate step after the deployment substrate is healthy.

## Files

- `dwarf/corpora/sources/cardano-cbor-dataset.json`
- `dwarf/scripts/runtime_cardano_cbor_dataset_differential.py`
- one new load primitive and one new assertion primitive
- primitive parameter schemas and registry entries
- `dwarf/scenarios/cardano-amaru-cbor-dataset-plutus-data-differential.yaml`
- `dwarf/docs/cardano-cbor-dataset-integration.md`
- focused unit/runtime-contract tests

## Corpus qualification result

The pinned dataset's current typed verifier was run over every Conway rule
before choosing the initial surface. `plutus_data` is the only rule whose 400
checked-in samples currently satisfy the repository's own category contract:
all 100 `valid` samples decode and all 300 zap samples are rejected. In
particular, all 100 `transaction_body/valid` samples are rejected by the same
upstream verifier. DWARF therefore uses `plutus_data` and does not weaken its
proof gate to accommodate mislabeled source samples.

The first real run against Amaru revision
`493bffba0cc4db2291643cdd6698197c374958b3` found 30 cases where Amaru accepted
a Plutus-data byte-string chunk larger than 64 bytes while Cardano ledger and
the dataset verifier rejected it. This is classified as known background, not
a new finding: Amaru PR #1330 fixed the bounded-byte decoding logic and merged
as `bbce06e56ba3bfc915840922fd37c868cc6000be` on 2026-09-17. The final gate is
an exact repeat against that fixed revision.

## Runtime proof

The exact scenario passed end to end through DWARF on `dwarf-host-a` as run
`20260917T160226Z-6bf185e1` from a clean public-main overlay at
`973718a3971a7bc9311c431c29a7176210b27c7d`. It retained seed `0xA7561CD0`,
the pinned dataset revision and aggregate digest, immutable verifier-image
identity and ledger revision, both target revisions and executable SHA-256 values, 100 per-input
records, and 199 verifier coverage files. Both implementations accepted the 25
valid samples and cleanly rejected all 75 zap samples; there were no
disagreements, expectation mismatches, crashes, or timeouts.

## Verification gates

- Runner unit tests with fake typed verifier and fake shims.
- Scenario JSON Schema and semantic validation.
- Exact end-to-end scenario execution on `dwarf-host-a` using fresh output,
  the pinned client dataset revision, real Cardano/Amaru shims, and the real
  upstream verifier image.
- No Antithesis submission and no live-topology claim in this slice.
