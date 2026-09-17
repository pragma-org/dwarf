# DWARF security policy

DWARF is a prototype adversarial real-node testing framework. Security reports
may concern DWARF itself, a tested node implementation, interoperability between
implementations, the test harness, or an external platform. This policy keeps
those scopes separate so a harness defect is not reported as a node
vulnerability and a real node finding is not lost as a failed test.

## Reporting a vulnerability

Do not open a public issue containing an uncoordinated vulnerability, exploit,
private run URL, credential, token, or sensitive reproducer.

Preferred reporting path: use GitHub Private Vulnerability Reporting from the
repository's **Security** tab when **Report a vulnerability** is available. If
that option is unavailable, contact the project owner through an established
private channel and request a private reporting location before sending the
sensitive details.

Include, when available:

- a short impact statement and affected component;
- exact DWARF, Cardano-node, Amaru, Moog, and image revisions/digests;
- scenario, seed, configuration, topology, and observation window;
- minimal reproduction and the expected versus observed behavior;
- evidence showing that setup completed and the intended target was reached;
- whether faults were active and the exact affected/protected identities;
- crash, assertion, transcript, trace, or independent-observer evidence;
- known related issues, reports, fixes, or possible duplicates;
- any disclosure deadline or coordination already in progress.

Do not send secrets that are not required to reproduce the issue. Redact
credentials and private report links from portable evidence bundles.

## Supported scope

Security fixes are evaluated against the current `main` revision and the exact
pinned revisions recorded by a finding. Historical releases may be used to
reproduce a report, but a finding is not considered resolved until the fix is
verified on the intended fixed revision.

In scope for this repository:

- DWARF controller, dashboard, scenario engine, evidence handling, and release
  packaging;
- DWARF-owned scenarios, workloads, probes, assertions, and node patch sets;
- incorrect security claims caused by target, setup, workload, fault-scope, or
  oracle defects;
- credential or sensitive-evidence exposure caused by DWARF;
- reproducible findings in Cardano-node or Amaru reached through DWARF.

Node findings remain owned by the affected node project. Antithesis, Moog,
registry, CI, operating-system, and dependency findings are coordinated with
their respective maintainers.

## Classification and accountability

Every candidate finding receives one primary class and owner:

| Class | Examples | Primary accountable owner | DWARF responsibility |
|---|---|---|---|
| DWARF product | controller, dashboard, evidence, secret handling | DWARF maintainer | reproduce, fix, verify, disclose |
| Harness / oracle | setup, fault scope, observer, vacuous assertion | DWARF scenario owner | repair harness and rerun before any node claim |
| Cardano-node | Haskell node ledger, consensus, networking, runtime | Cardano-node security/maintainers | preserve evidence, report privately, supply reproducer, verify fix |
| Amaru | Rust node ledger, consensus, networking, runtime | Amaru security/maintainers | preserve evidence, report privately, supply reproducer, verify fix |
| Interoperability / specification | implementations disagree and ownership is not yet attributable | joint Cardano/Amaru technical owners | prove the differential, identify the governing rule, coordinate assignment |
| Moog / Antithesis platform | launch, orchestration, fault injection, platform runtime | relevant platform maintainer | preserve request/run evidence and prevent invalid SUT attribution |
| Dependency / infrastructure | registry, CI, OS, third-party library | dependency or infrastructure owner | track DWARF exposure and verify the upgrade or mitigation |
| Unknown | evidence is insufficient to assign scope | DWARF triage owner | keep private, gather discriminating evidence, then reclassify |

One person or team owns the next action even when several projects must
collaborate. “Joint” does not mean ownerless.

## Finding lifecycle

Use exactly one state:

1. **Candidate** — a signal exists; target reachability, non-vacuity, novelty,
   and attribution are not complete.
2. **Confirmed** — the behavior is reproducible and the evidence supports its
   stated scope and impact.
3. **Reported** — the accountable owner received the private report and an
   external reference is retained privately.
4. **Accepted / duplicate** — the owner acknowledged the issue, accepted risk,
   or linked the authoritative prior issue.
5. **Fix available** — an exact candidate patch, commit, image, or release is
   available but DWARF verification is incomplete.
6. **Verified** — the original reproducer and neighboring regression cases pass
   on the fixed revision without losing workload coverage.
7. **Closed** — disclosure/credit and retained evidence are complete, or the
   candidate was conclusively classified invalid or non-security.

Alternative terminal classifications are **harness-invalid**,
**infrastructure-invalid**, **known/duplicate**, **not reproducible**, and
**non-security behavior**. Preserve the evidence and rationale for each.

## Required finding record

Every finding record must contain:

- stable finding identifier and concise title;
- status, severity, confidence, class, and affected versions;
- exact evidence and provenance;
- impact that was demonstrated, separated from hypothetical impact;
- primary accountable owner and current DWARF owner;
- private/public disclosure state and external reference, when safe;
- **next action**, responsible party, target date, and blocker;
- proposed fix or mitigation, if known;
- fix revision/image and DWARF retest evidence;
- duplicate/related findings and final closure rationale.

A run failure is not automatically a vulnerability. A run pass is not proof
that no vulnerability exists. Finding status is based on the supported claim
and evidence, not the framework exit code.

## Triage requirements

Before attributing a candidate to Cardano-node or Amaru:

1. Confirm exact target identity and source/image provenance.
2. Confirm setup completed and the intended workload reached the target.
3. Prove the relevant assertion was non-vacuous.
4. Check fault events against actual service, hostname, and container identity.
5. Separate node, interoperability, harness, infrastructure, known, and
   platform signals.
6. Search current project documentation, source, issues, pull requests, and
   prior DWARF/Antithesis reports for overlap.
7. Reproduce without unrelated faults when the claim does not require faults.
8. Retain a minimal, secret-free reproducer and independent outcome evidence.

## Severity and response targets

Severity reflects demonstrated impact, reachability, prerequisites,
repeatability, and affected deployments. It is not inferred from a panic string
alone.

- **Critical:** demonstrated safety/consensus compromise, broadly reachable
  remote compromise, or secret exposure with immediate material impact.
- **High:** repeatable remote node compromise or sustained availability loss on
  a realistic path.
- **Medium:** bounded availability, integrity, or interoperability impact with
  meaningful prerequisites.
- **Low:** conformance, hardening, or limited ingress behavior without a
  demonstrated safety, propagation, or exhaustion impact.

Proposed acknowledgement targets are the same business day for Critical, one
business day for High, and three business days for Medium/Low. These are
coordination targets, not a warranty or a promise on behalf of upstream
projects.

## Fix verification

A fix is verified only when DWARF records:

- the exact fixed source revision and immutable image digest;
- failure of the original reproducer on the affected build and success on the
  fixed build;
- neighboring positive and negative regression cases;
- preserved setup, target reachability, and workload coverage;
- no new correctness, liveness, or recovery regression in the relevant bundle;
- an updated finding status and explicit next action or closure reason.

For DWARF-owned instrumented node builds, verify the paired stock build at the
same source revision and quantify instrumentation overhead.

## Coordinated disclosure

Keep candidate and confirmed findings private until the accountable owner has a
reasonable opportunity to triage and remediate. Coordinate timing and wording
with the affected project. Public artifacts must remove credentials, private
URLs, access tokens, unrelated personal data, and exploit details that are not
needed for defensive verification.

Credit reporters according to their preference. If a report is a duplicate,
preserve the reporter's independent evidence and link it to the authoritative
issue when disclosure permits.
