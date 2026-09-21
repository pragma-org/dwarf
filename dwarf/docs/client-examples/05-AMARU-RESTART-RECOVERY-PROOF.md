# Card 05 Amaru restart and recovery proof

Status: The Amaru leg has one accepted collection-proof run.

Child explanation: DWARF restarted the real node. The listener, chain, and peer came back. The node then caught up by at least five blocks, and DWARF kept enough resource samples.

## Exact run

- Run ID: `20260921T045619Z-15e864a0`
- Framework commit: `b009507629c02b3437d28981c77bc6641b2d290f`
- Scenario SHA-256: `41b89b052a2ed9ad2b7621f102c7e5edaff785d30d435593f07c8dd9437960f8`
- Target: Amaru `10.11.20260912`
- Source revision: `b159172f25a9c389f82f20bca4f15e3032791638`
- Measurement revision: `nanoseconds-v2`
- Patch set SHA-256: `4c22d7b0c29a705d1471dcfb6ee09a306c936ce83fd47f808fb2bbb8c2c75de0`
- Executable digest: `sha256:05233bac96c1914a232a2d9c5a704f08401aff0b20356c015e848f295b919b78`
- Image digest: `sha256:c3f139e87b4ada079a4dc5c656a2ca06c6dc30ea55719d54bedb772c836de862`

All four security assertions passed. The restart event occurred at 22.848742977 run-relative seconds. The listener, chain-progress, and peer-role gates followed at 24.627932394, 28.107197208, and 28.176285019 seconds. The target advanced from block 701 before restart to block 702 at readiness.

The controlled synchronization range then advanced from block 702 to block 707 under the `single-controlled-producer` policy. Its run-relative events occurred at 28.395241286 and 33.062464060 seconds. The target remained running, did not report an OOM, and had no unclassified fatal signal.

The process resource collector retained 134 samples in total and 40 samples inside the exact `restart-recovery` window at a 0.25-second interval. It reported no sample errors and exceeded the frozen minimum of 30 restart-window samples.

## Retained digests

- Manifest: `e54c9f1434e5b59b9d18a5cb49dd88012a6d4fb7fd2053d052a8129b9eaceac3`
- Assertions: `a6d56c4368cf23cf312cb3852667c5f70ce8a72fecb890178f3859f288df7470`
- Measurement report: `d3f5b0230bc422cf971bdf374161214e6f2ef578ac13bede9e0048498c7f650a`
- Resource result: `b309293c8987eeb08d6f3040d8e820ecce27407fe321fa39c735209998a26a92`
- Restart-readiness proof: `2bd8b4b3dfcf970907dfd47b022dfb70d5ea34dc512d2a27b03225d7cd96d25f`
- Controlled-sync proof: `9bc2d79939afaa0b4767d62ee87e9c1dda7d8aa2594e28f30abc6562e84a5d5f`
- Health and progress proof: `a35a0f94c799e8032bcbfa6ee6ec9999b08828615fab4c17baa1f0554cf6d1a0`
- Exported bundle: `d0b0bce35254094636c83e59d688bf7be894c9bc0da9fae99f1eda2089e22217` (85 files)

`cardano-profile verify 20260921T045619Z-15e864a0` returned `OK`. Isolated bundle import reproduced the signed manifest SHA-256 exactly.

## Claim limit

This run proves collection and recovery for one restart in this environment. It does not establish a stable mean, p95, or p99 restart benchmark. It is not a mixed-node comparison.
