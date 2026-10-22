# Header-validation target contract

This unbuilt target adds one header-validation tap to the unchanged nanoseconds-v3
patch for Amaru 10.11.20260918, source aedfe797a5b8ef00d8b362be40b47a52c3b4a379.
The manifest freezes both patch hashes, sequential preimages, pinned base/probe
images, nightly-2026-09-04, cargo-zigbuild 0.23.4, Zig 0.15.2 and the locked musl
release command. The header preimage fixture is copied from that exact Git object;
its digest is checked before patch application. Existing patch sets are unchanged.

The wrapper calls the original validation body once, returns its unchanged Result,
and emits a monotonic duration for every returned Result. Ok means accepted;
ValidateHeaderError::Assert means rejected; missing nonces, stake distribution,
unknown pools or store errors mean unavailable. Panics and process termination do
not produce a terminal sample. Timing includes nonce evolution and Praos checks,
not queue time, receipt latency, block adoption or consensus progress. Enable
`amaru::consensus::measurement=debug` in retained JSON tracing for this event.

The new header patch applies to its frozen exact preimage. Full sequential
application of the combined patch set still requires every inherited v3 preimage
from the exact upstream source; it has not been independently reverified here.
Unit tests use synthetic records and patch application only. No node build, image,
executable, smoke probe, calibrated overhead or real accepted/rejected header proof
is supplied. There is deliberately no build-evidence.json or target registry entry.
The resolver must reject an unbuilt target. A future authorized build must use the
trusted build_amaru_measurement_target.py with this manifest and retain its exact
source, patch/preimage verification, toolchain, executable, image and probe hashes.
A future run must bind those identities and its node ID/scenario digest, successful
normal control, at least two accepted/rejected header samples with hashes, and raw
JSON events. The pinned opcert fallback still needs version-neutral migration and
a reviewed valid-header control. A parsed event or an available timing distribution
alone never makes this tap exercised or the measurement campaign complete.
