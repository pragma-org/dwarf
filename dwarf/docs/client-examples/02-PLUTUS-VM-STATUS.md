# Card 02 Plutus VM status

Status: The Cardano-node leg has one accepted revision-locked run. The complete card remains partial because the frozen Amaru chain has no Plutus V2 cost model.

Child explanation: Both script engines gave the same answer and used the same fuel in the saved test. The Cardano node also ran 30 scripts that work and 30 that fail. The old Amaru test chain cannot start those live V2 scripts because it has no V2 price list.

## Exact Cardano-node run

- Run ID: `20260920T135958Z-362eedc7`
- Framework commit: `fa3bde7407b3bbcfdafe6ce0cc6663d04553b6a8`
- Scenario SHA-256: `0c594be0ba5deb2779d93d54bf9941fceac2acbe100673dcf364013fb05f2161`
- Seed: `0x5107A502`
- Target: Cardano-node `11.1.2`
- Source revision: `fef83fed01d7926f3de83b3b917be5a4a48768b5`
- Measurement revision: `nanoseconds-v2`
- Image digest: `sha256:956ae21cf9141a7149692392453ea31ece00e33960c2cca0f79548beeacf7370`
- Executable digest: `sha256:3fb83f12ac1152e96c884c756a505484a39d9200da6c59b24678ac61218220eb`
- Patch-set SHA-256: `1c52fa42b7fd9ee3403165a5269ae851665536d2b920ed8be6fdbe490d1ed93c`
- Cost-model SHA-256: `675a27a3c1f2f9b32954c67c1f0ad21479713eef5513386638d78e05f5e277cc`

All three security assertions passed. The exact evaluator adapters ran each frozen program 30 times for each implementation. All 120 results and CPU/memory budgets matched the frozen expectation under the same retained 175-parameter cost model. Raw integer nanoseconds and fractional microseconds are retained for every evaluator record.

The live Cardano-node target included 30 valid and 30 invalid transactions and advanced from block 9 to block 174. It stayed running without a restart, out-of-memory condition, or fatal signal.

## Retained digests

- Manifest: `f18ab6b9ea15c484f40c1224f654bced4e1fe343a11c7bc54953c920573e4ef0`
- Assertions: `7e35cc1dc122d45db1efb7de9daacff6167a65856181b028485b1da074d640e2`
- Measurement report: `673728097db82a7006e6261b93ec33772c8161fcd7d294982f1a271951506b96`
- Evaluator report: `bb340151865e136df3e0eb021db9d7a162a71aa9c37217bf8a6df0325fcf746a`
- Live transaction result: `e3071d43fd9fe06ac2b902107de7dbfc3312b28ca4a0728de262968cccbb0b32`
- Health-and-progress proof: `a2c80d528b9df8131ddcd82d8585b328ae01a0c103df1248e8370dc81fc6a5d1`
- Exported bundle: `3a49f5cc19c6a43d514bd7cabac74095adb8ec6c974ecdc6d0061e5c50d0d920`

`cardano-profile verify 20260920T135958Z-362eedc7` returned `OK`. Archive import recomputed and matched the signed manifest digest across 552 files.

## Amaru blocker and claim limit

Amaru rehearsals `20260920T113104Z-1b3d7b41` and `20260920T113249Z-b7d21c2e` stopped before live transaction submission because the frozen chain configuration has no Plutus V2 cost model. See `findings/02-frozen-amaru-no-plutus-v2-cost-model.md`.

This evidence proves the two-program evaluator result and budget check and the Cardano-node live workload. It is not full Plutus conformance, a stable performance threshold, or an Amaru-versus-Cardano benchmark. It does not prove the required live Amaru leg.
