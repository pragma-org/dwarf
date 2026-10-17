import hashlib
import json
from pathlib import Path

import pytest

from profile_manager import forensic
from profile_manager import submit_primitives
from profile_manager.primitives import load_registry
from profile_manager.scenario import semantic_validate_scenario
from profile_manager.submit_primitives import (
    RedeemerContainerDifferentialMatches,
    _validation_stage,
    run_redeemer_container_differential,
)


def _write_corpus(root):
    cases = []
    expectations = {
        "no-script-redeemers-omitted": ("phase1_reject", "phase1_reject"),
        "no-script-redeemers-empty-list": ("phase1_reject", "decode_reject"),
        "no-script-redeemers-empty-map": ("phase1_reject", "decode_reject"),
        "script-redeemer-valid-control": ("phase1_reject", "phase1_reject"),
        "script-redeemers-omitted": ("phase1_reject", "phase1_reject"),
    }
    for index, (case_id, (target, reference)) in enumerate(expectations.items()):
        payload = bytes([0x84, index, 0xA0, 0xF5, 0xF6])
        filename = f"{case_id}.tx"
        (root / filename).write_text(
            json.dumps({"type": "Tx ConwayEra", "cborHex": payload.hex()}),
            encoding="utf-8",
        )
        cases.append(
            {
                "case_id": case_id,
                "tx_file": filename,
                "cbor_size": len(payload),
                "cbor_sha256": hashlib.sha256(payload).hexdigest(),
                "expected": {"target": target, "reference": reference},
            }
        )
    (root / "corpus.json").write_text(
        json.dumps({"schema_version": 1, "cases": cases}), encoding="utf-8"
    )


def test_differential_retains_decoder_stage_separately_from_ledger_rejection(tmp_path):
    _write_corpus(tmp_path)
    submitted = []

    def submit(url, payload):
        submitted.append((url, payload))
        empty = payload[1] in (1, 2)
        if url.endswith("reference") and empty:
            return {"http": 400, "outcome": "reject", "body": "DecoderError: empty redeemers"}
        if payload[1] == 3:
            return {
                "http": 400,
                "outcome": "reject",
                "body": "ValidationTagMismatch (IsValid True) FailedUnexpectedly",
            }
        return {
            "http": 400,
            "outcome": "reject",
            "body": "transaction failed phase one validation: MissingRedeemers",
        }

    report = run_redeemer_container_differential(
        corpus_dir=tmp_path,
        target_url="http://target",
        reference_url="http://reference",
        submitter=submit,
    )

    assert report["checks"] == {
        "all_cases_retained": True,
        "all_observations_classifiable": True,
        "structural_expectations_match": False,
        "no_transport_failure": True,
    }
    assert report["evidence_scope"] == "structural_decoder_only"
    assert report["ledger_control_status"]["valid_control_present"] is False
    assert "not run" in report["ledger_control_status"]["reason"]
    assert report["cases"][1]["observations"]["target"]["classification"] == "ledger_reject"
    assert report["cases"][1]["observations"]["reference"]["classification"] == "decode_reject"
    assert report["cases"][3]["observations"]["target"]["classification"] == "phase2_reject"
    assert len(submitted) == 10


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ({"http": 400, "body": "unrecognized rejection"}, "inconclusive_reject"),
        ({"http": 400, "body": "ConwayUtxowFailure (CollectErrors (NoRedeemer ...))"}, "ledger_reject"),
        ({"http": 400, "body": "transaction failed phase one validation"}, "ledger_reject"),
        ({"http": 400, "body": "ValidationTagMismatch (IsValid True) FailedUnexpectedly"}, "phase2_reject"),
        ({"http": 400, "body": "BadInputsUTxO: input already spent"}, "inconclusive_reject"),
        ({"http": 400, "body": "FeeTooSmallUTxO: minimum fee not met"}, "inconclusive_reject"),
        ({"http": 400, "body": "PPViewHashesDontMatch"}, "inconclusive_reject"),
        ({"http": 503, "body": "ValidationTagMismatch phase two validation"}, "unavailable"),
        ({"http": 503, "body": "service unavailable"}, "unavailable"),
        ({"http": 0, "error": "TimeoutError"}, "unavailable"),
    ],
)
def test_validation_stage_requires_diagnostic_evidence(response, expected):
    assert _validation_stage(response) == expected


def test_transport_failure_is_not_reported_as_a_node_crash(tmp_path):
    _write_corpus(tmp_path)

    report = run_redeemer_container_differential(
        corpus_dir=tmp_path,
        target_url="http://target",
        reference_url="http://reference",
        submitter=lambda url, payload: {
            "http": 503,
            "outcome": "crash",
            "body": "upstream unavailable",
        },
    )

    assert report["checks"]["no_transport_failure"] is False
    assert report["checks"]["all_observations_classifiable"] is False
    for case in report["cases"]:
        for observation in case["observations"].values():
            assert observation["classification"] == "unavailable"
            assert observation.get("outcome") != "crash"


def test_structural_corpus_cannot_satisfy_the_ledger_control_assertion(tmp_path):
    class Handle:
        run_dir = tmp_path

    result_dir = tmp_path / "outputs/redeemer-container-differential"
    result_dir.mkdir(parents=True)
    (result_dir / "result.json").write_text(
        json.dumps(
            {
                "evidence_scope": "structural_decoder_only",
                "ledger_control_status": {"valid_control_present": False},
                "checks": {
                    "all_cases_retained": True,
                    "all_observations_classifiable": True,
                    "structural_expectations_match": True,
                    "no_transport_failure": True,
                },
            }
        ),
        encoding="utf-8",
    )

    assertion = RedeemerContainerDifferentialMatches(
        params={"result_path": "outputs/redeemer-container-differential/result.json"}
    ).evaluate(Handle())

    assert assertion["result"] == "fail"
    assert "valid ledger control" in assertion["note"]


def test_redeemer_assertion_result_satisfies_real_run_handle_contract(tmp_path):
    handle = forensic.start_run(
        scenario_id="redeemer-assertion-contract",
        scenario_yaml=b"id: redeemer-assertion-contract\n",
        target={"implementation": "amaru", "version": "test"},
        runtime="devnet",
        profile_id=None,
        profile_resolved=None,
        framework_version="test",
        framework_commit="a" * 40,
        seed=1,
        runs_dir=tmp_path / "runs",
        state_dir=tmp_path / "state",
    )
    result_dir = handle.run_dir / "outputs/redeemer-container-differential"
    result_dir.mkdir(parents=True)
    ledger_control = {
        "complete": False,
        "valid_control_present": False,
        "mutation_payloads_match": True,
        "funding_inputs_isolated": True,
        "outcomes_conclusive": False,
    }
    (result_dir / "result.json").write_text(
        json.dumps({"ledger_control_status": ledger_control}), encoding="utf-8"
    )

    result = RedeemerContainerDifferentialMatches(
        params={"result_path": "outputs/redeemer-container-differential/result.json"}
    ).evaluate(handle)
    handle.assertion_result(**result)
    handle.end(exit_status="fail")

    assertions = json.loads((handle.run_dir / "assertions.json").read_text())
    assert assertions[0]["data_points_used"] == [ledger_control]


def _governance_body(*, denominator: int = 2) -> bytes:
    import cbor2

    action = [
        0,
        None,
        {33: cbor2.CBORTag(30, [1, denominator])},
        None,
    ]
    proposal = [100_000_000_000, b"\xe0" + b"r" * 28, action, ["https://dwarf.example/g", b"a" * 32]]
    return cbor2.dumps({0: [[b"i" * 32, 0]], 20: cbor2.CBORTag(258, [proposal])})


def test_governance_zero_denominator_mutation_preserves_every_other_body_field():
    import cbor2
    from fractions import Fraction
    from scripts.runtime_governance_zero_denominator import mutate_min_fee_ref_script_cost

    original = _governance_body()
    mutated = mutate_min_fee_ref_script_cost(original)
    valid_marker = cbor2.dumps(33) + cbor2.dumps(Fraction(1, 2))
    invalid_marker = cbor2.dumps(33) + cbor2.dumps(cbor2.CBORTag(30, [1, 0]))

    assert original.count(valid_marker) == 1
    assert mutated.count(invalid_marker) == 1
    assert mutated.replace(invalid_marker, valid_marker) == original


def test_governance_zero_denominator_final_body_is_resigned(tmp_path):
    import cbor2
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from scripts.runtime_governance_zero_denominator import (
        mutate_min_fee_ref_script_cost,
        resign_transaction_body,
    )

    keys = [Ed25519PrivateKey.from_private_bytes(bytes([value]) * 32) for value in (3, 7)]
    paths = []
    for index, key in enumerate(keys):
        path = tmp_path / f"key-{index}.skey"
        seed = key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        path.write_text(json.dumps({"cborHex": (b"\x58\x20" + seed).hex()}))
        paths.append(path)

    body = _governance_body()
    body_hash = hashlib.blake2b(body, digest_size=32).digest()
    witnesses = cbor2.CBORTag(
        258,
        [
            [
                key.public_key().public_bytes(
                    serialization.Encoding.Raw, serialization.PublicFormat.Raw
                ),
                key.sign(body_hash),
            ]
            for key in keys
        ],
    )
    template = b"\x84" + body + cbor2.dumps({0: witnesses}) + b"\xf5\xf6"
    mutated_body = mutate_min_fee_ref_script_cost(body)
    payload = resign_transaction_body(template, mutated_body, paths)
    assert payload[1 : 1 + len(mutated_body)] == mutated_body
    decoded = cbor2.loads(payload[1 + len(mutated_body) : -2])
    signed_hash = hashlib.blake2b(mutated_body, digest_size=32).digest()

    by_public = {bytes(public): bytes(signature) for public, signature in decoded[0]}
    for key in keys:
        public = key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        key.public_key().verify(by_public[public], signed_hash)


def test_governance_mutant_is_reference_first_with_attempt_bound_evidence(
    monkeypatch, tmp_path
):
    from scripts import runtime_governance_zero_denominator as governance

    payload = b"governance-zero-denominator"
    transaction_id = "f" * 64
    reference_log = tmp_path / "reference.log"
    target_log = tmp_path / "target.log"
    reference_log.write_text("startup\n", encoding="utf-8")
    target_log.write_text("startup\n", encoding="utf-8")
    reference = {
        "id": "node2",
        "container_id": "a" * 64,
        "container_image_id": "sha256:" + "b" * 64,
        "source_revision": "c" * 40,
        "log_path": str(reference_log),
    }
    target = {
        "id": "node1",
        "container_id": "d" * 64,
        "container_image_id": "sha256:" + "e" * 64,
        "source_revision": "1" * 40,
        "log_path": str(target_log),
    }
    identity = {
        "node_id": reference["id"],
        "container_id": reference["container_id"],
        "container_image_id": reference["container_image_id"],
        "source_revision": reference["source_revision"],
    }
    events = []

    def reference_submit(**_kwargs):
        events.append("reference")
        reference_log.write_text(
            reference_log.read_text(encoding="utf-8")
            + "MiniProtocolNum 6 ResponderDir terminated with exception "
            + "DecoderFailure LocalTxSubmission DeserialiseFailure 562 "
            + '"Denominator cannot be zero"\n',
            encoding="utf-8",
        )
        return {
            "http": 0,
            "body": "",
            "transport": "response-decode-failed",
            "classification": "unavailable",
            "protocol_receipt": {
                "outcome": "decode_close",
                "payload_sha256": hashlib.sha256(payload).hexdigest(),
                "payload_length": len(payload),
            },
            "identity_binding": identity,
        }

    def target_submit(_container_id, submitted):
        events.append("target")
        assert submitted == payload
        target_log.write_text(
            target_log.read_text(encoding="utf-8")
            + f"mempool: transaction.accepted tx_id={transaction_id}\n",
            encoding="utf-8",
        )
        return {"http": 202, "body": transaction_id}

    monkeypatch.setattr(governance, "_submit_reference_raw", reference_submit)
    monkeypatch.setattr(governance, "_submit_amaru_container", target_submit)

    observed = governance._submit_zero_denominator_attempts(
        runtime={"network_magic": 42},
        reference=reference,
        target=target,
        payload=payload,
        transaction_id=transaction_id,
        helper_image="sha256:" + "2" * 64,
        before_target=lambda: events.append("target-pre-submit-tip")
        or {"block": 7, "slot": 44, "hash": "3" * 64},
        admission_wait_seconds=0,
    )

    assert events == ["reference", "target-pre-submit-tip", "target"]
    assert observed["submission_order"] == ["reference", "target"]
    assert observed["target_pre_submit_tip"]["block"] == 7
    assert observed["reference"]["classification"] == "decode_reject"
    assert observed["reference"]["reference_decoder_evidence"][
        "payload_sha256"
    ] == hashlib.sha256(payload).hexdigest()
    assert observed["target"]["classification"] == "accepted"
    assert observed["target"]["local_mempool_admission_observed"] is True
    admission = observed["target"]["amaru_admission_evidence"]
    assert admission["transaction_id"] == transaction_id
    assert admission["payload_sha256"] == hashlib.sha256(payload).hexdigest()
    assert admission["identity"] == {
        "node_id": target["id"],
        "container_id": target["container_id"],
        "container_image_id": target["container_image_id"],
        "source_revision": target["source_revision"],
    }
    assert admission["log_start_offset"] == len("startup\n")
    assert len(admission["log_slice_sha256"]) == 64


def test_governance_http_202_without_new_exact_admission_is_inconclusive(
    tmp_path
):
    from scripts import runtime_governance_zero_denominator as governance

    transaction_id = "a" * 64
    log_path = tmp_path / "target.log"
    log_path.write_text(
        f"mempool: transaction.accepted tx_id={transaction_id}\n",
        encoding="utf-8",
    )
    target = {
        "id": "node1",
        "container_id": "b" * 64,
        "container_image_id": "sha256:" + "c" * 64,
        "source_revision": "d" * 40,
        "log_path": str(log_path),
    }

    observed = governance._submit_amaru_with_admission_log(
        target=target,
        payload=b"payload",
        transaction_id=transaction_id,
        submit=lambda _container_id, _payload: {
            "http": 202,
            "body": transaction_id,
        },
        wait_seconds=0,
    )

    assert observed["http"] == 202
    assert observed["classification"] == "unavailable"
    assert observed["local_mempool_admission_observed"] is False
    assert observed["amaru_admission_evidence"]["status"] == (
        "exact-transaction-admission-not-observed"
    )


def test_governance_admission_rejects_transaction_id_inside_larger_hex_token(
    tmp_path,
):
    from scripts import runtime_governance_zero_denominator as governance

    transaction_id = "a" * 64
    log_path = tmp_path / "target.log"
    log_path.write_text("startup\n", encoding="utf-8")
    target = {
        "id": "node1",
        "container_id": "b" * 64,
        "container_image_id": "sha256:" + "c" * 64,
        "source_revision": "d" * 40,
        "log_path": str(log_path),
    }

    def submit(_container_id, _payload):
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(
                "mempool: transaction.accepted "
                f"tx_id=0{transaction_id}f\n"
            )
        return {"http": 202, "body": transaction_id}

    observed = governance._submit_amaru_with_admission_log(
        target=target,
        payload=b"payload",
        transaction_id=transaction_id,
        submit=submit,
        wait_seconds=0,
    )

    assert observed["classification"] == "unavailable"
    assert observed["local_mempool_admission_observed"] is False
    assert observed["amaru_admission_evidence"]["status"] == (
        "exact-transaction-admission-not-observed"
    )


def test_governance_missing_reference_decoder_evidence_stops_before_target(
    monkeypatch, tmp_path
):
    from scripts import runtime_governance_zero_denominator as governance

    payload = b"mutant"
    reference_log = tmp_path / "reference.log"
    target_log = tmp_path / "target.log"
    reference_log.write_text("startup\n", encoding="utf-8")
    target_log.write_text("startup\n", encoding="utf-8")
    reference = {
        "id": "node2",
        "container_id": "a" * 64,
        "container_image_id": "sha256:" + "b" * 64,
        "source_revision": "c" * 40,
        "log_path": str(reference_log),
    }
    target = {
        "id": "node1",
        "container_id": "d" * 64,
        "container_image_id": "sha256:" + "e" * 64,
        "source_revision": "f" * 40,
        "log_path": str(target_log),
    }
    identity = {
        "node_id": reference["id"],
        "container_id": reference["container_id"],
        "container_image_id": reference["container_image_id"],
        "source_revision": reference["source_revision"],
    }
    monkeypatch.setattr(
        governance,
        "_submit_reference_raw",
        lambda **_kwargs: {
            "http": 0,
            "classification": "unavailable",
            "protocol_receipt": {
                "outcome": "transport_unavailable",
                "payload_sha256": hashlib.sha256(payload).hexdigest(),
                "payload_length": len(payload),
            },
            "identity_binding": identity,
        },
    )
    monkeypatch.setattr(
        governance,
        "_submit_amaru_container",
        lambda *_args: pytest.fail("target submission must not run"),
    )

    observed = governance._submit_zero_denominator_attempts(
        runtime={"network_magic": 42},
        reference=reference,
        target=target,
        payload=payload,
        transaction_id="1" * 64,
        helper_image="sha256:" + "2" * 64,
        admission_wait_seconds=0,
    )

    assert observed["submission_order"] == ["reference"]
    assert observed["reference"]["classification"] == "unavailable"
    assert observed["target"]["classification"] == "skipped"
    assert observed["target"]["reason"] == (
        "direct reference decoder evidence is unavailable"
    )


def test_governance_admission_uses_independent_inputs_and_requires_valid_control():
    from scripts.runtime_governance_zero_denominator import (
        _control_failure_reason,
        _control_submission_specs,
        _valid_control_complete,
        admission_collateral_inputs,
        admission_funding_amounts,
        admission_funding_inputs,
        admission_registration_input,
        assess_governance_admission,
        validate_admission_funding_outputs,
    )

    setup_id = "1" * 64
    assigned = admission_funding_inputs(setup_id)
    assert assigned == {
        "nonzero-control": {
            "target": f"{setup_id}#0",
            "reference": f"{setup_id}#2",
        },
        "zero-denominator": {"shared": f"{setup_id}#4"},
        "post-mutant-valid-traffic": {"target": f"{setup_id}#7"},
    }
    collateral = admission_collateral_inputs(setup_id)
    assert collateral == {
        "nonzero-control": {
            "target": f"{setup_id}#1",
            "reference": f"{setup_id}#3",
        },
        "zero-denominator": {"shared": f"{setup_id}#5"},
    }
    registration_input = admission_registration_input(setup_id)
    assert registration_input == f"{setup_id}#6"
    allocated = {
        *assigned["nonzero-control"].values(),
        *assigned["zero-denominator"].values(),
        *assigned["post-mutant-valid-traffic"].values(),
        *collateral["nonzero-control"].values(),
        *collateral["zero-denominator"].values(),
    }
    assert len(allocated) == 7
    assert registration_input not in allocated
    assert admission_funding_amounts(
        proposal_lovelace=105_000_000_000,
        collateral_lovelace=5_000_000,
        registration_lovelace=9_000_000,
        post_control_lovelace=5_000_000,
    ) == (
        105_000_000_000,
        5_000_000,
        105_000_000_000,
        5_000_000,
        105_000_000_000,
        5_000_000,
        9_000_000,
        5_000_000,
    )
    assert _control_submission_specs(assigned, collateral) == (
        (
            "target",
            f"{setup_id}#0",
            f"{setup_id}#1",
        ),
        (
            "reference",
            f"{setup_id}#2",
            f"{setup_id}#3",
        ),
    )
    validate_admission_funding_outputs(
        {
            f"{setup_id}#0": {"value": {"lovelace": 105_000_000_000}},
            f"{setup_id}#1": {"value": {"lovelace": 5_000_000}},
            f"{setup_id}#2": {"value": {"lovelace": 105_000_000_000}},
            f"{setup_id}#3": {"value": {"lovelace": 5_000_000}},
            f"{setup_id}#4": {"value": {"lovelace": 105_000_000_000}},
            f"{setup_id}#5": {"value": {"lovelace": 5_000_000}},
            f"{setup_id}#6": {"value": {"lovelace": 9_000_000}},
            f"{setup_id}#7": {"value": {"lovelace": 5_000_000}},
        },
        assigned=assigned,
        expected_lovelace=105_000_000_000,
        collateral=collateral,
        expected_collateral_lovelace=5_000_000,
        registration_input=registration_input,
        expected_registration_lovelace=9_000_000,
        post_control_input=assigned["post-mutant-valid-traffic"]["target"],
        expected_post_control_lovelace=5_000_000,
    )
    with pytest.raises(RuntimeError, match="exact independent funding output"):
        validate_admission_funding_outputs(
            {f"{setup_id}#0": {"value": {"lovelace": 105_000_000_000}}},
            assigned=assigned,
            expected_lovelace=105_000_000_000,
        )
    duplicated = json.loads(json.dumps(assigned))
    duplicated["nonzero-control"]["reference"] = duplicated["nonzero-control"][
        "target"
    ]
    with pytest.raises(RuntimeError, match="funding and collateral inputs are not isolated"):
        validate_admission_funding_outputs(
            {
                f"{setup_id}#0": {"value": {"lovelace": 105_000_000_000}},
                f"{setup_id}#4": {"value": {"lovelace": 105_000_000_000}},
            },
            assigned=duplicated,
            expected_lovelace=105_000_000_000,
        )

    records = [
        {
            "case_id": "nonzero-control",
            "target": {
                "classification": "accepted",
                "included": True,
                "target_adopted": True,
                "funding_input": assigned["nonzero-control"]["target"],
                "collateral_input": collateral["nonzero-control"]["target"],
                "payload_sha256": "a" * 64,
                "body_sha256": "c" * 64,
                "transaction_id": "a" * 64,
            },
            "reference": {
                "classification": "accepted",
                "included": True,
                "target_adopted": True,
                "funding_input": assigned["nonzero-control"]["reference"],
                "collateral_input": collateral["nonzero-control"]["reference"],
                "payload_sha256": "b" * 64,
                "body_sha256": "d" * 64,
                "transaction_id": "b" * 64,
            },
        },
        {
            "case_id": "zero-denominator",
            "funding_input": assigned["zero-denominator"]["shared"],
            "collateral_input": collateral["zero-denominator"]["shared"],
            "payload_sha256": "b" * 64,
            "target": {
                "classification": "accepted",
                "payload_sha256": "b" * 64,
                "local_mempool_admission_observed": True,
            },
            "reference": {
                "classification": "decode_reject",
                "payload_sha256": "b" * 64,
                "reference_decoder_evidence": {"matched_line": "Denominator cannot be zero"},
            },
            "propagation_observation": {
                "status": "not-included-after-reference-progress",
                "p2p_relay_observed": None,
                "relay_rejection_observed": None,
            },
        },
        {
            "case_id": "post-mutant-valid-traffic",
            "target": {
                "classification": "accepted",
                "included": True,
                "target_adopted": True,
                "local_mempool_admission_observed": True,
                "funding_input": assigned["post-mutant-valid-traffic"]["target"],
                "payload_sha256": "e" * 64,
                "body_sha256": "f" * 64,
                "transaction_id": "9" * 64,
            },
        },
    ]
    result = assess_governance_admission(records)
    assert _valid_control_complete(records[0]) is True
    assert result["complete"] is True
    assert result["valid_control_present"] is True
    assert result["funding_inputs_isolated"] is True
    assert result["mutation_payloads_match"] is True
    assert result["direct_reference_decoder_reject"] is True
    assert result["target_local_admission_observed"] is True
    assert result["propagation_absence_observed"] is True
    assert result["post_mutant_valid_control_complete"] is True
    assert result["p2p_relay_observed"] is None
    assert result["relay_rejection_observed"] is None
    assert result["enactment_observed"] is False
    incomplete_control = json.loads(json.dumps(records[0]))
    incomplete_control["reference"]["included"] = False
    assert _valid_control_complete(incomplete_control) is False
    assert _control_failure_reason(incomplete_control) == (
        "nonzero governance reference control was accepted but lacks exact "
        "transaction inclusion evidence"
    )
    adoption_missing = json.loads(json.dumps(records[0]))
    adoption_missing["target"]["target_adopted"] = False
    assert _valid_control_complete(adoption_missing) is False
    assert _control_failure_reason(adoption_missing) == (
        "nonzero governance target control lacks exact target adoption evidence"
    )
    identity_missing = json.loads(json.dumps(records[0]))
    identity_missing["reference"].pop("body_sha256")
    assert _valid_control_complete(identity_missing) is False
    assert _control_failure_reason(identity_missing) == (
        "nonzero governance reference control lacks its exact transaction identity"
    )
    spent_input = json.loads(json.dumps(records[0]))
    spent_input["reference"]["classification"] = "inconclusive_reject"
    assert _valid_control_complete(spent_input) is False
    assert _control_failure_reason(spent_input) == (
        "nonzero governance reference control was not accepted (inconclusive_reject)"
    )


def test_governance_target_control_spend_cannot_mask_reference_control():
    from scripts.runtime_governance_zero_denominator import (
        _valid_control_complete,
        admission_collateral_inputs,
        admission_funding_inputs,
    )

    setup_id = "7" * 64
    funding = admission_funding_inputs(setup_id)["nonzero-control"]
    collateral = admission_collateral_inputs(setup_id)["nonzero-control"]
    spent = {funding["target"], collateral["target"]}
    assert funding["reference"] not in spent
    assert collateral["reference"] not in spent

    control = {
        "case_id": "nonzero-control",
        "target": {
            "classification": "accepted",
            "included": True,
            "target_adopted": True,
            "funding_input": funding["target"],
            "collateral_input": collateral["target"],
            "payload_sha256": "1" * 64,
            "body_sha256": "5" * 64,
            "transaction_id": "2" * 64,
        },
        "reference": {
            "classification": "accepted",
            "included": True,
            "target_adopted": True,
            "funding_input": funding["reference"],
            "collateral_input": collateral["reference"],
            "payload_sha256": "3" * 64,
            "body_sha256": "6" * 64,
            "transaction_id": "4" * 64,
        },
    }
    assert control["target"]["payload_sha256"] != control["reference"]["payload_sha256"]
    assert _valid_control_complete(control) is True


def test_governance_post_mutant_control_has_an_independent_funding_input():
    from scripts.runtime_governance_zero_denominator import (
        POST_CONTROL_CASE,
        admission_collateral_inputs,
        admission_funding_inputs,
        admission_registration_input,
    )

    setup_id = "8" * 64
    funding = admission_funding_inputs(setup_id)
    post_input = funding[POST_CONTROL_CASE]["target"]
    all_prior = {
        *funding["nonzero-control"].values(),
        *funding["zero-denominator"].values(),
        *admission_collateral_inputs(setup_id)["nonzero-control"].values(),
        *admission_collateral_inputs(setup_id)["zero-denominator"].values(),
        admission_registration_input(setup_id),
    }

    assert post_input == f"{setup_id}#7"
    assert post_input not in all_prior


def test_governance_post_mutant_control_uses_three_block_terminal_threshold(
    monkeypatch, tmp_path
):
    from scripts import runtime_governance_zero_denominator as governance

    captured = {}

    def fake_wait(**kwargs):
        captured.update(kwargs)
        return {}

    monkeypatch.setattr(governance, "_wait_for_reference_inclusion", fake_wait)
    governance._wait_for_post_mutant_reference_inclusion(
        cli="/owned/cardano-cli",
        socket_path="/env/socket/node2/sock",
        network_magic=42,
        address="addr_test1target",
        destination=tmp_path / "included.json",
        transaction_id="7" * 64,
        initial_tip={"block": 10, "slot": 20, "hash": "a" * 64},
        evidence_path=tmp_path / "evidence.json",
        target_propagation_gate={"source": "retained"},
    )

    assert captured["terminal_progress_blocks"] == 3


def test_governance_registers_both_return_credentials_before_proposal_build(
    monkeypatch, tmp_path
):
    from types import SimpleNamespace
    import cbor2
    from scripts import runtime_governance_zero_denominator as governance

    events = []
    private_roots = []
    addresses = {
        governance.CONTROL_CASE: "stake_test1control",
        governance.ZERO_CASE: "stake_test1mutant",
    }

    def fake_checked(command):
        events.append(tuple(str(item) for item in command))
        if "key-gen" in command:
            vkey = Path(command[command.index("--verification-key-file") + 1])
            skey = Path(command[command.index("--signing-key-file") + 1])
            private_roots.append(vkey.parent)
            vkey.write_text('{"type":"StakeVerificationKeyShelley_ed25519"}')
            skey.write_text('{"type":"StakeSigningKeyShelley_ed25519"}')
            return SimpleNamespace(stdout="")
        if command[-2:] == ["--testnet-magic", "42"] and "build" in command[:5]:
            case_id = Path(command[command.index("--stake-verification-key-file") + 1]).parent.name
            return SimpleNamespace(stdout=addresses[case_id] + "\n")
        if "registration-certificate" in command:
            Path(command[command.index("--out-file") + 1]).write_text("certificate")
            return SimpleNamespace(stdout="")
        if "transaction" in command and "build" in command:
            Path(command[command.index("--out-file") + 1]).write_text("body")
            return SimpleNamespace(stdout="")
        if "transaction" in command and "sign" in command:
            body = cbor2.dumps({0: [[b"i" * 32, 0]]})
            payload = b"\x84" + body + cbor2.dumps({0: []}) + b"\xf5\xf6"
            Path(command[command.index("--out-file") + 1]).write_text(
                json.dumps({"cborHex": payload.hex()})
            )
            return SimpleNamespace(stdout="")
        if "stake-address-info" in command:
            address = command[command.index("--address") + 1]
            Path(command[command.index("--out-file") + 1]).write_text(
                json.dumps([{"address": address, "rewardAccountBalance": 0}])
            )
            return SimpleNamespace(stdout="")
        return SimpleNamespace(stdout="")

    def fake_inclusion(**kwargs):
        events.append(("reference-included", kwargs["transaction_id"]))
        kwargs["destination"].write_text("{}")
        kwargs["evidence_path"].write_text(
            json.dumps(
                {
                    "status": "included",
                    "observations": [
                        {
                            "included": True,
                            "tip": {"slot": 44, "block": 7, "hash": "a" * 64},
                        }
                    ],
                }
            )
        )
        return {}

    def fake_adoption(**kwargs):
        events.append(("target-adopted", kwargs["expected_tip"]["hash"]))
        kwargs["evidence_path"].write_text(
            json.dumps({"status": "adopted", "expected_tip": kwargs["expected_tip"]})
        )

    monkeypatch.setattr(governance, "_checked", fake_checked)
    monkeypatch.setattr(governance, "_query_reference_tip", lambda **_: {"slot": 43})
    monkeypatch.setattr(governance, "_wait_for_reference_inclusion", fake_inclusion)
    monkeypatch.setattr(governance, "_wait_for_target_adoption", fake_adoption)

    output_dir = tmp_path / "runs/run-1/outputs/governance-zero-denominator"
    output_dir.mkdir(parents=True)
    setup_id = "1" * 64
    with governance._registered_return_credentials(
        cli="/env/cardano-cli",
        controller_cli="/controller/cardano-cli",
        controller_key_root=tmp_path / "private",
        key_root=Path("/env/utxo-keys/utxo1"),
        socket_path="/node.socket",
        network_magic=42,
        change_address="addr_test1funded",
        stake_deposit=2_000_000,
        registration_input=governance.admission_registration_input(setup_id),
        output_dir=output_dir,
        target_log_path=tmp_path / "amaru.log",
    ) as registered:
        events.append(("proposal-builder",))
        proposal = governance._proposal_build_command(
            cli="/env/cardano-cli",
            socket_path="/node.socket",
            network_magic=42,
            funding_input=governance.admission_funding_inputs(setup_id)[
                governance.CONTROL_CASE
            ]["target"],
            change_address="addr_test1funded",
            proposal_path=output_dir / "nonzero-control/parameter-update.action",
            active_script_hash="1" * 56,
            guardrails_script_path=governance.GUARDRAILS_SCRIPT,
            collateral_input=governance.admission_collateral_inputs(setup_id)[
                governance.CONTROL_CASE
            ]["target"],
            output_path=output_dir / "nonzero-control/transaction.body",
        )
        assert registered[governance.CONTROL_CASE]["stake_address"] == addresses[governance.CONTROL_CASE]
        assert registered[governance.ZERO_CASE]["stake_address"] == addresses[governance.ZERO_CASE]
        assert "--certificate-file" not in proposal
        assert governance.admission_registration_input(setup_id) not in proposal

    reference_index = next(i for i, item in enumerate(events) if item[0] == "reference-included")
    target_index = next(i for i, item in enumerate(events) if item[0] == "target-adopted")
    proposal_index = events.index(("proposal-builder",))
    assert reference_index < target_index < proposal_index
    registration_build = next(
        command
        for command in events
        if "transaction" in command and "build" in command and "--certificate-file" in command
    )
    assert registration_build.count("--certificate-file") == 2
    assert registration_build[registration_build.index("--witness-override") + 1] == "3"
    assert governance.admission_registration_input(setup_id) in registration_build
    assert all(
        item not in registration_build
        for item in {
            *governance.admission_funding_inputs(setup_id)[
                governance.CONTROL_CASE
            ].values(),
            *governance.admission_funding_inputs(setup_id)[
                governance.ZERO_CASE
            ].values(),
            *governance.admission_collateral_inputs(setup_id)[
                governance.CONTROL_CASE
            ].values(),
            *governance.admission_collateral_inputs(setup_id)[
                governance.ZERO_CASE
            ].values(),
        }
    )
    assert all(not path.exists() for path in private_roots)
    receipt = json.loads((output_dir / "registration-prep/result.json").read_text())
    assert receipt["status"] == "registered"
    assert set(receipt["credentials"]) == {governance.CONTROL_CASE, governance.ZERO_CASE}
    with pytest.raises(RuntimeError, match="not registered"):
        governance._registered_stake_state([], expected_address="stake_test1missing")

def test_governance_active_fixture_and_live_control_value_are_bound(tmp_path):
    from fractions import Fraction
    from scripts.runtime_governance_zero_denominator import (
        GUARDRAILS_SCRIPT,
        _control_ratio,
        _governance_action_command,
        _guardrails_witness_arguments,
        _require_fresh_pparam_root,
        _verified_guardrails_identity,
        prepare_governance_anchor,
    )

    active = "186e32faa80a26810392fda6d559c7ed4721a65ce1c9d4ef3e1c87b4"
    runtime = {
        "governance_guardrails_fixture": {
            "fixture": "always-succeeds-v3",
            "effective_script_hash": active,
            "script_sha256": hashlib.sha256(GUARDRAILS_SCRIPT.read_bytes()).hexdigest(),
        }
    }
    gov_state = {
        "constitution": {"script": active},
        "nextRatifyState": {
            "enactedGovActions": [],
            "expiredGovActions": [],
            "ratificationDelayed": False,
            "nextEnactState": {
                "prevGovActionIds": {
                    "Committee": None,
                    "Constitution": None,
                    "HardFork": None,
                    "PParamUpdate": None,
                }
            }
        },
    }

    assert _verified_guardrails_identity(runtime=runtime, gov_state=gov_state) == active
    _require_fresh_pparam_root(gov_state)
    assert _control_ratio({"minFeeRefScriptCostPerByte": 15}) == Fraction(15, 1)
    args = _guardrails_witness_arguments(
        active_script_hash=active,
        script_path=GUARDRAILS_SCRIPT,
        collateral_input="a" * 64 + "#1",
    )
    assert args == [
        "--proposal-script-file",
        str(GUARDRAILS_SCRIPT),
        "--proposal-redeemer-value",
        "{}",
        "--tx-in-collateral",
        "a" * 64 + "#1",
    ]
    action_command = _governance_action_command(
        cli="/opt/cardano-cli",
        governance_action_deposit=100_000_000_000,
        stake_vkey=Path("/private/return.vkey"),
        anchor_url="https://dwarf.example/governance-zero-denominator",
        anchor_hash="d" * 64,
        guardrails_hash=active,
        control_ratio=Fraction(15, 1),
        action_path=Path("/work/action.json"),
    )
    assert "--ref-script-cost-per-byte" in action_command
    assert "--min-fee-ref-script-cost-per-byte" not in action_command
    assert action_command[action_command.index("--ref-script-cost-per-byte") + 1] == "15/1"
    assert "--check-anchor-data" in action_command

    output_dir = (
        tmp_path
        / "runs"
        / "run-anchor-contract"
        / "outputs"
        / "governance-zero-denominator"
    )
    anchor = prepare_governance_anchor(
        output_dir=output_dir,
        artifact_base_url="http://192.0.2.10:8787/",
    )
    assert anchor["url"] == (
        "http://192.0.2.10:8787/runs/run-anchor-contract/output?"
        "path=outputs%2Fgov-a.json"
    )
    assert anchor["bytes"] == (
        b'{"description":"DWARF zero-denominator governance admission control",'
        b'"schema_version":1}\n'
    )
    assert anchor["hash"] == hashlib.blake2b(
        anchor["bytes"], digest_size=32
    ).hexdigest()
    assert (output_dir.parent / "gov-a.json").read_bytes() == anchor["bytes"]

    with pytest.raises(RuntimeError, match="artifact base URL"):
        prepare_governance_anchor(
            output_dir=output_dir,
            artifact_base_url="https://user:secret@example.invalid/path",
        )

    stale = json.loads(json.dumps(gov_state))
    stale["nextRatifyState"]["nextEnactState"]["prevGovActionIds"]["PParamUpdate"] = {
        "txId": "b" * 64,
        "govActionIx": 0,
    }
    with pytest.raises(RuntimeError, match="fresh parameter-update root"):
        _require_fresh_pparam_root(stale)

    legacy = json.loads(json.dumps(gov_state))
    legacy["nextRatifyState"]["enactState"] = legacy["nextRatifyState"].pop(
        "nextEnactState"
    )
    _require_fresh_pparam_root(legacy)

    inconsistent = json.loads(json.dumps(gov_state))
    inconsistent["nextRatifyState"]["enactState"] = {
        "prevGovActionIds": {
            **inconsistent["nextRatifyState"]["nextEnactState"]["prevGovActionIds"],
            "PParamUpdate": {"txId": "c" * 64, "govActionIx": 0},
        }
    }
    with pytest.raises(RuntimeError, match="inconsistent"):
        _require_fresh_pparam_root(inconsistent)


def test_governance_ephemeral_keys_use_controller_cli_and_cleanup(monkeypatch, tmp_path):
    from scripts import runtime_governance_zero_denominator as governance

    controller_cli = tmp_path / "cardano-cli"
    controller_cli.write_text("controller", encoding="utf-8")
    commands = []

    def generated(command):
        commands.append(command)
        vkey = Path(command[command.index("--verification-key-file") + 1])
        skey = Path(command[command.index("--signing-key-file") + 1])
        vkey.write_text("vkey", encoding="utf-8")
        skey.write_text("skey", encoding="utf-8")

    monkeypatch.setattr(governance, "_checked", generated)
    retained_path = None
    with pytest.raises(RuntimeError, match="forced after key generation"):
        with governance._controller_generated_stake_keys(
            controller_cli=str(controller_cli),
            controller_key_root=tmp_path,
            case_id="control",
        ) as (vkey, skey):
            retained_path = vkey.parent
            assert vkey.is_file() and skey.is_file()
            assert commands[0][0] == str(controller_cli)
            assert "/env/" not in " ".join(commands[0])
            raise RuntimeError("forced after key generation")
    assert retained_path is not None and not retained_path.exists()


def test_governance_admission_transport_close_is_inconclusive():
    from scripts.runtime_governance_zero_denominator import assess_governance_admission

    records = [
        {
            "case_id": "nonzero-control",
            "funding_input": "2" * 64 + "#0",
            "payload_sha256": "a" * 64,
            "target": {"classification": "accepted", "included": True},
            "reference": {"classification": "accepted", "included": True},
        },
        {
            "case_id": "zero-denominator",
            "funding_input": "2" * 64 + "#1",
            "payload_sha256": "b" * 64,
            "target": {"classification": "decode_reject"},
            "reference": {"classification": "unavailable"},
        },
    ]
    result = assess_governance_admission(records)
    assert result["complete"] is False
    assert result["outcomes_conclusive"] is False


def test_fresh_live_control_assertion_does_not_require_historical_expected_split(tmp_path):
    class Handle:
        run_dir = tmp_path

    result_dir = tmp_path / "outputs/redeemer-container-differential"
    result_dir.mkdir(parents=True)
    (result_dir / "result.json").write_text(
        json.dumps(
            {
                "ledger_control_status": {
                    "complete": True,
                    "valid_control_present": True,
                    "mutation_payloads_match": True,
                    "funding_inputs_isolated": True,
                    "outcomes_conclusive": True,
                },
                "structural_diagnostics": {
                    "checks": {"structural_expectations_match": False}
                },
            }
        ),
        encoding="utf-8",
    )

    assertion = RedeemerContainerDifferentialMatches(
        params={"result_path": "outputs/redeemer-container-differential/result.json"}
    ).evaluate(Handle())

    assert assertion["result"] == "pass"


def test_differential_rejects_changed_fixture_before_submission(tmp_path):
    _write_corpus(tmp_path)
    first = next(tmp_path.glob("*.tx"))
    first.write_text(json.dumps({"cborHex": "8409a0f5f6"}), encoding="utf-8")
    submitted = []

    with pytest.raises(ValueError, match="digest mismatch"):
        run_redeemer_container_differential(
            corpus_dir=tmp_path,
            target_url="http://target",
            reference_url="http://reference",
            submitter=lambda url, payload: submitted.append((url, payload)),
        )

    assert submitted == []


def test_redeemer_container_primitives_and_scenario_validate():
    root = Path(__file__).resolve().parents[1]
    registry_path = root / "dwarf/primitives/registry.json"
    registry = load_registry(registry_path)

    assert registry["runtime_redeemer_container_differential"].family == "load"
    assert registry["redeemer_container_differential_matches"].family == "assertion"
    result = semantic_validate_scenario(
        root
        / "dwarf/scenarios/ledger-submit-empty-redeemers-container-differential-amaru-cardano-node.yaml",
        registry_path=registry_path,
    )
    assert result["errors"] == []


def test_governance_zero_denominator_primitives_and_scenario_validate(monkeypatch, tmp_path):
    root = Path(__file__).resolve().parents[1]
    registry_path = root / "dwarf/primitives/registry.json"
    registry = load_registry(registry_path)

    assert registry["runtime_governance_zero_denominator_admission"].family == "load"
    assert registry["governance_zero_denominator_admission_matches"].family == "assertion"
    result = semantic_validate_scenario(
        root
        / "dwarf/scenarios/governance-zero-denominator-admission-differential-amaru-cardano-node.yaml",
        registry_path=registry_path,
    )
    assert result["errors"] == []
    scenario = json.loads(
        (
            root
            / "dwarf/scenarios/governance-zero-denominator-admission-differential-amaru-cardano-node.yaml"
        ).read_text(encoding="utf-8")
    )
    assert scenario["substrate"]["governance_guardrails_fixture"] == {
        "name": "always-succeeds-v3",
        "expected_active_script_hash": "fa24fb305126805cf2164c161d852a0e7330cf988f1fe558cf7d4a64",
    }
    observation = next(
        item
        for item in scenario["load"]
        if item["primitive"] == "runtime_multi_node_observation"
    )
    assert observation["observation_primitives"] == [
        "tip_state",
        "connection_state",
    ]

    runtime_path = tmp_path / "outputs/substrate-compose/runtime.json"
    runtime_path.parent.mkdir(parents=True)
    runtime_path.write_text(json.dumps({"network": "testnet_42"}), encoding="utf-8")

    class Handle:
        run_dir = tmp_path

    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return {"admission_status": {"complete": False}}

    monkeypatch.setattr(
        "scripts.runtime_governance_zero_denominator.run_governance_zero_denominator_admission",
        fake_run,
    )
    primitive = submit_primitives.RuntimeGovernanceZeroDenominatorAdmission(
        params={
            "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
            "target_node": "node1",
            "reference_node": "node2",
            "output_dir": "outputs/governance-zero-denominator",
        }
    )
    primitive.bound_substrate = {
        "nodes": [
            {"id": "node1", "impl": "amaru", "version": "10.11.test", "source_revision": "a" * 40},
            {"id": "node2", "impl": "cardano-node", "version": "11.1.test", "source_revision": "b" * 40},
        ]
    }
    primitive.run(Handle(), None)
    assert captured["expected_target_version"] == "10.11.test"
    assert captured["expected_target_source"] == "a" * 40
    assert captured["expected_reference_version"] == "11.1.test"
    assert captured["expected_reference_source"] == "b" * 40


def test_governance_assertion_satisfies_real_run_handle_contract(tmp_path):
    handle = forensic.start_run(
        scenario_id="governance-zero-denominator-contract",
        scenario_yaml=b"id: governance-zero-denominator-contract\n",
        target={"implementation": "amaru", "version": "test"},
        runtime="devnet",
        profile_id=None,
        profile_resolved=None,
        framework_version="test",
        framework_commit="b" * 40,
        seed=2,
        runs_dir=tmp_path / "runs",
        state_dir=tmp_path / "state",
    )
    result_dir = handle.run_dir / "outputs/governance-zero-denominator"
    result_dir.mkdir(parents=True)
    admission_status = {
        "complete": False,
        "valid_control_present": True,
        "funding_inputs_isolated": True,
        "outcomes_conclusive": False,
        "enactment_observed": False,
    }
    (result_dir / "result.json").write_text(
        json.dumps({"admission_status": admission_status}), encoding="utf-8"
    )

    result = submit_primitives.GovernanceZeroDenominatorAdmissionMatches(
        params={}
    ).evaluate(handle)
    handle.assertion_result(**result)
    handle.end(exit_status="fail")
    assertions = json.loads((handle.run_dir / "assertions.json").read_text())
    assert assertions[0]["data_points_used"] == [admission_status]
