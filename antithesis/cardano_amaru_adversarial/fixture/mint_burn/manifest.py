"""Write mint_burn_corpus.json from the signed txs build.sh + edits.py just produced.

Expected verdicts, reason classes and the parity token (policy id / amount) are the rule each case
is DESIGNED to hit; the nodes' actual responses are graded by workload/mint_burn_differential.py.
"""
import hashlib, json, os
from pathlib import Path

HERE = Path(__file__).resolve().parent
E = os.environ
POL, OTH, MIN = E["POL"], E["OTH"], int(E["MIN"])

# case_id: (expected, reason_classes, parity token, rule, note)
CASES = {
    # A. minting policy not satisfied (the policy SIG case lives in the native-script family)
    "mint-script-missing": ("reject", ["missing_script"], POL, "UTXOW MissingScriptWitnesses",
        "mint 10 MINT, policy key signs, native policy script omitted from the witness set"),
    "mint-script-wrong": ("reject", ["missing_script", "extraneous_script"], POL,
        "UTXOW MissingScriptWitnesses + ExtraneousScriptWitnesses (co-occurring)",
        "a different native script supplied in place of the policy script: both rules apply, so the "
        "reported reason is a PRECEDENCE observation"),
    "extraneous-script-no-mint": ("reject", ["extraneous_script"], POL,
        "UTXOW ExtraneousScriptWitnesses", "ADA-only tx, nothing minted, policy script supplied"),
    # B. burn / quantity edges
    "burn-nonexistent": ("reject", ["value_not_conserved"], POL, "UTXO ValueNotConserved",
        "burn 5 MINT; inputs hold none (ADA balanced)"),
    "burn-int64-min": ("reject", ["value_not_conserved"], POL, "UTXO ValueNotConserved (int64 edge)",
        "burn -2^63 MINT (nonZeroInt64 minimum); inputs hold none. Divergence candidate: overflow "
        "in either node's value arithmetic"),
    "mint-zero-qty": ("decode_reject", [], None, "CDDL mint nonZeroInt64",
        "mint 0 MINT; otherwise valid (a lenient decoder would ACCEPT)"),
    "mint-empty-asset-map": ("decode_reject", [], None, "CDDL mint inner map non-empty",
        "mint {policy: {}}; otherwise valid"),
    "mint-empty-map": ("decode_reject", [], None, "CDDL mint non-empty", "mint {}; otherwise valid"),
    "asset-name-33b": ("decode_reject", [], None, "CDDL asset_name bytes .size (0..32)",
        "mint + output 10 of a 33-byte asset name; otherwise valid"),
    "output-zero-qty": ("decode_reject", [], None, "CDDL output multiasset positive_coin",
        "output carries 0 MINT, nothing minted; otherwise valid"),
    # C. asset value not preserved (ADA exact; only the multi-asset side is wrong)
    "asset-surplus": ("reject", ["value_not_conserved"], POL, "UTXO ValueNotConserved",
        "mint 10 MINT, output 11"),
    "asset-deficit": ("reject", ["value_not_conserved"], POL, "UTXO ValueNotConserved",
        "mint 10 MINT, output 9"),
    "asset-relabel": ("reject", ["value_not_conserved"], POL, "UTXO ValueNotConserved",
        "mint 10 MINT, output carries 10 MINTX (same policy, other name)"),
    "unminted-policy-output": ("reject", ["value_not_conserved"], OTH, "UTXO ValueNotConserved",
        "output carries 10 OTHER of an unminted, unwitnessed policy; no mint"),
    # D. multi-asset min-ADA / value size
    "minada-token-below": ("reject", ["output_too_small"], str(MIN - 1), "UTXO BabbageOutputTooSmall",
        f"token output at the multi-asset min-UTxO - 1 ({MIN - 1}; ADA-only min is lower)"),
    "value-too-big": ("reject", ["output_too_big"], "5000", "UTXO OutputTooBig (maxValueSize 5000)",
        "one output carrying 150 x 32-byte asset names (serialised value > 5000 B); both nodes "
        "print the size they computed - recorded, a size mismatch is a serialisation divergence"),
    # controls (expected accept; single-use)
    "mint-valid": ("accept", [], None, "control", "mint 10 MINT, policy script + key witness"),
    "multiasset-mint-valid": ("accept", [], None, "control (multi-asset MINT, not a burn)",
        "mint 10 MINT + 3 MINTB in one tx"),
    "minada-token-at-min": ("accept", [], None, "control (min-UTxO boundary)",
        f"token output at exactly the multi-asset min-UTxO {MIN}"),
}

cases = []
for cid, (expected, classes, token, rule, note) in CASES.items():
    cbor = bytes.fromhex(json.loads((HERE / f"{cid}.tx").read_text())["cborHex"])
    case = {"case_id": cid, "tx_file": f"{cid}.tx", "expected": expected, "input": E["IN"],
            "cbor_size": len(cbor), "cbor_sha256": hashlib.sha256(cbor).hexdigest(),
            "rule": rule, "reason_classes": classes, "note": note}
    if token:
        case["credential"] = token
    cases.append(case)

(HERE / "mint_burn_corpus.json").write_text(json.dumps({
    "description": "Mint/burn + multi-asset value phase-1 differential family (cardano-node vs "
    "Amaru). All cases spend the committed funding UTxO; ADA balanced exactly, fee >> min, no "
    "validity interval, policy key signs every minting case. Violations and decode edges are "
    f"idempotent; controls single-use. policy {POL} (all[sig]), other policy {OTH}. A valid BURN "
    "control is infeasible on the frozen non-forging substrate (no token UTxO can confirm). See "
    "dwarf/docs/mint-burn-value-phase1-differential-family.md.",
    "policy_id": POL, "min_utxo_one_token": MIN, "cases": cases}, indent=1) + "\n")
print(f"wrote {len(cases)} cases")
