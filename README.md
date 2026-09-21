# DWARF

<img src="dwarf/dashboard/static/dwarf-logo.png" alt="DWARF logo" width="20%">

DWARF is a security and adversarial-testing framework for Cardano node
implementations. It runs controlled workloads against real `cardano-node` and Amaru processes,
keeps the exact target identity, and produces reviewable run evidence.

## What DWARF is

DWARF starts or attaches to real Cardano-node and Amaru topologies. It sends
valid, invalid, malformed, high-load, recovery, and fault-oriented workloads to
the nodes. It then evaluates explicit assertions and records the result.

DWARF does not simulate either node, replace a node with a model, or turn a
configured collector into proof. A catalog entry states what DWARF can run or
collect. Only a completed real-node run with retained, non-vacuous evidence
supports a runtime claim.

The repository includes the framework code, dashboard, CLI, definitions,
schemas, documented evidence identities, and target build scripts. It does not
include private retained runtime bundles, operator logs, credentials, or local
machine state.

## Quick start with Docker

Requirements: a Linux Docker host, Git, Docker Engine, Docker Compose v2, and
enough local storage for the framework image and run data. Start from a fresh
clone:

```bash
git clone https://github.com/pragma-org/dwarf.git
cd dwarf
bash delivery/scripts/install.sh
bash delivery/scripts/status.sh
```

The installer validates the package, seeds the writable catalogs, builds
`dwarf/framework:current`, starts the hardened dashboard container, and waits
for `/api/status`. Open [http://127.0.0.1:8787/](http://127.0.0.1:8787/) and then
open **Operate**.

The default bind address exposes port 8787 on the host. For a loopback-only
installation, set a token and bind address before the install:

```bash
export DWARF_DASHBOARD_BIND=127.0.0.1
export ADA2_DWARF_TOKEN='replace-with-a-local-secret'
bash delivery/scripts/install.sh
```

The dashboard is usable without the optional host control channel. Real-node
deploy, remove, and host coverage controls need the supported control-channel
setup described in [INSTALL.md](INSTALL.md). Do not enable it unless the host is
intended to run those operations.

### Local-source developer path

Use this path to inspect definitions, validate scenarios, or develop without
starting the dashboard container:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r infrastructure/docker/requirements-framework.txt
python3 dwarf/cardano-profile --help
python3 dwarf/cardano-profile list-profiles
python3 dwarf/cardano-profile scenario validate --semantic dwarf/scenarios/client-example-cbor-decoding-cardano-patched.yaml
```

This path runs the Python framework from the checkout. A real-node scenario
still needs its declared container images, toolchains, topology, bootstrap
data, ports, storage, and hardware.

## Run a real-node test

The dashboard is the simplest supported path for a first run:

1. Open `/run`.
2. Select a scenario.
3. Check the resolved target and runtime.
4. Keep the scenario deployment profile or select a compatible override.
5. Confirm the exact node versions and version policy.
6. Select a compatible measurement profile, or use the scenario default.
7. Review the resolved primitives, seed, iterations, and local backend.
8. Wait for all readiness gates to report their current state.
9. Review the complete plan.
10. Start the run only when the selected profile is healthy and its documented
    prerequisites are present.

The wizard shows a contiguous 01–10 sequence. It does not hide a missing image,
an unsupported backend, an unknown version, or an unhealthy topology. After a
run, DWARF opens `/operate/runs/<run-id>` for the report and evidence downloads.

For two literal high-coverage recipes, including the exact labels and readiness
limits, see the [full-metrics compatibility audit](docs/measurement-full-metrics-compatibility-audit.md).

## How DWARF is organized

- **Scenario** — the versioned test recipe: workload, assertions, target,
  runtime, seed, and evidence requirements.
- **Target** — the implementation and executable surface under test, including
  its revision-qualified manifest or image identity.
- **Deployment profile** — the node implementation, topology, network,
  versions, images, genesis, and lifecycle settings.
- **Primitive** — one setup, load, fault, probe, assertion, or teardown action.
- **Measurement** — one typed signal and its collector/evidence contract.
- **Measurement profile** — a compatible selection of measurement taps for a
  target mode. Profiles select taps; run reports contain results.

Browse definitions at `/operate/measurements` and
`/operate/measurement-profiles`. Learn pages explain their semantics:
`/learn/measurements`, `/learn/coverage`, and `/learn/threat-coverage`.

### Measurement source badges

- **Stock** uses signals emitted by an unmodified node.
- **External** observes a process, container, network, or controller boundary.
- **Patched** uses revision-locked node instrumentation and requires exact
  provenance plus the documented stock control.
- **Reserved** holds a future catalog position. It is not available or
  exercised evidence.

Measurements can report counters, gauges, histograms, timing distributions,
resource windows, correlations, and lifecycle readiness. Raw integer
nanoseconds are retained when a patched target provides them; reports also
derive fractional microseconds. Legacy whole-microsecond evidence stays
readable.

**Catalogued is not exercised.** A zero, an `available` flag, or a finalized
collector does not prove that a workload exercised a metric. The report needs
the required correlated samples or events inside the stated workload window.

## Measurement program

The frozen five-card program proves the same small client requirements
separately for Amaru and Cardano-node:

1. **CBOR decoding** — outcome, round-trip consistency, and decode timing. The
   retained old Amaru failure remains a security finding; the exact upstream
   fixed revision passes the same corpus.
2. **Plutus VM** — real valid and expected-invalid on-chain Plutus V2
   transactions, evaluator outcomes, exposed budgets, and continued progress.
3. **Invalid mini-protocol** — malformed Handshake containment, liveness,
   timing, and resource cost. Its accepted whole-microsecond revision remains
   unchanged.
4. **Block application** — bounded canonical progress, raw chain-selection and
   fork evidence, correlations, timing, convergence, and fatal-health checks.
5. **Restart and sync** — a real restart, ordered readiness gates, controlled
   sync progress, speed, and resource windows.

All five frozen cards have accepted evidence for both implementations. This is
not a mixed-node benchmark, a production-performance claim, or proof that one
scenario exercises every implemented metric. Read the
[five-card index](dwarf/docs/client-examples/README.md) for run identities and
measurement revisions, and the [compatibility audit](docs/measurement-full-metrics-compatibility-audit.md)
for the exact missing metric cells.

## Results and evidence

Each run directory contains the resolved manifest, assertion results, an NDJSON
event log, probe outputs, measurement selection and reports, collector data,
and a manifest hash-chain record when applicable. The run inspector separates:

- framework completion from the security verdict;
- passed assertions from completed runs that contain a finding;
- available definitions from exercised metrics;
- raw evidence from normalized values and human-readable summaries; and
- missing evidence from a numeric zero.

Use the inspector to view artifacts or export a bundle. Verify an existing run
from the CLI with:

```bash
python3 dwarf/cardano-profile verify RUN_ID
python3 dwarf/cardano-profile bundle verify RUN_ID
```

Replace `RUN_ID` with a retained run identifier. The public source tree keeps
documented evidence identifiers and contracts, but not private run directories
or exported bundle archives. See the [forensic bundle format](dwarf/docs/forensic-bundle-format.md).

## Versions and prerequisites

Version selection is part of the test contract. Prefer `exact` or a confirmed
catalog selection. A profile and run record must retain the resolved semantic
version, source revision, executable or image digest, genesis/configuration
identity, and relevant patch-set digest. An `unknown` or unconfirmed selection
must not silently become the default.

The framework image does not contain every node target. Depending on the
scenario, prepare the documented Cardano-node or Amaru image, source checkout,
GHC/Cabal or Rust toolchain, ledger/bootstrap data, protocol parameters, cost
model, and host resources first. Coverage and patched targets have additional
revision-locked build steps. See [version-qualified devnets](dwarf/docs/version-qualified-devnets.md)
and the target [build notes](dwarf/targets/README.md).

## Repository map

```text
delivery/                         Docker install, build, deploy, and status scripts
infrastructure/docker/            Framework image definition and Python requirements
dwarf/cardano-profile             CLI entry point
dwarf/profile_manager/            Runner, dashboard, collectors, and evidence logic
dwarf/scenarios/                  Versioned real-node scenario definitions
dwarf/profiles/                   Deployment and topology profiles
dwarf/primitives/                 Primitive schemas and registry
dwarf/measurements/               Measurement definitions
dwarf/measurement-profiles/       Compatible tap selections
dwarf/targets/                    Target manifests and revision-locked build sources
dwarf/spec/                       Run, evidence, measurement, and contract schemas
dwarf/docs/                       Operator, security, evidence, and acceptance documents
tests/                            Unit, contract, semantic, and presentation tests
tools/                            Validation and public-tree audit tools
```

Runtime data is outside the tracked source tree by default, under
`${XDG_DATA_HOME:-$HOME/.local/share}/dwarf/`. It contains `runs/`, `bundles/`,
and writable `state/` data.

## Validate and develop

Run focused validation before a test or documentation change:

```bash
bash delivery/tests/test_delivery_contract.sh
python3 dwarf/cardano-profile scenario validate --semantic dwarf/scenarios/client-example-cbor-decoding-cardano-patched.yaml
pytest -q tests/test_public_tree_safety.py tests/test_public_readme_contract.py
```

Run the complete Python suite before release:

```bash
pytest -q
```

Scenario validation proves that a definition matches the schemas and semantic
registry. It does not prove that a node image is present or that the scenario
has run. Use `prereq-check`, profile readiness, and the run evidence for those
claims. The [CI validation guide](dwarf/docs/ci-validation-gate.md) describes
the staged gates.

## Security, reporting, and claim limits

Do not put tokens, wallets, private keys, credentials, operator logs, runtime
state, or retained client bundles in the repository. Keep secrets in the
supported local configuration and review bundle contents before sharing them.
Report vulnerabilities through the process in [SECURITY.md](SECURITY.md).

A completed run can contain a failed security assertion. DWARF preserves that
finding; it does not relabel it as a pass. Measurement collector failures remain
separate unless an explicit contract makes them gating. Supported definitions,
configured collectors, and independently proven Cardano-node and Amaru runs do
not establish full metric exercise, cross-implementation equivalence, stable
benchmark thresholds, or production safety.

## Project and documentation

DWARF is maintained by **PRAGMA**. Contributions are welcome through GitHub
issues and pull requests; follow the test and evidence rules above and the
ownership rules in [CODEOWNERS](CODEOWNERS). The repository does not currently
declare one repository-wide license. A component license applies only to the
component that contains it; for example, see the
[`dwarf-adversary` license](antithesis/components/dwarf-adversary/LICENSE).

Authoritative guides:

- [Install guide](INSTALL.md)
- [Operations guide](OPERATIONS.md)
- [Security policy](SECURITY.md)
- [Version-qualified real-node devnets](dwarf/docs/version-qualified-devnets.md)
- [Primitive reference](dwarf/docs/primitives-reference.md)
- [Forensic bundle format](dwarf/docs/forensic-bundle-format.md)
- [Five-card acceptance program](dwarf/docs/client-examples/README.md)
- [Full-metrics compatibility and `/run` recipes](docs/measurement-full-metrics-compatibility-audit.md)
