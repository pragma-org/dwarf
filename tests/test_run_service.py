from __future__ import annotations

from argparse import Namespace
import ast
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from profile_manager import cli, dashboard, forensic, run_service
from profile_manager.data import schedule_store, scheduler
from profile_manager.data.runs import recent_runs_payload


ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / "dwarf/scenarios/edge-cases-cbor-tx-body-amaru.yaml"
PARITY_SCENARIO = ROOT / "tests/fixtures/run-service-parity.yaml"


def test_resolved_plan_is_adapter_invariant_but_retains_honest_invocation(tmp_path):
    common = {
        "scenario_path": SCENARIO,
        "runs_dir": tmp_path / "runs",
        "state_dir": tmp_path / "state",
    }

    cli_plan = run_service.resolve_run(run_service.RunRequest(origin="cli", **common))
    dashboard_plan = run_service.resolve_run(
        run_service.RunRequest(origin="dashboard", **common)
    )
    scheduler_plan = run_service.resolve_run(
        run_service.RunRequest(origin="scheduler", **common)
    )

    assert cli_plan["plan_digest"] == dashboard_plan["plan_digest"]
    assert dashboard_plan["plan_digest"] == scheduler_plan["plan_digest"]
    assert cli_plan["resolved"] == dashboard_plan["resolved"] == scheduler_plan["resolved"]
    assert [
        cli_plan["invocation"]["origin"],
        dashboard_plan["invocation"]["origin"],
        scheduler_plan["invocation"]["origin"],
    ] == ["cli", "dashboard", "scheduler"]
    assert cli_plan["resolved"]["scenario"]["id"] == "edge-cases-cbor-tx-body-amaru"
    assert cli_plan["resolved"]["release_revision"]


def test_fuzz_iteration_override_is_part_of_the_frozen_execution_plan(tmp_path):
    request = run_service.RunRequest(
        scenario_path=ROOT / "dwarf/scenarios/amaru-cbor-block-fuzz.yaml",
        origin="ci",
        runs_dir=tmp_path / "runs",
        state_dir=tmp_path / "state",
        fuzz_iterations=37,
    )

    execution = run_service._resolve_execution(request)

    assert execution.plan["resolved"]["input_overrides"] == {
        "fuzz_iterations": 37
    }
    assert execution.scenario_definition.load[0].params["iterations"] == 37
    assert execution.plan["resolved"]["scenario"]["digest"] == run_service._sha256(
        execution.scenario_definition.raw_bytes
    )


def test_ci_execution_is_classified_once_in_ci_history(tmp_path, monkeypatch):
    monkeypatch.setenv("DWARF_SOURCE_REVISION", "c" * 40)

    result = run_service.execute_run(
        run_service.RunRequest(
            scenario_path=PARITY_SCENARIO,
            origin="ci",
            runs_dir=tmp_path / "runs",
            state_dir=tmp_path / "state",
        )
    )

    payload = recent_runs_payload(
        runs_dir=tmp_path / "runs",
        state_dir=tmp_path / "state",
        scope="ci",
        limit=20,
    )
    matches = [
        row for row in payload["recent_runs"] if row["run_id"] == result["run_id"]
    ]
    assert result["ledger"]["kind"] == "ci"
    assert len(matches) == 1
    assert matches[0]["origin"] == "ci"
    assert matches[0]["backend"] == "local"
    assert matches[0]["state"] == "completed"
    assert matches[0]["verdict"] == "pass"


def test_external_run_is_idempotent_and_terminal_without_bundle_is_inconclusive(
    tmp_path,
):
    request = run_service.ExternalRunRequest(
        backend="antithesis",
        origin="external",
        launch={
            "transport": "moog",
            "repository": "pragma-org/dwarf",
            "directory": "antithesis/cardano_node_dwarf",
            "commit": "a" * 40,
            "try": 1,
        },
        runs_dir=tmp_path / "runs",
        state_dir=tmp_path / "state",
    )

    first = run_service.reserve_external_run(request)
    second = run_service.reserve_external_run(request)

    assert first["created"] is True
    assert second["created"] is False
    assert first["run_id"] == second["run_id"]
    assert len(run_service.RunLedger(tmp_path / "state").list_entries()) == 1

    submitted = run_service.record_external_submission(
        first["run_id"],
        state_dir=tmp_path / "state",
        response={"returncode": 0, "stdout": "submitted", "stderr": ""},
        external_test_run_id="moog-test-1",
    )
    assert submitted["state"] == "running"
    assert submitted["verdict"] is None

    terminal = run_service.reconcile_external_status(
        first["run_id"],
        state_dir=tmp_path / "state",
        phase="completed",
        status_evidence={"test_run_id": "moog-test-1", "phase": "completed"},
    )
    assert terminal["state"] == "completed"
    assert terminal["verdict"] == "inconclusive"
    assert terminal["provenance"]["verdict_reason"] == "no_verified_result_bundle"


def test_existing_external_run_is_linked_without_fabricating_submission(tmp_path):
    external_test_run_id = "moog-existing-1"
    request = run_service.ExternalRunRequest(
        backend="antithesis",
        origin="external",
        launch={
            "transport": "moog",
            "mode": "link-existing",
            "external_test_run_id": external_test_run_id,
            "repository": "cyber-castellum/dwarf",
            "directory": "antithesis/cardano_amaru_miniprotocol_security",
            "commit": "a" * 40,
            "try": 1,
        },
        runs_dir=tmp_path / "runs",
        state_dir=tmp_path / "state",
    )
    reserved = run_service.reserve_external_run(request)
    evidence = {
        "test_run_id": external_test_run_id,
        "source": "moog-facts",
        "fact_sha256": "sha256:" + "b" * 64,
    }

    with pytest.raises(run_service.RunServiceError, match="not bound"):
        run_service.reconcile_external_status(
            reserved["run_id"],
            state_dir=tmp_path / "state",
            phase="finished",
            status_evidence={"test_run_id": "moog-existing-2", "phase": "finished"},
        )
    assert not (Path(reserved["run_dir"]) / "external/status.json").exists()

    with pytest.raises(run_service.RunServiceError, match="external test id mismatch"):
        run_service.link_existing_external_run(
            reserved["run_id"],
            state_dir=tmp_path / "state",
            external_test_run_id="moog-existing-2",
            link_evidence={
                **evidence,
                "test_run_id": "moog-existing-2",
            },
        )

    linked = run_service.link_existing_external_run(
        reserved["run_id"],
        state_dir=tmp_path / "state",
        external_test_run_id=external_test_run_id,
        link_evidence=evidence,
    )
    repeated = run_service.link_existing_external_run(
        reserved["run_id"],
        state_dir=tmp_path / "state",
        external_test_run_id=external_test_run_id,
        link_evidence=evidence,
    )

    run_dir = Path(reserved["run_dir"])
    assert linked["state"] == "running"
    assert repeated == linked
    assert linked["provenance"]["external_test_run_id"] == external_test_run_id
    assert linked["provenance"]["linked_existing"] is True
    assert json.loads((run_dir / "external/link.json").read_text()) == evidence
    assert not (run_dir / "external/submission.json").exists()
    assert len(run_service.RunLedger(tmp_path / "state").list_entries()) == 1

def test_existing_external_identity_rejects_changed_fact_metadata(tmp_path):
    common = {
        "backend": "antithesis",
        "origin": "external",
        "runs_dir": tmp_path / "runs",
        "state_dir": tmp_path / "state",
    }
    original = run_service.ExternalRunRequest(
        launch={
            "transport": "moog",
            "mode": "link-existing",
            "external_test_run_id": "moog-existing-1",
            "repository": "cyber-castellum/dwarf",
            "commit": "a" * 40,
        },
        **common,
    )
    changed = run_service.ExternalRunRequest(
        launch={
            **original.launch,
            "commit": "b" * 40,
        },
        **common,
    )

    first = run_service.reserve_external_run(original)
    with pytest.raises(
        run_service.RunServiceError, match="retained external launch request"
    ):
        run_service.reserve_external_run(changed)

    assert len(run_service.RunLedger(tmp_path / "state").list_entries()) == 1
    assert run_service.RunLedger(tmp_path / "state").list_entries()[0]["run_id"] == first[
        "run_id"
    ]


@pytest.mark.parametrize("different", [False, True])
def test_existing_external_link_is_concurrency_safe(tmp_path, different):
    external_test_run_id = "moog-existing-concurrent"
    reserved = run_service.reserve_external_run(
        run_service.ExternalRunRequest(
            backend="antithesis",
            origin="external",
            launch={
                "transport": "moog",
                "mode": "link-existing",
                "external_test_run_id": external_test_run_id,
                "repository": "cyber-castellum/dwarf",
                "commit": "a" * 40,
            },
            runs_dir=tmp_path / "runs",
            state_dir=tmp_path / "state",
        )
    )
    evidence = [
        {
            "test_run_id": external_test_run_id,
            "source": "moog-facts",
            "fact_sha256": "sha256:" + "a" * 64,
        },
        {
            "test_run_id": external_test_run_id,
            "source": "moog-facts",
            "fact_sha256": "sha256:" + ("b" if different else "a") * 64,
        },
    ]
    barrier = threading.Barrier(3)

    def link(index):
        barrier.wait()
        return index, run_service.link_existing_external_run(
            reserved["run_id"],
            state_dir=tmp_path / "state",
            external_test_run_id=external_test_run_id,
            link_evidence=evidence[index],
        )

    lock_path = Path(reserved["run_dir"]) / "external/link.lock"
    with lock_path.open("a+b") as lock_stream:
        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(link, index) for index in range(2)]
                barrier.wait()
                time.sleep(0.05)
                blocked = all(not future.done() for future in futures)
                fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)
                outcomes = []
                for future in futures:
                    try:
                        outcomes.append(("ok", future.result(timeout=5)))
                    except run_service.RunServiceError as exc:
                        outcomes.append(("error", str(exc)))
        finally:
            fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)

    assert blocked is True
    if different:
        assert [kind for kind, _ in outcomes].count("ok") == 1
        assert [kind for kind, _ in outcomes].count("error") == 1
        successful_index = next(value[0] for kind, value in outcomes if kind == "ok")
        assert json.loads(
            (Path(reserved["run_dir"]) / "external/link.json").read_text()
        ) == evidence[successful_index]
    else:
        assert [kind for kind, _ in outcomes] == ["ok", "ok"]
        assert outcomes[0][1][1] == outcomes[1][1][1]
    assert run_service.RunLedger(tmp_path / "state").get(reserved["run_id"])[
        "state"
    ] == "running"


def test_external_status_cannot_race_a_pending_existing_link(tmp_path):
    external_test_run_id = "moog-existing-status-race"
    reserved = run_service.reserve_external_run(
        run_service.ExternalRunRequest(
            backend="antithesis",
            origin="external",
            launch={
                "transport": "moog",
                "mode": "link-existing",
                "external_test_run_id": external_test_run_id,
                "repository": "cyber-castellum/dwarf",
                "commit": "c" * 40,
            },
            runs_dir=tmp_path / "runs",
            state_dir=tmp_path / "state",
        )
    )
    evidence = {
        "test_run_id": external_test_run_id,
        "source": "moog-facts",
        "fact_sha256": "sha256:" + "c" * 64,
    }
    lock_path = Path(reserved["run_dir"]) / "external/link.lock"
    pool = ThreadPoolExecutor(max_workers=1)
    with lock_path.open("a+b") as lock_stream:
        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
        try:
            future = pool.submit(
                run_service.link_existing_external_run,
                reserved["run_id"],
                state_dir=tmp_path / "state",
                external_test_run_id=external_test_run_id,
                link_evidence=evidence,
            )
            time.sleep(0.05)
            assert future.done() is False
            status_rejected = False
            try:
                run_service.reconcile_external_status(
                    reserved["run_id"],
                    state_dir=tmp_path / "state",
                    phase="finished",
                    status_evidence={
                        "test_run_id": external_test_run_id,
                        "phase": "finished",
                    },
                )
            except run_service.RunServiceError as exc:
                status_rejected = "not bound" in str(exc)
            status_written_before_link = (
                Path(reserved["run_dir"]) / "external/status.json"
            ).exists()
        finally:
            fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)
    linked = future.result(timeout=5)
    pool.shutdown(wait=True)

    assert status_rejected is True
    assert status_written_before_link is False
    assert linked["state"] == "running"


def test_external_launch_idempotency_rejects_a_different_scenario_binding(tmp_path):
    common = {
        "backend": "antithesis",
        "origin": "external",
        "launch": {
            "transport": "moog",
            "repository": "pragma-org/dwarf",
            "commit": "9" * 40,
            "try": 1,
        },
        "runs_dir": tmp_path / "runs",
        "state_dir": tmp_path / "state",
    }
    first = run_service.reserve_external_run(
        run_service.ExternalRunRequest(
            scenario_path=PARITY_SCENARIO,
            **common,
        )
    )

    with pytest.raises(run_service.RunServiceError, match="external scenario"):
        run_service.reserve_external_run(
            run_service.ExternalRunRequest(
                scenario_path=SCENARIO,
                **common,
            )
        )

    retained = run_service.RunLedger(tmp_path / "state").get(first["run_id"])
    assert retained["scenario"] == first["ledger"]["scenario"]


def test_execute_run_refuses_non_local_backend_before_engine_execution(
    tmp_path, monkeypatch
):
    calls = []
    monkeypatch.setattr(
        run_service.scenario_module,
        "run_scenario",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    with pytest.raises(run_service.RunServiceError, match="backend.*not implemented"):
        run_service.execute_run(
            run_service.RunRequest(
                scenario_path=PARITY_SCENARIO,
                origin="external",
                backend="antithesis",
                runs_dir=tmp_path / "runs",
                state_dir=tmp_path / "state",
            )
        )

    assert calls == []
    assert not (tmp_path / "runs").exists()


def test_service_binds_preloaded_scenario_registry_and_release_to_engine(
    tmp_path, monkeypatch
):
    observed = {}

    def stop_after_binding(path, **kwargs):
        observed.update(kwargs)
        raise RuntimeError("binding observed")

    monkeypatch.setattr(run_service.scenario_module, "run_scenario", stop_after_binding)

    with pytest.raises(RuntimeError, match="binding observed"):
        run_service.execute_run(
            run_service.RunRequest(
                scenario_path=PARITY_SCENARIO,
                origin="dashboard",
                runs_dir=tmp_path / "runs",
                state_dir=tmp_path / "state",
            )
        )

    plan = observed["run_service_plan"]
    assert observed["scenario_definition"].raw_bytes == PARITY_SCENARIO.read_bytes()
    assert observed["registry_definition"]
    assert observed["framework_commit"] == plan["resolved"]["release_revision"]
    assert plan["resolved"]["registry"]["entries"]


def test_service_executes_engine_once_and_returns_ledger_manifest_parity(
    tmp_path, monkeypatch
):
    run_dir = tmp_path / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    scenario_sha256 = run_service._sha256(PARITY_SCENARIO.read_bytes()).removeprefix(
        "sha256:"
    )
    release_revision = "5" * 40
    manifest = {
        "run_id": "run-1",
        "framework": {"commit": release_revision, "version": "0.1.0"},
        "scenario": {
            "id": "run-service-parity",
            "sha256": scenario_sha256,
        },
        "exit_status": "pass",
        "execution": {"security_verdict": "pass"},
        "assertion_summary": {"total": 1, "pass": 1, "fail": 0},
    }
    calls = []

    def fake_run(path, **kwargs):
        calls.append((Path(path), kwargs))
        plan = kwargs["run_service_plan"]
        inputs = run_dir / "inputs"
        inputs.mkdir()
        (inputs / "run-service-plan.json").write_text(
            json.dumps(plan, sort_keys=True), encoding="utf-8"
        )
        (run_dir / "scenario.yaml").write_bytes(
            kwargs["scenario_definition"].raw_bytes
        )
        manifest["run_service"] = {
            "plan_path": "inputs/run-service-plan.json",
            "plan_sha256": hashlib.sha256(
                run_service._canonical_bytes(plan)
            ).hexdigest(),
        }
        (run_dir / "manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        ledger = run_service.RunLedger(tmp_path / "state")
        ledger.create(
            run_id="run-1",
            kind="scenario",
            origin="dashboard",
            backend="local",
            scenario={"id": "run-service-parity", "sha256": scenario_sha256},
            release_revision=release_revision,
            evidence_path=str(run_dir),
            provenance={"actor": "run-service:dashboard"},
        )
        ledger.transition("run-1", "running")
        return SimpleNamespace(run_id="run-1", run_dir=run_dir)

    monkeypatch.setattr(run_service.scenario_module, "run_scenario", fake_run)
    monkeypatch.setenv("DWARF_SOURCE_REVISION", release_revision)
    monkeypatch.setattr(
        run_service.forensic_module,
        "verify",
        lambda *args, **kwargs: SimpleNamespace(ok=True, errors=[]),
    )

    result = run_service.execute_run(
        run_service.RunRequest(
            scenario_path=PARITY_SCENARIO,
            origin="dashboard",
            runs_dir=tmp_path / "runs",
            state_dir=tmp_path / "state",
        )
    )

    assert len(calls) == 1
    assert calls[0][1]["origin"] == "dashboard"
    assert calls[0][1]["backend"] == "local"
    assert calls[0][1]["run_kind"] == "scenario"
    assert result["manifest"] == manifest
    assert result["ledger"]["verdict"] == "pass"
    assert result["plan"]["plan_digest"] == json.loads(
        (run_dir / "inputs/run-service-plan.json").read_text()
    )["plan_digest"]


@pytest.mark.parametrize(
    ("target", "message"),
    [
        ("retained-plan", "retained run plan"),
        ("manifest", "manifest scenario"),
        ("ledger", "ledger origin"),
    ],
)
def test_service_rejects_tampered_plan_manifest_or_ledger(
    tmp_path, monkeypatch, target, message
):
    run_dir = tmp_path / "runs" / "run-tampered"
    run_dir.mkdir(parents=True)
    release_revision = "6" * 40
    scenario_sha256 = run_service._sha256(PARITY_SCENARIO.read_bytes()).removeprefix(
        "sha256:"
    )

    def fake_run(path, **kwargs):
        plan = json.loads(json.dumps(kwargs["run_service_plan"]))
        if target == "retained-plan":
            plan["invocation"]["origin"] = "external"
        inputs = run_dir / "inputs"
        inputs.mkdir()
        (inputs / "run-service-plan.json").write_text(json.dumps(plan))
        (run_dir / "scenario.yaml").write_bytes(
            kwargs["scenario_definition"].raw_bytes
        )
        manifest_scenario_id = (
            "wrong-scenario" if target == "manifest" else "run-service-parity"
        )
        attested_plan = kwargs["run_service_plan"]
        (run_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "run_id": "run-tampered",
                    "framework": {"commit": release_revision},
                    "scenario": {
                        "id": manifest_scenario_id,
                        "sha256": scenario_sha256,
                    },
                    "exit_status": "pass",
                    "execution": {"security_verdict": "pass"},
                    "run_service": {
                        "plan_path": "inputs/run-service-plan.json",
                        "plan_sha256": hashlib.sha256(
                            run_service._canonical_bytes(attested_plan)
                        ).hexdigest(),
                    },
                }
            )
        )
        ledger = run_service.RunLedger(tmp_path / "state")
        ledger.create(
            run_id="run-tampered",
            kind="scenario",
            origin="external" if target == "ledger" else "dashboard",
            backend="local",
            scenario={"id": "run-service-parity", "sha256": scenario_sha256},
            release_revision=release_revision,
            evidence_path=str(run_dir),
            provenance={"actor": "run-service:dashboard"},
        )
        ledger.transition("run-tampered", "running")
        return SimpleNamespace(run_id="run-tampered", run_dir=run_dir)

    monkeypatch.setenv("DWARF_SOURCE_REVISION", release_revision)
    monkeypatch.setattr(run_service.scenario_module, "run_scenario", fake_run)
    monkeypatch.setattr(
        run_service.forensic_module,
        "verify",
        lambda *args, **kwargs: SimpleNamespace(ok=True, errors=[]),
    )

    with pytest.raises(run_service.RunServiceError, match=message):
        run_service.execute_run(
            run_service.RunRequest(
                scenario_path=PARITY_SCENARIO,
                origin="dashboard",
                runs_dir=tmp_path / "runs",
                state_dir=tmp_path / "state",
            )
        )


def test_forensic_verify_rejects_retained_run_service_plan_tampering(
    tmp_path, monkeypatch
):
    revision = "7" * 40
    monkeypatch.setenv("DWARF_SOURCE_REVISION", revision)
    result = run_service.execute_run(
        run_service.RunRequest(
            scenario_path=PARITY_SCENARIO,
            origin="dashboard",
            runs_dir=tmp_path / "runs",
            state_dir=tmp_path / "state",
        )
    )
    run_dir = Path(result["run_dir"])
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["run_service"]["plan_path"] == "inputs/run-service-plan.json"
    assert len(manifest["run_service"]["plan_sha256"]) == 64

    retained = json.loads(
        (run_dir / "inputs/run-service-plan.json").read_text(encoding="utf-8")
    )
    retained["invocation"]["origin"] = "external"
    (run_dir / "inputs/run-service-plan.json").write_text(
        json.dumps(retained), encoding="utf-8"
    )

    verified = forensic.verify(
        result["run_id"], runs_dir=tmp_path / "runs", state_dir=tmp_path / "state"
    )
    assert not verified.ok
    assert any("run service plan" in error for error in verified.errors)


def test_reconciliation_failure_cannot_leave_completed_ledger_entry(
    tmp_path, monkeypatch
):
    revision = "8" * 40
    monkeypatch.setenv("DWARF_SOURCE_REVISION", revision)
    monkeypatch.setattr(
        run_service.forensic_module,
        "verify",
        lambda *args, **kwargs: SimpleNamespace(
            ok=False, errors=["injected reconciliation failure"]
        ),
    )

    with pytest.raises(run_service.RunServiceError, match="forensic bundle"):
        run_service.execute_run(
            run_service.RunRequest(
                scenario_path=PARITY_SCENARIO,
                origin="dashboard",
                runs_dir=tmp_path / "runs",
                state_dir=tmp_path / "state",
            )
        )

    entries = run_service.RunLedger(tmp_path / "state").list_entries()
    assert len(entries) == 1
    assert entries[0]["state"] == "failed"
    assert entries[0]["verdict"] == "inconclusive"
    assert entries[0]["provenance"]["reconciliation"]["state"] == "failed"


def test_production_scenario_execution_has_no_run_service_bypass():
    offenders = []
    source_root = ROOT / "dwarf/profile_manager"
    for path in source_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            called = (
                function.attr
                if isinstance(function, ast.Attribute)
                else function.id if isinstance(function, ast.Name) else None
            )
            if called == "run_scenario" and path.name != "run_service.py":
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")

    assert offenders == []


def test_cli_local_scenario_run_is_a_thin_run_service_adapter(tmp_path, monkeypatch):
    recorded = []

    def fake_execute(request):
        recorded.append(request)
        return {
            "run_id": "service-run",
            "run_dir": str(tmp_path / "runs/service-run"),
            "manifest": {
                "exit_status": "pass",
                "execution": {"security_verdict": "pass"},
                "assertion_summary": {"total": 1, "pass": 1, "fail": 0},
            },
        }

    monkeypatch.setattr(run_service, "execute_run", fake_execute)
    args = Namespace(
        scenario_command="run",
        path=str(SCENARIO),
        runs_dir=str(tmp_path / "runs"),
        state_dir=str(tmp_path / "state"),
        registry_path=None,
        backend="local-devnet",
        origin="scheduler",
        out=None,
        registry=None,
        tag="latest",
        json=False,
    )

    assert cli.cmd_scenario(args) == 0
    assert len(recorded) == 1
    assert recorded[0].origin == "scheduler"
    assert recorded[0].scenario_path == SCENARIO


def test_dashboard_and_scheduler_build_explicit_service_origins(tmp_path):
    dashboard_cmd = dashboard._default_cli_command_builder(
        "scenario_run", scenario_path=str(SCENARIO), run_origin="dashboard"
    )
    assert dashboard_cmd[-2:] == ["--origin", "dashboard"]

    captured = []

    def builder(action, **kwargs):
        captured.append((action, kwargs))
        return ["true"]

    entry = {"id": "scheduled-1", "scenario_path": str(SCENARIO)}
    monkey_runs = tmp_path / "runs"
    monkey_runs.mkdir()
    scheduler.fire_entry(
        entry,
        command_builder=builder,
        runner=lambda _cmd: 0,
        runs_dir=monkey_runs,
    )

    assert captured == [
        (
            "scenario_run",
            {"scenario_path": str(SCENARIO), "run_origin": "scheduler"},
        )
    ]


def test_cli_dashboard_and_scheduler_produce_real_parity_artifacts(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("ADA2_DWARF_CONTROL_SHIM", raising=False)
    roots = {origin: tmp_path / origin for origin in ("cli", "dashboard", "scheduler")}

    def invoke(cmd, origin):
        root = roots[origin]
        env = os.environ.copy()
        env.update(
            {
                "PYTHONPATH": str(ROOT / "dwarf"),
                "ADA2_DWARF_RUNS_DIR": str(root / "runs"),
                "ADA2_DWARF_STATE_DIR": str(root / "state"),
                "ADA2_DWARF_BUNDLES_DIR": str(root / "bundles"),
                "DWARF_SOURCE_REVISION": "5" * 40,
            }
        )
        completed = subprocess.run(
            cmd,
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr or completed.stdout
        return completed.returncode

    invoke(
        [
            sys.executable,
            str(ROOT / "dwarf/cardano-profile"),
            "scenario",
            "run",
            str(PARITY_SCENARIO),
            "--origin",
            "cli",
        ],
        "cli",
    )
    invoke(
        dashboard._default_cli_command_builder(
            "scenario_run",
            scenario_path=str(PARITY_SCENARIO),
            run_origin="dashboard",
        ),
        "dashboard",
    )

    monkeypatch.setattr(schedule_store, "mark_running", lambda _entry_id: None)
    monkeypatch.setattr(
        schedule_store,
        "record_fire",
        lambda _entry_id, *, run_id, fired_at_epoch: {
            "run_id": run_id,
            "fired_at_epoch": fired_at_epoch,
        },
    )
    (roots["scheduler"] / "runs").mkdir(parents=True)
    scheduled = scheduler.fire_entry(
        {"id": "scheduled-parity", "scenario_path": str(PARITY_SCENARIO)},
        command_builder=dashboard._default_cli_command_builder,
        runner=lambda cmd: invoke(cmd, "scheduler"),
        runs_dir=roots["scheduler"] / "runs",
    )
    assert scheduled and scheduled["run_id"]

    observed = {}
    for origin, root in roots.items():
        run_dirs = sorted((root / "runs").iterdir())
        assert len(run_dirs) == 1
        run_dir = run_dirs[0]
        plan = json.loads((run_dir / "inputs/run-service-plan.json").read_text())
        manifest = json.loads((run_dir / "manifest.json").read_text())
        ledger = json.loads(
            (root / "state/run-ledger/entries" / f"{run_dir.name}.json").read_text()
        )
        observed[origin] = {
            "run_dir": run_dir,
            "plan": plan,
            "manifest": manifest,
            "ledger": ledger,
            "layout": {
                str(path.relative_to(run_dir))
                for path in run_dir.rglob("*")
                if path.is_file()
            },
            "verify": forensic.verify(
                run_dir.name,
                runs_dir=root / "runs",
                state_dir=root / "state",
            ),
        }

    digests = {item["plan"]["plan_digest"] for item in observed.values()}
    resolved = {
        json.dumps(item["plan"]["resolved"], sort_keys=True)
        for item in observed.values()
    }
    assert len(digests) == 1
    assert len(resolved) == 1
    assert {
        origin: item["plan"]["invocation"]["origin"]
        for origin, item in observed.items()
    } == {"cli": "cli", "dashboard": "dashboard", "scheduler": "scheduler"}
    assert {
        origin: item["ledger"]["origin"] for origin, item in observed.items()
    } == {"cli": "cli", "dashboard": "dashboard", "scheduler": "scheduler"}
    assert {
        item["manifest"]["execution"]["security_verdict"]
        for item in observed.values()
    } == {"pass"}
    assert {
        json.dumps(item["manifest"]["assertion_summary"], sort_keys=True)
        for item in observed.values()
    } == {'{"fail": 0, "pass": 1, "total": 1}'}
    assert len({frozenset(item["layout"]) for item in observed.values()}) == 1
    assert len({frozenset(item["ledger"]) for item in observed.values()}) == 1
    assert all(item["verify"].ok for item in observed.values())
