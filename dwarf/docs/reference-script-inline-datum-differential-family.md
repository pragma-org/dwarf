# Reference-script + inline-datum spend differential family

The Conway/Babbage way real dApps spend: a script UTxO spent by providing its validator as a
**reference script** (from a reference-input UTxO, not the witness set), and datums carried
**inline** vs by **hash**. Extends `workload/mixed_phase1.py`; driver
`workload/refscript_differential.py` (reuses `plutus_differential`'s grade with a widened
reason table). Runs on a re-baked substrate whose setup tx (mined before the freeze) carries the
script UTxOs — genesis cannot carry datums or reference scripts.

> **STATUS (2026-09-27): 9/9 AGREE.** Amaru v10.11.20260925 (`eaf8ac3f`) is CONFORMANT with
> cardano-node 11.1.2 (`fef83fed`) on reference-script resolution, inline-vs-datum-hash, and the
> `is_valid` collateral paths. **The headline holds: spending a script provided via a reference
> input gives the same verdict as providing it in the witness** (`spendA-witness` ≡
> `spendA-refscript`). Evidence `fixture/refscript/graded-2026-09-27.json`.

## Substrate

Setup tx `9aecf690…` outputs (each 10 ADA): `#0` A_inline (ok scriptAddr, inline datum `d87980`),
`#1` B_hash (ok scriptAddr, datum hash `923918e4…`), `#2` C_ok (reference script = always-succeeds),
`#3` D_fail (fail scriptAddr, inline datum), `#4` C_fail (reference script = always-fails).
Collateral: funding UTxO `9708b921…#0`. ok scriptHash `186e32fa…`, fail scriptHash `ebb18a12…`.

## Cases & result

| # | case | expected | result |
|---|---|---|---|
| 1 | spendA-witness (inline datum, script in witness) | accept | AGREE |
| 2 | spendA-refscript (inline datum, script via reference C_ok) | accept | **AGREE — ≡ #1** |
| 3 | spendB-hash-witness (datum-hash, datum supplied, witness script) | accept | AGREE |
| 4 | spendB-refscript (datum-hash, datum supplied, reference script) | accept | AGREE |
| 5 | spendB-missing-datum (datum-hash, datum omitted) | reject | AGREE (missing datum) |
| 6 | spendD-fail-valid (always-fails, is_valid=true) | reject | AGREE (tag mismatch) |
| 7 | spendD-fail-invalid (always-fails, is_valid=false) | accept | AGREE (collateral consumed) |
| 8 | spendA-wrong-refscript (provide C_fail for an ok-locked UTxO) | reject | AGREE (missing script `186e32fa`) |
| 9 | spendA-refscript-also-refinput (C_ok as script source AND read-only ref) | accept | AGREE |

Accepts return the same tx id from both nodes; each accept consumes its script UTxO so runs one
per mempool reset (fresh on the frozen chain). #1≡#2 is the reference-script-resolution
equivalence; #1 (inline, no witness datum) vs #3 (hash, datum supplied) vs #5 (hash, datum
omitted → reject) is the inline-vs-hash triangle.

## Reason-reporting note (#8, NOT a divergence)

`spendA-wrong-refscript` provides the always-fails reference script for an always-succeeds-locked
UTxO. Both reject. cardano reports a failure **set** — `{PPViewHashesDontMatch, ExtraRedeemers,
MissingScriptWitnessesUTXOW(186e32fa…)}` — while Amaru reports the single
`missing required scripts: missing [186e32fa…]`. Amaru's reason is a member of cardano's set
(same missing script hash), so by class-set intersection the verdict and reason agree; the
difference is cardano's richer failure set, the same failure-set-vs-single-reason pattern seen in
the governance and withdrawal families.

**Harness note:** cardano's set here exceeds `mixed_phase1`'s 400-char reason cap, which hid the
intersecting `MissingScriptWitnesses` (3rd in the set) and produced a *false* REASON-DIVERGENCE on
the first grade. `refscript_differential` therefore reads the full response body (locally; it does
not change the shared cap) so the reason-class intersection is accurate. Regression-tested.

## Reproduce

```
# re-bake carries the 5 script UTxOs (setup tx before freeze). Then:
fixture/refscript/build.sh   # env A_INLINE/B_HASH/C_OK/D_FAIL/C_FAIL = 9aecf690…#0..#4
/home/nigel/rebake-refscript/reset-refpair.sh   # (--amaru-only for a fast reset)
cd workload && python3 refscript_differential.py --corpus ../fixture/refscript --amaru :3214 --cardano :8114
python3 refscript_differential.py --corpus ../fixture/refscript --single spendA-refscript …  # one accept per reset
```

Exit 0 all agree / 1 divergence / 2 inconclusive. Keys testnet-only, no value.

## Independent re-verification

Independently re-verified by a second agent (dwarf-v4-fd): the exact corpus transactions were
re-submitted on refpair (CBOR sha256-matched to this corpus), reading full response bodies,
one submission per reset. All 9 verdicts identical (every accept had matching amaru/cardano tx
ids; #8 mismatched-reference-script rejected by both, class-set intersection agreeing on the
missing script hash). Also re-confirmed on the literal clean labelled serving binary
amaru 10.11.0 (eaf8ac3f).
