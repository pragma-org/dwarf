# Five-example client acceptance program

This directory freezes the five smallest security-centered examples required by the current client acceptance goal.
The machine-readable cards are in `contracts/` and validate against
`dwarf/spec/v1/client-example-acceptance-card.schema.json`.

Technical status: the contracts are frozen. No card is implemented, rehearsed, or accepted merely because this
document exists. Each card lists its exact open implementation gaps and its claim limits. The program uses separate
Amaru and Cardano-node executions. It does not start mixed-node comparison, weekly automation, stable thresholds,
Antithesis, Moog, or presentation walkthrough work.

Child explanation: these are five exact recipes. Writing a recipe does not mean that the test passed. DWARF must still
build the missing tools, run each recipe, and keep the proof.

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
