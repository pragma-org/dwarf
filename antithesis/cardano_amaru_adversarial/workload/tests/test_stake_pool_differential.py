import json
import sys
import unittest
from pathlib import Path

WORKLOAD_DIR = Path(__file__).resolve().parents[1]
if str(WORKLOAD_DIR) not in sys.path:
    sys.path.insert(0, str(WORKLOAD_DIR))

import stake_pool_differential as spd  # noqa: E402

CORPUS = WORKLOAD_DIR.parent / "fixture" / "stake_pool"
CRED = "3e762d7f3adee5d616739be2f35d5f5e4d06d68e0d1f17a008123aab"


def result(amaru, cardano, masked=False):
    obs = {"amaru": amaru, "cardano": cardano}
    classes = {o["classification"] for o in obs.values()}
    return {"observations": obs, "masked": masked,
            "both_classifiable": classes <= {"accepted", "phase1_reject", "decode_reject"}}


def rej(reason):
    return {"classification": "phase1_reject", "status": 400, "reason": reason}


WITNESS_CASE = {"case_id": "w", "expected": "reject", "reason_classes": ["missing_witness"],
                "credential": CRED}
PRECEDENCE_CASE = {"case_id": "p", "expected": "reject",
                   "reason_classes": ["missing_witness", "wdrl_not_drep_delegated"]}


class GradeTests(unittest.TestCase):
    def test_same_rule_and_credential_agree(self):
        row = spd.grade(WITNESS_CASE, result(
            rej(f"missing required signatures for keys or roots: [{CRED}]"),
            rej(f'MissingVKeyWitnessesUTXOW (fromList [KeyHash {{unKeyHash = "{CRED}"}}])')))
        self.assertEqual(row["status"], "AGREE")

    def test_masked_node_is_inconclusive_never_agree(self):
        row = spd.grade(WITNESS_CASE, result(
            rej("missing required signatures"),
            {"classification": "masked", "status": 400, "reason": "All inputs are spent"},
            masked=True))
        self.assertEqual((row["status"], row["why"]), ("INCONCLUSIVE", "masked"))

    def test_one_node_accepting_a_violation_is_verdict_divergence(self):
        row = spd.grade(WITNESS_CASE, result(
            {"classification": "accepted", "status": 202, "reason": ""},
            rej("MissingVKeyWitnessesUTXOW")))
        self.assertEqual(row["status"], "VERDICT-DIVERGENCE")

    def test_different_rule_same_verdict_is_reason_divergence(self):
        row = spd.grade(PRECEDENCE_CASE, result(
            rej("attempted to withdraw from an account (x) that has no drep delegation"),
            rej("MissingVKeyWitnessesUTXOW")))
        self.assertEqual(row["status"], "REASON-DIVERGENCE")

    def test_failure_set_sharing_a_rule_with_single_reason_agrees(self):
        row = spd.grade(PRECEDENCE_CASE, result(
            rej("that has no drep delegation"),
            rej("[MissingVKeyWitnessesUTXOW ..., ConwayWdrlNotDelegatedToDRep ...]")))
        self.assertEqual(row["status"], "AGREE")

    def test_wrong_credential_is_reason_divergence(self):
        other = "0" * 56
        row = spd.grade(WITNESS_CASE, result(
            rej(f"missing required signatures for keys or roots: [{other}]"),
            rej(f"MissingVKeyWitnessesUTXOW {CRED}")))
        self.assertEqual(row["status"], "REASON-DIVERGENCE")


class CorpusTests(unittest.TestCase):
    def test_corpus_integrity(self):
        import hashlib
        manifest = json.loads((CORPUS / "stake_pool_corpus.json").read_text())
        ids = [c["case_id"] for c in manifest["cases"]]
        self.assertEqual(len(ids), len(set(ids)))
        for case in manifest["cases"]:
            cbor = bytes.fromhex(json.loads((CORPUS / case["tx_file"]).read_text())["cborHex"])
            self.assertEqual(len(cbor), case["cbor_size"], case["case_id"])
            self.assertEqual(hashlib.sha256(cbor).hexdigest(), case["cbor_sha256"], case["case_id"])
            self.assertIn(case["expected"], ("accept", "reject"))
            if case["expected"] == "reject":
                self.assertTrue(set(case["reason_classes"]) <= set(spd.REASON_CLASSES))
                self.assertTrue(case["reason_classes"], case["case_id"])


if __name__ == "__main__":
    unittest.main()
