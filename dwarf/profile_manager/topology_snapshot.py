"""Join desired, persisted, observed, and health topology evidence."""
from __future__ import annotations

from datetime import datetime, timezone
from collections import Counter
import json
from typing import Any


_OBSERVED_IDENTITY_FIELDS = ("implementation", "version", "image")
_PERSISTED_NODE_FIELDS = (
    "implementation",
    "version",
    "image",
    "role",
    "supporting",
)
_PERSISTED_PLAN_FIELDS = (
    "profile_id",
    "network_magic",
    "deployment_adapter",
    "catalog_revision",
    "version_policy",
    "topology",
)


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _node_rows(value: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    candidate: Any = value
    if isinstance(value.get("substrate"), dict):
        candidate = value["substrate"]
    nodes = candidate.get("nodes") if isinstance(candidate, dict) else None
    if not isinstance(nodes, list):
        return []
    return [dict(node) for node in nodes if isinstance(node, dict)]


def _plan(value: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    substrate = value.get("substrate")
    return dict(substrate) if isinstance(substrate, dict) else dict(value)


def _node_id(value: dict[str, Any]) -> str:
    return str(value.get("node_id") or value.get("id") or "")


def _identity(value: dict[str, Any], field: str) -> str:
    if field == "implementation":
        return str(value.get("implementation") or value.get("impl") or "")
    return str(value.get(field) or "")


def _plan_identity(value: dict[str, Any], field: str) -> str:
    if field == "profile_id":
        return str(value.get("profile_id") or value.get("topology_id") or "")
    if field == "topology":
        topology = value.get("topology")
        return (
            json.dumps(topology, sort_keys=True, separators=(",", ":"))
            if isinstance(topology, dict)
            else ""
        )
    return str(value.get(field) if value.get(field) is not None else "")


def _resource(value: dict[str, Any], *, ownership: str) -> dict[str, Any]:
    row = dict(value)
    managed = ownership == "managed"
    row.update(
        {
            "ownership": ownership,
            "mutation_allowed": managed,
            "allowed_actions": ["inspect", "logs"] if managed else ["inspect"],
        }
    )
    return row


def _action(allowed: bool, reason: str, *, confirmation: bool = False) -> dict[str, Any]:
    return {
        "allowed": allowed,
        "reason": reason,
        "confirmation_required": confirmation if allowed else False,
    }


def build_topology_snapshot(
    *,
    desired: dict[str, Any] | None,
    persisted: dict[str, Any] | None,
    managed: list[dict[str, Any]],
    unmanaged: list[dict[str, Any]],
    health: dict[str, Any] | None,
    generated_at: str,
    health_max_age_seconds: int = 30,
) -> dict[str, Any]:
    """Return the versioned, non-mutating desired-versus-observed snapshot."""
    desired = dict(desired or {})
    desired_nodes = _node_rows(desired)
    desired_by_id = {_node_id(node): node for node in desired_nodes if _node_id(node)}
    managed_rows = [_resource(row, ownership="managed") for row in managed]
    unmanaged_rows = [_resource(row, ownership="unmanaged") for row in unmanaged]
    managed_identity_counts = Counter(
        _node_id(row) for row in managed_rows if _node_id(row)
    )
    duplicate_managed_node_ids = sorted(
        node_id for node_id, count in managed_identity_counts.items() if count > 1
    )
    observed_by_id = {
        _node_id(row): row for row in managed_rows if _node_id(row)
    }
    not_running = sorted(
        node_id
        for node_id, row in observed_by_id.items()
        if node_id in desired_by_id and str(row.get("state") or "unknown") != "running"
    )
    unmanaged_conflicts = sorted(
        {
            _node_id(row)
            for row in unmanaged_rows
            if _node_id(row) in desired_by_id
            and str(row.get("topology_id") or desired.get("topology_id") or "")
            == str(desired.get("topology_id") or "")
        }
    )

    missing = sorted(set(desired_by_id) - set(observed_by_id))
    unexpected = sorted(set(observed_by_id) - set(desired_by_id))
    mismatches = []
    for node_id in sorted(set(desired_by_id) & set(observed_by_id)):
        expected = desired_by_id[node_id]
        actual = observed_by_id[node_id]
        fields = [
            field
            for field in _OBSERVED_IDENTITY_FIELDS
            if _identity(expected, field) != _identity(actual, field)
        ]
        if fields:
            mismatches.append(
                {
                    "node_id": node_id,
                    "fields": fields,
                    "desired": {field: _identity(expected, field) for field in fields},
                    "observed": {field: _identity(actual, field) for field in fields},
                }
            )

    persisted_nodes = _node_rows(persisted)
    persisted_by_id = {
        _node_id(node): node for node in persisted_nodes if _node_id(node)
    }
    persisted_node_mismatch = bool(desired_by_id) and (
        set(persisted_by_id) != set(desired_by_id)
        or any(
            any(
                _identity(desired_by_id[node_id], field)
                != _identity(persisted_by_id[node_id], field)
                for field in _PERSISTED_NODE_FIELDS
            )
            for node_id in set(persisted_by_id) & set(desired_by_id)
        )
    )
    persisted_plan = _plan(persisted)
    persisted_mismatched_fields = sorted(
        field
        for field in _PERSISTED_PLAN_FIELDS
        if desired.get(field) is not None
        and _plan_identity(desired, field) != _plan_identity(persisted_plan, field)
    )
    persisted_mismatch = persisted_node_mismatch or bool(
        persisted_mismatched_fields
    )

    health = dict(health or {})
    checked_at = health.get("checked_at")
    generated = _timestamp(generated_at)
    checked = _timestamp(checked_at) if isinstance(checked_at, str) else None
    age_seconds = (
        max(0, int((generated - checked).total_seconds()))
        if generated is not None and checked is not None
        else None
    )
    health_fresh = age_seconds is not None and age_seconds <= health_max_age_seconds
    health_view = {
        **health,
        "checked_at": checked_at,
        "age_seconds": age_seconds,
        "max_age_seconds": health_max_age_seconds,
        "fresh": health_fresh,
    }

    if not managed_rows:
        state, reason = "absent", "no_managed_resources"
    elif duplicate_managed_node_ids:
        state, reason = "drifted", "duplicate_managed_identity"
    elif missing:
        state, reason = "partially_deployed", "desired_nodes_missing"
    elif unexpected or mismatches:
        state, reason = "drifted", "observed_identity_drift"
    elif persisted_mismatch:
        state, reason = "drifted", "persisted_plan_drift"
    elif not_running:
        state, reason = "unhealthy", "managed_resources_not_running"
    elif not health_fresh:
        state, reason = "unhealthy", "health_evidence_stale"
    elif health.get("state") not in {"healthy", "catching_up"}:
        state, reason = "unhealthy", str(
            health.get("reason_code") or "health_not_healthy"
        )
    else:
        state, reason = "converged", "desired_observed_and_health_match"

    if state == "converged":
        public_state = "healthy"
    elif state == "absent":
        public_state = "no_active"
    elif reason == "health_evidence_stale" or health.get("state") in {None, "unknown"}:
        public_state = "unknown"
    else:
        public_state = "unhealthy"

    has_managed = bool(managed_rows)
    repair_allowed = state == "partially_deployed" or (
        state == "unhealthy"
        and (
            bool(not_running)
            or (health_fresh and health.get("state") == "unhealthy")
        )
    )
    actions = {
        "health": _action(True, "read-only health inspection"),
        "deploy": _action(
            state == "absent" and not unmanaged_conflicts,
            (
                "unmanaged resource collision"
                if unmanaged_conflicts
                else "topology must be absent"
            ),
        ),
        "repair": _action(
            repair_allowed,
            "repair requires fresh evidence of a partial or unhealthy managed topology",
        ),
        "redeploy": _action(
            has_managed,
            "redeploy requires managed resources",
            confirmation=True,
        ),
        "remove": _action(
            has_managed,
            "remove requires managed resources",
            confirmation=True,
        ),
        "save": _action(has_managed, "save requires managed observed resources"),
    }
    return {
        "schema_version": "v1",
        "state": public_state,
        "reason_code": reason,
        "checked_at": checked_at,
        "detail": health.get("detail"),
        "generated_at": generated_at,
        "topology_id": desired.get("topology_id"),
        "profile_id": desired.get("profile_id"),
        "desired": desired,
        "persisted": persisted,
        "observed": {
            "managed": sorted(
                managed_rows,
                key=lambda row: (_node_id(row), str(row.get("id") or "")),
            ),
            "unmanaged": sorted(
                unmanaged_rows,
                key=lambda row: (
                    str(row.get("name") or ""),
                    str(row.get("id") or ""),
                ),
            ),
        },
        "health": health_view,
        "unmanaged_visibility": {
            "state": "visible" if unmanaged_rows else "none",
            "count": len(unmanaged_rows),
            "mutation_allowed": False,
        },
        "reconciliation": {
            "state": state,
            "reason_code": reason,
            "missing_node_ids": missing,
            "unexpected_managed_node_ids": unexpected,
            "mismatched_nodes": mismatches,
            "not_running_node_ids": not_running,
            "duplicate_managed_node_ids": duplicate_managed_node_ids,
            "unmanaged_conflict_node_ids": unmanaged_conflicts,
            "persisted_mismatched_fields": persisted_mismatched_fields,
            "persisted_plan_matches": bool(persisted_nodes) and not persisted_mismatch,
        },
        "actions": actions,
    }


def refresh_snapshot_freshness(
    snapshot: dict[str, Any], *, now: str
) -> dict[str, Any]:
    """Re-evaluate cached evidence age without mutating retained evidence."""
    presented = json.loads(json.dumps(snapshot))
    health = presented.get("health")
    reconciliation = presented.get("reconciliation")
    if not isinstance(health, dict) or not isinstance(reconciliation, dict):
        return presented
    checked = _timestamp(health.get("checked_at"))
    current = _timestamp(now)
    max_age = health.get("max_age_seconds")
    if checked is None or current is None or not isinstance(max_age, int):
        return presented
    age = max(0, int((current - checked).total_seconds()))
    health["age_seconds"] = age
    health["fresh"] = age <= max_age
    if health["fresh"]:
        return presented
    previous_state = reconciliation.get("state")
    previous_reason = reconciliation.get("reason_code")
    reconciliation.update(
        {
            "state": "unhealthy",
            "reason_code": "health_evidence_stale",
            "stale_previous_state": previous_state,
            "stale_previous_reason_code": previous_reason,
        }
    )
    presented.update(
        {
            "state": "unknown",
            "reason_code": "health_evidence_stale",
            "detail": "The last successful topology observation has expired.",
        }
    )
    actions = presented.get("actions")
    if isinstance(actions, dict):
        for name, action in actions.items():
            if name == "health" or not isinstance(action, dict):
                continue
            action.update(
                {
                    "allowed": False,
                    "reason": "fresh topology evidence is required",
                    "confirmation_required": False,
                }
            )
    return presented
