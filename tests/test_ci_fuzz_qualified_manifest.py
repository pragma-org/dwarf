"""DWARF library fuzz failed on every push and schedule since 2026-11-05: the run service refuses the
Amaru tx-body target unless its manifest names an exact 40-hex source revision and a real binary
(`upstream_commit: "local"` is committed). CI builds the harness from the pinned AMARU_REF, so it
records that exact revision in the workspace manifest before running; the gate itself is unchanged."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import yaml

from profile_manager import run_service

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/dwarf-fuzz.yml"
MANIFEST = "dwarf/targets/manifests/amaru-cbor-decode-tx-body.yaml"
BINARY = "dwarf/targets/amaru/target/release/amaru-cbor-decode-tx-body"


def _workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _steps():
    return _workflow()["jobs"]["fuzz"]["steps"]


def _qualify_step():
    steps = [s for s in _steps() if MANIFEST in str(s.get("run") or "")]
    assert len(steps) == 1, "one step qualifies the tx-body manifest"
    return steps[0]


def test_the_manifest_is_qualified_after_the_build_and_before_the_runs():
    names = [s.get("name") for s in _steps()]
    qualify = names.index(_qualify_step()["name"])
    assert names.index("Build decoder harnesses") < qualify
    assert qualify < names.index("Run registered library fuzz scenarios through Run Service")


def _run_qualify(tmp_path, *, ref, binary="file"):
    (tmp_path / MANIFEST).parent.mkdir(parents=True)
    shutil.copy(ROOT / MANIFEST, tmp_path / MANIFEST)
    target = tmp_path / BINARY
    target.parent.mkdir(parents=True)
    if binary == "file":
        target.write_bytes(b"\x7fELF")
    elif binary == "symlink":
        (target.parent / "real").write_bytes(b"\x7fELF")
        target.symlink_to(target.parent / "real")
    env = {**os.environ, "AMARU_REF": ref}
    return subprocess.run(["bash", "-euo", "pipefail", "-c", _qualify_step()["run"]], cwd=tmp_path,
                          env=env, capture_output=True, text=True)


def test_the_workspace_manifest_names_the_exact_pinned_revision(tmp_path):
    ref = _workflow()["env"]["AMARU_REF"]
    result = _run_qualify(tmp_path, ref=ref)
    assert result.returncode == 0, result.stderr
    manifest = json.loads((tmp_path / MANIFEST).read_text(encoding="utf-8"))
    assert manifest["upstream_commit"] == ref
    assert run_service._REVISION.fullmatch(manifest["upstream_commit"])
    committed = json.loads((ROOT / MANIFEST).read_text(encoding="utf-8"))
    assert {k: v for k, v in manifest.items() if k != "upstream_commit"} == \
        {k: v for k, v in committed.items() if k != "upstream_commit"}


def test_no_qualification_without_an_exact_ref_or_a_real_binary(tmp_path):
    assert _run_qualify(tmp_path / "a", ref="v10.11").returncode != 0
    assert _run_qualify(tmp_path / "b", ref="a" * 40, binary="missing").returncode != 0
    assert _run_qualify(tmp_path / "c", ref="a" * 40, binary="symlink").returncode != 0


def test_the_run_service_gate_accepts_the_qualified_workspace_manifest(tmp_path, monkeypatch):
    monkeypatch.delenv("ADA2_DWARF_LAUNCH_ID", raising=False)
    ref = _workflow()["env"]["AMARU_REF"]
    assert _run_qualify(tmp_path, ref=ref).returncode == 0
    manifest_path = tmp_path / MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    # In CI the workspace is the repo root; here the relative binary path points into tmp_path.
    manifest["binary"] = str(tmp_path / manifest["binary"])
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    document = json.loads((ROOT / "dwarf/scenarios/amaru-cbor-tx-body-fuzz.yaml").read_text())
    document["load"][0]["manifests_dir"] = str(manifest_path.parent)
    scenario = tmp_path / "ci-tx-body.yaml"
    scenario.write_text(json.dumps(document), encoding="utf-8")
    run_service._enforce_direct_library_prerequisites(run_service.scenario_module.load_scenario(scenario))


def test_the_committed_manifest_alone_is_still_refused(tmp_path, monkeypatch):
    import pytest
    monkeypatch.delenv("ADA2_DWARF_LAUNCH_ID", raising=False)
    document = json.loads((ROOT / "dwarf/scenarios/amaru-cbor-tx-body-fuzz.yaml").read_text())
    scenario = tmp_path / "committed.yaml"
    scenario.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(run_service.RunServiceError):
        run_service._enforce_direct_library_prerequisites(run_service.scenario_module.load_scenario(scenario))
