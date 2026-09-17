# Cardano CBOR Dataset Integration

DWARF can replay the client-owned
[`cardano-cbor-dataset`](https://github.com/r2rationality/cardano-cbor-dataset)
against the real Cardano ledger decoder and Amaru kernel decoder without
starting a node or simulating either implementation.

## What the scenario proves

`cardano-amaru-cbor-dataset-plutus-data-differential`:

1. refuses a dataset checkout that is not at revision
   `a7561cd063550c2218898571520f14c3674efe91`;
2. selects the same 25 `valid`, `zap-1`, `zap-2`, and `zap-3` Conway
   `plutus_data` inputs for every run;
3. runs the dataset project's typed Haskell verifier against that exact staged
   subset and retains its coverage artifacts;
4. replays every selected byte string through DWARF's Cardano-ledger and Amaru
   kernel shims;
5. retains input hashes, target binary hashes, timing, stdout/stderr prefixes,
   classifications, and a per-input transcript; and
6. passes only if all 100 inputs reach both targets, all four categories are
   exercised, the typed verifier passes, both implementations agree, category
   expectations hold, and neither target crashes or times out.

The scenario is deliberately library-tier. It does **not** claim live-node,
transaction-submission, mempool, N2N/N2C, consensus, or deployment coverage.

## One-time preparation

Choose any external working directory; do not copy the 158 MB corpus into the
DWARF repository.

```bash
git clone https://github.com/r2rationality/cardano-cbor-dataset.git
cd cardano-cbor-dataset
git checkout a7561cd063550c2218898571520f14c3674efe91
docker build --platform=linux/amd64 -t cardano-cbor-dataset:a7561cd .
export DWARF_CARDANO_CBOR_DATASET_REPO="$PWD"
```

The pinned Haskell base image currently contains Debian snapshot entries plus
expired live Debian entries. If `apt-get update` returns 404s, build from a
temporary copy of the pinned Dockerfile that enables its existing
`snapshot.debian.org` entries with `check-valid-until=no` and removes the live
`deb.debian.org` entries. Do not change the dataset, ledger, crypto-library, or
base-image pins. The verified local image has immutable ID and repo digest
`sha256:9e29588ab98968002e8ee4d9015649e55fb4b3a6cbac6be412afbb0fcc766330`
and retains ledger revision
`6176e413b2be9880c23cedbdaa899f9752693b22` in its image label.

The Cardano and Amaru target shims referenced by the scenario must already be
built. The qualified Amaru revision is
`bbce06e56ba3bfc915840922fd37c868cc6000be` (PR #1330); its exact commit and
both target executable SHA-256 values are recorded in each run. The Amaru shim
toolchain is pinned to the same `nightly-2026-09-04` revision as that upstream
commit.

## Run through DWARF

From the DWARF repository root:

```bash
cardano-profile scenario run \
  dwarf/scenarios/cardano-amaru-cbor-dataset-plutus-data-differential.yaml
```

The run bundle contains:

- `outputs/cardano-cbor-dataset-plutus-data-differential/result.json`
- `outputs/cardano-cbor-dataset-plutus-data-differential/inputs.ndjson`
- `outputs/cardano-cbor-dataset-plutus-data-differential/summary.md`
- typed-verifier stdout/stderr logs; and
- `outputs/cardano-cbor-dataset-plutus-data-differential/reference-coverage/`.

`result.json` records the source revision, selected corpus digest, individual
input hashes, verifier identity, target manifest metadata, target binary
hashes, reachability counts, disagreement counts, expectation mismatches, and
crash/timeout counts.

## Why Plutus data is the first surface

DWARF qualified every Conway rule using the repository's own typed verifier.
`plutus_data` is currently the only rule for which all 400 checked-in samples
obey the advertised categories: 100 valid samples pass and 300 zap samples are
rejected. Other rules must not be promoted until their source contracts pass.

An earlier qualification run against Amaru commit `493bffba...` reproduced 30
over-64-byte acceptance mismatches. Amaru PR #1330 fixed that known decoder
issue and merged as `bbce06e...` on 2026-09-17. DWARF retains the old run as
regression evidence; it does not report it as a new vulnerability.

## Verified runs

- `20260917T153420Z-c0fc3b5c`: known-background reproduction against Amaru
  `493bffba...`; all 100 inputs reached both targets, with 30 bounded-byte
  acceptance mismatches and no crashes or timeouts. The assertion failed.
- `20260917T160226Z-6bf185e1`: fixed-revision proof from a clean public-main
  overlay (`973718a3971a7bc9311c431c29a7176210b27c7d`) against Amaru
  `bbce06e...`; 25 samples from each of the four categories reached both
  targets, both produced 25 accepts and 75 clean rejects, the upstream verifier
  passed, 199 verifier coverage files were retained, and there were zero
  disagreements, expectation mismatches, crashes, or timeouts. The bundle also
  retains the verifier repo digest, architecture, OS, and ledger-revision
  label. The DWARF assertion passed.

## Promotion path

Additional dataset rules should be promoted only when both implementations
have an equivalent typed surface. Live node/protocol scenarios remain separate
because decoder agreement alone does not prove admission, ledger application,
chain adoption, or recovery behavior.
