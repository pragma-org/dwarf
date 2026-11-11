"""F190. A public bundle export must not carry the operator's host paths: the retained run plan
declares `storage.runs_dir`/`state_dir` in the operator namespace (by contract; the run page reads
them), and the dashboard export shipped them to Pages in `inputs/run-service-plan.json`."""
from __future__ import annotations

import json
import tarfile

from scripts import runtime_bundle_export

HOST_RUNS = "/home/operator/.local/share/dwarf/runs"
HOST_STATE = "/home/operator/.local/share/dwarf/state"


def _run_dir(root):
    run_dir = root / "external-antithesis-abc"
    (run_dir / "inputs").mkdir(parents=True)
    (run_dir / "external").mkdir()
    (run_dir / "manifest.json").write_text(json.dumps({"run_id": run_dir.name, "exit_status": "pass"}))
    (run_dir / "inputs" / "run-service-plan.json").write_text(json.dumps(
        {"schema_version": "dwarf.run-service.invocation.v1", "resolved": {"scenario_id": "x"},
         "storage": {"runs_dir": HOST_RUNS, "state_dir": HOST_STATE}}))
    (run_dir / "external" / "launch.json").write_text(json.dumps(
        {"plan_path": HOST_RUNS + "/" + run_dir.name + "/inputs/run-service-plan.json", "directory": "antithesis/generated/x"}))
    return run_dir


def _members(tarball):
    with tarfile.open(tarball) as tf:
        return {m.name: tf.extractfile(m).read().decode() for m in tf.getmembers() if m.isfile()}


def test_public_export_redacts_the_operator_namespace_and_says_so(tmp_path):
    run_dir = _run_dir(tmp_path / "runs")
    public = runtime_bundle_export.run_export(run_dir=run_dir, output_dir=tmp_path / "public", target_run_id=run_dir.name,
                                              signing_actor="ci", key_path=None, redact_host_paths=True)
    files = _members(tmp_path / "public" / public["tarball_filename"])
    assert not any("/home/operator" in body for body in files.values())
    plan = json.loads(files[run_dir.name + "/inputs/run-service-plan.json"])
    assert plan["storage"] == {"runs_dir": "<operator-namespace>", "state_dir": "<operator-namespace>"}
    assert json.loads(files[run_dir.name + "/external/launch.json"])["plan_path"] == \
        "<operator-namespace>/" + run_dir.name + "/inputs/run-service-plan.json"
    assert "operator namespace paths were redacted" in files["README-export.txt"]
    assert public["host_paths_redacted"] is True
    assert runtime_bundle_export.verify_export_signature(tmp_path / "public")["verdict"] == "verified"
    private = runtime_bundle_export.run_export(run_dir=run_dir, output_dir=tmp_path / "private", target_run_id=run_dir.name,
                                               signing_actor="ci", key_path=None)
    assert HOST_RUNS in _members(tmp_path / "private" / private["tarball_filename"])[run_dir.name + "/inputs/run-service-plan.json"]
