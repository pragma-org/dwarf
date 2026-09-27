import sys, unittest
from pathlib import Path

WD = Path(__file__).resolve().parents[1]
if str(WD) not in sys.path:
    sys.path.insert(0, str(WD))
import plutus_differential as pd  # noqa: E402

FUND = "9708b921e619b34a6fafc375ebd46bf65d77eed427f953eabed2b9da9212c4a1"


def acc():
    return {"status": 202, "reason": '"<txid>"'}


def rej_tag_c():
    return {"status": 400, "reason": 'ConwayUtxowFailure (... ValidationTagMismatch ...)'}


def rej_tag_a():
    return {"status": 400, "reason": "transaction X is invalid: transaction failed phase two validation: validation tag mismatch: ..."}


def masked_c():
    return {"status": 400, "reason": 'ConwayMempoolFailure "All inputs are spent. ..."'}


class Classify(unittest.TestCase):
    def test_202_accepted(self):
        self.assertEqual(pd.classify(202, '"abc"'), pd.ACCEPTED)

    def test_phase2_reject_bodies(self):
        self.assertEqual(pd.classify(400, rej_tag_c()["reason"]), pd.PHASE2_REJECT)
        self.assertEqual(pd.classify(400, rej_tag_a()["reason"]), pd.PHASE2_REJECT)

    def test_funding_conflict_is_masked_not_reject(self):
        # a funding-input conflict must not read as a phase-2 reject
        self.assertEqual(pd.classify(400, masked_c()["reason"]), pd.MASKED)

    def test_no_response_unavailable(self):
        self.assertEqual(pd.classify(None, "", None), pd.UNAVAILABLE)


# real captured node phrasings (pair2, 2026-09-27)
REAL = {
    "amaru_isvalidfalse_passed": "transaction X is invalid: transaction failed phase two validation: expected scripts to fail but they passed",
    "cardano_isvalidfalse_passed": 'ConwayUtxowFailure (UtxoFailure (UtxosFailure (ValidationTagMismatch (IsValid False) PassedUnexpectedly)))',
    "amaru_isvalidtrue_failed": "transaction X is invalid: transaction failed phase two validation: expected scripts to pass but they failed: [UplcMachineError ...]",
    "cardano_isvalidtrue_failed": 'ConwayUtxowFailure (UtxoFailure (UtxosFailure (ValidationTagMismatch (IsValid True) (FailedUnexpectedly (PlutusFailure ...)))))',
}


class RealPhrasings(unittest.TestCase):
    def test_isvalid_false_passed_agrees(self):
        row = pd.grade({"case_id": "c", "expected": "reject"},
                       {"amaru": {"status": 400, "reason": REAL["amaru_isvalidfalse_passed"]},
                        "cardano": {"status": 400, "reason": REAL["cardano_isvalidfalse_passed"]}})
        self.assertEqual(row["status"], "AGREE", row.get("reason_classes"))

    def test_isvalid_true_failed_agrees(self):
        row = pd.grade({"case_id": "c", "expected": "reject"},
                       {"amaru": {"status": 400, "reason": REAL["amaru_isvalidtrue_failed"]},
                        "cardano": {"status": 400, "reason": REAL["cardano_isvalidtrue_failed"]}})
        self.assertEqual(row["status"], "AGREE", row.get("reason_classes"))


class Grade(unittest.TestCase):
    def test_both_accept_expected_accept_agree(self):
        row = pd.grade({"case_id": "c", "expected": "accept"}, {"amaru": acc(), "cardano": acc()})
        self.assertEqual(row["status"], "AGREE")

    def test_both_reject_tag_mismatch_agree(self):
        row = pd.grade({"case_id": "c", "expected": "reject"},
                       {"amaru": rej_tag_a(), "cardano": rej_tag_c()})
        self.assertEqual(row["status"], "AGREE")

    def test_one_accepts_one_rejects_is_verdict_divergence(self):
        # the headline is_valid divergence
        row = pd.grade({"case_id": "c", "expected": "reject"},
                       {"amaru": acc(), "cardano": rej_tag_c()})
        self.assertEqual(row["status"], "VERDICT-DIVERGENCE")

    def test_both_accept_but_expected_reject_is_verdict_divergence(self):
        # both wrongly accept a should-fail tx -> still flagged (matches_expected False)
        row = pd.grade({"case_id": "c", "expected": "reject"}, {"amaru": acc(), "cardano": acc()})
        self.assertEqual(row["status"], "VERDICT-DIVERGENCE")

    def test_masked_either_side_is_inconclusive(self):
        row = pd.grade({"case_id": "c", "expected": "reject"},
                       {"amaru": rej_tag_a(), "cardano": masked_c()})
        self.assertEqual((row["status"], row["why"]), ("INCONCLUSIVE", "masked"))

    def test_reject_with_no_shared_class_is_reason_divergence(self):
        row = pd.grade({"case_id": "c", "expected": "reject"},
                       {"amaru": {"status": 400, "reason": "some other error"},
                        "cardano": rej_tag_c()})
        self.assertEqual(row["status"], "REASON-DIVERGENCE")


if __name__ == "__main__":
    unittest.main()
