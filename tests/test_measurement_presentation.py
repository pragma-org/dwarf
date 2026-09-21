from profile_manager.measurement_presentation import (
    build_measurement_presentation,
    load_measurement_presentation_catalog,
)


def _target(implementation: str, mode: str) -> dict:
    return {
        "implementation": implementation,
        "version": "test-version",
        "source_revision": "a" * 40,
        "mode": mode,
        "image_digest": "sha256:" + "b" * 64,
    }


def _card(presentation: dict, concept_id: str) -> dict:
    return next(card for card in presentation["cards"] if card["concept_id"] == concept_id)


def test_catalog_defines_shared_concepts_and_four_source_classes():
    catalog = load_measurement_presentation_catalog()

    assert catalog["schema_version"] == "v1"
    assert [category["id"] for category in catalog["categories"]] == [
        "workload-outcomes",
        "latency-processing",
        "throughput-progress",
        "resources",
        "protocol-ledger",
        "unavailable",
    ]
    concepts = {concept["id"]: concept for concept in catalog["concepts"]}
    block = concepts["block-application-time"]
    assert block["title"] == "Block application time"
    assert {binding["implementation"] for binding in block["bindings"]} == {
        "amaru",
        "cardano-node",
    }
    assert catalog["source_labels"] == {
        "stock": "Stock",
        "external": "External",
        "patched": "Patched",
        "reserved": "Reserved / not implemented",
    }


def test_same_client_concept_uses_implementation_specific_source_badges():
    metric = {
        "block_application": {
            "status": "available",
            "unit": "us",
            "sample_count": 4,
            "median": 136,
            "mean": 134,
            "minimum": 119,
            "maximum": 145,
            "p95": 145,
            "p99": 145,
        }
    }

    amaru = build_measurement_presentation(metric, _target("amaru", "stock"))
    cardano = build_measurement_presentation(metric, _target("cardano-node", "patched"))
    amaru_card = _card(amaru, "block-application-time")
    cardano_card = _card(cardano, "block-application-time")

    assert amaru_card["title"] == cardano_card["title"] == "Block application time"
    assert amaru_card["source_label"] == "Stock"
    assert cardano_card["source_label"] == "Patched"
    assert amaru_card["raw_metric_ids"] == ["block_application"]
    assert cardano_card["raw_metric_ids"] == ["block_application"]


def test_distribution_leads_with_median_and_evidence_volume():
    def presented(n: int) -> dict:
        result = build_measurement_presentation(
            {
                "epoch_transition": {
                    "status": "available",
                    "unit": "us",
                    "sample_count": n,
                    "median": 168,
                    "mean": 167.5,
                    "minimum": 167,
                    "maximum": 169,
                    "p95": 169,
                    "p99": 169,
                }
            },
            _target("cardano-node", "patched"),
        )
        return _card(result, "epoch-transition-time")

    assert presented(2)["evidence_label"] == "Very small sample"
    assert presented(4)["evidence_label"] == "Very small sample"
    assert presented(29)["evidence_label"] == "Small sample"
    useful = presented(30)
    assert useful["evidence_label"] == "Useful sample"
    assert useful["metric_type"] == "distribution"
    assert useful["primary_label"] == "Median"
    assert useful["primary_value"] == 168
    assert useful["sample_count"] == 30
    assert useful["primary_support"] == "n=30 observed timings"


def test_scalar_and_count_never_show_false_zero_samples():
    result = build_measurement_presentation(
        {
            "sync_speed": {
                "status": "available",
                "unit": "blocks/s",
                "value": 0.5627,
                "duration_seconds": 10.66,
                "start_block_height": 1410,
                "end_block_height": 1416,
            },
            "ledger_samples": {
                "status": "available",
                "unit": "events",
                "value": 110,
            },
        },
        _target("cardano-node", "patched"),
    )

    sync = _card(result, "chain-sync-speed")
    ledger = _card(result, "ledger-events")
    assert sync["metric_type"] == "scalar"
    assert sync["primary_value"] == 0.5627
    assert sync["primary_display"] == "0.563"
    assert sync["sample_count"] is None
    assert "10.66" in sync["primary_support"]
    assert ledger["metric_type"] == "count"
    assert ledger["primary_label"] == "Event count"
    assert ledger["primary_value"] == 110
    assert ledger["primary_display"] == "110"
    assert ledger["sample_count"] is None


def test_scalar_uses_the_catalog_primary_statistic_instead_of_assuming_value():
    result = build_measurement_presentation(
        {
            "cpu_time_seconds": {
                "status": "available",
                "unit": "s",
                "start": 6.66,
                "end": 8.36,
                "delta": 1.7,
                "duration_seconds": 77.58,
                "rate_per_second": 0.0219,
            }
        },
        _target("cardano-node", "patched"),
    )

    cpu = _card(result, "cpu-time")
    assert cpu["primary_value"] == 1.7
    assert cpu["primary_display"] == "1.7"
    assert cpu["primary_label"] == "Change"
    assert cpu["sample_count"] is None


def test_scalar_technical_details_keep_derivation_inputs_and_outcomes():
    result = build_measurement_presentation(
        {
            "offered_operations": {
                "status": "available",
                "unit": "operations/s",
                "duration_seconds": 10.0,
                "offered_count": 100,
                "offered_rate": 10.0,
                "accepted_count": 4,
                "accepted_rate": 0.4,
                "rejected_count": 96,
                "rejected_rate": 9.6,
                "rejection_reasons": {"invalid": 96},
            }
        },
        _target("amaru", "stock"),
    )

    offered = _card(result, "offered-operation-rate")
    details = {item["key"]: item["value"] for item in offered["technical_values"]}
    assert offered["primary_value"] == 10.0
    assert offered["primary_display"] == "10"
    assert details["offered_count"] == 100
    assert details["accepted_count"] == 4
    assert details["rejected_count"] == 96
    assert details["rejection_reasons"] == '{"invalid": 96}'


def test_unavailable_and_reserved_are_distinct_and_never_invent_zeroes():
    result = build_measurement_presentation(
        {
            "epoch_transition": {
                "status": "unavailable",
                "unit": "us",
                "sample_count": 0,
                "reason": "no completed span was exported",
            }
        },
        _target("amaru", "stock"),
    )

    unavailable = _card(result, "epoch-transition-time")
    reserved = _card(result, "block-selection-time")
    assert unavailable["status"] == "unavailable"
    assert unavailable["source_label"] == "Stock"
    assert unavailable["primary_value"] is None
    assert unavailable["reason"] == "no completed span was exported"
    assert unavailable["sample_count"] is None
    assert reserved["status"] == "reserved"
    assert reserved["source_label"] == "Reserved / not implemented"
    assert reserved["primary_value"] is None
    assert "No DWARF collector" in reserved["reason"]


def test_cardano_stock_counts_and_reserved_internal_boundaries_keep_true_sources():
    result = build_measurement_presentation(
        {
            "ledger_samples": {"status": "available", "unit": "events", "value": 110},
            "protocol_events": {"status": "available", "unit": "events", "value": 108},
            "transaction_outcomes": {"status": "available", "unit": "transactions", "value": 4},
            "handler_queue_residence": {
                "status": "unavailable",
                "unit": "us",
                "reason": "stock Cardano-node does not emit the audited internal handler queue boundary",
            },
            "txsubmission_residence": {
                "status": "unavailable",
                "unit": "us",
                "reason": "stock Cardano-node does not emit a continuous request-to-mempool residence interval",
            },
        },
        _target("cardano-node", "patched"),
    )

    assert _card(result, "ledger-events")["source_label"] == "Stock"
    assert _card(result, "protocol-event-count")["source_label"] == "Stock"
    assert _card(result, "transaction-outcome-count")["source_label"] == "Stock"
    queue = _card(result, "handler-queue-residence")
    residence = _card(result, "txsubmission-residence")
    assert queue["status"] == residence["status"] == "reserved"
    assert queue["source_label"] == residence["source_label"] == "Reserved / not implemented"
    assert queue["raw_metric_ids"] == ["handler_queue_residence"]
    assert residence["raw_metric_ids"] == ["txsubmission_residence"]


def test_companion_outcomes_merge_into_parent_technical_details():
    result = build_measurement_presentation(
        {
            "block_application": {
                "status": "available",
                "unit": "us",
                "sample_count": 4,
                "median": 52,
            },
            "block_application_by_outcome": {
                "accepted": {
                    "status": "available",
                    "unit": "us",
                    "sample_count": 2,
                    "median": 41.5,
                },
                "rejected": {
                    "status": "available",
                    "unit": "us",
                    "sample_count": 2,
                    "median": 56.5,
                },
            },
        },
        _target("cardano-node", "patched"),
    )

    block = _card(result, "block-application-time")
    assert block["raw_metric_ids"] == [
        "block_application",
        "block_application_by_outcome",
    ]
    assert [outcome["outcome"] for outcome in block["outcomes"]] == [
        "accepted",
        "rejected",
    ]
    assert not any(
        card["concept_id"] == "block-application-time-by-outcome"
        for card in result["cards"]
    )


def test_unknown_metrics_remain_visible_with_conservative_fallback():
    result = build_measurement_presentation(
        {"future_metric": {"status": "available", "unit": "events", "value": 7}},
        _target("amaru", "stock"),
    )

    card = next(card for card in result["cards"] if card["raw_metric_ids"] == ["future_metric"])
    assert card["title"] == "Future metric"
    assert card["metric_type"] == "count"
    assert card["source_label"] == "Stock"
    assert card["description"].startswith("A retained DWARF measurement")


def test_acceptance_run_metric_inventory_has_canonical_client_metadata():
    amaru_ids = {
        "attempt_latency", "backlog", "batches_per_second", "block_application",
        "block_fetch", "block_fetch_by_outcome", "block_prepare", "chain_tip_updates",
        "cpu_percent", "cpu_time_seconds", "disk_read_bytes", "disk_write_bytes",
        "epoch_transition", "fd_count", "fork_switch", "fork_switch_by_outcome",
        "header_reception_to_fetch_request", "header_reception_to_fetch_request_by_outcome",
        "header_reception_to_terminal_forward", "header_reception_to_terminal_forward_by_outcome",
        "header_slot_start_to_reception", "header_slot_start_to_reception_by_outcome",
        "keepalive_round_trip", "mempool_accepted", "mempool_evicted", "mempool_received",
        "mempool_rejected", "mempool_size_bytes", "mempool_terminal_latency",
        "mempool_tx_count", "mux_failed", "network_rx_bytes", "network_tx_bytes",
        "offered_bytes_per_second", "offered_operations", "peer_connected",
        "rejected_operations", "rejected_operations_per_second", "restart_readiness",
        "rss_bytes", "successful_operations", "successful_operations_per_second",
        "sync_speed", "threads", "transfer_validation", "virtual_machine_acquire_arena",
        "virtual_machine_build_program", "virtual_machine_decode_script",
        "virtual_machine_evaluate",
    }
    cardano_ids = {
        "adopted_blocks", "attempt_latency", "backlog", "batches_per_second",
        "block_application", "block_application_by_outcome", "blockfetch_bytes",
        "blockfetch_duration", "cardano-stock-ledger-block-epoch.adopted_blocks",
        "chain_events", "cpu_percent", "cpu_time_seconds", "disk_read_bytes",
        "disk_write_bytes", "epoch_transition", "epoch_transition_by_outcome",
        "epoch_transition_duration", "fd_count", "handler_queue_residence",
        "keepalive_rtt", "ledger_samples", "mempool_accepted", "mempool_bytes",
        "mempool_rejected", "mempool_sync_duration", "mempool_tx_count",
        "network_rx_bytes", "network_tx_bytes", "offered_bytes_per_second",
        "offered_operations", "plutus_vm", "plutus_vm_by_outcome",
        "plutus_vm_duration", "protocol_events", "protocol_receive_decode",
        "protocol_receive_decode_by_outcome", "rejected_operations",
        "rejected_operations_per_second", "restart_readiness", "rollbacks", "rss_bytes",
        "served_blocks", "successful_operations", "successful_operations_per_second",
        "sync_speed", "threads", "transaction_outcomes", "txsubmission_residence",
    }

    for implementation, mode, metric_ids in (
        ("amaru", "stock", amaru_ids),
        ("cardano-node", "patched", cardano_ids),
    ):
        metrics = {
            metric_id: {"status": "available", "unit": "events", "value": 1}
            for metric_id in metric_ids
        }
        result = build_measurement_presentation(metrics, _target(implementation, mode))
        retained = {
            raw_id for card in result["cards"] for raw_id in card["raw_metric_ids"]
        }
        assert retained == metric_ids
        assert not any(
            card["description"].startswith("A retained DWARF measurement")
            for card in result["cards"]
        )


def test_fractional_microseconds_are_not_rounded_to_whole_microseconds():
    result = build_measurement_presentation(
        {
            "epoch_transition": {
                "status": "available",
                "unit": "us",
                "sample_count": 1,
                "mean": 2.184,
                "median": 2.184,
                "minimum": 2.184,
                "maximum": 2.184,
                "p95": 2.184,
                "p99": 2.184,
            }
        },
        _target("cardano-node", "patched"),
    )

    card = _card(result, "epoch-transition-time")
    assert card["primary_value"] == 2.184
    assert card["primary_display"] == "2.184"
