from pathlib import Path

import pytest

from profile_manager.measurement_collectors.amaru_patched import (
    AMARU_MEASUREMENT_PATCH_SHA256,
    AMARU_SOURCE_REVISION,
    AmaruPatchedCollector,
    build_amaru_patched_factories,
    load_amaru_patched_telemetry,
    paired_overhead_calibration,
)
from profile_manager.measurement_runtime import CollectorContext


FIXTURE = Path(__file__).parent / "fixtures/amaru-b159-patched-traces.ndjson"


def _identity(mode="patched"):
    value = {
        "implementation": "amaru",
        "version": "10.11.20260912",
        "source_revision": AMARU_SOURCE_REVISION,
        "mode": mode,
        "image_digest": "sha256:" + ("a" if mode == "stock" else "b") * 64,
    }
    if mode == "patched":
        value["patch_set_sha256"] = AMARU_MEASUREMENT_PATCH_SHA256
    return value


def _context(tmp_path, measurement_id):
    run_dir = tmp_path / "run"
    return CollectorContext(
        measurement_id=measurement_id,
        definition={"id": measurement_id},
        parameters={},
        run_dir=run_dir,
        collector_dir=run_dir / "measurements" / "collectors" / measurement_id,
    )


def _collect(tmp_path, measurement_id):
    collector = AmaruPatchedCollector(
        {"id": measurement_id},
        json_trace_paths=[FIXTURE],
        target_identity=_identity(),
        include_existing=True,
    )
    context = _context(tmp_path, measurement_id)
    collector.prepare(context)
    collector.start(context)
    collector.stop(context)
    return collector.finalize(context)


def test_patched_fixture_normalizes_only_exact_public_measurement_events():
    result = load_amaru_patched_telemetry([FIXTURE])

    assert result["source_revision"] == AMARU_SOURCE_REVISION
    assert result["patch_set_sha256"] == AMARU_MEASUREMENT_PATCH_SHA256
    assert result["source_record_count"] == 16
    assert result["normalized_record_count"] == 15
    assert result["rejected_record_count"] == 1
    assert result["rejection_reasons"] == {"invalid-json": 1}
    assert {event["kind"] for event in result["events"]} == {
        "protocol-decode",
        "blockfetch-handler",
        "blockfetch-block-decode",
        "blockfetch-queue-residence",
        "txsubmission-depth",
        "txsubmission-residence",
        "txsubmission-blocking-residence",
    }


def test_patched_collector_accepts_exact_live_json_formatter_shape(tmp_path):
    trace = tmp_path / "live.ndjson"
    trace.write_text(
        '{"timestamp":"2026-09-18T20:21:30.506537Z","level":"DEBUG",'
        '"fields":{"bytes":78,"decode_micros":0,"decode_outcome":"decoded",'
        '"message":"measurement.protocol_decode",'
        '"message_type":"amaru_protocols::blockfetch::messages::RequestRange",'
        '"state_outcome":"accepted","total_micros":1},'
        '"target":"amaru::protocols"}\n',
        encoding="utf-8",
    )

    result = load_amaru_patched_telemetry([trace])

    assert result["normalized_record_count"] == 1
    assert result["rejected_record_count"] == 0
    assert result["events"][0]["kind"] == "protocol-decode"
    assert result["events"][0]["fields"]["state_outcome"] == "accepted"


def test_patched_collector_ignores_unrelated_valid_node_events_without_marking_incomplete(
    tmp_path,
):
    trace = tmp_path / "mixed-live.ndjson"
    trace.write_text(
        '{"fields":{"message":"tip.adopt","slot":1615},'
        '"target":"amaru::consensus"}\n'
        '{"fields":{"decode_micros":2,"decode_outcome":"decoded",'
        '"message":"measurement.protocol_decode","state_outcome":"accepted",'
        '"total_micros":3},"target":"amaru::protocols"}\n',
        encoding="utf-8",
    )
    context = _context(tmp_path, "amaru-patched-protocol-decode")
    collector = AmaruPatchedCollector(
        {"id": "amaru-patched-protocol-decode"},
        json_trace_paths=[trace],
        target_identity=_identity(),
        include_existing=True,
    )
    collector.prepare(context)
    collector.start(context)

    result = collector.finalize(context)

    assert result["export"]["source_record_count"] == 2
    assert result["export"]["normalized_record_count"] == 1
    assert result["export"]["ignored_record_count"] == 1
    assert result["export"]["rejected_record_count"] == 0
    assert result["export"]["incomplete"] is False


def test_protocol_decode_keeps_malformed_rejected_and_accepted_timing(tmp_path):
    result = _collect(tmp_path, "amaru-patched-protocol-decode")
    measurements = result["measurements"]

    assert measurements["protocol_decode"]["sample_count"] == 3
    assert measurements["protocol_total"]["sample_count"] == 3
    assert measurements["protocol_total_by_state_outcome"]["accepted"]["mean"] == 14.0
    assert measurements["protocol_total_by_state_outcome"]["rejected"]["mean"] == 17.0
    assert measurements["protocol_total_by_state_outcome"]["not_attempted"]["mean"] == 8.0
    assert measurements["protocol_decode_by_decode_outcome"]["malformed"]["mean"] == 8.0


def test_blockfetch_and_txsubmission_reports_include_depths_and_all_terminal_outcomes(tmp_path):
    blockfetch = _collect(tmp_path / "bf", "amaru-patched-blockfetch-queues")
    tx = _collect(tmp_path / "tx", "amaru-patched-txsubmission-residence")

    assert blockfetch["measurements"]["block_decode"]["sample_count"] == 2
    assert blockfetch["measurements"]["block_decode_by_outcome"]["rejected"]["mean"] == 6.0
    assert blockfetch["measurements"]["queue_residence_by_outcome"]["replaced"]["mean"] == 33.0
    assert blockfetch["measurements"]["pending_depth"]["maximum"] == 1.0

    terminal = tx["measurements"]["advertised_to_terminal"]
    by_outcome = tx["measurements"]["advertised_to_terminal_by_outcome"]
    assert terminal["sample_count"] == 3
    assert set(by_outcome) == {"accepted", "body_timeout", "rejected_duplicate"}
    assert by_outcome["body_timeout"]["mean"] == 900.0
    assert tx["measurements"]["advertised_to_body"]["sample_count"] == 2
    assert tx["measurements"]["body_to_terminal"]["sample_count"] == 2
    assert tx["measurements"]["pending_depth"]["maximum"] == 3.0
    assert tx["measurements"]["blocking_residence_by_outcome"]["rejected"]["mean"] == 4.0


def test_collector_refuses_wrong_patch_or_source_identity(tmp_path):
    bad_patch = _identity()
    bad_patch["patch_set_sha256"] = "0" * 64
    collector = AmaruPatchedCollector(
        {"id": "amaru-patched-protocol-decode"},
        json_trace_paths=[FIXTURE],
        target_identity=bad_patch,
    )
    with pytest.raises(ValueError, match="patch-set identity"):
        collector.prepare(_context(tmp_path, "amaru-patched-protocol-decode"))

    wrong_source = _identity()
    wrong_source["source_revision"] = "0" * 40
    collector = AmaruPatchedCollector(
        {"id": "amaru-patched-protocol-decode"},
        json_trace_paths=[FIXTURE],
        target_identity=wrong_source,
    )
    with pytest.raises(ValueError, match="source revision"):
        collector.prepare(_context(tmp_path, "amaru-patched-protocol-decode"))


def _calibration_run(mode, count, mean, *, seed="0xA11CE"):
    identity = _identity(mode)
    return {
        "target": identity,
        "workload_identity": {
            "scenario_id": "amaru-measurement-overhead-calibration",
            "seed": seed,
            "workload_digest": "sha256:" + "c" * 64,
            "attempt_count": 1000,
        },
        "runner": {"script": "calibration.py", "script_sha256": "sha256:" + "a" * 64},
        "timing_policy": {"clock": "monotonic-perf-counter-ns", "warmup": "none"},
        "hardware": {"architecture": "x86_64", "logical_cpu_count": 8},
        "attempts": {"total": count, "outcomes": {"rejected": count}},
        "measurements": {
            "block_application": {
                "status": "available",
                "unit": "us",
                "sample_count": count,
                "mean": mean,
                "median": mean - 1,
                "p95": mean + 5,
                "p99": mean + 9,
            }
        },
    }


def test_paired_calibration_requires_identity_workload_and_sample_parity():
    result = paired_overhead_calibration(
        _calibration_run("stock", 40, 100.0),
        _calibration_run("patched", 40, 103.0),
        minimum_samples=30,
    )

    assert result["status"] == "available"
    assert result["performance_authority"] == "common-metric-calibrated"
    assert result["calibration_scope"] == ["block_application"]
    assert result["claim_boundary"] == (
        "Only the listed common externally observed metrics are calibrated; "
        "patched internal measurements require a surface-specific paired workload."
    )
    assert result["metrics"]["block_application"]["mean_percent_delta"] == 3.0
    assert result["metrics"]["block_application"]["stock_sample_count"] == 40
    assert result["metrics"]["block_application"]["patched_sample_count"] == 40

    insufficient = paired_overhead_calibration(
        _calibration_run("stock", 29, 100.0),
        _calibration_run("patched", 40, 103.0),
        minimum_samples=30,
    )
    assert insufficient["status"] == "unavailable"
    assert insufficient["performance_authority"] == "not-calibrated"
    assert insufficient["calibration_scope"] == []
    assert any("minimum sample count" in reason for reason in insufficient["reasons"])

    mismatched = paired_overhead_calibration(
        _calibration_run("stock", 40, 100.0),
        _calibration_run("patched", 40, 103.0, seed="different"),
        minimum_samples=30,
    )
    assert mismatched["status"] == "unavailable"
    assert any("workload identity" in reason for reason in mismatched["reasons"])

    wrong_runner = _calibration_run("patched", 40, 103.0)
    wrong_runner["runner"]["script_sha256"] = "sha256:" + "b" * 64
    runner_mismatch = paired_overhead_calibration(
        _calibration_run("stock", 40, 100.0), wrong_runner, minimum_samples=30
    )
    assert runner_mismatch["status"] == "unavailable"
    assert any("runner identity" in reason for reason in runner_mismatch["reasons"])

    wrong_hardware = _calibration_run("patched", 40, 103.0)
    wrong_hardware["hardware"]["logical_cpu_count"] = 16
    hardware_mismatch = paired_overhead_calibration(
        _calibration_run("stock", 40, 100.0), wrong_hardware, minimum_samples=30
    )
    assert hardware_mismatch["status"] == "unavailable"
    assert any("hardware identity" in reason for reason in hardware_mismatch["reasons"])

    wrong_outcomes = _calibration_run("patched", 40, 103.0)
    wrong_outcomes["attempts"]["outcomes"] = {"timeout": 40}
    outcome_mismatch = paired_overhead_calibration(
        _calibration_run("stock", 40, 100.0), wrong_outcomes, minimum_samples=30
    )
    assert outcome_mismatch["status"] == "unavailable"
    assert any("terminal outcome counts" in reason for reason in outcome_mismatch["reasons"])


def test_patched_factories_bind_runtime_owned_paths_and_identity():
    factories = build_amaru_patched_factories(
        json_trace_paths=[FIXTURE], target_identity=_identity(), include_existing=True
    )
    assert set(factories) == {
        "amaru-patched-protocol-decode",
        "amaru-patched-blockfetch-queues",
        "amaru-patched-txsubmission-residence",
    }
    collector = factories["amaru-patched-protocol-decode"](
        {
            "id": "amaru-patched-protocol-decode",
            "parameters": {"json_trace_paths": ["/etc/shadow"]},
        }
    )
    assert collector.json_paths == [FIXTURE]
    assert collector.target_identity["patch_set_sha256"] == AMARU_MEASUREMENT_PATCH_SHA256
