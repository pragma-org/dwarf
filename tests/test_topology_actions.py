from __future__ import annotations

import io
import json
import threading
from urllib.error import HTTPError

import pytest

from profile_manager import topology_actions
from profile_manager.topology_actions import (
    TopologyActionError,
    TopologyActionService,
    controller_request,
)


DIGEST = "sha256:" + "a" * 64


def test_controller_request_preserves_verified_terminal_failure(monkeypatch):
    execution_id = "execution-terminal-failure-001"
    audit_receipt = "controller/controller-audit/audit-terminal-failure.json"
    response = io.BytesIO(
        json.dumps(
            {
                "ok": False,
                "error": "retained topology differs from the current catalog plan",
                "execution_id": execution_id,
                "audit_receipt": audit_receipt,
            }
        ).encode()
    )

    def fail(request, timeout):
        raise HTTPError(request.full_url, 500, "failed", {}, response)

    monkeypatch.setenv("DWARF_CONTROLLER_URL", "http://127.0.0.1:9998")
    monkeypatch.setenv("DWARF_CONTROLLER_TOKEN", "controller-token-" + "x" * 32)
    monkeypatch.setattr(topology_actions, "urlopen", fail)

    with pytest.raises(
        TopologyActionError,
        match="retained topology differs from the current catalog plan",
    ) as raised:
        controller_request("topology.apply", {}, 10)

    assert raised.value.terminal is True
    assert raised.value.execution_id == execution_id
    assert raised.value.audit_receipt == audit_receipt


def test_controller_request_preserves_only_structured_run_failure(monkeypatch):
    failure = {
        "type": "DockerRuntimeError",
        "message": "scenario runner returned no canonical run identity",
        "code": "run_adapter_identity_missing",
        "stage": "adapter_execution",
        "return_code": 37,
        "trace": ["dwarf/controller/docker_runtime.py:1406:run_launch"],
    }
    response = io.BytesIO(
        json.dumps(
            {
                "ok": False,
                "error": "controller run failed",
                "failure": failure,
                "execution_id": "run-execution-retained-failure",
                "audit_receipt": (
                    "controller/controller-audit/audit-retained-failure.json"
                ),
            }
        ).encode()
    )

    def fail(request, timeout):
        raise HTTPError(request.full_url, 500, "PRIVATE_HTTP_SECRET", {}, response)

    monkeypatch.setenv("DWARF_CONTROLLER_URL", "http://127.0.0.1:9998")
    monkeypatch.setenv("DWARF_CONTROLLER_TOKEN", "controller-token-" + "x" * 32)
    monkeypatch.setattr(topology_actions, "urlopen", fail)

    with pytest.raises(TopologyActionError) as raised:
        controller_request("run.execute", {}, 10, "run-retained-failure")

    assert raised.value.failure == failure
    assert "PRIVATE_HTTP_SECRET" not in str(raised.value)


def _snapshot(*, action="deploy", allowed=True):
    return {
        "schema_version": "v1",
        "topology_id": "profile-e-haskell-preview-proof",
        "profile_id": "profile-e-haskell-preview-proof",
        "health": {"fresh": True},
        "desired": {
            "topology_id": "profile-e-haskell-preview-proof",
            "network_magic": 42,
            "catalog_revision": "b" * 64,
            "nodes": [
                {
                    "id": "cardano-1",
                    "implementation": "cardano-node",
                    "version": "11.1.2",
                    "image": "ghcr.io/intersectmbo/cardano-node@" + DIGEST,
                    "role": "producer",
                    "supporting": False,
                }
            ],
        },
        "observed": {"managed": [], "unmanaged": []},
        "actions": {action: {"allowed": allowed, "reason": "test policy"}},
    }


def test_preview_and_apply_use_exact_controller_plan_identity_and_ledger(tmp_path):
    calls = []

    def controller(operation, parameters, timeout_seconds, idempotency_key=None):
        calls.append((operation, parameters, idempotency_key))
        if operation == "topology.plan":
            return {
                "status": "planned",
                "plan_id": "plan-exact-001",
                "plan_digest": DIGEST,
                "action": "deploy",
                "topology_id": "profile-e-haskell-preview-proof",
            }
        assert parameters["plan_id"] == "plan-exact-001"
        assert parameters["plan_digest"] == DIGEST
        assert parameters["execution_id"].startswith("execution-")
        return {
            "status": "applied",
            "execution_id": parameters["execution_id"],
            "audit_receipt": "controller/controller-audit/audit-exact.json",
        }

    service = TopologyActionService(controller, tmp_path)
    preview = service.preview("deploy", _snapshot())
    events = list(service.apply(preview["plan_id"], preview["plan_digest"]))

    assert preview["execution_id"].startswith("execution-")
    assert events[0]["execution_id"] == preview["execution_id"]
    assert [event["stage"] for event in events] == ["accepted", "completed"]
    assert calls[0][0] == "topology.plan"
    assert set(calls[0][1]["topology"]["nodes"][0]) == {
        "id", "implementation", "version", "image"
    }
    assert calls[0][1]["topology"]["catalog_revision"] == "b" * 64
    assert calls[1][0] == "topology.apply"
    record = json.loads(
        (tmp_path / "topology-actions" / "plan-exact-001.json").read_text()
    )
    assert record["state"] == "applied"
    assert record["controller_audit_receipt"] == "controller/controller-audit/audit-exact.json"

    with pytest.raises(TopologyActionError, match="already applied"):
        list(service.apply(preview["plan_id"], preview["plan_digest"]))
    assert [call[0] for call in calls].count("topology.apply") == 1
    assert json.loads(
        (tmp_path / "topology-actions" / "plan-exact-001.json").read_text()
    )["state"] == "applied"


def test_terminal_controller_failure_retains_exact_cause_and_receipt(tmp_path):
    execution_id = "execution-terminal-failure-001"
    audit_receipt = "controller/controller-audit/audit-terminal-failure.json"

    def controller(operation, parameters, timeout_seconds, idempotency_key=None):
        if operation == "topology.plan":
            return {
                "status": "planned",
                "plan_id": "plan-terminal-failure-001",
                "plan_digest": DIGEST,
                "action": "deploy",
                "topology_id": "profile-e-haskell-preview-proof",
            }
        if operation == "topology.apply":
            raise TopologyActionError(
                "retained topology differs from the current catalog plan",
                execution_id=parameters["execution_id"],
                audit_receipt=audit_receipt,
                terminal=True,
            )
        raise AssertionError(operation)

    service = TopologyActionService(controller, tmp_path)
    preview = service.preview("deploy", _snapshot())
    assert preview["execution_id"] != execution_id

    with pytest.raises(
        TopologyActionError,
        match="retained topology differs from the current catalog plan",
    ) as raised:
        list(service.apply(preview["plan_id"], preview["plan_digest"]))

    assert raised.value.terminal is True
    assert raised.value.execution_id == preview["execution_id"]
    assert raised.value.audit_receipt == audit_receipt
    record = json.loads(
        (tmp_path / "topology-actions" / "plan-terminal-failure-001.json").read_text()
    )
    assert record["state"] == "failed"
    assert record["error"] == "retained topology differs from the current catalog plan"
    assert record["controller_audit_receipt"] == audit_receipt


def test_preview_binding_reuses_planned_and_uncertain_but_not_terminal_failed(
    tmp_path,
):
    attempts = 0

    def controller(operation, parameters, timeout_seconds, idempotency_key=None):
        nonlocal attempts
        if operation == "topology.plan":
            return {
                "status": "planned",
                "plan_id": "plan-binding-state-001",
                "plan_digest": DIGEST,
                "action": "deploy",
                "topology_id": "profile-e-haskell-preview-proof",
            }
        if operation == "topology.apply":
            attempts += 1
            if attempts == 1:
                raise TopologyActionError("controller request failed")
            raise TopologyActionError(
                "runtime gates failed: exact_identity",
                execution_id=parameters["execution_id"],
                audit_receipt="controller/controller-audit/audit-binding-state.json",
                terminal=True,
            )
        raise AssertionError(operation)

    service = TopologyActionService(controller, tmp_path)
    preview = service.preview("deploy", _snapshot())
    snapshot = _snapshot()

    def reusable(**changes):
        arguments = {
            "plan_id": preview["plan_id"],
            "plan_digest": preview["plan_digest"],
            "execution_id": preview["execution_id"],
            "action": "deploy",
            "topology_id": "profile-e-haskell-preview-proof",
            "snapshot": snapshot,
            **changes,
        }
        return service.preview_binding_reusable(**arguments)

    assert reusable() is True
    assert service.preview_binding_reusable(
        preview["plan_id"],
        "sha256:" + "0" * 64,
        preview["execution_id"],
        action="deploy",
        topology_id="profile-e-haskell-preview-proof",
        snapshot=snapshot,
    ) is False
    assert reusable(action="repair") is False
    assert reusable(topology_id="profile-other") is False

    volatile_snapshot = json.loads(json.dumps(snapshot))
    volatile_snapshot.update(
        {
            "checked_at": "2026-10-12T00:28:30Z",
            "generated_at": "2026-10-12T00:28:31Z",
        }
    )
    volatile_snapshot["health"] = {
        "fresh": True,
        "checked_at": "2026-10-12T00:28:30Z",
        "age_seconds": 1,
        "observation": {"tip_slot": 1315, "tip_block": 245},
        "observations": [
            {
                "checked_at": "2026-10-12T00:28:30Z",
                "tip": {"slot": 1315, "block": 245},
            }
        ],
    }
    assert reusable(snapshot=volatile_snapshot) is True

    for field, value in (
        ("observed", {"managed": [{"node_id": "foreign"}], "unmanaged": []}),
        ("reconciliation", {"state": "drifted"}),
        ("actions", {"deploy": {"allowed": False, "reason": "policy changed"}}),
    ):
        changed = json.loads(json.dumps(volatile_snapshot))
        changed[field] = value
        assert reusable(snapshot=changed) is False

    changed_health = json.loads(json.dumps(volatile_snapshot))
    changed_health["health"]["state"] = "unhealthy"
    changed_health["health"]["reason_code"] = "chain_progress_failed"
    assert reusable(snapshot=changed_health) is False

    changed_snapshot = json.loads(json.dumps(snapshot))
    changed_snapshot["desired"]["nodes"][0]["version"] = "changed"
    assert reusable(snapshot=changed_snapshot) is False

    with pytest.raises(TopologyActionError, match="controller request failed"):
        list(service.apply(preview["plan_id"], preview["plan_digest"]))
    assert reusable() is True

    with pytest.raises(TopologyActionError, match="exact_identity"):
        list(service.apply(preview["plan_id"], preview["plan_digest"]))
    assert reusable() is False

    record_path = tmp_path / "topology-actions" / f"{preview['plan_id']}.json"
    unknown = json.loads(record_path.read_text(encoding="utf-8"))
    unknown["state"] = "unknown"
    record_path.write_text(json.dumps(unknown), encoding="utf-8")
    with pytest.raises(TopologyActionError, match="cannot be reconciled"):
        reusable()


def test_apply_streams_controller_execution_progress_before_terminal_result(tmp_path):
    release_apply = threading.Event()
    status_calls = []

    def controller(operation, parameters, timeout_seconds, idempotency_key=None):
        if operation == "topology.plan":
            return {
                "status": "planned",
                "plan_id": "plan-progress-001",
                "plan_digest": DIGEST,
                "action": "deploy",
                "topology_id": "profile-e-haskell-preview-proof",
            }
        if operation == "execution.status":
            status_calls.append(parameters["execution_id"])
            release_apply.set()
            return {
                "schema_version": "v1",
                "execution_id": parameters["execution_id"],
                "state": "running",
                "accepted_at": "2026-10-08T00:00:00Z",
                "completed_at": None,
                "audit_receipt": None,
            }
        assert operation == "topology.apply"
        assert release_apply.wait(timeout=0.5)
        return {
            "status": "applied",
            "execution_id": parameters["execution_id"],
            "audit_receipt": "controller/controller-audit/audit-progress.json",
        }

    service = TopologyActionService(controller, tmp_path)
    preview = service.preview("deploy", _snapshot())

    events = list(service.apply(preview["plan_id"], preview["plan_digest"]))

    assert [event["stage"] for event in events] == [
        "accepted",
        "progress",
        "completed",
    ]
    assert events[1]["controller_state"] == "running"
    assert status_calls == [events[0]["execution_id"]]


@pytest.mark.parametrize("terminal", ["applied", "cancelled"])
def test_apply_refuses_receiptless_controller_success(tmp_path, terminal):
    def controller(operation, parameters, timeout_seconds, idempotency_key=None):
        if operation == "topology.plan":
            return {
                "status": "planned",
                "plan_id": f"plan-receiptless-{terminal}",
                "plan_digest": DIGEST,
                "action": "deploy",
                "topology_id": "profile-e-haskell-preview-proof",
            }
        assert operation == "topology.apply"
        return {"status": terminal, "execution_id": parameters["execution_id"]}

    service = TopologyActionService(controller, tmp_path)
    preview = service.preview("deploy", _snapshot())

    with pytest.raises(TopologyActionError, match="invalid apply result"):
        list(service.apply(preview["plan_id"], preview["plan_digest"]))

    record = json.loads(
        (tmp_path / "topology-actions" / f"plan-receiptless-{terminal}.json").read_text()
    )
    assert record["state"] == "failed"


@pytest.mark.parametrize("terminal", ["applied", "cancelled"])
def test_status_refuses_receiptless_controller_success(tmp_path, terminal):
    def controller(operation, parameters, timeout_seconds, idempotency_key=None):
        if operation == "topology.plan":
            return {
                "status": "planned",
                "plan_id": f"plan-status-receiptless-{terminal}",
                "plan_digest": DIGEST,
                "action": "deploy",
                "topology_id": "profile-e-haskell-preview-proof",
            }
        assert operation == "execution.status"
        return {"state": terminal, "execution_id": parameters["execution_id"]}

    service = TopologyActionService(controller, tmp_path)
    preview = service.preview("deploy", _snapshot())

    with pytest.raises(TopologyActionError, match="invalid execution status"):
        service.status(preview["execution_id"])


def test_disconnected_progress_generator_retains_mutation_lock_until_worker_stops(
    tmp_path,
):
    apply_started = threading.Event()
    release_apply = threading.Event()
    close_finished = threading.Event()
    plan_count = 0

    def controller(operation, parameters, timeout_seconds, idempotency_key=None):
        nonlocal plan_count
        if operation == "topology.plan":
            plan_count += 1
            return {
                "status": "planned",
                "plan_id": f"plan-disconnect-{plan_count}",
                "plan_digest": DIGEST,
                "action": "deploy",
                "topology_id": "profile-e-haskell-preview-proof",
            }
        if operation == "execution.status":
            return {
                "schema_version": "v1",
                "execution_id": parameters["execution_id"],
                "state": "running",
            }
        assert operation == "topology.apply"
        apply_started.set()
        assert release_apply.wait(timeout=1)
        return {
            "status": "applied",
            "execution_id": parameters["execution_id"],
            "audit_receipt": "controller/controller-audit/audit-disconnect.json",
        }

    service = TopologyActionService(controller, tmp_path)
    first = service.preview("deploy", _snapshot())
    second = service.preview("deploy", _snapshot())
    stream = service.apply(first["plan_id"], first["plan_digest"])
    accepted = next(stream)
    assert accepted["stage"] == "accepted"
    assert apply_started.wait(timeout=0.2)

    def close_stream():
        stream.close()
        close_finished.set()

    closer = threading.Thread(target=close_stream)
    closer.start()
    assert not close_finished.wait(timeout=0.05)
    with pytest.raises(TopologyActionError, match="another topology mutation"):
        list(service.apply(second["plan_id"], second["plan_digest"]))

    release_apply.set()
    assert close_finished.wait(timeout=0.5)
    closer.join()


def test_preview_refuses_disallowed_or_unmanaged_action(tmp_path):
    service = TopologyActionService(
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("controller called")),
        tmp_path,
    )
    with pytest.raises(TopologyActionError, match="not allowed"):
        service.preview("deploy", _snapshot(allowed=False))

    snapshot = _snapshot()
    snapshot["observed"]["unmanaged"] = [{"node_id": "cardano-1"}]
    with pytest.raises(TopologyActionError, match="unmanaged"):
        service.preview("deploy", snapshot)


def test_destructive_actions_require_exact_confirmation_and_cancel_is_typed(tmp_path):
    calls = []

    def controller(operation, parameters, timeout_seconds, idempotency_key=None):
        calls.append((operation, parameters, idempotency_key))
        if operation == "topology.plan":
            return {
                "status": "planned",
                "plan_id": "plan-remove-001",
                "plan_digest": DIGEST,
                "action": "remove",
                "topology_id": "profile-e-haskell-preview-proof",
            }
        if operation == "execution.cancel":
            return {"status": "cancelling", "execution_id": parameters["execution_id"]}
        return {
            "status": "applied",
            "execution_id": parameters["execution_id"],
            "audit_receipt": "controller/controller-audit/audit-remove.json",
        }

    service = TopologyActionService(controller, tmp_path)
    snapshot = _snapshot(action="remove")
    snapshot["observed"]["managed"] = [{"node_id": "cardano-1", "ownership": "managed"}]
    preview = service.preview("remove", snapshot)

    with pytest.raises(TopologyActionError, match="confirmation"):
        list(service.apply(preview["plan_id"], preview["plan_digest"], confirmation="yes"))
    events = list(
        service.apply(
            preview["plan_id"],
            preview["plan_digest"],
            confirmation="REMOVE profile-e-haskell-preview-proof",
        )
    )
    execution_id = events[0]["execution_id"]
    cancelled = service.cancel(execution_id, "operator requested cancellation")

    assert cancelled["status"] == "cancelling"
    assert calls[-1][0] == "execution.cancel"
    assert calls[-1][1] == {
        "execution_id": execution_id,
        "reason": "operator requested cancellation",
    }


def test_uncertain_apply_reuses_execution_identity_and_idempotency_on_retry(tmp_path):
    apply_calls = []

    def controller(operation, parameters, timeout_seconds, idempotency_key=None):
        if operation == "topology.plan":
            return {
                "status": "planned",
                "plan_id": "plan-retry-001",
                "plan_digest": DIGEST,
                "action": "deploy",
                "topology_id": "profile-e-haskell-preview-proof",
            }
        apply_calls.append((dict(parameters), idempotency_key))
        if len(apply_calls) == 1:
            raise TopologyActionError("controller response was lost")
        return {
            "status": "applied",
            "execution_id": parameters["execution_id"],
            "audit_receipt": "controller/controller-audit/audit-retry.json",
        }

    service = TopologyActionService(controller, tmp_path)
    preview = service.preview("deploy", _snapshot())
    with pytest.raises(TopologyActionError, match="response was lost"):
        list(service.apply(preview["plan_id"], preview["plan_digest"]))

    record = json.loads(
        (tmp_path / "topology-actions" / "plan-retry-001.json").read_text()
    )
    assert record["state"] == "uncertain"
    events = list(service.apply(preview["plan_id"], preview["plan_digest"]))

    assert events[-1]["status"] == "applied"
    assert apply_calls[0] == apply_calls[1]


def test_execution_status_reconciles_uncertain_ledger_with_controller_receipt(tmp_path):
    def controller(operation, parameters, timeout_seconds, idempotency_key=None):
        assert operation == "execution.status"
        assert parameters == {"execution_id": "execution-reconcile-001"}
        return {
            "schema_version": "v1",
            "execution_id": "execution-reconcile-001",
            "state": "applied",
            "audit_receipt": "controller/controller-audit/audit-reconcile.json",
        }

    service = TopologyActionService(controller, tmp_path)
    service.root.mkdir(parents=True)
    (service.root / "plan-reconcile-001.json").write_text(
        json.dumps(
            {
                "schema_version": "v1",
                "plan_id": "plan-reconcile-001",
                "plan_digest": DIGEST,
                "action": "deploy",
                "topology_id": "profile-e-haskell-preview-proof",
                "state": "uncertain",
                "execution_id": "execution-reconcile-001",
                "events": [],
            }
        ),
        encoding="utf-8",
    )

    result = service.status("execution-reconcile-001")

    assert result["state"] == "applied"
    retained = json.loads((service.root / "plan-reconcile-001.json").read_text())
    assert retained["state"] == "applied"
    assert retained["controller_audit_receipt"].endswith("audit-reconcile.json")
