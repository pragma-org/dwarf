# Card 01 frozen-target finding: Plutus byte-string bound

Status: The frozen Amaru revision has a completed security finding. The exact fixed-revision regression passed.

Child explanation: Both nodes received the same 100 messages. The old Amaru node accepted 30 broken messages. The exact newer Amaru node rejected them, so the saved test shows that the fix works.

## Exact boundary

The test used these fixed inputs and targets:

- Dataset revision: `a7561cd063550c2218898571520f14c3674efe91`
- Selection SHA-256: `3ed74e02f2f2be02ad942d26c193a125a34a0828373aac11cbf5efe082fdd467`
- Amaru revision: `b159172f25a9c389f82f20bca4f15e3032791638`
- Amaru adapter SHA-256: `3ff02655be359a28a937830ec996b0dbb2edb37d985ed91c705590948095047e`
- Amaru adapter-set SHA-256: `8d1eec53c984d18f926f23a4d8c9a98f5cc58f43c0f67854039a26684193498a`
- Amaru build-result SHA-256: `f0128720aef7d6e69646e28959e874ea707773cb436532f9899a291465d68f79`
- Cardano-node revision: `fef83fed01d7926f3de83b3b917be5a4a48768b5`
- Cardano adapter SHA-256: `40a73c765aed37f9a69ef1251873e631248c823adc81b61d27106d17ca94c546`
- Cardano adapter-set SHA-256: `6f51b43e149a7b4db0e5d3ba3c54fae86e3ac1cc816a0bff9fe65843429e290f`
- Cardano build-result SHA-256: `e4719dd1a7f0e0fffbac537c2b93e6555e222b9cbb2147a5c85921dac9897792`

## Result

Cardano-node returned all frozen expected outcomes: 25 accepted and 75 rejected. Amaru accepted 55 and rejected 45. Thus, Amaru had 30 expected-outcome mismatches. Both adapters had stable second-encode bytes for every accepted input.

All 30 mismatches contain a Plutus byte string that exceeds the 64-byte bound. Cardano-node returned `ByteString exceeds 64 bytes`. The frozen Amaru decoder collected byte-string chunks without checking this bound.

The Amaru change that introduced the affected decoder is an ancestor of the frozen revision. The later upstream fix, `d3a6dafcced78f5809a96619e883cf04911d2bdc`, is not an ancestor of the frozen revision. The frozen target therefore cannot pass `cbor_conformance_clean` without a target or patch change.

## Retained diagnostic evidence

- Amaru `result.json` SHA-256: `27088d288ba9724e8bf060a8db29afdc385244d8776acdf82966e3e814cab1c4`
- Amaru `inputs.ndjson` SHA-256: `a45031b51009e145ca30ec6aa1450eb40143c9aaf7703467f58289d5d895b73e`
- Amaru `report.md` SHA-256: `a2c72cee5ca391b4666678cf481fa8afa5dfc8cd9d8e40b5d8b456307997b6b4`
- Cardano `result.json` SHA-256: `55d824f041399fc00e6bb89e63b20d6da6fcee82022a81064bffaec7938e87d2`
- Cardano `inputs.ndjson` SHA-256: `b30467618d55da6b6b37128b2b152f976575d5c67aea431db868f5e73aaafea3`
- Cardano `report.md` SHA-256: `b164007b641eae4a8b1af8696f7ade2e78500e60d7b60fea54f8fbd200ada5eb`

These artifacts remain the accepted evidence for the old security finding. The failed assertion and its security meaning are unchanged. Card 03 evidence is unchanged.

## Formal DWARF rehearsal

Run `20260920T135054Z-28289dcd` executed the complete frozen Amaru scenario against framework commit `cd6b925af431f70f314d59f9266e48e34e0e6388` and the exact `nanoseconds-v2` target. Three of four assertions passed. The only failed assertion was `cbor_conformance_clean`, with the same 30 expected-outcome mismatches.

The real node contained all 200 live attempts, advanced from block 316 to block 423, stayed running, did not restart, did not report an out-of-memory condition, and retained no fatal signal. The run retained 241 CPU samples and 242 resident-memory samples. Thus, the failure is not an unavailable target, a vacuous workload, or a liveness failure.

- Manifest SHA-256: `e8c1f6d79f81f5e9882bd43dabe0feb681d2df061c464e072a294af7838b0a38`
- Assertions SHA-256: `67519fd6f93f81b6ce83ba2aee99846eb0be463bb6847eaeafea9c48309566f2`
- Codec result SHA-256: `e71b06330e8217975276ad183bc1891745bcccd4559ffe1305adf6c7f4bd5e24`
- Live protocol result SHA-256: `06d5e46e7e9dbdff8cd2685178c472d79038444d6543b8cf3b869efdea71bd0f`
- Exported failed-evidence bundle SHA-256: `aaef5dabea72cab7d8c6d6f23133753f11b86ce41de1ba56bb32e2c456f94a8c`

`cardano-profile verify 20260920T135054Z-28289dcd` returned `OK`. This verifies evidence integrity; it does not change the failed security verdict.

## Approved resolution

This run is a completed execution with a node conformance finding. Its `cbor_conformance_clean` assertion remains failed. DWARF must not classify the security verdict as a pass.

The regression target is exact Amaru revision `d3a6dafcced78f5809a96619e883cf04911d2bdc`, which contains the upstream fix at the same revision. The regression uses the unchanged frozen corpus and `nanoseconds-v2`. Its evidence must link back to this finding and identify both revisions.

Child explanation: The old node finished the test and showed a real bug. A newer exact node must receive the same messages and show that the bug is fixed.

## Accepted fixed-revision regression

Run `20260920T235440Z-050046a4` used exact Amaru revision `d3a6dafcced78f5809a96619e883cf04911d2bdc`, which contains the upstream fix. It used the same dataset revision and selection. All four frozen assertions passed.

The production codec processed 100 inputs: 25 were accepted and 75 were rejected. It had zero expected-outcome mismatches and zero round-trip failures. It retained raw integer nanoseconds and reported fractional microseconds. The real node also contained 100 unsupported-version cases and 100 malformed-CBOR cases. Its chain advanced from block 615 to block 705 during the workload. It stayed running, did not restart, did not report an out-of-memory condition, and emitted no fatal signal.

- Manifest SHA-256: `3e7412574ddcda16aaa788b4c27a4bc74f9728dd1fcc381fd5d822611fb65871`
- Assertions SHA-256: `545bb3ab6164782ba3aae56a95b9a9391f6170b1e0e80573873c96904c5f73ae`
- Codec result SHA-256: `89ac73f932ec9dc1cca0b78a0ec52714b3579f5289d5aad4227a4a4c354cf733`
- Codec raw inputs SHA-256: `1646e1b829640a104a86c5ee8cabe3df8410e734077bc461aa219e2dcb495118`
- Measurement report SHA-256: `1d2f60b939b5a62d1b3d4f44704b5415dcf2bf834dc8af6a4231c948900473f0`
- Live protocol result SHA-256: `9bd7645bf2e5e197c09dcd356bc7afd2aeb2bc78115e2b37d55f322769aa84a5`
- Health-and-progress proof SHA-256: `b84a76832c19eb20b88cb3ff79418c1d6dd7b6757346d50ed5408bad178ab1e6`
- Exported bundle SHA-256: `6fd2d11f9dcb17d181edc06e81983c3ff18968cfbc484fc20887ddbfc4513440`

`cardano-profile verify 20260920T235440Z-050046a4` returned `OK`. Bundle verification passed for all 97 files. Import recomputed the same manifest digest. A forensic check of the isolated imported run correctly reported that its preceding chain entry was not present in the temporary directory.

The old failed run and the new passing run are both retained. The new pass does not rewrite the old finding.
