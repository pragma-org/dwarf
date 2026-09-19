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
