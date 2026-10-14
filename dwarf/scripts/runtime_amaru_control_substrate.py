#!/usr/bin/env python3
"""Deploy a retained DWARF Amaru/mixed profile from the proven live control.

The Cardano producer cluster stays alive while the bootstrap producer snapshots
its coherent ChainDB.  Amaru starts from that bundle and peers directly with
the same producers.  A deployment is retained only after every runtime and
identity gate passes; partial or failed projects are removed with their volumes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any

import yaml


SCRIPT_DIR = Path(__file__).resolve().parent
DWARF_ROOT = SCRIPT_DIR.parent
for import_root in (DWARF_ROOT, SCRIPT_DIR):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from check_cardano_amaru_topology import collect_and_classify  # noqa: E402
from qualify_node_versions import (  # noqa: E402
    AMARU_RELAYS,
    BASE_PACKAGE,
    CARDANO_SERVICES,
    PROTECTED_PROJECTS,
    _amaru_log_text,
    _compose,
    _identity_observation,
    _project_containers,
    _project_is_fresh,
    _render_baseline,
    _run,
    cardano_experimental_protocols_policy,
    classify_log_signals,
    classify_terminal_runtime_failure,
    transform_compose_model,
)
from redeploy_cardano_amaru_topology import (  # noqa: E402
    fresh_readiness_proven,
    remember_bootstrap_evidence,
)


LIFECYCLE = "cardano_amaru_relay_bootstrap_control"
REQUIRED_GATES = {
    "fresh_state",
    "exact_identity",
    "required_services",
    "chain_progress",
    "fresh_window_advancement",
    "peer_formation",
    "consumer_amaru_only_path",
    "consumer_converged",
    "no_fatal_signatures",
    "no_restart_loop",
    "local_reward_window_contract",
}
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA64 = re.compile(r"^[0-9a-f]{64}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
TRANSFER_SOURCE_INSTALLATION_ID = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
)
HARDENING_EXCEPTION_SERVICES = {
    "configurator",
    "tracer",
    "tracer-sidecar",
    "log-tailer",
    "sidecar",
    "p1",
    "p2",
    "p3",
    "relay1",
    "relay2",
    "amaru-consumer",
    "amaru-consumer-seed",
    "amaru-relay-1",
    "amaru-relay-2",
    "amaru-target-extract",
    "plutus-v2-activate",
}
HARDENING_EXCEPTION = "proven-relay-bootstrap-writable-rootfs"
LOCAL_REWARD_WINDOW_EPOCH_FACTOR = 10
LOCAL_REWARD_WINDOW_CONTRACT = {
    "active_slot_coeff": Fraction(1, 5),
    "active_slot_coeff_inverse": 5,
    "epoch_length": 400,
    "epoch_length_scale_factor": 10,
    "randomness_stabilisation_window": 160,
    "reward_pulsing_window": 240,
    "security_parameter": 8,
    "slot_length_ms": 500,
    "stability_window": 120,
}


class RuntimeControlError(RuntimeError):
    """The retained live-control deployment cannot safely continue."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        stage: str | None = None,
    ) -> None:
        super().__init__(message)
        self.failure_code = code
        self.failure_stage = stage


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _operator_stock_registration_expected(config: dict[str, Any]) -> bool:
    """Return true when stock identity is governed by an operator registration."""

    if config.get("measurement_target_mode") != "stock":
        return False
    substrate = config.get("substrate")
    if not isinstance(substrate, dict):
        return False
    catalog = substrate.get("catalog_snapshot")
    return any(
        key in substrate or (isinstance(catalog, dict) and key in catalog)
        for key in ("operator_registration_id", "operator_overlay_revision")
    )


def _validated_operator_stock_content_id(
    config: dict[str, Any], *, amaru_image: str
) -> str | None:
    """Accept a bare stock config ID only through the persisted operator overlay."""

    if config.get("measurement_target_mode") != "stock" or not DIGEST.fullmatch(
        amaru_image
    ):
        return None
    substrate = config.get("substrate")
    if not isinstance(substrate, dict):
        return None
    registration_id = substrate.get("operator_registration_id")
    overlay_revision = substrate.get("operator_overlay_revision")
    catalog = substrate.get("catalog_snapshot")
    if (
        not isinstance(registration_id, str)
        or not registration_id
        or not isinstance(overlay_revision, str)
        or not DIGEST.fullmatch(overlay_revision)
        or not isinstance(catalog, dict)
        or catalog.get("operator_registration_id") != registration_id
        or catalog.get("operator_overlay_revision") != overlay_revision
    ):
        return None

    from profile_manager.deployment_versions import (
        DeploymentVersionGateError,
        operator_registration_release,
    )
    from profile_manager.version_discovery import load_operator_registration

    try:
        selected = load_operator_registration(registration_id)
        registration = selected["registration"]
        release = operator_registration_release(registration)
    except (DeploymentVersionGateError, KeyError, TypeError, ValueError):
        return None
    host_os = os.environ.get("DWARF_HOST_OS", "").strip().lower()
    host_arch = os.environ.get("DWARF_HOST_ARCH", "").strip().lower()
    if (
        release.get("implementation") != "amaru"
        or registration.get("config_image_id") != amaru_image
        or registration.get("platform") != f"{host_os}/{host_arch}"
        or registration.get("runtime_interface") != "extracted-binary"
        or config.get("amaru_runtime_interface") != "extracted-binary"
        or config.get("amaru_version") != registration.get("version")
    ):
        return None
    nodes = [
        node
        for node in substrate.get("nodes") or []
        if isinstance(node, dict)
        and node.get("impl") == "amaru"
        and not node.get("supporting")
    ]
    if not nodes or not all(
        node.get("image") == amaru_image
        and node.get("version") == registration.get("version")
        and node.get("amaru_runtime_interface") == "extracted-binary"
        and node.get("operator_registration") == registration
        for node in nodes
    ):
        return None
    return amaru_image


def validate_runtime_request(config: dict[str, Any]) -> None:
    project = str(config.get("compose_project") or "")
    runtime_root = Path(str(config.get("runtime_root") or ""))
    if config.get("lifecycle") != LIFECYCLE:
        raise RuntimeControlError(f"unsupported lifecycle; expected {LIFECYCLE}")
    if config.get("scope") not in {"amaru-only", "mixed"}:
        raise RuntimeControlError("live Amaru control scope must be amaru-only or mixed")
    if project in PROTECTED_PROJECTS:
        raise RuntimeControlError(f"compose project is protected: {project}")
    if not project.startswith("dwarf-profile-"):
        raise RuntimeControlError("retained project must use the dwarf-profile- prefix")
    if not str(config.get("profile_id") or "").startswith("profile-"):
        raise RuntimeControlError("profile_id must identify a DWARF profile")
    if not runtime_root.is_absolute() or runtime_root == Path("/"):
        raise RuntimeControlError("runtime_root must be a specific absolute path")
    mode = str(config.get("measurement_target_mode") or "stock")
    if mode not in {"stock", "patched"}:
        raise RuntimeControlError("measurement_target_mode must be stock or patched")
    if "@sha256:" not in str(config.get("cardano_image") or ""):
        raise RuntimeControlError("cardano_image must be pinned by digest")
    amaru_image = str(config.get("amaru_image") or "")
    identity = config.get("measurement_target_identity")
    imported_content_id = bool(
        mode == "patched"
        and DIGEST.fullmatch(amaru_image)
        and isinstance(identity, dict)
        and identity.get("image") == amaru_image
        and identity.get("image_digest") == amaru_image
        and identity.get("image_config_digest") == amaru_image
        and DIGEST.fullmatch(str(identity.get("image_manifest_digest") or ""))
        and isinstance(identity.get("source_image_reference"), str)
        and identity["source_image_reference"].endswith(
            "@" + str(identity["image_manifest_digest"])
        )
    )
    operator_stock_content_id = _validated_operator_stock_content_id(
        config, amaru_image=amaru_image
    )
    if (
        "@sha256:" not in amaru_image
        and not imported_content_id
        and not operator_stock_content_id
    ):
        if mode == "patched" and DIGEST.fullmatch(amaru_image):
            raise RuntimeControlError(
                "imported measurement target manifest provenance is invalid"
            )
        if (
            DIGEST.fullmatch(amaru_image)
            and _operator_stock_registration_expected(config)
        ):
            raise RuntimeControlError(
                "operator registration identity is invalid",
                code="operator_registration_identity_invalid",
                stage="retained_request_validation",
            )
        raise RuntimeControlError(
            "amaru_image must be pinned by manifest or verified config digest"
        )
    for key in ("supporting_cardano_version", "amaru_version"):
        if not str(config.get(key) or "").strip():
            raise RuntimeControlError(f"{key} is required")
    ownership = (config.get("substrate") or {}).get("controller_ownership")
    network_magic = (config.get("substrate") or {}).get("network_magic")
    if isinstance(network_magic, bool) or network_magic != 42:
        raise RuntimeControlError("proven Amaru lifecycle requires exact network magic 42")
    peer_sharing = config.get("peer_sharing")
    retained_peer_sharing = (config.get("substrate") or {}).get("peer_sharing")
    if peer_sharing is not False or retained_peer_sharing is not False:
        raise RuntimeControlError(
            "proven Amaru lifecycle requires peer_sharing=false in both request and substrate"
        )
    node_services = config.get("controller_node_services")
    if ownership is not None:
        if not isinstance(ownership, dict) or not all(
            str(ownership.get(key) or "") for key in ("installation_id", "topology_id")
        ):
            raise RuntimeControlError("controller ownership identity is invalid")
        if not isinstance(node_services, dict) or not node_services:
            raise RuntimeControlError("controller node-to-service identity is required")
    if mode == "patched":
        if config.get("amaru_runtime_interface") != "extracted-binary":
            raise RuntimeControlError(
                "patched target requires the extracted-binary Amaru runtime interface"
            )
        if not isinstance(identity, dict) or not identity:
            raise RuntimeControlError(
                "patched target requires measurement_target_identity"
            )
        if identity.get("target_mode") != "patched":
            raise RuntimeControlError("measurement_target_identity mode is not patched")
        if identity.get("image") != config.get("amaru_image"):
            raise RuntimeControlError(
                "measurement_target_identity image does not match amaru_image"
            )
        if identity.get("version") != config.get("amaru_version"):
            raise RuntimeControlError(
                "measurement_target_identity version does not match amaru_version"
            )
        if not SHA40.fullmatch(str(identity.get("source_revision") or "")):
            raise RuntimeControlError(
                "measurement_target_identity requires an exact source revision"
            )
        if not SHA64.fullmatch(str(identity.get("patch_set_sha256") or "")):
            raise RuntimeControlError(
                "measurement_target_identity requires an exact patch-set sha256"
            )
        for key in ("image_digest", "executable_digest", "build_result_sha256"):
            if not DIGEST.fullmatch(str(identity.get(key) or "")):
                raise RuntimeControlError(
                    f"measurement_target_identity requires immutable {key}"
                )
        if DIGEST.fullmatch(amaru_image):
            manifest_digest = str(identity.get("image_manifest_digest") or "")
            config_digest = str(identity.get("image_config_digest") or "")
            qualification_image_id = str(
                identity.get("qualification_image_id") or ""
            )
            transfer_manifest_sha256 = str(
                identity.get("transfer_manifest_sha256") or ""
            )
            source_reference = str(identity.get("source_image_reference") or "")
            if (
                not DIGEST.fullmatch(manifest_digest)
                or config_digest != amaru_image
                or not DIGEST.fullmatch(qualification_image_id)
                or not DIGEST.fullmatch(transfer_manifest_sha256)
                or not source_reference.endswith("@" + manifest_digest)
            ):
                raise RuntimeControlError(
                    "imported measurement target manifest provenance is invalid"
                )
            source_installation_id = str(
                identity.get("transfer_source_installation_id") or ""
            )
            if not TRANSFER_SOURCE_INSTALLATION_ID.fullmatch(
                source_installation_id
            ):
                raise RuntimeControlError(
                    "imported measurement target source installation identity is invalid"
                )
    if config.get("plutus_v2_genesis") is True:
        configured_path = str(config.get("plutus_v2_cost_model_path") or "")
        if not configured_path:
            raise RuntimeControlError("PlutusV2 cost model path is required")
        model_path = Path(configured_path)
        if not model_path.is_absolute():
            model_path = DWARF_ROOT / model_path
        if not model_path.is_file():
            raise RuntimeControlError(f"PlutusV2 cost model path does not exist: {model_path}")
        expected = str(config.get("plutus_v2_cost_model_sha256") or "")
        if not SHA64.fullmatch(expected):
            raise RuntimeControlError("PlutusV2 cost model sha256 is required")
        if hashlib.sha256(model_path.read_bytes()).hexdigest() != expected:
            raise RuntimeControlError("PlutusV2 cost model sha256 does not match")
    override = config.get("kes_genesis_override")
    if override is not None:
        raise RuntimeControlError(
            "kes_genesis_override is supported only by the Cardano-only runtime; "
            "Amaru/mixed execution requires an independently qualified matching "
            "Amaru KES contract"
        )


def _plutus_v2_model_path(config: dict[str, Any]) -> Path:
    path = Path(str(config["plutus_v2_cost_model_path"]))
    return path if path.is_absolute() else DWARF_ROOT / path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _plutus_v2_model(document: Any, *, source: str) -> list[int]:
    try:
        model = document["costModels"]["PlutusV2"]
    except (KeyError, TypeError):
        raise RuntimeControlError(f"{source} has no PlutusV2 cost model") from None
    if (
        not isinstance(model, list)
        or len(model) != 175
        or any(isinstance(value, bool) or not isinstance(value, int) for value in model)
    ):
        raise RuntimeControlError(f"{source} has an invalid PlutusV2 cost model")
    return model


def verify_plutus_v2_evidence(
    *,
    pinned_model_path: Path,
    expected_sha256: str,
    genesis_paths: list[Path],
    conway_genesis_paths: list[Path],
    live_protocol_parameters_path: Path,
) -> dict[str, Any]:
    if _sha256(pinned_model_path) != expected_sha256:
        raise RuntimeControlError("pinned PlutusV2 cost model sha256 does not match")
    pinned = json.loads(pinned_model_path.read_text(encoding="utf-8"))
    if (
        not isinstance(pinned, list)
        or len(pinned) != 175
        or any(isinstance(value, bool) or not isinstance(value, int) for value in pinned)
    ):
        raise RuntimeControlError("pinned PlutusV2 cost model is not a 175-integer array")
    if len(genesis_paths) != 3:
        raise RuntimeControlError("three generated Alonzo genesis files are required")
    genesis_evidence = []
    for path in genesis_paths:
        body = json.loads(path.read_text(encoding="utf-8"))
        if _plutus_v2_model(body, source=str(path)) != pinned:
            raise RuntimeControlError(f"generated PlutusV2 cost model does not match: {path}")
        genesis_evidence.append(
            {"kind": "alonzo", "path": str(path), "sha256": _sha256(path)}
        )
    if len({item["sha256"] for item in genesis_evidence}) != 1:
        raise RuntimeControlError("generated Alonzo genesis files are not identical")
    if len(conway_genesis_paths) != 3:
        raise RuntimeControlError("three generated Conway genesis files are required")
    conway_evidence = []
    for path in conway_genesis_paths:
        body = json.loads(path.read_text(encoding="utf-8"))
        drep = body.get("dRepVotingThresholds") or {}
        pools = body.get("poolVotingThresholds") or {}
        committee = body.get("committee") or {}
        if (
            drep.get("ppTechnicalGroup") != 0
            or drep.get("ppNetworkGroup") == 0
            or pools.get("ppSecurityGroup") == 0
            or committee.get("members") != {}
            or committee.get("threshold") != 0
            or body.get("committeeMinSize") != 0
            or int(body.get("govActionDeposit") or 0) <= 0
            or (body.get("constitution") or {}).get("script") is not None
        ):
            raise RuntimeControlError(
                f"generated Conway governance settings are not narrowly scoped: {path}"
            )
        conway_evidence.append(
            {"kind": "conway", "path": str(path), "sha256": _sha256(path)}
        )
    if len({item["sha256"] for item in conway_evidence}) != 1:
        raise RuntimeControlError("generated Conway genesis files are not identical")
    live_body = json.loads(live_protocol_parameters_path.read_text(encoding="utf-8"))
    if _plutus_v2_model(live_body, source="live protocol parameters") != pinned:
        raise RuntimeControlError("live PlutusV2 cost model does not match pinned model")
    return {
        "verified": True,
        "pinned_cost_model": {
            "path": str(pinned_model_path),
            "sha256": expected_sha256,
        },
        "cost_model_entry_count": len(pinned),
        "generated_genesis": genesis_evidence + conway_evidence,
        "live_protocol_parameters": {
            "path": str(live_protocol_parameters_path),
            "sha256": _sha256(live_protocol_parameters_path),
            "plutus_v2_cost_model_sha256": hashlib.sha256(
                json.dumps(pinned, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        },
    }


def _relative_to(path: Path, parent: Path) -> Path | None:
    try:
        return path.resolve().relative_to(parent.resolve())
    except ValueError:
        return None


def _published_ports_are_loopback_only(service: dict[str, Any]) -> bool:
    for port in service.get("ports") or []:
        if isinstance(port, dict):
            target = str(port.get("target") or "")
            published = port.get("published")
            host_ip = str(port.get("host_ip") or "")
            if (
                target == "8787"
                or str(published or "") == "8787"
                or published is None
                or host_ip not in {"127.0.0.1", "::1"}
            ):
                return False
            continue
        value = str(port)
        if value.startswith("127.0.0.1:") or value.startswith("[::1]:"):
            fields = value.removesuffix("/tcp").removesuffix("/udp").rsplit(":", 2)
            if len(fields) == 3 and "8787" not in fields[-2:]:
                continue
        return False
    return True


def _map_host_bind_sources(
    model: dict[str, Any], config: dict[str, Any], *, base_package: Path
) -> None:
    """Translate controller-container bind sources to the exact Docker-host root."""

    substrate = config.get("substrate") or {}
    host_runtime_text = str(substrate.get("docker_host_runtime_root") or "")

    def relative_bind(source: str) -> bool:
        return source.startswith(".") or "/" in source or "\\" in source

    if isinstance(substrate.get("controller_ownership"), dict):
        for service_name, service in (model.get("services") or {}).items():
            if not isinstance(service, dict):
                raise RuntimeControlError(f"{service_name} service must be a mapping")
            for volume in service.get("volumes") or []:
                if isinstance(volume, dict) and volume.get("type") == "bind":
                    source = str(volume.get("source") or "")
                elif isinstance(volume, str):
                    source = volume.partition(":")[0]
                    if not relative_bind(source):
                        continue
                else:
                    continue
                if not Path(source).is_absolute():
                    raise RuntimeControlError(
                        f"{service_name} relative bind source is forbidden"
                    )
    if (
        not host_runtime_text
        or not config.get("runtime_asset_root")
        or not config.get("docker_host_runtime_asset_root")
    ):
        return
    local_runtime = Path(str(config["runtime_root"]))
    host_runtime = Path(host_runtime_text)
    local_assets = Path(
        str(config.get("runtime_asset_root") or local_runtime / "adapter-assets")
    )
    host_assets = Path(
        str(
            config.get("docker_host_runtime_asset_root")
            or host_runtime / "adapter-assets"
        )
    )
    if not host_runtime.is_absolute() or ".." in host_runtime.parts:
        raise RuntimeControlError("Docker host runtime root is invalid")

    def mapped_source(source: str) -> str:
        candidate = Path(source)
        if not candidate.is_absolute():
            return source
        relative = _relative_to(candidate, base_package)
        if relative is not None:
            return str(host_assets / relative)
        relative = _relative_to(candidate, local_runtime)
        if relative is not None:
            return str(host_runtime / relative)
        relative = _relative_to(candidate, local_assets)
        if relative is not None:
            return str(host_assets / relative)
        raise RuntimeControlError(
            f"runtime bind source is outside the controller-owned roots: {source}"
        )

    for service_name, service in (model.get("services") or {}).items():
        if not isinstance(service, dict):
            raise RuntimeControlError(f"{service_name} service must be a mapping")
        volumes = service.get("volumes") or []
        if not isinstance(volumes, list):
            raise RuntimeControlError(f"{service_name} volumes must be a list")
        rewritten: list[Any] = []
        for volume in volumes:
            if isinstance(volume, dict):
                updated = dict(volume)
                if updated.get("type") == "bind":
                    source = str(updated.get("source") or "")
                    if not Path(source).is_absolute():
                        raise RuntimeControlError(
                            f"{service_name} relative bind source is forbidden"
                        )
                    updated["source"] = mapped_source(source)
                rewritten.append(updated)
                continue
            if not isinstance(volume, str):
                raise RuntimeControlError(f"{service_name} volume is invalid")
            source, separator, remainder = volume.partition(":")
            if separator and relative_bind(source) and not Path(source).is_absolute():
                raise RuntimeControlError(
                    f"{service_name} relative bind source is forbidden"
                )
            rewritten.append(
                f"{mapped_source(source)}:{remainder}"
                if separator and Path(source).is_absolute()
                else volume
            )
        service["volumes"] = rewritten


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader which rejects lossy duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as error:
            raise yaml.constructor.ConstructorError(
                None,
                None,
                "unhashable YAML mapping key",
                key_node.start_mark,
            ) from error
        if duplicate:
            raise yaml.constructor.ConstructorError(
                None,
                None,
                f"duplicate YAML key: {key}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def _merged_testnet_document(path: Path) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    try:
        documents = list(
            yaml.load_all(
                path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader
            )
        )
    except (OSError, yaml.YAMLError) as error:
        raise RuntimeControlError(
            f"local reward-window contract cannot read testnet YAML: {path}: {error}"
        ) from error
    for document in documents:
        if document is None:
            continue
        if not isinstance(document, dict):
            raise RuntimeControlError(
                "local reward-window contract requires mapping YAML documents"
            )
        collision = set(merged).intersection(document)
        if collision:
            raise RuntimeControlError(
                "local reward-window contract rejects duplicate YAML key across "
                f"documents: {sorted(collision)[0]}"
            )
        merged.update(document)
    return merged


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeControlError(
            f"local reward-window contract cannot read {label}: {path}"
        ) from error
    if not isinstance(body, dict):
        raise RuntimeControlError(
            f"local reward-window contract requires a {label} object"
        )
    return body


def _exact_integer(value: Any, *, label: str) -> int:
    if isinstance(value, bool):
        raise RuntimeControlError(
            f"local reward-window contract requires exact integer {label}"
        )
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        return int(value)
    raise RuntimeControlError(
        f"local reward-window contract requires exact integer {label}"
    )


def _exact_milliseconds(value: Any, *, label: str) -> int:
    ratio = Fraction(str(value)) * 1000
    if ratio.denominator != 1:
        raise RuntimeControlError(
            f"local reward-window contract requires exact integer {label} milliseconds"
        )
    return _exact_integer(ratio.numerator, label=f"{label} milliseconds")


def _validate_local_reward_window_contract(
    *,
    testnet_path: Path,
    global_parameters_path: Path,
    era_history_path: Path,
) -> dict[str, Any]:
    """Prove the generated local Cardano and Amaru timing inputs are identical.

    Cardano starts reward pulsing after ``4k/f`` slots.  The local controller
    profile follows the standard ``epochLength = 10k/f`` relation so the pulse
    has a non-empty completion window.  Amaru models that same relation as
    ``inverse(f) * epoch_length_scale_factor * k``.
    """

    testnet = _merged_testnet_document(Path(testnet_path))
    globals_doc = _read_json_object(
        Path(global_parameters_path), label="Amaru global parameters"
    )
    era_doc = _read_json_object(Path(era_history_path), label="Amaru era history")
    try:
        epoch_length = _exact_integer(testnet["epochLength"], label="epochLength")
        coefficient = Fraction(str(testnet["activeSlotsCoeff"]))
        monetary_expansion = Fraction(str(testnet["protocolParams"]["rho"]))
        treasury_cut = Fraction(str(testnet["protocolParams"]["tau"]))
        security_parameter = _exact_integer(
            testnet["securityParam"], label="securityParam"
        )
        protocol_security_parameter = _exact_integer(
            testnet["protocolConsts"]["k"], label="protocolConsts.k"
        )
        amaru_security_parameter = _exact_integer(
            globals_doc["consensus_security_param"],
            label="Amaru consensus_security_param",
        )
        amaru_coefficient_inverse = _exact_integer(
            globals_doc["active_slot_coeff_inverse"],
            label="Amaru active_slot_coeff_inverse",
        )
        scale_factor = _exact_integer(
            globals_doc["epoch_length_scale_factor"],
            label="Amaru epoch_length_scale_factor",
        )
        era_stability_window = _exact_integer(
            era_doc["stability_window"], label="era stability_window"
        )
        eras = era_doc["eras"]
        if not isinstance(eras, list) or len(eras) != 1:
            raise RuntimeControlError(
                "local reward-window contract requires one operative Conway era"
            )
        era = eras[0]
        era_params = era["params"]
        if era_params["era_name"] != "Conway" or era.get("end") is not None:
            raise RuntimeControlError(
                "local reward-window contract requires one operative Conway era"
            )
        era_epoch_length = _exact_integer(
            era_params["epoch_size_slots"], label="era epoch_size_slots"
        )
        era_slot_length_ms = _exact_integer(
            era_params["slot_length"], label="era slot_length"
        )
        cardano_slot_length_ms = _exact_milliseconds(
            testnet["slotLength"], label="slot"
        )
    except (KeyError, IndexError, TypeError, ValueError, ZeroDivisionError) as error:
        raise RuntimeControlError(
            "local reward-window contract is missing exact generated parameters"
        ) from error
    if coefficient <= 0 or coefficient > 1 or coefficient.numerator != 1:
        raise RuntimeControlError(
            "local reward-window contract requires activeSlotsCoeff = 1/n"
        )
    coefficient_inverse = coefficient.denominator
    expected_epoch_length = (
        coefficient_inverse
        * LOCAL_REWARD_WINDOW_EPOCH_FACTOR
        * security_parameter
    )
    stability_window = 3 * security_parameter * coefficient_inverse
    randomness_window = 4 * security_parameter * coefficient_inverse
    reward_pulsing_window = epoch_length - randomness_window
    consistent = (
        coefficient == LOCAL_REWARD_WINDOW_CONTRACT["active_slot_coeff"]
        and coefficient_inverse
        == LOCAL_REWARD_WINDOW_CONTRACT["active_slot_coeff_inverse"]
        and security_parameter
        == LOCAL_REWARD_WINDOW_CONTRACT["security_parameter"]
        and protocol_security_parameter
        == LOCAL_REWARD_WINDOW_CONTRACT["security_parameter"]
        and amaru_security_parameter
        == LOCAL_REWARD_WINDOW_CONTRACT["security_parameter"]
        and amaru_coefficient_inverse
        == LOCAL_REWARD_WINDOW_CONTRACT["active_slot_coeff_inverse"]
        and scale_factor
        == LOCAL_REWARD_WINDOW_CONTRACT["epoch_length_scale_factor"]
        and epoch_length == LOCAL_REWARD_WINDOW_CONTRACT["epoch_length"]
        and epoch_length == expected_epoch_length
        and era_epoch_length == LOCAL_REWARD_WINDOW_CONTRACT["epoch_length"]
        and era_stability_window
        == LOCAL_REWARD_WINDOW_CONTRACT["stability_window"]
        and stability_window == LOCAL_REWARD_WINDOW_CONTRACT["stability_window"]
        and randomness_window
        == LOCAL_REWARD_WINDOW_CONTRACT["randomness_stabilisation_window"]
        and reward_pulsing_window
        == LOCAL_REWARD_WINDOW_CONTRACT["reward_pulsing_window"]
        and era_slot_length_ms
        == LOCAL_REWARD_WINDOW_CONTRACT["slot_length_ms"]
        and cardano_slot_length_ms
        == LOCAL_REWARD_WINDOW_CONTRACT["slot_length_ms"]
        and monetary_expansion == Fraction(3, 1000)
        and treasury_cut == Fraction(1, 5)
        and reward_pulsing_window > 0
    )
    if not consistent:
        raise RuntimeControlError(
            "local reward-window contract is incompatible with the approved tuple: "
            "Cardano and Amaru require epoch=400, f=1/5, k=8, scale=10, "
            "stability=120, and slotLength=500ms"
        )
    return {
        "active_slot_coeff": f"{coefficient.numerator}/{coefficient.denominator}",
        "active_slot_coeff_inverse": coefficient_inverse,
        "epoch_length": epoch_length,
        "epoch_length_scale_factor": scale_factor,
        "monetary_expansion": "3/1000",
        "randomness_stabilisation_window": randomness_window,
        "reward_pulsing_window": reward_pulsing_window,
        "security_parameter": security_parameter,
        "slot_length_ms": cardano_slot_length_ms,
        "stability_window": stability_window,
        "treasury_cut": "1/5",
        "verified": True,
    }


def _replace_unique_line(text: str, pattern: str, replacement: str, *, label: str) -> str:
    updated, count = re.subn(pattern, replacement, text, flags=re.MULTILINE)
    if count != 1:
        raise RuntimeControlError(
            f"local reward-window contract expected one {label}, found {count}"
        )
    return updated


def _apply_local_reward_window_contract(asset_root: Path) -> dict[str, Any]:
    """Correct only the staged controller-owned local assets.

    The shared Antithesis package remains untouched.  The sole accepted legacy
    input is the reproduced k=20/f=.2/epoch=400 tuple whose reward window is
    empty in cardano-node; any other unexpected source tuple fails closed.
    """

    testnet_path = Path(asset_root) / "testnet.yaml"
    globals_path = Path(asset_root) / "amaru-runtime" / "global-parameters.json"
    era_path = Path(asset_root) / "amaru-runtime" / "era-history.json"
    source_hashes = {
        "testnet_yaml": _sha256(testnet_path),
        "global_parameters": _sha256(globals_path),
        "era_history": _sha256(era_path),
    }
    testnet = _merged_testnet_document(testnet_path)
    globals_doc = _read_json_object(globals_path, label="Amaru global parameters")
    era_doc = _read_json_object(era_path, label="Amaru era history")
    try:
        legacy_tuple = (
            _exact_integer(testnet.get("epochLength"), label="epochLength"),
            Fraction(str(testnet.get("activeSlotsCoeff"))),
            _exact_integer(testnet.get("securityParam"), label="securityParam"),
            _exact_integer(
                (testnet.get("protocolConsts") or {}).get("k"),
                label="protocolConsts.k",
            ),
            _exact_integer(
                globals_doc.get("consensus_security_param"),
                label="Amaru consensus_security_param",
            ),
            _exact_integer(
                globals_doc.get("epoch_length_scale_factor"),
                label="Amaru epoch_length_scale_factor",
            ),
            _exact_integer(
                era_doc.get("stability_window"), label="era stability_window"
            ),
        )
    except (TypeError, ValueError, ZeroDivisionError) as error:
        raise RuntimeControlError(
            "local reward-window contract has invalid staged source values"
        ) from error
    expected_legacy = (400, Fraction(1, 5), 20, 20, 20, 4, 300)
    if legacy_tuple != expected_legacy:
        # An already-correct staged package is accepted, but arbitrary values
        # are never silently normalized.
        contract = _validate_local_reward_window_contract(
            testnet_path=testnet_path,
            global_parameters_path=globals_path,
            era_history_path=era_path,
        )
        contract["artifacts"] = {
            "source_sha256": dict(source_hashes),
            "staged_sha256": dict(source_hashes),
        }
        return contract

    text = testnet_path.read_text(encoding="utf-8")
    text = _replace_unique_line(
        text, r"^  k: 20\s*$", "  k: 8", label="protocolConsts.k"
    )
    text = _replace_unique_line(
        text, r"^securityParam: 20\s*$", "securityParam: 8", label="securityParam"
    )
    text = _replace_unique_line(
        text,
        r"^protocolParams:\s*$",
        "protocolParams:\n  rho: 0.003\n  tau: 0.2",
        label="protocolParams",
    )
    testnet_path.write_text(text, encoding="utf-8")
    globals_doc["consensus_security_param"] = 8
    globals_doc["epoch_length_scale_factor"] = LOCAL_REWARD_WINDOW_EPOCH_FACTOR
    globals_path.write_text(
        json.dumps(globals_doc, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    era_doc["stability_window"] = 120
    era_path.write_text(
        json.dumps(era_doc, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    contract = _validate_local_reward_window_contract(
        testnet_path=testnet_path,
        global_parameters_path=globals_path,
        era_history_path=era_path,
    )
    contract["artifacts"] = {
        "source_sha256": source_hashes,
        "staged_sha256": {
            "testnet_yaml": _sha256(testnet_path),
            "global_parameters": _sha256(globals_path),
            "era_history": _sha256(era_path),
        },
    }
    return contract


def _stage_runtime_assets(
    config: dict[str, Any], *, base_package: Path
) -> dict[str, Any]:
    runtime_root = Path(str(config["runtime_root"]))
    asset_root = runtime_root / "adapter-assets"
    if asset_root.exists():
        raise RuntimeControlError(
            "runtime adapter assets already exist",
            code="runtime_assets_exist",
            stage="preflight",
        )
    required_files = (
        "testnet.yaml",
        "tracer-config.yaml",
        "relay-topology.json",
        "amaru-consumer-topology.json",
    )
    for name in required_files:
        source = base_package / name
        if not source.is_file():
            raise RuntimeControlError(
                f"required runtime asset is missing: {name}",
                code="required_runtime_asset_missing",
                stage="asset_staging",
            )
    source_runtime = base_package / "amaru-runtime"
    if not source_runtime.is_dir():
        raise RuntimeControlError(
            "required Amaru runtime assets are missing",
            code="required_runtime_asset_missing",
            stage="asset_staging",
        )
    source_model = None
    if config.get("plutus_v2_genesis") is True:
        source_model = _plutus_v2_model_path(config)

    try:
        asset_root.mkdir(parents=True)
        for name in required_files:
            shutil.copy2(base_package / name, asset_root / name)
        shutil.copytree(source_runtime, asset_root / "amaru-runtime")
        reward_window_contract = _apply_local_reward_window_contract(asset_root)
    except PermissionError as exc:
        raise RuntimeControlError(
            "runtime asset staging permission denied",
            code="runtime_asset_permission_denied",
            stage="asset_staging",
        ) from exc
    host_root = Path(
        str((config.get("substrate") or {}).get("docker_host_runtime_root") or "")
    )
    effective = {
        **config,
        "runtime_asset_root": str(asset_root),
        "docker_host_runtime_asset_root": str(host_root / "adapter-assets"),
        "local_reward_window_contract": reward_window_contract,
    }
    if source_model is not None:
        staged_model = asset_root / "plutus-v2-cost-model.json"
        try:
            shutil.copy2(source_model, staged_model)
        except PermissionError as exc:
            raise RuntimeControlError(
                "runtime asset staging permission denied",
                code="runtime_asset_permission_denied",
                stage="asset_staging",
            ) from exc
        effective["plutus_v2_cost_model_path"] = str(staged_model)
    return effective


def collect_local_reward_window_evidence(
    config: dict[str, Any], *, project: str, evidence_root: Path
) -> dict[str, Any]:
    """Verify the files actually consumed by both live client runtimes."""

    expected = config.get("local_reward_window_contract")
    if not isinstance(expected, dict) or expected.get("verified") is not True:
        raise RuntimeControlError("local reward-window contract was not staged")
    containers = _project_containers(project)
    required = (*CARDANO_SERVICES, *AMARU_RELAYS)
    missing = sorted(service for service in required if service not in containers)
    if missing:
        raise RuntimeControlError(
            "local reward-window runtime evidence is missing services: "
            + ", ".join(missing)
        )
    output_root = Path(evidence_root) / "local-reward-window"
    output_root.mkdir(parents=True, exist_ok=False)

    def copy_from(service: str, source: str, destination: Path) -> None:
        copied = _run(
            ["docker", "cp", f"{containers[service]}:{source}", str(destination)],
            timeout=120,
        )
        if copied.returncode == 0 and destination.is_file():
            return
        # Docker's archive-based copy fails when a sibling below the same
        # read-only volume is overlaid by a bind mount (the relay/consumer
        # topology file is exactly that shape).  Stream the one requested JSON
        # file without traversing the mount tree; argv is fixed and shell-free.
        streamed = _run(
            ["docker", "exec", containers[service], "cat", source], timeout=120
        )
        if streamed.returncode != 0 or not streamed.stdout:
            raise RuntimeControlError(
                f"cannot retain local reward-window input from {service}:{source}"
            )
        try:
            destination.write_text(streamed.stdout, encoding="utf-8")
        except OSError as error:
            raise RuntimeControlError(
                f"cannot retain local reward-window input from {service}:{source}"
            ) from error

    generated: dict[str, Any] = {}
    generated_normalized_sha256: str | None = None
    for service in CARDANO_SERVICES:
        destination = output_root / f"{service}-shelley-genesis.json"
        copy_from(service, "/configs/configs/shelley-genesis.json", destination)
        genesis = _read_json_object(destination, label=f"{service} Shelley genesis")
        try:
            observed = {
                "epoch_length": _exact_integer(
                    genesis["epochLength"], label=f"{service} epochLength"
                ),
                "security_parameter": _exact_integer(
                    genesis["securityParam"], label=f"{service} securityParam"
                ),
                "slot_length_ms": _exact_milliseconds(
                    genesis["slotLength"], label=f"{service} slotLength"
                ),
                "active_slot_coeff": str(Fraction(str(genesis["activeSlotsCoeff"]))),
                "monetary_expansion": str(
                    Fraction(str(genesis["protocolParams"]["rho"]))
                ),
                "treasury_cut": str(
                    Fraction(str(genesis["protocolParams"]["tau"]))
                ),
                "sha256": _sha256(destination),
                "normalized_sha256": hashlib.sha256(
                    json.dumps(
                        genesis, sort_keys=True, separators=(",", ":")
                    ).encode("utf-8")
                ).hexdigest(),
            }
        except (KeyError, TypeError, ValueError, ZeroDivisionError) as error:
            raise RuntimeControlError(
                f"local reward-window generated genesis is incomplete for {service}"
            ) from error
        for key in (
            "epoch_length",
            "security_parameter",
            "slot_length_ms",
            "active_slot_coeff",
            "monetary_expansion",
            "treasury_cut",
        ):
            if observed[key] != expected[key]:
                raise RuntimeControlError(
                    f"local reward-window generated genesis mismatch for {service}: {key}"
                )
        if generated_normalized_sha256 is None:
            generated_normalized_sha256 = observed["normalized_sha256"]
        elif observed["normalized_sha256"] != generated_normalized_sha256:
            raise RuntimeControlError(
                f"local reward-window generated genesis digest mismatch for {service}"
            )
        generated[service] = observed

    relay_inputs: dict[str, Any] = {}
    staged_testnet = Path(str(config["runtime_asset_root"])) / "testnet.yaml"
    expected_hashes = (expected.get("artifacts") or {}).get("staged_sha256") or {}
    for service in AMARU_RELAYS:
        globals_destination = output_root / f"{service}-global-parameters.json"
        era_destination = output_root / f"{service}-era-history.json"
        copy_from(
            service,
            "/amaru-runtime/global-parameters.json",
            globals_destination,
        )
        copy_from(service, "/amaru-runtime/era-history.json", era_destination)
        verified = _validate_local_reward_window_contract(
            testnet_path=staged_testnet,
            global_parameters_path=globals_destination,
            era_history_path=era_destination,
        )
        for key, value in verified.items():
            if expected.get(key) != value:
                raise RuntimeControlError(
                    f"local reward-window Amaru input mismatch for {service}: {key}"
                )
        hashes = {
            "global_parameters": _sha256(globals_destination),
            "era_history": _sha256(era_destination),
        }
        if any(hashes[key] != expected_hashes.get(key) for key in hashes):
            raise RuntimeControlError(
                f"local reward-window Amaru input hash mismatch for {service}"
            )
        relay_inputs[service] = {**verified, "sha256": hashes}

    receipt = {
        "verified": True,
        "contract": expected,
        "cardano_generated_genesis": generated,
        "amaru_runtime_inputs": relay_inputs,
    }
    _write_json(output_root / "receipt.json", receipt)
    return receipt



def prepare_runtime_model(
    baseline: dict[str, Any],
    config: dict[str, Any],
    *,
    base_package: Path = BASE_PACKAGE,
) -> dict[str, Any]:
    validate_runtime_request(config)
    model = transform_compose_model(
        baseline,
        scope=str(config["scope"]),
        project=str(config["compose_project"]),
        cardano_image=str(config["cardano_image"]),
        amaru_image=str(config["amaru_image"]),
        allowed_project_prefix="dwarf-profile-",
        amaru_runtime_interface=config.get("amaru_runtime_interface"),
        amaru_json_traces=bool(config.get("amaru_json_traces", False)),
        cardano_experimental_protocols=(
            bool(config["cardano_experimental_protocols"])
            if config.get("cardano_experimental_protocols") is not None
            else cardano_experimental_protocols_policy(
                str(config["scope"]), config.get("supporting_cardano_version")
            )
        ),
    )
    # Controller-owned local profiles promise one fixed Cardano upstream per
    # Amaru relay and advertise peer sharing as disabled.  Keep this policy
    # local to the controller adapter: qualification and Antithesis transforms
    # retain their existing peer-selection behavior.
    for service_name in ("amaru-relay-1", "amaru-relay-2"):
        relay = model["services"].get(service_name)
        if not isinstance(relay, dict):
            raise RuntimeControlError(f"{service_name} is missing from runtime model")
        environment = relay.setdefault("environment", {})
        if not isinstance(environment, dict):
            raise RuntimeControlError(f"{service_name} environment must be a mapping")
        environment["AMARU_PEER_MIX"] = "static!1"
        environment["AMARU_UPSTREAM_PEERS"] = "1"
    if config.get("plutus_v2_genesis") is True:
        configurator = model["services"]["configurator"]
        volumes = configurator.setdefault("volumes", [])
        if not isinstance(volumes, list):
            raise RuntimeControlError("configurator volumes must be a list")
        model_path = _plutus_v2_model_path(config)
        volumes.append(f"{model_path}:/dwarf/plutus-v2-cost-model.json:ro")
        command = configurator.get("command")
        if not isinstance(command, list) or len(command) != 1:
            raise RuntimeControlError("configurator command must contain one shell program")
        command[0] = command[0].rstrip() + """
for dir in /configs/[123]; do
  genesis="$$dir/configs/alonzo-genesis.json"
  conway_genesis="$$dir/configs/conway-genesis.json"
  jq --slurpfile model /dwarf/plutus-v2-cost-model.json '.costModels.PlutusV2 = $$model[0]' "$$genesis" > "$$genesis.tmp"
  mv "$$genesis.tmp" "$$genesis"
  jq -e '.costModels.PlutusV2 | length == 175' "$$genesis" >/dev/null
  jq '.dRepVotingThresholds.ppTechnicalGroup = 0
      | .committee.members = {}
      | .committee.threshold = 0
      | .committeeMinSize = 0
      | .constitution.script = null' "$$conway_genesis" > "$$conway_genesis.tmp"
  mv "$$conway_genesis.tmp" "$$conway_genesis"
done
mkdir -p /plutus-v2-activation
cp /dwarf/plutus-v2-cost-model.json /plutus-v2-activation/pinned-cost-model.json
cp /tmp/testnet/utxos/keys/genesis.1.vkey /plutus-v2-activation/utxo.vkey
cp /tmp/testnet/utxos/keys/genesis.1.skey /plutus-v2-activation/utxo.skey
cp /tmp/testnet/utxos/keys/stake.1.vkey /plutus-v2-activation/stake.vkey
echo "installed pinned PlutusV2 cost model in every generated Alonzo genesis"
"""
        volumes.append("plutus-v2-activation:/plutus-v2-activation")
        model.setdefault("volumes", {})["plutus-v2-activation"] = {}
        model["volumes"]["plutus-v2-activation-evidence"] = {}
        activation_command = r"""
set -euo pipefail
cleanup() {
  rm -f /activation/utxo.skey
}
record_stage() {
  jq -n --arg stage "$$1" --arg recorded_at "$$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    '{stage: $$stage, recorded_at: $$recorded_at}' \
    > /evidence/activation-status.json
}
trap cleanup EXIT
deadline=$$((SECONDS + 900))
while :; do
  if cardano-cli query tip --socket-path /state/node.socket --testnet-magic 42 > /evidence/tip.json 2>/evidence/query-error.txt; then
    era=$$(jq -r '.era // ""' /evidence/tip.json)
    epoch=$$(jq -r '.epoch // -1' /evidence/tip.json)
    if [ "$$era" = "Conway" ]; then break; fi
    if [ "$$SECONDS" -ge "$$deadline" ]; then
      echo "Conway activation window was not observed" >&2
      exit 41
    fi
  fi
  sleep 2
done
jq '{PlutusV2: .}' /activation/pinned-cost-model.json > /evidence/cost-models.json
cardano-cli query protocol-parameters --socket-path /state/node.socket \
  --testnet-magic 42 > /evidence/protocol-parameters-before.json
gov_deposit=$$(jq -r '.govActionDeposit' /evidence/protocol-parameters-before.json)
anchor_url=https://example.com
anchor_hash=$$(cardano-cli hash anchor-data --url "$$anchor_url")
cardano-cli conway governance action create-protocol-parameters-update \
  --testnet --governance-action-deposit "$$gov_deposit" \
  --deposit-return-stake-verification-key-file /activation/stake.vkey \
  --anchor-url "$$anchor_url" --anchor-data-hash "$$anchor_hash" \
  --cost-model-file /evidence/cost-models.json \
  --out-file /evidence/update.action
address=$$(cardano-cli address build --testnet-magic 42 --payment-verification-key-file /activation/utxo.vkey)
cardano-cli query utxo --address "$$address" --socket-path /state/node.socket \
  --testnet-magic 42 --out-file /evidence/utxo-before.json
tx_in=$$(jq -r 'to_entries | max_by(.value.value.lovelace) | .key' /evidence/utxo-before.json)
cardano-cli conway transaction build --testnet-magic 42 \
  --socket-path /state/node.socket --change-address "$$address" \
  --tx-in "$$tx_in" --proposal-file /evidence/update.action \
  --out-file /evidence/update.body
cardano-cli conway transaction sign --tx-body-file /evidence/update.body \
  --testnet-magic 42 --signing-key-file /activation/utxo.skey \
  --out-file /evidence/update.tx
record_stage transaction-built
cardano-cli conway transaction submit --socket-path /state/node.socket \
  --testnet-magic 42 --tx-file /evidence/update.tx \
  > /evidence/submit.json 2> /evidence/submit-error.txt
record_stage transaction-submitted
cardano-cli conway transaction txid --tx-file /evidence/update.tx \
  > /evidence/txid.json 2> /evidence/txid-error.txt
if ! jq -e '.txhash | type == "string" and test("^[0-9a-f]{64}$$")' \
  /evidence/txid.json >/dev/null; then
  record_stage malformed-txid
  echo "PlutusV2 transaction id was malformed" >&2
  exit 42
fi
tx_id=$$(jq -r '.txhash' /evidence/txid.json)
record_stage txid-validated
while :; do
  cardano-cli query utxo --address "$$address" --socket-path /state/node.socket \
    --testnet-magic 42 --out-file /evidence/utxo-after.json
  if jq -e --arg tx_id "$$tx_id" 'keys | any(startswith($$tx_id + "#"))' /evidence/utxo-after.json >/dev/null; then
    break
  fi
  if [ "$$SECONDS" -ge "$$deadline" ]; then
    record_stage inclusion-timeout
    echo "PlutusV2 update transaction was not included" >&2
    exit 43
  fi
  sleep 2
done
record_stage transaction-included
while :; do
  cardano-cli query protocol-parameters --socket-path /state/node.socket \
    --testnet-magic 42 > /evidence/live-protocol-parameters.json
  if jq -e --slurpfile model /activation/pinned-cost-model.json \
    '.costModels.PlutusV2 == $$model[0]' /evidence/live-protocol-parameters.json >/dev/null; then
    break
  fi
  if [ "$$SECONDS" -ge "$$deadline" ]; then
    record_stage activation-timeout
    echo "PlutusV2 cost model did not become active" >&2
    exit 44
  fi
  sleep 2
done
cardano-cli query tip --socket-path /state/node.socket --testnet-magic 42 > /evidence/active-tip.json
active_epoch=$$(jq -r '.epoch' /evidence/active-tip.json)
active_era=$$(jq -r '.era' /evidence/active-tip.json)
if [ "$$active_era" != "Conway" ]; then
  echo "PlutusV2 became active outside Conway" >&2
  exit 45
fi
record_stage cost-model-active
jq -n --arg tx_id "$$tx_id" --arg address "$$address" \
  --arg anchor_url "$$anchor_url" --arg anchor_hash "$$anchor_hash" \
  --argjson governance_action_deposit "$$gov_deposit" \
  --arg era "$$active_era" --argjson active_epoch "$$active_epoch" \
  --argjson submitted_epoch "$$epoch" \
  '{verified: true, update_transaction_id: $$tx_id, governance_action_id: ($$tx_id + "#0"), governance_action_deposit: $$governance_action_deposit, payment_address: $$address, anchor_url: $$anchor_url, anchor_hash: $$anchor_hash, submitted_in_epoch: $$submitted_epoch, active_in_epoch: $$active_epoch, active_era: $$era}' \
  > /evidence/result.json
""".strip() + "\n"
        model["services"]["plutus-v2-activate"] = {
            "image": str(config["cardano_image"]),
            "entrypoint": ["/bin/bash", "-lc"],
            "command": [activation_command],
            "read_only": True,
            "tmpfs": ["/tmp"],
            "volumes": [
                "p1-state:/state:ro",
                "plutus-v2-activation:/activation",
                "plutus-v2-activation-evidence:/evidence",
            ],
            "depends_on": {"p1": {"condition": "service_started"}},
            "restart": "no",
        }
        seed = model["services"]["amaru-consumer-seed"]
        seed.setdefault("depends_on", {})["plutus-v2-activate"] = {
            "condition": "service_completed_successfully"
        }
    for service_name, service in model["services"].items():
        if not _published_ports_are_loopback_only(service):
            raise RuntimeControlError(
                f"{service_name} published ports must bind loopback and avoid 8787"
            )
        labels = service.setdefault("labels", {})
        if not isinstance(labels, dict):
            raise RuntimeControlError(f"{service_name} labels must be a mapping")
        labels.update(
            {
                "ada2.managed": "dwarf",
                "ada2.profile": str(config["profile_id"]),
                "ada2.service": service_name,
            }
        )
        ownership = (config.get("substrate") or {}).get("controller_ownership")
        if isinstance(ownership, dict):
            labels.update(
                {
                    "io.dwarf.managed": "true",
                    "io.dwarf.installation": str(ownership["installation_id"]),
                    "io.dwarf.topology": str(ownership["topology_id"]),
                    "io.dwarf.component": "support",
                }
            )
        hardened = bool(
            service.get("read_only") is True
            and service.get("cap_drop") == ["ALL"]
            and "no-new-privileges:true" in (service.get("security_opt") or [])
        )
        if hardened:
            labels["io.dwarf.hardening"] = "read-only-rootfs-no-new-privileges"
        elif service_name in HARDENING_EXCEPTION_SERVICES:
            labels["io.dwarf.hardening.exception"] = HARDENING_EXCEPTION
        else:
            raise RuntimeControlError(
                f"{service_name} has no approved container hardening policy"
            )
    ownership = (config.get("substrate") or {}).get("controller_ownership")
    if isinstance(ownership, dict):
        node_by_id = {
            str(node.get("id") or ""): node
            for node in ((config.get("substrate") or {}).get("nodes") or [])
            if isinstance(node, dict)
        }
        for node_id, service_name in (config.get("controller_node_services") or {}).items():
            service = model["services"].get(str(service_name))
            node = node_by_id.get(str(node_id))
            if not isinstance(service, dict) or not isinstance(node, dict):
                raise RuntimeControlError("controller node-to-service identity is invalid")
            service["labels"].update(
                {
                    "io.dwarf.component": "node",
                    "io.dwarf.node_id": str(node_id),
                    "io.dwarf.implementation": str(node.get("impl") or ""),
                    "io.dwarf.version": str(node.get("version") or ""),
                }
            )
        for network in (model.get("networks") or {}).values():
            if not isinstance(network, dict):
                raise RuntimeControlError("runtime network specification is invalid")
            network["labels"] = {
                "io.dwarf.managed": "true",
                "io.dwarf.installation": str(ownership["installation_id"]),
                "io.dwarf.topology": str(ownership["topology_id"]),
                "io.dwarf.component": "network",
            }
        for volume in (model.get("volumes") or {}).values():
            if not isinstance(volume, dict):
                raise RuntimeControlError("runtime volume specification is invalid")
            if volume.get("external") is True or volume.get("name") is not None:
                raise RuntimeControlError(
                    "controller runtime volumes must be private project volumes"
                )
            labels = volume.setdefault("labels", {})
            if not isinstance(labels, dict):
                raise RuntimeControlError("runtime volume labels must be a mapping")
            labels.update(
                {
                    "io.dwarf.managed": "true",
                    "io.dwarf.installation": str(ownership["installation_id"]),
                    "io.dwarf.topology": str(ownership["topology_id"]),
                    "io.dwarf.component": "volume",
                }
            )
    retained_runtime = {
        "profile_id": config["profile_id"],
        "scope": config["scope"],
        "lifecycle": LIFECYCLE,
        "fresh_state_required": True,
        "retained_only_after_runtime_gates": True,
    }
    if config.get("plutus_v2_genesis") is True:
        retained_runtime["plutus_v2_genesis"] = True
    if config.get("local_reward_window_contract") is not None:
        retained_runtime["local_reward_window_contract"] = config[
            "local_reward_window_contract"
        ]
    _map_host_bind_sources(model, config, base_package=Path(base_package))
    model["x-dwarf-retained-runtime"] = retained_runtime
    return model


def _selected_node_metadata(config: dict[str, Any]) -> list[dict[str, Any]]:
    substrate = dict(config.get("substrate") or {})
    node_by_id = {
        str(node.get("id") or ""): node
        for node in (substrate.get("nodes") or [])
        if isinstance(node, dict)
    }
    return [
        {
            "id": node_id,
            "service": service_name,
            "implementation": str(node_by_id[node_id].get("impl") or ""),
            "version": str(node_by_id[node_id].get("version") or ""),
            "image": str(node_by_id[node_id].get("image") or ""),
            "supporting": node_by_id[node_id].get("supporting") is True,
        }
        for node_id, service_name in (config.get("controller_node_services") or {}).items()
        if node_id in node_by_id
    ]


def build_runtime_metadata(
    config: dict[str, Any],
    *,
    identity: dict[str, Any],
    observation: dict[str, Any],
    compose_file: str,
    plutus_v2: dict[str, Any] | None = None,
) -> dict[str, Any]:
    substrate = dict(config.get("substrate") or {})
    selected_nodes = _selected_node_metadata(config)
    declared_shape = dict(substrate.get("actual_topology") or {})
    actual_services = set(declared_shape.get("all_services") or ())
    if not actual_services:
        actual_services.update(CARDANO_SERVICES)
        actual_services.update(AMARU_RELAYS)
        actual_services.update(
            {
                "configurator",
                "tracer",
                "tracer-sidecar",
                "log-tailer",
                "sidecar",
                "amaru-consumer-seed",
            }
        )
        if config.get("measurement_target_mode") == "patched":
            actual_services.add("amaru-target-extract")
        if config.get("plutus_v2_genesis") is True:
            actual_services.add("plutus-v2-activate")
    metadata = {
        "schema_version": 1,
        "profile_id": config["profile_id"],
        "network_magic": substrate["network_magic"],
        "created_at": _utc_now(),
        "testbed": "local-devnet",
        "target_implementation": config["scope"].replace("-only", ""),
        "scope": config["scope"],
        "lifecycle": LIFECYCLE,
        "compose_project": config["compose_project"],
        "compose_file": compose_file,
        "logical_target_count": int(substrate.get("target_node_count") or 0),
        "declared_support_node_count": int(substrate.get("support_node_count") or 0),
        "actual_topology": {
            **declared_shape,
            "cardano_services": list(CARDANO_SERVICES),
            "amaru_services": list(AMARU_RELAYS),
            "bootstrap_strategy": "per-relay-safe-snapshot-of-live-producer",
            "bootstrap_sources": {
                "amaru-relay-1": "p1",
                "amaru-relay-2": "p2",
            },
            "isolated_consumer": "amaru-consumer",
            "selected_nodes": selected_nodes,
            "additional_services": sorted(
                actual_services - {item["service"] for item in selected_nodes}
            ),
        },
        "versions": {
            "cardano-node": config["supporting_cardano_version"],
            "amaru": config["amaru_version"],
            "policy": substrate.get("version_policy"),
            "policy_source": substrate.get("version_policy_source"),
            "status": substrate.get("version_status"),
            "deployment_adapter": substrate.get("deployment_adapter"),
            "catalog_revision": substrate.get("catalog_revision"),
            "catalog_snapshot": substrate.get("catalog_snapshot"),
        },
        "images": {
            "cardano-node": config["cardano_image"],
            "amaru": config["amaru_image"],
        },
        "identity": identity,
        "measurement_target": config.get("measurement_target_identity"),
        "amaru_json_traces": bool(config.get("amaru_json_traces")),
        "local_reward_window_contract": config.get(
            "local_reward_window_contract"
        ),
        "local_reward_window_runtime_evidence": config.get(
            "local_reward_window_runtime_evidence"
        ),
        "readiness": observation,
    }
    if plutus_v2 is not None:
        metadata["plutus_v2"] = plutus_v2
    return metadata


def collect_plutus_v2_evidence(
    config: dict[str, Any], *, project: str, evidence_root: Path
) -> dict[str, Any]:
    containers = _project_containers(project)
    configurator = containers.get("configurator")
    producer = containers.get("p1")
    activator = containers.get("plutus-v2-activate")
    if not configurator or not producer or not activator:
        raise RuntimeControlError(
            "PlutusV2 evidence requires configurator, activation, and p1 containers"
        )
    output_root = evidence_root / "plutus-v2"
    output_root.mkdir(parents=True, exist_ok=False)
    pinned_path = output_root / "pinned-cost-model.json"
    shutil.copy2(_plutus_v2_model_path(config), pinned_path)
    activation_root = output_root / "activation"
    activation_root.mkdir()
    activation_copy = _run(
        ["docker", "cp", f"{activator}:/evidence/.", str(activation_root)],
        timeout=120,
    )
    if activation_copy.returncode != 0:
        raise RuntimeControlError(
            activation_copy.stderr.strip() or "cannot retain PlutusV2 activation evidence"
        )
    activation_result_path = activation_root / "result.json"
    if not activation_result_path.is_file():
        raise RuntimeControlError("PlutusV2 activation result is missing")
    activation_result = json.loads(activation_result_path.read_text(encoding="utf-8"))
    if activation_result.get("verified") is not True:
        raise RuntimeControlError("PlutusV2 activation did not verify")
    genesis_paths = []
    conway_genesis_paths = []
    for index in range(1, 4):
        destination = output_root / f"alonzo-genesis-p{index}.json"
        copied = _run(
            [
                "docker", "cp",
                f"{configurator}:/configs/{index}/configs/alonzo-genesis.json",
                str(destination),
            ],
            timeout=120,
        )
        if copied.returncode != 0:
            raise RuntimeControlError(copied.stderr.strip() or f"cannot retain p{index} Alonzo genesis")
        genesis_paths.append(destination)
        conway_destination = output_root / f"conway-genesis-p{index}.json"
        conway_copied = _run(
            [
                "docker", "cp",
                f"{configurator}:/configs/{index}/configs/conway-genesis.json",
                str(conway_destination),
            ],
            timeout=120,
        )
        if conway_copied.returncode != 0:
            raise RuntimeControlError(
                conway_copied.stderr.strip()
                or f"cannot retain p{index} Conway genesis"
            )
        conway_genesis_paths.append(conway_destination)
    live_path = output_root / "live-protocol-parameters.json"
    queried = _run(
        [
            "docker", "exec", producer, "cardano-cli", "query", "protocol-parameters",
            "--socket-path", "/state/node.socket", "--testnet-magic", "42",
        ],
        timeout=120,
    )
    if queried.returncode != 0:
        raise RuntimeControlError(queried.stderr.strip() or "cannot query live protocol parameters")
    live_path.write_text(queried.stdout, encoding="utf-8")
    evidence = verify_plutus_v2_evidence(
        pinned_model_path=pinned_path,
        expected_sha256=str(config["plutus_v2_cost_model_sha256"]),
        genesis_paths=genesis_paths,
        conway_genesis_paths=conway_genesis_paths,
        live_protocol_parameters_path=live_path,
    )
    evidence["activation"] = {
        **activation_result,
        "artifacts": [
            {
                "path": str(path),
                "sha256": _sha256(path),
            }
            for path in sorted(activation_root.iterdir())
            if path.is_file()
        ],
    }
    return evidence


_PLUTUS_V2_SAFE_FAILURE_ARTIFACTS = (
    "activation-status.json",
    "tip.json",
    "query-error.txt",
    "protocol-parameters-before.json",
    "cost-models.json",
    "update.action",
    "utxo-before.json",
    "submit.json",
    "submit-error.txt",
    "txid.json",
    "txid-error.txt",
    "utxo-after.json",
    "live-protocol-parameters.json",
    "active-tip.json",
)
_PLUTUS_V2_FAILURE_BUDGET_BYTES = 256 * 1024
_PLUTUS_V2_STDERR_BUDGET_BYTES = 16 * 1024


def retain_plutus_v2_failure_evidence(
    *, project: str, evidence_root: Path
) -> dict[str, Any]:
    """Retain a bounded, non-secret activation receipt before failure teardown."""
    containers = _project_containers(project)
    activator = containers.get("plutus-v2-activate")
    if not activator:
        return {
            "retained": False,
            "reason": "activation-container-unavailable",
            "artifacts": [],
            "activator_state": None,
            "producer_tip": None,
            "total_bytes": 0,
            "budget_bytes": _PLUTUS_V2_FAILURE_BUDGET_BYTES,
        }

    output_root = evidence_root / "plutus-v2-failure"
    output_root.mkdir(parents=True, exist_ok=False)
    artifacts: list[dict[str, Any]] = []
    total_bytes = 0

    def accept_artifact(name: str, destination: Path) -> bool:
        nonlocal total_bytes
        if destination.is_symlink() or not destination.is_file():
            destination.unlink(missing_ok=True)
            return False
        size = destination.stat().st_size
        if size > _PLUTUS_V2_FAILURE_BUDGET_BYTES - total_bytes:
            destination.unlink()
            return False
        total_bytes += size
        artifacts.append(
            {"name": name, "sha256": _sha256(destination), "bytes": size}
        )
        return True

    activator_state: dict[str, Any] | None = None
    inspected = _run(
        ["docker", "inspect", "--format", "{{json .State}}", activator],
        timeout=30,
    )
    if inspected.returncode == 0:
        try:
            raw_state = json.loads(inspected.stdout)
        except json.JSONDecodeError:
            raw_state = None
        if isinstance(raw_state, dict):
            exit_code = raw_state.get("ExitCode")
            activator_state = {
                "status": str(raw_state.get("Status") or "")[:32],
                "exit_code": (
                    exit_code
                    if type(exit_code) is int and 0 <= exit_code <= 255
                    else None
                ),
                "oom_killed": raw_state.get("OOMKilled") is True,
                "started_at": str(raw_state.get("StartedAt") or "")[:64],
                "finished_at": str(raw_state.get("FinishedAt") or "")[:64],
            }
            state_path = output_root / "activator-state.json"
            _write_json(state_path, activator_state)
            accept_artifact("activator-state.json", state_path)

    logged = _run(
        ["docker", "logs", "--tail", "200", activator], timeout=30
    )
    if logged.returncode == 0 and logged.stderr:
        stderr_bytes = logged.stderr.encode("utf-8", errors="replace")
        stderr_bytes = stderr_bytes[-_PLUTUS_V2_STDERR_BUDGET_BYTES:]
        stderr_path = output_root / "activator-stderr.txt"
        stderr_path.write_bytes(stderr_bytes)
        accept_artifact("activator-stderr.txt", stderr_path)

    producer_tip: dict[str, Any] | None = None
    producer = containers.get("p1")
    if producer:
        tip_result = _run(
            [
                "docker",
                "exec",
                producer,
                "cardano-cli",
                "query",
                "tip",
                "--socket-path",
                "/state/node.socket",
                "--testnet-magic",
                "42",
            ],
            timeout=30,
        )
        if tip_result.returncode == 0:
            try:
                raw_tip = json.loads(tip_result.stdout)
            except json.JSONDecodeError:
                raw_tip = None
            if isinstance(raw_tip, dict):
                producer_tip = {
                    "observed_at": _utc_now(),
                    "slot": raw_tip.get("slot") if type(raw_tip.get("slot")) is int else None,
                    "block": raw_tip.get("block") if type(raw_tip.get("block")) is int else None,
                    "epoch": raw_tip.get("epoch") if type(raw_tip.get("epoch")) is int else None,
                    "era": str(raw_tip.get("era") or "")[:32],
                    "sync_progress": str(raw_tip.get("syncProgress") or "")[:32],
                }
                tip_path = output_root / "producer-tip.json"
                _write_json(tip_path, producer_tip)
                accept_artifact("producer-tip.json", tip_path)

    for name in _PLUTUS_V2_SAFE_FAILURE_ARTIFACTS:
        destination = output_root / name
        copied = _run(
            ["docker", "cp", f"{activator}:/evidence/{name}", str(destination)],
            timeout=30,
        )
        if copied.returncode != 0:
            destination.unlink(missing_ok=True)
            continue
        accept_artifact(name, destination)
    if not artifacts:
        output_root.rmdir()
    return {
        "retained": bool(artifacts),
        "reason": None if artifacts else "no-safe-activation-artifacts",
        "artifacts": artifacts,
        "activator_state": activator_state,
        "producer_tip": producer_tip,
        "total_bytes": total_bytes,
        "budget_bytes": _PLUTUS_V2_FAILURE_BUDGET_BYTES,
    }


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _validated_bootstrap_evidence(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict) or set(value) != set(AMARU_RELAYS):
        return {}
    validated: dict[str, dict[str, Any]] = {}
    for name in AMARU_RELAYS:
        relay = value.get(name)
        if not isinstance(relay, dict):
            return {}
        targets = relay.get("target_slots")
        if (
            not isinstance(targets, list)
            or len(targets) != 3
            or not all(type(slot) is int and slot >= 0 for slot in targets)
            or relay.get("committed") is not True
            or relay.get("exec_started") is not True
        ):
            return {}
        validated[name] = {
            "target_slots": list(targets),
            "committed": True,
            "exec_started": True,
        }
    return validated


def _retained_bootstrap_evidence(
    prior: dict[str, Any], evidence_root: Path
) -> dict[str, dict[str, Any]]:
    if "bootstrap_evidence" in prior:
        return _validated_bootstrap_evidence(prior.get("bootstrap_evidence"))

    remembered = remember_bootstrap_evidence(prior.get("observation") or {}, {})
    validated = _validated_bootstrap_evidence(remembered)
    if validated:
        return validated

    health_files = sorted(
        path
        for path in evidence_root.iterdir()
        if re.fullmatch(r"health-\d{3}\.json", path.name)
    )
    if len(health_files) > 256:
        return {}
    root = evidence_root.resolve()
    for path in health_files:
        if path.is_symlink() or not path.is_file():
            return {}
        try:
            path.resolve().relative_to(root)
            historical = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError, json.JSONDecodeError):
            return {}
        if not isinstance(historical, dict):
            return {}
        remembered = remember_bootstrap_evidence(historical, remembered)
        validated = _validated_bootstrap_evidence(remembered)
        if validated:
            return validated
    return {}


def _compose_logs(project: str, compose_file: Path) -> str:
    result = _run(
        _compose(project, compose_file, "logs", "--no-color", "--timestamps"),
        timeout=300,
    )
    return result.stdout + ("\n[stderr]\n" + result.stderr if result.stderr else "")


def _gate_result(gates: dict[str, bool]) -> dict[str, Any]:
    required = set(REQUIRED_GATES)
    if "plutus_v2_live_parameters" in gates:
        required.add("plutus_v2_live_parameters")
    failed = sorted(name for name in required if gates.get(name) is not True)
    return {
        "passed": not failed,
        "classification": "retained-runtime-ready" if not failed else "runtime-contract-failed",
        "required_gates": sorted(required),
        "failed_gates": failed,
        "gates": {name: gates.get(name) is True for name in sorted(required)},
    }


def _fresh_window_advanced(result: dict[str, Any]) -> bool:
    samples = ((result.get("observation") or {}).get("samples") or [])
    if len(samples) < 2:
        return False
    first = samples[0]
    final = samples[-1]

    def advanced(before: Any, after: Any) -> bool:
        return (
            isinstance(before, int)
            and not isinstance(before, bool)
            and isinstance(after, int)
            and not isinstance(after, bool)
            and after > before
        )

    first_tips = first.get("tips") or {}
    final_tips = final.get("tips") or {}
    if any(
        not advanced(
            (first_tips.get(name) or {}).get("slot"),
            (final_tips.get(name) or {}).get("slot"),
        )
        for name in CARDANO_SERVICES
    ):
        return False
    first_relays = first.get("amaru_relays") or {}
    final_relays = final.get("amaru_relays") or {}
    return all(
        advanced(
            (first_relays.get(name) or {}).get("current_slot"),
            (final_relays.get(name) or {}).get("current_slot"),
        )
        for name in AMARU_RELAYS
    )


def probe_retained_health(
    config: dict[str, Any], *, output: Path
) -> dict[str, Any]:
    """Collect a new semantic sample for an already-retained control runtime."""

    validate_runtime_request(config)
    runtime_root = Path(str(config["runtime_root"]))
    evidence_root = runtime_root / "evidence"
    try:
        evidence_root.resolve().relative_to(runtime_root.resolve())
    except ValueError as exc:
        raise RuntimeControlError(
            "retained runtime evidence escapes the runtime root"
        ) from exc
    if evidence_root.is_symlink() or not evidence_root.is_dir():
        raise RuntimeControlError("retained runtime evidence root is invalid")
    try:
        output.resolve().relative_to(evidence_root.resolve())
    except ValueError as exc:
        raise RuntimeControlError("fresh health output must remain in runtime evidence") from exc
    compose_file = runtime_root / "docker-compose.json"
    deployment_report = evidence_root / "deployment-report.json"
    if (
        compose_file.is_symlink()
        or deployment_report.is_symlink()
        or not compose_file.is_file()
        or not deployment_report.is_file()
    ):
        raise RuntimeControlError("retained runtime evidence is incomplete")
    prior = json.loads(deployment_report.read_text(encoding="utf-8"))
    if prior.get("passed") is not True or prior.get("retained") is not True:
        raise RuntimeControlError("retained runtime did not pass its deployment gates")
    bootstrap_evidence = _retained_bootstrap_evidence(prior, evidence_root)
    observation_path = output.with_name(output.stem + "-observation.json")
    observation = collect_and_classify(
        project=str(config["compose_project"]),
        output=observation_path,
        sample_seconds=max(1.0, float(config.get("sample_seconds") or 10.0)),
    )
    bootstrap_evidence = remember_bootstrap_evidence(
        observation, bootstrap_evidence
    )
    fresh_window_advanced = _fresh_window_advanced(observation)
    ready = (
        fresh_readiness_proven(observation, bootstrap_evidence)
        and fresh_window_advanced
    )
    identity = _identity_observation(
        str(config["compose_project"]),
        scope=str(config["scope"]),
        cardano_version=str(config["supporting_cardano_version"]),
        amaru_version=str(config["amaru_version"]),
        cardano_image=str(config["cardano_image"]),
        amaru_image=str(config["amaru_image"]),
        amaru_runtime_interface=config.get("amaru_runtime_interface"),
        measurement_target_identity=config.get("measurement_target_identity"),
        operator_stock_registration_expected=_operator_stock_registration_expected(
            config
        ),
        operator_stock_content_id=_validated_operator_stock_content_id(
            config, amaru_image=str(config["amaru_image"])
        ),
    )
    observed = observation.get("observation") or {}
    containers = observed.get("containers") or {}
    logs = _compose_logs(str(config["compose_project"]), compose_file)
    signals = classify_log_signals(logs + "\n" + _amaru_log_text(str(config["compose_project"])))
    gates = {
        "fresh_state": (prior.get("gates") or {}).get("fresh_state") is True,
        "local_reward_window_contract": (
            (prior.get("gates") or {}).get("local_reward_window_contract") is True
        ),
        "exact_identity": identity.get("matched") is True,
        "required_services": observation.get("state") == "healthy",
        "chain_progress": ready,
        "fresh_window_advancement": fresh_window_advanced,
        "peer_formation": observation.get("state") == "healthy",
        "consumer_amaru_only_path": (
            (observed.get("peer_contract") or {}).get(
                "amaru_consumer_only_amaru_upstreams"
            )
            is True
        ),
        "consumer_converged": ready,
        "no_restart_loop": bool(containers)
        and all(
            int(item.get("restart_count") or 0) == 0
            for item in containers.values()
        ),
        "no_fatal_signatures": not signals["fatal"],
    }
    if "plutus_v2_live_parameters" in (prior.get("required_gates") or []):
        gates["plutus_v2_live_parameters"] = (
            (prior.get("gates") or {}).get("plutus_v2_live_parameters") is True
        )
    classified = _gate_result(gates)
    result = {
        "schema_version": 1,
        "profile_id": config["profile_id"],
        "scope": config["scope"],
        "project": config["compose_project"],
        "observed_at": _utc_now(),
        "fresh_observation": True,
        "retained": True,
        "identity": identity,
        "selected_nodes": _selected_node_metadata(config),
        "actual_topology": (config.get("substrate") or {}).get(
            "actual_topology"
        ),
        "observation": observation,
        "bootstrap_evidence": bootstrap_evidence,
        "log_signals": signals,
        **classified,
    }
    _write_json(output, result)
    return result


def deploy(config: dict[str, Any], *, base_package: Path = BASE_PACKAGE) -> dict[str, Any]:
    validate_runtime_request(config)
    runtime_root = Path(config["runtime_root"])
    project = str(config["compose_project"])
    evidence_root = runtime_root / "evidence"
    compose_file = runtime_root / "docker-compose.json"
    report_file = evidence_root / "deployment-report.json"
    runtime_file = runtime_root / "runtime.json"
    if compose_file.exists() or runtime_file.exists():
        raise RuntimeControlError(
            f"runtime assets already exist under {runtime_root}",
            code="runtime_assets_exist",
            stage="preflight",
        )
    if not _project_is_fresh(project):
        raise RuntimeControlError(
            f"compose project is not fresh: {project}",
            code="compose_project_not_fresh",
            stage="preflight",
        )
    try:
        evidence_root.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise RuntimeControlError(
            "runtime asset staging permission denied",
            code="runtime_asset_permission_denied",
            stage="asset_staging",
        ) from exc
    effective_config = _stage_runtime_assets(config, base_package=base_package)
    model = prepare_runtime_model(
        _render_baseline(base_package), effective_config, base_package=base_package
    )
    _write_json(compose_file, model)

    events: list[dict[str, Any]] = []

    def emit(stage: str, **details: Any) -> None:
        event = {"at": _utc_now(), "stage": stage, **details}
        events.append(event)
        print(json.dumps(event, sort_keys=True), flush=True)

    gates: dict[str, bool] = {
        "fresh_state": True,
        "local_reward_window_contract": False,
    }
    if config.get("plutus_v2_genesis") is True:
        gates["plutus_v2_live_parameters"] = False
    observation: dict[str, Any] = {}
    identity: dict[str, Any] = {}
    signals: dict[str, list[str]] = {"fatal": [], "background": []}
    terminal_failure: str | None = None
    error: str | None = None
    retain = False
    plutus_v2_evidence: dict[str, Any] | None = None
    plutus_v2_failure_evidence: dict[str, Any] | None = None
    reward_window_evidence: dict[str, Any] | None = None
    bootstrap_evidence: dict[str, Any] = {}
    emit("prepared", project=project, runtime_root=str(runtime_root))
    try:
        up = _run(
            _compose(project, compose_file, "up", "-d"),
            timeout=max(900, int(config.get("healthy_timeout_seconds") or 1800)),
        )
        if up.returncode != 0:
            raise RuntimeControlError((up.stderr or up.stdout or "compose startup failed").strip())
        emit("started")
        reward_window_evidence = collect_local_reward_window_evidence(
            effective_config, project=project, evidence_root=evidence_root
        )
        gates["local_reward_window_contract"] = (
            reward_window_evidence.get("verified") is True
        )
        effective_config["local_reward_window_runtime_evidence"] = (
            reward_window_evidence
        )
        emit("local_reward_window_verified")
        deadline = time.monotonic() + max(
            1, int(config.get("healthy_timeout_seconds") or 1800)
        )
        attempt = 0
        while time.monotonic() <= deadline:
            attempt += 1
            observation = collect_and_classify(
                project=project,
                output=evidence_root / f"health-{attempt:03d}.json",
                sample_seconds=float(config.get("sample_seconds") or 10.0),
            )
            bootstrap_evidence = remember_bootstrap_evidence(
                observation, bootstrap_evidence
            )
            ready = (
                fresh_readiness_proven(observation, bootstrap_evidence)
                and _fresh_window_advanced(observation)
            )
            emit(
                "readiness",
                attempt=attempt,
                ready=ready,
                state=observation.get("state"),
            )
            if ready:
                break
            if observation.get("state") == "unhealthy":
                terminal_failure = classify_terminal_runtime_failure(
                    _amaru_log_text(project)
                )
                if terminal_failure:
                    raise RuntimeControlError(terminal_failure)
            time.sleep(max(1.0, float(config.get("sample_seconds") or 10.0)))

        identity = _identity_observation(
            project,
            scope=str(config["scope"]),
            cardano_version=str(config["supporting_cardano_version"]),
            amaru_version=str(config["amaru_version"]),
            cardano_image=str(config["cardano_image"]),
            amaru_image=str(config["amaru_image"]),
            amaru_runtime_interface=config.get("amaru_runtime_interface"),
            measurement_target_identity=config.get("measurement_target_identity"),
            operator_stock_registration_expected=_operator_stock_registration_expected(
                config
            ),
            operator_stock_content_id=_validated_operator_stock_content_id(
                config, amaru_image=str(config["amaru_image"])
            ),
        )
        _write_json(evidence_root / "identity.json", identity)
        logs = _compose_logs(project, compose_file) + "\n" + _amaru_log_text(project)
        (evidence_root / "compose-logs.txt").write_text(logs, encoding="utf-8")
        ps = _run(_compose(project, compose_file, "ps", "--all"), timeout=60)
        (evidence_root / "compose-ps.txt").write_text(
            ps.stdout + ps.stderr, encoding="utf-8"
        )

        fresh_window_advanced = _fresh_window_advanced(observation)
        healthy = (
            fresh_readiness_proven(observation, bootstrap_evidence)
            and fresh_window_advanced
        )
        observed = observation.get("observation") or {}
        containers = observed.get("containers") or {}
        gates.update(
            {
                "exact_identity": bool(identity.get("matched")),
                "required_services": observation.get("state") == "healthy",
                "chain_progress": healthy,
                "fresh_window_advancement": fresh_window_advanced,
                "peer_formation": observation.get("state") == "healthy",
                "consumer_amaru_only_path": (
                    (observed.get("peer_contract") or {}).get(
                        "amaru_consumer_only_amaru_upstreams"
                    )
                    is True
                ),
                "consumer_converged": healthy,
                "no_restart_loop": bool(containers)
                and all(
                    int(item.get("restart_count") or 0) == 0
                    for item in containers.values()
                ),
            }
        )
        signals = classify_log_signals(logs)
        gates["no_fatal_signatures"] = not signals["fatal"]
        classification = _gate_result(gates)
        core_failed = [
            name
            for name in classification["failed_gates"]
            if name != "plutus_v2_live_parameters"
        ]
        if core_failed:
            raise RuntimeControlError(
                "runtime gates failed: " + ", ".join(core_failed)
            )
        if config.get("plutus_v2_genesis") is True:
            plutus_v2_evidence = collect_plutus_v2_evidence(
                config, project=project, evidence_root=evidence_root
            )
            gates["plutus_v2_live_parameters"] = True
        retain = True
        metadata = build_runtime_metadata(
            effective_config,
            identity=identity,
            observation=observation,
            compose_file=str(compose_file),
            plutus_v2=plutus_v2_evidence,
        )
        _write_json(runtime_file, metadata)
        emit("retained", project=project, runtime_json=str(runtime_file))
    except Exception as exc:
        error = str(exc)
        if config.get("plutus_v2_genesis") is True:
            try:
                plutus_v2_failure_evidence = retain_plutus_v2_failure_evidence(
                    project=project, evidence_root=evidence_root
                )
            except Exception:
                plutus_v2_failure_evidence = {
                    "retained": False,
                    "reason": "activation-evidence-retention-failed",
                    "artifacts": [],
                }
            emit(
                "plutus_v2_failure_evidence",
                retained=plutus_v2_failure_evidence["retained"],
                artifacts=[
                    item["name"]
                    for item in plutus_v2_failure_evidence["artifacts"]
                ],
            )
        emit("failed", error=error)
    finally:
        clean_failure_teardown = None
        if not retain:
            down = _run(
                _compose(project, compose_file, "down", "-v", "--remove-orphans"),
                timeout=600,
            )
            clean_failure_teardown = down.returncode == 0 and _project_is_fresh(project)
            (evidence_root / "teardown.txt").write_text(
                down.stdout + ("\n[stderr]\n" + down.stderr if down.stderr else ""),
                encoding="utf-8",
            )
            emit("failure_teardown", passed=clean_failure_teardown)

    classified = _gate_result(gates)
    result = {
        "schema_version": 1,
        "profile_id": config["profile_id"],
        "scope": config["scope"],
        "lifecycle": LIFECYCLE,
        "project": project,
        "runtime_root": str(runtime_root),
        "completed_at": _utc_now(),
        "retained": retain,
        "error": error,
        "terminal_failure": terminal_failure,
        "identity": identity,
        "selected_nodes": _selected_node_metadata(effective_config),
        "actual_topology": (effective_config.get("substrate") or {}).get(
            "actual_topology"
        ),
        "observation": observation,
        "bootstrap_evidence": _validated_bootstrap_evidence(bootstrap_evidence),
        "plutus_v2_failure_evidence": plutus_v2_failure_evidence,
        "log_signals": signals,
        "local_reward_window_contract": reward_window_evidence,
        "events": events,
        "clean_failure_teardown": clean_failure_teardown,
        **classified,
    }
    _write_json(report_file, result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--base-package", type=Path, default=BASE_PACKAGE)
    parser.add_argument("--verify-retained", action="store_true")
    parser.add_argument("--health-output", type=Path)
    args = parser.parse_args(argv)
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        if args.verify_retained:
            if args.health_output is None:
                raise RuntimeControlError("--health-output is required for retained health")
            result = probe_retained_health(config, output=args.health_output)
        else:
            if args.health_output is not None:
                raise RuntimeControlError("--health-output requires --verify-retained")
            result = deploy(config, base_package=args.base_package)
    except Exception as exc:
        failure_code = str(getattr(exc, "failure_code", "") or "")
        failure_stage = str(getattr(exc, "failure_stage", "") or "")
        safe_failures = {
            (
                "compose_project_not_fresh",
                "preflight",
            ): "runtime control preflight failed",
            ("runtime_assets_exist", "preflight"): "runtime control preflight failed",
            (
                "required_runtime_asset_missing",
                "asset_staging",
            ): "runtime asset staging failed",
            (
                "runtime_asset_permission_denied",
                "asset_staging",
            ): "runtime asset staging failed",
            (
                "operator_registration_identity_invalid",
                "retained_request_validation",
            ): "operator registration validation failed",
        }
        safe_error = safe_failures.get((failure_code, failure_stage))
        if isinstance(exc, RuntimeControlError) and safe_error is not None:
            print(
                json.dumps(
                    {
                        "passed": False,
                        "error": safe_error,
                        "failure": {
                            "code": failure_code,
                            "stage": failure_stage,
                        },
                    },
                    sort_keys=True,
                )
            )
        else:
            print(
                json.dumps(
                    {"passed": False, "error": "runtime control failed"},
                    sort_keys=True,
                )
            )
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("passed") and result.get("retained") else 2


if __name__ == "__main__":
    raise SystemExit(main())
