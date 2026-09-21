# Card 04 block application status

Status: Complete. The Cardano-node canonical-v2 leg and the Amaru canonical-v3 leg are accepted.

Child explanation: Both nodes added enough real blocks. DWARF kept every chain turn, checked that each node settled and kept moving, and matched the timer samples to the measured block window.

The accepted Cardano-node run is `20260921T021935Z-3b58eafc` on `nanoseconds-v2`. It passed 4/4 assertions, advanced 88 blocks, retained 88 adopted block identities, and correlated 88 exact application timings. It had no fork or rollback event, panic, fatal exit, OOM, or restart. Its verified bundle SHA-256 is `c02a192f88cf9dc1caa6d9e3aea56a42c9436186e3596533cda19af7ab32af28`.

The accepted Amaru run is `20260921T035546Z-9747122c` on additive revision `nanoseconds-v3`. It passed 4/4 assertions, advanced 69 blocks, retained 56 adopted identities, and correlated 56 application timings. It kept 16 explicit fork or rollback events. One bounded same-height switch was followed by 36 stable advances. The target had no panic, fatal exit, OOM, or restart.

Every accepted Amaru proof sample keeps integer `elapsed_nanos` and exact fractional microseconds. The standard report contains 69 precise block-application samples. The minimum is 72.319 microseconds, the median is 136.551 microseconds, and the maximum is 347.806 microseconds. Its verified 81-file bundle SHA-256 is `f17b4892ca45d003a94b7c175d510cbd0890420bdf79420a46ed4dff79c0c808`.

The old strict-monotonic runs and the first canonical-v2 rehearsal remain unchanged. They are historical evidence, not accepted Card 04 evidence. The accepted rule keeps raw chain-selection evidence and evaluates bounded canonical progress and final convergence. A bounded same-height switch alone does not fail.

Exact evidence and claim limits are in `04-CARDANO-BLOCK-APPLICATION-PROOF.md`. This card proves collection on two separate local-devnet executions. It is not an Amaru-versus-Cardano performance comparison.
