# Evidence — opcert validation-precedence divergence (cardano-node vs Amaru)

Supporting evidence for `dwarf/docs/finding-opcert-precedence-divergence.md`: the one
cross-implementation divergence found in the opcert soak campaign. When a header breaks TWO
operational-certificate rules at once, cardano-node 11.1.2 and Amaru 10.11.20260918 **reject**
it (verdicts agree, no consensus split) but report **different rules**, because they validate
opcert rules in a different order.

Both runs used the `error-precedence` differential family on the mixed devnet (profile-zb,
`node1` cardano-node + `amaru-relay-1` Amaru), seed `20260925`. They are driver-direct smokes,
so there is no formal run-id manifest; the records below are extracted from each run's
`out/result.json`, `out/attempts.ndjson`, and `out/harness/evidence-persistent-*.ndjson`.

## Files

Per run (`resmoke-errorprec` = primary, 6 mismatches; `confirm-errorprec` = replay, 3 reproduced):

- `<run>-counters.json` — family/seed/iterations/conclusive/inconclusive/pass + `counters`.
- `<run>-reason-mismatches.json` — the full `reason_mismatches[]` records: per divergent
  iteration, `spec.params.rules` (the 2-rule combo), `reasons{node1, amaru-relay-1}`, and
  `canonical_a/canonical_b` (the canonical rule each node reported first).
- `<run>-precedence-summary.json` — output of
  `opcert_soak_result.summarize_precedence(result)`: the per-combo table and the per-node
  precedence edges (which rule each node ranks above which).
- `<run>-served-case-evidence.json` — the forger `opcert_case_served` lines for the divergent
  iterations, for BOTH nodes, showing the **same header hash** was served to each node (so the
  differing reasons are a validator-ordering property, not different inputs).

## Headline numbers

| run | iterations | conclusive | disagreements | reason_mismatches |
|---|---|---|---|---|
| resmoke-errorprec | 16 | 13 | 0 | 6 |
| confirm-errorprec | 10 | 8 | 0 | 3 |

## Reconstructed check orders (first → last)

- cardano-node 11.1.2: kes-before-window → cold-key → hot-key → counter-jump
- Amaru 10.11.20260918: counter-jump → cold-key → kes-before-window → hot-key

Deterministic (`(family, seed, iteration)`); reproduced bit-for-bit across the two runs.
Not a consensus risk — both nodes reject every double-violation header.
