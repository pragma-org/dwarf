import cbor2
import json
from types import SimpleNamespace

from profile_manager.submit_primitives import ValueCoinOverflowRegressionPassed
from scripts.runtime_value_coin_overflow import (
    I64_MAX,
    I64_OVERFLOW,
    assess_value_coin_overflow,
    boundary_control_allows_mutant,
    build_raw_template_command,
    build_value_coin_case,
    classify_value_coin_response,
)


def _template_payload():
    return cbor2.dumps(
        [
            {0: [[b"i" * 32, 0]], 1: [[b"address", 5_000_000]], 2: 1_000_000},
            {0: []},
            True,
            None,
        ]
    )


def test_build_value_coin_case_sets_exact_coin_and_resigns_body():
    signed_bodies = []

    def sign(body):
        signed_bodies.append(body)
        transaction = cbor2.loads(_template_payload())
        transaction[0] = cbor2.loads(body)
        return cbor2.dumps(transaction)

    built = build_value_coin_case(
        template_payload=_template_payload(),
        output_coin=I64_OVERFLOW,
        sign_body=sign,
    )

    assert built["output_coin"] == I64_OVERFLOW
    assert built["output_coin_boundary"] == "i64-max-plus-one"
    assert cbor2.loads(built["body"])[1][0][1] == I64_OVERFLOW
    assert cbor2.loads(built["payload"])[0][1][0][1] == I64_OVERFLOW
    assert signed_bodies == [built["body"]]
    assert len(built["transaction_id"]) == 64


def test_raw_template_uses_fixed_high_fee_so_mutation_cannot_mask_value_check():
    command = build_raw_template_command(
        cli="/bin/cardano-cli",
        funding_input="abc#0",
        address="addr_test1test",
        funding_lovelace=50_000_000,
        output_path="/work/transaction.body",
    )

    assert command == [
        "/bin/cardano-cli",
        "conway",
        "transaction",
        "build-raw",
        "--tx-in",
        "abc#0",
        "--tx-out",
        "addr_test1test+49000000",
        "--fee",
        "1000000",
        "--out-file",
        "/work/transaction.body",
    ]


def test_value_response_classifier_requires_the_expected_value_predicate():
    assert classify_value_coin_response(
        {"http": 400, "body": "ValueNotConservedUTxO"}
    ) == "value_reject"
    assert classify_value_coin_response(
        {"http": 400, "body": "value not preserved: balance = -1 lovelace"}
    ) == "value_reject"
    assert classify_value_coin_response(
        {"http": 400, "body": "BadInputsUTxO"}
    ) == "inconclusive_reject"
    assert classify_value_coin_response(
        {"http": 400, "body": "FeeTooSmallUTxO"}
    ) == "inconclusive_reject"
    assert classify_value_coin_response(
        {"http": 400, "body": "some unrelated client error"}
    ) == "inconclusive_reject"
    assert classify_value_coin_response(
        {"http": 400, "body": "invalid CBOR"}
    ) == "decode_reject"
    assert classify_value_coin_response({"http": 503, "body": ""}) == "unavailable"


def _record(case_id, funding_input, *, classification, running, included=False):
    return {
        "case_id": case_id,
        "funding_input": funding_input,
        "funding_input_resolvable": True,
        "transaction_id": f"tx-{funding_input}",
        "payload_sha256": f"payload-{funding_input}",
        "classification": classification,
        "value_rejection_marker": (
            "valuenotconservedutxo" if classification == "value_reject" else None
        ),
        "target_running_after": running,
        "included": included,
        "target_adopted": included,
    }


def _post_probe(
    *,
    boundary_input="b#0",
    classification="value_reject",
    running=True,
):
    return {
        "transaction_id": f"tx-{boundary_input}",
        "payload_sha256": f"payload-{boundary_input}",
        "classification": classification,
        "value_rejection_marker": (
            "valuenotconservedutxo" if classification == "value_reject" else None
        ),
        "target_running_after": running,
    }


def test_assessment_passes_only_with_valid_control_isolated_inputs_and_live_rejections():
    records = [
        _record("valid-control", "a#0", classification="accepted", running=True, included=True),
        {
            **_record("i64-max-control", "b#0", classification="value_reject", running=True),
            "output_coin": I64_MAX,
        },
        {
            **_record("i64-max-plus-one", "c#0", classification="value_reject", running=True),
            "output_coin": I64_OVERFLOW,
            "post_mutant_boundary_probe": _post_probe(),
        },
    ]

    status = assess_value_coin_overflow(records)

    assert status["complete"] is True
    assert status["regression_passed"] is True
    assert status["security_finding_observed"] is False


def test_assessment_retains_crash_as_security_finding_not_framework_success():
    records = [
        _record("valid-control", "a#0", classification="accepted", running=True, included=True),
        {
            **_record("i64-max-control", "b#0", classification="value_reject", running=True),
            "output_coin": I64_MAX,
        },
        {
            **_record("i64-max-plus-one", "c#0", classification="unavailable", running=False),
            "output_coin": I64_OVERFLOW,
            "post_mutant_boundary_probe": _post_probe(
                classification="unavailable", running=False
            ),
        },
    ]

    status = assess_value_coin_overflow(records)

    assert status["complete"] is True
    assert status["regression_passed"] is False
    assert status["security_finding_observed"] is True


def test_assessment_retains_live_container_with_dead_mempool_as_security_finding():
    records = [
        _record("valid-control", "a#0", classification="accepted", running=True, included=True),
        {
            **_record("i64-max-control", "b#0", classification="value_reject", running=True),
            "output_coin": I64_MAX,
        },
        {
            **_record("i64-max-plus-one", "c#0", classification="unavailable", running=True),
            "output_coin": I64_OVERFLOW,
            "post_mutant_boundary_probe": _post_probe(
                classification="unavailable", running=True
            ),
        },
    ]

    status = assess_value_coin_overflow(records)

    assert status["complete"] is True
    assert status["regression_passed"] is False
    assert status["security_finding_observed"] is True


def test_assessment_treats_post_probe_loss_after_clean_mutant_reject_as_finding():
    records = [
        _record("valid-control", "a#0", classification="accepted", running=True, included=True),
        {
            **_record("i64-max-control", "b#0", classification="value_reject", running=True),
            "output_coin": I64_MAX,
        },
        {
            **_record("i64-max-plus-one", "c#0", classification="value_reject", running=True),
            "output_coin": I64_OVERFLOW,
            "post_mutant_boundary_probe": _post_probe(
                classification="unavailable", running=False
            ),
        },
    ]

    status = assess_value_coin_overflow(records)

    assert status["complete"] is True
    assert status["regression_passed"] is False
    assert status["security_finding_observed"] is True


def test_assessment_is_incomplete_when_post_probe_identity_is_missing():
    records = [
        _record("valid-control", "a#0", classification="accepted", running=True, included=True),
        {
            **_record("i64-max-control", "b#0", classification="value_reject", running=True),
            "output_coin": I64_MAX,
        },
        {
            **_record("i64-max-plus-one", "c#0", classification="value_reject", running=True),
            "output_coin": I64_OVERFLOW,
            "post_mutant_boundary_probe": {
                "classification": "value_reject",
                "value_rejection_marker": "valuenotconservedutxo",
                "target_running_after": True,
            },
        },
    ]

    status = assess_value_coin_overflow(records)

    assert status["complete"] is False
    assert status["post_mutant_boundary_probe_observed"] is False
    assert status["regression_passed"] is False


def test_boundary_gate_stops_before_mutant_on_accept_crash_or_unresolved_input():
    base = {
        "case_id": "i64-max-control",
        "output_coin": I64_MAX,
        "classification": "value_reject",
        "value_rejection_marker": "valuenotconservedutxo",
        "target_running_after": True,
        "funding_input_resolvable": True,
    }

    assert boundary_control_allows_mutant(base) is True
    assert boundary_control_allows_mutant({**base, "classification": "accepted"}) is False
    assert boundary_control_allows_mutant({**base, "target_running_after": False}) is False
    assert boundary_control_allows_mutant({**base, "funding_input_resolvable": False}) is False


def test_regression_assertion_fails_on_retained_security_finding(tmp_path):
    output = tmp_path / "outputs/value-coin-overflow"
    output.mkdir(parents=True)
    (output / "result.json").write_text(
        json.dumps(
            {
                "regression_status": {
                    "complete": True,
                    "regression_passed": False,
                    "security_finding_observed": True,
                }
            }
        ),
        encoding="utf-8",
    )

    result = ValueCoinOverflowRegressionPassed(params={}).evaluate(
        SimpleNamespace(run_dir=tmp_path)
    )

    assert result["result"] == "fail"
    assert "security finding" in result["note"]
