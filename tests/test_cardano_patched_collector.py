from pathlib import Path

import pytest

from profile_manager.measurement_collectors.cardano_patched import (
    CARDANO_MEASUREMENT_PATCH_SHA256,
    CARDANO_SOURCE_REVISION,
    CardanoPatchedCollector,
    build_cardano_patched_factories,
    load_cardano_patched_telemetry,
)
from profile_manager.measurement_runtime import CollectorContext


def _identity(**overrides):
    value = {
        "implementation": "cardano-node",
        "version": "11.1.2",
        "source_revision": CARDANO_SOURCE_REVISION,
        "mode": "patched",
        "image_digest": "sha256:" + "a" * 64,
        "patch_set_sha256": CARDANO_MEASUREMENT_PATCH_SHA256,
    }
    value.update(overrides)
    return value


def _context(tmp_path, measurement_id="cardano-patched-protocol-decode"):
    run_dir = tmp_path / "run"
    return CollectorContext(
        measurement_id=measurement_id,
        definition={"id": measurement_id},
        parameters={},
        run_dir=run_dir,
        collector_dir=run_dir / "measurements" / "collectors" / measurement_id,
    )


def test_loader_keeps_every_protocol_terminal_outcome(tmp_path):
    trace = tmp_path / "cardano-measurement.ndjson"
    trace.write_text(
        '\n'.join([
            '{"schema_version":"v1","target":"cardano-node::measurement",'
            '"event":"protocol_receive_decode","boundary":"receive-plus-incremental-decode",'
            '"protocol":"ChainSync","state":"TokIdle","outcome":"accepted",'
            '"ended_monotonic_ns":10000,"duration_us":7}',
            '{"schema_version":"v1","target":"cardano-node::measurement",'
            '"event":"protocol_receive_decode","boundary":"receive-plus-incremental-decode",'
            '"protocol":"ChainSync","state":"TokNext","outcome":"decode_failure",'
            '"ended_monotonic_ns":20000,"duration_us":11}',
            '{"target":"unrelated"}',
            'not-json',
        ]) + '\n',
        encoding="utf-8",
    )

    result = load_cardano_patched_telemetry([trace])

    assert result["source_record_count"] == 4
    assert result["normalized_record_count"] == 2
    assert result["ignored_record_count"] == 1
    assert result["rejected_record_count"] == 1
    assert result["rejection_reasons"] == {"invalid-json": 1}
    assert {row["outcome"] for row in result["events"]} == {
        "accepted", "decode_failure"
    }


def test_ledger_stage_collector_reports_block_epoch_and_plutus_outcomes(tmp_path):
    trace = tmp_path / "ledger.ndjson"
    trace.write_text(
        '\n'.join(
            '{"schema_version":"v1","target":"cardano-node::measurement",'
            f'"event":"ledger_stage","stage":"{stage}","outcome":"{outcome}",'
            f'"ended_monotonic_ns":{index * 10000},"duration_us":{duration}}}'
            for index, (stage, outcome, duration) in enumerate([
                ("block-application", "accepted", 80),
                ("block-application", "rejected", 40),
                ("epoch-transition", "completed", 3500),
                ("plutus-vm", "accepted", 175),
                ("plutus-vm", "rejected", 210),
            ], start=1)
        ) + '\n', encoding="utf-8"
    )
    measurement_id = "cardano-patched-ledger-plutus-stages"
    collector = CardanoPatchedCollector(
        {"id": measurement_id}, json_trace_paths=[trace],
        target_identity=_identity(), include_existing=True,
    )
    context = _context(tmp_path, measurement_id)
    collector.prepare(context)
    collector.start(context)
    result = collector.finalize(context)["measurements"]

    assert result["block_application"]["sample_count"] == 2
    assert result["block_application_by_outcome"]["rejected"]["mean"] == 40.0
    assert result["epoch_transition"]["mean"] == 3500.0
    assert result["plutus_vm"]["sample_count"] == 2
    assert result["plutus_vm_by_outcome"]["accepted"]["mean"] == 175.0


def test_collector_retains_raw_normalized_and_per_outcome_distributions(tmp_path):
    trace = tmp_path / "cardano-measurement.ndjson"
    trace.write_text(
        '\n'.join(
            '{"schema_version":"v1","target":"cardano-node::measurement",'
            '"event":"protocol_receive_decode","boundary":"receive-plus-incremental-decode",'
            f'"protocol":"BlockFetch","state":"TokBusy","outcome":"{outcome}",'
            f'"ended_monotonic_ns":{index * 10000},"duration_us":{duration}}}'
            for index, (outcome, duration) in enumerate(
                [("accepted", 5), ("accepted", 9), ("timeout", 17)], start=1
            )
        ) + '\n',
        encoding="utf-8",
    )
    collector = CardanoPatchedCollector(
        {"id": "cardano-patched-protocol-decode"},
        json_trace_paths=[trace],
        target_identity=_identity(),
        include_existing=True,
    )
    context = _context(tmp_path)
    collector.prepare(context)
    collector.start(context)
    result = collector.finalize(context)

    metric = result["measurements"]["protocol_receive_decode"]
    assert metric["sample_count"] == 3
    assert metric["median"] == 9.0
    assert result["measurements"]["protocol_receive_decode_by_outcome"]["accepted"]["mean"] == 7.0
    assert result["measurements"]["protocol_receive_decode_by_outcome"]["timeout"]["mean"] == 17.0
    assert result["performance_authority"] == "requires-paired-calibration"
    assert (context.collector_dir / "raw/cardano-measurement.ndjson").is_file()
    assert (context.collector_dir / "normalized.ndjson").is_file()


def test_collector_fails_closed_on_target_identity(tmp_path):
    trace = tmp_path / "trace.ndjson"
    trace.write_text("")
    collector = CardanoPatchedCollector(
        {"id": "cardano-patched-protocol-decode"},
        json_trace_paths=[trace],
        target_identity=_identity(patch_set_sha256="0" * 64),
    )
    with pytest.raises(ValueError, match="patch-set identity"):
        collector.prepare(_context(tmp_path))


def test_factories_ignore_entry_supplied_paths(tmp_path):
    trace = tmp_path / "trace.ndjson"
    trace.write_text("")
    factories = build_cardano_patched_factories(
        json_trace_paths=[trace], target_identity=_identity()
    )
    collector = factories["cardano-patched-protocol-decode"](
        {"id": "cardano-patched-protocol-decode", "parameters": {"json_trace_paths": ["/etc/shadow"]}}
    )
    assert collector.json_paths == [trace]
    assert set(factories) == {
        "cardano-patched-protocol-decode",
        "cardano-patched-ledger-plutus-stages",
    }
