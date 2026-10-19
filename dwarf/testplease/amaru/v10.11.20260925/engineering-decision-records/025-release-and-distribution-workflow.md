---
type: process
status: accepted
---

# Release And Distribution Workflow

This document describes how Amaru release artifacts are built, drafted, published, and distributed.

## Context

Amaru now ships through several distribution channels:

- pre-compiled Linux archives for `x86_64` and `aarch64`
- Linux `.deb` and `.rpm` packages for `x86_64` and `aarch64`
- a pre-compiled macOS archive for `aarch64`
- Windows `.zip` and `.msi` artifacts for `x86_64`
- multi-arch Docker images
- generated metadata for Homebrew, Nix, and WinGet

Pre-compiled executables, packages and docker images are produced on two occasions:

- continuously on every merge/push on `main`, where they are referred to as "nightly builds";
- on release workflow dispatch, where they lead to a release; we intend to trigger a new release every _Thursday_;

Beside, external package manager metadata (Homebrew, Nix, ...) are derived from
published release assets rather than from local workspace state.

Note that crates.io publication is a separate concern and shall not be coupled to Amaru distribution releases

## Decision

### Two-step release lifecycle

Amaru shall use a dual-step release process:

1. `release.yml` builds and packages artifacts, and optionally creates a GitHub draft release.
2. `post-release.yml` runs only once a release is published, or when explicitly retried via `workflow_dispatch`.

The first step is responsible for producing immutable binaries and packages.
The second step is responsible for publishing dependent metadata that must point at already-published release URLs.

### Build and draft workflow

`release.yml` shall run on:

- every push to `main`, for nightly builds
- `workflow_dispatch`, with a boolean `release` input, for release preparation

The workflow shall compute build metadata before any target-specific build starts:

- nightly builds derive a version of the form `nightly-<git-sha>`
- release builds derive a version of the form `<major>.<minor>.<YYYYMMDD>`
- nightly builds require the `amaru` package version in `crates/amaru/Cargo.toml` to keep a `.0` patch version as a safety net

Release builds shall not rewrite `Cargo.toml`.
Instead, they shall override the patch version exposed by the `built` crate through `BUILT_OVERRIDE_amaru_PKG_VERSION_PATCH`.
This keeps `cargo --locked` viable while still making the compiled binary, generated manual page, and generated shell completions report the release version.

The build matrix shall produce:

- Linux `x86_64-unknown-linux-musl`
- Linux `aarch64-unknown-linux-musl`
- macOS `aarch64-apple-darwin`
- Windows `x86_64-pc-windows-msvc`

Linux package formats (`.deb` and `.rpm`) shall be built on top of the Linux
artifact legs so they reuse the same build output and cache instead of
triggering a separate rebuild. Similarly, the docker images are built from the
statically linked pre-compiled Linux artifacts.

The workflow shall also:

- stage distributable trees through `make dist`
- emit `.tar.gz`, `.zip`, `.deb`, `.rpm`, and `.msi` artifacts as appropriate
- emit a single aggregated checksum manifest named `amaru-<version>-checksums.manifest`
- generate [GitHub artifact attestations](https://docs.github.com/en/actions/concepts/security/artifact-attestations) from that manifest
- create a draft GitHub release for release runs only

If a same-version draft release already exists, the workflow shall delete the stale draft release and its tag before creating a fresh draft.
It shall refuse to overwrite an already-published (i.e. non-draft) release.

### Post-release workflow

`post-release.yml` shall run on:

- `release.published`
- `workflow_dispatch` with an explicit `release_tag`, so failures can be retried from the latest workflow definition

The post-release workflow shall:

- resolve the published release metadata through the GitHub API
- ensure the corresponding git tag exists
- download the published release assets
- generate and update:
  - `Formula/amaru.rb`
  - `flake.nix`
  - `manifests/...` for WinGet
  - the generated installation snippets in `README.md`
- open a pull request carrying those generated changes
- publish versioned Docker tags through the reusable Docker workflow

This means publishing a release is intentionally not the final step.
The repository must still ingest the generated package manager metadata and README changes by merging the post-release PR.

### Reusable Docker publishing

Docker publishing shall be isolated in `docker-publish.yml`, invoked in two contexts:

- nightly runs publish `latest`
- published releases publish version tags

This keeps Docker publication logic consistent while allowing the artifact source to differ:

- nightly Docker images consume workflow artifacts
- release Docker images consume published release assets

### `clap`-derived packaging assets

Amaru shall derive shell completion and manual page assets from the same `clap` command tree used by the executable itself.

To do this:

- the `amaru` binary exposes a hidden `shell-completions` command
- that command uses `clap_mangen` to render the man page
- it uses `clap_complete` to generate shell completion files

The generated assets differ by platform:

- Unix archives include Bash, Zsh, and Fish completions
- Windows artifacts include PowerShell completion

This command is used by `make cli-assets` and `make dist`, which means packaging no longer requires compiling a second helper binary in CI.

### Windows MSI versioning

Amaru release versions use a patch component of the form `YYYYMMDD`.
Windows Installer cannot use that full value in the third `ProductVersion` field, because the build component must stay within the MSI numeric limit.
Windows Installer also compares only the first three fields of `ProductVersion`, even if a fourth field is present.

Therefore, the MSI-specific version shall use:

- public Amaru release patch: `YYYYMMDD`
- MSI third field: the number of previous published Amaru releases, using a 0-based count
- MSI fourth field: the full public release date `YYYYMMDD`

This is implemented by querying the number of already-published releases before creating a new draft release.
For example:

- public release version: `10.10.20260610`
- if there have been 3 previous published releases, MSI internal version: `10.10.3.20260610`

This MSI-specific version is used only for the installer internals.
Release filenames, release tags, WinGet package version metadata, and all non-MSI channels continue to use the full public release version.
The release count is what makes MSI upgrades monotonic; the fourth date field is informational and preserved for operator readability.

## Consequences

- Amaru now has a clear separation between artifact production and publication side effects.
- The draft release is the review checkpoint for release artifacts.
- External package manager metadata is always regenerated from published release assets, not from assumptions about local build outputs.
- The README installation snippets are treated as generated release metadata and updated in the post-release PR.
- Nightly and release Docker images are published by the same reusable workflow, but from different artifact sources.
- The `built` crate remains the source of version metadata exposed by the executable, while CI can safely override only the release patch.
- MSI internal versioning now diverges from the public Amaru version; this is intentional and limited to Windows Installer constraints.
- MSI upgrades depend on the release-count field, not on the trailing date field, because Windows Installer ignores the fourth version field during upgrade comparisons.

The current process still has deliberate gaps:

- WinGet manifests are generated, but they are not yet submitted automatically to the public `winget-pkgs` registry
- the macOS artifact is not yet signed, notarized, or stapled
- because of the previous point, macOS distribution currently remains a raw archive workflow rather than a fully notarized end-user installation flow

Anyone operating or extending the release workflow should therefore treat the current system as:

- production-ready for artifact production, drafting, checksum publication, attestations, Docker publication, and metadata generation
- incomplete for public WinGet registration and Apple notarized distribution
