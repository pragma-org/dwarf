# Card 05 restart, recovery, and synchronization status

Status: Both implementation legs have accepted evidence on `nanoseconds-v2`.

Child explanation: DWARF restarted each real node. Each node opened its listener, moved its chain, found the required peer, and caught up again.

- Amaru run: `20260920T122606Z-32c0e998`
- Cardano-node run: `20260920T130232Z-2c68fb83`

Each run passed all four security assertions and retained ordered restart-readiness gates, a controlled synchronization range, target health, resource evidence across the process change, and an exportable bundle. The Cardano-node run advanced from block 862 to block 867, retained 156 restart-window resource samples and 30 controlled-sync samples, followed two process IDs, and reported zero sampler errors. Its bundle SHA-256 is `53e0d13fddbe44aec99381c0eed6594f0ee18726cb45c5e2a8bebcb0ec581f2a`.

Implementation-specific evidence and claim limits are in `05-AMARU-RESTART-RECOVERY-PROOF.md` and `05-CARDANO-RESTART-RECOVERY-PROOF.md`.

These runs prove one controlled restart and recovery per implementation in this environment. They do not establish stable restart or synchronization thresholds, and they are not a mixed-node comparison.
