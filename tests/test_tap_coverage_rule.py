"""The tap coverage rule: every measurement concept bound for a run's implementation and
mode has a value on at least one of its bound ids, or is a documented exception."""
import io
import json
import tarfile
from pathlib import Path

import pytest

from profile_manager.tap_coverage import (
    coverage_report,
    load_coverage_exceptions,
    report_identity,
    rule_holds,
)

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "docs/evidence/runs"
WORKLOAD_ARCHIVES = {
    "amaru-stock": "20261104T140927Z-a00e18a6-bundle-export.tar.gz",
    "amaru-patched": "20261104T144249Z-a47bed21-bundle-export.tar.gz",
    "cardano-stock": "20261104T130554Z-ef991882-bundle-export.tar.gz",
    "cardano-patched": "20261104T132207Z-b5b6981b-bundle-export.tar.gz",
}


def _report(archive: Path) -> dict:
    with tarfile.open(archive, "r:gz") as tar:
        member = next(m for m in tar.getmembers() if m.name.endswith("/measurements/report.json"))
        return json.loads(tar.extractfile(member).read())


def test_exception_catalog_is_well_formed():
    exceptions = load_coverage_exceptions()
    assert exceptions["schema_version"] == "v1"
    for row in exceptions["exceptions"]:
        assert row["implementation"] in {"amaru", "cardano-node"}
        assert set(row["modes"]) <= {"stock", "patched"} and row["modes"]
        assert row["reason"]
    concepts = {(r["implementation"], r["concept"]) for r in exceptions["exceptions"]}
    assert ("amaru", "header-arrival-offset") in concepts


@pytest.mark.parametrize("leg", sorted(WORKLOAD_ARCHIVES))
def test_retained_workload_archives_hold_the_rule(leg):
    """The Pages default set: every bound concept carries a value or a documented exception."""
    report = _report(RUNS / WORKLOAD_ARCHIVES[leg])
    implementation, mode = report_identity(report)
    assert (implementation, mode) == {"amaru-stock": ("amaru", "stock"), "amaru-patched": ("amaru", "patched"),
                                      "cardano-stock": ("cardano-node", "stock"), "cardano-patched": ("cardano-node", "patched")}[leg]
    result = coverage_report(report["measurements"], implementation, mode)
    assert "restart-readiness" in result["covered"] and "keepalive-round-trip-time" in result["covered"]
    assert "resident-memory" in result["covered"] and "workload-backlog" in result["covered"]
    assert "submit-to-mempool-visibility-time" in result["covered"]
    assert result["missing"] == [] and result["unbound_ids"] == []
    excepted = {row["concept"] for row in result["excepted"]}
    if implementation == "amaru":
        assert "header-arrival-offset" in excepted
    if leg == "cardano-patched":
        assert "txsubmission-residence" in result["covered"]   # the v5 contract, exercised through V2 inbound logic
    assert rule_holds(report) is True


def test_rule_holds_on_a_complete_synthetic_report():
    amaru_stock = _report(RUNS / WORKLOAD_ARCHIVES["amaru-stock"])
    metrics = {}
    for metric_id, raw in amaru_stock["measurements"].items():
        unit = (raw.get("all") or raw).get("unit") if isinstance(raw, dict) else None
        metrics[metric_id] = {"status": "available", "unit": unit or "events", "median": 3.0, "sample_count": 4, "value": 3}
    result = coverage_report(metrics, "amaru", "stock")
    assert result["missing"] == [] and result["unbound_ids"] == []
    assert rule_holds({**amaru_stock, "measurements": metrics}) is True


def test_zero_counts_only_with_evidence():
    metrics = {"mempool_rejected": {"status": "available", "unit": "events", "value": 0}}
    assert coverage_report(metrics, "amaru", "stock")["missing"][0]["concept"] == "mempool-rejected-count"
    metrics["mempool_rejected"]["offered_count"] = 40
    assert coverage_report(metrics, "amaru", "stock")["missing"] == []
    verdicts = {"header_validation": {"status": "available", "unit": "count", "value": {"accepted": 5, "rejected": 0}}}
    assert "header-validation-verdicts" in coverage_report(verdicts, "cardano-node", "stock")["covered"]


def test_pages_staging_enforces_the_rule_for_flagged_entries(tmp_path):
    from scripts import ci_stage_pages_campaigns as api
    row = {"url": "runs/" + WORKLOAD_ARCHIVES["amaru-stock"], "size_bytes": 1, "sha256": "a" * 64,
           "run_id": "20261104T013138Z-572908c9", "implementation": "amaru", "version": "10.11.20261029",
           "coverage_rule": True}
    api.validate_defaults({"schema_version": api._SCHEMA, "archives": [row]})
    api.validate_defaults({"schema_version": api._SCHEMA, "archives": [{**row, "coverage_rule": None, "coverage_exception": "Antithesis snapshot: bounded trace window"}]})
    with pytest.raises(ValueError, match="coverage"):
        api.validate_defaults({"schema_version": api._SCHEMA, "archives": [{**row, "coverage_rule": "yes"}]})
    # Every flagged archive in the committed set passes the rule; a report missing a tap is refused.
    for leg in WORKLOAD_ARCHIVES:
        api.check_coverage_rule(RUNS / WORKLOAD_ARCHIVES[leg])
    broken = dict(_report(RUNS / WORKLOAD_ARCHIVES["amaru-stock"]))
    broken["measurements"] = dict(broken["measurements"])
    broken["measurements"]["backlog"] = {"status": "unavailable", "unit": "operations", "reason": "no backlog samples were retained"}
    broken_path = tmp_path / "broken-bundle-export.tar.gz"
    payload = json.dumps(broken).encode()
    with tarfile.open(broken_path, "w:gz") as tar:
        info = tarfile.TarInfo("run/measurements/report.json")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    with pytest.raises(ValueError, match="workload-backlog"):
        api.check_coverage_rule(broken_path)


def test_scalar_peak_and_counter_delta_shapes_count_as_values():
    from profile_manager.tap_coverage import metric_has_value
    backlog = {"status": "available", "unit": "operations", "peak": 1.0, "sample_count": 122, "baseline": 1.0}
    assert metric_has_value(backlog)
    counter = {"status": "available", "unit": "s", "delta": 0.0, "start": 0.29, "end": 0.29, "resets": 0}
    assert not metric_has_value(counter), "a zero counter delta without evidence is not a value"
    assert metric_has_value({**counter, "delta": 12.5})


def test_an_empty_distribution_with_an_offered_count_is_an_evidenced_zero():
    """No header was validated before its prerequisite in run 20261104T131252Z-31130032,
    out of 489 validations observed: that is a zero-occurrence reading, not a gap."""
    from profile_manager.tap_coverage import metric_has_value
    empty = {"status": "unavailable", "unit": "us", "median": None, "sample_count": 0}
    assert not metric_has_value(empty)
    assert metric_has_value({**empty, "offered_count": 489})
