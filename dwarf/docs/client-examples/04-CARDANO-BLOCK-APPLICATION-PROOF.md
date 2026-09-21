# Card 04 block-application proof

Status: Both separate implementation legs have accepted collection-proof runs.

Child explanation: DWARF watched each real node add blocks. It kept every short chain detour, checked that the node settled and kept moving, and kept the exact timer evidence.

## Cardano-node accepted run

- Run ID: `20260921T021935Z-3b58eafc`
- Framework commit: `91ed1320da73ad0fa031cb7c68373d4727dca869`
- Scenario SHA-256: `5fadd0c60dee960eb247e327755ff65137a55d29776aa47f68f90159101666d4`
- Target: Cardano-node `11.1.2`, source `fef83fed01d7926f3de83b3b917be5a4a48768b5`
- Measurement revision: `nanoseconds-v2`
- Patch set: `1c52fa42b7fd9ee3403165a5269ae851665536d2b920ed8be6fdbe490d1ed93c`
- Image: `sha256:956ae21cf9141a7149692392453ea31ece00e33960c2cca0f79548beeacf7370`

All four assertions passed. The window advanced from block 18 to block 106. It retained 88 adopted identities, 88 application samples, 88 exact correlations, and no exclusion. It observed no fork or rollback event and no fatal health signal. The standard report contains 103 block-application samples. Its minimum is 26.892 microseconds, median is 37.883 microseconds, p95 is 59.293 microseconds, p99 is 69.252 microseconds, and maximum is 82.588 microseconds.

Retained SHA-256 digests:

- Manifest: `b0772ee433fb95b9955fafa2b230734fee4a48350923fca729eff61ec7980a24`
- Assertions: `04e25ed0a62842ed614b39a07ebbc5999a79151126afa692292814c680382ac1`
- Measurement report: `118970306b5797a95b876182d635ef70703a5799607c24e13526cfc0083baad7`
- Controlled proof: `059e322acc6052404c3198a76f39b7c2a148e967f0b226849bab6bc952c94c7f`
- Health proof: `be94a485b151dd1e32759ad9be4f0c6e46b2e0a3cf7dc8d9b32720d410fb1549`
- Bundle: `c02a192f88cf9dc1caa6d9e3aea56a42c9436186e3596533cda19af7ab32af28`

## Amaru accepted run

- Run ID: `20260921T035546Z-9747122c`
- Framework commit: `e292babc02250ebfa0cbb866339f86064439c744`
- Scenario SHA-256: `2f25b399ebfe78f00fe2b19f3e7e73f8b1749e89d14d11df903812cea8522770`
- Target: Amaru `10.11.20260912`, source `b159172f25a9c389f82f20bca4f15e3032791638`
- Supporting Cardano-node: `10.7.1`
- Measurement revision: `nanoseconds-v3`
- Patch set: `042f6b1840bc6a30e65d77ce702e1be9967b77564ecfb9c77c1e5c25520aad00`
- Executable: `sha256:6c33df932f50601166a0107ed9a47742ebc501218be5bc59f99a69f5fcddc94c`
- Image: `sha256:d120f9515d5bcc6aa68629e0370fa7bdf5a5612e5d35005aa231d32ab2b7169a`

All four assertions passed. The window advanced 69 blocks. It retained 56 adopted identities, 56 exact correlations, 13 explicitly excluded unpaired timings, and 16 explicit fork or rollback events. One same-height switch was within the frozen bounds and was followed by 36 stable advances. The final external tip matched the final retained selection. The target remained running with zero restarts, no OOM, and no fatal signal.

Every proof timing has integer nanoseconds and exact fractional microseconds. The standard report used `patched-monotonic-nanoseconds` for all 69 block-application samples. Its minimum is 72.319 microseconds, median is 136.551 microseconds, mean is 138.99665217391305 microseconds, p95 is 196.446 microseconds, p99 is 347.806 microseconds, and maximum is 347.806 microseconds.

Retained SHA-256 digests:

- Manifest: `96408821a8b7b88d5fac594bf706d330993555a68d9e3fabb29828199cf49903`
- Assertions: `46f16b8e058946c1d233f4053417872187b6e55cbb9ea31901209c306e047a8d`
- Measurement report: `78e9eab912ba410a709591ea363ad94be3847b767f53b57410312135fdaa7c7d`
- Controlled proof: `1266fa35d98d4a20225fef5b5b74be583214f7bc3491b63a589b8a33ff397f5b`
- Health proof: `18b8a6a55311462511af6e1047390b7fb673e88123c3580bb6ba2131671c5691`
- Resource result: `d176e495566d96d94a4e10b7d6748589faf7903560b698fae911b8d26178e033`
- Bundle: `f17b4892ca45d003a94b7c175d510cbd0890420bdf79420a46ed4dff79c0c808`

`cardano-profile verify` returned `OK` for both runs. Each isolated import reproduced its signed manifest digest.

## Claim limit

These runs prove collection for one local-devnet block range per exact target and hardware. They do not represent mainnet traffic. They do not establish stable thresholds or a cross-implementation ranking.
