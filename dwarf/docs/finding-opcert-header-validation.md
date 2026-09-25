# Finding note — operational-certificate / KES header validation (cardano-node + Amaru)

**From:** DWARF opcert header-validation differential testing (Pragma) · **Date:** 2026-09-24
**Scope:** serve a fixed corpus of mutated block headers to a live cardano-node 11.1.2
(`fef83fed`) node and a live Amaru 10.11.20260918 (`aedfe797`) node over ChainSync and
compare each node's accept/reject verdict and rejection reason against an expected table
(`dwarf/corpora/opcert/opcert-header-cases-v1.json`). Six operational-certificate rules plus
a valid control, run on standard devnet profiles (profile-z amaru-only, profile-zb mixed) and
on a purpose-built aged short-KES cardano-only profile. Both implementations enforce every
reachable rule; one architectural asymmetry in Amaru is documented as a sub-finding.

## Result summary — both nodes reject all reachable opcert rules

The header validation harness (`runtime_opcert_header_cases`) mutates one opcert/KES field per
case, serves the header, and records the served hash, observed verdict, and observed reason.
Assertions `opcert_case_verdicts_match_expected` (each node vs the expected table) and
`opcert_verdicts_agree` (cardano-node vs Amaru on the shared mixed devnet) both passed.

| rule (family) | case | cardano-node reason | Amaru reason |
|---|---|---|---|
| cold-key authorization | cold-key-unauthorized | `InvalidSignatureOCERT` | `InvalidSignature` |
| counter monotonicity (behind) | counter-behind | `CounterTooSmallOCERT` | cardano-only (see sub-finding) |
| counter monotonicity (over) | counter-jump | `CounterOverIncrementedOCERT` | `SequenceNumberTooFarAhead` |
| KES window (before) | kes-before-window | `KESBeforeStartOCERT` | `OpCertKesPeriodTooLarge` |
| KES window (after) | kes-after-window | `KESAfterEndOCERT` | cardano-only (see sub-finding) |
| KES hot-key binding | hot-key-mismatch | `InvalidKesSignatureOCERT` | `InvalidKesSignature` |
| (control) | valid-control | accepted | accepted |

Amaru's raw rejection strings from the retained run (`consumer.log`, target `amaru::consensus`):
- cold-key-unauthorized — `header validation failed: Invalid operational certificate signature from issuer`
- counter-jump — `Operational certificate sequence number (2) is too far ahead of the latest known sequence number (0).`
- kes-before-window — `Operational Certificate KES period (1) is greater than the block slot KES period (0).`
- hot-key-mismatch — `Invalid KES signature from leader: KES error: Invalid hash comparison`

cardano-node's raw rejection (aged boundary run, `HeaderProtocolError ... WrapValidationErr`):
- counter-behind — `CounterTooSmallOCERT 1 0`
- kes-after-window — `KESAfterEndOCERT (KESPeriod 25) ...`

## Valid-control baseline accepted across KES rollover

`valid-control` (an unmutated, correctly-signed header) is **accepted** by both nodes in every
run, including after the on-disk period-0 KES key is stepped forward (`unsoundPureUpdateKES`)
so signing straddles a KES-period rollover. This shows the six rejections are rule-specific,
not a blanket "reject everything" artefact. Amaru target progressed (block height 728 -> 795)
and stayed healthy through the case set (`target_progress_continues` pass).

## Cross-node agreement on the mixed devnet

On the shared mixed devnet (cardano-node 11.1.2 + Amaru 10.11.20260918, profile-zb), both
nodes were served the same five reachable cases and reached the **same verdict on every case**:
`opcert_verdicts_agree` reported `case_count: 5, disagreements: []`. cardano-node returned the
`*OCERT` reasons and Amaru its own reason vocabulary; the verdicts (accept/reject) matched.

## Aged short-KES boundary (counter-behind + kes-after-window, cardano-only)

Two rules are not reachable on a fresh standard-genesis devnet: `counter-behind` needs the
pool's on-chain opcert counter >= 1, and `kes-after-window` needs the chain aged past
`maxKESEvolutions` KES periods (~90 days on standard `slotsPerKESPeriod=129600`). A dedicated
profile `profile-opcert-aged-kes-cardano-1112` uses a custom short-KES genesis plus a
pre-rotated pool so both are crossed in minutes. Run `20260924T142553Z-a09fcd79` served
valid-control + the two boundary cases to cardano-node 11.1.2: valid-control accepted,
`counter-behind` rejected `CounterTooSmallOCERT`, `kes-after-window` rejected `KESAfterEndOCERT`
(`opcert_case_verdicts_match_expected` pass).

### Sub-finding (Amaru) — Amaru cannot be aged via a custom short-KES genesis

The aged boundary profile is **cardano-only** because Amaru rejects any devnet built with a
custom `slotsPerKESPeriod`. Amaru pins its slot -> KES-evolution mapping to its pre-derived
per-network parameters and does not re-derive it from the raw genesis, so a short-KES custom
genesis makes every forged block fail leader validation with:

`Invalid KES signature from leader: KES error: Invalid hash comparison`

This is the same generic Amaru KES-signature failure string observed for `hot-key-mismatch` in
the retained standard amaru run, here fired for **every** block rather than the single mutated
header. Consequently `counter-behind` and `kes-after-window` have no Amaru evidence and are
proven on cardano-node only. This is consistent with the previously documented Amaru behaviour
("trusts pre-derived params, does not re-validate raw genesis";
`dwarf/docs/finding-amaru-custom-testnet-bootstrap-wall.md`). No DWARF run of an aged-Amaru
attempt is retained — the differential is that such a devnet cannot be brought up at all.

## Cited runs and retention

- **Amaru standard** — `20260924T120659Z-9164d7ed` (profile-z, Amaru 10.11.20260918): 5 rules
  (cold-key, counter-jump, kes-before-window, hot-key-mismatch) + valid-control.
- **Mixed standard** — `20260924T124033Z-29ab341d` (profile-zb, cardano-node 11.1.2 + Amaru
  10.11.20260918): cardano-node 4 rules + Amaru 5 rules + cross-node agreement.
- **Aged boundary** — `20260924T142553Z-a09fcd79` (profile-opcert-aged-kes-cardano-1112,
  cardano-node 11.1.2): counter-behind + kes-after-window + valid-control.

The four standard-profile cardano-node rules are evidenced by the mixed run's cardano leg
(identical cardano-node 11.1.2 target). The dedicated cardano-only standard run
(`opcert-header-validation-cases-cardano-1112`, profile-v) was executed during bring-up but its
run outputs are **not retained** under `dwarf/runs/`; it is therefore not cited here, and the
cardano-node standard rejections above are taken from the retained mixed run instead of being
reproduced from a run that no longer exists.

## Evidence bundle

`reports/opcert-header-validation-evidence/` — the expected case table plus, per cited run, the
copied `result.json` (per-case served hash, expected vs observed verdict/reason), the run
`assertions.json`, and the relevant node reject log lines. See its `README.md` for the manifest.
