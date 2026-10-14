from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from profile_manager import forensic


def test_run_ledger_module_exists():
    assert importlib.util.find_spec("profile_manager.run_ledger"), "run ledger is missing"


def test_configured_ledger_root_is_shared_without_exposing_full_state(
    tmp_path, ledger_module, monkeypatch
):
    shared = tmp_path / "shared-run-ledger"
    monkeypatch.setenv("ADA2_DWARF_RUN_LEDGER_ROOT", str(shared))

    ledger = ledger_module.RunLedger(tmp_path / "controller-private-state")
    entry = _create(ledger)

    assert ledger.root == shared
    assert (shared / "entries" / f"{entry['run_id']}.json").is_file()
    assert not (tmp_path / "controller-private-state" / "run-ledger").exists()


@pytest.fixture
def ledger_module():
    assert importlib.util.find_spec("profile_manager.run_ledger"), "run ledger is missing"
    return importlib.import_module("profile_manager.run_ledger")


def _create(ledger, run_id="20261008T120000Z-1234abcd", **kwargs):
    return ledger.create(
        run_id=run_id, kind=kwargs.pop("kind", "scenario"), origin="cli",
        backend="local", scenario={"id": "example", "sha256": "a" * 64},
        profile={"id": "profile"}, topology={"id": "topology"},
        release_revision="b" * 40, **kwargs,
    )


def _start(tmp_path, **kwargs):
    return forensic.start_run(
        scenario_id="example", scenario_yaml=b'{"id":"example"}',
        target={"implementation": "amaru", "version": "test"},
        runtime="library", profile_id=None, profile_resolved=None,
        framework_version="test", framework_commit="b" * 40, seed=7,
        runs_dir=tmp_path / "runs", state_dir=tmp_path / "state", **kwargs,
    )


@pytest.mark.parametrize("kind", ["scenario", "deployment", "maintenance", "ci", "external"])
def test_schema_and_queued_identity_survive_reopen(tmp_path, ledger_module, kind):
    ledger = ledger_module.RunLedger(tmp_path / "state")
    entry = _create(ledger, kind=kind)
    schema = json.loads((Path(ledger_module.__file__).parent / "schemas/run-ledger-entry-v1.schema.json").read_text())
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    validator.validate(entry)
    assert ledger_module.RunLedger(tmp_path / "state").get(entry["run_id"]) == entry
    assert entry["state"] == "queued"
    assert entry["queued_at"] and entry["started_at"] is None
    assert entry["verdict"] is None
    assert entry["manifest_path"] is None and entry["evidence_path"] is None
    assert entry["scenario"]["id"] == "example"
    assert entry["profile"]["id"] == "profile"
    assert entry["topology"]["id"] == "topology"
    for field in ("run_id", "kind", "origin", "backend", "scenario", "profile", "topology", "release_revision", "state", "verdict", "manifest_path", "evidence_path", "queued_at", "started_at", "ended_at"):
        invalid = dict(entry)
        invalid.pop(field)
        assert not validator.is_valid(invalid), field


@pytest.mark.parametrize("invalid", [{"kind": "unknown"}, {"run_id": "../escape"}, {"origin": ""}, {"backend": ""}, {"state": "completed"}, {"queued_at": "yesterday"}])
def test_schema_invalid_entries_are_never_persisted(tmp_path, ledger_module, invalid):
    ledger = ledger_module.RunLedger(tmp_path)
    args = {"run_id": "safe", "kind": "scenario", "origin": "cli", "backend": "local", **invalid}
    with pytest.raises(ValueError):
        ledger.create(**args)
    assert ledger.list_entries() == []


@pytest.mark.parametrize("terminal", ["completed", "failed", "interrupted"])
def test_transitions_preserve_identity_and_refuse_reopening(tmp_path, ledger_module, terminal):
    ledger = ledger_module.RunLedger(tmp_path)
    queued = _create(ledger)
    running = ledger.transition(queued["run_id"], "running")
    assert running["started_at"] and running["ended_at"] is None
    assert ledger_module.RunLedger(tmp_path).get(queued["run_id"]) == running
    result = ledger.transition(queued["run_id"], terminal, verdict="pass" if terminal == "completed" else None, manifest_path="/runs/manifest.json", evidence_path="/runs/evidence")
    assert result["ended_at"]
    assert result["scenario"] == queued["scenario"]
    assert result["manifest_path"] == "/runs/manifest.json"
    with pytest.raises(ValueError, match="transition"):
        ledger.transition(queued["run_id"], "running")
    assert ledger.get(queued["run_id"]) == result


def test_duplicate_identity_and_illegal_queued_completion_are_refused(tmp_path, ledger_module):
    ledger = ledger_module.RunLedger(tmp_path)
    entry = _create(ledger)
    with pytest.raises(ValueError, match="exists"):
        _create(ledger)
    with pytest.raises(ValueError, match="transition"):
        ledger.transition(entry["run_id"], "completed")
    with pytest.raises((ValueError, TypeError)):
        ledger.transition(entry["run_id"], "running", run_id="replacement")
    assert ledger.get(entry["run_id"]) == entry


def test_failed_replace_retains_valid_previous_state_and_cleans_temp(tmp_path, ledger_module, monkeypatch):
    ledger = ledger_module.RunLedger(tmp_path)
    entry = _create(ledger)
    original_replace = ledger_module.os.replace

    def interrupted_replace(source, destination):
        assert Path(source).parent == Path(destination).parent
        raise OSError("simulated interruption before replace")

    monkeypatch.setattr(ledger_module.os, "replace", interrupted_replace)
    with pytest.raises(OSError, match="simulated interruption"):
        ledger.transition(entry["run_id"], "running")
    monkeypatch.setattr(ledger_module.os, "replace", original_replace)
    assert ledger.get(entry["run_id"]) == entry
    assert not list(ledger.entries_dir.glob("*.tmp"))


def test_process_crash_and_explicit_recovery_preserve_queued_and_fresh_runs(tmp_path, ledger_module):
    ledger = ledger_module.RunLedger(tmp_path)
    _create(ledger, "queued")
    script = "from profile_manager.run_ledger import RunLedger; import os,sys; l=RunLedger(sys.argv[1]); l.create(run_id='crashed',kind='scenario',origin='cli',backend='local'); l.transition('crashed','running'); os._exit(9)"
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], check=False)
    assert result.returncode == 9
    assert ledger.get("crashed")["state"] == "running"
    fresh = _create(ledger, "fresh")
    ledger.transition(fresh["run_id"], "running")
    crashed = ledger.get("crashed")
    ledger.transition("fresh", "completed")
    _create(ledger, "still-running")
    ledger.transition("still-running", "running")
    recovered = ledger.recover_interrupted(before=crashed["updated_at"])
    assert [item["run_id"] for item in recovered] == ["crashed"]
    assert ledger.get("crashed")["state"] == "interrupted"
    assert ledger.get("crashed")["verdict"] is None
    assert ledger.get("queued")["state"] == "queued"
    assert ledger.get("fresh")["state"] == "completed"
    assert ledger.get("still-running")["state"] == "running"
    assert ledger.recover_interrupted(before=crashed["updated_at"]) == []


def test_concurrent_readers_only_see_schema_valid_snapshots(tmp_path, ledger_module):
    ledger = ledger_module.RunLedger(tmp_path)
    started = Event()
    done = Event()
    snapshots = []

    def reader():
        started.set()
        while not done.is_set():
            for path in ledger.entries_dir.glob("*.json"):
                value = json.loads(path.read_text())
                ledger_module.validate_entry(value)
                snapshots.append(value)

    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(reader)
        started.wait(5)
        try:
            for index in range(30):
                entry = _create(ledger, f"run-{index}")
                ledger.transition(entry["run_id"], "running")
                ledger.transition(entry["run_id"], "completed", verdict="pass")
        finally:
            done.set()
        future.result(timeout=5)
    assert snapshots
    assert len(ledger.list_entries()) == 30


def test_competing_finalizations_cannot_overwrite_terminal_state(tmp_path, ledger_module):
    ledger = ledger_module.RunLedger(tmp_path)
    entry = _create(ledger)
    ledger.transition(entry["run_id"], "running")

    def finalize(state):
        try:
            return ledger_module.RunLedger(tmp_path).transition(entry["run_id"], state)["state"]
        except ValueError:
            return "refused"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(finalize, ["completed", "failed"]))
    assert results.count("refused") == 1
    assert ledger.get(entry["run_id"])["state"] in results


def test_forensic_publishes_running_before_manifest_and_finishes_after_chain(tmp_path, ledger_module):
    handle = _start(tmp_path)
    ledger = ledger_module.RunLedger(tmp_path / "state")
    running = ledger.get(handle.run_id)
    assert running["state"] == "running"
    assert running["kind"] == "scenario"
    assert running["release_revision"] == "b" * 40
    assert running["scenario"]["id"] == "example"
    assert not (handle.run_dir / "manifest.json").exists()
    chain = handle.end(exit_status="pass")
    finished = ledger.get(handle.run_id)
    manifest = json.loads((handle.run_dir / "manifest.json").read_text())
    assert finished["state"] == "completed"
    assert finished["verdict"] == manifest["execution"]["security_verdict"]
    assert finished["manifest_path"] == str((handle.run_dir / "manifest.json").resolve())
    assert finished["evidence_path"] == str(handle.run_dir.resolve())
    assert chain == json.loads((handle.run_dir / "chain.json").read_text())
    assert forensic.verify(handle.run_id, runs_dir=tmp_path / "runs", state_dir=tmp_path / "state").ok


def test_concurrent_forensic_starts_claim_unique_run_directories(
    tmp_path, ledger_module, monkeypatch
):
    monkeypatch.setattr(forensic, "_utc_timestamp_compact", lambda: "20261008T223524Z")

    with ThreadPoolExecutor(max_workers=16) as pool:
        handles = list(pool.map(lambda _: _start(tmp_path), range(32)))

    run_ids = [handle.run_id for handle in handles]
    assert len(run_ids) == len(set(run_ids)) == 32
    assert all((tmp_path / "runs" / run_id).is_dir() for run_id in run_ids)
    assert {
        entry["run_id"]
        for entry in ledger_module.RunLedger(tmp_path / "state").list_entries()
    } == set(run_ids)


def test_forensic_finalization_failure_keeps_running_state(tmp_path, ledger_module, monkeypatch):
    handle = _start(tmp_path)
    original_replace = ledger_module.os.replace

    def fail_chain(source, destination):
        if Path(destination).name == "chain.json":
            raise OSError("chain write interrupted")
        return original_replace(source, destination)

    monkeypatch.setattr(ledger_module.os, "replace", fail_chain)
    with pytest.raises(OSError, match="chain write interrupted"):
        handle.end(exit_status="pass")
    assert ledger_module.RunLedger(tmp_path / "state").get(handle.run_id)["state"] == "running"


def test_finalized_forensic_evidence_cannot_be_rewritten(tmp_path, ledger_module):
    handle = _start(tmp_path)
    handle.end(exit_status="pass")
    manifest = (handle.run_dir / "manifest.json").read_bytes()
    chain = (handle.run_dir / "chain.json").read_bytes()
    with pytest.raises(ValueError):
        handle.end(exit_status="fail")
    assert (handle.run_dir / "manifest.json").read_bytes() == manifest
    assert (handle.run_dir / "chain.json").read_bytes() == chain


def test_backfill_command_imports_only_requested_legacy_root(tmp_path, ledger_module):
    run_dir = tmp_path / "legacy" / "historic"
    run_dir.mkdir(parents=True)
    path = run_dir / "manifest.json"
    path.write_text(json.dumps({"run_id": "historic", "exit_status": "pass"}))
    payload = path.read_bytes()
    result = subprocess.run(
        [sys.executable, "-m", "profile_manager.run_ledger", "backfill", "--runs-dir", str(run_dir.parent), "--state-dir", str(tmp_path / "state"), "--kind", "external", "--backend", "legacy"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"run_ids": ["historic"], "count": 1}
    assert ledger_module.RunLedger(tmp_path / "state").get("historic")["queued_at"] is None
    assert path.read_bytes() == payload


def test_operational_run_has_no_test_verdict_and_forensic_failure_is_failed(tmp_path, ledger_module):
    handle = _start(tmp_path, run_kind="maintenance", origin="controller", backend="docker", topology_identity={"id": "local"})
    handle.end(exit_status="pass")
    entry = ledger_module.RunLedger(tmp_path / "state").get(handle.run_id)
    assert entry["kind"] == "maintenance"
    assert entry["origin"] == "controller"
    assert entry["backend"] == "docker"
    assert entry["topology"] == {"id": "local"}
    assert entry["verdict"] is None
    failed = _start(tmp_path)
    failed.end(exit_status="fail")
    assert ledger_module.RunLedger(tmp_path / "state").get(failed.run_id)["state"] == "failed"


@pytest.mark.parametrize(
    ("exit_status", "ledger_state"),
    [("error", "failed"), ("precondition_failed", "failed"), ("interrupted", "interrupted")],
)
def test_forensic_preserves_terminal_lifecycle_meaning(tmp_path, ledger_module, exit_status, ledger_state):
    handle = _start(tmp_path)
    handle.end(exit_status=exit_status)
    entry = ledger_module.RunLedger(tmp_path / "state").get(handle.run_id)
    assert entry["state"] == ledger_state
    assert json.loads((handle.run_dir / "manifest.json").read_text())["exit_status"] == exit_status


def test_existing_remote_command_receipt_retains_external_ssh_provenance(tmp_path, ledger_module):
    handle = forensic.record_remote_run(
        scenario_id="legacy-command", scenario_yaml=b'{"id":"legacy-command"}',
        target={"implementation": "amaru", "version": "test"},
        runtime="library", profile_id=None, profile_resolved=None,
        command_result=SimpleNamespace(returncode=0, stdout="ok", stderr="", rendered_command="legacy command"),
        runs_dir=tmp_path / "runs", state_dir=tmp_path / "state",
    )
    entry = ledger_module.RunLedger(tmp_path / "state").get(handle.run_id)
    assert entry["kind"] == "external"
    assert entry["origin"] == "legacy-remote-command"
    assert entry["backend"] == "ssh"


def test_explicit_backfill_preserves_manifest_bytes_provenance_and_idempotence(tmp_path, ledger_module):
    run_dir = tmp_path / "runs" / "historic"
    run_dir.mkdir(parents=True)
    manifest = {"run_id": "historic", "scenario": {"id": "historic-scenario"}, "framework": {"commit": "old"}, "started_at": "2026-01-01T00:00:00Z", "ended_at": "2026-01-01T00:01:00Z", "actor": "original", "exit_status": "pass"}
    path = run_dir / "manifest.json"
    payload = json.dumps(manifest, indent=2).encode()
    path.write_bytes(payload)
    ledger = ledger_module.RunLedger(tmp_path / "state")
    assert ledger.list_entries() == []
    assert forensic.list_recent_runs(runs_dir=tmp_path / "runs")[0]["run_id"] == "historic"
    entry = ledger.backfill_manifest(path, kind="external", backend="legacy")
    assert entry["origin"] == "legacy-backfill"
    assert entry["provenance"]["manifest_sha256"] == hashlib.sha256(payload).hexdigest()
    assert entry["provenance"]["actor"] == "original"
    assert entry["started_at"] == manifest["started_at"]
    assert entry["ended_at"] == manifest["ended_at"]
    assert entry["verdict"] == "fail"
    assert ledger.backfill_manifest(path, kind="external", backend="legacy") == entry
    assert path.read_bytes() == payload
    path.write_text(json.dumps({**manifest, "actor": "changed"}))
    with pytest.raises(ValueError, match="collision"):
        ledger.backfill_manifest(path, kind="external", backend="legacy")


def test_backfill_normalizes_legacy_null_profile_sentinel_without_rewriting_manifest(
    tmp_path, ledger_module
):
    run_dir = tmp_path / "runs" / "historic"
    run_dir.mkdir(parents=True)
    path = run_dir / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "run_id": "historic",
                "exit_status": "precondition_failed",
                "profile": {"id": None, "sha256": None},
            }
        )
    )
    original = path.read_bytes()
    entry = ledger_module.RunLedger(tmp_path / "state").backfill_manifest(
        path, kind="external", backend="legacy"
    )
    assert entry["profile"] is None
    assert entry["state"] == "failed"
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    ("exit_status", "ledger_state"),
    [
        ("pass", "completed"),
        ("fail", "failed"),
        ("error", "failed"),
        ("precondition_failed", "failed"),
        ("interrupted", "interrupted"),
    ],
)
def test_backfill_accepts_every_existing_manifest_outcome(
    tmp_path, ledger_module, exit_status, ledger_state
):
    run_dir = tmp_path / "runs" / exit_status
    run_dir.mkdir(parents=True)
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(json.dumps({"run_id": exit_status, "exit_status": exit_status}))
    entry = ledger_module.RunLedger(tmp_path / "state").backfill_manifest(
        manifest_path, kind="external", backend="legacy"
    )
    assert entry["state"] == ledger_state
    assert entry["provenance"]["legacy_exit_status"] == exit_status
