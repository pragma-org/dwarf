# Amaru node-capacity probe — 2026-09-10

## Conclusion

On `dwarf-host-a`, 100 additional real Amaru relay/consumer processes ran concurrently and continued applying blocks while the existing DWARF mixed network remained available. A 150-process population crossed the host's 16-thread load guardrail during concurrent catch-up. The defensible quick estimate for this workload is therefore:

- tested operating level: 100 Amaru relay/consumer nodes;
- estimated operating range: approximately 100–125 nodes during concurrent catch-up;
- observed overload boundary: 150 nodes;
- limiting resource at the boundary: CPU/scheduler and concurrent synchronization work, not RAM.

This is a capacity probe, not a formal DWARF scenario result.

## Test boundary

- Host: `dwarf-host-a`, AMD Ryzen 7 6800U, 8 cores / 16 threads, 27.19 GiB RAM, 8 GiB swap.
- Amaru: `Amaru 10.10.0`.
- Image: `ghcr.io/lambdasistemi/amaru-bootstrap-producer:03d2727b71e8d1fe7c793d5036dce3c3ce294f6c`.
- Image digest: `sha256:02e9d88dc144ed86009ed82d57014cdde404cccb1e33b152515f7c5539b19630`.
- Topology: temporary Amaru relay/consumer processes attached to the existing `cardano-amaru-testnet` Docker network.
- Upstream: the existing honest `relay1.example:3001` Cardano relay.
- State: a private volume per Amaru process, initialized from the same `testnet_42` bundle used by the proven mixed deployment.
- Workload: concurrent forward synchronization and block application on the real local devnet.
- Existing services remained running throughout: `p1`, `p2`, `p3`, `relay1`, `relay2`, `amaru-relay-1`, `amaru-relay-2`, `amaru-consumer`, and `dwarf-fw`.

## Measurements

| Added Amaru nodes | Aggregate container memory | Mean memory per node | Aggregate container CPU | Host available RAM | One-minute load | Result |
| ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 20 | 1,019 MiB | 51.0 MiB | 61.6% | 22 GiB | 0.96 | All running and applying blocks |
| 50 | 2,563 MiB | 51.3 MiB | 172.6% | 20 GiB | 1.86 | All running and applying blocks |
| 100, early | 5,220 MiB | 52.2 MiB | 340.6% | 17 GiB | 4.39 | All running and applying blocks |
| 100, stabilized | 6,458 MiB | 64.6 MiB | 316.4% | 15 GiB | 7.68 | Tested operating level |
| 150 | 8,722 MiB | 58.2 MiB | 690.5% | 13 GiB | 17.65 | Above the 16-thread load guardrail |
| 200 | 10,793 MiB | 54.0 MiB | 1,055.2% | 10 GiB | 20.84 | Sustained overload; no further scaling |

Docker CPU percentages use 100% for one logical CPU. The values above therefore correspond to approximately 0.62, 1.73, 3.41, 3.16, 6.90, and 10.55 logical CPUs respectively at the sampled instants.

## Runtime evidence

- At 100 stabilized nodes, sampled ledger checkpoints advanced during a ten-second interval: `191→193`, `116→118`, `115→117`, `98→100`, and `96→98`.
- All temporary processes remained running at the 100-node checkpoint.
- No temporary process logged a panic, fatal signal, OOM, `EADDRINUSE`, segmentation fault, or assertion failure.
- Every temporary process logged one initial `timeout fetching blocks` message and subsequently advanced its ledger state. The timeout is therefore classified as a recovered startup signal for this probe, not a fatal result.
- Swap remained effectively unchanged throughout the sweep.
- The original DWARF and mixed-network containers remained running throughout the probe.

## What this supports

- `dwarf-host-a` can run at least 100 additional real Amaru relay/consumer processes under active local-devnet catch-up alongside the existing mixed deployment.
- Amaru's measured memory footprint in this test was far below the one-GiB-per-node planning assumption used by the older Cardano large-node scenarios.
- For this topology and workload, concurrent synchronization pressure reaches the CPU/scheduler boundary before RAM is exhausted.

## What this does not support

- A claim that 100 Amaru block producers can run on the host.
- A claim about mainnet or production-chain initial synchronization.
- A fully connected 100-node Amaru mesh.
- A long-duration leak, steady-state throughput, fault-recovery, or adversarial-capacity result.
- A precise absolute maximum. The interval between the tested 100-node operating level and the 150-node overload boundary was not exhaustively searched.
- A direct Cardano-node versus Amaru efficiency comparison. The existing Cardano capacity figures are planning envelopes rather than equivalent measured sweeps.

## Cleanup

All temporary containers and volumes with the `dwarf-amaru-cap-20260910` prefix were removed. The original DWARF mixed deployment remained running. After cleanup, the host returned to approximately 23 GiB available RAM with swap unchanged.
