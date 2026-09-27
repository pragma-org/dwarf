# Phase-1 differential coverage — 2026-09-26

> **Superseded** by `differential-coverage-capstone-2026-09-27.md` (all families, findings, candidates and harness hardening as of 2026-09-27). Kept for its semantic edge-matrix detail.


Coverage record for the phase-1 (submit-API) differential + soak work on the latest supported pair.
Tight index, not a re-narration — see the linked finding/family docs for detail.

## Versions under test (ground-truthed via `--version`, sha convention)

| node | version | git commit |
|---|---|---|
| cardano-node | 11.1.2 | `fef83fed` |
| amaru | v10.11.20260925 | `eaf8ac3f` |

Also validated earlier on the as-run pair amaru v10.11.20260903 (`ea1f34e4`) + cardano-node 10.7.1
(`045bc187`); re-validated on the pair above (see each family doc's re-validation note).
Substrate: frozen (non-forging) refs on the rebake chain; submit-API differential (no N2N).

## Conformant edge matrix (amaru 0925 vs cardano-node 11.1.2 — all AGREE)

| surface | edges tested | verdict |
|---|---|---|
| min-fee / tx-size | 164180 reject / 164181 accept (gap 0, IsValid excluded) | AGREE |
| value conservation | out+fee > input; out+fee < input | both `ValueNotConservedUTxO` |
| output min-UTxO | out=1, out=1k (below); boundary accept | both `OutputTooSmallUTxO` / accept |
| cert deposit | stake-reg deposit ±1 vs 2000000 | both `IncorrectDeposit` (amaru enforces) |
| quantity | int64-max mint accept (uint64-max unbuildable by cardano-cli) | AGREE |
| validity interval | expired (invHereafter<tip); not-yet-valid (invBefore>tip); controls | both `OutsideValidityIntervalUTxO` |
| max-tx-size | 25965 B tx (> 16384) | both `MaxTxSizeUTxO` / "transaction too large" |
| native-script | multisig / RequireMOf / nested / timelock(before,after) incl. inclusive boundary | AGREE (family doc) |
| gov-cert witness | DRep-reg missing / wrong-key / multi-partial | AGREE (family doc) |
| gov vote authorization | committee unauthorized / missing-witness; DRep missing / wrong-key | AGREE (family doc) |

Family docs: `native-script-phase1-differential-family.md`,
`governance-signature-phase1-differential-family.md`,
`governance-vote-authorization-differential-family.md`,
`finding-amaru-minfee-isvalid-size-divergence-CLOSED.md`.

Not covered here (other lanes): cert-EPOCH / pool re-registration + retirement = the stake-pool
phase-2 family (27/27 AGREE); collateral + redeemer/exUnits = the collateral/redeemer family (P1).

## Divergences

| # | divergence | severity | status | doc |
|---|---|---|---|---|
| P1 | collateral witness-bypass (novel) | — | headline, sub-owned | (collateral/redeemer family) |
| 1 | submit accepts trailing bytes | LOW (mempool-ingress) | KNOWN, confirmed LIVE on 0925 | `finding-amaru-submit-trailing-bytes.md` |
| 2 | mempool admits conflicting-input (double-spend) txs | LOW (mempool-ingress) | source-confirmed by-design, hedged; not filed | `finding-amaru-mempool-input-conflict-admission.md` |

Both #1 and #2 are the same class — amaru's mempool is more permissive at ingress than cardano —
bounded, no consensus impact (block validation still enforces the rules; amaru does not forge Praos
blocks). The **headline novel finding is P1** (collateral); this capstone's own contribution is the
broad conformance result + the #1 currency confirmation + the #2 by-design observation.

## Soak techniques (both exhausted, honest negative)

- Byte-mutation (`workload/phase1_soak.py`): finds decode-layer divergences (→ #1) + decoder-leniency
  (amaru decodes further then validation-rejects; both reject). Cannot reach validation-stage
  divergences (random bytes break decode/signature first); interior-only run = 100% inconclusive.
- Semantic re-signed field-edge: mutate one field to an edge value + re-sign → validation-stage.
  Whole edge matrix above = conformant; surfaced #2.

## Substrate caveats (for anyone reusing these frozen refs)

- Frozen-node validity checks use the **ledger-tip slot**, not wall-clock. The two frozen stores sit
  at different tips (cardano ref **1297**, amaru **1199**) → keep validity-interval edges **outside
  the 1199–1297 band** (and `invalidBefore ≤ 1199`) or a tx valid on cardano but "not yet valid" on
  amaru gives a **false** tip-driven split. Timelock/native-script boundary edges: same rule.
- Epoch skew: refs frozen at epoch 3; the open gov action (govrebake) expires epoch 7 — vote well
  inside the window.
- `ConwayMempoolFailure "All inputs are spent"` is both shared-substrate contention (→ MASK) and the
  divergence #2 conflict-reject — capture #2 only via a dedicated controlled conflict test, not the
  general soak oracle.
