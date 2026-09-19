import json
import re
from pathlib import Path

from profile_manager.dashboard import render_route_html
from profile_manager.data.operate_run import _measurement_section
from profile_manager.views.operate_measurements import (
    render_operate_measurement_profiles,
    render_operate_measurements,
)


def test_measurement_catalogs_are_operator_visible():
    measurements = render_operate_measurements()
    profiles = render_operate_measurement_profiles()

    assert "Measurements" in measurements
    assert "amaru-stock-header-lifecycle" in measurements
    assert "/operate/measurements/amaru-stock-header-lifecycle" in measurements
    assert "/api/catalog/measurements/export" in measurements
    assert "/operate/measurements/new" in measurements
    assert "Measurement profiles" in profiles
    assert "amaru-security-default" in profiles
    assert "/operate/measurement-profiles/amaru-security-default" in profiles
    assert "/api/catalog/measurement-profiles/export" in profiles
    assert "/operate/measurement-profiles/new" in profiles


def test_measurement_catalog_routes_are_wired():
    assert "amaru-stock-header-lifecycle" in render_route_html(
        "/operate/measurements"
    )
    assert "amaru-security-default" in render_route_html(
        "/operate/measurement-profiles"
    )
    landing = render_route_html("/operate")
    assert 'href="/operate/measurements"' in landing
    assert "Independent measurements" in landing
    profile_card = re.search(
        r'<a class="tile" href="/operate/measurement-profiles">(.*?)</a>',
        landing,
        re.DOTALL,
    )
    assert profile_card is not None
    assert "Measurement profiles" in profile_card.group(1)
    assert '<span class="tile__metric">4</span>' in profile_card.group(1)
    assert "reusable stock and patched selections" in profile_card.group(1)

    measurement_card = re.search(
        r'<a class="tile" href="/operate/measurements">(.*?)</a>',
        landing,
        re.DOTALL,
    )
    assert measurement_card is not None
    assert "real-node taps · retained reports · honest unavailable values" in measurement_card.group(1)
    assert "reusable profiles" not in measurement_card.group(1)


def test_measurement_learn_route_explains_modes_outcomes_and_claim_boundary():
    html = render_route_html("/learn/measurements")
    lower = html.lower()

    assert "Measurements" in html
    assert "stock" in lower
    assert "coverage" in lower
    assert "patched" in lower
    assert "accepted" in lower
    assert "rejected" in lower
    assert "unavailable" in lower
    assert "do not change the security verdict by default" in html
    assert "/operate/measurements" in html
    assert "20260918T234213Z-64959688" in html
    assert "20260919T032200Z-59f94558" in html
    assert "all 12 collectors finalized" in html
    assert "Mixed-node measurement comparison has not started" in html


def test_run_measurement_section_surfaces_identity_collectors_and_metrics(tmp_path: Path):
    measurement_dir = tmp_path / "measurements"
    measurement_dir.mkdir()
    (measurement_dir / "summary.json").write_text(
        json.dumps(
            {
                "schema_version": "v1",
                "scenario": "amaru-measurement-e2e-stock",
                "duration_seconds": 4.25,
                "metric_count": 3,
                "available_count": 2,
                "unavailable_count": 1,
            }
        )
    )
    (measurement_dir / "selection.json").write_text(
        json.dumps(
            {
                "collector_states": {
                    "amaru-stock-resources": "finalized",
                    "amaru-external-sync-speed": "error",
                },
                "resolution": {
                    "profile": {"id": "amaru-security-default"},
                    "target_identity": {
                        "implementation": "amaru",
                        "version": "10.11.20260912",
                        "source_revision": "b159172",
                        "mode": "stock",
                        "image_digest": "sha256:abc",
                    },
                },
            }
        )
    )
    (measurement_dir / "runtime.json").write_text(
        json.dumps(
            {
                "collector_errors": [
                    {
                        "measurement_id": "amaru-external-sync-speed",
                        "phase": "start",
                        "type": "RuntimeError",
                        "message": "no adopted tip in bounded window",
                    }
                ]
            }
        )
    )
    (measurement_dir / "report.json").write_text(
        json.dumps(
            {
                "duration_seconds": 4.25,
                "measurements": {
                    "offered_operations": {
                        "status": "available",
                        "unit": "operations/s",
                        "offered_count": 40,
                        "offered_rate": 9.4,
                    },
                    "attempt_latency": {
                        "all": {
                            "status": "available",
                            "unit": "us",
                            "sample_count": 40,
                            "rejected_count": 0,
                            "median": 101000,
                            "p95": 102000,
                            "p99": 102400,
                        },
                        "by_outcome": {},
                    },
                    "sync_speed": {
                        "status": "unavailable",
                        "unit": "blocks/s",
                        "reason": "controlled range was not observed",
                    },
                },
            }
        )
    )

    section = _measurement_section(tmp_path)

    assert section["present"] is True
    assert section["profile_id"] == "amaru-security-default"
    assert section["target_identity"]["implementation"] == "amaru"
    assert section["collector_counts"] == {"finalized": 1, "error": 1}
    assert section["errors"][0]["message"] == "no adopted tip in bounded window"
    offered = next(row for row in section["metrics"] if row["name"] == "offered_operations")
    latency = next(row for row in section["metrics"] if row["name"] == "attempt_latency")
    unavailable = next(row for row in section["metrics"] if row["name"] == "sync_speed")
    assert offered["value"] == 9.4
    assert offered["count"] == 40
    assert offered["sample_count"] is None
    assert latency["sample_count"] == 40
    assert latency["count"] is None
    assert latency["median"] == 101000
    assert unavailable["reason"] == "controlled range was not observed"
    cards = {card["concept_id"]: card for card in section["presentation"]["cards"]}
    assert cards["offered-operation-rate"]["title"] == "Offered operation rate"
    assert cards["offered-operation-rate"]["source_label"] == "External"
    assert cards["workload-attempt-latency"]["primary_label"] == "Median"
    assert cards["workload-attempt-latency"]["sample_count"] == 40
    assert cards["chain-sync-speed"]["sample_count"] is None
    assert cards["chain-sync-speed"]["status"] == "unavailable"
    assert section["raw_url"].endswith("/measurements/raw")


def test_run_page_renders_measurement_report(monkeypatch, tmp_path: Path):
    run_id = "20260918T230610Z-measured"
    run_dir = tmp_path / run_id
    (run_dir / "measurements").mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "scenario": {"id": "cardano-measurement-e2e-patched"},
                "target": {"implementation": "cardano-node", "version": "11.1.2"},
                "runtime": "devnet",
                "exit_status": "pass",
                "assertion_summary": {"total": 1, "pass": 1, "fail": 0},
            }
        )
    )
    (run_dir / "chain.json").write_text(json.dumps({"prev_hash": "genesis"}))
    (run_dir / "assertions.json").write_text("[]")
    (run_dir / "measurements" / "summary.json").write_text(
        json.dumps(
            {
                "duration_seconds": 4.25,
                "metric_count": 6,
                "available_count": 5,
                "unavailable_count": 1,
            }
        )
    )
    (run_dir / "measurements" / "selection.json").write_text(
        json.dumps(
            {
                "collector_states": {"cardano-stock-resources": "finalized"},
                "resolution": {
                    "profile": {"id": "cardano-security-patched"},
                    "target_identity": {
                        "implementation": "cardano-node",
                        "version": "11.1.2",
                        "mode": "patched",
                    },
                },
            }
        )
    )
    (run_dir / "measurements" / "runtime.json").write_text(
        json.dumps({"collector_errors": []})
    )
    (run_dir / "measurements" / "report.json").write_text(
        json.dumps(
            {
                "measurements": {
                    "attempt_latency": {
                        "all": {
                            "status": "available",
                            "unit": "us",
                            "sample_count": 40,
                            "median": 101000,
                            "p95": 102000,
                            "p99": 102400,
                        },
                        "by_outcome": {},
                    },
                    "offered_operations": {
                        "status": "available",
                        "unit": "operations/s",
                        "offered_count": 100,
                        "offered_rate": 9.52,
                        "duration_seconds": 10.5
                    },
                    "sync_speed": {
                        "status": "available",
                        "unit": "blocks/s",
                        "value": 0.56,
                        "duration_seconds": 10.5,
                        "start_block_height": 100,
                        "end_block_height": 106
                    },
                    "ledger_samples": {
                        "status": "available",
                        "unit": "events",
                        "value": 110
                    },
                    "epoch_transition": {
                        "status": "available",
                        "unit": "us",
                        "sample_count": 2,
                        "median": 168,
                        "p95": 169,
                        "p99": 169
                    },
                    "restart_readiness": {
                        "status": "unavailable",
                        "unit": "s",
                        "reason": "the scenario did not request a restart"
                    }
                }
            }
        )
    )
    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(tmp_path))

    html = render_route_html(f"/operate/runs/{run_id}")

    assert "Measurement report" in html
    assert "cardano-security-patched" in html
    assert "attempt_latency" in html
    assert "102000" in html
    assert "measurements/report.json" in html
    assert "This run proves collection for this exact target and workload." in html
    assert "It is not automatically an Amaru-versus-Cardano benchmark." in html
    assert "Functional assertions passed" in html
    assert "Available report metrics" in html
    assert "Unavailable report metrics" in html
    assert "Collectors finalized" in html
    assert "Collector errors" in html
    assert "Workload and outcomes" in html
    assert "Latency and processing time" in html
    assert "Throughput and chain progress" in html
    assert "Unavailable measurements" in html
    assert "Workload attempt latency" in html
    assert "Chain sync speed" in html
    assert "Ledger events observed" in html
    assert "Evidence: Very small sample" in html
    assert "Reserved / not implemented" in html
    assert 'data-measurement-card' in html
    assert 'data-measurement-category="all"' in html
    measurement_groups = re.findall(
        r'<details class="measurement-group"[^>]*data-measurement-group="[^"]+"[^>]*>',
        html,
    )
    category_filters = re.findall(
        r'data-measurement-category="(?!all")[^"]+"',
        html,
    )
    assert len(measurement_groups) == len(category_filters)
    assert measurement_groups
    assert all(" open" not in group for group in measurement_groups)
    assert 'data-measurement-expand-all' in html
    assert 'data-measurement-collapse-all' in html
    assert '/static/js/measurement-report.js' in html
    assert f'/operate/runs/{run_id}/measurements/raw' in html
    assert '<table class="config-table measurement-table">' not in html
    assert "0 samples" not in html

    raw_html = render_route_html(f"/operate/runs/{run_id}/measurements/raw")
    assert "Raw measurement data" in raw_html
    assert f'href="/operate/runs/{run_id}"' in raw_html
    assert '<table class="config-table measurement-table">' in raw_html
    assert "attempt_latency" in raw_html
    assert "sync_speed" in raw_html
    assert "ledger_samples" in raw_html
    assert "measurements/report.json" in raw_html
    assert "measurements/report.md" in raw_html


def test_run_report_secondary_evidence_uses_closed_native_disclosures():
    template = (
        Path(__file__).parents[1]
        / "dwarf"
        / "dashboard"
        / "templates"
        / "operate"
        / "run.j2"
    ).read_text(encoding="utf-8")

    section_ids = {
        "manifest",
        "version-provenance",
        "execution-definition",
        "interesting-evidence",
        "chain",
        "provenance",
        "export",
        "replay",
        "diff",
        "actions",
        "cross-impl",
        "substrate-evidence",
        "floor-preview",
        "assertions",
        "probes",
        "log-tail",
    }
    for section_id in section_ids:
        opening = re.search(
            rf'<details class="run-disclosure[^"]*" data-run-section="{section_id}"[^>]*>',
            template,
        )
        assert opening is not None, section_id
        assert " open" not in opening.group(0), section_id

    assert '<summary class="run-disclosure__summary">' in template
    assert '<div class="run-disclosure__body">' in template
