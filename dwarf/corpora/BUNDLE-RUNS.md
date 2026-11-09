# Fixture bundles (`bundle-runs/`)

Thirteen runs recorded on 2026-04-27, read by the bundle-tool example scenarios
(chain-verify, diff, summary-compose, export-sarif, forensic-snapshot) and by
`tests/test_bundle_tool_fixture_runs.py`.

**Scrubbed 2026-11-09.** `dwarf/scripts/scrub_bundle_fixtures.py` replaced the recording host's
details with neutral values (`/home/<user>` → `/home/dwarf`, host name → `dwarf-host`, a private
subnet → the documentation range `192.0.2.0/24`), repaired every sha256 inside the fixtures that
named a changed file, and re-signed the two attestations with the same DWARF development key
(`development-embedded-key`, `operator_warning: true`) they were made with. Run ids and attestation
times are unchanged; chain and attestation verdicts are identical before and after.

Before publishing new fixtures run `python3 dwarf/scripts/scrub_bundle_fixtures.py --check`, passing
the recording host's names and private subnets with `--host` / `--subnet` (or `DWARF_SCRUB_HOSTS` /
`DWARF_SCRUB_SUBNETS`) so they are never written into the repository; it exits 1 when one remains.
