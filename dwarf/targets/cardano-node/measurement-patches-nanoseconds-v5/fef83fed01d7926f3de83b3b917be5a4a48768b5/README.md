# Cardano TxSubmission2 request/body handling: UNBUILT contract

Exact source: cardano-node 11.1.2,
`fef83fed01d7926f3de83b3b917be5a4a48768b5`, ouroboros-network 1.2.0.0,
GHC 9.6.7, Cabal 3.16.0.0. Patch-set SHA-256:
`95991a736b2e187ace2ec9873f0f10ffdc0ebd432269857dfbc2c74041371d0c`.
The manifest binds all patches, dependency archive digests and source preimages.
Patches 0001–0006 are byte-identical to v4. V4 and older identities remain intact
and never acquire this tap. V5 inherits their original capabilities.

The stock 11.1.2 audit already records blocking/pipeline/mempool-submit cumulative
waits, accepted/rejected mempool-add durations and counters. It lacks a joined
per-requested-transaction interval. This deliberately small contract measures
`inbound-v2-request-body-handling` in `TxSubmission.Inbound.V2`, using its native
FIFO body-request batches and `CollectTxs requested txs` identity. It adds:

- `request`: clocks around native `resolveTxRequest` and, with tracing enabled,
  forced `Map.size` of its result, after the scheduler selects this body request
  and before constructing `SendMsgRequestTxsPipelined`. The resolver returns a
  lazy `Map.fromList` expression; forcing its size completes the native Map
  structure and strict values needed for request encoding before the end stamp.
  This forcing stays outside telemetry catches, preserves native exceptions and
  inspects no transaction body. This additional forcing is disabled with tracing;
- `body`: clocks around the body callback's existing unsolicited/body-size
  checks, before native `applyReceivedTxs`;
- `terminal`: clocks around successful native `applyReceivedTxs`, which consults
  a mempool snapshot and updates shared/peer body state in STM.

A returned body yields `body-returned` then `body-handled`. An omitted requested
body yields `body-omitted` in both later phases; it has no fabricated body receipt.
`body-handled` only means that native body-state handling returned successfully.
It does not establish that the body was buffered, submitted to the mempool,
accepted, ledger-valid, included or adopted. Shared coordination can discard or
supersede bodies, and later submission may take a different path. That later
lifecycle is outside this contract. Native protocol violations, native
exceptions, disconnects and cancellation emit no fabricated terminal record;
any observed incomplete request remains incomplete evidence.

All requested transactions in a batch share its clock brackets. Per-transaction
correlation does not isolate individual validation/service cost. Neither clock
endpoint is a wire send/receive instant or an exact STM commit instant. For an
earlier bracket `[A0,A1]` and later bracket `[B0,B1]`, the collector retains only
lower `max(0, B0-A1)` and upper `B1-A0` bounds, separately for request-to-body,
body-to-terminal and request-to-terminal. It never reports an exact residence or
midpoint. Intervening tracing, scheduling, network and instrumentation overhead
remain within the residence window. Paired calibration is still required.

Depths are native peer-local point snapshots: `peerDownloadedTxs` size counts
buffered pending bodies, and `peerRequestedTxs` size counts in-flight requested
bodies. They are not advertised/unacknowledged txid depth, global shared-state
backlog, protocol pipeline-message depth or a time-weighted queue. Each native
phase snapshot contributes once per request batch, regardless of tx count.

The existing opt-in `DWARF_CARDANO_MEASUREMENT_TRACE` sink is reused. The registry
assigns non-reused nonzero peer sessions in its native registration transaction;
request serials follow body-request FIFO order in peer-local state, independently
of interspersed txid requests. Counter exhaustion permanently disables identity
with sentinel zero. Process trace session, exact bounded native peer text, peer
session, request serial, exact bounded rendered txid and native Map index/count
are retained. No transaction body or error payload is serialized. The exact
production consensus NodeKernel creates one V2 registry per node (the pinned
consensus dependency's `Ouroboros/Consensus/NodeKernel.hs`, initialization beside
`newSharedTxStateVar`). Several NodeKernels/overlapping peer registries in one
process/sink are unsupported. The random trace namespace separates processes;
it is an identifier, not an attestation. Frozen run/node/executable identity is
supplied by collector context, never inferred from a txid or namespace.

The sink guards bounded identity rendering, full strict JSON encoding and IO.
Synchronous diagnostic/clock/sink failures are contained and may produce only a
constant `unavailable` record, without invented timing or identity. Failure of
that fallback is contained. `SomeAsyncException` always rethrows immediately,
without fallback. Native `resolveTxRequest`, protocol guards and `applyReceivedTxs`
remain outside telemetry catches, preserving their outcomes and exceptions.
Clocks never execute inside STM. Disabled tracing does not render identities or
accumulate FIFO metadata. A sink that cannot initialize may produce no records;
absence never proves exercise.

The collector requires the exact source/patch/toolchain pair and immutable
image, executable, build-result and runtime-probe digests. It rejects unknown
fields, booleans as integers, invalid uint64 values, nonprintable/overlength
identities, invalid outcomes/depths and inconsistent phase ordering. In-flight depth must cover the batch before
handling and fall by exactly its native cardinality at terminal handling. Every
requested transaction and all three phases of its batch must be retained with
contiguous bounded indices, unique rendered IDs and consistent batch snapshots.
Exact duplicates do not inflate samples; conflicting keys poison their entire
batch. A peer session cannot change its native peer text. Joins and summaries
are deterministic, retaining original integer nanosecond brackets.

The standalone collector can report only `pending-control-evidence` or
`incomplete`; `measurement_complete` stays false. Exact compilation (including
`MonadCatch`/clock constraints and caller propagation), live probes, a non-vacuous
N2N body workload, omitted-body case, same-boundary normal control, frozen
scenario/node/process identity, independent health and paired calibration remain
mandatory. No Haskell execution or compilation occurred; reviewed source and
offline application are not behavior-equivalence or runtime evidence.

The separate campaign adapter reruns the strict join over every row claiming
this tap in the shared normalized stream. It requires an explicitly complete
collector export and reconciles all eight full metric shapes, units, finite
summaries and the reported maximum sample count against both collector and
per-tap results. It selects a deterministic metric at that count, retaining its
individual values, a compact batch lineage commitment, and the exact phase or
transaction endpoint digests used for each value. `source_event_set_digest` is
the canonical digest of the sorted unique full source-event digest list;
`source_event_count` records that list's length. The retained `normalized.ndjson`
source rows and shared request identity allow independent reconstruction without
repeating the whole list on every sample. Only correlation identifiers shared by all batch
contributors are copied. Depths have one sample per phase
per batch; the suite therefore requires run, trace, peer and request identity
rather than a transaction ID on every depth sample. Transaction identity and
cardinality remain mandatory inside the join. No generic evaluator rule changes.

Every tap-claimed row remains a campaign event, including duplicates and
conflicts. Other producer kinds remain in the retained raw/normalized artifacts.
Malformed, incomplete, unavailable or conflicting populations, duplicate count
inflation, unsupported metric shapes and mismatched counts produce zero numeric
samples. Projection is capped at 4,096 numeric samples and 16,384 tap-claimed
source rows, including duplicates. These limits are checked before normalization
and joining; oversized populations produce zero numeric samples while all events
and artifacts remain visible. Explicit `samples.json` cannot supply or override
numeric values; the original artifacts and reported counts stay unchanged. A coherent omitted-body
population can supply handling bounds only when all three numeric brackets are
present. Body handling is never relabeled acceptance, rejection or adoption.
Synthetic full-collector/reader/evaluator tests exercise this evidence boundary;
they establish no live-node qualification. Exact compilation, live probes,
non-vacuous N2N workload including omissions, same-boundary controls, frozen
identity, health and paired calibration remain blockers. The existing mempool
fallback remains blocked because it does not establish this N2N body-handling
boundary; a reviewed operator-selected workload is still required.

Offline verification sequentially applies all seven patches to the three
original SHA-256-verified archives. Retained V2/Types/Registry fixtures are exact
archive bytes; the helper fixture is the exact v4 0001/0004/0006 output. This is
an UNBUILT contract. No registry record, build evidence, executable, image,
probe, profile enablement, readiness, fallback or compatibility promotion is
supplied. No nodes, network, deployment, Prepare or Run operations were used.
