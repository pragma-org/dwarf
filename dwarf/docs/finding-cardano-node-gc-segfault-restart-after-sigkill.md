# Finding note — cardano-node 10.7.1 segfaults in the GHC garbage collector when it restarts after SIGKILL (for the cardano-node team)

**From:** DWARF Antithesis runs of a generated package (Pragma) · **Date:** 2026-11-11
**Scope:** authorized defensive conformance testing of a private two-producer devnet
(`testnet_42`) on the Antithesis platform. No exploit. A crash on restart, with two core dumps and
one identical stack. Local reproduction outside Antithesis is proposed, not yet done.

> **Status note (2026-11-11, after the local run):** the evidence so far **points to a platform
> (Antithesis) effect, not a cardano-node bug.** The crash appears only on the platform; the same
> image, same configuration and the same SIGKILL-and-restart on a plain Docker host do not crash.
> The two platform-only differences are the `libvoidstar` preload and the injector's choice of the
> kill moment. Treat this note as a platform-interaction report until a real instrumentation-off run is in.
> Run `a5fcb78d…` was meant to be that run but went out **instrumented** (a driver bug dropped the
> flag; the platform parameters show `custom.instrumented: true`); it crashed too, so the count is
> now 4 of 4 instrumented runs. The instrumentation-off run is resubmitted. Do not report it
> upstream to cardano-node on this evidence.

## Summary

In three one-hour Antithesis runs of the same package, every run had the same event:

1. The Antithesis fault injector kills one cardano-node 10.7.1 producer (`SIGKILL`, container
   exit 137).
2. The platform restarts the container about 8 seconds later.
3. The node prints its configuration and connects to the tracer, then dies with **signal 11
   (SIGSEGV) about 8 milliseconds after start**, before any ChainDB trace.

Two of the three crashes have a core dump. Both cores show the **same stack, at the same
instruction**, inside the GHC runtime's garbage collector:

```
#0  0x00000000004123b2 in evacuate1 ()          ; mov (%rbx),%r12  — closure pointer load
#1  0x0000000003dc8ba1 in scavenge_stack1 ()
#2  0x0000000003dc9316 in scavenge_one ()
#3  0x0000000003dca24a in scavenge_loop1 ()
#4  0x0000000003dbf90e in scavenge_until_all_done ()
#5  0x0000000003dc13a6 in GarbageCollect ()
#6  0x0000000003dae486 in scheduleDoGC ()
#7  0x0000000003daf23b in schedule ()
#8  0x0000000003dafc9c in scheduleWorker ()
#9  0x0000000003db431c in workerStart ()
```

The faulting instruction dereferences a closure pointer found on a thread stack during the first
garbage collections after start. A stack slot that is not a valid closure pointer this early in
the process points at memory that was written outside the runtime's rules: an `unsafe` FFI call,
a native library, or runtime state that the restart finds in an unexpected shape. The crash
happens only on a restart after an unclean exit; the first start of the same containers, in the
same package, never crashed in any run.

## Evidence

| Run (Antithesis) | Package commit | Node | Kill → restart → crash (virtual time) | pid | Core (zstd, sha256 prefix) |
|---|---|---|---|---|---|
| dc774cde43e2…-64-11 | 89e67d3e | node2 | kill ≈5076.9 → start 5084.82 → SIGSEGV 5084.83 | 259439 | — (not downloaded) |
| eaec880ac0cf…-64-11 | 1b956fcc | node1 | start 5119.23 → SIGSEGV 5119.27 | 268121 | `7aa85de58fbf19b4` |
| 5494150c6e67…-64-11 | 94a2d5dc | node2 | kill 4230.7 (exit 137) → start 4238.69 → SIGSEGV 4238.70 | 107346 | `5e289f9e7c7668b6` |
| a5fcb78d773f…-64-11 | 67347004 | node1 | exit 139 (instrumented after all: the submission driver dropped the flag) | — | — (not downloaded) |

- Package: `antithesis/generated/runtime-substrate-txsubmission-window-pressure-example-smoke`
  in `pragma-org/dwarf` (two cardano-node 10.7.1 producers, CF configurator rig, tracer).
- Binary: `ghcr.io/intersectmbo/cardano-node@sha256:3275d357053d21f3220f74b0854fd584e1fe322dfa1bbb78effd760c3191d14c`,
  `/nix/store/w8riazzgg1psca5d34mihj1h0psjm7b3-cardano-node-exe-cardano-node-10.7.1/bin/cardano-node`
  (sha256 prefix `9eb29b2d87295a18`), GHC 9.6.7.
- Node configuration at the time of the crashes: `LedgerDB.Backend = V2InMemory` (no LMDB),
  `ExperimentalProtocolsEnabled` and `ExperimentalHardForksEnabled` false, genesis hashes pinned,
  `AcceptedConnectionsLimit` declared, tracer forwarding on. The runs with the earlier package
  commits (89e67d3e, 1b956fcc) had the configurator's defaults for the two experimental flags.
- The platform preloads its instrumentation library (`/usr/lib/libvoidstar.so`) into every
  process. This note does not exclude an interaction between that library and the GHC runtime;
  see "What is not established".
- The chainhold package, which runs the same cardano-node image as relays next to an Amaru
  consumer, had no crash in two one-hour runs. Its relays were not killed by the fault injector
  in those runs.

## What is established

- The crash is deterministic for this state: two independent runs, two cores, one stack, one
  faulting instruction.
- It is a crash of cardano-node itself, inside the Haskell runtime's GC, not of DWARF and not of
  the Amaru node.
- The trigger is "restart after SIGKILL" *on the platform*. A fresh start of the same image and
  configuration does not crash, and one SIGKILL-and-restart on a plain Docker host with the same
  image digest does not crash either (local run below).

## What is not established

- Whether the node recovers on the next restart. The platform's exported logs for the crash moment
  end at the crash.
- Whether the crash needs the platform's instrumentation preload. A run with instrumentation off
  (`--no-instrumentation` in the DWARF submission) would answer this.
- The root cause. Candidates, in order: a native dependency or `unsafe` FFI path exercised only
  during recovery of an unclean ChainDB (ImmutableDB or VolatileDB validation), or a GHC 9.6.7
  runtime bug on this path.

## Upstream search (2026-11-11)

- `IntersectMBO/cardano-node`: no issue reports a segfault on restart after SIGKILL for 10.x.
  Older: #2050 (2020, "block producer die during startup"), #5276 (2023, serialisation segfault
  across 1.35.7/8.x). #6635 (open, 2026-08) is a Windows crash that leaves the ChainDB dirty — a
  different symptom, same recovery territory.
- `IntersectMBO/ouroboros-consensus`: #1885 (open since 2026-02-17) "Investigate segfaults in
  ChainDB q-s-m tests" — random segfaults in the ChainDB test suite, cause unknown. Possibly the
  same class; it is the closest upstream item. #1708 (closed) is about malformed snapshots, with a
  clean error, not a segfault.
- GHC: #24163 shows `evacuate1` under `scavenge_loop1`/`GarbageCollect` (version not stated);
  #24258 is GHC 9.6.3 crashing non-deterministically with SIGSEGV/SIGBUS. Neither matches this
  trigger.
- `pragma-org/amaru`: not involved (Amaru never crashed in the mixed runs). The only Amaru segfault
  item, #1558, is a musl build problem, unrelated.

So: not reported upstream in this form. It is new as a production-binary observation with a
deterministic trigger and a stack.

## Reproduce via DWARF

**Antithesis path (done, 3 of 3 runs):**

1. Public commit `94a2d5dc` (or later), directory
   `antithesis/generated/runtime-substrate-txsubmission-window-pressure-example-smoke`.
2. Submit through the DWARF Antithesis wizard with faults on, one hour.
3. In the Antithesis report, open "No unexpected crashes → cardano-node"; the core dump is an
   artifact of the finding. Backtrace it against the binary from the image above:
   `gdb -batch -ex "bt" ./cardano-node core`.

**Local path (done 2026-11-11, no crash):** catalog scenario
`runtime-substrate-restart-after-sigkill-example-smoke` composes the same two-producer devnet with
the **same image digest** (`sha256:3275d357…`, source revision `045bc187`), runs the TxSubmission
window load on node1, applies `runtime_kill_node` with `signal: KILL` to node2 (`docker kill -s
KILL`, verified down), `runtime_restart_node` (verified up), then a 20 s
`runtime_multi_node_observation`. Run `20261111T130152Z-09bed302`: node2 came back 8 s later,
validated its 12 ImmutableDB chunks (the unclean-shutdown path), rejoined node1 and advanced from
block 31 to 48 during the window; `all_nodes_started_clean`, `all_nodes_responsive` and
`reconnection_clean` all pass. So one SIGKILL-and-restart on a plain Docker host does **not**
reproduce the crash with this binary. What the platform adds and the local path does not: the
`libvoidstar` preload, the deterministic scheduler, and a kill moment chosen by the fault
injector. The next step is the instrumentation-off platform run (DWARF request field
`instrumentation: off`, which reaches MOOG as `--no-instrumentation`).

## Recommendations

1. ~~Report to `IntersectMBO/cardano-node`~~ — not on this evidence. First separate the node from
   the platform (items 2 and 3). If the crash survives an instrumentation-off run **and** reproduces
   off-platform, then report with the two cores, the binary digest and this stack, referencing
   ouroboros-consensus #1885. If it does not, report it to Antithesis as a preload/GHC-runtime
   interaction instead.
2. Run the same package once with the platform's instrumentation off, to separate the node from
   the preload.
3. ~~Build and run the local kill-and-restart scenario~~ — done, no crash; repeat it several times
   (different kill moments) before calling the local path clean.
