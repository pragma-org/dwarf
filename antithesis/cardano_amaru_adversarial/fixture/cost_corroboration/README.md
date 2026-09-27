# Cost-model corroboration sweep (empirical cross-check of fd source-diff d2bf074)

Third leg of the cost-model coverage (fd source-diff + this empirical sweep + the independent 2×
on the string finding). For each builtin family a redeemer-driven validator folds that builtin many
times so it dominates cost and aiken cannot const-fold; the script always evaluates `True`, so the
only reject cause is the declared ex-unit budget. Per family: measure cardano's EXACT cost via
`cardano-cli conway transaction calculate-plutus-script-cost`, then submit at exact (both accept),
at `steps−1`, and at `mem−1`. A conformant family rejects on BOTH nodes at −1; an under-charging
family accepts on amaru where cardano rejects.

**Result (2026-09-27, pair2, cardano-node 11.1.2 fef83fed / amaru 0925 eaf8ac3f): 11/11 families
AGREE (conformant).** `add`, `mul`, `index`, `cons`, `slice`, `serialiseData`, `equalsData`,
`blake2b_256`, `sha2_256`, `keccak_256` (+ a no-builtin baseline) all meter identically on both the
steps and mem axes at the knife-edge — accept at cardano-exact, reject at `steps−1` and `mem−1` on
both nodes.

This matches fd's source-diff: the per-builtin coefficients are the shared pparams cost model
(identical), and the Integer / ByteString / Data / Hashing size measures are conformant. The one
divergent family, String, is filed (`cb79184`) and independently 2×-verified in
`../cost_string_verify` (73.2% step under-charge). Crypto `verify*` and BLS cost are covered by
fd's source-diff (conformant) plus the functional crypto family (`6cfe3da`, 22/22 AGREE); they are
not folded-swept here because valid in-script crypto vectors are fragile to fold.

Reproduce: `pair2_sweep.sh` (builds each `cost.*` validator, measures, knife-edge probes with a
full reset between). `validators.ak` is the aiken source; `redeemers/` holds the fold inputs.
