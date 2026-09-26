# Native-script phase-1 differential family

A DWARF phase-1 differential family (extends `workload/mixed_phase1.py`) that submits
native-script violation and satisfied transactions to **both** cardano-node 11.1.2 and
Amaru, and applies a **verdict + reason-class parity** oracle. Coverage addition, not a
finding — a clean conformance pass that locks in Amaru native-script phase-1 behavior and
would catch a future regression or divergence.

## Result (2026-09-26)

**Amaru 10.11.20260903 (`ea1f34e4`) phase-1 native-script validation is CONFORMANT with
cardano-node 11.1.2 across `RequireAllOf` / `RequireMOf` (threshold) / nested /
`RequireTimeBefore` / `RequireTimeAfter`, including inclusive boundary semantics — verdict
+ reason-class parity, non-vacuous.**

| case | expected | cardano | amaru | parity |
|---|---|---|---|---|
| mintpolicy-sig-missing (`RequireAllOf[sig A]`, A omitted) | reject | `ScriptWitnessNotValidatingUTXOW(446bc0ec…)` | `native script(s) failed to validate [446bc0ec…]` | verdict + **same hash** |
| threshold-2of3-underfill (`RequireMOf 2/3`, 1 sig — N-1 boundary) | reject | `ScriptWitnessNotValidatingUTXOW(3c7f756a…)` | `native script(s) failed [3c7f756a…]` | verdict + same hash |
| nested-outer-missing (`All[Any[B,C], sig D]`, D omitted) | reject | `ScriptWitnessNotValidatingUTXOW(6255e58a…)` | `native script(s) failed [6255e58a…]` | verdict + same hash |
| timelock-before-violation (`before(S)`, invalidHereafter `S+1`) | reject | `ScriptWitnessNotValidatingUTXOW(5d09e310…)` | `native script(s) failed [5d09e310…]` | verdict + same hash |
| timelock-after-violation (`after(S)`, invalidBefore `S-1`) | reject | `ScriptWitnessNotValidatingUTXOW(592fb0f9…)` | `native script(s) failed [592fb0f9…]` | verdict + same hash |
| mintpolicy-sig-satisfied | accept | 202 | 202 | verdict |
| threshold-2of3-satisfied (2 sigs) | accept | 202 | 202 | verdict |
| nested-satisfied | accept | 202 | 202 | verdict |
| timelock-before-boundary (invalidHereafter `== S`) | accept | 202 | 202 | **inclusive, both** |
| timelock-after-boundary (invalidBefore `== S`) | accept | 202 | 202 | **inclusive, both** |

**Boundary semantics (the valuable part):** both implementations treat native-script
timelock boundaries **inclusively** — `invalidHereafter == before-lock` and
`invalidBefore == after-lock` are both accepted — and they agree. Boundary handling is
exactly where implementations tend to diverge, so recording the agreement is the point.

## Design

- All cases are **minting-policy** native scripts, so no pre-existing script-locked UTxO is
  needed in the frozen store; they all spend the committed funding UTxO
  `9708b921…#0` (see `fixture/funding/`).
- **Fund fee is 300000**, far above the ~174–180k min-fee for these tx sizes, so a
  rejection is unambiguously the **native-script rule** (`ScriptWitnessNotValidatingUTXOW`),
  not a fee/witness/size failure.
- **Violation** cases are idempotent (rejected → replay-safe): the repeatable oracle.
  **Satisfied/boundary** cases are **single-use** — an accept consumes the funding UTxO, so
  submit at most one per fresh mempool (restart the frozen reference + Amaru between
  accept-runs). Corpus is ordered violations-first for a clean single-pass violation run.
- Oracle: `workload/native_script_differential.py` reuses
  `mixed_phase1.observe_differential` (one transport + classification path across the
  phase-1 families) and adds `reason_parity` (both reject → both name the same script hash).

## Substrate caveats (reusable)

- **Frozen non-forging nodes use their LEDGER-TIP slot as "current slot" for
  validity-interval checks, not wall-clock.** The two frozen stores sit at slightly
  different tips (cardano reference **1298**, Amaru **1199** — the reference replays the
  immutable DB to 1298 while Amaru bootstraps the epoch-3 boundary snapshot at 1199).
  Therefore: keep timelock validity-interval slots **≤ both tips**, and keep `invalidBefore
  ≤ 1199`, or you get (a) `OutsideValidityIntervalUTxO` masking the script rule (both nodes,
  before the script runs) or (b) a spurious tip-driven verdict split for slots between 1199
  and 1298. The first "after" attempt (lock 2000 > tips) was validity-masked; it was re-run
  at lock 1000 for a clean script-boundary test.
- **Amaru 0918 emits verbose phase-1 errors** ("transaction … is invalid: transaction
  failed phase one validation: …"), unlike 807's coarse "transaction … is invalid". The
  `mixed_phase1` classifier gained a `"phase one validation"` marker so it recognizes the
  0918 form (fees, native-script, and validity-interval rejects) as `phase1_reject`; the
  807 coarse form is still handled by `_AMARU_VALIDATION_RE`.

## Reproduce

Bring up the frozen substrate (`prepare-rebake.sh`), then:

```
docker restart <cardano-ref> <amaru>          # fresh mempools (accepts are single-use)
cd workload && python3 native_script_differential.py \
  --amaru http://localhost:3012/api/submit/tx --cardano http://localhost:8090/api/submit/tx
# -> "VIOLATION VERDICT+REASON PARITY (5 cases): ALL AGREE"
```

Rebuild a case: `cardano-cli conway transaction build-raw --tx-in 9708b921…#0
--tx-out "<addr>+<total-fee>+1 <policyId>.4457415246" --mint "1 <policyId>.4457415246"
--mint-script-file policies/<policy>.json [--invalid-before|--invalid-hereafter <slot>]
--fee 300000` then `sign` with the committed `payment.skey` (plus the policy keys for a
satisfied control). Policy scripts + all fixtures are under `fixture/native_script/`.


## Re-validated against LATEST amaru — v10.11.20260925 (eaf8ac3f), 2026-09-26

Re-ran this family against the current tagged latest amaru (git_commit eaf8ac3f) via `node run` on the 0903-bootstrapped store (store format compatible; latest cannot freshly bootstrap a custom testnet — see amaru-custom-testnet-bootstrap-regression-0903-to-0925.md), vs cardano-node 11.1.2: **still CONFORMANT — all cases agree, no divergence.** Result is now current-version-validated.
