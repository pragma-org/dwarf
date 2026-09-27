import sys, unittest
from pathlib import Path
WD = Path(__file__).resolve().parents[1]
if str(WD) not in sys.path:
    sys.path.insert(0, str(WD))
import refscript_differential as r  # noqa: E402  (sets pd._MARKERS on import)
import plutus_differential as pd  # noqa: E402

# real captured pair (refpair, 2026-09-27)
A_WRONG = ("transaction x is invalid: transaction failed phase one validation: invalid transaction "
           "scripts: missing required scripts: missing [186e32faa80a26810392fda6d559c7ed4721a65ce1c9d4ef3e1c87b4]")
# cardano returns the failure SET; the intersecting MissingScriptWitnesses is LAST (>400 chars in)
C_WRONG_FULL = ('["ConwayUtxowFailure (PPViewHashesDontMatch Mismatch (RelEQ) {supplied: SJust (SafeHash \\"56..\\"), '
                'expected: SJust (SafeHash \\"51..\\")})", "ConwayUtxowFailure (ExtraRedeemers (ConwaySpending (AsIx {unAsIx = 0}) :| []))", '
                '"ConwayUtxowFailure (MissingScriptWitnessesUTXOW (NonEmptySet (fromList [ScriptHash \\"186e32faa80a26810392fda6d559c7ed4721a65ce1c9d4ef3e1c87b4\\"])))"]')


def rej(reason):
    return {"status": 400, "reason": reason}


class RefScriptGrade(unittest.TestCase):
    def test_wrong_refscript_full_body_agrees_on_missing_script(self):
        # the point: with the FULL failure set both cite missing-script -> AGREE
        row = pd.grade({"case_id": "spendA-wrong-refscript", "expected": "reject"},
                       {"amaru": rej(A_WRONG), "cardano": rej(C_WRONG_FULL)})
        self.assertEqual(row["status"], "AGREE", row.get("reason_classes"))

    def test_wrong_refscript_truncated_would_falsely_diverge(self):
        # regression note: truncating cardano's set to 400 chars hides MissingScriptWitnesses
        row = pd.grade({"case_id": "c", "expected": "reject"},
                       {"amaru": rej(A_WRONG), "cardano": rej(C_WRONG_FULL[:C_WRONG_FULL.find("MissingScript")])})
        self.assertEqual(row["status"], "REASON-DIVERGENCE")  # why the driver reads the full body

    def test_accept_agrees(self):
        row = pd.grade({"case_id": "c", "expected": "accept"},
                       {"amaru": {"status": 202, "reason": '"t"'}, "cardano": {"status": 202, "reason": '"t"'}})
        self.assertEqual(row["status"], "AGREE")

    def test_one_accepts_one_rejects_is_verdict_divergence(self):
        row = pd.grade({"case_id": "c", "expected": "reject"},
                       {"amaru": {"status": 202, "reason": '"t"'}, "cardano": rej(C_WRONG_FULL)})
        self.assertEqual(row["status"], "VERDICT-DIVERGENCE")


if __name__ == "__main__":
    unittest.main()
