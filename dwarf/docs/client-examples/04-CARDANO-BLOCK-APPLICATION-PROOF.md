# Card 04 Cardano-node block-application proof

Status: The Cardano-node leg has one accepted collection-proof run.

Child explanation: DWARF watched one real node add blocks. It matched each kept timer sample to the exact block hash that the node accepted. It also watched the node's resource use during the same time window.

## Exact run

- Run ID: `20260920T125840Z-778a7cf7`
- Framework commit: `9f5e9c2f42ad48b11bd864f6657f555fc683bf72`
- Scenario SHA-256: `87e4ce24f8185fc4a73f4d5a9af48d8f9b57f3d264bb597c4e468d6f6196119b`
- Seed: `0xB10C0004`
- Target: Cardano-node `11.1.2`
- Source revision: `fef83fed01d7926f3de83b3b917be5a4a48768b5`
- Measurement revision: `nanoseconds-v2`
- Patch set SHA-256: `1c52fa42b7fd9ee3403165a5269ae851665536d2b920ed8be6fdbe490d1ed93c`
- Executable digest: `sha256:3fb83f12ac1152e96c884c756a505484a39d9200da6c59b24678ac61218220eb`
- Image digest: `sha256:956ae21cf9141a7149692392453ea31ece00e33960c2cca0f79548beeacf7370`

All four security assertions passed. The exact controlled window advanced from block 751 to block 831. It retained 80 adopted block identities, 80 application samples, and 80 exact hash correlations. Every retained timing has integer raw nanoseconds and fractional microseconds. No application sample was excluded in this final run.

The resource collector retained 180 samples inside the exact 180-second `controlled-chain-progress` window. The run-wide measurement report retained 103 block-application samples. Its values range from 27.392 microseconds to 84.983 microseconds. The exact-window proof is the authority for the 80 adopted-block correlations.

## Retained digests

- Manifest: `be80c020d26c93dccff61cd3224837f27d5e96d607a73f403a17d5136f3a3560`
- Assertions: `27368128ef9eca9ac819f92a3702eb8422c284a283f1a7922febbaad387eb32f`
- Measurement report: `e12aacb033a1d3dfa6091d654bf51aeb06316c76d96c095c68144d2eba70c88f`
- Resource result: `8d7982dd30a5065028c04e94f8aa4586e70fab6c08d192dec1297c9ef29bc9f9`
- Controlled-progress proof: `ffdc7b8846a426bbd930b8d003af0cc1ee275b6ebdb1ff53cf2afa9c30d5ffe0`
- Health-and-progress proof: `e1a8558881a05ea15f50e0e50ce8ceabafc531a29eb001f72b5a12f0b9b467a2`
- Exported bundle: `1e03242b941f000db4487ba36566d890202e4eee5758abacd9d58daad38701d2`

`cardano-profile verify 20260920T125840Z-778a7cf7` returned `OK`. Archive verification returned `pass` for all 66 bundle-local files.

## Claim limit

This run proves collection for one local-devnet block range on this target and hardware. It does not represent mainnet traffic. It does not establish a stable mean, p95, or p99 threshold. It is not an Amaru-versus-Cardano benchmark.
