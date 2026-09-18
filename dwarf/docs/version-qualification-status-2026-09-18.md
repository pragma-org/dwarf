# Node-version qualification status — 2026-09-18

This is the bounded hand-off for DWARF's version-aware real-node deployment
work. The newest-to-oldest search is **paused** after the single mixed candidate
required by the stopping rule. No additional release or pair should be tested
without deliberately resuming this backlog.

## Evidence-backed defaults

| Scope | Default | Result | Evidence |
|---|---|---|---|
| Cardano-only | Cardano-node `11.1.2` | Confirmed on a fresh five-node devnet with exact identity, sustained progress, convergence, and clean teardown. | `state:version-qualifications/20260918T032343Z-dwarf-qual-cardano-11-1-2-b1f28ed0` |
| Amaru target | Amaru `10.11.20260730`, supporting Cardano-node `10.7.1` | Confirmed as a real Amaru relay/consumer target contract. This is not standalone Amaru block production. | `state:version-qualifications/20260918T053321Z-dwarf-qual-amaru-10-7-1-10-11-20260730-b42c10e1` |
| Mixed | Cardano-node `10.7.1` + Amaru `10.11.0` | Retained confirmed fallback. | `workbench:dwarf-latest/obj_7b551786555142b78aecdcc5`; `state:topology-health/latest.json` |

## Tested candidates not selected as defaults

- Amaru `10.11.20260912` with supporting Cardano-node `10.7.1` is
  `incompatible` with the retained bootstrap-store contract. The schema-3
  store cannot satisfy its required schema-4-to-5 migration because historical
  pool opcert sequence numbers are incomplete. Evidence:
  `state:version-qualifications/20260918T035418Z-dwarf-qual-amaru-10-7-1-10-11-20260912-0100e9d2`.
- Amaru `10.11.20260903` has the same scoped bootstrap incompatibility.
  Evidence:
  `state:version-qualifications/20260918T042638Z-dwarf-qual-amaru-10-7-1-10-11-20260903-abae8d37`.
- Cardano-node `11.1.2` + Amaru `10.11.20260730` is `incompatible` with the
  required exact mixed serve-through contract. Both Amaru relays followed the
  honest chain to slot 5193, but the isolated Cardano consumer stayed at slot
  1368 for the full 30-minute observation and ended 3825 slots behind. Exact
  identities, isolation, no-fatal, no-restart, and clean teardown passed; the
  consumer progress/convergence and associated peer/service gates failed.
  Evidence:
  `state:version-qualifications/20260918T055108Z-dwarf-qual-mixed-11-1-2-10-11-20260730-aecab52f`.

The last result is an exact pair/topology classification. It does not by itself
assign the cause to Cardano-node or Amaru. The official mixed-network workstream
also treats an Amaru-only downstream consumer as the responder-path boundary:
`cardano-node-antithesis` issues 179 and 182.

## Unknown and untested

All catalog releases without a scoped retained qualification remain `unknown`.
Release discovery does not promote them. Amaru releases above the tested
schema-migration boundary are not globally defective; they need a newer
bootstrap producer/snapshot contract before this qualification can be repeated
meaningfully.

## Search stopping rule and resumption point

The search stopped after the bounded Cardano-node `11.1.2` + Amaru
`10.11.20260730` mixed run. The proven `10.7.1 + 10.11.0` pair remains the
default. No additional pair was launched.

Future resumption point:

1. Recheck the current upstream `cardano_amaru` responder-path implementation,
   Amaru issues/PRs, and Cardano-node issues for changes since this evidence.
2. Determine why the exact `11.1.2` consumer accepts its seeded state but does
   not advance from Amaru `20260730`; preserve the current evidence as the
   baseline and do not assume which implementation is responsible.
3. If qualification resumes, change one dimension only. The next exact pair is
   Cardano-node `10.7.1` + Amaru `10.11.20260730`, unless newer upstream evidence
   makes a different pair the justified first candidate.
4. Continue newest-to-oldest only after that isolation step, using fresh volumes
   and the same exact-identity, isolated-consumer, restart/recovery, fatal-signal,
   convergence, and teardown gates.
