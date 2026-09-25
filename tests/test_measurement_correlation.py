from profile_manager.measurement_correlation import correlate_measurement_events


def test_correlates_transaction_header_block_peer_trace_and_span_ids():
    events = [
        {
            "kind": "tx-submitted",
            "elapsed_seconds": 1.0,
            "tx_id": "tx-1",
            "trace_id": "trace-1",
            "span_id": "span-submit",
        },
        {
            "kind": "tx-included",
            "elapsed_seconds": 3.0,
            "tx_hash": "tx-1",
            "block_hash": "block-1",
        },
        {
            "kind": "header-adopted",
            "elapsed_seconds": 2.0,
            "header_hash": "header-1",
            "block_hash": "block-1",
            "peer_id": "peer-a",
            "trace_id": "trace-1",
            "parent_span_id": "span-submit",
        },
    ]

    result = correlate_measurement_events(events)

    assert result["indexes"]["transactions"]["tx-1"] == [0, 1]
    assert result["indexes"]["blocks"]["block-1"] == [1, 2]
    assert result["indexes"]["headers"]["header-1"] == [2]
    assert result["indexes"]["peers"]["peer-a"] == [2]
    assert result["indexes"]["traces"]["trace-1"] == [0, 2]
    assert result["indexes"]["spans"]["span-submit"] == [0, 2]
    assert result["uncorrelated"] == []


def test_annotates_events_with_scenario_window_and_active_faults():
    events = [
        {"kind": "before", "elapsed_seconds": 1.0, "tx_id": "a"},
        {"kind": "during", "elapsed_seconds": 6.0, "tx_id": "b"},
        {"kind": "after", "elapsed_seconds": 11.0, "tx_id": "c"},
    ]
    windows = [
        {"phase_id": "baseline", "start": 0.0, "end": 4.0},
        {"phase_id": "hostile", "start": 4.0, "end": 9.0},
        {"phase_id": "recovery", "start": 9.0, "end": 15.0},
    ]
    faults = [
        {"fault_id": "partition", "start": 5.0, "end": 8.0},
        {"fault_id": "peer-churn", "start": 5.5, "end": None},
    ]

    result = correlate_measurement_events(events, windows=windows, faults=faults)

    assert result["events"][0]["window"] == "baseline"
    assert result["events"][0]["active_faults"] == []
    assert result["events"][1]["window"] == "hostile"
    assert result["events"][1]["active_faults"] == ["partition", "peer-churn"]
    assert result["events"][2]["window"] == "recovery"
    assert result["events"][2]["active_faults"] == ["peer-churn"]


def test_missing_identifiers_are_retained_as_uncorrelated_not_discarded():
    event = {"kind": "decoder-warning", "elapsed_seconds": 2.5}

    result = correlate_measurement_events([event])

    assert result["events"][0]["event_index"] == 0
    assert result["uncorrelated"] == [
        {
            "event_index": 0,
            "kind": "decoder-warning",
            "reason": "no supported correlation identifier",
        }
    ]

