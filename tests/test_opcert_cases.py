import json
from pathlib import Path
import pytest
from scripts import opcert_cases

CASES = Path("dwarf/corpora/opcert/opcert-header-cases-v1.json")


def test_case_table_loads_and_validates():
    cases = opcert_cases.load_cases()
    ids = [c["id"] for c in cases]
    assert ids[0] == "valid-control"
    assert len(ids) == len(set(ids)), "case ids must be unique"
    for name in ("cold-key-unauthorized", "counter-behind", "counter-jump",
                 "counter-plus-one", "kes-before-window", "kes-after-window",
                 "hot-key-mismatch"):
        assert name in ids, name
    for c in cases:
        assert c["mutation"] in opcert_cases.MUTATIONS
        assert c["expected_verdict"] in ("accept", "reject")
        assert set(c["expected_reason"]) == {"cardano-node", "amaru"}
    for c in cases:
        if c["expected_verdict"] == "accept":
            assert c["expected_reason"] == {"cardano-node": None, "amaru": None}
        else:
            assert c["expected_reason"]["cardano-node"] and c["expected_reason"]["amaru"]


def test_validate_rejects_unknown_mutation():
    with pytest.raises(ValueError, match="unknown mutation"):
        opcert_cases.validate_cases([{"id": "x", "family": "rule", "mutation": "Nope",
                                      "rule": "r", "expected_verdict": "reject",
                                      "expected_reason": {"cardano-node": "a", "amaru": "b"}}])


def test_validate_rejects_duplicate_ids():
    row = {"id": "dup", "family": "rule", "mutation": "NoMutation", "rule": "r",
           "expected_verdict": "accept", "expected_reason": {"cardano-node": None, "amaru": None}}
    with pytest.raises(ValueError, match="duplicate"):
        opcert_cases.validate_cases([row, dict(row)])


def test_validate_rejects_missing_field():
    with pytest.raises(ValueError, match="missing fields"):
        opcert_cases.validate_cases([{"id": "x", "family": "rule", "mutation": "NoMutation"}])


def test_validate_rejects_unknown_family():
    with pytest.raises(ValueError, match="bad family"):
        opcert_cases.validate_cases([{"id": "x", "family": "banana", "mutation": "NoMutation",
                                      "rule": "r", "expected_verdict": "accept",
                                      "expected_reason": {"cardano-node": None, "amaru": None}}])
