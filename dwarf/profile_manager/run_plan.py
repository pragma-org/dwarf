"""Resolve bounded dashboard input into an evidence-ready local run plan."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, replace
from typing import Any

from profile_manager import scenario as scenario_module
from profile_manager.data.scenarios import _scenarios_dir
from profile_manager.deployment_versions import (
    DeploymentVersionGateError,
    build_deployment_version_preview,
    build_measurement_target_identity,
    enforce_deployment_version_gate,
)
from profile_manager.measurement_execution import (
    AMARU_STOCK_CAPABILITIES,
    CARDANO_PATCHED_CAPABILITIES,
    CARDANO_STOCK_CAPABILITIES,
)
from profile_manager.measurement_resolution import (
    MeasurementResolutionError,
    resolve_measurements,
)
from profile_manager.profiles import find_profile, versioned_substrate_for_profile


_ID = re.compile(r"^[a-z][a-z0-9-]{0,127}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_-]{0,63}$")
_DECIMAL_SEED = re.compile(r"^(0|[1-9][0-9]{0,19})$")
_HEX_SEED = re.compile(r"^0[xX][0-9a-fA-F]{1,16}$")
_MAX_SEED = (1 << 64) - 1
_ALLOWED_FIELDS = {
    "scenario_id",
    "profile_id",
    "runtime",
    "version_policy",
    "cardano_version",
    "amaru_version",
    "compatibility_pair",
    "measurement_profile",
    "acknowledge_unknown_versions",
    "execution",
    "seed",
}


class RunPlanError(ValueError):
    def __init__(self, message: str, *, field: str = "request", details: Any = None):
        super().__init__(message)
        self.field = field
        self.details = details

    def as_dict(self) -> dict[str, Any]:
        return {"field": self.field, "message": str(self), "details": self.details}


def _optional_identifier(value: Any, *, field: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise RunPlanError(f"{field} must be a catalog identifier", field=field)
    return value


def _optional_version(value: Any, *, field: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        raise RunPlanError(f"{field} must be a bounded version label", field=field)
    return value


def _optional_seed(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise RunPlanError(
            "seed must be an unsigned decimal integer or 0x hexadecimal value",
            field="seed",
        )
    text = str(value)
    if not (_DECIMAL_SEED.fullmatch(text) or _HEX_SEED.fullmatch(text)):
        raise RunPlanError(
            "seed must be an unsigned decimal integer or 0x hexadecimal value",
            field="seed",
        )
    if int(text, 0) > _MAX_SEED:
        raise RunPlanError("seed must fit in an unsigned 64-bit integer", field="seed")
    return text


@dataclass(frozen=True)
class RunPlanRequest:
    scenario_id: str
    profile_id: str | None = None
    runtime: str | None = None
    version_policy: str | None = None
    cardano_version: str | None = None
    amaru_version: str | None = None
    compatibility_pair: str | None = None
    measurement_profile: str | None = None
    acknowledge_unknown_versions: bool = False
    execution: str = "local"
    seed: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.scenario_id, str) or not _ID.fullmatch(self.scenario_id):
            raise RunPlanError("scenario_id must be a catalog identifier", field="scenario_id")
        for field in ("profile_id", "compatibility_pair"):
            value = getattr(self, field)
            if value is not None and not _ID.fullmatch(value):
                raise RunPlanError(f"{field} must be a catalog identifier", field=field)
        if self.measurement_profile is not None and self.measurement_profile != "none":
            if not _ID.fullmatch(self.measurement_profile):
                raise RunPlanError(
                    "measurement_profile must be a catalog identifier or none",
                    field="measurement_profile",
                )
        if self.runtime is not None and self.runtime not in {"library", "devnet"}:
            raise RunPlanError("runtime must be library or devnet", field="runtime")
        if self.version_policy is not None and self.version_policy not in {
            "latest-confirmed",
            "latest-stable",
            "exact",
        }:
            raise RunPlanError(
                "version_policy must be latest-confirmed, latest-stable, or exact",
                field="version_policy",
            )
        for field in ("cardano_version", "amaru_version"):
            value = getattr(self, field)
            if value is not None and not _VERSION.fullmatch(value):
                raise RunPlanError(f"{field} must be a bounded version label", field=field)
        if not isinstance(self.acknowledge_unknown_versions, bool):
            raise RunPlanError(
                "acknowledge_unknown_versions must be boolean",
                field="acknowledge_unknown_versions",
            )
        if self.execution not in {"local", "github-actions", "antithesis"}:
            raise RunPlanError(
                "execution must be local, github-actions, or antithesis",
                field="execution",
            )
        normalized_seed = _optional_seed(self.seed)
        if normalized_seed != self.seed:
            object.__setattr__(self, "seed", normalized_seed)

    @classmethod
    def from_mapping(cls, value: Any) -> "RunPlanRequest":
        if not isinstance(value, dict):
            raise RunPlanError("request body must be an object")
        unknown = sorted(set(value) - _ALLOWED_FIELDS)
        if unknown:
            raise RunPlanError(
                f"unsupported request fields: {', '.join(unknown)}", field="request"
            )
        if len(json.dumps(value)) > 4096:
            raise RunPlanError("request body is too large")
        return cls(
            scenario_id=_optional_identifier(
                value.get("scenario_id"), field="scenario_id"
            )
            or "",
            profile_id=_optional_identifier(value.get("profile_id"), field="profile_id"),
            runtime=value.get("runtime"),
            version_policy=value.get("version_policy"),
            cardano_version=_optional_version(
                value.get("cardano_version"), field="cardano_version"
            ),
            amaru_version=_optional_version(
                value.get("amaru_version"), field="amaru_version"
            ),
            compatibility_pair=_optional_identifier(
                value.get("compatibility_pair"), field="compatibility_pair"
            ),
            measurement_profile=(
                "none"
                if value.get("measurement_profile") == "none"
                else _optional_identifier(
                    value.get("measurement_profile"), field="measurement_profile"
                )
            ),
            acknowledge_unknown_versions=value.get(
                "acknowledge_unknown_versions", False
            ),
            execution=value.get("execution", "local"),
            seed=_optional_seed(value.get("seed")),
        )


def _load_catalog_scenario(scenario_id: str):
    path = _scenarios_dir() / f"{scenario_id}.yaml"
    if path.parent != _scenarios_dir() or not path.is_file():
        raise RunPlanError("scenario is not in the active catalog", field="scenario_id")
    try:
        scenario = scenario_module.load_scenario(path)
    except (OSError, scenario_module.ScenarioValidationError) as exc:
        raise RunPlanError(str(exc), field="scenario_id") from exc
    if scenario.id != scenario_id:
        raise RunPlanError("scenario file and catalog identifier do not match", field="scenario_id")
    validation = scenario_module.semantic_validate_scenario(path)
    if validation["errors"]:
        raise RunPlanError(
            "; ".join(validation["errors"]),
            field="scenario_id",
            details=validation["errors"],
        )
    return scenario, validation["warnings"]


def _profile_supports(profile, implementation: str) -> bool:
    if implementation == "amaru":
        return profile.amaru_node_count > 0
    if implementation == "cardano-node":
        return profile.node_count > 0
    return False


def _primitive_inventory(scenario) -> tuple[list[dict[str, Any]], list[str]]:
    sections = ("setup", "load", "faults", "probes", "assertions", "teardown")
    cells: list[dict[str, Any]] = []
    names: list[str] = []

    def add(scope: str, owner: Any) -> None:
        for section in sections:
            primitives = [ref.primitive for ref in getattr(owner, section)]
            if primitives:
                cells.append({"scope": scope, "section": section, "primitives": primitives})
                names.extend(primitives)

    if scenario.phases:
        for phase in scenario.phases:
            add(phase.id, phase)
    else:
        add("scenario", scenario)
    return cells, list(dict.fromkeys(names))


def _compact_release(release: dict[str, Any]) -> dict[str, Any]:
    artifact = next(
        (
            item
            for item in release.get("artifacts") or []
            if item.get("kind") == "oci" and item.get("availability") == "available"
        ),
        None,
    )
    return {
        "implementation": release.get("implementation"),
        "version": release.get("version"),
        "source_revision": release.get("source_revision"),
        "image_reference": artifact.get("reference") if artifact else None,
        "image_digest": artifact.get("digest") if artifact else None,
    }


def _measurement_context(scenario, profile, preview) -> dict[str, Any]:
    if profile is None or preview is None:
        return {
            "profile": None,
            "requested": [],
            "resolved": [],
            "skipped": [],
            "incompatible": [],
            "disabled": [],
        }
    implementation = scenario.target["implementation"]
    mode = profile.measurement_target_mode
    image_reference = image_digest = executable_digest = None
    if mode == "patched":
        substrate = versioned_substrate_for_profile(profile, preview)
        node = next(
            (
                item
                for item in substrate["nodes"]
                if item.get("impl") == implementation and not item.get("supporting")
            ),
            None,
        )
        if node is None:
            raise RunPlanError("patched measurement target is unavailable", field="measurements")
        image_reference = node.get("image")
        image_digest = node.get("image_digest")
        executable_digest = node.get("executable_digest")
    try:
        identity = build_measurement_target_identity(
            preview,
            implementation=implementation,
            mode=mode,
            image_reference=image_reference,
            image_digest=image_digest,
            executable_digest=executable_digest,
        )
        if implementation == "amaru":
            capabilities = AMARU_STOCK_CAPABILITIES
        elif mode == "patched":
            capabilities = CARDANO_PATCHED_CAPABILITIES
        else:
            capabilities = CARDANO_STOCK_CAPABILITIES
        return resolve_measurements(
            scenario, target_identity=identity, capabilities=capabilities
        )
    except (DeploymentVersionGateError, MeasurementResolutionError, ValueError) as exc:
        raise RunPlanError(str(exc), field="measurements") from exc


def resolve_run_plan(request: RunPlanRequest | dict[str, Any]) -> dict[str, Any]:
    if isinstance(request, dict):
        request = RunPlanRequest.from_mapping(request)
    if not isinstance(request, RunPlanRequest):
        raise RunPlanError("request must be a RunPlanRequest")

    scenario, warnings = _load_catalog_scenario(request.scenario_id)
    if request.runtime is not None and request.runtime != scenario.runtime:
        raise RunPlanError(
            f"scenario runtime is {scenario.runtime}, not {request.runtime}", field="runtime"
        )

    topology_id = scenario_module._attached_topology_id(scenario)
    selected_profile_id = request.profile_id or scenario.profile
    if scenario.runtime == "library" and selected_profile_id:
        raise RunPlanError("library scenarios do not use deployment profiles", field="profile_id")
    if topology_id and request.profile_id:
        raise RunPlanError("attached scenarios use their named topology", field="profile_id")

    profile = None
    profile_data = None
    preview = None
    if selected_profile_id:
        try:
            profile = find_profile(selected_profile_id)
        except KeyError as exc:
            raise RunPlanError(str(exc), field="profile_id") from exc
        if not _profile_supports(profile, scenario.target["implementation"]):
            raise RunPlanError(
                f"profile {profile.id} does not contain a {scenario.target['implementation']} target",
                field="profile_id",
            )
        profile_data = asdict(profile)
        if request.version_policy:
            profile_data["version_policy"] = request.version_policy
            profile_data["version_policy_source"] = "explicit"
        if request.cardano_version:
            profile_data["cardano_version"] = request.cardano_version
        if request.amaru_version:
            profile_data["amaru_version"] = request.amaru_version
        if request.compatibility_pair:
            profile_data["compatibility_pair"] = request.compatibility_pair
        try:
            preview = build_deployment_version_preview(profile_data)
            enforce_deployment_version_gate(
                preview,
                acknowledge_unknown=request.acknowledge_unknown_versions,
            )
        except DeploymentVersionGateError as exc:
            raise RunPlanError(str(exc), field="versions") from exc

    effective_target = dict(scenario.target)
    if preview is not None:
        release = (preview.get("resolved") or {}).get(effective_target["implementation"])
        if release:
            effective_target["version"] = release["version"]
    effective_scenario = replace(
        scenario,
        target=effective_target,
        profile=selected_profile_id,
        seed=request.seed if request.seed is not None else scenario.seed,
        measurement_profile=(
            request.measurement_profile
            if request.measurement_profile is not None
            else scenario.measurement_profile
        ),
    )
    measurements = _measurement_context(effective_scenario, profile, preview)
    cells, primitive_names = _primitive_inventory(effective_scenario)
    from profile_manager.run_backends import classify_run_backends

    backends = classify_run_backends(effective_scenario)

    versions = None
    if preview is not None:
        versions = {
            "policy": preview.get("policy"),
            "policy_source": preview.get("policy_source"),
            "scope": preview.get("scope"),
            "status": preview.get("status"),
            "reason": preview.get("reason"),
            "catalog_revision": preview.get("catalog_revision"),
            "pair": preview.get("pair"),
            "unknown_acknowledged": request.acknowledge_unknown_versions,
            "resolved": {
                name: _compact_release(release)
                for name, release in (preview.get("resolved") or {}).items()
            },
            "supporting": {
                name: _compact_release(release)
                for name, release in (preview.get("supporting") or {}).items()
            },
        }

    required_checks = ["framework"]
    if topology_id:
        required_checks.append(f"topology:{topology_id}")
    elif selected_profile_id:
        required_checks.extend([f"profile:{selected_profile_id}", "deployed-version-identity"])

    return {
        "schema_version": 1,
        "request": asdict(request),
        "scenario": {
            "id": scenario.id,
            "title": scenario.title,
            "runtime": scenario.runtime,
            "target": effective_target,
            "digest": "sha256:" + hashlib.sha256(scenario.raw_bytes).hexdigest(),
            "seed": effective_scenario.seed,
            "seed_source": (
                "operator-override" if request.seed is not None else "scenario-default"
            ),
            "iterations": scenario.iterations,
            "iterations_editable": False,
            "iterations_reason": (
                "The scenario-level iterations field is recorded but not consumed "
                "by the current execution engine."
            ),
        },
        "profile": (
            {
                "id": profile.id,
                "label": profile.label,
                "snapshot": profile_data,
                "digest": "sha256:"
                + hashlib.sha256(
                    json.dumps(profile_data, sort_keys=True).encode("utf-8")
                ).hexdigest(),
            }
            if profile is not None
            else None
        ),
        "versions": versions,
        "measurements": measurements,
        "backends": backends,
        "primitive_cells": cells,
        "primitive_names": primitive_names,
        "readiness": {
            "mixed_topology_required": topology_id == "cardano_amaru",
            "topology_id": topology_id,
            "profile_required": bool(selected_profile_id),
            "profile_id": selected_profile_id,
            "scenario_composes_substrate": scenario.substrate is not None,
            "required_checks": required_checks,
        },
        "warnings": list(warnings),
    }


def digest_run_plan(plan: dict[str, Any]) -> str:
    """Return the stable digest used to detect a changed catalog selection."""
    body = json.dumps(plan, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(body).hexdigest()
