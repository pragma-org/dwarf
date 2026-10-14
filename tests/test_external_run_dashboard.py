import hashlib
import json
from pathlib import Path

from profile_manager.data.operate_run import operate_run_detail
from profile_manager.data.operate_runs import _enrich_run_row
from profile_manager.run_ledger import RunLedger
from profile_manager.templating import render
from profile_manager.views.operate_run import render_operate_run
from profile_manager.views.operate_runs import render_operate_runs


def _linked_external_run(
    tmp_path: Path,
    *,
    scenario: dict | None = None,
    terminal: bool = True,
    plan_runs_dir: Path | None = None,
    ledger_evidence_path: Path | None = None,
) -> tuple[Path, str]:
    runs_dir = tmp_path / "runs"
    state_dir = tmp_path / "state"
    run_id = "external-antithesis-dashboard-proof"
    run_dir = runs_dir / run_id
    (run_dir / "inputs").mkdir(parents=True)
    (run_dir / "external").mkdir()
    launch = {
        "mode": "link-existing",
        "transport": "moog",
        "external_test_run_id": "moog-run-123",
        "repository": "cyber-castellum/dwarf",
        "github_user": "j-gainsec",
        "directory": "antithesis/example",
        "commit": "a" * 40,
        "try": 1,
        "duration_hours": 1,
        "faults": True,
    }
    resolved = {
        "schema_version": "dwarf.external-run.plan.v1",
        "launch": launch,
        "release_revision": "b" * 40,
        "scenario": scenario,
        "evidence_layout": "external-run-evidence-v1",
        "verdict_contract": "verified-non-vacuous-bundle-or-inconclusive-v1",
    }
    plan = {
        "schema_version": "dwarf.run-service.invocation.v1",
        "plan_digest": "sha256:"
        + hashlib.sha256(
            json.dumps(
                resolved, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        ).hexdigest(),
        "resolved": resolved,
        "invocation": {"origin": "external", "backend": "antithesis"},
        "storage": {
            "runs_dir": str(plan_runs_dir or runs_dir),
            "state_dir": str(state_dir),
        },
    }
    fact = {
        "id": "moog-run-123",
        "key": {
            "commitId": "a" * 40,
            "directory": "antithesis/example",
            "repository": {
                "organization": "cyber-castellum",
                "repo": "dwarf",
            },
            "requester": "j-gainsec",
            "try": 1,
        },
        "value": {
            "duration": 1,
            "from": {"from": {"faults_enabled": True}},
            "phase": "finished",
            "outcome": "failure",
        },
    }
    (run_dir / "inputs" / "run-service-plan.json").write_text(json.dumps(plan))
    (run_dir / "external" / "launch.json").write_text(json.dumps(launch))
    (run_dir / "external" / "link.json").write_text(
        json.dumps({"source": "moog-facts", "test_run_id": "moog-run-123", "facts": [fact]})
    )
    (run_dir / "external" / "status.json").write_text(
        json.dumps({"test_run_id": "moog-run-123", "phase": "finished", "facts": [fact]})
    )
    external_key = "sha256:" + hashlib.sha256(
        json.dumps(
            {
                "backend": "antithesis",
                "transport": "moog",
                "external_test_run_id": "moog-run-123",
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    ledger = RunLedger(state_dir)
    ledger.create(
        run_id=run_id,
        kind="external",
        origin="external",
        backend="antithesis",
        scenario=scenario,
        release_revision="b" * 40,
        evidence_path=str(ledger_evidence_path or run_dir),
        provenance={"external_key": external_key},
    )
    linked_provenance = {
        "external_key": external_key,
        "external_test_run_id": "moog-run-123",
        "linked_existing": True,
    }
    ledger.transition(run_id, "running", provenance=linked_provenance)
    if terminal:
        ledger.transition(
        run_id,
        "completed",
        verdict="inconclusive",
        provenance={
            **linked_provenance,
            "verdict_reason": "no_verified_result_bundle",
        },
        )
    return runs_dir, run_id


def test_linked_external_run_detail_uses_ledger_and_provider_semantics(tmp_path):
    runs_dir, run_id = _linked_external_run(tmp_path)

    detail = operate_run_detail(run_id, runs_dir=runs_dir)

    assert detail is not None
    assert detail["presentation_kind"] == "external"
    assert detail["state"] == "completed"
    assert detail["verdict_status"] == "inconclusive"
    assert detail["backend"] == "antithesis"
    assert detail["external"]["test_run_id"] == "moog-run-123"
    assert detail["external"]["phase"] == "finished"
    assert detail["external"]["outcome"] == "failure"
    assert detail["external"]["linked_existing"] is True
    assert detail["external"]["submission_present"] is False
    assert detail["external"]["result_bundle_present"] is False
    assert detail["external"]["verdict_reason"] == "no_verified_result_bundle"
    assert detail["verify"] == {
        "ok": False,
        "status": "incomplete",
        "label": "INCOMPLETE",
        "errors": [],
    }


def test_linked_external_run_is_inspectable_from_history(tmp_path):
    runs_dir, run_id = _linked_external_run(tmp_path)

    row = _enrich_run_row(
        {
            "run_id": run_id,
            "run_kind": "external",
            "ledger_backed": True,
            "source": "local",
            "state": "completed",
            "verdict": "inconclusive",
        },
        runs_dir,
        profile_ids=set(),
        lifecycle_rows=[],
    )

    assert row["inspectable"] is True
    assert row["run_url"] == f"/operate/runs/{run_id}"


def test_linked_external_history_labels_inconclusive_verdict_honestly(
    tmp_path, monkeypatch
):
    runs_dir, run_id = _linked_external_run(tmp_path)
    other_id = "external-provider-failure"
    ledger = RunLedger(tmp_path / "state")
    ledger.create(
        run_id=other_id,
        kind="external",
        origin="external",
        backend="antithesis",
        evidence_path=str(runs_dir / other_id),
    )
    ledger.transition(other_id, "running")
    ledger.transition(other_id, "failed", verdict="fail")
    bundles_dir = tmp_path / "bundles"
    bundles_dir.mkdir()
    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(runs_dir))
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ADA2_DWARF_BUNDLES_DIR", str(bundles_dir))

    html = render_operate_runs(scope="external")

    assert f'href="/operate/runs/{run_id}"' in html
    assert other_id in html
    assert "■ inconclusive" in html
    assert "inconclusive (1)" in html
    assert "1 inconclusive" in html
    assert "■ no result" not in html

    filtered = render_operate_runs(scope="external", outcome="inconclusive")

    assert run_id in filtered
    assert other_id not in filtered


def test_external_evidence_path_accepts_exact_configured_host_mount_identity(
    tmp_path, monkeypatch
):
    host_runs_dir = Path("/srv/dwarf/runs")
    run_id = "external-antithesis-dashboard-proof"
    runs_dir, _ = _linked_external_run(
        tmp_path,
        plan_runs_dir=host_runs_dir,
        ledger_evidence_path=host_runs_dir / run_id,
    )
    monkeypatch.setenv("DWARF_HOST_RUNS_DIR", str(host_runs_dir))

    detail = operate_run_detail(run_id, runs_dir=runs_dir)

    assert detail is not None
    assert detail["verify"]["status"] == "incomplete"
    assert detail["external"]["receipt_integrity"] == "verified"
    assert detail["external"]["receipt_errors"] == []


def test_external_evidence_path_rejects_unconfigured_host_path(tmp_path, monkeypatch):
    configured_runs_dir = Path("/srv/dwarf/runs")
    wrong_runs_dir = Path("/srv/other/runs")
    run_id = "external-antithesis-dashboard-proof"
    runs_dir, _ = _linked_external_run(
        tmp_path,
        plan_runs_dir=wrong_runs_dir,
        ledger_evidence_path=wrong_runs_dir / run_id,
    )
    monkeypatch.setenv("DWARF_HOST_RUNS_DIR", str(configured_runs_dir))

    detail = operate_run_detail(run_id, runs_dir=runs_dir)

    assert detail is not None
    assert detail["verify"]["status"] == "fail"
    assert detail["external"]["receipt_integrity"] == "fail"
    assert "ledger evidence path does not match this run directory" in detail[
        "external"
    ]["receipt_errors"]


def test_configured_host_root_rejects_process_path_bypass(tmp_path, monkeypatch):
    configured_runs_dir = Path("/srv/dwarf/runs")
    wrong_plan_runs_dir = Path("/srv/wrong/runs")
    run_id = "external-antithesis-dashboard-proof"
    process_run_dir = tmp_path / "runs" / run_id
    runs_dir, _ = _linked_external_run(
        tmp_path,
        plan_runs_dir=wrong_plan_runs_dir,
        ledger_evidence_path=process_run_dir,
    )
    monkeypatch.setenv("DWARF_HOST_RUNS_DIR", str(configured_runs_dir))

    detail = operate_run_detail(run_id, runs_dir=runs_dir)

    assert detail is not None
    assert detail["verify"]["status"] == "fail"
    assert detail["external"]["receipt_integrity"] == "fail"
    assert "ledger evidence path does not match this run directory" in detail[
        "external"
    ]["receipt_errors"]


def test_linked_external_run_page_distinguishes_provider_outcome_from_test_verdict(
    tmp_path,
):
    runs_dir, run_id = _linked_external_run(tmp_path)
    detail = operate_run_detail(run_id, runs_dir=runs_dir)

    html = render(
        "operate/external_run.j2",
        page_title=f"External run · {run_id}",
        density="reading",
        active="operate",
        active_sub="runs",
        run=detail,
    )

    assert "Test verdict" in html
    assert "inconclusive" in html
    assert "Provider outcome" in html
    assert "failure" in html
    assert "No signed DWARF result bundle was supplied" in html
    assert "No submission receipt was created" in html
    assert "Receipt integrity" in html
    assert "verified" in html
    assert "evidence integrity is fail" not in html


def test_external_run_route_selects_external_inspector(tmp_path, monkeypatch):
    runs_dir, run_id = _linked_external_run(tmp_path)
    bundles_dir = tmp_path / "bundles"
    bundles_dir.mkdir()
    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(runs_dir))
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ADA2_DWARF_BUNDLES_DIR", str(bundles_dir))

    html = render_operate_run(run_id)

    assert html is not None
    assert "A ledger-backed external execution linked into DWARF history" in html
    assert "Forensic bundle for one scenario execution" not in html


def test_malformed_external_plan_is_not_advertised_as_inspectable(tmp_path):
    run_id = "external-malformed"
    run_dir = tmp_path / run_id
    (run_dir / "inputs").mkdir(parents=True)
    (run_dir / "inputs" / "run-service-plan.json").write_text("{}")

    row = _enrich_run_row(
        {"run_id": run_id, "run_kind": "external", "source": "local"},
        tmp_path,
        profile_ids=set(),
        lifecycle_rows=[],
    )

    assert row["inspectable"] is False
    assert row["run_url"] is None


def test_external_ledger_identity_wins_over_stray_scenario_manifest(tmp_path):
    runs_dir, run_id = _linked_external_run(tmp_path)
    (runs_dir / run_id / "manifest.json").write_text("{}")

    detail = operate_run_detail(run_id, runs_dir=runs_dir)

    assert detail is not None
    assert detail["presentation_kind"] == "external"
    assert detail["verdict_status"] == "inconclusive"


def test_arbitrary_external_manifest_path_is_never_verified(tmp_path):
    runs_dir, run_id = _linked_external_run(tmp_path)
    fake = tmp_path / "imported" / "fake-result" / "manifest.json"
    fake.parent.mkdir(parents=True)
    fake.write_text("{}")
    RunLedger(tmp_path / "state").reconcile_terminal_evidence(
        run_id,
        verdict="pass",
        manifest_path=str(fake),
        provenance={
            "external_test_run_id": "moog-run-123",
            "linked_existing": True,
            "verdict_reason": "verified_non_vacuous_result_bundle",
            "result_bundle": {
                "verdict": "imported",
                "signature_verdict": "verified",
            },
            "result_bundle_manifest_sha256": "0" * 64,
            "result_bundle_run_id": "fake-result",
        },
    )

    detail = operate_run_detail(run_id, runs_dir=runs_dir)

    assert detail is not None
    assert detail["external"]["result_bundle_present"] is True
    assert detail["external"]["result_bundle_verified"] is False
    assert detail["verify"]["ok"] is False
    assert detail["verify"]["status"] == "fail"


def test_missing_or_malformed_required_external_receipts_fail_integrity(tmp_path):
    runs_dir, run_id = _linked_external_run(tmp_path)
    run_dir = runs_dir / run_id
    (run_dir / "external" / "link.json").write_text("{")
    (run_dir / "external" / "status.json").unlink()

    detail = operate_run_detail(run_id, runs_dir=runs_dir)

    assert detail is not None
    assert detail["external"]["receipt_integrity"] == "fail"
    assert detail["verify"]["status"] == "fail"
    assert any("link receipt is malformed" in error for error in detail["external"]["receipt_errors"])
    assert any("status receipt is missing" in error for error in detail["external"]["receipt_errors"])


def test_non_object_external_plan_fields_fail_closed_without_dashboard_error(tmp_path):
    run_id = "external-malformed-nested"
    run_dir = tmp_path / "runs" / run_id
    (run_dir / "inputs").mkdir(parents=True)
    (run_dir / "inputs" / "run-service-plan.json").write_text(
        json.dumps(
            {
                "schema_version": "dwarf.run-service.invocation.v1",
                "resolved": {
                    "schema_version": "dwarf.external-run.plan.v1",
                    "evidence_layout": "external-run-evidence-v1",
                    "launch": [],
                },
                "invocation": [],
            }
        )
    )
    (run_dir / "manifest.json").write_text("{}")

    row = _enrich_run_row(
        {
            "run_id": run_id,
            "run_kind": "external",
            "ledger_backed": True,
            "source": "local",
        },
        tmp_path / "runs",
        profile_ids=set(),
        lifecycle_rows=[],
    )

    assert row["inspectable"] is False
    assert operate_run_detail(run_id, runs_dir=tmp_path / "runs") is None


def test_external_result_claim_must_match_recomputed_and_ledger_verdict(tmp_path):
    runs_dir, run_id = _linked_external_run(tmp_path)
    result_run_id = "signed-result-1"
    result_dir = tmp_path / "imported" / result_run_id
    result_dir.mkdir(parents=True)
    manifest = {
        "run_id": result_run_id,
        "external": {"test_run_id": "moog-run-123", "backend": "antithesis"},
        "exit_status": "pass",
        "assertion_summary": {"total": 1, "pass": 1, "fail": 0},
        "execution": {"security_verdict": "fail"},
    }
    manifest_hash = hashlib.sha256(
        json.dumps(
            manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()
    (result_dir / "manifest.json").write_text(json.dumps(manifest))
    (result_dir / "chain.json").write_text(
        json.dumps({"run_id": result_run_id, "manifest_hash": manifest_hash})
    )
    (result_dir / "assertions.json").write_text(json.dumps([{"result": "pass"}]))
    RunLedger(tmp_path / "state").reconcile_terminal_evidence(
        run_id,
        verdict="pass",
        manifest_path=str(result_dir / "manifest.json"),
        provenance={
            "external_test_run_id": "moog-run-123",
            "linked_existing": True,
            "verdict_reason": "verified_non_vacuous_result_bundle",
            "result_bundle": {
                "verdict": "imported",
                "signature_verdict": "verified",
            },
            "result_bundle_manifest_sha256": manifest_hash,
            "result_bundle_run_id": result_run_id,
        },
    )

    detail = operate_run_detail(run_id, runs_dir=runs_dir)

    assert detail is not None
    assert detail["external"]["result_bundle_present"] is True
    assert detail["external"]["result_bundle_verified"] is False
    assert any(
        "result bundle verdict is inconsistent" in error
        for error in detail["external"]["result_errors"]
    )


def test_external_result_scenario_must_match_ledger_identity(tmp_path):
    expected_scenario = {"id": "scenario-a", "sha256": "c" * 64}
    runs_dir, run_id = _linked_external_run(tmp_path, scenario=expected_scenario)
    result_run_id = "signed-result-scenario-mismatch"
    result_dir = tmp_path / "imported" / result_run_id
    result_dir.mkdir(parents=True)
    manifest = {
        "run_id": result_run_id,
        "external": {"test_run_id": "moog-run-123", "backend": "antithesis"},
        "scenario": {"id": "scenario-b", "sha256": "d" * 64},
        "exit_status": "pass",
        "assertion_summary": {"total": 1, "pass": 1, "fail": 0},
        "execution": {"security_verdict": "pass"},
    }
    manifest_hash = hashlib.sha256(
        json.dumps(
            manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()
    (result_dir / "manifest.json").write_text(json.dumps(manifest))
    (result_dir / "chain.json").write_text(
        json.dumps({"run_id": result_run_id, "manifest_hash": manifest_hash})
    )
    (result_dir / "assertions.json").write_text(json.dumps([{"result": "pass"}]))
    RunLedger(tmp_path / "state").reconcile_terminal_evidence(
        run_id,
        verdict="pass",
        manifest_path=str(result_dir / "manifest.json"),
        provenance={
            "external_test_run_id": "moog-run-123",
            "linked_existing": True,
            "verdict_reason": "verified_non_vacuous_result_bundle",
            "result_bundle": {
                "verdict": "imported",
                "signature_verdict": "verified",
            },
            "result_bundle_manifest_sha256": manifest_hash,
            "result_bundle_run_id": result_run_id,
        },
    )

    detail = operate_run_detail(run_id, runs_dir=runs_dir)

    assert detail is not None
    assert detail["external"]["result_bundle_verified"] is False
    assert any(
        "result bundle scenario identity is inconsistent" in error
        for error in detail["external"]["result_errors"]
    )


def test_same_external_id_with_different_moog_key_fails_receipt_integrity(tmp_path):
    runs_dir, run_id = _linked_external_run(tmp_path)
    link_path = runs_dir / run_id / "external" / "link.json"
    link = json.loads(link_path.read_text())
    link["facts"][0]["key"]["commitId"] = "f" * 40
    link_path.write_text(json.dumps(link))

    detail = operate_run_detail(run_id, runs_dir=runs_dir)

    assert detail is not None
    assert detail["external"]["receipt_integrity"] == "fail"
    assert any(
        "link fact immutable launch identity does not match" in error
        for error in detail["external"]["receipt_errors"]
    )


def test_malformed_external_result_counts_fail_closed_without_dashboard_error(tmp_path):
    expected_scenario = {"id": "scenario-a", "sha256": "c" * 64}
    runs_dir, run_id = _linked_external_run(tmp_path, scenario=expected_scenario)
    result_run_id = "signed-result-malformed-counts"
    result_dir = tmp_path / "imported" / result_run_id
    result_dir.mkdir(parents=True)
    manifest = {
        "run_id": result_run_id,
        "external": {"test_run_id": "moog-run-123", "backend": "antithesis"},
        "scenario": expected_scenario,
        "exit_status": "pass",
        "assertion_summary": {"total": 1, "pass": "bad", "fail": 0},
        "execution": {"security_verdict": "pass"},
    }
    manifest_hash = hashlib.sha256(
        json.dumps(
            manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()
    (result_dir / "manifest.json").write_text(json.dumps(manifest))
    (result_dir / "chain.json").write_text(
        json.dumps({"run_id": result_run_id, "manifest_hash": manifest_hash})
    )
    (result_dir / "assertions.json").write_text(json.dumps([{"result": "pass"}]))
    RunLedger(tmp_path / "state").reconcile_terminal_evidence(
        run_id,
        verdict="pass",
        manifest_path=str(result_dir / "manifest.json"),
        provenance={
            "external_test_run_id": "moog-run-123",
            "linked_existing": True,
            "verdict_reason": "verified_non_vacuous_result_bundle",
            "result_bundle": {
                "verdict": "imported",
                "signature_verdict": "verified",
            },
            "result_bundle_manifest_sha256": manifest_hash,
            "result_bundle_run_id": result_run_id,
        },
    )

    detail = operate_run_detail(run_id, runs_dir=runs_dir)

    assert detail is not None
    assert detail["external"]["result_bundle_verified"] is False
    assert detail["verify"]["status"] == "fail"
    assert any(
        "assertion summary is inconsistent or vacuous" in error
        for error in detail["external"]["result_errors"]
    )


def test_one_level_moog_request_shape_matches_cli_normalization(tmp_path):
    runs_dir, run_id = _linked_external_run(tmp_path)
    run_dir = runs_dir / run_id
    for name in ("link.json", "status.json"):
        path = run_dir / "external" / name
        receipt = json.loads(path.read_text())
        receipt["facts"][0]["value"] = {
            "from": {"duration": 1, "faults_enabled": True},
            "phase": "finished",
            "outcome": "failure",
        }
        path.write_text(json.dumps(receipt))

    detail = operate_run_detail(run_id, runs_dir=runs_dir)

    assert detail is not None
    assert detail["external"]["receipt_integrity"] == "verified"
    assert detail["external"]["phase"] == "finished"
    assert detail["external"]["outcome"] == "failure"


def test_external_receipts_require_ledger_bound_external_id(tmp_path):
    runs_dir, run_id = _linked_external_run(tmp_path)
    RunLedger(tmp_path / "state").annotate(
        run_id,
        provenance={
            "linked_existing": True,
            "verdict_reason": "no_verified_result_bundle",
        },
    )

    detail = operate_run_detail(run_id, runs_dir=runs_dir)

    assert detail is not None
    assert detail["external"]["receipt_integrity"] == "fail"
    assert any(
        "ledger has no bound external test id" in error
        for error in detail["external"]["receipt_errors"]
    )


def test_forged_plan_and_launch_do_not_bypass_ledger_external_key(tmp_path):
    runs_dir, run_id = _linked_external_run(tmp_path)
    run_dir = runs_dir / run_id
    plan_path = run_dir / "inputs" / "run-service-plan.json"
    launch_path = run_dir / "external" / "launch.json"
    plan = json.loads(plan_path.read_text())
    launch = dict(plan["resolved"]["launch"])
    launch["transport"] = "other"
    launch["commit"] = "f" * 40
    plan["resolved"]["launch"] = launch
    plan["plan_digest"] = "sha256:" + hashlib.sha256(
        json.dumps(
            plan["resolved"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    plan_path.write_text(json.dumps(plan))
    launch_path.write_text(json.dumps(launch))

    detail = operate_run_detail(run_id, runs_dir=runs_dir)

    assert detail is not None
    assert detail["external"]["receipt_integrity"] == "fail"
    assert any(
        "ledger external key does not match the retained plan" in error
        for error in detail["external"]["receipt_errors"]
    )


def test_present_running_status_must_contain_exact_bound_fact(tmp_path):
    runs_dir, run_id = _linked_external_run(tmp_path, terminal=False)
    status_path = runs_dir / run_id / "external" / "status.json"
    status = json.loads(status_path.read_text())
    status["facts"][0]["id"] = "unrelated-moog-run"
    status_path.write_text(json.dumps(status))

    detail = operate_run_detail(run_id, runs_dir=runs_dir)

    assert detail is not None
    assert detail["state"] == "running"
    assert detail["external"]["receipt_integrity"] == "fail"
    assert any(
        "status receipt does not contain exactly the matched external fact" in error
        for error in detail["external"]["receipt_errors"]
    )
