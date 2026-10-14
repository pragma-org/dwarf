import json
import subprocess
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from profile_manager import primitives
from profile_manager.primitives import build_runtime_bundle_timeline_command
from scripts import runtime_bundle_timeline


ROOT = Path(__file__).resolve().parents[1]


def _write_manifest(runs_dir: Path, run_id: str, *, started_at: str) -> None:
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "scenario": {"id": f"scenario-{run_id}"},
                "exit_status": "pass",
                "started_at": started_at,
                "ended_at": started_at,
            }
        ),
        encoding="utf-8",
    )


def test_latest_selector_uses_only_newest_completed_manifest_runs(tmp_path: Path):
    runs_dir = tmp_path / "runs"
    _write_manifest(runs_dir, "run-old", started_at="2026-10-11T01:00:00Z")
    _write_manifest(runs_dir, "run-middle", started_at="2026-10-11T02:00:00Z")
    _write_manifest(runs_dir, "run-new", started_at="2026-10-11T03:00:00Z")
    active_dir = runs_dir / "run-active"
    active_dir.mkdir(parents=True)
    (active_dir / "manifest.json").write_text(
        json.dumps(
            {
                "scenario": {"id": "scenario-active"},
                "exit_status": "unknown",
                "started_at": "2026-10-11T04:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    selected = runtime_bundle_timeline.select_latest_completed_bundle_ids(
        runs_dir, count=2
    )

    assert selected == ["run-middle", "run-new"]


def test_latest_selector_flows_through_command_and_real_cli(tmp_path: Path):
    runs_dir = tmp_path / "runs"
    output_dir = tmp_path / "output"
    _write_manifest(runs_dir, "run-old", started_at="2026-10-11T01:00:00Z")
    _write_manifest(runs_dir, "run-middle", started_at="2026-10-11T02:00:00Z")
    _write_manifest(runs_dir, "run-new", started_at="2026-10-11T03:00:00Z")

    command = build_runtime_bundle_timeline_command(
        runs_dir=runs_dir,
        bundle_ids=[],
        latest_count=2,
        output_dir=output_dir,
    )
    assert command[-2:] == ["--latest-count", "2"]

    exit_code = runtime_bundle_timeline.main(
        [
            "runtime_bundle_timeline.py",
            "--runs-dir",
            str(runs_dir),
            "--output-dir",
            str(output_dir),
            "--latest-count",
            "2",
        ]
    )

    assert exit_code == 0
    payload = json.loads((output_dir / "timeline.json").read_text(encoding="utf-8"))
    assert payload["summary"]["input_bundle_count"] == 2
    assert payload["summary"]["event_count"] == 2
    assert [event["bundle_id"] for event in payload["events"]] == [
        "run-middle",
        "run-new",
    ]


def test_explicit_selector_preserves_manifest_without_terminal_timestamp(tmp_path: Path):
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "historical-in-progress"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "scenario": {"id": "historical-scenario"},
                "exit_status": "unknown",
                "started_at": "2026-04-27T01:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    result = runtime_bundle_timeline.run_bundle_timeline(
        runs_dir=runs_dir,
        bundle_ids=["historical-in-progress"],
        output_dir=tmp_path / "output",
        scenario_id_filters=[],
        signature_token_filters=[],
    )

    assert result["event_count"] == 1
    assert result["signature_count"] == 1


def test_latest_selector_fails_when_no_completed_manifests_exist(
    tmp_path: Path, capsys
):
    runs_dir = tmp_path / "runs"
    active_dir = runs_dir / "active"
    active_dir.mkdir(parents=True)
    (active_dir / "manifest.json").write_text(
        json.dumps(
            {
                "scenario": {"id": "still-running"},
                "exit_status": "unknown",
                "started_at": "2026-10-11T04:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    exit_code = runtime_bundle_timeline.main(
        [
            "runtime_bundle_timeline.py",
            "--runs-dir",
            str(runs_dir),
            "--output-dir",
            str(tmp_path / "output"),
            "--latest-count",
            "7",
        ]
    )

    assert exit_code == 1
    assert "no completed run bundles found" in capsys.readouterr().err


def test_latest_selector_rejects_symlinked_run_manifest_and_evidence_paths(
    tmp_path: Path,
):
    runs_dir = tmp_path / "runs"
    _write_manifest(runs_dir, "run-safe", started_at="2026-10-11T01:00:00Z")

    outside_run = tmp_path / "outside-run"
    _write_manifest(tmp_path, "outside-run", started_at="2026-10-11T04:00:00Z")
    (runs_dir / "run-dir-link").symlink_to(outside_run, target_is_directory=True)

    manifest_link_run = runs_dir / "run-manifest-link"
    manifest_link_run.mkdir()
    (manifest_link_run / "manifest.json").symlink_to(
        outside_run / "manifest.json"
    )

    unsafe_evidence_run = runs_dir / "run-evidence-link"
    _write_manifest(
        runs_dir, "run-evidence-link", started_at="2026-10-11T03:00:00Z"
    )
    outside_outputs = tmp_path / "outside-outputs"
    outside_outputs.mkdir()
    (unsafe_evidence_run / "outputs").symlink_to(
        outside_outputs, target_is_directory=True
    )

    assert runtime_bundle_timeline.select_latest_completed_bundle_ids(
        runs_dir, count=7
    ) == ["run-safe"]


def test_old_corrupt_optional_evidence_cannot_block_latest_selection(tmp_path: Path):
    runs_dir = tmp_path / "runs"
    _write_manifest(runs_dir, "run-old", started_at="2026-10-11T01:00:00Z")
    corrupt = runs_dir / "run-old" / "outputs" / "sarif-export"
    corrupt.mkdir(parents=True)
    (corrupt / "result.json").write_text("[1]", encoding="utf-8")
    _write_manifest(runs_dir, "run-new", started_at="2026-10-11T02:00:00Z")

    assert runtime_bundle_timeline.select_latest_completed_bundle_ids(
        runs_dir, count=1
    ) == ["run-new"]


def test_latest_cli_rejects_run_swapped_to_symlink_after_selection(
    tmp_path: Path, monkeypatch, capsys
):
    runs_dir = tmp_path / "runs"
    _write_manifest(runs_dir, "run-selected", started_at="2026-10-11T02:00:00Z")
    outside_run = tmp_path / "outside-run"
    _write_manifest(tmp_path, "outside-run", started_at="2026-10-11T03:00:00Z")
    original_selector = runtime_bundle_timeline.select_latest_completed_bundle_ids

    def select_then_swap(root: Path, *, count: int) -> list[str]:
        selected = original_selector(root, count=count)
        selected_path = root / "run-selected"
        selected_path.rename(tmp_path / "original-selected")
        selected_path.symlink_to(outside_run, target_is_directory=True)
        return selected

    monkeypatch.setattr(
        runtime_bundle_timeline,
        "select_latest_completed_bundle_ids",
        select_then_swap,
    )

    exit_code = runtime_bundle_timeline.main(
        [
            "runtime_bundle_timeline.py",
            "--runs-dir",
            str(runs_dir),
            "--output-dir",
            str(tmp_path / "output"),
            "--latest-count",
            "1",
        ]
    )

    assert exit_code == 1
    assert "selected completed bundle changed before consumption" in capsys.readouterr().err
    assert not (tmp_path / "output" / "timeline.json").exists()


def test_cli_refuses_ambiguous_or_missing_bundle_selector(tmp_path: Path):
    common = [
        "runtime_bundle_timeline.py",
        "--runs-dir",
        str(tmp_path),
        "--output-dir",
        str(tmp_path / "output"),
    ]
    with pytest.raises(SystemExit):
        runtime_bundle_timeline.main(common)
    with pytest.raises(SystemExit):
        runtime_bundle_timeline.main(
            common + ["--bundle-id", "run-one", "--latest-count", "1"]
        )


def test_shipped_timeline_scenario_uses_valid_latest_completed_selector():
    scenario = json.loads(
        (ROOT / "dwarf/scenarios/runtime-bundle-timeline-example-smoke.yaml").read_text(
            encoding="utf-8"
        )
    )
    params = scenario["load"][0]
    assert params["latest_count"] == 7
    assert "bundle_ids" not in params
    # The primitive's native storage_roots().runs default follows the installed
    # source/release layout. A scenario-local home-relative path points at the
    # source tree inside containers instead of the persisted run store.
    assert "runs_dir" not in params

    schema = json.loads(
        (ROOT / "dwarf/primitives/load/runtime_bundle_timeline.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator(schema).validate(
        {key: value for key, value in params.items() if key != "primitive"}
    )


def test_timeline_primitive_defaults_to_installed_storage_root(monkeypatch, tmp_path: Path):
    runs_dir = tmp_path / "installed" / "runs"
    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(runs_dir))
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(tmp_path / "installed" / "state"))
    monkeypatch.setenv("ADA2_DWARF_BUNDLES_DIR", str(tmp_path / "installed" / "bundles"))
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(primitives.subprocess, "run", fake_run)

    class Handle:
        run_dir = tmp_path / "active-run"

        def log(self, **event):
            pass

    primitives.RuntimeBundleTimeline(
        params={
            "latest_count": 7,
            "output_dir": "outputs/bundle-timeline",
        }
    ).run(Handle(), None)

    runs_arg = captured["command"].index("--runs-dir") + 1
    assert captured["command"][runs_arg] == str(runs_dir.resolve())
