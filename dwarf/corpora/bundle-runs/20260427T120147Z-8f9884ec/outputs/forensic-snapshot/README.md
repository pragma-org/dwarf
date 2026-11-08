# Dwarf Forensic Snapshot

- Generated at: 2026-04-27T12:01:47Z
- Included bundles: 3
- Requested bundles: 20260427T092200Z-audittraildemo, 20260427T074053Z-ba7c46cc
- Seed bundles after tag filter: 20260427T092200Z-audittraildemo, 20260427T074053Z-ba7c46cc
- Tarball root: snapshot/runs/

## Verification

1. Extract `snapshot.tar.gz` into a working directory.
2. The included bundle tree will be under `snapshot/runs/`.
3. To verify any included bundle's provenance chain, run:

```bash
./cardano-profile bundle audit-trail <run-id> --runs-dir <extracted>/snapshot/runs
```

4. Cross-check bundle hashes and attestation verdicts against `snapshot-manifest.json`.

## Included Bundles

| run_id | scenario_id | attestation | tags |
| --- | --- | --- | --- |
| 20260427T091106Z-3bce7cb4 | runtime-bundle-attestation-example-smoke | verified | - |
| 20260427T092200Z-audittraildemo | runtime-bundle-replay-example-smoke | verified | - |
| 20260427T074053Z-ba7c46cc | runtime-corpus-synthesize-handshake-smoke | unsigned | milestone-1-evidence, release-candidate |
