# Independent 2× verification — Amaru string-builtin step under-charge

Independent confirmation (on **pair2**, distinct from fd's pair1) of fd's finding `cb79184`:
*amaru under-charges the Plutus string builtins, so a string-heavy phase-2 transaction is judged
`is_valid` by amaru where cardano-node rejects it for exceeding its execution-unit budget.* This
is a **consensus-validity divergence** (MEDIUM-HIGH): the two nodes disagree on whether the same
transaction is valid.

## Method (knife-edge cost differential)

Validator = fd's `string_amp` (`fixture/bls/validators/strcost.ak`): `decode_utf8(redeemer)` →
`append_string` ×10 → `equals_string`. The redeemer supplies the string at runtime so aiken cannot
constant-fold; the script always evaluates to `True`, so the **only** thing that can make it fail
phase-2 is the declared ex-unit budget being below the VM's metered cost.

1. Build the mint tx with ample ex-units; measure cardano's exact cost with
   `cardano-cli conway transaction calculate-plutus-script-cost online` against the pair2 cardano
   socket.
2. Declare that exact budget → submit to both (control).
3. Declare `(steps − 1, mem)` → submit to both (the split).

## Result (2026-09-27, cardano-node 11.1.2 fef83fed / amaru 0925 eaf8ac3f, pair2)

Cardano EXACT: **steps = 149,561,297**, mem = 16,284.

| trial | declared (steps, mem) | amaru | cardano |
|---|---|---|---|
| control | (149,561,297, 16,284) | **202 accept** | **202 accept** (same tx id `885d6eea…`) |
| split 1 | (149,561,296, 16,284) | **202 accept** | **reject** — ValidationTagMismatch Phase2Valid FailedUnexpectedly |
| split 2 | (149,561,296, 16,284) | **202 accept** | **reject** — same |

Binary search of amaru's accept boundary (mem fixed at cardano-exact 16,284) → amaru's true metered
**steps ≈ 40,031,500**. So amaru charges **26.8%** of cardano's steps for this append-dominated
script — a **73.2% step under-charge**. Any declared step budget in the open interval
**(40,031,500, 149,561,297)** is accepted by amaru and rejected by cardano: an `is_valid` split.

The mem axis agreed here (16,284 on both); the divergence for `append_string` is on **steps**.
fd's root-cause anchor is `value.rs:51 string_ex_mem = len/4`. This validator's append-dominated mix
exposes a larger step gap than fd's ~54% figure. Reproduce: `workload/plutus_cost_knife_edge.sh`
(fd's harness) or the pair2 driver noted in the evidence JSON.
