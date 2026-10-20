"""Freeze local campaign inputs. This module never builds or executes a campaign.

CandidateSnapshot is a content identity, not a source copy. The caller must keep
and later verify the same source bytes. Artifact declarations are future build
expectations, never observations. Release discovery/qualification are separate
operations; supply an already captured catalog (no refresh is performed here).
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from types import SimpleNamespace
from typing import Any

from jsonschema import Draft202012Validator

from profile_manager.data.catalog_definitions import load_definition
from profile_manager.data.measurement_compare import SCENARIO_TARGET_IDENTITY_FIELDS
from profile_manager.measurement_resolution import MeasurementResolutionError, resolve_measurements
from profile_manager.run_plan import digest_run_plan
from profile_manager.security_campaign_suite import validate_measurement_suite, validate_security_campaign_suite
from profile_manager.testplease_candidates import CandidateSnapshot
from profile_manager.version_catalog import load_version_catalog, resolve_release, resolve_verification, validate_version_catalog
from scripts.qualify_node_versions import classify_qualification

_SCHEMA = Path(__file__).resolve().parents[1] / "spec/v1/security-campaign.schema.json"
_MODES = ("stock", "patched", "coverage")
_ARTIFACT_FIELDS = ("image_digest", "binary_digest", "executable_digest")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_TREE = re.compile(r"^[0-9a-f]{64}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_NETWORK_FIELDS = ("era", "genesis_digest", "protocol_version")
_CALIBRATION_SCENARIOS = {
    "amaru": {
        "stock_scenario_id": "amaru-measurement-overhead-calibration-stock",
        "patched_scenario_id": "amaru-measurement-overhead-calibration-patched",
    },
    "cardano-node": {
        "stock_scenario_id": "cardano-measurement-overhead-calibration-stock",
        "patched_scenario_id": "cardano-measurement-overhead-calibration-patched",
    },
}


def serialize_campaign(document: dict[str, Any]) -> str:
    """Canonical UTF-8 JSON representation; non-JSON values/NaNs are rejected."""
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _digest(document: Any) -> str:
    return "sha256:" + hashlib.sha256(serialize_campaign(document).encode("utf-8")).hexdigest()


def digest_security_campaign(document: dict[str, Any]) -> str:
    """All frozen plan fields participate, except the two derived identifiers."""
    return _digest({k: v for k, v in document.items() if k not in {"campaign_id", "campaign_digest"}})


def campaign_evaluation_status(manifest: dict[str, Any], evaluation: dict[str, Any]) -> dict[str, Any]:
    """Project a separate derived status; never update the immutable plan.

    Runtime orchestration should retain the two evaluator records and expose
    this projection alongside the manifest, rather than writing plan fields.
    """
    if manifest.get("campaign_digest") != digest_security_campaign(manifest):
        raise ValueError("campaign manifest digest mismatch")
    security = evaluation.get("security_validity")
    measurements = evaluation.get("measurement_completeness")
    for record, kind in ((security, "campaign-security-validity"), (measurements, "campaign-measurement-completeness")):
        if not isinstance(record, dict) or record.get("kind") != kind or record.get("campaign_digest") != manifest["campaign_digest"] or record.get("campaign_id") != manifest["campaign_id"]:
            raise ValueError("evaluation does not belong to the frozen campaign")
    if not isinstance(security.get("evidence_digest"), str) or not _DIGEST.fullmatch(security["evidence_digest"]):
        raise ValueError("evaluation requires an immutable evidence digest")
    if security.get("evidence_digest") != measurements.get("evidence_digest"):
        raise ValueError("evaluation records refer to different evidence")
    if security.get("classification") not in {"pass", "finding", "inconclusive", "harness-invalid", "infrastructure-invalid"} or not isinstance(measurements.get("measurement_complete"), bool):
        raise ValueError("invalid derived campaign status")
    security_result = security["classification"]
    result = {"campaign_id": manifest["campaign_id"], "campaign_digest": manifest["campaign_digest"],
              "evidence_digest": security["evidence_digest"], "security_result": security_result,
              "measurement_completeness": "complete" if measurements["measurement_complete"] else "incomplete"}
    replay = evaluation.get("stock_replay_authority")
    if replay is not None:
        replay_entries = replay.get("entries") if isinstance(replay, dict) else None
        if (not isinstance(replay, dict) or replay.get("schema_version") != "v1"
                or replay.get("kind") != "campaign-stock-replay-authority"
                or replay.get("primary_campaign_digest") != manifest["campaign_digest"]
                or replay.get("primary_evidence_digest") != security["evidence_digest"]
                or replay.get("authority") not in {
                    "patched-primary-stock-confirmed",
                    "patched-primary-stock-disagrees",
                    "patched-primary-stock-replay-incomplete",
                }
                or replay.get("stock_security_result") not in {
                    "pass", "finding", "inconclusive", "harness-invalid", "infrastructure-invalid"
                }
                or not isinstance(replay_entries, list)
                or not replay_entries
                or not _DIGEST.fullmatch(str(replay.get("stock_campaign_digest") or ""))
                or not _DIGEST.fullmatch(str(replay.get("stock_evidence_digest") or ""))
                or replay.get("authority_digest")
                != _digest({key: value for key, value in replay.items() if key != "authority_digest"})):
            raise ValueError("invalid stock replay authority record")
        seen = set()
        complete_authority = replay["authority"] != "patched-primary-stock-replay-incomplete"
        for entry in replay_entries:
            if (not isinstance(entry, dict) or not isinstance(entry.get("entry_id"), str)
                    or not entry["entry_id"] or entry["entry_id"] in seen
                    or entry.get("authority") not in {"confirmed", "disagrees", "incomplete"}
                    or entry.get("primary_classification") not in {
                        "pass", "finding", "inconclusive", "harness-invalid", "infrastructure-invalid"
                    }
                    or entry.get("stock_classification") not in {
                        "pass", "finding", "inconclusive", "harness-invalid", "infrastructure-invalid"
                    }
                    or not isinstance(entry.get("matched_input_digests"), list)
                    or any(not isinstance(value, str) or not _DIGEST.fullmatch(value)
                           for value in entry["matched_input_digests"])
                    or complete_authority and (
                        entry["authority"] == "incomplete"
                        or not entry["matched_input_digests"]
                        or not isinstance(entry.get("primary_run_id"), str)
                        or not entry.get("primary_run_id")
                        or not isinstance(entry.get("stock_run_id"), str)
                        or not entry.get("stock_run_id")
                    )):
                raise ValueError("invalid stock replay authority entry")
            seen.add(entry["entry_id"])
        entry_authorities = {entry["authority"] for entry in replay_entries}
        expected_authority = (
            "patched-primary-stock-replay-incomplete"
            if "incomplete" in entry_authorities
            else "patched-primary-stock-disagrees"
            if "disagrees" in entry_authorities
            else "patched-primary-stock-confirmed"
        )
        stock_result = next(
            (value for value in (
                "harness-invalid", "infrastructure-invalid", "finding", "inconclusive", "pass"
            ) if any(entry["stock_classification"] == value for entry in replay_entries)),
            "inconclusive",
        )
        if (replay["authority"] != expected_authority
                or replay["stock_security_result"] != stock_result
                or any(
                    entry["authority"] == "confirmed"
                    and entry["primary_classification"] != entry["stock_classification"]
                    or entry["authority"] == "disagrees"
                    and entry["primary_classification"] == entry["stock_classification"]
                    for entry in replay_entries
                )):
            raise ValueError("stock replay authority does not match its entries")
        result["security_authority"] = replay["authority"]
        result["security_result"] = (
            replay["stock_security_result"]
            if replay["authority"] != "patched-primary-stock-replay-incomplete"
            else "inconclusive"
        )
    return result


def _schema_validate(document: dict[str, Any], *, selection: bool = False) -> None:
    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    if selection:
        schema = {"$ref": "#/$defs/selection", "$defs": schema["$defs"]}
    errors = sorted(Draft202012Validator(schema).iter_errors(document), key=lambda e: str(list(e.absolute_path)))
    if errors:
        raise ValueError(f"invalid campaign {'selection' if selection else 'manifest'}: {errors[0].message}")


def _freeze_definition(catalog: str, definition_id: str) -> dict[str, Any]:
    record = load_definition(catalog, definition_id)
    document = deepcopy(record.data)
    frozen = {"digest": _digest(document), "source_digest": "sha256:" + hashlib.sha256(record.source).hexdigest(), "document": document}
    if catalog == "scenarios":
        behavior = deepcopy(document)
        if isinstance(behavior.get("target"), dict):
            behavior["target"] = {k: v for k, v in behavior["target"].items() if k not in SCENARIO_TARGET_IDENTITY_FIELDS}
        # Same canonical behavior projection as retained measurement comparisons.
        frozen["behavior_digest"] = "sha256:" + hashlib.sha256(json.dumps(behavior, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return frozen


def _release_artifacts(release: dict[str, Any]) -> dict[str, Any]:
    artifacts = {key: None for key in _ARTIFACT_FIELDS}
    available = [a for a in release.get("artifacts", []) if a.get("availability") == "available"]
    images = [a for a in available if a.get("kind") in {"oci", "oci-derived"}]
    # Catalog order is an explicit selection preference, matching qualification's
    # exact_oci_reference contract. Preserve the full release alongside it.
    if images:
        artifacts["image_digest"] = images[0]["digest"]
        for key in ("binary_digest", "executable_digest"):
            value = images[0].get(key)
            if value is not None:
                if not _DIGEST.fullmatch(str(value)):
                    raise ValueError(f"release {key} is not immutable")
                artifacts[key] = value
    return artifacts


def _identity(implementation: str, selected: dict[str, Any], candidate: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any]:
    if selected["version"].lower() in {"latest", "stable", "main", "master", "any", "resolved-plan"}:
        raise ValueError("campaign versions must be explicit immutable selections")
    if selected["kind"] == "candidate":
        if implementation != candidate["implementation"]:
            raise ValueError("candidate implementation differs from selected node")
        return {"kind": "candidate", "implementation": implementation, "version": selected["version"],
                "source_revision": candidate["repository_commit"], "source_tree_digest": candidate["source_tree_digest"],
                "artifacts": {key: None for key in _ARTIFACT_FIELDS}, "qualification": {"status": "unknown"},
                "compatibility": deepcopy(selected.get("compatibility", {})), "capabilities": selected.get("capabilities", [])}
    release = deepcopy(resolve_release(catalog, implementation, selected["version"]))
    compatibility = {key: deepcopy(value) for key, value in release.get("compatibility", {}).items()
                     if key in _NETWORK_FIELDS and value is not None}
    for key in _NETWORK_FIELDS:
        value = release.get(key)
        if value is not None:
            if compatibility.get(key) is not None and compatibility[key] != value:
                raise ValueError(f"conflicting release {key} metadata")
            compatibility[key] = deepcopy(value)
    # Known metadata cannot be replaced by a conflicting operator assertion.
    for key, value in selected.get("compatibility", {}).items():
        if compatibility.get(key) is not None and compatibility[key] != value:
            raise ValueError(f"selected {key} conflicts with release metadata")
        compatibility[key] = value
    return {"kind": "release", "implementation": implementation, "version": release["version"],
            "source_revision": release["source_revision"], "source_tree_digest": None,
            "artifacts": _release_artifacts(release), "release": release,
            "qualification": {scope: deepcopy(resolve_verification(catalog, implementation, release["version"], scope)) for scope in ("cardano-only", "amaru-only", "mixed")},
            "compatibility": compatibility, "capabilities": selected.get("capabilities", [])}


def _check_pair(nodes: list[dict[str, Any]]) -> None:
    for field in _NETWORK_FIELDS:
        known = {serialize_campaign(n["identity"]["compatibility"][field]) for n in nodes if n["identity"]["compatibility"].get(field) is not None}
        if len(known) > 1:
            raise ValueError(f"incompatible topology {field}")


def _pair_status(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    missing = [{"node_id": node["node_id"], "field": field}
               for node in nodes for field in _NETWORK_FIELDS
               if node["identity"]["compatibility"].get(field) is None]
    return {"status": "pending-prerequisites" if missing else "declared-compatible", "missing": missing}


def _bound_scenario(document: dict[str, Any], topology: dict[str, Any]) -> dict[str, Any]:
    """Bind identities only; retain the source recipe's exact workload and oracles.

    This is a pending execution intent. Candidate artifact declarations and
    compatibility assertions still require independent runtime verification.
    """
    bound = deepcopy(document)
    nodes = {node["node_id"]: node["identity"] for node in topology["nodes"]}
    for node in bound.get("substrate", {}).get("nodes", []):
        if node["id"] not in nodes or nodes[node["id"]]["implementation"] != node["impl"]:
            raise ValueError("control/scenario node differs from the frozen topology")
        identity = nodes[node["id"]]
        node.update(version=identity["version"], source_revision=identity["source_revision"])
        for field in ("image", *_ARTIFACT_FIELDS):
            node.pop(field, None)
        image_digest = identity["artifacts"]["image_digest"]
        if image_digest:
            node["image_digest"] = image_digest
            artifacts = identity.get("release", {}).get("artifacts", [])
            image = next((a for a in artifacts if a.get("digest") == image_digest
                          and a.get("kind") in {"oci", "oci-derived"}), None)
            if image:
                node["image"] = image["reference"].split("@", 1)[0] + "@" + image_digest
    target = bound.get("target", {})
    matching = [identity for identity in nodes.values() if identity["implementation"] == target.get("implementation")]
    if matching:
        target.update(version=matching[0]["version"], source_revision=matching[0]["source_revision"])
    return bound


def _attachment_resolution(
    attachment: dict[str, Any], identity: dict[str, Any], variant: dict[str, Any] | None,
) -> dict[str, Any]:
    target = {"implementation": identity["implementation"], "version": identity["version"],
              "source_revision": identity["source_revision"], "source_tree_digest": identity["source_tree_digest"],
              "mode": identity["mode"], **identity["artifacts"]}
    if variant is not None:
        target.update(variant["artifacts"])
        return {"status": "pending-build", "reason": "candidate artifacts are declared, not independently observed",
                "target_identity": target, "build_variant": deepcopy(variant)}
    if target["image_digest"] is None:
        return {"status": "pending-artifact", "reason": "stock build image identity has not been observed", "target_identity": target}
    intent = SimpleNamespace(target={"implementation": identity["implementation"]}, measurement_profile=attachment["measurement_profile"], measurements=[])
    overrides = [{"id": measurement_id, "enabled": True} for measurement_id in attachment.get("additional_measurements", [])]
    try:
        resolution = resolve_measurements(intent, target_identity=target, capabilities=identity["capabilities"], run_overrides=overrides)
    except MeasurementResolutionError as exc:
        return {"status": "pending-prerequisites", "reason": str(exc), "target_identity": target, "resolution": exc.resolution}
    return {"status": "resolved-intent", "target_identity": target, "resolution": resolution}


def resolve_security_campaign(
    candidate: CandidateSnapshot,
    security_suite: dict[str, Any],
    measurement_suite: dict[str, Any],
    *, selection: dict[str, Any], catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve an independent JSON snapshot using only local catalog reads.

    Select entries by ID and implementations explicitly. Candidate builds are
    planned in all three modes from the same source tree. Supplied artifact
    digests describe expected future outputs; readiness and evidence stay pending.
    """
    selection = deepcopy(selection)
    _schema_validate(selection, selection=True)
    security = deepcopy(validate_security_campaign_suite(security_suite))
    measurements = deepcopy(validate_measurement_suite(measurement_suite))
    if not _SHA.fullmatch(candidate.repository_commit) or not _TREE.fullmatch(candidate.source_tree_digest):
        raise ValueError("candidate snapshot requires exact commit and source tree digest")
    source = {"implementation": candidate.implementation, "candidate_name": candidate.candidate_name,
              "repository_commit": candidate.repository_commit, "source_tree_digest": candidate.source_tree_digest,
              "source_path": f"dwarf/testplease/{candidate.implementation}/{candidate.candidate_name}"}
    implementations = selection["implementations"]
    if implementations.get(candidate.implementation, {}).get("kind") != "candidate" or sum(s["kind"] == "candidate" for s in implementations.values()) != 1:
        raise ValueError("select exactly the supplied candidate implementation")
    all_entries = {entry["id"]: entry for group in security["groups"] for entry in group["entries"]}
    entry_ids = selection.get("entry_ids", sorted(all_entries))
    if not set(entry_ids) <= all_entries.keys():
        raise ValueError("unknown campaign entry selection")
    entries = [all_entries[entry_id] for entry_id in sorted(entry_ids)]
    needed = {n["implementation"] for e in entries for n in e["topology"]["nodes"]}
    if candidate.implementation not in needed or needed != set(implementations):
        raise ValueError("select every topology implementation and include the candidate")
    catalog = validate_version_catalog(catalog if catalog is not None else load_version_catalog())
    identities = {impl: _identity(impl, value, source, catalog) for impl, value in sorted(implementations.items())}
    variants = []
    for mode in _MODES:
        declared = deepcopy(selection.get("build_variants", {}).get(mode, {}))
        variants.append({"mode": mode, "source": deepcopy(source), "status": "pending-build",
                         "artifacts": {key: declared.get("artifacts", {}).get(key) for key in _ARTIFACT_FIELDS},
                         "patch_set_digest": declared.get("patch_set_digest"),
                         "instrumentation_digest": declared.get("instrumentation_digest"),
                         "toolchain_digest": declared.get("toolchain_digest")})
    variants_by_mode = {variant["mode"]: variant for variant in variants}
    definitions: dict[str, dict[str, Any]] = {name: {} for name in ("scenarios", "profiles", "measurement-profiles", "measurements")}

    def freeze(catalog_name: str, definition_id: str) -> dict[str, Any]:
        if definition_id not in definitions[catalog_name]:
            definitions[catalog_name][definition_id] = _freeze_definition(catalog_name, definition_id)
        return definitions[catalog_name][definition_id]

    def freeze_scenario(definition_id: str) -> dict[str, Any]:
        scenario = freeze("scenarios", definition_id)
        profile_id = scenario["document"].get("profile")
        if profile_id:
            freeze("profiles", profile_id)
        return scenario

    # Include all exercise and conditional fallback recipes, including explicitly
    # blocked historical pins. They are retained contracts, never runnable defaults.
    for exercise in measurements["exercises"]:
        freeze("measurements", exercise["measurement_id"])
        freeze_scenario(exercise["scenario_id"])
        if exercise.get("fallback"):
            freeze_scenario(exercise["fallback"]["scenario_id"])
    calibration = deepcopy(_CALIBRATION_SCENARIOS[candidate.implementation])
    for scenario_id in calibration.values():
        freeze_scenario(scenario_id)
    topologies, frozen_entries = {}, []
    for entry in entries:
        scenario = freeze_scenario(entry["scenario_id"])
        control = freeze_scenario(entry["normal_control"]["scenario_id"])
        if entry["topology"].get("deployment_profile"):
            freeze("profiles", entry["topology"]["deployment_profile"])
        topology = deepcopy(entry["topology"])
        substrate = scenario["document"].get("substrate", {})
        if "topology" in substrate:
            topology["edges"] = deepcopy(substrate["topology"].get("edges", []))
        declared_nodes = {node["id"]: node for node in substrate.get("nodes", [])}
        for node in topology["nodes"]:
            node["identity"] = deepcopy(identities[node["implementation"]])
            if declared_nodes.get(node["node_id"], {}).get("role"):
                node["role"] = declared_nodes[node["node_id"]]["role"]
            modes = {a.get("target_mode", "stock") for a in entry["measurement_attachments"] if a["node_id"] == node["node_id"]}
            if len(modes) > 1:
                raise ValueError("one node requires one build mode within a security run")
            node["identity"]["mode"] = next(iter(modes), "stock")
            if node["identity"]["kind"] == "release" and node["identity"]["mode"] != "stock":
                raise ValueError("release build mode requires separately verified instrumentation provenance")
            qualification = node["identity"]["qualification"]
            if node["identity"]["kind"] == "release":
                scope = "mixed" if len({n["implementation"] for n in topology["nodes"]}) > 1 else ("amaru-only" if node["implementation"] == "amaru" else "cardano-only")
                node["identity"]["qualification"] = qualification[scope]
        _check_pair(topology["nodes"])
        topology_id = "topology-" + _digest(topology).split(":")[1]
        topology["id"] = topology_id
        topologies[topology_id] = topology
        attachments = []
        topology_nodes = {node["node_id"]: node for node in topology["nodes"]}
        for attachment in entry["measurement_attachments"]:
            profile = freeze("measurement-profiles", attachment["measurement_profile"])["document"]
            for tap in profile["measurements"]:
                freeze("measurements", tap["id"])
            for tap_id in attachment.get("additional_measurements", []):
                freeze("measurements", tap_id)
            identity = topology_nodes[attachment["node_id"]]["identity"]
            variant = variants_by_mode[identity["mode"]] if identity["kind"] == "candidate" else None
            attachments.append({**deepcopy(attachment), "topology_id": topology_id,
                                "planning_resolution": _attachment_resolution(attachment, identity, variant)})
        frozen_entries.append({"id": entry["id"], "topology_id": topology_id,
                               "contract": deepcopy(entry), "measurement_attachments": attachments,
                               "execution": {"status": "pending-runtime", "scenario_digest": scenario["digest"],
                                             "scenario": _bound_scenario(scenario["document"], topology),
                                             "normal_control": {"scenario_digest": control["digest"],
                                                                "scenario": _bound_scenario(control["document"], topology)},
                                             "compatibility": _pair_status(topology["nodes"])}})
    cells = []
    for topology_id, topology in sorted(topologies.items()):
        for node in topology["nodes"]:
            for exercise in measurements["exercises"]:
                if exercise["implementation"] != node["implementation"]:
                    continue
                tap = definitions["measurements"][exercise["measurement_id"]]["document"]
                cells.append({**deepcopy(exercise), "topology_id": topology_id, "node_id": node["node_id"],
                              "status": "pending", "target_modes": tap["compatibility"]["target_modes"],
                              "required_capabilities": tap["required_capabilities"],
                              "definition_digest": definitions["measurements"][exercise["measurement_id"]]["digest"]})
    refs = deepcopy(selection.get("threat_risk_refs", {"threat_model": [], "risk_register": []}))
    required_checks = set()
    for topology in topologies.values():
        implementations_in_topology = {n["implementation"] for n in topology["nodes"]}
        scope = "mixed" if len(implementations_in_topology) > 1 else ("amaru-only" if "amaru" in implementations_in_topology else "cardano-only")
        # Only inspect the pure gate classifier; run_qualification creates
        # resources even in dry-run mode and must never be called by planning.
        required_checks.update(classify_qualification(scope, {})["required_gates"])
    result = {"schema_version": "v1", "kind": "security-campaign-plan", "candidate": source,
              "selection": selection, "suites": {"security": {"digest": _digest(security), "document": security},
                                                   "measurement": {"digest": _digest(measurements), "document": measurements}},
              "threat_risk_refs": {"status": "declared" if any(refs.values()) else "unmapped", **refs},
              "definitions": definitions, "topologies": [topologies[key] for key in sorted(topologies)],
              "entries": frozen_entries, "build_variants": variants, "required_tap_cells": cells,
              "execution_policy": {
                  "strategy": "patched-first-stock-fallback",
                  "primary_mode": "patched",
                  "fallback_mode": "stock",
                  "calibration": {"status": "pending", **calibration},
                  "security_authority": {
                      "patched_results": "provisional",
                      "stock_replay": "required",
                      "replay_input": "exact-retained-input",
                  },
              },
              "security_result": "pending", "measurement_completeness": "pending",
              "readiness": {"status": "pending", "executed": False,
                            "qualification_contract": "qualify_node_versions.run_qualification",
                            "required_checks": sorted(required_checks)}}
    # Bind the reusable run-plan canonical digest as a separate compatibility aid.
    result["run_plan_digest"] = digest_run_plan(result)
    result["campaign_digest"] = digest_security_campaign(result)
    result["campaign_id"] = "campaign-" + result["campaign_digest"].split(":")[1]
    _schema_validate(result)
    return json.loads(serialize_campaign(result))
