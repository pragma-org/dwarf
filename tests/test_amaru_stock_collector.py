import json
from pathlib import Path

from profile_manager.measurement_collectors.amaru_stock import (
    AmaruStockCollector,
    build_amaru_stock_factories,
    load_amaru_telemetry,
)
from profile_manager.measurement_runtime import CollectorContext
from scripts import runtime_amaru_measurement_collector


FIXTURES = Path(__file__).parent / "fixtures"


def test_exact_b159_json_and_otlp_fixtures_normalize_all_audited_signal_families():
    result = load_amaru_telemetry(
        json_trace_paths=[FIXTURES / "amaru-b159-stock-traces.ndjson"],
        otlp_trace_paths=[FIXTURES / "amaru-b159-otlp-traces.json"],
    )

    assert result["source_revision"] == "b159172f25a9c389f82f20bca4f15e3032791638"
    assert result["source_record_count"] == 17
    assert result["normalized_record_count"] == 16
    assert result["rejected_record_count"] == 1
    assert result["rejection_reasons"] == {"invalid-json": 1}
    kinds = {event["kind"] for event in result["events"]}
    assert kinds == {
        "header-lifecycle",
        "fork-switch",
        "mempool-received",
        "mempool-accepted",
        "mempool-rejected",
        "mempool-state",
        "ledger-phase-one",
        "plutus-execution",
        "keepalive-round-trip",
        "peer-connected",
        "mux-failed",
        "chain-tip",
        "block-apply",
        "epoch-transition",
        "transaction-validation",
    }
    block = next(event for event in result["events"] if event["kind"] == "block-apply")
    assert block["duration_micros"] == 4_300_000.0
    assert block["trace_id"] == "trace-block"
    assert block["span_id"] == "span-block"


def test_header_report_uses_exact_upstream_intervals_without_invented_adopt_stage(tmp_path):
    result = _collect(tmp_path, "amaru-stock-header-lifecycle")

    assert result["measurements"]["header_slot_start_to_reception"]["mean"] == 100.0
    assert result["measurements"]["header_reception_to_fetch_request"]["mean"] == 20.0
    assert result["measurements"]["block_fetch"]["mean"] == 30.0
    assert result["measurements"]["header_reception_to_terminal_forward"]["mean"] == 80.0
    assert "adopt" not in result["measurements"]
    assert result["claims"]["exclusive_adopt_duration"] == "unavailable"


def test_stock_timing_keeps_rejected_outcomes_in_combined_and_per_outcome_distributions(
    tmp_path,
):
    trace = tmp_path / "outcomes.ndjson"
    records = [
        {
            "timestamp": "2026-09-18T12:00:00.000Z",
            "fields": {
                "outcome": "valid",
                "slot_start_to_header_micros": 100,
                "block_fetch_wait_micros": 20,
                "block_fetch_micros": 30,
                "forward_micros": 80,
            },
            "target": "amaru::consensus::perf::header",
            "span": {"name": "lifecycle"},
        },
        {
            "timestamp": "2026-09-18T12:00:00.100Z",
            "fields": {
                "outcome": "invalid",
                "slot_start_to_header_micros": 300,
                "block_fetch_wait_micros": 40,
                "block_fetch_micros": 50,
                "forward_micros": 120,
            },
            "target": "amaru::consensus::perf::header",
            "span": {"name": "lifecycle"},
        },
        {
            "timestamp": "2026-09-18T12:00:00.200Z",
            "fields": {"outcome": "applied", "duration_micros": 500},
            "target": "amaru::consensus::perf::fork",
            "span": {"name": "switch"},
        },
        {
            "timestamp": "2026-09-18T12:00:00.300Z",
            "fields": {"outcome": "abandoned", "duration_micros": 900},
            "target": "amaru::consensus::perf::fork",
            "span": {"name": "switch"},
        },
    ]
    trace.write_text("".join(json.dumps(record) + "\n" for record in records))

    header = _collect_from_paths(
        tmp_path / "header",
        "amaru-stock-header-lifecycle",
        json_paths=[trace],
        otlp_paths=[],
    )
    fork = _collect_from_paths(
        tmp_path / "fork",
        "amaru-stock-fork-switch",
        json_paths=[trace],
        otlp_paths=[],
    )

    header_outcomes = header["measurements"][
        "header_slot_start_to_reception_by_outcome"
    ]
    assert header["measurements"]["header_slot_start_to_reception"]["mean"] == 200.0
    assert header_outcomes["valid"]["mean"] == 100.0
    assert header_outcomes["invalid"]["mean"] == 300.0
    assert sum(row["sample_count"] for row in header_outcomes.values()) == 2
    fork_outcomes = fork["measurements"]["fork_switch_by_outcome"]
    assert fork["measurements"]["fork_switch"]["mean"] == 700.0
    assert fork_outcomes["applied"]["mean"] == 500.0
    assert fork_outcomes["abandoned"]["mean"] == 900.0


def test_stock_collectors_produce_client_scoreboard_rows_and_security_signals(tmp_path):
    block = _collect(tmp_path / "block", "amaru-stock-block-epoch")
    ledger = _collect(tmp_path / "ledger", "amaru-stock-ledger-rules")
    plutus = _collect(tmp_path / "plutus", "amaru-stock-plutus-execution")
    mempool = _collect(tmp_path / "mempool", "amaru-stock-mempool")
    network = _collect(tmp_path / "network", "amaru-stock-network")

    assert block["measurements"]["block_application"]["mean"] == 4_300_000.0
    assert block["measurements"]["epoch_transition"]["mean"] == 3_500_000.0
    assert ledger["measurements"]["transfer_validation"]["mean"] == 233.0
    assert ledger["measurements"]["ledger_rule_fees"]["mean"] == 11.0
    assert plutus["measurements"]["virtual_machine_evaluate"]["mean"] == 205.0
    assert mempool["measurements"]["mempool_received"]["value"] == 2
    assert mempool["measurements"]["mempool_accepted"]["value"] == 1
    assert mempool["measurements"]["mempool_rejected"]["by_reason"] == {"invalid": 1}
    terminal_latency = mempool["measurements"]["mempool_terminal_latency"]
    assert terminal_latency["all"]["sample_count"] == 2
    assert terminal_latency["by_outcome"]["accepted"]["mean"] == 100_000.0
    assert terminal_latency["by_outcome"]["rejected"]["mean"] == 50_000.0
    assert terminal_latency["samples"] == [
        {
            "elapsed_micros": 100_000.0,
            "outcome": "accepted",
            "transaction_id": "c" * 64,
        },
        {
            "elapsed_micros": 50_000.0,
            "outcome": "rejected",
            "transaction_id": "d" * 64,
        },
    ]
    assert mempool["measurements"]["mempool_tx_count"]["maximum"] == 7.0
    assert network["measurements"]["keepalive_round_trip"]["mean"] == 40.0
    assert network["measurements"]["peer_connected"]["value"] == 1
    assert network["measurements"]["mux_failed"]["value"] == 1


def test_missing_completed_stock_spans_are_unavailable_not_zero(tmp_path):
    result = _collect(
        tmp_path,
        "amaru-stock-block-epoch",
        otlp_paths=[],
    )

    assert result["measurements"]["block_application"]["status"] == "unavailable"
    assert result["measurements"]["block_application"]["mean"] is None
    assert "completed stock telemetry span" in result["measurements"]["block_application"]["reason"]


def test_exact_runtime_json_shape_normalizes_events_and_completed_node_spans(tmp_path):
    """Exercise the format emitted by the pinned binary, not only logical-schema fixtures."""
    trace = FIXTURES / "amaru-b159-runtime-json-traces.ndjson"
    loaded = load_amaru_telemetry(json_trace_paths=[trace])

    assert loaded["source_record_count"] == 8
    assert loaded["normalized_record_count"] == 5
    assert loaded["ignored_record_count"] == 1
    assert loaded["ignored_reasons"] == {"unsupported-json-event": 1}
    assert loaded["rejected_record_count"] == 0
    assert loaded["rejection_reasons"] == {}
    assert {event["kind"] for event in loaded["events"]} == {
        "block-prepare",
        "block-apply",
        "chain-tip",
        "header-lifecycle",
        "mux-failed",
    }
    block_apply = next(
        event for event in loaded["events"] if event["kind"] == "block-apply"
    )
    assert block_apply["duration_micros"] == 112.0
    assert block_apply["timing_source"] == "paired-node-json-span-events"
    assert block_apply["fields"]["point_slot"] == 2410

    result = _collect_from_paths(
        tmp_path,
        "amaru-stock-block-epoch",
        json_paths=[trace],
        otlp_paths=[],
    )
    assert result["measurements"]["block_prepare"]["mean"] == 5.0
    assert result["measurements"]["block_application"]["mean"] == 112.0
    assert result["measurements"]["block_application"]["timing_sources"] == [
        "paired-node-json-span-events"
    ]
    assert result["export"]["incomplete"] is False
    assert result["export"]["ignored_record_count"] == 1


def test_block_application_distribution_prefers_monotonic_nanoseconds(tmp_path):
    trace = tmp_path / "amaru.ndjson"
    rows = [
        {
            "timestamp": "2026-09-21T03:00:00.000001Z",
            "target": "amaru::ledger",
            "fields": {"message": "enter", "point_slot": 501},
            "span": {"name": "block.apply"},
            "id": 9,
        },
        {
            "timestamp": "2026-09-21T03:00:00.000004Z",
            "target": "amaru::ledger",
            "fields": {"message": "exit", "point_slot": 501},
            "span": {"name": "block.apply"},
            "id": 9,
        },
        {
            "timestamp": "2026-09-21T03:00:00.000005Z",
            "target": "amaru::ledger",
            "fields": {
                "message": "measurement.block_apply",
                "point_slot": 501,
                "outcome": "completed",
                "elapsed_micros": 2,
                "elapsed_nanos": 2184,
            },
        },
    ]
    trace.write_text("\n".join(json.dumps(row) for row in rows) + "\n")

    loaded = load_amaru_telemetry(json_trace_paths=[trace])
    precise = next(
        event
        for event in loaded["events"]
        if event.get("timing_source") == "patched-monotonic-nanoseconds"
    )
    assert precise["elapsed_nanos"] == 2184
    assert precise["duration_micros"] == 2.184

    result = _collect_from_paths(
        tmp_path,
        "amaru-stock-block-epoch",
        json_paths=[trace],
        otlp_paths=[],
    )
    distribution = result["measurements"]["block_application"]
    assert distribution["sample_count"] == 1
    assert distribution["minimum"] == 2.184
    assert distribution["maximum"] == 2.184
    assert distribution["timing_sources"] == ["patched-monotonic-nanoseconds"]


def test_collector_retains_bounded_raw_normalized_correlation_and_result_artifacts(tmp_path):
    result = _collect(tmp_path, "amaru-stock-mempool", max_source_bytes=1024)

    assert result["export"]["incomplete"] is True
    assert result["export"]["truncated_sources"]
    collector_dir = tmp_path / "measurements/collectors/amaru-stock-mempool"
    assert (collector_dir / "raw/amaru-json.ndjson").is_file()
    assert (collector_dir / "normalized.ndjson").is_file()
    assert (collector_dir / "correlations.json").is_file()
    assert (collector_dir / "result.json").is_file()
    retained = json.loads((collector_dir / "result.json").read_text())
    assert retained == result


def test_stock_collector_reports_the_bound_target_source_revision(tmp_path):
    revision = "aedfe797a5b8ef00d8b362be40b47a52c3b4a379"
    factories = build_amaru_stock_factories(
        json_trace_paths=[FIXTURES / "amaru-b159-stock-traces.ndjson"],
        otlp_trace_paths=[FIXTURES / "amaru-b159-otlp-traces.json"],
        include_existing=True,
        source_revision=revision,
    )
    collector = factories["amaru-stock-mempool"]({"id": "amaru-stock-mempool"})
    context = CollectorContext(
        measurement_id="amaru-stock-mempool",
        definition={"id": "amaru-stock-mempool"},
        parameters={},
        run_dir=tmp_path,
        collector_dir=tmp_path / "measurements/collectors/amaru-stock-mempool",
    )

    collector.prepare(context)
    collector.start(context)
    result = collector.finalize(context)

    assert result["source_revision"] == revision


def test_factory_registry_uses_runtime_owned_paths_not_scenario_parameters(tmp_path):
    factories = build_amaru_stock_factories(
        json_trace_paths=[FIXTURES / "amaru-b159-stock-traces.ndjson"],
        otlp_trace_paths=[FIXTURES / "amaru-b159-otlp-traces.json"],
        include_existing=True,
    )

    assert set(factories) == {
        "amaru-stock-header-lifecycle",
        "amaru-stock-fork-switch",
        "amaru-stock-mempool",
        "amaru-stock-ledger-rules",
        "amaru-stock-plutus-execution",
        "amaru-stock-block-epoch",
        "amaru-stock-network",
    }
    collector = factories["amaru-stock-mempool"](
        {
            "id": "amaru-stock-mempool",
            "parameters": {"json_trace_paths": ["/etc/shadow"]},
            "definition": {"id": "amaru-stock-mempool"},
        }
    )
    assert collector.json_paths == [FIXTURES / "amaru-b159-stock-traces.ndjson"]


def test_one_shot_cli_writes_a_real_collector_result(tmp_path):
    exit_code = runtime_amaru_measurement_collector.main(
        [
            "--measurement-id",
            "amaru-stock-block-epoch",
            "--json-trace",
            str(FIXTURES / "amaru-b159-stock-traces.ndjson"),
            "--otlp-trace",
            str(FIXTURES / "amaru-b159-otlp-traces.json"),
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    result = json.loads(
        (
            tmp_path
            / "measurements/collectors/amaru-stock-block-epoch/result.json"
        ).read_text()
    )
    assert result["measurements"]["block_application"]["mean"] == 4_300_000.0


def test_additive_collector_config_pins_source_and_does_not_add_a_report_store():
    config = json.loads(
        Path("dwarf/assets/measurements/amaru-stock-b159172.json").read_text()
    )

    assert config["source_revision"] == "b159172f25a9c389f82f20bca4f15e3032791638"
    assert config["storage"] == "dwarf-run-bundle"
    assert config["general_monitoring_store"] is False
    assert config["inputs"] == ["amaru-json-ndjson", "otlp-json-export"]
    assert config["claim_boundaries"]["outcome_timing"] == (
        "retain every timed outcome; report combined and per-outcome distributions"
    )


def _collect(
    tmp_path,
    measurement_id,
    *,
    otlp_paths=None,
    max_source_bytes=1_000_000,
):
    return _collect_from_paths(
        tmp_path,
        measurement_id,
        json_paths=[FIXTURES / "amaru-b159-stock-traces.ndjson"],
        otlp_paths=(
            [FIXTURES / "amaru-b159-otlp-traces.json"]
            if otlp_paths is None
            else otlp_paths
        ),
        max_source_bytes=max_source_bytes,
    )


def _collect_from_paths(
    tmp_path,
    measurement_id,
    *,
    json_paths,
    otlp_paths,
    max_source_bytes=1_000_000,
):
    entry = {
        "id": measurement_id,
        "parameters": {},
        "definition": {"id": measurement_id},
    }
    context = CollectorContext(
        measurement_id=measurement_id,
        definition=entry["definition"],
        parameters=entry["parameters"],
        run_dir=tmp_path,
        collector_dir=tmp_path / "measurements/collectors" / measurement_id,
    )
    collector = AmaruStockCollector(
        entry,
        json_trace_paths=json_paths,
        otlp_trace_paths=otlp_paths,
        include_existing=True,
        max_source_bytes=max_source_bytes,
    )
    collector.prepare(context)
    collector.start(context)
    collector.stop(context)
    return collector.finalize(context)
