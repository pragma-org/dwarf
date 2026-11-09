"""F105: an Amaru acceptance that cardano-node can never include is proven from Amaru's mempool.

In a mixed devnet cardano-node is the block producer. When it refuses to decode a boundary
transaction that Amaru accepts, the transaction can never be included, so "accepted counts only
when included" made a real divergence inconclusive. Amaru's submit API answers 202 with the tx id
only after its mempool inserted the transaction, and 409 Duplicate while it still holds it.
"""
from __future__ import annotations

import copy

import pytest

from scripts import runtime_redeemer_container_live_controls as lc
from scripts.runtime_inline_datum_tag24_boundary import assess_inline_datum_boundary
from scripts.runtime_multiasset_compact_boundary import assess_compact_boundary

TX = "e" * 64


def test_probe_proves_the_mempool_still_holds_the_transaction():
    seen = []

    def submit(container_id, payload):
        seen.append((container_id, payload))
        return {"http": 409, "body": "transaction already in mempool (duplicate)"}

    evidence = lc.probe_amaru_mempool_holds(
        "c" * 64, b"\x84", transaction_id=TX, first_response={"http": 202, "body": f'"{TX}"'}, submit=submit)
    assert seen == [("c" * 64, b"\x84")]
    assert evidence["held"] is True and evidence["accepted_transaction_id_matches"] is True
    assert evidence["method"] == "amaru-submit-api-duplicate-probe"


@pytest.mark.parametrize("first,probe", [
    ({"http": 202, "body": f'"{"f" * 64}"'}, {"http": 409, "body": "Duplicate"}),   # different tx id
    ({"http": 202, "body": f'"{TX}"'}, {"http": 202, "body": f'"{TX}"'}),          # accepted again: not held
    ({"http": 202, "body": f'"{TX}"'}, {"http": 0, "body": "", "transport": "down"}),
    ({"http": 400, "body": "invalid"}, {"http": 409, "body": "Duplicate"}),         # never accepted
])
def test_probe_fails_closed(first, probe):
    evidence = lc.probe_amaru_mempool_holds("c" * 64, b"\x84", transaction_id=TX, first_response=first,
                                            submit=lambda c, p: probe)
    assert evidence["held"] is False


def _records(control, boundary):
    return [
        {"case_id": control,
         "target": {"funding_input": "a" * 64 + "#0", "classification": "accepted", "included": True,
                    "target_adopted": True, "transaction_id": "b" * 64},
         "reference": {"funding_input": "a" * 64 + "#1", "classification": "accepted", "included": True,
                       "target_adopted": True, "transaction_id": "c" * 64}},
        {"case_id": boundary, "funding_input": "a" * 64 + "#2", "payload_sha256": "d" * 64,
         "nested_trailing_bytes": 1, "transaction_trailing_bytes": 0, "transaction_id": TX,
         "target": {"classification": "accepted", "included": False, "payload_sha256": "d" * 64},
         "reference": {"classification": "decode_reject", "included": False, "payload_sha256": "d" * 64}},
    ]


HELD = {"method": "amaru-submit-api-duplicate-probe", "held": True, "accepted_transaction_id_matches": True}


@pytest.mark.parametrize("assess,control,boundary", [
    (assess_inline_datum_boundary, "inline-datum-control", "inline-datum-tag24-trailing"),
    (assess_compact_boundary, "compact-control", "compact-boundary"),
])
def test_held_amaru_acceptance_is_conclusive_and_establishes_the_divergence(assess, control, boundary):
    records = _records(control, boundary)
    records[1]["target"]["target_mempool_evidence"] = dict(HELD)
    status = assess(records)
    assert status["outcomes_conclusive"] is True and status["complete"] is True
    assert status["divergence_established"] is True

    not_held = copy.deepcopy(records)
    not_held[1]["target"]["target_mempool_evidence"]["held"] = False
    assert assess(not_held)["complete"] is False

    # mempool evidence never makes a reference acceptance conclusive
    ref = copy.deepcopy(records)
    ref[1]["reference"] = {"classification": "accepted", "included": False, "payload_sha256": "d" * 64,
                           "target_mempool_evidence": dict(HELD)}
    ref[1]["target"] = {"classification": "decode_reject", "included": False, "payload_sha256": "d" * 64}
    assert assess(ref)["outcomes_conclusive"] is False


@pytest.mark.parametrize("module", ["runtime_inline_datum_tag24_boundary", "runtime_multiasset_compact_boundary"])
def test_boundary_runners_probe_amaru_when_an_acceptance_is_not_included(module):
    from pathlib import Path
    source = (Path(lc.__file__).parent / f"{module}.py").read_text()
    assert "probe_amaru_mempool_holds(" in source and "target_mempool_evidence" in source
