# Five-example client acceptance program

This directory freezes the five smallest security-centered examples required by the current client acceptance goal.
The machine-readable cards are in `contracts/` and validate against
`dwarf/spec/v1/client-example-acceptance-card.schema.json`.

Technical status on 2026-09-20: all five contracts remain frozen. G3-A through G3-C are implemented. Cards 01 and 05 have accepted evidence for both implementations on `nanoseconds-v2`. Card 03 has accepted evidence for both implementations on `whole-microseconds-v1`. Cards 02 and 04 have accepted Cardano-node legs and retained Amaru work. Gates 4 and 5 are not complete.

Child explanation: DWARF finished three of the five recipes for both nodes. Two Amaru parts still need work. The old Amaru bug stays visible beside the newer run that proves its fix.

| Card | Exact current state | Measurement revision |
|---|---|---|
| 01 | Cardano-node accepted as run `20260920T132629Z-ea000d37`; fixed Amaru accepted as run `20260920T235440Z-050046a4`; old Amaru run `20260920T135054Z-28289dcd` remains a completed finding | `nanoseconds-v2` |
| 02 | Cardano-node accepted as final run `20260920T135958Z-362eedc7`; Amaru blocked because the frozen chain has no Plutus V2 cost model | `nanoseconds-v2` |
| 03 | both legs accepted: Amaru `20260920T072858Z-2cc3bb0c`, Cardano-node `20260920T073447Z-ab81bfb7` | `whole-microseconds-v1` |
| 04 | Cardano-node accepted as run `20260920T125840Z-778a7cf7`; Amaru retained a real same-height fork finding | `nanoseconds-v2` |
| 05 | both legs accepted: Amaru `20260920T122606Z-32c0e998`, Cardano-node `20260920T130232Z-2c68fb83` | `nanoseconds-v2` |

The accepted labels are `framework proven` and `collection proven`. The full five-card client requirement remains partial because Cards 02 and 04 do not yet have accepted Amaru legs.

| Card | Functional focus | Non-functional focus | Gate 1 state |
|---|---|---|---|
| 01 | CBOR outcome and round-trip consistency | decode time by outcome | frozen; exact-release adapters and live cases required |
| 02 | Plutus result and CPU/memory budget | VM wall-clock time by outcome | frozen; evaluator adapters and Amaru live workload required |
| 03 | invalid Handshake containment and liveness | decode and target resource cost | frozen; malformed case and containment assertions required |
| 04 | adopted chain progress | block-application and resource distributions | frozen; controlled 30-block window required |
| 05 | real restart recovery | readiness, sync speed, recovery resources | frozen; real restart gates and controlled sync range required |

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
`nanoseconds-v2` targets supply finer timing for Cards 01, 02, 04, and 05. The three retained findings state why the
remaining Amaru legs cannot satisfy their frozen contracts.

Technical boundary: Gates 4 and 5 remain incomplete. Do not mark the five-card requirement complete while Card 01 and
the Amaru legs for Cards 02 and 04 remain blocked.

Child explanation: the tools are built. Some recipes passed. Three Amaru checks reached real limits, so the complete
five-card promise is still not finished.

## Dashboard inspection check

The deployed dashboard was rebuilt from framework commit
`0b4158da3034087d8f3b287a0b3aff1374751524` as image
`sha256:743fae4ea816062059d4ae6887827692c8bfc9f9efc28488635f7c3443413599`.
On 2026-09-20, the live check returned HTTP 200 for all ten frozen scenario pages, both measurement-profile pages,
and all ten retained accepted-or-blocked run pages. Each run export returned a non-empty gzip archive.

The ten run pages were also rendered in Chromium at 1440 by 900 pixels and 390 by 844 pixels. All 20 renders had
zero page overflow, broken images, browser errors, missing headings, missing run identifiers, or undersized mobile
form controls.

Child explanation: A person can open every final proof or blocker in the DWARF screen on a large or small device and
download its evidence package. This display check does not change a failed security result into a pass.
