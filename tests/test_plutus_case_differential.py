"""On-chain Plutus case differential: transaction finalisation and outcome assessment."""
from __future__ import annotations

from decimal import Decimal

import cbor2
import pytest

from scripts import plutus_cases as pc
from scripts import runtime_plutus_case_differential as d

PPARAMS = {"maxTxSize": 16384, "txFeePerByte": 44, "txFeeFixed": 155381, "utxoCostPerByte": 4310,
           "costModels": {"PlutusV1": [1], "PlutusV2": [1], "PlutusV3": [1]},
           "executionUnitPrices": {"priceSteps": Decimal("0.0000721"), "priceMemory": Decimal("0.0577")},
           "maxTxExecutionUnits": {"steps": 10_000_000_000, "memory": 14_000_000}}


def _template(language, program, budget):
    key = d.WITNESS_KEYS[language]
    body = {0: [[b"\x11" * 32, 0]], 1: [[b"\x60" + b"\x22" * 28, [20_000_000, {b"\x33" * 28: {b"T": 1}}]]], 2: 1_000_000}
    witnesses = {key: [cbor2.dumps(program)], 5: {(1, 0): [cbor2.CBORTag(121, []), [budget[1], budget[0]]]}}
    return cbor2.dumps([body, witnesses, True, None])


@pytest.mark.parametrize("language", ["PlutusV2", "PlutusV3"])
def test_finalised_transaction_carries_the_exact_program_and_a_converged_fee(language):
    case = pc.families()["bls12-381-builtins" if language == "PlutusV3" else "crypto-builtins"].cases[0]
    budget = d.case_budget(PPARAMS)
    template = _template(language, case.program(), budget)
    built = d.build_case_transaction(
        template_payload=template, address=b"\x60" + b"\x22" * 28, input_lovelace=20_000_000,
        program=case.program(), language=language, protocol_parameters=PPARAMS,
        budget=budget, sign_body=lambda body: cbor2.dumps([cbor2.loads(body), cbor2.loads(template)[1], True, None]),
    )
    tx = cbor2.loads(built["payload"])
    assert tx[1][d.WITNESS_KEYS[language]] == [cbor2.dumps(case.program())]
    assert built["fee"] == tx[0][2] and built["fee"] > 155381
    assert tx[0][1][0][1][0] == 20_000_000 - built["fee"]
    assert built["program_sha256"] == case.program_sha256() and built["language"] == language


def test_wrong_program_in_the_template_is_refused():
    family = pc.families()["crypto-builtins"]
    budget = d.case_budget(PPARAMS)
    template = _template("PlutusV2", family.cases[0].program(), budget)
    with pytest.raises(ValueError, match="expected program"):
        d.build_case_transaction(template_payload=template, address=b"\x60" + b"\x22" * 28, input_lovelace=20_000_000,
                                 program=family.cases[1].program(), language="PlutusV2", protocol_parameters=PPARAMS,
                                 budget=budget, sign_body=lambda body: b"")


def test_case_budget_stays_inside_the_live_transaction_limit():
    steps, memory = d.case_budget(PPARAMS)
    assert 0 < steps <= 10_000_000_000 // 2 and 0 < memory <= 14_000_000 // 2


def _record(case_id, expected, target, reference):
    return {"case_id": case_id, "expected": expected, "target": {"classification": target}, "reference": {"classification": reference}}


def _family(records, control_ok=True):
    control = {"target": {"classification": "accepted", "included": control_ok, "target_adopted": control_ok},
               "reference": {"classification": "accepted", "included": control_ok, "target_adopted": control_ok}}
    return {"family": "f", "control": control, "cases": records}


def test_assessment_passes_when_both_nodes_agree_with_the_specification():
    status = d.assess_case_outcomes([_family([_record("a", "accept", "accepted", "accepted"),
                                              _record("r", "reject", "phase2_reject", "inconclusive_reject")])])
    assert status["complete"] is True and status["agreement"] is True and status["specification_match"] is True
    assert status["disagreements"] == [] and status["case_count"] == 2


def test_assessment_names_a_disagreement():
    status = d.assess_case_outcomes([_family([_record("a", "accept", "accepted", "phase2_reject")])])
    assert status["agreement"] is False and status["disagreements"] == [{"family": "f", "case_id": "a", "target": "accepted", "reference": "phase2_reject"}]


def test_assessment_is_incomplete_without_an_adopted_control_or_with_an_unavailable_node():
    assert d.assess_case_outcomes([_family([_record("a", "accept", "accepted", "accepted")], control_ok=False)])["complete"] is False
    status = d.assess_case_outcomes([_family([_record("a", "accept", "unavailable", "accepted")])])
    assert status["complete"] is False and status["inconclusive"] == [{"family": "f", "case_id": "a"}]


def test_primitive_passes_resolved_identities_and_families(tmp_path, monkeypatch):
    import json as _json
    from profile_manager import submit_primitives as sp
    seen = {}

    def fake_run(**kwargs):
        seen.update(kwargs)
        return {"differential_status": {"complete": True}}

    monkeypatch.setattr(d, "run_plutus_case_differential", fake_run)
    (tmp_path / "outputs" / "substrate-compose").mkdir(parents=True)
    (tmp_path / "outputs" / "substrate-compose" / "runtime.json").write_text(_json.dumps({"nodes": []}), encoding="utf-8")

    class H:
        run_dir = tmp_path

    prim = sp.RuntimePlutusCaseDifferential(params={"runtime_metadata_path": "outputs/substrate-compose/runtime.json",
                                                    "target_node": "node1", "reference_node": "node2",
                                                    "families": ["crypto-builtins"]})
    prim.bound_substrate = {"nodes": [{"id": "node1", "version": "10.11.x", "source_revision": "a" * 40},
                                      {"id": "node2", "version": "11.1.3", "source_revision": "b" * 40}]}
    prim.run(H(), None)
    assert seen["family_names"] == ["crypto-builtins"] and seen["expected_target_version"] == "10.11.x"
    assert seen["expected_reference_source"] == "b" * 40


@pytest.mark.parametrize("status,result", [
    ({"complete": True, "agreement": True, "specification_match": True}, "pass"),
    ({"complete": True, "agreement": False, "specification_match": False, "disagreements": [{"case_id": "a"}]}, "fail"),
    ({"complete": False, "agreement": False, "specification_match": False}, "fail"),
])
def test_assertion_requires_complete_agreeing_specification_matching_evidence(tmp_path, status, result):
    import json as _json
    from profile_manager import submit_primitives as sp
    out = tmp_path / "outputs" / "plutus-case-differential"; out.mkdir(parents=True)
    (out / "result.json").write_text(_json.dumps({"differential_status": status}), encoding="utf-8")

    class H:
        run_dir = tmp_path

    verdict = sp.PlutusCaseOutcomesAgree(params={}).evaluate(H())
    assert verdict["result"] == result and verdict["primitive"] == "plutus_case_outcomes_agree"
