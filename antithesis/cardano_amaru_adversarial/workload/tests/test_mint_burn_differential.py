import hashlib
import json
import sys
import unittest
from pathlib import Path

WORKLOAD_DIR = Path(__file__).resolve().parents[1]
if str(WORKLOAD_DIR) not in sys.path:
    sys.path.insert(0, str(WORKLOAD_DIR))

import mint_burn_differential as mbd  # noqa: E402
import mixed_phase1  # noqa: E402

CORPUS = WORKLOAD_DIR.parent / "fixture" / "mint_burn"
POL = "d667c38e38d5712f10a038538e8d5de6467dfb6f2ccf9b70aa3d26ed"


def result(amaru, cardano, masked=False):
    obs = {"amaru": amaru, "cardano": cardano}
    classes = {o["classification"] for o in obs.values()}
    return {"observations": obs, "masked": masked,
            "both_classifiable": classes <= {"accepted", "phase1_reject", "decode_reject"}}


def rej(reason, detail=None):
    o = {"classification": "phase1_reject", "status": 400, "reason": reason[:400]}
    if detail is not None:
        o["detail"] = detail
    return o


def dec(reason):
    return {"classification": "decode_reject", "status": 400, "reason": reason}


ACC = {"classification": "accepted", "status": 202, "reason": ""}
MISSING = {"case_id": "m", "expected": "reject", "reason_classes": ["missing_script"], "credential": POL}
VALUE = {"case_id": "v", "expected": "reject", "reason_classes": ["value_not_conserved"],
         "credential": POL}
DECODE = {"case_id": "d", "expected": "decode_reject", "reason_classes": []}
BIG = {"case_id": "b", "expected": "reject", "reason_classes": ["output_too_big"], "credential": "5000"}


class GradeTests(unittest.TestCase):
    def test_missing_script_same_policy_agrees(self):
        row = mbd.grade(MISSING, result(
            rej(f"phase one validation: invalid transaction scripts: missing required scripts: missing [{POL}]"),
            rej(f'MissingScriptWitnessesUTXOW (fromList [ScriptHash "{POL}"])')))
        self.assertEqual(row["status"], "AGREE")

    def test_policy_id_past_400_chars_is_found_in_detail(self):
        long = "ValueNotConservedUTxO (Mismatch {" + "x" * 500 + f'ScriptHash "{POL}"' + "})"
        row = mbd.grade(VALUE, result(rej(f"value not preserved: balance = {POL}"),
                                      rej(long, detail=long)))
        self.assertEqual(row["status"], "AGREE")

    def test_policy_id_only_past_the_reason_cut_without_detail_is_unverified(self):
        long = "ValueNotConservedUTxO (Mismatch {" + "x" * 500 + f'ScriptHash "{POL}"' + "})"
        row = mbd.grade(VALUE, result(rej(f"value not preserved: balance = {POL}"), rej(long)))
        self.assertEqual(row["status"], "REASON-UNVERIFIED")

    def test_decode_reject_on_both_agrees(self):
        row = mbd.grade(DECODE, result(dec("invalid cbor"), dec("DeserialiseFailure zeros")))
        self.assertEqual(row["status"], "AGREE")

    def test_lenient_decoder_accept_is_verdict_divergence(self):
        row = mbd.grade(DECODE, result(ACC, dec("DeserialiseFailure zeros")))
        self.assertEqual(row["status"], "VERDICT-DIVERGENCE")

    def test_decode_further_then_phase1_reject_is_flagged_leniency(self):
        row = mbd.grade(DECODE, result(rej("phase one validation: value not preserved"),
                                       dec("DeserialiseFailure zeros")))
        self.assertEqual((row["status"], row["decode_leniency"]), ("VERDICT-DIVERGENCE", ["amaru"]))

    def test_masked_is_inconclusive_never_agree(self):
        row = mbd.grade(DECODE, result(
            dec("invalid cbor"), {"classification": "masked", "status": 400, "reason": "spent"},
            masked=True))
        self.assertEqual(row["status"], "INCONCLUSIVE")

    def test_value_size_mismatch_is_reason_divergence(self):
        row = mbd.grade(BIG, result(
            rej("output value is too large: maximum: 5000, actual: 5401"),
            rej("OutputTooBigUTxO [(5400,5000,TxOut ...)]")))
        self.assertEqual((row["status"], row["value_size"]),
                         ("REASON-DIVERGENCE", {"amaru": "5401", "cardano": "5400"}))

    def test_value_size_match_agrees(self):
        row = mbd.grade(BIG, result(
            rej("output value is too large: maximum: 5000, actual: 5400"),
            rej("OutputTooBigUTxO [(5400,5000,TxOut ...)]")))
        self.assertEqual(row["status"], "AGREE")


class TransportTests(unittest.TestCase):
    def test_detail_only_when_requested(self):
        body = "y" * 1000
        plain = mixed_phase1._observation(400, body)
        full = mixed_phase1._observation(400, body, detail=True)
        self.assertNotIn("detail", plain)
        self.assertEqual((len(full["reason"]), full["detail"]), (400, body))


class CorpusTests(unittest.TestCase):
    def test_corpus_integrity(self):
        manifest = json.loads((CORPUS / "mint_burn_corpus.json").read_text())
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
                self.assertTrue(set(case["reason_classes"]) <= set(mbd.REASON_CLASSES))


if __name__ == "__main__":
    unittest.main()
