import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

WORKLOAD_DIR = Path(__file__).resolve().parents[1]
if str(WORKLOAD_DIR) not in sys.path:
    sys.path.insert(0, str(WORKLOAD_DIR))

import mempool_differential as mpd  # noqa: E402

CORPUS = WORKLOAD_DIR.parent / "fixture" / "mempool"


def obs(cls, reason=""):
    return {"classification": cls, "status": 400, "reason": reason, "detail": reason}


def result(amaru, cardano):
    o = {"amaru": amaru, "cardano": cardano}
    return {"observations": o, "masked": any(x["classification"] == "masked" for x in o.values()),
            "both_classifiable": {x["classification"] for x in o.values()}
            <= {"accepted", "phase1_reject", "decode_reject"}}


SIZE = {"case_id": "s", "expected": "reject", "reason_classes": ["tx_too_large"], "credential": "16385"}


class GradeTests(unittest.TestCase):
    def test_same_original_size_agrees(self):
        row = mpd.grade(SIZE, result(obs("phase1_reject", "transaction too large: provided 16385 bytes"),
                                     obs("phase1_reject", "MaxTxSizeUTxO {supplied: 16385, expected: 16384}")))
        self.assertEqual(row["status"], "AGREE")

    def test_node_measuring_a_reencoding_fails_parity(self):
        row = mpd.grade(SIZE, result(obs("phase1_reject", "transaction too large: provided 16376 bytes"),
                                     obs("phase1_reject", "MaxTxSizeUTxO {supplied: 16385, expected: 16384}")))
        self.assertEqual(row["status"], "REASON-DIVERGENCE")

    def test_node_accepting_the_noncanonical_reject_is_verdict_divergence(self):
        row = mpd.grade(SIZE, result({"classification": "accepted", "status": 202, "reason": ""},
                                     obs("phase1_reject", "MaxTxSizeUTxO 16385")))
        self.assertEqual(row["status"], "VERDICT-DIVERGENCE")


class DuplicateTests(unittest.TestCase):
    def _run(self, first, second):
        with mock.patch.object(mpd, "observe_differential", side_effect=[first, second]), \
             mock.patch.object(mpd, "_payload", return_value=b""), \
             mock.patch.object(mpd, "_cases", return_value={"mp-base": {}}):
            return mpd.run_duplicate(Path("."), {})[0]["status"]

    ACC = {"classification": "accepted", "status": 202, "reason": ""}

    def test_both_refuse_the_second_copy(self):
        self.assertEqual(self._run(result(self.ACC, self.ACC),
                                   result(obs("masked", "duplicate"), obs("masked", "All inputs are spent"))),
                         "AGREE")

    def test_a_second_accept_is_a_divergence(self):
        self.assertEqual(self._run(result(self.ACC, self.ACC),
                                   result(self.ACC, obs("masked", "All inputs are spent"))),
                         "VERDICT-DIVERGENCE")


class CorpusTests(unittest.TestCase):
    def test_corpus_integrity_and_sizes(self):
        manifest = json.loads((CORPUS / "mempool_corpus.json").read_text())
        for case in manifest["cases"]:
            cbor = bytes.fromhex(json.loads((CORPUS / case["tx_file"]).read_text())["cborHex"])
            self.assertEqual(hashlib.sha256(cbor).hexdigest(), case["cbor_sha256"], case["case_id"])
            if case["case_id"].startswith("size-"):
                want = 16385 if case["expected"] == "reject" else 16384
                self.assertEqual(case["original_ledger_size"], want, case["case_id"])
                if "canonical" not in case["case_id"]:
                    self.assertLess(case["canonical_ledger_size"], case["original_ledger_size"])
                    self.assertLessEqual(case["canonical_ledger_size"], 16384)


if __name__ == "__main__":
    unittest.main()
