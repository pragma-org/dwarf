# Finding: Amaru Plutus VM accepts CEK `case` over built-in-type values at protocol version 10 (missing PV gate) — consensus is_valid divergence

**Status:** genuinely-new finding (advisory-first; public held pending GHSA filing).
**Severity:** HIGH (consensus-critical phase-2 is_valid divergence).
**Target:** amaru v10.11.20260925 (eaf8ac3f) vs cardano-node 11.1.2. Protocol version 10.
**Context:** authorized conformance testing on the local devnet (testnet_42), testnet-only keys; coordinated disclosure to PRAGMA.

## Summary

The Plutus CEK `case` term applied to a **built-in-type value** (e.g. a constant `True`) is **not
supported at protocol version 10** — cardano-node's Plutus VM rejects it. Amaru's CEK machine evaluates
it successfully and ACCEPTS the transaction. Since amaru runs phase-2 at submit (it rejects other
phase-2 failures identically to cardano), this is a true VM-evaluation divergence, not a submit gap:
amaru accepts — and would apply in a block — a phase-2 script that cardano-node rejects. On a mixed
network this is an is_valid consensus split (amaru follows a chain the Haskell reference rejects).

## Evidence (live, 2026-10-01, refpair, clean full reset)

Fixture `case-constant-control.tx` — a PlutusV3 minting script whose Flat body contains an explicit CEK
`case` term over a built-in constant. Script hash `1368c2c88f204f481c552346315ac8eb3336a15baa1c44fed7aaa139`.

- **amaru :3214** → HTTP 202 ACCEPTED (txid e30523158f8610e3…), node alive.
- **cardano-node :8114** → HTTP 400:
  `ConwayUtxowFailure (UtxoFailure (UtxosFailure (ValidationTagMismatch Phase2Valid (FailedUnexpectedly
  (PlutusFailure "The PlutusV3 script failed: ... The plutus evaluation error is: CekError An error has
  occurred: 'case' over a value of a built-in type failed with 'case' on values of built-in types is not
  supported in protocol version 10 ... The protocol version is: Version 10 ...")))))`

Control `case-native-control.tx` — same shape but `case` over a Data/tagged (sum) value, which IS
supported at pv10 — AGREES (both accept). So the divergence is specific to `case` over BUILT-IN-type
values at pv10. (The other phase-2-failing cases in the set — multipurpose-1, case-native-error,
case-native-missing — are rejected by BOTH, confirming amaru's phase-2 path is active and otherwise
conformant.)

## Root cause

cardano-node's Plutus VM gates the CEK `case` term so that, at protocol version 10, `case` is only
permitted over tagged (sum/`Constr`) values, not over built-in-type values; the latter is a later-PV
feature. Amaru's CEK implementation applies `case` to a built-in constant without enforcing that
protocol-version restriction, returning a successful evaluation where the reference VM raises a
CekError. The missing PV gate makes amaru's phase-2 acceptance set a strict superset of the reference's
at pv10.

## Impact

Consensus-critical. A transaction (or block) carrying such a script is accepted by amaru and rejected
by cardano-node at phase-2. On a mixed-client network amaru and cardano-node diverge on is_valid →
chain split / amaru adopts blocks the reference rejects. No crash (silent accept-invalid).

## Reproduce via DWARF

Substrate: the reference-script refpair (amaru :3214 / cardano-node :8114) with the pre-mined script
UTxOs (needs the Plutus/script substrate, not store-f). Run:
`bash dwarf/block_apply/submit_differential_run.sh <fixture>.tx refpair 3214 8114 --no-reset`
(reset the refpair with `rebake-refscript/reset-refpair.sh` between UTxO-reusing fixtures). Expected:
amaru accept / cardano reject (the pv10 CekError above). Scenario:
`ledger-submit-plutus-case-over-builtin-pv10-differential-amaru-cardano-node` (runtime_tx_submit_differential,
expected_target=accept / expected_reference=reject). Fixture committed under
`antithesis/cardano_amaru_adversarial/fixture/plutus_case/`.

## GHSA submission block (DRAFT — to finalise with the template; public HELD until filed)

- **Title:** Amaru Plutus VM accepts CEK `case` over built-in-type values at protocol version 10 (missing protocol-version gate) causing an is_valid consensus divergence from cardano-node
- **Severity / CVSS:** High. CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:N/I:H/A:N (scope-changed integrity: consensus split) — confirm with PRAGMA.
- **CWE:** CWE-670 (Always-Incorrect Control Flow Implementation) / CWE-757 (Selection of Less-Secure Algorithm During Negotiation — missing version gate).
- **Affected products:** amaru (observed v10.11.20260925 / eaf8ac3f) at protocol version 10.
- **CVE:** (request on filing)
- **Description:** At protocol version 10, cardano-node's Plutus VM rejects the CEK `case` term applied
  to built-in-type values ("not supported in protocol version 10"); amaru's CEK machine evaluates it
  and accepts the script. amaru runs phase-2 at submit (other phase-2 failures are rejected identically
  to cardano-node), so this is a genuine VM-semantics divergence. A transaction/block whose Plutus
  script uses `case` over a built-in value is accepted by amaru and rejected by cardano-node, splitting
  is_valid consensus on a mixed network. PoC: `case-constant-control.tx` (PlutusV3, script hash
  1368c2c8…) → amaru 202 / cardano-node phase-2 CekError. Fix: enforce the pv10 restriction on `case`
  over built-in types in amaru's CEK machine to match the reference.

## Source scope (audit of amaru 9eb5971f CEK pv-gating) — narrow, single-site

The missing gate is ONE eval site, not a broad class. amaru gates pv correctly everywhere else:
- `crates/amaru-uplc/src/builtin/default_function.rs:479` `is_available_in(protocol_version)` — builtins ARE pv-gated (>=PV10 / >=PV11 branches). A pv11-only builtin at pv10 is correctly rejected.
- `crates/amaru-uplc/src/flat/decode/decoder.rs:42` `is_constr_case_available(&self)` = `protocol_version >= PROTOCOL_VERSION_10 && machine_version.is_constr_case_available()` — the DECODE-time constr/case gate correctly checks BOTH protocol and machine version.
- DEFECT: `crates/amaru-uplc/src/machine/cek.rs:247` — the CEK eval of `case` over a built-in scrutinee:
  `Value::Con(constant) if self.machine_version.is_constr_case_available() => { ... }`
  gates by the MACHINE version ALONE (`self.machine_version.is_constr_case_available()`), omitting the
  protocol_version conjunct. So at protocol version 10, a script whose machine_version permits SOP/case
  evaluates `case` over a built-in constant successfully — where cardano-node rejects it (pv10). The
  branch falls through to `Value::Constr` (line 236, case-over-Data, correctly allowed pv10) which is why
  the case-over-Data control AGREES; only the built-in-scrutinee arm is mis-gated.

Fix: cek.rs:247 should use the protocol-version-aware check (as decoder.rs:42 does) — i.e. also require
protocol_version to permit case-over-builtin (the reference enables it only at pv >= 11) — instead of
`machine_version` alone.

Scope verdict: case-over-built-in-scrutinee at the CEK eval site is the SPECIFIC defect; builtins and
decode-time constr/case are correctly protocol-gated. Not a broad missing-pv-gate class.
