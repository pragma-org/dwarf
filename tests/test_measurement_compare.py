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


def test_alternate_ids_of_one_concept_contribute_once_per_record():
    depth = _record(
        "a2", "amaru", "patched", "w",
        {
            "pending_depth": {"status": "available", "unit": "requests", "median": 0, "sample_count": 9},
            "amaru-patched-txsubmission-residence.pending_depth": {"status": "available", "unit": "transactions", "median": 0, "sample_count": 4},
        },
    )
    cardano = _record(
        "c2", "cardano-node", "stock", "w2",
        {"adopted_blocks": {"status": "available", "unit": "blocks", "value": 173},
         "cardano-stock-ledger-block-epoch.adopted_blocks": {"status": "available", "unit": "blocks", "value": 173}},
    )
    result = build_measurement_comparison([depth, cardano], filters={"runs": "a2,c2", "show_unmeasured": "1"})
    rows = {r["metric_id"]: r for r in result["rows"]}
    assert rows["txsubmission-pending-depth"]["unit"] == "transactions"
    assert rows["blockfetch-pending-depth"]["unit"] == "requests"
    adv = rows["chain-advance-count"]
    contributors = [c for cell in adv["cells"].values() for c in cell["contributors"]]
    assert [c["metric_id"] for c in contributors] == ["adopted_blocks"], "the prefixed alternate never double-counts"


def test_unknown_pick_yields_no_rows():
    amaru = _record("a1", "amaru", "stock", "w", {"keepalive_round_trip": US(1)})
    result = build_measurement_comparison([amaru], filters={"runs": "nope"})
    assert result["rows"] == [] and result["picked_run_count"] == 0


def test_review_fixes_no_leftover_rows_antithesis_isolation_and_unit_guards():
    amaru = _record("a1", "amaru", "stock", "wa", {"block_application": US(100), "mempool_accepted": {"status": "available", "unit": "events", "value": 40}})
    # A patched cardano record: stock cardano-node emits no block_application ids at all.
    cardano = _record(
        "c1", "cardano-node", "patched", "wc",
        {"adopted_blocks": {"status": "available", "unit": "blocks", "value": 173},
         "cardano-stock-ledger-block-epoch.adopted_blocks": {"status": "available", "unit": "blocks", "value": 173},
         "block_application_by_outcome": {"status": "available", "unit": "us", "value": {"completed": 3}},
         "mempool_accepted": {"status": "available", "unit": "transactions", "value": 40}},
        session_order=1,
    )
    anti = _record("x1", "amaru", "patched", "anti", {"block_application": US(9)}, runtime="antithesis", derived_import_identity="e" * 64, session_order=2)
    result = build_measurement_comparison([amaru, cardano, anti], filters={"runs": "a1,c1,x1", "show_unmeasured": "1"})
    ids = [r["metric_id"] for r in result["rows"]]
    assert not [i for i in ids if "adopted_blocks" in i or i.endswith("_by_outcome")], ids
    block = [r for r in result["rows"] if r["metric_id"] == "block-application-time"]
    assert len(block) == 1 and {c["run_id"] for cell in block[0]["cells"].values() for c in cell["contributors"]} == {"a1"}
    assert any(r["metric_id"] == "block_application" and not r["concept_grouped"] for r in result["rows"]), "antithesis run keeps a raw row"
    accepted = [r for r in result["rows"] if r["metric_id"] == "mempool-accepted-count"]
    assert len(accepted) == 1 and sum(1 for c in accepted[0]["cells"].values() if c["value"] is not None) == 2
    index = _catalog_index()
    foreign = _record("c3", "cardano-node", "stock", "w", {"transaction_outcomes": {"status": "available", "unit": "events", "value": 40}})
    assert catalog_concept(index, foreign, "transaction_outcomes", foreign["metrics"]["transaction_outcomes"]) is None
    received = _record("a4", "amaru", "stock", "w", {"mempool_received": {"status": "available", "unit": "events", "value": 40}})
    assert catalog_concept(index, received, "mempool_received", received["metrics"]["mempool_received"])["id"] == "mempool-intake-count"


def test_metric_cell_reads_a_peak_primary_and_an_evidenced_zero():
    """workload-backlog's primary statistic is its peak; a header-prerequisite wait with
    no occurrences among 489 validations is an observed zero, not a blank cell
    (Compare page, Pages set of 2026-11-04)."""
    from profile_manager.data.measurement_compare import _metric_cell, cell_state
    backlog = _metric_cell("backlog", {"status": "available", "unit": "operations", "peak": 3.0, "baseline": 1.0, "sample_count": 122})
    assert backlog["primary_value"] == 3.0 and cell_state(backlog) == "available"
    empty = _metric_cell("header_prerequisite_unavailable", {"status": "unavailable", "unit": "us", "median": None, "sample_count": 0, "offered_count": 489})
    assert empty["primary_value"] == 0 and cell_state(empty) == "observed-zero"


# ---------------------------------------------------------------------------
# AT-4: ledger-bound Antithesis runs join the catalog concepts; hand imports stay isolated

def test_ledger_bound_antithesis_record_joins_catalog_concepts():
    from profile_manager.data.measurement_compare import _catalog_index, catalog_concept, is_antithesis_record
    index = _catalog_index()
    bound = _record("external-antithesis-" + "a" * 24, "amaru", "patched", "anti", {"block_application": US(9)},
                    runtime="antithesis", external_test_run_id="f" * 64)
    assert is_antithesis_record(bound) is False
    assert catalog_concept(index, bound, "block_application", bound["metrics"]["block_application"])["id"] == "block-application-time"


def test_hand_imported_antithesis_record_keeps_its_own_table():
    from profile_manager.data.measurement_compare import _catalog_index, catalog_concept, is_antithesis_record
    index = _catalog_index()
    imported = _record("x1", "amaru", "patched", "anti", {"block_application": US(9)}, runtime="antithesis", derived_import_identity="e" * 64)
    assert is_antithesis_record(imported) is True
    assert catalog_concept(index, imported, "block_application", imported["metrics"]["block_application"]) is None


def test_collect_records_picks_up_an_antithesis_result_bundle(tmp_path):
    import json
    from pathlib import Path
    from profile_manager import antithesis_results
    from profile_manager.data.measurement_compare import collect_measurement_run_records
    fixtures = Path(__file__).resolve().parent / "fixtures" / "antithesis_proxy"
    run = json.loads((fixtures / "run-completed.json").read_text()); props = json.loads((fixtures / "properties.json").read_text())
    run_dir = tmp_path / "runs" / ("external-antithesis-" + "b" * 24)
    entry = {"run_id": run_dir.name, "kind": "external", "backend": "antithesis", "evidence_path": str(run_dir),
             "scenario": {"id": "cardano-amaru-miniprotocol-security-local", "sha256": "c" * 64}, "provenance": {"external_test_run_id": "f" * 64}}
    result = antithesis_results.normalise_result(run, props, test_run_id="f" * 64)
    measurements = {"schema_version": "v1", "scenario": "cardano-amaru-miniprotocol-security-local", "source": "antithesis-events", "complete": False,
                    "target_identity": {"engine": "antithesis", "implementation": "cardano-node", "mode": "patched", "version": "10.6.2"},
                    "measurements": {"block_application": {"status": "available", "value": 3.2, "unit": "us"}}}
    antithesis_results.write_result_bundle(run_dir, entry=entry, result=result, raw={"run": run, "properties": props}, scenario_source=None, measurements=measurements)
    records = collect_measurement_run_records(tmp_path / "runs")
    assert len(records) == 1
    record = records[0]
    assert record["run_id"] == run_dir.name and record["runtime"] == "antithesis" and record["external_test_run_id"] == "f" * 64
    assert record["scenario_id"] == "cardano-amaru-miniprotocol-security-local" and record["evidence_state"] == "bound"
    assert record["implementation"] == "cardano-node" and record["verdict"] == "fail" and "block_application" in record["metrics"]


def test_rebuild_measurements_from_progress_lines():
    import json
    from profile_manager.antithesis_results import rebuild_measurements
    line = lambda service, measurements: {"output_text": json.dumps({"dwarf_measurements": service, "source_available": True, "source_window_incomplete": False,
        "collector_errors": [], "collectors": {"c1": {"source_records": 3, "normalized_records": 3, "incomplete": False, "measurements": measurements}}, "artifacts": "/evidence/" + service})}
    events = [line("sm-cardano-victim", {"block_application": {"status": "available", "value": 1.5, "unit": "us"}}),
              {"output_text": "not json"}, {"output_text": json.dumps({"other": 1})},
              line("sm-cardano-victim", {"block_application": {"status": "available", "value": 3.2, "unit": "us"}, "handler_service": {"status": "unavailable", "unit": "us"}})]
    identity = {"engine": "antithesis", "implementation": "cardano-node", "mode": "patched", "version": "10.6.2"}
    report = rebuild_measurements(events, scenario="s", target_identity=identity)
    assert report == {"schema_version": "v1", "scenario": "s", "target_identity": identity, "source": "antithesis-events", "complete": False,
                      "services": ["sm-cardano-victim"],
                      "measurements": {"block_application": {"status": "available", "value": 3.2, "unit": "us"},
                                       "handler_service": {"status": "unavailable", "value": None, "unit": "us"}}}


def test_rebuild_measurements_returns_none_without_lines():
    from profile_manager.antithesis_results import rebuild_measurements
    assert rebuild_measurements([], scenario="s", target_identity={}) is None
    assert rebuild_measurements([{"output_text": "{\"other\": 1}"}], scenario="s", target_identity={}) is None
