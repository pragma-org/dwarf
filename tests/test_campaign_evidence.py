"""Campaign evaluation consumes retained evidence; it never runs a workload."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from profile_manager import security_campaign as planner
from profile_manager import campaign_evidence as api
from profile_manager.security_campaign_suite import load_measurement_suite, load_security_campaign_suite
from profile_manager.testplease_candidates import CandidateSnapshot

ROOT = Path(__file__).resolve().parents[1]
D = "sha256:" + "d" * 64
COMPAT = {"era": "conway", "genesis_digest": D, "protocol_version": "10"}


@pytest.fixture(scope="module")
def campaign_plan():
    candidate = CandidateSnapshot("amaru", "proposal", "a" * 40, "b" * 64, Path("/unused"))
    manifest = planner.resolve_security_campaign(candidate,
        load_security_campaign_suite(ROOT / "dwarf/security-suites/github-security-regression-v1.yaml"),
        load_measurement_suite(ROOT / "dwarf/qualification-suites/node-measurements-v1.yaml"),
        selection={"entry_ids": ["amaru-cbor-tx-body-fuzz-structured"],
                   "implementations": {"amaru": {"kind": "candidate", "version": "proposal", "compatibility": COMPAT}}},
        catalog={"schema_version": 1, "updated_at": "2026-10-19T00:00:00Z", "releases": [], "compatibility_pairs": []})
    return manifest


@pytest.fixture
def campaign(campaign_plan):
    return deepcopy(campaign_plan)


def freeze(manifest):
    manifest["campaign_digest"] = planner.digest_security_campaign(manifest)
    manifest["campaign_id"] = "campaign-" + manifest["campaign_digest"].split(":")[1]
    return manifest


def evidence(manifest):
    builds = [{"mode": mode, "source": deepcopy(manifest["candidate"]),
               "artifacts": {key: D for key in ("image_digest", "binary_digest", "executable_digest")},
               "patch_set_digest": D if mode == "patched" else None,
               "instrumentation_digest": D if mode == "coverage" else None,
               "toolchain_digest": D} for mode in ("stock", "patched", "coverage")]
    entry = manifest["entries"][0]
    target = {"implementation": "amaru", "version": "proposal", "source_revision": "a" * 40,
              "source_tree_digest": "b" * 64, "mode": "stock", **builds[0]["artifacts"]}
    scenario_id = entry["contract"]["scenario_id"]
    scenario_digest = manifest["definitions"]["scenarios"][scenario_id]["digest"]
    control = {"run_id": "control-1", "scenario_id": scenario_id, "scenario_digest": scenario_digest,
               "execution_digest": planner._digest(entry["execution"]),
               "targets": {"node1": deepcopy(target)}, "status": "pass", "evidence": [{"diagnostic": "valid body decoded"}]}
    declared = manifest["definitions"]["scenarios"][scenario_id]["document"]["assertions"]
    assertions = [{"primitive": row["primitive"], "params": {key: value for key, value in row.items() if key != "primitive"},
                   "result": "pass", "evaluated_value": True, "data_points_used": 1} for row in declared]
    observation = {"run_id": "run-1", "node_id": "node1", "boundary": "decoder", "input_digest": D,
                   "diagnostic": "decoder processed malformed body", "activity_count": 1}
    security = {"entry_id": entry["id"], "run_id": "run-1", "mode": "stock", "scenario_id": scenario_id,
                "scenario_digest": scenario_digest, "contract_digest": planner._digest(entry["contract"]),
                "execution_digest": planner._digest(entry["execution"]),
                "boundary": "decoder", "targets": {"node1": target}, "normal_control": control,
                "reachability": [deepcopy(observation)], "oracle": {"observations": [observation]},
                "assertions": assertions, "errors": []}
    return {"schema_version": "v1", "campaign_digest": manifest["campaign_digest"],
            "builds": builds, "security": [security], "measurements": []}


def single_cell(manifest, *, rule=None, modes=None):
    # A small frozen campaign isolates completeness predicates from catalog gaps.
    cell = deepcopy(next(c for c in manifest["required_tap_cells"] if c["measurement_id"] == "amaru-stock-resources"))
    cell["target_modes"] = modes or ["stock"]
    cell["rule"] = rule or {"min_samples": 1, "correlation": ["run_id"]}
    manifest["required_tap_cells"] = [cell]
    return freeze(manifest), cell


def measurement(manifest, cell, ev, *, mode="stock"):
    target = deepcopy(ev["security"][0]["targets"]["node1"])
    target["mode"] = mode
    if mode == "patched":
        target["patch_set_sha256"] = "d" * 64
    if mode == "coverage":
        target["coverage_harness_sha256"] = "d" * 64
    control = deepcopy(ev["security"][0]["normal_control"])
    control.update(scenario_id=cell["scenario_id"], scenario_digest=manifest["definitions"]["scenarios"][cell["scenario_id"]]["digest"], targets={"node1": deepcopy(target)})
    return {"topology_id": cell["topology_id"], "node_id": cell["node_id"], "measurement_id": cell["measurement_id"],
            "mode": mode, "candidate": deepcopy(manifest["candidate"]), "scenario_digest": control["scenario_digest"],
            "normal_control": control, "samples": [{"run_id": "measurement-1", "value": 4}], "events": [],
            "result": {"schema_version": "v1", "measurement_id": cell["measurement_id"], "run_id": "measurement-1",
                       "status": "collected", "sample_count": 1, "target": target,
                       "provenance": {"scenario_id": cell["scenario_id"], "experimental_consent": mode == "patched"},
                       "correlation": {"definition_digest": cell["definition_digest"]}, "records": []}}


def security_result(manifest, ev):
    return api.evaluate_campaign_evidence(manifest, ev)["security_validity"]["entries"][0]


def cell_result(manifest, ev):
    return api.evaluate_campaign_evidence(manifest, ev)["measurement_completeness"]["cells"][0]


def test_pass_is_deterministic_and_does_not_mutate_retained_inputs(campaign):
    ev = evidence(campaign)
    before = deepcopy((campaign, ev))
    result = api.evaluate_campaign_evidence(campaign, ev)
    assert result == api.evaluate_campaign_evidence(campaign, ev)
    assert security_result(campaign, ev)["classification"] == "pass"
    assert result["measurement_completeness"]["measurement_complete"] is False
    assert (campaign, ev) == before


@pytest.mark.parametrize("which", ["targets", "normal_control"])
@pytest.mark.parametrize("field,value", [("source_revision", "e" * 40), ("executable_digest", "mutable"), ("version", "other"), ("source_tree_digest", "e" * 64)])
def test_security_requires_exact_target_and_control_identity(campaign, which, field, value):
    ev = evidence(campaign)
    record = ev["security"][0]
    targets = record["targets"] if which == "targets" else record["normal_control"]["targets"]
    targets["node1"][field] = value
    assert security_result(campaign, ev)["classification"] == "harness-invalid"


@pytest.mark.parametrize("mutation,expected", [("control-fail", "harness-invalid"), ("no-control", "inconclusive"),
    ("no-reachability", "inconclusive"), ("wrong-boundary", "inconclusive"), ("zero-activity", "inconclusive"),
    ("no-oracle", "inconclusive"), ("zero-points", "inconclusive"), ("no-assertions", "inconclusive"),
    ("finding", "finding"), ("harness", "harness-invalid"), ("infrastructure", "infrastructure-invalid")])
def test_security_nonvacuity_and_separate_classifications(campaign, mutation, expected):
    ev = evidence(campaign)
    row = ev["security"][0]
    if mutation == "control-fail": row["normal_control"]["status"] = "fail"
    elif mutation == "no-control": del row["normal_control"]
    elif mutation == "no-reachability": row["reachability"] = []
    elif mutation == "wrong-boundary": row["reachability"][0]["boundary"] = "http"
    elif mutation == "zero-activity": row["reachability"][0]["activity_count"] = 0
    elif mutation == "no-oracle": row["oracle"]["observations"] = []
    elif mutation == "zero-points": row["assertions"][0]["data_points_used"] = 0
    elif mutation == "no-assertions": row["assertions"] = []
    elif mutation == "finding": row["assertions"][0]["result"] = "fail"
    else: row["errors"] = [{"kind": mutation, "message": "explicit execution failure"}]
    assert security_result(campaign, ev)["classification"] == expected


def test_invalid_precedes_finding_and_finding_precedes_other_inconclusive_entries(campaign):
    ev = evidence(campaign)
    row = ev["security"][0]
    row["assertions"][0]["result"] = "fail"
    row["errors"] = [{"kind": "infrastructure", "message": "runner lost"}, {"kind": "harness", "message": "bad fixture"}]
    assert security_result(campaign, ev)["classification"] == "harness-invalid"


def test_http_timeout_does_not_prove_crash(campaign):
    ev = evidence(campaign)
    row = ev["security"][0]
    row["assertions"][0]["result"] = "fail"
    row["oracle"]["claim"] = "node-crash"
    row["oracle"]["observations"][0].update(kind="http", diagnostic="HTTP 503 timeout")
    assert security_result(campaign, ev)["classification"] == "inconclusive"
    row["oracle"]["observations"][0].update(kind="process", process_exited=True)
    assert security_result(campaign, ev)["classification"] == "finding"


@pytest.mark.parametrize("broken", ["empty-control", "http-only", "assertion-params", "missing-build"])
def test_bad_control_or_oracle_cannot_be_hidden_by_passing_assertions(campaign, broken):
    ev = evidence(campaign)
    row = ev["security"][0]
    if broken == "empty-control":
        row["normal_control"]["evidence"] = [{}]
    elif broken == "http-only":
        row["oracle"]["observations"][0]["kind"] = "http"
    elif broken == "assertion-params":
        row["assertions"][0]["params"] = {"unrelated": "assertion"}
    else:
        ev["builds"] = []
    assert security_result(campaign, ev)["classification"] in {"harness-invalid", "inconclusive"}


@pytest.mark.parametrize("missing", ["sample_count", "samples", "control", "candidate", "scenario", "node", "tap"])
def test_available_badge_cannot_replace_required_evidence(campaign, missing):
    manifest, cell = single_cell(campaign)
    ev = evidence(manifest)
    row = measurement(manifest, cell, ev)
    row["result"]["status"] = "available"
    if missing == "sample_count": row["result"]["sample_count"] = 0
    elif missing == "samples": row["samples"] = []
    elif missing == "control": del row["normal_control"]
    elif missing == "candidate": row["candidate"]["repository_commit"] = "e" * 40
    elif missing == "scenario": row["scenario_digest"] = D
    elif missing == "node": row["result"]["target"]["source_revision"] = "e" * 40
    else: row["result"]["correlation"]["definition_digest"] = D
    ev["measurements"] = [row]
    assert cell_result(manifest, ev)["state"] == "incomplete"


def test_nonzero_samples_and_control_verify_cell_without_gating_security(campaign):
    manifest, cell = single_cell(campaign)
    ev = evidence(manifest)
    row = measurement(manifest, cell, ev)
    row["result"]["threshold"] = {"informational": True, "exceeded": True}
    ev["measurements"] = [row]
    result = api.evaluate_campaign_evidence(manifest, ev)
    assert result["measurement_completeness"]["measurement_complete"] is True
    assert cell_result(manifest, ev)["state"] == "verified"
    assert result["security_validity"]["classification"] == "pass"


def test_event_rule_accepts_terminal_event_without_numeric_samples(campaign):
    manifest, cell = single_cell(campaign, rule={"required_event": "terminal", "outcomes": ["rejected"], "correlation": ["run_id", "input_id"]})
    ev = evidence(manifest)
    row = measurement(manifest, cell, ev)
    row.update(samples=[], events=[{"event": "terminal", "outcome": "rejected", "run_id": "measurement-1", "input_id": "input-1"}])
    row["result"]["sample_count"] = 0
    ev["measurements"] = [row]
    assert cell_result(manifest, ev)["state"] == "verified"


@pytest.mark.parametrize("broken", ["outcome", "correlation", "run", "restart", "progress", "coverage-count", "coverage-path"])
def test_conjunctive_exercise_predicates(campaign, broken):
    rule = {"min_samples": 2, "required_event": "terminal", "outcomes": ["accepted", "rejected"],
            "correlation": ["run_id", "input_id"], "restart": True, "progress": {"min_blocks": 2},
            "coverage_envelope": {"min_covered_functions": 2, "required_paths": ["decode"]}}
    manifest, cell = single_cell(campaign, rule=rule)
    ev = evidence(manifest)
    row = measurement(manifest, cell, ev)
    row.update(samples=[{"run_id": "measurement-1", "input_id": "i1", "value": 1}, {"run_id": "measurement-1", "input_id": "i2", "value": 2}],
               events=[{"event": "terminal", "run_id": "measurement-1", "input_id": "i1", "outcome": "accepted"},
                       {"event": "restart_started", "run_id": "measurement-1", "input_id": "i2", "outcome": "rejected"}],
               progress={"start_block": 10, "end_block": 12})
    row["result"].update(sample_count=2, coverage={"covered_functions": 2, "paths": ["decode"]})
    ev["measurements"] = [row]
    assert cell_result(manifest, ev)["state"] == "verified"
    if broken == "outcome": row["events"][1]["outcome"] = "unknown"
    elif broken == "correlation": del row["samples"][1]["input_id"]
    elif broken == "run": row["events"][0]["run_id"] = "other-run"
    elif broken == "restart": row["events"][1]["event"] = "startup-config"
    elif broken == "progress": row["progress"]["end_block"] = 10
    elif broken == "coverage-count": row["result"]["coverage"]["covered_functions"] = 0
    else: row["result"]["coverage"]["paths"] = []
    assert cell_result(manifest, ev)["state"] == "incomplete"


@pytest.mark.parametrize("state", ["unavailable", "failed", "blocked", "reserved", "exercised"])
def test_measurement_states_remain_distinct(campaign, state):
    manifest, cell = single_cell(campaign)
    ev = evidence(manifest)
    row = measurement(manifest, cell, ev)
    row["result"]["status"] = state
    ev["measurements"] = [row]
    assert cell_result(manifest, ev)["state"] == state
    assert api.evaluate_campaign_evidence(manifest, ev)["measurement_completeness"]["measurement_complete"] is False


def test_missing_runtime_and_each_mode_keep_campaign_incomplete(campaign):
    manifest, cell = single_cell(campaign, modes=["stock", "patched", "coverage"])
    ev = evidence(manifest)
    ev["measurements"] = [measurement(manifest, cell, ev)]
    cells = api.evaluate_campaign_evidence(manifest, ev)["measurement_completeness"]["cells"]
    assert {c["mode"]: c["state"] for c in cells} == {"stock": "verified", "patched": "missing", "coverage": "missing"}
    cell["runtime"] = {"status": "missing", "reason": "no collector"}
    manifest = freeze(manifest)
    ev["campaign_digest"] = manifest["campaign_digest"]
    assert all(c["state"] == "blocked" for c in api.evaluate_campaign_evidence(manifest, ev)["measurement_completeness"]["cells"])


def test_every_mode_can_verify_only_with_its_exact_build_and_consent(campaign):
    manifest, cell = single_cell(campaign, modes=["stock", "patched", "coverage"])
    ev = evidence(manifest)
    ev["measurements"] = [measurement(manifest, cell, ev, mode=mode) for mode in cell["target_modes"]]
    assert api.evaluate_campaign_evidence(manifest, ev)["measurement_completeness"]["measurement_complete"] is True
    ev["measurements"][1]["result"]["provenance"]["experimental_consent"] = False
    assert api.evaluate_campaign_evidence(manifest, ev)["measurement_completeness"]["measurement_complete"] is False


def test_correlation_identifiers_without_an_observation_are_not_samples(campaign):
    manifest, cell = single_cell(campaign)
    ev = evidence(manifest)
    row = measurement(manifest, cell, ev)
    row["samples"] = [{"run_id": "measurement-1"}]
    ev["measurements"] = [row]
    assert cell_result(manifest, ev)["state"] == "incomplete"


@pytest.mark.parametrize("bad", ["manifest-digest", "evidence-digest", "duplicate-entry", "unknown-entry", "duplicate-cell", "bad-count", "bad-container"])
def test_malformed_or_ambiguous_inputs_fail_clearly(campaign, bad):
    manifest, cell = single_cell(campaign)
    ev = evidence(manifest)
    if bad == "manifest-digest": manifest["candidate"]["candidate_name"] = "tampered"
    elif bad == "evidence-digest": ev["campaign_digest"] = D
    elif bad == "duplicate-entry": ev["security"] *= 2
    elif bad == "unknown-entry": ev["security"][0]["entry_id"] = "unknown"
    elif bad == "duplicate-cell": ev["measurements"] = [measurement(manifest, cell, ev)] * 2
    elif bad == "bad-count": ev["security"][0]["assertions"][0]["data_points_used"] = True
    else: ev["security"] = {}
    with pytest.raises(ValueError, match=".+"):
        api.evaluate_campaign_evidence(manifest, ev)


def test_persistence_keeps_manifest_and_raw_evidence_bytes_and_separate_status(campaign, tmp_path):
    manifest_path, raw_path = tmp_path / "manifest.json", tmp_path / "retained.json"
    manifest_path.write_text(json.dumps(campaign))
    raw_path.write_text(json.dumps(evidence(campaign)))
    before = (manifest_path.read_bytes(), raw_path.read_bytes())
    paths = api.persist_campaign_evaluation(manifest_path, raw_path)
    assert set(paths) == {"security_validity", "measurement_completeness"}
    records = {key: json.loads(path.read_text()) for key, path in paths.items()}
    assert records["security_validity"]["classification"] == "pass"
    assert (manifest_path.read_bytes(), raw_path.read_bytes()) == before
    status = planner.campaign_evaluation_status(campaign, records)
    assert status["campaign_digest"] == campaign["campaign_digest"]
    assert status["security_result"] == "pass"
    assert status["measurement_completeness"] == "incomplete"
    assert api.persist_campaign_evaluation(manifest_path, raw_path) == paths


def test_strict_json_reader_rejects_duplicate_keys(campaign, tmp_path):
    manifest_path, raw_path = tmp_path / "manifest.json", tmp_path / "raw.json"
    manifest_path.write_text(json.dumps(campaign))
    raw_path.write_text('{"security": [], "security": []}')
    with pytest.raises(ValueError, match="duplicate"):
        api.persist_campaign_evaluation(manifest_path, raw_path)


def test_persistence_refuses_an_output_alias_to_raw_evidence(campaign, tmp_path):
    manifest_path, raw_path = tmp_path / "manifest.json", tmp_path / "raw.json"
    manifest_path.write_text(json.dumps(campaign))
    raw_path.write_text(json.dumps(evidence(campaign)))
    output = tmp_path / f"{campaign['campaign_id']}.security-validity.json"
    output.symlink_to(raw_path)
    before = raw_path.read_bytes()
    with pytest.raises(ValueError, match="aliases"):
        api.persist_campaign_evaluation(manifest_path, raw_path)
    assert raw_path.read_bytes() == before


@pytest.mark.parametrize("mutation", ["mixed", "missing-digest"])
def test_status_projection_rejects_invalid_evidence_records(campaign, mutation):
    result = api.evaluate_campaign_evidence(campaign, evidence(campaign))
    if mutation == "mixed":
        result["measurement_completeness"]["evidence_digest"] = D
    else:
        del result["security_validity"]["evidence_digest"]
        del result["measurement_completeness"]["evidence_digest"]
    with pytest.raises(ValueError, match="evidence"):
        planner.campaign_evaluation_status(campaign, result)


def test_release_executable_observation_is_exactly_shared_with_normal_control(campaign):
    node = campaign["topologies"][0]["nodes"][0]
    node["identity"].update(kind="release", source_tree_digest=None, qualification={"status": "unknown"},
                            artifacts={"image_digest": D, "binary_digest": None, "executable_digest": None})
    manifest = freeze(campaign)
    ev = evidence(manifest)
    assert security_result(manifest, ev)["classification"] == "pass"
    ev["security"][0]["normal_control"]["targets"]["node1"]["executable_digest"] = "sha256:" + "f" * 64
    assert security_result(manifest, ev)["classification"] == "harness-invalid"


@pytest.mark.parametrize("bad", ["null-field", "object-outcome", "event-node", "unknown-status-in-blocked-cell"])
def test_malformed_nested_evidence_has_actionable_value_errors(campaign, bad):
    manifest, cell = single_cell(campaign)
    ev = evidence(manifest)
    row = measurement(manifest, cell, ev)
    ev["measurements"] = [row]
    if bad == "null-field":
        ev["security"][0]["reachability"][0]["node_id"] = None
    elif bad == "object-outcome":
        row["events"] = [{"event": "terminal", "run_id": "measurement-1", "outcome": {}}]
    elif bad == "event-node":
        row["events"] = [{"event": "terminal", "run_id": "measurement-1", "node_id": []}]
    else:
        cell["runtime"] = {"status": "missing", "reason": "no runtime"}
        manifest = freeze(manifest)
        ev["campaign_digest"] = manifest["campaign_digest"]
        row["result"]["status"] = "invented-status"
    with pytest.raises(ValueError, match=".+"):
        api.evaluate_campaign_evidence(manifest, ev)


@pytest.mark.parametrize("missing", ["topology", "scenario", "node", "build-mode", "unknown-rule", "malformed-progress"])
def test_frozen_manifest_cross_references_fail_clearly(campaign, missing):
    ev = evidence(campaign)
    if missing == "topology": campaign["topologies"] = []
    elif missing == "scenario": del campaign["definitions"]["scenarios"][campaign["entries"][0]["contract"]["scenario_id"]]
    elif missing == "node": campaign["required_tap_cells"][0]["node_id"] = "unknown-node"
    elif missing == "build-mode": campaign["build_variants"] = [v for v in campaign["build_variants"] if v["mode"] != "stock"]
    elif missing == "unknown-rule": campaign["required_tap_cells"][0]["rule"] = {"invented_predicate": True}
    else: campaign["required_tap_cells"][0]["rule"] = {"progress": "unknown"}
    freeze(campaign)
    ev["campaign_digest"] = campaign["campaign_digest"]
    with pytest.raises(ValueError, match=".+"):
        api.evaluate_campaign_evidence(campaign, ev)


def native_fuzz_evidence(manifest):
    """Real scenario declarations and the failure-only lists emitted by primitives."""
    scenario_id = manifest["entries"][0]["contract"]["scenario_id"]
    definition = manifest["definitions"]["scenarios"][scenario_id]
    definition["document"]["assertions"] = [
        {"primitive": "parse_succeeds_or_clean_error", "min_outcomes_count": 2},
        {"primitive": "roundtrip_equals_original", "min_inputs_parsed": 2},
    ]
    definition["digest"] = planner._digest(definition["document"])
    freeze(manifest)
    ev = evidence(manifest)
    ev["security"][0]["assertions"] = [
        {"primitive": "parse_succeeds_or_clean_error", "params": {"min_outcomes_count": 2},
         "evaluated_value": {"ok": 2, "clean_error": 3, "crash": 0, "total_outcomes": 5, "min_outcomes_count": 2},
         "data_points_used": [], "result": "pass"},
        {"primitive": "roundtrip_equals_original", "params": {"min_inputs_parsed": 2},
         "evaluated_value": {"ok": 2, "matched": 2, "mismatched": 0, "min_inputs_parsed": 2},
         "data_points_used": [], "result": "pass", "note": None},
    ]
    return ev


def test_native_zero_crash_and_zero_mismatch_fuzz_pass_is_nonvacuous(campaign):
    ev = native_fuzz_evidence(campaign)
    before = deepcopy(ev)
    assert security_result(campaign, ev)["classification"] == "pass"
    assert ev == before


def test_flattened_scenario_parameters_match_retained_numeric_count_records(campaign):
    ev = native_fuzz_evidence(campaign)
    for assertion in ev["security"][0]["assertions"]:
        assertion["data_points_used"] = 1
    assert security_result(campaign, ev)["classification"] == "pass"


@pytest.mark.parametrize("primitive", ["parse_succeeds_or_clean_error", "roundtrip_equals_original"])
def test_native_failure_only_data_point_lists_can_establish_a_finding(campaign, primitive):
    ev = native_fuzz_evidence(campaign)
    assertion = next(row for row in ev["security"][0]["assertions"] if row["primitive"] == primitive)
    assertion["result"] = "fail"
    if primitive == "parse_succeeds_or_clean_error":
        assertion["evaluated_value"].update(crash=1, total_outcomes=6)
        assertion["data_points_used"] = [{"input_id": "fuzz-input-5", "size": 7}]
    else:
        assertion["evaluated_value"].update(matched=1, mismatched=1)
        assertion["data_points_used"] = [{"input_id": "fuzz-input-1", "input_hex": "00", "reencoded_hex": "01"}]
    assert security_result(campaign, ev)["classification"] == "finding"


@pytest.mark.parametrize("primitive", ["parse_succeeds_or_clean_error", "roundtrip_equals_original"])
@pytest.mark.parametrize("mutation", ["changed", "omitted", "extra"])
def test_native_assertion_parameters_bind_flattened_frozen_declarations(campaign, primitive, mutation):
    ev = native_fuzz_evidence(campaign)
    assert security_result(campaign, ev)["classification"] == "pass"
    assertion = next(row for row in ev["security"][0]["assertions"] if row["primitive"] == primitive)
    if mutation == "omitted": assertion["params"] = {}
    elif mutation == "extra": assertion["params"]["unused"] = "unexpected"
    else: assertion["params"][next(iter(assertion["params"]))] = 3
    assert security_result(campaign, ev)["classification"] == "harness-invalid"


@pytest.mark.parametrize("mutation", ["zero-outcomes", "too-few-outcomes", "zero-parsed", "wrong-total", "wrong-minimum", "missing-crash-point"])
def test_native_fuzz_counters_must_prove_the_declared_nonvacuity(campaign, mutation):
    ev = native_fuzz_evidence(campaign)
    parse, roundtrip = ev["security"][0]["assertions"]
    if mutation == "zero-outcomes": parse["evaluated_value"].update(ok=0, clean_error=0, total_outcomes=0)
    elif mutation == "too-few-outcomes": parse["evaluated_value"].update(ok=1, clean_error=0, total_outcomes=1)
    elif mutation == "zero-parsed": roundtrip["evaluated_value"].update(ok=0, matched=0)
    elif mutation == "wrong-total": parse["evaluated_value"]["total_outcomes"] = 500
    elif mutation == "wrong-minimum": parse["evaluated_value"]["min_outcomes_count"] = 1
    else:
        parse.update(result="fail", evaluated_value={"ok": 2, "clean_error": 3, "crash": 1, "total_outcomes": 6, "min_outcomes_count": 2})
    assert security_result(campaign, ev)["classification"] == "inconclusive"


@pytest.mark.parametrize("points", [{}, ["input-1"], [None], True])
def test_malformed_native_data_point_lists_remain_errors(campaign, points):
    ev = native_fuzz_evidence(campaign)
    ev["security"][0]["assertions"][0]["data_points_used"] = points
    with pytest.raises(ValueError, match="data_points_used"):
        security_result(campaign, ev)


def test_native_assertion_list_is_also_valid_normal_control_evidence(campaign):
    ev = native_fuzz_evidence(campaign)
    ev["security"][0]["normal_control"]["evidence"] = deepcopy(ev["security"][0]["assertions"])
    assert security_result(campaign, ev)["classification"] == "pass"


def native_load_evidence(manifest, params=None):
    params = {} if params is None else params
    scenario_id = manifest["entries"][0]["contract"]["scenario_id"]
    definition = manifest["definitions"]["scenarios"][scenario_id]
    definition["document"]["assertions"] = [{"primitive": "load_events_are_ok", **params}]
    definition["digest"] = planner._digest(definition["document"])
    freeze(manifest)
    ev = evidence(manifest)
    minimum, event_count = params.get("min_completed", 1), params.get("min_event_count", 2)
    ev["security"][0]["assertions"] = [{
        "primitive": "load_events_are_ok", "params": deepcopy(params),
        "evaluated_value": {"completed": max(minimum, event_count), "non_ok": 0,
                            "min_completed": minimum, "min_event_count": event_count,
                            "required_completed": max(minimum, event_count)},
        "data_points_used": [], "result": "pass", "note": None,
    }]
    return ev


@pytest.mark.parametrize("params", [{}, {"min_completed": 3, "min_event_count": 2}, {"min_completed": 1, "min_event_count": 4}])
def test_native_load_success_uses_completed_population_and_frozen_minimums(campaign, params):
    ev = native_load_evidence(campaign, params)
    before = deepcopy(ev)
    assert security_result(campaign, ev)["classification"] == "pass"
    assert ev == before


def test_native_load_failure_with_sufficient_completed_events_is_a_finding(campaign):
    ev = native_load_evidence(campaign)
    assertion = ev["security"][0]["assertions"][0]
    assertion["evaluated_value"]["non_ok"] = 1
    assertion.update(result="fail", data_points_used=[{"outcome": "crash", "exit_code": 1}])
    assert security_result(campaign, ev)["classification"] == "finding"


@pytest.mark.parametrize("mutation", ["zero", "insufficient", "insufficient-with-failure", "required-count", "minimum", "missing-failure", "excess-failure", "ok-point", "wrong-result"])
def test_native_load_inadequate_or_inconsistent_evidence_is_inconclusive(campaign, mutation):
    ev = native_load_evidence(campaign)
    assertion = ev["security"][0]["assertions"][0]
    counters = assertion["evaluated_value"]
    if mutation in {"zero", "insufficient"}:
        counters["completed"] = 0 if mutation == "zero" else 1
        assertion.update(result="fail", note="expected at least 2 load completed events with outcomes")
    elif mutation == "insufficient-with-failure":
        counters.update(completed=1, non_ok=1)
        assertion.update(result="fail", data_points_used=[{"outcome": "crash", "exit_code": 1}])
    elif mutation == "required-count": counters["required_completed"] = 1
    elif mutation == "minimum": counters["min_completed"] = 2
    elif mutation == "wrong-result": assertion["result"] = "fail"
    else:
        counters["non_ok"] = 1
        assertion["result"] = "fail"
        if mutation == "excess-failure":
            assertion["data_points_used"] = [{"outcome": "crash", "exit_code": 1}] * 2
        elif mutation == "ok-point":
            assertion["data_points_used"] = [{"outcome": "ok", "exit_code": 0}]
    assert security_result(campaign, ev)["classification"] == "inconclusive"


@pytest.mark.parametrize("malformed", [True, -1, "2", None])
def test_native_load_malformed_completed_counts_remain_input_errors(campaign, malformed):
    ev = native_load_evidence(campaign)
    ev["security"][0]["assertions"][0]["evaluated_value"]["completed"] = malformed
    with pytest.raises(ValueError, match="completed"):
        security_result(campaign, ev)


@pytest.mark.parametrize("completed,expected", [(2, "pass"), (1, "harness-invalid")])
def test_native_load_normal_control_requires_sufficient_completed_population(campaign, completed, expected):
    ev = native_load_evidence(campaign)
    control_assertion = deepcopy(ev["security"][0]["assertions"][0])
    control_assertion["evaluated_value"]["completed"] = completed
    ev["security"][0]["normal_control"]["evidence"] = [control_assertion]
    assert security_result(campaign, ev)["classification"] == expected


@pytest.mark.parametrize("primitive", ["load_events_are_ok", "parse_succeeds_or_clean_error", "roundtrip_equals_original"])
@pytest.mark.parametrize("mutation", ["valid", "zero-population", "wrong-minimum", "wrong-result"])
def test_numeric_points_cannot_override_supplied_native_counters(campaign, primitive, mutation):
    ev = native_load_evidence(campaign) if primitive == "load_events_are_ok" else native_fuzz_evidence(campaign)
    assertion = next(row for row in ev["security"][0]["assertions"] if row["primitive"] == primitive)
    assertion["data_points_used"] = 1
    counters = assertion["evaluated_value"]
    if mutation == "zero-population":
        assertion["result"] = "fail"
        if primitive == "load_events_are_ok": counters["completed"] = 0
        elif primitive == "parse_succeeds_or_clean_error": counters.update(ok=0, clean_error=0, total_outcomes=0)
        else: counters.update(ok=0, matched=0)
    elif mutation == "wrong-minimum":
        key = {"load_events_are_ok": "required_completed", "parse_succeeds_or_clean_error": "min_outcomes_count", "roundtrip_equals_original": "min_inputs_parsed"}[primitive]
        counters[key] = 99
    elif mutation == "wrong-result": assertion["result"] = "fail"
    assert security_result(campaign, ev)["classification"] == ("pass" if mutation == "valid" else "inconclusive")


def test_legacy_numeric_assertions_without_native_counter_mappings_remain_valid(campaign):
    ev = evidence(campaign)
    assert all(row["evaluated_value"] is True for row in ev["security"][0]["assertions"])
    assert security_result(campaign, ev)["classification"] == "pass"


def control_context(manifest, context):
    if context == "security":
        ev = evidence(manifest)
        row = ev["security"][0]
    else:
        manifest, cell = single_cell(manifest)
        ev = evidence(manifest)
        row = measurement(manifest, cell, ev)
        row["boundary"] = "process-observation"
        ev["measurements"] = [row]
    return ev, row, row["normal_control"]


def assert_control_state(manifest, ev, context, valid):
    result = security_result(manifest, ev) if context == "security" else cell_result(manifest, ev)
    assert result["classification" if context == "security" else "state"] == (
        "pass" if valid and context == "security" else "verified" if valid
        else "harness-invalid" if context == "security" else "incomplete")


@pytest.mark.parametrize("context", ["security", "measurement"])
@pytest.mark.parametrize("contradiction", ["result", "failed-diagnostic", "mixed-evidence", "mixed-assertions", "run_id", "node_id", "boundary", "top-node", "top-boundary", "point-run", "point-node", "point-boundary"])
def test_control_contradictions_cannot_be_overridden_by_diagnostics_or_passes(campaign, context, contradiction):
    ev, row, control = control_context(campaign, context)
    passed = {"primitive": "control_probe", "params": {}, "result": "pass", "data_points_used": 1}
    failed = {**passed, "result": "fail", "diagnostic": "looks successful"}
    if contradiction == "result": control["result"] = "fail"
    elif contradiction == "failed-diagnostic": control["evidence"] = [{"result": "fail", "diagnostic": "looks successful"}]
    elif contradiction == "mixed-evidence": control["evidence"] += [passed, failed]
    elif contradiction == "mixed-assertions": control["assertions"] = [passed, failed]
    elif contradiction.startswith("top-"):
        control["node_id" if contradiction == "top-node" else "boundary"] = "unrelated"
    elif contradiction.startswith("point-"):
        field = {"point-run": "run_id", "point-node": "node_id", "point-boundary": "boundary"}[contradiction]
        control["evidence"].append({**passed, "data_points_used": [{field: "unrelated", "value": 1}]})
    else: control["evidence"][0][contradiction] = "unrelated"
    assert_control_state(campaign, ev, context, False)


@pytest.mark.parametrize("context", ["security", "measurement"])
@pytest.mark.parametrize("bound_metadata", [False, True])
def test_controls_accept_legacy_omissions_and_correct_optional_bindings(campaign, context, bound_metadata):
    ev, row, control = control_context(campaign, context)
    if bound_metadata:
        control.update(result="pass", node_id="node1", boundary=row["boundary"])
        control["evidence"][0].update(run_id=control["run_id"], node_id="node1", boundary=row["boundary"])
    assert_control_state(campaign, ev, context, True)


def test_control_diagnostic_cannot_override_a_native_counter_contradiction(campaign):
    ev = native_load_evidence(campaign)
    control_row = deepcopy(ev["security"][0]["assertions"][0])
    control_row["evaluated_value"]["completed"] = 0
    control_row.update(data_points_used=1, diagnostic="claimed successful control")
    ev["security"][0]["normal_control"]["evidence"] = [control_row]
    assert security_result(campaign, ev)["classification"] == "harness-invalid"


@pytest.mark.parametrize("context", ["security", "measurement"])
@pytest.mark.parametrize("control_target", [False, True])
@pytest.mark.parametrize("target", [None, [], "not-a-target"])
def test_null_and_nonmapping_targets_never_raise_attribute_errors(campaign, context, control_target, target):
    ev, row, control = control_context(campaign, context)
    if control_target: control["targets"]["node1"] = target
    elif context == "security": row["targets"]["node1"] = target
    else: row["result"]["target"] = target
    if target is None:
        assert_control_state(campaign, ev, context, False)
    else:
        with pytest.raises(ValueError, match="target"):
            api.evaluate_campaign_evidence(campaign, ev)


def native_operation_control(primitive):
    """Native retained rows: outer verdict, inner operation payload/result object."""
    if primitive == "all_nodes_responsive":
        evaluated = {"completed": 1, "responsive_node_count": 1, "node_count": 1}
        payload = {"outcome": "ok", "artifact_summary": {"has_observation_summary": True},
                   "result": {"summary": {"responsive_node_count": 1, "node_count": 1},
                              "per_node": {"node1": {"process_running": True, "listening": True}}}}
    else:
        evaluated = {"completed": 1, "unexpected_body_rejected": True, "rejection_reason": "unexpected transaction body"}
        payload = {"outcome": "ok", "result": {"unexpected_body_rejected": True, "rejection_reason": "unexpected transaction body"}}
    return {"primitive": primitive, "params": {}, "evaluated_value": evaluated,
            "data_points_used": [payload], "result": "pass", "note": None}


@pytest.mark.parametrize("context", ["security", "measurement"])
@pytest.mark.parametrize("primitive", ["all_nodes_responsive", "txsubmission_unexpected_body_rejected"])
def test_native_control_operation_results_are_payloads_not_assertion_verdicts(campaign, context, primitive):
    ev, row, control = control_context(campaign, context)
    assertion = native_operation_control(primitive)
    assertion["data_points_used"][0].update(run_id=control["run_id"], node_id="node1", boundary=row["boundary"])
    control["evidence"] = [assertion]
    before = deepcopy(ev)
    assert_control_state(campaign, ev, context, True)
    assert ev == before


@pytest.mark.parametrize("primitive", ["all_nodes_responsive", "txsubmission_unexpected_body_rejected"])
@pytest.mark.parametrize("mutation", ["outer-failure", "mixed-verdicts", "control-failure", "wrong-run", "wrong-node", "wrong-boundary", "ambiguous-node"])
def test_structured_operation_results_cannot_override_control_verdicts_or_bindings(campaign, primitive, mutation):
    ev, row, control = control_context(campaign, "security")
    assertion = native_operation_control(primitive)
    point = assertion["data_points_used"][0]
    point["result"]["result"] = "pass"
    control["evidence"] = [assertion]
    if mutation == "outer-failure": assertion.update(result="fail", diagnostic="looks successful")
    elif mutation == "mixed-verdicts":
        control["assertions"] = [{**deepcopy(assertion), "result": "fail"}]
    elif mutation == "control-failure": control["result"] = "fail"
    elif mutation == "ambiguous-node": point.update(node_id="node1", correlation={"node_id": "other-node"})
    else: point[{"wrong-run": "run_id", "wrong-node": "node_id", "wrong-boundary": "boundary"}[mutation]] = "unrelated"
    if mutation == "ambiguous-node":
        with pytest.raises(ValueError, match="node_id"):
            api.evaluate_campaign_evidence(campaign, ev)
    else:
        assert_control_state(campaign, ev, "security", False)


def test_raw_operation_points_alone_do_not_establish_a_control_verdict(campaign):
    ev, row, control = control_context(campaign, "security")
    control["evidence"] = []
    control["data_points_used"] = native_operation_control("all_nodes_responsive")["data_points_used"]
    assert_control_state(campaign, ev, "security", False)


def test_structured_result_is_still_invalid_at_the_actual_assertion_verdict_layer(campaign):
    ev, row, control = control_context(campaign, "security")
    assertion = native_operation_control("txsubmission_unexpected_body_rejected")
    assertion["result"] = {"result": "pass", "diagnostic": "not an assertion verdict"}
    control["evidence"] = [assertion]
    with pytest.raises(ValueError, match="result"):
        api.evaluate_campaign_evidence(campaign, ev)


def test_library_evidence_uses_exact_executable_without_relaxing_devnet_images(campaign):
    ev = evidence(campaign)
    for targets in (ev["security"][0]["targets"], ev["security"][0]["normal_control"]["targets"]):
        target = targets["node1"]
        target.pop("image_digest")
        target.update(runtime="library", target_id="amaru-cbor-decode-tx-body", library_manifest_digest="sha256:" + "f" * 64)
    assert api.evaluate_campaign_evidence(campaign, ev)["security_validity"]["classification"] == "pass"
    campaign["topologies"][0]["mode"] = "shared-runtime"
    freeze(campaign)
    ev["campaign_digest"] = campaign["campaign_digest"]
    assert api.evaluate_campaign_evidence(campaign, ev)["security_validity"]["classification"] == "harness-invalid"


@pytest.fixture(scope="module")
def mixed_mode_plan():
    """Use the planner's actual patched candidate + stock release contract."""
    suite = load_security_campaign_suite(ROOT / "dwarf/security-suites/github-security-regression-v1.yaml")
    scenario_id = "ledger-submit-transaction-array-arity-differential-amaru-cardano-node"
    entry = next(e for group in suite["groups"] for e in group["entries"] if e["id"] == scenario_id)
    entry["measurement_attachments"][0].update(
        target_mode="patched", measurement_profile="amaru-security-patched",
        result_namespace="nodes/node1/measurements/patched")
    return planner.resolve_security_campaign(
        CandidateSnapshot("amaru", "proposal", "a" * 40, "b" * 64, Path("/unused")),
        suite, load_measurement_suite(ROOT / "dwarf/qualification-suites/node-measurements-v1.yaml"),
        selection={"entry_ids": [scenario_id], "implementations": {
            "amaru": {"kind": "candidate", "version": "proposal", "compatibility": COMPAT},
            "cardano-node": {"kind": "release", "version": "99.0.0", "compatibility": COMPAT}},
            "build_variants": {"patched": {"patch_set_digest": D}}},
        catalog={"schema_version": 1, "updated_at": "2026-10-19T00:00:00Z", "compatibility_pairs": [],
                 "releases": [{"implementation": "cardano-node", "version": "99.0.0", "channel": "stable",
                               "source_revision": "c" * 40, "verification": {}, "artifacts": [{
                                   "kind": "oci", "reference": "example.invalid/cardano:99.0.0",
                                   "digest": "sha256:" + "c" * 64, "availability": "available"}]}]})


def mixed_mode_evidence(manifest):
    retained = evidence(manifest)
    stock = next(build for build in retained["builds"] if build["mode"] == "stock")
    stock["artifacts"] = {key: "sha256:" + "f" * 64 for key in stock["artifacts"]}
    row = retained["security"][0]
    row["targets"]["node1"].update(mode="patched", patch_set_sha256="d" * 64)
    row["targets"]["node2"] = {
        "implementation": "cardano-node", "version": "99.0.0", "source_revision": "c" * 40,
        "mode": "stock", "image_digest": "sha256:" + "c" * 64,
        "executable_digest": "sha256:" + "e" * 64}
    row["normal_control"]["targets"] = deepcopy(row["targets"])
    peer_observation = {**row["reachability"][0], "node_id": "node2"}
    row["reachability"].append(deepcopy(peer_observation))
    row["oracle"]["observations"].append(peer_observation)
    row["normal_control"]["evidence"] = [{**observation, "run_id": row["normal_control"]["run_id"]}
                                          for observation in row["reachability"]]
    return retained


@pytest.mark.parametrize("row_mode", ["stock", "patched"])
def test_security_and_control_validate_each_planner_frozen_node_mode(mixed_mode_plan, row_mode):
    retained = mixed_mode_evidence(mixed_mode_plan)
    # Only the patched candidate needs a build observation. The stock release
    # peer has its frozen catalog artifact and no instrumentation prerequisites.
    retained["builds"] = [build for build in retained["builds"] if build["mode"] == "patched"]
    retained["security"][0]["mode"] = row_mode
    before = deepcopy((mixed_mode_plan, retained))
    result = security_result(mixed_mode_plan, retained)
    assert result["classification"] == "pass", result["reasons"]
    assert (mixed_mode_plan, retained) == before


def test_all_stock_security_and_control_cannot_replace_frozen_patched_node(mixed_mode_plan):
    retained = mixed_mode_evidence(mixed_mode_plan)
    stock = next(build for build in retained["builds"] if build["mode"] == "stock")
    row = retained["security"][0]
    for targets in (row["targets"], row["normal_control"]["targets"]):
        targets["node1"].update(mode="stock", **stock["artifacts"])
        targets["node1"].pop("patch_set_sha256")
    result = security_result(mixed_mode_plan, retained)
    assert result["classification"] == "harness-invalid"
    assert "target mode differs for node1" in result["reasons"]


@pytest.mark.parametrize("mutation", ["control-stock", "peer-patched", "missing-build", "target-patch", "control-patch"])
def test_mixed_security_preserves_per_node_build_and_control_checks(mixed_mode_plan, mutation):
    retained = mixed_mode_evidence(mixed_mode_plan)
    row = retained["security"][0]
    if mutation == "control-stock":
        row["normal_control"]["targets"]["node1"]["mode"] = "stock"
    elif mutation == "peer-patched":
        row["targets"]["node2"]["mode"] = "patched"
    elif mutation == "missing-build":
        retained["builds"] = [build for build in retained["builds"] if build["mode"] != "patched"]
    else:
        targets = row["targets"] if mutation == "target-patch" else row["normal_control"]["targets"]
        del targets["node1"]["patch_set_sha256"]
    assert security_result(mixed_mode_plan, retained)["classification"] == "harness-invalid"


@pytest.mark.parametrize("mode", ["stock", "patched", "coverage"])
def test_historical_manifest_without_node_modes_retains_row_mode_fallback(campaign, mode):
    campaign["topologies"][0]["nodes"][0]["identity"].pop("mode")
    freeze(campaign)
    retained = evidence(campaign)
    row = retained["security"][0]
    row["mode"] = mode
    for targets in (row["targets"], row["normal_control"]["targets"]):
        targets["node1"]["mode"] = mode
        if mode == "patched":
            targets["node1"]["patch_set_sha256"] = "d" * 64
        elif mode == "coverage":
            targets["node1"]["coverage_harness_sha256"] = "d" * 64
    result = security_result(campaign, retained)
    assert result["classification"] == "pass", result["reasons"]


@pytest.mark.parametrize('location', ['security', 'control'])
@pytest.mark.parametrize('digest', [None, 'sha256:' + '0' * 64])
def test_security_requires_exact_execution_digest(campaign, location, digest):
    retained = evidence(campaign)
    row = retained['security'][0]
    (row if location == 'security' else row['normal_control'])['execution_digest'] = digest
    result = security_result(campaign, retained)
    assert result['classification'] == 'harness-invalid'
    assert any('execution' in reason for reason in result['reasons'])


@pytest.mark.parametrize('status', ['pending-prerequisites', None])
def test_pending_or_missing_execution_compatibility_cannot_pass(campaign, status):
    execution = campaign['entries'][0]['execution']
    if status is None:
        execution.pop('compatibility')
    else:
        execution['compatibility']['status'] = status
    freeze(campaign)
    if status is None:
        with pytest.raises(ValueError, match="compatibility.*required"):
            security_result(campaign, evidence(campaign))
    else:
        result = security_result(campaign, evidence(campaign))
        assert result['classification'] == 'inconclusive'
        assert any('compatibility' in reason for reason in result['reasons'])


@pytest.mark.parametrize('shared', [True, False])
def test_mixed_targets_require_one_shared_input_at_the_same_boundary(mixed_mode_plan, shared):
    retained = mixed_mode_evidence(mixed_mode_plan)
    row = retained['security'][0]
    for observations in (row['reachability'], row['oracle']['observations']):
        if not shared:
            observations[1]['input_digest'] = 'sha256:' + '9' * 64
        # Irrelevant participants never satisfy or invalidate the target join.
        observations.append({**observations[0], 'node_id': 'observer', 'input_digest': 'sha256:' + '8' * 64})
    result = security_result(mixed_mode_plan, retained)
    assert result['classification'] == ('pass' if shared else 'inconclusive'), result['reasons']
    if not shared:
        assert any('shared input' in reason for reason in result['reasons'])


def test_declared_compatibility_cannot_hide_a_missing_node_prerequisite(campaign):
    campaign['topologies'][0]['nodes'][0]['identity']['compatibility'].pop('protocol_version')
    freeze(campaign)
    result = security_result(campaign, evidence(campaign))
    assert result['classification'] == 'harness-invalid'
    assert any('compatibility' in reason for reason in result['reasons'])


def test_historical_single_node_without_execution_binding_preserves_legacy_pass(campaign):
    retained = evidence(campaign)
    campaign['entries'][0].pop('execution')
    retained['security'][0].pop('execution_digest')
    retained['security'][0]['normal_control'].pop('execution_digest')
    freeze(campaign)
    retained['campaign_digest'] = campaign['campaign_digest']
    result = security_result(campaign, retained)
    assert result['classification'] == 'pass'


@pytest.mark.parametrize('mutation', ['shared', 'generic', 'one-node', 'different-input', 'wrong-run', 'wrong-boundary', 'outer-implementation'])
def test_mixed_execution_control_requires_one_shared_bound_input(mixed_mode_plan, mutation):
    retained = mixed_mode_evidence(mixed_mode_plan)
    row = retained['security'][0]
    control = row['normal_control']
    control['evidence'] = [{**row['reachability'][0], 'run_id': control['run_id'], 'node_id': node}
                           for node in mixed_mode_plan['entries'][0]['contract']['target_nodes']]
    if mutation == 'generic': control['evidence'] = [{'diagnostic': 'accepted control'}]
    elif mutation == 'one-node': control['evidence'] = control['evidence'][:1]
    elif mutation == 'different-input': control['evidence'][1]['input_digest'] = 'sha256:' + '8' * 64
    elif mutation == 'wrong-run': control['evidence'][1]['run_id'] = 'another-control'
    elif mutation == 'wrong-boundary': control['evidence'][1]['boundary'] = 'other'
    elif mutation == 'outer-implementation':
        mixed_mode_plan['topologies'][0]['nodes'][1]['implementation'] = 'amaru'
        control['evidence'] = [{'diagnostic': 'accepted control'}]
        freeze(mixed_mode_plan)
        retained['campaign_digest'] = mixed_mode_plan['campaign_digest']
    result = security_result(mixed_mode_plan, retained)
    assert result['classification'] == ('pass' if mutation == 'shared' else 'harness-invalid'), result['reasons']
    if mutation in {'generic', 'one-node', 'different-input'}:
        assert any('normal control' in reason and 'shared input' in reason for reason in result['reasons'])


def test_legacy_mixed_control_keeps_generic_diagnostic_behavior(mixed_mode_plan):
    retained = mixed_mode_evidence(mixed_mode_plan)
    mixed_mode_plan['entries'][0].pop('execution')
    freeze(mixed_mode_plan)
    retained['campaign_digest'] = mixed_mode_plan['campaign_digest']
    row = retained['security'][0]
    row.pop('execution_digest')
    row['normal_control'].pop('execution_digest')
    row['normal_control']['evidence'] = [{'diagnostic': 'accepted historical control'}]
    assert security_result(mixed_mode_plan, retained)['classification'] == 'pass'
