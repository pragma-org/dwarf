# Card 05 Cardano-node restart and recovery proof

Status: The Cardano-node leg has one accepted collection-proof run.

Child explanation: DWARF restarted the real node. The listener, chain, and peer came back in order. The node then caught up by five blocks. DWARF kept resource samples from both the old and new processes.

## Exact run

- Run ID: `20260920T130232Z-2c68fb83`
- Framework commit: `9f5e9c2f42ad48b11bd864f6657f555fc683bf72`
- Scenario SHA-256: `76dfeba29356d08484bbcc2f91a2a4aff0598d63d86eae3e839bafa4dea1d2b1`
- Seed: `0x5E570005`
- Target: Cardano-node `11.1.2`
- Source revision: `fef83fed01d7926f3de83b3b917be5a4a48768b5`
- Measurement revision: `nanoseconds-v2`
- Patch set SHA-256: `1c52fa42b7fd9ee3403165a5269ae851665536d2b920ed8be6fdbe490d1ed93c`
- Executable digest: `sha256:3fb83f12ac1152e96c884c756a505484a39d9200da6c59b24678ac61218220eb`
- Image digest: `sha256:956ae21cf9141a7149692392453ea31ece00e33960c2cca0f79548beeacf7370`

All four security assertions passed. DWARF retained the restart, listener-ready, chain-progress-ready, and peer-role-ready events in order. The controlled synchronization range advanced from block 862 to block 867. The target remained running, did not report an OOM, and had no unclassified fatal signal.

The resource collector followed two process IDs across the restart and reported no sample errors. It retained 217 samples in total, 156 samples inside the exact `restart-recovery` window, and 30 samples inside the exact `controlled-sync-range` window. The 0.25-second interval preserves the frozen minimum of 30 samples for both required windows.

## Retained digests

- Manifest: `283af0f2de02fe49904c70d30b594d2bd2f48ec1ccbb308a74be1281a6f3b552`
- Assertions: `311e25ae02bb746aeba106cc503a90a1d454259de334442616da606e06db7bbd`
- Measurement report: `a12d52fd3a660d140b0a7207c140ab4697f4cb031cf142ba999544a8c0bd4a64`
- Resource result: `17ffeb676ca17ca325b9a8c317196e62cd96e8ed8aab147dfc5259f897936e38`
- Restart-readiness proof: `bf57508bfcc59ecbdde0f9a7cab3053645cc016701e4e594946f5491d3659d59`
- Controlled-sync proof: `d602640ca9cea51a832645b07d163dce5007169a548c2e3c2da9c39a4ecc1b6d`
- Exported bundle: `53e0d13fddbe44aec99381c0eed6594f0ee18726cb45c5e2a8bebcb0ec581f2a`

`cardano-profile verify 20260920T130232Z-2c68fb83` returned `OK`. Archive verification returned `pass` for all 70 bundle-local files.

## Claim limit

This run proves collection and recovery for one restart in this environment. It does not establish a stable mean, p95, or p99 restart benchmark. It is not a mixed-node comparison.
