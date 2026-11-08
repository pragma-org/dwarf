"""The bundle-tool example scenarios run against recorded fixture runs shipped in the repository.

They referenced April 2026 demo runs that existed on no install, so they failed with an empty or
broken chain. The recorded runs now live under ``dwarf/corpora/bundle-runs`` and the scenarios
point there; the tools only read the runs directory.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from profile_manager import primitives

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "dwarf" / "corpora" / "bundle-runs"
SCENARIOS = [
    "runtime-bundle-chain-verify-example-smoke",
    "runtime-bundle-export-sarif-example-smoke",
    "runtime-bundle-summary-compose-example-smoke",
    "runtime-forensic-snapshot-example-smoke",
    "runtime-bundle-diff-example-divergence",
]


@pytest.mark.parametrize("scenario_id", SCENARIOS)
def test_bundle_tool_scenarios_read_the_shipped_fixture_runs(scenario_id):
    text = (ROOT / "dwarf" / "scenarios" / f"{scenario_id}.yaml").read_text(encoding="utf-8")
    document = json.loads(text)
    runs_dirs = {step.get("runs_dir") for step in document.get("load", []) if "runs_dir" in step}
    assert runs_dirs == {"dwarf/corpora/bundle-runs"}
    assert primitives._resolve_runtime_path("dwarf/corpora/bundle-runs").resolve() == (primitives.DWARF_ROOT / "corpora" / "bundle-runs").resolve()
    for run_id in sorted(set(re.findall(r"20260427T\d{6}Z-[a-z0-9]+", text))):
        assert (FIXTURES / run_id).is_dir(), run_id
        assert any((FIXTURES / run_id).iterdir())
