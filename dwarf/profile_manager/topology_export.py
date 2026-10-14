"""Safe, tamper-evident topology configuration export and generation.

The artifacts in this module are declarative data.  They never contain a
command, environment block, Docker argument, or user-selected image.  Every
deployable profile is resolved through the installed version catalog and the
existing deployment adapter before it can be saved or replayed.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import hmac
import json
import re
from typing import Any

from profile_manager.deployment_versions import (
    DeploymentVersionGateError,
    build_deployment_version_preview,
)
from profile_manager.profiles import Profile, versioned_substrate_for_profile
from profile_manager.version_catalog import CatalogError


SCHEMA_VERSION = "dwarf.topology-configuration/v1"
_REPLAYABLE_KINDS = {"generated_configuration", "exact_deployed_snapshot"}
_TOPOLOGY_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_IMMUTABLE_IMAGE = re.compile(r"^.+@sha256:[0-9a-f]{64}$")
_SECRET_KEY = re.compile(
    r"(^|_)(?:api_?)?(?:token|password|passwd|secret|credential|private_?key|auth)(?:$|_)",
    re.IGNORECASE,
)
_COMMON_FIELDS = {
    "schema_version",
    "artifact_kind",
    "topology_id",
    "exported_at",
    "deployment_evidence",
    "profile",
    "resolved",
    "provenance",
    "content_sha256",
}
_KIND_FIELDS = {
    "catalog_profile": _COMMON_FIELDS,
    "generated_configuration": _COMMON_FIELDS | {"generator"},
    "exact_deployed_snapshot": _COMMON_FIELDS
    | {"observed", "redacted_paths", "generator", "controller_attestation"},
}


class TopologyExportError(ValueError):
    """A topology artifact cannot be generated, trusted, or replayed."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _digest(value: dict[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "content_sha256"}
    return "sha256:" + hashlib.sha256(_canonical(body)).hexdigest()


def _seal(value: dict[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result["content_sha256"] = _digest(result)
    return result


def _redact(value: Any, *, path: str, redacted_paths: list[str]) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if _SECRET_KEY.search(str(key)):
                result[str(key)] = "[REDACTED]"
                redacted_paths.append(child_path)
            else:
                result[str(key)] = _redact(
                    item, path=child_path, redacted_paths=redacted_paths
                )
        return result
    if isinstance(value, list):
        return [
            _redact(item, path=f"{path}[{index}]", redacted_paths=redacted_paths)
            for index, item in enumerate(value)
        ]
    return value


def _profile_dict(profile: Profile) -> dict[str, Any]:
    """Return only portable declarative fields, never host-derived paths."""

    raw = asdict(profile)
    result = {
        "id": profile.id,
        "label": profile.label,
        "haskell_count": int(profile.node_count),
        "amaru_count": int(profile.amaru_node_count),
        "network_magic": int(profile.network_magic),
        "peer_sharing": bool(profile.peer_sharing),
        "shared_genesis": bool(profile.shared_genesis),
        "version_policy": profile.version_policy,
        "measurement_target_mode": profile.measurement_target_mode,
        "amaru_json_traces": bool(profile.amaru_json_traces),
        "cardano_measurement_traces": bool(profile.cardano_measurement_traces),
        "plutus_v2_genesis": bool(profile.plutus_v2_genesis),
    }
    for key in (
        "topology_pattern",
        "amaru_network",
        "cardano_version",
        "amaru_version",
        "compatibility_pair",
        "measurement_revision",
        "measurement_patch_revision",
        "measurement_patch_set_sha256",
        "plutus_v2_cost_model_sha256",
        "cardano_experimental_protocols",
        "kes_genesis_override",
        "generated_topology",
    ):
        if raw.get(key) is not None:
            result[key] = raw[key]
    return result


def _provenance(preview: dict[str, Any], substrate: dict[str, Any]) -> dict[str, Any]:
    return {
        "profile_id": substrate.get("profile_id"),
        "catalog_revision": substrate.get("catalog_revision"),
        "version_policy": substrate.get("version_policy"),
        "version_policy_source": substrate.get("version_policy_source"),
        "selection_status": substrate.get("version_status"),
        "deployment_adapter": substrate.get("deployment_adapter"),
        "resolved_releases": {
            implementation: {
                key: release.get(key)
                for key in ("implementation", "version", "source_revision", "channel")
            }
            for implementation, release in sorted(
                (preview.get("resolved") or {}).items()
            )
        },
    }


def _assert_immutable_nodes(substrate: dict[str, Any]) -> None:
    nodes = substrate.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise TopologyExportError("resolved topology contains no nodes")
    for node in nodes:
        if not isinstance(node, dict) or not _IMMUTABLE_IMAGE.fullmatch(
            str(node.get("image") or "")
        ):
            raise TopologyExportError("resolved topology image is not digest pinned")


def build_catalog_profile_export(
    profile: Profile,
    version_preview: dict[str, Any],
    *,
    exported_at: str,
) -> dict[str, Any]:
    """Export a catalog declaration without claiming it is deployed."""

    substrate = versioned_substrate_for_profile(profile, version_preview)
    _assert_immutable_nodes(substrate)
    return _seal(
        {
            "schema_version": SCHEMA_VERSION,
            "artifact_kind": "catalog_profile",
            "topology_id": profile.id,
            "exported_at": exported_at,
            "deployment_evidence": False,
            "profile": _profile_dict(profile),
            "resolved": {"substrate": substrate},
            "provenance": _provenance(version_preview, substrate),
        }
    )


def _node_identity(node: dict[str, Any]) -> dict[str, str]:
    return {
        "id": str(node.get("id") or node.get("node_id") or ""),
        "implementation": str(node.get("implementation") or node.get("impl") or ""),
        "version": str(node.get("version") or ""),
        "image": str(node.get("image") or ""),
    }


def build_exact_deployed_snapshot(
    *,
    profile: Profile,
    version_preview: dict[str, Any],
    retained_deployment: dict[str, Any],
    observed_nodes: list[dict[str, Any]],
    reconciliation_state: str,
    exported_at: str,
    semantic_health: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build replayable evidence only for an exactly converged deployment."""

    if reconciliation_state not in {"converged", "unhealthy"}:
        raise TopologyExportError(
            "exact export requires a complete identity-matching topology"
        )
    retained_substrate = retained_deployment.get("substrate")
    if not isinstance(retained_substrate, dict):
        raise TopologyExportError("retained deployment has no substrate")
    expected = versioned_substrate_for_profile(profile, version_preview)
    _assert_immutable_nodes(expected)
    if _canonical(_execution_substrate_identity(retained_substrate)) != _canonical(
        _execution_substrate_identity(expected)
    ):
        raise TopologyExportError(
            "retained execution substrate differs from the trusted profile resolution"
        )
    expected_nodes = sorted(
        (_node_identity(node) for node in expected["nodes"]), key=lambda item: item["id"]
    )
    retained_nodes = sorted(
        (
            _node_identity(node)
            for node in retained_substrate.get("nodes") or []
            if isinstance(node, dict)
        ),
        key=lambda item: item["id"],
    )
    observed_identities = sorted(
        (_node_identity(node) for node in observed_nodes if isinstance(node, dict)),
        key=lambda item: item["id"],
    )
    if retained_nodes != expected_nodes:
        raise TopologyExportError("retained deployment identity differs from catalog resolution")
    if observed_identities != expected_nodes:
        raise TopologyExportError("observed node identity differs from retained deployment")

    redacted_paths: list[str] = []
    safe_substrate = _redact(
        retained_substrate,
        path="resolved.substrate",
        redacted_paths=redacted_paths,
    )
    runtime_parameters = {
        key: retained_deployment[key]
        for key in ("compose_project", "healthy_timeout_seconds", "runtime_root")
        if retained_deployment.get(key) is not None
    }
    safe_runtime = _redact(
        runtime_parameters,
        path="resolved.runtime_parameters",
        redacted_paths=redacted_paths,
    )
    safe_semantic_health = _redact(
        dict(semantic_health or {}),
        path="observed.semantic_health",
        redacted_paths=redacted_paths,
    )
    safe_observed_nodes = _redact(
        sorted(
            (dict(node) for node in observed_nodes),
            key=lambda item: str(item.get("id") or item.get("node_id") or ""),
        ),
        path="observed.nodes",
        redacted_paths=redacted_paths,
    )
    artifact = {
            "schema_version": SCHEMA_VERSION,
            "artifact_kind": "exact_deployed_snapshot",
            "topology_id": profile.id,
            "exported_at": exported_at,
            # Only the controller service can promote this candidate to
            # authenticated deployment evidence.
            "deployment_evidence": False,
            "profile": _profile_dict(profile),
            "resolved": {
                "substrate": safe_substrate,
                "runtime_parameters": safe_runtime,
            },
            "observed": {
                "reconciliation_state": reconciliation_state,
                "semantic_health": safe_semantic_health,
                "nodes": safe_observed_nodes,
            },
            "provenance": {
                **_provenance(version_preview, expected),
                "retained_deployment_sha256": "sha256:"
                + hashlib.sha256(_canonical(retained_deployment)).hexdigest(),
            },
            "redacted_paths": sorted(redacted_paths),
        }
    generator = _generator_contract_from_profile(profile)
    if generator is not None:
        artifact["generator"] = generator
    return _seal(artifact)


def generate_topology_configuration(
    *,
    topology_id: str,
    node_count: int,
    implementation: str,
    version: str,
    network_magic: int,
    exported_at: str,
    catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Preview a bounded topology resolved only from catalogued releases."""

    if not _TOPOLOGY_ID.fullmatch(str(topology_id)):
        raise TopologyExportError("topology_id must be lowercase kebab-case")
    if isinstance(node_count, bool) or not isinstance(node_count, int) or not 1 <= node_count <= 32:
        raise TopologyExportError("node_count must be an integer from 1 through 32")
    if implementation not in {"cardano-node", "amaru"}:
        raise TopologyExportError("implementation must be cardano-node or amaru")
    if (
        isinstance(network_magic, bool)
        or not isinstance(network_magic, int)
        or not 1 <= network_magic <= 2**32 - 1
    ):
        raise TopologyExportError(
            "network_magic must be an integer from 1 through 4294967295"
        )
    generator = {
        "implementation": implementation,
        "network_magic": network_magic,
        "node_count": node_count,
        "version": str(version),
    }
    profile_data: dict[str, Any] = {
        "id": topology_id,
        "label": f"Generated {implementation} topology ({node_count} nodes)",
        "node_count": node_count if implementation == "cardano-node" else 0,
        "amaru_node_count": node_count if implementation == "amaru" else 0,
        "network_magic": network_magic,
        "peer_sharing": False,
        "topology_pattern": "local-mesh" if node_count > 1 else None,
        "shared_genesis": implementation == "cardano-node",
        "version_policy": "exact",
        "generated_topology": {"schema_version": "v1", **generator},
    }
    profile_data["cardano_version" if implementation == "cardano-node" else "amaru_version"] = str(version)
    try:
        profile = Profile.from_dict(profile_data)
        preview = build_deployment_version_preview(profile_data, catalog=catalog)
        substrate = versioned_substrate_for_profile(profile, preview)
    except (CatalogError, DeploymentVersionGateError, KeyError, TypeError, ValueError) as exc:
        raise TopologyExportError(str(exc)) from exc
    _assert_immutable_nodes(substrate)
    return _seal(
        {
            "schema_version": SCHEMA_VERSION,
            "artifact_kind": "generated_configuration",
            "topology_id": topology_id,
            "exported_at": exported_at,
            "deployment_evidence": False,
            "generator": generator,
            "profile": _profile_dict(profile),
            "resolved": {"substrate": substrate},
            "provenance": _provenance(preview, substrate),
        }
    )


def generate_topology_shape_configuration(
    *,
    topology_id: str,
    haskell_count: int,
    amaru_count: int,
    cardano_version: str | None,
    amaru_version: str | None,
    network_magic: int,
    exported_at: str,
    catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Preview a v2 bounded Cardano, Amaru, or mixed topology shape."""

    if not _TOPOLOGY_ID.fullmatch(str(topology_id)):
        raise TopologyExportError("topology_id must be lowercase kebab-case")
    counts = (haskell_count, amaru_count)
    if any(
        isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= 32
        for count in counts
    ) or not 1 <= sum(counts) <= 32:
        raise TopologyExportError("total node count must be an integer from 1 through 32")
    if (
        isinstance(network_magic, bool)
        or not isinstance(network_magic, int)
        or not 1 <= network_magic <= 2**32 - 1
    ):
        raise TopologyExportError(
            "network_magic must be an integer from 1 through 4294967295"
        )
    if haskell_count and not str(cardano_version or "").strip():
        raise TopologyExportError("cardano_version is required when Cardano nodes are selected")
    if amaru_count and not str(amaru_version or "").strip():
        raise TopologyExportError("amaru_version is required when Amaru nodes are selected")
    implementation = (
        "mixed" if haskell_count and amaru_count
        else "cardano-node" if haskell_count else "amaru"
    )
    generator = {
        "schema_version": "v2",
        "implementation": implementation,
        "network_magic": network_magic,
        "haskell_count": haskell_count,
        "amaru_count": amaru_count,
        "cardano_version": str(cardano_version) if haskell_count else None,
        "amaru_version": str(amaru_version) if amaru_count else None,
    }
    profile_data: dict[str, Any] = {
        "id": topology_id,
        "label": (
            f"Generated {implementation} topology "
            f"({haskell_count} Cardano / {amaru_count} Amaru)"
        ),
        "haskell_count": haskell_count,
        "amaru_count": amaru_count,
        "network_magic": network_magic,
        "peer_sharing": False,
        "topology_pattern": "local-mesh" if sum(counts) > 1 else None,
        "shared_genesis": haskell_count > 0,
        "version_policy": "exact",
        "generated_topology": generator,
    }
    if haskell_count:
        profile_data["cardano_version"] = str(cardano_version)
    if amaru_count:
        profile_data["amaru_version"] = str(amaru_version)
    try:
        profile = Profile.from_dict(profile_data)
        preview = build_deployment_version_preview(profile_data, catalog=catalog)
        substrate = versioned_substrate_for_profile(profile, preview)
    except (CatalogError, DeploymentVersionGateError, KeyError, TypeError, ValueError) as exc:
        raise TopologyExportError(str(exc)) from exc
    _assert_immutable_nodes(substrate)
    return _seal({
        "schema_version": SCHEMA_VERSION,
        "artifact_kind": "generated_configuration",
        "topology_id": topology_id,
        "exported_at": exported_at,
        "deployment_evidence": False,
        "generator": generator,
        "profile": _profile_dict(profile),
        "resolved": {"substrate": substrate},
        "provenance": _provenance(preview, substrate),
    })


def validate_topology_configuration(
    value: dict[str, Any],
    *,
    attestation_secret: str | None = None,
    allow_unverified_exact: bool = False,
) -> dict[str, Any]:
    """Verify strict kind schema and authenticate exact deployment evidence."""

    if not isinstance(value, dict):
        raise TopologyExportError("topology configuration must be an object")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise TopologyExportError("unsupported topology configuration schema")
    kind = value.get("artifact_kind")
    allowed_fields = _KIND_FIELDS.get(str(kind))
    if allowed_fields is None:
        raise TopologyExportError("unsupported topology artifact kind")
    required_fields = set(allowed_fields)
    if kind == "exact_deployed_snapshot":
        required_fields.discard("generator")
        required_fields.discard("controller_attestation")
    if set(value) - allowed_fields or not required_fields <= set(value):
        raise TopologyExportError(f"invalid {kind} artifact schema")
    if value.get("content_sha256") != _digest(value):
        raise TopologyExportError("topology content digest does not match")
    if value.get("topology_id") != (value.get("profile") or {}).get("id"):
        raise TopologyExportError("topology and profile identities differ")
    substrate = (value.get("resolved") or {}).get("substrate")
    if not isinstance(substrate, dict):
        raise TopologyExportError("topology configuration has no resolved substrate")
    _assert_immutable_nodes(substrate)
    expected_resolved = (
        {"substrate", "runtime_parameters"}
        if kind == "exact_deployed_snapshot"
        else {"substrate"}
    )
    if not isinstance(value.get("resolved"), dict) or set(value["resolved"]) != expected_resolved:
        raise TopologyExportError(f"invalid {kind} resolved schema")
    if kind in {"catalog_profile", "generated_configuration"}:
        if value.get("deployment_evidence") is not False:
            raise TopologyExportError("configuration preview cannot claim deployment evidence")
    else:
        observed = value.get("observed")
        if not isinstance(observed, dict) or set(observed) != {
            "reconciliation_state", "semantic_health", "nodes"
        }:
            raise TopologyExportError("invalid exact deployment observation schema")
        if not isinstance(value.get("redacted_paths"), list):
            raise TopologyExportError("exact deployment redactions must be a list")
        if allow_unverified_exact and value.get("controller_attestation") is None:
            if value.get("deployment_evidence") is not False:
                raise TopologyExportError(
                    "unattested snapshot cannot claim deployment evidence"
                )
        else:
            if value.get("deployment_evidence") is not True or not attestation_secret:
                raise TopologyExportError("exact snapshot requires controller attestation")
            if not verify_exact_configuration_attestation(
                value, secret=attestation_secret
            ):
                raise TopologyExportError("controller attestation is invalid")
    return value


def _attestation_material(
    *, candidate_sha256: str, installation_id: str, release_revision: str
) -> dict[str, str]:
    return {
        "schema_version": "v1",
        "candidate_sha256": candidate_sha256,
        "installation_id": installation_id,
        "release_revision": release_revision,
    }


def attest_exact_configuration(
    value: dict[str, Any],
    *,
    secret: str,
    installation_id: str,
    release_revision: str,
) -> dict[str, Any]:
    """Promote a controller-built candidate to authenticated evidence."""

    if len(secret) < 32:
        raise TopologyExportError("controller attestation secret is invalid")
    checked = validate_topology_configuration(value, allow_unverified_exact=True)
    if checked.get("artifact_kind") != "exact_deployed_snapshot":
        raise TopologyExportError("only exact deployment snapshots can be attested")
    candidate_sha256 = str(checked["content_sha256"])
    material = _attestation_material(
        candidate_sha256=candidate_sha256,
        installation_id=installation_id,
        release_revision=release_revision,
    )
    signature = "sha256:" + hmac.new(
        secret.encode("utf-8"), _canonical(material), hashlib.sha256
    ).hexdigest()
    return _seal(
        {
            **checked,
            "deployment_evidence": True,
            "controller_attestation": {**material, "signature": signature},
        }
    )


def verify_exact_configuration_attestation(
    value: dict[str, Any], *, secret: str
) -> bool:
    """Verify both the candidate hash and controller HMAC."""

    if not isinstance(secret, str) or len(secret) < 32:
        return False
    attestation = value.get("controller_attestation")
    if not isinstance(attestation, dict) or set(attestation) != {
        "schema_version", "candidate_sha256", "installation_id",
        "release_revision", "signature",
    }:
        return False
    if value.get("content_sha256") != _digest(value):
        return False
    candidate = {
        key: item
        for key, item in value.items()
        if key not in {"controller_attestation", "content_sha256"}
    }
    candidate["deployment_evidence"] = False
    if _digest(candidate) != attestation.get("candidate_sha256"):
        return False
    material = _attestation_material(
        candidate_sha256=str(attestation.get("candidate_sha256") or ""),
        installation_id=str(attestation.get("installation_id") or ""),
        release_revision=str(attestation.get("release_revision") or ""),
    )
    expected = "sha256:" + hmac.new(
        secret.encode("utf-8"), _canonical(material), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, str(attestation.get("signature") or ""))


def _declarative_substrate_identity(substrate: dict[str, Any]) -> dict[str, Any]:
    """Backward-compatible alias for the complete execution identity."""

    return _execution_substrate_identity(substrate)


def _execution_substrate_identity(substrate: dict[str, Any]) -> dict[str, Any]:
    """Remove only controller-instance decoration, retaining every deploy input."""

    return {
        key: item
        for key, item in substrate.items()
        if key not in {"controller_ownership", "docker_host_runtime_root"}
    }


def _generator_contract_from_profile(profile: Profile) -> dict[str, Any] | None:
    """Return a generator request only from explicit persisted provenance."""

    origin = profile.generated_topology
    if origin is None:
        return None
    if origin.get("schema_version") == "v2":
        contract = dict(origin)
        try:
            canonical_profile = Profile.from_dict(
                generate_topology_shape_configuration(
                    topology_id=profile.id,
                    haskell_count=contract["haskell_count"],
                    amaru_count=contract["amaru_count"],
                    cardano_version=contract["cardano_version"],
                    amaru_version=contract["amaru_version"],
                    network_magic=contract["network_magic"],
                    exported_at="",
                )["profile"]
            )
        except (TopologyExportError, KeyError, TypeError, ValueError) as exc:
            raise TopologyExportError(
                f"persisted generator provenance cannot be resolved: {exc}"
            ) from exc
        if _canonical(asdict(profile)) != _canonical(asdict(canonical_profile)):
            raise TopologyExportError(
                "persisted generator provenance differs from the generated profile"
            )
        return contract
    contract = {
        key: origin[key]
        for key in ("implementation", "network_magic", "node_count", "version")
    }
    try:
        canonical_profile = Profile.from_dict(
            generate_topology_configuration(
                topology_id=profile.id,
                node_count=contract["node_count"],
                implementation=contract["implementation"],
                version=contract["version"],
                network_magic=contract["network_magic"],
                exported_at="",
            )["profile"]
        )
    except (TopologyExportError, KeyError, TypeError, ValueError) as exc:
        raise TopologyExportError(
            f"persisted generator provenance cannot be resolved: {exc}"
        ) from exc
    if _canonical(asdict(profile)) != _canonical(asdict(canonical_profile)):
        raise TopologyExportError(
            "persisted generator provenance differs from the generated profile"
        )
    return contract


def _rebuild_from_generator(checked: dict[str, Any]) -> dict[str, Any]:
    generator = checked.get("generator")
    if isinstance(generator, dict) and generator.get("schema_version") == "v2":
        if set(generator) != {
            "schema_version", "implementation", "network_magic", "haskell_count",
            "amaru_count", "cardano_version", "amaru_version",
        }:
            raise TopologyExportError(
                "replayable generated topology has no valid v2 generator contract"
            )
        rebuilt = generate_topology_shape_configuration(
            topology_id=str(checked["topology_id"]),
            haskell_count=generator["haskell_count"],
            amaru_count=generator["amaru_count"],
            cardano_version=generator["cardano_version"],
            amaru_version=generator["amaru_version"],
            network_magic=generator["network_magic"],
            exported_at=str(checked.get("exported_at") or ""),
        )
        if _canonical(_execution_substrate_identity(rebuilt["resolved"]["substrate"])) != _canonical(
            _execution_substrate_identity(checked["resolved"]["substrate"])
        ):
            raise TopologyExportError("generator resolution differs from the exported snapshot")
        return rebuilt["profile"]
    if not isinstance(generator, dict) or set(generator) != {
        "implementation", "network_magic", "node_count", "version"
    }:
        raise TopologyExportError("replayable generated topology has no valid generator contract")
    if checked.get("topology_id") != (checked.get("profile") or {}).get("id"):
        raise TopologyExportError("generator topology identity differs from profile")
    rebuilt = generate_topology_configuration(
        topology_id=str(checked["topology_id"]),
        node_count=generator["node_count"],
        implementation=generator["implementation"],
        version=generator["version"],
        network_magic=generator["network_magic"],
        exported_at=str(checked.get("exported_at") or ""),
    )
    if _canonical(_execution_substrate_identity(rebuilt["resolved"]["substrate"])) != _canonical(
        _execution_substrate_identity(checked["resolved"]["substrate"])
    ):
        raise TopologyExportError("generator resolution differs from the exported snapshot")
    return rebuilt["profile"]


def replay_profile_from_snapshot(
    value: dict[str, Any], *, attestation_secret: str | None = None
) -> dict[str, Any]:
    """Return only the declarative profile after verifying a replayable artifact."""

    checked = validate_topology_configuration(
        value, attestation_secret=attestation_secret
    )
    if checked.get("artifact_kind") not in _REPLAYABLE_KINDS:
        raise TopologyExportError("this topology artifact is not replayable")
    if checked.get("artifact_kind") == "generated_configuration" or checked.get("generator") is not None:
        return _rebuild_from_generator(checked)
    profile_data = checked.get("profile")
    if not isinstance(profile_data, dict):
        raise TopologyExportError("replayable topology has no profile")
    try:
        from profile_manager.profiles import find_profile

        trusted_profile = find_profile(str(checked["topology_id"]))
        normalized = _profile_dict(trusted_profile)
        preview = build_deployment_version_preview(asdict(trusted_profile))
        substrate = versioned_substrate_for_profile(trusted_profile, preview)
    except (CatalogError, DeploymentVersionGateError, KeyError, TypeError, ValueError) as exc:
        raise TopologyExportError(f"replay profile no longer resolves: {exc}") from exc
    if _canonical(_execution_substrate_identity(substrate)) != _canonical(
        _execution_substrate_identity(checked["resolved"]["substrate"])
    ):
        raise TopologyExportError("replay resolution differs from the exported snapshot")
    return normalized
