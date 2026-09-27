# PlutusData (datum / redeemer) CBOR canonicalization & decode-strictness differential

The Plutus slice of the decode-strictness surface (distinct from tx-structure CBOR and the
integrity-hash/languageViews lane): does Amaru's PlutusData decoder accept a non-canonical encoding
cardano-node rejects, decode it to different data, or compute a different datum hash? Any of those
is a phase-2 execution or datum-hash consensus split. Non-canonical / malformed PlutusData is
spliced into a redeemer or a supplied datum by byte surgery (body untouched → signatures valid);
driver `workload/plutusdata_differential.py`.

> **STATUS (2026-09-27): 9/9 AGREE, no divergence.** Amaru v10.11.20260925 (`eaf8ac3f`) matches
> cardano-node 11.1.2 (`fef83fed`) on PlutusData decode strictness and datum-hashing. Graded on
> refpair; evidence `fixture/plutusdata/graded-2026-09-27.json`.

## Cases & result

| case | crafted PlutusData | expected | result |
|---|---|---|---|
| pd-canonical-mint | canonical unit redeemer (control) | accept | AGREE |
| pd-canonical-datum | datum-hash spend, canonical supplied datum (control) | accept | AGREE |
| pd-indefinite-array | unit as tag121 + **indefinite** array | reject | AGREE — decode-lenient on both, both reject via **integrity hash** |
| pd-nonminimal-tag | tag121 via a **non-minimal** 2-byte arg | reject | AGREE — integrity hash |
| pd-nonminimal-int | int 0 as a **non-minimal** uint64 | reject | AGREE — integrity hash |
| pd-indefinite-bytes | bytestring via **indefinite** chunks | reject | AGREE — integrity hash |
| pd-dup-map-key | map with a **duplicate key** | reject | AGREE — both decode-accept (neither strict-rejects dup keys), both reject via integrity hash |
| pd-unchunked-65b | 65-byte bytestring in **one chunk** (>64) | reject | AGREE — **both DECODE-REJECT** (64-byte PlutusData chunk limit, amaru: "…exceeds the 64-byte limit") |
| pd-datum-noncanon | datum-hash spend, **non-canonical** supplied datum | reject | AGREE — both reject; both hash the supplied bytes to the same value (`1b442147…`) → extraneous/not-allowed supplemental datum |

Key positive results:
- **The 64-byte PlutusData bytestring chunk limit is enforced at decode by both** (the only
  decode-layer rejection here); amaru gives a precise message.
- **Both are equally lenient** at decode on indefinite-length, non-minimal-int/tag, and
  duplicate-map-key PlutusData, and **both catch the non-canonical encoding via the script-integrity
  hash** — identical verdict.
- **Datum-hashing is over the supplied bytes on both:** a non-canonical supplied datum hashes to the
  same value (`1b442147…`) on both nodes and is rejected as an extraneous supplemental datum — no
  re-canonicalization and no datum-hash split. (cardano reports the fuller failure set
  `{PPViewHashesDontMatch, NotAllowedSupplementalDatums, MissingRequiredDatums}`; amaru reports the
  single "extraneous supplemental datums" naming the same hash — the failure-set-vs-single-reason
  pattern, graded AGREE by class-set intersection.)

## Boundary

PlutusData in redeemers/datums is always under the script-integrity hash, so a raw byte-edit is
caught by the integrity hash on both nodes. The **pure decoder-acceptance** question (would a node
accept a non-canonical encoding *with a matching integrity hash*) needs the integrity hash
recomputed the way each node does it (the languageViews / integrity-hash lane) and is not isolated
here; within reach, both nodes' verdicts on the same crafted bytes agree.

## Coverage boundary — N2C (node-to-client)

Amaru's `node run` exposes **no node-to-client socket / LocalStateQuery interface** — only
node-to-node (N2N) plus the HTTP submit API. There is no N2C surface to differential-test, and
wallet-style local-state-query clients cannot use Amaru the way they use cardano-node.

## Reproduce

```
# base Plutus mint (unit redeemer) + datum-hash spend, then splice non-canonical PlutusData:
fixture/plutusdata/pd_craft2.py ; fixture/plutusdata/pd_datum2.py   # cbor2 in a venv (/tmp/collat-venv)
/home/nigel/rebake-refscript/reset-refpair.sh
cd workload && python3 plutusdata_differential.py --corpus ../fixture/plutusdata --amaru :3214 --cardano :8114
python3 plutusdata_differential.py --corpus ../fixture/plutusdata --control pd-canonical-mint …   # accepts, per reset
```
Exit 0 all agree / 1 divergence / 2 inconclusive. Keys testnet-only.
