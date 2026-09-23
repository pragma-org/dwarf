# Operational-certificate header validation design

## Scope

Add live-devnet security tests for the operational certificate (opcert) carried in every Praos block header: authorization by the pool's cold key, counter monotonicity, and the KES validity window. The tests run against real Cardano-node and Amaru targets on the three supported topologies:

| Topology | Profile | Targets |
|---|---|---|
| Amaru-only | `profile-z-amaru-20260918-nanoseconds-v3` | Amaru 10.11.20260918 |
| Cardano-node-only | `profile-v-cardano-measurement-nanoseconds-v2` | Cardano-node 11.1.2 |
| Mixed | `profile-zb-mixed-1112-amaru-20260918-nanoseconds-v3` | Amaru 10.11.20260918 and Cardano-node 11.1.2 |

This is the first of five sub-projects (opcert; then the transaction-validation foundation; governance signatures; native-script thresholds; Byron bootstrap witnesses). It reuses the existing scenario, primitive, profile, measurement, assertion, SARIF, evidence and Learn systems. Library-tier header-vector testing is out of scope: DWARF tests live nodes.

## Threat model and case families

Every case keeps the header well-formed and correctly signed, and breaks exactly one opcert rule; all other header fields stay valid, so a node's verdict is attributable to that one rule. Cases are data rows in a case table (the pattern already used by the handshake `version-table-forward-compat-v1` set), not code. Families:

1. **Rule + boundary (this cycle).** The six rules, each with its at/just-inside/just-outside boundary:
   - cold-key authorization: opcert not authorized by the pool cold key.
   - counter behind: counter below the highest the node has recorded for that pool.
   - counter jump: counter more than one above the recorded value (and the exactly-one-ahead boundary, which must be accepted).
   - KES period before the opcert window (and the first valid period, accepted).
   - KES period after the opcert window (and the last valid period, accepted).
   - hot-key mismatch: header signed with a KES key the opcert does not authorize.
   - `valid-control`: no mutation, must be accepted.
2. **Encoding/structure (next).** The same logical opcert in different byte forms (non-canonical ints, indefinite vs definite arrays, extra/missing/duplicate fields, trailing bytes). The header re-signs each variant so the case reaches the decoder rather than failing the signature check. Expected: both nodes agree (accept-and-equal, or both reject).
3. **State/timing (later).** Multiple pools, counters across a target restart, epoch and KES-evolution boundaries.
4. **Generated (later).** Structured randomization within families 1–3; never raw random bytes (those never reach opcert logic and are already covered by header CBOR fuzzers).

## Reference generator

Reuse the official ouroboros-consensus `gen-header` mutation set already used in dwarf-v4's KES-boundary lab (`tools/kes_boundary_cases.py`, `reports/consensus-kes-boundary-evidence/scripts/build_cases.sh`) and mirrored by Amaru's own `crates/amaru-ouroboros/tests/data/header-test-cases.json`. Mutation names map 1:1 to the case table: `MutateColdKey`, `MutateCounterUnder`, `MutateCounterOver1`, `MutateKESPeriodBefore`, `MutateKESPeriod`, `MutateKESKey`, `NoMutation`.

## Delivery approach

**Approach A — live forger.** A DWARF peer follows the live devnet chain honestly, holds one devnet pool's DWARF-generated keys (VRF, KES, cold), and at that pool's leader slot builds a header extending the current tip, applies one case, signs it, and serves it (with its block) over ChainSync/BlockFetch. Counter cases are measured against the counter the target has actually seen on chain, which is why a live devnet is required. A `valid-control` header from the same peer, on the same path, proves any rejection is caused by the case and not the harness. Approach C (pre-built chain, dwarf-v4 style) is retained only as a possible future supplement; approach B (library-tier vectors, no live node) is explicitly rejected — it is not what DWARF does.

**Phase 1 — isolated target.** The target under test gets the forger as a dedicated upstream; no other producer sees the mutated header. One clean per-node verdict; runs identically on all three topologies. This models a malicious/compromised upstream peer.

**Phase 2 — mesh injection (mixed devnet, after phase 1).** The same forger peers with the producers to observe spread: chain-split containment, honest-chain survival, and whether the target disconnects the offending peer. This is where a cross-implementation divergence (e.g. one node adopting a header the other rejects) becomes visible network-wide.

## Components

1. **Opcert forger peer** — Haskell, built on the existing `antithesis/components/dwarf-kes-adversary` component and ouroboros-consensus header code. Follows the tip, applies one case from the case table, re-signs, serves over ChainSync/BlockFetch to the target only, and writes a ground-truth record per case (case id, family, rule broken, header bytes+hash, parent point, slot, pool, expected verdict). The existing adversary only bit-flips the KES signature; this extends it to build validly-signed headers with a single opcert mutation.
2. **Opcert case table** — one data file (rule + boundary rows for this cycle). Adding a case is adding a row.
3. **Primitive `runtime_opcert_header_cases`** — starts the forger against the chosen target in a deployed profile, runs the selected cases each preceded by `valid-control`, joins ground truth with observed verdicts, writes `result.json` and `attempts.ndjson` (calibration-primitive pattern). Fail-closed: if the pool is not leader in time, the control is not accepted, or the target never received the header, the case is `inconclusive`, never a pass.
4. **Profiles** — reuse profile-z / profile-v / profile-zb. The forger is an extra container on the devnet network, wired only to the target (phase 1). No new profile field unless deployment proves one is needed.
5. **Measurement taps** — new stock header-validation taps `amaru-stock-header-validation` and `cardano-stock-header-validation`: per-header verdict, rejection reason, and validation time parsed from each node's normal logs, correlated by header hash. Implemented this cycle (rejections are the core signal). Patched-mode variants (`*-patched-header-validation`) are scaffolded only: definition + collector skeleton returning `unavailable` with a reason (never zero) + coverage entry + tests. Existing header/chain/resource/network taps run unchanged.
6. **Assertions** —
   - `opcert_case_verdicts_match_expected`: each case reaches its declared verdict (valid accepted, broken rejected), with per-case reason retained; fail-closed on any `inconclusive`.
   - `opcert_verdicts_agree`: on the mixed devnet, both nodes give the same verdict per case.
   - reuse `target_progress_continues`: the target recovers and keeps following the honest chain.
   An accepted broken case yields `expected_security_finding` classification, as with the V16 run.
7. **Stub replacement** — `runtime_praos_header_assertion_probe` currently hard-codes `{"header_rejected": True}` (`dwarf/scripts/runtime_hardening_probe.py:651`) and is mapped to TM-013. Change it to fail-closed `unavailable` and repoint TM-013 / the header-validation coverage row to the new scenarios, so the coverage page stops showing fabricated evidence. Rebuilding that primitive fully is out of scope.
8. **Scenarios** — one per topology for the rule+boundary set (`opcert-header-validation-cases-{amaru,cardano,mixed}-*`), Amaru and Cardano-node targets on the mixed devnet, `evidence_intent: finding-validation`.

## Data flow (one case)

forger follows tip -> serve `valid-control` -> confirm target adopts it -> serve mutated header -> target validates and logs verdict -> header-validation tap yields (verdict, reason) by hash -> primitive joins ground truth + observed -> `result.json` -> assertions -> SARIF + finding classification + retained bundle.

## Error handling (fail-closed)

- Missing leadership / unaccepted control / undelivered header -> `inconclusive`, not pass.
- Target crash or restart -> recorded finding signal (`target_progress_continues` fails).
- Forger connects to the target only (phase 1); it never touches other DWARF stacks or the client's devnets.
- Taps that cannot extract a verdict report `unavailable` with a reason; never a synthesized verdict.

## Coverage and honesty

Map TM-013 / RR-013 (Praos header assertion) and the `partial` header-validation client rule in `measurement-coverage/v1.yaml` to the new scenarios; its stated missing step ("bounded valid and invalid header corpus") is what this delivers. The `/learn/coverage` mini-protocol matrix ChainSync column gains real Amaru and mixed entries.

## Verification

- Unit tests first (red), then implement: case table load/validation; verdict join; both assertions; tap parsers fed real cardano-node and Amaru log lines; stub fail-closed result; forger case-application logic (Haskell test alongside the existing adversary test).
- Scenario schema + semantic validation; profile rendering.
- One real run per topology on cardano-box with retained evidence (manifest, assertions, SARIF, header-validation tap, metrics); non-vacuous (a real rejection with a real reason, and an accepted control).
- Full test suite compared against `main` (only the pre-existing failures allowed).
- Docs regenerated (`gen_reference.py`); pinned catalog/primitive counts updated.

## Stop conditions

Stop and report if: the forger cannot produce a validly-signed header the honest target accepts as `valid-control` (no attributable baseline); Amaru cannot be fed headers over ChainSync as a responder on a DWARF devnet; or a case cannot be made attributable to exactly one rule. Do not substitute a decode-only or static check for a live-node verdict.

## Out of scope (later sub-projects / phases)

Encoding, state and generated case families; phase-2 mesh injection; patched-mode header-validation taps (scaffolded only); the transaction-validation foundation and surfaces B–D.

