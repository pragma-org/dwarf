from scripts.runtime_amaru_measurement_calibration import (
    ACCEPTANCE_CASE_SET,
    build_attempt_record,
    build_case_plan,
    build_handshake_frame,
    build_result_context,
    build_workload_identity,
    classify_case_response,
    classify_unsupported_handshake_response,
    summarize_attempts,
)
from scripts import runtime_amaru_measurement_calibration as calibration


def _mux_response(payload: bytes) -> bytes:
    return b"\x00\x00\x00\x00" + len(payload).to_bytes(4, "big") + payload


def test_attempt_record_retains_bounded_wire_transcript_and_every_elapsed_time():
    request = bytes.fromhex("0000000100000000000000028200")
    response = bytes.fromhex("8202a100")

    record = build_attempt_record(
        attempt_id="handshake-0000",
        outcome="rejected",
        detail="refuse-response",
        elapsed_micros=123,
        request=request,
        response=response,
    )

    assert record["elapsed_micros"] == 123
    assert record["request_hex"] == request.hex()
    assert record["request_length"] == len(request)
    assert record["response_hex"] == response.hex()
    assert record["response_length"] == len(response)
    assert record["request_sha256"].startswith("sha256:")
    assert record["response_sha256"].startswith("sha256:")


def test_result_context_records_timing_policy_run_identity_and_hardware(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "scripts.runtime_amaru_measurement_calibration.platform.uname",
        lambda: type("Uname", (), {"system": "Linux", "release": "test", "machine": "x86_64"})(),
    )
    monkeypatch.setattr(
        "scripts.runtime_amaru_measurement_calibration.os.cpu_count", lambda: 8
    )
    monkeypatch.setattr(
        "scripts.runtime_amaru_measurement_calibration._physical_memory_bytes",
        lambda: 16_000,
    )

    result = build_result_context(output_dir=tmp_path / "run-123")

    assert result["run_id"] == "run-123"
    assert result["timing_policy"] == {
        "clock": "monotonic-perf-counter-ns",
        "start": "before-tcp-connect",
        "stop": "terminal-response-eof-timeout-or-disconnect",
        "warmup": "none",
        "outcome_inclusion": "all",
    }
    assert result["hardware"] == {
        "system": "Linux",
        "kernel_release": "test",
        "architecture": "x86_64",
        "logical_cpu_count": 8,
        "physical_memory_bytes": 16_000,
    }
    assert result["runner"]["script_sha256"].startswith("sha256:")


def test_calibration_attempt_summary_keeps_every_terminal_outcome_and_elapsed_time():
    attempts = [
        {"attempt_id": "a0", "outcome": "rejected", "elapsed_micros": 10},
        {"attempt_id": "a1", "outcome": "timeout", "elapsed_micros": 30},
        {"attempt_id": "a2", "outcome": "disconnected", "elapsed_micros": 20},
        {"attempt_id": "a3", "outcome": "unclassified", "elapsed_micros": 40},
    ]

    result = summarize_attempts(attempts)

    assert result["combined"]["sample_count"] == 4
    assert result["combined"]["mean"] == 25.0
    assert set(result["by_outcome"]) == {
        "rejected",
        "timeout",
        "disconnected",
        "unclassified",
    }
    assert all(row["sample_count"] == 1 for row in result["by_outcome"].values())


def test_calibration_workload_identity_is_mode_independent_and_exact():
    left = build_workload_identity(attempt_count=40, seed="0xA11CE501")
    right = build_workload_identity(attempt_count=40, seed="0xA11CE501")

    assert left == right
    assert left["attempt_count"] == 40
    assert left["transport"] == "tcp"
    assert left["target_port"] == 3000
    assert left["mux_direction_bit"] == 0
    assert left["response_cap_bytes"] == 256
    assert left["workload_digest"].startswith("sha256:")


def test_calibration_connects_to_the_inbound_responder_mux_direction():
    frame = build_handshake_frame()
    header_word = int.from_bytes(frame[4:8], "big")

    assert header_word >> 31 == 0
    assert header_word & 0x7FFF0000 == 0


def test_acceptance_case_plan_is_balanced_and_reaches_all_decode_outcomes():
    plan = build_case_plan(attempt_count=120, case_set=ACCEPTANCE_CASE_SET)

    assert len(plan) == 120
    counts = {
        case: sum(1 for row in plan if row["name"] == case)
        for case in {row["name"] for row in plan}
    }
    assert counts == {
        "supported-version-acceptance": 40,
        "unsupported-version-refusal": 40,
        "malformed-cbor-rejection": 40,
    }
    assert {row["expected_external_outcome"] for row in plan} == {
        "accepted",
        "rejected",
    }
    assert {row["expected_decode_outcome"] for row in plan} == {
        "decoded",
        "malformed",
    }


def test_acceptance_workload_identity_pins_every_case_and_wire_payload():
    identity = build_workload_identity(
        attempt_count=120,
        seed="0xA11CE502",
        case_set=ACCEPTANCE_CASE_SET,
    )

    assert identity["case_set"] == ACCEPTANCE_CASE_SET
    assert identity["attempt_count"] == 120
    assert [row["attempt_count"] for row in identity["cases"]] == [40, 40, 40]
    assert all(row["payload_hex"] for row in identity["cases"])
    assert identity["workload_digest"].startswith("sha256:")


def test_unsupported_handshake_eof_and_response_are_both_retained_as_rejections():
    assert classify_unsupported_handshake_response(b"") == ("rejected", "eof")
    assert classify_unsupported_handshake_response(b"refuse") == (
        "rejected",
        "refuse-response",
    )


def test_supported_case_requires_an_actual_handshake_accept_message():
    accepted = _mux_response(bytes.fromhex("83010a182a"))
    refused = _mux_response(bytes.fromhex("820280"))

    assert classify_case_response("supported-version-acceptance", accepted) == (
        "accepted",
        "accept-response",
    )
    assert classify_case_response("supported-version-acceptance", refused) == (
        "rejected",
        "refuse-response",
    )
    assert classify_case_response("supported-version-acceptance", b"not-a-mux-frame") == (
        "unclassified",
        "invalid-handshake-response",
    )
    assert classify_case_response("unsupported-version-refusal", accepted) == (
        "accepted",
        "accept-response",
    )


def test_mux_response_reader_retains_a_fragmented_complete_frame():
    frame = _mux_response(bytes.fromhex("83010a182a"))

    class FragmentedSocket:
        def __init__(self):
            self.fragments = [frame[:3], frame[3:8], frame[8:10], frame[10:]]

        def recv(self, size):
            fragment = self.fragments.pop(0) if self.fragments else b""
            assert len(fragment) <= size
            return fragment

    assert calibration._recv_mux_frame(FragmentedSocket()) == frame
