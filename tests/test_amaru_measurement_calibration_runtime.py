from scripts.runtime_amaru_measurement_calibration import (
    build_attempt_record,
    build_handshake_frame,
    build_result_context,
    build_workload_identity,
    classify_unsupported_handshake_response,
    summarize_attempts,
)


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


def test_unsupported_handshake_eof_and_response_are_both_retained_as_rejections():
    assert classify_unsupported_handshake_response(b"") == ("rejected", "eof")
    assert classify_unsupported_handshake_response(b"refuse") == (
        "rejected",
        "refuse-response",
    )
