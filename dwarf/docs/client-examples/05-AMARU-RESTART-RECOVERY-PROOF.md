# Card 05 Amaru restart and recovery proof

Status: The Amaru leg has one accepted collection-proof run.

Child explanation: DWARF restarted the real node. The listener, chain, and peer came back. The node then caught up by at least five blocks, and DWARF kept enough resource samples.

## Exact run

- Run ID: `20260920T122606Z-32c0e998`
- Target: Amaru `10.11.20260912`
- Source revision: `b159172f25a9c389f82f20bca4f15e3032791638`
- Measurement revision: `nanoseconds-v2`
- Patch set SHA-256: `4c22d7b0c29a705d1471dcfb6ee09a306c936ce83fd47f808fb2bbb8c2c75de0`
- Image digest: `sha256:c3f139e87b4ada079a4dc5c656a2ca06c6dc30ea55719d54bedb772c836de862`

All four security assertions passed. The restart-readiness collector retained the listener, chain-progress, and peer-role gates in order. The controlled synchronization range advanced by at least five blocks. The target remained running, did not report an OOM, and had no unclassified fatal signal.

The process resource collector followed the target across two host PIDs. It retained 94 samples in total and 49 samples inside the exact `restart-recovery` window. The sample interval was 0.25 seconds, and the collector reported no sample errors. This exceeds the frozen minimum of 30 samples.

## Retained digests

- Manifest: `1ead3c8e821723576c4b36da2ac29f1623e4179fb5e685e70c0f29c3a3d4edac`
- Assertions: `a6d56c4368cf23cf312cb3852667c5f70ce8a72fecb890178f3859f288df7470`
- Measurement report: `e8a8feecd8aa360ccd00b4495b62cb4a0abac8a061f441fbeed7b00e68db8f75`
- Resource result: `42ca061e9d1da163ac33179e12690f63efb35d4634213896651cadf2c33b83c1`
- Restart-readiness result: `f0270b01ec8c373b35045e055a74d0399d02c46b75ffc87a0e865d60c7915412`
- Synchronization-speed result: `53e8a8b78e5665a9b6613c7b677f758b598a56676b3af7992dfa901864abf67a`

`cardano-profile verify 20260920T122606Z-32c0e998` returned `OK`.

## Claim limit

This run proves collection and recovery for one restart in this environment. It does not establish a stable mean, p95, or p99 restart benchmark. It is not a mixed-node comparison.
