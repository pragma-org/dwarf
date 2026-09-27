"""Write gov_proposal_corpus.json from the signed txs build.sh + edits.py just produced.

Expected verdicts, reason classes and the parity token are the rule each case is DESIGNED to hit;
the nodes' actual responses are graded by workload/gov_proposal_differential.py, never here.
"""
import hashlib, json, os
from pathlib import Path

HERE = Path(__file__).resolve().parent
E = os.environ
RET, UNREG, FAKE, DEP, GUARD = E["RET"], E["UNREG"], E["FAKE"], int(E["DEP"]), E["GUARD"]

# case_id: (expected, reason_classes, parity token, rule, note)
CASES = {
    "deposit-low": ("reject", ["deposit"], str(DEP - 1), "GOV ProposalDepositIncorrect",
        "InfoAction declaring govActionDeposit - 1, tx balanced to the DECLARED deposit"),
    "deposit-high": ("reject", ["deposit"], str(DEP + 1), "GOV ProposalDepositIncorrect",
        "InfoAction declaring govActionDeposit + 1, tx balanced to the declared deposit"),
    "return-unregistered": ("reject", ["return_account"], UNREG, "GOV ProposalReturnAccountDoesNotExist",
        "InfoAction returning to a never-registered stake key (no same-tx registration)"),
    "return-wrong-network": ("reject", ["return_network"], None, "GOV ProposalProcedureNetworkIdMismatch",
        "return account = the (same-tx registered) key's MAINNET reward address"),
    "hardfork-prev-nonexistent": ("reject", ["prev_action"], FAKE, "GOV InvalidPrevGovActionId",
        "HardFork 11.0 naming a nonexistent prev action (root is null)"),
    "noconfidence-prev-nonexistent": ("reject", ["prev_action"], FAKE, "GOV InvalidPrevGovActionId",
        "NoConfidence naming a nonexistent prev action (committee root is null)"),
    "constitution-prev-nonexistent": ("reject", ["prev_action"], FAKE, "GOV InvalidPrevGovActionId",
        "NewConstitution naming a nonexistent prev action (constitution root is null)"),
    "hardfork-skip-major": ("reject", ["cant_follow"], None, "GOV ProposalCantFollow",
        "HardFork 10.0 -> 12.0 (skips a major version)"),
    "hardfork-minor-on-major-bump": ("reject", ["cant_follow"], None, "GOV ProposalCantFollow",
        "HardFork 10.0 -> 11.1 (a major bump must reset minor to 0)"),
    "hardfork-same-version": ("reject", ["cant_follow"], None, "GOV ProposalCantFollow",
        "HardFork 10.0 -> 10.0 (not a successor)"),
    "committee-expiry-too-small": ("reject", ["expiry"], None, "GOV ExpirationEpochTooSmall",
        "UpdateCommittee adding a member expiring at epoch 1 (<= current on BOTH frozen stores: "
        "amaru epoch 2, cardano epoch 3; epoch 3 is deliberately avoided)"),
    "committee-conflicting": ("reject", ["conflicting_committee"], None, "GOV ConflictingCommitteeUpdate",
        "UpdateCommittee adding AND removing sitting member 349e55f8 (script)"),
    "ppupdate-no-policy": ("reject", ["policy_hash"], GUARD, "GOV InvalidGuardrailsScriptHash",
        "ParameterChange (maxTxSize 16385) with NO policy hash; constitution has guardrails fa24fb30"),
    "ppupdate-wrong-policy": ("reject", ["policy_hash"], GUARD, "GOV InvalidGuardrailsScriptHash",
        "ParameterChange with an all-zero policy hash"),
    "ppupdate-malformed-zero": ("reject", ["malformed", "policy_hash"], None,
        "GOV MalformedProposal and/or InvalidGuardrailsScriptHash (co-occurring)",
        "ParameterChange maxTxSize 0 with no policy hash: two rules apply (amaru checks well-formedness "
        "first), so the reported reason is a PRECEDENCE observation"),
    "treasury-no-policy": ("reject", ["policy_hash"], GUARD, "GOV InvalidGuardrailsScriptHash",
        "TreasuryWithdrawal of 1 ADA to the (same-tx registered) return account, no policy hash"),
    "anchor-url-129": ("decode_reject", [], None, "CDDL url .size (0..128)",
        "anchor url 129 bytes; otherwise valid (a lenient decoder would ACCEPT)"),
    "anchor-hash-31": ("decode_reject", [], None, "CDDL hash32", "anchor data hash 31 bytes; otherwise valid"),
    # controls (expected accept; single-use)
    "info-valid": ("accept", [], None, "control", "stake-reg + InfoAction returning to it (same tx)"),
    "hardfork-major-valid": ("accept", [], None, "control", "HardFork 10.0 -> 11.0, prev null"),
    "hardfork-minor-valid": ("accept", [], None, "control", "HardFork 10.0 -> 10.1, prev null"),
    "committee-add-valid": ("accept", [], None, "control",
        "UpdateCommittee adding a fresh cold key expiring at epoch 50, threshold 2/3, prev null"),
    "two-proposals-valid": ("accept", [], None, "control", "InfoAction + HardFork 11.0 in one tx (2 deposits)"),
    "anchor-url-128-valid": ("accept", [], None, "control (url boundary)", "anchor url of exactly 128 bytes"),
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

(HERE / "gov_proposal_corpus.json").write_text(json.dumps({
    "description": "Conway governance-PROPOSAL phase-1 differential family (cardano-node vs Amaru). "
    "Every case spends the committed funding UTxO (fee 300000 >> min, no validity interval) and "
    "registers its return account in the same tx (the substrate has no usable registered reward "
    "account; both ledgers run GOV after CERTS). Gov state at the freeze: pv 10.0, 0 proposals, all "
    "roots null. Violations and decode edges idempotent; controls single-use. See "
    "dwarf/docs/gov-proposal-phase1-differential-family.md.",
    "return_account": RET, "cases": cases}, indent=1) + "\n")
print(f"wrote {len(cases)} cases")
