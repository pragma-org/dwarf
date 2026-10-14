from __future__ import annotations

import json
import io
from pathlib import Path
import shutil
import subprocess
import tarfile

import pytest

from profile_manager import cli, dashboard
from profile_manager.data import bundle_import as dashboard_bundle_import
from profile_manager.data.operate_run import operate_run_detail
from profile_manager import run_service
from scripts import bundle_import, runtime_bundle_export, runtime_bundle_sign


ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / "tests/fixtures/run-service-parity.yaml"


def _run_bundle(
    root: Path,
    run_id: str = "external-result-1",
    *,
    scenario: dict | None = None,
    external: dict | None = None,
    exit_status: str = "pass",
    security_verdict: str = "pass",
    marker: str | None = None,
) -> Path:
    run_dir = root / run_id
    run_dir.mkdir(parents=True)
    manifest = {
        "run_id": run_id,
        "exit_status": exit_status,
        "execution": {"security_verdict": security_verdict},
        "assertion_summary": {"total": 1, "pass": 1, "fail": 0},
    }
    if scenario is not None:
        manifest["scenario"] = scenario
    if external is not None:
        manifest["external"] = external
    if marker is not None:
        manifest["artifact_marker"] = marker
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    from profile_manager import forensic

    (run_dir / "chain.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "manifest_hash": forensic._sha256_hex(
                    forensic._canonical_json(manifest)
                ),
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "assertions.json").write_text(
        json.dumps([{"id": "real-assertion", "result": "pass"}]),
        encoding="utf-8",
    )
    return run_dir


def test_signed_bundle_import_verifies_then_copies_exact_result(tmp_path):
    run_dir = _run_bundle(tmp_path / "source")
    export_dir = tmp_path / "export"
    exported = runtime_bundle_export.run_export(
        run_dir=run_dir,
        output_dir=export_dir,
        target_run_id=run_dir.name,
        signing_actor="ci",
        key_path=None,
    )

    result = bundle_import.import_bundle(
        tarball_path=export_dir / exported["tarball_filename"],
        signature_path=export_dir / "signature.json",
        runs_dir=tmp_path / "imported",
    )

    assert result["verdict"] == "imported"
    assert result["signature_verdict"] == "verified"
    assert len(result["signing_public_key_hex"]) == 64
    imported = Path(result["destination_dir"])
    assert json.loads((imported / "manifest.json").read_text())["run_id"] == run_dir.name
    assert (imported / "assertions.json").read_bytes() == (
        run_dir / "assertions.json"
    ).read_bytes()


def test_signed_bundle_import_rejects_tampered_tarball_without_copying(tmp_path):
    run_dir = _run_bundle(tmp_path / "source")
    export_dir = tmp_path / "export"
    exported = runtime_bundle_export.run_export(
        run_dir=run_dir,
        output_dir=export_dir,
        target_run_id=run_dir.name,
        signing_actor="ci",
        key_path=None,
    )
    tarball = export_dir / exported["tarball_filename"]
    tarball.write_bytes(tarball.read_bytes() + b"tampered")

    result = bundle_import.import_bundle(
        tarball_path=tarball,
        signature_path=export_dir / "signature.json",
        runs_dir=tmp_path / "imported",
    )

    assert result["verdict"] == "tampered"
    assert not (tmp_path / "imported" / run_dir.name).exists()


def test_signed_bundle_import_binds_v2_target_identity(tmp_path):
    run_dir = _run_bundle(tmp_path / "source", "actual-result-id")
    export_dir = tmp_path / "export"
    exported = runtime_bundle_export.run_export(
        run_dir=run_dir,
        output_dir=export_dir,
        target_run_id=run_dir.name,
        signing_actor="real-export",
        key_path=None,
    )
    tarball = export_dir / exported["tarball_filename"]
    signing_root = tmp_path / "wrong-target-signing"
    signing_root.mkdir()
    shutil.copy2(tarball, signing_root / tarball.name)
    runtime_bundle_sign.run_signature(
        run_dir=signing_root,
        output_dir=signing_root / "outputs" / "signature",
        target_run_id="different-result-id",
        signing_actor="trusted-signer",
        key_path=None,
    )

    result = bundle_import.import_bundle(
        tarball_path=tarball,
        signature_path=signing_root / "outputs" / "signature" / "signature.json",
        runs_dir=tmp_path / "imported",
    )

    assert result["verdict"] == "tampered"
    assert result["reason"] == "signature_target_identity_mismatch"
    assert not (tmp_path / "imported" / run_dir.name).exists()


def test_trusted_empty_signature_cannot_exclude_and_authenticate_forged_tarball(
    tmp_path,
):
    external = run_service.reserve_external_run(
        run_service.ExternalRunRequest(
            backend="antithesis",
            launch={"transport": "moog", "commit": "f" * 40, "try": 1},
            runs_dir=tmp_path / "runs",
            state_dir=tmp_path / "state",
            scenario_path=SCENARIO,
        )
    )
    run_service.record_external_submission(
        external["run_id"],
        state_dir=tmp_path / "state",
        response={"returncode": 0, "stdout": "submitted", "stderr": ""},
        external_test_run_id="antithesis-forged-1",
    )
    forged = _run_bundle(
        tmp_path / "forged-source",
        "antithesis-forged-result",
        scenario=external["ledger"]["scenario"],
        external={"backend": "antithesis", "test_run_id": "antithesis-forged-1"},
    )
    export_dir = tmp_path / "export"
    exported = runtime_bundle_export.run_export(
        run_dir=forged,
        output_dir=export_dir,
        target_run_id=forged.name,
        signing_actor="ignored-real-export",
        key_path=None,
    )
    tarball = export_dir / exported["tarball_filename"]

    empty = tmp_path / "empty-signing-root"
    empty.mkdir()
    signature = runtime_bundle_sign.run_signature(
        run_dir=empty,
        output_dir=empty / "outputs" / "signature",
        target_run_id=forged.name,
        signing_actor="trusted-but-replayed",
        key_path=None,
    )
    signature_path = empty / "outputs" / "signature" / "signature.json"
    signature_payload = json.loads(signature_path.read_text())
    signature_payload["excluded_relpaths"].append(tarball.name)
    signature_path.write_text(json.dumps(signature_payload), encoding="utf-8")

    terminal = run_service.reconcile_external_status(
        external["run_id"],
        state_dir=tmp_path / "state",
        phase="completed",
        status_evidence={
            "test_run_id": "antithesis-forged-1",
            "phase": "completed",
        },
        result_bundle_path=tarball,
        result_signature_path=signature_path,
        trusted_public_key_hex=signature["public_key_hex"],
        result_runs_dir=tmp_path / "external-results",
    )

    assert terminal["state"] == "completed"
    assert terminal["verdict"] == "inconclusive"
    assert terminal["manifest_path"] is None
    assert terminal["provenance"]["verdict_reason"] == "result_bundle_not_verified"


def test_signature_metadata_comes_from_the_same_snapshot_that_was_verified(
    tmp_path, monkeypatch
):
    run_dir = _run_bundle(tmp_path / "source")
    original_export = tmp_path / "original-export"
    replacement_export = tmp_path / "replacement-export"
    original = runtime_bundle_export.run_export(
        run_dir=run_dir,
        output_dir=original_export,
        target_run_id=run_dir.name,
        signing_actor="verified-actor",
        key_path=None,
    )
    runtime_bundle_export.run_export(
        run_dir=run_dir,
        output_dir=replacement_export,
        target_run_id=run_dir.name,
        signing_actor="replacement-actor",
        key_path=None,
    )
    signature_path = original_export / "signature.json"
    replacement_signature_path = replacement_export / "signature.json"
    real_verify = runtime_bundle_sign.verify_signature

    def verify_then_replace(**kwargs):
        result = real_verify(**kwargs)
        signature_path.write_bytes(replacement_signature_path.read_bytes())
        return result

    monkeypatch.setattr(runtime_bundle_sign, "verify_signature", verify_then_replace)
    result = bundle_import._verify_export_signature(
        original_export / original["tarball_filename"], signature_path
    )

    assert result["verdict"] == "verified"
    assert result["signing_actor"] == "verified-actor"


def test_external_result_uses_the_exact_input_snapshots_that_were_verified(
    tmp_path, monkeypatch
):
    external = run_service.reserve_external_run(
        run_service.ExternalRunRequest(
            backend="antithesis",
            launch={"transport": "moog", "commit": "8" * 40, "try": 1},
            runs_dir=tmp_path / "runs",
            state_dir=tmp_path / "state",
            scenario_path=SCENARIO,
        )
    )
    run_service.record_external_submission(
        external["run_id"],
        state_dir=tmp_path / "state",
        response={"returncode": 0, "stdout": "submitted", "stderr": ""},
        external_test_run_id="antithesis-snapshot-1",
    )
    common = {
        "run_id": "antithesis-snapshot-result",
        "scenario": external["ledger"]["scenario"],
        "external": {
            "backend": "antithesis",
            "test_run_id": "antithesis-snapshot-1",
        },
    }
    signed_source = _run_bundle(
        tmp_path / "signed-source", marker="signed-original", **common
    )
    replacement_source = _run_bundle(
        tmp_path / "replacement-source", marker="unsigned-replacement", **common
    )
    signed_export = tmp_path / "signed-export"
    replacement_export = tmp_path / "replacement-export"
    signed = runtime_bundle_export.run_export(
        run_dir=signed_source,
        output_dir=signed_export,
        target_run_id=signed_source.name,
        signing_actor="trusted-signer",
        key_path=None,
    )
    replacement = runtime_bundle_export.run_export(
        run_dir=replacement_source,
        output_dir=replacement_export,
        target_run_id=replacement_source.name,
        signing_actor="untrusted-replacement",
        key_path=None,
    )
    signed_tarball = signed_export / signed["tarball_filename"]
    replacement_tarball = replacement_export / replacement["tarball_filename"]
    signature_path = signed_export / "signature.json"
    replacement_signature_path = replacement_export / "signature.json"
    signature = json.loads(signature_path.read_text())
    real_verify = bundle_import._verify_export_signature

    def verify_then_swap(path, signature):
        result = real_verify(path, signature)
        signed_tarball.write_bytes(replacement_tarball.read_bytes())
        signature_path.write_bytes(replacement_signature_path.read_bytes())
        return result

    monkeypatch.setattr(bundle_import, "_verify_export_signature", verify_then_swap)
    terminal = run_service.reconcile_external_status(
        external["run_id"],
        state_dir=tmp_path / "state",
        phase="completed",
        status_evidence={
            "test_run_id": "antithesis-snapshot-1",
            "phase": "completed",
        },
        result_bundle_path=signed_tarball,
        result_signature_path=signature_path,
        trusted_public_key_hex=signature["public_key_hex"],
        result_runs_dir=tmp_path / "external-results",
    )

    assert terminal["verdict"] == "pass"
    assert terminal["provenance"]["result_bundle"]["signing_actor"] == "trusted-signer"
    assert (
        terminal["provenance"]["result_bundle"]["signing_public_key_hex"]
        == signature["public_key_hex"]
    )
    imported_manifest = json.loads(Path(terminal["manifest_path"]).read_text())
    assert imported_manifest["artifact_marker"] == "signed-original"


def test_verified_non_vacuous_result_bundle_sets_external_test_verdict(tmp_path):
    external = run_service.reserve_external_run(
        run_service.ExternalRunRequest(
            backend="antithesis",
            launch={"transport": "moog", "commit": "b" * 40, "try": 1},
            runs_dir=tmp_path / "runs",
            state_dir=tmp_path / "state",
            scenario_path=SCENARIO,
        )
    )
    run_service.record_external_submission(
        external["run_id"],
        state_dir=tmp_path / "state",
        response={"returncode": 0, "stdout": "submitted", "stderr": ""},
        external_test_run_id="antithesis-live-1",
    )
    scenario = external["ledger"]["scenario"]
    source = _run_bundle(
        tmp_path / "source",
        "antithesis-result-1",
        scenario=scenario,
        external={"backend": "antithesis", "test_run_id": "antithesis-live-1"},
    )
    export_dir = tmp_path / "export"
    exported = runtime_bundle_export.run_export(
        run_dir=source,
        output_dir=export_dir,
        target_run_id=source.name,
        signing_actor="antithesis-import",
        key_path=None,
    )
    signature = json.loads((export_dir / "signature.json").read_text())

    initial_terminal = run_service.reconcile_external_status(
        external["run_id"],
        state_dir=tmp_path / "state",
        phase="finished",
        status_evidence={
            "test_run_id": "antithesis-live-1",
            "phase": "finished",
        },
    )
    assert initial_terminal["verdict"] == "inconclusive"

    terminal = run_service.reconcile_external_status(
        external["run_id"],
        state_dir=tmp_path / "state",
        phase="finished",
        status_evidence={
            "test_run_id": "antithesis-live-1",
            "phase": "finished",
        },
        result_bundle_path=export_dir / exported["tarball_filename"],
        result_signature_path=export_dir / "signature.json",
        trusted_public_key_hex=signature["public_key_hex"],
        result_runs_dir=tmp_path / "external-results",
    )

    manifest_path = Path(tmp_path / "external-results" / source.name / "manifest.json")
    assert terminal["state"] == "completed"
    assert terminal["verdict"] == "pass"
    assert terminal["manifest_path"] == str(manifest_path)
    assert terminal["provenance"]["verdict_reason"] == (
        "verified_non_vacuous_result_bundle"
    )

    repeated = run_service.reconcile_external_status(
        external["run_id"],
        state_dir=tmp_path / "state",
        phase="finished",
        status_evidence={
            "test_run_id": "antithesis-live-1",
            "phase": "finished",
        },
        result_bundle_path=export_dir / exported["tarball_filename"],
        result_signature_path=export_dir / "signature.json",
        trusted_public_key_hex=signature["public_key_hex"],
        result_runs_dir=tmp_path / "external-results",
    )
    assert repeated["verdict"] == "pass"
    assert repeated["manifest_path"] == str(manifest_path)
    assert repeated["provenance"]["verdict_reason"] == (
        "verified_non_vacuous_result_bundle"
    )
    assert repeated["provenance"]["result_bundle"]["idempotent"] is True


def test_external_status_refuses_a_different_backend_test_id(tmp_path):
    external = run_service.reserve_external_run(
        run_service.ExternalRunRequest(
            backend="antithesis",
            launch={"transport": "moog", "commit": "c" * 40, "try": 1},
            runs_dir=tmp_path / "runs",
            state_dir=tmp_path / "state",
        )
    )
    run_service.record_external_submission(
        external["run_id"],
        state_dir=tmp_path / "state",
        response={"returncode": 0, "stdout": "submitted", "stderr": ""},
        external_test_run_id="expected-test-id",
    )

    with pytest.raises(run_service.RunServiceError, match="external test id"):
        run_service.reconcile_external_status(
            external["run_id"],
            state_dir=tmp_path / "state",
            phase="completed",
            status_evidence={
                "test_run_id": "different-test-id",
                "phase": "completed",
            },
        )

    retained = run_service.RunLedger(tmp_path / "state").get(external["run_id"])
    assert retained["state"] == "running"
    assert "status_evidence_path" not in retained["provenance"]


def test_signed_but_inconsistent_external_result_remains_inconclusive(tmp_path):
    external = run_service.reserve_external_run(
        run_service.ExternalRunRequest(
            backend="antithesis",
            launch={"transport": "moog", "commit": "d" * 40, "try": 1},
            runs_dir=tmp_path / "runs",
            state_dir=tmp_path / "state",
            scenario_path=SCENARIO,
        )
    )
    run_service.record_external_submission(
        external["run_id"],
        state_dir=tmp_path / "state",
        response={"returncode": 0, "stdout": "submitted", "stderr": ""},
        external_test_run_id="antithesis-live-2",
    )
    source = _run_bundle(
        tmp_path / "source",
        "antithesis-result-invalid",
        scenario=external["ledger"]["scenario"],
        external={"backend": "antithesis", "test_run_id": "antithesis-live-2"},
        exit_status="error",
        security_verdict="pass",
    )
    export_dir = tmp_path / "export"
    exported = runtime_bundle_export.run_export(
        run_dir=source,
        output_dir=export_dir,
        target_run_id=source.name,
        signing_actor="antithesis-import",
        key_path=None,
    )
    signature = json.loads((export_dir / "signature.json").read_text())

    terminal = run_service.reconcile_external_status(
        external["run_id"],
        state_dir=tmp_path / "state",
        phase="completed",
        status_evidence={
            "test_run_id": "antithesis-live-2",
            "phase": "completed",
        },
        result_bundle_path=export_dir / exported["tarball_filename"],
        result_signature_path=export_dir / "signature.json",
        trusted_public_key_hex=signature["public_key_hex"],
        result_runs_dir=tmp_path / "external-results",
    )

    assert terminal["state"] == "completed"
    assert terminal["verdict"] == "inconclusive"
    assert terminal["manifest_path"] is None
    assert terminal["provenance"]["verdict_reason"] == (
        "result_bundle_verdict_mismatch"
    )


def _multipart(boundary: str, fields: dict[str, tuple[str, bytes]]) -> bytes:
    parts = []
    for name, (filename, content) in fields.items():
        parts.extend(
            [
                f"--{boundary}\r\n".encode(),
                (
                    f'Content-Disposition: form-data; name="{name}"; '
                    f'filename="{filename}"\r\n\r\n'
                ).encode(),
                content,
                b"\r\n",
            ]
        )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts)


def test_dashboard_import_requires_and_forwards_detached_signature(
    tmp_path, monkeypatch
):
    observed = {}

    def fake_run(argv, **kwargs):
        observed["argv"] = argv
        observed["bundle"] = Path(argv[2]).read_bytes()
        signature_index = argv.index("--signature-path") + 1
        observed["signature"] = Path(argv[signature_index]).read_bytes()
        return subprocess.CompletedProcess(argv, 0, '{"verdict":"imported"}\n', "")

    monkeypatch.setattr(dashboard_bundle_import, "_runs_dir", lambda: tmp_path / "runs")
    monkeypatch.setattr(dashboard_bundle_import.subprocess, "run", fake_run)
    boundary = "dwarf-boundary"
    status, body = dashboard_bundle_import.handle_bundle_import_post(
        f"multipart/form-data; boundary={boundary}",
        _multipart(
            boundary,
            {
                "bundle": ("result.tar.gz", b"signed archive bytes"),
                "signature": ("signature.json", b'{"signature":"detached"}'),
            },
        ),
    )

    assert status == 200
    assert "Import succeeded" in body
    assert observed["bundle"] == b"signed archive bytes"
    assert observed["signature"] == b'{"signature":"detached"}'
    rendered = dashboard.render_route_html("/operate/bundles")
    assert 'name="signature"' in rendered


def test_dashboard_export_pair_round_trips_through_native_import(tmp_path, monkeypatch):
    runs_dir = tmp_path / "runs"
    source = _run_bundle(runs_dir, "native-browser-import")
    bundles_dir = tmp_path / "bundles"

    bundle_response = dashboard.dispatch_api_request(
        f"/runs/{source.name}/bundle",
        runs_dir=runs_dir,
        bundles_dir=bundles_dir,
    )
    signature_response = dashboard.dispatch_api_request(
        f"/runs/{source.name}/bundle/signature",
        runs_dir=runs_dir,
        bundles_dir=bundles_dir,
    )

    assert bundle_response is not None
    assert signature_response is not None
    assert bundle_response[:2] == (200, "application/gzip")
    assert signature_response[:2] == (200, "application/json")
    detail = operate_run_detail(source.name, runs_dir=runs_dir)
    assert detail is not None
    assert detail["bundle_url"] == f"/runs/{source.name}/bundle"
    assert detail["bundle_signature_url"] == f"/runs/{source.name}/bundle/signature"
    bundle_bytes = bundle_response[2]
    signature_bytes = signature_response[2]
    export_name = f"{source.name}-bundle-export.tar.gz"

    imported_runs = tmp_path / "imported-runs"
    monkeypatch.setattr(dashboard_bundle_import, "_runs_dir", lambda: imported_runs)
    boundary = "native-browser-import-boundary"
    status, body = dashboard_bundle_import.handle_bundle_import_post(
        f"multipart/form-data; boundary={boundary}",
        _multipart(
            boundary,
            {
                "bundle": (export_name, bundle_bytes),
                "signature": ("signature.json", signature_bytes),
            },
        ),
    )

    assert status == 200
    assert "Import succeeded" in body
    assert "&quot;verdict&quot;: &quot;imported&quot;" in body
    assert "&quot;signature_verdict&quot;: &quot;verified&quot;" in body
    assert (imported_runs / source.name / "manifest.json").is_file()

    # The exact signed pair may be submitted again without replacing evidence.
    monkeypatch.setattr(dashboard_bundle_import, "_runs_dir", lambda: runs_dir)
    repeat_status, repeat_body = dashboard_bundle_import.handle_bundle_import_post(
        f"multipart/form-data; boundary={boundary}",
        _multipart(
            boundary,
            {
                "bundle": (export_name, bundle_bytes),
                "signature": ("signature.json", signature_bytes),
            },
        ),
    )
    assert repeat_status == 200, repeat_body
    assert "&quot;idempotent&quot;: true" in repeat_body

    # Idempotence is exact: a changed retained evidence file still collides.
    (source / "assertions.json").write_text("[]", encoding="utf-8")
    collision_status, collision_body = dashboard_bundle_import.handle_bundle_import_post(
        f"multipart/form-data; boundary={boundary}",
        _multipart(
            boundary,
            {
                "bundle": (export_name, bundle_bytes),
                "signature": ("signature.json", signature_bytes),
            },
        ),
    )
    assert collision_status == 502
    assert "&quot;verdict&quot;: &quot;collision&quot;" in collision_body


def test_dashboard_export_pair_rejects_tamper_without_import(tmp_path, monkeypatch):
    runs_dir = tmp_path / "runs"
    source = _run_bundle(runs_dir, "native-browser-tamper")
    bundle_response = dashboard.dispatch_api_request(
        f"/runs/{source.name}/bundle",
        runs_dir=runs_dir,
        bundles_dir=tmp_path / "bundles",
    )
    signature_response = dashboard.dispatch_api_request(
        f"/runs/{source.name}/bundle/signature",
        runs_dir=runs_dir,
        bundles_dir=tmp_path / "bundles",
    )
    assert bundle_response is not None
    assert signature_response is not None

    imported_runs = tmp_path / "tampered-imports"
    monkeypatch.setattr(dashboard_bundle_import, "_runs_dir", lambda: imported_runs)
    boundary = "native-browser-tamper-boundary"
    status, body = dashboard_bundle_import.handle_bundle_import_post(
        f"multipart/form-data; boundary={boundary}",
        _multipart(
            boundary,
            {
                "bundle": (
                    f"{source.name}-bundle-export.tar.gz",
                    bundle_response[2] + b"tampered",
                ),
                "signature": ("signature.json", signature_response[2]),
            },
        ),
    )

    assert status == 502
    assert "&quot;verdict&quot;: &quot;tampered&quot;" in body
    assert not (imported_runs / source.name).exists()


def test_dashboard_import_preserves_safe_signature_bound_unicode_filename(
    tmp_path, monkeypatch
):
    source = _run_bundle(tmp_path / "source", "unicode-browser-import")
    initial_export = tmp_path / "initial-export"
    exported = runtime_bundle_export.run_export(
        run_dir=source,
        output_dir=initial_export,
        target_run_id=source.name,
        signing_actor="unicode-import-test",
        key_path=None,
    )
    signed_input = tmp_path / "signed-input"
    signed_input.mkdir()
    archive_name = "DWARF evidence λ.tar.gz"
    archive = signed_input / archive_name
    shutil.copy2(initial_export / exported["tarball_filename"], archive)
    signature_dir = signed_input / "outputs" / "signature"
    runtime_bundle_sign.run_signature(
        run_dir=signed_input,
        output_dir=signature_dir,
        target_run_id=source.name,
        signing_actor="unicode-import-test",
        key_path=None,
    )

    imported_runs = tmp_path / "unicode-imports"
    monkeypatch.setattr(dashboard_bundle_import, "_runs_dir", lambda: imported_runs)
    boundary = "unicode-filename-boundary"
    status, body = dashboard_bundle_import.handle_bundle_import_post(
        f"multipart/form-data; boundary={boundary}",
        _multipart(
            boundary,
            {
                "bundle": (archive_name, archive.read_bytes()),
                "signature": (
                    "signature.json",
                    (signature_dir / "signature.json").read_bytes(),
                ),
            },
        ),
    )

    assert status == 200
    assert "&quot;verdict&quot;: &quot;imported&quot;" in body
    assert (imported_runs / source.name / "manifest.json").is_file()


def test_idempotent_import_rejects_existing_file_symlink(tmp_path):
    source = _run_bundle(tmp_path / "source", "symlink-collision")
    export_dir = tmp_path / "export"
    exported = runtime_bundle_export.run_export(
        run_dir=source,
        output_dir=export_dir,
        target_run_id=source.name,
        signing_actor="symlink-test",
        key_path=None,
    )
    retained = source / "assertions.json"
    outside = tmp_path / "outside-assertions.json"
    retained.rename(outside)
    retained.symlink_to(outside)

    result = bundle_import.import_bundle(
        tarball_path=export_dir / exported["tarball_filename"],
        signature_path=export_dir / "signature.json",
        runs_dir=source.parent,
    )

    assert result["verdict"] == "collision"
    assert result["reason"] == "existing_destination_unsupported_entry"
    assert result["imported_run_id"] is None


def test_idempotent_import_rejects_existing_run_root_symlink(tmp_path):
    source = _run_bundle(tmp_path / "source", "root-symlink-collision")
    export_dir = tmp_path / "export"
    exported = runtime_bundle_export.run_export(
        run_dir=source,
        output_dir=export_dir,
        target_run_id=source.name,
        signing_actor="root-symlink-test",
        key_path=None,
    )
    external = tmp_path / "external" / source.name
    shutil.copytree(source, external)
    runs_dir = tmp_path / "imports"
    runs_dir.mkdir()
    (runs_dir / source.name).symlink_to(external, target_is_directory=True)

    result = bundle_import.import_bundle(
        tarball_path=export_dir / exported["tarball_filename"],
        signature_path=export_dir / "signature.json",
        runs_dir=runs_dir,
    )

    assert result["verdict"] == "collision"
    assert result["reason"] == "existing_destination_unsupported_entry"
    assert result["imported_run_id"] is None


def test_installed_cli_import_uses_hardened_importer_and_canonical_runs_dir(
    tmp_path, monkeypatch, capsys
):
    source = _run_bundle(tmp_path / "source", "installed-cli-import")
    export_dir = tmp_path / "export"
    exported = runtime_bundle_export.run_export(
        run_dir=source,
        output_dir=export_dir,
        target_run_id=source.name,
        signing_actor="cli-import-test",
        key_path=None,
    )
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("ADA2_DWARF_RUNTIME_ROOT", str(runtime_root))
    args = cli.build_parser().parse_args(
        [
            "bundle",
            "import",
            str(export_dir / exported["tarball_filename"]),
            "--signature-path",
            str(export_dir / "signature.json"),
        ]
    )

    assert cli.cmd_bundle(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "imported"
    assert Path(payload["destination_dir"]) == runtime_root / "runs" / source.name
    assert (runtime_root / "runs" / source.name / "manifest.json").is_file()


def _archive_with_files(path: Path, sizes: list[int]) -> None:
    with tarfile.open(path, "w:gz") as handle:
        for index, size in enumerate(sizes):
            info = tarfile.TarInfo(f"run/file-{index}")
            info.size = size
            handle.addfile(info, io.BytesIO(b"x" * size))


def test_bundle_extraction_enforces_member_file_and_total_limits(
    tmp_path, monkeypatch
):
    archive = tmp_path / "oversized.tar.gz"
    _archive_with_files(archive, [4, 4, 4])
    monkeypatch.setattr(bundle_import, "MAX_ARCHIVE_MEMBERS", 2, raising=False)
    monkeypatch.setattr(bundle_import, "MAX_ARCHIVE_FILE_BYTES", 3, raising=False)
    monkeypatch.setattr(bundle_import, "MAX_ARCHIVE_EXPANDED_BYTES", 7, raising=False)

    with tarfile.open(archive, "r:gz") as handle:
        with pytest.raises(ValueError, match="archive member limit exceeded"):
            bundle_import._safe_extract(handle, tmp_path / "member-limit")

    monkeypatch.setattr(bundle_import, "MAX_ARCHIVE_MEMBERS", 10, raising=False)
    with tarfile.open(archive, "r:gz") as handle:
        with pytest.raises(ValueError, match="archive file size limit exceeded"):
            bundle_import._safe_extract(handle, tmp_path / "file-limit")

    monkeypatch.setattr(bundle_import, "MAX_ARCHIVE_FILE_BYTES", 10, raising=False)
    with tarfile.open(archive, "r:gz") as handle:
        with pytest.raises(ValueError, match="archive expanded size limit exceeded"):
            bundle_import._safe_extract(handle, tmp_path / "total-limit")
