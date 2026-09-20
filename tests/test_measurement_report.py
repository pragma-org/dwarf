import json

from profile_manager.measurement_report import (
    backlog_summary,
    degradation_summary,
    distribution_summary,
    rate_summary,
    recovery_summary,
    write_measurement_reports,
)


def test_distribution_uses_fixed_nearest_rank_percentiles_and_units():
    samples = [
        {"value": value, "unit": "us", "elapsed_seconds": float(value)}
        for value in range(1, 21)
    ]

    result = distribution_summary(samples, minimum_samples=5)

    assert result == {
        "status": "available",
        "unit": "us",
        "sample_count": 20,
        "excluded_warmup_count": 0,
        "rejected_count": 0,
        "rejection_reasons": {},
        "mean": 10.5,
        "median": 10.5,
        "p95": 19.0,
        "p99": 20.0,
        "minimum": 1.0,
        "maximum": 20.0,
    }


def test_distribution_excludes_warmup_and_retains_rejection_reasons():
    samples = [
        {"value": 100, "unit": "ms", "elapsed_seconds": 1},
        {"value": 10, "unit": "ms", "elapsed_seconds": 6},
        {
            "value": 50,
            "unit": "ms",
            "elapsed_seconds": 7,
            "accepted": False,
            "rejection_reason": "incomplete-span",
        },
        {
            "value": None,
            "unit": "ms",
            "elapsed_seconds": 8,
            "accepted": False,
            "rejection_reason": "missing-end",
        },
        {"value": 20, "unit": "ms", "elapsed_seconds": 9},
    ]

    result = distribution_summary(
        samples, warmup_seconds=5, minimum_samples=3
    )

    assert result["status"] == "insufficient-samples"
    assert result["sample_count"] == 2
    assert result["excluded_warmup_count"] == 1
    assert result["rejected_count"] == 2
    assert result["rejection_reasons"] == {
        "incomplete-span": 1,
        "missing-end": 1,
    }
    assert result["mean"] == 15.0
    assert result["p95"] is None
    assert result["p99"] is None


def test_empty_distribution_is_unavailable_not_zero():
    result = distribution_summary([], unit="blocks/s")

    assert result["status"] == "unavailable"
    assert result["unit"] == "blocks/s"
    assert result["sample_count"] == 0
    assert result["mean"] is None
    assert result["p95"] is None


def test_rate_reports_offered_accepted_rejected_and_reasons():
    events = [
        {"value": 3, "accepted": True},
        {"value": 2, "accepted": True},
        {"value": 1, "accepted": False, "rejection_reason": "busy"},
        {"value": 2, "accepted": False, "rejection_reason": "invalid"},
    ]

    result = rate_summary(events, duration_seconds=2, unit="tx/s")

    assert result == {
        "status": "available",
        "unit": "tx/s",
        "duration_seconds": 2.0,
        "offered_count": 8.0,
        "accepted_count": 5.0,
        "rejected_count": 3.0,
        "offered_rate": 4.0,
        "accepted_rate": 2.5,
        "rejected_rate": 1.5,
        "rejection_reasons": {"busy": 1.0, "invalid": 2.0},
    }


def test_backlog_reports_peak_growth_and_drain_time():
    result = backlog_summary(
        [
            {"elapsed_seconds": 0, "value": 0, "unit": "tx"},
            {"elapsed_seconds": 1, "value": 10, "unit": "tx"},
            {"elapsed_seconds": 2, "value": 20, "unit": "tx"},
            {"elapsed_seconds": 3, "value": 10, "unit": "tx"},
            {"elapsed_seconds": 4, "value": 0, "unit": "tx"},
        ]
    )

    assert result["status"] == "available"
    assert result["peak"] == 20.0
    assert result["peak_at_seconds"] == 2.0
    assert result["growth_rate_per_second"] == 10.0
    assert result["drain_time_seconds"] == 2.0


def test_recovery_and_hostile_degradation_are_explicit():
    recovery = recovery_summary(
        [
            {"elapsed_seconds": 5, "value": 20},
            {"elapsed_seconds": 6, "value": 60},
            {"elapsed_seconds": 8, "value": 92},
        ],
        fault_removed_at=5,
        baseline_value=100,
        recovery_fraction=0.9,
        unit="tx/s",
    )
    degradation = degradation_summary(
        baseline_value=100, hostile_value=70, unit="tx/s"
    )

    assert recovery["status"] == "available"
    assert recovery["recovery_time_seconds"] == 3.0
    assert recovery["recovery_threshold"] == 90.0
    assert degradation["absolute_delta"] == -30.0
    assert degradation["percent_delta"] == -30.0


def test_report_writer_emits_normalized_readable_and_compact_artifacts(tmp_path):
    report = {
        "schema_version": "v1",
        "scenario": "amaru-security-example",
        "duration_seconds": 120,
        "measurements": {
            "transfer": {
                "status": "available",
                "unit": "us",
                "sample_count": 100,
                "mean": 52.0,
                "median": 50.0,
                "p95": 70.0,
                "p99": 90.0,
            },
            "block_application": {
                "status": "unavailable",
                "unit": "us",
                "sample_count": 0,
                "reason": "trace capability was not exposed by this stock build",
            },
        },
    }

    artifacts = write_measurement_reports(tmp_path, report)

    assert artifacts == {
        "summary": "measurements/summary.json",
        "report": "measurements/report.json",
        "readable": "measurements/report.md",
        "compact_table": "measurements/compact-table.json",
    }
    aggregate = json.loads((tmp_path / artifacts["report"]).read_text())
    compact = json.loads((tmp_path / artifacts["compact_table"]).read_text())
    readable = (tmp_path / artifacts["readable"]).read_text()
    assert aggregate == report
    assert compact["rows"][0]["metric"] == "transfer"
    assert compact["rows"][1]["mean"] is None
    assert "unavailable" in readable
    assert "trace capability was not exposed" in readable


def test_compact_row_uses_all_bucket_for_outcome_partitioned_metrics(tmp_path):
    report = {
        "schema_version": "v1",
        "scenario": "amaru-measurement-e2e-stock",
        "duration_seconds": 10.7,
        "measurements": {
            "attempt_latency": {
                "all": {
                    "status": "available",
                    "unit": "us",
                    "sample_count": 100,
                    "mean": 100238.11,
                    "median": 101108.0,
                    "p95": 102037.0,
                    "p99": 102166.0,
                },
                "by_outcome": {
                    "rejected": {
                        "status": "available",
                        "unit": "us",
                        "sample_count": 100,
                        "median": 101108.0,
                    }
                },
            }
        },
    }

    artifacts = write_measurement_reports(tmp_path, report)

    compact = json.loads((tmp_path / artifacts["compact_table"]).read_text())
    row = next(r for r in compact["rows"] if r["metric"] == "attempt_latency")
    assert row["status"] == "available"
    assert row["sample_count"] == 100
    assert row["median"] == 101108.0
    assert row["unit"] == "us"

    summary = json.loads((tmp_path / artifacts["summary"]).read_text())
    assert summary["available_count"] == 1
    assert summary["unavailable_count"] == 0


def test_distribution_preserves_sub_microsecond_precision():
    result = distribution_summary(
        [
            {"value": 2.184, "unit": "us"},
            {"value": 0.999, "unit": "us"},
            {"value": 3.001, "unit": "us"},
        ],
        unit="us",
    )

    assert result["minimum"] == 0.999
    assert result["median"] == 2.184
    assert result["maximum"] == 3.001
