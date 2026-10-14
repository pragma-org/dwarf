import base64
import json
from pathlib import Path
import subprocess
from urllib.parse import urlencode

from profile_manager import dashboard, scenario
from profile_manager.data.compare import _comparison_from_run, aggregate_strip
from profile_manager.data.operate_compare import _read_comparison_summary
from profile_manager.data.scenarios import invalidate_scenario_cache
from scripts import runtime_cardano_cbor_dataset_differential as dataset_runtime


def _scenario(path: Path, *, load=None) -> Path:
    path.write_text(
        json.dumps(
            {
                "spec_version": "v1",
                "id": "compare-fixture",
                "title": "Compare fixture",
                "authors": ["dwarf"],
                "target": {"implementation": "amaru", "version": "any"},
                "runtime": "library",
                "seed": 1,
                "load": load or [],
                "assertions": [],
                "teardown": [],
            }
        ),
        encoding="utf-8",
    )
    return path


def _qualified_dataset_load(tmp_path: Path) -> dict:
    repo = tmp_path / "dataset-repo"
    dataset = repo / "dataset" / "conway-123-100"
    for category in dataset_runtime.REQUIRED_CATEGORIES:
        category_dir = dataset / "plutus_data" / category
        category_dir.mkdir(parents=True, exist_ok=True)
        (category_dir / "00001.cbor").write_bytes(b"fixture")
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    for target_id in ("cardano-node-target", "amaru-target"):
        binary = tmp_path / target_id
        binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        binary.chmod(0o755)
        (manifests / f"{target_id}.yaml").write_text(
            json.dumps(
                {
                    "id": target_id,
                    "binary": str(binary),
                    "input_format": "stdin_bytes",
                    "implementation": target_id.split("-target")[0],
                    "language": "fixture",
                    "upstream_commit": "fixture",
                }
            ),
            encoding="utf-8",
        )
    verifier = tmp_path / "verifier"
    verifier.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    verifier.chmod(0o755)
    return {
        "primitive": "runtime_cardano_cbor_dataset_differential",
        "dataset_repo_dir": "$DWARF_CARDANO_CBOR_DATASET_REPO",
        "dataset_dir": "$DWARF_CARDANO_CBOR_DATASET_REPO/dataset/conway-123-100",
        "expected_source_revision": dataset_runtime.QUALIFIED_DATASET_REVISION,
        "source_repository": dataset_runtime.QUALIFIED_DATASET_REPOSITORY,
        "era": "conway",
        "rule": "plutus_data",
        "samples_per_category": 1,
        "manifests_dir": str(manifests),
        "reference_target_id": "cardano-node-target",
        "candidate_target_id": "amaru-target",
        "reference_verifier_binary": str(verifier),
    }


def test_compare_preflight_blocks_unconfigured_qualified_dataset_before_dispatch(
    tmp_path, monkeypatch
):
    scenario_path = _scenario(
        tmp_path / "dataset.yaml",
        load=[
            {
                "primitive": "runtime_cardano_cbor_dataset_differential",
                "dataset_repo_dir": "$DWARF_CARDANO_CBOR_DATASET_REPO",
                "dataset_dir": "$DWARF_CARDANO_CBOR_DATASET_REPO/dataset/conway-123-100",
            }
        ],
    )
    monkeypatch.delenv("DWARF_CARDANO_CBOR_DATASET_REPO", raising=False)
    built = []

    response = dashboard.dispatch_mutating_request(
        method="POST",
        path="/api/scenario/compare",
        body=urlencode({"token": "secret", "path": str(scenario_path)}).encode(),
        expected_token="secret",
        cli_command_builder=lambda *args, **kwargs: built.append((args, kwargs)),
    )

    assert response is not None
    status, content_type, body = response
    assert status == 409
    assert content_type == "application/json; charset=utf-8"
    payload = json.loads(body)
    assert payload["error"] == "compare-requirement-unavailable"
    assert payload["requirement"] == "DWARF_CARDANO_CBOR_DATASET_REPO"
    assert "not configured" in payload["message"]
    assert built == []


def test_compare_preflight_accepts_verified_qualified_dataset_inputs(tmp_path, monkeypatch):
    load = _qualified_dataset_load(tmp_path)
    repo = tmp_path / "dataset-repo"
    monkeypatch.setenv("DWARF_CARDANO_CBOR_DATASET_REPO", str(repo))
    monkeypatch.setattr(
        dataset_runtime,
        "_git_revision",
        lambda _path: dataset_runtime.QUALIFIED_DATASET_REVISION,
    )
    scenario_path = _scenario(tmp_path / "dataset.yaml", load=[load])

    assert scenario.compare_preflight(scenario_path) == {
        "ready": True,
        "code": "ready",
        "message": "Compare requirements are available.",
        "requirement": None,
    }


def test_compare_preflight_rejects_empty_or_unqualified_dataset(tmp_path, monkeypatch):
    repo = tmp_path / "dataset-repo"
    (repo / "dataset" / "conway-123-100").mkdir(parents=True)
    monkeypatch.setenv("DWARF_CARDANO_CBOR_DATASET_REPO", str(repo))
    load = _qualified_dataset_load(tmp_path / "qualified")
    load["dataset_repo_dir"] = "$DWARF_CARDANO_CBOR_DATASET_REPO"
    load["dataset_dir"] = "$DWARF_CARDANO_CBOR_DATASET_REPO/dataset/conway-123-100"
    scenario_path = _scenario(tmp_path / "empty.yaml", load=[load])
    monkeypatch.setattr(
        dataset_runtime,
        "_git_revision",
        lambda _path: dataset_runtime.QUALIFIED_DATASET_REVISION,
    )

    result = scenario.compare_preflight(scenario_path)

    assert result["ready"] is False
    assert result["code"] == "compare-requirement-unavailable"
    assert "missing real dataset rule directory" in result["message"]

    load["source_repository"] = "https://example.invalid/unqualified.git"
    unqualified = _scenario(tmp_path / "unqualified.yaml", load=[load])
    result = scenario.compare_preflight(unqualified)
    assert result["ready"] is False
    assert "repository is unqualified" in result["message"]


def test_compare_preflight_rejects_missing_target_executable(tmp_path, monkeypatch):
    load = _qualified_dataset_load(tmp_path)
    repo = tmp_path / "dataset-repo"
    monkeypatch.setenv("DWARF_CARDANO_CBOR_DATASET_REPO", str(repo))
    monkeypatch.setattr(
        dataset_runtime,
        "_git_revision",
        lambda _path: dataset_runtime.QUALIFIED_DATASET_REVISION,
    )
    Path(load["manifests_dir"], "amaru-target.yaml").unlink()
    scenario_path = _scenario(tmp_path / "missing-target.yaml", load=[load])

    result = scenario.compare_preflight(scenario_path)

    assert result["ready"] is False
    assert result["code"] == "compare-requirement-unavailable"
    assert "target manifest not found" in result["message"]


def test_compare_preflight_rejects_unavailable_reference_verifier_image(
    tmp_path, monkeypatch
):
    load = _qualified_dataset_load(tmp_path)
    repo = tmp_path / "dataset-repo"
    monkeypatch.setenv("DWARF_CARDANO_CBOR_DATASET_REPO", str(repo))
    monkeypatch.setattr(
        dataset_runtime,
        "_git_revision",
        lambda _path: dataset_runtime.QUALIFIED_DATASET_REVISION,
    )
    load.pop("reference_verifier_binary")
    load["reference_verifier_image"] = "qualified-verifier@sha256:" + "a" * 64
    monkeypatch.setattr(
        dataset_runtime,
        "_inspect_container_image",
        lambda *_args: (_ for _ in ()).throw(
            RuntimeError("reference verifier image is unavailable")
        ),
    )
    scenario_path = _scenario(tmp_path / "missing-verifier.yaml", load=[load])

    result = scenario.compare_preflight(scenario_path)

    assert result["ready"] is False
    assert result["code"] == "compare-requirement-unavailable"
    assert "reference verifier image is unavailable" in result["message"]


def test_compare_status_requires_success_before_agreement():
    passed = {"exit_status": "pass", "assertion_summary": {"pass": 1, "fail": 0}}
    different = {"exit_status": "pass", "assertion_summary": {"pass": 0, "fail": 1}}

    assert scenario._compare_status(passed, passed, iteration_diff_rows=[]) == (
        "AGREED",
        True,
        True,
    )
    assert scenario._compare_status(passed, different, iteration_diff_rows=[]) == (
        "DIVERGED",
        False,
        True,
    )
    assert scenario._compare_status(
        passed,
        passed,
        iteration_diff_rows=[(0, "ok", "clean_error")],
    ) == ("DIVERGED", False, True)


def test_matching_failed_sources_are_incomplete_not_agreed(tmp_path, monkeypatch):
    scenario_path = _scenario(tmp_path / "compare.yaml")
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    created = []

    def execute(_request):
        run_id = f"run-{len(created) + 1}"
        run_dir = runs_dir / run_id
        run_dir.mkdir()
        (run_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "exit_status": "fail",
                    "assertion_summary": {"pass": 0, "fail": 1},
                }
            ),
            encoding="utf-8",
        )
        created.append(run_dir)
        return {"run_id": run_id, "run_dir": str(run_dir)}

    monkeypatch.setattr("profile_manager.run_service.execute_run", execute)
    monkeypatch.setattr(
        "profile_manager.testcase_lifecycle.summarize_run_behavior",
        lambda **_kwargs: {"signature": "same-failure"},
    )
    monkeypatch.setattr(
        "profile_manager.testcase_lifecycle.summarize_run_resources",
        lambda **_kwargs: {"signature": "same-resources"},
    )

    result = scenario.compare_run(
        scenario_path,
        runs_dir=runs_dir,
        state_dir=tmp_path / "state",
    )

    assert result.agreed is False
    assert result.comparable is False
    payload = json.loads(result.comparison_json_path.read_text(encoding="utf-8"))
    assert payload["result"] == "INCOMPLETE"
    assert payload["agreed"] is False
    assert payload["comparable"] is False
    assert payload["runs"]["amaru"]["exit_status"] == "fail"
    assert payload["runs"]["cardano-node"]["exit_status"] == "fail"
    assert "Result: **INCOMPLETE**" in result.comparison_path.read_text(encoding="utf-8")


def test_compare_dashboard_keeps_incomplete_distinct_from_divergence(tmp_path):
    run_dir = tmp_path / "run-cardano"
    run_dir.mkdir()
    (run_dir / "cross-impl-comparison.json").write_text(
        json.dumps(
            {
                "scenario_id": "failed-compare",
                "result": "INCOMPLETE",
                "agreed": False,
                "comparable": False,
                "runs": {
                    "amaru": {"run_id": "run-amaru", "exit_status": "fail"},
                    "cardano-node": {"run_id": "run-cardano", "exit_status": "fail"},
                },
            }
        ),
        encoding="utf-8",
    )

    comparison = _comparison_from_run(run_dir)

    assert comparison is not None
    assert comparison["result"] == "INCOMPLETE"
    assert comparison["comparable"] is False
    strip = aggregate_strip([comparison], all_runs_24h_count=1)
    assert strip == {
        "total": 1,
        "agreed": 0,
        "diverged": 0,
        "incomplete": 1,
        "metric_divergent": 0,
        "runs_24h": 1,
    }


def test_both_compare_readers_fail_closed_on_forged_or_legacy_failed_sources(tmp_path):
    base = {
        "scenario_id": "failed-compare",
        "result": "AGREED",
        "agreed": True,
        "runs": {
            "amaru": {"run_id": "run-amaru", "exit_status": "fail"},
            "cardano-node": {"run_id": "run-cardano", "exit_status": "fail"},
        },
    }
    for index, comparable in enumerate((True, None)):
        payload = dict(base)
        if comparable is not None:
            payload["comparable"] = comparable
        run_dir = tmp_path / f"run-{index}"
        run_dir.mkdir()
        (run_dir / "cross-impl-comparison.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

        detailed = _comparison_from_run(run_dir)
        summary = _read_comparison_summary(run_dir)

        assert detailed is not None and summary is not None
        for record in (detailed, summary):
            assert record["result"] == "INCOMPLETE"
            assert record["agreed"] is False
            assert record["comparable"] is False


def test_compare_reader_rejects_inconsistent_success_summary(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "cross-impl-comparison.json").write_text(
        json.dumps(
            {
                "scenario_id": "inconsistent",
                "result": "DIVERGED",
                "agreed": True,
                "comparable": True,
                "runs": {
                    "amaru": {"exit_status": "pass"},
                    "cardano-node": {"exit_status": "pass"},
                },
            }
        ),
        encoding="utf-8",
    )

    result = _comparison_from_run(run_dir)

    assert result is not None
    assert result["result"] == "INCOMPLETE"
    assert result["agreed"] is False
    assert result["comparable"] is False


def test_live_compare_terminal_state_distinguishes_divergence_from_incomplete():
    script = Path("dwarf/dashboard/static/js/compare-runner.js").read_bytes()
    module_url = "data:text/javascript;base64," + base64.b64encode(script).decode("ascii")
    program = (
        f"const m = await import({json.dumps(module_url)});"
        "console.log(JSON.stringify(["
        "m.comparisonTerminalState('AGREED', 0),"
        "m.comparisonTerminalState('DIVERGED', 1),"
        "m.comparisonTerminalState('INCOMPLETE', 1)"
        "]));"
    )

    completed = subprocess.run(
        ["node", "--input-type=module", "-e", program],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == ["done", "done", "incomplete"]


def test_basic_compare_renders_unavailable_reason_and_keeps_launch_disabled(
    tmp_path, monkeypatch
):
    scenario_path = _scenario(
        tmp_path / "dataset.yaml",
        load=[
            {
                "primitive": "runtime_cardano_cbor_dataset_differential",
                "dataset_repo_dir": "$DWARF_CARDANO_CBOR_DATASET_REPO",
                "dataset_dir": "$DWARF_CARDANO_CBOR_DATASET_REPO/dataset/conway-123-100",
            }
        ],
    )
    monkeypatch.setenv("ADA2_DWARF_SCENARIOS_DIR", str(tmp_path))
    monkeypatch.delenv("DWARF_CARDANO_CBOR_DATASET_REPO", raising=False)
    invalidate_scenario_cache()

    html = dashboard.render_route_html("/operate/compare", token="private-test-token")
    script = Path("dwarf/dashboard/static/js/compare-runner.js").read_text(
        encoding="utf-8"
    )

    assert str(scenario_path) in html
    assert 'data-available="false"' in html
    assert 'aria-disabled="true"' not in html
    assert "Unavailable: Required compare input" in html
    assert "Required compare input DWARF_CARDANO_CBOR_DATASET_REPO is not configured." in html
    assert 'row.dataset.available === "true"' in script
    assert '$("[data-run-compare]").disabled = !available' in script
    assert 'result === "DIVERGED"' in script
    assert 'pill.textContent = "INCOMPLETE"' in script
