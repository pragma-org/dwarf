# Client acceptance examples

This directory freezes the five smallest security-centered examples required by the current client acceptance goal.
The machine-readable cards are in `contracts/` and validate against
`dwarf/spec/v1/client-example-acceptance-card.schema.json`.

Technical status on 2026-09-21: all five contracts remain frozen. G3-A through G3-C are implemented. All five cards have accepted evidence for both implementations. Card 03 keeps its accepted `whole-microseconds-v1` evidence. Cards 01, 02, and 05 use `nanoseconds-v2`. Card 04 uses `nanoseconds-v3` for Amaru and `nanoseconds-v2` for Cardano-node. Gates 4 and 5 are complete.

Child explanation: DWARF finished all five recipes for both nodes. The old findings stay visible beside the newer runs that resolve them.

An additive sixth card measures simple signed payments without changing the accepted five-card evidence. Its separate Amaru and Cardano-node runs each retain 35 attempts: 30 accepted, 5 expected duplicate-payment rejections, and 0 timeouts. The runs prove collection for their exact target, version, workload, and environment. They are not an automatic comparison.

| Card | Exact current state | Measurement revision |
|---|---|---|
| 01 | Cardano-node accepted as run `20260920T132629Z-ea000d37`; fixed Amaru accepted as run `20260920T235440Z-050046a4`; old Amaru run `20260920T135054Z-28289dcd` remains a completed finding | `nanoseconds-v2` |
| 02 | both legs accepted; fresh accounting runs retain all 60 attempts: Cardano-node `20260921T202611Z-27eeadb5`, Amaru additive on-chain V2 topology `20260921T204537Z-ff5a800a`; original accepted runs Cardano-node `20260920T135958Z-362eedc7` and Amaru `20260921T013953Z-565b77c3`, plus the old topology finding, remain retained | `nanoseconds-v2` |
| 03 | both legs accepted: Amaru `20260920T072858Z-2cc3bb0c`, Cardano-node `20260920T073447Z-ab81bfb7` | `whole-microseconds-v1` |
| 04 | both canonical-progress legs accepted: Amaru `20260921T035546Z-9747122c`, Cardano-node `20260921T021935Z-3b58eafc`; the earlier same-height fork finding remains retained | Amaru `nanoseconds-v3`; Cardano-node `nanoseconds-v2` |
| 05 | both run-relative restart and controlled-sync legs accepted: Amaru `20260921T045619Z-15e864a0`, Cardano-node `20260921T045807Z-8e2bbb0e` | `nanoseconds-v2` |
| 06 | both simple-transfer legs accepted: Amaru `20260921T194749Z-26542a57`, Cardano-node `20260921T195334Z-e713d0de`; each retains 35 attempts and 35 duration samples | `nanoseconds-v2` |

The accepted labels are `framework proven`, `collection proven`, and `client requirement complete`. Use `client requirement partial` only for retained historical runs that did not satisfy a frozen card.

The client requirement is complete for these five frozen cards. It does not authorize a mixed-node comparison, stable benchmark thresholds, weekly automation, a presentation, or an Antithesis or Moog run.

| Card | Functional focus | Non-functional focus | Gate 1 state |
|---|---|---|---|
| 01 | CBOR outcome and round-trip consistency | decode time by outcome | frozen; exact-release adapters and live cases required |
| 02 | Plutus result and CPU/memory budget | VM wall-clock time by outcome | frozen; evaluator adapters and Amaru live workload required |
| 03 | invalid Handshake containment and liveness | decode and target resource cost | frozen; malformed case and containment assertions required |
| 04 | adopted chain progress | block-application and resource distributions | frozen; controlled 30-block window required |
| 05 | real restart recovery | readiness, sync speed, recovery resources | frozen; real restart gates and controlled sync range required |
| 06 | real signed simple payments | outcome duration, offered rates, target resources, and available protocol stages | accepted; exact separate targets and complete attempt accounting required |

The existing retained Amaru stock run `20260918T234213Z-64959688` and Cardano patched run
`20260919T032200Z-59f94558` are framework evidence. They are not final evidence for these cards. The Amaru run has no
patched target proof. The Cardano run has only four Plutus VM samples, 29 block-application samples, no rejected
protocol-decode samples, and no restart readiness result.

## Gate 2 calibration status

Gate 2 now has a retained, matched Amaru stock/patched calibration pair. This pair proves the patched measurement path;
it does not complete contract 03 or any other final client example.

- Patched run: `20260919T135417Z-cca4cc8d`.
- Stock run: `20260919T142302Z-f856f303`.
- Workload digest: `sha256:cb649d0b4f89d338615371178cff9676684c9b41a5c8a9fdce34bdc04846c5a9`.
- Outcomes per leg: 40 accepted, 40 refused, 40 malformed, and 0 unexpected.
- Pair evidence: `state/evidence/measurement-pairs/20260919T142302Z-amaru-stock-patched-handshake/result.json`.
- Pair evidence SHA-256: `sha256:a1096645186f25b5c6cca2f8a53b0efdcf98296b2f5b98527192b5a738ba5d69`.
- External-roundtrip envelope: maximum 5% absolute delta across mean, median, p95, and p99; minimum 30 samples per leg;
  exact source, runner, timing, hardware, workload, and terminal-outcome parity.
- Observed maximum absolute delta: `0.059686888%` from 120 samples per leg.
- Gate behavior: informational and non-gating.

Technical claim boundary: this pair calibrates the common externally observed Handshake round trip only. It does not
bound internal hook cost, throughput, saturation behavior, or another workload. Patched-only behavior is not an Amaru
vulnerability unless it reproduces against stock Amaru.

Child explanation: the normal node and the node with measuring marks got the same messages. Their outside response time
was almost the same. This does not tell us the exact cost of each measuring mark inside the node.

## Gate 3 gap matrix

The evidence-cited Gate 3 matrix is in `GATE-3-FIVE-CARD-GAP-MATRIX.md`.

The matrix audits every frozen scenario, primitive, assertion, measurement, and collector. It classifies each required
item as `proven`, `reusable`, `missing`, or `accepted unavailable`. It does not treat a definition, a similar primitive,
or a finalized zero-sample collector as final runtime proof.

G3-A through G3-C are implemented and tested. The accepted Card 03 evidence remains unchanged. The additive
`nanoseconds-v2` targets supply finer timing for Cards 01, 02, 04, and 05. The retained findings preserve the exact
historical limits. Card 04 uses the additive Amaru `nanoseconds-v3` target for its canonical-progress evidence.

Gate 4 completed the reviewed real-node execution sequence. Gate 5 retained one accepted Amaru run and one accepted Cardano-node run for each card. The card contracts and proof pages record the exact target revisions, images, workloads, seeds, hardware, samples, logs, reports, and accepted bundle digests.

Child explanation: the tools and all ten final node runs passed their frozen checks. Each recipe has proof that a person can inspect and export.

## Dashboard inspection check

The deployed dashboard was rebuilt from framework commit
`0dc7cedaeb358760020680c57b6112827862630b` as image
`sha256:5091cad75242a0dcbb00537b9688e496f8bfbe3142c189994685c8f7029d1117`.
On 2026-09-21, the live health path reported the three expected Cardano-node processes. The route check returned HTTP 200 and the correct object identity for all ten frozen scenario pages, both measurement-profile pages, and all ten accepted run pages.

The full Chromium audit checked 88 routes at desktop, tablet, and mobile sizes. It completed 264 page checks, 19 interaction checks, 40 download checks, and 3 token-gate checks with no failures.
The exact-card audit checked the final scenario, profile, and run routes at 1440 by 900 pixels and 390 by 844 pixels. It completed 90 page checks, 19 interaction checks, 40 download checks, and 3 token-gate checks with no failures.
The deployed `/learn/measurements` route and representative navigation completed 50 page checks at desktop and mobile sizes, 19 interaction checks, 40 download checks, and 3 token-gate checks with no failures.

Each of the ten final run exports was non-empty, passed gzip integrity, passed the signed DWARF bundle verifier, imported into an isolated run directory, and retained the source manifest SHA-256 digest. The export check did not change the accepted evidence or rerun Card 03.

Child explanation: A person can open every final proof in the DWARF screen on a large or small device and download its evidence package. DWARF also checked that each downloaded package opens and keeps the same signed proof list.
