import json
from pathlib import Path

from jsonschema import validate

from profile_manager import forensic, testcase_lifecycle
from profile_manager.data.operate_run import operate_run_detail
from profile_manager.templating import render


ROOT = Path(__file__).resolve().parents[1]
SARIF_SCHEMA = json.loads(
    (ROOT / "dwarf/spec/sarif-schema-2.1.0.json").read_text(encoding="utf-8")
)
RUN_TEMPLATE = ROOT / "dwarf/dashboard/templates/operate/run.j2"


def _finished_run(tmp_path: Path, *, assertion_result: str | None = None):
    runs_dir = tmp_path / "runs"
    state_dir = tmp_path / "state"
    scenario = b'{"spec_version":"v1","id":"evidence-default-test"}\n'
    handle = forensic.start_run(
        scenario_id="evidence-default-test",
        scenario_yaml=scenario,
        target={"implementation": "amaru", "version": "test"},
        runtime="library",
        profile_id=None,
        profile_resolved=None,
        framework_version="test",
        framework_commit="test",
        seed=7,
        runs_dir=runs_dir,
        state_dir=state_dir,
    )
    if assertion_result is not None:
        handle.assertion_result(
            primitive="example_assertion",
            params={"minimum": 1},
            evaluated_value={"actual": 0},
            data_points_used=1,
            result=assertion_result,
            note="expected at least one observed event",
        )
    handle.end(exit_status="pass" if assertion_result != "fail" else "fail")
    return runs_dir, state_dir, handle.run_id


def test_every_completed_run_gets_schema_valid_automatic_sarif(tmp_path):
    runs_dir, _state_dir, run_id = _finished_run(tmp_path)
    sarif_path = runs_dir / run_id / "outputs/sarif-export/dwarf-export.sarif"
    result_path = runs_dir / run_id / "outputs/sarif-export/result.json"

    sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
    result = json.loads(result_path.read_text(encoding="utf-8"))
    validate(instance=sarif, schema=SARIF_SCHEMA)
    assert sarif["runs"][0]["results"] == []
    assert result["generation"] == "automatic-run-finalization"
    assert result["schema_valid"] is True


def test_automatic_sarif_maps_failed_assertions_as_findings(tmp_path):
    runs_dir, _state_dir, run_id = _finished_run(tmp_path, assertion_result="fail")
    sarif = json.loads(
        (runs_dir / run_id / "outputs/sarif-export/dwarf-export.sarif").read_text(
            encoding="utf-8"
        )
    )

    result = sarif["runs"][0]["results"][0]
    assert result["ruleId"] == "dwarf.assertion.example_assertion"
    assert result["level"] == "error"
    assert "expected at least one observed event" in result["message"]["text"]


def test_missing_historic_predecessor_is_incomplete_not_tampering(tmp_path):
    runs_dir, state_dir, run_id = _finished_run(tmp_path)
    run_dir = runs_dir / run_id
    chain = json.loads((run_dir / "chain.json").read_text(encoding="utf-8"))
    chain["prev_hash"] = "a" * 64
    (run_dir / "chain.json").write_text(json.dumps(chain), encoding="utf-8")

    detail = operate_run_detail(run_id, runs_dir=runs_dir)

    assert detail is not None
    assert detail["verify"]["status"] == "incomplete"
    assert detail["verify"]["label"] == "INCOMPLETE"


def test_manifest_digest_mismatch_remains_a_tamper_failure(tmp_path):
    runs_dir, _state_dir, run_id = _finished_run(tmp_path)
    manifest_path = runs_dir / run_id / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["actor"] = "changed-after-run"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    detail = operate_run_detail(run_id, runs_dir=runs_dir)

    assert detail is not None
    assert detail["verify"]["status"] == "fail"
    assert detail["verify"]["label"] == "FAIL"


def test_new_run_reroots_instead_of_inheriting_unverifiable_history(tmp_path):
    runs_dir, state_dir, first_id = _finished_run(tmp_path)
    first_chain_path = runs_dir / first_id / "chain.json"
    first_chain = json.loads(first_chain_path.read_text(encoding="utf-8"))
    first_chain["prev_hash"] = "b" * 64
    first_chain_path.write_text(json.dumps(first_chain), encoding="utf-8")

    runs_dir, state_dir, second_id = _finished_run(tmp_path)
    second_chain = json.loads(
        (runs_dir / second_id / "chain.json").read_text(encoding="utf-8")
    )

    assert second_chain["prev_hash"] == "genesis"
    assert second_chain["continuity"] == "re-rooted"
    assert second_chain["continuity_reason"] == "previous-head-unverifiable"
    assert second_chain["previous_head_run_id"] == first_id
    assert forensic.verify(second_id, runs_dir=runs_dir, state_dir=state_dir).ok


def test_run_page_reports_sarif_generation_origin_truthfully():
    template = RUN_TEMPLATE.read_text(encoding="utf-8")

    assert "run.export.generation == 'automatic-run-finalization'" in template
    assert "regenerated explicitly" in template


def test_run_inspector_groups_exact_primitives_by_phase(tmp_path):
    runs_dir, _state_dir, run_id = _finished_run(tmp_path)
    (runs_dir / run_id / "scenario.yaml").write_text(
        """\
spec_version: v1
id: evidence-default-test
attach:
  topology: cardano_amaru
setup:
  - primitive: runtime_attach_topology
load:
  - primitive: runtime_tracer_capture
  - primitive: runtime_multi_node_observation
assertions:
  - primitive: chain_select_differential
""",
        encoding="utf-8",
    )

    detail = operate_run_detail(run_id, runs_dir=runs_dir)

    assert detail is not None
    assert detail["profile_id"] is None
    assert detail["execution"] == {"kind": "attach", "label": "attach", "value": "cardano_amaru"}
    assert [phase["label"] for phase in detail["phase_inventory"]] == ["setup", "load", "assert"]
    html = render(
        "operate/run.j2",
        page_title=f"Run · {run_id}",
        density="reading",
        active="operate",
        active_sub="runs",
        run=detail,
    )
    assert "Execution definition" in html
    assert "cardano_amaru" in html
    assert "{&#39;id&#39;: None" not in html
    for primitive in (
        "runtime_attach_topology",
        "runtime_tracer_capture",
        "runtime_multi_node_observation",
        "chain_select_differential",
    ):
        assert primitive in html


def test_run_detail_includes_matching_lifecycle_evidence(tmp_path):
    runs_dir, state_dir, run_id = _finished_run(tmp_path)
    testcase_lifecycle.ingest_run_issue(
        runs_dir=runs_dir,
        state_dir=state_dir,
        run_id=run_id,
        classification="crash",
        triage_reason="decoder-abort",
        producer="scenario",
        source_artifact_path="manifest.json",
    )

    detail = operate_run_detail(run_id, runs_dir=runs_dir)

    assert detail is not None
    assert len(detail["interesting_evidence"]) == 1
    evidence = detail["interesting_evidence"][0]
    assert evidence["source_run_id"] == run_id
    assert evidence["classification"] == "crash"
    assert evidence["triage_reason"] == "decoder-abort"
    assert evidence["artifact_available"] is True
    assert detail["evidence_tags"] == ["crash"]
    html = render(
        "operate/run.j2",
        page_title=f"Run · {run_id}",
        density="reading",
        active="operate",
        active_sub="runs",
        run=detail,
    )
    assert "Interesting evidence" in html
    assert "decoder-abort" in html
    assert f'/api/assets/testcases/{evidence["id"]}/artifact' in html
    assert f'/operate/testcases/{evidence["id"]}' in html


def test_run_without_lifecycle_evidence_omits_interesting_evidence_section(tmp_path):
    runs_dir, _state_dir, run_id = _finished_run(tmp_path)

    detail = operate_run_detail(run_id, runs_dir=runs_dir)

    assert detail is not None
    html = render(
        "operate/run.j2",
        page_title=f"Run · {run_id}",
        density="reading",
        active="operate",
        active_sub="runs",
        run=detail,
    )
    assert "Interesting evidence" not in html
