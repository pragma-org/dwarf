"""Bundle primitives resolve a repo-relative runs_dir, and a diff of absent files is not a divergence.

The scenarios name ``dwarf/corpora/bundle-runs``; the controller runs helpers from ``/``, so an
unresolved relative path found no bundles. The diff then compared two absent files, called the
result a divergence, and the smoke scenario passed without reading a bundle (F101).
"""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from profile_manager import primitives
from profile_manager.primitives import DWARF_ROOT


class _Handle:
    def __init__(self, run_dir):
        self.run_dir = run_dir
        self.logs = []

    def log(self, **entry):
        self.logs.append(entry)


_PARAMS = {"runs_dir": "dwarf/corpora/bundle-runs", "target_run_id": "20260427T092200Z-audittraildemo",
           "left_run_id": "20260427T064030Z-33297ed4", "right_run_id": "20260427T075729Z-9262386a",
           "compare_relpaths": ["outputs/runtime-cardano-lsq-extract/result.json"]}


@pytest.mark.parametrize("cls", [primitives.RuntimeBundleDedupe, primitives.RuntimeBundleTriage,
                                 primitives.RuntimeBundleChain, primitives.RuntimeBundleReplay,
                                 primitives.RuntimeBundleDiff, primitives.RuntimeBundleChainVerify])
def test_bundle_primitives_resolve_a_repo_relative_runs_dir(cls, tmp_path, monkeypatch):
    seen = []

    def fake_run(command, *args, **kwargs):
        seen.append(list(command))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    try:
        cls(params=dict(_PARAMS)).run(_Handle(tmp_path), None)
    except Exception:  # noqa: BLE001 - the fake helper writes no report; only the command matters
        pass
    assert seen, "the helper was not invoked"
    command = seen[0]
    assert "--runs-dir" in command
    assert command[command.index("--runs-dir") + 1] == str(DWARF_ROOT / "corpora" / "bundle-runs")


def _diff_handle(comparisons):
    payload = {"outcome": "ok", "comparison_verdict": "diff", "result": {"comparisons": comparisons}}
    return payload


@pytest.mark.parametrize("left,right", [(False, False), (True, False), (False, True)])
def test_bundle_diff_with_an_absent_side_is_not_clean(left, right, monkeypatch):
    payload = _diff_handle([{"relpath": "outputs/x/result.json", "left_exists": left, "right_exists": right,
                             "verdict": "missing_in_both"}])
    monkeypatch.setattr(primitives, "_events_from_handle", lambda *a, **k: [{"payload": payload}])
    result = primitives.BundleDiffCompletedClean(params={}).evaluate(None)
    assert result["result"] == "fail"


def test_bundle_diff_of_two_present_files_is_clean(monkeypatch):
    payload = _diff_handle([{"relpath": "outputs/x/result.json", "left_exists": True, "right_exists": True,
                             "verdict": "diff"}])
    monkeypatch.setattr(primitives, "_events_from_handle", lambda *a, **k: [{"payload": payload}])
    assert primitives.BundleDiffCompletedClean(params={}).evaluate(None)["result"] == "pass"


def test_resolved_runs_dir_is_json_serialisable():
    """The primitives log runs_dir in their events; a Path broke the event log live (F101b)."""
    import json
    value = primitives._optional_runtime_path("dwarf/corpora/bundle-runs")
    assert isinstance(value, str)
    json.dumps({"runs_dir": value})
