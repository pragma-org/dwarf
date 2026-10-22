# Cardano header validation: unbuilt contract

Source is cardano-node 11.1.2 at
`fef83fed01d7926f3de83b3b917be5a4a48768b5`, with GHC 9.6.7 and
Cabal 3.16.0.0. The manifest binds the complete patch set and dependency archive
identities. The first three patches are byte-identical to nanoseconds-v2;
older targets retain their original capabilities and do not acquire this tap.

The new boundary is `Ouroboros.Consensus.HeaderValidation.validateHeader` in
ouroboros-consensus 4.2.1.0. Its envelope checks run before its consensus protocol
checks, unchanged. This is header validation, not block download, block adoption,
ledger application, or proof of any particular opcert rejection reason.

The wrapper evaluates only the outer `Either` to WHNF, as the native consumer
must do. It never evaluates or shows a successful payload. A native `Right` is
accepted; native `HeaderProtocolError` and `HeaderEnvelopeError` are rejected,
with the native error rendered as at most 1024 printable ASCII characters.
This bounded diagnostic can be truncated and is not itself a decisive assertion
about an individual native nested error. Synchronous evaluation exceptions are unavailable,
never rejected, and the original exception is rethrown. No exception text or
full header/state is serialized. Classification, native diagnostic rendering and
sanitization, hash rendering, clock reads, complete strict JSON encoding, and
sink IO are contained separately from native evaluation. Synchronous
instrumentation failure attempts a constant bounded unavailable record without a header hash,
diagnostic, or timing; synchronous failure of that fallback is also swallowed.
The original native Left/Right or captured synchronous validation exception is
preserved after both attempts. The guard detects `SomeAsyncException` and immediately rethrows it,
including `ThreadKilled`, `UserInterrupt`, and custom async exceptions. A native
async exception exits before the end clock, hash, classification, encoding, sink
or fallback; an async instrumentation failure also exits without a fallback
write. Cancellation is never converted into unavailable evidence. No successful
payload is forced to produce diagnostics.
Tracing remains opt-in through `DWARF_CARDANO_MEASUREMENT_TRACE`; when disabled
the helper returns the original thunk without evaluating it.

Elapsed nanoseconds and the end timestamp use the monotonic clock. Timing ends
before classification, hashing, rendering and sink IO; `duration_us` is the
integer nanosecond duration divided by 1000. The public header hash is bounded
and the collector requires exactly 64 lowercase hex characters. The minimal
instrumentation-failure record is retained as unavailable and uncorrelated, with no fabricated duration or hash; it cannot supply a verdict or
timing sample. The collector also requires the exact new source/patch/toolchain pair, immutable image,
executable, build-result and probe digests, and internally consistent timing.
Raw input, normalized outcomes and hash indexes are retained. Unavailable,
malformed, absent, truncated or one-sided evidence stays incomplete. Even both
accepted and rejected samples remain pending same-boundary normal controls and
frozen node/scenario evidence; the collector cannot declare measurement complete.

The retained consensus fixture is byte-exact from the manifest's SHA-256-checked
4.2.1.0 archive. The retained network-helper fixture is the exact new-file output
of v2's first patch. Offline verification also applied all five patches in order
to the three SHA-256-checked retained dependency archives. No inherited source
was missing for these application checks. Fixture/application checks are not
compiler or runtime evidence.

No build, executable, image, registry readiness, compatibility confirmation,
probe, calibration or runtime proof is supplied. The existing patched preset is
unchanged: this tap requires explicit selection with a future exact v3 target.
A clean exact-source build and provenance-bearing image, meaningful live-event
probe, paired overhead calibration, accepted/rejected controls correlated by
header hash, and independent live-node health evidence remain required. The
opcert fallback remains blocked pending its own migration and prerequisites.

The earlier unbuilt v3 patch-set identity
`81b5f3e2cbff8d27bb40946097ad58c4b0dee38ce634922854ead4d04b7c158d`
lacked complete diagnostic containment and is denied this capability. Use only
the current manifest identity; this correction supplies no build/runtime proof.

The intermediate unbuilt patch-set identity
`09b7d2b1bac9808128f88645f96e25c5ace095213bf299717f5287d11dfc7375`
lacked async cancellation propagation and is also denied this capability.
