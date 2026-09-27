import hashlib
import json
import sys
import unittest
from pathlib import Path

WORKLOAD_DIR = Path(__file__).resolve().parents[1]
if str(WORKLOAD_DIR) not in sys.path:
    sys.path.insert(0, str(WORKLOAD_DIR))

import metadata_differential as mdd  # noqa: E402

CORPUS = WORKLOAD_DIR.parent / "fixture" / "metadata"
SENT = "7126b40bc313ba23efc76a292e102ba1e8528de756c47738d8a010c0d808cb3b"
CANON = "c80ec55e3358e19fd6dfedbf6d9efbfc48b5129f5f4c2937ccd1843132f7aece"


def result(amaru, cardano, masked=False):
    obs = {"amaru": amaru, "cardano": cardano}
    classes = {o["classification"] for o in obs.values()}
    return {"observations": obs, "masked": masked,
            "both_classifiable": classes <= {"accepted", "phase1_reject", "decode_reject"}}


def rej(reason):
    return {"classification": "phase1_reject", "status": 400, "reason": reason, "detail": reason}


CASE = {"case_id": "c", "expected": "reject", "reason_classes": ["conflicting_hash"], "credential": SENT}


class GradeTests(unittest.TestCase):
    def test_both_hash_the_sent_bytes_agree(self):
        row = mdd.grade(CASE, result(
            rej(f"metadata hash mismatch: supplied {CANON} expected {SENT}"),
            rej(f'ConflictingMetadataHash Mismatch {{supplied: "{CANON}", expected: "{SENT}"}}')))
        self.assertEqual(row["status"], "AGREE")

    def test_a_node_hashing_its_reencoding_fails_parity(self):
        row = mdd.grade(CASE, result(
            rej(f"metadata hash mismatch: supplied {CANON} expected {CANON[::-1]}"),
            rej(f'ConflictingMetadataHash Mismatch {{supplied: "{CANON}", expected: "{SENT}"}}')))
        self.assertEqual(row["status"], "REASON-DIVERGENCE")

    def test_missing_aux_marker_does_not_match_missing_hash(self):
        self.assertEqual(mdd.base.reason_classes("MissingTxBodyMetadataHash (x)", "cardano",
                                                 mdd.REASON_CLASSES), {"missing_hash"})

    def test_submit_api_bearer_closed_is_inconclusive(self):
        closed = {"classification": "unknown", "status": 400,
                  "reason": '{"contents":{"contents":"BearerClosed ..."}}'}
        row = mdd.grade({"case_id": "t", "expected": "decode_reject", "reason_classes": []},
                        result({"classification": "decode_reject", "status": 400,
                                "reason": "text exceeds 64 bytes"}, closed))
        self.assertEqual(row["status"], "INCONCLUSIVE")


class CorpusTests(unittest.TestCase):
    def test_corpus_integrity(self):
        manifest = json.loads((CORPUS / "metadata_corpus.json").read_text())
        ids = [c["case_id"] for c in manifest["cases"]]
        self.assertEqual(len(ids), len(set(ids)))
        for case in manifest["cases"]:
            cbor = bytes.fromhex(json.loads((CORPUS / case["tx_file"]).read_text())["cborHex"])
            self.assertEqual(len(cbor), case["cbor_size"], case["case_id"])
            self.assertEqual(hashlib.sha256(cbor).hexdigest(), case["cbor_sha256"], case["case_id"])
            self.assertIn(case["expected"], ("accept", "reject", "decode_reject"))
            self.assertIn(bytes.fromhex(case["input"].split("#")[0]), cbor, case["case_id"])
            if case["expected"] == "reject":
                self.assertTrue(set(case["reason_classes"]) <= set(mdd.REASON_CLASSES))


if __name__ == "__main__":
    unittest.main()
