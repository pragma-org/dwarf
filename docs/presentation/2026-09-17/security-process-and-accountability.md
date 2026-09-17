# DWARF security process and accountability

Status: presentation proposal backed by root `SECURITY.md`

Date: 2026-09-10

## Decision to request

Adopt one evidence-led lifecycle for DWARF, Cardano-node, Amaru,
interoperability, harness, Moog/Antithesis, and infrastructure findings. Every
candidate gets one primary class, one accountable owner, and one explicit next
action.

## The process

```text
signal
  → preserve privately
  → prove target + setup + non-vacuity
  → classify scope and novelty
  → reproduce and bound demonstrated impact
  → report to accountable owner
  → track fix / accepted risk / duplicate
  → rerun original + neighboring cases
  → disclose and close
```

The classification is claim-specific. One Antithesis run may simultaneously
contain a real Amaru availability finding, a harness-invalid fork signal, and a
passing KES non-adoption property.

## Ownership matrix

| Signal belongs to | Primary owner | What DWARF must do next |
|---|---|---|
| DWARF product | DWARF maintainer | fix, regression-test, package, disclose |
| Harness / oracle | scenario owner | repair and rerun before making a node claim |
| Cardano-node | Cardano-node security/maintainers | submit private reproducer; verify fixed build |
| Amaru | Amaru security/maintainers | submit private reproducer; verify fixed build |
| Interoperability/spec | joint technical owners | prove common input/outcome and identify governing rule |
| Moog / Antithesis | relevant platform maintainer | retain run/request evidence; prevent SUT misattribution |
| Dependency/infrastructure | component owner | track affected DWARF surface and verify mitigation |
| Unknown | DWARF triage owner | gather discriminating evidence, then reclassify |

## Required “what next” fields

No finding appears in the presentation or tracker without:

- stable ID, status, severity, confidence, and class;
- affected versions and exact evidence provenance;
- demonstrated impact versus hypothetical impact;
- accountable external owner and current DWARF owner;
- next action, responsible party, target date, and blocker;
- private/public disclosure state and safe external reference;
- fixed revision/image, retest scenario, and closure rationale.

## Presentation examples

| Signal | Correct class | Current state | Accountable owner | Next action |
|---|---|---|---|---|
| Amaru epoch rewards panic | Amaru ledger/liveness | Verified/fixed | Amaru maintainers | keep regression in relevant weekly/release bundle |
| Exact-fee acceptance differential | Interoperability, likely Amaru ledger | Confirmed, closure limited | joint ledger owners until rule attribution | reproduce on current builds; link authoritative issue/fix |
| Amaru listener restart `EADDRINUSE` | Amaru networking/availability | Confirmed in W36; known background for later runs | Amaru maintainers | link/upstream status; verify candidate fixed revision |
| Bare service names vs explicit container names | DWARF/Moog harness contract | Harness-invalid | DWARF scenario owner | repair exclusions; locally prove protected controls; rerun |
| Antithesis ENODEV event | Platform | Platform signal | Antithesis | retain event and platform disposition; no node claim |
| 466-run chain agreement | No vulnerability | Proven negative result | DWARF evidence owner | keep pinned regression; repeat on material revisions |

## Initial disclosure rule

Use GitHub Private Vulnerability Reporting when available. Otherwise establish a
private channel with the project owner before sending details. Never put
credentials, private Antithesis report URLs, live access tokens, or an
uncoordinated exploit in a public issue or evidence bundle.

## Proposed response targets

- Critical: same-business-day acknowledgement.
- High: one business day.
- Medium/Low: three business days.

These are DWARF coordination targets for stakeholder agreement, not promises on
behalf of Cardano-node, Amaru, Moog, or Antithesis.
