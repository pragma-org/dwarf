import json
from pathlib import Path

from profile_manager.measurement_collectors.amaru_external import (
    RestartReadinessCollector,
    SyncSpeedCollector,
    WorkloadAccountingCollector,
)
from profile_manager.measurement_runtime import CollectorContext


FIXTURES = Path(__file__).parent / "fixtures"


def _context(tmp_path, measurement_id):
    return CollectorContext(
        measurement_id=measurement_id,
        definition={"id": measurement_id},
        parameters={},
        run_dir=tmp_path,
        collector_dir=tmp_path / "measurements/collectors" / measurement_id,
    )


def _entry(measurement_id):
    return {"id": measurement_id, "definition": {"id": measurement_id}, "parameters": {}}


def test_workload_accounting_reports_offered_success_bytes_batches_and_backlog(tmp_path):
    (tmp_path / "log.ndjson").write_text(
        "\n".join(
            json.dumps(event)
            for event in [
                {
                    "ts": "2026-09-18T12:00:00.000Z",
                    "phase": "phase:hostile:load",
                    "event": "workload_accounting",
                    "payload": {
                        "attempted": 10,
                        "successful": 8,
                        "rejected": 2,
                        "bytes": 1000,
                        "batches": 2,
                        "backlog": 5,
                    },
                },
                {
                    "ts": "2026-09-18T12:00:02.000Z",
                    "phase": "phase:hostile:load",
                    "event": "workload_accounting",
                    "payload": {
                        "attempted": 5,
                        "successful": 4,
                        "rejected": 1,
                        "bytes": 500,
                        "batches": 1,
                        "backlog": 0,
                    },
                },
            ]
        )
        + "\n"
    )
    collector = WorkloadAccountingCollector(_entry("amaru-external-workload-accounting"))
    context = _context(tmp_path, "amaru-external-workload-accounting")
    collector.prepare(context)
    collector.start(context)
    collector.on_marker({"phase_id": "run", "state": "start", "elapsed_seconds": 0}, context)
    collector.on_marker({"phase_id": "run", "state": "end", "elapsed_seconds": 2}, context)
    collector.stop(context)
    result = collector.finalize(context)

    measurements = result["measurements"]
    assert measurements["offered_operations"]["offered_rate"] == 7.5
    assert measurements["successful_operations"]["value"] == 12
    assert measurements["successful_operations_per_second"]["value"] == 6.0
    assert measurements["rejected_operations_per_second"]["value"] == 1.5
    assert measurements["offered_bytes_per_second"]["value"] == 750.0
    assert measurements["batches_per_second"]["value"] == 1.5
    assert measurements["backlog"]["peak"] == 5.0
    assert measurements["backlog"]["drain_time_seconds"] == 2.0


def test_iteration_fallback_is_labeled_generic_not_transaction_acceptance(tmp_path):
    (tmp_path / "log.ndjson").write_text(
        json.dumps(
            {
                "ts": "2026-09-18T12:00:00.000Z",
                "phase": "load",
                "event": "iteration",
                "payload": {"i": 0, "status": "clean_error", "input_bytes": 20},
            }
        )
        + "\n"
    )
    collector = WorkloadAccountingCollector(_entry("amaru-external-workload-accounting"))
    context = _context(tmp_path, "amaru-external-workload-accounting")
    collector.prepare(context)
    collector.start(context)
    collector.stop(context)
    result = collector.finalize(context)

    assert result["measurements"]["offered_operations"]["offered_count"] == 1.0
    assert result["claims"]["transaction_submission_acceptance"] == "unavailable"
    assert result["claims"]["successful_operations"] == "workload completed or cleanly handled"


def test_workload_offered_rate_uses_attempted_not_only_classified_outcomes(tmp_path):
    (tmp_path / "log.ndjson").write_text(
        json.dumps(
            {
                "ts": "2026-09-18T12:00:00.000Z",
                "phase": "phase:hostile:load",
                "event": "workload_accounting",
                "payload": {
                    "attempted": 10,
                    "successful": 6,
                    "rejected": 2,
                    "bytes": 1000,
                    "batches": 1,
                    "backlog": 2,
                },
            }
        )
        + "\n"
    )
    collector = WorkloadAccountingCollector(_entry("amaru-external-workload-accounting"))
    context = _context(tmp_path, "amaru-external-workload-accounting")
    collector.prepare(context)
    collector.start(context)
    collector.on_marker({"phase_id": "run", "state": "start", "elapsed_seconds": 0}, context)
    collector.on_marker({"phase_id": "run", "state": "end", "elapsed_seconds": 2}, context)
    collector.stop(context)
    result = collector.finalize(context)

    offered = result["measurements"]["offered_operations"]
    assert offered["offered_count"] == 10.0
    assert offered["offered_rate"] == 5.0
    assert offered["accepted_count"] == 6.0
    assert offered["rejected_count"] == 2.0
    assert offered["unclassified_count"] == 2.0
    assert offered["unclassified_rate"] == 1.0


def test_workload_attempt_latency_includes_accepted_rejected_and_timeout(tmp_path):
    (tmp_path / "log.ndjson").write_text(
        json.dumps(
            {
                "ts": "2026-09-18T12:00:00.000Z",
                "phase": "phase:hostile:load",
                "event": "workload_accounting",
                "payload": {
                    "attempted": 3,
                    "successful": 1,
                    "rejected": 1,
                    "bytes": 300,
                    "batches": 1,
                    "backlog": 0,
                    "attempts": [
                        {"input_id": "a", "outcome": "accepted", "elapsed_micros": 100},
                        {"input_id": "b", "outcome": "rejected", "elapsed_micros": 250},
                        {"input_id": "c", "outcome": "timeout", "elapsed_micros": 900},
                    ],
                },
            }
        )
        + "\n"
    )
    collector = WorkloadAccountingCollector(_entry("amaru-external-workload-accounting"))
    context = _context(tmp_path, "amaru-external-workload-accounting")
    collector.prepare(context)
    collector.start(context)
    collector.on_marker({"phase_id": "run", "state": "start", "elapsed_seconds": 0}, context)
    collector.on_marker({"phase_id": "run", "state": "end", "elapsed_seconds": 1}, context)
    collector.stop(context)
    result = collector.finalize(context)

    latency = result["measurements"]["attempt_latency"]
    assert latency["all"]["sample_count"] == 3
    assert latency["all"]["maximum"] == 900.0
    assert latency["by_outcome"]["accepted"]["mean"] == 100.0
    assert latency["by_outcome"]["rejected"]["mean"] == 250.0
    assert latency["by_outcome"]["timeout"]["mean"] == 900.0
    assert result["attempts"] == [
        {"elapsed_micros": 100.0, "input_id": "a", "outcome": "accepted"},
        {"elapsed_micros": 250.0, "input_id": "b", "outcome": "rejected"},
        {"elapsed_micros": 900.0, "input_id": "c", "outcome": "timeout"},
    ]


def test_restart_readiness_requires_listener_progress_and_peer_role(tmp_path):
    events_dir = tmp_path / "events"
    events_dir.mkdir()
    (events_dir / "target-hooks.ndjson").write_text(
        "\n".join(
            json.dumps(event)
            for event in [
                {"event": "restart_started", "elapsed_seconds": 10, "payload": {"target_node": "amaru-1"}},
                {"event": "listener_ready", "elapsed_seconds": 12, "payload": {"target_node": "amaru-1"}},
                {"event": "peer_role_ready", "elapsed_seconds": 13, "payload": {"target_node": "amaru-1", "role": "relay"}},
                {"event": "chain_progress_ready", "elapsed_seconds": 14, "payload": {"target_node": "amaru-1", "block_height": 230}},
            ]
        )
        + "\n"
    )
    collector = RestartReadinessCollector(
        _entry("amaru-external-restart-readiness"), target_node="amaru-1"
    )
    context = _context(tmp_path, "amaru-external-restart-readiness")
    collector.prepare(context)
    collector.start(context)
    collector.stop(context)
    result = collector.finalize(context)

    readiness = result["measurements"]["restart_readiness"]
    assert readiness["status"] == "available"
    assert readiness["value"] == 4.0
    assert readiness["unit"] == "s"
    assert readiness["gates"] == {
        "listener": 12.0,
        "peer_role": 13.0,
        "chain_progress": 14.0,
    }


def test_restart_readiness_missing_gate_is_unavailable(tmp_path):
    events_dir = tmp_path / "events"
    events_dir.mkdir()
    (events_dir / "target-hooks.ndjson").write_text(
        json.dumps({"event": "restart_started", "elapsed_seconds": 10, "payload": {"target_node": "amaru-1"}}) + "\n"
    )
    collector = RestartReadinessCollector(
        _entry("amaru-external-restart-readiness"), target_node="amaru-1"
    )
    context = _context(tmp_path, "amaru-external-restart-readiness")
    collector.prepare(context)
    collector.start(context)
    collector.stop(context)
    result = collector.finalize(context)

    readiness = result["measurements"]["restart_readiness"]
    assert readiness["status"] == "unavailable"
    assert readiness["value"] is None
    assert readiness["missing_gates"] == ["chain_progress", "listener", "peer_role"]


def test_sync_speed_uses_real_adopted_tip_range_and_monotonic_equivalent_time(tmp_path):
    tips = iter(
        [
            {"block_height": 200, "block_hash": "a" * 64},
            {"block_height": 230, "block_hash": "b" * 64},
        ]
    )
    collector = SyncSpeedCollector(
        _entry("amaru-external-sync-speed"),
        tip_probe=lambda: next(tips),
        monotonic_clock=iter([10.0, 12.0]).__next__,
        expected_start_height=200,
        expected_end_height=230,
        peer_policy="single-controlled-producer",
    )
    context = _context(tmp_path, "amaru-external-sync-speed")
    collector.prepare(context)
    collector.start(context)
    collector.stop(context)
    result = collector.finalize(context)

    speed = result["measurements"]["sync_speed"]
    assert speed["status"] == "available"
    assert speed["value"] == 15.0
    assert speed["unit"] == "blocks/s"
    assert speed["start_block_height"] == 200
    assert speed["end_block_height"] == 230
    assert speed["duration_seconds"] == 2.0
    assert speed["controlled_range_match"] is True
    assert speed["peer_policy"] == "single-controlled-producer"


def test_sync_speed_is_unavailable_when_controlled_range_does_not_match(tmp_path):
    tips = iter(
        [
            {"block_height": 199, "block_hash": "a" * 64},
            {"block_height": 230, "block_hash": "b" * 64},
        ]
    )
    collector = SyncSpeedCollector(
        _entry("amaru-external-sync-speed"),
        tip_probe=lambda: next(tips),
        monotonic_clock=iter([10.0, 12.0]).__next__,
        expected_start_height=200,
        expected_end_height=230,
        peer_policy="single-controlled-producer",
    )
    context = _context(tmp_path, "amaru-external-sync-speed")
    collector.prepare(context)
    collector.start(context)
    collector.stop(context)
    result = collector.finalize(context)

    speed = result["measurements"]["sync_speed"]
    assert speed["status"] == "unavailable"
    assert speed["value"] is None
    assert speed["controlled_range_match"] is False
