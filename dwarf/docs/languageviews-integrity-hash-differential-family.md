# Script-integrity-hash / languageViews / cost-model differential family

The highest-yield integrity question: `script_data_hash = hash(redeemers ‖ datums ‖ languageViews)`,
where `languageViews` is the CBOR of each **used** Plutus language's cost model. If Amaru builds
`languageViews` or the hash even slightly differently, one node's computed hash matches the tx
body and the other's does not → that node returns `PPViewHashesDontMatch` while the other **accepts**
= a consensus divergence. Method: a valid tx carries cardano-cli's computed `script_data_hash` in
the body; submitting it to Amaru tests whether Amaru computes the **same** hash. Driver
`workload/lv_differential.py`.

> **STATUS (2026-09-27): 4/4 AGREE, no divergence.** Amaru v10.11.20260925 (`eaf8ac3f`) computes
> `script_data_hash` identically to cardano-node 11.1.2 (`fef83fed`) for single-PlutusV1,
> single-PlutusV3, and **mixed V1+V3** transactions (both cost models in `languageViews`), and both
> reject a corrupted hash. Graded on pair4; evidence `fixture/languageviews/graded-2026-09-27.json`.

## Cases & result

| case | tx | expected | result |
|---|---|---|---|
| v1-mint | mint under a PlutusV1 always-succeeds policy | accept | AGREE (both accept, same tx id) — V1 cost model in languageViews matches |
| v3-mint | mint under a PlutusV3 always-succeeds policy (control) | accept | AGREE |
| **mixed-v1v3** | **one tx minting under BOTH a V1 and a V3 policy** | accept | **AGREE (both accept, same tx id)** — both cost models included/ordered/encoded identically |
| mixed-badhash | mixed-v1v3 with one bit flipped in the body `script_data_hash` (re-signed) | reject | AGREE — both `PPViewHashesDontMatch` / "script integrity hash mismatch" |

The mixed V1+V3 case is the prize: `languageViews` must carry two cost models, and Amaru assembles
and hashes them exactly as cardano-node does (same resulting `script_data_hash`, hence the same tx
id on accept). The corrupted-hash negative confirms both nodes actively compute and check the hash
and reject a mismatch (the same integrity-hash rejection also seen across the PlutusData family).

## Boundary

The chain carries PlutusV1 + PlutusV3 cost models only (no V2), so V1, V3, and their mix are the
reachable `languageViews` combinations. Single-V3 `script_data_hash` conformance is corroborated by
every accepted V3 Plutus tx across the reference-script / ScriptContext / PlutusData / ref-fee
families (all carry a cardano-computed hash that Amaru accepted).

## Reproduce

```
# V1 always-succeeds 4d01000033222220051200120011 ; V3 46450101002499 ; build valid mints, then:
fixture/languageviews/…  (v1-mint, v3-mint, mixed-v1v3; mixed-badhash = flip a bit in body key 11 + re-sign)
/home/nigel/reset-pair.sh pair4     # FULL reset (spends the shared 9708b921)
cd workload && python3 lv_differential.py --corpus ../fixture/languageviews --amaru :3213 --cardano :8113
python3 lv_differential.py --corpus ../fixture/languageviews --control mixed-v1v3 …   # accepts, per reset
```
Exit 0 all agree / 1 divergence / 2 inconclusive. Keys testnet-only.
