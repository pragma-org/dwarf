# Plutus CEK-interpreter: VALUE & structure-semantics differential

A **depth** test of the CEK interpreter core (companion to the div/mod sign-semantics family):
does Amaru's VM compute the same *value* and preserve the same *structure* as cardano-node for
bignum results, ByteString value edges, and deeply-nested PlutusData? Operands are fed via the
**redeemer** (runtime values) so aiken cannot constant-fold — the VM must actually evaluate each
builtin. Validators assert the Cardano-spec result; a value or structure divergence would flip the
assertion on one node, accepting on one and rejecting on the other — an `is_valid` **consensus
split**. The two out-of-range cases double as **VM liveness checks** (the builtin must error, not
crash). Driver `workload/plutus_differential.py`.

> **STATUS (2026-09-27): 17/17 AGREE, no divergence, no VM crash.** Amaru v10.11.20260925
> (`eaf8ac3f`) computes the Cardano-spec value/structure for every case. Graded on pair2; evidence
> `fixture/cek_value/graded-2026-09-27.json`.

## Cases & result

**Bignum VALUE** (arbitrary-precision; Plutus ints never overflow int64):

| case | assertion | result |
|---|---|---|
| bignum-mul-ok | `multiplyInteger(2^128, 2^128) == 2^256` (78-digit result) | AGREE (both accept) |
| bignum-modmul-ok | `modInteger(2^128·2^128, 2^127−1)` == spec value | AGREE (both accept) |
| bignum-mul-wrong | asserts `2^256 + 1` | AGREE (both reject) |
| bignum-modmul-wrong | asserts modular result `+ 1` | AGREE (both reject) |

**ByteString VALUE edges** (`bs = 00112233445566778899aabbccddeeff`):

| case | assertion | result |
|---|---|---|
| bs-index-ok | `indexByteString(bs, 5) == 0x55` | AGREE (both accept) |
| bs-index-wrong | asserts wrong byte | AGREE (both reject) |
| bs-index-oob | `indexByteString(bs, 16)` **out of range** | AGREE (both reject — builtin errors, **amaru live, no crash**) |
| bs-slice-mid | `sliceByteString(2, 4)` length == 4 | AGREE (both accept) |
| bs-slice-clamp | `sliceByteString(14, 10)` clamps → length 2 | AGREE (both accept) |
| bs-slice-past | `sliceByteString(20, 4)` start past end → length 0 | AGREE (both accept) |
| i2b-be-w0 | `byteStringToInteger(BE, integerToByteString(BE, 0, 258)) == 258` (minimal width) | AGREE (both accept) |
| i2b-le-w0 | little-endian minimal-width round-trip → 258 | AGREE (both accept) |
| i2b-be-w4 | fixed width 4 round-trip → 258 | AGREE (both accept) |
| i2b-oversized | width 10 (oversized pad) round-trip → 258 | AGREE (both accept) |
| i2b-toosmall | `integerToByteString(BE, 1, 258)` width too small | AGREE (both reject — builtin errors, **amaru live, no crash**) |

**Deep-nested PlutusData** (construct + traverse):

| case | assertion | result |
|---|---|---|
| nested-ok | depth-5 nested list `[[[[[42]]]]]`, `unListData`×5 + `unIData` leaf == 42 | AGREE (both accept) |
| nested-wrong | depth-5 leaf == 43 | AGREE (both reject) |

Endianness legend: `en=1` big-endian, `en=0` little-endian; width `0` = minimal. The wrong-value
controls confirm the assertions are non-vacuous (a VM using a different value would accept them).

## Reproduce

```
# aiken module cekval (bignum_mul, bignum_modmul, bs_index, bs_slice_len, i2b_b2i, nested_traverse)
/home/nigel/reset-pair.sh pair2
cd workload
python3 plutus_differential.py --corpus ../fixture/cek_value \
  --amaru http://localhost:3211/api/submit/tx --cardano http://localhost:8111/api/submit/tx   # 6 rejects
python3 plutus_differential.py --corpus ../fixture/cek_value --single bignum-mul-ok …          # accepts, one per reset
```
Exit 0 all agree / 1 divergence / 2 inconclusive. Keys testnet-only.
