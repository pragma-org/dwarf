# Node-version qualification status — 2026-09-18

This is the evidence-backed hand-off for DWARF's version-aware real-node
deployment work. The bounded newest-to-oldest search is complete for this goal;
no additional release or pair was tested after the newest exact mixed pair
failed the required serve-through contract.

## Corrected lifecycle

The authoritative lifecycle is the proven
`cardano_amaru_relay_bootstrap_control` topology. Cardano producers remain live
with coherent genesis, configuration, credentials, and ChainDB. Each Amaru
bootstrap wrapper works from a safe snapshot of its paired live producer and
then starts the exact selected Amaru binary against that same producer.

Results from the superseded offline synthetic ChainDB, copy, and separately
relaunch approach are retained as harness evidence. They do not classify node
release compatibility.

## Evidence-backed defaults

| Scope | Default | Result | Evidence |
|---|---|---|---|
| Cardano-only | Cardano-node `11.1.2` | Confirmed on a fresh five-node devnet with exact identity, sustained progress, convergence, clean process health, and clean teardown. | `state:version-qualifications/20260918T032343Z-dwarf-qual-cardano-11-1-2-b1f28ed0` |
| Amaru target | Amaru `10.11.20260912`, supporting Cardano-node `10.7.1` | Confirmed with exact binaries, two advancing Amaru relays, an isolated Amaru-fed consumer that reached zero-lag convergence, no fatal signal or restart loop, and clean teardown. This is not standalone Amaru block production. | `state:version-qualifications/20260918T091139Z-dwarf-qual-amaru-10-7-1-10-11-20260912-ae435ef5` |
| Mixed | Cardano-node `10.7.1` + Amaru `10.11.0` | Reproduced from fresh volumes with the corrected lifecycle and retained as the confirmed bounded-search fallback. | `state:version-qualifications/20260918T085655Z-dwarf-qual-mixed-10-7-1-10-11-0-cffd4b7c`; `workbench:dwarf-latest/obj_7b551786555142b78aecdcc5` |

## Tested exact pair not selected

Cardano-node `11.1.2` + Amaru `10.11.20260912` is `incompatible` with
the tested mixed serve-through contract. Exact identities, fresh state, Amaru
path isolation, no-fatal, no-restart, and clean teardown passed. The Cardano
producers and both Amaru relays advanced to slot 5001, but the isolated
Cardano-node consumer whose only upstreams were those Amaru relays remained at
its seeded slot 1362 for the full 30-minute recovery window and ended 3639
slots behind. Consumer progress, convergence, peer formation, and the combined
required-service readiness gate failed.

Evidence:
`state:version-qualifications/20260918T092619Z-dwarf-qual-mixed-11-1-2-10-11-20260912-89b35bbd`.

This is an exact pair/topology classification. It does not assign an unproven
root cause to Cardano-node or Amaru. The upstream responder-path work remains
tracked by `cardano-node-antithesis` issue 182; its completed child issue 179
defines the isolated Amaru-fed consumer boundary used here.

## Superseded results

The following retained runs used the discarded synthetic/relaunch lifecycle
and are no longer compatibility findings:

- Cardano-node `10.7.1` + Amaru `10.11.20260912`:
  `state:version-qualifications/20260918T035418Z-dwarf-qual-amaru-10-7-1-10-11-20260912-0100e9d2`.
- Cardano-node `10.7.1` + Amaru `10.11.20260903`:
  `state:version-qualifications/20260918T042638Z-dwarf-qual-amaru-10-7-1-10-11-20260903-abae8d37`.
- Cardano-node `11.1.2` + Amaru `10.11.20260730`:
  `state:version-qualifications/20260918T055108Z-dwarf-qual-mixed-11-1-2-10-11-20260730-aecab52f`.

Amaru issue 1152 and merged PR 1153 document the chain-store/opcert migration
boundary exposed by the discarded harness. The corrected lifecycle proves that
boundary did not prevent the current stable Amaru release from passing its
target contract.

## Unknown and untested

All catalog releases without retained scope-specific runtime evidence remain
`unknown`. Release discovery never promotes them. In particular, the successful
Amaru-target run with supporting Cardano-node `10.7.1` is not silently promoted
into a separate mixed compatibility claim; scope boundaries remain explicit.

## Search stopping rule and resumption point

The search stopped after the exact newest stable pair
`11.1.2 + 10.11.20260912` completed its full recovery window. The confirmed
`10.7.1 + 10.11.0` pair remains the mixed default. No additional pair was
launched.

To resume later without repeating work:

1. Recheck the current Amaru wiki/source/issues, Cardano-node source/issues, and
   `cardano-node-antithesis` responder-path work for changes since this evidence.
2. Preserve the corrected live-producer snapshot lifecycle and the exact failed
   run above as the baseline.
3. Change one version dimension at a time. The next justified Cardano step is
   `11.1.1 + 10.11.20260912`; the next justified Amaru step is
   `11.1.2 + 10.11.20260903`.
4. Require the same exact-identity, fresh-state, isolated-consumer, sustained
   progress, convergence, fatal-signal, restart-loop, and clean-teardown gates.
