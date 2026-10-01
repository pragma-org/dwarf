# R1b — value / int64 knife-edges (2026-10-01, funded store-f, live submit)

Probing whether value fields other than the output-coin hit the coin-overflow `value.rs:293`
`lovelace_to_i64` unreachable (same finding, broader trigger) or a new crash site. Verified amaru
reject reasons / panic sites, not just codes.

| case | amaru | classification |
|------|-------|----------------|
| fee = 2^63 | CRASH — "entered unreachable code: Lovelace exceeds i64::MAX: 9223372036854775808" | SAME-TRIGGER (value.rs:293 lovelace_to_i64, via the fee field) |
| output coin = 2^64-1 (u64 max) | CRASH — "Lovelace exceeds i64::MAX: 18446744073709551615" | SAME-TRIGGER (same unreachable, u64-max value) |
| two outputs 2^62 each (sum 2^63) | reject — "value not preserved: balance = (-9223172036855775808, [])" | CLEAN-NEGATIVE — summed in a wider type, rejected on conservation, NO crash |
| mint quantity = 2^63 (native policy) | reject — "Invalid CBOR transaction: 9223372036854775808 overflows target type ... u64 to i64" | CLEAN-NEGATIVE — mint path catches the overflow GRACEFULLY at decode, no unreachable |

## Verdict
- NO new crash site; NO new finding (no GHSA).
- ENHANCEMENT to the existing coin-overflow finding: the `value.rs:293 lovelace_to_i64` unreachable
  fires on ANY single value field > i64::MAX — confirmed via the FEE field and a u64-max output coin,
  not only the originally-filed output-coin trigger. (Fold these two broader triggers into the
  coin-overflow finding doc at consolidation — that doc lives on another branch.)
- BOUNDARY (clean-negatives that sharpen the finding): the crash is per-field (the single-value
  conversion). It does NOT fire on the output SUM (amaru balances in a wider type) nor on the MINT
  quantity (amaru's CBOR decoder rejects the u64->i64 overflow gracefully). So the defect is specifically
  the un-guarded `lovelace_to_i64` on a single coin/fee value, not a general value-arithmetic gap.

Builder: dwarf/block_apply/build_r1b.py.
