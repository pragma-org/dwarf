# DWARF Antithesis scenario specification

**Contract version:** 1.5 · **Reviewed:** 2026-10-21

**Audience:** scenario authors, operators, maintainers and coding agents.

**Canonical location:** `antithesis/spec.md`.

This is the reusable contract for creating, packaging, validating and running a
DWARF scenario through MOOG on Antithesis. Read it before modifying a harness or
declaring a package ready. Section 9 provides an optional scenario reproduction
record. Generating or supplying that file is not a wizard prerequisite; the user
deferred this convenience feature on 2026-10-20. Retain validation evidence for
the exact assets and link to this contract rather than forking its requirements.

This document is a specification, **not an automated validator or a certificate
that existing bundles conform**. Existing validators cover subsets. A passing
local run, accepted MOOG request or historical success cannot replace missing
evidence for the exact submitted artifact. Section 8 records current gaps.

## 1. Scope and authority

Requirements below distinguish three layers:

- **Platform:** Antithesis image, Compose, setup and Test Composer behavior.
- **MOOG delivery:** DWARF's existing repository → config image → launch path.
- **Harness:** the services, bootstrap, workload and observations needed for the
  chosen Cardano/Amaru experiment. A standalone decoder test does not need the
  mixed-network harness.

“Must” means a requirement of this DWARF contract for the stated scope.
“Recommended” preserves an upstream recommendation. A deliberate exception must
name the affected requirement, reason, replacement evidence and reviewer in the
scenario record; do not silently mark it passed. An unproven target version is
valid experimental input. Distinguish an unknown compatibility outcome from a
missing harness capability, inaccessible artifact or broken observation path.

Use executable source and inspected artifacts to establish implemented behavior.
Use current official docs to establish platform requirements. When they disagree,
record the mismatch and resolve it before submission. Historical reports establish
only what happened to their recorded revision and inputs.

Keep the dedicated Antithesis wizard/backend separate from Local `/run`.
Do not change local execution to satisfy this contract.

## 2. Lifecycle and evidence boundaries

```text
scenario question + selected targets + chosen harness
  → source/build package (may contain build contexts)
  → built workload images + final submission Compose
  → static contract review + stopped-image inspection
  → fresh local startup + actual workload commands + Snouty validation
  → public immutable inputs + exact MOOG request
  → MOOG builds/pushes configuration image
  → Antithesis setup → workload/fault exploration → terminal evidence
```

| Gate | Required evidence | Claim allowed |
|---|---|---|
| G0 Design | Experiment definition, service/peer/fault maps, assertion semantics | Experiment specified |
| G1 Artifact | Resolved Compose, complete mounts, image architecture/catalog checks, requirement matrix | Structurally reviewed |
| G2 Local | Fresh readiness, direct commands, non-vacuous observations, Snouty output | Locally exercised |
| G3 Delivery | Anonymous image access for this MOOG route, public commit/tree, locally built candidate config-image inspection, reviewed request | Ready to submit |
| G4 Remote startup | Actual MOOG-built config-image digest/content, exact images/services, setup event, discovered and invoked commands, endpoint evidence | Remote workload executing |
| G5 Terminal | Finished run, assertion encounters, actual fault scope and recovery evidence | Bounded campaign result |

Fail at the first unsatisfied gate. A real SUT failure after valid setup is a
test result; a broken harness or missing observations make the affected claim
inconclusive. Record each separately.

## 3. Compose and image contract

Check the **final submitted Compose and config image**, not just source YAML.
Before submission inspect a locally built config-image candidate using the
inspected MOOG recipe; after the agent builds the actual image, verify that image
and its launch identity. Candidate inspection does not prove the remote build.
For each row record `pass`, `fail`, `not checked`, or justified `not applicable`,
with the exact file/service/image and retained evidence.

| ID | Requirement and reason | Verification |
|---|---|---|
| C01 | **MOOG:** place `docker-compose.yaml` at the selected repository directory's root; place it at `/docker-compose.yaml` in the resulting config image. Include every referenced configuration and relative bind-mount asset. | Inspect submitted Git tree and stopped config-image filesystem; resolve Compose from that directory. No developer-machine absolute paths. |
| C02 | **Platform:** all service binaries and dependencies must run as Linux x86-64, without internet access. Pin the resolved image digest for reproducibility; record source revision separately. | Inspect image platform and actual executable; include bootstrap data, SDK, libraries and scripts during build. `platform: linux/amd64` alone does not prove the image contains a working binary. |
| C03 | **MOOG:** final service entries must reference already-built, available images. Build contexts may exist in a preparation/source package; materialize them before submission and remove `build:` from the final MOOG bundle. | Resolve every final `image:`; verify anonymous digest access for this public-image route. MOOG builds the **config image**, not the service images. Snouty's separate build/launch workflow does not change MOOG's behavior. |
| C04 | **Platform:** omit `pull_policy` and custom `logging.driver`; no startup registry pulls, package installs or external downloads. | Inspect resolved Compose and image entrypoints. Preserve default container logging and retain evidence written only to volumes. Check remote logging-volume/efficiency properties; reduce unnecessary output without removing the observations needed by the oracle. |
| C05 | **Platform:** submitted networks must not use `internal: true`; this prevents connection to the Antithesis network. The simulated environment is already without internet. | Inspect every final network and the config-image copy. Local isolation overrides are separate artifacts and must not leak into submission. Do not depend on MOOG to rewrite a literal Boolean. |
| C06 | **DWARF/MOOG:** set explicit unique `container_name` values equal to Compose service keys, including helpers. Maintain DNS-valid hostnames without underscores; matching host/container names is recommended. Existing `.example` hostnames require an explicit peer/name map. | Compare service key → container name → hostname → peer addresses → exclusion payload. Preserve aliases used by topology files when changing names. |
| C07 | **Platform recommendation adopted by DWARF:** set `init: true` for service entrypoints so PID 1 behavior does not obscure signals, exit status or core dumps. Document any helper exception. | Inspect every service, including one-shot extractors and seeders. |
| C08 | **Harness:** dependencies must express real readiness. Use completion for one-shot prerequisites and semantic health/readiness for targets. | `service_started`, open ports and running containers alone are insufficient. Capture readiness output, restart counts, timeouts and failure handling. |
| C09 | **Harness:** declare all shared volumes, access modes, owners and producer/consumer paths. Keep writable node stores private. | Map each config/socket/log/status mount to its writer and readers. Test with the image's actual user. Avoid shared writable Amaru stores and stale ready markers. |
| C10 | **MOOG:** fault exclusions are a string of comma-separated classes from `network,kill,pause,stop`; use explicit classes or omit the label. | Reject Boolean/string `true`, unknown tokens and name mismatches. Run the existing label validator, then inspect the request and remote events; label syntax alone cannot establish effective exclusions. Inspect the full launcher fault profile: these four classes do not describe every platform fault category. |
| C11 | **Platform/harness:** one designated readiness path emits `setup_complete` only after the experiment is ready. No driver may shortcut setup. | Record emitter service/function, prerequisite conditions and SDK event. Retain failure/timeout evidence. SDKs or the documented `$ANTITHESIS_OUTPUT_DIR/sdk.jsonl` interface must reach Antithesis; stdout text alone is not that interface. |
| C12 | **Platform/harness:** bake the exact Test Composer catalog into workload images before entrypoint execution. | Inspect `/opt/antithesis/test/v1/<template>/<command>` in a stopped container; verify executable modes, shebang/interpreter, dependencies and default-user permissions. No inherited unwanted commands. Runtime mounts/copy/chmod are not catalog proof. |
| C13 | **Harness:** include a real driver and explicit properties/coverage events. | Supported names include `parallel_driver_`, `serial_driver_`, `singleton_driver_`, `anytime_`, `eventually_`, `finally_`. Record exact command set and intended schedule. Require expected nonzero discovery, per-command exit outcomes and property encounters. An eventual assertion alone is not workload delivery. |
| C14 | **Delivery:** package configuration and public test material only. | Inspect archive/tree for credentials, private keys, `.env` secrets, host metadata (`._*`, `.DS_Store`) and stale bytecode. Synthetic testnet keys must be identified as disposable test fixtures; never include operator credentials. |
| C15 | **Harness:** preserve deterministic input/state provenance and observable results. | Record seed, immutable corpus, genesis, bootstrap provenance, version bindings, endpoints, oracle/parser revision and assertion encounters. Reproduce from fresh state. |

The official Docker best-practices page recommends C06–C08; this contract makes
their review explicit. Public image access is required by the current DWARF
public-image MOOG route, not a universal Antithesis restriction: other deployments
can use their configured private Antithesis registry.

For our recorded image-parameter integration, use `repository@sha256:<digest>`
rather than `repository:tag@sha256:<digest>`; the latter previously failed the
Antithesis image-parameter validator. Verify the current launcher behavior rather
than inferring it from Docker accepting the syntax.

Illustrative fragment only — it is not a runnable or complete scenario:

```yaml
services:
  observer:
    image: ghcr.io/OWNER/WORKLOAD@sha256:REPLACE_WITH_VERIFIED_DIGEST
    platform: linux/amd64
    container_name: observer
    hostname: observer
    init: true
    labels:
      com.antithesis.exclude_from_faults: "network,kill,pause,stop"
    # Add the chosen harness's mounts, readiness dependencies and command.
    # Test commands already exist inside this immutable image.
networks:
  default:
    driver: bridge
    internal: false
```

An exclusion protects the observer only when that matches the experiment design.
Never apply all exclusions to all SUT services by default.

### Runtime preload compatibility (C02)

Antithesis injects `/usr/lib/libvoidstar.so` using `/etc/ld.so.preload`
([official environment documentation](https://antithesis.com/docs/configuration/the_antithesis_environment/)).
A binary-only `ldd` closure does not include dependencies introduced by that
preload. The 21 October patched Cardano run failed during setup because its
bundled loader could not find `libpthread.so.0`, although ordinary local startup
and `--version` had passed.

For a custom loader/runtime bundle, retain the ordinary executable dependencies
and a coherent libc family from the same builder. Preserve the wrapper's actual
library-search paths; do not mix Nix/base-image libc components with the staged
builder libc. Fail preparation on unresolved `ldd` dependencies, including
`=> not found` with exit code zero. Record every staged library's hash and the
actual node executable hash. The dedicated Cardano source adapter now adds the
builder's libc6 DSOs and requires pthread/dl/rt/util/anl compatibility libraries.

Exercise the stopped image with a dependency-bearing preload and verify that
its constructor executes, then repeat fresh composed startup. Keep synthetic
probe mounts out of the published package. This is a regression for the observed
loader mechanism; it does not emulate the real Antithesis library or prove its
complete ABI. Actual remote setup remains a separate gate.

## 4. Cardano/Amaru harness contract

Reuse the relevant existing engine path and recipe. The profile-bundle builder,
native scenario generator and mixed workload assembler are different paths; do
not replace a mixed workload with the old generic Python workload image.
Start from the source map in section 10.

For the retained mixed control architecture, the following roles are connected.
Exact service names and implementation differ by recipe; document the chosen
variant and its delta instead of combining them silently.

| Role | Necessary connections and observations |
|---|---|
| Configurator | Generates producer configuration/genesis/keys into shared config volumes; exits successfully before nodes consume them. Verify every producer has the same genesis start times and network magic. |
| Cardano producers and relays | Mount their own state/configs and the tracer socket; topology peers resolve inside Compose. Establish real tip progress and producer agreement through CLI/tracer evidence. |
| Tracer + tracer-sidecar | Nodes connect to the tracer socket; tracer writes into the shared tracer volume; tracer-sidecar reads those logs and produces the expected chain-point/observation stream. Verify parser compatibility with selected versions. |
| Bootstrap producer or relay-bootstrap wrapper | Waits for the required committed chain history, produces non-empty bootstrap state from the intended chain and reports completion. The upstream producer variant uses a snapshot copy rather than writing to the live producer ChainDB. Other wrappers need their own inspected equivalent. |
| Amaru relays | Wait for bootstrap, copy into private stores, use the intended selected binary, publish startup markers and stay running. Record bootstrap tooling separately from the target version. |
| Amaru-fed consumer | Seeds from matching state and reaches producers only through Amaru. Prove advancement beyond its seeded tip and convergence; direct producer peers would invalidate this observation. |
| Readiness/assertion sidecar | Reads startup/status evidence and the underlying chain observations. Delays setup until the relevant nodes **and workload prerequisites** are ready. Preserve the selected recipe's readiness and convergence checks. |
| Log-tailer | Makes the expected tracer output available in container logs; preserve any additional raw volume logs needed for postmortem evidence. |
| Scenario-specific victims, fuzzers and observer | Use actual selected victim binaries, correct listening ports, semantic readiness, corpus/seed and observation mounts. For paired delivery, prove an equivalent ordered stimulus/observation prefix before comparing outcomes. |

For the current mixed mini-protocol recipe, the sidecar waits for both relay
startup markers, the Cardano victim socket, Amaru victim bootstrap completion and
`/sm-status/prefault-ready`, then invokes `/bin/sidecar`. The observer has mounts
for paired fuzzer logs, both victim runtime logs, control/victim sockets and status.
Those mounts are functional dependencies, not optional logging decoration.

Do not generalize historical ports or bootstrap epochs to every release. The
retained wrapper connects to Cardano on 3001 and serves Amaru clients on 3000;
inspect the selected binary's actual listener. The upstream producer requires
deep epoch history; an arbitrary short setup timeout is invalid.

When selecting versions or measurements:

- Record requested version → resolved image → actual service/binary used.
  Keep bootstrap/tooling/control versions distinct from the SUT target.
- Unknown versions and compatibility failures can be the experiment. Missing
  executable adapters or bootstrap inputs must be explicit preparation gaps.
- Record whether each measurement is applied, merely recorded, unsupported or
  unverified. A selected tap is not evidence that its collector runs.
- Enabling structured traces must preserve the workload observer's input
  contract. The measured mixed harness exposed a text-only Amaru tip parser
  when JSON traces were enabled. Verify both the collector and the workload
  observer against actual selected-node output. The generated Antithesis
  observer now reuses DWARF's existing JSON/text tip parser; this does not change
  the shared Local recipe.
- A setup marker must not be written when required baseline tips are missing.
  For the connected mixed workload, require actual producer, consumer and both
  victim tips, with nonnegative integer slot/block and a 64-hex hash. Reject
  malformed successful CLI output; a status string alone is insufficient. Clear
  stale workload readiness at startup and exercise the exact package from fresh
  volumes. Snouty command discovery cannot replace direct command execution.
- Sequential latest-tip reads are not an atomic chain snapshot. A consumer can
  advance after producers are sampled; unmatched hashes alone do not prove a
  fork or containment failure. Retain exact tips and mark convergence unknown
  unless matching hashes are observed. Keep fatal assertions independent of
  this uncertainty, require positive evidence for recovery, and retain separate
  ancestry/fork-depth checks. Test skewed and same-height mismatched samples.
- SDK assertions, node coverage instrumentation and DWARF measurement patches
  are separate mechanisms. Verify each requested capability in the actual image.
- Verify instrumentation settings through the deployed MOOG request path. At
  inspected MOOG revision `2642c60`, the pending encoder omits
  `has_instrumentation` and its decoder defaults true; a local CLI flag alone
  does not prove remote behavior.

## 5. Workload, faults and recovery

Every scenario must state the operation sent to the SUT, the relevant endpoints,
the result classifications and why each assertion is meaningful. Retain driver
entry, endpoint reachability and at least one known-good/control encounter.
Distinguish timeout, transport failure, preparation failure, decode/semantic
rejection and acceptance. A zero-sample passing invariant proves nothing.

For differential claims, use equivalent immutable inputs and compatible state
provenance. A common fuzzer-log prefix proves matching logged descriptions, not
automatically identical wire bytes. State the measured boundary.

Create a fault map per service. Separate control, observer, victim, stimulus and
one-shot helper roles; explain each exclusion. A completed helper without a
label is not evidence that it received faults. Current MOOG derives exclusion
names from Compose service keys; verify those names against remote containers.

Antithesis stops infrastructure fault injection and terminates other running
Test Composer commands when scheduling `eventually_`. A custom
`ANTITHESIS_STOP_FAULTS` hook is not required for that platform behavior.
Persistent Compose fuzzer entrypoints are not Composer commands: explicitly
state whether protocol stimulus continues during recovery and how this affects
the property. If recovery requires stimulus to cease, implement and verify that
separate coordination rather than assuming the platform stops it.

Recovery requires actual post-fault progress/convergence and suitable retries.
A socket existing, a function returning true or a faults-off local run cannot
prove recovery under remote fault exploration.

## 6. Reproducible verification procedure

Use a fresh, dedicated working directory and Compose project. Explicit
`container_name`, volume `name` and network `name` override project isolation:
check for collisions before startup. Preserve any local override and its diff
against the intended submitted artifact. Never use broad Docker cleanup.

### Pass A — inspect implementation and artifacts

1. Fill the scenario record and C01–C15 matrix. Read existing working recipe,
   historical failures and current official docs. Inspect generated output.
2. Resolve the final Compose using the intended submission environment:

   ```sh
   docker compose -f /absolute/path/to/final/docker-compose.yaml config --quiet
   docker compose -f /absolute/path/to/final/docker-compose.yaml config --format json
   ```

   Record the effective model, not environment-dependent source alone. Inspect
   networks, names, dependencies, mounts, images and labels.
3. Run the existing label check from the repository's Python environment:

   ```sh
   PYTHONPATH=dwarf python3 - /absolute/path/to/final/docker-compose.yaml <<'PY'
   import sys
   from pathlib import Path
   from profile_manager.antithesis_validation import moog_fault_exclusion_errors
   errors = moog_fault_exclusion_errors(Path(sys.argv[1]))
   print("\n".join(errors) if errors else "Fault-label syntax passed")
   raise SystemExit(bool(errors))
   PY
   ```

   This only checks label syntax; it does not validate the entire contract or
   prove runtime exclusions. Run relevant existing package/binding tests when
   those components change; select tests from section 10.
4. Inspect the exact workload digest with `docker image inspect` and
   `docker create` **without starting it**. Use `docker cp`/archive inspection
   to record the baked command tree, file modes, interpreters and dependencies.
   Check the image's default user and parent-directory permissions. Inspect
   all services that contain Composer templates, including inherited sidecars.
5. Build and inspect a local candidate config image using the inspected MOOG
   packaging recipe. Compare its Compose/assets with the reviewed final bundle
   and record hashes. A successful build is insufficient. Repeat this identity
   and content check on the actual agent-produced config image after submission;
   its digest does not exist at the pre-submission stage.

### Pass B — exercise the exact experiment

1. Start from empty scenario-owned state. Record the Compose project and
   effective configuration used by Snouty so subsequent `docker compose exec`
   commands target the same containers.
2. Run `snouty --version` and `snouty validate --help` on the compatible Linux
   validation host, then
   `snouty validate /absolute/path/to/final --timeout 1800 --keep-running`
   or the scenario's documented timeout. Verify this against the installed
   version; 1800 is an example budget, not a readiness guarantee.
3. Require setup detection and the exact expected command catalog. Snouty 0.6.1
   scans running containers after setup and does **not execute the commands**;
   this does not replace stopped-image inspection or step 4. Its host may need
   the `docker-compose` executable as well as Docker Compose v2. Verify real
   bootstrap, semantic readiness, stable processes and selected binaries.
4. Run the selected workload commands explicitly against those ready containers.
   Capture exit codes, SDK events, observations, assertion encounters and
   classification. Preserve faults-off scope; repeat from fresh state if
   timing/state reuse is relevant.
5. Retain evidence, then remove only the dedicated project and its volumes.
   Snouty cleanup may leave named volumes. Verify those are absent before any
   repeat; stale ready markers/transcripts can falsely satisfy setup.

Review both passes again against the matrix. Have another reviewer examine the
source-to-artifact mapping and remaining gaps where available. Do not replace
evidence with a reviewer endorsement.

## 7. Publication, MOOG submission and remote checks

The existing publication process is manual: push the reviewed workload package,
the owner makes it public, then verify unauthenticated access to the exact digest.
There is no assumed GitHub publishing workflow. Use a temporary empty Docker
credential configuration for an anonymous check and verify registry access,
not just the presence of a cached authenticated image.

Record the public repository, exact commit, bundle directory and public Git tree
file modes. Never submit a moving branch as the artifact identity. A browser or
Windows upload may change scripts to mode `100644`; use executable Git modes or
bake mode `0755` into the image with `COPY --chmod`.

Use the existing MOOG command construction/transport. Inspect the deployed
release version, tenant/test launcher, request schema and units. Historical
runbooks use MOOG v0.5.1.3; the inspected upstream requester workflow pins
v0.5.1.5. Neither proves the deployed version. Do not substitute `moog-head`.
Do not invent a generic dry-run flag; use a verified non-submitting inspection
path for the installed release. Do not use a live launch POST as a credential
probe.

Before submission, record the concrete request: repo/commit/directory, requester,
tenant/launcher, duration **and units**, fault scope, instrumentation behavior,
candidate config-image identity and expected commands. The actual MOOG config
image is built after the request; record its digest when available. Confirm existing user authorization
covers that request and its paid execution. If not, obtain it for this concrete
request; do not repeatedly ask for actions already authorized.

After submission, retain MOOG request/test ID and Antithesis run ID separately.
Check the agent-produced config-image digest and contents against the reviewed
candidate, and verify the actual launch payload where accessible. If this cannot
be inspected, record the limitation. Check build/setup logs, actual service images, container/hostname mapping, command
discovery **and invocation**, all intended endpoints and nonzero observations.
For fault campaigns inspect actual injected events and exclusions. Wait for
terminal status and classify each property as pass, SUT failure, harness failure
or inconclusive with its encounters and evidence.

MOOG `finished` with `outcome=failure` does not distinguish a setup failure
from a completed campaign containing findings. Inspect the direct terminal
report before classifying it. If the run/properties proxy returns 404 or the
Recent runs list is empty, recover the signed report URL from the finished MOOG
fact and open it in the authenticated browser. Those discovery failures do not
establish that the report is unavailable. Keep the signed URL private.

The next author must be able to recover the submitted inputs and understand the
result without the originating chat. Store sanitized request, source/artifact
identities, setup logs, command outputs, raw observations, assertion counts,
fault events, terminal report links and limitations together.

In the authenticated report, inspect **Environment → Notes → Docker-Compose**,
the image inventory, **Fault Configuration**, and expanded **Properties →
Antithesis Test Templates / SDK / Setup**. These expose stronger evidence than
the recent-runs row. Distinguish current properties from historical “not found”
entries. Use selected example logs to verify actual execution and fault events.
Preserve run IDs and sanitized evidence; do not publish signed report URLs or
credentials in the spec or workbench.

“All commands started/completed” is insufficient: old reports show these
summaries passing with zero discovered commands, or while individual commands
failed. Conversely, exit code 0 is insufficient for property success: an SDK
assertion can report a failure while its command returns 0. Check all three:
expected catalog, execution outcomes, and meaningful assertion results.

## 8. Current package status and lessons — 2026-10-20

This section is a dated example, not a compatibility catalog or evergreen status.
The [reconciliation audit](../docs/plans/2026-10-20-antithesis-engine-reconciliation.md)
and its [local exercise evidence](../docs/plans/verification/antithesis-engine-reconciliation/package-exercise-results.json)
describe the generated mixed mini-protocol package at application commit `6231810`.
The local faults-off exercise passed with selected Cardano 10.6.2 and Amaru
10.11.20260918; it did not establish remote conformance.

Direct contract checks of that earlier package found:

- **C05 gap in `6231810`:** generated, candidate and inspected config-image Compose
  contain literal `internal: true` on both networks. The generator in
  `antithesis_topology.py` emits this value. Inspected MOOG `PushTest.hs` only
  substitutes the literal `${INTERNAL_NETWORK}` token; it cannot repair these
  Booleans. Correct the remote artifact path and revalidate before submission.
- **C06/C07 gap in `6231810`:** `amaru-target-extract` has no explicit
  `container_name`, `hostname` or `init`. Its fault exclusion label does not
  establish a matching generated runtime name. Document its one-shot role and
  fix or justify the applicable requirements.
- **C03 unresolved:** generated `sm-workload` has a build context; the manual
  candidate replaces it with a local-only image. A public final digest and
  anonymous retrieval remain pending.
- Other 20 services explicitly match service/container names and set
  `init: true`; all 21 declare `linux/amd64`. This is structural evidence only.
- No new remote run has validated this package. Current broad measurement
  collection and automatic build preparation remain incomplete.

The subsequent adapter fix at `bbbb465` emits non-internal networks and gives the
extractor an explicit matching name/hostname and `init: true`, without changing
the shared Local transformation. Fresh artifact inspection confirms those
changes in the regenerated candidate and configuration image. See the
[selected-version scenario record](cardano_amaru_miniprotocol_security/spec.md)
for its exact evidence and remaining gates. This does not retroactively repair
the earlier saved artifacts or establish remote conformance.

The closest historical mixed campaign completed, but protected container-name
mismatches invalidated containment and limited recovery conclusions. Earlier
campaigns also encountered Boolean exclusions, pre-entrypoint inherited tests,
executable-bit loss, stale volumes, premature readiness, wrong Amaru ports,
genesis clock races and observation parsers that counted no samples.
See the [recorded pitfalls](../dwarf/docs/antithesis-scenario-preflight-checklist.md#common-pitfalls-recorded-from-this-project).
Treat each as a concrete regression risk in the applicable scenario.

### Authenticated historical checks, added 2026-10-20

The user provided browser access after the initial source audit. Six selected
reports were inspected directly, with expanded properties and two selected log
windows. The [versioned evidence summary](../docs/plans/verification/antithesis-engine-reconciliation/browser-runs-20261020/evidence.json)
contains full run IDs, MOOG metadata, selected observations and artifact hashes.
This is not an exhaustive tenant audit.

| Historical run | Direct observation | Requirement it tests |
|---|---|---|
| Mixed latest `e9dc8b…-60-7` | Three expected commands discovered. Containment: 13,624 passing / 1,245 failing evaluations. Log shows a kill of `dwarf-control-p2` at 707.109s while exclusions name `p2`; the subsequent observation reports connection refused and containment false. | C06/C10: compare actual names and fault events. This control failure prevents a clean containment claim. |
| Mixed earlier `45500e…-60-7` | All three commands have recorded exit-101 failures. The inspected driver example logs `PermissionDenied` at 713.727s. General started/completed summaries still pass. | C12/C13: discovery/scheduling is not successful execution. This log establishes the driver failure; other command root causes were not individually traced. |
| Mixed intermediate `239fd7…-60-7` | Composer lists/runs `mixed-kes-security` commands; intended mini-protocol command properties are not found. The workload image is `dwarf-kes-workload`. | C12: inspect the baked catalog and unwanted inherited commands. |
| Upstream control `be50c1…-59-13` | Completed, but both relay-exit properties and fork depth fail. Unique commands found is 0. | A retained control harness can illustrate topology; this run does not prove workload Composer integration or a clean campaign. |
| Phase-1 baseline `0195e5…-59-13` | Underfee driver: 151,735 passing / 0 failing executions; classifiable agreement: 155,608 passing / 0 failing evaluations. Separate fork-depth property fails. | Bounded positive evidence for that workload; preserve the separate failure and do not generalize to other workloads. |
| Runtime setup `eea71a…-59-13` | Report identifies bootstrap-producer exit 75 during setup, with no setup-complete. | C08/C11: images accepted and services started do not establish bootstrap readiness. The deeper cause of exit 75 was not established in this inspection. |

The latest report's Compose is byte-identical to the retained mixed mini-protocol
recipe, and is now [saved with the evidence](../docs/plans/verification/antithesis-engine-reconciliation/browser-runs-20261020/mixed-latest-expanded-reported-compose.yaml).
It uses `${INTERNAL_NETWORK:-false}` on both networks. The control and phase-1
reports use explicit false. This recovers the **report-displayed historical YAML**;
it does not recover the entire historical Git tree or download the config-image
filesystem.

The report also lists thread pausing, clock jitter and CPU modulation beyond
the four MOOG label classes. Record the complete effective fault profile rather
than treating an exclusion label as protection from every fault. Latest-run
logging exceeded its report's recommended output rate; verbosity is another
review item, separate from whether required observations exist.

These historical checks strengthen the contract. The subsequent adapter fix and
fresh candidate checks address the network/helper gaps separately; publication
and remote proof still require their own evidence.

### Generated-image publication handoff, added 2026-10-20

The dedicated wizard now supports explicit GHCR image push, manual public
visibility, anonymous manifest/config verification, a separate runtime ZIP,
exact public Git blob/mode verification, and a preview from the existing MOOG
request builder. Generated packages requiring no build can save an image receipt
without Docker and proceed to verification. Supplied URL/Compose packages retain
their staging-only workflow in this checkpoint.

These are bounded delivery checks, **not completion of G3**. The exported runtime
ZIP omits the private preview provenance. The handoff does not execute C01–C15
in full, verify every layer blob, qualify a selected version or submit a request.
The separate package-validation checkpoint below adds static checks and candidate
inspection. `submission_ready` remains false. The exact-tree verifier requires
exported file bytes and modes with no extras. Any optional documentation added
to a package changes its identity and requires revalidation. A scenario record
is not required. The request builder's local `ready` state is not submission approval.

See reconciliation section 24 for implementation, tests, read-only live evidence
and operational limits. The prior one-hour request is independent and must not be
repeated while exercising this preparation UI.

### Saved-package validation, added 2026-10-20

The dedicated Antithesis wizard can check saved preparations from all three
sources, generated built packages, and verified public exports. Select the exact
snapshot; reports bind to its inventory hash and do not modify its bytes.

- **Check package:** bounded UTF-8/YAML parsing, duplicate-key and shape checks,
  packaged relative dependencies, image syntax/build directives, names, hostname,
  network/logging/pull-policy restrictions, declared volumes/configs/dependencies,
  init review, metadata filenames and existing MOOG fault-label validation.
- **Inspect images/config image:** local Linux/amd64 image metadata without pulling
  service images; anonymous GHCR manifest/config checks; Docker Compose schema
  check without host interpolation or env-file resolution; build the pinned MOOG
  config recipe and compare every packaged file and directory mode from a stopped
  container. Verify the known INTERNAL_NETWORK replacement. Cleanup only that
  inspection's generated container and image tag.
- Separate `pass`, `fail`, `warn` and `not_checked` findings. A finished job means
  checking completed, not that every requirement passed. Static errors prevent
  candidate construction. Contexts overwriting base system paths are rejected
  before the recipe can execute its fixed sed command.
- Full Compose schema resolution needs the explicit Docker action. Public checks
  cover GHCR manifests/configs, not layer retrieval; other registries remain
  unchecked. Final candidate image identity is retained, but the mutable Debian
  base digest and actual remote build remain unverified. Local and remote image
  identities can differ; this is reported without rejecting experimental versions.
- Fresh offline readiness, binary behavior, actual-user mount access, workload
  command catalog/execution, setup SDK event, assertions, fault application,
  content-level credential review, MOOG operational checks and remote evidence
  remain distinct requirements. `execution_ready` and `submission_ready` stay false.

Automatic scenario-specific `spec.md` creation is deferred. It is neither
required to perform these checks nor an eligibility requirement for Local /run.
See reconciliation section 25 for the reviewed implementation and evidence.

## 9. Optional per-scenario record template

When useful, copy this structure to `<scenario>/spec.md` and fill applicable fields.
Keep the completed matrix/evidence beside it or link to versioned retained files.
This is an authoring template, not a schema currently parsed by DWARF.

```markdown
# <Scenario name> — Antithesis scenario record
Contract: antithesis/spec.md v1.3 at <full Git commit>
Reviewed date / authors / reviewers:
Status: <G0/G1/G2/G3/G4/G5; first failed gate>

## Experiment
Question; targets; input/fault surface; expected observations:
Harness family; baseline recipe/commit; deliberate changes:
Known-good control and prior bounded evidence:
Versions requested → resolved image → actual service/binary:
Bootstrap/tooling versions; state/genesis/corpus provenance:
Measurements requested → applied/recorded/unsupported/unverified:

## Compose and runtime
Public repository / commit / bundle directory:
Source package hash / final Compose hash / local candidate config-image digest:
Service table: key | container | hostname | role | image digest | init | readiness
Peer/port map; networks; local-only override diff:
Mount table: volume/path | writer | readers | permissions | retained evidence
Setup emitter and complete readiness sequence:
Fault map: service | eligible classes | excluded classes | reason | actual events

## Test catalog and observations
Workload image digest / build source revision / platform:
Stopped-image command inventory, modes, default-user permissions:
Command | template | schedule | operation | assertion | required encounters
Result classifications and unknown/unobservable behavior:
Recovery: infrastructure faults; persistent stimulus; retries; evidence:
SDK assertions / node coverage / measurement-patch evidence separately:

## Verification
C01–C15: status | exact implementation/artifact | evidence | gap/exception
Pass A review and second review:
Pass B review and second review:
Fresh-state local command results / Snouty version, output and limitations:
Anonymous image access / public Git modes / config-image inspection:
Deployed MOOG version and verified request settings/units:
Authorization reference for concrete paid request:

## Run result
MOOG ID / Antithesis ID / launcher / timestamps:
Actual MOOG-built config-image digest / content comparison / launch payload:
Remote startup, command invocation, endpoint and fault-scope evidence:
Terminal property outcomes and encounters:
SUT failures / harness failures / inconclusive observations:
Raw evidence location / hashes / sanitized report links:
Cleanup receipt; unresolved gaps; next reproducible action:
```

## 10. Source map and maintenance

Inspect these sources at the revision being used; the names are navigation aids,
not claims that every path implements every requirement.

| Area | Repository source |
|---|---|
| Full existing checklist and failure history | [Preflight checklist](../dwarf/docs/antithesis-scenario-preflight-checklist.md) |
| Original mixed bootstrap contract | [Upstream control README](upstream_amaru_control/README.md) |
| Current retained control recipe | [Relay bootstrap control](cardano_amaru_relay_bootstrap_control/docker-compose.yaml) |
| Mixed recipe and command image | [Compose](cardano_amaru_miniprotocol_security/docker-compose.yaml), [Dockerfile](cardano_amaru_miniprotocol_security/workload/Dockerfile), [observer](cardano_amaru_miniprotocol_security/workload/miniprotocol_observer.py) |
| Topology, target and workload binding | [Topology](../dwarf/profile_manager/antithesis_topology.py), [harness](../dwarf/profile_manager/antithesis_harness.py), [workload binding](../dwarf/profile_manager/antithesis_workload_binding.py) |
| Existing other engine paths | [Profile bundle](../dwarf/profile_manager/antithesis.py), [native generator](../dwarf/profile_manager/antithesis_generator.py) |
| Package staging and label validation | [Preparation](../dwarf/profile_manager/antithesis_preparation.py), [label validator](../dwarf/profile_manager/antithesis_validation.py) |
| Existing MOOG request and transport | [MOOG](../dwarf/profile_manager/moog.py), [remote](../dwarf/profile_manager/remote.py) |
| Relevant focused tests | [Compose](../tests/test_antithesis_compose_assets.py), [validation gate](../tests/test_antithesis_validation_gate.py), [mixed binding](../tests/test_antithesis_miniprotocol_binding.py), [Amaru binding](../tests/test_antithesis_amaru_binding.py), [MOOG integration](../tests/test_moog_integration.py) |
| Historical terminal results | [Evidence ledger](../docs/presentation/2026-09-17/evidence-ledger.md), [cross-source research](../docs/plans/verification/antithesis-engine-reconciliation/remote-validation-research.json) |

Official docs re-read through `snouty docs show` on the review date:

- [Container environment and architecture](https://antithesis.com/docs/setup/overview/)
- [Compose, config image and setup signal](https://antithesis.com/docs/setup/docker_compose/)
- [Docker settings: names, init, logging, networks, pulls](https://antithesis.com/docs/best_practices/docker_best_practices/)
- [Test Composer command semantics](https://antithesis.com/docs/product/writing_tests/test_templates/test_composer_reference/)
- [Setup mode](https://antithesis.com/docs/setup/setup_mode/)
- [Coverage instrumentation](https://antithesis.com/docs/product/writing_tests/instrumentation/coverage_instrumentation/)

Inspected MOOG source at `2642c60b5598562294e553fb3491fc5228033c91`:
[exclusion parser](https://github.com/cardano-foundation/moog/blob/2642c60b5598562294e553fb3491fc5228033c91/src/Docker/FaultExclusion.hs),
[config build and launch](https://github.com/cardano-foundation/moog/blob/2642c60b5598562294e553fb3491fc5228033c91/src/User/Agent/PushTest.hs),
[request serialization](https://github.com/cardano-foundation/moog/blob/2642c60b5598562294e553fb3491fc5228033c91/src/User/Types.hs).
This is not proof of the deployed agent revision.

### Official Antithesis skills: reuse with the MOOG boundary

The [official skills repository](https://github.com/antithesishq/antithesis-skills)
is useful preparation and analysis guidance. Reviewed revision:
`527c182d17aac476fdf9c9ca5e290e03caa4b62a` on 2026-10-20.
It does not replace DWARF's existing engine or MOOG submission route.
This source review did not invoke those workflows or install/update tools.

| Skill area | Applicable to DWARF | Required adaptation |
|---|---|---|
| Documentation and research | Official platform documentation, source analysis, property catalogs and topology reasoning | Reconcile with the chosen existing harness, current source and retained evidence; preserve their provenance. |
| [Setup](https://github.com/antithesishq/antithesis-skills/blob/527c182d17aac476fdf9c9ca5e290e03caa4b62a/antithesis-setup/SKILL.md) | Image architecture, Compose settings, SDK setup, local validation and log hygiene | Reuse existing recipe paths. Its preparation `build:` directives are not final MOOG service images. Its fresh-project scaffolding and direct-launch handoff do not override this contract. |
| [Workload](https://github.com/antithesishq/antithesis-skills/blob/527c182d17aac476fdf9c9ca5e290e03caa4b62a/antithesis-workload/SKILL.md) | Drivers, assertions, meaningful reachability, test-command layout and property review | Keep setup signaling outside commands; reconcile suggested assertion conventions with the actual SDK and image. Route any proposed test submission through MOOG. |
| [Launch](https://github.com/antithesishq/antithesis-skills/blob/527c182d17aac476fdf9c9ca5e290e03caa4b62a/antithesis-launch/SKILL.md) | Build/validate sequencing and exact-config discipline | Its `snouty launch` step is **not** DWARF's submission procedure. Use section 7 and existing MOOG request/transport; preserve tenant, launcher, duration units and fault settings. Do not substitute the skill's generic launcher defaults. |
| [Triage](https://github.com/antithesishq/antithesis-skills/blob/527c182d17aac476fdf9c9ca5e290e03caa4b62a/antithesis-triage/SKILL.md) and debugging | Methods for interpreting reports, assertion encounters, logs and causality | A MOOG-created run can still be analyzed by its Antithesis run ID. Direct API/report access depends on separate available credentials and authorization; retain MOOG↔Antithesis identity mapping. Debugger sessions may consume resources and are not implicit in read-only triage. |

Do not follow a nested setup/workload instruction to invoke direct launch.
The user-confirmed delivery route remains:

```text
DWARF harness + validation → reviewed public artifacts → MOOG → Antithesis
                                                             ↓
                                            reports / logs / triage
```

Snouty documentation and local validation do not bypass MOOG. Direct
`snouty launch` would. Result inspection does not change the submission route.

The inspected setup/launch/workload/triage skills require Snouty >= 0.8.0;
our retained local exercise used 0.6.1. Recheck the selected version's help and
engine/store selection before adapting commands; do not silently update the
operator environment. The upstream setup skill additionally directs authors to disable
ANSI color (`NO_COLOR=1` and tool-specific controls) so logs remain readable.
Record these as versioned upstream guidance, not proof the existing package
already implements them.

When a new failure exposes a missing requirement, update this contract, its
version/date and the existing checklist; add a regression check in the relevant
implementation when changing behavior. Preserve historical evidence boundaries.
For shipped docs or future `/learn` embedding, render this canonical file and
identify its revision. Do not maintain an independently edited UI copy.
