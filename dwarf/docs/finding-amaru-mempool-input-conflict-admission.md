# Amaru's mempool admits conflicting-input (double-spend) transactions

**Component:** `amaru` mempool ingress (`crates/amaru-consensus/src/stages/mempool/stage.rs`,
`crates/amaru-mempool/src/strategies/in_memory_mempool.rs`)
**Type:** Mempool-admission conformance divergence vs the Haskell reference node (mempool validates
against the ledger UTxO set, not a mempool-adjusted set; no input-conflict check)
**Status:** Observation — verified empirically + confirmed in source. **LIKELY BY-DESIGN / a
known young-mempool simplification, not an unintended bug** (hedged pending maintainer confirmation).
LOW severity; sibling to `finding-amaru-submit-trailing-bytes.md` (both: amaru mempool more
permissive at ingress; bounded, no consensus impact).
**Found by:** DWARF phase-1 semantic-edge soak (value/output edges), 2026-09-26.
**Provenance (ground-truthed via `--version`):** amaru `v10.11.20260925` (git `eaf8ac3f`) vs
cardano-node `11.1.2` (git `fef83fed`); both frozen (non-forging) at the rebake chain.
**Not filed upstream** (by-design/known-limitation; no upstream issue tracks it — only the mempool
implementation PRs exist).

## Summary

Amaru's mempool admits **multiple transactions that spend the same input**. cardano-node admits the
first and rejects each conflicting one with
`ConwayMempoolFailure "All inputs are spent. Transaction has probably already been included"`.

## Evidence (dedicated controlled conflict test — both nodes frozen, fair)

1. Submit tx1 (spends `9708b921…#0`) → **both accept** (202).
2. Submit tx2 — a *different* tx that **also spends `9708b921…#0`** → cardano **400**
   `ConwayMempoolFailure "All inputs are spent…"`; amaru **202 ACCEPTED**.
3. Submit tx3 — a third conflicting spend of the same input → same (cardano reject, amaru accept).

Reproduced across the value/output/cert soak batches (any second spend of an already-admitted
input is amaru-accepted / cardano-rejected). Deterministic.

## Root cause (source, amaru `eaf8ac3f`)

- `stages/mempool/stage.rs` — `validate_and_insert` (≈L112) validates each submitted tx via
  `ledger.validate_tx(&tx)`; the function comment (≈L110) is *"Validate a transaction against the
  current ledger state"*. It validates against the **ledger UTxO set**, where the input is still
  unspent — **not** a mempool-adjusted set that accounts for inputs already reserved by pending
  mempool txs.
- `strategies/in_memory_mempool.rs` — `insert` (≈L79) rejects only an exact **duplicate tx id**
  (`entries_by_id.contains_key(&tx_id)`); there is **no input-conflict / double-spend check** across
  distinct mempool entries.

So two distinct txs spending the same input both pass `validate_tx` (ledger says the input is
unspent) and both `insert` (distinct tx ids) → both admitted. cardano's mempool instead applies txs
to a mempool-local ledger view, so the second sees the input as already consumed.

**Flip side, same root (2026-09-26, cardano-node 11.1.2 vs Amaru `eaf8ac3f`):** mempool **chaining** is refused. A tx that spends an output of a tx still pending in the mempool is accepted by cardano (202) and rejected by Amaru (`failed to prepare … unknown (but required) transaction input … <parent>#0`), because Amaru validates only against the ledger state. Evidence: cardano-box `/tmp/nsprobe/chain/evidence.json`.

## Severity — LOW (mempool-admission conformance; bounded)

- Mempool ingress only. Block validation still enforces no-double-spend, and amaru does not produce
  Praos blocks, so no consensus/safety impact.
- Consequence: amaru's mempool can hold + relay conflicting transactions until one is mined
  (mempool bloat; a peer could receive conflicting txs from amaru). Same class/impact as the
  submit-trailing-bytes ingress permissiveness.
- On a live (advancing) node the window is until inclusion; on the frozen node it is permanent — but
  cardano is equally frozen and still rejects, so the divergence is amaru's admission policy, not a
  frozen artifact.

## Measurement note (classifier)

The general oracle maps `ConwayMempoolFailure "All inputs are spent"` → **MASKED** (it is also the
symptom of shared-substrate contention when a soak's funding UTxO is consumed). That rule would
**mask this divergence in an uncontrolled soak**, so it must be captured by the **dedicated
controlled conflict test above** (tx1 both-accept → conflicting tx2 → cardano-reject/amaru-accept),
not the general soak oracle. (Coordinated with the shared-classifier work so the MASKED rule stays
for contention while the dedicated test remains un-masked.)

## Recommendation

If amaru intends cardano-equivalent mempool semantics: validate submitted txs against a
mempool-adjusted UTxO view (or add an input-conflict check in `insert`) so a second spend of an
already-pending input is rejected at ingress, mirroring cardano's `ConwayMempoolFailure`. If the
current behaviour is intentional for the young mempool, document it as a known ingress difference.
