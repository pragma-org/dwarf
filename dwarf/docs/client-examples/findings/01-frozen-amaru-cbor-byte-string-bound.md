# Card 01 frozen-target finding: Plutus byte-string bound

Status: Card 01 cannot be accepted with the frozen Amaru revision.

Child explanation: Both nodes received the same 100 messages. Cardano rejected 30 broken messages that Amaru accepted. The Amaru version in this test does not check one required size limit.

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

These artifacts are diagnostic evidence. They are not accepted Gate 5 evidence. Card 01 remains incomplete. The frozen contract and Card 03 evidence are unchanged.

## Formal DWARF rehearsal

Run `20260920T135054Z-28289dcd` executed the complete frozen Amaru scenario against framework commit `cd6b925af431f70f314d59f9266e48e34e0e6388` and the exact `nanoseconds-v2` target. Three of four assertions passed. The only failed assertion was `cbor_conformance_clean`, with the same 30 expected-outcome mismatches.

The real node contained all 200 live attempts, advanced from block 316 to block 423, stayed running, did not restart, did not report an out-of-memory condition, and retained no fatal signal. The run retained 241 CPU samples and 242 resident-memory samples. Thus, the failure is not an unavailable target, a vacuous workload, or a liveness failure.

- Manifest SHA-256: `e8c1f6d79f81f5e9882bd43dabe0feb681d2df061c464e072a294af7838b0a38`
- Assertions SHA-256: `67519fd6f93f81b6ce83ba2aee99846eb0be463bb6847eaeafea9c48309566f2`
- Codec result SHA-256: `e71b06330e8217975276ad183bc1891745bcccd4559ffe1305adf6c7f4bd5e24`
- Live protocol result SHA-256: `06d5e46e7e9dbdff8cd2685178c472d79038444d6543b8cf3b869efdea71bd0f`
- Exported failed-evidence bundle SHA-256: `aaef5dabea72cab7d8c6d6f23133753f11b86ce41de1ba56bb32e2c456f94a8c`

`cardano-profile verify 20260920T135054Z-28289dcd` returned `OK`. This verifies evidence integrity; it does not change the failed security verdict.
