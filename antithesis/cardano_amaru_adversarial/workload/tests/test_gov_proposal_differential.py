import hashlib
import json
import sys
import unittest
from pathlib import Path

WORKLOAD_DIR = Path(__file__).resolve().parents[1]
if str(WORKLOAD_DIR) not in sys.path:
    sys.path.insert(0, str(WORKLOAD_DIR))

import gov_proposal_differential as gpd  # noqa: E402
import stake_pool_differential as spd  # noqa: E402

CORPUS = WORKLOAD_DIR.parent / "fixture" / "gov_proposal"
FAKE = "ab" * 32


def result(amaru, cardano, masked=False):
    obs = {"amaru": amaru, "cardano": cardano}
    classes = {o["classification"] for o in obs.values()}
    return {"observations": obs, "masked": masked,
            "both_classifiable": classes <= {"accepted", "phase1_reject", "decode_reject"}}


def rej(reason):
    return {"classification": "phase1_reject", "status": 400, "reason": reason, "detail": reason}


PREV = {"case_id": "p", "expected": "reject", "reason_classes": ["prev_action"], "credential": FAKE}
PRECEDENCE = {"case_id": "m", "expected": "reject", "reason_classes": ["malformed", "policy_hash"]}
DECODE = {"case_id": "d", "expected": "decode_reject", "reason_classes": []}


class GradeTests(unittest.TestCase):
    def test_prev_action_same_id_agrees(self):
        row = gpd.grade(PREV, result(
            rej(f"invalid proposals: invalid previous governance action id: Some(ProposalId {{ {FAKE} }})"),
            rej(f'ConwayGovFailure (InvalidPrevGovActionId (ProposalProcedure {{ ... SafeHash "{FAKE}" }}))')))
        self.assertEqual(row["status"], "AGREE")

    def test_first_failure_vs_failure_set_agrees_by_intersection(self):
        row = gpd.grade(PRECEDENCE, result(
            rej("malformed parameter change proposal: max_transaction_size cannot be 0"),
            rej("[ConwayGovFailure (MalformedProposal ...), ConwayGovFailure (InvalidPolicyHash ...)]")))
        self.assertEqual(row["status"], "AGREE")

    def test_different_gov_rule_is_reason_divergence(self):
        row = gpd.grade(PREV, result(rej("incorrect proposal deposit: provided 1, expected 2"),
                                     rej(f"InvalidPrevGovActionId {FAKE}")))
        self.assertEqual(row["status"], "REASON-DIVERGENCE")

    def test_decode_leniency_is_flagged_by_the_shared_grade(self):
        row = spd.grade(DECODE, result(
            rej("phase one validation: ..."),
            {"classification": "decode_reject", "status": 400, "reason": "DeserialiseFailure"}))
        self.assertEqual((row["status"], row["decode_leniency"]), ("VERDICT-DIVERGENCE", ["amaru"]))

    def test_guardrails_hash_constructor_name_of_cardano_11_1_2(self):
        guard = "fa24fb305126805cf2164c161d852a0e7330cf988f1fe558cf7d4a64"
        case = {"case_id": "g", "expected": "reject", "reason_classes": ["policy_hash"], "credential": guard}
        row = gpd.grade(case, result(
            rej(f'invalid guardrails script hash: provided None, expected Some(Hash<28>("{guard}"))'),
            rej(f'ConwayGovFailure (InvalidGuardrailsScriptHash SNothing (SJust (ScriptHash "{guard}")))')))
        self.assertEqual(row["status"], "AGREE")

    def test_masked_is_inconclusive(self):
        row = gpd.grade(PREV, result(
            rej("x"), {"classification": "masked", "status": 400, "reason": "spent"}, masked=True))
        self.assertEqual(row["status"], "INCONCLUSIVE")


class CorpusTests(unittest.TestCase):
    def test_corpus_integrity(self):
        manifest = json.loads((CORPUS / "gov_proposal_corpus.json").read_text())
        ids = [c["case_id"] for c in manifest["cases"]]
        self.assertEqual(len(ids), len(set(ids)))
        for case in manifest["cases"]:
            cbor = bytes.fromhex(json.loads((CORPUS / case["tx_file"]).read_text())["cborHex"])
            self.assertEqual(len(cbor), case["cbor_size"], case["case_id"])
            self.assertEqual(hashlib.sha256(cbor).hexdigest(), case["cbor_sha256"], case["case_id"])
            self.assertIn(case["expected"], ("accept", "reject", "decode_reject"))
            self.assertIn(bytes.fromhex(case["input"].split("#")[0]), cbor, case["case_id"])
            if case["expected"] == "reject":
                self.assertTrue(case["reason_classes"], case["case_id"])
                self.assertTrue(set(case["reason_classes"]) <= set(gpd.REASON_CLASSES))


if __name__ == "__main__":
    unittest.main()
