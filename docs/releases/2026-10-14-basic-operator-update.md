# DWARF Basic operator update — draft release notes

Status: prepared source candidate; not a published tag, signed installer release,
or published container image. Release version is intentionally not assigned yet.

## Operator changes

- Basic is the default. The six-stage Run page contains topology, immutable
  versions, scenario discovery, individual measurement taps, Prepare, and Run.
- Local installation builds both dashboard and controller, pulls missing pinned
  targets before startup, and checks component revision compatibility. Legacy
  SSH control is optional for this local workflow.
- Scenario discovery includes security goals, implementation/surface grouping,
  and threat-model/risk-register filters, with clearer measurement selection.
- Version discovery is explicitly requested by the operator. Local Linux ELF
  binaries/archives and compatible Docker images can be imported experimentally.
- Compatibility decisions remain scope- and evidence-specific. A passing run
  does not globally qualify a version, measurement, scenario, or mixed pair.
- Exact healthy local topologies can be reused across unrelated operator-ledger
  updates without losing immutable identity, fresh readiness, or consent checks.
- Results distinguish execution state, assertion verdict, available metrics,
  unavailable evidence, immutable identities, and exportable artifacts.

## Verification retained for the local-import workflow

A real browser selected an official Amaru `v10.11.20260925` Linux binary as an
experimental import, selected a compatible Amaru topology and six stock/external
taps, acknowledged the exact experimental context, prepared the existing topology,
ran the workload, and opened the canonical evidence page.

Run `20261014T085451Z-1ab6fd40` completed in approximately 133.5 seconds with PASS:

- 1,200 Handshake attempts: 400 accepted, 400 unsupported-version refusals,
  and 400 malformed-CBOR rejections; zero unexpected outcomes.
- Canonical seed `0xA11CE502`, also retained by the helper as decimal `2703025410`.
- Six selected collectors retained results; the report contains 27 available
  metric fields and 15 unavailable fields. This is not an all-metrics claim.
- The existing topology was preserved; the imported binary was not automatically
  marked supported or made the catalog default.

The run identity is a traceable acceptance receipt, not a promise that private
operator bundles are included in this source release. Historical public reports
and documented evidence identities are retained separately, without deletion.

## Limits

- This new acceptance covers the imported stock binary and selected stock/external
  measurements. It does not prove every scenario, every version, patched-tap
  execution on that binary, or cross-implementation performance equivalence.
- Patched measurements require actual revision-matched instrumentation and
  retained provenance. DWARF cannot add source instrumentation to a precompiled
  stock binary.
- Existing historical client-card evidence remains historical, not a fresh
  qualification of every card against this release revision.
- Source-clone installation is documented in [INSTALL.md](../../INSTALL.md).
  Do not advertise an unsigned candidate installer as a trusted public release.

## Publication checklist

### Preparation validation (2026-10-14)

- All 1,782 existing public documentation/report paths are preserved. All 1,573
  files under `reports/` retain their original blobs.
- 433 tracked operational files (including six old run bundles) are removed from
  the new source tree, not from public history. Public reports are unaffected.
- The changed-file scan found no credential patterns, private operational HTTP
  endpoints, or files exceeding GitHub's 100 MiB individual-file limit.
- Delivery, signed-installer, and release-candidate assembly contract tests pass.
- Focused public-safety, README, and imported-Amaru measurement checks: 85 passed.
- Framework suite: 2,870 passed, 33 failed, 2 skipped. All 33 failures also
  reproduce on the unchanged source snapshot. These remain publication blockers;
  no test was disabled to claim a green release.
- The bare repository-root pytest command has an Antithesis harness module-name
  collision. The framework suite is explicitly `PYTHONPATH=dwarf:. pytest -q tests`;
  harness-local suites require their documented execution context.

This candidate is ready for source review, **not ready for publication**.

Before publishing source:

- Preserve all public documentation/report paths and existing finding content.
- Export no private Git ancestry, credentials, keys, run state, or private bundles.
- Review the source diff, README links, runtime regression results, and installer
  contracts. Keep the public branch descended from the existing public main.
- Select the public commit and immutable release version explicitly.

Before triggering `.github/workflows/dwarf-release.yml` with a version tag:

- Configure the approved release signing secret and matching public key; the
  checked-in placeholder is not a trust root.
- Configure immutable golden Amaru/Cardano image references and verify their
  Linux amd64/arm64 indexes.
- Let the release workflow build matching dashboard/controller indexes, retain
  provenance and SBOMs, verify platform/config labels, sign the manifest, and
  publish checksummed assets.
- Verify a fresh installation of those exact published assets. Existing local
  candidate tests do not substitute for that final release-artifact check.

No public push, tag, workflow dispatch, registry publication, or release creation
is part of this preparation step.
