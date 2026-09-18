"""Resolve scenario measurement intent against an immutable real target."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, is_dataclass
from typing import Any, Iterable

from profile_manager.data.catalog_definitions import (
    CatalogError,
    DefinitionRecord,
    load_definition,
)


_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_DEFAULT_PROFILES = {
    ("amaru", "stock"): "amaru-security-default",
    ("amaru", "patched"): "amaru-security-patched",
}


class MeasurementResolutionError(ValueError):
    def __init__(self, message: str, *, resolution: dict[str, Any]):
        super().__init__(message)
        self.resolution = resolution


def _definition_digest(record: DefinitionRecord) -> str:
    return "sha256:" + hashlib.sha256(record.source).hexdigest()


def _selection_dict(value: Any, *, source: str) -> dict[str, Any]:
    if is_dataclass(value):
        value = asdict(value)
    if not isinstance(value, dict):
        raise ValueError("measurement override must be an object")
    measurement_id = value.get("id")
    enabled = value.get("enabled")
    parameters = value.get("parameters") or {}
    gate = value.get("threshold_gate") or {"enabled": False, "thresholds": []}
    if not isinstance(measurement_id, str) or not measurement_id:
        raise ValueError("measurement override id is required")
    if not isinstance(enabled, bool):
        raise ValueError(f"measurement {measurement_id} enabled must be boolean")
    if not isinstance(parameters, dict) or len(json.dumps(parameters)) > 16384:
        raise ValueError(f"measurement {measurement_id} parameters are not bounded")
    if not isinstance(gate, dict) or set(gate) - {"enabled", "thresholds"}:
        raise ValueError(f"measurement {measurement_id} threshold gate is invalid")
    gate_enabled = gate.get("enabled", False)
    thresholds = gate.get("thresholds") or []
    if not isinstance(gate_enabled, bool):
        raise ValueError(
            f"measurement {measurement_id} threshold gate enabled must be boolean"
        )
    if not isinstance(thresholds, list):
        raise ValueError(f"measurement {measurement_id} thresholds must be a list")
    if gate_enabled and not thresholds:
        raise ValueError(f"measurement {measurement_id} requires at least one threshold")
    return {
        "id": measurement_id,
        "enabled": enabled,
        "parameters": dict(parameters),
        "threshold_gate": {
            "enabled": gate_enabled,
            "thresholds": list(thresholds),
        },
        "source": source,
    }


def _target_identity(identity: dict[str, Any]) -> dict[str, Any]:
    required = {"implementation", "version", "source_revision", "mode", "image_digest"}
    missing = sorted(required - set(identity))
    if missing:
        raise ValueError(f"target identity missing {', '.join(missing)}")
    if identity["implementation"] not in {"amaru", "cardano-node"}:
        raise ValueError("target implementation is unsupported")
    if identity["mode"] not in {"stock", "coverage", "patched"}:
        raise ValueError("target mode is unsupported")
    if not isinstance(identity["version"], str) or not identity["version"]:
        raise ValueError("target version is not exact")
    if not isinstance(identity["source_revision"], str) or not _REVISION.fullmatch(
        identity["source_revision"]
    ):
        raise ValueError("target source_revision is not an exact commit")
    if not isinstance(identity["image_digest"], str) or not _DIGEST.fullmatch(
        identity["image_digest"]
    ):
        raise ValueError("target image_digest must be immutable sha256")
    executable_digest = identity.get("executable_digest")
    if executable_digest is not None and not _DIGEST.fullmatch(executable_digest):
        raise ValueError("target executable_digest must be immutable sha256")
    return dict(identity)


def _compatibility_reason(
    definition: dict[str, Any],
    *,
    target: dict[str, Any],
    capabilities: set[str],
) -> str | None:
    compatibility = definition["compatibility"]
    if compatibility["implementation"] != target["implementation"]:
        return (
            f"implementation requires {compatibility['implementation']}, "
            f"target is {target['implementation']}"
        )
    matching_version = next(
        (
            item for item in compatibility["versions"]
            if item["version"] == target["version"]
        ),
        None,
    )
    if matching_version is None:
        supported = ", ".join(item["version"] for item in compatibility["versions"])
        return f"version {target['version']} is unsupported; expected {supported}"
    if matching_version["source_revision"] != target["source_revision"]:
        return (
            f"source revision {target['source_revision']} does not match "
            f"{matching_version['source_revision']}"
        )
    if target["mode"] not in compatibility["target_modes"]:
        modes = ", ".join(compatibility["target_modes"])
        return f"target mode {target['mode']} is unsupported; expected {modes}"
    missing = sorted(set(definition["required_capabilities"]) - capabilities)
    if missing:
        return f"missing capabilities: {', '.join(missing)}"
    return None


def resolve_measurements(
    scenario: Any,
    *,
    target_identity: dict[str, Any],
    capabilities: Iterable[str],
    run_overrides: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve selected taps; incompatible explicit intent fails before execution."""
    try:
        target = _target_identity(target_identity)
    except ValueError as exc:
        empty = {
            "target_identity": dict(target_identity),
            "profile": None,
            "requested": [],
            "resolved": [],
            "skipped": [],
            "incompatible": [{"id": None, "reason": str(exc)}],
            "disabled": [],
        }
        raise MeasurementResolutionError(str(exc), resolution=empty) from exc
    if scenario.target.get("implementation") != target["implementation"]:
        reason = (
            f"scenario targets {scenario.target.get('implementation')}, "
            f"but deployed identity is {target['implementation']}"
        )
        empty = {
            "target_identity": target,
            "profile": None,
            "requested": [],
            "resolved": [],
            "skipped": [],
            "incompatible": [{"id": None, "reason": reason}],
            "disabled": [],
        }
        raise MeasurementResolutionError(reason, resolution=empty)

    profile_id = scenario.measurement_profile
    profile_source = "explicit-profile"
    if profile_id is None:
        profile_id = _DEFAULT_PROFILES.get((target["implementation"], target["mode"]))
        profile_source = "implicit-default"
    if profile_id == "none":
        profile_meta: dict[str, Any] | None = {"id": "none", "source": "explicit-none"}
        profile_selections: list[dict[str, Any]] = []
    elif profile_id:
        try:
            profile_record = load_definition("measurement-profiles", profile_id)
        except CatalogError as exc:
            resolution = {
                "target_identity": target,
                "profile": {"id": profile_id, "source": profile_source},
                "requested": [],
                "resolved": [],
                "skipped": [],
                "incompatible": [{"id": profile_id, "reason": "measurement profile is unavailable"}],
                "disabled": [],
            }
            raise MeasurementResolutionError(
                f"measurement profile {profile_id} is unavailable", resolution=resolution
            ) from exc
        profile_meta = {
            "id": profile_id,
            "source": profile_source,
            "definition_digest": _definition_digest(profile_record),
        }
        profile_selections = [
            _selection_dict(item, source=profile_source)
            for item in profile_record.data["measurements"]
        ]
    else:
        profile_meta = None
        profile_selections = []

    ordered: dict[str, dict[str, Any]] = {
        item["id"]: item for item in profile_selections
    }
    for item in scenario.measurements:
        normalized = _selection_dict(item, source="scenario-override")
        ordered[normalized["id"]] = normalized
    for item in run_overrides or []:
        try:
            normalized = _selection_dict(item, source="launch-override")
        except ValueError as exc:
            resolution = {
                "target_identity": target,
                "profile": profile_meta,
                "requested": [],
                "resolved": [],
                "skipped": [],
                "incompatible": [{"id": None, "reason": str(exc)}],
                "disabled": [],
            }
            raise MeasurementResolutionError(str(exc), resolution=resolution) from exc
        ordered[normalized["id"]] = normalized

    resolution: dict[str, Any] = {
        "target_identity": target,
        "profile": profile_meta,
        "requested": [
            {"id": item["id"], "enabled": item["enabled"], "source": item["source"]}
            for item in ordered.values()
        ],
        "resolved": [],
        "skipped": [],
        "incompatible": [],
        "disabled": [],
    }
    capability_set = {str(item) for item in capabilities}
    for selection in ordered.values():
        if not selection["enabled"]:
            resolution["disabled"].append(dict(selection))
            continue
        try:
            record = load_definition("measurements", selection["id"])
        except CatalogError:
            reason = "measurement definition is unavailable"
            entry = {"id": selection["id"], "source": selection["source"], "reason": reason}
            resolution["incompatible"].append(entry)
            continue
        reason = _compatibility_reason(
            record.data, target=target, capabilities=capability_set
        )
        if (
            reason is None
            and selection["threshold_gate"]["enabled"]
            and not record.data["threshold_gate"]["supported"]
        ):
            reason = "does not support threshold gating"
        if reason:
            entry = {
                "id": selection["id"],
                "source": selection["source"],
                "definition_digest": _definition_digest(record),
                "reason": reason,
            }
            if selection["source"] == "implicit-default":
                resolution["skipped"].append(entry)
            else:
                resolution["incompatible"].append(entry)
            continue
        resolution["resolved"].append(
            {
                **selection,
                "definition_digest": _definition_digest(record),
                "definition": record.data,
            }
        )

    if resolution["incompatible"]:
        first = resolution["incompatible"][0]
        raise MeasurementResolutionError(
            f"measurement {first['id'] or 'selection'} is incompatible: {first['reason']}",
            resolution=resolution,
        )
    return resolution
