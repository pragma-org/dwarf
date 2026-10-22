# Cardano BlockFetch merged-request queue: unbuilt contract

Exact source: cardano-node 11.1.2,
`fef83fed01d7926f3de83b3b917be5a4a48768b5`, GHC 9.6.7,
Cabal 3.16.0.0. The patch-set SHA-256 is
`a31c97952f26eae5f5320815b8e435f0b8a30bc0911ff1f56d4b6e9756bc6882`.
The manifest binds every patch, source preimage and dependency archive.
Patches 0001–0005 are byte-identical to the current nanoseconds-v3 set.
The v3 directory and identity remain intact; they do not acquire this queue tap.
The inherited header capability is recognized for the exact v4 pair as well.

In ouroboros-network 1.2.0.0, `ClientState.addNewFetchRequest` atomically merges
requests in a per-client `TFetchRequestVar` (`TMergeVar`).
`acknowledgeFetchRequest` takes the merged request with the existing control
message semantics. The new `acknowledgeFetchRequestDwarf` exposes sidecar metadata
to the native client while the original API retains its return shape.

Pending depth counts the merged request's range fragments immediately after
enqueue, and zero immediately after take. It is a point sample, not a continuous
depth, time-weighted backlog, or the stock in-flight request/block counters.
A new request receives a per-client serial; subsequent merged writes keep it
and increment the merge index. The STM metadata is updated/taken with the native
request and disappears with its client state. The existing client registry
assigns non-reused nonzero client session IDs; exhaustion disables correlated
evidence. The exact production NodeKernel creates one fetch registry per node.
The 128-bit random trace namespace separates process/sink instances and restarts;
node/run/executable identity is still supplied by the frozen collector context.
This namespace is an identifier, not a cryptographic attestation. Combining
several NodeKernels into one process/sink is outside this exact node contract.

Monotonic clocks bracket enqueue and dequeue calls **outside STM**. They never
run inside retried transactions. For the first enqueue bracket `[E0,E1]` and
matching take bracket `[D0,D1]`, the collector retains:

- residence lower bound: `max(0, D0 - E1)`;
- residence upper bound: `D1 - E0`.

These bounds contain STM scheduling/commit uncertainty. A dequeue may begin
waiting before enqueue starts. An enqueue clock can end after the request is
already taken, so a zero lower bound is meaningful. There is no exact residence
field or midpoint estimate. Every merged write must be retained with contiguous
indices, and all bracket ordering must be consistent. Raw integer nanoseconds,
joined brackets and fractional-microsecond distributions are retained.

Handler service brackets only a successful native `addFetchedBlock` call, with
the native header's public block hash and the accepted request metadata. It
excludes download, body matching, decoding and ledger adoption. It does not
force or serialize a block, payload or returned value. A native exception or
cancellation leaves without a terminal service record; its original exception
propagates. Missing service remains incomplete evidence.

Tracing uses the existing opt-in `DWARF_CARDANO_MEASUREMENT_TRACE` sink.
Synchronous clock failures become unavailable timestamps; bounded hash rendering,
strict JSON encoding, metadata checks and sink IO are guarded separately from
native operations. Synchronous diagnostic failures attempt only a constant
unavailable record, with no fabricated IDs, hash or timing. Failure of that
write is swallowed. Every `SomeAsyncException`, including custom async
exceptions, is rethrown immediately without a fallback. The native operation
is outside all telemetry catches. Disabled tracing does not inspect block hashes
or payloads, or update queue metadata. A failed sink initialization can leave no
records; absence never counts as exercise.

The collector requires this exact source/patch/toolchain pair and immutable
image, executable, build-result and meaningful-probe digests. It rejects unknown
fields, booleans as integers, out-of-range uint64 IDs/timestamps, depths above
65536, malformed hashes and contradictory brackets. Exact duplicate events do
not inflate samples; conflicting event keys invalidate their entire request.
Requests from different client/trace sessions cannot be joined. At least one
coherent enqueue/take/service group is necessary, and samples alone still yield
only pending-control-evidence. Same-boundary normal controls, non-vacuous
workload, frozen scenario/node/process identity, independent live health and
paired overhead calibration remain required. `measurement_complete` stays false.

The new helper constraints include `MonadCatch` on request helpers,
`blockFetchClient`, `fetchLogicIterationAct`, `fetchLogicIteration`,
`fetchLogicIterations`, and `blockFetchLogic`. The request helpers also require
`MonadMonotonicTime`. These propagated constraints are a **hard compile blocker**:
a clean exact-source compile must validate all callers and the intended toolchain
before this target can be considered built. No compilation or Haskell execution
was performed, so behavior preservation is a reviewed design, not a runtime proof.

Offline verification applied all six patches sequentially, verifying each
preimage, against all three original SHA-256-checked dependency archives named
in the manifest. Retained fixture files are byte-exact network archive source,
except `DwarfMeasurement.hs`, which is the exact output of v3's 0001/0004 patches.
No inherited source was missing. These are patch-application checks only.

No build evidence, executable, image, target registry record, probe, readiness,
calibration, runtime or compatibility promotion is supplied. The existing
patched preset is unchanged. The block-application fallback remains blocked
pending its separate version migration and real queue reachability proof.

The campaign evidence adapter now runs this tap's trusted joined projection over
the queue subset of the retained normalized rows. It reconciles the four native
distributions and per-tap count, selects a deterministic matching metric, and
retains numeric samples bound to source event digests and shared correlation identifiers. Every
original queue row remains an event; duplicates cannot inflate samples. Other
producer kinds remain in `normalized.ndjson` but are excluded from this tap's
campaign signals.
Malformed, conflicting, incomplete or unavailable evidence contributes no samples.
Residence lower/upper bounds remain distinct, without an exact value or midpoint.

This tap's campaign rule requires the native `blockfetch_handler_queue` event
and correlates signals by `run_id`, `trace_session`, `client_id` and `request_id`.
Enqueue/take events deliberately contain no block hash; the trusted join still
requires valid public service block hashes without inventing hashes on other
rows. No generic evaluator requirement is relaxed and no missing node identity
is created. Compilation, retained controls/workload/health and calibration remain
independently required as described above; adapter support does not establish
automatic campaign completeness or runtime readiness.
