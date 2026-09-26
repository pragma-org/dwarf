# Conway governance-signature phase-1 differential family

A DWARF phase-1 differential family (extends `workload/mixed_phase1.py`) covering the
**reachable slice** of Conway governance-signature validation: gov-cert **required-witness**
checking. Coverage addition, not a finding — a conformance pass with a verdict +
reason-class parity oracle.

## Reachability (probe-first, 2026-09-26)

Governance signature checks need specific ledger state / keys. What the baked substrate
(frozen cardano reference + Amaru 0918 `testnet_42` store, both sharing the re-bake genesis)
actually reaches:

- **Committee hot-key authorization — NOT reachable.** The baked Conway committee is 7
  members that are **all script hashes** (`scriptHash-349e55f8…`, threshold 2/3), confirmed
  in `conway-genesis.json` and live `gov-state`. We do not have the committee member
  scripts, so we cannot sign/authorize as a committee member. Would need a genesis re-bake
  with **key-hashed** committee members (or the scripts).
- **DRep voting — NOT reachable on this substrate.** Live `drep-state --all-dreps` is `[]`
  (the "dreps=2" in bootstrap logs is Conway's two PREDEFINED abstain / no-confidence
  DReps, not signable); `gov-state` has 0 proposals; this genesis has no `initialDReps`. A
  vote needs a **registered DRep + an open gov action present in BOTH frozen stores**, but
  the two frozen ledgers cannot share on-chain setup (register + propose would have to be
  mined identically into both before the freeze). Would need a genesis re-bake with
  pre-registered DReps + a pre-opened gov action mined into the chain before the freeze.
- **Gov-cert required-witness — REACHABLE.** Registering a fresh DRep needs that DRep
  credential's own vkey witness, and "not registered" is the correct pre-state, so **only**
  the UTXOW witness rule applies (no pre-existing DRep/proposal needed). This family covers
  it.

The two blocked angles are where governance-specific divergences are most likely to live;
re-baking genesis for them is a scope/resource decision surfaced to the operator.

## Cases & result

All spend the committed funding UTxO `9708b921…#0`; **fee 300000 ≫ min** so a rejection is
unambiguously the witness rule (`MissingVKeyWitness`), not fee/size. Violations idempotent;
satisfied controls single-use (an accept consumes the UTxO — one per fresh mempool).

| case | expected | cardano | amaru | parity |
|---|---|---|---|---|
| drepreg-missing-witness (DRep witness omitted) | reject | `MissingVKeyWitnessesUTXOW(KeyHash 9cf93249…)` | `verification key witness: missing required signatures … [9cf93249…]` | verdict + **same cred** |
| drepreg-wrong-key (signed by a different drep key) | reject | `MissingVKeyWitnessesUTXOW(9cf93249…)` | same cred `9cf93249…` | verdict + same cred |
| drepreg-multi-partial (2 reg certs, only 1 drep signs) | reject | `MissingVKeyWitnessesUTXOW(cd3e0c1e…)` | same cred `cd3e0c1e…` | verdict + same cred |
| drepreg-present (correct DRep witness) | accept | 202 | 202 | verdict |
| drepreg-multi-both (both drep witnesses) | accept | 202 | 202 | verdict |

**Result: Amaru 10.11.20260903 (`ea1f34e4`) is CONFORMANT with cardano-node 11.1.2** on
gov-cert required-witness validation — verdict + reason-class parity (both reject with
`MissingVKeyWitness` naming the **same** governance credential; both accept the controls).
Notably the wrong-key case confirms both require a witness from the **specific** credential,
not merely any signature.

## Reason-reporting note (DRep update / retirement — masked, documented not tested)

DRep **update** and **retirement** certs need the DRep already registered; on the frozen
store (no registered DRep) their missing-witness test is **masked** by `DRepNotRegistered`.
Both nodes still reject (verdict parity holds), but they report the co-occurring failures
differently: cardano-node emits a failure **set** `{ConwayDRepNotRegistered, MissingVKey…}`,
while Amaru reports a **single** reason — for `update` it surfaces the witness error, for
`retirement` the not-registered error. This is a reason-*reporting* difference (like the
opcert precedence observation), **not** a verdict divergence, and it is confounded by the
not-registered state, so update/retirement are excluded from the corpus. Testing their
witness rule cleanly would need a re-bake with a pre-registered DRep.

## Substrate caveats (shared with the native-script family)

- Frozen nodes use their **ledger-tip slot** for validity checks; not relevant here (no
  timelocks), but keep fees ≫ min and other fields valid so a rejection is attributable to
  the witness rule.
- Amaru 0918 emits verbose phase-1 errors; `mixed_phase1`'s `"phase one validation"` marker
  (added for the native-script family) classifies them as `phase1_reject`.

## Reproduce

Bring up the frozen substrate (`prepare-rebake.sh`), then:

```
docker restart <cardano-ref> <amaru>       # fresh mempools (accepts are single-use)
cd workload && python3 governance_differential.py \
  --amaru http://localhost:3012/api/submit/tx --cardano http://localhost:8090/api/submit/tx
# -> "VIOLATION VERDICT+REASON PARITY (3 cases): ALL AGREE"
```

Rebuild: `cardano-cli conway governance drep registration-certificate
--drep-verification-key-file keys/drep.vkey --key-reg-deposit-amt 500000000 --out-file
certs/drepreg.cert`; `build-raw --tx-in 9708b921…#0 --tx-out <addr>+<total-fee-deposit>
--certificate-file certs/drepreg.cert --fee 300000`; `sign` with the committed
`fixture/funding/payment.skey` (add `keys/drep.skey` for the satisfied control; omit it, or
use `keys/wrong.skey`, for the violations). Committed keys/certs are testnet-only, no value.
