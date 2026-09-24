# Opcert derived randomized differential soaks — design

## Scope

Turn the deterministic operational-certificate (opcert) header-case runs
(`runtime_opcert_header_cases`, four scenarios, fixed 5–8 case rows) into
randomized 3-hour differential **soaks**. This delivers the "generated" case
family that `docs/plans/2026-09-23-opcert-header-validation-design.md` deferred
("Structured randomization within families 1–3; never raw random bytes").

The unit of work is a **soak**: a seeded loop that, for one *family*, generates
a fresh randomized case each iteration, serves it to the target(s) through the
existing opcert forger, reads the verdict(s) through the existing parser, checks
a per-family **invariant**, and keeps going until a wall-clock time budget
(default 10800s = 3h) is spent. Every iteration is appended to `attempts.ndjson`;
`result.json` carries the counters and the seed-derived parameters needed to
reproduce any failing iteration.

Randomization is **structured, within a family only**. The generator never
emits raw random header bytes: raw-byte header fuzzing never reaches opcert
logic and is already covered by the CBOR fuzzers (`dwarf/scripts/fuzz*`), so it
is an explicit out-of-scope boundary here.

This is an **extension of the existing opcert substrate**, not a parallel tool
(hard rule: extend DWARF primitives/forger/parser, never reinvent). The soak
mode is a sibling primitive to `runtime_opcert_header_cases` and reuses its
substrate detection, forger invocation, log parser, and fail-closed join.

## Reuse vs. extend (what is shared, what is new)

Reused unchanged, imported directly:

- **Substrate detection** — `runtime_opcert_header_cases._load_runtime`, the
  `cardano_amaru_relay_bootstrap_control` branch, `_amaru_project_ip`,
  `_amaru_extract_keys`, `_host_env_dir`, `_container_image`, `_docker`,
  `_container_state`, `_cardano_tip`, `_consumer_topology`, and the isolated-
  consumer lifecycle (fresh-DB cardano consumer / cloned amaru consumer).
- **Forger invocation** — the `serve-case` subprocess loop of the
  `dwarf-opcert-adversary-latest` peer (evidence ndjson → `served_hash_by_case`).
- **Parser** — `scripts.header_validation_parse.parse_cardano_header_events`,
  `parse_amaru_header_events`, `amaru_reason`, `verdict_by_hash`.
- **Fail-closed join semantics** — the exact accept/reject/reason logic of
  `join_cases` (accept ⇒ verdict==accepted; reject ⇒ verdict==rejected AND
  reason matches; never-served or never-observed ⇒ inconclusive, never pass).
- **`target_progress_continues`** assertion, unchanged.

Extended (new code, all under the existing components):

- **Forger (`serve-case`)** gains a deterministic `--case-spec FILE` override
  (seed-derived JSON) plus one new `encoding-form` CBOR re-encoder code path.
  This is an extension of the *one* forger, not a new binary.
- **Soak driver** `dwarf/scripts/runtime_opcert_header_soak.py` — the time-
  budget loop, per-iteration invariant, counters, and result assembly.
- **Family generators** `dwarf/scripts/opcert_soak_families.py` — seed-
  deterministic case generation for the four families.
- **Primitive** `runtime_opcert_header_soak` (sibling `LoadPrimitive`).
- **Assertions** `opcert_soak_invariant_holds`, `opcert_soak_verdicts_agree`.
- **Scenarios** — the six-cell run matrix.

## Soak mode (`runtime_opcert_header_soak`)

Parameters: `family`, `seed`, `time_budget_seconds` (default 10800),
`profile_id`, `target_node` (single-target families) or `target_nodes` (the two
compared nodes, differential families), `output_dir`.

Loop until `time.monotonic()` exceeds the budget:

1. **Generate** — `opcert_soak_families.generate_case(family, seed, iteration)`
   returns a `SoakCaseSpec` (seed+iteration ⇒ deterministic; replayable).
2. **Serve** — write the spec to a per-iteration `case-spec-<n>.json`, invoke the
   forger `serve-case --case-spec <file> …` against each target's isolated
   consumer (reusing the driver's substrate-specific consumer wiring).
3. **Parse** — read the consumer's logs since the iteration start through the
   existing parser; `verdict_by_hash` keyed on the forger's served header hash.
4. **Evaluate the family invariant** — see below.
5. **Record every iteration** — append one line to `attempts.ndjson` and update
   counters: `pass` / `mismatch` / `inconclusive` for all families, plus
   `agree` / `disagree` for differential families.

On an invariant violation, record it as a finding signal (append the full
seed-derived spec) and **keep going** to gather more instances, but mark the run
failed. Fail-closed:

- Inconclusive iterations (no leader slot within the per-iteration timeout, or
  the served header never observed on a side) are counted separately and never
  counted as pass or as agree/disagree.
- A run with **zero conclusive iterations FAILS** (the whole soak is vacuous).
- The time budget bounds the run: the loop checks the monotonic clock before
  each iteration and the final partial iteration is allowed to finish or is cut
  at its per-iteration timeout, never unbounded.

`result.json` body:

```json
{
  "schema_version": "v1",
  "family": "encoding-form",
  "seed": 267515629,
  "differential": true,
  "target_nodes": ["node1", "amaru-relay-1"],
  "iterations": 431,
  "conclusive": 402,
  "inconclusive": 29,
  "pass": false,
  "counters": {"pass": 401, "mismatch": 1, "inconclusive": 29,
               "agree": 401, "disagree": 1},
  "mismatches": [
    {"iteration": 118, "case_id": "encoding-form-000118",
     "spec": {"family": "encoding-form", "seed": 267515629, "iteration": 118,
              "base_case": "valid-control", "expected_verdict": "accept",
              "params": {"encoding_form": "trailing-bytes", "trailing_len": 3,
                         "trailing_seed": 91827}},
     "served_hash": "…", "observed_verdict": "rejected", "observed_reason": null}
  ],
  "disagreements": [
    {"iteration": 118, "case_id": "encoding-form-000118",
     "spec": {"…": "…"},
     "verdicts": {"node1": "accepted", "amaru-relay-1": "rejected"}}
  ],
  "duration_seconds": 10807.4,
  "runtime_root": "…", "compose_project": "…",
  "target_health": {"before": {}, "after": {}, "tip_before": {}, "tip_after": {}}
}
```

`target_health` is assembled exactly as the deterministic driver does, so
`target_progress_continues` reads the soak `result.json` unchanged.

## Families and invariants

Two families are **differential** (compare two nodes per iteration, `differential:
true`, `target_nodes` of length 2); two are **single-target** (one node, one
invariant, `differential: false`, `target_node`).

### A `encoding-form` (differential)

Random CBOR re-encodings of an otherwise-**valid** opcert header: non-canonical
integer encodings, definite vs. indefinite arrays, an extra / duplicated /
missing map key, and appended trailing bytes — each re-signed so the header
reaches the decoder (not stopped at the signature check). This is the family most
likely to find a real divergence: a live trailing-bytes divergence between Amaru
and cardano-node has already been observed elsewhere (see memory
`amaru-findings-fix-status`: submit-trailing-bytes still open).

- Base case: `valid-control` (the real pool header at the tip), then the chosen
  re-encoding is applied to its opcert/header bytes and re-signed.
- Invariant: **both nodes AGREE**, and a semantically-valid re-encoding is
  ACCEPTED. One-accepts / one-rejects on the same iteration ⇒ divergence finding
  (recorded in `disagreements`). A both-reject on a semantically-valid re-encoding
  ⇒ mismatch (over-rejection) recorded in `mismatches`.

### B `accept-boundary` (single-target)

Random valid-within-window opcerts: counter exactly `recorded + 1` (the accept
boundary), KES period uniform in `[start, start + maxKESEvolutions]`.

- The generator emits a normalized `kes_period_fraction ∈ [0, 1)` (genesis-
  independent, seed-deterministic); the forger maps it at serve time to
  `start + floor(fraction * (maxKESEvolutions + 1))` using the live genesis, so
  the draw stays replayable without the generator knowing the genesis.
- Base case: `counter-plus-one` extended with the sampled in-window KES period.
- Invariant: **ACCEPTED**. Any rejection ⇒ over-rejection finding (`mismatches`).

> **Family B observability limitation — NON-SOAKABLE (excluded from the 3h
> campaign).** Verified live on `profile-v` (2026-09-24): family B is served
> correctly (the `counter-plus-one` header is delivered, `opcert_case_served`
> recorded) but the accept is **never observable**, so every iteration is
> fail-closed `inconclusive` (0 conclusive over repeated iterations). Root
> cause is structural, not a driver bug: cardano-node (and Amaru) emit **no**
> trace for a header that merely passes ChainSync validation — an accept only
> surfaces once the **block** is adopted (`ValidCandidate` /
> `AddedToCurrentChain`, or Amaru `tip.adopt`). To adopt the block the consumer
> must BlockFetch its body, and the forger's on-demand BlockFetch responder
> (`onDemandBlockFetchResponder`) fetches bodies from the **upstream real node
> by point**. A `counter-plus-one` header is re-signed and therefore has a
> **new hash absent upstream**, so the body-fetch misses (`"body miss;
> skipping"`) and the isolated consumer can never adopt the block. Reject cases
> stay observable because header validation fails in the ChainSync client and
> raises a traced exception; `valid-control` stays observable because it is a
> byte-identical **real** block whose body IS upstream. A synthetic valid
> accept is thus not attributable in this isolated-consumer header-server soak.
> Making B observable would require the forger to synthesize and serve a full
> valid block body under the bumped opcert (a substantial forger change with
> its own correctness risk); its marginal value is low because `valid-control`
> in the deterministic opcert case set already proves "a valid header is
> accepted". **Decision:** the two `opcert-soak-accept-boundary-{cardano,amaru}-*`
> scenarios are kept on disk but tagged `non-soakable` / `campaign-excluded`
> and carry a `promotion_blockers` note; they are **not** part of the 3h
> campaign. Families A, C, D remain soakable.

### C `restart-persistence` (single-target)

Rotate the pool to a random counter `N ∈ [1, k]` (reuses the opcert-rotation
machinery established for the aged profile — legitimate cold-key-signed opcert
issuance, install, let it forge counter-`N` blocks), restart the target node,
then serve a header carrying a counter `r ∈ [0, N-1]`.

- Base case: `counter-behind` generalized to the sampled `replay_counter`.
- Invariant: **REJECTED** (counter-too-small) after the restart. Acceptance ⇒
  counter-replay-after-restart finding (`mismatches`).

### D `kes-period-differential` (differential)

On a **NORMAL** (non-short-KES) devnet, sweep random slots straddling KES-period
boundaries and serve **valid-control** headers at those slots.

- NORMAL genesis is used on purpose (not the aged/short-KES profile): Amaru
  already cannot host short-KES, and the hypothesis under test is Amaru's
  slot→KES-evolution *computation* (memory `gap1-bootstrap-trust-source`: Amaru
  pins leader-election nonces / KES mapping in-binary). Short-KES would change
  the very quantity being probed.
- The generator emits a `slot_offset_fraction ∈ [0, 1)`; the forger picks a live
  tip slot whose KES period is within a small window of a boundary, offset by the
  fraction.
- Invariant: **both nodes AGREE** on the valid control. Disagreement ⇒ the
  KES-period-computation divergence finding (`disagreements`).

## Forger extension (one binary, extended)

`dwarf-opcert-adversary-latest serve-case` gains:

- `--case-spec FILE` — a JSON spec `{base_case, params, seed}`. When present it
  deterministically overrides the hard-coded per-`caseId` behavior in
  `DwarfOpcertAdversary.applyCase`: `counter_delta`, `kes_period_offset` (or
  `kes_period_fraction`), `replay_counter`, `slot_offset_fraction`, and
  `encoding_form`. Absent ⇒ current behavior (deterministic scenarios unchanged).
- A new **encoding-form** re-encoder: builds the valid opcert/header, then
  re-serializes with the requested structural deviation (non-canonical int,
  definite/indefinite array, extra/duplicate/missing map key, trailing bytes),
  re-signs, and serves. All byte-level randomness is driven by the spec `seed`
  so the exact bytes are reproducible.
- Evidence: each `opcert_case_served` line echoes the applied `spec` (base_case,
  params, seed) alongside the existing `header_hash`, so a finding row in
  `result.json` carries everything needed to replay it.

Raw-byte fuzzing is explicitly **not** added here — out of scope (CBOR fuzzers
own it).

## Assertions

- `opcert_soak_invariant_holds` — PASS iff `mismatches == []` **and**
  `conclusive > 0` (fail-closed on a vacuous run). Consumes the soak `result.json`.
- `opcert_soak_verdicts_agree` — differential families only: PASS iff
  `disagreements == []` **and** `conclusive > 0`. Consumes the soak `result.json`.
- `target_progress_continues` — reused unchanged (honest producer kept advancing).

An accepted invalid case or a cross-node disagreement classifies as
`expected_security_finding`, as in the V16 run.

## Run matrix (6 scenarios, each a 3h soak)

`time_budget_seconds: 10800`; scenario `timeout_seconds: 11700` (safely above the
budget so the primitive is never killed mid-drain). `evidence_intent:
finding-validation`.

| Scenario id | Family | Profile | Target(s) | Assertions | Campaign status |
|---|---|---|---|---|---|
| `opcert-soak-encoding-mixed-*` | A | `profile-zb-mixed-1112-amaru-20260918-nanoseconds-v3` | `node1` + `amaru-relay-1` | invariant_holds, verdicts_agree, progress | GO |
| `opcert-soak-kes-period-mixed-*` | D | `profile-zb-…` | `node1` + `amaru-relay-1` | invariant_holds, verdicts_agree, progress | GO |
| `opcert-soak-accept-boundary-cardano-*` | B | `profile-v-cardano-measurement-nanoseconds-v2` | `node1` | invariant_holds, progress | **EXCLUDED — non-soakable (Family B observability limitation)** |
| `opcert-soak-accept-boundary-amaru-*` | B | `profile-z-amaru-20260918-nanoseconds-v3` | `amaru-relay-1` | invariant_holds, progress | **EXCLUDED — non-soakable (Family B observability limitation)** |
| `opcert-soak-restart-persistence-cardano-*` | C | `profile-v-…` | `node1` | invariant_holds, progress | GO (adopt-gated) |
| `opcert-soak-restart-persistence-amaru-*` | C | `profile-z-…` | `amaru-relay-1` | invariant_holds, progress | GO (adopt-gated) |

## Stop conditions

1. If Amaru cannot be driven for family C (counter rotation / restart) or family
   B on its substrate, **that cell degrades to cardano-only and is reported** —
   not forced. The Amaru cell records an explicit `unavailable` reason.
2. Amaru already cannot host short-KES — family D uses NORMAL genesis precisely to
   test the KES-period computation on a standard devnet.
3. The generator must be **seed-deterministic** so any finding is replayable. A
   family whose randomization cannot be made replayable is a red flag: fail the
   task rather than ship non-reproducible randomization.

## Fail-closed rules (summary)

- No leader slot / undelivered header in the per-iteration timeout ⇒ inconclusive,
  tracked separately, never pass, never agree/disagree.
- Differential compare is on the **verdict** (accepted/rejected), not the header
  hash (the two isolated consumers may sit at different tips). If either side is
  inconclusive for an iteration, that iteration is inconclusive on **both** sides
  and excluded from agree/disagree (guards against false agreement when one node
  simply never received the header).
- Zero conclusive iterations ⇒ run FAILS.
- Every `mismatches[]` / `disagreements[]` row carries the full seed-derived spec.

## Out of scope

- Raw-byte header fuzzing (CBOR fuzzers own it).
- New profiles: the six cells reuse `profile-v`, `profile-z`, `profile-zb`.
  Family C's rotation reuses the aged-profile opcert-rotation machinery on the
  standard profiles; no new short-KES genesis.
- Phase-2 mesh injection of soak headers (the deterministic design's later phase).
