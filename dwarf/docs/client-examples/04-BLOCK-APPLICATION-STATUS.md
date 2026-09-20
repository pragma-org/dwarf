# Card 04 block application status

Status: The Cardano-node leg is accepted. The Amaru leg is blocked by a retained frozen-contract finding.

Child explanation: The Cardano node added and measured enough real blocks. The Amaru node changed between two valid chains at the same height, so the frozen rule rejected its run.

The accepted Cardano-node run is `20260920T125840Z-778a7cf7` on `nanoseconds-v2`. It passed 4/4 assertions, retained 80 adopted block identities, correlated 80 exact application timings, and retained 180 resource samples in the controlled window. Its exported bundle SHA-256 is `1e03242b941f000db4487ba36566d890202e4eee5758abacd9d58daad38701d2`.

The Amaru rehearsal `20260920T114304Z-a8ac5c5e` reached the exact production topology but observed real same-height fork switches. The frozen monotonic-height requirement cannot accept that behavior without a contract or topology decision. See `findings/03-frozen-amaru-block-application-forks.md`.

Exact Cardano evidence and claim limits are in `04-CARDANO-BLOCK-APPLICATION-PROOF.md`. This card remains a partial client requirement. It is not an implementation performance comparison.
