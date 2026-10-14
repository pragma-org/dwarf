from __future__ import annotations

import uuid

import pytest

from controller.contracts import (
    AuditReceipt,
    ContractError,
    Operation,
    authenticate_bearer,
    parse_request,
)


REQUEST_ID = str(uuid.UUID("d05f28b3-d44c-4b3e-a99d-3831c49f48d0"))
DIGEST = "sha256:" + "a" * 64
IMAGE = "ghcr.io/pragma-org/amaru@" + DIGEST


def _request(operation: str, **body):
    return {
        "schema_version": "v1",
        "request_id": REQUEST_ID,
        "operation": operation,
        "timeout_seconds": 10,
        **body,
    }


def _topology():
    return {
        "topology_id": "local-security-lab",
        "network_magic": 42,
        "nodes": [
            {
                "id": "amaru-1",
                "implementation": "amaru",
                "version": "0.3.0",
                "image": IMAGE,
            },
            {
                "id": "cardano-1",
                "implementation": "cardano-node",
                "version": "11.1.2",
                "image": "ghcr.io/intersectmbo/cardano-node@" + DIGEST,
            },
        ],
    }


def test_inventory_contract_is_strict_and_typed():
    request = parse_request(_request("inventory.get"))

    assert request.operation is Operation.INVENTORY_GET
    assert request.timeout_seconds == 10
    assert request.idempotency_key is None
    assert request.parameters == {}

    with pytest.raises(ContractError, match="unknown fields"):
        parse_request(_request("inventory.get", command="docker ps"))
    with pytest.raises(ContractError, match="unsupported operation"):
        parse_request(_request("shell.exec", argv=["docker", "rm", "-f", "everything"]))


def test_image_inspect_contract_is_read_only_bounded_and_source_honest():
    request = parse_request(
        _request(
            "image.inspect",
            implementation="amaru",
            version="10.12.0-local",
            image="local/amaru@" + DIGEST,
            declared_source_revision="b" * 40,
        )
    )

    assert request.operation is Operation.IMAGE_INSPECT
    assert request.idempotency_key is None
    assert dict(request.parameters) == {
        "implementation": "amaru",
        "version": "10.12.0-local",
        "image": "local/amaru@" + DIGEST,
        "declared_source_revision": "b" * 40,
    }
    config = parse_request(
        _request(
            "image.inspect",
            implementation="cardano-node",
            version="11.2-local",
            image=DIGEST,
        )
    )
    assert config.parameters["image"] == DIGEST
    tag_lookup = parse_request(
        _request(
            "image.inspect",
            implementation="amaru",
            version="10.12.0-local",
            image="local/amaru:experimental-1",
        )
    )
    assert tag_lookup.parameters["image"] == "local/amaru:experimental-1"
    for change, message in (
        ({"image": "--format={{json .Config}}"}, "image lookup"),
        ({"image": "https://user:secret@example/image:tag"}, "image lookup"),
        ({"implementation": "mixed"}, "implementation"),
        ({"declared_source_revision": "not-a-revision"}, "source"),
        ({"command": "docker image inspect"}, "unknown fields"),
    ):
        body = _request(
            "image.inspect",
            implementation="amaru",
            version="10.12.0-local",
            image="local/amaru@" + DIGEST,
        )
        body.update(change)
        with pytest.raises(ContractError, match=message):
            parse_request(body)


def test_artifact_package_contract_accepts_only_opaque_frozen_identity():
    request = parse_request(
        _request(
            "artifact.package",
            timeout_seconds=900,
            idempotency_key="package-import-0123456789abcdef01234567",
            import_id="import-0123456789abcdef01234567",
            execution_id="execution-0123456789abcdef0123456789abcdef",
            implementation="amaru",
            version="10.11.local-test",
            platform="linux/amd64",
            upload_sha256="sha256:" + "1" * 64,
            executable_sha256="sha256:" + "2" * 64,
            executable_size=123456,
            declared_source_revision="b" * 40,
        )
    )

    assert request.operation is Operation.ARTIFACT_PACKAGE
    assert request.timeout_seconds == 900
    assert dict(request.parameters) == {
        "import_id": "import-0123456789abcdef01234567",
        "execution_id": "execution-0123456789abcdef0123456789abcdef",
        "implementation": "amaru",
        "version": "10.11.local-test",
        "platform": "linux/amd64",
        "upload_sha256": "sha256:" + "1" * 64,
        "executable_sha256": "sha256:" + "2" * 64,
        "executable_size": 123456,
        "declared_source_revision": "b" * 40,
    }


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"import_id": "../../tmp/payload"}, "import_id"),
        ({"execution_id": "execution-not-canonical"}, "execution_id"),
        ({"implementation": "mixed"}, "implementation"),
        ({"version": "bad label"}, "version"),
        ({"platform": "linux/mips64"}, "platform"),
        ({"upload_sha256": "sha256:nope"}, "upload_sha256"),
        ({"executable_sha256": "2" * 64}, "executable_sha256"),
        ({"executable_size": 0}, "executable_size"),
        ({"executable_size": 256 * 1024 * 1024 + 1}, "executable_size"),
        ({"declared_source_revision": "unverified"}, "source"),
        ({"staging_path": "/tmp/upload"}, "unknown fields"),
        ({"filename": "amaru"}, "unknown fields"),
    ],
)
def test_artifact_package_contract_rejects_browser_paths_and_malformed_identity(
    change, message
):
    body = _request(
        "artifact.package",
        timeout_seconds=900,
        idempotency_key="package-import-0123456789abcdef01234567",
        import_id="import-0123456789abcdef01234567",
        execution_id="execution-0123456789abcdef0123456789abcdef",
        implementation="amaru",
        version="local-test",
        platform="linux/amd64",
        upload_sha256="sha256:" + "1" * 64,
        executable_sha256="sha256:" + "2" * 64,
        executable_size=123456,
    )
    body.update(change)
    with pytest.raises(ContractError, match=message):
        parse_request(body)


def test_topology_health_contract_accepts_only_a_safe_topology_id():
    request = parse_request(
        _request("topology.health", topology_id="profile-e-haskell-preview-proof")
    )

    assert request.operation is Operation.TOPOLOGY_HEALTH
    assert request.parameters == {"topology_id": "profile-e-haskell-preview-proof"}
    with pytest.raises(ContractError, match="unknown fields"):
        parse_request(
            _request(
                "topology.health",
                topology_id="profile-e-haskell-preview-proof",
                command="docker exec",
            )
        )


def test_topology_plan_accepts_only_resolved_digest_pinned_nodes():
    request = parse_request(
        _request("topology.plan", action="deploy", topology=_topology(), timeout_seconds=30)
    )

    assert request.operation is Operation.TOPOLOGY_PLAN
    assert request.parameters["action"] == "deploy"
    assert request.parameters["topology"]["nodes"][0]["image"] == IMAGE

    unsafe = _topology()
    unsafe["nodes"][0]["image"] = "ghcr.io/pragma-org/amaru:latest"
    with pytest.raises(ContractError, match="digest-pinned"):
        parse_request(_request("topology.plan", action="deploy", topology=unsafe))


def test_topology_plan_accepts_verified_local_config_content_identity():
    topology = _topology()
    topology["nodes"][0]["image"] = DIGEST

    request = parse_request(
        _request("topology.plan", action="deploy", topology=topology, timeout_seconds=30)
    )

    assert request.parameters["topology"]["nodes"][0]["image"] == DIGEST


def test_topology_plan_preserves_exact_resolver_catalog_revision():
    topology = _topology()
    topology["catalog_revision"] = "b" * 64

    request = parse_request(
        _request("topology.plan", action="deploy", topology=topology, timeout_seconds=30)
    )

    assert request.parameters["topology"]["catalog_revision"] == "b" * 64

    topology["catalog_revision"] = "not-a-catalog-revision"
    with pytest.raises(ContractError, match="catalog_revision"):
        parse_request(
            _request("topology.plan", action="deploy", topology=topology)
        )


def test_topology_plan_preserves_exact_operator_registration_identity():
    topology = _topology()
    topology.update(
        {
            "operator_registration_id": "registration-946656af29004639bb6e398cc8d52658",
            "operator_overlay_revision": "sha256:" + "c" * 64,
        }
    )

    request = parse_request(
        _request("topology.plan", action="repair", topology=topology, timeout_seconds=30)
    )

    assert request.parameters["topology"]["operator_registration_id"] == (
        "registration-946656af29004639bb6e398cc8d52658"
    )
    assert request.parameters["topology"]["operator_overlay_revision"] == (
        "sha256:" + "c" * 64
    )

    for missing in ("operator_registration_id", "operator_overlay_revision"):
        incomplete = dict(topology)
        incomplete.pop(missing)
        with pytest.raises(ContractError, match="must be provided together"):
            parse_request(_request("topology.plan", action="repair", topology=incomplete))

    malformed = dict(topology)
    malformed["operator_overlay_revision"] = "not-a-revision"
    with pytest.raises(ContractError, match="operator_overlay_revision"):
        parse_request(_request("topology.plan", action="repair", topology=malformed))

    both_null = dict(_topology())
    both_null.update(
        {"operator_registration_id": None, "operator_overlay_revision": None}
    )
    with pytest.raises(ContractError, match="operator_registration_id"):
        parse_request(_request("topology.plan", action="repair", topology=both_null))

    null_only = dict(_topology())
    null_only["operator_registration_id"] = None
    with pytest.raises(ContractError, match="must be provided together"):
        parse_request(_request("topology.plan", action="repair", topology=null_only))


def test_topology_plan_accepts_typed_repair():
    request = parse_request(
        _request("topology.plan", action="repair", topology=_topology(), timeout_seconds=30)
    )

    assert request.parameters["action"] == "repair"
    assert request.parameters["topology"]["topology_id"] == "local-security-lab"


@pytest.mark.parametrize(
    "field,value",
    [
        ("command", "docker rm -f dwarf"),
        ("host_path", "/var/run/docker.sock"),
        ("environment", {"DOCKER_HOST": "unix:///var/run/docker.sock"}),
        ("bind_mounts", ["/:/host"]),
    ],
)
def test_topology_plan_rejects_arbitrary_execution_and_host_paths(field, value):
    payload = _request("topology.plan", action="deploy", topology=_topology())
    payload[field] = value

    with pytest.raises(ContractError, match="unknown fields"):
        parse_request(payload)


def test_apply_is_plan_bound_and_requires_replay_key():
    request = parse_request(
        _request(
            "topology.apply",
            plan_id="plan-20261008-001",
            plan_digest=DIGEST,
            idempotency_key="deploy-local-security-lab-20261008-001",
            timeout_seconds=2400,
        )
    )
    assert request.operation is Operation.TOPOLOGY_APPLY
    assert request.idempotency_key == "deploy-local-security-lab-20261008-001"

    identified = parse_request(
        _request(
            "topology.apply",
            plan_id="plan-20261008-001",
            plan_digest=DIGEST,
            execution_id="execution-local-security-lab-001",
            idempotency_key="deploy-local-security-lab-20261008-002",
            timeout_seconds=2400,
        )
    )
    assert identified.parameters["execution_id"] == "execution-local-security-lab-001"

    with pytest.raises(ContractError, match="idempotency_key"):
        parse_request(
            _request("topology.apply", plan_id="plan-1", plan_digest=DIGEST)
        )
    with pytest.raises(ContractError, match="unknown fields"):
        parse_request(
            _request(
                "topology.apply",
                plan_id="plan-1",
                plan_digest=DIGEST,
                idempotency_key="apply-plan-1",
                topology=_topology(),
            )
        )


def test_run_execute_is_launch_bound_and_forbids_arbitrary_commands():
    request = parse_request(
        _request(
            "run.execute",
            launch_id="launch-0123456789abcdef01234567",
            plan_digest=DIGEST,
            execution_id="run-execution-0123456789abcdef",
            idempotency_key="run-launch-0123456789abcdef01234567",
            timeout_seconds=2400,
        )
    )

    assert request.operation is Operation.RUN_EXECUTE
    assert request.parameters == {
        "launch_id": "launch-0123456789abcdef01234567",
        "plan_digest": DIGEST,
        "execution_id": "run-execution-0123456789abcdef",
    }
    with pytest.raises(ContractError, match="unknown fields"):
        parse_request(
            _request(
                "run.execute",
                launch_id="launch-0123456789abcdef01234567",
                plan_digest=DIGEST,
                execution_id="run-execution-0123456789abcdef",
                idempotency_key="run-launch-unsafe",
                timeout_seconds=2400,
                command=["docker", "rm", "-f", "everything"],
            )
        )
def test_save_uses_a_safe_logical_filename_not_an_operator_path():
    request = parse_request(
        _request(
            "topology.save",
            topology_id="local-security-lab",
            file_name="local-security-lab.yaml",
            idempotency_key="save-local-security-lab-1",
        )
    )
    assert request.parameters["file_name"] == "local-security-lab.yaml"

    for unsafe in ("../escape.yaml", "/tmp/topology.yaml", "nested/topology.yaml"):
        with pytest.raises(ContractError, match="file_name"):
            parse_request(
                _request(
                    "topology.save",
                    topology_id="local-security-lab",
                    file_name=unsafe,
                    idempotency_key="save-local-security-lab-1",
                )
            )


def test_log_stream_and_cancel_are_bounded_identifier_only_contracts():
    logs = parse_request(
        _request(
            "logs.stream",
            resource_kind="topology",
            resource_id="local-security-lab",
            cursor="cursor-0001",
            max_lines=500,
            follow_seconds=20,
        )
    )
    assert logs.parameters["max_lines"] == 500

    cancel = parse_request(
        _request(
            "execution.cancel",
            execution_id="run-20261008-001",
            reason="operator requested cancellation",
            idempotency_key="cancel-run-20261008-001",
        )
    )
    assert cancel.operation is Operation.EXECUTION_CANCEL

    status = parse_request(
        _request(
            "execution.status",
            execution_id="run-20261008-001",
        )
    )
    assert status.operation is Operation.EXECUTION_STATUS
    assert status.parameters == {"execution_id": "run-20261008-001"}

    with pytest.raises(ContractError, match="follow_seconds"):
        parse_request(
            _request(
                "logs.stream",
                resource_kind="topology",
                resource_id="local-security-lab",
                max_lines=100,
                follow_seconds=31,
            )
        )
    with pytest.raises(ContractError, match="execution_id"):
        parse_request(
            _request(
                "execution.cancel",
                execution_id="run-1; docker rm -f x",
                reason="unsafe",
                idempotency_key="cancel-run-1",
            )
        )


def test_operation_specific_timeout_ceiling_is_enforced():
    with pytest.raises(ContractError, match="timeout_seconds"):
        parse_request(_request("inventory.get", timeout_seconds=11))
    with pytest.raises(ContractError, match="timeout_seconds"):
        parse_request(
            _request(
                "topology.apply",
                plan_id="plan-1",
                plan_digest=DIGEST,
                idempotency_key="apply-plan-1",
                timeout_seconds=2401,
            )
        )

    health = parse_request(
        _request(
            "topology.health",
            topology_id="profile-z",
            timeout_seconds=120,
        )
    )
    assert health.timeout_seconds == 120
    with pytest.raises(ContractError, match="timeout_seconds"):
        parse_request(
            _request(
                "topology.health",
                topology_id="profile-z",
                timeout_seconds=121,
            )
        )


def test_bearer_authentication_fails_closed_without_logging_or_fallback():
    token = "t" * 48
    authenticate_bearer(f"Bearer {token}", token)

    for header in (None, "", token, "Basic " + token, "Bearer wrong"):
        with pytest.raises(ContractError, match="authentication failed"):
            authenticate_bearer(header, token)
    with pytest.raises(ContractError, match="configured token"):
        authenticate_bearer("Bearer short", "short")


def test_audit_receipt_requires_digests_and_bundle_relative_artifacts():
    receipt = AuditReceipt.from_mapping(
        {
            "schema_version": "v1",
            "audit_id": "audit-20261008-001",
            "request_id": REQUEST_ID,
            "operation": "topology.apply",
            "status": "applied",
            "actor": "dashboard",
            "accepted_at": "2026-10-08T05:00:00Z",
            "completed_at": "2026-10-08T05:00:05Z",
            "request_sha256": DIGEST,
            "plan_sha256": DIGEST,
            "before_sha256": DIGEST,
            "after_sha256": DIGEST,
            "artifact_paths": ["controller-audit/audit-20261008-001.json"],
            "redactions": ["authorization"],
        }
    )
    assert receipt.status == "applied"
    assert receipt.artifact_paths == (
        "controller-audit/audit-20261008-001.json",
    )

    with pytest.raises(ContractError, match="artifact_paths"):
        AuditReceipt.from_mapping(
            {
                **receipt.to_mapping(),
                "artifact_paths": ["/etc/shadow"],
            }
        )
