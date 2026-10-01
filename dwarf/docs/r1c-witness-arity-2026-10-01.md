# R1c — witness / arity leniency (2026-10-01, funded store-f, live submit, full resets)

All cases spend funded 9708b921 on a value-balanced body (witnesses are the only deviation; all 5 share
one txid since the body is identical). Full pair reset (cardano mempool cleared) between the accepted
cases — required because same-txid same-input submits otherwise MASK as "inputs already spent" on the
frozen cardano-ref (verify-don't-predict caught a false duplicate-witness "divergence" from an
amaru-only reset).

| case | amaru | cardano | verdict |
|------|-------|---------|---------|
| missing-witness (empty wit set) | reject (phase-1 invalid tx) | reject (MissingVKeyWitnessesUTXOW) | AGREE |
| wrong-key-witness (fresh key) | reject (phase-1 invalid tx) | reject (missing required witness) | AGREE |
| extra-witness (valid + extra) | accept 202 | accept 202 | AGREE (extra witnesses allowed) |
| duplicate-witness (funder twice) | accept 202 | accept 202 | AGREE (dup vkey witness deduped/allowed by BOTH) |
| vkey-arity-3elem ([vk,sig,x]) | accept 202 | DECODE-REJECT ("Size mismatch... Expected 4, found 3") | DIVERGENCE — reconfirm of the known witness-element-arity-leniency finding |

## Verdict
- NO new finding (no GHSA). amaru verifies required witnesses correctly (missing + wrong-key rejected),
  and duplicate/extra witnesses AGREE with cardano.
- RECONFIRM (latest eaf8ac3f) of the existing witness-element-arity-leniency finding: amaru accepts a
  malformed 3-element vkey witness that cardano decode-rejects. Fold into that finding's doc as a
  latest-version reconfirm (another-branch doc; enhancement, not new).
- Lesson reinforced: same-txid single-use families need a FULL reset (reset-pair.sh, restarts the
  cardano-ref) between accepted cases, not an amaru-only reset, or cardano masks on "inputs spent".

Builder: dwarf/block_apply/build_r1c.py.
