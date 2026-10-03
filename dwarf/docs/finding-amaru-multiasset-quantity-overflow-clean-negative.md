# Clean-negative: amaru native-asset quantity-accumulator overflow is NOT a DoS (2026-10-03)

**Hypothesis (user-approved, to verify empirically):** accumulating ONE native-asset quantity past
`u64::MAX` panics amaru's ledger thread at `amaru-kernel` `checked_add(...).unwrap_or_else(|| unreachable!("multi-asset quantity overflow"))` (cited value.rs:201, 0918 tree), reached via `add_mint`/`consume_value` — a HIGH remote DoS.

**Verdict: REFUTED. No crash, no DoS.** amaru (eaf8ac3f / 0925) rejects cleanly at phase-one
value-conservation and stays alive; it holds per-asset balances **beyond u64::MAX** without panicking.

## Substrate

Asset X = (policy `d667c38e38d5712f10a038538e8d5de6467dfb6f2ccf9b70aa3d26ed`, name `MINT`) — the same
asset as the graded-AGREE `mint-int64-max`. A forged setup block (point_hash
`9dc7a8dd606330c55390d83e55cccd71bcfa5286ea2ddf630b69269870243fa7`, slot 1209, height 214, parent
1000/`181e9b48`) carrying **two txs** — `setup-mint-A` (mint i64::MAX X -> A#0) and `setup-mint-B`
(spend A#1, mint i64::MAX X -> B#0) — was applied to amaru (fresh GOLDEN). **Intra-block chaining
worked** (B spends A's same-block output); the trigger hydrating A#0+B#0 is the proof the block applied.
`amaru` does NOT mempool-chain (it hydrates UTxO from the confirmed ledger only), which is why the
setup had to be block-applied rather than submit-chained.

## Observations (verify-don't-predict)

| tx | consumed/produced X | amaru | cardano |
|----|---------------------|-------|---------|
| `trigger-overflow` (out X=1) | consumed 2^64, net = **u64::MAX** (2^64-1) | **400 reject** "value not preserved: balance=[MINT: 18446744073709551615]", **ALIVE** | 400 DecoderFailure, ALIVE |
| `trigger-net-2pow64` (ADA-only out) | consumed 2^64, net = **2^64** (over u64::MAX) | **400 reject** "value not preserved: balance=[MINT: 18446744073709551616]", **ALIVE** | — |
| `trigger-just-under` (control) | net = 0, output 2*i64::MAX = 2^64-2 | **202 ACCEPT**, ALIVE | 202 ACCEPT, ALIVE |

No `panicked`, no `multi-asset quantity overflow`, no `checked_add`/`unreachable` in
`/tmp/amaru-pair1.log` for any trigger. amaru's reported balance is accurate even at **2^64**
(18446744073709551616), i.e. the value balance is held in an arbitrary-precision / wide integer, not a
naive `u64` — so the cited `checked_add` is either not on this path or operates on a non-`u64` type on
0925.

## Where each stops

- **amaru:** phase-one validation, value-conservation (`value not preserved`). Not decode, not
  value-size, not a crash. Rejects and stays alive.
- **cardano:** rejects at submit but surfaces as `DecoderFailure` — cardano's MaryValue IS `Word64`-bounded,
  so its reject *reply* cannot serialize the >=2^64 balance (the same Word64 error-channel seam as the
  ExUnits finding). Node healthy. (Ironically amaru is *more* robust here than cardano's error channel.)

## Conclusion

No new finding. amaru's native-asset quantity accumulation does not overflow-panic and is not a DoS on
this construction (net balances of 2^64-1 and 2^64 both rejected cleanly, alive). An over-`u64::MAX`
single-asset *output* is unreachable because such a tx cannot conserve value (rejected before any store
write). Fixtures retained as clean-negative / conformance evidence; do not re-probe without a new reach.
