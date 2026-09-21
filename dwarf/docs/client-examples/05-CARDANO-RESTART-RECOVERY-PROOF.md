# Card 05 Cardano-node restart and recovery proof

Status: The Cardano-node leg has one accepted collection-proof run.

Child explanation: DWARF restarted the real node. The listener, chain, and peer came back in order. The node then caught up by five blocks. DWARF kept resource samples from both the old and new processes.

## Exact run

- Run ID: `20260921T045807Z-8e2bbb0e`
- Framework commit: `b009507629c02b3437d28981c77bc6641b2d290f`
- Scenario SHA-256: `76dfeba29356d08484bbcc2f91a2a4aff0598d63d86eae3e839bafa4dea1d2b1`
- Seed: `0x5E570005`
- Target: Cardano-node `11.1.2`
- Source revision: `fef83fed01d7926f3de83b3b917be5a4a48768b5`
- Measurement revision: `nanoseconds-v2`
- Patch set SHA-256: `1c52fa42b7fd9ee3403165a5269ae851665536d2b920ed8be6fdbe490d1ed93c`
- Executable digest: `sha256:3fb83f12ac1152e96c884c756a505484a39d9200da6c59b24678ac61218220eb`
- Image digest: `sha256:956ae21cf9141a7149692392453ea31ece00e33960c2cca0f79548beeacf7370`

All four security assertions passed. The restart event occurred at 12.203754293 run-relative seconds. The listener, chain-progress, and peer-role gates followed at 43.613261448, 43.696754341, and 43.770176351 seconds. The target advanced from block 11 before restart to block 28 at readiness.

The controlled synchronization range then advanced from block 28 to block 33 under the `three-node-controlled-local-mesh` policy. Its run-relative events occurred at 43.933904762 and 55.934900395 seconds. The target remained running, did not report an OOM, and had no unclassified fatal signal.

The resource collector retained 225 samples in total and 173 samples inside the exact `restart-recovery` window at a 0.25-second interval. It reported no sample errors and exceeded the frozen minimum of 30 restart-window samples.

## Retained digests

- Manifest: `b21f5535993133b331f6e1d15b40c6c73a746ca2d9fe18d9caf28d241d771943`
- Assertions: `311e25ae02bb746aeba106cc503a90a1d454259de334442616da606e06db7bbd`
- Measurement report: `c48f5781be30f54bf9e39e318238b5b73fb978abf2f2ccaa2b51001eb0cf4e23`
- Resource result: `8e0265418aa3e077dc0e17e766b76ab2eb9d19c69d57d48968bb329bb0291e54`
- Restart-readiness proof: `970558063400b4ad6a804e09e4d271b98c63d419b88d896882f17c6e5c04d2fd`
- Controlled-sync proof: `643260973cd015d83a0250a8a497ceb5a43b9b2ee12ae9dd622b01af34f8950c`
- Health and progress proof: `d44e324116f08db6ca7b008de7d2583519d9cab4a01d685b64f0b43997c86dec`
- Exported bundle: `0cf0d4e2d6eeb49a82c3b3053c2b2a5b0eb39e37c8489301df76b06a2a48cfce` (70 files)

`cardano-profile verify 20260921T045807Z-8e2bbb0e` returned `OK`. Isolated bundle import reproduced the signed manifest SHA-256 exactly.

## Claim limit

This run proves collection and recovery for one restart in this environment. It does not establish a stable mean, p95, or p99 restart benchmark. It is not a mixed-node comparison.
