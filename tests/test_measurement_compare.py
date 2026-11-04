"""Python mirror of the offline viewer's measurement comparison model."""
from profile_manager.data.measurement_compare import (
    FILTER_KEYS,
    _catalog_index,
    _group_label,
    _metric_cell,
    build_measurement_comparison,
    catalog_concept,
    describe_metric,
    group_key,
)


def _record(run_id, impl, mode, scenario, metrics, **extra):
    base = {
        "run_id": run_id, "implementation": impl, "version": "v1", "source_revision": "a" * 40,
        "image_digest": "sha256:" + ("c" if mode == "patched" else "d") * 64, "mode": mode,
        "scenario_id": scenario, "scenario_behavior_sha256": "s" * 64, "runtime": "devnet",
        "measurement_profile_id": f"{impl}-{mode}", "measurement_profile_digest": "p" * 64,
        "collector_digests": ["k" * 64], "collector_missing": [], "profile_id": "profile-x", "profile_sha256": "q" * 64,
        "report_schema_version": "v1", "provenance_state": "complete", "identity_missing": [], "identity_source": "report",
        "exit_status": "pass", "verdict": "pass", "ended_at": "2026-11-04T01:00:00Z", "evidence_state": "verified",
        "run_url": f"/runs/{run_id}", "bundle_url": f"/runs/{run_id}/bundle",
        "sources": [], "session_order": 0, "metrics": {k: _metric_cell(k, v) for k, v in metrics.items()},
    }
    base.update(extra)
    return base


def US(median):
    return {"status": "available", "unit": "us", "median": median, "sample_count": 10}


def test_metric_cell_sums_verdict_dicts():
    cell = _metric_cell("header_validation", {"status": "available", "unit": "count", "value": {"accepted": 205, "rejected": 0}})
    assert cell["primary_value"] == 205 and cell["positive_evidence"]


def test_group_identity_includes_mode():
    stock = _record("r1", "amaru", "stock", "w", {})
    patched = _record("r2", "amaru", "patched", "w", {}, image_digest=stock["image_digest"])
    assert group_key(stock) != group_key(patched)
    assert " · stock" in _group_label(stock, "exact") and " · patched" in _group_label(patched, "exact")


def test_catalog_index_emits_concepts_and_binding_lists():
    index = _catalog_index()
    assert index["concepts"]["keepalive-round-trip-time"]["comparison_direction"] == "lower"
    assert [b["concept"] for b in index["bindings"]["cardano-node"]["keepalive_rtt"]] == ["keepalive-round-trip-time"]
    assert {b["concept"] for b in index["bindings"]["amaru"]["pending_depth"]} == {"blockfetch-pending-depth", "txsubmission-pending-depth"}
    assert index["unit_short"]["microseconds"] == "us" and index["implementation_labels"]["amaru"] == "Amaru"
    assert index["concepts"]["chain-advance-count"]["compare_as"] is None
    assert index["concepts"]["adopted-block-count"]["compare_as"] == "chain-advance-count"


def test_describe_is_unit_aware():
    index = _catalog_index()
    rec = _record("r", "amaru", "patched", "w", {})
    assert describe_metric(index, rec, "pending_depth", {"unit": "requests"})["title"] == "BlockFetch pending depth"
    assert describe_metric(index, rec, "pending_depth", {"unit": "transactions"})["title"] == "TxSubmission pending depth"


def test_runs_filter_merges_implementations_on_concept_rows():
    amaru = _record(
        "a1", "amaru", "stock", "amaru-measurement-workload-stock-portable",
        {"keepalive_round_trip": US(585), "chain_tip_updates": {"status": "available", "unit": "events", "value": 160}},
    )
    cardano = _record(
        "c1", "cardano-node", "stock", "cardano-measurement-workload-stock-portable",
        {"keepalive_rtt": US(569.5), "adopted_blocks": {"status": "available", "unit": "blocks", "value": 173}},
        session_order=1,
    )
    gated = build_measurement_comparison([amaru, cardano], filters={})
    assert gated["focus_required"] is True
    assert not any(r["concept_grouped"] for r in gated["rows"]), "no concept rows without picks"
    assert {r["metric_id"] for r in gated["rows"]} >= {"keepalive_round_trip", "keepalive_rtt"}
    picked = build_measurement_comparison([amaru, cardano], filters={"runs": "a1,c1"})
    assert picked["focus_required"] is False and len(picked["groups"]) == 2
    rows = {r["metric_id"]: r for r in picked["rows"]}
    ka = rows["keepalive-round-trip-time"]
    assert ka["concept_grouped"] and ka["comparison_direction"] == "lower" and not ka["approximate"]
    assert sorted(c["value"] for c in ka["cells"].values()) == [569.5, 585]
    assert {c["metric_id"] for cell in ka["cells"].values() for c in cell["contributors"]} == {"keepalive_round_trip", "keepalive_rtt"}
    adv = rows["chain-advance-count"]
    assert adv["approximate"] and adv["comparison_direction"] == "neutral" and adv["unit"] == "events"
    assert sorted(c["value"] for c in adv["cells"].values()) == [160, 173]
    assert "runs" in FILTER_KEYS
    assert catalog_concept(_catalog_index(), amaru, "keepalive_round_trip", amaru["metrics"]["keepalive_round_trip"])["id"] == "keepalive-round-trip-time"


def test_unknown_pick_yields_no_rows():
    amaru = _record("a1", "amaru", "stock", "w", {"keepalive_round_trip": US(1)})
    result = build_measurement_comparison([amaru], filters={"runs": "nope"})
    assert result["rows"] == [] and result["picked_run_count"] == 0
