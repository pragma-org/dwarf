# Clean-negative — amaru header-level block-apply validation (TM-008/RR-008, TM-013/RR-013)

**Status:** CLEAN-NEGATIVE (conformance). No finding. amaru v10.11 eaf8ac3f vs cardano-node 11.1.2, Conway pv10, local devnet, testnet-only keys, PATH-B forge bridge, 2026-10-03.

## Summary

Four malformed-HEADER blocks were forged at the GOLDEN tip+1 (parent 181e9b48/slot 1000/height 213), each with a VALID body (`fixture/mempool/mp-base.tx`) and VALID VRF+KES (pool 97b0, epoch-3 nonce 85e36f9d) — the ONLY defect in each is one header field — served single-block on 192.168.30.16:3001, applied to amaru, and compared against cardano-node. **amaru rejected all four (hash-verified on our point_hash each, stayed alive, no tip.adopt / no apply), matching cardano-node's reject.** No accept-invalid-block / header-validation gap.

## Results (4/4 REJECTED, clean)

| Variant | point_hash | Defect | amaru reject site | cardano |
|---|---|---|---|---|
| 1a prevhash-zeros | 624365ae | header prev_hash = all-zeros (not GOLDEN tip) | `InvalidHeaderParent` — "actual parent 0000…, expected 1000.181e9b48(213)" | REJECT (Praos prevHash chaining) |
| 1b slot-nonmonotonic | 98deb238 | header slot 948 < parent slot 1000 | `InvalidHeaderPoint` — "slot does not progress from parent" | REJECT (slot must progress) |
| 1c height-noninc | e24f2b06 | block_height 213 = parent (not 214) | `InvalidHeaderHeight{actual=213, expected=214}` | REJECT (blockNo = succ(parent)) |
| 2 body-hash-mismatch | 30d00577 | header block_body_hash does not bind the served body | `block.mismatched_hash` — expected 0000…, actual 3eb4a553… | REJECT (bodyHash binds body) |

So amaru's header-validation (parent-chain linkage, slot monotonicity, height increment) and body-hash binding **all fire correctly** on a malformed-header block cardano rejects. These surfaces were previously only CBOR-fuzz + `*-example-smoke` covered; they are now exercised as a live mixed-pair acceptance test = TM-008/RR-008 and TM-013/RR-013 upgraded to live-differential COVERED.

## Evidence discipline (verify-don't-predict)

For variant 2, a generic error-counting watcher initially flagged "rejected" off MUX connection-churn (78 mux.failed + a fetch/reject retry loop, ~20k generic "error" signals), NOT the real block signal. This was re-inspected rather than taken at face value; the TRUE signal is `block.mismatched_hash` (clean reject). Same verdict, properly evidenced — the mux churn is transport noise, not the block decision.

## Reproduce via DWARF

Scenarios (internal, 85be3ec): `dwarf/scenarios/ledger-block-apply-header-{prevhash-discontinuity,slot-nonmonotonic,height-noninc,body-hash-mismatch}-differential-amaru-cardano-node.yaml` (expected_outcome=reject, evidence_intent=risk-support). Forge each malformed header via `forge_block` at the GOLDEN tip+1 (the broken field is a forge param; body-hash mismatch uses the `body_hash_override` forge flag), serve single-block, `block_apply_differential` + `block_apply_outcome_matches` expected reject.
