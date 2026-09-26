# Amaru custom-testnet bootstrap regression (v10.11.20260903 → v10.11.20260925)

> **Current, reproducible interop/usability regression.** Amaru's `node bootstrap` can no longer
> full-store-bootstrap a **custom testnet** (`testnet_<U32>`) from local snapshots. This blocks
> standing up a fresh Amaru node on a DWARF custom devnet with the latest release. Discovered
> while re-validating the four phase-1 differential families against latest amaru.

## Version provenance

- Works: **v10.11.20260903** — `git_commit ea1f34e4` (relay-image built from this; supported
  `amaru node bootstrap --network testnet_42 --era-history <file>`).
- Regressed: **v10.11.20260925** — `git_commit eaf8ac3f` (tag confirmed), the current tagged
  latest. Built locally from source (`cargo build --release`, nightly-2026-08-03) for this test.
- cardano-node 11.1.2 is current (unaffected).
- The change is in the ~271-commit window between ea1f34e4 and eaf8ac3f (the S3 / peer-snapshot
  bootstrap redesign).

## Symptoms (exact)

1. `amaru node bootstrap --network testnet_42 …` — `--era-history` was **removed** from bootstrap,
   and bootstrap is now **S3-only**:
   ```
   ERROR amaru::cli: error description="no era history available for network testnet_42;
   S3 bootstrap is only supported for mainnet, preprod, and preview"
   ```
   (source: `crates/amaru-bootstrap/src/bootstrap/mod.rs` — `bootstrap_snapshots` calls
   `network.as_era_history()`, which is `None` for a custom testnet.) The bootstrap `--help`
   now shows only **S3 Snapshot Options** (`--s3-bucket`, `--s3-endpoint`, …); no local-snapshot
   or `--era-history` input remains.
2. The local import path moved to `amaru dev ledger states import`, but it **rejects a custom
   testnet** and offers **no global-parameter override flags**:
   ```
   ERROR amaru::cli: error description="no global parameters available for network testnet_42"
   # and: `--consensus-security-param` / `--era-history` / `--help-global-parameters` are all
   #      "unexpected argument" on `dev ledger states import`.
   ```
   It also only builds the **ledger** store, not the full ledger+chain+nonces+headers store that
   `node bootstrap` produced.

So on eaf8ac3f there is **no working command** to freshly full-store-bootstrap a custom-testnet
Amaru node from local cardano-node snapshots. `snapshot create` still works locally; the
regression is on the bootstrap/import side.

## What still works (the workaround used for re-validation)

- `amaru node run` still accepts custom-testnet global parameters (via `AMARU_GLOBAL_*` env /
  the "Network Global Parameters Overrides" flags) **and** `--era-history <file>`.
- The on-disk **store format is compatible**: eaf8ac3f `node run` opens a 0903-bootstrapped
  `testnet_42` store cleanly (`build.ledger_opened tip=…`, `submit_api.started`, all protocol/gov
  params loaded, no migration or panic).

**Workaround:** bootstrap the store once with a pre-regression binary (v10.11.20260903), then run
the latest binary (eaf8ac3f) against that store. This validates eaf8ac3f's **validation** path
(what the differential families exercise) without needing eaf8ac3f's broken bootstrap.

## Impact on "latest amaru support"

Support for v10.11.20260925 is **PARTIAL**: **validation = yes** (all four phase-1 families
conformant — see below), **custom-testnet full-store bootstrap = no** (regressed). A DWARF devnet
on latest amaru must bootstrap its store with 0903 (or wait for an upstream fix), then run 0925.

## Upstream-flag candidate

Usability/interop regression: dropping local custom-testnet `node bootstrap` (in favour of
S3-only for well-known networks) removes the only path to bootstrap a private/custom devnet with
the release binary. Candidate to raise with the amaru team (novelty check pending). Note `node run`
retains custom-testnet support, so the fix is plausibly re-exposing local snapshots + era-history
(or global-parameter overrides) to `node bootstrap` / `dev ledger states import`.

## Cross-reference

Latest-amaru re-validation of the four phase-1 families ran against eaf8ac3f on 0903-bootstrapped
stores (this workaround): min-fee band (gap still 0), native-script, gov-cert, gov-vote — all
CONFORMANT with cardano-node 11.1.2 (see each family doc's re-validation note).
