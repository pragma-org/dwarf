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
    list_definitions,
    load_definition,
)


_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_SHA64 = re.compile(r"^[0-9a-f]{64}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DEFAULT_PROFILES = {
    ("amaru", "stock"): "amaru-security-default",
    ("amaru", "patched"): "amaru-security-patched",
    ("cardano-node", "stock"): "cardano-security-default",
    ("cardano-node", "patched"): "cardano-security-patched",
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
    required = {"implementation", "version", "source_revision", "mode"}
    missing = sorted(required - set(identity))
    if missing:
        raise ValueError(f"target identity missing {', '.join(missing)}")
    if identity["implementation"] not in {"amaru", "cardano-node"}:
        raise ValueError("target implementation is unsupported")
    if identity["mode"] not in {"stock", "coverage", "patched"}:
        raise ValueError("target mode is unsupported")
    if not isinstance(identity["version"], str) or not identity["version"]:
        raise ValueError("target version is not exact")
    local_config = identity.get("identity_kind") == "operator-local-config"
    source_revision = identity["source_revision"]
    if source_revision is not None and (
        not isinstance(source_revision, str) or not _REVISION.fullmatch(source_revision)
    ):
        raise ValueError("target source_revision is not an exact commit")
    if source_revision is None and not local_config:
        raise ValueError("target source_revision is not an exact commit")
    if local_config:
        config_id = identity.get("image_config_id")
        executable_digest = identity.get("executable_digest")
        platform = identity.get("platform")
        registration_id = identity.get("operator_registration_id")
        overlay_revision = identity.get("operator_overlay_revision")
        controller_installation_id = identity.get("controller_installation_id")
        if not isinstance(config_id, str) or not _DIGEST.fullmatch(config_id):
            raise ValueError("local target image_config_id must be immutable sha256")
        if not isinstance(executable_digest, str) or not _DIGEST.fullmatch(
            executable_digest
        ):
            raise ValueError("local target executable_digest must be immutable sha256")
        if platform not in {"linux/amd64", "linux/arm64"}:
            raise ValueError("local target platform is unsupported")
        if not isinstance(registration_id, str) or not _IDENTIFIER.fullmatch(
            registration_id
        ):
            raise ValueError("local target registration is invalid")
        if not isinstance(overlay_revision, str) or not _DIGEST.fullmatch(
            overlay_revision
        ):
            raise ValueError("local target overlay revision is invalid")
        if not isinstance(controller_installation_id, str) or not _IDENTIFIER.fullmatch(
            controller_installation_id
        ):
            raise ValueError("local target controller installation is invalid")
        known_incompatible = identity.get("known_incompatible_measurements") or []
        if (
            not isinstance(known_incompatible, list)
            or len(known_incompatible) > 64
            or any(
                not isinstance(item, str) or not _IDENTIFIER.fullmatch(item)
                for item in known_incompatible
            )
        ):
            raise ValueError("local target incompatibility evidence is invalid")
        instrumentation = identity.get("verified_instrumentation")
        if instrumentation is not None:
            if not isinstance(instrumentation, dict) or set(instrumentation) != {
                "capabilities",
                "evidence_sha256",
                "controller_installation_id",
                "config_image_id",
            }:
                raise ValueError("local target instrumentation evidence is invalid")
            verified = instrumentation["capabilities"]
            if (
                not isinstance(verified, list)
                or len(verified) > 64
                or any(
                    not isinstance(item, str) or not _IDENTIFIER.fullmatch(item)
                    for item in verified
                )
                or not _DIGEST.fullmatch(str(instrumentation["evidence_sha256"]))
                or not _IDENTIFIER.fullmatch(
                    str(instrumentation["controller_installation_id"])
                )
                or instrumentation["config_image_id"] != config_id
            ):
                raise ValueError("local target instrumentation evidence is invalid")
    elif identity["mode"] in {"stock", "patched"}:
        image_digest = identity.get("image_digest")
        if not isinstance(image_digest, str) or not _DIGEST.fullmatch(image_digest):
            raise ValueError("target image_digest must be immutable sha256")
    executable_digest = identity.get("executable_digest")
    if executable_digest is not None and not _DIGEST.fullmatch(str(executable_digest)):
        raise ValueError("target executable_digest must be immutable sha256")
    if identity["mode"] == "coverage":
        if not isinstance(executable_digest, str) or not _DIGEST.fullmatch(
            executable_digest
        ):
            raise ValueError("coverage target executable_digest must be immutable sha256")
        if not _DIGEST.fullmatch(str(identity.get("build_result_sha256") or "")):
            raise ValueError("coverage target build_result_sha256 must be immutable sha256")
        if not _SHA64.fullmatch(str(identity.get("coverage_harness_sha256") or "")):
            raise ValueError("coverage target coverage_harness_sha256 must be immutable sha256")
        if identity.get("engine") != "cargo-fuzz/libFuzzer":
            raise ValueError("coverage target engine must be cargo-fuzz/libFuzzer")
        if identity.get("performance_authority") != "non-authoritative":
            raise ValueError("coverage target performance authority must be non-authoritative")
        if identity.get("non_authoritative_performance") is not True:
            raise ValueError("coverage target performance must be labelled non-authoritative")
    return dict(identity)


def _measurement_classification(
    definition: dict[str, Any],
    *,
    target: dict[str, Any],
    capabilities: set[str],
) -> tuple[dict[str, str], dict[str, str]]:
    compatibility = definition["compatibility"]
    if compatibility["implementation"] != target["implementation"]:
        return (
            {
                "state": "incompatible",
                "reason": (
                    f"implementation requires {compatibility['implementation']}, "
                    f"target is {target['implementation']}"
                ),
            },
            {"state": "unavailable", "reason": "tap targets another implementation"},
        )
    local_config = target.get("identity_kind") == "operator-local-config"
    if definition.get("id") in set(
        target.get("known_incompatible_measurements") or []
    ):
        return (
            {
                "state": "incompatible",
                "reason": "retained evidence marks this tap known-incompatible",
            },
            {"state": "available", "reason": "required capabilities are present"},
        )
    required_capabilities = set(definition["required_capabilities"])
    missing = sorted(required_capabilities - capabilities)
    if missing:
        capability = {
            "state": "unavailable",
            "reason": f"missing capabilities: {', '.join(missing)}",
        }
    elif local_config and definition.get("collection_mode") == "patched-node":
        instrumentation = target.get("verified_instrumentation") or {}
        verified = set(instrumentation.get("capabilities") or [])
        if not required_capabilities.issubset(verified):
            capability = {
                "state": "unavailable",
                "reason": "required instrumentation has not been independently verified",
            }
        else:
            capability = {
                "state": "unknown",
                "reason": "required instrumentation is present but unqualified for this tap",
            }
    elif local_config and definition.get("collection_mode") == "stock-telemetry":
        capability = {
            "state": "unknown",
            "reason": "stock telemetry is configured but output has not been validated for this binary",
        }
    else:
        capability = {"state": "available", "reason": "required capabilities are present"}

    matching_version = next(
        (
            item for item in compatibility["versions"]
            if item["version"] == target["version"]
        ),
        None,
    )
    if local_config:
        source_revision = target.get("source_revision")
        if source_revision is None:
            version_state = {
                "state": "unknown",
                "reason": "local target source revision is unknown",
            }
        elif not any(
            item["source_revision"] == source_revision
            for item in compatibility["versions"]
        ):
            version_state = {
                "state": "unknown",
                "reason": "operator-declared source revision has no confirmed tap contract",
            }
        else:
            version_state = {
                "state": "unknown",
                "reason": "operator-declared source revision is unverified",
            }
    elif matching_version is None:
        supported = ", ".join(item["version"] for item in compatibility["versions"])
        version_state = {
            "state": "incompatible",
            "reason": f"version {target['version']} is unsupported; expected {supported}",
        }
    elif matching_version["source_revision"] != target["source_revision"]:
        version_state = {
            "state": "incompatible",
            "reason": (
                f"source revision {target['source_revision']} does not match "
                f"{matching_version['source_revision']}"
            ),
        }
    else:
        version_state = {"state": "confirmed", "reason": "exact release contract matches"}

    if target["mode"] not in compatibility["target_modes"] and not (
        local_config and definition.get("collection_mode") == "patched-node"
    ):
        modes = ", ".join(compatibility["target_modes"])
        mode_state = {
            "state": "incompatible",
            "reason": f"target mode {target['mode']} is unsupported; expected {modes}",
        }
    elif local_config and target["mode"] not in compatibility["target_modes"]:
        mode_state = {
            "state": "unknown",
            "reason": "verified local instrumentation is not catalog-qualified",
        }
    else:
        mode_state = {"state": "confirmed", "reason": "target mode matches"}

    incompatible = next(
        (
            state
            for state in (version_state, mode_state)
            if state["state"] == "incompatible"
        ),
        None,
    )
    if incompatible is not None:
        return incompatible, capability
    if version_state["state"] == "unknown" or mode_state["state"] == "unknown":
        reasons = [
            state["reason"]
            for state in (version_state, mode_state)
            if state["state"] == "unknown"
        ]
        return {"state": "unknown", "reason": "; ".join(reasons)}, capability
    return {"state": "confirmed", "reason": "exact target contract matches"}, capability


def resolve_measurements(
    scenario: Any,
    *,
    target_identity: dict[str, Any],
    capabilities: Iterable[str],
    run_overrides: Iterable[dict[str, Any]] | None = None,
    experimental_consent: dict[str, Any] | None = None,
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

    if experimental_consent is None:
        consented: set[str] = set()
        consent_digest = None
    elif (
        not isinstance(experimental_consent, dict)
        or set(experimental_consent) != {"tap_ids", "context_digest"}
        or not isinstance(experimental_consent.get("tap_ids"), list)
        or not isinstance(experimental_consent.get("context_digest"), str)
        or not _DIGEST.fullmatch(experimental_consent["context_digest"])
    ):
        consented = set()
        consent_digest = None
    else:
        consented = {str(item) for item in experimental_consent["tap_ids"]}
        consent_digest = experimental_consent["context_digest"]
    if (
        experimental_consent is not None
        and (
            consent_digest is None
            or len(consented) != len(experimental_consent.get("tap_ids", []))
            or any(not _IDENTIFIER.fullmatch(item) for item in consented)
            or len(consented) > 64
        )
    ):
        raise MeasurementResolutionError(
            "experimental measurement consent is invalid",
            resolution={
                "target_identity": target,
                "profile": profile_meta,
                "requested": [],
                "resolved": [],
                "skipped": [],
                "incompatible": [],
                "unavailable": [],
                "experimental": [],
                "disabled": [],
            },
        )
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
        "unavailable": [],
        "experimental": [],
        "disabled": [],
    }
    capability_set = {str(item) for item in capabilities}
    unknown_enabled: list[dict[str, Any]] = []
    available_by_id = {}
    for record in list_definitions("measurements"):
        compatibility, capability = _measurement_classification(
            record.data, target=target, capabilities=capability_set
        )
        if compatibility["state"] != "incompatible" and capability["state"] != "unavailable":
            available_by_id[record.definition_id] = {
                "id": record.definition_id,
                "definition_digest": _definition_digest(record),
                "definition": record.data,
                "compatibility": compatibility,
                "capability": capability,
            }
    requested_order = [
        measurement_id
        for measurement_id in ordered
        if measurement_id in available_by_id
    ]
    resolution["available"] = [
        available_by_id[measurement_id]
        for measurement_id in (
            requested_order
            + sorted(set(available_by_id).difference(requested_order))
        )
    ]
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
        compatibility, capability = _measurement_classification(
            record.data, target=target, capabilities=capability_set
        )
        reason = None
        destination = None
        if compatibility["state"] == "incompatible":
            reason = compatibility["reason"]
            destination = "incompatible"
        elif capability["state"] == "unavailable":
            reason = capability["reason"]
            destination = (
                "unavailable"
                if target.get("identity_kind") == "operator-local-config"
                else "incompatible"
            )
        if (
            reason is None
            and selection["threshold_gate"]["enabled"]
            and not record.data["threshold_gate"]["supported"]
        ):
            reason = "does not support threshold gating"
            destination = "incompatible"
        if reason:
            entry = {
                "id": selection["id"],
                "source": selection["source"],
                "definition_digest": _definition_digest(record),
                "reason": reason,
                "compatibility": compatibility,
                "capability": capability,
            }
            if selection["source"] == "implicit-default":
                resolution["skipped"].append(entry)
            if destination == "unavailable":
                resolution["unavailable"].append(entry)
            elif selection["source"] != "implicit-default":
                resolution["incompatible"].append(entry)
            continue
        experimental = (
            compatibility["state"] == "unknown" or capability["state"] == "unknown"
        )
        if experimental and selection["source"] != "implicit-default":
            unknown_enabled.append(
                {
                    "id": selection["id"],
                    "definition_digest": _definition_digest(record),
                }
            )
        if experimental and selection["id"] not in consented:
            entry = {
                "id": selection["id"],
                "source": selection["source"],
                "definition_digest": _definition_digest(record),
                "reason": "unknown tap compatibility requires experimental consent",
                "compatibility": compatibility,
                "capability": capability,
            }
            resolution["experimental"].append(entry)
            if selection["source"] == "implicit-default":
                resolution["skipped"].append(entry)
            continue
        resolution["resolved"].append(
            {
                **selection,
                "definition_digest": _definition_digest(record),
                "definition": record.data,
                "compatibility": compatibility,
                "capability": capability,
                "experimental_consent": experimental,
            }
        )

    expected_tap_ids = sorted(item["id"] for item in unknown_enabled)
    consent_target = {
        field: target.get(field)
        for field in (
            "implementation",
            "version",
            "source_revision",
            "identity_kind",
            "image_config_id",
            "executable_digest",
            "platform",
            "operator_registration_id",
            "operator_overlay_revision",
            "controller_installation_id",
            "verified_instrumentation",
            "known_incompatible_measurements",
        )
    }
    consent_definition_digests = {
        item["id"]: item["definition_digest"] for item in unknown_enabled
    }
    consent_context = {
        "tap_ids": expected_tap_ids,
        "target_identity": consent_target,
        "definition_digests": consent_definition_digests,
    }
    expected_digest = "sha256:" + hashlib.sha256(
        json.dumps(
            consent_context, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    expected_consent = {
        "tap_ids": expected_tap_ids,
        "context_digest": expected_digest,
    }
    consent_valid = (
        bool(expected_tap_ids)
        and consented == set(expected_tap_ids)
        and consent_digest == expected_digest
    )
    if expected_tap_ids:
        resolution["experimental_consent_required"] = expected_consent
    if not consent_valid:
        tentatively_resolved = [
            item
            for item in resolution["resolved"]
            if item.get("experimental_consent")
        ]
        resolution["resolved"] = [
            item
            for item in resolution["resolved"]
            if not item.get("experimental_consent")
        ]
        present = {item["id"] for item in resolution["experimental"]}
        for item in tentatively_resolved:
            if item["id"] not in present:
                resolution["experimental"].append(
                    {
                        "id": item["id"],
                        "source": item["source"],
                        "definition_digest": item["definition_digest"],
                        "reason": "unknown tap compatibility requires experimental consent",
                        "compatibility": item["compatibility"],
                        "capability": item["capability"],
                    }
                )
    resolved_experimental = sorted(
        item["id"] for item in resolution["resolved"] if item["experimental_consent"]
    )
    classified_ids = {
        item["id"]
        for bucket in (
            resolution["experimental"],
            resolution["unavailable"],
            resolution["incompatible"],
        )
        for item in bucket
        if item.get("id")
    }
    unused_consent = sorted(
        consented - set(resolved_experimental) - classified_ids
    )
    if unused_consent:
        resolution["incompatible"].append(
            {
                "id": unused_consent[0],
                "source": "experimental-consent",
                "reason": "experimental consent does not match an enabled unknown tap",
            }
        )
    if resolved_experimental:
        resolution["experimental_consent"] = {
            **expected_consent,
            "target_identity": consent_target,
            "definition_digests": consent_definition_digests,
        }

    if resolution["incompatible"]:
        first = resolution["incompatible"][0]
        raise MeasurementResolutionError(
            f"measurement {first['id'] or 'selection'} is incompatible: {first['reason']}",
            resolution=resolution,
        )
    explicit_experimental = [
        item for item in resolution["experimental"] if item["source"] != "implicit-default"
    ]
    if explicit_experimental:
        first = explicit_experimental[0]
        message = (
            "experimental consent context is stale"
            if experimental_consent is not None
            else f"measurement {first['id']} requires experimental consent"
        )
        raise MeasurementResolutionError(
            message,
            resolution=resolution,
        )
    explicit_unavailable = [
        item for item in resolution["unavailable"] if item["source"] != "implicit-default"
    ]
    if explicit_unavailable:
        first = explicit_unavailable[0]
        raise MeasurementResolutionError(
            f"measurement {first['id']} is unavailable: {first['reason']}",
            resolution=resolution,
        )
    return resolution
