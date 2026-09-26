# Finding note — operational-certificate / KES soak campaign (cardano-node + Amaru)

**From:** DWARF opcert soak + differential campaign (Pragma) · **Date:** 2026-09-25
**Scope:** extend the deterministic opcert header-validation corpus
(`dwarf/docs/finding-opcert-header-validation.md`) into randomised, long-running soak
families that forge many mutated operational-certificate / KES block headers per run and
compare each live node's accept/reject verdict — and, on the shared mixed devnet, the
cardano-node-vs-Amaru verdict — against a per-case expected table. Targets are cardano-node
11.1.2 and Amaru 10.11.20260918 on the standard devnet profiles (profile-v cardano-only,
profile-zb mixed). Three new families (rules-differential, cross-pool, kes-evolution) were
added on top of the original encoding / kes-period / restart-persistence soaks.

## Headline

**Three cross-implementation cardano-node-vs-Amaru findings across the opcert surface — one is a
real availability risk.** Aside from the crash below, the mixed differential soaks recorded zero
verdict (accept/reject) disagreements on every conclusive attempt, and every cardano-only rejection
family fired the expected `*OCERT` reason. The three findings:

1. **Non-canonical-CBOR node crash / DoS — HEADLINE, real risk** (`finding-amaru-noncanonical-cbor-crash.md`).
   A valid live-tip Praos header re-encoded with non-canonical CBOR (same logical header, different
   bytes) **crashes an Amaru 10.11.20260918 node** (chain-store integrity panic,
   `types.rs:88:9` — Amaru keys stored headers by the raw-wire hash but integrity-checks the
   canonical hash) while cardano-node 11.1.2 **accepts** it. Remotely triggerable via ChainSync;
   deterministic, reproduced 4×. Availability/DoS-class (no consensus split observed).
2. **Validation-precedence divergence — reported-reason only** (`finding-opcert-precedence-divergence.md`).
   On a header breaking two opcert rules at once, both nodes reject but report **different rules**
   (Amaru checks counter first, cardano-node last). Behavioural/diagnostic, not a consensus risk.
3. **Opcert-field decode-leniency — decoder-strictness, severity-bounded** (`finding-opcert-field-decode-leniency.md`).
   Amaru's header decoder accepts a truncated hot-vkey / cold-sig / an extra opcert field that
   cardano-node rejects at decode. Decode-stage; validation-stage bounded (see that note).

Findings 2–3 are reported-reason/decoder behavioural divergences; finding 1 is the genuine risk.

## Original soak families

| run id | scenario | family | iterations | conclusive | inconclusive | disagree / mismatch |
|---|---|---|---|---|---|---|
| `20260925T064711Z-65d12bb3` | `opcert-soak-encoding-mixed-1112-amaru-20260918` | encoding-form (mixed differential) | 98 | 73 | 25 | 0 / 0 |
| `20260925T091246Z-56747e3a` | `opcert-soak-kes-period-mixed-1112-amaru-20260918` | kes-period-differential (mixed) | 554 | 545 | 9 | 0 / 0 |
| `20260925T140303Z-6aff099c` | `opcert-soak-restart-persistence-cardano-1112` | restart-persistence (cardano-only) | 32 | 32 | 0 | 0 / 0 |

- **A — encoding-form (mixed).** ⚠️ **The original run's "73 agree / 0 disagree" was PRE-FIX
  VACUOUS for header-content parity.** Byte evidence from that run (`20260925T064711Z-65d12bb3`,
  canonical wrapped size 858): 4 of 7 forms (definite-array, duplicate-map-key, extra-map-key,
  missing-optional-key) served **byte-identical canonical** headers (no-op), 2 forms
  (indefinite-array, noncanonical-int) deviated only the **outer NodeToNode envelope**, and only
  trailing-bytes changed the header stream — because `reEncodeOpcert` could not descend into the
  CBOR-in-CBOR wire wrapper (fixed in `d865648`). A's forms that are applicable to a real Praos
  header are **indefinite-array, noncanonical-int, and trailing-bytes** (the map / definite-array
  forms have no matching node in a real header's canonical encoding). The **fixed** family A
  (post-`d865648`) is what surfaced the non-canonical-CBOR crash — finding 1 above
  (`dwarf/docs/finding-amaru-noncanonical-cbor-crash.md`). Post-fix, A's applicable forms either **crash Amaru**
  (noncanonical-int / indefinite-array — the crash finding) or are **trivially handled**
  (trailing-bytes), so there is no separate A parity figure to report; the crash is A's
  headline result.
- **D — kes-period-differential (mixed).** The largest retained soak: 554 iterations,
  545 conclusive, `counters.agree 545 / disagree 0 / mismatch 0`, `disagreements: []`.
  Mixed target progressed (tip block height 584 -> 2728) over the ~90-minute run.
- **C — restart-persistence (cardano-only).** 32/32 conclusive pass, `mismatch 0`; exercises
  opcert-state persistence across a node restart on cardano-node 11.1.2.

Inconclusive attempts are cases where one node did not return a usable verdict within the
window (e.g. the mutated header was never selected); DWARF counts them as inconclusive rather
than substituting a pass, and they are excluded from the agree/disagree tally.

## New family #1 — rules-differential (mixed, reason-parity + magnitude sweep)

Run `resmoke-rulesdiff` (`opcert-soak-rules-differential-mixed-1112-amaru-20260918`,
profile-zb mixed): each iteration forges one opcert rule violation and checks that
cardano-node and Amaru **both** reject, recording each node's own reason string and testing
for reason parity against the expected table.

- `result.json`: `iterations 30, conclusive 27, inconclusive 3, counters.agree 27,
  disagree 0, mismatch 0, reason_mismatches []`, `disagreements []`.
- Verdict combos over the 30 attempts: `(rejected, rejected) x27`, `(inconclusive, rejected) x3`.
- Rules exercised: cold-key-unauthorized (9), kes-before-window (8), counter-jump (7),
  hot-key-mismatch (6).
- **Magnitude sweep** (confirms rejection is not a single-point artefact): counter-jump
  `counter_jump ∈ {2, 3, 13, 50, 250}` and kes-before-window `kes_periods_ahead ∈ {1, 4, 9, 25, 100}`.
- Reason vocabularies differ but both are correct rejections, e.g. kes-before-window →
  cardano-node `KESBeforeStartOCERT` / Amaru `OpCertKesPeriodTooLarge`; the harness treats
  these as an expected reason pair, so `reason_mismatches` stays empty.

## New family #2 — cross-pool confusion (cardano-only)

Run `resmoke-crosspool` (`opcert-soak-crosspool-cardano-1112`, profile-v): forge headers
whose opcert is authorised by a **foreign** pool's cold key (variant
`foreign-cold-authorization`) and confirm cardano-node rejects.

- `result.json`: `iterations 23, conclusive 23, counters.pass 23, mismatch 0`,
  `disagreements []`.
- Every attempt: `observed_verdict rejected`, `observed_reason InvalidSignatureOCERT` (23/23),
  matching the expected reason (`cardano-node: InvalidSignatureOCERT`, `amaru: InvalidSignature`).

## New family #3 — kes-evolution (cardano-only)

Runs `resmoke-kesevo` (seed 7) and `resmoke-kesevo-s2` (seed 2)
(`opcert-soak-kesevo-cardano-1112`, profile-v): forge a header whose KES signature was
evolved to the wrong step for its slot's KES period (`kes_evolution_delta`).

- **Over-evolution (delta > 0) rejects.** In `resmoke-kesevo-s2`, all five delta ∈ {+1, +2}
  attempts were `rejected` with `observed_reason InvalidKesSignatureOCERT`
  (`result.json`: `iterations 6, conclusive 5, pass 5, inconclusive 1`).
- **Under-evolution (delta < 0) is not reachable on a fresh devnet.** delta ∈ {-1, -2}
  attempts came back `inconclusive` (`resmoke-kesevo`: `iterations 2, conclusive 0,
  inconclusive 2`; the single delta -2 in s2 also inconclusive). A KES key cannot be
  de-evolved below its current on-disk step early in a run, so under-evolution needs an
  **aged devnet** (chain advanced several KES periods) to exercise — same aging constraint
  documented for counter-behind / kes-after-window in the deterministic finding.

## Cross-implementation agreement (the headline, in numbers)

| mixed differential soak | conclusive | disagreements | reason mismatches |
|---|---|---|---|
| encoding-form (A) | 73 | 0 | n/a (accept path) |
| kes-period (D) | 545 | 0 | 0 |
| rules-differential (#1) | 27 | 0 | 0 |
| **total mixed** | **645** | **0** | **0** |

No mixed soak produced a single case where cardano-node accepted a header Amaru rejected (or
vice-versa). The subsequently-added **error-precedence** differential family (family #4,
`dwarf/docs/finding-opcert-precedence-divergence.md`) likewise produced **0 verdict
disagreements** — but it is the one family that surfaced a **reported-reason (validation-order)
divergence**: on a header that breaks two opcert rules at once, the two nodes reject on a
different rule (Amaru checks counter-monotonicity first, cardano-node last). That is the
campaign's single divergence, and it is diagnostic/behavioural, not a consensus split (both
nodes reject). The cardano-only families (cross-pool, kes-evolution over-evolution,
restart-persistence) each rejected / passed exactly as expected with the correct `*OCERT`
reason; they are cardano-only because Amaru cannot be aged via a custom short-KES genesis
(see the aging sub-finding in `dwarf/docs/finding-opcert-header-validation.md`).

## Non-soakable control

`opcert-soak-accept-boundary-{cardano-1112,amaru-20260918}` is a positive-control /
boundary-acceptance scenario, not a randomised soak; it is referenced in the coverage map
under header-validation but **excluded from the campaign soak loop** (it forges a single
correctly-signed boundary header rather than a mutation stream). It has no soak run cited here.

## Cited runs and retention

- `20260925T064711Z-65d12bb3` — encoding-form mixed soak (A).
  `outputs/opcert-soak/attempts.ndjson` (98 lines; no top-level `result.json` was written for
  this run — counters recomputed from the retained attempts: 73 agree / 0 disagree / 25 inconclusive).
- `20260925T091246Z-56747e3a` — kes-period mixed soak (D). `outputs/opcert-soak/result.json`.
- `20260925T140303Z-6aff099c` — restart-persistence cardano soak (C). `outputs/opcert-soak/result.json`.
- `resmoke-rulesdiff` — rules-differential mixed smoke (#1). `out/result.json`, `out/attempts.ndjson`.
- `resmoke-crosspool` — cross-pool cardano smoke (#2). `out/result.json`, `out/attempts.ndjson`.
- `resmoke-kesevo`, `resmoke-kesevo-s2` — kes-evolution cardano smokes (#3). `out/result.json`, `out/attempts.ndjson`.

All numbers above are copied from the retained `result.json` / `attempts.ndjson` under
`/home/nigel/.local/share/dwarf/runs/`. The `resmoke-*` runs are bounded smoke validations of
the three new families; the full-length mixed soaks (A, D) and the cardano restart soak (C)
are the retained long-run evidence.
