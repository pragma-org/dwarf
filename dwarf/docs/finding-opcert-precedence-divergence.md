# Finding note — operational-certificate validation-precedence divergence (cardano-node vs Amaru)

**From:** DWARF opcert soak campaign, error-precedence family (Pragma) · **Date:** 2026-09-25
**Scope:** the differential (mixed devnet) `error-precedence` opcert soak family
(`dwarf/scenarios/opcert-soak-error-precedence-mixed-1112-amaru-20260918.yaml`) forges a single
block header that violates **two** operational-certificate rules at once and records which rule
each live node reports, then checks whether cardano-node 11.1.2 (`fef83fed`) and Amaru
10.11.20260918 (`aedfe797`) **agree on that precedence**. This is the one cross-implementation
divergence found in the whole opcert soak + differential campaign
(`dwarf/docs/finding-opcert-soak-campaign.md`).

## Headline

**cardano-node and Amaru validate operational-certificate rules in a DIFFERENT ORDER.** When a
header breaks two opcert rules simultaneously, each implementation rejects on whichever rule its
validator checks first — and the two implementations do not check them in the same order. This
is a **reported-reason / validation-order divergence only, NOT a consensus or safety risk**: both
nodes **reject** every such header (verdicts agree, 0 disagreements), so no node accepts a block
the other rejects. Only the *reported rejection reason* differs.

The divergence is **deterministic and reproduces bit-for-bit** — it was found in one run
(`resmoke-errorprec`, 6 reason mismatches over 13 conclusive iterations) and reproduced in a
second, independent replay (`confirm-errorprec`, 3 of the same combos in a shorter window). The
family generator is `(family, seed, iteration)`-deterministic, so every combo is replayable by
construction.

## The two check orders (first-reported = highest precedence)

Reconstructed from the divergences (see `summarize_precedence` and the evidence bundle):

| implementation | operational-certificate check order (first → last) |
|---|---|
| **cardano-node 11.1.2** | kes-before-window → cold-key → hot-key → **counter-jump (last)** |
| **Amaru 10.11.20260918** | **counter-jump (first)** → cold-key → kes-before-window → hot-key |

Two inversions between the implementations:

1. **counter position.** Amaru checks counter monotonicity **first**; cardano-node checks it
   **last**. Any combo that pairs `counter-jump` with another rule therefore diverges —
   cardano-node reports the co-broken rule, Amaru reports `SequenceNumberTooFarAhead`
   (canonical `counter-too-large`).
2. **cold-key vs KES-window.** Amaru checks the cold-key (issuer) signature **before** the KES
   start-period window; cardano-node checks the KES window first. So `cold-key + kes-before-window`
   diverges — cardano-node reports `KESBeforeStartOCERT`, Amaru reports `InvalidSignature`
   (canonical `cold-key-unauthorized`).

## Divergence table (per 2-rule combo)

Of the six combos of the four fresh-devnet-reachable rules (cold-key-unauthorized, counter-jump,
kes-before-window, hot-key-mismatch), **four diverge** and **two agree**:

| combo | cardano-node reports | Amaru reports | result |
|---|---|---|---|
| cold-key + counter-jump | `InvalidSignatureOCERT` (cold-key) | `SequenceNumberTooFarAhead` (counter-too-large) | **DIVERGE** |
| counter-jump + kes-before-window | `KESBeforeStartOCERT` (kes-before-window) | `SequenceNumberTooFarAhead` (counter-too-large) | **DIVERGE** |
| counter-jump + hot-key-mismatch | `InvalidKesSignatureOCERT` (hot-key-mismatch) | `SequenceNumberTooFarAhead` (counter-too-large) | **DIVERGE** |
| cold-key + kes-before-window | `KESBeforeStartOCERT` (kes-before-window) | `InvalidSignature` (cold-key-unauthorized) | **DIVERGE** |
| cold-key + hot-key-mismatch | cold-key | cold-key | agree (both rank cold-key above hot-key) |
| kes-before-window + hot-key-mismatch | kes-before-window | kes-before-window | agree (both rank kes-before-window above hot-key) |

The two non-diverging combos agree because cold-key and kes-before-window both sit **ahead of**
hot-key in *both* implementations' orders — the disagreements are confined to counter position and
the cold-key-vs-KES pair.

## Why this is not a consensus risk

Both implementations **reject** every double-violation header — the finding is not that one node
accepts what the other rejects (that would be a chain-split / safety issue), but that the two
nodes attribute the rejection to a **different rule**. Across `resmoke-errorprec` +
`confirm-errorprec`: 0 verdict disagreements, both nodes `rejected` on every conclusive attempt.
A block that is invalid stays invalid on both nodes; only operator-facing diagnostics /
error-reporting differ. This matters for tooling, dashboards, and any logic that parses a node's
specific rejection reason, but it does not affect which chain either node follows.

Note also that the forger serves the **identical header bytes** (same header hash) to both nodes
each iteration (see the served-case evidence — e.g. iteration 0 hash `b0057f11…` recorded for
both `node1` and `amaru-relay-1`), so the differing reasons are purely a validator-ordering
property, not an artefact of serving different inputs.

## Run evidence

| run | family | seed | iterations | conclusive | disagreements | reason_mismatches |
|---|---|---|---|---|---|---|
| `resmoke-errorprec` | error-precedence (mixed) | 20260925 | 16 | 13 | 0 | **6** |
| `confirm-errorprec` (replay) | error-precedence (mixed) | 20260925 | 10 | 8 | 0 | **3** |

- `resmoke-errorprec` reason mismatches at iterations 0, 5, 9, 10, 13, 14 (the four diverging
  combos above; `counter-jump + kes-before-window` recurred 3×).
- `confirm-errorprec` reproduced iterations 0, 5, 6 (cold-key+counter and counter+kes) bit-for-bit
  in its shorter ~500 s window; the un-reached combos are replayable from the same seed by
  construction (`generate_case("error-precedence", 20260925, i)`).
- Both runs: `counters.disagree 0`, `mismatch 0` (no expected-table mismatch — this family has no
  predicted per-node reason; the oracle is agreement), the divergence lives entirely in
  `reason_mismatches`.

Extracted records, counters, per-run precedence summaries, and the served-case (identical-hash)
evidence are in `reports/opcert-precedence-divergence-evidence/`.

## Reproduce / characterize

- Scenario: `dwarf/scenarios/opcert-soak-error-precedence-mixed-1112-amaru-20260918.yaml`
  (differential mixed, `node1` + `amaru-relay-1`, seed `0xE44090EC` / `444090909`; assertions
  `opcert_soak_invariant_holds`, `opcert_soak_verdicts_agree`, `opcert_soak_reasons_agree`).
- Generator: `dwarf/scripts/opcert_soak_families.py` `error-precedence` family — seed-deterministic
  2-rule combos + swept magnitudes; the forger (`applyErrorPrecedence`) stacks both mutations into
  one header.
- Finding table (programmatic): `opcert_soak_result.summarize_precedence(result)` builds the
  per-combo table and the per-node precedence edges directly from a run's `result.json`
  (`reason_mismatches`), no re-run needed. Covered by `tests/test_summarize_precedence.py`.

## Recommendation

Report to the Amaru maintainers as a **behavioural / diagnostics parity** item, not a consensus
bug: when multiple opcert rules are violated, Amaru and cardano-node surface different rejection
reasons because they validate the rules in a different order (notably counter-monotonicity first
in Amaru vs last in cardano-node). Aligning the check order — or documenting the intended order —
would make cross-implementation rejection diagnostics consistent. No safety action is required:
both nodes correctly reject every invalid header.
