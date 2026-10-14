"""Single-flight read-only topology health checks for the operator UI."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from profile_manager.config import storage_roots
from profile_manager.topology_snapshot import (
    build_topology_snapshot,
    refresh_snapshot_freshness,
)


_LOCK = threading.Lock()
_THREAD: threading.Thread | None = None
_LAST_RESULT: dict[str, Any] | None = None
_LAST_RESULTS: dict[str, dict[str, Any]] = {}
_STARTED_AT: str | None = None
_INFLIGHT_PROFILE_ID: str | None = None
_INFLIGHT_DESIRED_PLAN_SHA256: str | None = None


def _validated_desired_plan_sha256(value: str | None) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None
    ):
        raise ValueError("desired topology plan identity is invalid")
    return value


def _profile_key(profile_id: str | None) -> str:
    return profile_id if profile_id is not None else "__active__"


def _completed_for(profile_id: str | None) -> dict[str, Any] | None:
    """Return only evidence produced for this request scope."""
    if profile_id is None:
        memory = _LAST_RESULT
    else:
        memory = _LAST_RESULTS.get(_profile_key(profile_id))
    if memory is not None:
        return _public_copy(memory)
    persisted = _read_persisted_result(profile_id)
    if persisted is None:
        return None
    if profile_id is not None and persisted.get("profile_id") != profile_id:
        return None
    return persisted


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _public_copy(value: dict[str, Any] | None) -> dict[str, Any] | None:
    return json.loads(json.dumps(value)) if value is not None else None


def _result_path(profile_id: str | None = None) -> Path:
    root = storage_roots().state / "topology-health"
    if profile_id is None:
        return root / "dashboard-latest.json"
    digest = hashlib.sha256(profile_id.encode("utf-8")).hexdigest()[:24]
    return root / "profiles" / f"{digest}.json"


def _write_persisted_result(
    value: dict[str, Any], *, profile_id: str | None = None
) -> bool:
    path = _result_path(profile_id)
    temporary = path.with_suffix(".json.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        return False
    return True


def _read_persisted_result(
    profile_id: str | None = None,
) -> dict[str, Any] | None:
    try:
        value = json.loads(_result_path(profile_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or not value.get("state"):
        return None
    return value


def _age_seconds(value: dict[str, Any]) -> int | None:
    stamp = value.get("checked_at") or value.get("completed_at")
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        observed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - observed).total_seconds()))


def _present_completed(
    value: dict[str, Any], *, observation_source: str
) -> dict[str, Any]:
    presented = _public_copy(value) or {}
    presented.setdefault("schema_version", "v1")
    presented = refresh_snapshot_freshness(presented, now=_utc_now())
    presented.update(
        {
            "observation_source": observation_source,
            "cached": observation_source == "persisted",
            "age_seconds": _age_seconds(value),
            "previous": None,
            "previous_is_current": True,
        }
    )
    return presented


def _integer(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def classify_current_profile_health(
    first: dict[str, Any], second: dict[str, Any] | None
) -> dict[str, Any]:
    """Classify two exact-profile observations without masking failures."""
    base = {
        "checked_at": _utc_now(), "topology_kind": "managed_profile",
        "profile_id": first.get("profile_id"), "redeploy_supported": False,
        "evidence_scope": "current_active_profile",
    }
    topology_result = first.get("topology_result")
    if second is None and isinstance(topology_result, dict) and topology_result.get("state"):
        return {
            **base,
            **topology_result,
            "profile_id": first.get("profile_id"),
            "redeploy_supported": False,
            "evidence_scope": "current_active_profile",
            "observations": [first],
        }
    if not first.get("enabled"):
        state = first.get("state") or "unknown"
        reason = "no_active_profile" if state == "no_active" else "active_profile_probe_failed"
        return {
            **base, "state": state, "reason_code": reason,
            "detail": first.get("error") or "The active profile could not be observed.",
            "observations": [first],
        }
    if second is None or not second.get("enabled"):
        return {
            **base, "state": "unknown", "reason_code": "active_profile_second_sample_failed",
            "detail": (second or {}).get("error") or "The second progress sample is unavailable.",
            "observations": [first] + ([second] if second else []),
        }
    if second.get("profile_id") != first.get("profile_id"):
        return {
            **base, "state": "unknown", "reason_code": "active_profile_changed",
            "detail": "The active profile changed between health samples.", "observations": [first, second],
        }
    latest_topology_result = second.get("topology_result")
    if isinstance(latest_topology_result, dict) and latest_topology_result.get("state"):
        return {
            **base,
            **latest_topology_result,
            "profile_id": first.get("profile_id"),
            "redeploy_supported": False,
            "evidence_scope": "current_active_profile",
            "observations": [first, second],
        }
    first_health = first.get("health") or {}
    second_health = second.get("health") or {}
    first_parsed = first_health.get("parsed") or {}
    parsed = second_health.get("parsed") or {}
    expected = _integer(second.get("expected_nodes"))
    processes = _integer(parsed.get("cardano_node_processes"))
    sockets = _integer(parsed.get("socket_count"))
    listeners = _integer(parsed.get("listener_count"))
    first_slot = _integer(first_parsed.get("tip_slot"))
    second_slot = _integer(parsed.get("tip_slot"))
    sync_value = parsed.get("sync_progress")
    try:
        sync = (
            float(sync_value)
            if isinstance(sync_value, (str, int, float))
            and not isinstance(sync_value, bool)
            else None
        )
    except ValueError:
        sync = None
    observation = {
        "expected_nodes": expected, "node_processes": processes,
        "socket_count": sockets, "listener_count": listeners,
        "loopback_only": parsed.get("loopback_only"),
        "first_tip_slot": first_slot, "tip_slot": second_slot,
        "tip_block": parsed.get("tip_block"), "sync_progress": parsed.get("sync_progress"),
    }
    common = {**base, "observation": observation, "observations": [first, second]}
    if first_health.get("returncode") != 0 or second_health.get("returncode") != 0:
        return {**common, "state": "unknown", "reason_code": "active_profile_probe_failed",
                "detail": second_health.get("stderr") or "The profile health command failed."}
    if expected is None or processes != expected or sockets != expected or listeners != expected:
        return {**common, "state": "unhealthy", "reason_code": "active_profile_readiness_failed",
                "detail": "Process, socket, or listener counts do not match the active profile."}
    if parsed.get("loopback_only") != "true":
        return {**common, "state": "unhealthy", "reason_code": "active_profile_exposure_failed",
                "detail": "The active profile is not restricted to loopback listeners."}
    if first_slot is None or second_slot is None or second_slot <= first_slot:
        return {**common, "state": "unhealthy", "reason_code": "active_profile_not_progressing",
                "detail": "The active profile tip did not advance between bounded samples."}
    if sync is None or sync < 99.0:
        return {**common, "state": "catching_up", "reason_code": "active_profile_catching_up",
                "detail": "The active profile is progressing but is not yet synchronized."}
    return {**common, "state": "healthy", "reason_code": "active_profile_ready_and_progressing",
            "detail": "The exact active profile passed readiness and bounded progress checks."}


def _authoritative_controller_result(
    first: dict[str, Any], desired: dict[str, Any], *, now: str
) -> bool:
    """Accept only the controller's complete, fresh, identity-bound health result."""
    from profile_manager.profiles import deployment_topologies_reusable

    profile_id = str(desired.get("profile_id") or "")
    result = first.get("topology_result")
    controller = result.get("controller_health") if isinstance(result, dict) else None
    semantic = controller.get("semantic_health") if isinstance(controller, dict) else None
    if (
        first.get("enabled") is not True
        or first.get("transport") != "controller"
        or first.get("profile_id") != profile_id
        or not isinstance(result, dict)
        or result.get("state") != "healthy"
        or not isinstance(controller, dict)
        or controller.get("available") is not True
        or controller.get("schema_version") != "v1"
        or controller.get("topology_id") != profile_id
        or controller.get("state") != "healthy"
        or controller.get("converged") is not True
        or not isinstance(semantic, dict)
        or type(semantic.get("schema_version")) is not int
        or semantic.get("schema_version") != 1
        or semantic.get("profile_id") != profile_id
        or semantic.get("completed_at") != result.get("checked_at")
        or semantic.get("fresh_observation") is not True
        or semantic.get("retained") is not True
        or semantic.get("passed") is not True
        or semantic.get("classification") != "retained-runtime-ready"
        or semantic.get("identity_matched") is not True
        or semantic.get("sample_count") != 2
    ):
        return False
    try:
        checked = datetime.fromisoformat(
            str(result["checked_at"]).replace("Z", "+00:00")
        )
        generated = datetime.fromisoformat(now.replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        return False
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=timezone.utc)
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    age_seconds = (generated - checked).total_seconds()
    if age_seconds < 0 or age_seconds > 30:
        return False
    required_gates = semantic.get("required_gates")
    gates = semantic.get("gates")
    if (
        not isinstance(required_gates, list)
        or not required_gates
        or any(not isinstance(name, str) or not name for name in required_gates)
        or len(required_gates) != len(set(required_gates))
        or not isinstance(gates, dict)
        or set(gates) != set(required_gates)
        or any(gates.get(name) is not True for name in required_gates)
        or semantic.get("failed_gates") != []
    ):
        return False
    retained_evidence = controller.get("retained_evidence")
    if (
        not isinstance(retained_evidence, dict)
        or retained_evidence.get("profile_id") != profile_id
        or any(
            not isinstance(retained_evidence.get(field), str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", retained_evidence[field]) is None
            for field in (
                "deployment_config_sha256",
                "compose_sha256",
                "semantic_report_sha256",
            )
        )
    ):
        return False
    persisted = first.get("persisted_deployment")
    if persisted != controller.get("persisted_deployment"):
        return False
    observed_nodes = first.get("observed_nodes")
    controller_nodes = controller.get("nodes")
    expected_nodes = controller.get("expected_nodes")
    if (
        isinstance(expected_nodes, bool)
        or not isinstance(expected_nodes, int)
        or expected_nodes < 1
        or not isinstance(observed_nodes, list)
        or len(observed_nodes) != expected_nodes
        or not isinstance(controller_nodes, list)
        or len(controller_nodes) != expected_nodes
        or any(
            not isinstance(node, dict)
            or node.get("container_state") != "running"
            or node.get("listener_ready") is not True
            or node.get("chain_ready") is not True
            or node.get("synchronized") is not True
            or node.get("healthy") is not True
            for node in controller_nodes
        )
    ):
        return False
    retained_desired = _verified_retained_desired(
        profile_id=profile_id,
        persisted=persisted,
        observed_nodes=observed_nodes,
        expected_desired=desired,
    )
    return bool(
        retained_desired is not None
        and deployment_topologies_reusable(retained_desired, desired)
    )


def _default_probe(
    profile_id: str | None = None,
    *,
    profile_snapshot: dict[str, Any] | None = None,
    operator_registration_id: str | None = None,
) -> dict[str, Any]:
    from profile_manager.data.health import _live_health, controller_inventory

    if profile_id is None:
        inventory = controller_inventory()
        if not inventory.get("available"):
            checked_at = _utc_now()
            detail = str(
                inventory.get("error") or "controller inventory is unavailable"
            )
            snapshot = build_topology_snapshot(
                desired=None,
                persisted=None,
                managed=[],
                unmanaged=[],
                health={
                    "state": "unknown",
                    "reason_code": "inventory_unavailable",
                    "detail": detail,
                    "checked_at": checked_at,
                },
                generated_at=checked_at,
            )
            snapshot.update(
                {
                    "state": "unknown",
                    "reason_code": "inventory_unavailable",
                    "detail": detail,
                }
            )
            snapshot["reconciliation"].update(
                {"state": "unknown", "reason_code": "inventory_unavailable"}
            )
            for action in ("deploy", "repair", "redeploy", "remove", "save"):
                snapshot["actions"][action] = {
                    "allowed": False,
                    "reason": "controller inventory is unavailable",
                    "confirmation_required": False,
                }
            return snapshot
        if inventory.get("available"):
            topology_ids = sorted(
                {
                    str(row.get("topology_id") or "")
                    for row in (inventory.get("managed") or [])
                    if isinstance(row, dict) and row.get("topology_id")
                }
            )
            if len(topology_ids) == 1:
                return _default_probe(topology_ids[0])
            if not topology_ids:
                checked_at = _utc_now()
                snapshot = build_topology_snapshot(
                    desired=None,
                    persisted=None,
                    managed=[],
                    unmanaged=[
                        row
                        for row in (inventory.get("unmanaged") or [])
                        if isinstance(row, dict)
                    ],
                    health={
                        "state": "no_active",
                        "reason_code": "no_managed_resources",
                        "checked_at": checked_at,
                    },
                    generated_at=checked_at,
                )
                snapshot["actions"]["deploy"] = {
                    "allowed": False,
                    "reason": "select a topology before deploy",
                    "confirmation_required": False,
                }
                return snapshot
            checked_at = _utc_now()
            snapshot = build_topology_snapshot(
                desired=None,
                persisted=None,
                managed=[
                    row
                    for row in (inventory.get("managed") or [])
                    if isinstance(row, dict)
                ],
                unmanaged=[
                    row
                    for row in (inventory.get("unmanaged") or [])
                    if isinstance(row, dict)
                ],
                health={
                    "state": "unknown",
                    "reason_code": "multiple_managed_topologies",
                    "checked_at": checked_at,
                },
                generated_at=checked_at,
            )
            snapshot.update(
                {
                    "state": "unknown",
                    "reason_code": "multiple_managed_topologies",
                    "detail": "Select one managed topology before taking an action.",
                }
            )
            snapshot["reconciliation"].update(
                {
                    "state": "ambiguous",
                    "reason_code": "multiple_managed_topologies",
                }
            )
            for action in ("deploy", "repair", "redeploy", "remove", "save"):
                snapshot["actions"][action] = {
                    "allowed": False,
                    "reason": "select one managed topology before taking an action",
                    "confirmation_required": False,
                }
            return snapshot

    if profile_id is not None:
        desired = _current_desired_topology(
            profile_id,
            profile_snapshot=profile_snapshot,
            operator_registration_id=operator_registration_id,
        )
        inventory = controller_inventory()
        topology_managed = [
            row
            for row in (inventory.get("managed") or [])
            if isinstance(row, dict) and row.get("topology_id") == profile_id
        ]
        if not topology_managed:
            checked_at = _utc_now()
            first = {
                "enabled": False,
                "state": "no_active",
                "profile_id": profile_id,
                "persisted_deployment": None,
            }
            classified = {
                "state": "no_active",
                "reason_code": "no_managed_resources",
                "checked_at": checked_at,
                "profile_id": profile_id,
            }
            snapshot = build_current_topology_snapshot(
                desired=desired,
                first=first,
                second=None,
                classified_health=classified,
                inventory=inventory,
                generated_at=checked_at,
            )
            if not inventory.get("available"):
                detail = str(
                    inventory.get("error") or "controller inventory is unavailable"
                )
                snapshot.update(
                    {
                        "state": "unknown",
                        "reason_code": "inventory_unavailable",
                        "detail": detail,
                    }
                )
                snapshot["health"].update(
                    {
                        "state": "unknown",
                        "reason_code": "inventory_unavailable",
                        "detail": detail,
                    }
                )
                snapshot["reconciliation"].update(
                    {"state": "unknown", "reason_code": "inventory_unavailable"}
                )
                snapshot["actions"]["deploy"].update(
                    {"allowed": False, "reason": "controller inventory is unavailable"}
                )
            return snapshot

        first = _live_health(profile_id)
        if not first.get("enabled"):
            classified = classify_current_profile_health(first, None)
            return build_current_topology_snapshot(
                desired=desired,
                first=first,
                second=None,
                classified_health=classified,
                inventory=inventory,
            )
        if _authoritative_controller_result(first, desired, now=_utc_now()):
            classified = classify_current_profile_health(first, None)
            return build_current_topology_snapshot(
                desired=desired,
                first=first,
                second=None,
                classified_health=classified,
                inventory=inventory,
            )
        time.sleep(10)
        second = _live_health(profile_id)
        classified = classify_current_profile_health(first, second)
        return build_current_topology_snapshot(
            desired=desired,
            first=first,
            second=second,
            classified_health=classified,
            inventory=inventory,
        )

    raise AssertionError("unscoped topology health must return from controller inventory")


def _current_desired_topology(
    profile_id: str,
    *,
    profile_snapshot: dict[str, Any] | None = None,
    operator_registration_id: str | None = None,
) -> dict[str, Any]:
    """Resolve the current catalog-bound desired topology for one profile."""
    from profile_manager.deployment_versions import build_deployment_version_preview
    from profile_manager.profiles import Profile, desired_topology_for_profile, find_profile

    if profile_snapshot is None:
        profile = find_profile(profile_id)
    else:
        profile = Profile.from_dict(profile_snapshot)
        if profile.id != profile_id:
            raise ValueError("resolved profile snapshot identity does not match request")
    preview_kwargs = {}
    if operator_registration_id is not None:
        preview_kwargs["operator_registration_id"] = operator_registration_id
    preview = build_deployment_version_preview(vars(profile), **preview_kwargs)
    desired = desired_topology_for_profile(profile, preview)
    if (
        not isinstance(desired, dict)
        or desired.get("profile_id") != profile_id
        or desired.get("topology_id") != profile_id
    ):
        raise ValueError("resolved desired topology identity does not match request")
    return desired


def topology_action_snapshot(profile_id: str) -> dict[str, Any]:
    """Keep observed health evidence while requesting the current desired identity."""
    snapshot = _default_probe(profile_id)
    retained_desired = snapshot.get("desired")
    if not isinstance(retained_desired, dict):
        raise ValueError("topology snapshot has no retained desired identity")
    has_registration = "operator_registration_id" in retained_desired
    has_overlay_revision = "operator_overlay_revision" in retained_desired
    registration_id = retained_desired.get("operator_registration_id")
    overlay_revision = retained_desired.get("operator_overlay_revision")
    if has_registration != has_overlay_revision or (
        has_registration
        and (
            not isinstance(registration_id, str)
            or not registration_id
            or not isinstance(overlay_revision, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", overlay_revision) is None
        )
    ):
        raise ValueError("operator registration identity is invalid")
    current_desired = _current_desired_topology(
        profile_id, operator_registration_id=registration_id
    )
    return {**snapshot, "desired": current_desired}


def _verified_retained_desired(
    *,
    profile_id: str,
    persisted: dict[str, Any] | None,
    observed_nodes: list[dict[str, Any]],
    expected_desired: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Rebuild current desired identity from an exactly joined retained plan."""
    from profile_manager.profiles import desired_topology_from_retained_substrate

    if not isinstance(persisted, dict) or persisted.get("profile_id") != profile_id:
        return None
    substrate = persisted.get("substrate")
    if not isinstance(substrate, dict) or substrate.get("profile_id") != profile_id:
        return None
    configured_nodes = substrate.get("nodes")
    if not isinstance(configured_nodes, list) or not configured_nodes:
        return None
    configured_by_id = {
        str(node.get("id") or ""): node
        for node in configured_nodes
        if isinstance(node, dict) and node.get("id")
    }
    observed_by_id = {
        str(node.get("node_id") or ""): node
        for node in observed_nodes
        if isinstance(node, dict) and node.get("node_id")
    }
    if (
        len(configured_by_id) != len(configured_nodes)
        or len(observed_by_id) != len(observed_nodes)
        or set(configured_by_id) != set(observed_by_id)
    ):
        return None
    nodes = []
    for configured in configured_nodes:
        node_id = str(configured["id"])
        observed = observed_by_id[node_id]
        implementation = str(
            configured.get("implementation") or configured.get("impl") or ""
        )
        version = str(configured.get("version") or "")
        image = str(configured.get("image") or "")
        if (
            not implementation
            or not version
            or not image
            or str(observed.get("implementation") or "") != implementation
            or str(observed.get("version") or "") != version
            or str(observed.get("image") or "") != image
        ):
            return None
        nodes.append(
            {
                "id": node_id,
                "implementation": implementation,
                "version": version,
                "image": image,
                "role": str(configured.get("role") or ""),
                "supporting": bool(configured.get("supporting", False)),
            }
        )
    desired = desired_topology_from_retained_substrate(
        profile_id, substrate, expected_desired=expected_desired
    )
    if desired.get("nodes") != nodes:
        return None
    return desired


def build_current_topology_snapshot(
    *,
    desired: dict[str, Any],
    first: dict[str, Any],
    second: dict[str, Any] | None,
    classified_health: dict[str, Any],
    inventory: dict[str, Any],
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Join one completed health observation with controller inventory."""
    topology_id = str(desired.get("topology_id") or "")
    managed = [
        row
        for row in (inventory.get("managed") or [])
        if isinstance(row, dict) and row.get("topology_id") == topology_id
    ]
    identity_sample = second or first
    verified_nodes = identity_sample.get("observed_nodes")
    controller_identity_join = bool(
        identity_sample.get("transport") == "controller"
        and "observed_nodes" in identity_sample
    )
    exact_mapping = False
    if isinstance(verified_nodes, list):
        verified_by_id = {
            str(node.get("node_id") or ""): node
            for node in verified_nodes
            if isinstance(node, dict) and node.get("node_id")
        }
        managed_by_id = {
            str(row.get("node_id") or ""): row
            for row in managed
            if row.get("node_id")
        }
        exact_mapping = bool(
            verified_by_id
            and len(verified_nodes) == len(verified_by_id)
            and len(managed) == len(managed_by_id)
            and set(verified_by_id) == set(managed_by_id)
            and all(
                node.get("container_name") == managed_by_id[node_id].get("name")
                for node_id, node in verified_by_id.items()
            )
        )
        if exact_mapping:
            managed = [
                {
                    **row,
                    "implementation": verified_by_id[str(row["node_id"])][
                        "implementation"
                    ],
                    "version": verified_by_id[str(row["node_id"])]["version"],
                    "image": verified_by_id[str(row["node_id"])]["image"],
                    "runtime_image": verified_by_id[str(row["node_id"])][
                        "runtime_image"
                    ],
                }
                for row in managed
            ]
    if controller_identity_join and not exact_mapping:
        classified_health = {
            **classified_health,
            "state": "unhealthy",
            "reason_code": "controller_identity_inventory_binding_failed",
            "detail": (
                "Verified controller node identities did not bind exactly to "
                "current inventory."
            ),
        }
    unmanaged = [
        row for row in (inventory.get("unmanaged") or []) if isinstance(row, dict)
    ]
    persisted = (second or first).get("persisted_deployment") or first.get(
        "persisted_deployment"
    )
    if controller_identity_join and exact_mapping:
        retained_desired = _verified_retained_desired(
            profile_id=topology_id,
            persisted=persisted,
            observed_nodes=verified_nodes,
            expected_desired=desired,
        )
        if retained_desired is not None:
            desired = retained_desired
    return build_topology_snapshot(
        desired=desired,
        persisted=persisted,
        managed=managed,
        unmanaged=unmanaged,
        health=classified_health,
        generated_at=generated_at or _utc_now(),
    )


def normalize_probe_failure(
    error: BaseException,
    *,
    previous: dict[str, Any] | None,
    profile_id: str | None = None,
) -> dict[str, Any]:
    result = {
        "schema_version": "v1",
        "state": "unknown",
        "reason_code": "topology_probe_failed",
        "detail": str(error),
        "checked_at": _utc_now(),
        "previous": _public_copy(previous),
        "previous_is_current": False,
    }
    if profile_id is not None:
        result["profile_id"] = profile_id
    return result


def topology_health_snapshot(*, profile_id: str | None = None) -> dict[str, Any]:
    with _LOCK:
        checking = _THREAD is not None and _THREAD.is_alive()
        memory_result = _public_copy(
            _LAST_RESULT
            if profile_id is None
            else _LAST_RESULTS.get(_profile_key(profile_id))
        )
        persisted_result = None
        if memory_result is None:
            candidate = _read_persisted_result(profile_id)
            if candidate is not None and (
                profile_id is None or candidate.get("profile_id") == profile_id
            ):
                persisted_result = candidate
        previous = memory_result or persisted_result
        if checking:
            if profile_id != _INFLIGHT_PROFILE_ID:
                return {
                    "schema_version": "v1",
                    "state": "conflict",
                    "reason_code": "different_profile_check_in_progress",
                    "requested_profile_id": profile_id,
                    "inflight_profile_id": _INFLIGHT_PROFILE_ID,
                    "previous": _completed_for(profile_id),
                    "previous_is_current": False,
                }
            return {
                "schema_version": "v1",
                "state": "checking",
                "started_at": _STARTED_AT,
                "previous": previous,
                "previous_is_current": False,
                "requested_profile_id": _INFLIGHT_PROFILE_ID,
            }
        if (
            profile_id is not None
            and previous is not None
            and previous.get("profile_id") != profile_id
        ):
            return {
                "schema_version": "v1",
                "state": "idle",
                "reason_code": "no_snapshot_for_requested_profile",
                "requested_profile_id": profile_id,
                "cached_profile_id": previous.get("profile_id"),
                "previous": None,
                "previous_is_current": False,
            }
        if previous is None:
            return {
                "schema_version": "v1",
                "state": "idle",
                "previous": None,
                "previous_is_current": False,
            }
        return _present_completed(
            previous,
            observation_source="memory" if memory_result is not None else "persisted",
        )


def topology_health_evidence() -> dict[str, Any] | None:
    """Return the last completed public observation, never an in-flight placeholder."""
    with _LOCK:
        return _public_copy(_LAST_RESULT) or _public_copy(_read_persisted_result())


def topology_redeploy_command() -> list[str]:
    """Build the one host-controlled mixed-topology mutation command."""
    from profile_manager.config import load_config
    from profile_manager.remote import render_topology_redeploy_command

    return render_topology_redeploy_command(
        load_config(), topology_id="cardano_amaru"
    )


def _run_probe(
    probe: Callable[[], dict[str, Any]],
    profile_id: str | None = None,
    desired_plan_sha256: str | None = None,
) -> None:
    global _LAST_RESULT
    try:
        result = probe()
        if not isinstance(result, dict) or not result.get("state"):
            raise ValueError("topology probe returned an invalid result")
        completed = _public_copy(result) or {}
        completed.setdefault("schema_version", "v1")
        if profile_id is not None:
            completed.setdefault("profile_id", profile_id)
    except BaseException as error:  # noqa: BLE001 - surface probe failures as data
        with _LOCK:
            previous = _completed_for(profile_id)
        completed = normalize_probe_failure(
            error, previous=previous, profile_id=profile_id
        )
    if desired_plan_sha256 is None:
        completed.pop("request_binding", None)
    else:
        completed["request_binding"] = {
            "profile_id": profile_id,
            "desired_plan_sha256": desired_plan_sha256,
        }
    _write_persisted_result(completed, profile_id=profile_id)
    with _LOCK:
        if profile_id is None:
            _LAST_RESULT = completed
        _LAST_RESULTS[_profile_key(profile_id)] = completed


def request_topology_health_check(
    *,
    probe: Callable[[], dict[str, Any]] | None = None,
    profile_id: str | None = None,
    desired_plan_sha256: str | None = None,
) -> dict[str, Any]:
    """Start one fresh check, or join the check already in flight."""
    global _THREAD, _STARTED_AT, _INFLIGHT_PROFILE_ID
    global _INFLIGHT_DESIRED_PLAN_SHA256
    desired_plan_sha256 = _validated_desired_plan_sha256(desired_plan_sha256)
    if desired_plan_sha256 is not None and profile_id is None:
        raise ValueError("desired topology plan identity requires a profile identity")
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            if profile_id != _INFLIGHT_PROFILE_ID:
                return {
                    "schema_version": "v1",
                    "state": "conflict",
                    "reason_code": "different_profile_check_in_progress",
                    "requested_profile_id": profile_id,
                    "inflight_profile_id": _INFLIGHT_PROFILE_ID,
                    "previous": _completed_for(profile_id),
                    "previous_is_current": False,
                }
            if (
                desired_plan_sha256 is not None
                and desired_plan_sha256 != _INFLIGHT_DESIRED_PLAN_SHA256
            ):
                return {
                    "schema_version": "v1",
                    "state": "conflict",
                    "reason_code": "different_topology_check_in_progress",
                    "requested_profile_id": profile_id,
                    "inflight_profile_id": _INFLIGHT_PROFILE_ID,
                    "requested_desired_plan_sha256": desired_plan_sha256,
                    "inflight_desired_plan_sha256": _INFLIGHT_DESIRED_PLAN_SHA256,
                    "previous": _completed_for(profile_id),
                    "previous_is_current": False,
                }
            return {
                "schema_version": "v1",
                "state": "checking",
                "started_at": _STARTED_AT,
                "previous": _completed_for(profile_id),
                "previous_is_current": False,
                "requested_profile_id": _INFLIGHT_PROFILE_ID,
            }
        previous = _completed_for(profile_id)
        _STARTED_AT = _utc_now()
        _INFLIGHT_PROFILE_ID = profile_id
        _INFLIGHT_DESIRED_PLAN_SHA256 = desired_plan_sha256
        selected_probe = probe or _default_probe
        if probe is None and profile_id is not None:
            selected_probe = lambda: _default_probe(profile_id)
        _THREAD = threading.Thread(
            target=_run_probe,
            args=(selected_probe, profile_id, desired_plan_sha256),
            name="dwarf-topology-health",
            daemon=True,
        )
        _THREAD.start()
        response = {
            "schema_version": "v1",
            "state": "checking",
            "started_at": _STARTED_AT,
            "previous": previous,
            "previous_is_current": False,
        }
        if profile_id is not None:
            response["requested_profile_id"] = profile_id
        if desired_plan_sha256 is not None:
            response["requested_desired_plan_sha256"] = desired_plan_sha256
        return response


def wait_for_topology_health(
    *,
    timeout: float,
    profile_id: str | None = None,
    desired_plan_sha256: str | None = None,
) -> dict[str, Any]:
    desired_plan_sha256 = _validated_desired_plan_sha256(desired_plan_sha256)
    with _LOCK:
        thread = _THREAD
        inflight_profile_id = _INFLIGHT_PROFILE_ID
        inflight_desired_plan_sha256 = _INFLIGHT_DESIRED_PLAN_SHA256
    if (
        desired_plan_sha256 is not None
        and thread is not None
        and thread.is_alive()
        and (
            profile_id != inflight_profile_id
            or desired_plan_sha256 != inflight_desired_plan_sha256
        )
    ):
        return {
            "schema_version": "v1",
            "state": "conflict",
            "reason_code": "different_topology_check_in_progress",
            "requested_profile_id": profile_id,
            "inflight_profile_id": inflight_profile_id,
            "requested_desired_plan_sha256": desired_plan_sha256,
            "inflight_desired_plan_sha256": inflight_desired_plan_sha256,
            "previous": _completed_for(profile_id),
            "previous_is_current": False,
        }
    if thread is not None:
        thread.join(timeout=timeout)
    snapshot = topology_health_snapshot(profile_id=profile_id)
    if desired_plan_sha256 is None or snapshot.get("state") == "checking":
        return snapshot
    completed_binding = snapshot.get("request_binding") or {}
    if completed_binding == {
        "profile_id": profile_id,
        "desired_plan_sha256": desired_plan_sha256,
    }:
        return snapshot
    return {
        "schema_version": "v1",
        "state": "conflict",
        "reason_code": "completed_topology_identity_mismatch",
        "requested_profile_id": profile_id,
        "requested_desired_plan_sha256": desired_plan_sha256,
        "completed_request_binding": completed_binding,
        "previous": snapshot,
        "previous_is_current": False,
    }


def reset_topology_health_state() -> None:
    """Clear process-local state. Intended for deterministic tests."""
    global _THREAD, _LAST_RESULT, _STARTED_AT, _INFLIGHT_PROFILE_ID
    global _INFLIGHT_DESIRED_PLAN_SHA256
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            raise RuntimeError("cannot reset topology health while a probe is running")
        _THREAD = None
        _LAST_RESULT = None
        _LAST_RESULTS.clear()
        _STARTED_AT = None
        _INFLIGHT_PROFILE_ID = None
        _INFLIGHT_DESIRED_PLAN_SHA256 = None
