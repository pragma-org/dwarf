# Card 05 restart, recovery, and synchronization status

Status: Both implementation legs have accepted evidence on `nanoseconds-v2`.

Child explanation: DWARF restarted each real node. Each node opened its listener, moved its chain, found the required peer, and caught up again.

- Amaru run: `20260921T045619Z-15e864a0`
- Cardano-node run: `20260921T045807Z-8e2bbb0e`

Each run passed all four security assertions and retained ordered restart-readiness gates, a controlled synchronization range, target health, resource evidence across the process change, and an exportable bundle. All restart and synchronization events now use the same run-relative monotonic clock as the phase markers.

The Amaru run advanced from block 702 to block 707. It retained 40 restart-window resource samples and reported no sampler errors. Its bundle SHA-256 is `d0b0bce35254094636c83e59d688bf7be894c9bc0da9fae99f1eda2089e22217`. The Cardano-node run advanced from block 28 to block 33. It retained 173 restart-window resource samples and reported no sampler errors. Its bundle SHA-256 is `0cf0d4e2d6eeb49a82c3b3053c2b2a5b0eb39e37c8489301df76b06a2a48cfce`.

The earlier Card 05 collection runs remain retained. The accepted contract points to the corrected clock-domain runs above.

Implementation-specific evidence and claim limits are in `05-AMARU-RESTART-RECOVERY-PROOF.md` and `05-CARDANO-RESTART-RECOVERY-PROOF.md`.

These runs prove one controlled restart and recovery per implementation in this environment. They do not establish stable restart or synchronization thresholds, and they are not a mixed-node comparison.
