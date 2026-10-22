# Security campaign automation

`.github/workflows/dwarf-security-campaign.yml` implements zero-configuration
GitHub-hosted discovery, stock Amaru builds, and build-artifact staging. A push that
changes `dwarf/testplease/**` starts the flow. Manual dispatch processes the
currently tracked Amaru candidates. Candidate compilation and every follow-on job
run on disposable `ubuntu-latest` runners.

The default is the bounded stock Amaru proof. Preparation retains exact candidate
source, executable and image identities. The frozen plan begins at
`runtime-compatibility-qualification-pending`; the existing runtime qualification
must pass before scenarios start. The existing `ci_security_campaign.py` runner
uses the local DWARF scenario and export paths, with owned-resource cleanup.

Cardano-solo, mixed, patched and compiler-coverage support are retained for future
validation, not enabled by the default scope. All execution jobs run on
`ubuntu-latest` with read-only repository permissions. No `contents: write`
permission or repository signing secret is required. The runner stages synthetic
Git identity metadata in `synthetic-git.json`; it does not execute candidate build
hooks during discovery or change the ordinary local DWARF pipeline.

## Submission and reviewed selection

Submit ordinary uncompiled upstream source under either directory:

```text
dwarf/testplease/amaru/<candidate-name>/
dwarf/testplease/cardano-node/<candidate-name>/
```

No client manifest, command, workflow or scenario path is required. A source push
compares the complete before/after commit range, with NUL-delimited paths and rename
detection disabled. Every new or changed candidate directory is selected once;
deleted files still select a surviving directory, removed directories are recorded,
and an initial branch push scans its complete tracked intake. An unavailable base
commit fails discovery rather than silently omitting candidates. Intake validates
all candidate layouts and rejects ambiguity, unsafe names and escaping symlinks.
The complete tree digest includes build support, executable bits and symlink text.
The repository commit, source digest and each variant plan digest are retained.

Manual dispatch defaults to `stock-amaru`. A source push also uses `stock-amaru`.
This builds only stock Amaru, performs its calibration, runs its existing control
and measured scenario, and verifies the resulting evidence. It does not wait for
Cardano-solo or mixed workloads. The support devnet still uses Cardano producer
and qualification nodes required by the existing Amaru runtime.

`stock-cardano` selects only the Cardano workload and its evidence aggregation.
It skips the Amaru workload and mixed jobs, but retains the same shared Amaru
build, calibration and pair qualification used in the locally verified workflow.
This is a Cardano-only workload scope, not yet a Cardano-only preparation path.

`stock-solo` (Amaru then Cardano) and `full` (the wider campaign) remain available
for later validation. Their job definitions are preserved; they are not part of
the default run or a claim of current end-to-end success. The weekly metadata
schedule has been removed from this execution workflow.

Trusted tools are checked out separately from the default branch and their exact
revision is retained. Submitted source is checked out separately at the event SHA
and read only as data on `ubuntu-latest`, with read-only contents permission and
checkout credential persistence disabled. Candidate build hooks are never invoked
there; candidate modules are never placed on `PYTHONPATH`. Trusted references are:

- `dwarf/security-suites/github-security-regression-v1.yaml`: six reviewed security,
  conformance, network/consensus and recovery groups, with explicit controls/oracles.
- `dwarf/qualification-suites/node-measurements-v1.yaml`: exercise contracts and
  explicit missing, reserved or blocked tap prerequisites.
- `dwarf/versions/catalog.json`: committed reviewed identities, frozen separately
  from official metadata previews.

Each reference is parsed, validated and retained with its source-file SHA-256.
Suite presence and group counts do not establish exercised coverage or a finding.

Mixed security entries freeze one heterogeneous topology and the original security
scenario, including its node roles, declared edges, workloads, faults, controls
and native assertions. The entry's `execution` binds operator-selected versions
and source identities to that recipe; the unmodified definition and digest remain
retained separately. Differential submission recipes retain their decoder/admission
boundaries and do not acquire inclusion, consensus or crash claims.

Each measurement attachment identifies a node, compatible profile, `target_mode`
(stock when omitted) and separate result namespace within that same mixed run.
One node has one build mode per run. Candidate attachments retain the matching
pending build variant, exact source tree and declared artifact expectations;
these are never observed build results. Release attachments retain exact catalog
identities even when taps await prerequisites. A non-stock release mode requires
separately verified instrumentation provenance and is currently rejected here.
Known genesis, era or protocol conflicts reject planning. Missing compatibility
stays `pending-prerequisites`; matching declarations are not runtime readiness.

Measurement exercise cells and paired calibration runs remain supporting
evidence. They cannot replace the entry's mixed security scenario or its controls
and cross-implementation assertions. The stock fallback plan keeps compatible
stock cells; the patched primary plan keeps compatible passive and internal cells.
Taps that share an implementation, node and exercise scenario are enabled in one
run. Unsupported patched or coverage taps remain explicit and unavailable until
their separately verified build lanes exist. Known working versions are evidence
history, not an allowlist.

## Execution boundary and build preflight

Discovery, compilation, preparation, calibration, workload execution and
aggregation use separate disposable `ubuntu-latest` jobs. No pull-request event runs candidate code. Both repository
checkouts disable credential persistence, submodules and LFS. Only reviewed DWARF
tools enter `PYTHONPATH`, and candidate metadata cannot supply a command, builder,
scenario or output path.

The build job reads the builder image from the committed
`infrastructure/docker/amaru-stock-builder.identity.json`; workflow input cannot
replace it. The candidate is copied through the bounded source-freeze code and
compiled with `cargo fetch --locked` and `cargo zigbuild --locked`. The network
fetch is recorded and does not claim hermetic or offline closure.

Before compilation, trusted host code resolves
`cardano-foundation/cardano-configurations` at the submission commit timestamp and
retains the exact commit, fixed download URLs and SHA-256 hashes for mainnet,
preprod and preview peer snapshots. The build container receives those files
read-only. It receives no GitHub token. A deterministic synthetic Git repository is
created inside the disposable copy because Amaru's build scripts require `HEAD`;
`synthetic-git.json` binds that synthetic identity to the submission commit and
time so it cannot be confused with an upstream Amaru commit.

The build retains `Cargo.lock`, Cargo metadata, logs, the synthetic Git receipt,
peer-snapshot receipt, executable and a Docker-loadable runtime archive. The hosted
campaign stage rechecks the discovery digest, source identity, committed builder
identity and every retained file hash. The next trusted step registers the loaded
image in a disposable registry whose linux/amd64 service image is pinned by digest.
It derives the candidate version from retained Cargo metadata, selects the committed
`cardano-only` default as the supporting release, and writes a frozen catalog,
selection, manifest, build observation and registration receipt. These records
remain pending because neither matching static declarations nor a successful build
proves runtime compatibility. The executor deduplicates equivalent version-pair
qualification requests, requires the exact candidate config image ID during that
qualification, and authorizes only the frozen topology IDs covered by a passing
observation. Stock and patched calibration run as two isolated jobs, after which
one selector checkpoints the decision. A valid patched calibration runs the
patched work-item matrix;
missing, invalid or excess-overhead calibration selects stock once without
rebuilding. The compiler-coverage mode remains a separate future lane and cannot borrow
another artifact identity.

When patched is selected, the same six security entries and controls run once
more against the already built stock image. This replay skips measurement cells.
DWARF requires equality of the complete retained same-boundary input-digest set
before it labels an entry stock-confirmed or stock-disagrees. A matching seed
alone is not exact-input evidence. Missing or unequal input sets leave replay
incomplete and block publication.

The initial Amaru lane selects six Docker-backed cases: transaction-array arity,
empty redeemers, inline-datum tag-24 trailing data, the compact multiasset
boundary, deep UPLC Delay stack safety and the governance zero-denominator
admission comparison. The governance case is a validation/conformance check. A
difference is reported as a bug unless separate impact evidence supports a
security classification.

The runtime adapter consumes observed builds; it does not build them. The serial
local interface remains available for diagnosis and explicit resume:

```bash
PYTHONPATH=dwarf python3 dwarf/scripts/ci_security_campaign.py \
  --manifest campaign/manifest.json \
  --stock-manifest campaign/stock-manifest.json \
  --build-observations campaign/build-observations.json \
  --directory campaign/runtime \
  --catalog campaign/version-catalog.json \
  --signing-key /operator-controlled/private-signing-key
```

The hosted workflow instead supplies `--work-item` and
`--work-item-inventory`. The item is derived from the frozen manifest and can
select only one calibration leg, security entry, grouped measurement recipe or
stock replay. A security item keeps its normal control and tested scenario in the
same runner. The candidate build and trusted controller image are each produced
once, retained by immutable identity, and restored by the individual runners.

Workload scheduling preserves the existing local DWARF execution path. Every
shard ultimately invokes `cardano-profile scenario run`, followed by the existing
bundle sign, export, import and verification commands. Actions does not redefine
scenario phases, measurement taps or evidence formatting. It only transports the
one-time build artifacts and assigns frozen local runs to disposable runners.

The bounded `stock-solo` scope builds only the submitted Amaru stock artifact,
runs `amaru-measurement-e2e-stock-portable`, and then runs
`cardano-measurement-e2e-stock-portable` against the selected stock Cardano
release. Cardano starts only after the Amaru shard succeeds. The proof authority
is deliberately limited to the non-vacuous `amaru-stock-resources` and
`cardano-stock-resources` results plus their signed portable bundles. Every other
collector output remains in the bundles, while excluded measurements and known
full-campaign blockers remain explicit deferred metadata. A successful aggregate
is labelled `stock-solo` and `scoped-complete`, reports security as `not-run`, and
cannot enter the full release or Pages publication job.

Executable work is partitioned by the scenario's actual topology and runs in
this dependency order: **Amaru solo, Cardano solo, then mixed**. A measurement
recipe whose existing scenario requires both implementations stays in the mixed
phase even when the tap targets one node. Within each phase, independent scenarios
fan out to separate runners with a bounded parallelism of six. A later phase waits
for the preceding phase to finish; retained failures remain visible but do not hide
independent evidence from the later phases.

The hosted job creates its signing key after candidate compilation, outside the
candidate container. It retains only the public key and labels the signer
`integrity-only-untrusted-for-publication`; the private key expires with the runner.
These signatures allow same-run archive integrity checks. They do not grant trusted
publisher status and cannot authorize release or Pages publication.

## Retention, resume and cleanup

Discovery, build evidence, prepared inputs, calibration shards, workload shards
and the aggregate are retained for **30 days** as Actions artifacts. They are
evidence for this workflow run, not published reports.
Preserve sealed runs, checkpoint files, frozen inputs, raw exercise evidence,
archive/signature receipts and cleanup errors before disposable workers are removed.
Download evidence before artifact expiry; automatic release publication is currently deferred.
Actions expiration alone must never trigger destructive cleanup or an automatic rerun.

An explicit retry uses the same candidate snapshot, suite digest, catalog binding
and observed artifact identities. The local campaign command accepts `--resume`;
it skips completed cells only when retained archive digests and verification remain
valid, then retries failed/missing cells. Restore the exact checkpoint directory and
its referenced archives first. A new workflow run does not automatically reconstruct
that state, and no resume input is currently exposed. Do not silently switch to a
new release, source tree, patch set or suite during a retry.

Scenario execution starts only after the pending compatibility state is replaced by
a successful exact-pair runtime qualification. No signing key is exposed to the
candidate build. The hosted runtime retains its lock through scenario execution and
cleanup and keeps signing outside the candidate container.

The build removes only its uniquely named build container. Scenario cleanup uses
the exact Docker projects registered by that campaign in a `finally` path.
Never infer ownership from a broad prefix or perform global Docker pruning.

## Publication boundary

The current workflow retains downloadable Actions artifacts for 30 days. It does
not publish releases or change Pages defaults. It requires no repository signing
secret or user-supplied signing key. Native evidence receipts continue to use an
automatically generated temporary per-job key; this needs no user configuration.

The former release-publication job and its repository-secret requirement have
been removed. Pages retains its independent push/manual workflow. Publishing new
campaigns to Releases or Pages is deferred; it must not block the working Amaru run.

## Focused offline verification

```bash
PYTHONPATH=dwarf python3 -m pytest -q tests/test_campaign_work_items.py tests/test_ci_campaign_fanout.py tests/test_security_campaign_workflow.py tests/test_ci_security_campaign.py
git diff --check
```

The focused hosted path also includes `tests/test_ci_amaru_stock_build.py`,
`tests/test_ci_hosted_campaign_stage.py` and
`tests/test_ci_hosted_campaign_plan.py`. Tests parse the workflow and exercise the
trusted preparation, transfer, registration and frozen-plan logic with synthetic
inputs. They do not run Actions, build real nodes, execute scenarios, deploy or
publish.
