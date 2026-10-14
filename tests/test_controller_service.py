from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid

import pytest

from controller.docker_runtime import DockerRuntimeError
from controller.service import ControllerConflict, ControllerService, _bounded_failure
from profile_manager import dashboard
from profile_manager.data import catalog_definitions as definitions
from profile_manager.data import health


TOKEN = "controller-token-" + "x" * 32
DIGEST = "sha256:" + "a" * 64


class FakeRuntime:
    def __init__(self):
        self.apply_calls = 0
        self.cancel_calls = []
        self.current = {}
        self.block_apply = False
        self.fail_apply = False
        self.failure_message = "retained topology differs from the current catalog plan"
        self.prepare_calls = []
        self.run_calls = []
        self.image_inspect_calls = []
        self.package_calls = []
        self.package_gate = None
        self.package_failure = None
        self.package_cleanup_calls = []
        self.package_cleanup_failure = False

    def health(self):
        return {"docker": "ok", "socket": "ok"}

    def inventory(self):
        return {
            "managed": [],
            "managed_count": 0,
            "unmanaged_count": 3,
            "installation_id": "test-install",
        }

    def inspect_image(self, image, *, implementation):
        self.image_inspect_calls.append((image, implementation))
        return {
            "config_image_id": "sha256:" + "b" * 64,
            "manifest_references": [image],
            "platform": "linux/amd64",
            "runtime_interface": "extracted-binary",
        }

    def package_artifact(self, parameters, execution_id, *, timeout, cancel_event):
        self.package_calls.append((parameters, execution_id, timeout, cancel_event))
        if self.package_failure is not None:
            raise self.package_failure
        if self.package_gate is not None:
            assert self.package_gate.wait(timeout=2)
        return {
            "status": "packaged",
            "config_image_id": "sha256:" + "d" * 64,
            "manifest_references": [],
            "platform": parameters["platform"],
            "runtime_interface": "extracted-binary",
            "executable_sha256": parameters["executable_sha256"],
            "upload_sha256": parameters["upload_sha256"],
            "qualification": "unverified",
        }

    def cleanup_artifact_package(self, parameters, execution_id):
        self.package_cleanup_calls.append((parameters, execution_id))
        if self.package_cleanup_failure:
            raise DockerRuntimeError("cleanup failed")

    def topology_health(self, topology_id):
        return {
            "schema_version": "v1",
            "topology_id": topology_id,
            "state": "healthy",
            "reason_code": "node_and_chain_healthy",
        }

    def snapshot(self, topology_id):
        return self.current.get(topology_id, {"topology_id": topology_id, "resources": []})

    def prepare_topology(self, topology):
        self.prepare_calls.append(topology)
        return {
            "schema_version": "v1",
            "adapter": "runtime_compose_substrate.py",
            "compose_project": "dwarf-profile-test-install-" + topology["topology_id"],
            "substrate": {"nodes": topology["nodes"]},
        }

    def apply(self, plan, execution_id, cancel_event):
        self.apply_calls += 1
        if self.fail_apply:
            raise DockerRuntimeError(self.failure_message)
        if self.block_apply:
            assert cancel_event.wait(timeout=2)
            return {"status": "cancelled", "topology_id": plan["topology_id"]}
        topology = plan.get("topology")
        topology_id = plan["topology_id"]
        self.current[topology_id] = {
            "topology_id": topology_id,
            "resources": list(topology.get("nodes", ())) if topology else [],
        }
        return {"status": "applied", "topology_id": topology_id}

    def cancel(self, execution_id):
        self.cancel_calls.append(execution_id)

    def logs(self, **parameters):
        return {"lines": ["one", "two"], "next_cursor": "cursor-2", **parameters}

    def run_launch(
        self,
        *,
        launch_id,
        plan_digest,
        execution_id,
        timeout,
        cancel_event=None,
        request_controller=None,
    ):
        self.run_calls.append(
            {
                "launch_id": launch_id,
                "plan_digest": plan_digest,
                "execution_id": execution_id,
                "timeout": timeout,
                "cancel_event": cancel_event,
                "request_controller": request_controller,
            }
        )
        return {
            "status": "completed",
            "run_id": "20261008T120000Z-controller",
            "exit_code": 0,
            "exit_status": "pass",
            "security_verdict": "pass",
        }

    def export_topology(self, topology_id):
        candidate = {
            "schema_version": "dwarf.topology-configuration/v1",
            "artifact_kind": "exact_deployed_snapshot",
            "topology_id": topology_id,
            "exported_at": "2026-10-08T12:00:00Z",
            "deployment_evidence": False,
            "profile": {"id": topology_id},
            "resolved": {
                "substrate": {
                    "nodes": [
                        {
                            "id": "node1",
                            "impl": "cardano-node",
                            "version": "11.1.2",
                            "image": "ghcr.io/example/node@" + DIGEST,
                        }
                    ]
                },
                "runtime_parameters": {},
            },
            "observed": {
                "reconciliation_state": "converged",
                "semantic_health": {"state": "healthy"},
                "nodes": [],
            },
            "provenance": {"catalog_revision": "catalog"},
            "redacted_paths": [],
        }
        candidate["content_sha256"] = "sha256:" + hashlib.sha256(
            json.dumps(candidate, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return candidate


def test_save_returns_the_exact_configuration_and_matching_receipt(tmp_path):
    service = ControllerService(
        TOKEN, tmp_path, FakeRuntime(), "revision-123", "test-install"
    )

    status, payload = _dispatch(
        service,
        _request(
            "topology.save",
            topology_id="local-lab",
            file_name="local-lab.json",
            idempotency_key="save-local-lab-001",
        ),
    )

    assert status == 200
    assert payload["content_sha256"].startswith("sha256:")
    assert payload["configuration"]["artifact_kind"] == "exact_deployed_snapshot"
    assert payload["configuration"]["deployment_evidence"] is True
    assert payload["configuration"]["controller_attestation"]["installation_id"] == "test-install"
    stored = json.loads(
        (tmp_path / "controller" / "topologies" / "local-lab.json").read_text()
    )
    assert stored == payload["configuration"]
    receipt = json.loads((tmp_path / payload["audit_receipt"]).read_text())
    assert receipt["after_sha256"] == "sha256:" + hashlib.sha256(
        json.dumps(stored, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def test_save_times_out_without_exporting_while_lifecycle_lock_is_held(tmp_path):
    runtime = FakeRuntime()
    runtime.export_topology = lambda _: (_ for _ in ()).throw(
        AssertionError("export must not race a lifecycle mutation")
    )
    service = ControllerService(TOKEN, tmp_path, runtime, "revision-123", "test-install")
    service._mutation_lock.acquire()
    try:
        status, payload = _dispatch(
            service,
            _request(
                "topology.save",
                topology_id="local-lab",
                file_name="local-lab.json",
                idempotency_key="save-local-lab-locked",
                timeout_seconds=1,
            ),
        )
    finally:
        service._mutation_lock.release()

    assert status == 504
    assert payload["error"] == "controller mutation lock timed out"
    assert not (tmp_path / "controller" / "topologies" / "local-lab.json").exists()


def _request(operation, *, request_id=None, timeout_seconds=10, **parameters):
    return {
        "schema_version": "v1",
        "request_id": request_id or str(uuid.uuid4()),
        "operation": operation,
        "timeout_seconds": timeout_seconds,
        **parameters,
    }


def _topology(topology_id="local-lab"):
    return {
        "topology_id": topology_id,
        "network_magic": 42,
        "nodes": [
            {
                "id": "amaru-1",
                "implementation": "amaru",
                "version": "0.3.0",
                "image": "ghcr.io/pragma-org/amaru@" + DIGEST,
            }
        ],
    }


def _controller_profile(root, topology_id="local-lab"):
    root.mkdir(parents=True, exist_ok=True)
    (root / ".dwarf-catalog.lock").touch()
    directory = root / topology_id
    directory.mkdir()
    source = (
        json.dumps(
            {
                "id": topology_id,
                "label": "Controller lifecycle profile",
                "node_type": "amaru",
                "node_count": 0,
                "amaru_node_count": 1,
                "network_magic": 42,
                "peer_sharing": False,
                "topology_pattern": "local-mesh",
                "shared_genesis": True,
            },
            indent=2,
        )
        + "\n"
    ).encode()
    path = directory / "profile.yaml"
    path.write_bytes(source)
    return path, hashlib.sha256(source).hexdigest()


def _dispatch(service, body, token=TOKEN):
    return service.dispatch(
        "POST",
        "/v1/requests",
        {"Authorization": f"Bearer {token}"},
        json.dumps(body).encode(),
    )


def _plan(service, topology_id="local-lab"):
    status, payload = _dispatch(
        service,
        _request(
            "topology.plan",
            action="deploy",
            topology=_topology(topology_id),
            timeout_seconds=30,
        ),
    )
    assert status == 200
    return payload


def test_service_authenticates_before_parsing_and_health_reports_identity(tmp_path):
    runtime = FakeRuntime()
    service = ControllerService(
        token=TOKEN,
        state_dir=tmp_path,
        runtime=runtime,
        release_revision="revision-123",
        installation_id="test-install",
    )

    status, health = service.dispatch("GET", "/healthz", {}, b"")
    assert status == 200
    assert health == {
        "status": "ok",
        "release_revision": "revision-123",
        "installation_id": "test-install",
        "runtime": {"docker": "ok", "socket": "ok"},
        "state_writable": True,
        "peer_revision": "revision-123",
        "revision_compatible": True,
    }

    status, payload = service.dispatch("POST", "/v1/requests", {}, b"not-json")
    assert status == 401
    assert payload == {"ok": False, "error": "authentication failed"}
    status, payload = _dispatch(service, _request("inventory.get"), token="wrong")
    assert status == 401
    assert payload["error"] == "authentication failed"


def test_service_image_inspect_returns_only_curated_identity_and_declared_provenance(tmp_path):
    runtime = FakeRuntime()
    service = ControllerService(
        token=TOKEN,
        state_dir=tmp_path,
        runtime=runtime,
        release_revision="revision-123",
        installation_id="test-install",
    )
    image = "local/amaru@" + DIGEST

    status, payload = _dispatch(
        service,
        _request(
            "image.inspect",
            implementation="amaru",
            version="10.12.0-local",
            image=image,
            declared_source_revision="c" * 40,
        ),
    )

    assert status == 200
    assert runtime.image_inspect_calls == [(image, "amaru")]
    assert set(payload) == {
        "schema_version", "implementation", "version", "requested_image",
        "config_image_id", "manifest_references", "platform",
        "runtime_interface",
        "declared_source_revision", "provenance", "installation_id", "inspected_at",
    }
    assert payload["provenance"] == {
        "source_revision": "unverified",
        "public": None,
        "qualification": "unverified",
    }
    assert payload["installation_id"] == "test-install"
    assert payload["declared_source_revision"] == "c" * 40
    assert "labels" not in payload and "environment" not in payload


def test_artifact_package_returns_one_handle_and_polls_terminal_result(tmp_path):
    runtime = FakeRuntime()
    runtime.package_gate = threading.Event()
    service = ControllerService(TOKEN, tmp_path, runtime, "revision-123", "test-install")
    execution_id = "execution-0123456789abcdef0123456789abcdef"
    request = _request(
        "artifact.package",
        timeout_seconds=60,
        idempotency_key="package-import-0123456789abcdef01234567",
        import_id="import-0123456789abcdef01234567",
        execution_id=execution_id,
        implementation="amaru",
        version="local-test",
        platform="linux/amd64",
        upload_sha256="sha256:" + "1" * 64,
        executable_sha256="sha256:" + "2" * 64,
        executable_size=128,
    )

    status, accepted = _dispatch(service, request)
    assert status == 202
    assert accepted == {"status": "accepted", "execution_id": execution_id}
    status, running = _dispatch(
        service, _request("execution.status", execution_id=execution_id)
    )
    assert status == 200 and running["state"] == "running"
    duplicate_status, duplicate = _dispatch(service, request)
    assert duplicate_status == 202
    assert duplicate == accepted

    runtime.package_gate.set()
    deadline = time.time() + 2
    while time.time() < deadline:
        _, terminal = _dispatch(
            service, _request("execution.status", execution_id=execution_id)
        )
        if terminal["state"] != "running":
            break
        time.sleep(0.01)
    assert terminal["state"] == "applied"
    assert terminal["result"]["status"] == "packaged"
    assert len(runtime.package_calls) == 1

    restarted = ControllerService(
        TOKEN, tmp_path, FakeRuntime(), "revision-123", "test-install"
    )
    _, retained = _dispatch(
        restarted, _request("execution.status", execution_id=execution_id)
    )
    assert retained["state"] == "applied"
    assert retained["result"]["config_image_id"] == "sha256:" + "d" * 64


def test_artifact_package_reports_only_fixed_safe_failure_receipt(tmp_path):
    runtime = FakeRuntime()
    runtime.package_failure = DockerRuntimeError(
        f"probe stderr contained {TOKEN}",
        code="artifact_interface_probe_failed",
        stage="artifact_probe",
    )
    service = ControllerService(TOKEN, tmp_path, runtime, "revision-123", "test-install")
    execution_id = "execution-1123456789abcdef0123456789abcdef"
    request = _request(
        "artifact.package",
        timeout_seconds=60,
        idempotency_key="package-import-1123456789abcdef01234567",
        import_id="import-1123456789abcdef01234567",
        execution_id=execution_id,
        implementation="amaru",
        version="local-test",
        platform="linux/amd64",
        upload_sha256="sha256:" + "1" * 64,
        executable_sha256="sha256:" + "2" * 64,
        executable_size=128,
    )

    assert _dispatch(service, request)[0] == 202
    deadline = time.time() + 2
    while time.time() < deadline:
        _, terminal = _dispatch(
            service, _request("execution.status", execution_id=execution_id)
        )
        if terminal["state"] != "running":
            break
        time.sleep(0.01)

    assert terminal["state"] == "failed"
    assert terminal["failure"] == {
        "type": "DockerRuntimeError",
        "message": "packaged artifact does not expose the required run interface",
        "code": "artifact_interface_probe_failed",
        "stage": "artifact_probe",
    }
    assert TOKEN not in json.dumps(terminal)


def test_artifact_package_cancelled_during_docker_call_finishes_cancelled(tmp_path):
    class CancelledRuntime(FakeRuntime):
        def package_artifact(
            self, parameters, execution_id, *, timeout, cancel_event
        ):
            self.package_calls.append((parameters, execution_id, timeout, cancel_event))
            assert cancel_event.wait(timeout=2)
            return {"status": "cancelled"}

    runtime = CancelledRuntime()
    service = ControllerService(TOKEN, tmp_path, runtime, "revision-123", "test-install")
    execution_id = "execution-2123456789abcdef0123456789abcdef"
    request = _request(
        "artifact.package",
        timeout_seconds=60,
        idempotency_key="package-import-2123456789abcdef01234567",
        import_id="import-2123456789abcdef01234567",
        execution_id=execution_id,
        implementation="amaru",
        version="local-test",
        platform="linux/amd64",
        upload_sha256="sha256:" + "1" * 64,
        executable_sha256="sha256:" + "2" * 64,
        executable_size=128,
    )
    assert _dispatch(service, request)[0] == 202

    cancel_status, cancelling = _dispatch(
        service,
        _request(
            "execution.cancel",
            execution_id=execution_id,
            reason="operator requested cancellation",
            idempotency_key="cancel-package-2123456789abcdef",
        ),
    )
    assert cancel_status == 200 and cancelling["status"] == "cancelling"
    deadline = time.time() + 2
    while time.time() < deadline:
        _, terminal = _dispatch(
            service, _request("execution.status", execution_id=execution_id)
        )
        if terminal["state"] not in {"running", "cancelling"}:
            break
        time.sleep(0.01)

    assert terminal["state"] == "cancelled"
    assert terminal["result"] == {"status": "cancelled"}
    assert terminal["failure"] is None


def test_artifact_package_cancel_does_not_hide_cleanup_failure(tmp_path):
    class CleanupFailedRuntime(FakeRuntime):
        def package_artifact(
            self, parameters, execution_id, *, timeout, cancel_event
        ):
            assert cancel_event.wait(timeout=2)
            raise DockerRuntimeError(
                "untrusted cleanup detail",
                code="artifact_cleanup_failed",
                stage="artifact_cleanup",
            )

    runtime = CleanupFailedRuntime()
    service = ControllerService(TOKEN, tmp_path, runtime, "revision-123", "test-install")
    execution_id = "execution-4123456789abcdef0123456789abcdef"
    request = _request(
        "artifact.package",
        timeout_seconds=60,
        idempotency_key="package-import-4123456789abcdef01234567",
        import_id="import-4123456789abcdef01234567",
        execution_id=execution_id,
        implementation="amaru",
        version="local-test",
        platform="linux/amd64",
        upload_sha256="sha256:" + "1" * 64,
        executable_sha256="sha256:" + "2" * 64,
        executable_size=128,
    )
    assert _dispatch(service, request)[0] == 202
    assert _dispatch(
        service,
        _request(
            "execution.cancel",
            execution_id=execution_id,
            reason="operator requested cancellation",
            idempotency_key="cancel-package-4123456789abcdef",
        ),
    )[0] == 200
    deadline = time.time() + 2
    while time.time() < deadline:
        _, terminal = _dispatch(
            service, _request("execution.status", execution_id=execution_id)
        )
        if terminal["state"] not in {"running", "cancelling"}:
            break
        time.sleep(0.01)

    assert terminal["state"] == "failed"
    assert terminal["failure"]["code"] == "artifact_cleanup_failed"
    assert terminal["failure"]["message"] == "artifact package cleanup could not be verified"


def test_artifact_probe_failure_is_not_relabelled_when_cancel_arrives_during_cleanup(
    tmp_path,
):
    class ProbeFailedRuntime(FakeRuntime):
        def __init__(self):
            super().__init__()
            self.probe_failed = threading.Event()
            self.release_cleanup = threading.Event()

        def package_artifact(
            self, parameters, execution_id, *, timeout, cancel_event
        ):
            self.probe_failed.set()
            assert self.release_cleanup.wait(timeout=2)
            raise DockerRuntimeError(
                "untrusted probe detail",
                code="artifact_probe_failed",
                stage="artifact_probe",
            )

        def cancel(self, execution_id):
            super().cancel(execution_id)
            self.release_cleanup.set()

    runtime = ProbeFailedRuntime()
    service = ControllerService(TOKEN, tmp_path, runtime, "revision-123", "test-install")
    execution_id = "execution-6123456789abcdef0123456789abcdef"
    request = _request(
        "artifact.package",
        timeout_seconds=60,
        idempotency_key="package-import-6123456789abcdef01234567",
        import_id="import-6123456789abcdef01234567",
        execution_id=execution_id,
        implementation="amaru",
        version="local-test",
        platform="linux/amd64",
        upload_sha256="sha256:" + "1" * 64,
        executable_sha256="sha256:" + "2" * 64,
        executable_size=128,
    )
    assert _dispatch(service, request)[0] == 202
    assert runtime.probe_failed.wait(timeout=2)
    assert _dispatch(
        service,
        _request(
            "execution.cancel",
            execution_id=execution_id,
            reason="operator requested cancellation",
            idempotency_key="cancel-package-6123456789abcdef",
        ),
    )[0] == 200
    deadline = time.time() + 2
    while time.time() < deadline:
        _, terminal = _dispatch(
            service, _request("execution.status", execution_id=execution_id)
        )
        if terminal["state"] not in {"running", "cancelling"}:
            break
        time.sleep(0.01)

    assert terminal["state"] == "failed"
    assert terminal["failure"]["code"] == "artifact_probe_failed"
    assert terminal["failure"]["message"] == "packaged artifact executable smoke failed"
    receipt = json.loads((tmp_path / terminal["audit_receipt"]).read_text())
    assert receipt["status"] == "failed"
    failure_path = next(
        path for path in receipt["artifact_paths"] if path.endswith("-failure.json")
    )
    failure_receipt = json.loads((tmp_path / failure_path).read_text())
    assert failure_receipt["failure"]["code"] == "artifact_probe_failed"


def test_service_returns_typed_topology_health(tmp_path):
    service = ControllerService(
        token=TOKEN,
        state_dir=tmp_path,
        runtime=FakeRuntime(),
        release_revision="revision-123",
        installation_id="test-install",
    )

    status, payload = _dispatch(
        service,
        _request("topology.health", topology_id="profile-e-haskell-preview-proof"),
    )

    assert status == 200
    assert payload["topology_id"] == "profile-e-haskell-preview-proof"
    assert payload["reason_code"] == "node_and_chain_healthy"


def test_run_execute_is_controller_owned_audited_and_status_visible(tmp_path):
    runtime = FakeRuntime()
    service = ControllerService(
        TOKEN, tmp_path, runtime, "revision-123", "test-install"
    )
    body = _request(
        "run.execute",
        launch_id="launch-0123456789abcdef01234567",
        plan_digest=DIGEST,
        execution_id="run-execution-0123456789abcdef",
        idempotency_key="run-launch-0123456789abcdef01234567",
        timeout_seconds=2400,
    )

    status, payload = _dispatch(service, body)

    assert status == 200
    assert payload["status"] == "applied"
    assert payload["result"]["run_id"] == "20261008T120000Z-controller"
    assert len(runtime.run_calls) == 1
    assert runtime.run_calls[0] == {
        "launch_id": "launch-0123456789abcdef01234567",
        "plan_digest": DIGEST,
        "execution_id": "run-execution-0123456789abcdef",
        "timeout": runtime.run_calls[0]["timeout"],
        "cancel_event": runtime.run_calls[0]["cancel_event"],
        "request_controller": runtime.run_calls[0]["request_controller"],
    }
    assert callable(runtime.run_calls[0]["request_controller"])
    assert 2399 <= runtime.run_calls[0]["timeout"] <= 2400
    retained_for_resolution = runtime.run_calls[0]["request_controller"](
        "execution.status",
        {"execution_id": "run-execution-0123456789abcdef"},
        10,
    )
    assert retained_for_resolution["state"] == "applied"
    with pytest.raises(ControllerConflict, match="unsupported"):
        runtime.run_calls[0]["request_controller"](
            "image.inspect",
            {"image": "sha256:" + "a" * 64},
            10,
        )
    receipt = json.loads((tmp_path / payload["audit_receipt"]).read_text())
    assert receipt["operation"] == "run.execute"
    assert receipt["status"] == "applied"
    assert receipt["plan_sha256"] == DIGEST
    status_code, retained = _dispatch(
        service,
        _request(
            "execution.status",
            execution_id="run-execution-0123456789abcdef",
        ),
    )
    assert status_code == 200
    assert retained["state"] == "applied"
    assert retained["run_id"] == "20261008T120000Z-controller"


def test_run_failure_is_identical_across_execution_receipt_response_and_idempotency(
    tmp_path,
):
    class FailedRuntime(FakeRuntime):
        def run_launch(self, **kwargs):
            self.run_calls.append(kwargs)
            error = DockerRuntimeError(
                "scenario runner returned no canonical run identity",
                code="run_adapter_identity_missing",
                stage="adapter_execution",
                return_code=37,
                adapter_output={
                    "stdout": ["adapter started"],
                    "stderr": [
                        "/usr/bin/env: cardano-profile: Permission denied",
                        "Authorization: Bearer INLINE_SECRET",
                    ],
                },
            )
            raise error

    runtime = FailedRuntime()
    service = ControllerService(TOKEN, tmp_path, runtime, "rev", "test-install")
    request = _request(
        "run.execute",
        launch_id="launch-0123456789abcdef01234567",
        plan_digest=DIGEST,
        execution_id="run-execution-retained-failure",
        idempotency_key="run-retained-failure",
    )

    status, response = _dispatch(service, request)

    assert status == 500
    failure = response["failure"]
    assert failure["stage"] == "adapter_execution"
    assert failure["code"] == "run_adapter_identity_missing"
    assert failure["return_code"] == 37
    assert failure["type"] == "DockerRuntimeError"
    assert failure["adapter_output"] == {
        "stdout": ["[redacted]"],
        "stderr": [
            "adapter process permission denied",
            "[redacted]",
        ],
    }
    assert failure["trace"]
    assert all(
        re.fullmatch(r"(?:dwarf|tests)/[^:]+:\d+:[A-Za-z0-9_<>]+", frame)
        for frame in failure["trace"]
    )
    execution = json.loads(
        (
            tmp_path
            / "controller/executions/run-execution-retained-failure.json"
        ).read_text()
    )
    assert execution["failure"] == failure
    audit = json.loads((tmp_path / response["audit_receipt"]).read_text())
    failure_path = next(
        item for item in audit["artifact_paths"] if item.endswith("-failure.json")
    )
    sealed = json.loads((tmp_path / failure_path).read_text())
    assert sealed["failure"] == failure
    idempotency = json.loads(
        (tmp_path / "controller/idempotency/run-retained-failure.json").read_text()
    )
    assert idempotency["failure"] == failure
    status_again, response_again = _dispatch(service, request)
    assert status_again == 409
    assert response_again["error"] == "idempotent operation is failed"
    assert len(runtime.run_calls) == 1
    encoded = json.dumps(
        {
            "response": response,
            "execution": execution,
            "audit": audit,
            "sealed": sealed,
            "idempotency": idempotency,
        }
    )
    assert "Authorization" not in encoded
    assert "Bearer" not in encoded
    assert "INLINE_SECRET" not in encoded
    assert "token=" not in encoded


def test_run_resolution_failure_retains_safe_errno_and_trusted_trace(tmp_path):
    class ResolutionFailingRuntime(FakeRuntime):
        def run_launch(self, **kwargs):
            raise FileNotFoundError(2, "PRIVATE_PATH_SECRET")

    service = ControllerService(
        TOKEN, tmp_path, ResolutionFailingRuntime(), "rev", "test-install"
    )
    status, response = _dispatch(
        service,
        _request(
            "run.execute",
            launch_id="launch-0123456789abcdef01234567",
            plan_digest=DIGEST,
            execution_id="run-execution-resolution-failure",
            idempotency_key="run-resolution-failure",
        ),
    )

    assert status == 500
    assert response["failure"]["stage"] == "run_launch"
    assert response["failure"]["code"] == "run_execution_failed"
    assert response["failure"]["type"] == "FileNotFoundError"
    assert response["failure"]["errno"] == 2
    assert "PRIVATE_PATH_SECRET" not in json.dumps(response)


def test_cancelled_run_waiting_for_mutation_lock_never_reaches_runtime(tmp_path):
    runtime = FakeRuntime()
    service = ControllerService(TOKEN, tmp_path, runtime, "rev", "test-install")
    service._mutation_lock.acquire()
    response = []
    thread = threading.Thread(
        target=lambda: response.append(
            _dispatch(
                service,
                _request(
                    "run.execute",
                    launch_id="launch-0123456789abcdef01234567",
                    plan_digest=DIGEST,
                    execution_id="run-execution-cancel-before-start",
                    idempotency_key="run-cancel-before-start",
                    timeout_seconds=10,
                ),
            )
        )
    )
    thread.start()
    deadline = time.monotonic() + 2
    path = (
        tmp_path
        / "controller"
        / "executions"
        / "run-execution-cancel-before-start.json"
    )
    while time.monotonic() < deadline and not path.is_file():
        time.sleep(0.01)
    assert path.is_file()
    cancel_status, cancel_result = _dispatch(
        service,
        _request(
            "execution.cancel",
            execution_id="run-execution-cancel-before-start",
            reason="operator cancelled before start",
            idempotency_key="cancel-run-before-start",
        ),
    )
    service._mutation_lock.release()
    thread.join(timeout=2)

    assert cancel_status == 200
    assert cancel_result["status"] == "cancelling"
    assert runtime.run_calls == []
    assert response[0][1]["status"] == "cancelled"


def test_too_late_run_cancel_preserves_completed_result(tmp_path):
    class CompletedRuntime(FakeRuntime):
        def __init__(self):
            super().__init__()
            self.completed = threading.Event()
            self.release = threading.Event()

        def run_launch(self, **kwargs):
            result = super().run_launch(**kwargs)
            self.completed.set()
            assert self.release.wait(timeout=2)
            return result

    runtime = CompletedRuntime()
    service = ControllerService(TOKEN, tmp_path, runtime, "rev", "test-install")
    response = []
    thread = threading.Thread(
        target=lambda: response.append(
            _dispatch(
                service,
                _request(
                    "run.execute",
                    launch_id="launch-0123456789abcdef01234567",
                    plan_digest=DIGEST,
                    execution_id="run-execution-too-late-cancel",
                    idempotency_key="run-too-late-cancel",
                    timeout_seconds=10,
                ),
            )
        )
    )
    thread.start()
    assert runtime.completed.wait(timeout=1)
    cancel_status, _ = _dispatch(
        service,
        _request(
            "execution.cancel",
            execution_id="run-execution-too-late-cancel",
            reason="arrived after scenario completion",
            idempotency_key="cancel-run-too-late",
        ),
    )
    runtime.release.set()
    thread.join(timeout=2)

    assert cancel_status == 200
    assert response[0][1]["status"] == "applied"
    assert response[0][1]["result"]["run_id"] == "20261008T120000Z-controller"


def test_run_cleanup_failure_is_not_hidden_by_a_cancellation_request(tmp_path):
    class CleanupFailingRuntime(FakeRuntime):
        def __init__(self):
            super().__init__()
            self.started = threading.Event()

        def run_launch(self, **kwargs):
            self.started.set()
            assert kwargs["cancel_event"].wait(timeout=2)
            raise RuntimeError("controller could not leave the managed topology network")

    runtime = CleanupFailingRuntime()
    service = ControllerService(TOKEN, tmp_path, runtime, "rev", "test-install")
    response = []
    thread = threading.Thread(
        target=lambda: response.append(
            _dispatch(
                service,
                _request(
                    "run.execute",
                    launch_id="launch-0123456789abcdef01234567",
                    plan_digest=DIGEST,
                    execution_id="run-execution-cleanup-failure",
                    idempotency_key="run-cleanup-failure",
                    timeout_seconds=10,
                ),
            )
        )
    )
    thread.start()
    assert runtime.started.wait(timeout=1)
    _dispatch(
        service,
        _request(
            "execution.cancel",
            execution_id="run-execution-cleanup-failure",
            reason="operator requested cancellation",
            idempotency_key="cancel-run-cleanup-failure",
        ),
    )
    thread.join(timeout=2)

    assert response[0][0] == 500
    retained = json.loads(
        (
            tmp_path
            / "controller"
            / "executions"
            / "run-execution-cleanup-failure.json"
        ).read_text()
    )
    assert retained["state"] == "failed"


def test_controller_health_fails_closed_when_compose_is_unavailable(tmp_path):
    runtime = FakeRuntime()
    runtime.health = lambda: {
        "docker": "ok",
        "compose": "error",
        "socket": "ok",
        "golden_images": "ok",
    }
    service = ControllerService(
        token=TOKEN,
        state_dir=tmp_path,
        runtime=runtime,
        release_revision="revision-123",
        installation_id="test-install",
    )

    status, payload = service.dispatch("GET", "/healthz", {}, b"")

    assert status == 503
    assert payload["status"] == "error"
    assert payload["runtime"]["compose"] == "error"


def test_controller_startup_reconciles_runtime_network_attachments(tmp_path):
    class ReconcilingRuntime(FakeRuntime):
        def __init__(self):
            super().__init__()
            self.reconcile_calls = 0

        def reconcile_run_attachments(self):
            self.reconcile_calls += 1
            return []

    runtime = ReconcilingRuntime()

    ControllerService(TOKEN, tmp_path, runtime, "rev", "test-install")

    assert runtime.reconcile_calls == 1


def test_plan_persists_server_resolved_deployment_not_only_client_identity(tmp_path):
    runtime = FakeRuntime()
    service = ControllerService(TOKEN, tmp_path, runtime, "rev", "test-install")

    response = _plan(service)
    retained = json.loads(
        (tmp_path / "controller" / "plans" / f"{response['plan_id']}.json").read_text()
    )["plan"]

    assert runtime.prepare_calls == [_topology()]
    assert retained["deployment"]["adapter"] == "runtime_compose_substrate.py"
    assert retained["deployment"]["substrate"]["nodes"] == _topology()["nodes"]


@pytest.mark.parametrize(
    ("stage", "message", "expected_message"),
    (
        (
            "prepare_topology",
            "requested identity differs from the catalog-resolved topology",
            "requested identity differs from the catalog-resolved topology",
        ),
        (
            "snapshot",
            "managed topology runtime metadata is unavailable",
            "managed topology runtime metadata is unavailable",
        ),
    ),
)
def test_plan_failure_retains_only_bounded_stage_diagnostics(
    tmp_path, monkeypatch, stage, message, expected_message
):
    runtime = FakeRuntime()
    if stage == "prepare_topology":
        monkeypatch.setattr(
            runtime,
            "prepare_topology",
            lambda _topology: (_ for _ in ()).throw(DockerRuntimeError(message)),
        )
    else:
        monkeypatch.setattr(
            runtime,
            "snapshot",
            lambda _topology_id: (_ for _ in ()).throw(DockerRuntimeError(message)),
        )
    state_dir = tmp_path / stage
    service = ControllerService(TOKEN, state_dir, runtime, "rev", "test-install")

    status, payload = _dispatch(
        service,
        _request(
            "topology.plan",
            action="redeploy",
            topology=_topology(),
            timeout_seconds=30,
        ),
    )

    assert status == 409
    assert payload == {
        "ok": False,
        "error": f"controller planning failed during {stage}",
        "failure": {
            "stage": stage,
            "cause": {
                "type": "DockerRuntimeError",
                "message": expected_message,
            },
        },
    }
    assert list((state_dir / "controller" / "plans").iterdir()) == []
    assert list((state_dir / "controller" / "executions").iterdir()) == []
    refusal_paths = list((state_dir / "controller" / "controller-audit").glob("refusal-*.json"))
    assert len(refusal_paths) == 1
    refusal = json.loads(refusal_paths[0].read_text())
    assert refusal["reason"] == payload["error"]
    assert refusal["failure"] == payload["failure"]
    encoded = json.dumps({"payload": payload, "refusal": refusal})
    assert "OTHER_SECRET" not in encoded
    assert "user:pass" not in encoded
    assert "token=leak" not in encoded


def test_unexpected_plan_failure_remains_generic_server_error(tmp_path, monkeypatch):
    runtime = FakeRuntime()
    monkeypatch.setattr(
        runtime,
        "prepare_topology",
        lambda _topology: (_ for _ in ()).throw(
            AttributeError('{"password":"OTHER_SECRET"} http://user:pass@host?token=leak')
        ),
    )
    state_dir = tmp_path / "unexpected"
    service = ControllerService(TOKEN, state_dir, runtime, "rev", "test-install")

    status, payload = _dispatch(
        service,
        _request(
            "topology.plan",
            action="redeploy",
            topology=_topology(),
            timeout_seconds=30,
        ),
    )

    assert status == 500
    assert payload == {"ok": False, "error": "controller operation failed"}
    assert list((state_dir / "controller" / "plans").iterdir()) == []
    assert list((state_dir / "controller" / "executions").iterdir()) == []
    refusal_paths = list((state_dir / "controller" / "controller-audit").glob("refusal-*.json"))
    assert len(refusal_paths) == 1
    refusal = json.loads(refusal_paths[0].read_text())
    assert refusal["reason"] == "controller operation failed"
    assert refusal["failure"] == {
        "stage": "prepare_topology",
        "cause": {
            "type": "AttributeError",
            "message": "controller runtime failed",
        },
    }
    encoded = json.dumps({"payload": payload, "refusal": refusal})
    assert "OTHER_SECRET" not in encoded
    assert "user:pass" not in encoded
    assert "token=leak" not in encoded


def test_plan_is_read_only_and_apply_is_bound_to_persisted_digest(tmp_path):
    runtime = FakeRuntime()
    service = ControllerService(TOKEN, tmp_path, runtime, "rev", "test-install")

    plan = _plan(service)
    assert runtime.apply_calls == 0
    assert plan["status"] == "planned"
    assert plan["plan_digest"].startswith("sha256:")

    status, applied = _dispatch(
        service,
        _request(
            "topology.apply",
            plan_id=plan["plan_id"],
            plan_digest=plan["plan_digest"],
            idempotency_key="apply-local-lab-001",
            timeout_seconds=2400,
        ),
    )
    assert status == 200
    assert applied["status"] == "applied"
    assert runtime.apply_calls == 1
    receipt_path = tmp_path / applied["audit_receipt"]
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] == "applied"
    assert receipt["plan_sha256"] == plan["plan_digest"]

    status, refused = _dispatch(
        service,
        _request(
            "topology.apply",
            plan_id=plan["plan_id"],
            plan_digest="sha256:" + "b" * 64,
            idempotency_key="apply-local-lab-002",
            timeout_seconds=2400,
        ),
    )
    assert status == 409
    assert "plan digest" in refused["error"]


def test_apply_rejects_when_profile_was_deleted_after_plan(tmp_path, monkeypatch):
    profiles = tmp_path / "profiles"
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    _path, digest = _controller_profile(profiles)
    monkeypatch.setenv("ADA2_DWARF_PROFILES_DIR", str(profiles))
    monkeypatch.setenv("ADA2_DWARF_SCENARIOS_DIR", str(scenarios))
    runtime = FakeRuntime()
    service = ControllerService(TOKEN, tmp_path / "service", runtime, "rev", "test-install")
    plan = _plan(service)

    definitions.delete_user_definition("profiles", "local-lab", expected_sha256=digest)
    status, payload = _dispatch(
        service,
        _request(
            "topology.apply",
            plan_id=plan["plan_id"],
            plan_digest=plan["plan_digest"],
            idempotency_key="apply-deleted-profile",
            timeout_seconds=30,
        ),
    )

    assert status == 409
    assert "profile" in payload["error"]
    assert runtime.apply_calls == 0


def test_plan_rejects_a_symlinked_configured_profile_root(tmp_path, monkeypatch):
    external = tmp_path / "external-profiles"
    _controller_profile(external)
    configured = tmp_path / "configured-profiles"
    os.symlink(external, configured)
    monkeypatch.setenv("ADA2_DWARF_PROFILES_DIR", str(configured))
    service = ControllerService(
        TOKEN, tmp_path / "service", FakeRuntime(), "rev", "test-install"
    )

    status, payload = _dispatch(
        service,
        _request(
            "topology.plan",
            action="deploy",
            topology=_topology(),
            timeout_seconds=30,
        ),
    )

    assert status == 409
    assert "profile catalog" in payload["error"]


def test_apply_rejects_when_profile_bytes_changed_after_plan(tmp_path, monkeypatch):
    profiles = tmp_path / "profiles"
    path, _digest = _controller_profile(profiles)
    monkeypatch.setenv("ADA2_DWARF_PROFILES_DIR", str(profiles))
    runtime = FakeRuntime()
    service = ControllerService(TOKEN, tmp_path / "service", runtime, "rev", "test-install")
    plan = _plan(service)
    changed = json.loads(path.read_text())
    changed["label"] = "Changed after plan"
    path.write_text(json.dumps(changed, indent=2) + "\n")

    status, payload = _dispatch(
        service,
        _request(
            "topology.apply",
            plan_id=plan["plan_id"],
            plan_digest=plan["plan_digest"],
            idempotency_key="apply-changed-profile",
            timeout_seconds=30,
        ),
    )

    assert status == 409
    assert "profile changed" in payload["error"]
    assert runtime.apply_calls == 0


def test_apply_holds_profile_lifecycle_lock_until_owned_state_is_visible(
    tmp_path, monkeypatch
):
    profiles = tmp_path / "profiles"
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    path, digest = _controller_profile(profiles)
    monkeypatch.setenv("ADA2_DWARF_PROFILES_DIR", str(profiles))
    monkeypatch.setenv("ADA2_DWARF_SCENARIOS_DIR", str(scenarios))
    started = threading.Event()
    release = threading.Event()

    class BlockingRuntime(FakeRuntime):
        def apply(self, plan, execution_id, cancel_event):
            started.set()
            assert release.wait(5)
            return super().apply(plan, execution_id, cancel_event)

    runtime = BlockingRuntime()
    service = ControllerService(TOKEN, tmp_path / "service", runtime, "rev", "test-install")
    plan = _plan(service)
    apply_result = []
    apply_thread = threading.Thread(
        target=lambda: apply_result.append(
            _dispatch(
                service,
                _request(
                    "topology.apply",
                    plan_id=plan["plan_id"],
                    plan_digest=plan["plan_digest"],
                    idempotency_key="apply-lock-profile",
                    timeout_seconds=30,
                ),
            )
        )
    )
    apply_thread.start()
    assert started.wait(5)

    monkeypatch.setattr(
        health,
        "controller_inventory",
        lambda **_kwargs: {
            "available": True,
            "managed": [
                {"topology_id": topology_id}
                for topology_id in runtime.current
            ],
            "owned_resources": [],
            "unmanaged": [],
        },
    )
    delete_result = []
    delete_thread = threading.Thread(
        target=lambda: delete_result.append(
            dashboard.dispatch_catalog_mutating_request(
                method="POST",
                path="/api/catalog/profiles/local-lab/delete",
                body=(
                    "token=secret&expected_sha256=" + digest
                ).encode(),
                expected_token="secret",
            )
        )
    )
    delete_thread.start()
    time.sleep(0.2)
    assert delete_thread.is_alive(), "profile deletion did not wait for apply"

    release.set()
    apply_thread.join(5)
    delete_thread.join(5)

    assert apply_result and apply_result[0][0] == 200
    assert delete_result and delete_result[0][0] == 409
    assert path.is_file()
    assert runtime.apply_calls == 1


def test_idempotency_survives_restart_and_conflicting_reuse_is_refused(tmp_path):
    runtime = FakeRuntime()
    first = ControllerService(TOKEN, tmp_path, runtime, "rev", "test-install")
    plan = _plan(first)
    apply_body = _request(
        "topology.apply",
        plan_id=plan["plan_id"],
        plan_digest=plan["plan_digest"],
        idempotency_key="apply-local-lab-replay",
        timeout_seconds=2400,
    )
    status, original = _dispatch(first, apply_body)
    assert status == 200

    restarted = ControllerService(TOKEN, tmp_path, runtime, "rev", "test-install")
    replay = {**apply_body, "request_id": str(uuid.uuid4())}
    status, repeated = _dispatch(restarted, replay)
    assert status == 200
    assert repeated == original
    assert runtime.apply_calls == 1

    record_path = next((tmp_path / "controller" / "idempotency").glob("*.json"))
    record = json.loads(record_path.read_text())
    record["intent_sha256"] = "sha256:" + "0" * 64
    record_path.write_text(json.dumps(record))
    status, conflict = _dispatch(restarted, replay)
    assert status == 409
    assert "idempotency key" in conflict["error"]


def test_stale_plan_is_refused_before_runtime_mutation(tmp_path):
    runtime = FakeRuntime()
    service = ControllerService(TOKEN, tmp_path, runtime, "rev", "test-install")
    plan = _plan(service)
    runtime.current["local-lab"] = {"topology_id": "local-lab", "resources": [{"drift": True}]}

    status, payload = _dispatch(
        service,
        _request(
            "topology.apply",
            plan_id=plan["plan_id"],
            plan_digest=plan["plan_digest"],
            idempotency_key="apply-stale-local-lab",
            timeout_seconds=2400,
        ),
    )
    assert status == 409
    assert "observed state changed" in payload["error"]
    assert runtime.apply_calls == 0


def test_running_apply_can_be_cancelled_by_managed_execution_id(tmp_path):
    runtime = FakeRuntime()
    runtime.block_apply = True
    service = ControllerService(TOKEN, tmp_path, runtime, "rev", "test-install")
    plan = _plan(service)
    apply_result = []

    def run_apply():
        apply_result.append(
            _dispatch(
                service,
                _request(
                    "topology.apply",
                    plan_id=plan["plan_id"],
                    plan_digest=plan["plan_digest"],
                    idempotency_key="apply-cancellable-local-lab",
                    timeout_seconds=2400,
                ),
            )
        )

    thread = threading.Thread(target=run_apply)
    thread.start()
    execution_id = None
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        records = list((tmp_path / "controller" / "executions").glob("*.json"))
        if records:
            record = json.loads(records[0].read_text())
            if record["state"] == "running":
                execution_id = record["execution_id"]
                break
        time.sleep(0.01)
    assert execution_id

    status, cancelled = _dispatch(
        service,
        _request(
            "execution.cancel",
            execution_id=execution_id,
            reason="operator requested cancellation",
            idempotency_key="cancel-local-lab-001",
        ),
    )
    thread.join(timeout=2)
    assert status == 200
    assert cancelled["status"] == "cancelling"
    assert runtime.cancel_calls == [execution_id]
    assert apply_result[0][1]["status"] == "cancelled"


def test_cancel_status_remains_nonterminal_until_worker_stops(tmp_path):
    class DelayedCancellationRuntime(FakeRuntime):
        def __init__(self):
            super().__init__()
            self.started = threading.Event()
            self.cancel_observed = threading.Event()
            self.release = threading.Event()

        def apply(self, plan, execution_id, cancel_event):
            self.apply_calls += 1
            self.started.set()
            assert cancel_event.wait(timeout=2)
            self.cancel_observed.set()
            assert self.release.wait(timeout=2)
            return {"status": "cancelled", "topology_id": plan["topology_id"]}

    runtime = DelayedCancellationRuntime()
    service = ControllerService(TOKEN, tmp_path, runtime, "rev", "test-install")
    plan = _plan(service)
    response = []
    apply_thread = threading.Thread(
        target=lambda: response.append(
            _dispatch(
                service,
                _request(
                    "topology.apply",
                    plan_id=plan["plan_id"],
                    plan_digest=plan["plan_digest"],
                    execution_id="execution-delayed-cancel-001",
                    idempotency_key="apply-delayed-cancel-001",
                    timeout_seconds=10,
                ),
            )
        )
    )
    apply_thread.start()
    assert runtime.started.wait(timeout=1)

    cancel_status, cancel_result = _dispatch(
        service,
        _request(
            "execution.cancel",
            execution_id="execution-delayed-cancel-001",
            reason="operator requested cancellation",
            idempotency_key="cancel-delayed-001",
        ),
    )
    assert cancel_status == 200
    assert cancel_result["status"] == "cancelling"
    assert runtime.cancel_observed.wait(timeout=1)
    status_code, status_result = _dispatch(
        service,
        _request("execution.status", execution_id="execution-delayed-cancel-001"),
    )
    assert status_code == 200
    assert status_result["state"] == "cancelling"
    assert status_result["completed_at"] is None

    runtime.release.set()
    apply_thread.join(timeout=2)
    assert response[0][1]["status"] == "cancelled"
    final_status = _dispatch(
        service,
        _request("execution.status", execution_id="execution-delayed-cancel-001"),
    )[1]
    assert final_status["state"] == "cancelled"
    assert final_status["audit_receipt"].startswith("controller/controller-audit/")


def test_cancel_while_waiting_for_mutation_lock_reaches_runtime_before_mutation(tmp_path):
    class PreCancelledRuntime(FakeRuntime):
        def __init__(self):
            super().__init__()
            self.entered = threading.Event()

        def apply(self, plan, execution_id, cancel_event):
            self.apply_calls += 1
            self.entered.set()
            assert cancel_event.is_set()
            return {"status": "cancelled", "topology_id": plan["topology_id"]}

    runtime = PreCancelledRuntime()
    service = ControllerService(TOKEN, tmp_path, runtime, "rev", "test-install")
    plan = _plan(service)
    assert service._mutation_lock.acquire(blocking=False)
    response = []
    thread = threading.Thread(
        target=lambda: response.append(
            _dispatch(
                service,
                _request(
                    "topology.apply",
                    plan_id=plan["plan_id"],
                    plan_digest=plan["plan_digest"],
                    execution_id="execution-lock-cancel-001",
                    idempotency_key="apply-lock-cancel-001",
                    timeout_seconds=10,
                ),
            )
        )
    )
    thread.start()
    deadline = time.monotonic() + 2
    execution_path = tmp_path / "controller" / "executions" / "execution-lock-cancel-001.json"
    while not execution_path.is_file() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert execution_path.is_file()

    cancel_status, cancel_result = _dispatch(
        service,
        _request(
            "execution.cancel",
            execution_id="execution-lock-cancel-001",
            reason="cancel while queued",
            idempotency_key="cancel-lock-cancel-001",
        ),
    )
    assert cancel_status == 200
    assert cancel_result["status"] == "cancelling"
    service._mutation_lock.release()
    thread.join(timeout=2)

    assert runtime.entered.is_set()
    assert response[0][1]["status"] == "cancelled"
    assert runtime.current == {}


def test_too_late_cancel_preserves_applied_terminal_state_and_receipt(tmp_path):
    class AppliedBeforeCancelRuntime(FakeRuntime):
        def __init__(self):
            super().__init__()
            self.mutation_completed = threading.Event()
            self.return_allowed = threading.Event()

        def apply(self, plan, execution_id, cancel_event):
            self.apply_calls += 1
            self.current[plan["topology_id"]] = {
                "topology_id": plan["topology_id"],
                "resources": [{"mutation": "applied"}],
            }
            self.mutation_completed.set()
            assert self.return_allowed.wait(timeout=2)
            assert cancel_event.is_set()
            return {"status": "applied", "topology_id": plan["topology_id"]}

        def cancel(self, execution_id):
            super().cancel(execution_id)
            self.return_allowed.set()

    runtime = AppliedBeforeCancelRuntime()
    service = ControllerService(TOKEN, tmp_path, runtime, "rev", "test-install")
    plan = _plan(service)
    response = []
    thread = threading.Thread(
        target=lambda: response.append(
            _dispatch(
                service,
                _request(
                    "topology.apply",
                    plan_id=plan["plan_id"],
                    plan_digest=plan["plan_digest"],
                    execution_id="execution-too-late-cancel-001",
                    idempotency_key="apply-too-late-cancel-001",
                    timeout_seconds=10,
                ),
            )
        )
    )
    thread.start()
    assert runtime.mutation_completed.wait(timeout=1)

    cancel_status, cancel_result = _dispatch(
        service,
        _request(
            "execution.cancel",
            execution_id="execution-too-late-cancel-001",
            reason="arrived after mutation",
            idempotency_key="cancel-too-late-001",
        ),
    )
    assert cancel_status == 200
    assert cancel_result["status"] == "cancelling"
    thread.join(timeout=2)

    assert response[0][0] == 200
    assert response[0][1]["status"] == "applied"
    final = _dispatch(
        service,
        _request("execution.status", execution_id="execution-too-late-cancel-001"),
    )[1]
    assert final["state"] == "applied"
    receipt = json.loads((tmp_path / final["audit_receipt"]).read_text())
    assert receipt["status"] == "applied"


def test_inventory_and_logs_return_only_runtime_typed_payloads(tmp_path):
    runtime = FakeRuntime()
    service = ControllerService(TOKEN, tmp_path, runtime, "rev", "test-install")
    status, inventory = _dispatch(service, _request("inventory.get"))
    assert status == 200
    assert inventory["managed_count"] == 0
    assert inventory["unmanaged_count"] == 3

    status, logs = _dispatch(
        service,
        _request(
            "logs.stream",
            resource_kind="controller",
            resource_id="controller",
            max_lines=2,
            follow_seconds=0,
        ),
    )
    assert status == 200
    assert logs["lines"] == ["one", "two"]
    assert logs["max_lines"] == 2


def test_timeout_and_runtime_failure_leave_terminal_durable_records(tmp_path):
    runtime = FakeRuntime()
    runtime.block_apply = True
    service = ControllerService(TOKEN, tmp_path, runtime, "rev", "test-install")
    plan = _plan(service)
    status, payload = _dispatch(
        service,
        _request(
            "topology.apply",
            plan_id=plan["plan_id"],
            plan_digest=plan["plan_digest"],
            idempotency_key="apply-timeout-local-lab",
            timeout_seconds=1,
        ),
    )
    assert status == 504
    assert "timed out" in payload["error"]
    assert payload["execution_id"].startswith("execution-")
    assert payload["audit_receipt"].startswith("controller/controller-audit/")
    execution = json.loads(next((tmp_path / "controller" / "executions").glob("*.json")).read_text())
    idempotency = json.loads(next((tmp_path / "controller" / "idempotency").glob("*.json")).read_text())
    assert execution["state"] == "timed_out"
    assert idempotency["state"] == "failed"

    failure_root = tmp_path / "failure"
    failing_runtime = FakeRuntime()
    failing_runtime.fail_apply = True
    failing = ControllerService(TOKEN, failure_root, failing_runtime, "rev", "test-install")
    failed_plan = _plan(failing, "failure-lab")
    failed_request = _request(
        "topology.apply",
        plan_id=failed_plan["plan_id"],
        plan_digest=failed_plan["plan_digest"],
        idempotency_key="apply-failure-local-lab",
        timeout_seconds=2400,
    )
    failed_status, failed_payload = _dispatch(failing, failed_request)
    assert failed_status == 500
    assert failed_payload["error"] == "retained topology differs from the current catalog plan"
    assert failed_payload["failure"] == {
        "type": "DockerRuntimeError",
        "message": "retained topology differs from the current catalog plan",
    }
    assert failed_payload["execution_id"].startswith("execution-")
    assert failed_payload["audit_receipt"].startswith("controller/controller-audit/")
    failed_execution = json.loads(
        next((failure_root / "controller" / "executions").glob("*.json")).read_text()
    )
    assert failed_execution["state"] == "failed"
    assert failed_execution["failure"] == failed_payload["failure"]
    failure_receipt = json.loads(
        next(
            path
            for path in (failure_root / "controller" / "controller-audit").glob("*.json")
            if not path.name.endswith("-failure.json")
        ).read_text()
    )
    assert failure_receipt["status"] == "failed"
    assert failure_receipt["before_sha256"].startswith("sha256:")
    assert failure_receipt["after_sha256"].startswith("sha256:")
    failure_artifact = next(
        path
        for path in failure_receipt["artifact_paths"]
        if path.endswith("-failure.json")
    )
    assert json.loads((failure_root / failure_artifact).read_text()) == {
        "schema_version": "v1",
        "failure": failed_payload["failure"],
        "redactions": ["authorization", "credentials"],
    }
    failed_idempotency = json.loads(
        next((failure_root / "controller" / "idempotency").glob("*.json")).read_text()
    )
    assert failed_idempotency["state"] == "failed"
    assert failed_idempotency["failure"] == failed_payload["failure"]
    assert failed_idempotency["execution_id"] == failed_payload["execution_id"]
    assert failed_idempotency["audit_receipt"] == failed_payload["audit_receipt"]
    status_code, retained_status = _dispatch(
        failing,
        _request(
            "execution.status",
            execution_id=failed_payload["execution_id"],
        ),
    )
    assert status_code == 200
    assert retained_status["state"] == "failed"
    assert retained_status["failure"] == failed_payload["failure"]
    assert _dispatch(failing, failed_request)[0] == 409
    assert failing_runtime.apply_calls == 1


def test_controller_failure_detail_is_bounded_and_credential_safe():
    secret = "controller-token-" + "s" * 32
    safe_preflight = DockerRuntimeError(
        "Amaru control lifecycle requires peer_sharing=false"
    )
    safe_preflight.failure_code = "amaru_peer_sharing_contract_invalid"
    safe_preflight.failure_stage = "adapter_preflight"
    assert _bounded_failure(safe_preflight, secret=secret) == {
        "type": "DockerRuntimeError",
        "message": "Amaru control lifecycle requires peer_sharing=false",
        "code": "amaru_peer_sharing_contract_invalid",
        "stage": "adapter_preflight",
    }
    safe_freshness = DockerRuntimeError(
        "Amaru deployment preflight found retained project resources"
    )
    safe_freshness.failure_code = "compose_project_not_fresh"
    safe_freshness.failure_stage = "adapter_preflight"
    assert _bounded_failure(safe_freshness, secret=secret) == {
        "type": "DockerRuntimeError",
        "message": "Amaru deployment preflight found retained project resources",
        "code": "compose_project_not_fresh",
        "stage": "adapter_preflight",
    }
    for code, stage, message in (
        (
            "runtime_assets_exist",
            "adapter_preflight",
            "Amaru deployment preflight found retained runtime assets",
        ),
        (
            "required_runtime_asset_missing",
            "adapter_asset_staging",
            "Amaru deployment runtime assets are unavailable",
        ),
        (
            "runtime_asset_permission_denied",
            "adapter_asset_staging",
            "Amaru deployment runtime assets are not writable",
        ),
    ):
        safe = DockerRuntimeError(message, code=code, stage=stage)
        assert _bounded_failure(safe, secret=secret) == {
            "type": "DockerRuntimeError",
            "message": message,
            "code": code,
            "stage": stage,
        }

    failure = _bounded_failure(
        DockerRuntimeError(
            "request http://alice:uri-pass@localhost/action?token=query-secret failed "
            f"Bearer {secret} password=hunter2 "
            "payload={\"token\":\"OTHER_SECRET\", \"password\": \"abc def\", "
            "'credential': 'quoted value', \"refresh_token\":\"quoted-refresh\"} "
            "access_token=access-secret client_secret='client secret' "
            "Authorization: Digest username=\"alice\", response=\"digest-secret\", "
            "nonce=\"nonce-secret\"\n" + "x" * 900
        ),
        secret=secret,
    )

    assert failure == {
        "type": "DockerRuntimeError",
        "message": "controller runtime failed",
    }
    for leaked in (
        secret,
        "uri-pass",
        "query-secret",
        "hunter2",
        "OTHER_SECRET",
        "abc def",
        "quoted value",
        "quoted-refresh",
        "access-secret",
        "client secret",
        "digest-secret",
        "nonce-secret",
    ):
        assert leaked not in failure["message"]
    standalone_digest = _bounded_failure(
        DockerRuntimeError(
            'handshake Digest username="alice", response="standalone-digest-secret", '
            'nonce="standalone-nonce-secret"'
        ),
        secret=secret,
    )
    assert standalone_digest["message"] == "controller runtime failed"
    assert "standalone-digest-secret" not in standalone_digest["message"]
    assert "standalone-nonce-secret" not in standalone_digest["message"]

    unknown = _bounded_failure(
        RuntimeError("raw subprocess stderr includes password=untrusted-secret"),
        secret=secret,
    )
    assert unknown == {
        "type": "RuntimeError",
        "message": "controller runtime failed",
    }


def test_secret_bearing_runtime_failure_is_redacted_from_every_controller_record(tmp_path):
    secrets = (
        "uri-pass",
        "query-secret",
        "bearer-secret",
        "OTHER_SECRET",
        "abc def",
        "registry-auth-blob",
        "refresh-secret",
        "access-secret",
        "client-secret",
        "digest-secret",
        "nonce-secret",
        "standalone-digest-secret",
        "standalone-nonce-secret",
    )
    runtime = FakeRuntime()
    runtime.fail_apply = True
    runtime.failure_message = (
        "http://alice:uri-pass@localhost/action?token=query-secret "
        "Bearer bearer-secret "
        "{\"token\":\"OTHER_SECRET\", \"password\": \"abc def\"} "
        "refresh_token=refresh-secret access_token=access-secret "
        "client_secret=client-secret DOCKER_AUTH_CONFIG=registry-auth-blob "
        "Authorization: Digest username=\"alice\", response=\"digest-secret\", "
        "nonce=\"nonce-secret\" "
        "Digest username=\"alice\", response=\"standalone-digest-secret\", "
        "nonce=\"standalone-nonce-secret\""
    )
    service = ControllerService(TOKEN, tmp_path, runtime, "rev", "test-install")
    plan = _plan(service, "redaction-lab")
    request = _request(
        "topology.apply",
        plan_id=plan["plan_id"],
        plan_digest=plan["plan_digest"],
        idempotency_key="apply-redaction-local-lab",
        timeout_seconds=2400,
    )

    status, response = _dispatch(service, request)

    assert status == 500
    retained = [response]
    retained.extend(
        json.loads(path.read_text())
        for directory in (
            tmp_path / "controller" / "executions",
            tmp_path / "controller" / "idempotency",
            tmp_path / "controller" / "controller-audit",
        )
        for path in directory.glob("*.json")
    )
    serialized = json.dumps(retained, sort_keys=True)
    for secret_value in secrets:
        assert secret_value not in serialized
    assert "controller runtime failed" in serialized


def test_apply_uses_caller_declared_execution_identity(tmp_path):
    runtime = FakeRuntime()
    service = ControllerService(TOKEN, tmp_path, runtime, "rev", "test-install")
    plan = _plan(service)

    status, result = _dispatch(
        service,
        _request(
            "topology.apply",
            plan_id=plan["plan_id"],
            plan_digest=plan["plan_digest"],
            execution_id="execution-dashboard-known-001",
            idempotency_key="apply-dashboard-known-001",
            timeout_seconds=2400,
        ),
    )

    assert status == 200
    assert result["execution_id"] == "execution-dashboard-known-001"

    status_code, status_result = _dispatch(
        service,
        _request(
            "execution.status",
            execution_id="execution-dashboard-known-001",
        ),
    )
    assert status_code == 200
    assert status_result["execution_id"] == "execution-dashboard-known-001"
    assert status_result["state"] == "applied"
    assert status_result["audit_receipt"] == result["audit_receipt"]


def test_mutation_lock_timeout_never_leaves_a_running_execution(tmp_path):
    runtime = FakeRuntime()
    service = ControllerService(TOKEN, tmp_path, runtime, "rev", "test-install")
    plan = _plan(service)
    service._mutation_lock.acquire()
    try:
        status, result = _dispatch(
            service,
            _request(
                "topology.apply",
                plan_id=plan["plan_id"],
                plan_digest=plan["plan_digest"],
                execution_id="execution-lock-timeout-001",
                idempotency_key="apply-lock-timeout-001",
                timeout_seconds=1,
            ),
        )
    finally:
        service._mutation_lock.release()

    assert status == 504
    execution = json.loads(
        (tmp_path / "controller" / "executions" / "execution-lock-timeout-001.json").read_text()
    )
    assert execution["state"] == "timed_out"
    receipt = json.loads(
        next((tmp_path / "controller" / "controller-audit").glob("*.json")).read_text()
    )
    assert receipt["status"] == "timed_out"


def test_timed_out_worker_retains_exclusive_lock_until_it_really_stops(tmp_path):
    class UncooperativeRuntime(FakeRuntime):
        def __init__(self):
            super().__init__()
            self.started = threading.Event()
            self.release = threading.Event()

        def apply(self, plan, execution_id, cancel_event):
            self.apply_calls += 1
            self.started.set()
            self.release.wait(timeout=5)
            return {"status": "cancelled", "topology_id": plan["topology_id"]}

    runtime = UncooperativeRuntime()
    service = ControllerService(
        TOKEN,
        tmp_path,
        runtime,
        "rev",
        "test-install",
        cancellation_grace_seconds=0.05,
    )
    plan = _plan(service)
    response = []

    request_thread = threading.Thread(
        target=lambda: response.append(
            _dispatch(
                service,
                _request(
                    "topology.apply",
                    plan_id=plan["plan_id"],
                    plan_digest=plan["plan_digest"],
                    execution_id="execution-uncooperative-001",
                    idempotency_key="apply-uncooperative-001",
                    timeout_seconds=1,
                ),
            )
        )
    )
    request_thread.start()
    assert runtime.started.wait(timeout=1)
    time.sleep(1.2)

    assert request_thread.is_alive()
    assert service._mutation_lock.acquire(blocking=False) is False

    runtime.release.set()
    request_thread.join(timeout=2)
    assert response[0][0] == 504
    assert service._mutation_lock.acquire(blocking=False) is True
    service._mutation_lock.release()


def test_cancel_failure_cannot_release_locks_while_runtime_worker_is_alive(tmp_path):
    class RaisingCancelRuntime(FakeRuntime):
        def __init__(self):
            super().__init__()
            self.started = threading.Event()
            self.release = threading.Event()

        def apply(self, plan, execution_id, cancel_event):
            self.apply_calls += 1
            self.started.set()
            self.release.wait(timeout=5)
            return {"status": "cancelled", "topology_id": plan["topology_id"]}

        def cancel(self, execution_id):
            self.cancel_calls.append(execution_id)
            raise RuntimeError("runtime process group did not stop")

    runtime = RaisingCancelRuntime()
    service = ControllerService(
        TOKEN,
        tmp_path,
        runtime,
        "rev",
        "test-install",
        cancellation_grace_seconds=0.05,
    )
    plan = _plan(service)
    response = []
    request_thread = threading.Thread(
        target=lambda: response.append(
            _dispatch(
                service,
                _request(
                    "topology.apply",
                    plan_id=plan["plan_id"],
                    plan_digest=plan["plan_digest"],
                    execution_id="execution-cancel-failure-001",
                    idempotency_key="apply-cancel-failure-001",
                    timeout_seconds=1,
                ),
            )
        )
    )

    request_thread.start()
    assert runtime.started.wait(timeout=1)
    time.sleep(1.2)

    assert request_thread.is_alive()
    assert service._mutation_lock.acquire(blocking=False) is False

    runtime.release.set()
    request_thread.join(timeout=2)
    assert response[0][0] == 504
    assert service._mutation_lock.acquire(blocking=False) is True
    service._mutation_lock.release()


def test_timeout_does_not_relabel_a_too_late_applied_mutation(tmp_path):
    class TooLateRuntime(FakeRuntime):
        def apply(self, plan, execution_id, cancel_event):
            self.apply_calls += 1
            time.sleep(1.2)
            self.current[plan["topology_id"]] = {
                "topology_id": plan["topology_id"],
                "resources": [{"mutation": "applied"}],
            }
            return {"status": "applied", "topology_id": plan["topology_id"]}

    runtime = TooLateRuntime()
    service = ControllerService(
        TOKEN,
        tmp_path,
        runtime,
        "rev",
        "test-install",
        cancellation_grace_seconds=0.05,
    )
    plan = _plan(service)

    status, result = _dispatch(
        service,
        _request(
            "topology.apply",
            plan_id=plan["plan_id"],
            plan_digest=plan["plan_digest"],
            execution_id="execution-timeout-too-late-001",
            idempotency_key="apply-timeout-too-late-001",
            timeout_seconds=1,
        ),
    )

    assert status == 200
    assert result["status"] == "applied"
    execution = json.loads(
        (tmp_path / "controller" / "executions" / "execution-timeout-too-late-001.json").read_text()
    )
    assert execution["state"] == "applied"
    receipt = json.loads((tmp_path / execution["audit_receipt"]).read_text())
    assert receipt["status"] == "applied"


def test_success_receipt_is_persisted_before_terminal_execution_state(tmp_path):
    runtime = FakeRuntime()
    service = ControllerService(TOKEN, tmp_path, runtime, "rev", "test-install")
    plan = _plan(service)
    execution_id = "execution-receipt-order-001"
    observed_states = []
    write_receipt = service._write_receipt

    def observing_write_receipt(**kwargs):
        execution_path = tmp_path / "controller" / "executions" / f"{execution_id}.json"
        observed_states.append(json.loads(execution_path.read_text())["state"])
        return write_receipt(**kwargs)

    service._write_receipt = observing_write_receipt
    status, result = _dispatch(
        service,
        _request(
            "topology.apply",
            plan_id=plan["plan_id"],
            plan_digest=plan["plan_digest"],
            execution_id=execution_id,
            idempotency_key="apply-receipt-order-001",
        ),
    )

    assert status == 200
    assert observed_states == ["running"]
    retained = json.loads(
        (tmp_path / "controller" / "executions" / f"{execution_id}.json").read_text()
    )
    assert retained["state"] == "applied"
    assert (tmp_path / retained["audit_receipt"]).is_file()


def test_restart_marks_uncertain_running_records_interrupted(tmp_path):
    executions = tmp_path / "controller" / "executions"
    idempotency = tmp_path / "controller" / "idempotency"
    executions.mkdir(parents=True)
    idempotency.mkdir(parents=True)
    (executions / "execution-old.json").write_text(
        json.dumps({"execution_id": "execution-old", "state": "running"})
    )
    (idempotency / "apply-old.json").write_text(
        json.dumps({"idempotency_key": "apply-old", "state": "running"})
    )

    ControllerService(TOKEN, tmp_path, FakeRuntime(), "rev", "test-install")

    assert json.loads((executions / "execution-old.json").read_text())["state"] == "interrupted"
    assert json.loads((idempotency / "apply-old.json").read_text())["state"] == "interrupted"


def test_restart_reconciles_only_interrupted_artifact_execution_resources(tmp_path):
    execution_id = "execution-3123456789abcdef0123456789abcdef"
    executions = tmp_path / "controller" / "executions"
    executions.mkdir(parents=True)
    parameters = {
        "import_id": "import-3123456789abcdef01234567",
        "execution_id": execution_id,
        "implementation": "amaru",
        "version": "local-test",
        "platform": "linux/amd64",
        "upload_sha256": "sha256:" + "1" * 64,
        "executable_sha256": "sha256:" + "2" * 64,
        "executable_size": 128,
    }
    (executions / f"{execution_id}.json").write_text(
        json.dumps(
            {
                "schema_version": "v1",
                "execution_id": execution_id,
                "operation": "artifact.package",
                "request_parameters": parameters,
                "state": "running",
                "accepted_at": "2026-10-13T12:00:00Z",
            }
        )
    )
    runtime = FakeRuntime()

    ControllerService(TOKEN, tmp_path, runtime, "revision-123", "test-install")

    assert runtime.package_cleanup_calls == [(parameters, execution_id)]
    retained = json.loads((executions / f"{execution_id}.json").read_text())
    assert retained["state"] == "interrupted"
    assert retained["artifact_cleanup"] == "completed"
    assert retained["reconciliation_reason"] == "controller_restarted_during_mutation"


def test_restart_retries_failed_artifact_cleanup_without_replaying_package(tmp_path):
    execution_id = "execution-5123456789abcdef0123456789abcdef"
    executions = tmp_path / "controller" / "executions"
    executions.mkdir(parents=True)
    parameters = {
        "import_id": "import-5123456789abcdef01234567",
        "execution_id": execution_id,
        "implementation": "amaru",
        "version": "local-test",
        "platform": "linux/amd64",
        "upload_sha256": "sha256:" + "1" * 64,
        "executable_sha256": "sha256:" + "2" * 64,
        "executable_size": 128,
    }
    path = executions / f"{execution_id}.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "v1",
                "execution_id": execution_id,
                "operation": "artifact.package",
                "request_parameters": parameters,
                "state": "running",
            }
        )
    )
    failed = FakeRuntime()
    failed.package_cleanup_failure = True
    ControllerService(TOKEN, tmp_path, failed, "revision-123", "test-install")
    assert json.loads(path.read_text())["artifact_cleanup"] == "failed"

    recovered = FakeRuntime()
    ControllerService(TOKEN, tmp_path, recovered, "revision-123", "test-install")

    retained = json.loads(path.read_text())
    assert recovered.package_cleanup_calls == [(parameters, execution_id)]
    assert retained["artifact_cleanup"] == "completed"
    assert retained["state"] == "interrupted"
    assert recovered.package_calls == []


def test_restart_marks_receiptless_success_interrupted(tmp_path):
    executions = tmp_path / "controller" / "executions"
    executions.mkdir(parents=True)
    path = executions / "execution-crash-window.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "v1",
                "execution_id": "execution-crash-window",
                "topology_id": "local-lab",
                "state": "applied",
                "accepted_at": "2026-10-08T00:00:00Z",
                "completed_at": "2026-10-08T00:00:01Z",
            }
        )
    )

    service = ControllerService(TOKEN, tmp_path, FakeRuntime(), "rev", "test-install")
    status, result = _dispatch(
        service,
        _request("execution.status", execution_id="execution-crash-window"),
    )

    assert status == 200
    assert result["state"] == "interrupted"
    assert result["audit_receipt"] is None
    retained = json.loads(path.read_text())
    assert retained["state"] == "interrupted"
    assert retained["reconciliation_reason"] == "terminal_execution_receipt_missing"


@pytest.mark.parametrize("receipt_kind", ["malformed", "cross-request"])
def test_restart_rejects_unbound_success_receipt(tmp_path, receipt_kind):
    execution_id = "execution-unbound-receipt"
    request_id = "11111111-1111-4111-8111-111111111111"
    other_request_id = "22222222-2222-4222-8222-222222222222"
    audit_id = "audit-unbound-receipt"
    relative = f"controller/controller-audit/{audit_id}.json"
    receipt_path = tmp_path / relative
    receipt_path.parent.mkdir(parents=True)
    receipt = {
        "schema_version": "v1",
        "audit_id": audit_id,
        "request_id": other_request_id if receipt_kind == "cross-request" else request_id,
        "operation": "topology.apply",
        "status": "applied",
        "actor": "dashboard",
        "accepted_at": "2026-10-08T00:00:00Z",
        "completed_at": "2026-10-08T00:00:01Z",
        "request_sha256": DIGEST,
        "plan_sha256": DIGEST,
        "before_sha256": DIGEST,
        "after_sha256": DIGEST,
        "artifact_paths": [relative],
        "redactions": ["authorization"],
    }
    if receipt_kind == "malformed":
        receipt.pop("request_sha256")
    receipt_path.write_text(json.dumps(receipt))
    executions = tmp_path / "controller" / "executions"
    executions.mkdir(exist_ok=True)
    execution_path = executions / f"{execution_id}.json"
    execution_path.write_text(
        json.dumps(
            {
                "schema_version": "v1",
                "execution_id": execution_id,
                "request_id": request_id,
                "operation": "topology.apply",
                "topology_id": "local-lab",
                "state": "applied",
                "accepted_at": "2026-10-08T00:00:00Z",
                "completed_at": "2026-10-08T00:00:01Z",
                "audit_receipt": relative,
            }
        )
    )

    ControllerService(TOKEN, tmp_path, FakeRuntime(), "rev", "test-install")

    retained = json.loads(execution_path.read_text())
    assert retained["state"] == "interrupted"
    assert retained["audit_receipt"] is None
    assert retained["reconciliation_reason"] == "terminal_execution_receipt_missing"


def test_live_status_invalidates_success_when_retained_receipt_disappears(tmp_path):
    runtime = FakeRuntime()
    service = ControllerService(TOKEN, tmp_path, runtime, "rev", "test-install")
    plan = _plan(service)
    execution_id = "execution-receipt-deleted"
    status, result = _dispatch(
        service,
        _request(
            "topology.apply",
            plan_id=plan["plan_id"],
            plan_digest=plan["plan_digest"],
            execution_id=execution_id,
            idempotency_key="apply-receipt-deleted",
        ),
    )
    assert status == 200
    (tmp_path / result["audit_receipt"]).unlink()

    status, observed = _dispatch(
        service,
        _request("execution.status", execution_id=execution_id),
    )

    assert status == 200
    assert observed["state"] == "interrupted"
    assert observed["audit_receipt"] is None
    retained = json.loads(
        (tmp_path / "controller" / "executions" / f"{execution_id}.json").read_text()
    )
    assert retained["reconciliation_reason"] == "terminal_execution_receipt_missing"


@pytest.mark.parametrize("validation", ["live-status", "restart"])
def test_run_result_tampering_is_invalidated_and_never_returned(tmp_path, validation):
    runtime = FakeRuntime()
    service = ControllerService(TOKEN, tmp_path, runtime, "rev", "test-install")
    execution_id = "run-execution-result-tamper"
    status, _ = _dispatch(
        service,
        _request(
            "run.execute",
            launch_id="launch-0123456789abcdef01234567",
            plan_digest=DIGEST,
            execution_id=execution_id,
            idempotency_key="run-result-tamper",
        ),
    )
    assert status == 200
    path = tmp_path / "controller" / "executions" / f"{execution_id}.json"
    record = json.loads(path.read_text())
    record["run_id"] = "forged-run"
    record["result"]["run_id"] = "forged-run"
    record["result"]["security_verdict"] = "forged-pass"
    path.write_text(json.dumps(record))
    if validation == "restart":
        service = ControllerService(TOKEN, tmp_path, runtime, "rev", "test-install")

    status, observed = _dispatch(
        service,
        _request("execution.status", execution_id=execution_id),
    )

    assert status == 200
    assert observed["state"] == "interrupted"
    assert observed["audit_receipt"] is None
    assert observed["run_id"] is None
    assert observed["result"] is None
    retained = json.loads(path.read_text())
    assert retained["reconciliation_reason"] == "terminal_execution_receipt_missing"
    assert retained["run_id"] is None
    assert retained["result"] is None


@pytest.mark.parametrize(
    "tamper", ["filename", "coordinated-clone", "record-id", "request-intent"]
)
def test_restart_rejects_execution_identity_or_intent_tampering(tmp_path, tamper):
    runtime = FakeRuntime()
    service = ControllerService(TOKEN, tmp_path, runtime, "rev", "test-install")
    plan = _plan(service)
    execution_id = "execution-original"
    status, _ = _dispatch(
        service,
        _request(
            "topology.apply",
            plan_id=plan["plan_id"],
            plan_digest=plan["plan_digest"],
            execution_id=execution_id,
            idempotency_key="apply-original",
        ),
    )
    assert status == 200
    original_path = tmp_path / "controller" / "executions" / f"{execution_id}.json"
    record = json.loads(original_path.read_text())
    queried_id = execution_id
    if tamper in {"filename", "coordinated-clone"}:
        queried_id = "execution-clone"
        target_path = original_path.with_name(f"{queried_id}.json")
        if tamper == "coordinated-clone":
            record["execution_id"] = queried_id
        target_path.write_text(json.dumps(record))
    elif tamper == "record-id":
        target_path = original_path
        record["execution_id"] = "execution-other"
        target_path.write_text(json.dumps(record))
    else:
        target_path = original_path
        record["request_sha256"] = "sha256:" + "f" * 64
        target_path.write_text(json.dumps(record))

    restarted = ControllerService(TOKEN, tmp_path, runtime, "rev", "test-install")
    status, observed = _dispatch(
        restarted,
        _request("execution.status", execution_id=queried_id),
    )

    if tamper in {"filename", "record-id"}:
        assert status == 409
        assert "identity" in observed["error"]
    else:
        assert status == 200
        assert observed["state"] == "interrupted"
        assert observed["audit_receipt"] is None
    retained = json.loads(target_path.read_text())
    assert retained["state"] == "interrupted"
