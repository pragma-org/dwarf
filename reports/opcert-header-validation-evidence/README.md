# Evidence bundle — opcert / KES header validation (cardano-node + Amaru)

Retained outputs behind `dwarf/docs/finding-opcert-header-validation.md`. Every file here is
copied verbatim from a retained run under `dwarf/runs/`; nothing is synthesised.

## Expected case table
- `case-table.json` — copy of `dwarf/corpora/opcert/opcert-header-cases-v1.json`: the eight
  cases (6 reject rules + valid-control + counter-plus-one boundary accept) and the expected
  per-node reason for each.

## Cited runs

### amaru-20260924T120659Z-9164d7ed  (profile-z, Amaru 10.11.20260918)
- `result-amaru.json` — 5 cases, all matched: valid-control accept; cold-key-unauthorized
  `InvalidSignature`; counter-jump `SequenceNumberTooFarAhead`; kes-before-window
  `OpCertKesPeriodTooLarge`; hot-key-mismatch `InvalidKesSignature`.
- `assertions.json` — `opcert_case_verdicts_match_expected` pass, `target_progress_continues` pass.
- `amaru-reject-loglines.txt` — the four raw `amaru::consensus` rejection strings.

### mixed-20260924T124033Z-29ab341d  (profile-zb, cardano-node 11.1.2 + Amaru 10.11.20260918)
- `result-cardano.json` — cardano-node leg: cold-key `InvalidSignatureOCERT`, counter-jump
  `CounterOverIncrementedOCERT`, kes-before-window `KESBeforeStartOCERT`, hot-key-mismatch
  `InvalidKesSignatureOCERT`, valid-control accept.
- `result-amaru.json` — Amaru leg (same five cases).
- `assertions.json` — `opcert_case_verdicts_match_expected` (x2), `opcert_verdicts_agree`
  (`case_count: 5, disagreements: []`), `target_progress_continues` — all pass.

### boundary-20260924T142553Z-a09fcd79  (profile-opcert-aged-kes-cardano-1112, cardano-node 11.1.2)
- `result-cardano.json` — valid-control accept; counter-behind `CounterTooSmallOCERT`;
  kes-after-window `KESAfterEndOCERT`.
- `assertions.json` — `opcert_case_verdicts_match_expected` pass.
- `evidence-counter-behind.ndjson`, `evidence-kes-after-window.ndjson` — per-case harness evidence.
- `cardano-reject-loglines.txt` — raw `HeaderProtocolError ... WrapValidationErr` node lines.

## Not retained
The dedicated cardano-only standard-profile run (`opcert-header-validation-cases-cardano-1112`,
profile-v) is not retained under `dwarf/runs/`; its four standard cardano-node rules are
evidenced instead by the mixed run cardano leg. No aged-Amaru run exists — see the Amaru
short-KES pinning sub-finding in the finding note.
