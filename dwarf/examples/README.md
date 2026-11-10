# DWARF example evidence

Real evidence bundles from DWARF runs, published so you can see what a run produces and
check its results yourself.

## campaign-2026-11-09

The local wiring campaign: every scenario in the campaign set run end to end on a single
build host. **117 scenarios, all validated end to end.** One bundle per scenario: the
latest run.

- 116 runs passed every assertion.
- `ledger-submit-value-coin-i64-overflow-regression-latest-amaru` is validated by
  detection. Its run fails because it detects an open Amaru bug: v10.11.20261029 panics
  on a coin value above i64::MAX (value.rs:337).

Each scenario folder holds:

| File | What it is |
|---|---|
| `summary.json` | Run id, outcome, framework commit, and each assertion with its result |
| `bundle.tar.gz` | The full run folder: scenario, log, manifest, assertions, outputs, measurements |

`index.json` lists all 117 with their outcome and framework commit.

### What was masked

These bundles are real recorded runs, so they named the machine they ran on. Before
publishing, `dwarf/scripts/scrub_bundle_fixtures.py --private-ips --signing-keys` was run
on every bundle:

- Home paths (`/home/<user>`, `/Users/<user>`) became `/home/dwarf`.
- The build host's name became `dwarf-host`.
- Every private (RFC 1918) address became `192.0.2.<last octet>` (RFC 5737 documentation
  range). The last octet is kept, so different peers stay different. Public addresses
  (for example public preview relays) are unchanged.
- The key bytes of every signing key (`*.skey`, type `...SigningKey...`) became
  `redacted-by-dwarf`. These were throwaway devnet keys. Verification keys are unchanged.
- Every sha256 inside a bundle that named a changed file was updated, and the one
  attestation (`runtime-bundle-attestation-example-smoke`) was re-signed with the DWARF
  development key, keeping its attested run id and time.

Two binary files could not be masked in place and were removed. Each leaves a
`<name>.REMOVED.txt` with the original file's sha256 and the reason:

- `outputs/cbor-conformance-adapter/adapter` (client-example-cbor-decoding): a compiled
  adapter binary that embeds build-host paths.
- `outputs/substrate-checkpoint/substrate-checkpoint.tar`
  (runtime-substrate-checkpoint-resume-recovers): a binary substrate checkpoint that holds
  devnet genesis keys and host paths.

Because of the masking, a file's sha256 can differ from the value in the original run's
records outside the bundle; inside each bundle the references are consistent.

### Reproduce

Each `bundle.tar.gz` contains the exact `scenario.yaml` that ran. Load it into DWARF
(dashboard or `cardano-profile scenario run`) to run the same scenario on your own
substrate.
