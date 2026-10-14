import json
import re
from pathlib import Path
from types import SimpleNamespace

from profile_manager import dashboard
from profile_manager.data import operate_targets


TARGET_ID = "amaru-cbor-decode-plutus-data"
SCENARIO_ID = "cardano-amaru-cbor-dataset-plutus-data-differential"


def test_packaged_target_detail_exposes_honest_runtime_and_bound_scenario(monkeypatch):
    monkeypatch.delenv("DWARF_CARDANO_CBOR_DATASET_REPO", raising=False)

    html = dashboard.render_route_html(f"/operate/targets/{TARGET_ID}")

    assert html is not None
    assert "Runtime availability" in html
    assert 'data-target-runtime-state="unavailable"' in html
    assert SCENARIO_ID in html
    assert f'/run?scenario={SCENARIO_ID}' in html
    assert "Required compare input DWARF_CARDANO_CBOR_DATASET_REPO is not configured." in html
    assert "Review in Run" in html
    assert "Start in Run" not in html


def test_target_scenario_link_selects_exact_scenario_in_run_destination():
    html = dashboard.render_route_html(f"/run?scenario={SCENARIO_ID}")

    assert html is not None
    assert (
        f'<option value="{SCENARIO_ID}" selected>' in html
    )
    assert re.search(
        rf'data-scenario-id="{re.escape(SCENARIO_ID)}"[^>]+aria-selected="true"',
        html,
    )


def test_unknown_target_scenario_query_is_not_selected_in_run_destination():
    html = dashboard.render_route_html("/run?scenario=not-a-catalog-scenario")

    assert html is not None
    assert 'value="not-a-catalog-scenario"' not in html
    assert 'data-scenario-id="not-a-catalog-scenario"' not in html


def test_ready_exact_target_scenario_gets_direct_basic_start(
    tmp_path: Path, monkeypatch
):
    binary = tmp_path / "target-bin"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    manifest = tmp_path / "target-demo.yaml"
    manifest.write_text(
        json.dumps(
            {
                "id": "target-demo",
                "binary": str(binary),
                "input_format": "file_arg",
                "implementation": "amaru",
                "language": "shell",
                "upstream_commit": "a" * 40,
                "decoder_type": "CBOR codec",
                "invariants": ["bounded input"],
            }
        ),
        encoding="utf-8",
    )
    scenario = SimpleNamespace(
        definition_id="scenario-demo",
        path=tmp_path / "scenario-demo.yaml",
        data={
            "id": "scenario-demo",
            "title": "Demo target scenario",
            "load": [{"primitive": "cbor_fuzz_target", "target_id": "target-demo"}],
        },
    )
    monkeypatch.setattr(
        operate_targets,
        "list_definitions",
        lambda catalog: [scenario] if catalog == "scenarios" else [],
        raising=False,
    )
    monkeypatch.setattr(
        operate_targets,
        "compare_preflight",
        lambda _path: {
            "ready": True,
            "code": "ready",
            "message": "Compare requirements are available.",
            "requirement": None,
        },
        raising=False,
    )
    monkeypatch.setattr(
        operate_targets,
        "resolve_run_plan",
        lambda _request: {"availability": {"state": "ready", "reason": "Ready."}},
        raising=False,
    )

    result = operate_targets.target_runtime_entrypoints(
        "target-demo", source_path=manifest, source=manifest.read_bytes()
    )

    assert result["state"] == "ready"
    assert result["reason"] == "Executable target and one scenario are ready."
    assert result["scenarios"] == [
        {
            "id": "scenario-demo",
            "title": "Demo target scenario",
            "start_url": "/run?scenario=scenario-demo",
            "available": True,
            "state": "ready",
            "reason": "Ready.",
        }
    ]


def test_target_reference_matching_uses_only_declared_target_keys():
    assert operate_targets.scenario_references_target(
        {
            "load": [
                {
                    "primitive": "runtime_cardano_cbor_dataset_differential",
                    "candidate_target_id": "target-demo",
                }
            ]
        },
        "target-demo",
    )
    assert not operate_targets.scenario_references_target(
        {
            "title": "target-demo is mentioned here",
            "load": [{"primitive": "noop", "note": "target-demo"}],
        },
        "target-demo",
    )


def test_unsupported_target_input_mode_is_not_runtime_ready(
    tmp_path: Path, monkeypatch
):
    binary = tmp_path / "target-bin"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    manifest = tmp_path / "target-demo.yaml"
    manifest.write_text(
        json.dumps(
            {
                "id": "target-demo",
                "binary": str(binary),
                "input_format": "socket_stream",
                "implementation": "amaru",
                "language": "shell",
                "upstream_commit": "a" * 40,
                "decoder_type": "CBOR codec",
                "invariants": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(operate_targets, "list_definitions", lambda _catalog: [])

    result = operate_targets.target_runtime_entrypoints(
        "target-demo", source_path=manifest, source=manifest.read_bytes()
    )

    assert result["state"] == "unavailable"
    assert "unsupported target input_format" in result["reason"]


def test_ready_scenario_reports_missing_executable_instead_of_ready(
    tmp_path: Path, monkeypatch
):
    manifest = tmp_path / "target-demo.yaml"
    manifest.write_text(
        json.dumps(
            {
                "id": "target-demo",
                "binary": str(tmp_path / "missing-target"),
                "input_format": "file_arg",
                "implementation": "amaru",
                "language": "shell",
                "upstream_commit": "a" * 40,
                "decoder_type": "CBOR codec",
                "invariants": [],
            }
        ),
        encoding="utf-8",
    )
    scenario = SimpleNamespace(
        definition_id="scenario-demo",
        path=tmp_path / "scenario-demo.yaml",
        data={"id": "scenario-demo", "load": [{"target_id": "target-demo"}]},
    )
    monkeypatch.setattr(operate_targets, "list_definitions", lambda _catalog: [scenario])
    monkeypatch.setattr(
        operate_targets,
        "compare_preflight",
        lambda _path: {"ready": True, "code": "ready", "message": "Ready."},
    )
    monkeypatch.setattr(
        operate_targets,
        "resolve_run_plan",
        lambda _request: {"availability": {"state": "ready", "reason": "Ready."}},
    )

    result = operate_targets.target_runtime_entrypoints(
        "target-demo", source_path=manifest, source=manifest.read_bytes()
    )

    assert result["scenarios"][0]["available"] is False
    assert result["scenarios"][0]["reason"].startswith(
        "Target executable is unavailable:"
    )


def test_target_detail_renders_unavailable_when_plan_resolution_has_expected_io_failure(
    monkeypatch,
):
    monkeypatch.delenv("DWARF_CARDANO_CBOR_DATASET_REPO", raising=False)
    monkeypatch.setattr(
        operate_targets,
        "resolve_run_plan",
        lambda _request: (_ for _ in ()).throw(OSError("catalog snapshot unavailable")),
    )
    monkeypatch.setattr(
        operate_targets,
        "compare_preflight",
        lambda _path: {"ready": True, "code": "ready", "message": "Ready."},
    )

    html = dashboard.render_route_html(f"/operate/targets/{TARGET_ID}")

    assert html is not None
    assert 'data-target-runtime-state="unavailable"' in html
