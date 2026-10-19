"""Catalog-only campaign contracts; loading never runs or accepts a campaign.

Security oracles and measurement exercise requirements remain separate. Static
readiness can expose blockers, but only a later evidence evaluator can establish
measurement completeness or a security result.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from profile_manager.data.catalog_definitions import (
    CatalogError, InvalidDefinitionError, list_definitions, load_definition,
)
from profile_manager.measurements import _validation_message


_SPEC_ROOT = Path(__file__).resolve().parents[1] / "spec/v1"


def _validate(document: dict[str, Any], name: str) -> None:
    schema = json.loads((_SPEC_ROOT / f"{name}.schema.json").read_text(encoding="utf-8"))
    attachment = json.loads((_SPEC_ROOT / "measurement-attachment.schema.json").read_text(encoding="utf-8"))
    registry = Registry().with_resource(attachment["$id"], Resource.from_contents(attachment))
    errors = sorted(Draft202012Validator(schema, registry=registry).iter_errors(document),
                    key=lambda error: str(list(error.absolute_path)))
    if errors:
        raise InvalidDefinitionError(_validation_message(errors[0]))


def _lookup(catalog: str, definition_id: str) -> dict[str, Any]:
    try:
        return load_definition(catalog, definition_id).data
    except CatalogError as exc:
        raise InvalidDefinitionError(f"unknown {catalog} reference {definition_id}: {exc}") from exc


def _scenario(scenario_id: str, *, executable: bool = True) -> dict[str, Any]:
    document = _lookup("scenarios", scenario_id)
    if executable:
        identities = [document.get("target", {})] + document.get("substrate", {}).get("nodes", [])
        if any(identity.get("version") not in {None, "any", "resolved-plan"} for identity in identities):
            raise InvalidDefinitionError(f"scenario {scenario_id} is pinned; use a blocked candidate until migrated")
        if document.get("profile"):
            _profile(document["profile"])
    return document


def _profile(profile_id: str) -> dict[str, Any]:
    document = _lookup("profiles", profile_id)
    if document.get("version_policy") == "exact" or any(
        document.get(field) for field in ("cardano_version", "amaru_version", "node_versions")
    ):
        raise InvalidDefinitionError(f"profile {profile_id} is pinned; reusable entries require operator-selected identities")
    return document


def _profile_nodes(document: dict[str, Any]) -> dict[str, str]:
    """Mirror the node identities of the versioned profile substrate, without resolving releases."""
    from profile_manager.profiles import Profile, deployment_adapter_for_profile

    profile = Profile.from_dict(document)
    nodes = {f"node{index}": "cardano-node" for index in range(1, profile.node_count + 1)}
    nodes.update({f"amaru{index}": "amaru" for index in range(1, profile.amaru_node_count + 1)})
    if (
        not profile.node_count and profile.amaru_node_count
        and deployment_adapter_for_profile(profile) == "amaru-control"
    ):
        nodes["bootstrap-cardano"] = "cardano-node"
    return nodes


def _unique(values: list[str], label: str) -> None:
    if len(values) != len(set(values)):
        raise InvalidDefinitionError(f"duplicate {label}")


def validate_measurement_attachment(
    document: dict[str, Any], *, nodes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Bind one existing measurement profile to exactly one declared node."""
    _validate(document, "measurement-attachment")
    matches = [node for node in nodes if node["node_id"] == document["node_id"]]
    if len(matches) != 1:
        raise InvalidDefinitionError("measurement attachment requires one exact topology node")
    if matches[0]["implementation"] != document["implementation"]:
        raise InvalidDefinitionError("measurement attachment implementation differs from node")
    if not document["result_namespace"].startswith(f"nodes/{document['node_id']}/measurements/"):
        raise InvalidDefinitionError("result namespace must belong to the exact node")
    profile = _lookup("measurement-profiles", document["measurement_profile"])
    if profile["implementation"] not in {document["implementation"], "mixed"}:
        raise InvalidDefinitionError("measurement profile implementation differs from attachment")
    mode = document.get("target_mode", "stock")
    if mode not in profile["target_modes"]:
        raise InvalidDefinitionError("measurement profile does not support attachment target mode")
    selections = [m["id"] for m in profile["measurements"] if m["enabled"]]
    selections += document.get("additional_measurements", [])
    _unique(selections, "measurement selection")
    for measurement_id in selections:
        tap = _lookup("measurements", measurement_id)
        if tap["compatibility"]["implementation"] != document["implementation"]:
            raise InvalidDefinitionError(f"measurement {measurement_id} implementation differs from node")
        if mode not in tap["compatibility"]["target_modes"]:
            raise InvalidDefinitionError(f"measurement {measurement_id} does not support attachment target mode")
    return document


def validate_security_campaign_suite(document: dict[str, Any]) -> dict[str, Any]:
    """Validate security recipes, reusable identities, controls and node bindings."""
    _validate(document, "security-campaign-suite")
    if document["suite_kind"] != "security":
        raise InvalidDefinitionError("expected security suite")
    _unique([group["id"] for group in document["groups"]], "group id")
    _unique([entry["id"] for group in document["groups"] for entry in group["entries"]], "entry id")
    for group in document["groups"]:
        for candidate in group["blocked_candidates"]:
            _scenario(candidate["scenario_id"], executable=False)
        for entry in group["entries"]:
            scenario = _scenario(entry["scenario_id"])
            _scenario(entry["normal_control"]["scenario_id"])
            topology = entry["topology"]
            nodes = topology["nodes"]
            expected_mode = "library" if scenario["runtime"] == "library" else "shared-runtime"
            if topology["mode"] != expected_mode:
                raise InvalidDefinitionError("topology mode must preserve the source scenario runtime")
            if scenario["runtime"] == "library" and any(
                node["implementation"] != scenario["target"]["implementation"] for node in nodes
            ):
                raise InvalidDefinitionError("library topology must preserve the source target implementation")
            _unique([node["node_id"] for node in nodes], "topology node")
            node_ids = {node["node_id"] for node in nodes}
            if not set(entry["target_nodes"]) <= node_ids:
                raise InvalidDefinitionError("target_nodes must exist in topology")
            profile = None
            if "deployment_profile" in topology:
                profile = _profile(topology["deployment_profile"])
            if scenario.get("profile") and topology.get("deployment_profile") != scenario["profile"]:
                raise InvalidDefinitionError("topology deployment profile must preserve the source scenario binding")
            substrate = scenario.get("substrate", {}).get("nodes", [])
            expected = None
            if substrate:
                expected = {node["id"]: node["impl"] for node in substrate}
            elif scenario.get("profile"):
                expected = _profile_nodes(profile)
            if expected is not None:
                actual = {node["node_id"]: node["implementation"] for node in nodes}
                if actual != expected or topology["mode"] != "shared-runtime":
                    raise InvalidDefinitionError("topology must preserve the scenario's shared nodes and implementations")
            attachments = entry["measurement_attachments"]
            _unique([a["result_namespace"] for a in attachments], "result namespace")
            for attachment in attachments:
                validate_measurement_attachment(attachment, nodes=nodes)
    return document


def validate_measurement_suite(document: dict[str, Any]) -> dict[str, Any]:
    """Require a non-vacuous exercise or explicit fallback for every catalog tap."""
    _validate(document, "security-campaign-suite")
    if document["suite_kind"] != "measurement":
        raise InvalidDefinitionError("expected measurement suite")
    mapped = set()
    for exercise in document["exercises"]:
        tap = _lookup("measurements", exercise["measurement_id"])
        mapped.add(exercise["measurement_id"])
        if tap["compatibility"]["implementation"] != exercise["implementation"]:
            raise InvalidDefinitionError("exercise implementation differs from measurement")
        _scenario(exercise["scenario_id"])
        fallback = exercise.get("fallback")
        if fallback:
            _scenario(fallback["scenario_id"], executable=fallback["status"] == "ready")
    catalog = {record.definition_id for record in list_definitions("measurements")}
    missing = catalog - mapped
    if missing:
        raise InvalidDefinitionError(f"unmapped catalog measurements: {', '.join(sorted(missing))}")
    return document


def measurement_suite_readiness(document: dict[str, Any]) -> dict[str, Any]:
    """Return static prerequisites only; no samples or security results are evaluated."""
    validate_measurement_suite(document)
    missing_runtime = sorted({e["measurement_id"] for e in document["exercises"]
                              if e["runtime"]["status"] == "missing"})
    blocked_fallbacks = sorted({e["measurement_id"] for e in document["exercises"]
                               if e.get("fallback", {}).get("status") == "blocked"})
    return {
        "measurement_complete": False,
        "status": "blocked" if missing_runtime or blocked_fallbacks else "pending-evidence",
        "missing_runtime": missing_runtime,
        "blocked_fallbacks": blocked_fallbacks,
        "required_exercises": sorted({e["measurement_id"] for e in document["exercises"]}),
    }


def _load(path: str | Path) -> dict[str, Any]:
    try:
        document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise InvalidDefinitionError(f"invalid suite YAML: {exc}") from exc
    if not isinstance(document, dict):
        raise InvalidDefinitionError("suite must contain an object")
    return document


def load_security_campaign_suite(path: str | Path) -> dict[str, Any]:
    return validate_security_campaign_suite(_load(path))


def load_measurement_suite(path: str | Path) -> dict[str, Any]:
    return validate_measurement_suite(_load(path))
