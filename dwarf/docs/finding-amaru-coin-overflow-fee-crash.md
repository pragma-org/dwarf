# Finding: Amaru node crash (DoS) on a transaction whose fee (or coin) exceeds i64::MAX

**Severity: HIGH** (remote, unauthenticated, single-transaction node crash / liveness).
**Status: CONFIRMED, reproducible** (2/2 with full amaru relaunch between runs) on a dedicated,
mempool-isolated pair, 2026-09-30.
**Affected:** Amaru `v10.11.0` git `eaf8ac3f` (git_dirty=false, confirmed from the running
binary's `build.version` log). **Reference:** cardano-node 11.1.2 (`fef83fed`) rejects the identical
transaction gracefully (`ValueNotConservedUTxO`) and stays up.

## Summary

A single, structurally valid Conway transaction whose **fee field is ≥ 2^63** (`i64::MAX + 1`)
crashes the whole Amaru node. The `ledger` thread panics at
`crates/amaru-kernel/src/cardano/value.rs:293`, the process exits, and the submit API becomes
unreachable. cardano-node decodes `Coin` as `Word64`, finds the fee is not value-conserved, and
returns an ordinary phase-1 reject.

This is a **NEW REACH of the same panic-DoS class** as two findings already in hand — the
verification-key non-curve-point crash (`finding-amaru-vkey-noncurve-point-crash.md`) and the
stake-address-output latent panic (`finding-amaru-stakeaddr-output.md`). Same class (an amaru
`unreachable!`/`expect` on attacker-controlled, decode-valid input reachable from mempool submit),
**distinct entry point**: the balance/fee **arithmetic** path, not witness decode or output
resolution.

## Root cause (source, amaru `eaf8ac3f`)

Amaru's `Balance.coin` accumulator is an `i64`
(`crates/amaru-kernel/src/cardano/value.rs`). Fee handling reaches
`context.produce_lovelace(fees)`:

- `crates/amaru-ledger/src/rules/transaction/phase_one/fees.rs:54` — after the `fees < minimum`
  check passes (2^63 ≫ min), `context.produce_lovelace(fees)`.
- `crates/amaru-ledger/src/context/default/validation.rs:472-474` — `produce_lovelace` does
  `self.balance -= &Value::Coin(amount)`, which sub-tracts through the i64 accumulator.
- `crates/amaru-kernel/src/cardano/value.rs:292-293` — the conversion:
  ```rust
  fn lovelace_to_i64(amount: u64) -> i64 {
      i64::try_from(amount).unwrap_or_else(|_| unreachable!("Lovelace exceeds i64::MAX: {amount}"))
  }
  ```
  Any `Coin > i64::MAX` (2^63-1) makes `i64::try_from` fail and hits `unreachable!` → panic.

The release profile (`Cargo.toml [profile.release]`) sets no `overflow-checks`, and the mempool
calls `ledger.validate_tx` directly with no `catch_unwind` outside tests
(`crates/amaru-consensus/src/stages/mempool/stage.rs`). `fees::execute` runs **after** input
resolution but **before** the verification-key-witness check
(`crates/amaru-ledger/src/rules/transaction/phase_one/mod.rs`: `inputs → fees → … →
verification_key_witness`), so the panic fires on any decode-valid transaction that spends a
resolvable input — no valid signature over the edited body is even required to reach it (we
re-sign anyway, to isolate the fee as the sole cause).

The same `unreachable!` guards every coin that flows through the balance accumulator (outputs,
donation, collateral return), so a ≥ 2^63 value in any of those fields is the same class of crash;
this finding demonstrates the fee entry point.

## Reproduction

Fixtures (`antithesis/cardano_amaru_adversarial/fixture/coin_overflow/`) are derived from the
committed `mp-base.tx` (which spends the funding UTxO `9708b921…c4a1#0`) by editing **only** the fee
field (body key 2) and re-signing — `txedit.self_check` + an Ed25519 signature verification confirm
each case differs from `mp-base` solely in the fee:

| case | fee | expected |
|---|---|---|
| `fee-i64max` | 2^63 − 1 (i64::MAX) | reject, **no panic** (controlled negative) |
| `fee-i64over` | 2^63 | **amaru crash** / cardano clean reject |
| `fee-u64max` | 2^64 − 1 | amaru crash / cardano clean reject |

Driver: `workload/coin_overflow_differential.py` (reuses `mixed_phase1` transport + classification;
crash oracle = amaru `UNAVAILABLE` + `alive_after=false` on a liveness re-probe + a panic string in
the host log `/tmp/amaru-pair1.log`).

Run (pair1: amaru submit `:3210`, cardano submit `:8110`):

```
# control (idempotent, no crash):
python coin_overflow_differential.py --case fee-i64max --amaru-log /tmp/amaru-pair1.log
  -> AGREE: both phase1_reject, amaru alive.
     amaru:   "value not preserved: balance = (-9223372036853775807, [])"
     cardano: ConwayUtxowFailure … ValueNotConservedUTxO

# crash (relaunch amaru between runs: reset-pair.sh pair1 --amaru-only):
python coin_overflow_differential.py --case fee-i64over --amaru-log /tmp/amaru-pair1.log
  -> CRASH-DIVERGENCE (2/2, PIDs 191128 then 191688):
     amaru:   classification "unavailable" (RemoteDisconnected), alive_after=false
     cardano: phase1_reject (ValueNotConservedUTxO), alive_after=true
     panic log:
       thread 'ledger' (…) panicked at crates/amaru-kernel/src/cardano/value.rs:293:46:
       internal error: entered unreachable code: Lovelace exceeds i64::MAX: 9223372036854775808
```

The `fee-i64max` control rejects **without** a panic (amaru computes
`balance = -(2^63-1 - 1_000_000)`, i.e. `lovelace_to_i64(2^63-1) = i64::MAX` succeeds), which pins
the crash threshold to exactly 2^63.

## Confounds ruled out

- **Provenance:** the running binary logs `git_commit=eaf8ac3fa309f2a174c045454c276194c9312b8d`,
  `git_dirty=false`.
- **Determinism:** 2/2 identical crashes, each on a freshly relaunched amaru process (distinct PID).
- **Threshold:** the 2^63-1 control does not crash; only ≥ 2^63 does.
- **Fairness:** both nodes frozen at the same rebake chain, same funding input, same bytes; cardano
  rejects cleanly while amaru crashes.
- **Not a signature/decoder artifact:** the tx is re-signed (valid witness) and decode-valid; the
  panic is in fee arithmetic, reached before the signature check.

## Impact

Any peer that can reach the submit API (or, on a forwarding path, relay a transaction to an amaru
node) can crash it with one ~265-byte transaction that spends nothing of theirs and costs nothing.
Repeated submission is a trivial, deterministic remote DoS against amaru relays/nodes. Block
validation would hit the same panic on apply if such a tx were ever included, but block producers
(cardano-node) reject it first, so the realized surface is the mempool/submit ingress.

## Remediation

Replace the `unreachable!` in `lovelace_to_i64` with a fallible path that returns a validation
error (e.g. treat a `Coin > i64::MAX` as value-not-preserved / an overflow reject), and audit every
`unreachable!`/`expect` on the `Value`/`Balance` arithmetic path (add, sub, mint) for the same
class. Consider enabling `overflow-checks` in release for the ledger crates.

## Provenance / not filed upstream

Found by the DWARF ledger-lane differential sweep, 2026-09-30, on the isolated pair1
(amaru eaf8ac3f `:3210` / cardano-node 11.1.2 `:8110`). Not filed upstream.
