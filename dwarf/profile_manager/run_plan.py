"""Resolve bounded dashboard input into an evidence-ready local run plan."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path
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
    AMARU_JSON_TRACE_CAPABILITIES,
    AMARU_PATCHED_CAPABILITIES,
    AMARU_STOCK_CAPABILITIES,
    CARDANO_PATCHED_CAPABILITIES,
    CARDANO_STOCK_CAPABILITIES,
)
from profile_manager.measurement_resolution import (
    MeasurementResolutionError,
    resolve_measurements,
)
from profile_manager.measurement_targets import (
    MeasurementTargetError,
    cbor_conformance_adapter_provenance,
    measurement_target_registry_root,
    plutus_conformance_adapter_provenance,
    resolve_cbor_conformance_adapter,
    resolve_plutus_conformance_adapter,
)
from profile_manager.profiles import (
    desired_topology_from_substrate,
    find_profile,
    operator_preference_scope,
    versioned_substrate_for_profile,
)


_ID = re.compile(r"^[a-z][a-z0-9-]{0,127}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_-]{0,63}$")
_DECIMAL_SEED = re.compile(r"^(0|[1-9][0-9]{0,19})$")
_HEX_SEED = re.compile(r"^0[xX][0-9a-fA-F]{1,16}$")
_MAX_SEED = (1 << 64) - 1
_PLATFORM = re.compile(r"^linux/(?:amd64|arm64)$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_ARCH_ALIASES = {"aarch64": "arm64", "arm64": "arm64", "x86_64": "amd64", "amd64": "amd64"}
_SUPPORTED_HOST_OSES = {"darwin", "linux"}
_CREDENTIAL_FOR_EXECUTION = {
    "github-actions": "github-actions",
    "antithesis": "antithesis",
}
_EXPLICIT_NO_MEASUREMENTS = {
    "profile": {"id": "none", "source": "explicit"},
    "requested": [],
    "resolved": [],
    "skipped": [],
    "incompatible": [],
    "disabled": [],
    "target_identity": {},
}


def explicit_no_measurements_context() -> dict[str, Any]:
    """Return the one immutable launch representation for explicit no-measurements."""
    return json.loads(json.dumps(_EXPLICIT_NO_MEASUREMENTS))


def is_explicit_no_measurements_context(value: Any) -> bool:
    """Accept only the exact producer-owned no-measurements contract."""
    return value == _EXPLICIT_NO_MEASUREMENTS
_EXACT_TARGET_IDENTITY_FIELDS = (
    "implementation",
    "version",
    "source_revision",
    "mode",
    "image_reference",
    "source_image_reference",
    "image_digest",
    "image_manifest_digest",
    "image_config_digest",
    "executable_digest",
    "patch_set_sha256",
    "build_result_sha256",
    "runtime_probe_log_sha256",
    "qualification_image_id",
    "transfer_source_installation_id",
    "transfer_manifest_sha256",
    "version_catalog_revision",
    "identity_kind",
    "image_config_id",
    "platform",
    "operator_registration_id",
    "operator_overlay_revision",
    "controller_installation_id",
    "experimental_instrumentation",
)
_EXACT_TARGET_SEMANTIC_FIELDS = (
    "implementation",
    "version",
    "source_revision",
    "mode",
    "patch_set_sha256",
)
_ALLOWED_FIELDS = {
    "scenario_id",
    "profile_id",
    "runtime",
    "version_policy",
    "cardano_version",
    "amaru_version",
    "compatibility_pair",
    "operator_registration_id",
    "measurement_profile",
    "measurement_overrides",
    "experimental_measurement_consent",
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


def _measurement_overrides(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 64:
        raise RunPlanError(
            "measurement_overrides must be a list of at most 64 tap selections",
            field="measurement_overrides",
        )
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"id", "enabled"}:
            raise RunPlanError(
                "each measurement override must contain only id and enabled",
                field="measurement_overrides",
            )
        measurement_id = item.get("id")
        enabled = item.get("enabled")
        if not isinstance(measurement_id, str) or not _ID.fullmatch(measurement_id):
            raise RunPlanError(
                "measurement override id must be a catalog identifier",
                field="measurement_overrides",
            )
        if measurement_id in seen:
            raise RunPlanError(
                f"duplicate measurement override {measurement_id}",
                field="measurement_overrides",
            )
        if not isinstance(enabled, bool):
            raise RunPlanError(
                f"measurement override {measurement_id} enabled must be boolean",
                field="measurement_overrides",
            )
        seen.add(measurement_id)
        result.append({"id": measurement_id, "enabled": enabled})
    return result


def _experimental_measurement_consent(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"tap_ids", "context_digest"}:
        raise RunPlanError(
            "experimental measurement consent must contain tap_ids and context_digest",
            field="experimental_measurement_consent",
        )
    tap_ids = value.get("tap_ids")
    digest = value.get("context_digest")
    if (
        not isinstance(tap_ids, list)
        or not tap_ids
        or len(tap_ids) > 64
        or len(set(tap_ids)) != len(tap_ids)
        or any(not isinstance(item, str) or not _ID.fullmatch(item) for item in tap_ids)
        or not isinstance(digest, str)
        or not _DIGEST.fullmatch(digest)
    ):
        raise RunPlanError(
            "experimental measurement consent is invalid",
            field="experimental_measurement_consent",
        )
    return {"tap_ids": sorted(tap_ids), "context_digest": digest}


@dataclass(frozen=True)
class RunPlanRequest:
    scenario_id: str
    profile_id: str | None = None
    runtime: str | None = None
    version_policy: str | None = None
    cardano_version: str | None = None
    amaru_version: str | None = None
    compatibility_pair: str | None = None
    operator_registration_id: str | None = None
    measurement_profile: str | None = None
    measurement_overrides: list[dict[str, Any]] | None = None
    experimental_measurement_consent: dict[str, Any] | None = None
    acknowledge_unknown_versions: bool = False
    execution: str = "local"
    seed: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.scenario_id, str) or not _ID.fullmatch(self.scenario_id):
            raise RunPlanError("scenario_id must be a catalog identifier", field="scenario_id")
        for field in ("profile_id", "compatibility_pair", "operator_registration_id"):
            value = getattr(self, field)
            if value is not None and not _ID.fullmatch(value):
                raise RunPlanError(f"{field} must be a catalog identifier", field=field)
        if self.measurement_profile is not None and self.measurement_profile != "none":
            if not _ID.fullmatch(self.measurement_profile):
                raise RunPlanError(
                    "measurement_profile must be a catalog identifier or none",
                    field="measurement_profile",
                )
        normalized_overrides = _measurement_overrides(self.measurement_overrides)
        if normalized_overrides != self.measurement_overrides:
            object.__setattr__(self, "measurement_overrides", normalized_overrides)
        normalized_consent = _experimental_measurement_consent(
            self.experimental_measurement_consent
        )
        if normalized_consent != self.experimental_measurement_consent:
            object.__setattr__(
                self, "experimental_measurement_consent", normalized_consent
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
            operator_registration_id=_optional_identifier(
                value.get("operator_registration_id"),
                field="operator_registration_id",
            ),
            measurement_profile=(
                "none"
                if value.get("measurement_profile") == "none"
                else _optional_identifier(
                    value.get("measurement_profile"), field="measurement_profile"
                )
            ),
            measurement_overrides=_measurement_overrides(
                value.get("measurement_overrides")
            ),
            experimental_measurement_consent=_experimental_measurement_consent(
                value.get("experimental_measurement_consent")
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


def _conformance_requirements(scenario) -> list[dict[str, Any]]:
    """Return immutable logical adapter identities without host paths."""
    sections = ("setup", "load", "faults", "probes", "assertions", "teardown")
    owners = scenario.phases or (scenario,)
    requirements: list[dict[str, Any]] = []
    for owner_index, owner in enumerate(owners):
        for section in sections:
            for ref_index, ref in enumerate(getattr(owner, section)):
                if ref.primitive == "runtime_version_pinned_plutus_conformance":
                    adapters = ref.params.get("adapters") or {}
                    if not isinstance(adapters, dict):
                        continue
                    for implementation, value in adapters.items():
                        if not isinstance(value, dict) or not value.get("adapter_set_sha256"):
                            continue
                        requirements.append(
                            {
                                "adapter_family": "plutus",
                                "implementation": str(implementation),
                                "source_revision": str(value.get("source_revision") or ""),
                                "adapter_set_sha256": str(value.get("adapter_set_sha256") or ""),
                                "location": (
                                    ["phases", owner_index, section, ref_index, "adapters", implementation]
                                    if scenario.phases
                                    else [section, ref_index, "adapters", implementation]
                                ),
                            }
                        )
                elif (
                    ref.primitive == "runtime_version_pinned_cbor_conformance"
                    and ref.params.get("adapter_set_sha256")
                ):
                    requirements.append(
                        {
                            "adapter_family": "cbor",
                            "implementation": str(ref.params.get("implementation") or ""),
                            "source_revision": str(ref.params.get("source_revision") or ""),
                            "adapter_set_sha256": str(ref.params.get("adapter_set_sha256") or ""),
                            "location": (
                                ["phases", owner_index, section, ref_index]
                                if scenario.phases
                                else [section, ref_index]
                            ),
                        }
                    )
    return sorted(
        requirements,
        key=lambda item: (
            item["implementation"],
            item["source_revision"],
            item["adapter_set_sha256"],
            item["adapter_family"],
            json.dumps(item["location"], separators=(",", ":")),
        ),
    )


def _conformance_bindings(
    scenario, *, registry_root: str | Path, host_os: str, host_arch: str
) -> list[dict[str, Any]]:
    bindings = []
    for requirement in _conformance_requirements(scenario):
        declared = {
            key: requirement[key]
            for key in ("implementation", "source_revision", "adapter_set_sha256")
        }
        try:
            resolver = (
                resolve_cbor_conformance_adapter
                if requirement["adapter_family"] == "cbor"
                else resolve_plutus_conformance_adapter
            )
            record = resolver(
                **declared,
                registry_root=registry_root,
                host_os=host_os,
                host_arch=host_arch,
            )
        except MeasurementTargetError:
            resolved = None
        else:
            provenance = (
                cbor_conformance_adapter_provenance
                if requirement["adapter_family"] == "cbor"
                else plutus_conformance_adapter_provenance
            )
            resolved = provenance(record)
        bindings.append(
            {
                "kind": "resolved-registry",
                "adapter_family": requirement["adapter_family"],
                "location": requirement["location"],
                "declared": declared,
                "resolved": resolved,
            }
        )
    return bindings


def _target_verifiers(scenario):
    sections = ("setup", "load", "faults", "probes", "assertions", "teardown")
    if scenario.phases:
        for phase_index, phase in enumerate(scenario.phases):
            for section in sections:
                for index, ref in enumerate(getattr(phase, section)):
                    if ref.primitive == "runtime_verify_exact_target":
                        yield ["phases", phase_index, section, index], dict(ref.params)
        return
    for section in sections:
        for index, ref in enumerate(getattr(scenario, section)):
            if ref.primitive == "runtime_verify_exact_target":
                yield [section, index], dict(ref.params)


def _scenario_target_bindings(scenario, target_identity: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate frozen pins or bind an explicitly portable verifier to this plan."""

    bindings: list[dict[str, Any]] = []
    for location, params in _target_verifiers(scenario):
        kind = str(params.get("identity_binding") or "frozen")
        if kind not in {"frozen", "resolved-plan"}:
            raise RunPlanError(
                f"scenario {scenario.id} has unsupported target identity binding {kind}",
                field="scenario_id",
            )
        declared = {
            field: params[field]
            for field in _EXACT_TARGET_IDENTITY_FIELDS
            if field in params
        }
        semantic_mismatches = [
            field
            for field in _EXACT_TARGET_SEMANTIC_FIELDS
            if field in declared and target_identity.get(field) != declared[field]
        ]
        if semantic_mismatches:
            raise RunPlanError(
                f"scenario {scenario.id} target constraints are incompatible with the "
                f"selected target: {', '.join(sorted(semantic_mismatches))}",
                field="scenario_id",
                details={"binding": kind, "mismatches": sorted(semantic_mismatches)},
            )

        if kind == "frozen":
            mismatches = [
                field
                for field, expected in declared.items()
                if target_identity.get(field) != expected
            ]
            if mismatches:
                raise RunPlanError(
                    f"scenario {scenario.id} frozen target identity is incompatible with "
                    f"the selected qualified target: {', '.join(sorted(mismatches))}. "
                    "Select a matching historical target or a resolved-plan scenario.",
                    field="scenario_id",
                    details={"binding": "frozen", "mismatches": sorted(mismatches)},
                )
            bindings.append(
                {
                    "kind": "frozen",
                    "location": location,
                    "declared": declared,
                }
            )
            continue

        forbidden = sorted(
            set(declared)
            - set(_EXACT_TARGET_SEMANTIC_FIELDS)
        )
        if forbidden:
            raise RunPlanError(
                f"scenario {scenario.id} resolved-plan binding also freezes artifact "
                f"fields: {', '.join(forbidden)}",
                field="scenario_id",
                details={"binding": "resolved-plan", "frozen_artifacts": forbidden},
            )
        if target_identity.get("identity_kind") == "operator-local-config":
            required = {
                "implementation",
                "version",
                "mode",
                "identity_kind",
                "image_reference",
                "image_config_id",
                "executable_digest",
                "platform",
                "operator_registration_id",
                "operator_overlay_revision",
                "controller_installation_id",
            }
        else:
            required = {
                "implementation",
                "version",
                "source_revision",
                "mode",
                "image_reference",
                "image_digest",
            }
        if (
            target_identity.get("mode") == "patched"
            and target_identity.get("identity_kind") != "operator-local-config"
        ):
            required.update(
                {
                    "executable_digest",
                    "patch_set_sha256",
                    "build_result_sha256",
                    "runtime_probe_log_sha256",
                }
            )
        missing = sorted(
            field for field in required if target_identity.get(field) in (None, "")
        )
        if missing:
            raise RunPlanError(
                f"scenario {scenario.id} cannot bind an incomplete resolved target: "
                f"{', '.join(missing)}",
                field="scenario_id",
                details={"binding": "resolved-plan", "missing": missing},
            )
        if target_identity.get("source_image_reference"):
            imported_required = {
                "image_manifest_digest",
                "image_config_digest",
                "qualification_image_id",
                "transfer_source_installation_id",
                "transfer_manifest_sha256",
            }
            imported_missing = sorted(
                field
                for field in imported_required
                if target_identity.get(field) in (None, "")
            )
            if imported_missing:
                raise RunPlanError(
                    f"scenario {scenario.id} cannot bind incomplete imported target "
                    f"provenance: {', '.join(imported_missing)}",
                    field="scenario_id",
                    details={
                        "binding": "resolved-plan",
                        "missing": imported_missing,
                    },
                )
        resolved = {
            field: target_identity[field]
            for field in _EXACT_TARGET_IDENTITY_FIELDS
            if target_identity.get(field) not in (None, "")
        }
        bindings.append(
            {
                "kind": "resolved-plan",
                "location": location,
                "declared": declared,
                "resolved": resolved,
            }
        )
    return bindings


def _scenario_substrate_version_profile(scenario, request) -> dict[str, Any] | None:
    """Project a self-composed portable substrate onto the existing version resolver."""

    substrate = scenario.substrate or {}
    nodes = list(substrate.get("nodes") or [])
    portable = [node for node in nodes if node.get("identity_binding") == "resolved-plan"]
    if not portable:
        return None
    if len(portable) != len(nodes):
        raise RunPlanError(
            f"scenario {scenario.id} mixes frozen and resolved-plan substrate nodes",
            field="scenario_id",
        )
    cardano_count = sum(node.get("impl") == "cardano-node" for node in nodes)
    amaru_count = sum(node.get("impl") == "amaru" for node in nodes)
    network = str(substrate.get("network") or "")
    network_magic = int(network.removeprefix("testnet_")) if network.startswith("testnet_") else 42
    explicit_release_selection = any(
        (request.cardano_version, request.amaru_version, request.compatibility_pair)
    )
    data = {
        "id": f"scenario-{scenario.id}-version-selection",
        "label": f"{scenario.title} version selection",
        "haskell_count": cardano_count,
        "amaru_count": amaru_count,
        "network_magic": network_magic,
        "peer_sharing": False,
        "topology_pattern": "local-mesh",
        "shared_genesis": True,
        "measurement_target_mode": "stock",
        "version_policy": request.version_policy
        or ("exact" if explicit_release_selection else "latest-confirmed"),
        "version_policy_source": (
            "explicit"
            if request.version_policy or explicit_release_selection
            else "implicit-default"
        ),
    }
    if request.cardano_version:
        data["cardano_version"] = request.cardano_version
    if request.amaru_version:
        data["amaru_version"] = request.amaru_version
    if request.compatibility_pair:
        data["compatibility_pair"] = request.compatibility_pair
    return data


def _scenario_substrate_bindings(scenario, preview) -> list[dict[str, Any]]:
    if preview is None:
        return []
    releases = {
        **(preview.get("supporting") or {}),
        **(preview.get("resolved") or {}),
    }
    bindings = []
    for index, node in enumerate((scenario.substrate or {}).get("nodes") or []):
        if node.get("identity_binding") != "resolved-plan":
            continue
        release = releases.get(node["impl"])
        if not isinstance(release, dict):
            raise RunPlanError(
                f"scenario {scenario.id} has no resolved {node['impl']} release for {node['id']}",
                field="versions",
            )
        compact = _compact_release(release)
        required = ("version", "source_revision", "image_reference", "image_digest")
        missing = [field for field in required if compact.get(field) in (None, "")]
        if missing:
            raise RunPlanError(
                f"scenario {scenario.id} resolved {node['impl']} identity is incomplete: "
                + ", ".join(missing),
                field="versions",
            )
        bindings.append(
            {
                "kind": "resolved-plan",
                "location": ["substrate", "nodes", index],
                "declared": {
                    "id": node["id"],
                    "impl": node["impl"],
                    "role": node["role"],
                    "identity_binding": "resolved-plan",
                },
                "resolved": {
                    "version": compact["version"],
                    "source_revision": compact["source_revision"],
                    "image": compact["image_reference"],
                    "image_digest": compact["image_digest"],
                },
            }
        )
    return bindings


def _compact_release(release: dict[str, Any]) -> dict[str, Any]:
    artifact = next(
        (
            item
            for item in release.get("artifacts") or []
            if item.get("kind") == "oci" and item.get("availability") == "available"
        ),
        None,
    )
    operator_registration = (
        artifact.get("operator_registration") if artifact else None
    )
    execution_reference = artifact.get("reference") if artifact else None
    if (
        isinstance(execution_reference, str)
        and "@" not in execution_reference
        and _DIGEST.fullmatch(str(artifact.get("digest") or ""))
    ):
        execution_reference = f"{execution_reference}@{artifact['digest']}"
    if (
        artifact
        and not artifact.get("digest")
        and isinstance(operator_registration, dict)
    ):
        execution_reference = artifact.get("config_image_id")
    return {
        "implementation": release.get("implementation"),
        "version": release.get("version"),
        "source_revision": release.get("source_revision"),
        "image_reference": execution_reference,
        "image_digest": artifact.get("digest") if artifact else None,
        "image_platforms": (
            sorted(artifact["platforms"])
            if artifact
            and isinstance(artifact.get("platforms"), list)
            and all(isinstance(item, str) and _PLATFORM.fullmatch(item) for item in artifact["platforms"])
            else []
        ),
        "image_public": artifact.get("public") is True if artifact else False,
        "image_config_id": artifact.get("config_image_id") if artifact else None,
        "operator_registration": operator_registration,
    }


def _capability_context(value: dict[str, Any] | None) -> dict[str, Any]:
    supplied = dict(value or {})
    host_os = str(
        supplied.get("host_os")
        or os.environ.get("DWARF_HOST_OS")
        or platform.system()
    ).strip().casefold()
    raw_arch = str(
        supplied.get("host_arch")
        or os.environ.get("DWARF_HOST_ARCH")
        or platform.machine()
    ).strip().casefold()
    host_arch = _ARCH_ALIASES.get(raw_arch, raw_arch)
    available = supplied.get("available_image_digests")
    if available is None:
        available = os.environ.get("DWARF_AVAILABLE_IMAGE_DIGESTS", "").split(",")
    credentials = supplied.get("credentials")
    if credentials is None:
        credentials = os.environ.get("DWARF_AVAILABLE_CREDENTIALS", "").split(",")
    return {
        "host_os": host_os,
        "host_arch": host_arch,
        "host_platform": f"{host_os}/{host_arch}",
        "container_platform": f"linux/{host_arch}",
        "available_image_digests": sorted(
            {str(item).strip() for item in available if str(item).strip()}
        ),
        "credentials": sorted(
            {str(item).strip() for item in credentials if str(item).strip()}
        ),
        "measurement_target_registry": Path(
            supplied.get("measurement_target_registry")
            or measurement_target_registry_root()
        ),
        "inspected_images": {
            str(reference): dict(identity)
            for reference, identity in (supplied.get("inspected_images") or {}).items()
            if isinstance(reference, str) and isinstance(identity, dict)
        },
    }


def classify_run_availability(
    plan: dict[str, Any],
    *,
    capability_context: dict[str, Any] | None = None,
    execution: str | None = None,
) -> dict[str, Any]:
    """Classify a resolved plan without probing, pulling, building, or reading secrets."""
    context = _capability_context(capability_context)
    requested_execution = execution or str((plan.get("request") or {}).get("execution") or "local")
    credential = _CREDENTIAL_FOR_EXECUTION.get(requested_execution)
    missing_credentials = (
        [credential] if credential and credential not in context["credentials"] else []
    )

    records: dict[str, dict[str, Any]] = {}
    versions = plan.get("versions") or {}
    for group in ("resolved", "supporting"):
        for implementation, record in (versions.get(group) or {}).items():
            records[str(implementation)] = dict(record)
    identity = (plan.get("measurements") or {}).get("target_identity") or {}
    locally_qualified_digest = None
    if identity.get("implementation") and identity.get("image_digest"):
        implementation = str(identity["implementation"])
        if identity.get("mode") == "patched" and identity.get("image_reference"):
            # A patched identity exists only after the strict local registry
            # resolver has accepted its qualification record.  Runtime use is
            # still independently re-inspected by the controller.
            locally_qualified_digest = str(identity["image_digest"])
        inherited = records.get(implementation, {})
        records[implementation] = {
            **inherited,
            "implementation": implementation,
            "image_reference": identity.get("image_reference"),
            "image_digest": identity.get("image_digest"),
            "image_public": inherited.get("image_public") is True
            and identity.get("mode") == "stock",
        }

    required_images = [
        {
            "implementation": implementation,
            "reference": record.get("image_reference"),
            "digest": record.get("image_digest"),
            "platforms": (
                sorted(record["image_platforms"])
                if isinstance(record.get("image_platforms"), list)
                and all(
                    isinstance(item, str) and _PLATFORM.fullmatch(item)
                    for item in record["image_platforms"]
                )
                else []
            ),
            "public": record.get("image_public") is True,
            "config_image_id": record.get("image_config_id"),
            "operator_registration": record.get("operator_registration"),
        }
        for implementation, record in sorted(records.items())
    ]
    required_digests = sorted(
        str(item["digest"] or item["config_image_id"])
        for item in required_images
        if item.get("digest") or item.get("config_image_id")
    )

    required_adapters = []
    for binding in (plan.get("scenario") or {}).get(
        "required_conformance_adapters", []
    ):
        requirement = binding.get("declared") or {}
        safe = {
            "implementation": str(requirement.get("implementation") or ""),
            "source_revision": str(requirement.get("source_revision") or ""),
            "adapter_set_sha256": str(requirement.get("adapter_set_sha256") or ""),
        }
        try:
            adapter_family = str(binding.get("adapter_family") or "plutus")
            resolver = (
                resolve_cbor_conformance_adapter
                if adapter_family == "cbor"
                else resolve_plutus_conformance_adapter
            )
            record = resolver(
                **safe,
                registry_root=context["measurement_target_registry"],
                host_os="linux",
                host_arch=context["host_arch"],
            )
        except MeasurementTargetError as error:
            required_adapters.append(
                {**safe, "available": False, "reason": str(error)}
            )
        else:
            provenance = (
                cbor_conformance_adapter_provenance
                if adapter_family == "cbor"
                else plutus_conformance_adapter_provenance
            )
            current = provenance(record)
            frozen = binding.get("resolved")
            if not isinstance(frozen, dict):
                required_adapters.append(
                    {
                        **safe,
                        "available": False,
                        "reason": f"run plan must be re-resolved after the qualified {adapter_family} conformance adapter becomes available",
                    }
                )
            elif current != frozen:
                required_adapters.append(
                    {
                        **safe,
                        "available": False,
                        "reason": f"qualified {adapter_family} conformance adapter provenance changed after plan resolution",
                    }
                )
            else:
                required_adapters.append(
                    {**safe, "available": True, **current}
                )
    unavailable_adapters = [
        item for item in required_adapters if item["available"] is not True
    ]

    state = "ready"
    reason = "No additional target image or credential is required."
    if (
        context["host_os"] not in _SUPPORTED_HOST_OSES
        or context["host_arch"] not in {"amd64", "arm64"}
    ):
        state = "unsupported-platform"
        reason = f"Host platform {context['host_platform']} is outside the supported Docker quickstart matrix."
    elif missing_credentials:
        state = "requires-credential"
        reason = f"Execution backend {requested_execution} requires configured credential capability."
    elif unavailable_adapters:
        state = "requires-build"
        reason = str(unavailable_adapters[0]["reason"])
    elif required_images:
        local_images = [
            item for item in required_images if isinstance(item.get("operator_registration"), dict)
        ]
        local_mismatches = []
        local_present = set()
        for item in local_images:
            registration = item.get("operator_registration") or {}
            observed = context["inspected_images"].get(
                str(item.get("reference") or "")
            ) or context["inspected_images"].get(
                str(registration.get("image_reference") or "")
            )
            if observed is None:
                continue
            declared_platforms = item.get("platforms") or []
            if (
                observed.get("config_image_id") == item.get("config_image_id")
                and observed.get("platform") in declared_platforms
                and (
                    (
                        item.get("digest")
                        and str(item.get("reference") or "").endswith(
                            "@" + str(item.get("digest"))
                        )
                    )
                    or (
                        not item.get("digest")
                        and item.get("config_image_id")
                    )
                )
            ):
                local_present.add(str(item.get("implementation")))
            else:
                local_mismatches.append(item)
        incomplete_identity = [
            item
            for item in required_images
            if not item.get("reference")
            or (not item.get("digest") and not item.get("config_image_id"))
        ]
        incompatible = [
            item
            for item in required_images
            if item.get("platforms")
            and context["container_platform"] not in item["platforms"]
        ]
        missing = [
            item
            for item in required_images
            if str(item.get("implementation")) not in local_present
            if item.get("digest") not in context["available_image_digests"]
            and (
                locally_qualified_digest is None
                or item.get("digest") != locally_qualified_digest
            )
        ]
        not_downloadable = [
            item for item in missing
            if (
                not item.get("public")
                or not item.get("platforms")
                or not item.get("digest")
                or not str(item.get("reference") or "").endswith(
                    "@" + str(item.get("digest") or "")
                )
            )
        ]
        if incompatible:
            state = "unsupported-platform"
            reason = f"A required image does not publish {context['container_platform']}."
        elif local_mismatches:
            state = "requires-build"
            reason = "A registered local image identity or platform differs from its inspected registration."
        elif incomplete_identity:
            state = "requires-build"
            reason = "At least one required target lacks an immutable image identity."
        elif not missing:
            reason = "Every required immutable target image is already present, including any non-public target."
        elif not_downloadable:
            state = "requires-build"
            reason = "At least one missing required target is not published as a proven downloadable image for this platform."
        else:
            state = "pullable"
            reason = "Every missing target image is public, immutable, and published for this container platform."

    return {
        "state": state,
        "reason": reason,
        "host_platform": context["host_platform"],
        "container_platform": context["container_platform"],
        "execution": requested_execution,
        "missing_credentials": missing_credentials,
        "required_images": required_images,
        "required_image_digests": required_digests,
        "required_conformance_adapters": required_adapters,
    }


def _measurement_context(
    scenario,
    profile,
    preview,
    *,
    run_overrides: list[dict[str, Any]] | None = None,
    experimental_consent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if profile is None or preview is None:
        return {
            "profile": None,
            "requested": [],
            "resolved": [],
            "skipped": [],
            "incompatible": [],
            "disabled": [],
        }
    if (
        scenario.measurement_profile == "none"
        and not getattr(scenario, "measurements", [])
        and not run_overrides
    ):
        return explicit_no_measurements_context()
    implementation = scenario.target["implementation"]
    mode = profile.measurement_target_mode
    image_reference = image_digest = image_manifest_digest = executable_digest = None
    node = None
    try:
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
                raise RunPlanError(
                    "patched measurement target is unavailable", field="measurements"
                )
            image_reference = node.get("image")
            image_digest = node.get("image_digest")
            image_manifest_digest = node.get("image_manifest_digest")
            executable_digest = node.get("executable_digest")
        identity = build_measurement_target_identity(
            preview,
            implementation=implementation,
            mode=mode,
            image_reference=image_reference,
            image_digest=image_digest,
            image_manifest_digest=image_manifest_digest,
            executable_digest=executable_digest,
        )
        for field in (
            "source_image_reference",
            "image_config_digest",
            "qualification_image_id",
            "patch_set_sha256",
            "build_result_sha256",
            "runtime_probe_log_sha256",
            "transfer_source_installation_id",
            "transfer_manifest_sha256",
        ):
            if node is not None and node.get(field) not in (None, ""):
                identity[field] = node[field]
        if implementation == "amaru" and mode == "patched":
            capabilities = set(AMARU_PATCHED_CAPABILITIES)
        elif implementation == "amaru":
            capabilities = set(AMARU_STOCK_CAPABILITIES)
        elif mode == "patched":
            capabilities = CARDANO_PATCHED_CAPABILITIES
        else:
            capabilities = CARDANO_STOCK_CAPABILITIES
        if implementation == "amaru" and identity.get("experimental_instrumentation") is not None:
            from profile_manager.experimental_instrumentation import CAPABILITIES
            capabilities |= CAPABILITIES
        if implementation == "amaru" and profile.amaru_json_traces is not True:
            capabilities -= AMARU_JSON_TRACE_CAPABILITIES
        return resolve_measurements(
            scenario,
            target_identity=identity,
            capabilities=capabilities,
            run_overrides=run_overrides,
            experimental_consent=experimental_consent,
        )
    except MeasurementResolutionError as exc:
        raise RunPlanError(
            str(exc), field="measurements", details=exc.resolution
        ) from exc
    except (DeploymentVersionGateError, ValueError) as exc:
        raise RunPlanError(str(exc), field="measurements") from exc


def _validate_operator_preference_subject(
    subject: dict[str, Any], versions: dict[str, Any], profile_id: str
) -> None:
    current_target = (versions.get("resolved") or {}).get(subject.get("implementation"))
    if subject.get("scope") == "mixed" and subject.get("mixed_pair") != versions.get("pair"):
        raise RunPlanError(
            "operator preference no longer matches the exact mixed pair",
            field="versions",
        )
    mismatched = (
        subject.get("scope") != versions.get("scope")
        or subject.get("topology_profile_id") != profile_id
        or "sha256:" + str(subject.get("deployment_identity_sha256") or "")
        != versions.get("deployment_identity_sha256")
        or not isinstance(current_target, dict)
        or current_target.get("version") != subject.get("version")
        or current_target.get("image_digest") != subject.get("manifest_digest")
    )
    if mismatched:
        raise RunPlanError(
            "operator preference no longer matches the exact deployment identity",
            field="versions",
        )


def resolve_run_plan(
    request: RunPlanRequest | dict[str, Any],
    *,
    capability_context: dict[str, Any] | None = None,
    request_controller=None,
) -> dict[str, Any]:
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
    operator_preference = None
    if selected_profile_id:
        try:
            profile = find_profile(selected_profile_id)
        except KeyError as exc:
            raise RunPlanError(str(exc), field="profile_id") from exc
        if profile.kes_genesis_override and profile.amaru_node_count > 0:
            raise RunPlanError(
                "short-KES profiles are currently supported only by the "
                "Cardano-only runtime; Amaru/mixed needs a qualified matching "
                "two-client KES contract",
                field="profile_id",
            )
        if not _profile_supports(profile, scenario.target["implementation"]):
            raise RunPlanError(
                f"profile {profile.id} does not contain a {scenario.target['implementation']} target",
                field="profile_id",
            )
        profile_data = asdict(profile)
        explicit_version_selection = any(
            value is not None
            for value in (
                request.version_policy,
                request.cardano_version,
                request.amaru_version,
                request.compatibility_pair,
                request.operator_registration_id,
            )
        )
        selected_operator_registration_id = request.operator_registration_id
        if not explicit_version_selection:
            try:
                from profile_manager.operator_version_decisions import (
                    OperatorVersionDecisionLedger,
                    preferred_decision,
                )

                operator_state = OperatorVersionDecisionLedger().read()
                operator_preference = preferred_decision(
                    operator_state, operator_preference_scope(profile)
                )
            except ValueError as exc:
                raise RunPlanError(
                    f"operator version state is invalid: {exc}", field="versions"
                ) from exc
            if operator_preference is not None:
                subject = operator_preference["decision"]["subject"]
                scenario_digest = hashlib.sha256(scenario.raw_bytes).hexdigest()
                if subject["scenario_id"] != scenario.id:
                    operator_preference = None
                elif subject["scenario_sha256"] != scenario_digest:
                    raise RunPlanError(
                        "active operator preference has a stale scenario revision; "
                        "select an explicit version or clear the preference",
                        field="versions",
                    )
                else:
                    profile_data["version_policy"] = "exact"
                    if subject["scope"] == "mixed":
                        pair = subject.get("mixed_pair")
                        pair_id = pair.get("id") if isinstance(pair, dict) else None
                        if not isinstance(pair_id, str) or not pair_id:
                            raise RunPlanError(
                                "operator preference has no exact mixed compatibility pair",
                                field="versions",
                            )
                        profile_data["compatibility_pair"] = pair_id
                    else:
                        matching = [
                            item
                            for item in operator_state["registrations"]
                            if item["implementation"] == subject["implementation"]
                            and item["version"] == subject["version"]
                            and item["config_image_id"] == subject["config_image_id"]
                            and item.get("manifest_digest")
                            == subject.get("manifest_digest")
                        ]
                        if len(matching) > 1:
                            raise RunPlanError(
                                "operator preference artifact identity is ambiguous",
                                field="versions",
                            )
                        if matching:
                            selected_operator_registration_id = matching[0][
                                "registration_id"
                            ]
                        elif subject["implementation"] == "amaru":
                            profile_data["amaru_version"] = subject["version"]
                        else:
                            profile_data["cardano_version"] = subject["version"]
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
            preview = build_deployment_version_preview(
                profile_data,
                operator_registration_id=selected_operator_registration_id,
                request_controller=request_controller,
            )
            enforce_deployment_version_gate(
                preview,
                acknowledge_unknown=request.acknowledge_unknown_versions,
            )
        except DeploymentVersionGateError as exc:
            raise RunPlanError(
                str(exc),
                field="versions",
                details={
                    "code": exc.code,
                    "requires_acknowledgement": (
                        exc.code == "unknown-version-ack-required"
                    ),
                },
            ) from exc
    elif (substrate_version_profile := _scenario_substrate_version_profile(scenario, request)) is not None:
        profile_data = substrate_version_profile
        try:
            preview = build_deployment_version_preview(profile_data)
            enforce_deployment_version_gate(
                preview,
                acknowledge_unknown=request.acknowledge_unknown_versions,
            )
        except DeploymentVersionGateError as exc:
            raise RunPlanError(
                str(exc),
                field="versions",
                details={
                    "code": exc.code,
                    "requires_acknowledgement": exc.code == "unknown-version-ack-required",
                },
            ) from exc

    effective_target = dict(scenario.target)
    if preview is not None:
        release = (preview.get("resolved") or {}).get(effective_target["implementation"])
        if release:
            effective_target["version"] = release["version"]
            effective_target["source_revision"] = release.get("source_revision")
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
    measurements = _measurement_context(
        effective_scenario,
        profile,
        preview,
        run_overrides=request.measurement_overrides,
        experimental_consent=request.experimental_measurement_consent,
    )
    target_bindings = _scenario_target_bindings(
        effective_scenario, measurements.get("target_identity") or {}
    )
    substrate_bindings = _scenario_substrate_bindings(effective_scenario, preview)
    cells, primitive_names = _primitive_inventory(effective_scenario)
    capability = _capability_context(capability_context)
    conformance_adapters = _conformance_bindings(
        effective_scenario,
        registry_root=capability["measurement_target_registry"],
        host_os="linux",
        host_arch=capability["host_arch"],
    )
    from profile_manager.run_backends import classify_run_backends

    backends = classify_run_backends(effective_scenario)

    versions = None
    topology_shape = None
    if preview is not None:
        versions = {
            "policy": preview.get("policy"),
            "policy_source": preview.get("policy_source"),
            "scope": preview.get("scope"),
            "status": preview.get("status"),
            "reason": preview.get("reason"),
            "catalog_revision": preview.get("catalog_revision"),
            "operator_overlay_revision": preview.get("operator_overlay_revision"),
            "operator_registration_id": preview.get("operator_registration_id"),
            "pair": preview.get("pair"),
            "requires_acknowledgement": bool(
                preview.get("requires_acknowledgement")
            ),
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
        if profile is not None:
            substrate = versioned_substrate_for_profile(profile, preview)
            desired_topology = desired_topology_from_substrate(profile.id, substrate)
            topology_shape = substrate.get("actual_topology")
            versions.update(
                {
                    "deployment_identity_sha256": desired_topology["deployment_sha256"],
                    "topology_plan_sha256": desired_topology["plan_sha256"],
                    # Freeze the complete resolver-owned operand used by the shared
                    # deployment-equivalence predicate.  A compact release projection
                    # cannot preserve absent/false/malformed catalog metadata states.
                    "expected_desired_topology": desired_topology,
                }
            )
        else:
            versions["selection_kind"] = "scenario-substrate"
        if operator_preference is not None:
            subject = operator_preference["decision"]["subject"]
            _validate_operator_preference_subject(subject, versions, profile.id)
            versions["operator_preference"] = {
                "preference_id": operator_preference["preference"]["preference_id"],
                "decision_id": operator_preference["decision"]["decision_id"],
                "subject_key": operator_preference["decision"]["subject_key"],
                "overlay_revision": operator_state["revision"],
            }

    required_checks = ["framework"]
    if topology_id:
        required_checks.append(f"topology:{topology_id}")
    elif selected_profile_id:
        required_checks.extend([f"profile:{selected_profile_id}", "deployed-version-identity"])

    plan = {
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
            "target_bindings": target_bindings,
            "substrate_bindings": substrate_bindings,
            "required_conformance_adapters": conformance_adapters,
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
        "topology_shape": topology_shape,
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
    plan["availability"] = classify_run_availability(
        plan,
        capability_context=capability_context,
        execution=request.execution,
    )
    return plan


def stable_run_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Return only catalog-resolved, execution-affecting plan fields."""

    transient = {"availability", "launch_id", "created_at", "artifacts"}
    return {key: value for key, value in plan.items() if key not in transient}


def digest_run_plan(plan: dict[str, Any]) -> str:
    """Return the stable digest used to detect a changed catalog selection."""
    stable_plan = stable_run_plan(plan)
    body = json.dumps(stable_plan, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(body).hexdigest()
