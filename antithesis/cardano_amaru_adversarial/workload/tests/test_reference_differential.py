import sys, unittest
from pathlib import Path
WD = Path(__file__).resolve().parents[1]
if str(WD) not in sys.path:
    sys.path.insert(0, str(WD))
import reference_differential as rd  # noqa: E402

NONE = "0" * 64
FUND = "9708b921e619b34a6fafc375ebd46bf65d77eed427f953eabed2b9da9212c4a1"
# real captured phrasings (pair2, 2026-09-27)
C_UNKNOWN = f'ConwayUtxowFailure (UtxoFailure (BadInputsUTxO (NonEmptySet (fromList [TxIn (TxId {{unTxId = SafeHash "{NONE}"}}) (TxIx {{unTxIx = 0}})]))))'
A_UNKNOWN = f"failed to prepare transaction x for validation: failed to hydrate validation context: unknown (but required) transaction input or reference input: {NONE}#0"
C_NONDISJ = f'ConwayUtxowFailure (UtxoFailure (BabbageNonDisjointRefInputs (TxIn (TxId {{unTxId = SafeHash "{FUND}"}}) (TxIx {{unTxIx = 0}}) :| [])))'
A_NONDISJ = f"transaction x is invalid: transaction failed phase one validation: invalid inputs: inputs included in both reference inputs and spent inputs: intersection [{FUND}#0]"


def rej(reason):
    return {"status": 400, "reason": reason}


class Classify(unittest.TestCase):
    def test_accept(self):
        self.assertEqual(rd.classify(202, '"tx"'), rd.ACCEPTED)

    def test_bad_reference_input_is_reject_not_masked(self):
        # the key point: a bad REFERENCE input yields BadInputsUTxO, which must read as a real
        # reject here, NOT masked (unlike a funding-mempool conflict)
        self.assertEqual(rd.classify(400, C_UNKNOWN), rd.REJECT)

    def test_mempool_conflict_is_masked(self):
        self.assertEqual(rd.classify(400, 'ConwayMempoolFailure "All inputs are spent. ..."'), rd.MASKED)

    def test_no_response_unavailable(self):
        self.assertEqual(rd.classify(None, ""), rd.UNAVAILABLE)


class Grade(unittest.TestCase):
    def test_unknown_ref_agrees_with_input_parity(self):
        row = rd.grade({"case_id": "c", "expected": "reject"}, {"amaru": rej(A_UNKNOWN), "cardano": rej(C_UNKNOWN)})
        self.assertEqual(row["status"], "AGREE")
        self.assertTrue(row["input_parity"])

    def test_nondisjoint_agrees_with_input_parity(self):
        row = rd.grade({"case_id": "c", "expected": "reject"}, {"amaru": rej(A_NONDISJ), "cardano": rej(C_NONDISJ)})
        self.assertEqual(row["status"], "AGREE")

    def test_accept_control_agrees(self):
        row = rd.grade({"case_id": "c", "expected": "accept"},
                       {"amaru": {"status": 202, "reason": '"t"'}, "cardano": {"status": 202, "reason": '"t"'}})
        self.assertEqual(row["status"], "AGREE")

    def test_one_accepts_one_rejects_is_verdict_divergence(self):
        row = rd.grade({"case_id": "c", "expected": "reject"},
                       {"amaru": {"status": 202, "reason": '"t"'}, "cardano": rej(C_UNKNOWN)})
        self.assertEqual(row["status"], "VERDICT-DIVERGENCE")

    def test_different_rule_is_reason_divergence(self):
        row = rd.grade({"case_id": "c", "expected": "reject"}, {"amaru": rej(A_UNKNOWN), "cardano": rej(C_NONDISJ)})
        self.assertEqual(row["status"], "REASON-DIVERGENCE")

    def test_masked_is_inconclusive(self):
        row = rd.grade({"case_id": "c", "expected": "reject"},
                       {"amaru": rej(A_UNKNOWN), "cardano": rej('ConwayMempoolFailure "All inputs are spent"')})
        self.assertEqual(row["status"], "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main()
