# Coverage and Measurement Documentation Audit Design

## Purpose

Make the public Learn pages and README describe the current evidence set: five frozen client cards, one separate additive simple-transfer card, and fresh complete external workload accounting for the two Card 02 Plutus runs.

## Findings

- The authoritative scenario catalog has 268 entries. Runtime rendering already derives this count from current scenario files.
- The client evidence ledger contains cards 01 through 06, but its function name, template variables, DOM identifiers, and some copy still say `five_card_evidence` or “five-card evidence.”
- Card 06 already uses only its contract mappings: TM-012, TM-024, RR-012, and RR-019. Its two retained runs appear in the ledger and measurement join.
- Card 02’s two fresh runs are in the ledger and measurement guide, but the measurement-coverage map has no Card 02 evidence rules. RR-031 therefore has no verified retained measurement record.
- The root README describes the original five-card program but omits the additive Card 06 and the completed 60-attempt Plutus accounting evidence.
- The threat/risk route recomputes 268 scenarios, 34 of 36 mapped threats, 34 of 35 mapped risks, and preserves gaps TM-030, TM-031, and RR-027.

## Selected design

Use one render-time `client_card_evidence` ledger for cards 01 through 06. Keep `five_card_evidence` as a deprecated compatibility alias only where removal would break callers outside this change. Rename page context, embedded JSON, and DOM identifiers to match the general ledger. Preserve “five frozen cards” only for the original accepted program.

Add Card 02 measurement evidence rules for the taps named by its accepted collector contract and proved by the fresh reports: Amaru External workload accounting, stock Plutus execution, and stock resources; Cardano External workload accounting, patched ledger/Plutus stages, and stock resources. Do not infer unsupported stages.

Update the Learn copy and README with short, public-safe statements. Keep counts derived from current catalogs and add tests that compare displayed values with the authoritative payload rather than duplicating them.

## Verification

Use red-green contract tests for naming, mappings, counts, links, collapsed presentation, and public safety. Run focused and full tests, the scenario validator with offline profile renders, browser interaction checks at desktop and mobile sizes, internal deployment verification, and the public candidate safety audit before normal pushes.
