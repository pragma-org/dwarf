# Card 01 Cardano-node CBOR decoding proof

Status: Both current Card 01 legs have accepted runs. The historical frozen Amaru failure remains a completed security finding.

Child explanation: The Cardano node and the exact newer Amaru node gave the expected answer for every saved message. They also stayed awake after 200 bad hello messages. The old Amaru failure stays visible because it found a real bug.

The exact old Amaru rehearsal is retained as failed run `20260920T135054Z-28289dcd`. It passed containment, progress, and round-trip assertions, but it failed outcome parity for the same 30 over-limit inputs. Exact fixed-revision run `20260920T235440Z-050046a4` passed all four assertions against the unchanged corpus.

## Exact run

- Run ID: `20260920T132629Z-ea000d37`
- Framework commit: `88d11aca6f9a78492f3ed18b7afd45a615dec2fa`
- Scenario SHA-256: `b26482ad5361cc18bf44f4670babb16ce71bfbb952465f2455f56266fff9129b`
- Seed: `0xA7561CD0`
- Target: Cardano-node `11.1.2`
- Source revision: `fef83fed01d7926f3de83b3b917be5a4a48768b5`
- Measurement revision: `nanoseconds-v2`
- Patch set SHA-256: `1c52fa42b7fd9ee3403165a5269ae851665536d2b920ed8be6fdbe490d1ed93c`
- Executable digest: `sha256:3fb83f12ac1152e96c884c756a505484a39d9200da6c59b24678ac61218220eb`
- Image digest: `sha256:956ae21cf9141a7149692392453ea31ece00e33960c2cca0f79548beeacf7370`
- Dataset revision: `a7561cd063550c2218898571520f14c3674efe91`
- Dataset selection SHA-256: `3ed74e02f2f2be02ad942d26c193a125a34a0828373aac11cbf5efe082fdd467`

All four security assertions passed. The production codec processed 100 inputs: 25 were accepted and 75 were rejected. There were no expected-outcome mismatches and no second-encoding failures. Raw integer nanoseconds are retained for every input. The report derives fractional microseconds without removing the sub-microsecond part.

The live node contained 100 unsupported-version attempts and 100 malformed-CBOR attempts. The target stayed running, did not restart, did not report an out-of-memory condition, and retained no fatal signal. The honest chain advanced from block 1572 to block 1682.

The run retained 244 CPU samples and 245 resident-memory samples. Accepted codec inputs have 25 samples, so that timing group is a small collection proof under the frozen quality rule. Rejected codec inputs have 75 samples and satisfy the useful-distribution floor. This page does not describe the 25 accepted samples as a stable benchmark.

## Retained digests

- Manifest: `efa2f0fc10eaf71cc1a11e4fbdc3042895bf83ee37f6d946e3f651ada0f3d691`
- Assertions: `0ac3cb15dbb23f507f2ded235d79c44041d9f5bd3131aa309b1ab126c59578ff`
- Measurement report: `e420ee87b93fe045744d46b183f0dd64677673a62051b7aae5504f22721ec571`
- Codec result: `a104e8d65320c2d97e3f7d5427135da1583d1a18fccacea404e8b130e338febf`
- Codec raw inputs: `b7a456e2e56228ec887146f43e6e23a23bc52e866a49f52a220670e7486af446`
- Live protocol result: `ac536b86fd9491dc9c78c5a10283077a579d1095a9eca252f7dd2ec2307220d7`
- Health-and-progress proof: `1a2a90033b34474ccf1558e8cafb6fe20e9c789c43702a663ce935a99f1f3f5e`
- Exported bundle: `f856535bae03fbf1e53da0b3a2db09f425afde30d2f5a87df28f3c076a452214`

`cardano-profile verify 20260920T132629Z-ea000d37` returned `OK`. Archive import recomputed and matched the signed manifest digest for all 70 files.

## Exact fixed Amaru regression

- Source revision: `d3a6dafcced78f5809a96619e883cf04911d2bdc`
- Measurement revision: `nanoseconds-v2`
- Patch set SHA-256: `4c22d7b0c29a705d1471dcfb6ee09a306c936ce83fd47f808fb2bbb8c2c75de0`
- Executable digest: `sha256:2970fa583d53967c72fdacb7e61d2954e1fb8d1b56fc7a34590018551085184e`
- Image digest: `sha256:c890ee54aad19fe36aa80efbdee2f8a155dd80b2207970e844c836d37d8c5932`
- Manifest SHA-256: `3e7412574ddcda16aaa788b4c27a4bc74f9728dd1fcc381fd5d822611fb65871`
- Assertions SHA-256: `545bb3ab6164782ba3aae56a95b9a9391f6170b1e0e80573873c96904c5f73ae`
- Exported bundle SHA-256: `6fd2d11f9dcb17d181edc06e81983c3ff18968cfbc484fc20887ddbfc4513440`

The fixed Amaru codec returned all 100 expected outcomes and retained stable second encodings. Its live target contained all 200 invalid protocol attempts and continued from block 615 to block 705 without a restart or fatal signal.

## Claim limit

These runs prove the Cardano-node and fixed Amaru functional and collection boundaries for the frozen corpus and live cases on this hardware. They do not prove full CBOR conformance, establish a stable timing threshold, or form an Amaru-versus-Cardano benchmark. The old Amaru mismatch remains a separate completed finding in `findings/01-frozen-amaru-cbor-byte-string-bound.md`.
