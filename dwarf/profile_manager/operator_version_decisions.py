"""Durable local operator registrations, decisions, and preferences.

This ledger is deliberately separate from DWARF's shared version catalog and
qualification records.  Every mutation is an append guarded by an optimistic
revision and an interprocess lock; readers either see one complete document or
fail closed.
"""
from __future__ import annotations

import copy
import errno
import fcntl
import hashlib
import json
import os
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from jsonschema import Draft202012Validator, FormatChecker

from profile_manager.config import storage_roots
from profile_manager.run_ledger import atomic_write_bytes


MAX_STATE_BYTES = 4 * 1024 * 1024
_SCHEMA_PATH = Path(__file__).parent / "schemas/operator-version-decisions-v1.schema.json"
_VALIDATOR = Draft202012Validator(
    json.loads(_SCHEMA_PATH.read_text(encoding="utf-8")),
    format_checker=FormatChecker(),
)


class InvalidOperatorVersionState(ValueError):
    """The persisted document does not satisfy the versioned contract."""


class UnsafeOperatorVersionState(ValueError):
    """The state path or opened object is not safe to trust or mutate."""


class StaleOperatorVersionState(RuntimeError):
    """The supplied optimistic revision no longer names current state."""


class IndeterminateOperatorVersionCommit(RuntimeError):
    """Replacement is visible but its parent-directory durability is unknown."""

    def __init__(self, observed_revision: str):
        self.observed_revision = observed_revision
        super().__init__(
            "operator version state commit durability is indeterminate; "
            f"observed revision {observed_revision}"
        )


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def canonical_revision(document: dict[str, Any]) -> str:
    """Hash canonical content while excluding the document's own revision."""
    content = {key: value for key, value in document.items() if key != "revision"}
    return "sha256:" + hashlib.sha256(_canonical_bytes(content)).hexdigest()


def subject_key(subject: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(subject)).hexdigest()


def preference_scope_key(subject: dict[str, Any]) -> str:
    return f"{subject.get('scope')}:{subject.get('topology_profile_id')}"


def _exact_sha256(value: Any, *, field: str) -> str:
    text = str(value or "")
    if text.startswith("sha256:"):
        text = text[7:]
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise InvalidOperatorVersionState(f"canonical evidence has invalid {field}")
    return text


def decision_subject_from_plan_projection(evidence: dict[str, Any]) -> dict[str, Any]:
    """Project a prospective subject from one frozen run plan, not run evidence."""
    try:
        scenario = evidence["scenario"]
        profile = evidence["profile"]
        versions = evidence["versions"]
        target = evidence["measurements"]["target_identity"]
        implementation = str(target["implementation"])
        version = str(target["version"])
        resolved = versions["resolved"][implementation]
    except (KeyError, TypeError) as exc:
        raise InvalidOperatorVersionState("canonical evidence is identity-incomplete") from exc
    if resolved.get("version") != version:
        raise InvalidOperatorVersionState("canonical evidence release identity disagrees")
    image_reference = target.get("image_reference")
    manifest_digest = target.get("image_manifest_digest") or target.get("image_digest")
    config_image_id = target.get("image_config_digest") or target.get("image_config_id")
    if not isinstance(image_reference, str) or not image_reference:
        raise InvalidOperatorVersionState("canonical evidence has no image reference")
    if config_image_id is None:
        raise InvalidOperatorVersionState("canonical evidence has no inspected config image ID")
    subject = {
        "implementation": implementation,
        "version": version,
        "image_reference": image_reference,
        "manifest_digest": "sha256:" + _exact_sha256(manifest_digest, field="manifest digest"),
        "config_image_id": "sha256:" + _exact_sha256(config_image_id, field="config image ID"),
        "scenario_id": str(scenario["id"]),
        "scenario_sha256": _exact_sha256(scenario["sha256"], field="scenario SHA-256"),
        "scope": str(versions["scope"]),
        "topology_profile_id": str(profile["id"]),
        "deployment_identity_sha256": _exact_sha256(
            versions["deployment_identity_sha256"], field="deployment identity"
        ),
        "mixed_pair": copy.deepcopy(versions.get("pair")),
    }
    probe = empty_state()
    probe["decisions"] = [{
        "decision_id": "validation-probe", "subject_key": subject_key(subject),
        "subject": subject, "decision": "supported", "reason": "validation probe",
        "actor": "server", "decided_at": "1970-01-01T00:00:00Z",
        "evidence": {"run_id": "validation-probe", "manifest_sha256": "0" * 64,
                     "operational_outcome": "completed", "assertion_verdict": "unknown"},
    }]
    probe["revision"] = canonical_revision(probe)
    validate_state(probe)
    return subject


def decision_subject_from_evidence(manifest: dict[str, Any]) -> dict[str, Any]:
    """Read a future immutable manifest-bound subject; never rebuild old evidence."""
    stamped = manifest.get("operator_version_evidence")
    if not isinstance(stamped, dict):
        raise InvalidOperatorVersionState(
            "canonical manifest has no immutable operator version evidence binding"
        )
    subject = stamped.get("subject")
    if not isinstance(subject, dict):
        raise InvalidOperatorVersionState("operator version evidence subject is missing")
    if stamped.get("subject_key") != subject_key(subject):
        raise InvalidOperatorVersionState("operator version evidence subject hash mismatch")
    run_service_hash = (manifest.get("run_service") or {}).get("plan_sha256")
    stamped_plan_hash = stamped.get("run_service_plan_sha256")
    if (
        not isinstance(stamped_plan_hash, str)
        or len(stamped_plan_hash) != 64
        or any(character not in "0123456789abcdef" for character in stamped_plan_hash)
        or stamped_plan_hash != run_service_hash
    ):
        raise InvalidOperatorVersionState("operator version evidence run-plan binding mismatch")
    run_service_file_hash = stamped.get("run_service_file_sha256")
    if (
        not isinstance(run_service_file_hash, str)
        or len(run_service_file_hash) != 64
        or any(character not in "0123456789abcdef" for character in run_service_file_hash)
    ):
        raise InvalidOperatorVersionState("operator version retained run-plan hash is invalid")
    stamp_schema = stamped.get("schema_version")
    if stamp_schema not in {"v1", "v2"}:
        raise InvalidOperatorVersionState("operator version evidence schema is missing")
    launch_plan_hash = stamped.get("launch_plan_sha256")
    inspection = stamped.get("runtime_inspection")
    if (
        not isinstance(launch_plan_hash, str)
        or len(launch_plan_hash) != 64
        or any(character not in "0123456789abcdef" for character in launch_plan_hash)
        or not isinstance(inspection, dict)
        or inspection.get("path") != "launch/preflight.json"
    ):
        raise InvalidOperatorVersionState("operator version evidence retained binding is invalid")
    inspection_hash = inspection.get("sha256")
    if (
        not isinstance(inspection_hash, str)
        or len(inspection_hash) != 64
        or any(character not in "0123456789abcdef" for character in inspection_hash)
    ):
        raise InvalidOperatorVersionState("operator version evidence runtime inspection is invalid")
    binding = {
        "subject_key": stamped["subject_key"],
        "run_service_plan_sha256": stamped_plan_hash,
        "run_service_file_sha256": run_service_file_hash,
        "launch_plan_sha256": launch_plan_hash,
        "runtime_inspection_sha256": inspection_hash,
    }
    if stamp_schema == "v2":
        topology_identity = stamped.get("topology_identity")
        if not isinstance(topology_identity, dict):
            raise InvalidOperatorVersionState("operator version topology binding is missing")
        for field in (
            "deployment_sha256",
            "expected_plan_sha256",
            "observed_plan_sha256",
            "expected_desired_sha256",
            "observed_desired_sha256",
        ):
            _exact_sha256(topology_identity.get(field), field=f"operator version {field}")
        if _exact_sha256(
            topology_identity.get("deployment_sha256"), field="operator version deployment identity"
        ) != _exact_sha256(
            subject.get("deployment_identity_sha256"), field="operator version subject deployment identity"
        ):
            raise InvalidOperatorVersionState("operator version topology binding disagrees with subject")
        binding["topology_identity"] = topology_identity
    expected_binding = "sha256:" + hashlib.sha256(_canonical_bytes(binding)).hexdigest()
    if stamped.get("binding_sha256") != expected_binding:
        raise InvalidOperatorVersionState("operator version evidence subject-to-plan binding mismatch")
    manifest_scenario = manifest.get("scenario")
    manifest_target = manifest.get("target")
    manifest_profile = manifest.get("profile")
    if (
        not isinstance(manifest_scenario, dict)
        or manifest_scenario.get("id") != subject.get("scenario_id")
        or _exact_sha256(
            manifest_scenario.get("sha256"), field="manifest scenario SHA-256"
        )
        != stamped.get("executed_scenario_sha256")
        or not isinstance(manifest_target, dict)
        or manifest_target.get("implementation") != subject.get("implementation")
        or manifest_target.get("version") != subject.get("version")
        or not isinstance(manifest_profile, dict)
        or manifest_profile.get("id") != subject.get("topology_profile_id")
    ):
        raise InvalidOperatorVersionState("operator version evidence disagrees with run manifest")
    probe = empty_state()
    probe["decisions"] = [{
        "decision_id": "validation-probe", "subject_key": subject_key(subject),
        "subject": copy.deepcopy(subject), "decision": "supported",
        "reason": "validation probe", "actor": "server",
        "decided_at": "1970-01-01T00:00:00Z",
        "evidence": {"run_id": "validation-probe", "manifest_sha256": "0" * 64,
                     "operational_outcome": "completed", "assertion_verdict": "unknown"},
    }]
    probe["revision"] = canonical_revision(probe)
    validate_state(probe)
    return copy.deepcopy(subject)


def _retained_bytes(
    root: Path, relative: str, *, label: str
) -> bytes:
    if not root.is_absolute():
        root = Path.cwd() / root
    components = Path(relative).parts
    if not components or any(part in {"", ".", ".."} for part in components):
        raise InvalidOperatorVersionState(f"{label} path is invalid")
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    opened_directories: list[int] = []
    descriptor: int | None = None
    try:
        try:
            current_descriptor = os.open(root, directory_flags)
        except OSError as exc:
            raise InvalidOperatorVersionState(f"{label} root is unsafe") from exc
        opened_directories.append(current_descriptor)
        root_opened = os.fstat(current_descriptor)
        if (
            not stat.S_ISDIR(root_opened.st_mode)
            or root_opened.st_uid not in {0, os.geteuid()}
        ):
            raise InvalidOperatorVersionState(f"{label} root is unsafe")
        for component in components[:-1]:
            try:
                current_descriptor = os.open(
                    component, directory_flags, dir_fd=current_descriptor
                )
            except OSError as exc:
                raise InvalidOperatorVersionState(f"{label} parent is unsafe") from exc
            opened_directories.append(current_descriptor)
            parent_opened = os.fstat(current_descriptor)
            if (
                not stat.S_ISDIR(parent_opened.st_mode)
                or parent_opened.st_uid not in {0, os.geteuid()}
            ):
                raise InvalidOperatorVersionState(f"{label} parent is unsafe")
        flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            descriptor = os.open(
                components[-1], flags, dir_fd=current_descriptor
            )
        except OSError as exc:
            raise InvalidOperatorVersionState(f"{label} is unavailable") from exc
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise InvalidOperatorVersionState(f"{label} is not a retained regular file")
        if opened.st_uid not in {0, os.geteuid()}:
            raise InvalidOperatorVersionState(f"{label} owner is unsafe")
        if opened.st_size > MAX_STATE_BYTES:
            raise InvalidOperatorVersionState(f"{label} is too large")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            payload = stream.read(MAX_STATE_BYTES + 1)
        if len(payload) > MAX_STATE_BYTES:
            raise InvalidOperatorVersionState(f"{label} is too large")
        return payload
    finally:
        if descriptor is not None:
            os.close(descriptor)
        for directory_descriptor in reversed(opened_directories):
            os.close(directory_descriptor)


def _retained_json(
    root: Path, relative: str, *, label: str
) -> tuple[dict[str, Any], bytes]:
    payload = _retained_bytes(root, relative, label=label)
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise InvalidOperatorVersionState(f"{label} is invalid") from exc
    if not isinstance(value, dict):
        raise InvalidOperatorVersionState(f"{label} must be an object")
    return value, payload


def _runtime_config_ids(
    preflight: dict[str, Any], *, resolved: dict[str, Any], versions: dict[str, Any], profile_id: str
) -> tuple[dict[str, str], dict[str, str]]:
    if preflight.get("state") != "ready":
        raise InvalidOperatorVersionState("run preflight was not ready")
    checks = preflight.get("checks")
    if not isinstance(checks, list) or not any(
        isinstance(item, dict)
        and item.get("id") == "deployed-version-identity"
        and item.get("passed") is True
        for item in checks
    ):
        raise InvalidOperatorVersionState("verified deployed-version identity is unavailable")
    health = preflight.get("health")
    health_evidence = health.get("health") if isinstance(health, dict) else None
    if (
        not isinstance(health_evidence, dict)
        or health_evidence.get("fresh") is not True
        or health_evidence.get("state") != "healthy"
    ):
        raise InvalidOperatorVersionState("run preflight health is not fresh and healthy")
    observations = (
        health_evidence.get("observations")
        if isinstance(health_evidence, dict)
        else None
    )
    health_snapshot = preflight.get("health")
    desired = health_snapshot.get("desired") if isinstance(health_snapshot, dict) else None
    expected_desired = versions.get("expected_desired_topology")
    if not isinstance(desired, dict) or not isinstance(expected_desired, dict):
        raise InvalidOperatorVersionState("complete frozen topology identity is unavailable")

    def verified_plan_hash(value: dict[str, Any], *, label: str) -> str:
        claimed = _exact_sha256(value.get("plan_sha256"), field=f"{label} topology plan")
        body = {key: item for key, item in value.items() if key != "plan_sha256"}
        actual = hashlib.sha256(_canonical_bytes(body)).hexdigest()
        if claimed != actual:
            raise InvalidOperatorVersionState(f"{label} topology plan is not canonical")
        return claimed

    observed_plan = verified_plan_hash(desired, label="observed")
    expected_plan = verified_plan_hash(expected_desired, label="expected")
    for label, value in (
        ("observed", desired.get("catalog_reuse_metadata")),
        ("expected", expected_desired.get("catalog_reuse_metadata")),
    ):
        if not isinstance(value, dict) or not isinstance(value.get("artifacts"), list):
            raise InvalidOperatorVersionState(
                f"{label} catalog reuse metadata is unavailable"
            )
    from profile_manager.profiles import deployment_topologies_reusable

    if (
        desired.get("profile_id") != profile_id
        or expected_desired.get("profile_id") != profile_id
        or expected_plan
        != _exact_sha256(versions.get("topology_plan_sha256"), field="launch topology plan")
        or _exact_sha256(
            expected_desired.get("deployment_sha256"),
            field="expected deployment identity",
        )
        != _exact_sha256(
            versions.get("deployment_identity_sha256"),
            field="launch deployment identity",
        )
        or _exact_sha256(desired.get("deployment_sha256"), field="observed deployment identity")
        != _exact_sha256(versions.get("deployment_identity_sha256"), field="launch deployment identity")
        or not deployment_topologies_reusable(desired, expected_desired)
    ):
        raise InvalidOperatorVersionState("runtime desired topology disagrees with launch identity")
    topology_identity = {
        "deployment_sha256": _exact_sha256(
            versions.get("deployment_identity_sha256"), field="deployment identity"
        ),
        "expected_plan_sha256": expected_plan,
        "observed_plan_sha256": observed_plan,
        "expected_desired_sha256": "sha256:" + hashlib.sha256(
            _canonical_bytes(expected_desired)
        ).hexdigest(),
        "observed_desired_sha256": "sha256:" + hashlib.sha256(
            _canonical_bytes(desired)
        ).hexdigest(),
    }
    desired_nodes = desired.get("nodes") if isinstance(desired, dict) else None
    if not isinstance(desired_nodes, list) or not desired_nodes:
        raise InvalidOperatorVersionState("retained desired node mapping is unavailable")
    expected_nodes: dict[str, tuple[str, str]] = {}
    for node in desired_nodes:
        if not isinstance(node, dict):
            raise InvalidOperatorVersionState("retained desired node mapping is malformed")
        node_id = str(node.get("id") or "")
        implementation = str(node.get("implementation") or node.get("impl") or "")
        version = str(node.get("version") or "")
        if not node_id or not implementation or not version or node_id in expected_nodes:
            raise InvalidOperatorVersionState("retained desired node mapping is malformed")
        expected_nodes[node_id] = (implementation, version)
    identities: dict[str, set[str]] = {str(key): set() for key in resolved}
    saw_observation = False
    for observation in observations if isinstance(observations, list) else []:
        if not isinstance(observation, dict):
            raise InvalidOperatorVersionState("runtime node observation is malformed")
        controller = observation.get("controller_health")
        if not isinstance(controller, dict):
            topology = observation.get("topology_result")
            controller = (
                topology.get("controller_health")
                if isinstance(topology, dict)
                else None
            )
        nodes = controller.get("nodes") if isinstance(controller, dict) else None
        if not isinstance(nodes, list):
            raise InvalidOperatorVersionState("runtime node observation is malformed")
        observed_by_id: dict[str, dict[str, Any]] = {}
        for node in nodes:
            if not isinstance(node, dict):
                raise InvalidOperatorVersionState("runtime node observation is malformed")
            node_id = str(node.get("id") or "")
            if not node_id or node_id in observed_by_id:
                raise InvalidOperatorVersionState("runtime node observation has duplicate identity")
            observed_by_id[node_id] = node
        if set(observed_by_id) != set(expected_nodes):
            raise InvalidOperatorVersionState("runtime node observation is incomplete")
        saw_observation = True
        for node_id, node in observed_by_id.items():
            implementation = str(node.get("implementation") or "")
            expected_implementation, expected_version = expected_nodes[node_id]
            if (
                node.get("healthy") is not True
                or implementation != expected_implementation
                or str(node.get("version") or "") != expected_version
            ):
                raise InvalidOperatorVersionState("runtime node observation disagrees with desired identity")
            identity = str(node.get("target_image_id") or "")
            if implementation in identities:
                identities[implementation].add(
                    "sha256:" + _exact_sha256(identity, field="runtime config image ID")
                )
    if not saw_observation:
        raise InvalidOperatorVersionState("verified runtime target identity is unavailable")
    selected: dict[str, str] = {}
    for implementation, expected in resolved.items():
        observed = identities.get(str(implementation), set())
        if len(observed) != 1:
            raise InvalidOperatorVersionState("verified runtime target identity is unavailable")
        selected[str(implementation)] = next(iter(observed))
        planned = expected.get("image_config_id") if isinstance(expected, dict) else None
        if planned is not None and (
            "sha256:" + _exact_sha256(planned, field="planned config image ID")
            != selected[str(implementation)]
        ):
            raise InvalidOperatorVersionState("planned and observed config image IDs disagree")
    return selected, topology_identity


def build_operator_version_evidence(
    run_dir: str | os.PathLike[str], *, run_service_plan_sha256: str
) -> dict[str, Any]:
    """Bind a new run to immutable launch inputs and fresh runtime inspection."""
    root = Path(run_dir)
    service_plan, service_bytes = _retained_json(
        root, "inputs/run-service-plan.json", label="retained run-service plan"
    )
    observed_service_hash = hashlib.sha256(_canonical_bytes(service_plan)).hexdigest()
    if observed_service_hash != run_service_plan_sha256:
        raise InvalidOperatorVersionState("retained run-service plan hash mismatch")
    launch_plan, launch_bytes = _retained_json(
        root, "launch/plan.json", label="retained launch plan"
    )
    preflight, preflight_bytes = _retained_json(
        root, "launch/preflight.json", label="retained runtime inspection"
    )
    try:
        scenario = launch_plan["scenario"]
        profile = launch_plan["profile"]
        versions = launch_plan["versions"]
        if not all(isinstance(value, dict) for value in (scenario, profile, versions)):
            raise TypeError
        implementation = str(scenario["target"]["implementation"])
        version = str(scenario["target"]["version"])
        resolved = versions["resolved"][implementation]
        service_scenario = service_plan["resolved"]["scenario"]
        if not all(isinstance(value, dict) for value in (resolved, service_scenario)):
            raise TypeError
    except (KeyError, TypeError) as exc:
        raise InvalidOperatorVersionState("retained launch identity is incomplete") from exc
    scenario_sha = _exact_sha256(scenario.get("digest"), field="scenario SHA-256")
    artifacts = launch_plan.get("artifacts")
    if not isinstance(artifacts, dict):
        raise InvalidOperatorVersionState("retained launch artifacts are malformed")
    executed_scenario_sha = _exact_sha256(
        artifacts.get("scenario.yaml"),
        field="materialized scenario SHA-256",
    )
    service_target = service_scenario.get("target")
    if (
        service_scenario.get("id") != scenario.get("id")
        or _exact_sha256(service_scenario.get("digest"), field="run-service scenario SHA-256")
        != executed_scenario_sha
        or service_scenario.get("profile_id") != profile.get("id")
        or not isinstance(service_target, dict)
        or service_target.get("implementation") != implementation
        or service_target.get("version") != version
        or preflight.get("profile_id") != profile.get("id")
        or resolved.get("version") != version
    ):
        raise InvalidOperatorVersionState("retained run identity disagrees")
    actual_scenario_sha = hashlib.sha256(
        _retained_bytes(root, "launch/scenario.yaml", label="retained scenario")
    ).hexdigest()
    if actual_scenario_sha != executed_scenario_sha:
        raise InvalidOperatorVersionState("retained scenario hash mismatch")

    all_resolved = versions.get("resolved")
    if not isinstance(all_resolved, dict) or not all_resolved:
        raise InvalidOperatorVersionState("retained release identity is incomplete")
    runtime_config_ids, topology_identity = _runtime_config_ids(
        preflight,
        resolved=all_resolved,
        versions=versions,
        profile_id=str(profile["id"]),
    )
    config_image_id = runtime_config_ids[implementation]
    image_reference = resolved.get("image_reference")
    if not isinstance(image_reference, str) or not image_reference:
        raise InvalidOperatorVersionState("retained launch has no image reference")
    manifest_value = resolved.get("image_digest")
    manifest_digest = (
        "sha256:" + _exact_sha256(manifest_value, field="manifest digest")
        if manifest_value is not None
        else None
    )
    subject = {
        "implementation": implementation,
        "version": version,
        "image_reference": image_reference,
        "manifest_digest": manifest_digest,
        "config_image_id": config_image_id,
        "scenario_id": str(scenario["id"]),
        "scenario_sha256": scenario_sha,
        "scope": str(versions["scope"]),
        "topology_profile_id": str(profile["id"]),
        "deployment_identity_sha256": _exact_sha256(
            versions["deployment_identity_sha256"], field="deployment identity"
        ),
        "mixed_pair": copy.deepcopy(versions.get("pair")),
    }
    subject_digest = subject_key(subject)
    launch_hash = hashlib.sha256(launch_bytes).hexdigest()
    inspection_hash = hashlib.sha256(preflight_bytes).hexdigest()
    binding = {
        "subject_key": subject_digest,
        "run_service_plan_sha256": run_service_plan_sha256,
        "run_service_file_sha256": hashlib.sha256(service_bytes).hexdigest(),
        "launch_plan_sha256": launch_hash,
        "runtime_inspection_sha256": inspection_hash,
        "topology_identity": topology_identity,
    }
    stamp = {
        "schema_version": "v2",
        "subject": subject,
        "subject_key": subject_digest,
        "run_service_plan_sha256": run_service_plan_sha256,
        "run_service_file_sha256": hashlib.sha256(service_bytes).hexdigest(),
        "launch_plan_sha256": launch_hash,
        "runtime_inspection": {
            "path": "launch/preflight.json",
            "sha256": inspection_hash,
            "source": "fresh-run-preflight",
        },
        "topology_identity": topology_identity,
        "executed_scenario_sha256": executed_scenario_sha,
        "binding_sha256": "sha256:" + hashlib.sha256(_canonical_bytes(binding)).hexdigest(),
    }
    # Reuse the durable ledger schema as the authoritative subject validator.
    probe = empty_state()
    probe["decisions"] = [{
        "decision_id": "validation-probe", "subject_key": subject_digest,
        "subject": copy.deepcopy(subject), "decision": "supported",
        "reason": "validation probe", "actor": "server",
        "decided_at": "1970-01-01T00:00:00Z",
        "evidence": {"run_id": "validation-probe", "manifest_sha256": "0" * 64,
                     "operational_outcome": "completed", "assertion_verdict": "unknown"},
    }]
    probe["revision"] = canonical_revision(probe)
    validate_state(probe)
    return stamp


def build_operator_version_execution_target(
    run_dir: str | os.PathLike[str], *, run_service_plan_sha256: str
) -> dict[str, Any]:
    """Resolve a mutation target only from the frozen, verified launch contract."""
    root = Path(run_dir)
    stamp = build_operator_version_evidence(
        root, run_service_plan_sha256=run_service_plan_sha256
    )
    launch_plan, launch_bytes = _retained_json(
        root, "launch/plan.json", label="retained launch plan"
    )
    preflight, preflight_bytes = _retained_json(
        root, "launch/preflight.json", label="retained runtime inspection"
    )
    if (
        hashlib.sha256(launch_bytes).hexdigest() != stamp["launch_plan_sha256"]
        or hashlib.sha256(preflight_bytes).hexdigest()
        != stamp["runtime_inspection"]["sha256"]
    ):
        raise InvalidOperatorVersionState(
            "retained launch evidence changed during verification"
        )
    measurements = launch_plan.get("measurements")
    from profile_manager.run_plan import is_explicit_no_measurements_context

    if not is_explicit_no_measurements_context(measurements):
        raise InvalidOperatorVersionState(
            "frozen launch does not explicitly select no measurements"
        )
    versions = launch_plan.get("versions") or {}
    expected_desired = versions.get("expected_desired_topology") or {}
    subject = stamp["subject"]
    implementation = str(subject["implementation"])
    resolved = (versions.get("resolved") or {}).get(implementation) or {}
    registration = resolved.get("operator_registration")
    registration_id = str(expected_desired.get("operator_registration_id") or "")
    overlay_revision = str(expected_desired.get("operator_overlay_revision") or "")
    if (
        implementation != "amaru"
        or not isinstance(registration, dict)
        or registration.get("registration_id") != registration_id
        or not registration_id
        or not overlay_revision.startswith("sha256:")
        or registration.get("config_image_id") != subject.get("config_image_id")
        or registration.get("version") != subject.get("version")
    ):
        raise InvalidOperatorVersionState(
            "frozen operator registration identity is unavailable"
        )
    runtime_interface = str(registration.get("runtime_interface") or "")
    if runtime_interface != "extracted-binary":
        raise InvalidOperatorVersionState(
            "frozen operator runtime interface is unsupported or unverified"
        )
    desired_nodes = expected_desired.get("nodes")
    candidates = [
        item
        for item in desired_nodes if isinstance(item, dict)
        and str(item.get("implementation") or item.get("impl") or "") == implementation
        and item.get("supporting") is not True
    ] if isinstance(desired_nodes, list) else []
    if len(candidates) != 1:
        raise InvalidOperatorVersionState("frozen execution node identity is unavailable")
    desired_node = candidates[0]
    node_id = str(desired_node.get("id") or "")
    if (
        not node_id
        or desired_node.get("version") != subject.get("version")
        or desired_node.get("image") != subject.get("config_image_id")
    ):
        raise InvalidOperatorVersionState("frozen execution node identity disagrees")

    health = preflight.get("health")
    evidence = health.get("health") if isinstance(health, dict) else None
    observations = evidence.get("observations") if isinstance(evidence, dict) else None
    bindings: list[dict[str, Any]] = []
    for observation in observations if isinstance(observations, list) else []:
        controller = observation.get("controller_health") if isinstance(observation, dict) else None
        if not isinstance(controller, dict) and isinstance(observation, dict):
            topology = observation.get("topology_result")
            controller = topology.get("controller_health") if isinstance(topology, dict) else None
        nodes = controller.get("nodes") if isinstance(controller, dict) else None
        match = next(
            (item for item in nodes if isinstance(item, dict) and item.get("id") == node_id),
            None,
        ) if isinstance(nodes, list) else None
        if not isinstance(match, dict):
            raise InvalidOperatorVersionState("frozen execution node observation is unavailable")
        binding = {
            "node_id": node_id,
            "service": str(match.get("service") or ""),
            "container": str(match.get("container_name") or ""),
            "config_image_id": str(match.get("target_image_id") or ""),
            "artifact_image_id": str(match.get("artifact_image_id") or ""),
            "wrapper_image_id": str(match.get("runtime_image_id") or ""),
            "runtime_container_image_id": str(
                match.get("runtime_container_image_id") or ""
            ),
            "wrapper_matched": match.get("wrapper_matched") is True,
        }
        if (
            binding["service"] != "amaru-relay-1"
            or not binding["container"]
            or binding["config_image_id"] != subject.get("config_image_id")
            or binding["artifact_image_id"] != subject.get("config_image_id")
            or "sha256:" + _exact_sha256(
                binding["wrapper_image_id"], field="wrapper config image ID"
            ) != binding["wrapper_image_id"]
            or "sha256:" + _exact_sha256(
                binding["runtime_container_image_id"],
                field="runtime container image ID",
            ) != binding["runtime_container_image_id"]
            or binding["runtime_container_image_id"] != binding["wrapper_image_id"]
            or binding["wrapper_matched"] is not True
        ):
            raise InvalidOperatorVersionState(
                "frozen execution service identity is not verified"
            )
        bindings.append(binding)
    if not bindings or any(item != bindings[0] for item in bindings[1:]):
        raise InvalidOperatorVersionState(
            "frozen execution service observations disagree"
        )
    return {
        "subject": copy.deepcopy(subject),
        "registration_id": registration_id,
        "operator_overlay_revision": overlay_revision,
        "runtime_interface": runtime_interface,
        "service": bindings[0],
    }


def verified_operator_version_subject(
    manifest: dict[str, Any], *, run_dir: str | os.PathLike[str]
) -> dict[str, Any]:
    """Verify the stamp and the exact retained files it names."""
    subject = decision_subject_from_evidence(manifest)
    stamp = manifest["operator_version_evidence"]
    root = Path(run_dir)
    for relative, expected in (
        ("inputs/run-service-plan.json", stamp["run_service_file_sha256"]),
        ("launch/plan.json", stamp["launch_plan_sha256"]),
        ("launch/preflight.json", stamp["runtime_inspection"]["sha256"]),
    ):
        _value, payload = _retained_json(root, relative, label=f"retained {relative}")
        if hashlib.sha256(payload).hexdigest() != expected:
            raise InvalidOperatorVersionState(f"retained {relative} hash mismatch")
    scenario_payload = _retained_bytes(
        root, "launch/scenario.yaml", label="retained launch/scenario.yaml"
    )
    if hashlib.sha256(scenario_payload).hexdigest() != stamp["executed_scenario_sha256"]:
        raise InvalidOperatorVersionState("retained launch/scenario.yaml hash mismatch")
    return subject


def preferred_decision(document: dict[str, Any], scope_key: str) -> dict[str, Any] | None:
    preference = active_preferences(document).get(scope_key)
    if preference is None:
        return None
    decision = next(
        (item for item in document["decisions"] if item["decision_id"] == preference["decision_id"]),
        None,
    )
    if decision is None or decision["decision"] != "supported":
        return None
    return {"preference": copy.deepcopy(preference), "decision": copy.deepcopy(decision)}


def empty_state() -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema_version": "v1",
        "revision": "",
        "registrations": [],
        "decisions": [],
        "preferred_defaults": [],
    }
    document["revision"] = canonical_revision(document)
    return document


def _validation_error(document: Any):
    return next(_VALIDATOR.iter_errors(document), None)


def validate_state(document: Any) -> dict[str, Any]:
    error = _validation_error(document)
    if error is not None:
        location = ".".join(str(part) for part in error.absolute_path)
        detail = f" {location}" if location else ""
        raise InvalidOperatorVersionState(
            f"invalid operator version state{detail}: {error.message}"
        )
    assert isinstance(document, dict)
    expected = canonical_revision(document)
    if document["revision"] != expected:
        raise InvalidOperatorVersionState("invalid operator version state revision")

    seen: dict[str, set[str]] = {
        "registration_id": set(),
        "decision_id": set(),
        "preference_id": set(),
    }
    for collection, identity in (
        ("registrations", "registration_id"),
        ("decisions", "decision_id"),
        ("preferred_defaults", "preference_id"),
    ):
        for entry in document[collection]:
            if entry[identity] in seen[identity]:
                raise InvalidOperatorVersionState(f"duplicate {identity}")
            seen[identity].add(entry[identity])
    for event in document["decisions"]:
        if event["subject_key"] != subject_key(event["subject"]):
            raise InvalidOperatorVersionState("decision subject_key mismatch")
    decisions_by_id = {
        event["decision_id"]: event for event in document["decisions"]
    }
    for event in document["preferred_defaults"]:
        if event["action"] != "set":
            continue
        referenced = decisions_by_id.get(event["decision_id"])
        if referenced is None or referenced["decision"] != "supported":
            raise InvalidOperatorVersionState(
                "preference requires a supported decision reference"
            )
        if event["scope_key"] != preference_scope_key(referenced["subject"]):
            raise InvalidOperatorVersionState("preference scope mismatch")
    return document


def active_decisions(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    active: dict[str, dict[str, Any]] = {}
    for event in document["decisions"]:
        active[event["subject_key"]] = event
    return active


def active_preferences(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    current_supported = {
        event["decision_id"]: event
        for event in active_decisions(document).values()
        if event["decision"] == "supported"
    }
    active: dict[str, dict[str, Any]] = {}
    for event in document["preferred_defaults"]:
        if event["action"] == "clear":
            active.pop(event["scope_key"], None)
        elif (
            event["decision_id"] in current_supported
            and event["scope_key"]
            == preference_scope_key(current_supported[event["decision_id"]]["subject"])
        ):
            active[event["scope_key"]] = event
        else:
            active.pop(event["scope_key"], None)
    return active


class OperatorVersionDecisionLedger:
    """One fail-closed, process-safe local operator overlay."""

    def __init__(self, state_dir: str | os.PathLike[str] | None = None):
        if state_dir is None:
            configured = os.environ.get(
                "ADA2_DWARF_VERSION_CATALOG_STATE_DIR", ""
            ).strip()
            state = Path(configured) if configured else storage_roots().state
        else:
            state = Path(state_dir)
        self.state_dir = state
        self.root = state / "version-catalog"
        self.path = self.root / "operator-decisions-v1.json"
        self.lock_path = self.root / "operator-decisions-v1.lock"

    @staticmethod
    def _trusted_owner(opened: os.stat_result) -> None:
        if opened.st_uid not in {0, os.geteuid()}:
            raise UnsafeOperatorVersionState("unsafe operator state owner")

    def _ensure_root(self) -> None:
        # Refuse symlinked configured roots rather than resolving into an
        # operator-controlled or unexpected location.
        for candidate in (self.state_dir, self.root):
            try:
                opened = candidate.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(opened.st_mode):
                raise UnsafeOperatorVersionState("unsafe operator state symlink")
            if not stat.S_ISDIR(opened.st_mode):
                raise UnsafeOperatorVersionState("operator state root is not a directory")
            self._trusted_owner(opened)
        try:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError as exc:
            raise UnsafeOperatorVersionState("cannot create operator state root") from exc
        opened = self.root.lstat()
        if stat.S_ISLNK(opened.st_mode) or not stat.S_ISDIR(opened.st_mode):
            raise UnsafeOperatorVersionState("unsafe operator state root")
        self._trusted_owner(opened)

    def _validate_existing_roots(self) -> None:
        for candidate in (self.state_dir, self.root):
            try:
                opened = candidate.lstat()
            except FileNotFoundError:
                return
            if stat.S_ISLNK(opened.st_mode):
                raise UnsafeOperatorVersionState("unsafe operator state symlink")
            if not stat.S_ISDIR(opened.st_mode):
                raise UnsafeOperatorVersionState("operator state root is not a directory")
            self._trusted_owner(opened)

    def _read_existing(self) -> dict[str, Any]:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            descriptor = os.open(self.path, flags)
        except FileNotFoundError:
            return empty_state()
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.EFTYPE if hasattr(errno, "EFTYPE") else -1}:
                raise UnsafeOperatorVersionState("unsafe operator state symlink") from exc
            raise UnsafeOperatorVersionState("cannot safely open operator state") from exc
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise UnsafeOperatorVersionState("operator state is not a regular file")
            self._trusted_owner(opened)
            if opened.st_size > MAX_STATE_BYTES:
                raise UnsafeOperatorVersionState("operator state is too large")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                payload = stream.read(MAX_STATE_BYTES + 1)
            if len(payload) > MAX_STATE_BYTES:
                raise UnsafeOperatorVersionState("operator state is too large")
        finally:
            os.close(descriptor)
        try:
            document = json.loads(payload.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise InvalidOperatorVersionState("invalid operator version state JSON") from exc
        return validate_state(document)

    def _fsync_root(self) -> None:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(self.root, flags)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISDIR(opened.st_mode):
                raise UnsafeOperatorVersionState("operator state root is not a directory")
            self._trusted_owner(opened)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def read(self) -> dict[str, Any]:
        self._validate_existing_roots()
        if not self.root.exists():
            return empty_state()
        return copy.deepcopy(self._read_existing())

    @contextmanager
    def _lock(self):
        self._ensure_root()
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        try:
            root_descriptor = os.open(
                self.root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow
            )
        except OSError as exc:
            raise UnsafeOperatorVersionState("cannot safely lock operator state") from exc
        descriptor = None
        try:
            try:
                descriptor = os.open(
                    self.lock_path.name,
                    os.O_RDWR | os.O_CREAT | nofollow,
                    0o600,
                    dir_fd=root_descriptor,
                )
            except OSError as exc:
                raise UnsafeOperatorVersionState(
                    "cannot safely lock operator state"
                ) from exc
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise UnsafeOperatorVersionState("operator lock is not a regular file")
            self._trusted_owner(opened)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            if descriptor is not None:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)
            os.close(root_descriptor)

    def _mutate(
        self,
        expected_revision: str,
        mutator: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any]:
        with self._lock():
            current = self._read_existing()
            if expected_revision != current["revision"]:
                raise StaleOperatorVersionState("operator version state revision is stale")
            updated = copy.deepcopy(current)
            mutator(updated)
            updated["revision"] = canonical_revision(updated)
            validate_state(updated)
            try:
                atomic_write_bytes(self.path, _canonical_bytes(updated) + b"\n")
            except OSError:
                # Replacement can succeed before the directory fsync reports an
                # error.  A matching readback proves visibility, not durability;
                # retry only the parent-directory flush while holding the lock.
                observed = self._read_existing()
                if observed["revision"] != updated["revision"]:
                    raise
                try:
                    self._fsync_root()
                except OSError as exc:
                    raise IndeterminateOperatorVersionCommit(
                        observed["revision"]
                    ) from exc
            return copy.deepcopy(updated)

    def append_registration(
        self, registration: dict[str, Any], *, expected_revision: str
    ) -> dict[str, Any]:
        def append(document: dict[str, Any]) -> None:
            if any(
                item["registration_id"] == registration.get("registration_id")
                for item in document["registrations"]
            ):
                raise ValueError("registration_id already exists")
            document["registrations"].append(copy.deepcopy(registration))

        return self._mutate(expected_revision, append)

    def append_decision(
        self, event: dict[str, Any], *, expected_revision: str
    ) -> dict[str, Any]:
        def append(document: dict[str, Any]) -> None:
            if any(
                item["decision_id"] == event.get("decision_id")
                for item in document["decisions"]
            ):
                raise ValueError("decision_id already exists")
            if event.get("subject_key") != subject_key(event.get("subject", {})):
                raise ValueError("decision subject_key mismatch")
            document["decisions"].append(copy.deepcopy(event))

        return self._mutate(expected_revision, append)

    def append_decision_and_optional_preference(
        self,
        event: dict[str, Any],
        *,
        expected_revision: str,
        prefer: bool,
        preference_event: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Atomically retain one decision and its optional default preference."""
        def append(document: dict[str, Any]) -> None:
            if event.get("subject_key") != subject_key(event.get("subject", {})):
                raise ValueError("decision subject_key mismatch")
            if any(
                item["decision_id"] == event.get("decision_id")
                for item in document["decisions"]
            ):
                raise ValueError("decision_id already exists")
            document["decisions"].append(copy.deepcopy(event))
            if not prefer:
                return
            if event.get("decision") != "supported" or not isinstance(preference_event, dict):
                raise ValueError("only a supported decision can become preferred")
            if preference_event.get("decision_id") != event.get("decision_id"):
                raise ValueError("preference decision does not match appended decision")
            if preference_event.get("scope_key") != preference_scope_key(event["subject"]):
                raise ValueError("preference scope does not match decision subject")
            if any(
                item["preference_id"] == preference_event.get("preference_id")
                for item in document["preferred_defaults"]
            ):
                raise ValueError("preference_id already exists")
            document["preferred_defaults"].append(copy.deepcopy(preference_event))

        return self._mutate(expected_revision, append)

    def append_preference(
        self, event: dict[str, Any], *, expected_revision: str
    ) -> dict[str, Any]:
        def append(document: dict[str, Any]) -> None:
            if any(
                item["preference_id"] == event.get("preference_id")
                for item in document["preferred_defaults"]
            ):
                raise ValueError("preference_id already exists")
            if event.get("action") == "set":
                decision_id = event.get("decision_id")
                active_supported = {
                    item["decision_id"]: item
                    for item in active_decisions(document).values()
                    if item["decision"] == "supported"
                }
                if decision_id not in active_supported:
                    raise ValueError("preference requires an active supported decision")
                if event.get("scope_key") != preference_scope_key(
                    active_supported[decision_id]["subject"]
                ):
                    raise ValueError("preference scope does not match decision subject")
            document["preferred_defaults"].append(copy.deepcopy(event))

        return self._mutate(expected_revision, append)
