# Card 02 Plutus VM status

Status: Both revision-locked legs have accepted evidence. Card 02 is complete.

Child explanation: Both script engines gave the same answer and used the same fuel. Each real node ran 30 scripts that work and 30 scripts that fail. The new Amaru test chain put the required V2 price list into its real rule book before the test started.

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

## Exact Amaru run

- Run ID: `20260921T013953Z-565b77c3`
- Framework commit: `05e757016838afd0594aeeea5b7019698d797e73`
- Scenario SHA-256: `3dc7db5b24e3e6396cf6e25304db3c5a96469863be94d540ca2c3b468ed49b01`
- Seed: `0x5107A502`
- Target: Amaru `10.11.20260912`
- Source revision: `b159172f25a9c389f82f20bca4f15e3032791638`
- Support node: Cardano-node `10.7.1`
- Measurement revision: `nanoseconds-v2`
- Image digest: `sha256:c3f139e87b4ada079a4dc5c656a2ca06c6dc30ea55719d54bedb772c836de862`
- Executable digest: `sha256:05233bac96c1914a232a2d9c5a704f08401aff0b20356c015e848f295b919b78`
- Patch-set SHA-256: `4c22d7b0c29a705d1471dcfb6ee09a306c936ce83fd47f808fb2bbb8c2c75de0`
- Cost-model SHA-256: `675a27a3c1f2f9b32954c67c1f0ad21479713eef5513386638d78e05f5e277cc`

The additive topology submitted Conway governance update transaction `f2a39979f67f1853abfe59cbe1c92e8e015ea997bc09c8beb69c45dd7183c84e`. Governance action `f2a39979f67f1853abfe59cbe1c92e8e015ea997bc09c8beb69c45dd7183c84e#0` activated the pinned 175-entry Plutus V2 cost model in epoch 2. The run retains the signed transaction, action, anchor, six generated genesis files, live protocol parameters, and their digests.

All three security assertions passed. The evaluator leg retained 120 matched result-and-budget records. The live target included 30 valid transactions as valid and 30 expected-invalid transactions as invalid. All 60 attempt IDs, lock transaction IDs, and spend transaction IDs are unique. Amaru advanced from block 335 to block 662. It stayed running without a restart, out-of-memory condition, fatal signal, or unexpected exit.

## Amaru retained digests

- Manifest: `436232f557906fbb58661b528db1ac6d37c73ac21506e7daef9dce9ad2956927`
- Assertions: `98e71e58ab5f7696b121ae1c498155d24524c35146f86af2926effe41f304c06`
- Measurement report: `86c3dc58e43a3571243693a26de6622f983d926a3cd9d3c046f4678bd5d2c525`
- Live transaction result: `9403d75f28e7ad4125aef0d774a40e340d12fcee9d1d892437cd20a1f158708c`
- Health-and-progress proof: `eec8329a68e6358ae5fef76b369e19ef85ee402a2dd6b799ea4fc2ed8e677f64`
- Exported bundle: `bf5604de608889cabc4ea30242a2236aaccf12cd06c07999c14ee26a8a3c7ed0`

`cardano-profile verify 20260921T013953Z-565b77c3` returned `OK`. Bundle-local verification passed across 575 files. Archive import recomputed and matched the signed manifest digest. The isolated import does not include the previous run-chain ancestor, so it does not independently prove full local chain ancestry.

## Historical finding and claim limit

Diagnostic runs `20260920T113104Z-1b3d7b41` and `20260920T113249Z-b7d21c2e` still prove that the preserved older topology had no active Plutus V2 cost model. See `findings/02-frozen-amaru-no-plutus-v2-cost-model.md`. The additive topology resolves this gap without changing the old topology or its evidence.

This evidence proves the two-program evaluator result and budget check and both live workloads. It is not full Plutus conformance, a stable performance threshold, or an Amaru-versus-Cardano benchmark.
